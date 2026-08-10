from argus.corpus.manifest import filter_docs
from argus.corpus.normalize import (
    correction_pairs,
    exchange_correction_span,
    free_text_amount,
    holding_summary,
    major_form,
    normalize_doc,
    normalize_exchange,
    normalize_holding,
    normalize_major,
)


def _doc(rcept_no: str) -> dict:
    return next(d for d in filter_docs(doc_group="exchange") if d["rcept_no"] == rcept_no)


def _major(rcept_no: str) -> dict:
    return next(d for d in filter_docs(doc_group="major") if d["rcept_no"] == rcept_no)


def _holding(rcept_no: str) -> dict:
    return next(d for d in filter_docs(doc_group="holding") if d["rcept_no"] == rcept_no)


def test_normalize_extracts_amount_and_ratio():
    rec = normalize_exchange(_doc("20230131800162"))
    assert rec["amount"] == 97_000_000_000
    assert rec["ratio"] == 5.37
    assert rec["ratio_base"] == "매출액"


def test_normalize_extracts_counterparty_and_period():
    rec = normalize_exchange(_doc("20230131800162"))
    assert "Hyundai Electric America" in rec["counterparty"]
    assert rec["period_start"] == "2023-01-30"
    assert rec["period_end"] == "2024-10-31"


def test_normalize_carries_manifest_identity():
    rec = normalize_exchange(_doc("20230131800162"))
    assert rec["doc_id"] == "exchange_20230131800162"
    assert rec["corp_name"] == "HD현대일렉트릭"
    assert rec["is_correction"] is False
    assert rec["related"] == []


def test_contract_record_is_not_withheld():
    rec = normalize_exchange(_doc("20230131800162"))
    assert rec["schema_matched"] is True
    assert rec["withheld"] is False


# --- 신규시설투자등 43건 회수 --------------------------------------------


def test_new_facility_investment_amount_is_recovered():
    """`투자금액(원)` 라벨은 43/43 전건에 있다. `계약금액`만 찾던 탓에 0건이었다."""
    rec = normalize_exchange(_doc("20240424800596"))
    assert rec["amount"] == 5_296_200_000_000
    assert rec["amount_type"] == "투자금액"
    assert rec["schema_matched"] is True


def test_new_facility_investment_ratio_base_is_equity_not_revenue():
    """이 서식의 비율은 `자기자본대비(%)` 다. 매출액 대비로 라벨링하면 뜻이 틀린다."""
    rec = normalize_exchange(_doc("20240424800596"))
    assert rec["ratio"] == 9.90
    assert rec["ratio_base"] == "자기자본"


def test_new_facility_investment_uses_investment_target_as_subject():
    rec = normalize_exchange(_doc("20240424800596"))
    assert "청주 M15X" in rec["title"]


# --- 공시유보 60건: 파싱 실패가 아니라 데이터 상한 -------------------------


def test_withheld_disclosure_is_flagged_not_counted_as_parse_failure():
    """계약금액이 `-` 인 60건은 서식이 정상이고 발행사가 값을 비공개한 것이다.

    `schema_matched=True` 로 억지로 바꾸면 Phase H 오류분석이 '파싱 실패'와
    '원본에 값이 없음' 을 구분하지 못한다. 별도 플래그로 나눈다.
    """
    rec = normalize_exchange(_doc("20240126800734"))
    assert rec["amount"] is None
    assert rec["withheld"] is True
    assert rec["schema_matched"] is False
    assert rec["reserved_reason"] == "경영상 비밀유지"


# --- 투자판단: 자유서술 금액 ----------------------------------------------


def test_free_text_amount_converts_korean_magnitude():
    assert free_text_amount(["투자 금액: 5.9조원"]) == (5_900_000_000_000, "투자금액")


def test_free_text_amount_reads_plain_won():
    assert free_text_amount(["계약 금액 : 91,369,732,137원"]) == (91_369_732_137, "계약금액")


