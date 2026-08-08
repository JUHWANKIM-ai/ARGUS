"""E2E 평가 하네스. 질의 -> 답변 -> 근거 표기까지 관통해 본다."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

REQUIRED_KEYS = {"question_id", "question", "retrieved_context", "think_trace", "answer"}


def load_cases() -> list[dict]:
    path = ROOT / "evalset" / "questions.jsonl"
    with path.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


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
        "total_llm_calls": sum(r.get("llm_calls", 0) for r in rows),
        "avg_llm_calls_per_question": sum(r.get("llm_calls", 0) for r in rows) / n,
        "total_prompt_tokens": sum(r.get("prompt_tokens", 0) for r in rows),
        "total_completion_tokens": sum(r.get("completion_tokens", 0) for r in rows),
    }


def main() -> None:
    from fastapi.testclient import TestClient

    from argus.api.server import app
    from argus.clova import CALL_LOG

    client = TestClient(app)
    rows = []
    for case in load_cases():
        CALL_LOG.clear()
        resp = client.get("/answer", params={"question_id": case["question_id"],
                                             "question": case["question"]})
        body = resp.json() if resp.status_code == 200 else {}
        totals = CALL_LOG.totals()
        rows.append({"case": case["question_id"], "type": case["type"],
                     **score(body, case),
                     "llm_calls": totals["calls"],
                     "prompt_tokens": totals["prompt_tokens"],
                     "completion_tokens": totals["completion_tokens"]})

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
