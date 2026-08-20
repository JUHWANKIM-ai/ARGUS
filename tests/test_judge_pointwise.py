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
        {"n": 3, "covered": [1, 0, 1], "grounded": 1, "no_fabrication": 1, "reason": "r"}))
    out = critic.judge_open("q", "e", ["a", "b", "c"], "answer")
    assert out.covered == (True, False, True)
    assert out.covers_key_points is True
    assert out.reason == "r"


def test_judge_open_rejects_a_length_mismatch(monkeypatch):
    """A short `covered` array means the judge did not count the points, even
    though `n` itself is right. Padding it with False would turn a scoring
    failure into a wrong answer.

    Both calls the retry can make return this same fixed response (`_stub_chat`
    is deterministic), so this also doubles as evidence that the systematic
    kind of mismatch survives the retry rather than being silently masked by
    it.
    """
    _stub_chat(monkeypatch, json.dumps(
        {"n": 3, "covered": [1, 1], "grounded": 1, "no_fabrication": 1, "reason": "r"}))
    out = critic.judge_open("q", "e", ["a", "b", "c"], "answer")
    assert out.covered == ()
    assert out.correct is False
    assert "길이" in out.reason


def test_judge_open_rejects_a_wrong_n_even_when_the_array_length_matches(monkeypatch):
    """`n` is validated independently of the `covered` array length. A judge
    that still miscounted internally but happened to land the array at the
    right length is exactly the failure mode `n` exists to catch -- the array
    length alone cannot see it."""
    _stub_chat(monkeypatch, json.dumps(
        {"n": 5, "covered": [1, 0, 1], "grounded": 1, "no_fabrication": 1, "reason": "r"}))
    out = critic.judge_open("q", "e", ["a", "b", "c"], "answer")
    assert out.covered == ()
    assert out.correct is False
    assert "길이" in out.reason


def test_judge_open_rejects_a_missing_array(monkeypatch):
    _stub_chat(monkeypatch, json.dumps(
        {"n": 1, "grounded": 1, "no_fabrication": 1, "reason": "r"}))
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


def test_the_point_count_reaches_the_prompt_as_a_literal():
    """The prompt used to state the length constraint only relationally
    ("배열의 길이는 요소 개수와 반드시 같아야 합니다") and never gave the actual
    number. The count now has to appear as a literal digit in the user
    message, and the system prompt has to point the judge at the `n` field it
    must commit to.

    The concrete worked example (with its own literal `n`) lives in the user
    message now, not the system prompt -- see the schema-example tests below
    for why, and for what proves that placement is actually correct. This
    test only checks that the system prompt still names the field
    abstractly: it references `` `n` `` (backtick-quoted, as a field name)
    rather than a JSON-literal `"n"`, since the system prompt no longer
    embeds a concrete instance at all."""
    messages = prompts.build_open_judge_messages(
        "q", "evidence", ["첫째", "둘째", "셋째"], "a")
    system, user = messages[0]["content"], messages[1]["content"]
    assert "요소 개수: 3" in user
    assert "`n`" in system


def test_the_generation_prompt_is_untouched():
    """Only the scorer changes. The generation rules are what the ablation
    measures, so they stay frozen."""
    assert "공시 분석 전문가" in prompts._BASE_RULES
    assert "covered" not in prompts._BASE_RULES


# --- The schema example is built per-call, not hardcoded ---------------------
# A second-round measurement (round 1, 43 open-question calls) found 6
# remaining length mismatches after the count-as-literal and commit-to-`n`
# fix above, all shaped identically: `n=2` with a two-element `covered` array,
# regardless of the question's real point count. That is the literal example
# this prompt used to hardcode (`{"n":2,"covered":[0,1],...}`), copied
# verbatim -- a few-shot echo distinct from the segmentation defect the first
# round targeted, and it showed up on a different set of questions than the
# nine that were systematically wrong before.
#
# The fix moves the worked example out of the frozen `SYSTEM_OPEN_JUDGE`
# constant and builds it from the real point count in `build_open_judge_messages`
# instead, so that copying it verbatim produces a correctly-shaped answer.

def _extract_example(user: str) -> dict:
    """Pull the `n`/`covered` schema example back out of a built user
    message, the same way a test that isn't allowed to guess at internal
    layout would: by looking for what the prompt actually says is there
    ("예시 응답 형식: ") rather than re-deriving the JSON independently."""
    marker = "예시 응답 형식: "
    assert marker in user, "no schema example found in the user message"
    line = user.split(marker, 1)[1].splitlines()[0]
    return json.loads(line)


def test_the_schema_example_carries_the_real_point_count():
    """A hardcoded example -- the exact bug this test guards against, since
    the prompt used to hardcode `n=2` in `SYSTEM_OPEN_JUDGE` -- cannot pass
    this for two different point counts at once: whatever fixed number it
    picks, one of the two calls below will disagree with it."""
    for points in (["가", "나"], ["가", "나", "다", "라", "마", "바"]):
        messages = prompts.build_open_judge_messages("q", "e", points, "a")
        example = _extract_example(messages[1]["content"])
        assert example["n"] == len(points)


