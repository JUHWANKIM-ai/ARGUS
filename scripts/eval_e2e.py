"""E2E 평가 하네스. 질의 -> 답변 -> 근거 표기까지 관통해 본다.

Phase H 입력용으로 문항별 기록을 넓혔다. 점수만 남기면 **왜 틀렸는지**를 다시 돌려야
알 수 있고, 재실행은 크레딧을 두 번 쓴다. think_trace 전문·호출별 토큰·지연·hop/스텝·
골든 근거 적중을 한 번에 남긴다 (귀속은 `scripts/error_analysis.py` 가 한다).

**dev 만 읽는다.** heldout 은 최종 구성에서 딱 1회 쓴다.
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

REQUIRED_KEYS = {"question_id", "question", "retrieved_context", "think_trace", "answer"}

_BUDGET_RE = re.compile(r"budget steps=(\d+) hops=(\d+) llm=(\d+) tokens=(\d+)")


def load_cases() -> list[dict]:
    path = ROOT / "evalset" / "questions.jsonl"
    with path.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def budget_used(think_trace: str) -> dict:
    """파이프라인이 실제로 쓴 스텝·hop. trace 끝에 항상 실려 있다 (게이트 F)."""
    match = _BUDGET_RE.search(think_trace or "")
    if not match:
        return {"steps": 0, "hops": 0, "llm": 0, "tokens": 0}
    return {"steps": int(match.group(1)), "hops": int(match.group(2)),
            "llm": int(match.group(3)), "tokens": int(match.group(4))}


def golden_in_evidence(case: dict, retrieved_context: str) -> dict:
    """골든 근거가 **프롬프트에 실렸는가**. 후보에 걸린 것과 다르다."""
    golden = [e["rcept_no"] for e in (case.get("evidence") or [])]
    present = [g for g in golden if g in (retrieved_context or "")]
    return {"n_golden": len(golden), "present": present,
            "any": bool(present), "all": bool(golden) and len(present) == len(golden)}


def _norm(text: str) -> str:
    return (text or "").replace(",", "").replace(" ", "")


def score(response: dict, case: dict) -> dict:
    has_schema = REQUIRED_KEYS <= set(response)
    answer = response.get("answer", "")
    gold = case.get("gold", "")
    must_cite = case.get("must_cite", "")
    return {
        "has_schema": has_schema,
        "answer_match": bool(gold) and _norm(gold) in _norm(answer),
        "has_citation": bool(must_cite) and must_cite in answer,
    }


def summarize(rows: list[dict]) -> dict:
    n = max(len(rows), 1)
    return {
        "cases": len(rows),
        "schema_ok": sum(r["has_schema"] for r in rows),
        "answer_match": sum(r["answer_match"] for r in rows),
        "has_citation": sum(r["has_citation"] for r in rows),
        "golden_in_evidence": sum(bool(r.get("golden", {}).get("any")) for r in rows),
        "total_llm_calls": sum(r.get("llm_calls", 0) for r in rows),
        "avg_llm_calls_per_question": sum(r.get("llm_calls", 0) for r in rows) / n,
        "total_prompt_tokens": sum(r.get("prompt_tokens", 0) for r in rows),
        "total_completion_tokens": sum(r.get("completion_tokens", 0) for r in rows),
        "total_hops": sum(r.get("hops", 0) for r in rows),
        "max_steps": max((r.get("steps", 0) for r in rows), default=0),
        "total_latency_s": round(sum(r.get("latency_s", 0.0) for r in rows), 1),
    }


def main() -> None:
    from fastapi.testclient import TestClient

    from argus.api.server import app
    from argus.clova import CALL_LOG

    client = TestClient(app)
    rows = []
    for case in load_cases():
        CALL_LOG.clear()
        started = time.time()
        resp = client.get("/answer", params={"question_id": case["question_id"],
                                             "question": case["question"]})
        latency = time.time() - started
        body = resp.json() if resp.status_code == 200 else {}
        totals = CALL_LOG.totals()
        trace = body.get("think_trace", "")
        used = budget_used(trace)
        rows.append({"case": case["question_id"], "type": case["type"],
                     **score(body, case),
                     "llm_calls": totals["calls"],
                     "prompt_tokens": totals["prompt_tokens"],
                     "completion_tokens": totals["completion_tokens"],
                     # --- Phase H 입력 ---
                     "answer": body.get("answer", ""),
                     "think_trace": trace,
                     "calls": [{"tag": r.tag, "prompt_tokens": r.prompt_tokens,
                                "completion_tokens": r.completion_tokens,
                                "latency_s": round(r.latency_s, 3), "finish": r.finish}
                               for r in CALL_LOG.records],
                     "hops": used["hops"], "steps": used["steps"],
                     "latency_s": round(latency, 3),
                     "golden": golden_in_evidence(case, body.get("retrieved_context", ""))})

    summary = summarize(rows)
    out = ROOT / "artifacts" / "eval_report.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({"rows": rows, "summary": summary},
                              ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"{'case':<8}{'type':<16}{'schema':>8}{'answer':>8}{'cite':>7}{'calls':>7}{'tok':>8}")
    for r in rows:
        print(f"{r['case']:<8}{r['type']:<16}{str(r['has_schema']):>8}"
              f"{str(r['answer_match']):>8}{str(r['has_citation']):>7}"
              f"{r['llm_calls']:>7}{r['prompt_tokens'] + r['completion_tokens']:>8}")
    print()
    for k, v in summary.items():
        print(f"  {k:<32} {v}")


if __name__ == "__main__":
    main()
