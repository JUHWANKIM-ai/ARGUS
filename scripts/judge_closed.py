"""closed 문항에 judge 를 **진단으로** 건다. 규약 「근거 기반」·「추론 논리성」의 사각.

    PYTHONIOENCODING=utf-8 python scripts/judge_closed.py \
        --arm A37 --run artifacts/ablation/v5/a37

**채점을 바꾸지 않는다.** closed 는 EM 으로 판정하고 그 판정은 그대로 둔다. 여기서
재는 것은 *"EM 이 맞다고 한 답을 judge 도 맞다고 하는가"* 뿐이다. 게이트로 만들면
정답률이 흔들려 v1~v5 시계열이 깨진다. **먼저 재고, 게이트는 그 수를 보고 정한다.**

## 왜 필요한가

골든셋 전수 검토(`docs/goldenset-review-2026-08-16.md` §3)가 사각을 찾았다:

    closed 69문항은 EM 채점이라 judge 를 안 거친다.
    → 「근거 기반」(환각)·「추론 논리성」을 **안 잰다.** 답이 맞으면 통과다.

`critic`·`verify` 가 돌아 완전한 사각은 아니지만, 규약 지표 여덟 중 둘이 69문항에서
비어 있다는 사실은 그대로다. 이 스크립트가 그 칸을 채운다.

**골든을 요점으로 넘긴다.** closed 의 골든은 값 하나(`answer`)라 `key_points` 가
비어 있다. 그대로 부르면 judge 가 대조할 것이 없어 판정이 무의미해진다.
"""
from __future__ import annotations

import argparse
import collections
import io
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

COHORTS = ("full", "valuechain", "safety")


def load_cases() -> dict[str, dict]:
    """`heldout.jsonl` 은 열지 않는다."""
    out: dict[str, dict] = {}
    for name in ("questions.jsonl", "valuechain.jsonl", "safety.jsonl",
                 "regression.jsonl"):
        path = ROOT / "evalset" / name
        if not path.exists():
            continue
        for line in io.open(path, encoding="utf-8"):
            row = json.loads(line)
            out[row["question_id"]] = row
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--arm", default="A37")
    parser.add_argument("--run", default="artifacts/ablation/v5/a37")
    parser.add_argument("--out", default="artifacts/audit/judge_closed_A37.json")
    args = parser.parse_args()

    from argus.agent.critic import judge_open

    cases = load_cases()
    rows: list[dict] = []
    for cohort in COHORTS:
        base = ROOT / f"{args.run}_{cohort}" / args.arm
        report = base / "report.json"
        if not report.exists():
            continue
        for row in json.load(io.open(report, encoding="utf-8"))["rows"]:
            if row.get("answer_mode") == "open":
                continue                       # 이미 judge 를 거친다
            case = cases.get(row["question_id"]) or {}
            resp = base / "responses" / f'{row["question_id"]}.json'
            if not resp.exists():
                continue
            payload = json.load(io.open(resp, encoding="utf-8")).get("response") or {}
            rows.append((row, case, payload))

    print("closed·트랙전용 %d문항에 judge 를 건다 (진단 · 채점 불변)" % len(rows))

    def judged(question, evidence, points, answer):
        """429(rate exceeded)에 물러섰다 다시 친다. 이미 한 호출은 `RequestCache`
        가 받아 두므로 재개는 공짜다. 여기서 죽으면 앞서 쓴 크레딧을 버린다."""
        import time

        for attempt in range(6):
            try:
                return judge_open(question, evidence, points, answer)
            except RuntimeError as exc:
                if "429" not in str(exc) or attempt == 5:
                    raise
                time.sleep(2 ** attempt)
        raise RuntimeError("unreachable")

    out, agree, flips = [], 0, []
    for row, case, payload in rows:
        golden = str(case.get("answer") or "").strip()
        points = case.get("answer_key_points") or ([golden] if golden else None)
        verdict = judged(case.get("question") or "",
                         payload.get("retrieved_context") or "",
                         points,
                         payload.get("answer") or "")
        judged_ok = (verdict.covers_key_points and verdict.grounded
                     and verdict.no_fabrication)
        em_ok = bool(row.get("correct"))
        if judged_ok == em_ok:
            agree += 1
        else:
            flips.append((row["question_id"], row.get("track"), em_ok, judged_ok,
                          verdict.reason[:80]))
        out.append({"question_id": row["question_id"], "track": row.get("track"),
                    "answer_mode": row.get("answer_mode"),
                    "EM": em_ok,
                    "judge": {"covers_key_points": verdict.covers_key_points,
                              "grounded": verdict.grounded,
                              "no_fabrication": verdict.no_fabrication,
                              "reason": verdict.reason},
                    "일치": judged_ok == em_ok})
        print("  %s %-7s EM=%-5s judge=%-5s %s"
              % ("  " if judged_ok == em_ok else "🔴", row["question_id"],
                 em_ok, judged_ok, verdict.reason[:56]))

    doc = {"베이스라인": args.arm, "성격": "진단 전용 — 채점을 바꾸지 않는다",
           "n": len(out), "EM 과 일치": agree,
           "일치율": round(agree / max(len(out), 1), 4),
           "갈린 문항": [{"question_id": q, "track": t, "EM": e, "judge": j,
                       "reason": r} for q, t, e, j, r in flips],
           "문항": out}
    path = ROOT / args.out
    path.parent.mkdir(parents=True, exist_ok=True)
    io.open(path, "w", encoding="utf-8").write(
        json.dumps(doc, ensure_ascii=False, indent=2) + "\n")

    print()
    print("일치 %d/%d (%.1f%%)" % (agree, len(out), 100 * agree / max(len(out), 1)))
    axes = collections.Counter()
    for item in out:
        for k, v in item["judge"].items():
            if v is False:
                axes[k] += 1
    print("judge 가 떨어뜨린 축:", dict(axes) or "없음")
    print("저장:", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
