"""Rebuild the exchange index so the supply-region field reaches retrieval.

Usage:
    python scripts/v7_rebuild_index.py --check     reports the diff only
    python scripts/v7_rebuild_index.py --apply     backs up, then swaps
    python scripts/v7_rebuild_index.py --restore   restores the backup

The renderer emits a field the shipped index does not carry, and the repository
contract is that a renderer change is followed by a rebuild rather than by a
change to the test that noticed. Every prior measurement was taken against the
shipped strings, so the swap is reversible and the backup is written first.
"""
from __future__ import annotations

import filecmp
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path("artifacts")
PAYLOAD = ("exchange_records.jsonl", "exchange_bm25.json")
STAGE = Path("artifacts/v7_index_stage")


def backup_dir() -> Path:
    return Path("artifacts/v7_index_backup")


def build() -> None:
    STAGE.mkdir(parents=True, exist_ok=True)
    subprocess.run([sys.executable, "scripts/build_index.py",
                    "--group", "exchange", "--out-dir", str(STAGE)], check=True)


def report() -> None:
    for name in PAYLOAD:
        live, fresh = ROOT / name, STAGE / name
        same = live.exists() and fresh.exists() and filecmp.cmp(live, fresh, shallow=False)
        print("  %-26s %s" % (name, "same" if same else "different"))


def apply() -> None:
    backup_dir().mkdir(parents=True, exist_ok=True)
    for name in PAYLOAD:
        shutil.copy2(ROOT / name, backup_dir() / name)
        shutil.copy2(STAGE / name, ROOT / name)
    print("swap complete. Run --restore to revert")


def restore() -> None:
    for name in PAYLOAD:
        shutil.copy2(backup_dir() / name, ROOT / name)
    print("restore complete")


def main(argv) -> int:
    mode = argv[0] if argv else "--check"
    if mode == "--restore":
        restore()
        return 0
    build()
    report()
    if mode == "--apply":
        apply()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