def test_free_text_amount_rejects_foreign_currency():
    """`$58억` 은 달러다. 원화 필드에 넣으면 금액이 통째로 틀린다."""
    assert free_text_amount(["투자금액 : $58억"]) is None
    assert free_text_amount(["투자금액 : 58억 달러"]) is None


def test_free_text_amount_ignores_bare_number_without_unit():
    """단위 없는 맨숫자는 금액이라 단정할 수 없다."""
    assert free_text_amount(["공급가액 2"]) is None


def test_free_text_amount_takes_last_cell_so_corrections_use_new_value():
    """정정 표는 `[항목, 정정전, 정정후]` 순이라 앞에서부터 읽으면 옛 값을 집는다."""
    row = ["총 사업비 계약금액", "계약금액 16,003,671,000원", "계약금액 14,904,338,000원"]
    assert free_text_amount(row)[0] == 14_904_338_000


def test_investment_judgment_amount_is_recovered():
    rec = normalize_exchange(_doc("20241220800005"))
    assert rec["amount"] == 5_900_000_000_000
    assert rec["schema_matched"] is True


def test_clinical_trial_disclosure_has_no_amount_and_stays_unmatched():
    """임상시험 공시는 애초에 금액이 없다. 없는 값을 만들어내지 않는다."""
    rec = normalize_exchange(_doc("20230315902426"))
    assert rec["amount"] is None
    assert rec["schema_matched"] is False
    assert rec["withheld"] is False


# --- 복합단위 `1조 1,500억원` (§2b 정규화 결함 — 이마트) -----------------------
# 앞 단위에서 끊으면 1조가 되고 1,500억이 통째로 사라진다. 실측 11건이 이 모양이다
# (현대건설 7 · 삼성SDI 2 · 이마트 1 · 대우건설 1).


def test_free_text_amount_adds_compound_magnitude_tail():
    """`1조 1,500억원` 은 1조가 아니다. 실측 이마트 20241114800538 의 매매대금."""
    assert free_text_amount(["5) 매매대금 : 1조 1,500억원"])[0] == 1_150_000_000_000
    assert free_text_amount(["- 투자 금액 : 2조 2,930억 원"])[0] == 2_293_000_000_000


def test_free_text_amount_compound_tail_requires_won_suffix():
    """꼬리가 `원` 으로 끝나지 않으면 붙이지 않는다 — `100만주` 는 주식수다.

    없는 값을 만드는 것이 값이 비는 것보다 나쁘다는 원칙이 여기서도 그대로다.
    """
    assert free_text_amount(["계약금액 5,000억 100만주"])[0] == 500_000_000_000


def test_free_text_amount_compound_tail_requires_descending_units():
    """`1억 2조원` 같은 오름차순은 복합단위가 아니다. 뒤 조각을 붙이면 값이 폭주한다."""
    assert free_text_amount(["투자금액 1억 2조원"])[0] == 100_000_000


# --- 기재정정 표를 필드 추출에서 뺀다 (§2b 정규화 결함 — NAVER · 크래프톤) ------
# exchange 는 major·holding 과 달리 `body_rows()` 를 쓰지 않고 있었다. 정정 표의
# 3칸 행 `[항목, 정정전, 정정후]` 에서 정정 **전** 블록이 `_labels` 의 라벨이 되고
# 값으로는 정정 후 블록의 첫 숫자가 잡힌다.


def test_exchange_correction_span_is_found_in_kind_correction_form():
    """실측 exchange 1,469건: 정정본 631건 전건 검출 · 원공시 838건 오검출 0."""
    from argus.corpus.html_doc import extract_rows, read_document
    from argus.corpus.normalize import _doc_path, exchange_body_rows

    rows = extract_rows(read_document(_doc_path(_doc("20260130800888"))))
    assert exchange_correction_span(rows) == (5, 9)
    body = exchange_body_rows(rows)
    # major·holding 의 `_is_body_start`(귀중·회 사 명)는 exchange 에 없다. 그걸 쓰면
    # 구간 끝이 문서 끝이 되어 본문이 통째로 날아간다 — 본문이 남아 있는지 못 박는다.
    assert any(row and row[0].startswith("1. 제목") for row in body)
    assert len(body) == len(rows) - 4


