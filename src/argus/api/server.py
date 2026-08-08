"""평가용 API. 응답 스키마는 주최 측 고정 계약이다 (CONVENTION §4)."""
from __future__ import annotations

from fastapi import FastAPI, Query
from pydantic import BaseModel

from argus.agent.pipeline import answer_question

app = FastAPI(title="ARGUS 공시 Agent")


class AnswerResponse(BaseModel):
    question_id: str
    question: str
    retrieved_context: str
    think_trace: str
    answer: str


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/answer", response_model=AnswerResponse)
def answer(
    question_id: str = Query(...),
    question: str = Query(...),
) -> AnswerResponse:
    return AnswerResponse(**answer_question(question_id, question))
