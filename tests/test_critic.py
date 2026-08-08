from argus.agent import critic


def _fake_chat(result_json):
    class R:
        text = result_json
        prompt_tokens = 100
        completion_tokens = 30
    return lambda *a, **k: R()


def test_quick_check_is_free_and_detects_missing_citation():
    assert critic.quick_check("매출액은 333조원입니다")["has_citation"] is False


def test_quick_check_accepts_valid_citation():
    text = "매출액은 333조원입니다 (근거: 사업보고서 2025.12, 접수번호 20260310002820, III-1)"
    assert critic.quick_check(text)["has_citation"] is True


def test_judge_parses_json(monkeypatch):
    monkeypatch.setattr(critic, "chat", _fake_chat(
        '{"supported": true, "has_citation": true, "hallucination": false, "reason": "일치"}'))
    v = critic.judge("근거", "답변")
    assert v.supported and v.passed


def test_judge_fails_on_hallucination(monkeypatch):
    monkeypatch.setattr(critic, "chat", _fake_chat(
        '{"supported": false, "has_citation": true, "hallucination": true, "reason": "근거 없음"}'))
    assert critic.judge("근거", "답변").passed is False


def test_judge_survives_malformed_json(monkeypatch):
    monkeypatch.setattr(critic, "chat", _fake_chat("판정 불가"))
    v = critic.judge("근거", "답변")
    assert v.passed is False and "파싱" in v.reason