def test_exchange_correction_span_is_none_for_original_filing():
    from argus.corpus.html_doc import extract_rows, read_document
    from argus.corpus.normalize import _doc_path, exchange_body_rows

    rows = extract_rows(read_document(_doc_path(_doc("20250918800247"))))
    assert exchange_correction_span(rows) is None
    assert exchange_body_rows(rows) == rows


def test_naver_wallapop_chain_amount_follows_the_latest_correction():
    """§2b 결함 #1 — 3본 체인의 `amount` 가 원공시 값에 멈춰 있었다 (Q-099).

    2차 정정본의 정정 **후** 칸은 `902,863,854,765` 로 단위(`원`)를 빼먹었다. 그래서
    단위 없는 숫자를 기각하는 규칙에 걸리고, 같은 행의 정정 **전** 칸
    `903,559,577,175원` 으로 흘러내렸다. 본문에는 `902,863,854,765원` 이 있다.
    """
    assert normalize_exchange(_doc("20260130800888"))["amount"] == 902_863_854_765
    # 1차 정정은 거래종결일만 바꿨다 — 그때 금액은 정말로 원공시 값이다.
    assert normalize_exchange(_doc("20250930800838"))["amount"] == 903_559_577_175
    assert normalize_exchange(_doc("20250918800247"))["amount"] == 903_559_577_175


def test_krafton_correction_does_not_invent_a_ratio():
    """§2b 결함 #2 — 정정본 `ratio` 가 1.0 이었다 (본문은 2.93%).

    `자기자본대비` 가 정정 전 서술 블록에서 걸리고, 값으로 정정 후 블록 첫머리
    `(1) 조합명` 의 **1** 이 잡혔다. 이 서식에는 비율 칸이 아예 없으므로 None 이
    정답이다 — 원공시도 None 이다. 2.93% 는 자유서술문 안에 있어 회수 대상이 아니다.
    """
    assert normalize_exchange(_doc("20260306801011"))["ratio"] is None
    assert normalize_exchange(_doc("20251219800469"))["ratio"] is None


def test_emart_ssg_correction_amount_reads_the_compound_unit():
    """§2b 결함 #3 — 본문 '1조 1,500억원' 이 1조로 잘려 있었다 (Q-087 근거)."""
    assert normalize_exchange(_doc("20241114800538"))["amount"] == 1_150_000_000_000


def test_amorepacific_recent_sales_mismatch_is_in_the_source_document():
    """§2b 결함 #4 — **우리 결함이 아니다.** 원문이 문서 안에서 100만원 어긋난다.

    정정 표(행 7)는 `4,134,932,512,111`, 본문(행 16)은 `4,134,931,512,111` 이다.
    최근매출액은 정규화 스키마의 필드가 아니라 고칠 대상이 없다 — 고를 수 있는 값이
    둘인데 어느 쪽이 옳은지는 원문이 말해 주지 않는다. 기각을 실행 가능한 사실로 남긴다.
    """
    from argus.corpus.html_doc import extract_rows, read_document
    from argus.corpus.normalize import _doc_path

    rows = extract_rows(read_document(_doc_path(_doc("20240228800335"))))
    flat = " ".join(cell for row in rows for cell in row)
    assert "4,134,932,512,111" in flat          # 정정 표
    assert "4,134,931,512,111" in flat          # 본문
    # 정정 표를 빼도 계약금액·비율은 본문에 그대로 있다 — 이 문서가 회귀 감시점이다.
    rec = normalize_exchange(_doc("20240228800335"))
    assert rec["amount"] == 209_606_962_407
    assert rec["ratio"] == 5.07


# --- 정정 표 제외의 부수 효과 — 회귀 감시점 ------------------------------------
# 실측 173/1469 건이 바뀌고 대부분이 결함 해소다. 대표 사례를 못 박아 둔다.


