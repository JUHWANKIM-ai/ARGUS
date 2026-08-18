import json

from scripts import v7_report
from scripts.v7_report import (
    _base_stable,
    fingerprint_mismatch,
    fingerprints,
    totals,
    verdict,
    wrong_sets,
)


def test_a_higher_minimum_by_two_is_adopted():
    ok, why = verdict({"V7-1": [106, 107, 108]}, [103, 104, 104], set(), {}, {})
    assert ok is True


def test_a_higher_mean_with_the_same_minimum_is_rejected():
    """A live run is a single trial. If the minimum stays unchanged, it is
    not adopted."""
    ok, why = verdict({"V7-1": [103, 110, 110]}, [103, 104, 104], set(), {}, {})
    assert ok is False
    assert any("최솟값" in w for w in why)


def test_an_improvement_inside_the_judge_band_is_rejected():
    """The judge's band width is 1. A +1 improvement alone cannot be
    distinguished from noise."""
    ok, why = verdict({"V7-1": [104, 105, 105]}, [103, 104, 104], set(), {}, {})
    assert ok is False
    assert any("잡음" in w for w in why)


def test_a_new_flip_on_a_stable_question_is_rejected():
    ok, why = verdict({"V7-1": [106, 107, 108]}, [103, 104, 104],
                      {"Q-001"}, {"V7-1": {"Q-001"}}, {"V7-1": {"Q-001"}})
    assert ok is False
    assert any("Q-001" in w for w in why)


def test_a_judge_band_gain_with_fewer_flips_than_baseline_is_adopted():
    """A gain of 1 alone is not distinguishable from judge noise, but a
    strictly smaller flip set than the baseline's is itself the result this
    generation targets, so it is adopted through that branch instead."""
    ok, why = verdict({"V7-1": [104, 105, 105]}, [103, 104, 104], set(),
                      {"V7-1": {"Q-001"}}, {"V7-1": {"Q-001"}}, {"Q-001", "Q-002"})
    assert ok is True


def test_a_judge_band_gain_with_flips_unchanged_or_worse_is_rejected():
    """The flip-count escape hatch only opens when the count strictly
    dropped. Equal or higher leaves the gain inside the judge band alone,
    which is a rejection."""
    ok, why = verdict({"V7-1": [104, 105, 105]}, [103, 104, 104], set(),
                      {"V7-1": {"Q-001", "Q-002"}}, {"V7-1": {"Q-001", "Q-002"}},
                      {"Q-003", "Q-004"})
    assert ok is False
    assert any("잡음" in w for w in why)


def test_a_gain_of_exactly_two_is_adopted_regardless_of_flips():
    """Gain 2 is the boundary where the spec's own clause ("minimum gain of
    2 or more") adopts unconditionally, bypassing the flip-count check
    entirely. Flips here are equal to the baseline's, not decreased, so the
    only way this can pass is through the gain branch, not the escape
    hatch."""
    ok, why = verdict({"V7-1": [105, 106, 106]}, [103, 104, 104], set(),
                      {"V7-1": {"Q-001", "Q-002"}}, {"V7-1": {"Q-001", "Q-002"}},
                      {"Q-003", "Q-004"})
    assert ok is True


def test_a_non_positive_gain_is_rejected_even_with_fewer_flips():
    """The flip-count escape hatch belongs to the judge-band branch only. A
    minimum that does not rise at all is rejected regardless of how many
    fewer questions flip, since a single evaluation is judged on the minimum
    it is expected to produce."""
    ok, why = verdict({"V7-1": [102, 103, 103]}, [103, 104, 104], set(),
                      {"V7-1": {"Q-001"}}, {"V7-1": {"Q-001"}},
                      {"Q-001", "Q-002", "Q-003"})
    assert ok is False
    assert any("최솟값" in w for w in why)


# --- Full round count required on both sides --------------------------------

def test_a_truncated_arm_is_rejected_regardless_of_its_minimum():
    """A truncated arm's minimum is taken over fewer draws and is therefore
    systematically easier to beat than a complete arm's; it must not enter
    the comparison at all."""
    ok, why = verdict({"V7-X": [106]}, [103, 104, 104], set(), {}, {})
    assert ok is False
    assert any("회차" in w for w in why)
    assert any("1" in w and "3" in w for w in why)          # names the actual counts


def test_a_truncated_baseline_also_blocks_the_verdict():
    ok, why = verdict({"V7-1": [106, 107, 108]}, [103, 104], set(), {}, {})
    assert ok is False
    assert any("회차" in w for w in why)


# --- Regression against the union of wrong sets, not against flips ---------

def test_an_always_wrong_question_blocks_adoption_even_though_it_never_flips():
    """A question wrong in every round of the arm sits in both the union and
    the intersection of its wrong sets, so it is absent from `flips` (union
    minus intersection). It is present in `wrong` (the union), and a stable
    v6 question wrong in every round of the arm is exactly the permanent
    regression condition 2 exists to catch."""
    ok, why = verdict({"V7-1": [106, 107, 108]}, [103, 104, 104],
                      {"Q-001"}, {"V7-1": set()}, {"V7-1": {"Q-001"}})
    assert ok is False
    assert any("Q-001" in w for w in why)


