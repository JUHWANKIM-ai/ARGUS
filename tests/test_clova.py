import pytest

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


# --- Rate-limit retry (429 only), see docs/design-rationale.md#chat-rate-limit -


def _success_payload(text: str = "답변", prompt: int = 11, completion: int = 7) -> dict:
    return {
        "status": {"code": "20000"},
        "result": {
            "message": {"role": "assistant", "content": text},
            "finishReason": "stop",
            "usage": {"promptTokens": prompt, "completionTokens": completion,
                      "totalTokens": prompt + completion},
        },
    }


def test_a_429_then_200_returns_the_success_and_takes_the_reset_wait(monkeypatch):
    """One 429 followed by a 200 still returns the answer, and the wait
    between attempts must come from the response's reset header."""
    waits: list[float] = []
    monkeypatch.setattr(clova, "_sleep", lambda s: waits.append(s))
    calls = {"n": 0}

    def fake(url, headers, body, timeout=60):
        calls["n"] += 1
        if calls["n"] == 1:
            return clova._PostResult(429, {"status": {"code": "42901"}}, 0.05,
                                      {"x-ratelimit-reset-requests": "5s"})
        return clova._PostResult(200, _success_payload(), 0.4, {})

    monkeypatch.setattr(clova, "_post", fake)
    clova.CALL_LOG.clear()

    r = clova.chat([{"role": "user", "content": "안녕"}])

    assert r.text == "답변"
    assert calls["n"] == 2
    assert waits == [5.0]
    assert clova.CALL_LOG.totals()["calls"] == 1        # one logical call, not two


def test_a_non_429_error_raises_immediately_and_is_not_retried(monkeypatch):
    waits: list[float] = []
    monkeypatch.setattr(clova, "_sleep", lambda s: waits.append(s))
    calls = {"n": 0}

    def fake(url, headers, body, timeout=60):
        calls["n"] += 1
        return 500, {"raw": "internal error"}, 0.1

    monkeypatch.setattr(clova, "_post", fake)
    clova.CALL_LOG.clear()

    with pytest.raises(RuntimeError):
        clova.chat([{"role": "user", "content": "안녕"}])

    assert calls["n"] == 1                              # no retry for a non-429 status
    assert waits == []
    assert clova.CALL_LOG.totals()["calls"] == 0


def test_exhausting_every_retry_attempt_raises(monkeypatch):
    waits: list[float] = []
    monkeypatch.setattr(clova, "_sleep", lambda s: waits.append(s))
    calls = {"n": 0}

    def fake(url, headers, body, timeout=60):
        calls["n"] += 1
        return 429, {"status": {"code": "42901"}}, 0.05

    monkeypatch.setattr(clova, "_post", fake)
    clova.CALL_LOG.clear()

    with pytest.raises(RuntimeError):
        clova.chat([{"role": "user", "content": "안녕"}])

    assert calls["n"] == clova._RETRY_ATTEMPTS
    assert clova.CALL_LOG.totals()["calls"] == 0        # never succeeded, nothing recorded


def test_wait_falls_back_to_the_fixed_value_when_the_header_is_absent(monkeypatch):
    waits: list[float] = []
    monkeypatch.setattr(clova, "_sleep", lambda s: waits.append(s))
    calls = {"n": 0}

    def fake(url, headers, body, timeout=60):
        calls["n"] += 1
        if calls["n"] == 1:
            return clova._PostResult(429, {}, 0.05, {})     # no reset header at all
        return clova._PostResult(200, _success_payload(), 0.4, {})

    monkeypatch.setattr(clova, "_post", fake)
    clova.CALL_LOG.clear()

    clova.chat([{"role": "user", "content": "안녕"}])

    assert waits == [clova._FALLBACK_WAIT_S]


def test_wait_falls_back_when_a_test_double_returns_a_plain_tuple_with_no_headers(monkeypatch):
    """A test double that returns a bare (status, payload, latency) tuple, the
    shape every existing caller of `_post` already uses, must not crash and
    must fall back to the fixed wait rather than reading `.headers`."""
    waits: list[float] = []
    monkeypatch.setattr(clova, "_sleep", lambda s: waits.append(s))
    calls = {"n": 0}

    def fake(url, headers, body, timeout=60):
        calls["n"] += 1
        if calls["n"] == 1:
            return 429, {"status": {"code": "42901"}}, 0.05
        return 200, _success_payload(), 0.4

    monkeypatch.setattr(clova, "_post", fake)
    clova.CALL_LOG.clear()

    clova.chat([{"role": "user", "content": "안녕"}])

    assert waits == [clova._FALLBACK_WAIT_S]


# --- Reset-header parsing ----------------------------------------------------


def test_reset_wait_reads_the_primary_header_name():
    assert clova._reset_wait_s({"x-ratelimit-reset-requests": "12"}) == 12.0


def test_reset_wait_accepts_a_seconds_suffix():
    assert clova._reset_wait_s({"x-ratelimit-reset-requests": "12s"}) == 12.0


def test_reset_wait_accepts_a_compound_duration():
    assert clova._reset_wait_s({"x-ratelimit-reset-requests": "1m5s"}) == 65.0


def test_reset_wait_falls_back_to_the_standard_retry_after_header():
    assert clova._reset_wait_s({"retry-after": "30"}) == 30.0


def test_reset_wait_is_case_insensitive():
    assert clova._reset_wait_s({"X-RateLimit-Reset-Requests": "9"}) == 9.0


def test_reset_wait_is_none_when_the_value_is_unparseable():
    assert clova._reset_wait_s({"x-ratelimit-reset-requests": "soon"}) is None


def test_reset_wait_is_none_when_no_header_is_present():
    assert clova._reset_wait_s({}) is None