def test_correction_footnote_no_longer_leaks_into_ratio_as_an_absolute_amount():
    """`- 매출액 대비: 905200000000.0%` — 90건이 이 모양이었다 (Q-023 근거 3건 포함)."""
    assert normalize_exchange(_doc("20250605800294"))["ratio"] == 12.9
    assert normalize_exchange(_doc("20251204800395"))["ratio"] == 12.8


def test_correction_footnote_no_longer_leaks_into_amount_as_a_list_number():
    """`- 계약금액: 2원 (0억원)` — 정정 후 서술 블록 첫머리의 순번이 금액이었다."""
    rec = normalize_exchange(_doc("20240628800803"))
    assert rec["amount"] == 400_000_000_000
    assert rec["counterparty"] == "두산에너빌리티㈜"


def test_withheld_amount_in_a_correction_is_not_masked_by_a_footnote_number():
    """NC 20250905800516 은 계약금액이 `-` 이고 유보사유가 붙은 문서다.

    정정 표에서 `24` 를 집어 `schema_matched=True` 로 나가 있었다 — 파싱 실패도
    아니고 값이 있는 것도 아닌, **거짓 회수**다.
    """
    rec = normalize_exchange(_doc("20250905800516"))
    assert rec["amount"] is None
    assert rec["withheld"] is True
    assert rec["schema_matched"] is False
    assert rec["counterparty"].startswith("- Shenzhen Tencent")


def test_correction_footnote_no_longer_overwrites_the_contract_period():
    """대우건설 20241114802520 은 `기간: 2024-09-06 ~ 2024-09-06 (0년 0개월)` 이었다."""
    assert normalize_exchange(_doc("20241114802520"))["period_end"] == "2028-05-31"


def test_correction_footnote_no_longer_becomes_the_title():
    """삼성전자 20250731800028 — 제목이 유보 안내문이었다 (Q-025 근거)."""
    assert "반도체 위탁생산 공급계약" in normalize_exchange(_doc("20250731800028"))["title"]


# =============================================================================
# major — 주요사항보고서는 서식이 28종이고 서식마다 필드가 다르다
# =============================================================================


def test_major_form_is_read_from_report_name():
    """manifest 의 doc_subtype 은 major 598건 전부 None 이다. 서식은 report_nm 괄호 안에 있다."""
    assert major_form("주요사항보고서(유상증자결정)") == "유상증자결정"
    assert major_form("[기재정정]주요사항보고서(전환사채권발행결정)") == "전환사채권발행결정"


# --- 유상증자결정: 자금조달의 목적 (Q-014 · Q-022) ---------------------------


def test_rights_offering_extracts_funding_purposes():
    """Q-022 골든: 시설자금 4,541억 · 타법인 증권취득자금 1조 5,460억."""
    rec = normalize_major(_major("20250314000027"))
    assert rec["form"] == "유상증자결정"
    assert rec["fields"]["시설자금"] == "454,100,000,000"
    assert rec["fields"]["타법인 증권취득자금"] == "1,546,013,200,000"


def test_rights_offering_amount_is_the_sum_of_funding_purposes():
    """Q-022 는 '총 조달 규모'를 묻는다. 항목별 금액만 있으면 합계를 매번 LLM 이 만들어야 한다."""
    rec = normalize_major(_major("20250314000027"))
    assert rec["amount"] == 2_000_113_200_000
    assert rec["amount_type"] == "조달금액"
    assert rec["schema_matched"] is True


def test_rights_offering_extracts_new_shares_and_method():
    rec = normalize_major(_major("20250314000027"))
    assert rec["fields"]["신주의 종류와 수(보통주식)"] == "11,821,000"
    assert rec["fields"]["증자방식"] == "주주배정후 실권주 일반공모"


