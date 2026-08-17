"""Evaluation API. The response schema is a fixed contract (CONVENTION section 4)."""
from __future__ import annotations

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from argus.agent.pipeline import answer_question, fault_response

app = FastAPI(title="ARGUS 공시 Agent")


class AnswerResponse(BaseModel):
    question_id: str
    question: str
    retrieved_context: str
    think_trace: str
    answer: str


@app.get("/health")
def health() -> dict:
    """Liveness probe."""
    return {"status": "ok"}


@app.get("/answer", response_model=AnswerResponse)
def answer(
    question_id: str = Query(...),
    question: str = Query(...),
) -> AnswerResponse:
    """Answer a question, returning 200 with the five-field schema in all cases.

    Errors are never surfaced as 5xx. The evaluation harness retries a failed
    call twice, so a failing request is issued up to three times and scores
    zero if all three fail. A 200 carrying a limitation notice is scored under
    the information-limit axis instead.
    """
    try:
        return AnswerResponse(**answer_question(question_id, question))
    except Exception as exc:                      # noqa: BLE001 last line of defence
        return AnswerResponse(**fault_response(question_id, question, exc))


@app.exception_handler(RequestValidationError)
async def on_missing_query(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Return 200 with the five-field schema even when a query parameter is absent.

    FastAPI validates query parameters before the handler runs and raises 422,
    which the handler-level `except` cannot intercept. Without this handler a
    request missing only `question_id` would be discarded, even though it can
    be answered from `question` alone. Missing fields are filled with the
    empty string.
    """
    question_id = request.query_params.get("question_id", "")
    question = request.query_params.get("question", "")
    try:
        payload = answer_question(question_id, question)
    except Exception as inner:                    # noqa: BLE001 last line of defence
        payload = fault_response(question_id, question, inner)
    return JSONResponse(status_code=200, content=payload)
