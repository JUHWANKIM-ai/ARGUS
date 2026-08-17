"""골든 근거를 **필수 / 참고**로 가른다. LLM 0회 · 0원.

    PYTHONIOENCODING=utf-8 python scripts/mark_required_evidence.py \
        --arm A38 --run artifacts/ablation/v5/a38

**평가셋 파일을 고치지 않는다.** 별도 주석 파일 하나만 낸다
(`artifacts/audit/golden_evidence_required.json`). 골든의 `evidence` 목록을 건드리면
`has_citation`(골든 접수번호 인용 여부)과 골든 회수의 v1~v5 시계열이 통째로 뜻이 바뀐다.

## 왜 필요한가

규약 §5 「근거 완전성」은 *"답변 도출에 **필수적인** 데이터를 검색 근거에 포함했는가"*
다. 그런데 우리 골든의 `evidence` 는 문항마다 뜻이 다르다. 어떤 것은 최소 집합이고
어떤 것은 관련 있는 것 전부다. 그대로 재면 우리 스스로를 깎는다.

## 판정 규칙, 문자열이 아니라 **실측**이다

첫 판은 *"레코드의 금액·계약상대·접수번호가 골든 문면에 나오면 필수"* 로 짰다.
**틀렸다.** 계산 문항은 골든이 **파생값**이라 어느 레코드의 금액도 문면에 없다.
`Q-109` 는 두 근거의 합이 골든인데 그 규칙은 둘 다 '참고' 로 내린다. 필수를 참고로
내리는 오류는 근거 완전성을 **부당하게 좋아 보이게** 만든다.

그래서 관측으로 가른다. 우리에겐 그 관측이 이미 있다:

    참고  = 프롬프트에 **안 실렸는데 그 문항이 정답**이었다 → 답에 필요하지 않았다
    필수  = 그 밖의 전부 (실린 것 · 안 실렸고 오답이었던 것)

**기본값이 '필수' 다.** 확신할 수 없으면 필수로 둔다. 모르는 것을 유리하게 세는
쪽이 이 저장소가 가장 경계하는 일이다.

**이 판정은 arm 에 매인다.** `A38` 이 안 실어도 답이 섰다는 사실이 다른 arm 에서도
참이라는 보장은 없다. 산출물에 arm 이름을 박아 두는 이유다.
"""
from __future__ import annotations

import argparse
import collections
import io
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
COHORTS = ("full", "valuechain", "safety")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--arm", default="A38")
    parser.add_argument("--run", default="artifacts/ablation/v5/a38")
    parser.add_argument("--out", default="artifacts/audit/golden_evidence_required.json")
    args = parser.parse_args()

    cases: dict[str, dict] = {}
    for name in ("questions.jsonl", "valuechain.jsonl", "safety.jsonl",
                 "regression.jsonl"):
        path = ROOT / "evalset" / name
        if not path.exists():
            continue
        for line in io.open(path, encoding="utf-8"):
            row = json.loads(line)
            cases[row["question_id"]] = row

    observed: dict[str, dict] = {}
    for cohort in COHORTS:
        report = ROOT / f"{args.run}_{cohort}" / args.arm / "report.json"
        if not report.exists():
            continue
        for row in json.load(io.open(report, encoding="utf-8"))["rows"]:
            observed[row["question_id"]] = row

    out, stat = {}, collections.Counter()
    for qid, case in sorted(cases.items()):
        evidence = [e.get("rcept_no") for e in (case.get("evidence") or [])
                    if e.get("rcept_no")]
        if not evidence:
            continue
        row = observed.get(qid) or {}
        loaded = set(row.get("golden_in_evidence") or [])
        correct = bool(row.get("correct"))

        marks = {}
        for rcept in evidence:
            if rcept in loaded:
                required, why = True, "프롬프트에 실렸다 — 없어도 되는지 안 재 봤다"
            elif correct:
                required, why = False, "안 실렸는데 정답이었다 — 답에 필요하지 않았다"
            else:
                required, why = True, "안 실렸고 오답이었다 — 필요했을 수 있다"
            marks[rcept] = {"required": required, "why": why}
            stat["필수" if required else "참고"] += 1
        out[qid] = {"track": case.get("track"), "correct": correct,
                    "evidence": marks,
                    "필수": sum(1 for m in marks.values() if m["required"]),
                    "전체": len(marks)}

    need = sum(v["필수"] for v in out.values())
    loaded_need = sum(
        1 for qid, v in out.items()
        for rc, m in v["evidence"].items()
        if m["required"] and rc in set((observed.get(qid) or {}).get(
            "golden_in_evidence") or []))

    doc = {"생성": "scripts/mark_required_evidence.py", "베이스라인": args.arm,
           "성격": "🔴 평가셋을 고치지 않는 별도 주석. evidence 목록은 그대로다",
           "규칙": ["참고 = 안 실렸는데 그 문항이 정답이었다 (답에 필요하지 않았다)",
                  "필수 = 그 밖의 전부",
                  "🔴 기본값이 필수 — 확신할 수 없으면 필수로 둔다",
                  "🔴 이 판정은 arm 에 매인다"],
           "집계": dict(stat),
           "근거 완전성(규약 정의)": {"필수 근거": need, "그중 적재": loaded_need,
                             "비율": round(loaded_need / max(need, 1), 4)},
           "문항": out}
    path = ROOT / args.out
    path.parent.mkdir(parents=True, exist_ok=True)
    io.open(path, "w", encoding="utf-8").write(
        json.dumps(doc, ensure_ascii=False, indent=2) + "\n")

    total = stat["필수"] + stat["참고"]
    print("골든 근거 %d건 · 필수 %d · 참고 %d" % (total, stat["필수"], stat["참고"]))
    print()
    print("근거 완전성")
    print("  종전 잣대 (골든 전건)   %d/%d = %.1f%%"
          % (loaded_need + sum(1 for v in out.values() for m in v["evidence"].values()
                               if not m["required"]) * 0, total,
             100 * (total - stat["참고"] - (need - loaded_need)) / total))
    print("  🔴 규약 정의 (필수만)    %d/%d = %.1f%%"
          % (loaded_need, need, 100 * loaded_need / max(need, 1)))
    print()
    print("참고로 내려간 근거 (안 실렸는데 정답이었다):")
    for qid, item in sorted(out.items()):
        extra = [rc for rc, m in item["evidence"].items() if not m["required"]]
        if extra:
            print("   %-7s %d건  %s" % (qid, len(extra), extra))
    print("\n저장:", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
