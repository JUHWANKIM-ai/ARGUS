from argus.agent.router import doc_group, out_of_corpus, route


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


# --- 코퍼스 범위 밖 연도 차단 --------------------------------------------------
# 코퍼스는 2023.01~2026.03 이다. 검색 단계에서만 막으면 스텝을 태우고 나서야 한계를
# 고지한다 — 라우터에서 단락시키면 스텝이 0이다.

def test_year_before_the_corpus_is_unanswerable():
    r = route("삼성전자의 2021년 연결기준 매출액은 얼마인가?")
    assert r.kind == "unanswerable"
    assert "out_of_range" in r.reason


def test_year_after_the_corpus_is_unanswerable():
    r = route("SK하이닉스가 사업보고서에서 제시한 2027년 예상 영업이익은 얼마인가?")
    assert r.kind == "unanswerable"


def test_one_year_inside_the_range_keeps_the_question_alive():
    """'제53기(2021년)와 제57기(2025년)' 는 2025년 보고서로 답이 나온다 — 죽이면 안 된다."""
    r = route("삼성전자 DS 부문 매출액이 제53기(2021년)와 제57기(2025년) 사이에 "
              "어떻게 달라졌는지 설명해줘")
    assert r.kind != "unanswerable"
    assert r.years == [2021, 2025]


def test_relative_time_expressions_are_not_blocked():
    """'2021년 대비 최근' 처럼 기준점만 범위 밖인 질의를 죽이면 답할 수 있는 걸 못 답한다."""
    assert route("삼성전자의 2021년 대비 최근 매출액 변화는?").kind != "unanswerable"


def test_year_free_question_is_not_blocked():
    assert route("삼성전자가 체결한 반도체 위탁생산 공급계약의 계약금액은?").kind != "unanswerable"


def test_corpus_boundaries_are_inclusive():
    assert out_of_corpus([2023]) is False
    assert out_of_corpus([2026]) is False
    assert out_of_corpus([2022]) is True
    assert out_of_corpus([]) is False


# --- 문서군 판정 (= 인덱스 선택) -----------------------------------------------

def test_periodic_report_words_route_to_sections():
    assert doc_group("삼성전자 2023년 사업보고서의 부문별 매출액은?") == "periodic"
    assert doc_group("두산에너빌리티의 2026년 반기보고서에 기재된 매출액을 알려줘.") == "periodic"


def test_fiscal_term_notation_routes_to_sections():
    """'제6기 연결 매출액' 처럼 보고서를 안 짚어도 정기공시다."""
    assert doc_group("LG에너지솔루션의 제6기(2025년) 연결 매출액은?") == "periodic"


def test_holding_wins_over_the_bare_report_word():
    """지분공시가 '대량보유상황보고서' 라서 '보고서' 를 먼저 보면 지분 질의가 통째로 샌다."""
    assert doc_group("한국산업은행이 제출한 HMM 대량보유상황보고서에서 보유비율은?") == "holding"


def test_major_event_words_route_to_major():
    assert doc_group("카카오가 2025년 5월에 결의한 제14회 전환사채 발행 조건을 정리해줘") == "major"
    assert doc_group("고려아연이 결의한 유상증자는 철회되었는가?") == "major"


def test_exchange_stays_exchange():
    """기존 성능을 건드리지 않는 조건 — 거래소공시 질의는 문서군 판정이 바뀌면 안 된다."""
    for question in (
        "HD현대일렉트릭이 2023년 1월에 체결한 변압기 공급계약의 계약금액은 얼마인가?",
        "두산퓨얼셀이 2024년 6월에 공시한 연료전지 시스템 공급계약 해지의 계약상대는?",
        "SK하이닉스가 2024년 4월 24일 공시한 신규 시설투자의 투자 대상은?",
        "삼성전자가 2025년 7월 28일 공시한 반도체 위탁생산 공급계약은 정정공시가 있었는가?",
    ):
        assert doc_group(question) == "exchange", question
        assert route(question).group == "exchange"


# --- 발주처(counterparty) 라우팅 ------------------------------------------------
# 판정은 **그래프 노드 이름과의 일치**로만 한다 — 정규식으로 회사명을 추측하지 않는다.
# 노드 이름만으로는 dev 42문항 중 24문항이 걸린다(실측). 그래서 제동 둘을 함께 건다:
#   ① 문서군이 exchange 일 것   — 그래프 모집단이 거래소공시다 (§7.7 ②)
#   ② 발주 관계 어휘가 있을 것  — '계약상대'·'발주'·'수주'
# 둘을 걸면 dev 42문항 발화가 **0건**이다 (실측, LLM 0회).

def test_counterparty_query_routes_to_the_graph():
    r = route("한국수력원자력이 발주한 계약을 수주한 기업들의 계약금액을 비교해줘")
    assert r.counterparty == "한국수력원자력"


def test_counterparty_is_none_for_ordinary_company_queries():
    """노드 이름이 들어 있어도 발주 관계를 묻지 않으면 2-hop 은 오염이다."""
    assert route("삼성전자의 2025년 연결기준 매출액은 얼마인가?").counterparty is None
    assert route("삼성SDI가 2025년 3월에 결의한 유상증자의 총 조달 규모는?").counterparty is None


def test_counterparty_does_not_fire_outside_exchange():
    """그래프 모집단은 거래소공시다. 정기공시 질의에 붙이면 오답 근거가 된다.

    실측 Q-045 가 이 경계다 — '발주처·품목·계약기간·수주총액' 이라 어휘가 걸리지만
    근거는 사업보고서 '매출 및 수주상황' 섹션이다.
    """
    r = route("삼성전자가 2025년 사업보고서 '매출 및 수주상황' 에 기재한 "
              "수주 계약의 발주처·품목·계약기간·수주총액을 정리해줘.")
    assert r.group == "periodic"
    assert r.counterparty is None


def test_counterparty_rescues_a_question_with_no_universe_company():
    """발주처는 universe 밖일 수 있다(공공기관). 기업명이 없다고 단락시키면
    파트너가 전부 상장사인데도 답을 못 낸다 — 실측 Q-107·Q-109·Q-116."""
    r = route("한국중부발전을 계약상대로 하는 단일판매·공급계약을 공시한 "
              "상장사 세 곳 중, 계약금액이 가장 큰 계약을 공시한 곳은 어디인가?")
    assert r.companies == []
    assert r.counterparty == "한국중부발전"
    assert r.kind != "unanswerable"


def test_unknown_counterparty_stays_unanswerable():
    """그래프에 없는 이름은 발주처가 아니다. 어휘만으로 살려 주면 안 된다."""
    r = route("존재하지않는발주처ZZZ가 발주한 계약의 계약상대는 어디인가?")
    assert r.counterparty is None
    assert r.kind == "unanswerable"
