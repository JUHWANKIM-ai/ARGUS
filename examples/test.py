import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

#: 배포된 평가 서버. 로컬 구동본을 쓰려면 `--host 127.0.0.1:8000`.
DEFAULT_HOST = "223.130.129.131"

#: 규약이 정한 문항당 상한과 같다.
TIMEOUT_S = 300

#: 요청 간 간격. 상류에 분당 요청 한도가 있어 연속 호출은 한계 고지를 부른다.
PAUSE_S = 2.0

#: 응답 스키마. 다섯 키가 모두 있어야 정상이다.
KEYS = ("question_id", "question", "retrieved_context", "think_trace", "answer")


def ask(host: str, question_id: str, question: str) -> tuple[dict, float]:
    """질의 하나를 던지고 (응답, 소요초) 를 돌려준다."""
    query = urllib.parse.urlencode({"question_id": question_id,
                                    "question": question})
    url = f"http://{host}/answer?{query}"
    started = time.monotonic()
    with urllib.request.urlopen(url, timeout=TIMEOUT_S) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    return body, time.monotonic() - started


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--host", default=DEFAULT_HOST,
                    help=f"API 호스트 (기본 {DEFAULT_HOST})")
    ap.add_argument("--questions", default="examples/questions.json",
                    help="질의 JSON 경로")
    ap.add_argument("--out", default="examples/answers.json",
                    help="응답을 적을 JSON 경로")
    ap.add_argument("--pause", type=float, default=PAUSE_S,
                    help=f"요청 간 대기 초 (기본 {PAUSE_S})")
    args = ap.parse_args(argv)

    spec = json.loads(Path(args.questions).read_text(encoding="utf-8"))
    items = spec["questions"]
    print(f"host={args.host} 질의 {len(items)}건\n")

    answers, failures = {}, 0
    for index, item in enumerate(items, start=1):
        qid, question = item["question_id"], item["question"]
        print(f"[{index}/{len(items)}] {qid} {question[:44]}...", flush=True)
        try:
            body, elapsed = ask(args.host, qid, question)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            failures += 1
            print(f"          실패 {exc}\n")
            answers[qid] = {"question_id": qid, "question": question,
                            "error": f"{type(exc).__name__}: {exc}"}
            continue

        missing = [key for key in KEYS if key not in body]
        record = {key: body.get(key, "") for key in KEYS}
        record["elapsed_s"] = round(elapsed, 1)
        if missing:
            record["missing_keys"] = missing
        answers[qid] = record

        head = " ".join((record["answer"] or "").split())[:100]
        print(f"          {elapsed:.1f}초  {head}\n", flush=True)
        if index < len(items):
            time.sleep(args.pause)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {"host": args.host,
               "fetched": datetime.now().isoformat(timespec="seconds"),
               "n": len(items),
               "failures": failures,
               "answers": answers}
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    print(f"{out} 에 {len(answers)}건 저장. 실패 {failures}건.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
