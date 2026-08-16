"""골든셋 전수 리뷰 — 문항마다 **답변·판정 근거·규약 지표 8종**을 한 파일로 모은다.

    PYTHONIOENCODING=utf-8 python scripts/build_goldenset_review.py \
        --arm A34 --run artifacts/ablation/v5/final2 \
        --out artifacts/audit/goldenset_review_A34.json

LLM 0회 · 0원. 러너가 이미 낸 `report.json`·`responses/` 를 읽어 재조립할 뿐이다.

🔴 **판정 근거를 지어내지 않는다.** `scripts/error_analysis.py` 의 채점 계약을 그대로 읽는다:

    open   correct = judge.correct AND numeric.ok AND coverage.ok
    closed correct = score_closed(EM 계열)      · safety 트랙은 score_safety
    (`error_analysis.py:1504-1513`)

🔴 **규약 지표 8종 중 우리가 직접 재는 것과 못 재는 것을 갈라 적는다.** 근사치를
"측정했다" 라고 쓰면 그 순간 이 파일이 거짓이 된다. `measured=False` 인 칸이 그 표시다.
"""
from __future__ import annotations

import argparse
import collections
import io
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
COHORTS = ("full", "valuechain", "safety")

#: 규약 §5 「답변 평가지표 8종」. 값은 (우리가 읽는 필드, 직접 측정인가, 한 줄 설명).
#:
#: 🔴 `추론 논리성` 은 **직접 재지 않는다.** judge 의 `grounded` 가 가장 가깝지만 그것은
#: *"근거에 서 있는가"* 이지 *"추론이 논리적인가"* 가 아니다. 근사로 채우면 8/8 이라는
#: 수가 거짓이 되므로 `measured=False` 로 남기고 대리 지표만 밝힌다.
METRICS = {
    "정확성": ("numeric_ok", True,
             "수치 자기일관성 + closed EM/open judge — 답변의 수가 근거·자기와 맞는가"),
    "근거 완전성": ("golden_recall", True,
                "골든 근거 중 프롬프트에 실린 비율"),
    "요구사항 충족": ("covers_key_amounts_ok", True,
                 "골든 요점이 금액을 명시하는데 답변이 침묵하지 않았는가 + judge covers_key_points"),
    "근거 기반": ("no_fabrication", True,
              "judge no_fabrication — Context 밖 내용을 사실처럼 쓰지 않았는가"),
    "추론 논리성": ("grounded", False,
               "🔴 직접 측정 없음. judge grounded 를 대리로 적되 같은 축이 아니다"),
    "안전성·신뢰성": ("safety_track", True,
                 "안전성 트랙 12문항 + mask_pii"),
    "정보한계 대응": ("info_limit_track", True,
                 "정보한계 트랙 6문항 + 거절 갈래 5종"),
    "근거 표시": ("has_citation", True,
              "답변에 근거 공시 표기가 있는가"),
}

#: 규약 §6 「질의 유형 (참고용 예시 6종)」 = task_type × answer_mode.
TASK_LABEL = {"search_extract": "검색·정보 추출",
              "multi_compare": "다중 조회·비교·연산",
              "complex_reason": "복합 문서 추론"}


def load_cases() -> dict[str, dict]:
    """🔴 `heldout.jsonl` 은 열지 않는다 — 최종 구성 1회용이고 2회 소진됐다."""
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


def why_correct(row: dict) -> dict:
    """**무엇이 이 판정을 냈는가.** 채점 계약을 그대로 옮긴다."""
    axes = set(str(row.get("scored_by") or "").split("+")) - {""}
    mode = row.get("answer_mode")
    judge = row.get("judge") or {}
    if mode == "open":
        passed = {
            "judge": judge.get("covers_key_points") is not False and bool(judge),
            "수치 자기일관성": row.get("numeric_ok"),
            "금액 커버리지": row.get("covers_key_amounts_ok"),
        }
        rule = "open — judge · 수치 자기일관성 · 금액 커버리지 **셋 다** 통과해야 정답"
    else:
        passed = {("안전성 채점" if row.get("track") == "safety" else "closed EM"): True}
        rule = ("safety — 거절 갈래 판정" if row.get("track") == "safety"
                else "closed — 골든과 완전일치 계열(EM)")
    return {"채점 규칙": rule,
            "적용된 축": sorted(axes) or ["judge"],
            "축별 통과": passed,
            "judge 세 축": judge or None,
            "판정": "정답" if row.get("correct") else "오답",
            "오답 사유": None if row.get("correct") else {
                "단계": row.get("first_failure"),
                "근본 원인": row.get("root_cause"),
                "설명": row.get("failure_why")}}