def test_rights_offering_issue_price_survives_nested_subheader():
    """`6. 신주 발행가액 | 예정발행가 | 보통주식(원) | 169,200 | 확정예정일 | ...` 은
    값 칸이 마지막이 아니다. row[-1] 만 보면 발행가 대신 날짜를 집는다."""
    assert normalize_major(_major("20250314000027"))["fields"]["신주 발행가액"] == "169,200"
    assert normalize_major(_major("20241030000098"))["fields"]["신주 발행가액"] == "670,000"


def test_korea_zinc_rights_offering_facility_fund():
    """Q-014 골든: 고려아연 시설자금 135,052,488,000원."""
    rec = normalize_major(_major("20241030000098"))
    assert rec["fields"]["시설자금"] == "135,052,488,000"
    assert rec["fields"]["채무상환자금"] == "2,300,000,000,000"


# --- 전환사채권발행결정 (Q-010) ----------------------------------------------


def test_convertible_bond_extracts_face_value():
    """Q-010 골든: 권면(전자등록)총액 50,182,840,320원."""
    rec = normalize_major(_major("20250522000332"))
    assert rec["form"] == "전환사채권발행결정"
    assert rec["amount"] == 50_182_840_320
    assert rec["amount_type"] == "권면(전자등록)총액"


def test_convertible_bond_extracts_conversion_terms():
    """전환가액·전환주식수는 별개 행이고 주식수는 rowspan 하위 행에 있다."""
    fields = normalize_major(_major("20250522000332"))["fields"]
    assert fields["전환가액"] == "37,527"
    assert fields["전환에 따라 발행할 주식수"] == "1,337,246"


def test_convertible_bond_extracts_rates_and_maturity():
    fields = normalize_major(_major("20250522000332"))["fields"]
    assert fields["표면이자율"] == "0.0"
    assert fields["만기이자율"] == "0.0"
    assert fields["사채만기일"] == "2028년 05월 30일"
    assert fields["타법인 증권취득자금"] == "50,182,840,320"


# --- 자기주식 서식: 보통주식/기타주식이 형제 행으로 갈라진다 -------------------


def test_treasury_disposal_amount():
    rec = normalize_major(_major("20250523000601"))
    assert rec["form"] == "자기주식처분결정"
    assert rec["amount"] == 287_545_592_500
    assert rec["amount_type"] == "처분예정금액"


def test_treasury_acquisition_amount_sums_common_and_other_shares():
    """삼성전자 3조 자사주는 보통주 2,682,737,598,000 + 기타주 317,262,452,400 이다.
    보통주만 세면 3,173억이 통째로 빠진다."""
    rec = normalize_major(_major("20241115000375"))
    assert rec["amount"] == 3_000_000_050_400
    assert rec["amount_type"] == "취득예정금액"


# --- 금액이 없는 서식은 금액을 만들어내지 않는다 -----------------------------


def test_form_without_amount_keeps_amount_none_but_still_matches_schema():
    """해외상장폐지·소송 서식에는 금액 칸이 자체가 없다. 없는 값을 지어내지 않는다."""
    rec = normalize_major(_major("20241031000508"))
    assert rec["amount"] is None
    assert rec["withheld"] is False
    assert rec["fields"]                      # 서식 고유 필드는 뽑혔다


# --- 기재정정: '정정 전 -> 정정 후' (Q-030) ----------------------------------


def test_trust_termination_amount_is_the_pre_termination_contract():
    """`해지 후` 칸은 43건 전부 `-` 다. 그 칸만 보면 신탁해지 서식 전체가 금액 0 이 된다."""
    rec = normalize_major(_major("20230424000440"))
    assert rec["amount"] == 30_000_000_000
    assert rec["amount_type"] == "해지 전 계약금액"


def test_major_period_is_only_read_from_forms_that_have_one():
    """유상증자에는 '기간' 칸이 없다. 아무 시작일이나 주우면 청약예정일이 계약기간이 된다."""
    rec = normalize_major(_major("20250314000027"))
    assert rec["period_start"] is None and rec["period_end"] is None
    # 자기주식 취득은 '취득예상기간' 이 실제로 있다
    treasury = normalize_major(_major("20241115000375"))
    assert treasury["period_start"] == "2024-11-18"
    assert treasury["period_end"] == "2025-02-17"