# --- `wrong_sets` keeps a round exactly when `totals` does ------------------

def _write_report(tmp_path, arm, cohort, round_no, rows, fingerprint=None):
    base = tmp_path / arm / cohort / str(round_no)
    base.mkdir(parents=True, exist_ok=True)
    report = {
        "crosstab": {"overall": {"correct": sum(1 for row in rows if row["correct"]),
                                  "n": len(rows)}},
        "rows": rows,
    }
    if fingerprint is not None:
        report["index_fingerprint"] = fingerprint
    (base / "report.json").write_text(json.dumps(report), encoding="utf-8")


def _redirect(tmp_path, monkeypatch):
    monkeypatch.setattr(v7_report, "out_dir",
                        lambda arm, cohort, round_no: tmp_path / arm / cohort / str(round_no))


def test_wrong_sets_drops_a_round_exactly_when_totals_does(tmp_path, monkeypatch):
    """A round interrupted after the first cohort must not count for flips
    if it does not count for totals, or a question wrong in every completed
    round of that arm reads as a phantom flip."""
    _redirect(tmp_path, monkeypatch)
    wrong_row = [{"question_id": "Q-001", "correct": False}]
    for cohort in v7_report.COHORTS:
        _write_report(tmp_path, "ARM", cohort, 1, wrong_row)
        _write_report(tmp_path, "ARM", cohort, 3, wrong_row)
    # Round 2: only the first cohort was written -- an interrupted round.
    _write_report(tmp_path, "ARM", v7_report.COHORTS[0], 2, wrong_row)

    assert totals("ARM") == [0, 0]                 # round 2 dropped, as before
    rounds = wrong_sets("ARM")
    assert set(rounds) == {1, 3}                    # round 2 dropped here too
    assert rounds[1] == {"Q-001"} and rounds[3] == {"Q-001"}


def test_wrong_sets_keeps_a_complete_round_with_zero_wrong_answers(tmp_path, monkeypatch):
    """`found` (in the old implementation) or the for-else `break` (in the
    fix) must not be confused with "some cohort had wrong answers": a
    genuinely clean complete round has to be kept, as an empty set."""
    _redirect(tmp_path, monkeypatch)
    clean_row = [{"question_id": "Q-001", "correct": True}]
    for cohort in v7_report.COHORTS:
        _write_report(tmp_path, "ARM", cohort, 1, clean_row)

    rounds = wrong_sets("ARM")
    assert rounds == {1: set()}


# --- `_base_stable` reads any available baseline round, not just round 1 ---

def test_base_stable_does_not_require_round_1_specifically(tmp_path, monkeypatch):
    """If round 1 is missing but rounds 2 and 3 exist, the question universe
    must still be enumerable from one of those, or condition 2 silently
    never fires because the stable set is empty."""
    _redirect(tmp_path, monkeypatch)
    rows = [{"question_id": "Q-001", "correct": True},
            {"question_id": "Q-002", "correct": False}]
    for cohort in v7_report.COHORTS:
        _write_report(tmp_path, "BASE", cohort, 2, rows)
        _write_report(tmp_path, "BASE", cohort, 3, rows)
    # Round 1 is deliberately absent.

    stable = _base_stable("BASE", wrong_sets("BASE"))
    assert stable == {"Q-001"}                      # Q-002 was wrong at least once


def test_base_stable_is_empty_with_no_baseline_rounds():
    assert _base_stable("BASE", {}) == set()


# --- Index fingerprint keeps a rebuilt-index arm out of the shared table ---

def test_fingerprints_reads_the_first_available_report(tmp_path, monkeypatch):
    _redirect(tmp_path, monkeypatch)
    rows = [{"question_id": "Q-001", "correct": True}]
    for cohort in v7_report.COHORTS:
        _write_report(tmp_path, "A42", cohort, 1, rows, fingerprint="idx-old")
        _write_report(tmp_path, "V7-5", cohort, 1, rows, fingerprint="idx-new")

    fps = fingerprints(["A42", "V7-5", "V7-1"])
    assert fps == {"A42": "idx-old", "V7-5": "idx-new", "V7-1": None}


def test_fingerprint_mismatch_is_none_when_every_measured_arm_agrees():
    fps = {"A42": "idx-old", "V7-1": "idx-old", "V7-2": None}
    assert fingerprint_mismatch(fps) is None


def test_fingerprint_mismatch_names_the_disagreeing_arms():
    fps = {"A42": "idx-old", "V7-1": "idx-old", "V7-5": "idx-new"}
    reason = fingerprint_mismatch(fps)
    assert reason is not None
    assert "V7-5" in reason and "A42" in reason and "V7-1" in reason


def test_main_refuses_a_comparison_table_across_differing_fingerprints(
        tmp_path, monkeypatch, capsys):
    _redirect(tmp_path, monkeypatch)
    rows = [{"question_id": "Q-001", "correct": True}]
    for cohort in v7_report.COHORTS:
        _write_report(tmp_path, "A42", cohort, 1, rows, fingerprint="idx-old")
        _write_report(tmp_path, "V7-5", cohort, 1, rows, fingerprint="idx-new")

    code = v7_report.main(["A42", "V7-5"])
    out = capsys.readouterr().out
    assert code != 0
    assert "V7-5" in out and "A42" in out
