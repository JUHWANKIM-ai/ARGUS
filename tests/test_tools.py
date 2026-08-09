import pytest

from argus.agent.tools import CALC_TOOLS, RETRIEVAL_TOOLS, TOOL_SPECS, execute

_JSON_TYPES = {"string", "number", "integer", "boolean", "array", "object"}


def _spec(name):
    return next(s for s in TOOL_SPECS if s["function"]["name"] == name)


def test_specs_cover_seven_retrieval_tools():
    names = {s["function"]["name"] for s in TOOL_SPECS}
    assert RETRIEVAL_TOOLS <= names
    assert RETRIEVAL_TOOLS == {
        "resolve_company", "query_events", "list_filings",
        "list_sections", "read_section", "search_chunks", "get_history",
    }


def test_specs_expose_seven_calc_tools():
    """policy §4 — 계산은 Tool 이 한다. 노출되지 않은 함수는 LLM 이 대신 계산한다."""
    names = {s["function"]["name"] for s in TOOL_SPECS}
    assert CALC_TOOLS == {
        "calc_pct_change", "calc_share", "calc_total", "calc_compare",
        "calc_period", "calc_cagr", "calc_mean",
    }
    assert CALC_TOOLS <= names


def test_specs_have_no_duplicate_names():
    names = [s["function"]["name"] for s in TOOL_SPECS]
    assert len(names) == len(set(names)) == 14


def test_specs_are_valid_function_calling_shape():
    for spec in TOOL_SPECS:
        assert spec["type"] == "function"
        fn = spec["function"]
        assert {"name", "description", "parameters"} <= set(fn)
        assert fn["description"].strip()
        params = fn["parameters"]
        assert params["type"] == "object"
        props = params["properties"]
        assert isinstance(props, dict)
        for prop, schema in props.items():
            assert schema.get("type") in _JSON_TYPES, f"{fn['name']}.{prop}"
            if schema["type"] == "array":
                assert schema["items"]["type"] in _JSON_TYPES, f"{fn['name']}.{prop}"
        assert set(params["required"]) <= set(props), fn["name"]


def test_calc_specs_declare_every_argument_required():
    """계산 도구는 인자가 빠지면 계산 자체가 성립하지 않는다."""
    for name in CALC_TOOLS:
        params = _spec(name)["function"]["parameters"]
        assert set(params["required"]) == set(params["properties"]), name


def test_every_spec_is_dispatchable():
    for spec in TOOL_SPECS:
        out = execute(spec["function"]["name"], {})
        assert "알 수 없는 도구" not in out.get("error", "")


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


def test_search_chunks_accepts_period_filter():
    """기간 필터가 랭킹을 좌우한다 — corp_name 만으로는 골든이 15/26 이었다."""
    out = execute("search_chunks", {"query": "변압기", "corp_name": "HD현대일렉트릭",
                                    "ym": "202301", "top_k": 5})
    assert "error" not in out
    assert out["count"] >= 1
    assert out["chunks"][0]["doc_id"] == "exchange_20230131800162"


def test_search_chunks_spec_exposes_period():
    props = _spec("search_chunks")["function"]["parameters"]["properties"]
    assert "year" in props and "ym" in props


# --- F-2. 계산 도구가 execute() 로 실제 호출되는지 ---

def test_execute_calc_pct_change():
    out = execute("calc_pct_change", {"before": 6.5e12, "after": 8.1e12})
    assert out["rounded"] == 24.6
    assert out["unit"] == "%"


def test_execute_calc_share():
    out = execute("calc_share", {"part": 1_301_282, "whole": 3_336_059})
    assert out["rounded"] == 39.0
    assert out["unit"] == "%"


def test_execute_calc_total():
    out = execute("calc_total", {"values": [10.2, 0.6, 0.5]})
    assert out["rounded"] == 11.3


def test_execute_calc_mean():
    out = execute("calc_mean", {"values": [10.2, 0.6, 0.5]})
    assert out["rounded"] == 3.8


def test_execute_calc_cagr():
    out = execute("calc_cagr", {"begin": 100, "end": 121, "years": 2})
    assert out["rounded"] == 10.0
    assert out["unit"] == "%"


def test_execute_calc_compare():
    out = execute("calc_compare", {"a": 11.2, "b": 8.5})
    assert out["larger"] == "a"
    assert round(out["diff"], 1) == 2.7


def test_execute_calc_period_matches_hcx_failure_case():
    """실측: HCX-005 가 Direct·CoT 둘 다 9년 5개월로 오답낸 문항."""
    out = execute("calc_period", {"start": "2025-07-24", "end": "2033-12-31"})
    assert out["value"] == "8년 5개월"
    assert out["years"] == 8 and out["months"] == 5


def test_execute_calc_period_accepts_korean_dates():
    out = execute("calc_period", {"start": "2025년 7월 24일", "end": "2033년 12월 31일"})
    assert out["value"] == "8년 5개월"


def test_execute_calc_period_reports_unparsable_dates():
    assert "error" in execute("calc_period", {"start": "미정", "end": "2033-12-31"})


@pytest.mark.parametrize("name,args", [
    ("calc_pct_change", {"before": 0, "after": 100}),
    ("calc_share", {"part": 1, "whole": 0}),
    ("calc_cagr", {"begin": 0, "end": 100, "years": 3}),
    ("calc_cagr", {"begin": 100, "end": 121, "years": 0}),
    ("calc_mean", {"values": []}),
])
def test_calc_tools_return_error_instead_of_raising(name, args):
    out = execute(name, args)
    assert "error" in out


def test_calc_tools_report_missing_argument():
    assert "인자 오류" in execute("calc_pct_change", {"before": 1})["error"]
