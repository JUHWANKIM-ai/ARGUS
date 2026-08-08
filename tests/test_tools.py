from argus.agent.tools import TOOL_SPECS, execute


def test_specs_cover_seven_tools():
    names = {s["function"]["name"] for s in TOOL_SPECS}
    assert names == {
        "resolve_company", "query_events", "list_filings",
        "list_sections", "read_section", "search_chunks", "get_history",
    }


def test_specs_are_valid_function_calling_shape():
    for spec in TOOL_SPECS:
        assert spec["type"] == "function"
        fn = spec["function"]
        assert {"name", "description", "parameters"} <= set(fn)
        assert fn["parameters"]["type"] == "object"


def test_resolve_company_handles_alias():
    out = execute("resolve_company", {"name": "현대차"})
    assert out["corp_code"] == "00164742"
    assert out["corp_name"] == "현대자동차"


def test_query_events_filters_by_subtype():
    out = execute("query_events", {"corp_name": "삼성전자", "doc_subtype": "단일판매공급계약체결"})
    assert out["count"] >= 1
    assert all(e["doc_subtype"] == "단일판매공급계약체결" for e in out["events"])


def test_query_events_defaults_to_latest_only():
    out = execute("query_events", {"corp_name": "삼성전자"})
    assert all(e.get("is_latest", True) for e in out["events"])


def test_unknown_tool_returns_error():
    assert "error" in execute("no_such_tool", {})
