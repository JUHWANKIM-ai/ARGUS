"""거래소공시 표 -> 정규화 레코드 (CONVENTION §7.3 스키마)."""
from __future__ import annotations

import re
from pathlib import Path

from argus.config import settings
from argus.corpus.dates import parse_date
from argus.corpus.html_doc import extract_rows, extract_title, read_document

_NUM = re.compile(r"-?[\d,]+(?:\.\d+)?")
_TITLE_DATE = re.compile(r"^\(\d{4}[.\-]\d{2}[.\-]\d{2}\)\s*")

# 계약 서식 전용 라벨에 의존하는 doc_subtype. 나머지 343건은 서식이 다르다.
CONTRACT_SUBTYPES = {"단일판매공급계약체결", "단일판매공급계약해지"}


def _to_number(text: str) -> float | None:
    match = _NUM.search(text or "")
    if not match:
        return None
    try:
        return float(match.group(0).replace(",", ""))
    except ValueError:
        return None


def _clean(value: str | None) -> str | None:
    """DART 표는 빈 값을 '-' 로 채운다. None 으로 정규화한다."""
    if value is None:
        return None
    stripped = value.strip()
    return None if stripped in {"-", "–", "—", ""} else stripped


def _find(rows: list[list[str]], *keywords: str) -> str | None:
    """라벨 셀에 키워드가 모두 들어간 행의 마지막 셀을 돌려준다."""
    for row in rows:
        joined = " ".join(row)
        if all(k in joined for k in keywords):
            return _clean(row[-1])
    return None


def _doc_path(doc: dict) -> Path:
    folder = settings().corpus_root / doc["file_path"]
    xml = sorted(folder.glob("*.xml"))
    if not xml:
        raise FileNotFoundError(f"XML 없음: {folder}")
    return xml[0]


def normalize_exchange(doc: dict) -> dict:
    html = read_document(_doc_path(doc))
    rows = extract_rows(html)
    title = extract_title(html)

    amount = _to_number(_find(rows, "계약금액") or "")
    ratio = _to_number(_find(rows, "매출액대비") or "")
    start = parse_date(_find(rows, "시작일") or "")
    end = parse_date(_find(rows, "종료일") or "")
    # 라벨은 '확정일자' 가 아니라 '7. 계약(수주)일자' 다. 없으면 접수일로 폴백한다.
    event = (parse_date(_find(rows, "계약", "일자") or "")
             or parse_date(_find(rows, "수주", "일자") or "")
             or parse_date(doc["rcept_dt"]))

    # 제목은 '법인명/서식명/(2023.01.31)서식명' 형태다. 날짜 접두어를 떼어낸다.
    raw_title = title.split("/")[-1].strip() if title else doc["report_nm"]
    clean_title = _TITLE_DATE.sub("", raw_title).strip() or doc["report_nm"]

    return {
        "doc_id": doc["doc_id"],
        "rcept_no": doc["rcept_no"],
        "rcept_dt": doc["rcept_dt"],
        "corp_code": doc["corp_code"],
        "corp_name": doc["corp_name"],
        "sector": doc["sector"],
        "doc_subtype": doc["doc_subtype"],
        "is_correction": bool(doc["is_correction"]),
        "title": clean_title,
        "amount": int(amount) if amount is not None else None,
        "amount_type": "계약금액",
        "ratio": ratio,
        "ratio_base": "매출액",
        "counterparty": _find(rows, "계약상대"),
        "period_start": start.isoformat() if start else None,
        "period_end": end.isoformat() if end else None,
        "event_date": event.isoformat() if event else None,
        "reserved_reason": _find(rows, "유보사유"),
        "note": None,
        "related": [],
        # 계약 서식이 아닌 343건(투자판단·신규시설투자)은 필드가 비게 된다.
        # 조용히 통과시키지 않고 명시적으로 표시해 Task 10에서 집계한다.
        "schema_matched": doc["doc_subtype"] in CONTRACT_SUBTYPES and amount is not None,
        "raw_rows": rows[:40],
    }
