import subprocess
import sys
from pathlib import Path

from argus.config import settings


def test_build_index_writes_artifacts():
    root = settings().repo_root
    subprocess.run(
        [sys.executable, "scripts/build_index.py", "--group", "exchange", "--limit", "20"],
        cwd=root, check=True,
    )
    assert (settings().artifacts_root / "exchange_records.jsonl").exists()
    assert (settings().artifacts_root / "exchange_bm25.json").exists()


def test_built_records_have_schema_keys():
    import json
    path = settings().artifacts_root / "exchange_records.jsonl"
    first = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    for key in ("doc_id", "amount", "counterparty", "period_start", "related", "markdown"):
        assert key in first
