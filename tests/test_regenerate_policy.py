# tests/test_regenerate_policy.py
import pytest

from argus import ablation
from argus.agent import pipeline


def _arm(**kw):
    return ablation.replace(ablation.ARMS["A42"], name="probe", **kw)


def test_clean_regeneration_is_always_kept():
    """깨끗해진 답변은 플래그와 무관하게 채택한다. 현행 동작이다."""
    assert pipeline._keeps_regenerated(["calculation"], []) is True


def test_partial_improvement_is_rejected_by_default():
    """기본 arm 은 종전대로 원본을 유지한다."""
    with ablation.use(ablation.ARMS["A42"]):
        assert pipeline._keeps_regenerated(
            ["calculation", "provenance"], ["provenance"]) is False


def test_partial_improvement_is_kept_when_enabled():
    with ablation.use(_arm(regenerate_partial=True)):
        assert pipeline._keeps_regenerated(
            ["calculation", "provenance"], ["provenance"]) is True


def test_a_different_failure_is_not_an_improvement():
    """종류가 바뀌기만 한 것은 개선이 아니다. 진부분집합만 채택한다."""
    with ablation.use(_arm(regenerate_partial=True)):
        assert pipeline._keeps_regenerated(
            ["calculation"], ["provenance"]) is False


def test_a_grown_failure_set_is_rejected():
    with ablation.use(_arm(regenerate_partial=True)):
        assert pipeline._keeps_regenerated(
            ["calculation"], ["calculation", "provenance"]) is False
