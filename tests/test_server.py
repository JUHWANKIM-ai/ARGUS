import pytest
from fastapi.testclient import TestClient

from argus import clova
from argus.agent import pipeline
from argus.api.server import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def stub_clova(monkeypatch):
    """/answer 가 pipeline 에 연결된 뒤로 이 테스트들은 실제 CLOVA 를 때린다.

    스키마·식별자 검증이 목적이므로 전송 계층만 가짜로 바꾼다 (단언은 그대로).
    """
    def _fake_post(url, headers, body, timeout=60):
        return 200, {
            "result": {
                "message": {"role": "assistant", "content": "테스트 답변"},
                "finishReason": "stop",
                "usage": {"promptTokens": 10, "completionTokens": 5, "totalTokens": 15},
            }
        }, 0.01

    monkeypatch.setattr(clova, "_post", _fake_post)


def test_health():
    assert client.get("/health").json() == {"status": "ok"}


def test_answer_returns_exact_schema_keys():
    resp = client.get("/answer", params={"question_id": "Q-001", "question": "테스트"})
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"question_id", "question", "retrieved_context", "think_trace", "answer"}


def test_answer_echoes_identifiers():
    body = client.get("/answer", params={"question_id": "Q-777", "question": "삼성전자"}).json()
    assert body["question_id"] == "Q-777"
    assert body["question"] == "삼성전자"


# --- 쿼리 누락 (규약 감사 F-4) ------------------------------------------------
# 🔴 **종전에는 422 를 단언했다.** FastAPI 의 쿼리 검증이 핸들러보다 먼저 돌아 `answer()`
# 의 `except Exception` 에 닿지 않기 때문이고, 그것을 사실대로 적은 테스트였다.
# 규약 감사(2026-08-16)에서 **그게 손해라는 것**이 드러나 계약을 바꿨다 — `question` 이
# 왔는데 `question_id` 만 빠진 요청은 답할 수 있는데 422 는 그것을 버린다. 422 는 5xx 가
# 아니라 재시도(주최측 답변 #3)도 안 붙으므로 버리면 끝이다.


def test_missing_params_still_returns_200_and_schema():
    resp = client.get("/answer")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == _SCHEMA
    assert all(isinstance(v, str) for v in body.values())        # 주최측 #15


def test_missing_question_id_still_answers_the_question():
    """🔴 이 한 건이 이 변경의 이유다 — 답할 수 있는 질의를 스스로 버리지 않는다."""
    resp = client.get("/answer", params={"question": "삼성전자 매출액"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["question"] == "삼성전자 매출액"     # 질의는 살아서 파이프라인에 닿는다
    assert body["question_id"] == ""                # 없는 것은 빈 문자열 (주최측 #15 허용)
    assert body["answer"]                           # 한계 고지든 답이든 비어 있지 않다


# --- 마지막 방어선 -------------------------------------------------------------
# 🔴 주최측 2026-08-12 답변 #3: 호출 에러 시 **재시도 2회**. 500 을 내면 같은 질의가 최대
# 3회 들어오고 세 번 다 실패하면 그 문항은 0점이다. 파이프라인은 생성·critic 의
# `RuntimeError` 만 잡으므로(§상류 장애 표식), 그 밖의 예외는 여기서 막아야 한다.

_SCHEMA = {"question_id", "question", "retrieved_context", "think_trace", "answer"}


@pytest.mark.parametrize("exc", [
    KeyError("evidence"),                 # 우리 버그 — RuntimeError 가 아니다
    TypeError("NoneType is not subscriptable"),
    ValueError("bad filter"),
])
def test_answer_never_returns_5xx_on_unexpected_exception(monkeypatch, exc):
    def _boom(question_id, question):
        raise exc

    monkeypatch.setattr("argus.api.server.answer_question", _boom)
    resp = client.get("/answer", params={"question_id": "Q-500", "question": "터진다"})

    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == _SCHEMA
    assert all(isinstance(v, str) for v in body.values())    # 주최측 #15
    assert body["question_id"] == "Q-500" and body["question"] == "터진다"
    assert body["answer"].startswith("공시에서 확인되지 않습니다")


def test_fault_response_is_marked_so_runners_do_not_cache_it(monkeypatch):
    """**삼키지 않는다** — `upstream_failure()` 가 되읽어야 오답으로 굳지 않는다."""
    monkeypatch.setattr("argus.api.server.answer_question",
                        lambda *a: (_ for _ in ()).throw(KeyError("evidence")))
    body = client.get("/answer", params={"question_id": "Q-500", "question": "x"}).json()

    failure = pipeline.upstream_failure(body["think_trace"])
    assert failure is not None
    assert failure["stage"] == pipeline.FAULT_STAGE      # 상류 장애(generate·critic)와 갈린다
    assert failure["retryable"] is True
    assert "KeyError" in body["think_trace"]             # 사람이 읽을 원인도 남는다
