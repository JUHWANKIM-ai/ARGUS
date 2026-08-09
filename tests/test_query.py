from argus.agent.query import (
    COMMON_TERMS,
    STOPWORDS,
    STRATEGIES,
    core_terms,
    extract_terms,
    plan_hops,
    relax_period,
)


def test_removes_boilerplate_terms():
    terms = extract_terms("HD현대일렉트릭이 2023년 1월에 체결한 변압기 공급계약의 계약금액은 얼마인가?")
    assert "변압기" in terms
    for noise in ("공급계약", "계약금액", "체결", "얼마"):
        assert noise not in terms


def test_keeps_distinctive_nouns():
    terms = extract_terms("삼성전자가 2025년 7월에 체결한 반도체 위탁생산 공급계약")
    assert "반도체" in terms and "위탁생산" in terms


def test_stopwords_cover_common_forms():
    for word in ("공급계약", "계약금액", "매출액대비", "주요경영사항"):
        assert word in STOPWORDS


# --- F-3 질의 재구성 ----------------------------------------------------------

def test_core_terms_drops_measured_common_words_and_numbers():
    """DF 0.30 이상 어휘와 숫자는 문서를 가르지 못한다 — 필터를 풀 때 노이즈만 된다."""
    core = core_terms(extract_terms(
        "HD현대일렉트릭이 2023년 1월에 체결한 변압기 공급계약의 계약금액은 얼마인가?"))
    assert "변압기" in core and "현대일렉트릭" in core
    for noise in ("2023", "계약", "금액", "체결한", "얼마인"):
        assert noise not in core


def test_core_terms_keeps_discriminative_stem():
    """'해지된' 은 어미만 떨어내고 '해지' 로 살린다 — 1,469건 중 20건뿐인 변별 어휘다."""
    core = core_terms(extract_terms("2023년에 계약이 해지된 사례가 존재하는가?"))
    assert "해지" in core


def test_core_terms_returns_empty_rather_than_guessing():
    assert core_terms([]) == []
    assert core_terms(["계약", "금액", "2025"]) == []


def test_relax_period_widens_one_level_at_a_time():
    assert relax_period({"ym": "202301"}) == {"year": 2023}
    assert relax_period({"year": 2023}) == {}
    assert relax_period({}) == {}


def test_plan_hops_broadens_monotonically():
    plans = plan_hops("HD현대일렉트릭이 2023년 1월에 체결한 변압기 공급계약의 계약금액은?",
                      {"ym": "202301"})
    assert [p.strategy for p in plans] == list(STRATEGIES)
    assert [p.hop for p in plans] == [0, 1, 2, 3]
    assert plans[0].period == {"ym": "202301"}      # 좁게 시작
    assert plans[1].period == {"year": 2023}        # 기간 필터 완화
    assert plans[2].period == {}                    # 기업 필터만
    assert plans[3].use_corp is False               # 전역
    assert all(p.use_corp for p in plans[:3])
    assert [p.top_k for p in plans] == sorted(p.top_k for p in plans)


def test_plan_hops_narrows_query_as_filter_widens():
    """필터를 넓히는 hop 에서는 질의를 핵심어로 좁힌다 — 안 그러면 무관 문서가 올라온다."""
    plans = plan_hops("HD현대일렉트릭이 2023년 1월에 체결한 변압기 공급계약의 계약금액은?",
                      {"ym": "202301"})
    assert "2023" in plans[0].query
    assert "2023" not in plans[2].query and "변압기" in plans[2].query


def test_plan_hops_falls_back_to_company_when_no_terms_survive():
    plans = plan_hops("얼마인가?", {}, fallback_terms=["삼성전자"])
    assert all("삼성전자" in p.query for p in plans)


def test_common_terms_are_measured_not_guessed():
    for word in ("계약", "금액", "매출", "대비"):
        assert word in COMMON_TERMS
