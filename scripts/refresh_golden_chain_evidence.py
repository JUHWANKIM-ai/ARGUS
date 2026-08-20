r"""골든 근거가 정정 체인의 최신본을 빠뜨린 자리를 채운다.

## 왜 필요한가

`Q-030` 의 골든 근거는 원본(20241030000098)과 첫 정정본(20241113000148)을 담는데,
같은 체인에는 하루 뒤의 두 번째 정정본(20241114000001)이 있고 그것이 `is_latest` 다.
정정 이력 추적이 제대로 도는 답변은 최신본을 인용하는데, 채점은 "골든 접수번호가
답변에 있는가" 로 세므로 **더 최신 근거를 단 것이 누락으로 기록된다.**

전 문항을 훑어 같은 모양이 다섯 문항에 있음을 확인했다. 드러난 하나만 고치면 나머지
넷이 남는다.

## 무엇을 하는가

각 문항의 골든 근거 중 `is_latest=False` 인 것을 찾고, 그 `related` 체인에서 `is_latest`
인 접수번호가 골든에 없으면 **덧붙인다.**

🔴 **지우지 않는다.** 원본과 중간 정정본은 그 문항이 무엇을 근거로 출제됐는지의 기록이고,
"무엇이 언제 얼마로 바뀌었는가" 를 묻는 문항에서는 실제로 필요하다(`Q-054` 가 그렇다).
덧붙이기만 하면 채점상 손해가 없다 — `has_citation` 은 골든 중 **하나라도** 답변에 있으면
성립하고, 새로 넣은 근거가 프롬프트에 안 실렸는데 문항이 정답이면
`scripts/mark_required_evidence.py` 가 그것을 '참고' 로 내려 필수 분모에서 뺀다.

🔴 **이 스크립트는 평가셋을 바꾼다.** 이전 측정과의 비교는 근거 표시·근거 완전성 두 축에서
끊긴다. 그 둘을 인용하는 문서는 갱신 후 다시 재야 한다.

Usage:
    python scripts/refresh_golden_chain_evidence.py --check   # 바꾸지 않고 목록만
    python scripts/refresh_golden_chain_evidence.py
"""
from __future__ import annotations

import argparse
import io
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
EVALSETS = ("questions", "regression", "valuechain", "safety")
#: `is_latest` 와 `related` 를 아는 정형 레코드. 정기공시는 정정 체인이 없다.
RECORDS = ("exchange_records", "major_records", "holding_records")

WHY = "정정 체인의 최신본 — 2026-08-21 보강, 종전 골든이 이 판을 담지 않았다"


def load_records() -> dict:
    out = {}
    for name in RECORDS:
        path = ROOT / "artifacts" / ("%s.jsonl" % name)
        if not path.exists():
            sys.exit("🔴 레코드가 없습니다: %s" % path)
        for line in io.open(path, encoding="utf-8"):
            if line.strip():
                row = json.loads(line)
                if row.get("rcept_no"):
                    out[row["rcept_no"]] = row
    return out


def missing_latest(case: dict, rec: dict) -> list[str]:
    """골든에 없는, 같은 체인의 최신 접수번호. 순서를 고정해 실행마다 같게 만든다."""
    golden = [e["rcept_no"] for e in (case.get("evidence") or [])]
    have = set(golden)
    out: list[str] = []
    for rcept_no in golden:
        row = rec.get(rcept_no)
        if not row or row.get("is_latest") is not False:
            continue
        for other in row.get("related") or []:
            if (rec.get(other, {}).get("is_latest")
                    and other not in have and other not in out):
                out.append(other)
    return sorted(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="바꾸지 않고 목록만 낸다")
    args = ap.parse_args()

    rec = load_records()
    added_total = 0
    for name in EVALSETS:
        path = ROOT / "evalset" / ("%s.jsonl" % name)
        lines = [l for l in io.open(path, encoding="utf-8")]
        out_lines, touched = [], 0
        for line in lines:
            if not line.strip():
                out_lines.append(line)
                continue
            case = json.loads(line)
            #: 🔴 손대지 않는 줄은 **원문 그대로** 내보낸다. 왕복이 바이트 동일함을
            #: 확인했지만(78·10·12·12 전부), 그것에 의지하지 않는 편이 안전하다.
            extra = missing_latest(case, rec)
            if not extra:
                out_lines.append(line)
                continue
            for rcept_no in extra:
                case["evidence"].append(
                    {"rcept_no": rcept_no, "section": None, "why": WHY})
            print("%s  +%d  %s" % (case["question_id"], len(extra),
                                   " ".join(extra)))
            out_lines.append(json.dumps(case, ensure_ascii=False) + "\n")
            touched += 1
            added_total += len(extra)
        if touched and not args.check:
            io.open(path, "w", encoding="utf-8", newline="").writelines(out_lines)
            print("  -> %s 갱신 (문항 %d)" % (path.name, touched))

    print("\n덧붙인 근거 %d건%s" % (added_total, " (검사만 함)" if args.check else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
