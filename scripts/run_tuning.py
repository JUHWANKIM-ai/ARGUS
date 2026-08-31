"""튜닝 데이터셋을 Object Storage 에 올리고 학습을 생성한다.

두 걸음을 한 파일에 둔다. 학습 생성 API 가 버킷 이름과 파일 경로를 요구하므로,
올린 경로를 그대로 다음 호출에 넘기는 편이 손으로 옮겨 적는 것보다 안전하다.

의존성을 늘리지 않는다. `requirements.txt` 는 제출용으로 깎아둔 목록이라
`boto3` 하나를 위해 늘리지 않고, 서명은 표준 라이브러리로 직접 만든다.
Object Storage 는 S3 호환이므로 서명 방식도 AWS Signature V4 그대로다.

    python scripts/run_tuning.py upload
    python scripts/run_tuning.py create --name argus-a92-82turn

자격증명은 `.env` 에서 읽는다.
    NCP_ACCESS_KEY · NCP_SECRET_KEY · NCP_BUCKET
    NCP_ENDPOINT   (없으면 https://kr.object.ncloudstorage.com)
    NCP_REGION     (없으면 kr-standard)
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import hmac
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, "src")

from argus.config import CLOVA_HOST, settings

DATASET = Path("data/tuning/argus_golden_A92_20260831.jsonl")
TUNING_URL = CLOVA_HOST + "/tuning/v2/tasks"

#: 튜닝 대상은 런타임과 같은 베이스여야 A92 성적과 나란히 놓을 수 있다.
#: 다른 베이스에 태우면 튜닝이 아니라 증류가 되고 비교가 성립하지 않는다.
MODEL = "HCX-005"

#: API 기본값은 0.0 이다. 82턴짜리 데이터셋을 기본값 8 epoch 로 돌리면 암기로
#: 가는데, 검증 비율이 0 이면 그것을 볼 눈이 없다. 여덟 행을 떼어 손실이 갈리는
#: 지점을 보이게 한다.
VALIDATION_SPLIT = 0.1

#: 문서 예제의 `"1e-5f"` 는 서버가 거부한다 — `40001 Invalid parameter:
#: learningRate`. 표에 적힌 기본값 표기를 그대로 쓰면 통과하고, 응답은 `1e-05`
#: 로 돌려준다. 예제가 아니라 표를 따른다.
LEARNING_RATE = "1.0E-5"


def _env(name: str, default: str | None = None) -> str:
    settings()                      # `.env` 를 프로세스 환경으로 올린다
    value = os.environ.get(name, default)
    if not value:
        raise SystemExit(f"{name} 이 .env 에 없습니다")
    return value


# --- AWS Signature V4 --------------------------------------------------------
#
# 서명은 다섯 조각을 순서대로 쌓는다. 정규 요청 → 서명 문자열 → 서명 키 →
# 서명 → Authorization 헤더. 조각 하나라도 순서나 개행이 어긋나면 서버는
# SignatureDoesNotMatch 만 돌려주고 어디가 틀렸는지는 말해 주지 않는다.


def _sign(key: bytes, message: str) -> bytes:
    return hmac.new(key, message.encode("utf-8"), hashlib.sha256).digest()


def _signing_key(secret: str, stamp: str, region: str, service: str) -> bytes:
    key = _sign(f"AWS4{secret}".encode("utf-8"), stamp)
    for part in (region, service, "aws4_request"):
        key = _sign(key, part)
    return key


def _authorization(method: str, host: str, path: str, payload: bytes,
                   access: str, secret: str, region: str,
                   now: dt.datetime) -> dict:
    """PUT 하나에 필요한 헤더 묶음. 쿼리 문자열은 쓰지 않으므로 비운다."""
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    stamp = now.strftime("%Y%m%d")
    payload_hash = hashlib.sha256(payload).hexdigest()
    signed = "host;x-amz-content-sha256;x-amz-date"

    canonical = "\n".join([
        method, path, "",
        f"host:{host}",
        f"x-amz-content-sha256:{payload_hash}",
        f"x-amz-date:{amz_date}",
        "", signed, payload_hash])
    scope = f"{stamp}/{region}/s3/aws4_request"
    to_sign = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope,
                         hashlib.sha256(canonical.encode("utf-8")).hexdigest()])
    signature = hmac.new(_signing_key(secret, stamp, region, "s3"),
                         to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
    return {"Host": host,
            "x-amz-date": amz_date,
            "x-amz-content-sha256": payload_hash,
            "Authorization": (f"AWS4-HMAC-SHA256 Credential={access}/{scope}, "
                              f"SignedHeaders={signed}, Signature={signature}")}


def upload(key_path: str) -> str:
    """데이터셋을 버킷에 올리고 버킷 안 경로를 돌려준다."""
    access, secret = _env("NCP_ACCESS_KEY"), _env("NCP_SECRET_KEY")
    bucket = _env("NCP_BUCKET")
    endpoint = _env("NCP_ENDPOINT", "https://kr.object.ncloudstorage.com")
    region = _env("NCP_REGION", "kr-standard")

    payload = DATASET.read_bytes()
    host = endpoint.split("://", 1)[1].rstrip("/")
    path = f"/{bucket}/{key_path}"
    headers = _authorization("PUT", host, path, payload, access, secret, region,
                             dt.datetime.now(dt.timezone.utc))
    headers["Content-Type"] = "application/octet-stream"

    request = urllib.request.Request(endpoint.rstrip("/") + path, data=payload,
                                     headers=headers, method="PUT")
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            print(f"올림 {response.status} · {len(payload):,} bytes → {bucket}/{key_path}")
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"업로드 실패 {exc.code}: {exc.read().decode('utf-8', 'replace')}")
    return key_path


def create(name: str, key_path: str, epochs: int, learning_rate: str,
           validation_split: float, lora_r: int, lora_alpha: int) -> dict:
    """학습을 생성하고 응답 본문을 돌려준다."""
    body = {"name": name,
            "model": MODEL,
            "tuningType": "PEFT",
            "trainEpochs": epochs,
            "learningRate": learning_rate,
            "validationSplitRatio": validation_split,
            "loraR": lora_r,
            "loraAlpha": lora_alpha,
            "trainingDatasetBucket": _env("NCP_BUCKET"),
            "trainingDatasetFilePath": key_path,
            "trainingDatasetAccessKey": _env("NCP_ACCESS_KEY"),
            "trainingDatasetSecretKey": _env("NCP_SECRET_KEY")}
    request = urllib.request.Request(
        TUNING_URL, data=json.dumps(body).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {settings().clova_api_key}"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"학습 생성 실패 {exc.code}: "
                         f"{exc.read().decode('utf-8', 'replace')}")
    task = payload.get("result", {})
    print(f"학습 {task.get('id')} · {task.get('status')} · {task.get('model')} "
          f"· {task.get('trainEpochs')} epoch")
    return payload


def status(task_id: str) -> dict:
    """학습 하나의 현재 상태. 손실은 `statusInfo` 안에 실려 온다."""
    request = urllib.request.Request(
        f"{TUNING_URL}/{task_id}", method="GET",
        headers={"Authorization": f"Bearer {settings().clova_api_key}"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"학습 조회 실패 {exc.code}: "
                         f"{exc.read().decode('utf-8', 'replace')}")
    task = payload.get("result", {})
    info = task.get("statusInfo") or {}
    data = task.get("dataset") or {}
    print(f"{task.get('id')} · {task.get('status')} · {task.get('baseModel')} "
          f"· {task.get('trainEpochs')} epoch")
    print(f"  데이터셋 행 {data.get('rowCount')} · 토큰 {data.get('token')}")
    # 명세의 필드 표에는 없지만 실제 응답에 실려 오고, RUNNING 초반에는 손실도
    # 스텝도 비어 있어 이것이 유일한 진행 신호다. `DATASET_DOWNLOAD` 가 100 이면
    # 파일 자체는 받아들여졌다는 뜻이다.
    steps = " · ".join(f"{s['step']} {s['progress']}"
                       for s in info.get("progressInfo") or [])
    if steps:
        print(f"  {steps}")
    # 82턴짜리 학습에서 실제로 봐야 하는 값이다. 훈련 손실만 내려가고 검증 손실이
    # 돌아서면 암기가 시작된 지점이다.
    #
    # `dataRows` 와 `numOfTokens` 는 서버가 파일을 실제로 읽고 센 값이라,
    # 우리가 만든 82턴이 그대로 받아들여졌는지는 이 숫자로 확인한다.
    #
    # `failureReason` 은 코드로 온다. 우리 데이터셋을 가리키는 것은 `file.format`
    # (규격 불일치) · `file.encoding` (UTF-8 아님) · `file.extension` (확장자) 이고,
    # `resource.timeout` 은 GPU 확보 실패라 우리 잘못이 아니고 다시 걸면 된다.
    for field in ("dataRows", "numOfTokens", "currEpoch", "totalTrainEpochs",
                  "currStep", "totalTrainSteps", "trainLoss", "validLoss",
                  "lossDiff", "estimatedTime", "endDatetime",
                  "sendWeightSuccess", "uploadedWeights",
                  "failureReason", "message"):
        value = info.get(field)
        # 손실은 0.0 으로 올 수 있으므로 참거짓으로 걸러내지 않는다. 아직 안 채워진
        # 자리(None·빈 문자열·빈 배열)만 건너뛴다.
        if value is None or value == "" or value == []:
            continue
        print(f"  {field} {value}")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    default_key = f"argus/tuning/{DATASET.name}"

    up = sub.add_parser("upload", help="데이터셋을 버킷에 올린다")
    up.add_argument("--path", default=default_key, help="버킷 안 경로")

    make = sub.add_parser("create", help="학습을 생성한다")
    make.add_argument("--name", default="argus-a92")
    make.add_argument("--path", default=default_key, help="버킷 안 경로")
    make.add_argument("--epochs", type=int, default=8)
    make.add_argument("--learning-rate", default=LEARNING_RATE)
    make.add_argument("--validation-split", type=float, default=VALIDATION_SPLIT)
    make.add_argument("--lora-r", type=int, default=8)
    make.add_argument("--lora-alpha", type=int, default=64)

    look = sub.add_parser("status", help="학습 상태를 조회한다")
    look.add_argument("task_id")

    args = parser.parse_args()
    if args.command == "upload":
        upload(args.path)
    elif args.command == "status":
        status(args.task_id)
    else:
        payload = create(args.name, args.path, args.epochs, args.learning_rate,
                         args.validation_split, args.lora_r, args.lora_alpha)
        out = Path("scratch/tuning") / f"create_{args.name}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        print(out)


if __name__ == "__main__":
    main()
