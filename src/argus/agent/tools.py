"""도구 14종 = 검색 7종 + 계산 7종.

검색 7종은 CONVENTION §7.7 의 참조 명세를 따른다.
계산 7종은 policy §4 ("LLM = Reasoning, Tool = Calculation") 을 강제하기 위해
`compute.py` 의 결정적 함수를 function calling 스펙으로 노출한 것이다.
노출되지 않은 계산 함수는 LLM 이 대신 계산하므로 §4 위반이다.
"""
from __future__ import annotations

import json
from functools import lru_cache

from argus.agent import compute
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


# --- 계산 도구 (policy §4) ---------------------------------------------------
# 반환 형태를 통일한다. `rounded` 는 답변에 그대로 쓰라고 주는 값이고
# `expr` 은 LLM 이 계산 과정을 설명할 때 쓰라고 주는 식이다 (LLM 은 재계산하지 않는다).

_fmt = compute.fmt_num


def _num_result(value: float, unit: str, expr: str, digits: int = 1) -> dict:
    return {"value": value, "rounded": round(value, digits), "unit": unit, "expr": expr}


def calc_pct_change(before: float, after: float) -> dict:
    return _num_result(compute.pct_change(before, after), "%",
                       f"({_fmt(after)} - {_fmt(before)}) / {_fmt(before)} × 100")


def calc_share(part: float, whole: float) -> dict:
    return _num_result(compute.share(part, whole), "%",
                       f"{_fmt(part)} / {_fmt(whole)} × 100")


def calc_total(values: list[float]) -> dict:
    return _num_result(compute.total(values), "",
                       " + ".join(_fmt(v) for v in values) or "0")


def calc_mean(values: list[float]) -> dict:
    return _num_result(compute.mean(values), "",
                       f"({' + '.join(_fmt(v) for v in values)}) / {len(values)}")


def calc_cagr(begin: float, end: float, years: float) -> dict:
    return _num_result(compute.cagr(begin, end, years), "%",
                       f"(({_fmt(end)} / {_fmt(begin)})^(1/{_fmt(years)}) - 1) × 100")


def calc_compare(a: float, b: float) -> dict:
    out = compute.compare(a, b)
    return {"value": out["larger"], "unit": "", "expr": f"{_fmt(a)} vs {_fmt(b)}", **out}


def calc_period(start: str, end: str) -> dict:
    """계약기간 등 두 날짜 사이의 만 년·개월.

    실측에서 HCX 가 Direct·CoT 모두 틀린 영역이다
    (2025-07-24 ~ 2033-12-31 → 8년 5개월인데 9년 5개월로 답했다).
    """
    parts = compute.period_parts(start, end)
    if parts is None:
        return {"error": f"날짜를 해석할 수 없습니다: start={start!r} end={end!r}"}
    years, months = parts
    return {"value": f"{years}년 {months}개월", "years": years, "months": months,
            "unit": "기간", "start": start, "end": end,
            "expr": f"{start} ~ {end}"}


def _spec(name: str, desc: str, props: dict, required: list[str]) -> dict:
    return {"type": "function",
            "function": {"name": name, "description": desc,
                         "parameters": {"type": "object", "properties": props,
                                        "required": required}}}


_STR = {"type": "string"}
_INT = {"type": "integer"}
_NUM = {"type": "number"}
_BOOL = {"type": "boolean"}
_NUMS = {"type": "array", "items": {"type": "number"}}

_RETRIEVAL_SPECS = [
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

# policy §4 — 수치 연산은 전부 여기를 통과해야 한다. LLM 이 직접 계산하면 안 된다.
_CALC_SPECS = [
    _spec("calc_pct_change", "증감률(%)을 계산한다. 전기 대비 증가율·감소율은 반드시 이 도구를 쓴다",
          {"before": {**_NUM, "description": "기준 시점 값 (0 불가)"},
           "after": {**_NUM, "description": "비교 시점 값"}}, ["before", "after"]),
    _spec("calc_share", "비중·구성비(%)를 계산한다. 매출액 대비 비율 등에 쓴다",
          {"part": {**_NUM, "description": "부분 값"},
           "whole": {**_NUM, "description": "전체 값 (0 불가)"}}, ["part", "whole"]),
    _spec("calc_total", "여러 수치를 합산한다. 단위는 호출자가 맞춰서 넣는다",
          {"values": {**_NUMS, "description": "합산할 수치 목록"}}, ["values"]),
    _spec("calc_mean", "여러 수치의 산술평균을 계산한다",
          {"values": {**_NUMS, "description": "평균낼 수치 목록 (비어 있으면 불가)"}}, ["values"]),
    _spec("calc_cagr", "연평균 성장률 CAGR(%)을 계산한다. 여러 해에 걸친 성장률은 이 도구를 쓴다",
          {"begin": {**_NUM, "description": "기초 값 (0 초과)"},
           "end": {**_NUM, "description": "기말 값"},
           "years": {**_NUM, "description": "경과 연수 (0 초과)"}}, ["begin", "end", "years"]),
    _spec("calc_compare", "두 수치의 대소·차이·배수를 비교한다. 순위 판단에 쓴다",
          {"a": {**_NUM, "description": "첫 번째 값"},
           "b": {**_NUM, "description": "두 번째 값"}}, ["a", "b"]),
    _spec("calc_period", "두 날짜 사이의 기간을 '몇 년 몇 개월'로 계산한다. "
                         "계약기간 산정은 반드시 이 도구를 쓴다 (직접 계산하면 틀린다)",
          {"start": {**_STR, "description": "시작일 (예: 2025-07-24)"},
           "end": {**_STR, "description": "종료일 (예: 2033-12-31)"}}, ["start", "end"]),
]

TOOL_SPECS = _RETRIEVAL_SPECS + _CALC_SPECS

RETRIEVAL_TOOLS = frozenset(s["function"]["name"] for s in _RETRIEVAL_SPECS)
CALC_TOOLS = frozenset(s["function"]["name"] for s in _CALC_SPECS)

_DISPATCH = {
    "resolve_company": resolve_company_tool,
    "query_events": query_events,
    "list_filings": list_filings,
    "list_sections": list_sections,
    "read_section": read_section,
    "search_chunks": search_chunks,
    "get_history": get_history,
    "calc_pct_change": calc_pct_change,
    "calc_share": calc_share,
    "calc_total": calc_total,
    "calc_mean": calc_mean,
    "calc_cagr": calc_cagr,
    "calc_compare": calc_compare,
    "calc_period": calc_period,
}


def execute(name: str, args: dict) -> dict:
    fn = _DISPATCH.get(name)
    if not fn:
        return {"error": f"알 수 없는 도구: {name}"}
    try:
        return fn(**args)
    except TypeError as exc:
        return {"error": f"인자 오류: {exc}"}
    except ValueError as exc:
        # 0 나눗셈·빈 목록 등 계산이 성립하지 않는 입력. 파이프라인을 죽이지 않는다.
        return {"error": f"계산 오류: {exc}"}
