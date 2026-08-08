"""manifest.jsonl / universe.csv 로딩과 LLM 없는 프리필터."""
from __future__ import annotations

import csv
import json
from functools import lru_cache

from argus.config import settings


@lru_cache(maxsize=1)
def load_manifest() -> tuple[dict, ...]:
    path = settings().corpus_root / "manifest.jsonl"
    with path.open(encoding="utf-8") as fh:
        return tuple(json.loads(line) for line in fh if line.strip())


@lru_cache(maxsize=1)
def load_universe() -> dict[str, dict]:
    path = settings().corpus_root / "universe.csv"
    # universe.csv 는 UTF-8 BOM 이다
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return {row["corp_name"]: row for row in csv.DictReader(fh)}


@lru_cache(maxsize=1)
def _alias_index() -> dict[str, str]:
    """통용명·영문명·법인명을 모두 corp_name 으로 되돌리는 색인."""
    index: dict[str, str] = {}
    for corp_name, row in load_universe().items():
        for alias in (corp_name, row.get("listed_name"), row.get("corp_eng_name")):
            if alias:
                index[alias.strip().lower()] = corp_name
    return index


def resolve_company(name: str) -> dict | None:
    corp_name = _alias_index().get(name.strip().lower())
    return load_universe().get(corp_name) if corp_name else None


def filter_docs(
    corp_name: str | None = None,
    doc_group: str | None = None,
    doc_subtype: str | None = None,
    year: int | None = None,
    include_corrections: bool = True,
) -> list[dict]:
    rows = load_manifest()
    if corp_name:
        resolved = resolve_company(corp_name)
        target = resolved["corp_name"] if resolved else corp_name
        rows = [r for r in rows if r["corp_name"] == target]
    if doc_group:
        rows = [r for r in rows if r["doc_group"] == doc_group]
    if doc_subtype:
        rows = [r for r in rows if r.get("doc_subtype") == doc_subtype]
    if year:
        rows = [r for r in rows if str(r.get("rcept_dt", ""))[:4] == str(year)]
    if not include_corrections:
        rows = [r for r in rows if not r.get("is_correction")]
    return list(rows)
