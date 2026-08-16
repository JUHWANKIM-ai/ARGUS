"""평가용 API. 응답 스키마는 주최 측 고정 계약이다 (CONVENTION §4)."""
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


@app.exception_handler(RequestValidationError)
async def on_missing_query(request: Request, exc: RequestValidationError) -> JSONResponse:
    """🔴 **쿼리가 빠져도 200 + 5키를 낸다.** `answer()` 의 `except` 로는 못 잡는다.

    FastAPI 의 쿼리 검증은 **핸들러보다 먼저** 돌아 `422` 를 던지므로 아래 핸들러가 없으면
    위의 `except Exception` 에 닿지 않는다. 규약 감사(2026-08-16 · `docs/compliance-audit`
    F-4)가 실측으로 잡은 구멍이다.

    🔴 **이득은 계약 일관성이 아니라 `question_id` 하나다.** `question` 이 왔는데
    `question_id` 만 빠진 요청은 **답할 수 있다.** 422 로 버리면 답할 수 있는 질의를
    스스로 0점으로 만든다. 그래서 있는 것으로 답하고 없는 것은 빈 문자열로 채운다.

    422 는 5xx 가 아니라 재시도(주최측 답변 #3)를 부르지 않으므로, 버리면 그걸로 끝이다.
    """
    question_id = request.query_params.get("question_id", "")
    question = request.query_params.get("question", "")
    try:
        payload = answer_question(question_id, question)
    except Exception as inner:                    # noqa: BLE001 — 여기도 마지막 방어선이다
        payload = fault_response(question_id, question, inner)
    return JSONResponse(status_code=200, content=payload)
