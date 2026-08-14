import json

from argus.config import settings
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


# --- R2C: schema_matched=False 서식의 자유서술 --------------------------------
# 실측 Q-101(현대제철 20250325800001). 정형 칸이 전부 비어 근거가 **108자 · 숫자 0개**
# 인데 원문에는 `○ 투자금액 : $58억` 과 원화환산 `8조 5,080억원` 이 있다.
# 데이터 상한이 아니라 렌더링이 본문을 통째로 버린 것이었다.

INVEST_ROWS = [
    ["1. 제목", "美 전기로 제철소 건설 추진"],
    ["2. 주요내용",
     "○ 투자 개요 : 미국 자동차강판 특화 전기로 제철소 건설 ○ 장소 : 미국 루이지애나 주 "
     "○ 투자금액 : $58억 ○ 건설기간 : 2026년 3분기 ~ 2029년 1분기"],
    ["3. 이사회결의일(결정일) 또는 사실확인일", "2025-03-24"],
    ["- 사외이사 참석여부", "참석(명)", "5"],
    ["4. 기타 투자판단과 관련한 중요사항"],
    ["1) 상기 2. '투자금액' $58억을 원화 환산 시 8조 5,080억원이며, 이는 2025년 3월 24일 "
     "서울외국환중개 매매기준율(1,466.9원/달러)을 기준으로 환산한 금액입니다."],
    ["※ 관련공시", "-"],
]

INVEST = {
    "doc_id": "exchange_20250325800001",
    "rcept_no": "20250325800001",
    "rcept_dt": "20250325",
    "corp_name": "현대제철",
    "sector": "철강",
    "doc_subtype": "투자판단관련주요경영사항",
    "form": "투자판단관련주요경영사항",
    "is_correction": False,
    "title": "투자판단 관련 주요경영사항",
    "amount": None,
    "amount_type": None,
    "event_date": "2025-03-25",
    "fields": {},
    "corrections": [],
    "schema_matched": False,
    "raw_rows": INVEST_ROWS,
}


def test_free_text_lands_when_the_schema_did_not_match():
    """근거에 숫자가 한 글자도 없던 것이 이 지점이다."""
    md = to_markdown(INVEST)
    assert "$58억" in md
    assert "美 전기로 제철소 건설 추진" in md


def test_free_text_keeps_a_value_that_sits_at_the_tail_of_the_cell():
    """실측에서 `$58억` 은 240자 칸의 205번째 자리다. 앞에서 짧게 자르면 못 싣는다."""
    assert "$58억" in to_markdown(INVEST).split("- 주요내용:")[1]


def test_free_text_reads_the_row_after_a_label_only_row():
    md = to_markdown(INVEST)
    assert "8조 5,080억원" in md
    assert "1,466.9원/달러" in md


def test_schema_matched_records_never_gain_a_single_byte():
    """🔴 이 저장소에서 가장 비싼 회귀다.

    `schema_matched=True` 가 근거의 대부분(실측 3,150건 중 2,912건)이고, 한 글자라도
    바뀌면 BM25 색인 문자열이 바뀌어 **측정된 arm 이 전부 무효**가 된다. 같은 원문을
    주고 게이트만 뒤집어, 자유서술이 한 줄도 안 붙는지 본다.
    """
    matched = dict(INVEST, schema_matched=True)
    md = to_markdown(matched)
    assert "$58억" not in md
    assert "주요내용" not in md
    assert md == to_markdown(dict(INVEST, schema_matched=True, raw_rows=[]))


def test_shipped_index_still_matches_the_renderer_on_the_matched_path():
    """🔴 색인은 **박제된 값**이다 — `markdown` 이 `artifacts/*_records.jsonl` 에 들어
    있고 BM25 는 그 문자열을 그대로 색인한다. 렌더러가 색인과 갈라지면 근거와 검색이
    서로 다른 코퍼스를 보게 된다.

    `schema_matched=True` 경로만 잠근다. 반대쪽은 이번 수정으로 **일부러** 갈라져
    있고, 재빌드로만 닫힌다 (R2C 보고서 참조).
    """
    checked = 0
    for group in ("exchange", "major", "holding"):
        path = settings().artifacts_root / f"{group}_records.jsonl"
        if not path.exists():
            continue
        for line in path.open(encoding="utf-8"):
            record = json.loads(line)
            if not record.get("schema_matched"):
                continue
            assert to_markdown(record) == record["markdown"], record["rcept_no"]
            checked += 1
    assert checked > 2_000, f"잠근 레코드가 너무 적다: {checked}"


def test_free_text_does_not_repeat_what_the_header_already_says():
    """제목이 머리글로 올라간 서식에서 같은 문장을 두 번 싣지 않는다."""
    record = dict(INVEST, title="美 전기로 제철소 건설 추진")
    assert to_markdown(record).count("美 전기로 제철소 건설 추진") == 1


def test_free_text_record_stays_bounded():
    """근거가 길어지는 것 자체가 위험이다 — 실측 최대 858자로 묶여 있다."""
    fat = dict(INVEST, raw_rows=[["2. 주요내용", "가" * 5000],
                                 ["4. 기타 사항", "나" * 5000],
                                 ["5. 기타 사항", "다" * 5000],
                                 ["6. 기타 사항", "라" * 5000],
                                 ["7. 기타 사항", "마" * 5000]])
    assert len(to_markdown(fat)) < 1_500


def test_records_without_raw_rows_render_exactly_as_before():
    """옛 레코드·테스트 픽스처에 `raw_rows` 가 없어도 터지지 않는다."""
    assert to_markdown(dict(INVEST, raw_rows=[])) == to_markdown(
        {k: v for k, v in INVEST.items() if k != "raw_rows"})
