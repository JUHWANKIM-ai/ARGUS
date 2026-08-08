"""평가용 API. 응답 스키마는 주최 측 고정 계약이다 (CONVENTION §4)."""
from __future__ import annotations

from fastapi import FastAPI, Query
from pydantic import BaseModel

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
    # TODO(Task 12+): 라우팅 -> 검색 -> critic -> HCX 생성으로 교체
    return AnswerResponse(
        question_id=question_id,
        question=question,
        retrieved_context="",
        think_trace="skeleton",
        answer="아직 구현되지 않았습니다.",
    )
