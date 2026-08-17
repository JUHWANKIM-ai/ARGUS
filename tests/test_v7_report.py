from scripts.v7_report import verdict


def test_a_higher_minimum_by_two_is_adopted():
    ok, why = verdict({"V7-1": [106, 107, 108]}, [103, 104, 104], set(), {})
    assert ok is True


def test_a_higher_mean_with_the_same_minimum_is_rejected():
    """A live run is a single trial. If the minimum stays unchanged, it is
    not adopted."""
    ok, why = verdict({"V7-1": [103, 110, 110]}, [103, 104, 104], set(), {})
    assert ok is False
    assert any("최솟값" in w for w in why)


def test_an_improvement_inside_the_judge_band_is_rejected():
    """The judge's band width is 1. A +1 improvement alone cannot be
    distinguished from noise."""
    ok, why = verdict({"V7-1": [104, 105, 105]}, [103, 104, 104], set(), {})
    assert ok is False
    assert any("잡음" in w for w in why)


def test_a_new_flip_on_a_stable_question_is_rejected():
    ok, why = verdict({"V7-1": [106, 107, 108]}, [103, 104, 104],
                      {"Q-001"}, {"V7-1": {"Q-001"}})
    assert ok is False
    assert any("Q-001" in w for w in why)