def test_the_schema_examples_covered_array_length_matches_n():
    """The example's `covered` array is exactly as long as the `n` it states,
    for a point count the old hardcoded example (`n=2`) does not share."""
    points = ["가", "나", "다", "라", "마", "바"]
    messages = prompts.build_open_judge_messages("q", "e", points, "a")
    example = _extract_example(messages[1]["content"])
    assert example["n"] == 6
    assert len(example["covered"]) == 6


def test_the_schema_examples_covered_values_are_not_uniform():
    """A run of identical values invites a judge that echoes the example to
    copy the values too, not just the array shape -- the observed echo
    reproduced the whole instance verbatim. Both digits appearing rules out
    the all-0 or all-1 array that would invite exactly that."""
    points = ["가", "나", "다", "라", "마", "바"]
    messages = prompts.build_open_judge_messages("q", "e", points, "a")
    example = _extract_example(messages[1]["content"])
    assert set(example["covered"]) == {0, 1}


# --- Retry on a scoring failure ----------------------------------------------
# A single retry of the identical judge call. The 9 questions that failed all
# three independently sampled rounds are a systematic segmentation defect no
# retry fixes -- the count-as-literal and commit-to-`n` changes above target
# those. The other 6 failed only one round of three, which looks like ordinary
# sampling noise, and one retry is cheap insurance against it.
#
# A retry test that stubs the same success on both calls proves nothing: the
# second call could be a no-op and the test would still pass. Each test below
# makes the first call fail and asserts the actual call count, so a retry that
# silently never fires would be caught.

class _Result:
    def __init__(self, text):
        self.text = text
        self.prompt_tokens = 1
        self.completion_tokens = 1


def test_judge_open_retries_once_and_recovers_from_a_transient_mismatch(monkeypatch):
    """First call miscounts, second call (the retry) gets it right. The retry
    must actually re-query -- if it merely replayed the first response, the
    final verdict would still be the length-mismatch failure."""
    responses = [
        json.dumps({"n": 2, "covered": [1], "grounded": 1, "no_fabrication": 1,
                    "reason": "short"}),
        json.dumps({"n": 3, "covered": [1, 0, 1], "grounded": 1,
                    "no_fabrication": 1, "reason": "ok"}),
    ]
    calls = []

    def _fake_chat(*args, **kwargs):
        calls.append((args, kwargs))
        return _Result(responses[len(calls) - 1])

    monkeypatch.setattr(critic, "chat", _fake_chat)
    out = critic.judge_open("q", "e", ["a", "b", "c"], "answer")

    assert len(calls) == 2
    assert out.covered == (True, False, True)
    assert out.reason == "ok"


def test_judge_open_gives_up_after_one_retry(monkeypatch):
    """A mismatch that survives the retry is still a scoring failure with an
    empty `covered` -- padding is still forbidden. Exactly two calls are made,
    not an unbounded number."""
    calls = []

    def _fake_chat(*args, **kwargs):
        calls.append(1)
        return _Result(json.dumps(
            {"n": 2, "covered": [1], "grounded": 1, "no_fabrication": 1,
             "reason": "still short"}))

    monkeypatch.setattr(critic, "chat", _fake_chat)
    out = critic.judge_open("q", "e", ["a", "b", "c"], "answer")

    assert len(calls) == 2
    assert out.covered == ()
    assert out.correct is False


def test_judge_open_does_not_retry_a_first_call_success(monkeypatch):
    """No mismatch, no retry: exactly one call."""
    calls = []

    def _fake_chat(*args, **kwargs):
        calls.append(1)
        return _Result(json.dumps(
            {"n": 3, "covered": [1, 1, 1], "grounded": 1, "no_fabrication": 1,
             "reason": "ok"}))

    monkeypatch.setattr(critic, "chat", _fake_chat)
    critic.judge_open("q", "e", ["a", "b", "c"], "answer")

    assert len(calls) == 1


def test_judge_open_retry_bypasses_the_request_cache(monkeypatch):
    """The retry sends byte-identical messages at temperature 0. A request
    cache keyed on (url, body) -- installed during real measurement runs,
    `scripts/ablation.py`'s `RequestCache` -- would otherwise replay the first
    response verbatim, and the retry would not be a retry at all: just a
    second read of the first mismatched answer. `no_cache()` is the same
    mechanism `pipeline._self_consistent` uses for its own repeated-sample
    calls, and the retry needs it for the same reason.
    """
    entered = []

    class _SpyNoCache:
        def __enter__(self):
            entered.append("enter")
            return self

        def __exit__(self, *exc):
            entered.append("exit")
            return False

    monkeypatch.setattr(critic, "no_cache", _SpyNoCache)

    calls = []

    def _fake_chat(*args, **kwargs):
        calls.append(1)
        return _Result(json.dumps(
            {"n": 2, "covered": [1], "grounded": 1, "no_fabrication": 1,
             "reason": "short"}))

    monkeypatch.setattr(critic, "chat", _fake_chat)
    critic.judge_open("q", "e", ["a", "b", "c"], "answer")

    # Entered and exited exactly once: only around the retry, not the first
    # (cache-eligible) call.
    assert entered == ["enter", "exit"]
