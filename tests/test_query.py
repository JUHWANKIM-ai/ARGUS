from argus.agent.query import (
    COMMON_TERMS,
    CORRECTION_PATH,
    MAX_TIME_POINTS,
    SPLIT_TOP_K,
    STOPWORDS,
    STRATEGIES,
    core_terms,
    extract_terms,
    path_hints,
    plan_hops,
    plan_section_hops,
    relax_period,
    report_hints,
    time_points,
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
    """DF 0.30 이상 어휘와 숫자는 문서를 가르지 못한다. 필터를 풀 때 노이즈만 된다."""
    core = core_terms(extract_terms(
        "HD현대일렉트릭이 2023년 1월에 체결한 변압기 공급계약의 계약금액은 얼마인가?"))
    assert "변압기" in core and "현대일렉트릭" in core
    for noise in ("2023", "계약", "금액", "체결한", "얼마인"):
        assert noise not in core


def test_core_terms_keeps_discriminative_stem():
    """'해지된' 은 어미만 떨어내고 '해지' 로 살린다. 1,469건 중 20건뿐인 변별 어휘다."""
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
    """필터를 넓히는 hop 에서는 질의를 핵심어로 좁힌다. 안 그러면 무관 문서가 올라온다."""
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


# --- 다지점 질의 분해 ----------------------------------------------------------
# 넓히기로는 "A사 2024년 것" 과 "B사 2025년 것" 을 동시에 얻을 수 없다. 분해해야 한다.

def test_time_points_reads_every_month_not_just_the_first():
    """'2025년 3월과 2026년 3월' 은 시점이 둘이다. 앞엣것만 잡으면 뒤엣것은 영영 안 온다."""
    points = time_points("2025년 3월과 2026년 3월에 제출한 보고서를 비교해줘", [2025, 2026])
    assert points == [{"ym": "202503"}, {"ym": "202603"}]


def test_time_points_falls_back_to_years_when_months_are_absent():
    points = time_points("제53기(2021년)와 제57기(2025년) 사이의 매출", [2021, 2025])
    assert points == [{"year": 2021}, {"year": 2025}]


def test_time_points_stays_empty_for_a_single_point():
    """축이 하나면 분해하지 않는다. 걸면 지금 맞히는 단일 시점 질의가 깨진다."""
    assert time_points("2023년 1월에 체결한 계약", [2023]) == []
    assert time_points("2023년에 체결한 계약", [2023]) == []
    assert time_points("계약금액은?", []) == []


def test_time_points_deduplicates_and_is_capped():
    """같은 시점을 두 번 써도 셀은 하나다. 상한이 없으면 셀이 기업 수와 곱해져 터진다."""
    assert time_points("2025년 3월과 2025년 3월", [2025]) == []
    many = " ".join(f"20{n}년 1월" for n in range(10, 20))
    assert len(time_points(many, [])) == MAX_TIME_POINTS


def test_plan_hops_splits_only_the_first_hop():
    """분해는 hop 0 에서만 한다. 넓히는 hop 까지 곱하면 스텝이 상한을 넘는다."""
    plans = plan_hops("2025년 7월과 2024년 5월에 체결한 변압기 계약", {"ym": "202507"},
                      periods=[{"ym": "202507"}, {"ym": "202405"}])
    assert plans[0].periods == ({"ym": "202507"}, {"ym": "202405"})
    assert all(p.periods == () for p in plans[1:])
    assert plans[0].top_k == SPLIT_TOP_K       # 셀마다 넓게 받으면 근거가 부푼다


def test_plan_hops_without_split_is_byte_identical():
    """단일 시점 질의는 종전 계획 그대로여야 한다. 회귀 방지의 전제다."""
    question = "HD현대일렉트릭이 2023년 1월에 체결한 변압기 공급계약의 계약금액은?"
    assert plan_hops(question, {"ym": "202301"}) == plan_hops(
        question, {"ym": "202301"}, periods=[{"ym": "202301"}])


# --- 목차 경로 선택 (병목 B) ---------------------------------------------------

def test_path_hints_ignores_filing_meta_words():
    """'사업보고서' 의 '사업' 이 'II. 사업의 내용' 을 1위로 올리면 배당 질의가 엉뚱한 대목을 판다."""
    pool = ("II. 사업의 내용", "III. 재무에 관한 사항 > 6. 배당에 관한 사항")
    hints = path_hints("HMM이 2024년 3월 20일 제출한 2023년 사업보고서의 "
                       "제48기 보통주 주당 현금배당금은 얼마인가?", pool)
    assert hints[0] == "III. 재무에 관한 사항 > 6. 배당에 관한 사항"


def test_path_hints_ignores_the_confirmation_boilerplate():
    """'어느 보고서에서 확인했는지' 의 '확인' 이 【 전문가의 확인 】 을 1위로 올렸다 (실측 Q-046)."""
    pool = ("【 전문가의 확인 】", "II. 사업의 내용 > 4. 매출 및 수주상황")
    hints = path_hints("삼성전자 DS 부문 매출액을 각 값을 어느 보고서에서 확인했는지와 "
                       "함께 설명해줘", pool)
    assert hints[0] == "II. 사업의 내용 > 4. 매출 및 수주상황"


def test_path_hints_adds_the_summary_financials_twin():
    """같은 지표가 두 경로에 실린다. 한 경로만 보면 한쪽 기업을 놓친다 (실측 Q-049)."""
    pool = ("II. 사업의 내용 > 4. 매출 및 수주상황",
            "III. 재무에 관한 사항 > 1. 요약재무정보")
    hints = path_hints("LG에너지솔루션의 제6기 연결 매출액과 두산에너빌리티의 "
                       "제63기 연결 매출액 중 어느 쪽이 큰가?", pool)
    assert set(hints[:2]) == set(pool)


def test_path_hints_adds_the_correction_header_path():
    """'정 정 신 고 (보고)' 는 1,052 문서 중 157 건뿐이라 공통 목차 모집단에서 빠진다."""
    pool = ("III. 재무에 관한 사항 > 6. 배당에 관한 사항",)
    hints = path_hints("정정 반영 후 최종 배당금은 얼마인가?", pool)
    assert CORRECTION_PATH in hints


def test_path_hints_does_not_add_the_correction_path_without_a_cue():
    assert CORRECTION_PATH not in path_hints(
        "삼성전자 2025년 매출액은?", ("II. 사업의 내용 > 4. 매출 및 수주상황",))


def test_section_hop_zero_carries_every_path_candidate():
    """hop 0 이 1순위 경로만 보면 2순위 경로에 실린 골든은 영영 안 온다."""
    paths = ("II. 사업의 내용 > 4. 매출 및 수주상황",
             "III. 재무에 관한 사항 > 1. 요약재무정보")
    plans = plan_section_hops("연결 매출액 비교", {}, paths)
    assert plans[0].paths == paths


def test_gisu_without_a_report_type_means_the_annual_report():
    """기수는 사업연도 단위 표현이다. 실측, 종류 표기 없는 기수 문항의 골든 5/5 가 사업보고서."""
    assert report_hints("제57기 연결 매출액은?")["doc_subtype"] == "annual"
    assert report_hints("제50기 분기보고서의 매출액은?")["doc_subtype"] == "quarter"
    assert "doc_subtype" not in report_hints("2025년 매출액은?")
