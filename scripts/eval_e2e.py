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


def main() -> None:
    from fastapi.testclient import TestClient

    from argus.api.server import app

    client = TestClient(app)
    cases = load_cases()
    rows = []
    for case in cases:
        resp = client.get(
            "/answer",
            params={"question_id": case["question_id"], "question": case["question"]},
        )
        body = resp.json() if resp.status_code == 200 else {}
        rows.append({"case": case["question_id"], "type": case["type"], **score(body, case)})

    out = ROOT / "artifacts" / "eval_report.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")

    total = len(rows)
    print(f"{'case':<8}{'type':<16}{'schema':>8}{'answer':>8}{'cite':>7}")
    for row in rows:
        print(f"{row['case']:<8}{row['type']:<16}{str(row['has_schema']):>8}"
              f"{str(row['answer_match']):>8}{str(row['has_citation']):>7}")
    print(f"\n스키마 준수 {sum(r['has_schema'] for r in rows)}/{total} · "
          f"정답 일치 {sum(r['answer_match'] for r in rows)}/{total} · "
          f"근거 표기 {sum(r['has_citation'] for r in rows)}/{total}")


if __name__ == "__main__":
    main()
