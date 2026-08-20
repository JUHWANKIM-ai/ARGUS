"""The baseline re-scoring runner.

The strict judge breaks comparison with every v1-v7 number. The only honest
baseline is A43's stored server responses scored by the new judge, so this
runner re-judges without regenerating: the answers must not change, or the
comparison measures two things at once.
"""
import json

import pytest


def test_the_runner_reuses_stored_responses_and_never_regenerates(monkeypatch, tmp_path):
    import sys

    sys.path.insert(0, ".")
    from scripts import v8_rejudge

    monkeypatch.setattr(v8_rejudge.ea, "run_case",
                        lambda *a, **k: pytest.fail("응답을 다시 생성하면 안 된다"))
    src = tmp_path / "responses"
    src.mkdir()
    (src / "Q-901.json").write_text(json.dumps(
        {"question_id": "Q-901",
         "response": {"answer": "a", "retrieved_context": "e"}}),
        encoding="utf-8")
    assert v8_rejudge.stored_response(src, "Q-901")["answer"] == "a"


def test_a_missing_response_is_reported_not_skipped(tmp_path):
    import sys

    sys.path.insert(0, ".")
    from scripts import v8_rejudge

    with pytest.raises(FileNotFoundError):
        v8_rejudge.stored_response(tmp_path, "Q-999")


def test_open_questions_only(tmp_path):
    """Closed questions are scored by normalised exact match with no LLM
    call, so re-judging them would spend calls and change nothing."""
    import sys

    sys.path.insert(0, ".")
    from scripts import v8_rejudge

    cases = [{"question_id": "Q-1", "answer_mode": "open"},
             {"question_id": "Q-2", "answer_mode": "closed"}]
    assert [c["question_id"] for c in v8_rejudge.open_cases(cases)] == ["Q-1"]
