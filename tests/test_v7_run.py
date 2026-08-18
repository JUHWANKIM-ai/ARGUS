# tests/test_v7_run.py
import json
import socket
import ssl
import urllib.error
from pathlib import Path

import pytest

from scripts import v7_run
from scripts.v7_run import out_dir, with_transport_retry


def test_out_dir_separates_arm_cohort_and_round():
    """The round number must be part of the path so that three runs do not overwrite each other."""
    first = out_dir("A42", "full", 1)
    second = out_dir("A42", "full", 2)
    assert first != second
    assert first.parts[-3:] == ("v7", "A42_full_r1", "A42")


def test_out_dir_never_touches_v6():
    assert "v6" not in str(out_dir("A42", "full", 1))


# --- Index fingerprint, so a later comparison can tell which index a run ---
# --- used (V7-5 rebuilds it and must never be summed with the rest) --------

def test_index_fingerprint_is_a_short_deterministic_digest(tmp_path, monkeypatch):
    payload = tmp_path / "exchange_bm25.json"
    payload.write_text('{"a": 1}', encoding="utf-8")
    monkeypatch.setattr(v7_run, "INDEX_FILE", payload)

    first = v7_run.index_fingerprint()
    assert first and len(first) == 12
    assert v7_run.index_fingerprint() == first             # deterministic

    payload.write_text('{"a": 2}', encoding="utf-8")
    assert v7_run.index_fingerprint() != first              # content is actually hashed


def test_index_fingerprint_is_empty_when_the_index_is_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(v7_run, "INDEX_FILE", tmp_path / "missing.json")
    assert v7_run.index_fingerprint() == ""


def test_run_arm_records_the_index_fingerprint_in_the_report(tmp_path, monkeypatch):
    """Wiring check: `run_arm` must actually write the fingerprint into
    report.json, not merely have the function available. No LLM calls: the
    cohort is stubbed empty so the per-question loop never runs.
    """
    from scripts import error_analysis as ea

    monkeypatch.setattr(v7_run, "ROOT", tmp_path / "v7")
    index_file = tmp_path / "exchange_bm25.json"
    index_file.write_text("stub-index-content", encoding="utf-8")
    monkeypatch.setattr(v7_run, "INDEX_FILE", index_file)
    monkeypatch.setattr(ea, "_load_records", lambda: {})
    monkeypatch.setattr(ea, "load_cohort", lambda name: [])

    v7_run.run_arm("A42", 1, ["full"])

    report = json.loads(
        (v7_run.out_dir("A42", "full", 1) / "report.json").read_text(encoding="utf-8"))
    assert report["index_fingerprint"] == v7_run.index_fingerprint()
    assert report["index_fingerprint"]


# --- with_transport_retry, resilience against transport faults -------------
# --- (socket timeouts, connection resets) that with_retry does not retry ---


class _FlakyCall:
    """Callable that raises a given exception a fixed number of times, then
    returns a fixed result. Used to simulate a transient transport fault
    without making any network call.
    """

    def __init__(self, exc, fail_times, result="ok"):
        self.exc = exc
        self.fail_times = fail_times
        self.result = result
        self.calls = 0

    def __call__(self, *args, **kwargs):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise self.exc
        return self.result


def _no_sleep(seconds):
    """Stand-in for time.sleep so retry tests run instantly."""


def test_transport_retry_returns_eventual_success_after_transient_failures():
    flaky = _FlakyCall(TimeoutError("The read operation timed out"), fail_times=2)

    result = with_transport_retry(flaky, sleep=_no_sleep)

    assert result == "ok"
    assert flaky.calls == 3


@pytest.mark.parametrize("exc", [
    TimeoutError("timed out"),
    ConnectionResetError("connection reset"),
    ConnectionAbortedError("connection aborted"),
    ssl.SSLError("ssl error"),
    socket.gaierror("name resolution failure"),
    urllib.error.URLError("url error"),
])
def test_transport_retry_covers_each_transport_error_type(exc):
    flaky = _FlakyCall(exc, fail_times=1)

    result = with_transport_retry(flaky, sleep=_no_sleep)

    assert result == "ok"
    assert flaky.calls == 2


def test_transport_retry_does_not_retry_a_non_transport_error():
    """A programming error must propagate immediately, with no retry at all."""
    flaky = _FlakyCall(ValueError("not a transport error"), fail_times=99)

    with pytest.raises(ValueError):
        with_transport_retry(flaky, sleep=_no_sleep)

    assert flaky.calls == 1


def test_transport_retry_does_not_retry_a_missing_file_error():
    flaky = _FlakyCall(FileNotFoundError("no such file"), fail_times=99)

    with pytest.raises(FileNotFoundError):
        with_transport_retry(flaky, sleep=_no_sleep)

    assert flaky.calls == 1


def test_transport_retry_does_not_retry_keyboard_interrupt():
    flaky = _FlakyCall(KeyboardInterrupt(), fail_times=99)

    with pytest.raises(KeyboardInterrupt):
        with_transport_retry(flaky, sleep=_no_sleep)

    assert flaky.calls == 1


def test_transport_retry_does_not_retry_an_assertion_error():
    flaky = _FlakyCall(AssertionError("budget exceeded"), fail_times=99)

    with pytest.raises(AssertionError):
        with_transport_retry(flaky, sleep=_no_sleep)

    assert flaky.calls == 1


def test_transport_retry_propagates_the_last_error_once_attempts_are_exhausted():
    flaky = _FlakyCall(ConnectionError("connection reset"), fail_times=99)

    with pytest.raises(ConnectionError):
        with_transport_retry(flaky, attempts=3, sleep=_no_sleep)

    assert flaky.calls == 3              # exactly the attempt cap, no more, no fewer


def test_transport_retry_widens_the_wait_between_attempts():
    waits = []
    flaky = _FlakyCall(TimeoutError("timed out"), fail_times=3)

    with_transport_retry(flaky, base_wait_s=1.0, sleep=waits.append)

    assert len(waits) == 3
    assert waits == sorted(waits)        # non-decreasing
    assert waits[-1] > waits[0]           # and it actually widens, not flat
