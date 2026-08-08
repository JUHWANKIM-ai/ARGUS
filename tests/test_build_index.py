import json
import subprocess
import sys

from argus.config import settings


def _build(out_dir, limit=20):
    subprocess.run(
        [sys.executable, "scripts/build_index.py", "--group", "exchange",
         "--limit", str(limit), "--out-dir", str(out_dir)],
        cwd=settings().repo_root, check=True,
    )


def test_build_index_writes_artifacts(tmp_path):
    """실제 artifacts/ 를 덮지 않는다 — 덮으면 API 가 20건만 서빙하게 된다."""
    _build(tmp_path)
    assert (tmp_path / "exchange_records.jsonl").exists()
    assert (tmp_path / "exchange_bm25.json").exists()


def test_built_records_have_schema_keys(tmp_path):
    _build(tmp_path)
    first = json.loads((tmp_path / "exchange_records.jsonl")
                       .read_text(encoding="utf-8").splitlines()[0])
    for key in ("doc_id", "amount", "counterparty", "period_start",
                "related", "is_latest", "markdown"):
        assert key in first
