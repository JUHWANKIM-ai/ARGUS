"""한 arm 이 틀린 문항을 질문·답변·원인과 함께 한 파일로 모은다. LLM 0회 · 0원.

🔴 **원인을 지어내지 않는다.** 저장소의 실패 귀속 기계(`scripts/error_analysis.py` 의
`attribute`·`root_cause`)가 산출물에 적어 둔 `first_failure`·`root_cause`·`failure_why`
를 그대로 옮기고, 사람이 읽을 한 줄만 그 값에서 파생한다. 단계 이름의 뜻은
`docs/pipeline/08-error-analysis.md` 의 정의를 옮긴 것이다.

    python scripts/build_failures.py --arm A34 --run artifacts/ablation/v5/final2 \
        --out artifacts/audit/baseline_failures_v5.json

`--run` 은 코호트별 폴더의 **공통 접두**다 (`<run>_full` · `<run>_valuechain` ·
`<run>_safety`). 러너가 같은 `--out` 에 두 코호트를 쓰면 뒤엣것이 앞엣것을 덮으므로
(v5 에서 실제로 한 번 겪었다) 코호트마다 폴더를 나눠 두는 것이 정본 형태다.
"""
from __future__ import annotations

import argparse
import collections
import io
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
COHORTS = ("full", "valuechain", "safety")

#: 실패 단계의 뜻. `docs/pipeline/08-error-analysis.md` 의 정의를 그대로 옮긴다.
STAGE = {
    "ROUTING": "라우팅 — 질의를 잘못 갈랐다",
    "RETRIEVAL": "검색 — 골든 근거가 후보에 아예 없었다",
    "RANKING": "재순위 — 후보에는 있었는데 근거 칸에 못 실렸다",
    "TOOL": "계산·전사 — 재계산과 답변의 수치가 어긋났다",
    "GENERATION": "생성 — 골든 근거가 프롬프트에 실렸는데 답이 틀렸다",
    "VERIFY": "검증 — critic 이 오답을 통과시켰고, 대조할 골든 수치가 없어 "
              "생성 단계로는 가를 수 없다",
    "HALLUCINATION": "지어냄 — 답할 수 없는데 답했다 (데이터 상한이 아니라 진짜 실패)",
    "DATA_LIMIT": "데이터 상한 — 우리 시스템의 실패가 아니다",
    "UNKNOWN": "미귀속",
}


def load_questions() -> dict[str, dict]:
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--arm", required=True)
    parser.add_argument("--run", required=True,
                        help="코호트별 폴더의 공통 접두 (예: artifacts/ablation/v5/final2)")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    questions = load_questions()
    failures: list[dict] = []
    totals: dict[str, dict] = {}
    sources: dict[str, str] = {}

    for cohort in COHORTS:
        path = ROOT / f"{args.run}_{cohort}" / args.arm / "report.json"
        if not path.exists():
            continue
        sources[cohort] = str(path.relative_to(ROOT)).replace("\\", "/")
        rows = json.load(io.open(path, encoding="utf-8"))["rows"]
        totals[cohort] = {"n": len(rows),
                          "correct": sum(1 for r in rows if r.get("correct"))}
        for row in rows:
            if row.get("correct"):
                continue
            case = questions.get(row["question_id"], {})
            stage = row.get("first_failure") or "UNKNOWN"
            failures.append({
                "question_id": row["question_id"],
                "cohort": cohort,
                "track": row.get("track"),
                "difficulty": row.get("difficulty"),
                "answer_mode": row.get("answer_mode"),
                "corp_name": row.get("corp_name"),
                "question": case.get("question"),
                "golden": case.get("answer"),
                "golden_key_points": case.get("answer_key_points"),
                "golden_evidence": [e.get("rcept_no")
                                    for e in (case.get("evidence") or [])],
                "our_answer": row.get("answer"),
                "cause": {"stage": stage,
                          "stage_meaning": STAGE.get(stage, stage),
                          "root_cause": row.get("root_cause"),
                          "why": row.get("failure_why")},
                "evidence_facts": {
                    "골든 근거가 후보에 있었나": row.get("golden_in_candidates"),
                    "골든 근거가 프롬프트에 실렸나": row.get("golden_in_evidence"),
                    "골든 값이 근거 안에 있었나": row.get("gold_value_in_evidence"),
                    "골든 순위": row.get("golden_rank")},
                "gate_facts": {
                    "critic 이 통과시켰나": row.get("critic_supported"),
                    "critic 이 오답을 통과시켰나": row.get("critic_passed_wrong_answer"),
                    "근거 표기가 있었나": row.get("has_citation"),
                    "수치 자기일관성": row.get("numeric_ok"),
                    "수치 실패 축": row.get("numeric_failed_kinds"),
                    "금액 커버리지": row.get("covers_key_amounts_ok"),
                    "금액 커버리지 사유": row.get("covers_key_amounts_reason"),
                    "judge 판정": row.get("judge")},
                "cost": {"llm_calls": row.get("n_llm_calls"),
                         "prompt_tokens": row.get("prompt_tokens"),
                         "completion_tokens": row.get("completion_tokens")},
                "think_trace": row.get("think_trace")})

    doc = {
        "베이스라인": args.arm,
        "설명": ("이 arm 이 틀린 문항 전량. 🔴 원인은 지어낸 것이 아니라 저장소의 "
                 "실패 귀속 기계가 산출물에 적은 first_failure·root_cause·failure_why "
                 "를 그대로 옮긴 것이다."),
        "정본": sources,
        "성적": totals,
        "실패단계_뜻": STAGE,
        "오답": sorted(failures, key=lambda f: (f["cohort"], f["question_id"]))}

    out_path = ROOT / args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    io.open(out_path, "w", encoding="utf-8").write(
        json.dumps(doc, ensure_ascii=False, indent=2) + "\n")

    print("저장:", out_path)
    print("오답 %d건 · 코호트별 %s" % (
        len(failures), dict(collections.Counter(f["cohort"] for f in failures))))
    print("단계별:", dict(collections.Counter(f["cause"]["stage"] for f in failures)))
    missing = [f["question_id"] for f in failures if not f["question"]]
    print("🔴 질문을 못 붙인 것:", missing or "없음")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
