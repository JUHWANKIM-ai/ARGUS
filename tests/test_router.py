from pathlib import Path

import pytest

from argus.agent.router import doc_group, out_of_corpus, route


_GRAPH_PATH = Path(__file__).resolve().parents[1] / "artifacts/counterparty_graph.json"


@pytest.fixture(autouse=True)
def ensure_counterparty_graph():
    """산출물이 없으면 값싸게 재생성한다 — `tests/test_tools.py::ensure_counterparty_graph`
    와 같은 패턴 (`14b0953` 에서 같은 사유로 도입됐다).

    `artifacts/` 는 gitignore 대상이라 신규 클론·격리 워크트리·CI 에는 파일이 없다.
    발주처 테스트는 그 경우 **skip 이 아니라 하드 실패**한다 — 라우터가 빈 노드 목록을
    받아 발주처를 하나도 못 찾기 때문이다.

    만든 직후 **캐시를 푼다.** `router._counterparty_nodes` 와 `tools._counterparty_graph`
    가 둘 다 `lru_cache` 라, 파일이 없던 시점에 한 번이라도 불리면 빈 값이 굳어
    **뒤에 파일을 만들어도 안 풀린다** (파일 실행 순서에 따라 재현된다).
    """
    if _GRAPH_PATH.exists():
        return
    from scripts.build_counterparty_graph import RECORDS_PATH, build_graph
    if not RECORDS_PATH.exists():
        pytest.skip(f"{RECORDS_PATH} 이 없어 counterparty_graph.json 을 재생성할 수 없다.")
    build_graph(_GRAPH_PATH)

    from argus.agent import router as _router
    from argus.agent import tools as _tools
    _tools._counterparty_graph.cache_clear()
    _router._counterparty_nodes.cache_clear()


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
# 이름은 **그래프 노드와의 일치**로만 정한다 — 정규식으로 회사명을 추측하지 않는다.
# 노드 이름만으로 걸리는 문항 수(분모 명시, canonical 기준 실측):
#   dev 42문항(측정 코호트) **20** · dev 78문항(파일 전체) **24** · 회귀 10문항 **3**
# 그래서 제동 둘을 함께 건다:
#   ① 문서군이 exchange 일 것 — 그래프 모집단이 거래소공시다 (§7.7 ②)
#   ② 역할 결속 — 발주 방향 표지('계약상대'·'발주')가 **노드 이름 바로 뒤**에 올 것
# 둘을 걸면 dev 42 · dev 78 · 회귀 10 발화가 전부 **0건**이다 (실측, LLM 0회).

def test_counterparty_query_routes_to_the_graph():
    r = route("한국수력원자력이 발주한 계약을 수주한 기업들의 계약금액을 비교해줘")
    assert r.counterparty == "한국수력원자력"


def test_counterparty_is_none_for_ordinary_company_queries():
    """노드 이름이 들어 있어도 발주 관계를 묻지 않으면 2-hop 은 오염이다."""
    assert route("삼성전자의 2025년 연결기준 매출액은 얼마인가?").counterparty is None
    assert route("삼성SDI가 2025년 3월에 결의한 유상증자의 총 조달 규모는?").counterparty is None


def test_a_company_that_received_the_order_is_not_the_counterparty():
    """어휘를 문장 어디서나 찾으면 **받는 쪽**이 발주처로 잡힌다.

    셋 다 노드이고 '수주'·'계약금액' 어휘도 있지만 셋 다 공시한 쪽이다. 노드 뒤에
    붙은 것이 '가 … 수주한' 이면 발주처가 아니다 — 역할 결속이 이걸 가른다.
    """
    r = route("삼성전자와 SK하이닉스와 현대자동차가 2024년에 수주한 계약의 계약금액을 비교해줘")
    assert r.counterparty is None


def test_conjunctive_particles_do_not_bind_the_role():
    """`와`·`과` 는 격조사가 아니라 **접속·공동격**이다.

    "X와 발주처 Y" 는 "X 그리고 발주처 Y" 라서 발주처는 Y 인데, 조사라는 이유로
    결속 목록에 넣으면 앞의 X 가 잡힌다. 셋 다 앞의 기업이 **공시한 쪽**이다.
    긴 이름 우선 정렬로는 안 막힌다 — 첫 문장에서 `한국수력원자력` 뒤에는
    `의계약관계`(≠`계약상대`)가 와서 탈락하고 `삼성전자와발주` 가 통과했다.
    """
    for question in (
        "삼성전자와 발주처 한국수력원자력의 계약 관계를 정리해줘",
        "SK하이닉스와 발주처가 겹치는 공급계약이 있는가?",
        "두산에너빌리티와 발주 구조가 같은 계약을 공시한 상장사는?",
    ):
        assert route(question).counterparty is None, question


def test_genitive_particle_still_binds_the_role():
    """`의` 는 남긴다 — '한국수력원자력의 발주' 는 관형격이라 그 노드가 발주 주체다."""
    assert route("한국수력원자력의 발주 계약을 수주한 상장사는?").counterparty         == "한국수력원자력"


def test_counterparty_survives_when_it_is_also_a_named_universe_company():
    """`route.companies` 를 통째로 배제하면 안 되는 이유.

    발주처가 질문에도 등장하는 universe 기업일 수 있다. 배제하면 겨냥한 Q-114 가
    통째로 되돌아간다 — 배제해야 하는 것은 '질문에 나온 기업' 이 아니라
    '받는 쪽으로 나온 기업' 이다.
    """
    r = route("삼성전자를 계약상대로 하는 공급계약을 공시한 상장사는 어디인가?")
    assert "삼성전자" in r.companies
    assert r.counterparty == "삼성전자"


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
