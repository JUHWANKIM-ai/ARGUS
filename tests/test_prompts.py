from argus.agent.prompts import (CITATION_RE, SYSTEM_ANSWER, SYSTEM_ANSWER_COT,
                                 build_answer_messages)


def test_system_prompt_states_hard_rules():
    for rule in ["공시에서 확인되지 않", "접수번호", "추측"]:
        assert rule in SYSTEM_ANSWER


# --- A7 ② 시점 지정 질의 -------------------------------------------------------
# 체인 hop 이 붙은 뒤 파이프라인이 **항상 최신값으로 수렴**한다. 실측 Q-022 는
# '2025년 3월에 결의한' 으로 시점을 못박았는데 체인 최신본 값을 답했고, 채점기의
# 이유가 그대로다 — "제출답변에는 최초 공시가 아닌 최종 수정된 공시 내용만 포함".
#
# 🔴 반대 방향이 더 위험하다. Q-005·Q-006·Q-052 는 **같은 문장 안에 날짜가 있는데
# 정답이 최신값**이다 ("2023년 6월에 공시한 … 정정공시에서 얼마로 바뀌었는가"). 그래서
# 규칙의 축은 '질문에 날짜가 있는가' 가 아니라 **질문이 어느 시점의 값을 요구하는가** 다.


def test_time_anchor_rule_is_in_both_prompt_variants():
    for system in (SYSTEM_ANSWER, SYSTEM_ANSWER_COT):
        assert "최초 공시 당시" in system          # 원공시를 요구하는 표현
        assert "정정 반영 후" in system            # 최신본을 요구하는 표현
        assert "최신본" in system


def test_time_anchor_rule_keeps_latest_as_the_default():
    """시점 표현이 없으면 최신본이다 — 종전 동작을 규칙으로 못박아 둔다."""
    assert "시점 표현이 없으면" in SYSTEM_ANSWER


def test_time_anchor_rule_covers_two_point_questions():
    """증감·차이 질의는 두 시점이 다 필요하다. '최신본만' 으로 뭉개면 Q-048·Q-061 이 깨진다."""
    assert "증감" in SYSTEM_ANSWER or "차이" in SYSTEM_ANSWER


def test_build_messages_shape():
    msgs = build_answer_messages("질문", "근거", use_cot=False)
    assert msgs[0]["role"] == "system"
    assert msgs[-1]["role"] == "user"
    assert "근거" in msgs[-1]["content"]


def test_cot_flag_changes_instruction():
    plain = build_answer_messages("q", "e", use_cot=False)[0]["content"]
    cot = build_answer_messages("q", "e", use_cot=True)[0]["content"]
    assert cot != plain
    assert "단계" in cot


def test_citation_regex_matches_reference_format():
    text = "매출액은 333조원입니다 (근거: 사업보고서 2025.12, 접수번호 20260310002820, III-1 요약재무정보)"
    assert CITATION_RE.search(text)


def test_citation_regex_rejects_missing_receipt():
    assert not CITATION_RE.search("매출액은 333조원입니다 (근거: 사업보고서)")
