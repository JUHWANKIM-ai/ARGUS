r"""미공개셋 **서버 3회**를 채점한다. `scratch/score_heldout.py` 의 회차판이다.

    PYTHONIOENCODING=utf-8 python scratch/score_heldout_rounds.py --arm A86c

## 왜 따로 필요한가

`score_heldout.py` 는 `server_<코호트>/<팔>` 하나만 읽는다. `server_sweep.py --round N`
은 회차마다 `_r2` · `_r3` 접미를 붙인 디렉터리에 적으므로, 3회를 세려면 접미를 붙여
같은 함수를 세 번 불러야 한다.

**채점 규칙은 한 줄도 안 옮겨 적는다** — `score_heldout.score` 를 그대로 부른다.
그 파일이 정본이고, 여기서 다시 만들면 두 곳이 갈라진다.

## 🔴 이 실행의 성격 (2026-08-26)

미공개셋은 여태 **재는 데만** 썼다. 이번 실행은 다르다 — 후보 규칙 ⑦(재계산이 꼬리
블록에서 나오면 뒤집지 않는다)을 **넣을지 정하려고** 산다. 사용자가 그 값을 알고
고른 것이다(대안 B: 안 재고 ⑦을 보류).

그러므로 **이 뒤로 이 셋의 수치는 「독립 측정」이 아니다.** 문서에 그렇게 적어야 한다.
"""
import argparse
import importlib.util
import json
import sys
from pathlib import Path

sys.path.insert(0, ".")
sys.path.insert(0, "src")
sys.stdout.reconfigure(encoding="utf-8")

from scripts import error_analysis as ea                          # noqa: E402

_spec = importlib.util.spec_from_file_location("_score_heldout",
                                               "scratch/score_heldout.py")
_sh = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_sh)

SUFFIX = ("", "_r2", "_r3")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", default="A86c")
    ap.add_argument("--rounds", type=int, default=3)
    args = ap.parse_args()

    records = ea._load_records()
    per_round, rows_by_round = [], {}
    for index, suffix in enumerate(SUFFIX[:args.rounds], start=1):
        subsets, total_ok, total_n = [], 0, 0
        for cohort, path, _, _ in _sh.SUBSETS:
            report, rows = _sh.score(cohort + suffix, path, records, args.arm)
            ok = sum(1 for r in rows if r["correct"])
            total_ok += ok
            total_n += len(rows)
            subsets.append({"이름": cohort, "정답": ok, "분모": len(rows),
                            "오답": [r["question_id"] for r in rows
                                   if not r["correct"]]})
            rows_by_round.setdefault(index, []).extend(rows)
        per_round.append({"r": index, "합계": "%d/%d" % (total_ok, total_n),
                          "부분집합": subsets})
        print("r%d  %d/%d   %s" % (index, total_ok, total_n,
                                   " · ".join("%s %d/%d" % (s["이름"], s["정답"],
                                                            s["분모"])
                                              for s in subsets)), flush=True)

    every = {}
    for index, rows in rows_by_round.items():
        for r in rows:
            every.setdefault(r["question_id"], []).append(r["correct"])
    always = sorted(q for q, v in every.items() if not any(v))
    ever = sorted(q for q, v in every.items() if not all(v))

    out = {
        "생성": "scratch/score_heldout_rounds.py",
        "🔴 성격": "후보 규칙 ⑦ 판단에 썼다 — 이 뒤로 「독립 측정」이 아니다",
        "팔": args.arm,
        "회차": args.rounds,
        "회차별": per_round,
        "매회차오답": always,
        "한번이상오답": ever,
    }
    dest = Path("scratch/audit/heldout_%s_rounds.json" % args.arm.lower())
    dest.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n매 회차 오답 %s" % (always or "없음"))
    print("한 번이라도 오답 %s" % (ever or "없음"))
    print("%s 에 적었다" % dest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
