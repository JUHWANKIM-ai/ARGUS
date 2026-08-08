from scripts.eval_e2e import load_cases, score


def test_evalset_loads():
    cases = load_cases()
    assert len(cases) >= 6
    assert {"question_id", "question", "type"} <= set(cases[0])


def test_score_detects_schema_violation():
    result = score({"answer": "x"}, {"gold": "", "must_cite": ""})
    assert result["has_schema"] is False


def test_score_detects_answer_and_citation():
    resp = {
        "question_id": "Q-001", "question": "q",
        "retrieved_context": "ctx", "think_trace": "t",
        "answer": "계약금액은 97,000,000,000원입니다 (접수번호 20230131800162)",
    }
    result = score(resp, {"gold": "97,000,000,000", "must_cite": "20230131800162"})
    assert result["has_schema"] and result["answer_match"] and result["has_citation"]


def test_unanswerable_case_requires_limitation_phrase():
    resp = {
        "question_id": "Q-006", "question": "q",
        "retrieved_context": "", "think_trace": "t",
        "answer": "공시에서 확인되지 않습니다.",
    }
    assert score(resp, {"gold": "확인", "must_cite": ""})["answer_match"]
