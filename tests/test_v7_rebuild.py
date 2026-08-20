from pathlib import Path

import pytest

from scripts import v7_rebuild_index
from scripts.v7_rebuild_index import backup_dir, PAYLOAD


def test_backup_is_outside_the_live_artifacts():
    assert "scratch/ablation" not in str(backup_dir()).replace("\\", "/")


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


def test_apply_restore_apply_cycle_succeeds_on_the_second_apply(tmp_path, monkeypatch):
    """Without clearing the backup on restore, a second apply is refused with
    advice ("run --restore first") the operator has just followed, and the
    only apparent way out is deleting the backup directory by hand, which
    for a gitignored artifacts/ tree would destroy the only surviving copy
    of the pre-rebuild index. Runs entirely against tmp_path.
    """
    live = tmp_path / "live"
    stage = tmp_path / "stage"
    backup = tmp_path / "backup"
    _seed(live, "original-content")
    _seed(stage, "first-rebuild-content")

    monkeypatch.setattr(v7_rebuild_index, "ROOT", live)
    monkeypatch.setattr(v7_rebuild_index, "STAGE", stage)
    monkeypatch.setattr(v7_rebuild_index, "backup_dir", lambda: backup)

    v7_rebuild_index.apply()
    for name in PAYLOAD:
        assert (live / name).read_text(encoding="utf-8") == "first-rebuild-content"
        assert (backup / name).read_text(encoding="utf-8") == "original-content"

    v7_rebuild_index.restore()
    for name in PAYLOAD:
        assert (live / name).read_text(encoding="utf-8") == "original-content"
        assert not (backup / name).exists()             # backup cleared
    assert not backup.exists()                           # directory itself removed

    # A fresh rebuild stages new content. The second apply must not be
    # refused just because a backup existed once before.
    _seed(stage, "second-rebuild-content")
    v7_rebuild_index.apply()
    for name in PAYLOAD:
        assert (live / name).read_text(encoding="utf-8") == "second-rebuild-content"
        assert (backup / name).read_text(encoding="utf-8") == "original-content"


def test_restore_refuses_when_no_swap_is_live(tmp_path, monkeypatch):
    live = tmp_path / "live"
    backup = tmp_path / "backup"
    _seed(live, "current-live")

    monkeypatch.setattr(v7_rebuild_index, "ROOT", live)
    monkeypatch.setattr(v7_rebuild_index, "backup_dir", lambda: backup)

    with pytest.raises(SystemExit):
        v7_rebuild_index.restore()
    for name in PAYLOAD:
        assert (live / name).read_text(encoding="utf-8") == "current-live"


def test_apply_refusal_names_a_live_swap_distinctly_from_a_partial_backup(
        tmp_path, monkeypatch):
    """The refusal message for a complete backup (a swap is live, restore
    first) must read differently from an incomplete one (neither clean nor
    live), since restore itself cannot safely run against a partial backup
    either."""
    live = tmp_path / "live"
    stage = tmp_path / "stage"
    backup = tmp_path / "backup"
    _seed(live, "already-swapped")
    _seed(stage, "fresh-from-rebuild")
    backup.mkdir(parents=True)
    (backup / PAYLOAD[0]).write_text("only-one-file-backed-up", encoding="utf-8")
    # PAYLOAD[1] is deliberately absent from the backup: a partial state.

    monkeypatch.setattr(v7_rebuild_index, "ROOT", live)
    monkeypatch.setattr(v7_rebuild_index, "STAGE", stage)
    monkeypatch.setattr(v7_rebuild_index, "backup_dir", lambda: backup)

    with pytest.raises(SystemExit) as live_case:
        v7_rebuild_index.apply()
    assert "restore first" not in str(live_case.value)

    with pytest.raises(SystemExit) as restore_case:
        v7_rebuild_index.restore()
    assert "restore first" not in str(restore_case.value)


def test_backup_state_reports_all_three_states(tmp_path, monkeypatch):
    backup = tmp_path / "backup"
    monkeypatch.setattr(v7_rebuild_index, "backup_dir", lambda: backup)

    assert v7_rebuild_index._backup_state() == "clean"

    _seed(backup, "true-original")
    assert v7_rebuild_index._backup_state() == "live"

    (backup / PAYLOAD[0]).unlink()
    assert v7_rebuild_index._backup_state() == "partial"
