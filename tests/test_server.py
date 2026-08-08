import pytest
from fastapi.testclient import TestClient

from argus import clova
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
