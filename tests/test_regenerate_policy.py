# tests/test_regenerate_policy.py
import pytest

from argus import ablation
from argus.agent import pipeline


def _arm(**kw):
    return ablation.replace(ablation.ARMS["A42"], name="probe", **kw)


def test_clean_regeneration_is_always_kept():
    """A clean result is adopted regardless of the flag. This is the prior behaviour."""
    assert pipeline._keeps_regenerated(["calculation"], []) is True


def test_partial_improvement_is_rejected_by_default():
    """The default arm keeps the original, as before."""
    with ablation.use(ablation.ARMS["A42"]):
        assert pipeline._keeps_regenerated(
            ["calculation", "provenance"], ["provenance"]) is False


def test_partial_improvement_is_kept_when_enabled():
    with ablation.use(_arm(regenerate_partial=True)):
        assert pipeline._keeps_regenerated(
            ["calculation", "provenance"], ["provenance"]) is True


def test_a_different_failure_is_not_an_improvement():
    """A failure set that merely changed kind is not an improvement. Only a strict
    subset is adopted."""
    with ablation.use(_arm(regenerate_partial=True)):
        assert pipeline._keeps_regenerated(
            ["calculation"], ["provenance"]) is False


def test_a_grown_failure_set_is_rejected():
    with ablation.use(_arm(regenerate_partial=True)):
        assert pipeline._keeps_regenerated(
            ["calculation"], ["calculation", "provenance"]) is False


def test_an_unchanged_failure_set_is_not_an_improvement():
    """An unchanged failure set is not an improvement. The check requires a strict
    subset, not subset-or-equal, so an identical set must still be rejected."""
    with ablation.use(_arm(regenerate_partial=True)):
        assert pipeline._keeps_regenerated(["calculation"], ["calculation"]) is False
