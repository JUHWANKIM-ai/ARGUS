from fastapi.testclient import TestClient

from argus.api.server import app

client = TestClient(app)


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
