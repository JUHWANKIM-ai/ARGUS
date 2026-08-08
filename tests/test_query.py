from argus.agent.query import STOPWORDS, extract_terms


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
