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


def test_answer_requires_params():
    assert client.get("/answer").status_code == 422


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
