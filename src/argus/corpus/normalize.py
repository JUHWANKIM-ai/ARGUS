"""거래소공시 표 -> 정규화 레코드 (CONVENTION §7.3 스키마)."""
from __future__ import annotations

import re
from pathlib import Path

from argus.config import settings
from argus.corpus.dates import parse_date
from argus.corpus.html_doc import extract_rows, extract_title, read_document

_NUM = re.compile(r"-?[\d,]+(?:\.\d+)?")
_TITLE_DATE = re.compile(r"^\(\d{4}[.\-]\d{2}[.\-]\d{2}\)\s*")
_WS_ALL = re.compile(r"\s+")

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


def _labels(row: list[str]) -> str:
    """값 셀(마지막)을 뺀 라벨 부분. 공백은 지운다.

    계획서 원안은 행 전체를 이어 붙여 키워드를 찾았는데, 그러면 서술형 값이
    라벨로 오인된다. 대표 사례가 정정공시의 '3. 정정사유 | 변경계약으로 인한
    계약금액 및 계약기간 정정' 행이다. 여기에 '계약금액' 이 들어 있어서 진짜
    금액 행보다 먼저 걸리고, 숫자 대신 문장이 잡혀 222건이 통째로 비었다.
    라벨 칸만 보면 이 오염이 사라진다.

    공백을 지우는 이유는 서식마다 '매출액대비(%)' 와 '매출액 대비(%)' 가
    섞이기 때문이다.
    """
    return _WS_ALL.sub("", " ".join(row[:-1]))


def _rows_matching(rows: list[list[str]], *keywords: str):
    """라벨에 키워드가 모두 들어간 행들을 순서대로 준다."""
    for row in rows:
        if len(row) < 2:
            continue  # 값 칸이 없는 서술 행
        if all(_WS_ALL.sub("", k) in _labels(row) for k in keywords):
            yield row


def _find(rows: list[list[str]], *keywords: str) -> str | None:
    """라벨이 맞는 행의 값 셀을 돌려준다. 값이 비었으면 다음 후보를 계속 본다."""
    for row in _rows_matching(rows, *keywords):
        value = _clean(row[-1])
        if value is not None:
            return value
    return None


def _find_number(rows: list[list[str]], *keywords: str) -> float | None:
    """값이 숫자로 읽히는 첫 행을 찾는다. 숫자가 아니면 건너뛴다."""
    for row in _rows_matching(rows, *keywords):
        number = _to_number(_clean(row[-1]) or "")
        if number is not None:
            return number
    return None


def _find_date(rows: list[list[str]], *keywords: str):
    """값이 날짜로 읽히는 첫 행을 찾는다."""
    for row in _rows_matching(rows, *keywords):
        parsed = parse_date(_clean(row[-1]) or "")
        if parsed is not None:
            return parsed
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

    # 해지 서식은 '2. 해지내역 | 해지금액(원)' 을 쓴다. 체결 서식의 '계약금액' 과 별개다.
    amount, amount_type = _find_number(rows, "계약금액"), "계약금액"
    if amount is None:
        terminated = _find_number(rows, "해지금액")
        if terminated is not None:
            amount, amount_type = terminated, "해지금액"

    ratio = _find_number(rows, "매출액대비")
    start = _find_date(rows, "시작일")
    end = _find_date(rows, "종료일")
    # 라벨은 '확정일자' 가 아니라 '7. 계약(수주)일자' 다. 해지 서식은 '6. 해지일자'.
    # 어느 쪽도 없으면 접수일로 폴백한다.
    event = (_find_date(rows, "계약", "일자")
             or _find_date(rows, "수주", "일자")
             or _find_date(rows, "해지일자")
             or parse_date(doc["rcept_dt"]))

    # <title> 은 서식명('단일판매ㆍ공급계약체결')뿐이라 검색어가 되지 못한다.
    # 실제 내용은 '- 체결계약명 | 2500kVA 배전변압기 등 3,500대' 행에 있다.
    # 이걸 제목으로 올려야 "변압기 공급계약" 같은 질의에 걸린다.
    subject = (_find(rows, "체결계약명")
               or _find(rows, "해지계약명")
               or _find(rows, "계약명")
               or _find(rows, "투자대상")
               or _find(rows, "사고발생내용"))

    raw_title = title.split("/")[-1].strip() if title else doc["report_nm"]
    form_name = _TITLE_DATE.sub("", raw_title).strip() or doc["report_nm"]
    clean_title = f"{form_name} — {subject}" if subject else form_name

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
        "amount_type": amount_type,
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
