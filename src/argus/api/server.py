"""평가용 API. 응답 스키마는 주최 측 고정 계약이다 (CONVENTION §4)."""
from __future__ import annotations

from fastapi import FastAPI, Query
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
    return {"status": "ok"}


@app.get("/answer", response_model=AnswerResponse)
def answer(
    question_id: str = Query(...),
    question: str = Query(...),
) -> AnswerResponse:
    """🔴 **어떤 예외에도 200 + 5키를 낸다.** 사유는 `pipeline.fault_response` 독스트링.

    요약: 주최측은 호출 에러 시 **재시도 2회**다(2026-08-12 답변 #3). 500 을 내면 같은
    질의가 최대 3회 들어오고 세 번 다 실패하면 그 문항은 **0점**이며, 비결정적 실패면
    캐시를 못 타 **크레딧이 3배**다. 200 + 한계 고지는 정보한계대응 항목에 걸린다.
    """
    try:
        return AnswerResponse(**answer_question(question_id, question))
    except Exception as exc:                      # noqa: BLE001 — 마지막 방어선이다
        return AnswerResponse(**fault_response(question_id, question, exc))