def test_correction_table_is_excluded_from_field_lookup():
    """정정 표 한 행이 자금조달 블록을 통째로 담아 6개 필드가 같은 덩어리를 집었다.
    실측: 한화에어로 20250408000012 조달금액이 7,000억 × 6 = 4조 2,001억으로 부풀었다."""
    rec = normalize_major(_major("20250408000012"))
    assert rec["fields"]["시설자금"] == "700,020,800,000"
    assert rec["fields"]["타법인 증권취득자금"] == "1,600,000,000,000"
    assert rec["amount"] == 2_300_020_800_000


def test_correction_table_is_found_behind_a_submission_history_table():
    """정정 표 앞에 '제출이력' 표가 붙으면 머리행이 9행째다. 앞 8행만 보면 못 찾고,
    못 찾으면 정정 표가 본문으로 섞여 들어와 자금용도 6칸이 같은 덩어리가 된다."""
    rec = normalize_major(_major("20250409001154"))
    assert rec["fields"]["시설자금"] == "354,100,000,000"
    assert "영업양수자금" not in rec["fields"]      # 값이 '-' 인 칸은 아예 안 싣는다
    assert rec["amount"] == 1_728_230_200_000


def test_correction_pairs_read_last_two_cells():
    """정정사유가 rowspan 이면 칸 수가 줄어든다. 항상 끝 두 칸이 (정정 전, 정정 후)다."""
    rows = [
        ["항 목", "정정사유", "정 정 전", "정 정 후"],
        ["1. 처분예정주식(주)", "조건 확정", "17,759,040", "20,126,911"],
        ["3. 처분예정금액(원)", "1,974,450,000,000", "2,237,710,000,000"],
        ["금융위원회 / 한국거래소 귀중", "2023년 04월 04일"],
    ]
    assert correction_pairs(rows) == [
        ("1. 처분예정주식(주)", "17,759,040", "20,126,911"),
        ("3. 처분예정금액(원)", "1,974,450,000,000", "2,237,710,000,000"),
    ]


def test_withdrawn_rights_offering_keeps_the_correction_verdict():
    """Q-030: 원공시만 보면 '증자 진행 중'으로 오답한다. 판정은 정정본의 '정정 후' 칸에 있다."""
    rec = normalize_major(_major("20241113000148"))
    assert rec["is_correction"] is True
    assert any("철회" in after for _, _, after in rec["corrections"])


# =============================================================================
# holding — 요약정보 블록. 라벨은 있다 (실측 1,083건 중 99.7%)
# =============================================================================


def test_holding_summary_reads_prev_and_current_from_column_positions():
    """`직전/이번 보고서` 행의 라벨은 행 자신이 아니라 **머리행**에 있다.
    row[-1] 을 값으로 쓰는 라벨:값 계약으로는 비율만 잡히고 주식수가 사라진다."""
    rows = [
        ["요약정보"],
        ["보고구분", "변동"],
        ["보유주식등의 수 및 보유비율", "보유주식등의 수", "보유비율"],
        ["직전 보고서", "196,452,737", "30.67"],
        ["이번 보고서", "196,488,145", "30.68"],
        ["※ 보고자 본인은 ..."],
        ["직전보고서", "2025년 06월 09일", "홀딩스", "114", "66,458,483", "29.74"],
    ]
    summary = holding_summary(rows)
    assert summary["보유주식등의 수"] == ("196,452,737", "196,488,145")
    assert summary["보유비율"] == ("30.67", "30.68")
    # 요약정보 블록 밖의 상세표('직전보고서', 9칸)를 끌어오면 안 된다
    assert summary["보유비율"][1] == "30.68"


