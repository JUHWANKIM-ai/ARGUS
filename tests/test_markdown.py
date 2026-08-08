from argus.corpus.markdown import to_markdown

RECORD = {
    "doc_id": "exchange_20250728800035",
    "rcept_no": "20250728800035",
    "rcept_dt": "20250728",
    "corp_name": "삼성전자",
    "sector": "반도체·전자부품",
    "doc_subtype": "단일판매공급계약체결",
    "is_correction": False,
    "title": "반도체 위탁생산 공급계약",
    "amount": 22764764160000,
    "amount_type": "계약금액",
    "ratio": 7.6,
    "ratio_base": "매출액",
    "counterparty": "글로벌 대형기업",
    "period_start": "2025-07-24",
    "period_end": "2033-12-31",
    "event_date": "2025-07-26",
    "reserved_reason": "경영상 비밀유지",
    "note": None,
    "related": [],
}


def test_markdown_header_has_company_and_title():
    md = to_markdown(RECORD)
    assert md.startswith("## ")
    assert "삼성전자" in md
    assert "반도체 위탁생산 공급계약" in md


def test_markdown_formats_amount_with_commas_and_eok():
    md = to_markdown(RECORD)
    assert "22,764,764,160,000원" in md
    assert "227,648억원" in md


def test_markdown_includes_receipt_number_for_citation():
    assert "20250728800035" in to_markdown(RECORD)


def test_markdown_includes_computed_duration():
    # 날짜 연산은 결정적 함수가 담당한다
    assert "8년 5개월" in to_markdown(RECORD)