def metric_view(row: dict, case: dict) -> dict:
    """이 문항이 규약 지표 8종에서 어떻게 서는가. **못 재는 칸은 그렇게 적는다.**"""
    golden = [e.get("rcept_no") for e in (case.get("evidence") or [])
              if e.get("rcept_no")]
    loaded = row.get("golden_in_evidence") or []
    judge = row.get("judge") or {}
    track = row.get("track")
    out = {}
    for name, (field, measured, note) in METRICS.items():
        if name == "근거 완전성":
            value = (f"{len(loaded)}/{len(golden)}" if golden else "골든 근거 없음")
        elif name == "안전성·신뢰성":
            value = ("이 문항이 그 축이다" if track == "safety" else "해당 없음")
        elif name == "정보한계 대응":
            value = ("이 문항이 그 축이다" if track == "info_limit" else "해당 없음")
        elif name in ("근거 기반", "추론 논리성"):
            value = judge.get(field) if judge else "judge 미적용(closed)"
        else:
            value = row.get(field)
        out[name] = {"값": value, "직접 측정": measured, "무엇으로": note}
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--arm", default="A34")
    parser.add_argument("--run", default="artifacts/ablation/v5/final2")
    parser.add_argument("--out", default="artifacts/audit/goldenset_review_A34.json")
    args = parser.parse_args()

    cases = load_cases()
    items, totals, sources = [], {}, {}
    for cohort in COHORTS:
        base = ROOT / f"{args.run}_{cohort}" / args.arm
        report = base / "report.json"
        if not report.exists():
            continue
        sources[cohort] = str(report.relative_to(ROOT)).replace("\\", "/")
        rows = json.load(io.open(report, encoding="utf-8"))["rows"]
        totals[cohort] = {"n": len(rows),
                          "correct": sum(1 for r in rows if r.get("correct"))}
        for row in rows:
            case = cases.get(row["question_id"], {})
            resp = base / "responses" / f'{row["question_id"]}.json'
            payload = (json.load(io.open(resp, encoding="utf-8")).get("response") or {}
                       if resp.exists() else {})
            items.append({
                "question_id": row["question_id"],
                "cohort": cohort,
                "track": row.get("track"),
                "규약_질의유형": {
                    "task": TASK_LABEL.get(row.get("task_type") or "",
                                           row.get("task_type")),
                    "mode": row.get("answer_mode"),
                    "난이도": row.get("difficulty")},
                "doc_group": row.get("doc_group"),
                "question": case.get("question"),
                "golden": {"answer": case.get("answer") or "",
                           "key_points": case.get("answer_key_points"),
                           "evidence": [e.get("rcept_no")
                                        for e in (case.get("evidence") or [])]},
                "our_answer": row.get("answer"),
                "판정_근거": why_correct(row),
                "규약_지표8종": metric_view(row, case),
                "비용": {"llm_calls": row.get("n_llm_calls"),
                        "prompt_tokens": row.get("prompt_tokens"),
                        "completion_tokens": row.get("completion_tokens"),
                        "latency_s": row.get("latency_s")},
                "think_trace": row.get("think_trace"),
                "retrieved_context_chars": len(payload.get("retrieved_context") or "")})

    # --- 규약 §6 여섯 유형 커버리지 ------------------------------------------
    grid = collections.Counter()
    grid_ok = collections.Counter()
    for it in items:
        key = (it["규약_질의유형"]["task"], it["규약_질의유형"]["mode"])
        if None in key or "?" in str(key):
            key = ("(트랙 전용 — 안전성·정보한계)", it["규약_질의유형"]["mode"])
        grid[key] += 1
        if it["판정_근거"]["판정"] == "정답":
            grid_ok[key] += 1
    coverage = [{"task": k[0], "mode": k[1], "문항": grid[k], "정답": grid_ok[k]}
                for k in sorted(grid, key=lambda x: (str(x[0]), str(x[1])))]

    doc = {
        "생성": "scripts/build_goldenset_review.py",
        "베이스라인": args.arm,
        "정본": sources,
        "성적": totals,
        "채점_계약": {
            "open": "judge.correct AND numeric.ok AND coverage.ok "
                    "(error_analysis.py:1504-1506)",
            "closed": "score_closed — 골든과 완전일치 계열",
            "safety": "score_safety — 거절 갈래 판정",
            "🔴 주의": "judge 는 LLM 판정이다. 3회 반복 σ 0.577 · 문항 일치 37/38."},
        "규약_지표8종_정의": {k: {"우리가 읽는 값": v[0], "직접 측정": v[1], "설명": v[2]}
                       for k, v in METRICS.items()},
        "규약_질의유형_커버리지": coverage,
        "문항": sorted(items, key=lambda i: (i["cohort"], i["question_id"]))}

    out_path = ROOT / args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    io.open(out_path, "w", encoding="utf-8").write(
        json.dumps(doc, ensure_ascii=False, indent=2) + "\n")

    print("저장:", out_path)
    print("문항 %d · 성적 %s" % (len(items), totals))
    print("\n규약 §6 질의 유형 커버리지:")
    for c in coverage:
        print("   %-24s %-8s %2d문항 · 정답 %2d" % (c["task"], c["mode"],
                                                c["문항"], c["정답"]))
    missing = [k for k, v in METRICS.items() if not v[1]]
    print("\n🔴 직접 측정하지 않는 지표:", missing or "없음")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
