"""골든셋 전수에 「추론 논리성」 검사 다섯을 건다. LLM 0회 · 0원.

    PYTHONIOENCODING=utf-8 python scripts/score_reasoning.py

**커버리지가 5/7 이라는 사실을 함께 낸다.** 이 점수를 *"「추론 논리성」 97%"* 로
읽으면 안 된다. 우리가 **보는 양식 안에서** 결함이 없다는 뜻이다. 안 보는 둘
(전제 누락 · 비약·무관)은 이 수에 안 들어 있다.
기준과 시험은 `docs/reasoning-metric-2026-08-16.md`.
"""
from __future__ import annotations

import collections
import io
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def _args():
    """판 경로를 인자로 받는다. 박아 두면 판이 바뀔 때마다 스크립트를 고쳐야 하고,
    그러다 **옛 판의 답변을 새 판이라고 채점하는 사고**를 이미 한 번 냈다 (2026-08-16,
    A34 답변을 A38 점수로 적을 뻔했다)."""
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--review", default="scratch/audit/goldenset_review_A38.json")
    ap.add_argument("--run", default="scratch/ablation/v5/a38",
                    help="코호트 접미사 앞까지. 예: scratch/ablation/v5/cn")
    ap.add_argument("--arm", default="A38")
    ap.add_argument("--out", default="scratch/audit/reasoning_score_A38.json")
    return ap.parse_args()


def main() -> int:
    args = _args()
    from argus.agent import aggregate, critic, reasoning

    rows = []
    review = ROOT / args.review
    for item in json.load(io.open(review, encoding="utf-8"))["문항"]:
        rows.append((item["question_id"], item["track"], item["our_answer"] or "",
                     item.get("retrieved_context_chars", 0)))

    #: 근거는 응답 파일에서 되찾는다. 리뷰 JSON 은 길이만 들고 있다.
    def evidence_of(qid: str) -> str:
        for cohort in ("full", "valuechain", "safety"):
            path = (ROOT / f"{args.run}_{cohort}/{args.arm}/responses"
                    / f"{qid}.json")
            if path.exists():
                return (json.load(io.open(path, encoding="utf-8")).get("response")
                        or {}).get("retrieved_context") or ""
        return ""

    hits = collections.Counter()
    detail = []
    for qid, track, answer, _chars in rows:
        flags = []

        _new, aligned = aggregate.fix_self_contradiction(answer)
        if aligned:
            flags.append(("① 수치 자기정합", "; ".join(aligned)))

        calc = critic.verify_answer_arithmetic(answer)
        if not calc.get("ok", True):
            flags.append(("② 식 불성립", ", ".join(calc.get("failed") or [])))

        prov = critic.verify_number_provenance(answer, evidence_of(qid))
        if not prov.get("ok", True):
            flags.append(("③ 근거 밖 수치", ", ".join(prov.get("failed") or [])))

        for result in reasoning.check_all(answer):
            label = {"self_negating": "⑦ 자기부정",
                     "out_of_order": "⑥ 순서 오류"}[result["kind"]]
            flags.append((label, result["why"]))

        for name, _why in flags:
            hits[name] += 1
        if flags:
            detail.append((qid, track, flags))

    total = len(rows)
    clean = total - len(detail)
    print("골든셋 %d문항 · 「추론 논리성」 검사 다섯 (커버리지 5/7)" % total)
    print()
    print("  결함 없음  %d/%d (%.1f%%)" % (clean, total, 100 * clean / total))
    print("  결함 있음  %d문항" % len(detail))
    print()
    for name in ("① 수치 자기정합", "② 식 불성립", "③ 근거 밖 수치", "⑥ 순서 오류", "⑦ 자기부정"):
        print("    %-16s %d건" % (name, hits[name]))
    print()
    for qid, track, flags in detail:
        print("  🔴 %-7s %-11s %s" % (qid, track,
                                     " | ".join(f"{n}: {w[:60]}" for n, w in flags)))

    out = ROOT / args.out
    io.open(out, "w", encoding="utf-8").write(json.dumps(
        {"베이스라인": args.arm, "정본": args.run, "n": total, "결함 없음": clean,
         "🔴 이 수를 읽는 법": "우리가 보는 양식(5/7) 안에서 결함이 없다는 "
                          "뜻이다. 전제 누락·비약은 이 수에 없다.",
         "양식별": dict(hits),
         "문항": [{"question_id": q, "track": t,
                 "결함": [{"양식": n, "무엇": w} for n, w in f]}
                for q, t, f in detail]}, ensure_ascii=False, indent=2) + "\n")
    print("\n저장:", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
