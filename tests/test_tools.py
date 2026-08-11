import json
from pathlib import Path

import pytest

from argus.agent.tools import (CALC_TOOLS, RETRIEVAL_TOOLS, TOOL_SPECS,
                               chain_probe, execute)

_JSON_TYPES = {"string", "number", "integer", "boolean", "array", "object"}

_ROOT = Path(__file__).resolve().parents[1]
_GRAPH_PATH = _ROOT / "artifacts/counterparty_graph.json"


@pytest.fixture(autouse=True)
def ensure_counterparty_graph():
    """산출물이 없으면 값싸게 재생성한다 — tests/test_counterparty.py::ensure_graph 와 같은 패턴.

    `artifacts/` 는 gitignore 대상이라 신규 클론·격리 워크트리에서는 파일이 없을 수 있다.
    """
    if _GRAPH_PATH.exists():
        return
    from scripts.build_counterparty_graph import RECORDS_PATH, build_graph
    if not RECORDS_PATH.exists():
        pytest.skip(
            f"{RECORDS_PATH} 이 없어 counterparty_graph.json 을 재생성할 수 없다."
        )
    build_graph(_GRAPH_PATH)


def _spec(name):
    return next(s for s in TOOL_SPECS if s["function"]["name"] == name)


def test_specs_cover_eight_retrieval_tools():
    names = {s["function"]["name"] for s in TOOL_SPECS}
    assert RETRIEVAL_TOOLS <= names
    assert RETRIEVAL_TOOLS == {
        "resolve_company", "query_events", "list_filings",
        "list_sections", "read_section", "search_chunks", "get_history",
        "query_by_counterparty",
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
    assert len(names) == len(set(names)) == 15


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


# --- 정정·후속 공시 체인 (Phase H 1위 병목) -------------------------------------
# 한화오션 잠수함 체인은 금액이 **줄어든다**. '가장 큰 값 = 최신' 규칙을 쓰면 전부 틀린다.
SUBMARINE = "20231222800809"          # 원공시 1,102,000,000,000원 (체인 최대 금액)
SUBMARINE_LATEST = "20251204800395"   # 최신 정정본 899,400,000,000원

FORD = "20241015800258"               # LG에너지솔루션 Ford 원계약 (대체됨)
FORD_CORRECTION = "20251217800853"    # related 로 이어진 정정본
FORD_TERMINATION = "20251217800800"   # related 는 비었고 ※관련공시로만 이어지는 해지


def test_get_history_orders_the_chain_by_filing_date_not_amount():
    """`is_latest` 는 체인 위치로만 정해진다. 금액으로 정하면 감소 체인에서 뒤집힌다."""
    out = execute("get_history", {"rcept_no": SUBMARINE})
    order = [c["rcept_no"] for c in out["chain"]]

    assert order == sorted(order)                       # 접수일 오름차순
    assert out["latest"] == SUBMARINE_LATEST == order[-1]
    amounts = [c["amount"] for c in out["chain"] if c["amount"]]
    assert max(amounts) != out["chain"][-1]["amount"]   # 최신본이 최대 금액이 아니다
    assert [c["is_latest"] for c in out["chain"]] == [False] * (len(order) - 1) + [True]


def test_get_history_spans_document_groups():
    """정정 체인은 문서군을 가리지 않는다 — 고려아연 유상증자 철회는 `major` 체인이다."""
    out = execute("get_history", {"rcept_no": "20241030000098"})
    assert "error" not in out
    assert "20241113000148" in [c["rcept_no"] for c in out["chain"]]


def test_get_history_carries_markdown_so_the_chain_can_become_evidence():
    """체인을 찾아 놓고 근거를 다시 조회해야 하면 스텝이 두 배가 된다."""
    out = execute("get_history", {"rcept_no": SUBMARINE})
    latest = next(c for c in out["chain"] if c["rcept_no"] == SUBMARINE_LATEST)
    assert latest["markdown"].startswith("## ")
    assert SUBMARINE_LATEST in latest["markdown"]
    assert latest["doc_id"] == f"exchange_{SUBMARINE_LATEST}"


def test_get_history_links_the_termination_by_reference_not_by_related():
    """해지 공시 20건 중 `related` 로 이어진 것은 0건이다. ※관련공시 역방향이 유일한 다리."""
    out = execute("get_history", {"rcept_no": FORD})
    assert FORD_CORRECTION in [c["rcept_no"] for c in out["chain"]]
    assert FORD_TERMINATION in [f["rcept_no"] for f in out["followups"]]
    assert all(f["kind"] == "followup" for f in out["followups"])


def test_get_history_can_leave_the_followups_out():
    out = execute("get_history", {"rcept_no": FORD, "include_followups": False})
    assert out["followups"] == []


def test_get_history_reports_an_unknown_receipt():
    assert "error" in execute("get_history", {"rcept_no": "00000000000000"})


def test_chain_probe_flags_a_superseded_document_and_its_links():
    """씨앗 판정은 도구 호출이 아니다 — 레코드 한 건만 본다 (스텝을 쓰지 않는다)."""
    probe = chain_probe(f"exchange_{FORD}")
    assert probe["is_latest"] is False
    assert FORD_CORRECTION in probe["links"]
    assert FORD_TERMINATION in probe["links"]           # 후속 해지까지 한 번에


def test_chain_probe_accepts_a_bare_receipt_number():
    assert chain_probe(FORD)["rcept_no"] == FORD


def test_chain_probe_of_a_latest_document_is_not_a_seed():
    """최신본을 씨앗으로 삼으면 지금 맞히는 문항에 무관한 체인이 붙는다 (제동 ②)."""
    assert chain_probe(f"exchange_{SUBMARINE_LATEST}")["is_latest"] is True


def test_chain_probe_of_an_unknown_document_is_empty():
    assert chain_probe("exchange_00000000000000") == {}
    assert chain_probe("") == {}


def test_get_history_spec_exposes_followups():
    props = _spec("get_history")["function"]["parameters"]["properties"]
    assert "include_followups" in props


# --- query_by_counterparty (Task 4) — 발주처 단위 fan-in/fan-out 완전 열거 -------
# BM25 자유텍스트로는 "이 허브에 몇 개사가 걸렸는가"를 물을 수단이 없다(Q-110·Q-114·
# Q-117 이 구조적으로 불가). partners 를 통째로 돌려주는 것이 이 도구의 존재 이유다.

def test_counterparty_tool_is_registered():
    names = {s.get("function", s).get("name", s.get("name")) for s in TOOL_SPECS}
    assert "query_by_counterparty" in names
    assert "query_by_counterparty" in RETRIEVAL_TOOLS
    assert len(TOOL_SPECS) == 15


def test_query_by_counterparty_returns_the_value_chain():
    out = execute("query_by_counterparty", {"name": "한국수력원자력"})
    assert out["kind"] == "public"
    assert {"두산에너빌리티", "한전기술", "현대건설"} <= set(out["partners"])
    assert out["records"], "레코드가 비면 근거로 못 쓴다"
    for r in out["records"]:
        assert r.get("rcept_no"), "근거 인용 앵커가 없다"


def test_query_by_counterparty_rejects_vague_names():
    """비실명으로 조회하면 빈 결과가 아니라 사유를 돌려준다."""
    out = execute("query_by_counterparty", {"name": "글로벌 대형기업"})
    assert out.get("error")
    assert "비실명" in out["error"]


def test_query_by_counterparty_reports_unknown_orderer():
    out = execute("query_by_counterparty", {"name": "존재하지않는가상발주처스트링"})
    assert out.get("error")


#: W3(`normalize.exchange_body_rows()`, 기재정정 표 제외) 이전에 `counterparty` 로
#: 올라오던 안내문 원문 (삼성바이오로직스 20230328801361). 정정 표 칸에만 있던 값이라
#: 지금 코퍼스에는 없다 — `artifacts/counterparty_graph.json` 의 `excluded_boilerplate`
#: 가 27종 → **0종**이 됐다 (tests/test_counterparty.py 의 `BOILERPLATE_BEFORE_W3`).
_BOILERPLATE_RAW_BEFORE_W3 = (
    "- 계약상대는 경영상 비밀 유지사유로 2023년 6월 30일 이전 공개될 예정 이며, "
    "해당 일자는 추후 변동 가능함"
)


def test_query_by_counterparty_distinguishes_boilerplate_from_vague(monkeypatch):
    """"비밀유지" 안내문은 비실명과 다른 사유(회사명이 아니라 안내문)로 거절돼야 한다.

    🔴 **이 분기는 현행 코퍼스에서 도달 불가다.** `excluded_boilerplate` 가 비어서
    (W3 이 안내문을 상류에서 지웠다) 실물 그래프로는 사유 분기를 태울 수 없고, 같은
    문자열을 넣으면 일반 사유("2회 이상 등장하는 발주처가 아닙니다")로 떨어진다.

    도달 불가라고 테스트를 지우면 **분기가 죽은 채로 썩는다** — 상류가 다시 더러워졌을
    때(정정 표 복귀·새 서식) 사유가 뭉개진 것을 아무도 모른다. 그래서 ① 지금 실물
    그래프가 비어 있다는 사실과 ② 비면 일반 사유로 떨어진다는 현재 동작, ③ 안내문이
    기록돼 있을 때는 별도 사유가 나온다는 계약을, 셋 다 고정한다.
    """
    from argus.agent import tools

    graph = json.loads(_GRAPH_PATH.read_text(encoding="utf-8"))
    # ① 상류가 깨끗해졌다 (필터가 약해진 것이 아니다).
    assert graph["excluded_boilerplate"] == []

    # ② 기록이 없으면 일반 사유로 떨어진다 — 비실명 사유와는 여전히 다르다.
    out = execute("query_by_counterparty", {"name": _BOILERPLATE_RAW_BEFORE_W3})
    assert out.get("error")
    assert "비실명" not in out["error"]

    # ③ 기록이 있으면 안내문 전용 사유가 나온다 (W3 이전 그래프를 합성해 재현).
    revived = dict(graph, excluded_boilerplate=[
        {"raw": _BOILERPLATE_RAW_BEFORE_W3, "count": 2,
         "reason": "B1: 회사명이 아니라 비밀유지/공시유보 안내문"},
    ])
    monkeypatch.setattr(tools, "_counterparty_graph", lambda: revived)
    out = execute("query_by_counterparty", {"name": _BOILERPLATE_RAW_BEFORE_W3})
    assert out.get("error")
    assert "비실명" not in out["error"]
    assert "안내문" in out["error"]


def test_query_by_counterparty_filters_by_year():
    out = execute("query_by_counterparty", {"name": "한국수력원자력", "year": "1900"})
    assert out.get("records") == []


def test_query_by_counterparty_spec_has_name_and_year():
    spec = _spec("query_by_counterparty")["function"]["parameters"]
    assert "name" in spec["properties"] and "year" in spec["properties"]
    assert spec["required"] == ["name"]


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
