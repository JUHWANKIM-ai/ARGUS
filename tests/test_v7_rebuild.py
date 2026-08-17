from pathlib import Path

import pytest

from scripts import v7_rebuild_index
from scripts.v7_rebuild_index import backup_dir, PAYLOAD


def test_backup_is_outside_the_live_artifacts():
    assert "artifacts/ablation" not in str(backup_dir()).replace("\\", "/")


def test_payload_names_both_files_the_renderer_touches():
    assert set(PAYLOAD) == {"exchange_records.jsonl", "exchange_bm25.json"}


def _seed(dir_path: Path, content: str) -> None:
    dir_path.mkdir(parents=True, exist_ok=True)
    for name in PAYLOAD:
        (dir_path / name).write_text(content, encoding="utf-8")


def test_apply_refuses_when_a_backup_already_exists(tmp_path, monkeypatch):
    """A second --apply must not overwrite a backup that already holds the
    true original: without this guard the original is silently replaced by
    the already-swapped content, and these payload files are gitignored, so
    there is no git-level recovery.

    Redirects the module's path constants into tmp_path rather than running
    against the real artifacts/ directory.
    """
    live = tmp_path / "live"
    stage = tmp_path / "stage"
    backup = tmp_path / "backup"
    _seed(live, "already-swapped")
    _seed(stage, "fresh-from-rebuild")
    _seed(backup, "true-original")

    monkeypatch.setattr(v7_rebuild_index, "ROOT", live)
    monkeypatch.setattr(v7_rebuild_index, "STAGE", stage)
    monkeypatch.setattr(v7_rebuild_index, "backup_dir", lambda: backup)

    with pytest.raises(SystemExit):
        v7_rebuild_index.apply()

    for name in PAYLOAD:
        assert (backup / name).read_text(encoding="utf-8") == "true-original"
        assert (live / name).read_text(encoding="utf-8") == "already-swapped"


def test_apply_proceeds_when_no_backup_is_present(tmp_path, monkeypatch):
    """With no pre-existing backup, apply backs up the live content and then
    swaps in the staged content, same as before the guard was added."""
    live = tmp_path / "live"
    stage = tmp_path / "stage"
    backup = tmp_path / "backup"
    _seed(live, "current-live")
    _seed(stage, "fresh-from-rebuild")

    monkeypatch.setattr(v7_rebuild_index, "ROOT", live)
    monkeypatch.setattr(v7_rebuild_index, "STAGE", stage)
    monkeypatch.setattr(v7_rebuild_index, "backup_dir", lambda: backup)

    v7_rebuild_index.apply()

    for name in PAYLOAD:
        assert (backup / name).read_text(encoding="utf-8") == "current-live"
        assert (live / name).read_text(encoding="utf-8") == "fresh-from-rebuild"
