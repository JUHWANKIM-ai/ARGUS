"""Per-point judging for open answers.

A single `covers_key_points` boolean let an answer covering three of six
golden points score as correct. The judge now returns one verdict per point
and the property takes a strict majority over that array.
See docs/open-issues.md item 8.
"""
import json

from argus.agent import critic, prompts


def _verdict(covered, grounded=True, no_fabrication=True, reason="ok"):
    return critic.OpenVerdict(tuple(covered), grounded, no_fabrication, reason)


def test_six_points_need_four_to_pass():
    """Strict majority: three of six is not a pass, four is. This is the
    Q-106 shape, which scored as correct while missing the 68.11% -> 50.06%
    collapse the question was about."""
    assert _verdict([True, True, True, False, False, False]).covers_key_points is False
    assert _verdict([True, True, True, True, False, False]).covers_key_points is True


def test_five_points_need_three_to_pass():
    """The Q-117 and Q-112 shape."""
    assert _verdict([True, True, False, False, False]).covers_key_points is False
    assert _verdict([True, True, True, False, False]).covers_key_points is True


def test_a_tie_fails_because_the_rule_is_strict_majority():
    """Two points with one covered is a tie, not a majority."""
    assert _verdict([True, False]).covers_key_points is False
    assert _verdict([True, True]).covers_key_points is True


def test_no_points_fails():
    """An open question with no golden points cannot be scored, so it does
    not pass. Every open question in the current set carries points, so this
    path does not fire in measurement."""
    assert _verdict([]).covers_key_points is False


def test_correct_is_still_the_and_of_three_axes():
    assert _verdict([True, True, True]).correct is True
    assert _verdict([True, True, True], grounded=False).correct is False
    assert _verdict([True, True, True], no_fabrication=False).correct is False
    assert _verdict([True, False, False]).correct is False


def _stub_chat(monkeypatch, text):
    class _Result:
        def __init__(self):
            self.text = text
            self.prompt_tokens = 1
            self.completion_tokens = 1

    monkeypatch.setattr(critic, "chat", lambda *a, **k: _Result())


def test_judge_open_carries_the_array_through(monkeypatch):
    _stub_chat(monkeypatch, json.dumps(
        {"covered": [1, 0, 1], "grounded": 1, "no_fabrication": 1, "reason": "r"}))
    out = critic.judge_open("q", "e", ["a", "b", "c"], "answer")
    assert out.covered == (True, False, True)
    assert out.covers_key_points is True
    assert out.reason == "r"


def test_judge_open_rejects_a_length_mismatch(monkeypatch):
    """A short array means the judge did not count the points. Padding it
    with False would turn a scoring failure into a wrong answer."""
    _stub_chat(monkeypatch, json.dumps(
        {"covered": [1, 1], "grounded": 1, "no_fabrication": 1, "reason": "r"}))
    out = critic.judge_open("q", "e", ["a", "b", "c"], "answer")
    assert out.covered == ()
    assert out.correct is False
    assert "길이" in out.reason


def test_judge_open_rejects_a_missing_array(monkeypatch):
    _stub_chat(monkeypatch, json.dumps(
        {"grounded": 1, "no_fabrication": 1, "reason": "r"}))
    out = critic.judge_open("q", "e", ["a"], "answer")
    assert out.covered == ()
    assert out.correct is False


def test_judge_open_still_reports_a_parse_failure(monkeypatch):
    _stub_chat(monkeypatch, "not json at all")
    out = critic.judge_open("q", "e", ["a"], "answer")
    assert out.correct is False
    assert "파싱 실패" in out.reason


def test_the_judge_prompt_numbers_the_points_and_asks_for_an_array():
    messages = prompts.build_open_judge_messages("q", "evidence", ["첫째", "둘째"], "a")
    system, user = messages[0]["content"], messages[1]["content"]
    assert "covered" in system
    assert "1. 첫째" in user and "2. 둘째" in user


def test_the_generation_prompt_is_untouched():
    """Only the scorer changes. The generation rules are what the ablation
    measures, so they stay frozen."""
    assert "공시 분석 전문가" in prompts._BASE_RULES
    assert "covered" not in prompts._BASE_RULES
