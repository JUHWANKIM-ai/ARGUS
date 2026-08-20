"""Regenerate the submission integrity manifest for the preprocessing artifacts.

The manifest is what lets a judge verify that the bundle they downloaded from cloud
storage is the one the evaluation server runs on. It must be rebuilt whenever any
listed file changes -- otherwise it certifies a bundle that no longer exists.

Usage:
    python scripts/build_preprocessing_manifest.py            # 기준일 = 오늘
    python scripts/build_preprocessing_manifest.py --as-of 2026-08-20
    python scripts/build_preprocessing_manifest.py --check    # 재생성 없이 대조만

Rationale for the file list and the raw/ exclusion: docs/CONVENTION.md section 9.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import pathlib
import sys

OUT = pathlib.Path("artifacts/audit/preprocessing_manifest.json")

#: 제출 대상 20개. 런타임이 여는 파일(deploy/RUNBOOK.md) 전부와, 그 파일들을 만든
#: 전처리가 읽는 대장·기업목록을 더한 것이다. 🔴 이 목록이 제출물의 정의다 --
#: 여기서 빠지면 심사자가 받는 묶음에서도 빠진다.
FILES = [
    "artifacts/counterparty_graph.json",
    "artifacts/eval_report.json",
    "artifacts/exchange_bm25.json",
    "artifacts/exchange_records.jsonl",
    "artifacts/holding_bm25.json",
    "artifacts/holding_records.jsonl",
    "artifacts/major_bm25.json",
    "artifacts/major_records.jsonl",
    "artifacts/nary_records.jsonl",
    "artifacts/nary_records.meta.json",
    "artifacts/periodic_sections.jsonl",
    "artifacts/periodic_sections.txt",
    "artifacts/references.jsonl",
    "artifacts/section_index.json",
    "artifacts/section_index.npz",
    "data/dart-disclosures/manifest.jsonl",
    "data/dart-disclosures/universe.csv",
    "data/dart-disclosures/universe.xlsx",
    "data/dart-disclosures/README.md",
    "data/dart-disclosures/data_filter.md",
]

RAW = pathlib.Path("data/dart-disclosures/raw")

EXCLUDED = ("data/dart-disclosures/raw (5.2G) — 주최측이 제공한 원본 공시이지 "
            "우리 전처리 산출물이 아니다")
RAW_NOTE = ("data/dart-disclosures/raw — 주최측 제공 원본. 전처리 산출물은 아니나 "
            "백업·재현용으로 함께 올린다")


def digest(path: pathlib.Path) -> tuple[int, str]:
    """Return (bytes, sha256) streaming in chunks -- one file is 883 MB."""
    h = hashlib.sha256()
    size = 0
    with path.open("rb") as fh:
        while chunk := fh.read(1 << 20):
            h.update(chunk)
            size += len(chunk)
    return size, h.hexdigest()


def entries() -> list[dict]:
    out = []
    for name in FILES:
        path = pathlib.Path(name)
        if not path.exists():
            sys.exit("🔴 제출 대상이 없습니다: %s" % name)
        size, sha = digest(path)
        out.append({"path": name, "bytes": size, "sha256": sha})
    return out


def raw_totals() -> dict | None:
    """Count raw/ recursively. Returns None when the corpus is not on this machine."""
    if not RAW.is_dir():
        return None
    files = [p for p in RAW.rglob("*") if p.is_file()]
    return {"파일수": len(files),
            "바이트": sum(p.stat().st_size for p in files),
            "설명": RAW_NOTE}


def build(as_of: str) -> dict:
    rows = entries()
    total = sum(r["bytes"] for r in rows)
    doc = {"생성": "제출용 전처리 산출물 목록",
           "기준일": as_of,
           "파일수": len(rows),
           "총바이트": total,
           "🔴 제외": EXCLUDED,
           "파일": rows}
    raw = raw_totals()
    if raw is None:
        #: raw 가 없는 기계에서 돌리면 이전 값을 옮겨 적지 않는다. 재지 않은 수를
        #: 잰 것처럼 적는 것이 빈 칸보다 나쁘다.
        doc["🔴 raw 포함 시"] = "이 기계에 raw/ 가 없어 재지 않았다"
        doc["총계(raw 포함)"] = "미측정"
    else:
        doc["🔴 raw 포함 시"] = raw
        doc["총계(raw 포함)"] = "%.2f GB" % ((total + raw["바이트"]) / 1e9)
    return doc


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--as-of", default=datetime.date.today().isoformat())
    ap.add_argument("--check", action="store_true",
                    help="재생성 없이 현재 파일이 대조표와 맞는지만 본다")
    args = ap.parse_args()

    if args.check:
        old = json.loads(OUT.read_text(encoding="utf-8"))
        recorded = {r["path"]: (r["bytes"], r["sha256"]) for r in old["파일"]}
        drift = []
        for row in entries():
            want = recorded.get(row["path"])
            if want != (row["bytes"], row["sha256"]):
                drift.append(row["path"])
        missing = sorted(set(recorded) - {f for f in FILES})
        for name in missing:
            print("대조표에만 있음: %s" % name)
        print("기준일 %s · 파일 %d개 · 어긋남 %d건%s"
              % (old.get("기준일"), len(FILES), len(drift),
                 (": " + ", ".join(drift)) if drift else ""))
        sys.exit(1 if drift or missing else 0)

    doc = build(args.as_of)
    OUT.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n",
                   encoding="utf-8")
    print("%s 갱신 — 기준일 %s · %d개 · %.2f GB"
          % (OUT, doc["기준일"], doc["파일수"], doc["총바이트"] / 1e9))


if __name__ == "__main__":
    main()
