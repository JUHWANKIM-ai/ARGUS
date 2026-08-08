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
