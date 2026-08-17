# tests/test_v7_run.py
from pathlib import Path

from scripts.v7_run import out_dir


def test_out_dir_separates_arm_cohort_and_round():
    """The round number must be part of the path so that three runs do not overwrite each other."""
    first = out_dir("A42", "full", 1)
    second = out_dir("A42", "full", 2)
    assert first != second
    assert first.parts[-3:] == ("v7", "A42_full_r1", "A42")


def test_out_dir_never_touches_v6():
    assert "v6" not in str(out_dir("A42", "full", 1))
