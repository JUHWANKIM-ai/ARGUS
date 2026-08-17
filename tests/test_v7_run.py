# tests/test_v7_run.py
import json
from pathlib import Path

from scripts import v7_run
from scripts.v7_run import out_dir


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
