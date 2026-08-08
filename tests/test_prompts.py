from argus.agent.prompts import CITATION_RE, SYSTEM_ANSWER, build_answer_messages


def test_system_prompt_states_hard_rules():
    for rule in ["공시에서 확인되지 않", "접수번호", "추측"]:
        assert rule in SYSTEM_ANSWER


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
