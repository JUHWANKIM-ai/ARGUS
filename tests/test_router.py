from argus.agent.router import route


def test_out_of_universe_is_unanswerable():
    r = route("존재하지않는기업ZZZ의 2025년 매출액은?")
    assert r.kind == "unanswerable"
    assert r.by == "rule"


def test_two_companies_triggers_multi_compare():
    r = route("삼성전자와 SK하이닉스 중 2025년 설비투자가 더 큰 곳은?")
    assert r.kind == "multi_compare"
    assert set(r.companies) == {"삼성전자", "SK하이닉스"}


def test_single_lookup_is_low_cost_path():
    r = route("삼성전자의 2025년 연결기준 매출액은 얼마인가?")
    assert r.kind == "closed_lookup"
    assert r.use_cot is False          # 단순 조회에 CoT 는 이득 없음


def test_comparison_over_time_is_complex():
    r = route("삼성전자의 2023년 사업보고서와 2025년 사업보고서를 비교했을 때 핵심 사업은 어떻게 변화했는지 설명해줘")
    assert r.kind == "complex_reason"
    assert r.use_sc is False           # open-ended 는 self-consistency 부적합


def test_year_extraction():
    r = route("삼성전자의 2025년 매출액은?")
    assert 2025 in r.years


def test_closed_calc_enables_sc_and_cot():
    r = route("삼성전자의 2024년 대비 2025년 영업이익 증가율은 몇 %인가?")
    assert r.use_cot is True
    assert r.use_sc is True


def test_corpus_wide_existence_query_is_not_unanswerable():
    """기업명이 없어도 '~존재하는가' 는 전역 질의다. 근거 404건이 실재한다."""
    r = route("2023년에 계약이 해지된 사례가 존재하는가?")
    assert r.kind != "unanswerable"
    assert r.use_cot is True


def test_ratio_lookup_stays_on_cheap_path():
    """'매출액 대비 비율' 은 레코드에 있는 단일 값이다. CoT+SC 로 가면 비용만 뛴다."""
    r = route("삼성전자가 2025년 7월에 체결한 공급계약의 매출액 대비 비율은?")
    assert r.kind == "closed_lookup"
    assert r.use_cot is False and r.use_sc is False


def test_unknown_company_still_unanswerable():
    """전역 질의 예외가 '범위 밖 기업' 판정을 무너뜨리면 안 된다."""
    assert route("존재하지않는기업ZZZ의 2025년 매출액은?").kind == "unanswerable"
