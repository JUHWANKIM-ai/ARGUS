"""Rebuild the exchange index so the supply-region field reaches retrieval.

Usage:
    python scripts/v7_rebuild_index.py --check     reports the diff only
    python scripts/v7_rebuild_index.py --apply     backs up, then swaps
    python scripts/v7_rebuild_index.py --restore   restores the backup

See docs/design-rationale.md#v7-index-rebuild
"""
from __future__ import annotations

import filecmp
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path("artifacts")
PAYLOAD = ("exchange_records.jsonl", "exchange_bm25.json")
STAGE = Path("scratch/v7_index_stage")


def backup_dir() -> Path:
    return Path("scratch/v7_index_backup")


def build() -> None:
    STAGE.mkdir(parents=True, exist_ok=True)
    subprocess.run([sys.executable, "scripts/build_index.py",
                    "--group", "exchange", "--out-dir", str(STAGE)], check=True)


def report() -> None:
    for name in PAYLOAD:
        live, fresh = ROOT / name, STAGE / name
        same = live.exists() and fresh.exists() and filecmp.cmp(live, fresh, shallow=False)
        print("  %-26s %s" % (name, "same" if same else "different"))


def _backup_state() -> str:
    """Which of three states the backup directory is in.

    "clean": no backup file at all, nothing is live, apply() may proceed.
    "live": every payload file is backed up, a swap is currently in effect.
    "partial": some but not all payload files are backed up. Neither apply()
    nor restore() ever leaves this behind on their own; it means a previous
    run was interrupted midway, and it is kept distinct from "live" so the
    refusal message does not send the operator to --restore when a restore
    cannot safely run either.
    """
    existing = [n for n in PAYLOAD if (backup_dir() / n).exists()]
    if not existing:
        return "clean"
    if len(existing) == len(PAYLOAD):
        return "live"
    return "partial"


def apply() -> None:
    state = _backup_state()
    if state == "live":
        raise SystemExit("a swap is currently live; run --restore first")
    if state == "partial":
        raise SystemExit(
            "backup directory is incomplete (neither clean nor a live swap); "
            "inspect scratch/v7_index_backup by hand before proceeding")
    backup_dir().mkdir(parents=True, exist_ok=True)
    for name in PAYLOAD:
        shutil.copy2(ROOT / name, backup_dir() / name)
        shutil.copy2(STAGE / name, ROOT / name)
    print("swap complete. Run --restore to revert")


def restore() -> None:
    state = _backup_state()
    if state == "clean":
        raise SystemExit("no swap is live; nothing to restore")
    if state == "partial":
        raise SystemExit(
            "backup directory is incomplete (neither clean nor a live swap); "
            "inspect scratch/v7_index_backup by hand before proceeding")
    for name in PAYLOAD:
        shutil.copy2(backup_dir() / name, ROOT / name)
    # Cleared on success so the tool returns to the "clean" state: otherwise
    # apply() refuses forever after the first apply/restore cycle, and the
    # obvious workaround is deleting this directory by hand, which for
    # artifacts/exchange_bm25.json is the only copy of the pre-rebuild index.
    for name in PAYLOAD:
        (backup_dir() / name).unlink()
    if backup_dir().exists() and not any(backup_dir().iterdir()):
        backup_dir().rmdir()
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
