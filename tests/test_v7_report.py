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


def test_a_judge_band_gain_with_fewer_flips_than_baseline_is_adopted():
    """A gain of 1 alone is not distinguishable from judge noise, but a
    strictly smaller flip set than the baseline's is itself the result this
    generation targets, so it is adopted through that branch instead."""
    ok, why = verdict({"V7-1": [104, 105, 105]}, [103, 104, 104], set(),
                      {"V7-1": {"Q-001"}}, {"Q-001", "Q-002"})
    assert ok is True


def test_a_judge_band_gain_with_flips_unchanged_or_worse_is_rejected():
    """The flip-count escape hatch only opens when the count strictly
    dropped. Equal or higher leaves the gain inside the judge band alone,
    which is a rejection."""
    ok, why = verdict({"V7-1": [104, 105, 105]}, [103, 104, 104], set(),
                      {"V7-1": {"Q-001", "Q-002"}}, {"Q-003", "Q-004"})
    assert ok is False
    assert any("잡음" in w for w in why)


def test_a_gain_of_exactly_two_is_adopted_regardless_of_flips():
    """Gain 2 is the boundary where the spec's own clause ("minimum gain of
    2 or more") adopts unconditionally, bypassing the flip-count check
    entirely. Flips here are equal to the baseline's, not decreased, so the
    only way this can pass is through the gain branch, not the escape
    hatch."""
    ok, why = verdict({"V7-1": [105, 106, 106]}, [103, 104, 104], set(),
                      {"V7-1": {"Q-001", "Q-002"}}, {"Q-003", "Q-004"})
    assert ok is True


def test_a_non_positive_gain_is_rejected_even_with_fewer_flips():
    """The flip-count escape hatch belongs to the judge-band branch only. A
    minimum that does not rise at all is rejected regardless of how many
    fewer questions flip, since a single evaluation is judged on the minimum
    it is expected to produce."""
    ok, why = verdict({"V7-1": [102, 103, 103]}, [103, 104, 104], set(),
                      {"V7-1": {"Q-001"}}, {"Q-001", "Q-002", "Q-003"})
    assert ok is False
    assert any("최솟값" in w for w in why)
