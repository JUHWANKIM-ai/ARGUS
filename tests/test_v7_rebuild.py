from pathlib import Path

from scripts.v7_rebuild_index import backup_dir, PAYLOAD


def test_backup_is_outside_the_live_artifacts():
    assert "artifacts/ablation" not in str(backup_dir()).replace("\\", "/")


def test_payload_names_both_files_the_renderer_touches():
    assert set(PAYLOAD) == {"exchange_records.jsonl", "exchange_bm25.json"}
