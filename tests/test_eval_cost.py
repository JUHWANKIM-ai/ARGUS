from argus.clova import CALL_LOG


def test_call_log_totals_shape():
    CALL_LOG.clear()
    t = CALL_LOG.totals()
    assert set(t) == {"calls", "prompt_tokens", "completion_tokens", "latency_s"}


def test_eval_report_includes_cost_fields():
    from scripts.eval_e2e import summarize
    rows = [{"case": "Q-1", "type": "closed", "has_schema": True,
             "answer_match": True, "has_citation": True,
             "llm_calls": 2, "prompt_tokens": 100, "completion_tokens": 50}]
    s = summarize(rows)
    assert s["total_llm_calls"] == 2
    assert s["avg_llm_calls_per_question"] == 2.0
