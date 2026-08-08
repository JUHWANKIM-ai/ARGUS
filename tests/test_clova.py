from argus import clova


def _fake_post(url, headers, body, timeout=60):
    assert "maxTokens" not in body or "tools" not in body, "tools와 maxTokens 동시 전송 금지"
    return 200, {
        "status": {"code": "20000"},
        "result": {
            "message": {"role": "assistant", "content": "답변"},
            "finishReason": "stop",
            "usage": {"promptTokens": 11, "completionTokens": 7, "totalTokens": 18},
        },
    }, 0.5


def test_chat_parses_usage(monkeypatch):
    monkeypatch.setattr(clova, "_post", _fake_post)
    clova.CALL_LOG.clear()
    r = clova.chat([{"role": "user", "content": "안녕"}])
    assert r.text == "답변"
    assert r.prompt_tokens == 11
    assert r.completion_tokens == 7


def test_tools_and_max_tokens_are_exclusive(monkeypatch):
    monkeypatch.setattr(clova, "_post", _fake_post)
    clova.CALL_LOG.clear()
    clova.chat([{"role": "user", "content": "안녕"}], tools=[{"type": "function"}])


def test_call_log_accumulates_totals(monkeypatch):
    monkeypatch.setattr(clova, "_post", _fake_post)
    clova.CALL_LOG.clear()
    clova.chat([{"role": "user", "content": "1"}])
    clova.chat([{"role": "user", "content": "2"}])
    totals = clova.CALL_LOG.totals()
    assert totals["calls"] == 2
    assert totals["prompt_tokens"] == 22
    assert totals["completion_tokens"] == 14
