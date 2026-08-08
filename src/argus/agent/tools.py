"""도구 7종. CONVENTION §7.7 의 참조 명세를 따른다."""
from __future__ import annotations

import json
from functools import lru_cache

from argus.config import settings
from argus.corpus.manifest import filter_docs, resolve_company
from argus.retrieve.bm25 import Bm25Index


@lru_cache(maxsize=1)
def _records() -> list[dict]:
    path = settings().artifacts_root / "exchange_records.jsonl"
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


@lru_cache(maxsize=1)
def _index() -> Bm25Index | None:
    path = settings().artifacts_root / "exchange_bm25.json"
    return Bm25Index.load(path) if path.exists() else None


def resolve_company_tool(name: str) -> dict:
    row = resolve_company(name)
    if not row:
        return {"error": f"universe 70개사에 '{name}' 이 없습니다"}
    return {"corp_code": row["corp_code"], "corp_name": row["corp_name"],
            "sector": row["sector"], "market": row["market"]}


def query_events(corp_name: str | None = None, doc_subtype: str | None = None,
                 year: int | None = None, min_amount: float | None = None,
                 is_latest: bool = True) -> dict:
    rows = _records()
    if corp_name:
        resolved = resolve_company(corp_name)
        target = resolved["corp_name"] if resolved else corp_name
        rows = [r for r in rows if r["corp_name"] == target]
    if doc_subtype:
        rows = [r for r in rows if r.get("doc_subtype") == doc_subtype]
    if year:
        rows = [r for r in rows if str(r.get("rcept_dt", ""))[:4] == str(year)]
    if min_amount is not None:
        rows = [r for r in rows if (r.get("amount") or 0) >= min_amount]
    if is_latest:
        rows = [r for r in rows if r.get("is_latest", True)]
    return {"count": len(rows), "events": rows[:20]}


def list_filings(corp_name: str, doc_group: str = "periodic",
                 base_year: int | None = None) -> dict:
    docs = filter_docs(corp_name=corp_name, doc_group=doc_group, include_corrections=True)
    if base_year:
        docs = [d for d in docs if d.get("base_year") == base_year]
    return {"count": len(docs),
            "filings": [{"doc_id": d["doc_id"], "report_nm": d["report_nm"],
                         "rcept_no": d["rcept_no"], "rcept_dt": d["rcept_dt"],
                         "base_year": d.get("base_year")} for d in docs]}


def list_sections(doc_id: str) -> dict:
    path = settings().artifacts_root / "periodic_sections.jsonl"
    if not path.exists():
        return {"error": "섹션 인덱스가 없습니다. scripts/build_index.py 를 먼저 실행하세요"}
    out = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            rec = json.loads(line)
            if rec["doc_id"] == doc_id and rec.get("n_chars", 0) >= 100:
                out.append({"path": rec["path"], "n_chars": rec["n_chars"],
                            "n_tables": rec.get("n_tables", 0)})
    return {"count": len(out), "sections": out}


def read_section(doc_id: str, path: str) -> dict:
    idx = settings().artifacts_root / "periodic_sections.jsonl"
    if not idx.exists():
        return {"error": "섹션 인덱스가 없습니다"}
    with idx.open(encoding="utf-8") as fh:
        for line in fh:
            rec = json.loads(line)
            if rec["doc_id"] == doc_id and rec["path"] == path:
                return {"doc_id": doc_id, "path": path, "text": rec["text"]}
    return {"error": f"섹션 없음: {doc_id} / {path}"}


