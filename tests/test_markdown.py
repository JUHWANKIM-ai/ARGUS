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


# --- major/holding 필드 렌더링 -----------------------------------------------
# Phase H 실측: 레코드에 값을 넣어도 to_markdown 이 안 실으면 LLM 은 못 본다.
# major·holding 근거 블록에 권면총액·전환가액·보유비율이 한 줄도 없던 것이 그 지점이다.

MAJOR = {
    "doc_id": "major_20250522000332",
    "rcept_no": "20250522000332",
    "rcept_dt": "20250522",
    "corp_name": "카카오",
    "sector": "인터넷·플랫폼",
    "doc_subtype": None,
    "form": "전환사채권발행결정",
    "is_correction": False,
    "title": "주요사항보고서(전환사채권발행결정)",
    "amount": 50_182_840_320,
    "amount_type": "권면(전자등록)총액",
    "fields": {"전환가액": "37,527", "전환에 따라 발행할 주식수": "1,337,246"},
    "corrections": [],
}

HOLDING = {
    "doc_id": "holding_20250310000839",
    "rcept_no": "20250310000839",
    "rcept_dt": "20250310",
    "corp_name": "두산에너빌리티",
    "doc_subtype": "대량보유상황보고서",
    "form": "대량보유상황보고서",
    "reporter": "(주)두산",
    "is_correction": False,
    "title": "주식등의대량보유상황보고서(일반) · 보고자 (주)두산",
    "fields": {
        "보유주식등의 수": "직전 196,452,737주 → 이번 196,488,145주",
        "보유비율": "직전 30.67% → 이번 30.67%",
    },
    "corrections": [],
}


def test_markdown_renders_major_form_label_not_none():
    """doc_subtype 이 None 인 major 598건이 `## [None] ...` 로 나가고 있었다."""
    md = to_markdown(MAJOR)
    assert "[None]" not in md
    assert "전환사채권발행결정" in md


def test_markdown_carries_major_fields():
    md = to_markdown(MAJOR)
    assert "50,182,840,320원" in md
    assert "37,527" in md
    assert "1,337,246" in md


def test_markdown_carries_holding_stake():
    md = to_markdown(HOLDING)
    assert "196,488,145" in md
    assert "30.67" in md
    assert "(주)두산" in md


def test_markdown_renders_correction_verdict():
    """Q-030: '유상증자결정 -> 유상증자결정 철회' 는 정정본에만 있는 판정 근거다."""
    record = dict(MAJOR, is_correction=True,
                  corrections=[("-", "유상증자결정", "유상증자결정 철회")])
    assert "유상증자결정 철회" in to_markdown(record)


def test_markdown_stays_compact():
    """근거 길이는 문항당 토큰 비용에 직결된다. 필드가 늘어도 한 문서가 폭주하면 안 된다."""
    assert len(to_markdown(MAJOR)) < 600
    assert len(to_markdown(HOLDING)) < 600
