"""골든셋 전수에 「추론 논리성」 검사 넷을 건다. LLM 0회 · 0원.

    PYTHONIOENCODING=utf-8 python scripts/score_reasoning.py

🔴 **커버리지가 3/7 이라는 사실을 함께 낸다.** 이 점수를 "「추론 논리성」 100%" 로
읽으면 안 된다 — 우리가 **보는 양식 안에서** 결함이 없다는 뜻이다. 안 보는 셋
(전제 누락 · 비약 · 순서 오류)은 이 수에 안 들어 있다.
기준과 시험은 `docs/reasoning-metric-2026-08-16.md`.
"""
from __future__ import annotations

import collections
import io
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

#: ⑦ 자기부정 — 거절 문구와 실답 금액이 한 답변에 함께 있는가. **후보 검사**다.
_REFUSE = ("확인되지 않습니다", "제공하지 않습니다", "제공해 드릴 수 없습니다",
           "판단을 생성하지 않습니다", "어느 기업을 말씀하시는지")
_MONEY = re.compile(r"\d{1,3}(?:,\d{3}){2,}\s*원")


def body_of(answer: str) -> str:
    """우리가 붙인 각주·근거 표기를 뗀 본문. 거기 실린 수는 모델의 주장이 아니다."""
    return re.split(r"\n*\((?:근거|주의)[:：]", answer or "")[0]


def main() -> int:
    from argus.agent import aggregate, critic

    rows = []
    review = ROOT / "artifacts/audit/goldenset_review_A38.json"
    for item in json.load(io.open(review, encoding="utf-8"))["문항"]:
        rows.append((item["question_id"], item["track"], item["our_answer"] or "",
                     item.get("retrieved_context_chars", 0)))

    #: 근거는 응답 파일에서 되찾는다 — 리뷰 JSON 은 길이만 들고 있다.
    def evidence_of(qid: str) -> str:
        for cohort in ("full", "valuechain", "safety"):
            path = (ROOT / f"artifacts/ablation/v5/a38_{cohort}/A38/responses"
                    / f"{qid}.json")
            if path.exists():
                return (json.load(io.open(path, encoding="utf-8")).get("response")
                        or {}).get("retrieved_context") or ""
        return ""

    hits = collections.Counter()
    detail = []
    for qid, track, answer, _chars in rows:
        body = body_of(answer)
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

        if any(k in body for k in _REFUSE) and _MONEY.search(body):
            flags.append(("⑦ 자기부정(후보)", _MONEY.search(body).group(0)))

        for name, _why in flags:
            hits[name] += 1
        if flags:
            detail.append((qid, track, flags))

    total = len(rows)
    clean = total - len(detail)
    print("골든셋 %d문항 · 「추론 논리성」 검사 넷 (커버리지 3/7 + 후보 1)" % total)
    print()
    print("  결함 없음  %d/%d (%.1f%%)" % (clean, total, 100 * clean / total))
    print("  결함 있음  %d문항" % len(detail))
    print()
    for name in ("① 수치 자기정합", "② 식 불성립", "③ 근거 밖 수치", "⑦ 자기부정(후보)"):
        print("    %-16s %d건" % (name, hits[name]))
    print()
    for qid, track, flags in detail:
        print("  🔴 %-7s %-11s %s" % (qid, track,
                                     " | ".join(f"{n}: {w[:60]}" for n, w in flags)))

    out = ROOT / "artifacts/audit/reasoning_score_A38.json"
    io.open(out, "w", encoding="utf-8").write(json.dumps(
        {"베이스라인": "A38", "n": total, "결함 없음": clean,
         "🔴 이 수를 읽는 법": "우리가 보는 양식(3/7 + 후보 1) 안에서 결함이 없다는 "
                          "뜻이다. 전제 누락·비약·순서 오류는 이 수에 없다.",
         "양식별": dict(hits),
         "문항": [{"question_id": q, "track": t,
                 "결함": [{"양식": n, "무엇": w} for n, w in f]}
                for q, t, f in detail]}, ensure_ascii=False, indent=2) + "\n")
    print("\n저장:", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