def search_chunks(query: str, corp_name: str | None = None, top_k: int = 5,
                  year: int | None = None, ym: str | None = None) -> dict:
    """하이브리드 검색 폴백.

    기간 조건이 랭킹을 좌우한다 — 실측에서 corp_name 만으로는 골든이 15/26 이지만
    rcept_dt prefix 를 함께 걸면 1위가 된다. 도구 스펙에도 노출해야 한다.
    """
    index = _index()
    if index is None:
        return {"error": "BM25 인덱스가 없습니다"}
    filters = {}
    if corp_name:
        resolved = resolve_company(corp_name)
        filters["corp_name"] = resolved["corp_name"] if resolved else corp_name
    prefix = {}
    if ym:
        prefix["rcept_dt"] = str(ym).replace("-", "")[:6]
    elif year:
        prefix["rcept_dt"] = str(year)[:4]
    hits = index.search(query, top_k=top_k, prefix=prefix or None, **filters)
    by_id = {r["doc_id"]: r for r in _records()}
    return {"count": len(hits),
            "chunks": [{"doc_id": d, "score": round(s, 3),
                        "markdown": by_id.get(d, {}).get("markdown", "")} for d, s in hits]}


def get_history(rcept_no: str) -> dict:
    """정정 체인 추적. 유일하게 is_latest 를 무시한다."""
    rows = _records()
    origin = next((r for r in rows if r["rcept_no"] == rcept_no), None)
    if not origin:
        return {"error": f"레코드 없음: {rcept_no}"}
    related = origin.get("related", [])
    chain = [origin] + [r for r in rows if r["rcept_no"] in related]
    return {"rcept_no": rcept_no, "chain_length": len(chain),
            "chain": [{"rcept_no": c["rcept_no"], "rcept_dt": c["rcept_dt"],
                       "is_correction": c["is_correction"],
                       "amount": c.get("amount")} for c in chain]}


def _spec(name: str, desc: str, props: dict, required: list[str]) -> dict:
    return {"type": "function",
            "function": {"name": name, "description": desc,
                         "parameters": {"type": "object", "properties": props,
                                        "required": required}}}


_STR = {"type": "string"}
_INT = {"type": "integer"}
_NUM = {"type": "number"}
_BOOL = {"type": "boolean"}

TOOL_SPECS = [
    _spec("resolve_company", "기업 통용명을 DART corp_code·섹터로 변환한다",
          {"name": {**_STR, "description": "기업 통용명 (예: 현대차)"}}, ["name"]),
    _spec("query_events", "계약·투자·해지 등 정형 이벤트를 조회한다 (기본 최신 정정본)",
          {"corp_name": _STR, "doc_subtype": _STR, "year": _INT,
           "min_amount": _NUM, "is_latest": _BOOL}, []),
    _spec("list_filings", "정기공시 문서 목록을 조회한다",
          {"corp_name": _STR, "doc_group": _STR, "base_year": _INT}, ["corp_name"]),
    _spec("list_sections", "보고서의 목차 경로와 글자수를 조회한다 (기재생략 스텁 제외)",
          {"doc_id": _STR}, ["doc_id"]),
    _spec("read_section", "특정 섹션 원문을 읽는다 (표는 마크다운)",
          {"doc_id": _STR, "path": _STR}, ["doc_id", "path"]),
    _spec("search_chunks", "목차로 찾기 어려운 주제를 하이브리드 검색한다. "
                           "기간(year/ym)을 주면 랭킹이 크게 좋아진다",
          {"query": _STR, "corp_name": _STR, "top_k": _INT,
           "year": {**_INT, "description": "접수 연도 (예: 2023)"},
           "ym": {**_STR, "description": "접수 연월 (예: 202301). year 보다 우선"}},
          ["query"]),
    _spec("get_history", "공시 정정 이력 체인을 추적한다",
          {"rcept_no": _STR}, ["rcept_no"]),
]

_DISPATCH = {
    "resolve_company": resolve_company_tool,
    "query_events": query_events,
    "list_filings": list_filings,
    "list_sections": list_sections,
    "read_section": read_section,
    "search_chunks": search_chunks,
    "get_history": get_history,
}


def execute(name: str, args: dict) -> dict:
    fn = _DISPATCH.get(name)
    if not fn:
        return {"error": f"알 수 없는 도구: {name}"}
    try:
        return fn(**args)
    except TypeError as exc:
        return {"error": f"인자 오류: {exc}"}