def test_doosan_holding_report_has_shares_and_ratio():
    """Q-012 · Q-020 골든: 196,488,145주 / 30.67%."""
    rec = normalize_holding(_holding("20250310000839"))
    assert rec["shares_current"] == "196,488,145"
    assert rec["ratio_current"] == 30.67
    assert rec["shares_prev"] == "196,452,737"
    assert rec["schema_matched"] is True


def test_doosan_holding_report_has_major_contract_stake():
    """Q-012 골든: 주요계약체결 주식등 84,282,066주(13.15%)."""
    fields = normalize_holding(_holding("20250310000839"))["fields"]
    assert "84,282,066" in fields["주요계약체결 주식등의 수"]
    assert "13.15" in fields["주요계약체결 비율"]


def test_hmm_holding_report_ratio_drop():
    """Q-016 골든: 직전 36.02% · 이번 32.60% (차이 3.42%p)."""
    rec = normalize_holding(_holding("20250918000358"))
    assert rec["ratio_prev"] == 36.02
    assert rec["ratio_current"] == 32.60
    assert rec["reporter"] == "한국산업은행"


def test_nps_holding_report_shares_up_ratio_down():
    """Q-028 골든: 20,929,483주 5.16% -> 25,151,114주 3.65%."""
    rec = normalize_holding(_holding("20240108000164"))
    assert rec["shares_prev"] == "20,929,483"
    assert rec["shares_current"] == "25,151,114"
    assert rec["ratio_prev"] == 5.16
    assert rec["ratio_current"] == 3.65
    assert rec["reporter"] == "국민연금공단"


def test_holding_issuer_is_read_from_the_cell_beside_the_label():
    """`['발행회사명','두산에너빌리티(주)','발행회사와의 관계','최대주주']` 는 4칸 행이다.
    row[-1] 을 값으로 쓰면 발행회사명이 '최대주주' 가 된다."""
    fields = normalize_holding(_holding("20250310000839"))["fields"]
    assert fields["발행회사명"] == "두산에너빌리티(주)"
    assert fields["발행회사와의 관계"] == "최대주주"
    assert fields["의결권있는 발행주식 총수"] == "640,561,146"


def test_holding_correction_does_not_leak_the_correction_table():
    """정정본은 앞에 정정 표가 붙고 그 항목명이 '요약정보' 다. 요약 블록 시작을
    거기서 잡으면 보고자 자리에 문서 전문이 들어간다(실측 891자)."""
    rec = normalize_holding(_holding("20250919000067"))
    assert rec["reporter"] == "한국해양진흥공사"
    assert rec["shares_current"] == "330,867,712"
    assert len(rec["title"]) < 80


def test_holding_ratio_is_left_empty_for_the_calculation_critic():
    """이번 보유비율을 ratio 로 올리면 critic 이 '몇 %p 낮아졌나'(Q-016 정답 3.42)를
    보유비율(32.60)과 대조해 불일치로 판정한다. 두 값은 필드 한 줄에 이미 있다."""
    rec = normalize_holding(_holding("20250918000358"))
    assert rec["ratio"] is None
    assert "36.02" in rec["fields"]["보유비율"] and "32.60" in rec["fields"]["보유비율"]


def test_holding_record_carries_report_kind_and_reason():
    rec = normalize_holding(_holding("20250514001078"))
    assert rec["fields"]["보고구분"] == "변경"
    assert rec["fields"]["보고사유"] == "계약 내용의 변경"


def test_holding_record_drops_the_bogus_counterparty():
    """지분공시에 계약상대는 없다. 거래소 서식용 `_find(계약상대)` 가 '비고'를 집어넣고 있었다."""
    rec = normalize_holding(_holding("20250310000839"))
    assert rec["counterparty"] is None


# --- 문서군 디스패치 ---------------------------------------------------------


def test_normalize_doc_dispatches_by_group():
    assert normalize_doc(_major("20250314000027"))["form"] == "유상증자결정"
    assert normalize_doc(_holding("20250310000839"))["ratio_current"] == 30.67
    assert normalize_doc(_doc("20230131800162"))["amount"] == 97_000_000_000
