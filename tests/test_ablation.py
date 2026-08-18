"""Phase I, arm 스위치 검증. **LLM 0회.**

09-ablation §I-3 은 "arm 외 모든 것을 고정한다" 고만 말하지만, 진짜 위험은 그 반대편에
있다. **끈 줄 알았는데 안 꺼진 것**이다. 그러면 Δ 는 0 에 가깝게 나오고 우리는 그것을
"기여가 없다" 로 읽는다. 스위치가 실제로 동작하는지 확인할 값싼 수단은 `think_trace` 다.
검색 hop 은 전부 key=value 로 남으므로 크레딧 한 푼 없이 되읽을 수 있다.

여기 테스트는 전부 `_gather_evidence` / `shape_evidence` 를 직접 부른다. 생성은 타지
않으므로 CLOVA 호출이 0 이고, `conftest.block_clova_network` 가 그걸 강제한다.
"""
import contextlib
import json
from dataclasses import replace

import pytest

from argus import ablation
from argus.agent import pipeline
from argus.agent.budget import Budget
from argus.agent.router import route

from pathlib import Path as _Path


_GRAPH_PATH = _Path(__file__).resolve().parents[1] / "artifacts/counterparty_graph.json"


@pytest.fixture(autouse=True)
def ensure_counterparty_graph():
    """산출물이 없으면 값싸게 재생성한다. `tests/test_tools.py::ensure_counterparty_graph`
    와 같은 패턴 (`14b0953` 에서 같은 사유로 도입됐다).

    `artifacts/` 는 gitignore 대상이라 신규 클론·격리 워크트리·CI 에는 파일이 없다.
    발주처 테스트는 그 경우 **skip 이 아니라 하드 실패**한다. 라우터가 빈 노드 목록을
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

#: 정정 체인이 실재하는 문항 (Phase H 1위 병목). A4 이상에서만 `strat=chain` 이 떠야 한다.
CHAIN_Q = "삼성중공업이 2023년 6월에 공시한 원유운반선 2척 공급계약의 계약금액은 이후 정정공시에서 얼마로 바뀌었는가?"
#: 정기공시. A1 은 exchange 인덱스만 보므로 `group=periodic` 이 트레이스에 뜨면 안 된다.
PERIODIC_Q = "LG에너지솔루션이 2025년 사업보고서에서 밝힌 주요 5대 매출처에 대한 매출 비중은 약 몇 %인가?"
#: 지분공시. `보유비율:` 등 추출 필드가 근거에 실리는지로 L2 를 검증한다.
HOLDING_Q = "한국산업은행이 2025년 9월 18일 제출한 HMM 대량보유상황보고서에서 보유비율은 직전 보고서 대비 몇 %p 낮아졌는가?"
#: 시점이 둘인 문항. `split_query` 를 끄면 hop 0 이 셀로 안 쪼개져야 한다.
SPLIT_Q = "HD현대일렉트릭이 2025년 7월 영국 National Grid와 체결한 변압기 공급계약의 계약금액은 2024년 5월 같은 상대와 체결한 계약보다 큰가?"


def gather(arm: str, question: str) -> tuple[str, str]:
    """한 arm 으로 근거를 모으고 (성형된 근거, 트레이스) 를 돌려준다. LLM 0회."""
    with ablation.use(arm):
        evidence, trace = pipeline._gather_evidence(route(question), question, Budget())
        return ablation.shape_evidence(evidence), " | ".join(trace)


# --- 사다리 정의 --------------------------------------------------------------

def test_run_order_covers_every_arm():
    assert set(ablation.RUN_ORDER) == set(ablation.ARMS)
    assert ablation.RUN_ORDER[0] == "A8"          # 가장 비싼 구성을 먼저 확보한다


#: 모든 스위치. **손으로 적지 않는다**. 목록에서 빠진 플래그는 아래 '단일 변수 조작'
#: 검사를 조용히 통과하고, 그 결과가 "그 구성요소는 효과가 없었다" 로 보고된다.
FLAGS = ablation.FLAG_NAMES


def test_flag_names_covers_every_switch_on_the_dataclass():
    assert "counterparty_hop" in FLAGS
    assert "name" not in FLAGS
    assert len(FLAGS) == len(ablation.Arm.__dataclass_fields__) - 1


def test_cumulative_ladder_never_turns_a_component_off():
    """A0 → A8 은 **누적 추가**다. 중간에 꺼지는 플래그가 있으면 Δ 의 귀속이 깨진다."""
    flags = [f for f in FLAGS if f != "retrieval"]
    depth = {"meta": 0, "exchange": 1, "all": 2}
    for lower, upper in zip(ablation.LADDER, ablation.LADDER[1:]):
        a, b = ablation.ARMS[lower], ablation.ARMS[upper]
        assert depth[b.retrieval] >= depth[a.retrieval], (lower, upper)
        for flag in flags:
            assert getattr(b, flag) >= getattr(a, flag), (lower, upper, flag)


def test_the_ladder_ends_at_the_full_configuration():
    """사다리 마지막 칸이 곧 완성 구성이어야 LOO 의 기준선과 사다리 끝이 같아진다."""
    assert ablation.LADDER[-1] == "A8"
    assert ablation.ARMS["A8"] == ablation.A8


def test_loo_arms_differ_from_a8_by_exactly_one_flag():
    """LOO 는 한 요소만 빼야 Δ 귀속이 가능하다. **기준선은 A8**(완성 구성)이다.

    **예외는 도달 의존뿐이다.** `followup_hop` 은 `chain_hop` 안에서만 읽히므로
    `L1` 은 둘을 함께 끈다. 부모 없이 자식만 켜 두면 Δ 가 조용히 0 이 된다. 그 예외는
    `ablation.FLAG_DEPENDENCIES` 에 **데이터로** 선언돼 있고, 여기서 그것만 허용한다.
    선언 밖에서 플래그가 둘 이상 움직이면 단일 변수 조작이 깨진 것이다.
    """
    for name, flag in (("L1", "chain_hop"), ("L2", "schema_fields"),
                       ("L3", "counterparty_hop"), ("L4", "followup_hop"),
                       ("L6", "meta_corp"), ("L7", "meta_date"), ("L8", "meta_group")):
        arm = ablation.ARMS[name]
        diff = {f for f in FLAGS if getattr(arm, f) != getattr(ablation.A8, f)}
        allowed = {flag} | set(ablation.FLAG_DEPENDENCIES.get(flag, ()))
        assert diff <= allowed and flag in diff, (name, sorted(diff), sorted(allowed))


def test_followup_flag_is_actually_wired_into_the_chain_hop():
    """**플래그가 배선 안 되면 조용한 no-op 이고 Δ 가 0 으로 보고된다.**

    `_expand_chain` 이 `get_history` 를 부를 때 `include_followups` 를 arm 에서 읽는지
    직접 확인한다. 도구 호출을 가로채 인자를 본다. LLM 0회 · 검색 0회.
    """
    from argus.agent import pipeline
    from argus.agent.budget import Budget

    seen_args = []

    def _capture(name, args):
        seen_args.append((name, dict(args)))
        return {"error": "테스트 — 체인 조회를 여기서 끊는다"}

    seed = {"rcept_no": "20241015800258", "links": ["00000000000000"],
            "corp": "테스트", "is_latest": False}
    for arm, expected in (("A8", True), ("L4", False), ("L2", True)):
        seen_args.clear()
        with ablation.use(arm), pytest.MonkeyPatch.context() as mp:
            mp.setattr(pipeline.tools, "execute", _capture)
            # v3 R2 ④ 로 `_chain_seeds` 가 질문을 받는다 (제동 ②', 해지 예외).
            # 이 스텁은 씨앗을 고정하는 것이 목적이라 인자만 맞춘다.
            mp.setattr(pipeline, "_chain_seeds", lambda picked, question="": [seed])
            pipeline._expand_chain([], set(), Budget(), [], "")

        assert seen_args, f"{arm}: get_history 가 호출되지 않았다"
        name, args = seen_args[0]
        assert name == "get_history"
        assert args.get("include_followups") is expected, (arm, args)


def test_get_history_really_accepts_the_flag_without_stubbing():
    """**위 테스트는 `execute` 를 스텁하므로 인자명 오타를 못 잡는다.**

    `tools.execute` 는 `fn(**args)` 이고 `TypeError` 를 `{"error": "인자 오류: …"}` 로
    삼킨다. `_expand_chain` 은 그 오류를 `skip=tool_error` 로 적고 **그냥 넘어간다**.
    즉 인자명이 틀리면 체인 hop 이 조용히 아무것도 안 하고 **Δ 가 0 으로 보고된다.**
    이 모듈이 막으려는 바로 그 실패다. 그래서 진짜 경로를 한 번 탄다 (LLM 0회 · I/O 만).
    """
    from argus.agent import tools

    seed = "20241015800258"          # 실측: chain 2 · followups 1 (v3 프로브에서 확인)
    on = tools.execute("get_history", {"rcept_no": seed, "include_followups": True})
    off = tools.execute("get_history", {"rcept_no": seed, "include_followups": False})

    assert "error" not in on and "error" not in off, (on.get("error"), off.get("error"))
    assert len(on["followups"]) > len(off["followups"]) == 0
    assert on["chain_length"] == off["chain_length"], "체인은 플래그와 무관해야 한다"


def test_every_loo_arm_has_a_label_for_the_report():
    """보고서가 '무엇을 뺐는가' 칸을 이 사전에서 읽는다. 빠지면 표가 비어 나간다.

    라벨 집합을 리터럴로 박지 않는다. `L4` 를 넣을 때 이 테스트가 먼저 깨졌다.
    `ARMS` 에 있는 `L*` arm 전부가 라벨을 가져야 한다는 **규칙**을 단언한다.
    """
    loo = {n for n in ablation.ARMS if n.startswith("L")}
    assert loo, "LOO arm 이 없다"
    assert set(ablation.LOO_REMOVED) == loo
    assert all(ablation.LOO_REMOVED[n] for n in loo), "빈 라벨이 있다"


# --- 메타 축 LOO, `L6`(기업) · `L7`(접수일) · `L8`(인덱스 군) ---------------------
# v3 는 이 셋을 *"구조적으로 측정 불가"* 로 적고 LOO 표를 비워 뒀다. 표현 자리는
# `search_chunks` 의 **인자**였다. 아래 검사가 그 배선을 못 박는다. 인자가 안 떨어지면
# Δ 가 조용히 0 이 되고 우리는 그것을 "메타 프리필터는 기여가 없다" 로 읽는다.

def test_meta_axis_arms_exist_and_complete_the_loo_table():
    for name in ("L6", "L7", "L8"):
        assert name in ablation.ARMS and name in ablation.LOO_REMOVED
        assert name in ablation.RUN_ORDER
    #: `L5` 는 v3 가 기각한 이름이라 재사용하지 않는다. 같은 칸으로 읽힌다.
    assert "L5" not in ablation.ARMS


@pytest.mark.parametrize("arm,dropped", [
    ("A8", set()),
    ("L6", {"corp_name"}),
    ("L7", {"ym", "year", "rcept_dt"}),
    ("L8", {"group"}),
])
def test_meta_axes_are_dropped_from_the_search_arguments(arm, dropped):
    """**플래그가 배선 안 되면 조용한 no-op 이다.** 축이 실제로 인자에서 떨어지는지
    `_search_args` 를 직접 본다. 다른 키는 하나도 안 건드려야 단일 변수 조작이다."""
    args = {"query": "q", "top_k": 5, "corp_name": "삼성전자", "ym": "202506",
            "year": 2025, "rcept_dt": "20250601", "group": "holding",
            "base_year": "2023", "doc_subtype": "annual", "path": "III. 재무"}
    with ablation.use(arm):
        out = pipeline._search_args(args)
    assert set(args) - set(out) == dropped, (arm, sorted(set(args) - set(out)))
    assert all(out[k] == args[k] for k in out), "남은 인자의 값이 바뀌었다"


def test_the_default_arm_gets_the_very_same_arguments_object():
    """회귀 방어. `A8` 에서 사전을 새로 만들면 키 순서가 흔들릴 수 있다.
    같은 객체를 그대로 돌려주는 것이 '한 바이트도 안 바뀐다' 의 증명이다."""
    args = {"query": "q", "top_k": 5, "corp_name": "삼성전자", "group": "holding"}
    with ablation.use("A8"):
        assert pipeline._search_args(args) is args


def test_l7_keeps_the_fiscal_year_axis():
    """접수연도 ≠ 사업연도. `base_year` 까지 떼면 `L7` 이 접수일 축 하나의 Δ 를
    못 낸다 (`query.plan_section_hops` 주석 1번, '2023년 사업보고서' 는 2024-03 접수)."""
    with ablation.use("L7"):
        out = pipeline._search_args({"base_year": "2023", "doc_subtype": "annual",
                                     "rcept_dt": "20240320"})
    assert out == {"base_year": "2023", "doc_subtype": "annual"}


def _search_calls(arm: str, question: str) -> tuple[list[dict], str]:
    """진짜 경로를 한 번 태우고 `search_chunks` 가 **실제로 받은 인자**를 모은다.

    스텁이 아니라 **가로채고 그대로 넘긴다**. 도구가 인자를 받아 실제로 동작하는지도
    같이 확인해야 한다 (`test_get_history_really_accepts_the_flag_without_stubbing`
    이 같은 이유로 있다). LLM 0회 · I/O 만.
    """
    seen: list[dict] = []
    real = pipeline.tools.execute

    def _spy(name, args):
        if name == "search_chunks":
            seen.append(dict(args))
        return real(name, args)

    with ablation.use(arm), pytest.MonkeyPatch.context() as mp:
        mp.setattr(pipeline.tools, "execute", _spy)
        evidence, _ = pipeline._gather_evidence(route(question), question, Budget())
    return seen, evidence


def test_l6_really_stops_passing_the_corp_filter_on_a_live_search():
    """인자가 안 떨어지면 Δ 가 조용히 0 이 되고 우리는 그것을 "기업 축은 기여가
    없다" 로 읽는다. 진짜 검색 경로에서 그 인자를 직접 본다."""
    on, _ = _search_calls("A8", HOLDING_Q)
    off, _ = _search_calls("L6", HOLDING_Q)
    assert on and any("corp_name" in a for a in on)
    assert off and not any("corp_name" in a for a in off)
    #: 축이 빠지면 후보 집합이 넓어진다. 근거가 같으면 조작이 아무것도 안 한 것이다.
    assert [a.get("query") for a in on] == [a.get("query") for a in off], \
        "질의 자체는 안 바뀌어야 단일 변수 조작이다"


def test_l7_really_stops_passing_the_receipt_date_prefix_on_a_live_search():
    on, _ = _search_calls("A8", HOLDING_Q)
    off, _ = _search_calls("L7", HOLDING_Q)
    assert any({"ym", "year", "rcept_dt"} & set(a) for a in on)
    assert not any({"ym", "year", "rcept_dt"} & set(a) for a in off)


def test_l8_really_stops_passing_the_group_argument_on_a_live_search():
    on, _ = _search_calls("A8", HOLDING_Q)
    off, _ = _search_calls("L8", HOLDING_Q)
    assert any(a.get("group") == "holding" for a in on)
    assert not any("group" in a for a in off)


@pytest.mark.parametrize("axis", ablation.META_UNREACHABLE_AXES)
def test_meta_axes_cannot_be_moved_on_an_arm_that_never_calls_search_chunks(axis):
    """`A0` 은 `filter_docs` 만 쓰고 `ORACLE` 은 골든을 주입한다. 어느 쪽에서도
    이 축은 후보 집합을 안 바꾼다. 켜면 **조용한 Δ 0** 이므로 예외로 막는다."""
    with pytest.raises(ValueError):
        replace(ablation.A0, name="A0-meta", **{axis: False})
    with pytest.raises(ValueError):
        replace(ablation.ORACLE, name="ORACLE-meta", **{axis: False})


# --- A8, 발주처 2-hop 은 **BM25 이후 단계**에 선다 -------------------------------
# A4 에 세우면 조용히 Δ=0 이 난다. `A0` 은 `uses_bm25=False` 조기 반환이 이 hop **앞**에
# 있어서 켜도 발화하지 않고, A1b→A4 전이에서는 hop_ladder·chain_hop·split_query 와 함께
# 묻힌다. 그래서 v2 는 이 플래그만 A8 로 옮겼다. 여기 검사가 그 자리를 고정한다.

def test_a8_arm_exists_and_toggles_the_counterparty_hop():
    assert "A8" in ablation.ARMS
    with ablation.use("A8"):
        assert ablation.enabled("counterparty_hop")
    with ablation.use("A7"):
        assert not ablation.enabled("counterparty_hop")


def test_a8_adds_only_the_counterparty_hop_on_top_of_a7():
    a7, a8 = ablation.flags("A7"), ablation.flags("A8")
    assert {k for k in a8 if a8[k] != a7[k]} == {"counterparty_hop"}


def test_l3_removes_only_the_counterparty_hop():
    """LOO 는 한 요소만 빼야 Δ 귀속이 가능하다."""
    a8 = ablation.flags("A8")
    l3 = ablation.flags("L3")
    diff = {k for k in a8 if a8[k] != l3[k]}
    assert diff == {"counterparty_hop"}


def test_l3_is_the_same_configuration_as_a7():
    """`A8 − counterparty_hop = A7` 이라는 항등식. 깨지면 사다리 Δ 와 LOO Δ 가
    부호만 다른 같은 값이라는 전제가 무너진다."""
    assert ablation.flags("L3") == ablation.flags("A7")


def test_a7_has_no_flag_of_its_own():
    """A7 은 무조건 코드다 (`router.missing_named_report` · `pipeline._as_of_ym`).
    플래그가 A6 과 같다는 것이 "Δ(A6→A7) 은 정의상 0" 의 근거이고, 그 사실을 여기
    못 박아 둔다. 나중에 누가 A7 에 플래그를 붙이면 보고서 문장이 거짓이 된다."""
    assert ablation.flags("A7") == ablation.flags("A6")


def test_enabled_rejects_a_flag_name_that_does_not_exist():
    """오타가 조용히 False 를 내면 "그 구성요소는 효과 없음" 으로 보고된다."""
    with pytest.raises(KeyError):
        ablation.enabled("counterparty")            # 실제 이름은 counterparty_hop


# --- A2, 임베딩 하이브리드는 사다리 **밖**에 있다 (G1 격리) ---------------------

def test_the_v1_ladder_never_turns_dense_on():
    """G1 이 불허로 확정되면 A2 만 빼고 곡선이 성립해야 한다 (§I-2).

    사다리 arm 하나라도 `dense=True` 면 A4·A5·A6 이 전부 재측정 대상이 되고,
    v1 이 이미 쓴 3,708원어치 결과가 통째로 무효가 된다.
    """
    for name in (*ablation.LADDER, "L1", "L2", "L3"):
        assert ablation.ARMS[name].dense is False, name
    assert set(ablation.DENSE_ARMS) == {a for a in ablation.ARMS
                                        if ablation.ARMS[a].dense}


def test_a2_arms_differ_from_their_baseline_by_exactly_the_dense_flag():
    """단일 변수 조작. 하나라도 더 바뀌면 Δ 가 임베딩의 것이 아니다."""
    for name, baseline in (("A2", ablation.A1B), ("A2F", ablation.A8)):
        diff = [f for f in FLAGS
                if getattr(ablation.ARMS[name], f) != getattr(baseline, f)]
        assert diff == ["dense"], (name, diff)


def test_default_arm_is_the_submission_configuration():
    """스위치를 안 건드린 운영 경로가 곧 **제출 구성**이어야 한다.

    v4 에서 `A8` → `A20r`, 규약 감사에서 `A20r` → `A25+A26`, 재감사에서 → `A28`,
    **v5 에서 → `A34` → 정확성 개선 둘을 얹어 `A37`**.

    **v5 는 성적을 잰 세대다.** `A28` 이 틀리던 14문항 중 12건을 잡았다:
    그리드 66→72/72 · 전체 82→88/88 · 밸류체인 7→10/12 · 안전성 9→12/12 ·
    인용률 0.975→0.988 · 합계 98→**110/112** · 회귀 0.
    정본: `artifacts/ablation/v5/final2_*`.

    여덟 플래그가 전부 **LLM 0회**다. 새 모델도 새 호출도 없다.
    남은 둘(`Q-107`·`Q-117`)은 검색이 막고 `A29c` 가 기각으로 그 사실을 붙든다.

    감사가 잰 것은 성적이 아니라 **규약 준수**다. `F-2`(근거 표시가 실질 답변 8건 중
    3건)·`F-3`(지시대상 불명을 '범위 밖' 으로 오진)을 닫는다. 같은 날 같은 캐시로 잰
    값에서 **그리드 66/72 동률**이고 인용률만 0.750 → 0.972 로 오른다. 둘 다 LLM 0회다.
    정본: `artifacts/ablation/v4/repro_0816/`.

    이 값을 바꾸면 **평가 서버도 다시 배포해야 한다**. `api/server.py` 는 arm 을
    지정하지 않으므로 이것이 곧 주최측이 받는 답변의 구성이다.
    """
    assert ablation.current() == ablation.A43
    assert ablation.current().name == "A43"
    # v5 정확성 개선 둘. 성적은 안 움직이고 규약 「정확성」 축만 움직인다.
    assert ablation.current().fix_unit_parens is True
    assert ablation.current().counterparty_exempt is True
    # **v6, 열거 완전성** (2026-08-17). 골든 112 에서는 안 걸리던 축이라
    #    `A38` 까지 못 봤다. 처음 보는 문항이 잡았다: `N-013`·`N-014` 가 근거에 세 곳이
    #    다 실렸는데 답변에 두 곳만 적었다(계약상대 hop 은 `partners=3` 을 정확히 셌다).
    #    로스터를 근거 **머리**로 올려 둘 다 3/3 이 됐다.
    #    문면이 성적을 가르고 있었다. 같은 축의 heldout 문항은 *"빠짐없이"* 가 있어
    #    통과했고 없는 문항이 빠뜨렸다. 이 스위치가 그 의존을 끊는다.
    assert ablation.current().roster_line is True
    # 반올림 복원, N-011 이 잡았다. 근거에 원값이 둘 다 있는데 답변이
    #    억으로만 적었다. 값은 맞고 정밀도만 잃은 자리다.
    assert ablation.current().expand_rounded is True
    # 결과값 부재, H-008 이 잡았다. 치환은 "근거 밖 수" 만 보는데
    #    모델은 맞는 수를 틀리게 엮어 그 망을 빠져나갔다.
    assert ablation.current().state_total is True
    # 같은 날 공시 가르기, K-004. 둘 다 근거에 있었고 선택이 틀렸다.
    assert ablation.current().date_disambiguation is True
    # v7 promotion, A42 -> A43. Three-round minimum 102 -> 107, flipping
    #    questions 9 -> 3. See docs/design-rationale.md#operational-default-history
    assert ablation.current().unit_line is True
    # v5 가 켠 여덟 스위치. 하나라도 빠지면 잰 성적이 아니다.
    for flag in ("speculation_gate", "identifier_gate", "reverse_chain",
                 "aggregate_line", "substitute_aggregate", "substitute_verified",
                 "revision_series", "correction_fields"):
        assert getattr(ablation.current(), flag) is True, flag
    # v4 가 켠 것도 그대로 남아 있어야 한다. 결합은 `A28` 위에 얹은 것이다.
    for flag in ("attach_citation", "ambiguous_gate", "quarter_gate",
                 "regenerate_on_verify", "provenance_gate"):
        assert getattr(ablation.current(), flag) is True, flag
    # `A8` 과의 차이는 넷뿐이고 **검색·생성 경로는 여전히 같다**. 둘은 검증 단계,
    #    둘은 생성 뒤(부착)와 라우팅이다. 프롬프트는 `A8` 시절과 바이트 동일이다.
    diff = [f for f in ablation.FLAG_NAMES
            if getattr(ablation.A28, f) != getattr(ablation.A8, f)]
    assert diff == ["provenance_gate", "regenerate_on_verify", "quarter_gate",
                    "ambiguous_gate", "attach_citation"], diff


def test_the_default_arm_kept_the_v1_flag_set():
    """v2 는 `counterparty_hop` 을 A4 에서 A8 로 **옮겼을 뿐**이다. 옛 기본값(A6)이
    그 플래그를 A4 로부터 물려받고 있었으므로 새 기본값(A8)과 플래그가 같아야 한다.
    다르면 이름을 바로잡은 것이 아니라 **운영 동작을 바꾼 것**이다."""
    v1_default = replace(ablation.A6, name="A8", counterparty_hop=True)
    assert ablation.flags(v1_default) == ablation.flags(ablation.A8)


def test_use_restores_previous_arm_even_on_error():
    before = ablation.current()
    with pytest.raises(ValueError):
        with ablation.use("A0"):
            assert ablation.current().name == "A0"
            raise ValueError("boom")
    assert ablation.current() == before


def test_unknown_retrieval_mode_is_rejected():
    with pytest.raises(ValueError):
        ablation.Arm("X", retrieval="dense")


# --- A0, 메타 프리필터만 -----------------------------------------------------

def test_a0_never_calls_bm25(monkeypatch):
    """'검색을 껐다' 는 주장은 도구 호출 목록으로만 증명된다."""
    seen = []
    original = pipeline.tools.execute
    monkeypatch.setattr(pipeline.tools, "execute",
                        lambda name, args: seen.append(name) or original(name, args))
    evidence, trace = gather("A0", CHAIN_Q)
    assert "search_chunks" not in seen and "get_history" not in seen
    assert "strat=meta" in trace and "tool=filter_docs" in trace
    assert evidence                                    # 그래도 근거는 나온다 (§I-1)


def test_a0_periodic_takes_sections_without_ranking():
    evidence, trace = gather("A0", PERIODIC_Q)
    assert "strat=meta" in trace
    assert "top_score=-" in trace                      # 순위가 없다는 것이 A0 의 정의다


# --- A1, exchange 인덱스 1,469건만 -------------------------------------------

def test_a1_does_not_look_at_the_periodic_index():
    """A1b 의 Δ 는 '인덱스 커버리지' 다. A1 이 정기공시를 보면 그 Δ 가 사라진다."""
    _, trace = gather("A1", PERIODIC_Q)
    assert "group=periodic" not in trace


def test_a1b_does_look_at_the_periodic_index():
    _, trace = gather("A1b", PERIODIC_Q)
    assert "group=periodic" in trace


def test_a1_routes_holding_query_into_the_exchange_index(monkeypatch):
    captured = []
    original = pipeline.tools.execute
    monkeypatch.setattr(pipeline.tools, "execute",
                        lambda name, args: captured.append((name, args)) or original(name, args))
    gather("A1", HOLDING_Q)
    groups = {a.get("group") for name, a in captured if name == "search_chunks"}
    assert groups == {None}                            # group 인자 자체가 안 나간다


# --- A4, Agent 루프 (사다리 · 체인 · 분해) ------------------------------------

def test_chain_hop_is_off_below_a4():
    for arm in ("A1", "A1b"):
        _, trace = gather(arm, CHAIN_Q)
        assert "strat=chain" not in trace, arm


def test_chain_hop_is_on_from_a4():
    for arm in ("A4", "A5", "A6", "A7", "A8"):
        _, trace = gather(arm, CHAIN_Q)
        assert "strat=chain" in trace, arm


def test_l1_removes_only_the_chain_hop():
    """L1 = A8 − 정정체인 hop. 사다리와 분해는 그대로 살아 있어야 한다."""
    _, trace = gather("L1", CHAIN_Q)
    assert "strat=chain" not in trace
    assert "tool=search_chunks" in trace


def test_hop_ladder_is_off_below_a4():
    _, trace = gather("A1b", CHAIN_Q)
    hops = {h["hop"] for h in pipeline.parse_hops(trace)}
    assert hops <= {0}, hops


def test_split_query_is_off_below_a4(monkeypatch):
    """다지점 분해가 꺼지면 hop 0 이 셀로 안 쪼개진다. 검색 호출 수로 드러난다."""
    def count(arm: str) -> int:
        seen = []
        original = pipeline.tools.execute
        monkeypatch.setattr(pipeline.tools, "execute",
                            lambda name, args: seen.append((name, args)) or original(name, args))
        with ablation.use(arm):
            pipeline._gather_evidence(route(SPLIT_Q), SPLIT_Q, Budget())
        return sum(1 for name, a in seen if name == "search_chunks" and "ym" in a)

    assert count("A1b") < count("A4")


def test_events_fallback_is_off_below_a4(monkeypatch):
    seen = []
    monkeypatch.setattr(pipeline.tools, "execute",
                        lambda name, args: seen.append(name) or {"count": 0, "chunks": []})
    with ablation.use("A1b"):
        pipeline._gather_evidence(route(CHAIN_Q), CHAIN_Q, Budget())
    assert "query_events" not in seen


# --- A5, 계산 결과를 근거에 싣는가 --------------------------------------------

AMOUNT_BLOCK = ("## [공급계약 체결] 삼성전자 – 반도체 위탁생산\n"
                "- 공시일: 2025-07-28 (접수번호 20250728800123)\n"
                "- 계약금액: 22,764,764,160,000원 (227,648억원)\n"
                "- 매출액 대비: 7.6%\n"
                "- 기간: 2025-07-24 ~ 2033-12-31 (8년 5개월)")


def test_a5_off_strips_precomputed_values_but_keeps_the_raw_inputs():
    out = ablation.shape_evidence(AMOUNT_BLOCK, ablation.A4)
    assert "(227,648억원)" not in out and "22,764,764,160,000원" in out
    assert "(8년 5개월)" not in out and "2025-07-24 ~ 2033-12-31" in out


def test_a5_off_keeps_the_ratio_line():
    """분모(최근매출액)가 근거에 없다. 비율까지 떼면 계산 ablation 이 정보 ablation 이 된다."""
    assert "- 매출액 대비: 7.6%" in ablation.shape_evidence(AMOUNT_BLOCK, ablation.A4)


def test_a5_on_is_a_no_op():
    assert ablation.shape_evidence(AMOUNT_BLOCK, ablation.A6) == AMOUNT_BLOCK


def test_a5_off_applies_to_the_live_pipeline():
    evidence, _ = gather("A4", CHAIN_Q)
    assert "억원)" not in evidence


# --- L2, major·holding 추출 필드 ----------------------------------------------

def test_l2_removes_the_holding_schema_fields():
    full, _ = gather("A8", HOLDING_Q)
    loo, _ = gather("L2", HOLDING_Q)
    assert "보유비율:" in full
    assert "보유비율:" not in loo
    assert len(loo) < len(full)


def test_l2_keeps_the_evidence_blocks_themselves():
    """필드만 뺀다. 블록이 통째로 사라지면 그건 검색 ablation 이다."""
    full, _ = gather("A8", HOLDING_Q)
    loo, _ = gather("L2", HOLDING_Q)
    assert full.count("\n## ") == loo.count("\n## ")


def test_l2_does_not_change_retrieval():
    """성형은 생성 직전에만 건다. 트레이스가 A8 과 글자 하나까지 같아야 한다."""
    _, full = gather("A8", HOLDING_Q)
    _, loo = gather("L2", HOLDING_Q)
    assert full == loo


# --- A6, Verifier -------------------------------------------------------------

class _Fake:
    prompt_tokens, completion_tokens = 100, 50

    def __init__(self, text):
        self.text = text


def _answer(arm: str, question: str, text: str, monkeypatch) -> dict:
    monkeypatch.setattr(pipeline, "chat", lambda *a, **k: _Fake(text))
    with ablation.use(arm):
        return pipeline.answer_question("Q-T", question)


def test_verifier_is_silent_below_a6(monkeypatch):
    out = _answer("A5", CHAIN_Q, "계약금액은 970억원입니다. (근거: 접수번호 20230602800079)",
                  monkeypatch)
    assert "verify " not in out["think_trace"]


def test_verifier_runs_at_a6_and_records_its_verdict(monkeypatch):
    out = _answer("A6", CHAIN_Q, "계약금액은 970억원입니다. (근거: 접수번호 20230602800079)",
                  monkeypatch)
    assert "verify " in out["think_trace"]


def test_verifier_flags_an_admitted_evidence_gap(monkeypatch):
    out = _answer("A6", CHAIN_Q,
                  "정정공시에 대한 언급이 근거에 없으므로 기존 금액이 유지됩니다. "
                  "(근거: 접수번호 20230602800079)", monkeypatch)
    assert "자동 검증에서 결함" in out["answer"]
    assert "kinds=evidence_gap" in out["think_trace"]


# --- A2, 끈 것이 실제로 꺼졌는가 ------------------------------------------------
# 이 파일 머리말의 계약을 dense 에도 그대로 건다. dense 는 **켜졌는지도** 확인해야 한다.
# 임베딩 저장소가 비어 있으면 조용히 아무 일도 안 일어나고, 그 침묵이 "Δ 0" 으로 읽힌다.

DENSE_ARM_QUESTIONS = (CHAIN_Q, PERIODIC_Q, HOLDING_Q, SPLIT_Q)


def test_dense_leaves_no_trace_when_the_arm_is_off():
    """A2 를 껐을 때 `think_trace` 에 dense 흔적이 하나도 없어야 한다."""
    for arm in ("A0", "A1", "A1b", "A4", "A5", "A6", "A7", "A8", "L1", "L2", "L3"):
        for question in DENSE_ARM_QUESTIONS:
            _, trace = gather(arm, question)
            assert "fusion=" not in trace, (arm, question)
            assert "dense_n=" not in trace, (arm, question)


def test_dense_never_touches_the_embedding_api_when_the_arm_is_off(monkeypatch):
    """`conftest` 가 CLOVA 를 막고 있으므로 호출이 있었다면 **여기서 죽는다**.
    그래도 명시적으로 센다. 크레딧은 조용히 새는 것이 가장 무섭다."""
    from argus import embed

    calls = []
    monkeypatch.setattr(embed, "query_vector",
                        lambda text: calls.append(text) or None)
    for arm in ("A1b", "A6"):
        for question in DENSE_ARM_QUESTIONS:
            gather(arm, question)
    assert calls == []


class _StubDense:
    """dense 순위를 결정적으로 흉내낸다. **API 0회**, 배선만 검증한다."""

    added = 0
    lazy = False

    def __init__(self):
        self.seen = []

    def rank(self, query, candidates, top_k):
        self.seen.append((query, tuple(candidates)))
        return list(reversed(candidates))[:top_k]       # BM25 와 최대한 다른 순서


def test_dense_arm_actually_fuses(monkeypatch):
    """켰는데 안 켜진 것도 같은 크기의 위험이다. 침묵이 'Δ 0' 으로 읽힌다."""
    from argus.agent import tools
    from argus.retrieve import bm25

    index = tools._group_index("exchange")
    stub = _StubDense()
    monkeypatch.setattr(index, "dense", stub)
    _, trace = gather("A2F", CHAIN_Q)
    assert "fusion=rrf" in trace
    assert bm25.LAST_FUSION["dense_n"] > 0
    assert stub.seen                                    # 후보를 실제로 받아 갔다


def test_dense_sees_only_prefiltered_candidates(monkeypatch):
    """§I-1, 메타 프리필터가 A0 이자 최대 기여 요소다. dense 가 그 앞에 서면 안 된다."""
    from argus.agent import tools

    index = tools._group_index("exchange")
    stub = _StubDense()
    monkeypatch.setattr(index, "dense", stub)
    gather("A2F", CHAIN_Q)
    total = len(index.doc_ids)
    assert all(len(cands) < total for _, cands in stub.seen), "전 코퍼스가 넘어갔다"


def test_retrieval_metrics_never_calls_the_llm():
    """A2 의 1차 산출물은 **생성 0회**로 나와야 한다 (§I-5c 크레딧 규칙).

    `conftest` 가 CLOVA 를 막고 있으므로 생성이 끼어들면 여기서 AssertionError 가 난다.
    """
    from scripts import ablation as runner

    case = {"question_id": "Q-T", "question": CHAIN_Q, "difficulty": "상",
            "doc_group": "exchange",
            "evidence": [{"rcept_no": "20230602800079"}]}
    report = runner.retrieval_metrics("A6", [case])
    assert report["totals"]["golden"] == 1
    assert report["rows"][0]["dense_calls"] == 0


def test_retrieval_metrics_reproduces_the_v1_a6_baseline():
    """게이트 회귀. 얼려 둔 42문항에서 A6 은 v1 과 같은 수를 내야 한다.
    골든 근거 **59/60** · Recall@5 **58/60** (04-retriever 게이트 D).
    이게 어긋나면 A2 의 Δ 는 dense 가 아니라 파이프라인 변화의 것이다."""
    import json

    from scripts import ablation as runner

    if not runner.SNAPSHOT_PATH.exists():
        pytest.skip("스냅샷 없음")
    cases = runner.load_dev(runner.SNAPSHOT_PATH)
    totals = runner.retrieval_metrics("A6", cases)["totals"]
    assert (totals["golden"], totals["evidence_hit"], totals["recall5"]) == (60, 59, 58)
    assert json.loads(runner.SNAPSHOT_PATH.read_text(encoding="utf-8"))["n"] == 42


GROUPS = ("exchange", "major", "holding")


def test_dense_store_only_holds_ids_that_exist_in_its_own_index():
    """문서군별 인덱스 구조를 dense 도 지킨다. 사이드카가 남의 문서를 담으면
    라우터가 고른 인덱스 밖 문서가 근거로 새어 든다."""
    from argus.agent import tools

    for group in GROUPS:
        index = tools._group_index(group)
        store = index.dense.store if index and index.dense else None
        if not store or not len(store):
            continue
        assert set(store.ids) <= set(index.doc_ids), group


def test_dense_store_covers_the_record_groups():
    """산출물 계약, 정형 3종은 **전량** 담긴다 (3,150건, float16 6.2MB).

    빌드가 끝나기 전에는 건너뛴다. 부분 상태를 실패로 세면 빌드 중 테스트가 못 돈다.
    """
    from argus.agent import tools

    sizes = {g: len(tools._group_index(g).dense.store) for g in GROUPS
             if tools._group_index(g) and tools._group_index(g).dense}
    wanted = {g: len(tools._group_index(g).doc_ids) for g in sizes}
    if any(sizes[g] < wanted[g] for g in sizes):
        pytest.skip(f"dense 빌드 미완 {sizes} / {wanted} "
                    "(scripts/build_embeddings.py --records)")
    assert sizes == wanted
    assert sum(wanted.values()) == 3150


def test_verifier_never_calls_the_llm(monkeypatch):
    """검증 4종은 전부 결정적이다. 여기서 호출이 붙으면 A6 의 Δ가 비용 Δ와 섞인다."""
    calls = []
    monkeypatch.setattr(pipeline, "chat",
                        lambda *a, **k: calls.append(1) or _Fake("답 (근거: 접수번호 20230602800079)"))
    with ablation.use("A6"):
        pipeline.answer_question("Q-T", CHAIN_Q)
    assert len(calls) == 1                             # 생성 1회뿐, critic 도 안 탄다


# --- 발주처 2-hop 은 **독립 플래그**다 -----------------------------------------
# `hop_ladder` 에 묶어 두면 A0·A1b 에서 함께 꺼져 이 hop 만의 Δ 를 분리해서 잴 수 없다.
# `chain_hop` 을 따로 둔 것과 같은 이유다. 여기서 재는 것은 **끈 것이 실제로 꺼지는가** 다.

COUNTERPARTY_Q = ("한국수력원자력을 계약상대로 하는 단일판매·공급계약을 "
                  "공시한 상장사는 어디인가?")


def test_counterparty_hop_is_off_everywhere_below_a8():
    """사다리에서 이 hop 은 **A8 에서만** 켜진다. 한 칸이라도 미리 켜지면 A8 의 Δ 가
    그 전이에 묻히고, 우리는 그것을 "발주처 hop 은 효과가 없다" 로 읽는다."""
    for arm in ("A0", "A1", "A1b", "A4", "A5", "A6", "A7", "L3"):
        _, trace = gather(arm, COUNTERPARTY_Q)
        assert "strat=counterparty" not in trace, arm


def test_counterparty_hop_is_on_at_a8():
    _, trace = gather("A8", COUNTERPARTY_Q)
    assert "strat=counterparty" in trace


def test_a8_actually_adds_counterparty_evidence_that_a7_does_not_have():
    """켰는데 안 켜진 것이 가장 무서운 실패다. 침묵이 'Δ 0' 으로 읽힌다.
    A8 의 근거에 발주처 블록이 **실제로 더 실려야** Δ 를 읽을 자격이 생긴다."""
    full, _ = gather("A8", COUNTERPARTY_Q)
    off, _ = gather("A7", COUNTERPARTY_Q)
    assert "발주처 관계:" in full
    assert "발주처 관계:" not in off
    assert full.count("\n## ") > off.count("\n## ")


def test_counterparty_hop_off_removes_the_evidence_too(monkeypatch):
    """표식만 사라지고 근거는 남으면 '끈 줄 알았는데 안 꺼진 것' 이다."""
    seen = []
    original = pipeline.tools.execute
    monkeypatch.setattr(
        pipeline.tools, "execute",
        lambda name, args: seen.append(name) or original(name, args))

    with ablation.use("L3"):
        evidence, _ = pipeline._gather_evidence(route(COUNTERPARTY_Q),
                                                COUNTERPARTY_Q, Budget())
    assert "query_by_counterparty" not in seen
    assert "발주처 관계:" not in evidence


def test_a0_cannot_fire_the_counterparty_hop_even_when_forced():
    """A8 을 meta 단(A0)에 세우면 안 되는 이유를 못 박아 둔다. `uses_bm25=False`
    조기 반환이 이 hop **앞**에 있어서, 플래그를 켜도 발화가 0 이다. 이걸 모르고
    A0+cp 로 Δ 를 재면 "발주처 hop 은 효과 없음" 이라는 틀린 결론이 나온다.

    이제 그 조합은 **만들 수조차 없다**. `Arm.__post_init__` 이 `retrieval='meta'` 와
    도달 불가 플래그의 조합을 거부한다(자기무력화 arm 가드). 예전 이 테스트는 조합을
    만들어 "발화가 0" 만 확인했는데, 그것은 **Δ 0 을 여전히 측정할 수 있다**는 뜻이었다.
    거부가 더 강한 제동이므로 그쪽을 못박고, 원래의 근거(A0 에서 hop 이 안 뜬다)도 함께 둔다.
    """
    with pytest.raises(ValueError, match="counterparty_hop"):
        replace(ablation.A0, name="A0-cp-on", counterparty_hop=True)

    # 근거, 실제 A0 구성에서 이 hop 은 트레이스에 흔적이 없다 (조기 반환이 앞에 있다).
    evidence, trace = gather("A0", COUNTERPARTY_Q)
    assert "strat=counterparty" not in trace
    assert "발주처 관계:" not in evidence


# --- v2 러너, 코호트와 분모 (LLM 0회) ------------------------------------------
# 여기서 재는 것은 정답률이 아니라 **분모**다. 회귀 10·밸류체인 12가 그리드에 섞이면
# 축별 분모가 v1 과 달라져 "문항이 늘어난 효과" 조차 읽을 수 없게 된다.

def test_the_runner_and_the_scorer_read_the_same_cohort():
    """코호트를 러너가 따로 적으면 게이트 D 의 분모와 조용히 갈라진다."""
    from scripts import ablation as runner
    from scripts import error_analysis as ea

    for name in ("full", "valuechain"):
        assert [c["question_id"] for c in runner.load_cases(name)] == \
               [c["question_id"] for c in ea.load_cohort(name)]


def test_full_cohort_is_dev_78_plus_regression_10():
    from scripts import ablation as runner

    cases = runner.load_cases("full")
    tracks = {}
    for case in cases:
        tracks[case.get("track")] = tracks.get(case.get("track"), 0) + 1
    assert len(cases) == 88
    assert tracks.get("regression") == 10
    assert "valuechain" not in tracks          # 밸류체인은 별도 코호트다


def test_summarise_keeps_the_off_grid_tracks_out_of_the_rate():
    """회귀 10문항이 그리드 정답률에 섞이면 v1 과 비교할 축이 사라진다."""
    from scripts import ablation as runner
    from scripts import error_analysis as ea

    rows = [
        {**_BLANK_ROW, "question_id": "Q-g1", "track": "grid", "correct": True},
        {**_BLANK_ROW, "question_id": "Q-g2", "track": "grid", "correct": False},
        {**_BLANK_ROW, "question_id": "Q-r1", "track": "regression", "correct": True},
        {**_BLANK_ROW, "question_id": "Q-r2", "track": "regression", "correct": True},
    ]
    report = ea.build_report([], rows)
    report["arm"] = "A8"
    summary = runner.summarise({"A8": report})["arms"]["A8"]
    assert (summary["correct"], summary["n_scored"]) == (1, 2)      # 그리드만
    assert summary["tracks"]["regression"]["correct"] == 2          # 따로 보인다


def test_summarise_reads_the_grid_variant_of_the_root_cause_histogram():
    """전체 88 기준을 쓰면 회귀 트랙이 섞여 arm 겨냥 건수가 부푼다 (실측: VERIFY 4건
    중 1건이 회귀 트랙이었다)."""
    from scripts import ablation as runner
    from scripts import error_analysis as ea

    rows = [
        {**_BLANK_ROW, "question_id": "Q-g1", "track": "grid", "correct": False,
         "first_failure": "VERIFY", "root_cause": "VERIFY"},
        {**_BLANK_ROW, "question_id": "Q-r1", "track": "regression", "correct": False,
         "first_failure": "VERIFY", "root_cause": "VERIFY"},
    ]
    report = ea.build_report([], rows)
    summary = runner.summarise({"A8": report})["arms"]["A8"]
    assert summary["root_causes"] == {"VERIFY/VERIFY": 1}           # 그리드만
    assert summary["failures"] == {"VERIFY": 1}
    assert summary["failures_all"] == {"VERIFY": 2}                 # 전체는 따로 남는다


def test_the_request_cache_is_shared_across_out_directories():
    """`--out` 이 요청 캐시를 함께 가르면 v1·T7 이 이미 산 응답을 다시 산다."""
    from scripts import ablation as runner

    assert runner.CACHE_DIR == runner.ROOT / "artifacts" / "ablation" / "cache"


#: `ea.build_report` 가 요구하는 최소 필드. 값은 집계에 안 쓰이는 것만 채운다.
_BLANK_ROW = {
    "task_type": "search_extract", "answer_mode": "closed", "difficulty": "하",
    "doc_group": "exchange", "sector": "", "corp_name": [], "answerable": True,
    "answer_form": "number_won", "scored_by": "em", "judge": None,
    "first_failure": None, "root_cause": "", "failure_why": "",
    "gold_value_in_evidence": True, "has_citation": True, "route": "search",
    "route_group": "exchange", "route_corps": [], "n_hops": 0, "n_steps": 1,
    "n_llm_calls": 1, "prompt_tokens": 10, "completion_tokens": 5, "judge_tokens": 0,
    "latency_s": 1.0, "calls": [], "golden": [], "golden_rank": {},
    "golden_in_candidates": [], "golden_in_evidence": [], "golden_evidence_all": True,
    "calc_checked": [], "calc_skipped": [], "calc_mismatch": False, "calc_reasons": [],
    "data_limit": {"withheld": [], "no_amount": [], "shell_only": [],
                   "asks_value": False, "blocking": False},
    "answer": "", "think_trace": "",
}


def test_counterparty_hop_is_independent_of_the_hop_ladder():
    """L3 이 성립하는 근거. ladder 가 꺼진 자리에서도 이 hop 은 혼자 발화해야 한다.
    그래야 A8 의 Δ 가 `hop_ladder` 와 섞이지 않는다."""
    solo = replace(ablation.A1B, name="A1b-cp-on", counterparty_hop=True)
    with ablation.use(solo):
        _, trace_lines = pipeline._gather_evidence(
            route(COUNTERPARTY_Q), COUNTERPARTY_Q, Budget())
    trace = " | ".join(trace_lines)
    assert "strat=counterparty" in trace
    assert not ablation.enabled("hop_ladder", solo)


# --- heldout, 되돌릴 수 없는 조작은 코드가 막아야 한다 ---------------------------
# heldout 18문항은 **최종 구성 1회용**이다. "1회만" 을 문서에 적어 두는 것으로는 두 번째
# 실행을 못 막는다. 다음 세션의 우리가 같은 명령을 다시 치면 heldout 이 조용히 개발셋이
# 되고 그것은 되돌릴 수 없다. 아래 테스트는 그 제동이 실제로 걸리는지만 본다. LLM 0회.

def test_heldout_loader_refuses_anything_that_is_not_heldout(tmp_path, monkeypatch):
    """split 검사가 살아 있는가. dev 문항이 섞이면 낙폭이 dev 성능으로 희석된다."""
    from scripts import ablation as runner

    bad = tmp_path / "evalset"
    bad.mkdir()
    (bad / "heldout.jsonl").write_text(
        '{"question_id":"Q-002","split":"dev"}\n', encoding="utf-8")
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    with pytest.raises(SystemExit, match="heldout 아닌 split"):
        runner.load_heldout()


def test_heldout_is_the_18_question_file_and_nothing_else():
    from scripts import ablation as runner

    cases = runner.load_heldout()
    assert len(cases) == 18
    assert {c["split"] for c in cases} == {"heldout"}
    assert {c.get("track") for c in cases} == {"grid"}


def test_the_arm_loop_never_reaches_the_heldout_file():
    """`--heldout` 없이는 열리지 않는다. 코호트 세 개 중 어느 것도 heldout 을 안 준다."""
    from scripts import ablation as runner
    from scripts import error_analysis as ea

    held = {c["question_id"] for c in runner.load_heldout()}
    for cohort in ea.COHORTS:
        ids = {c["question_id"] for c in runner.load_cases(cohort)}
        assert ids & held == set(), f"{cohort} 코호트가 heldout 을 물고 있다"


@pytest.fixture
def fake_root(tmp_path, monkeypatch):
    """`ROOT` 를 옮겨 heldout 제동을 실제 저장소 없이 검사한다."""
    from scripts import ablation as runner

    (tmp_path / "artifacts").mkdir()
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    return tmp_path


def _put_ledger(root, where: str, **fields):
    from scripts import ablation as runner

    path = root / "artifacts" / where / runner.HELDOUT_LEDGER
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"use": 2, "arm": "A8", "correct": 15, "n_scored": 18,
               "rate": 0.833, "generated": "2026-08-10T22:38:00"}
    payload.update(fields)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_a_second_paid_heldout_run_is_refused(fake_root):
    """이 테스트가 이 파일에서 가장 값비싼 것을 지킨다. 산출물이 있으면 거부한다."""
    from scripts import ablation as runner

    _put_ledger(fake_root, "ablation/v2/heldout")
    with pytest.raises(SystemExit, match="최종 구성 1회용"):
        runner.assert_heldout_unused(offline=False)


def test_changing_out_does_not_unlock_the_heldout_brake(fake_root):
    """**가장 중요한 제동 테스트.** 제동이 `--out` 아래 원장 하나만 봤을 때는
    `--out artifacts/ablation/v3/heldout` 으로 **무방비로 통과**했다. 되돌릴 수 없는
    조작에서 "인자를 바꾸면 풀리는 제동" 은 제동이 아니다.

    v2 원장만 있고 v3 폴더는 비어 있는 상태, 그래도 거부돼야 한다.
    """
    from scripts import ablation as runner

    _put_ledger(fake_root, "ablation/v2/heldout")
    (fake_root / "artifacts" / "ablation" / "v3" / "heldout").mkdir(parents=True)
    with pytest.raises(SystemExit, match="`--out` 을 바꿔도"):
        runner.assert_heldout_unused(offline=False)


def test_a_third_run_would_be_counted_as_the_third(fake_root):
    """`len(HISTORY)+1` 로만 세면 3번째 실행도 '2회째' 로 기록된다.
    횟수가 틀리면 낙폭의 의미가 사라진다. 코드 이력과 디스크 원장 중 **큰 쪽** + 1 이다."""
    from scripts import ablation as runner

    known = len(runner.HELDOUT_HISTORY)                   # 현재 2 (v1 A6 · v2 A8)
    assert runner.heldout_next_use() == known + 1         # 원장 없음 → 코드 이력 다음
    _put_ledger(fake_root, "ablation/v2/heldout", use=known)
    assert runner.heldout_next_use() == known + 1
    _put_ledger(fake_root, "ablation/v3/heldout", use=known + 1)
    assert runner.heldout_next_use() == known + 2         # 원장이 더 크면 그쪽을 따른다


def test_an_unreadable_ledger_counts_as_used_not_as_unused(fake_root):
    """원장을 못 읽으면 '이미 썼다' 쪽으로 본다. 파일 하나 깨진 것이
    재측정 허가가 되면 안 된다."""
    from scripts import ablation as runner

    path = _put_ledger(fake_root, "ablation/v2/heldout")
    path.write_text("{ 깨진 json", encoding="utf-8")
    # 못 읽으면 `len(HISTORY)+1` 를 후보로 넣으므로 **알려진 횟수보다 크게** 센다.
    assert runner.heldout_next_use() > len(runner.HELDOUT_HISTORY) + 1
    with pytest.raises(SystemExit, match="최종 구성 1회용"):
        runner.assert_heldout_unused(offline=False)


def test_a_rescore_is_allowed_because_offline_proves_zero_llm_calls(fake_root):
    """`--offline` 은 요청 캐시 미적중이 예외라 **LLM 0회가 증명된다**. heldout 을
    새로 '여는' 것이 아니므로 거부 대상이 아니다."""
    from scripts import ablation as runner

    _put_ledger(fake_root, "ablation/v2/heldout")
    runner.assert_heldout_unused(offline=True)            # 예외가 없어야 한다


def test_the_first_heldout_run_is_not_blocked(fake_root):
    from scripts import ablation as runner

    runner.assert_heldout_unused(offline=False)


def test_the_brake_does_not_take_a_path_argument(fake_root):
    """서명으로 못박는다. 위치를 인자로 받으면 `--out` 이 제동을 푼다."""
    import inspect

    from scripts import ablation as runner

    assert list(inspect.signature(runner.assert_heldout_unused).parameters) == ["offline"]


def test_the_use_count_carries_every_run_that_actually_happened():
    """몇 회째인지가 **코드에** 있어야 한다. 문서에만 적으면 산출물만 보는 사람이
    '아직 안 썼다' 로 읽는다.

    v1 은 A6 으로 1회(15/18), v2 는 A8 으로 2회(15/18)를 썼다. 이력이 1건에 멈춰 있던
    동안 두 가지가 어긋나 있었다. 소스만 읽는 사람은 "1회 썼다" 로 읽었고, 디스크
    원장이 사라진 상태에서 `heldout_next_use()` 가 **3회째 실행을 '2회째' 로** 기록했다.
    그래서 `== 1` 로 고정하지 않고 **마지막 항목**을 검사한다 (3회째는 없어야 하지만,
    있게 된다면 그때도 이 목록이 사실이어야 한다).
    """
    from scripts import ablation as runner

    history = runner.HELDOUT_HISTORY
    assert len(history) >= 2
    assert [h["use"] for h in history] == list(range(1, len(history) + 1))
    first, last = history[0], history[-1]
    assert (first["use"], first["arm"], first["correct"], first["n_scored"]) == (
        1, "A6", 15, 18)
    assert (last["use"], last["arm"], last["correct"], last["n_scored"]) == (
        2, "A8", 15, 18)
    # 두 축을 다 든다. 하나만 적으면 그것이 '그 낙폭' 으로 굳는다.
    assert last["gap_pp"] == -1.4                       # dev 72 축
    assert last["gap_pp_measured36"] == -8.3            # v1(36) 과 나란히 읽는 축


def test_the_brake_also_accepts_the_scored_report_as_evidence(fake_root):
    """**제동의 세 번째 겹.** 원장(`heldout_run.json`)을 지워도 `report.json` 의
    `"split": "heldout"` 이 남아 유료 재실행을 막는다. 그 파일들은 커밋되어 있다."""
    from scripts import ablation as runner

    report = (fake_root / "artifacts" / "ablation" / "v9" / "heldout"
              / "A8_heldout_run" / "report.json")
    report.parent.mkdir(parents=True)
    report.write_text(json.dumps({"arm": "A8", "split": "heldout"}), encoding="utf-8")
    assert runner.heldout_ledgers() == []               # 원장은 없다
    assert runner.heldout_reports() == [report]
    with pytest.raises(SystemExit, match="split"):
        runner.assert_heldout_unused(offline=False)


def test_the_real_repository_still_carries_both_heldout_brakes():
    """저장소 실물에서 제동이 살아 있는지 본다. 원장과 채점 산출물 **양쪽**."""
    from scripts import ablation as runner

    ledgers = [p.name for p in runner.heldout_ledgers()]
    reports = [p.parent.name for p in runner.heldout_reports()]
    assert ledgers, "heldout 원장이 저장소에서 사라졌다"
    assert "A8_heldout_run" in reports, "v2 heldout 채점 산출물이 사라졌다"


def test_the_heldout_folder_name_is_not_mistaken_for_an_arm():
    """폴더는 `A8_heldout_run` 이고 **구성은 `A8`** 이다. 폴더 이름을 arm 으로 적으면
    `ARMS` 에 없는 이름이 되어 `arm_config` 가 비고, 어떤 구성으로 쟀는지가 사라진다."""
    assert "A8_heldout_run" not in ablation.ARMS
    assert "A8" in ablation.ARMS


def _heldout_report(correct_ids, all_ids, spent=673.8, cost=673.8):
    """`write_heldout_ledger` 가 읽는 최소 report. 채점은 이미 끝난 상태를 흉내낸다."""
    rows = [dict(_BLANK_ROW, question_id=q, track="grid", correct=q in correct_ids)
            for q in all_ids]
    scored = len(all_ids)
    return {
        "n_cases": scored, "rows": rows,
        "crosstab": {"grid_overall": {"n": scored, "n_scored": scored,
                                      "correct": len(correct_ids),
                                      "rate": round(len(correct_ids) / scored, 3)}},
        "totals": {"cost_won": cost}, "this_run": {"spent_won": spent},
        "failure_histogram": {}, "root_cause_histogram_grid": {},
    }


def test_the_verdict_is_undecidable_when_the_worst_axis_is_missing(fake_root, monkeypatch):
    """`measured 36` 축은 gitignore 된 `eval/error_analysis.json` 에 의존한다. 그 축이
    없을 때 예전 코드는 조용히 넘겨 `gap_pp_worst` 가 **유리한 축으로 떨어지고**
    '과적합 아님' 이 찍혔다. 그것이 과적합 검사를 무력화하는 경로다."""
    from scripts import ablation as runner

    monkeypatch.setattr(runner, "_load_report", lambda *a, **k: None)
    out = fake_root / "artifacts" / "ablation" / "v2" / "heldout"
    out.mkdir(parents=True)
    ids = [f"Q-{i:03d}" for i in range(1, 19)]
    ledger = runner.write_heldout_ledger(
        out, "A8", _heldout_report(set(ids[:15]), ids))

    assert ledger["worst_axis_available"] is False
    assert ledger["measured36_error"]                       # 이유가 남아야 한다
    # `False`(=신호 없음)가 아니라 `None`(=판정 불가)이어야 한다. 이 구분이 전부다.
    #    `False` 면 보고서가 "과적합 아님" 을 찍고 검사가 조용히 통과한다.
    assert ledger["overfit_signal"] is None
    assert ledger["verdict"].startswith("🔴 판정 불가")
    assert ledger["gap_pp_worst"] is None


def test_the_verdict_uses_the_worst_axis_not_the_kindest(fake_root, monkeypatch):
    """두 축이 다 있으면 **작은 쪽(=나쁜 쪽)** 으로 판정한다.

    낙폭은 **원 분자/분모**에서 계산한다 (`_exact_rate`). 예전 `gap()` 은 `report.json`
    의 이미 반올림된 `rate` 를 서로 빼서 **이중 반올림**을 냈다. 15/18 − 33/36 =
    −8.333 인데 0.833 − 0.917 로 −8.4 가 나왔고, 게이트 §14 가 그 −8.4 를 "판정은 이
    수로 해야 한다" 며 찍었다. 여유가 1.7%p 뿐인 게이트에서 0.1%p 를 흘리는 계산이었다.
    """
    from scripts import ablation as runner

    ids = [f"Q-{i:03d}" for i in range(1, 19)]
    # dev 72 축은 낙폭 −1.4%p, measured 36 축은 −8.3%p 가 되도록 꾸민다.
    # `rate` 에는 **반올림된 값을 일부러 넣는다**. 그것을 쓰면 −8.4 가 나오므로,
    # 이 테스트가 이중 반올림의 회귀를 잡는다.
    monkeypatch.setattr(runner, "_load_report", lambda root, arm: (
        {"crosstab": {"grid_overall": {"correct": 61, "n_scored": 72, "rate": 0.847}},
         "rows": []} if arm == "A8" else None))
    monkeypatch.setattr(runner, "_grid_rate", lambda rows, wanted=None: {
        "correct": 33, "n_scored": 36, "rate": 0.917})
    out = fake_root / "artifacts" / "ablation" / "v2" / "heldout"
    out.mkdir(parents=True)
    ledger = runner.write_heldout_ledger(
        out, "A8", _heldout_report(set(ids[:15]), ids))

    assert ledger["gap_pp"] == -1.4
    assert ledger["dev_grid_measured36"]["gap_pp"] == -8.3  # 이중 반올림이면 −8.4 다
    assert ledger["gap_pp_worst"] == -8.3                   # 유리한 −1.4 가 아니다
    assert ledger["overfit_signal"] is False                # −8.3 > −10.0
    assert ledger["gate_retired_after_this_run"] is True
    # 파생값도 같이 못박는다. 여유를 %p 와 **문항 수**로 둘 다 적는 값이다.
    assert ledger["overfit_slack_pp"] == 1.7                # 이중 반올림이면 1.6
    assert ledger["overfit_slack_questions"] == 0.6         # 이중 반올림이면 0.58
    assert "-8.3%p" in ledger["verdict"]


def test_the_gap_is_computed_from_the_raw_fraction_not_the_rounded_rate(fake_root):
    """이중 반올림의 회귀를 함수 단위로 잡는다. `rate` 필드에 **틀린 값을 심어도**
    낙폭이 분자/분모에서 나와야 한다. `rate` 를 읽으면 이 테스트가 깨진다."""
    from scripts import ablation as runner

    out = fake_root / "artifacts" / "ablation" / "v2" / "heldout"
    out.mkdir(parents=True)
    ids = [f"Q-{i:03d}" for i in range(1, 19)]
    report = _heldout_report(set(ids[:15]), ids)
    report["crosstab"]["grid_overall"]["rate"] = 0.0        # 독약: 읽으면 낙폭이 +83.3 이 된다
    ledger = runner.write_heldout_ledger(out, "A8", report)
    assert ledger["rate"] == 0.0                            # 원 필드는 그대로 옮겨 적는다
    # heldout 쪽 분자/분모(15/18)에서 계산되므로 dev 축이 없으면 None, 있으면 −8.3 이다.
    assert ledger["gap_pp"] is None or ledger["gap_pp"] < 0


def test_a_rescore_does_not_overwrite_the_paid_spend_record(fake_root, monkeypatch):
    """재집계는 0원을 쓴다. 그 0 을 `run.json` 에 덮으면 **673.8원 기록이 사라진다**
    (`_spend_meta` 가 같은 사고로 한 번 덮인 적이 있다)."""
    from scripts import ablation as runner

    out = fake_root / "artifacts" / "ablation" / "v2" / "heldout"
    # `monkeypatch` 로 바꾼다. 직접 대입은 테스트가 끝나도 **모듈 전역이 그대로 남아**
    #    뒤에 오는 테스트의 조용한 오염원이 된다 (지금은 피해 0 이지만 순서에 의존한다).
    monkeypatch.setattr(runner, "OUT_DIR", out)
    run_dir = out / "A8_heldout_run"
    run_dir.mkdir(parents=True)
    (run_dir / "run.json").write_text(
        json.dumps({"arm": "A8_heldout_run", "n_run": 18, "spent_won": 673.8}),
        encoding="utf-8")

    ids = [f"Q-{i:03d}" for i in range(1, 19)]
    monkeypatch.setattr(runner, "load_heldout",
                        lambda: [dict(_BLANK_ROW, question_id=q, split="heldout")
                                 for q in ids])
    monkeypatch.setattr(runner, "run_arm",
                        lambda *a, **k: {"arm": "A8_heldout_run", "n_run": 18,
                                         "stopped_at": None, "spent_won": 0.0,
                                         "runs": []})
    monkeypatch.setattr(runner, "score_arm",
                        lambda *a, **k: _heldout_report(set(ids[:15]), ids))
    report = runner.run_heldout("A8", cache=None, records={}, stop_won=800.0)

    saved = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    assert saved["spent_won"] == 673.8                      # 0 으로 안 덮였다
    assert saved["spent_won_rescore"] == 0.0                # 이번 실행분은 따로 남는다
    assert report["arm"] == "A8"                            # 폴더 이름이 아니라 구성
    assert report["arm_config"]["counterparty_hop"] is True
    assert report["split"] == "heldout"


def test_a_rescore_does_not_bump_the_use_count(fake_root, monkeypatch):
    """재집계는 heldout 을 새로 여는 것이 아니므로 '3회째' 가 되면 안 된다."""
    from scripts import ablation as runner

    monkeypatch.setattr(runner, "_load_report", lambda *a, **k: None)
    out = fake_root / "artifacts" / "ablation" / "v2" / "heldout"
    out.mkdir(parents=True)
    ids = [f"Q-{i:03d}" for i in range(1, 19)]
    report = _heldout_report(set(ids[:15]), ids)

    first = runner.write_heldout_ledger(out, "A8", report)
    assert first["use"] == len(runner.HELDOUT_HISTORY) + 1   # 이 fake root 의 첫 원장
    again = runner.write_heldout_ledger(out, "A8", report)
    assert again["use"] == first["use"], "재집계가 횟수를 올렸다"


# --- 지출 기록 보존, 같은 사고의 세 번째 서식지 --------------------------------

def test_the_ladder_rescore_does_not_zero_the_paid_spend_record(fake_root, monkeypatch):
    """**C-1.** 사다리 루프의 `run.json` 쓰기에는 지출 보존 가드가 없었다.
    `--offline --arms A0` 은 물론이고 **이미 다 캐시된 arm 을 그냥 `--arms` 로 다시
    부르는 것만으로도** `spent_won` 이 0 이 된다(응답 파일 적중 → `fresh_won` 델타 0).

    실측 피해: `A0` 재집계 한 번에 1,901.0 → 0.0, 총지출 5,066.6 → 3,165.6원.
    `_spend_meta` 가 총지출을 **항상 `run.json` 합**으로 계산하므로 권위값 자체가 지워지고,
    `artifacts/` 는 gitignore 라 복구 수단이 없다.
    """
    from scripts import ablation as runner

    out = fake_root / "artifacts" / "ablation" / "v2"
    monkeypatch.setattr(runner, "OUT_DIR", out)
    run_dir = out / "A0"
    run_dir.mkdir(parents=True)
    (run_dir / "run.json").write_text(
        json.dumps({"arm": "A0", "n_run": 88, "spent_won": 1901.0}), encoding="utf-8")

    runner._write_run("A0", {"arm": "A0", "n_run": 88, "stopped_at": None,
                             "spent_won": 0.0, "runs": []})

    saved = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    assert saved["spent_won"] == 1901.0                     # 0 으로 안 덮였다
    assert saved["spent_won_rescore"] == 0.0
    assert "재집계" in saved["spent_won_source"]
    # 총지출의 권위값이 살아 있는가, 여기가 실제 피해가 나타나는 자리다.
    assert runner.spend_from_runs(out, recursive=True)["won"] == 1901.0


def test_the_guard_lives_in_one_place_so_a_fourth_habitat_cannot_appear():
    """같은 부류가 두 번 나왔으면 세 번도 나온다. `run.json` 을 쓰는 지점이
    **`_write_run` 하나**임을 소스로 못박는다. 직접 `write_text` 를 다시 넣으면 깨진다."""
    import re
    from pathlib import Path as P

    source = (P(__file__).resolve().parents[1] / "scripts" / "ablation.py").read_text(
        encoding="utf-8")
    writes = re.findall(r'\(?\s*(?:run_path|arm_dir\([^)]*\)\s*/\s*"run\.json")\s*\)?'
                        r'\.write_text', source)
    assert len(writes) == 1, f"`run.json` 쓰기가 {len(writes)}곳이다 — 가드가 갈라진다"
    assert source.count("def _write_run(") == 1


def test_a_second_paid_run_keeps_the_earlier_amount_visible(fake_root, monkeypatch):
    """두 번째 **유료** 실행도 앞의 값을 조용히 지우지 않는다. 지운 돈은 복구되지
    않지만 지워진 사실은 남아야 누계를 다시 셀 수 있다."""
    from scripts import ablation as runner

    out = fake_root / "artifacts" / "ablation" / "v2"
    monkeypatch.setattr(runner, "OUT_DIR", out)
    (out / "A0").mkdir(parents=True)
    (out / "A0" / "run.json").write_text(json.dumps({"spent_won": 1901.0}),
                                         encoding="utf-8")
    runner._write_run("A0", {"arm": "A0", "spent_won": 42.0, "runs": []})
    saved = json.loads((out / "A0" / "run.json").read_text(encoding="utf-8"))
    assert saved["spent_won"] == 42.0                       # 이번 실행이 권위값
    assert saved["spent_won_previous"] == 1901.0            # 앞의 값이 안 사라진다


def test_a_rescore_does_not_wipe_the_inherited_slot(fake_root, monkeypatch):
    """**네 번째 서식지 (R2E).** 재집계가 `spent_won` 은 지키면서
    `spent_won_previous` 를 조용히 버렸다.

    실측 피해: `v3/A8` 이 R2D 보고서 §0 표에 `15.5 / 370.3` 으로 적혀 있는데 커밋된
    산출물은 `15.5 / None` 이다. 그 사이에 재집계가 한 번 돌았고 370.3원은 이제 그
    표에만 남아 있다. R2E 시점에 같은 위험에 노출돼 있던 것은 `v3/ORACLE` 의
    1,530.4원이고, `--arm ORACLE --offline` 한 번으로 사라질 수 있었다.
    """
    from scripts import ablation as runner

    out = fake_root / "artifacts" / "ablation" / "v3"
    monkeypatch.setattr(runner, "OUT_DIR", out)
    (out / "ORACLE").mkdir(parents=True)
    (out / "ORACLE" / "run.json").write_text(
        json.dumps({"spent_won": 160.0, "spent_won_previous": 1530.4}),
        encoding="utf-8")

    # 재집계, LLM 0회라 `spent_won` 이 0 이다.
    runner._write_run("ORACLE", {"arm": "ORACLE", "spent_won": 0.0, "runs": []})

    saved = json.loads((out / "ORACLE" / "run.json").read_text(encoding="utf-8"))
    assert saved["spent_won"] == 160.0                      # ①~③ 의 가드
    assert saved["spent_won_previous"] == 1530.4, "승계 슬롯이 재집계에 지워졌다"


def test_a_paid_subcohort_run_flags_the_parent_summary_as_stale(fake_root):
    """**여덟 번째 결함의 기전, 만들어지는 자리에서 잡는다.**

    R2F 는 증상을 고쳤다(`render_gate` 의 지출 검산이 `if held_report:` 안에 있어
    heldout 없는 v3 에서 한 번도 안 돌았다). 그런데 검산이 잡아야 했던 **사건**은
    렌더가 아니라 실행에서 생긴다. 하위 코호트를 `--out …/vc` 로 유료 실행하면 그
    폴더의 `summary.json` 만 갱신되고, **부모의 `spent_won` 은 재귀 합인데 손대지
    않는다.** v3 에서 `vc/L3` 51.9원이 정확히 그렇게 갈렸고 게이트를 렌더할 때까지
    아무도 몰랐다.

    분모 라벨은 이 테스트의 관심이 아니다. 재는 것은 **금액의 짝**이다.
    """
    from scripts import ablation as runner

    v3 = fake_root / "artifacts" / "ablation" / "v3"
    (v3 / "A8").mkdir(parents=True)
    (v3 / "A8" / "run.json").write_text(json.dumps({"spent_won": 553.2}),
                                        encoding="utf-8")
    # 부모 요약은 그 시점의 재귀 합(553.2)으로 얼어붙어 있다.
    (v3 / "summary.json").write_text(
        json.dumps({"meta": {"spent_won": 553.2}, "summary": {}}), encoding="utf-8")

    vc = v3 / "vc"
    (vc / "L3").mkdir(parents=True)
    (vc / "summary.json").write_text(
        json.dumps({"meta": {"spent_won": 0.0}, "summary": {}}), encoding="utf-8")

    # 아직은 짝이 맞다. 하위 코호트가 결제하기 전이다.
    assert runner.stale_parent_summaries(vc) == []

    # `vc/L3` 를 유료로 돌렸다. 부모의 재귀 합이 605.1 로 올랐는데 파일은 553.2 다.
    (vc / "L3" / "run.json").write_text(json.dumps({"spent_won": 51.9}),
                                        encoding="utf-8")
    stale = runner.stale_parent_summaries(vc)
    assert len(stale) == 1, f"부모의 낡음을 못 봤다: {stale}"
    assert stale[0]["stored_won"] == 553.2
    assert stale[0]["actual_won"] == 605.1
    assert stale[0]["gap_won"] == 51.9
    assert "재집계" in stale[0]["prescription"]

    # 재집계로 부모를 다시 쓰면 경고가 사라진다. 경고가 처방과 짝이 맞는지 본다.
    (v3 / "summary.json").write_text(
        json.dumps({"meta": {"spent_won": 605.1}, "summary": {}}), encoding="utf-8")
    assert runner.stale_parent_summaries(vc) == []


def test_the_v1_root_summary_is_never_compared_as_a_parent(fake_root):
    """v1 자리에서 멈춘다. 안 멈추면 이 검사가 **모든 실행에서** 거짓 경보를 낸다.

    `artifacts/ablation/summary.json` 의 `spent_won` 은 **v1 만**의 값인데 그 폴더를
    재귀로 세면 `v2`·`v3` 가 통째로 더해진다(`assert_not_v1_root` ①: 3,144.7 →
    8,211.3원). 그 자리는 낡은 것이 아니라 애초에 재귀 합이 아닌 파일이다.
    """
    from scripts import ablation as runner

    v1 = fake_root / "artifacts" / "ablation"
    (v1 / "A6").mkdir(parents=True)
    (v1 / "A6" / "run.json").write_text(json.dumps({"spent_won": 3144.7}),
                                        encoding="utf-8")
    (v1 / "summary.json").write_text(
        json.dumps({"meta": {"spent_won": 3144.7}, "summary": {}}), encoding="utf-8")

    v3 = v1 / "v3"
    (v3 / "A8").mkdir(parents=True)
    (v3 / "A8" / "run.json").write_text(json.dumps({"spent_won": 5066.6}),
                                        encoding="utf-8")

    # v1 을 재귀로 세면 8,211.3 이라 "낡았다" 로 보이지만 그것은 다른 회계다.
    assert runner.spend_from_runs(v1, recursive=True)["won"] == 8211.3
    assert runner.stale_parent_summaries(v3) == [], "v1 자리를 부모로 대조했다"
    assert runner.stale_parent_summaries(v3 / "vc") == []


def test_the_run_rows_record_whether_the_question_was_replayed(monkeypatch):
    """**I-5.** 행에 `cached` 가 없으면 산출물만 보고 "재생인가 측정인가" 를 알 수
    없다. `spent_won 0.0` 인 arm(전량 재생)이 §2 곡선에서 신규 측정과 같은 격으로 선다."""
    from scripts import ablation as runner
    from scripts import error_analysis as ea

    class FakeCache:
        hits = 3
        fresh_won = 10.0

    monkeypatch.setattr(runner, "_use_dirs", lambda arm: None)
    monkeypatch.setattr(runner, "arm_dir", lambda arm: _Path(str(arm)))
    monkeypatch.setattr(_Path, "mkdir", lambda *a, **k: None)
    monkeypatch.setattr(ea, "with_retry", lambda fn, case, **k: (
        {"llm_calls": 1, "prompt_tokens": 10, "completion_tokens": 2,
         "latency_s": 0.02, "response": {}}, True))          # cached=True
    live = runner.run_arm("A8", [{"question_id": "Q-001", "answer_mode": "closed"}],
                          FakeCache(), budget_won=1000.0)
    row = live["runs"][0]
    assert row["cached"] is True                             # 재생이었다
    assert row["judge_cached"] is None                       # closed 는 judge 가 없다
    assert row["request_cache_hits"] == 0
    assert row["spent_won"] == 0.0


def test_the_carried_cache_stats_are_marked_as_not_this_process(tmp_path):
    """재집계가 이어받은 `cache_hits/misses` 에 **표시를 박는다.** 예전에는 아무 표시
    없이 남아서 다음 집계가 이번 프로세스의 값으로 읽었다. 그리고 `cache_misses > 0` 인데
    `fresh_*_tokens == 0` 인 **자기모순**을 조용히 넘기지 않는다 (v2 실측: 435 vs 0)."""
    from scripts import ablation as runner

    (tmp_path / "summary.json").write_text(json.dumps({"meta": {
        "cache_hits": 663, "cache_misses": 435,
        "fresh_prompt_tokens": 0, "fresh_completion_tokens": 0,
        "generated": "2026-08-10T01:02:03"}}), encoding="utf-8")

    class FakeCache:
        hits = misses = fresh_prompt = fresh_completion = 0
        fresh_won = 0.0

    meta = runner._spend_meta(tmp_path, FakeCache(), live={})
    assert meta["cache_stats_are_this_process"] is False
    assert meta["cache_stats_carried_from"] == "2026-08-10T01:02:03"
    assert "435" in meta["cache_stats_inconsistent"]


def test_the_embedding_usage_field_is_counted_in_the_budget(tmp_path):
    """**M-5.** 임베딩 응답은 `promptTokens` 가 아니라 `inputTokens` 를 쓴다. 그것을
    안 세면 dense arm 의 비용이 예산 제동에서 통째로 빠져 상한 판정이 낙관적으로 나온다."""
    from argus import clova
    from scripts import ablation as runner

    cache = runner.RequestCache(tmp_path / "cache")
    original = clova._post
    try:
        clova._post = lambda url, headers, body, timeout=60: (
            200, {"result": {"usage": {"inputTokens": 1000}}}, 0.01)
        cache.install()
        clova._post("https://x/embedding", {}, {"text": "…"})
    finally:
        clova._post = original
    assert cache.fresh_prompt == 1000


# --- v1 자리 덮어쓰기 거부 (I-3) ------------------------------------------------

def test_writing_into_the_v1_root_is_refused_while_a_version_folder_lives_there(fake_root):
    """**I-3.** `artifacts/ablation/v2/` 는 `artifacts/ablation/` **안**에 있다.
    `--out` 없이 돌리는 재집계는 ① v1 지출에 v2 를 합산하고(3,144.7 → 8,211.3)
    ② v1 의 42문항 `report.json` 을 현재 88문항 기준으로 재작성하고
    ③ `--report` 로는 v2 렌더러가 v1 `gate_I.md` 를 덮는다."""
    from scripts import ablation as runner

    v1 = fake_root / "artifacts" / "ablation"
    (v1 / "v2").mkdir(parents=True)
    (v1 / "v2" / "summary.json").write_text("{}", encoding="utf-8")
    with pytest.raises(SystemExit, match="v1 자리"):
        runner.assert_not_v1_root(v1, "arm 산출물")
    # 판 폴더가 아직 없으면 v1 자리는 그냥 그 판의 자리다. 막지 않는다.
    other = fake_root / "artifacts" / "ablation2"
    other.mkdir()
    runner.assert_not_v1_root(other, "arm 산출물")


def test_the_v1_root_refusal_names_the_way_out(fake_root):
    """거부 메시지가 **무엇을 대신 치라는지** 말해야 한다. 아니면 다음 사람이 가드를 지운다."""
    from scripts import ablation as runner

    v1 = fake_root / "artifacts" / "ablation"
    (v1 / "v2").mkdir(parents=True)
    (v1 / "v2" / "summary.json").write_text("{}", encoding="utf-8")
    with pytest.raises(SystemExit) as caught:
        runner.assert_not_v1_root(v1, "게이트 보고서")
    message = str(caught.value)
    assert "--out artifacts/ablation/v2" in message
    assert "ablation_report.py" in message


# --- 게이트 보고서가 산출물만으로 재현되는가 (I-4) --------------------------------

def test_the_budget_line_comes_from_the_artifact_not_the_cli_flag():
    """**I-4.** `budget_cap` 이 CLI 에서만 오면 `--budget 20000` 을 잊은 `--report` 가
    같은 산출물에서 다른 문장을 낸다: "상한 20,000원 대비 25.3% (정지선 16,000)" ↔
    "12,000원 대비 42.2% (9,600)". "LLM 0회 재생성 가능" 이 절반만 사실이 되는 지점이다."""
    from scripts import ablation as runner

    cap, source, note = runner.resolve_budget_cap({"budget_won": 20000.0}, 12000.0)
    assert cap == 20000.0                          # 산출물이 이긴다
    assert "summary.json" in source
    assert "20,000" in note and "12,000" in note    # 다르면 다르다고 적는다
    # 플래그를 안 줘도 (기본값 12,000) 같은 결과여야 한다. 그것이 '재현 가능' 의 뜻이다
    assert runner.resolve_budget_cap({"budget_won": 20000.0}, runner.BUDGET_WON)[0] == 20000.0
    # 산출물에 상한이 없을 때만 CLI 로 떨어진다
    cap, source, note = runner.resolve_budget_cap({}, 12000.0)
    assert (cap, note) == (12000.0, "")
    assert "CLI" in source


def test_the_gate_does_not_keep_its_own_copy_of_the_manual_allocation():
    """겨냥 배분은 **채점기가 단일 원천**이다 (`ea.TARGETED_BY_MANUAL_REVIEW`).
    게이트가 같은 표를 다시 적으면 둘이 갈리고, 어느 쪽이 참인지 산출물로 판정할 수
    없다. "A6·A1b 공동 1위" 라는 틀린 판정이 두 번 나온 자리다.

    배분표가 **묶음으로** 다시 적혀 있으면 안 된다 (산문에서 한 문항을 언급하는 것은
    사본이 아니다. §13 이 Q-094 를 근거로 든다). 대신 상수를 참조해야 한다.

    **건수를 리터럴로 적지 않는다.** 종전에는 `== 13` 이 박혀 있었고, 재측정으로
    오답이 11 로 줄었을 때 **상수가 아니라 이 테스트가** 낡음을 붙잡았다. 총수의 정본
    대조는 `tests/test_error_analysis.py::test_manual_targeting_matches_the_current_wrong_set`
    이 최신 세대 산출물과 직접 한다.
    """
    from pathlib import Path as P

    from scripts import error_analysis as ea

    source = (P(__file__).resolve().parents[1] / "scripts" / "ablation.py").read_text(
        encoding="utf-8")
    assert "TARGETED_BY_MANUAL_REVIEW" in source, "게이트가 단일 원천을 참조하지 않는다"
    for arm, ids, _ in ea.TARGETED_BY_MANUAL_REVIEW:
        if len(ids) < 2:
            continue
        assert '", "'.join(ids) not in source, f"{arm} 의 배분표가 게이트에 사본으로 남았다"
    assert ea.TARGETED_BY_MANUAL_REVIEW, "단일 원천이 비었다"


def test_the_regenerated_v2_gate_reads_the_way_the_documents_quote_it():
    """실물 v2 산출물로 게이트를 렌더해 **문서가 인용하는 문장**을 확인한다.
    산출물이 없으면(신규 클론·CI) 사유를 밝히고 스킵한다. 하드 실패가 아니다."""
    from scripts import ablation as runner

    root = _Path(__file__).resolve().parents[1] / "artifacts" / "ablation" / "v2"
    if not (root / "summary.json").exists():
        pytest.skip(f"{root}/summary.json 이 없다 (gitignore 산출물) — 렌더 대조를 건너뛴다.")
    text = runner.render_gate(root, budget_cap=runner.BUDGET_WON)
    # I-4, 플래그 없이 렌더해도 산출물의 상한(20,000)이 나온다
    assert "상한 20,000원 대비" in text
    # I-9, 분모 36 을 "measured 42" 로 부르지 않는다
    assert "measured 42 중 그리드 36" in text
    assert "| measured 42 |" not in text
    # I-1, measured 36 낙폭이 −8.3%p 다 (이중 반올림이면 −8.4)
    assert "-8.4%p" not in text
    # I-6, 지연 값을 응답 시간으로 인용하지 말라는 경고가 문서에 있다.
    # 필드 이름(`latency_s_mean`)을 단언하지 않는다. 그 이름은 이미 두 번 바뀌었고
    # (`latency_s_mean` → `latency_s_mean_recorded` → 문단이 산출물에서 계산),
    # 이 단언이 그때마다 깨졌다. **경고의 존재**를 재야지 표기를 재면 안 된다.
    assert "'평균 응답 시간' 으로 인용하지 않는다" in text
    # 그리고 그 경고 옆의 수치가 문자열이 아니라 산출물에서 온 것인지. arm 이름과 초 단위가
    # 함께 찍혀야 한다. 하드코딩이던 동안 이 문단은 방향까지 반대인 값을 싣고 있었다.
    assert "현 세대 실측:" in text or "현 세대 산출물에 값이 없다" in text
    # M-7, 부분 실행 검사가 표에 있다
    assert "부분 실행 검사" in text
    # M-8, "요청 캐시가 막았다" 가 아니라 "캐시 전체"
    assert "캐시 전체가" in text


# --- v1 게이트(ablation_report.py), 공용 캐시를 v1 지출로 읽지 않는다 (I-2) --------

def test_the_v1_gate_does_not_read_the_shared_cache_as_its_own_spend():
    """**I-2.** `spend_from_cache()` 는 `artifacts/ablation/cache/` **전부**를 더한다.
    그 캐시는 `--out` 으로 갈리지 않는 공용 캐시라 v1 게이트를 쓴 시점엔 309건이었고
    지금은 985건이다. v1 게이트를 지금 재생성하면 실제 결제 **3,708원이 11,524.2원**으로,
    "상한 대비 30.9%" 가 **"96.0%"** 로 뛴다. 문서가 인용하는 값이라(`total_plan`)
    조용히 틀리면 제출물이 크레딧을 거의 다 쓴 것처럼 보인다.
    """
    from scripts import ablation_report as rep
    from scripts import error_analysis as ea

    # v1 의 지출은 **실측 usage 를 고정**해 둔다. 원 단위 값을 박으면 표시 반올림만 남는다.
    # R2F, 이 단언이 그 서술과 어긋나 있었다. `V1_PAID_WON` 은 `ea.cost_won` 이
    #    **단가를 곱한 파생값**이라, 3707.7 을 박으면 단가 상수를 고치는 순간 이 테스트가
    #    깨진다. 과거 지출은 한 푼도 안 바뀌었는데도. 그래서 **고정하는 것을 usage 로
    #    옮기고**, 원 단위 값은 같은 함수로 다시 계산해 대조한다(이중 서식지 제거).
    assert rep.V1_PAID_REQUESTS == 309
    assert (rep.V1_PAID_PROMPT_TOKENS, rep.V1_PAID_COMPLETION_TOKENS) == (493_629, 34_539)
    assert rep.V1_PAID_WON == ea.cost_won(rep.V1_PAID_PROMPT_TOKENS,
                                          rep.V1_PAID_COMPLETION_TOKENS)
    # 공용 캐시 합은 여전히 계산하지만 **v1 지출이 아니라고** 이름과 표에 적는다.
    shared = rep.spend_from_cache()
    assert shared["requests"] >= rep.V1_PAID_REQUESTS
    assert "공용 캐시 전체" in rep.spend_from_cache.__doc__

    root = _Path(__file__).resolve().parents[1] / "artifacts" / "ablation"
    if not (root / "A6" / "report.json").exists():
        pytest.skip(f"{root}/A6/report.json 이 없다 (gitignore 산출물) — 렌더 대조를 건너뛴다.")
    text = rep.render()                                  # 파일을 쓰지 않는다
    assert "상한 12,000원 대비 **30.9%**" in text          # 96.0% 가 아니다
    assert "v1 지출이 아니다" in text                      # 공용 캐시 행에 라벨이 붙는다
    assert "재계산으로 되살릴 수 없다" in text


def test_the_v1_gate_uses_the_same_precision_rule_as_the_runner():
    """정밀도 규칙의 **세 번째 서식지**. v1 표 전량이 반올림된 `rate` 를 서로 뺐다.
    실측으로 A4 Δ 가 +16.6%p(옳은 값 +16.7) 로, 난이도 '중' 이 +66.6(옳은 값 +66.7) 로
    나왔다. 규칙은 한 곳(`ablation._exact_rate`)에서만 정하고 여기서는 import 한다."""
    from scripts import ablation as runner
    from scripts import ablation_report as rep

    assert rep.exact_rate is runner._exact_rate
    assert rep.exact_rate({"correct": 30, "n_scored": 36}) == 30 / 36
    # 반올림된 rate 를 쓰면 +16.6, 원 분자/분모면 +16.7 이다 (30/36 − 24/36 = 16.667)
    assert rep.delta(rep.exact_rate({"correct": 24, "n_scored": 36}),
                     rep.exact_rate({"correct": 30, "n_scored": 36})) == "+16.7%p"
    assert rep.delta(round(24 / 36, 3), round(30 / 36, 3)) == "+16.6%p"   # 옛 경로


# --- 거부된 heldout 실행이 흔적을 남기지 않는가 (C/I-5) ---------------------------

def test_a_refused_heldout_run_leaves_no_trace_on_disk(fake_root, monkeypatch):
    """**C/I-5.** "거부된 실행이 빈 폴더를 흔적으로 남기지 않는다" 는 성질은
    `assert_heldout_unused` 와 `OUT_DIR.mkdir` 의 **호출 순서**로만 참이었고, 모든 heldout
    테스트가 제동 함수를 직접 불러서 `main()` 을 한 번도 안 태웠다. 그래서 누군가
    `mkdir` 을 방어벽 앞으로 옮겨도 스위트가 잡지 못했다. 여기서 `main()` 을 실제로 태운다.

    빈 폴더와 `index_snapshot.json` 은 산출물만 보는 사람에게 **"돌렸다" 는 흔적**이 되고,
    heldout 처럼 다시 못 재는 실행에서 그 착각은 위험하다.
    """
    from scripts import ablation as runner

    _put_ledger(fake_root, "ablation/v2/heldout")
    before = sorted(str(p.relative_to(fake_root)) for p in fake_root.rglob("*"))
    # `main()` 은 모듈 전역 `OUT_DIR` 을 갈아치운다. monkeypatch 로 복원되게 한다.
    monkeypatch.setattr(runner, "OUT_DIR", runner.OUT_DIR)
    # `_utf8_console()` 을 막는다. pytest 의 capture 는 기본 인코딩으로 쓴 임시파일이라
    #    sys.stdout 을 utf-8 로 재설정하면 캡처를 되읽을 때 죽는다 (그 함수 docstring 참조).
    monkeypatch.setattr(runner, "_utf8_console", lambda: None)

    with pytest.raises(SystemExit, match="최종 구성 1회용"):
        runner.main(["--heldout", "--arm", "A8",
                     "--out", "artifacts/ablation/v3/heldout", "--budget", "1000"])

    after = sorted(str(p.relative_to(fake_root)) for p in fake_root.rglob("*"))
    assert after == before, f"거부된 실행이 흔적을 남겼다: {sorted(set(after) - set(before))}"


def test_the_refusal_happens_before_any_directory_is_created(fake_root, monkeypatch):
    """같은 성질을 반대편에서 못박는다. `mkdir` 이 불렸다면 그 자체가 실패다."""
    from scripts import ablation as runner

    _put_ledger(fake_root, "ablation/v2/heldout")
    monkeypatch.setattr(runner, "OUT_DIR", runner.OUT_DIR)
    monkeypatch.setattr(runner, "_utf8_console", lambda: None)
    made = []
    real_mkdir = _Path.mkdir
    monkeypatch.setattr(_Path, "mkdir",
                        lambda self, *a, **k: (made.append(str(self)),
                                              real_mkdir(self, *a, **k))[1])
    with pytest.raises(SystemExit):
        runner.main(["--heldout", "--arm", "A8",
                     "--out", "artifacts/ablation/v3/heldout"])
    assert made == [], f"제동 앞에서 디렉터리를 만들었다: {made}"


# --- ORACLE, 근거를 골든만으로 교체한다 (진단 전용 · 사다리 밖) ---------------
# `A0`~`A8` 은 전부 "검색 + 생성" 의 합성 성능이라 **근거를 고정한 생성 능력을 잰 적이
# 없다.** 이 arm 이 그 축을 만든다.
# **R2E 재측정이 R1 의 계약을 깼다**. `Δ = 검색이 못 가져온 몫` 이 아니다. 주입은
# 근거를 더하지 않고 **교체**하므로 근거가 줄고, 정오가 갈린 문항 전수에서 골든 회수가
# `A8` 과 동일했다. 아래 테스트들은 **그 계약이 아니라 주입의 무결성**을 지킨다.
# 계약 서술의 정본은 `src/argus/ablation.py` 머리말 §ORACLE 이다.
# 여기 테스트가 지키는 성질은 셋이다.
#
#   ① 플래그가 기본 꺼짐이고 `A8` 과 **그 하나만** 다르다 (Δ 의 귀속)
#   ② 주입 없이 켜면 **명확히 실패**한다 (조용한 빈 근거 = 최악의 Δ 0)
#   ③ 골든이 근거에 실제로 실린다 (켰는데 안 켜진 것도 같은 크기의 위험)

#: 정형 레코드 골든 (`section` 이 없다 → `tools._record_of` 로 열린다).
ORACLE_RECORD_Q = ("Q-001", "HD현대일렉트릭이 2023년 1월에 체결한 2500kVA 배전변압기 등 "
                   "3,500대 공급계약의 계약금액은 얼마인가?",
                   [{"rcept_no": "20230131800162", "section": None}])
#: 정기공시 골든 (`section` 이 있다 → 섹션 인덱스로 열린다. `doc_id` 는 `periodic_<접수번호>`).
ORACLE_SECTION_Q = ("Q-046", "삼성전자 DS 부문 매출액이 제53기와 제57기 사이에 "
                    "어떻게 달라졌는지 각 값을 들어 설명하라.",
                    [{"rcept_no": "20240312000736",
                      "section": "II. 사업의 내용 > 4. 매출 및 수주상황"},
                     {"rcept_no": "20260310002820",
                      "section": "II. 사업의 내용 > 4. 매출 및 수주상황"}])


@pytest.fixture(autouse=True)
def no_oracle_golden_leaks():
    """주입은 모듈 전역이다. 한 테스트가 남긴 골든이 다음 테스트를 조용히 통과시키면
    "주입 없이도 돈다" 는 거짓이 스위트 안에서 만들어진다."""
    yield
    ablation.set_oracle_golden(None)


def _oracle_evidence(question_id: str, question: str, golden: list[dict]) -> str:
    """골든을 주입하고 근거만 조립한다. **LLM 0회**, 생성 경로를 타지 않는다."""
    with ablation.use("ORACLE"), ablation.use_oracle_golden({question_id: golden}):
        evidence, _ = pipeline._gather_golden(question_id, question, Budget())
        return ablation.shape_evidence(evidence)


# --- ① 플래그 --------------------------------------------------------------------

def test_the_oracle_flag_is_off_by_default():
    """기본값이 곧 운영 경로다. 참이면 제출 구성이 골든을 읽는다. 있을 수 없는 일이다."""
    assert ablation.Arm("X").golden_oracle is False
    assert ablation.A8.golden_oracle is False
    assert ablation.current().golden_oracle is False
    assert "golden_oracle" in FLAGS                  # `FLAG_NAMES` 가 자동으로 잡는다


def test_oracle_differs_from_a8_by_exactly_the_golden_flag():
    """단일 변수 조작. 하나라도 더 바뀌면 Δ 가 '근거 교체' 의 것이 아니다."""
    a8, oracle = ablation.flags("A8"), ablation.flags("ORACLE")
    assert {k for k in oracle if oracle[k] != a8[k]} == {"golden_oracle"}


def test_oracle_is_a_side_branch_not_a_rung_and_not_a_loo():
    """사다리에 넣으면 누적 곡선이 '골든을 본 성적' 을 품는다. LOO 표에 넣으면
    '뺀 것' 칸이 성립하지 않는다. `A2`/`A2F` 와 같은 곁가지다."""
    assert "ORACLE" not in ablation.LADDER
    assert "ORACLE" not in ablation.LOO_REMOVED
    assert "ORACLE" in ablation.ARMS and "ORACLE" in ablation.RUN_ORDER
    assert ablation.RUN_ORDER[-1] == "ORACLE"        # 가장 나중, 사다리를 다 확보한 뒤다


def test_oracle_is_declared_diagnostic_so_the_report_cannot_quote_it_as_a_score():
    """평가 시점에 골든이 없다. 이 목록이 없으면 산출물만 보는 사람이 숫자를
    제출 성적으로 읽는다 (러너의 `render_summary` 가 이 목록을 읽어 경고를 찍는다)."""
    assert "ORACLE" in ablation.DIAGNOSTIC_ARMS
    assert all(ablation.ARMS[a].golden_oracle for a in ablation.DIAGNOSTIC_ARMS)


def test_the_oracle_flag_refuses_to_hide_a_retrieval_loo():
    """오라클 위에서는 검색 플래그가 **안 읽힌다**. `L1 + golden_oracle` 의 Δ 는
    정의상 0 이고, 우리는 0 을 "그 구성요소는 기여가 없다" 로 읽는다.
    `META_UNREACHABLE_FLAGS` 와 같은 종류의 방어를 여기도 건다."""
    for flag in ("chain_hop", "counterparty_hop", "hop_ladder", "split_query"):
        extra = {"followup_hop": False} if flag == "chain_hop" else {}
        with pytest.raises(ValueError, match="golden_oracle"):
            replace(ablation.ORACLE, name="ORACLE-broken", **{flag: False}, **extra)
    with pytest.raises(ValueError, match="golden_oracle"):
        replace(ablation.ORACLE, name="ORACLE-dense", dense=True)


def test_the_oracle_flag_does_not_disable_evidence_shaping_or_the_verifier():
    """`shape_evidence` 와 `_verify` 는 근거 출처와 무관하게 돈다. 그래서 이 셋은
    도달 불가 목록에 **없어야** 한다. 있으면 오라클 위 A5·L2·A6 이 못 서게 된다."""
    for flag in ("schema_fields", "calc_values", "verifier"):
        assert flag not in ablation.ORACLE_UNREACHABLE_FLAGS
        replace(ablation.ORACLE, name="ORACLE-shaped", **{flag: False})   # 예외가 없어야 한다


# --- ② 주입이 없으면 **조용히 넘어가지 않는다** ------------------------------------

def test_answering_with_oracle_and_no_injection_fails_loudly():
    """이 파일에서 가장 중요한 오라클 테스트다.

    빈 근거로 내려가면 파이프라인이 `UNANSWERABLE` 을 내보내고 채점기가 오답으로 세며,
    우리는 그것을 "오라클을 줘도 생성이 다 틀렸다" 로 읽는다. 측정이 아니라 **주입
    누락**인데 그것이 판정으로 굳는다. `conftest` 가 CLOVA 를 막고 있으므로 생성까지
    갔다면 다른 예외가 났을 것이다. 즉 **근거 조립 단계에서** 멈춰야 한다.
    """
    ablation.set_oracle_golden(None)
    with ablation.use("ORACLE"):
        with pytest.raises(ablation.OracleGoldenMissing, match="주입되지 않았"):
            pipeline.answer_question("Q-001", ORACLE_RECORD_Q[1])


def test_a_question_missing_from_the_injected_mapping_also_fails_loudly():
    """코호트와 매핑이 어긋난 경우. 조용히 빈 근거가 되면 그 문항만 오답으로 굳는다."""
    with ablation.use("ORACLE"), ablation.use_oracle_golden({"Q-001": []}):
        with pytest.raises(ablation.OracleGoldenMissing, match="Q-999"):
            pipeline.answer_question("Q-999", ORACLE_RECORD_Q[1])


def test_an_injected_empty_golden_is_not_the_same_as_no_injection():
    """`answerable=false` 문항은 골든이 **실제로 없다**. 그 빈 목록과 '주입 안 됨' 을
    같이 취급하면 제동이 정상 문항에서 발화한다."""
    with ablation.use_oracle_golden({"Q-037": []}):
        assert ablation.oracle_golden_for("Q-037") == []


def test_the_injection_is_restored_after_the_context_exits():
    assert ablation.oracle_golden() is None
    with ablation.use_oracle_golden({"Q-001": []}):
        assert ablation.oracle_golden() == {"Q-001": []}
    assert ablation.oracle_golden() is None


# --- ③ 골든이 실제로 실리는가 (LLM 0회) -------------------------------------------

def test_oracle_puts_the_golden_record_into_the_evidence():
    qid, question, golden = ORACLE_RECORD_Q
    evidence = _oracle_evidence(qid, question, golden)
    assert golden[0]["rcept_no"] in evidence
    assert evidence.lstrip().startswith("## ")       # 검색 경로와 같은 블록 형식


def test_oracle_puts_the_golden_section_into_the_evidence():
    """정기공시는 **문서가 아니라 섹션이 근거 단위**다. 목차까지 맞아야 실린 것이다
    (`error_analysis.evidence_hit` 이 같은 규칙으로 센다)."""
    from argus.retrieve.bm25 import norm_path

    qid, question, golden = ORACLE_SECTION_Q
    evidence = _oracle_evidence(qid, question, golden)
    blocks = evidence.split("\n\n## ")
    headers = [b.split("\n", 1)[0] for b in blocks]
    for item in golden:
        assert any(item["rcept_no"] in b for b in blocks)
        assert any(norm_path(item["section"]) in norm_path(h) for h in headers)


def test_oracle_evidence_carries_every_golden_document_of_the_cohort():
    """'켰는데 안 켜진 것' 을 막는 값이다. 오라클의 정의상 골든 **전건**이 실려야
    하고, 안 실리면 그 arm 이 재는 것("근거를 골든만으로 교체했을 때 답이 서는가")이
    정의되지 않는다. 전건이 실려도 잔여 오답을 곧바로 '생성 실패' 로 읽을 수는
    없다. 그 읽기는 근거가 `A8` 과 **바이트 동일**한 문항에서만 성립한다(R2E).

    LLM 0회, 근거 조립 함수만 부른다.
    """
    from scripts import error_analysis as ea

    cases = [c for c in ea.load_cohort("full") if c.get("evidence")]
    mapping = {c["question_id"]: c["evidence"] for c in cases}
    misses = []
    with ablation.use("ORACLE"), ablation.use_oracle_golden(mapping):
        for case in cases:
            evidence, _ = pipeline._gather_golden(case["question_id"],
                                                  case["question"], Budget())
            hit = ea.evidence_hit(case, ablation.shape_evidence(evidence))
            if not hit["all"]:
                misses.append((case["question_id"], hit["n_present"], hit["n_golden"]))
    assert misses == [], f"골든이 근거에 안 실린 문항: {misses}"


def test_oracle_leaves_a_trace_that_phase_h_can_read_back():
    """`parse_hops` 가 골든 문서를 `ids=` 에서 못 읽으면 Phase H 가 **전건을
    RETRIEVAL 로 귀속**한다. 이 측정의 결론이 통째로 뒤집힌다."""
    from scripts import error_analysis as ea

    qid, question, golden = ORACLE_RECORD_Q
    with ablation.use("ORACLE"), ablation.use_oracle_golden({qid: golden}):
        _, lines = pipeline._gather_golden(qid, question, Budget())
    trace = " | ".join(lines)
    assert "oracle=golden" in trace and "제출 구성이 될 수 없다" in trace
    case = {"question_id": qid, "evidence": golden}
    hops = pipeline.parse_hops(trace)
    assert ea.golden_positions(case, hops)["in_candidates"] == [golden[0]["rcept_no"]]


def test_the_default_arm_never_walks_the_oracle_path(monkeypatch):
    """기본 동작은 한 글자도 바뀌면 안 된다. 플래그가 꺼져 있으면 이 함수는
    **불리지 않는다**. 불린다면 운영 경로가 골든을 읽고 있다는 뜻이다."""
    def _boom(*args, **kwargs):
        raise AssertionError("기본 arm 이 오라클 경로를 탔다")

    monkeypatch.setattr(pipeline, "_gather_golden", _boom)
    monkeypatch.setattr(pipeline, "chat",
                        lambda *a, **k: _Fake("답 (근거: 접수번호 20230602800079)"))
    for arm in ("A8", "A0", "L4"):
        with ablation.use(arm):
            pipeline.answer_question("Q-T", CHAIN_Q)


# --- 스텁 없는 도구 인자 검사 -------------------------------------------------
# `tools.execute` 는 `fn(**args)` 이고 `TypeError` 를 `{"error": "인자 오류: …"}` 로 삼킨다.
# 오라클의 섹션 경로는 그 오류를 만나면 **문서 순서 폴백**으로 조용히 넘어가므로, 스텁
# 테스트로는 인자명 오타를 못 잡는다 (`get_history` 에서 이미 한 번 겪은 함정이다).
# 그래서 진짜 도구를 한 번 태운다. LLM 0회 · I/O 만.

def test_search_chunks_really_accepts_the_doc_id_filter_without_stubbing():
    from argus.agent import tools

    _, question, golden = ORACLE_SECTION_Q
    doc_id = f'{pipeline.PERIODIC_DOC_PREFIX}{golden[0]["rcept_no"]}'
    out = tools.execute("search_chunks",
                        {"query": question, "top_k": pipeline.ORACLE_SECTION_POOL,
                         "group": "periodic", "doc_id": doc_id,
                         "path": golden[0]["section"], "include_stubs": True})
    assert "error" not in out, out.get("error")
    assert out["chunks"], "골든 문서 안에서 한 조각도 못 골랐다"
    assert {c["doc_id"] for c in out["chunks"]} == {doc_id}, "다른 문서가 섞였다"


def test_the_doc_id_filter_is_off_by_default_so_the_search_path_is_unchanged():
    """기본 동작 보존. 인자를 안 주면 후보 집합이 종전과 같아야 한다.
    이 필터가 검색 경로에 새면 A8 의 Δ 가 오라클 배선의 것이 된다."""
    from argus.agent import tools

    _, question, golden = ORACLE_SECTION_Q
    plain = tools.execute("search_chunks", {"query": question, "top_k": 5,
                                            "group": "periodic",
                                            "path": golden[0]["section"]})
    assert "error" not in plain
    assert len({c["doc_id"] for c in plain["chunks"]}) > 1, "문서가 하나로 좁혀졌다"


# --- 러너, 주입은 러너의 몫이고 heldout 에는 절대 가지 않는다 --------------------

def test_the_runner_injects_the_cohort_golden_before_running_oracle():
    from scripts import ablation as runner
    from scripts import error_analysis as ea

    cases = ea.load_cohort("full")
    assert runner.install_oracle_golden(["A8", "L4"], cases) is None   # 오라클이 없으면 무동작
    assert ablation.oracle_golden() is None

    mapping = runner.install_oracle_golden(["ORACLE"], cases)
    assert set(mapping) == {c["question_id"] for c in cases}
    assert ablation.oracle_golden() == mapping


def test_the_runner_refuses_to_inject_heldout_golden():
    """heldout 은 구조적으로 여기 못 오지만(러너의 heldout 경로가 앞에서 반환한다)
    되돌릴 수 없는 조작의 제동은 겹으로 건다."""
    from scripts import ablation as runner

    cases = [{"question_id": "Q-H1", "split": "heldout", "evidence": []}]
    with pytest.raises(SystemExit, match="heldout"):
        runner.install_oracle_golden(["ORACLE"], cases)
    assert ablation.oracle_golden() is None


def test_the_summary_marks_the_diagnostic_arm_so_it_is_not_read_as_a_score():
    """표 안에서 ORACLE 은 다른 arm 과 똑같이 생겼다. 이 경고 줄이 유일한 구분이다."""
    from scripts import ablation as runner
    from scripts import error_analysis as ea

    rows = [{**_BLANK_ROW, "question_id": "Q-g1", "track": "grid", "correct": True}]
    report = ea.build_report([], rows)
    meta = {"spent_won": 0.0, "budget_won": 1.0, "cache_hits": 0, "n_cases": 1}
    text = runner.render_summary(runner.summarise({"ORACLE": report}), meta)
    assert "진단 전용" in text and "성적으로 인용" in text
    # 자리도 있어야 한다. 사다리·LOO 표에는 안 찍히므로, 여기 칸이 없으면 경고만 뜨고
    #    잰 값은 산출물 어디에도 안 남는다 (다음 사람이 report.json 을 직접 열어야 한다).
    assert "## 2b. 진단 arm" in text and "| ORACLE |" in text

    plain = runner.render_summary(runner.summarise({"A8": report}), meta)
    assert "진단 전용" not in plain and "2b." not in plain


def test_the_machine_readable_summary_also_marks_the_diagnostic_arm():
    """렌더된 md 에만 경고를 두면 **기계가 읽는 자리는 무방비**다 (R2F).

    `summary.json` 의 `arms["ORACLE"]["rate"]` 는 다른 arm 과 모양이 같아서, 다음
    도구가 그 수를 집어 성적으로 쓰면 아무것도 막지 않는다. `latency_s_note` 와
    같은 규칙으로 플래그와 주석을 붙였고, 여기서 그것을 잠근다.
    """
    from scripts import ablation as runner
    from scripts import error_analysis as ea

    rows = [{**_BLANK_ROW, "question_id": "Q-g1", "track": "grid", "correct": True}]
    report = ea.build_report([], rows)
    summary = runner.summarise({"ORACLE": report, "A8": report})
    assert summary["arms"]["ORACLE"]["diagnostic"] is True
    assert summary["arms"]["A8"]["diagnostic"] is False
    note = summary["arms"]["ORACLE"]["diagnostic_note"]
    # 깨진 계약을 되살리지 않는다. 주석이 '상한' 을 부정하고 Δ 를 금지해야 한다.
    assert "상한도 아니다" in note and "교체" in note
    assert summary["arms"]["A8"]["diagnostic_note"] is None


def test_the_per_arm_error_matrix_also_carries_the_diagnostic_brake():
    """`error_matrix.md` 는 **arm 폴더 안에 홀로 있다**. 경고가 `summary.md`·
    `gate_I.md` 에만 있으면 이 파일만 복사됐을 때 아무것도 막지 않는다 (R2F).

    실물이 그랬다: `v3/ORACLE/error_matrix.md` 가 제출 arm 의 파일과 **모양이 완전히
    같은 채로** `| grid | 64/72 | 0.889 |` 과 *"3축 결론은 이 행에서만 낸다"* 를 찍었다.
    출하된 파일은 다음 **재집계**에서 갱신된다. `--report` 는 이 파일을 안 만든다.
    """
    from scripts import error_analysis as ea

    report = ea.build_report([], [])
    report["arm"] = "ORACLE"
    text = ea.render_matrix([], report)
    assert "진단 전용" in text and "상한도 아니다" in text
    report["arm"] = "A8"
    assert "진단 전용" not in ea.render_matrix([], report)


def test_no_renderer_string_revives_the_broken_oracle_contract():
    """R1 의 두 줄(`Δ = 검색이 아직 못 가져온 몫` · `잔여 오답 = 순수 생성 실패`)은
    R2E 재측정에서 깨졌다. **렌더러가 그것을 단정문으로 다시 찍으면 안 된다.**

    문자열 금지가 아니라 **단정 금지**다. "이렇게 읽지 마라" 는 서술은 그 어구를
    포함해도 된다. 그래서 부정어와 같은 문장에 있는지를 본다.
    """
    source = (_GRAPH_PATH.parents[1] / "scripts" / "ablation.py").read_text(
        encoding="utf-8")
    for phrase in ("검색이 아직 못 가져온 몫", "순수 생성 실패"):
        for line in source.splitlines():
            if phrase not in line:
                continue
            window = source[max(0, source.index(line) - 400):
                            source.index(line) + len(line) + 400]
            assert any(w in window for w in ("아니다", "안 된다", "깨졌다", "뿐이다")), (
                f"깨진 ORACLE 계약을 단정문으로 되살렸다: {line.strip()}")


# --- judge 재현 편차 (σ) ---------------------------------------------------------
# 개방형 문항은 LLM judge 가 채점하는데 **재현 편차를 한 번도 안 쟀다.** 밸류체인
# 12문항에서 A8 의 정답이 재측정마다 6 → 5 → 6 으로 움직인 기록이 있고, 다음 라운드
# arm 들의 Δ 는 1~2문항 규모로 예상된다. σ 를 모르면 Δ 가 신호인지 잡음인지 못 가른다.
# 여기 테스트가 지키는 것은 둘이다. ① 기본값 1 에서 종전 경로가 안 바뀐다.
# ② 반복 회차는 **요청 캐시를 우회한다** (안 하면 3회가 전부 같은 파일을 읽어 측정이 죽는다).

_OPEN_CASE = {"question_id": "Q-901", "answer_mode": "open", "track": "valuechain",
              "question": "밸류체인 질문", "answer_key_points": ["가", "나"],
              "answerable": True, "task_type": "complex_reason", "difficulty": "상",
              "doc_group": "exchange", "evidence": []}


def _seed_response(base: _Path, qid: str = "Q-901") -> None:
    """응답 파일을 미리 깔아 둔다. **생성은 다시 하지 않는다** 는 전제 그 자체다."""
    path = base / "responses" / f"{qid}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "question_id": qid, "llm_calls": 2, "prompt_tokens": 900,
        "completion_tokens": 100, "latency_s": 0.02, "calls": [],
        "response": {"answer": "답변입니다 (근거: 접수번호 20250101000001)",
                     "retrieved_context": "## [공급계약] 어떤기업 – 접수번호 20250101000001",
                     "think_trace": "route=search difficulty=상"}}),
        encoding="utf-8")


class _FakeCache:
    """`RequestCache` 의 최소 대역. `no_cache()` 가 실제로 불렸는지만 센다."""

    hits = misses = 0
    fresh_won = 0.0

    def __init__(self):
        self.bypass_calls = 0
        self.bypassed = 0

    @contextlib.contextmanager
    def no_cache(self):
        self.bypass_calls += 1
        self.bypassed += 1                   # 진짜 캐시가 우회 호출을 세는 자리
        yield self


def _stub_judge(monkeypatch, verdicts):
    """`judge_open` 을 회차별 판정 목록으로 대체한다. **LLM 0회.**"""
    from argus.agent import critic

    calls = []

    def fake(question, evidence, key_points, answer, budget=None):
        mark = verdicts[min(len(calls), len(verdicts) - 1)]
        calls.append(answer)
        return critic.OpenVerdict(mark, mark, mark, f"stub#{len(calls)}")

    monkeypatch.setattr(critic, "judge_open", fake)
    return calls


def test_judge_repeat_one_changes_nothing_about_the_old_path(tmp_path, monkeypatch):
    """**기본값 1 에서는 기존 동작이 한 글자도 바뀌면 안 된다.** judge 는 정확히
    한 번 돌고, 회차 폴더는 안 생기며, 산출물은 종전 자리(`judge/<qid>.json`)에만 쌓인다."""
    from scripts import ablation as runner

    monkeypatch.setattr(runner, "OUT_DIR", tmp_path)
    _seed_response(tmp_path / "A8")
    calls = _stub_judge(monkeypatch, [True])

    live = runner.run_arm("A8", [_OPEN_CASE], _FakeCache(), budget_won=1000.0)

    assert len(calls) == 1                                   # judge 1회
    assert not (tmp_path / "A8" / runner.JUDGE_REPEAT_DIRNAME).exists()
    assert (tmp_path / "A8" / "judge" / "Q-901.json").exists()
    assert "judge_repeat" not in live                        # run.json 서식도 그대로
    assert "judge_repeat_verdicts" not in live["runs"][0]


def test_judge_repeat_three_keeps_every_round_and_never_touches_the_canonical_file(
        tmp_path, monkeypatch):
    """회차를 **덮지 않고 따로** 남긴다. 정본 `judge/<qid>.json` 은 1회차 값 그대로여야
    한다. 그 파일 형식을 깨면 `ea.analyse` 를 비롯한 다른 도구가 통째로 못 읽는다."""
    from scripts import ablation as runner

    monkeypatch.setattr(runner, "OUT_DIR", tmp_path)
    _seed_response(tmp_path / "A8")
    # 정본 1회 + 회차 3회 = 4회. 회차 2 만 판정이 뒤집힌다.
    calls = _stub_judge(monkeypatch, [True, True, False, True])
    cache = _FakeCache()

    live = runner.run_arm("A8", [_OPEN_CASE], cache, budget_won=1000.0, judge_repeat=3)

    assert len(calls) == 4                                   # 정본 1 + 회차 3
    assert cache.bypass_calls == 3                           # 회차마다 캐시 우회
    rounds = runner.judge_round_dirs("A8")
    assert [p.name for p in rounds] == ["round_1", "round_2", "round_3"]
    marks = [json.loads((p / "Q-901.json").read_text(encoding="utf-8"))["correct"]
             for p in rounds]
    assert marks == [True, False, True]
    # 정본은 첫 호출 값 그대로. 회차가 이 파일을 덮지 않았다.
    canonical = json.loads(
        (tmp_path / "A8" / "judge" / "Q-901.json").read_text(encoding="utf-8"))
    assert canonical["correct"] is True and canonical["reason"] == "stub#1"
    assert live["judge_repeat"] == 3
    assert live["runs"][0]["judge_repeat_verdicts"] == [True, False, True]
    assert live["runs"][0]["judge_repeat_agreed"] is False
    # 지출의 출처를 산출물이 스스로 말한다. 실측 v3 full 은 `cache_misses 0` 인데
    #    1,266원을 썼다. 우회 호출 수가 안 남으면 그 조합이 자기모순으로 읽힌다.
    assert live["judge_repeat_bypassed_calls"] == 3


def test_a_finished_round_is_not_bought_twice(tmp_path, monkeypatch):
    """끊긴 실행을 이어 돌릴 때 앞 회차를 다시 결제하지 않는다. 회차마다 파일이 따로라
    이 재사용이 회차 간 독립성을 깨지 않는다."""
    from scripts import ablation as runner

    monkeypatch.setattr(runner, "OUT_DIR", tmp_path)
    _seed_response(tmp_path / "A8")
    done = tmp_path / "A8" / runner.JUDGE_REPEAT_DIRNAME / "round_1" / "Q-901.json"
    done.parent.mkdir(parents=True)
    done.write_text(json.dumps({"question_id": "Q-901", "correct": True,
                                "covers_key_points": True, "grounded": True,
                                "no_fabrication": True, "reason": "이미 산 값"}),
                    encoding="utf-8")
    calls = _stub_judge(monkeypatch, [True])
    cache = _FakeCache()

    runner.run_arm("A8", [_OPEN_CASE], cache, budget_won=1000.0, judge_repeat=2)

    assert len(calls) == 2                       # 정본 1 + 회차 2 뿐 (회차 1은 재사용)
    assert cache.bypass_calls == 1
    assert json.loads(done.read_text(encoding="utf-8"))["reason"] == "이미 산 값"


def test_the_repeat_rounds_really_bypass_the_request_cache(tmp_path):
    """**측정이 성립하는 유일한 조건.** 온도 0.0 이라 같은 프롬프트는 캐시가 반드시
    적중한다. 우회하지 않으면 3회가 디스크에서 같은 파일을 세 번 읽을 뿐이고 "편차 0"
    이 judge 의 성질인지 캐시의 성질인지 가를 수 없다. 우회는 **읽기·쓰기 양쪽**을 끈다."""
    from argus import clova
    from scripts import ablation as runner

    cache = runner.RequestCache(tmp_path / "cache")
    seen = []
    original = clova._post
    try:
        def fake(url, headers, body, timeout=60):
            seen.append(body)
            return 200, {"result": {
                "message": {"content": '{"n":%d}' % len(seen)},
                "usage": {"promptTokens": 100, "completionTokens": 10}}}, 0.01

        clova._post = fake
        cache.install()
        body = {"messages": [{"role": "user", "content": "판정하라"}]}
        clova._post("https://x/chat", {}, body)                  # ① 미적중 -> 저장
        clova._post("https://x/chat", {}, body)                  # ② 적중 (호출 0)
        with cache.no_cache():
            clova._post("https://x/chat", {}, body)              # ③ 우회 -> 진짜 호출
        clova._post("https://x/chat", {}, body)                  # ④ 다시 적중
    finally:
        clova._post = original

    assert len(seen) == 2                       # ①③ 만 실제 호출됐다
    assert (cache.hits, cache.misses, cache.bypassed) == (2, 1, 1)
    # 우회 호출은 캐시에 **쓰지도 않는다.** 썼다면 ④가 3회차 응답을 읽었을 것이고,
    #    나아가 일반 경로의 정본 응답까지 이번 회차 값으로 갈렸을 것이다.
    saved = list((tmp_path / "cache").glob("*.json"))
    assert len(saved) == 1
    stored = json.loads(saved[0].read_text(encoding="utf-8"))
    assert stored["payload"]["result"]["message"]["content"] == '{"n":1}'
    # 비용은 우회분도 센다. 안 세면 예산 제동이 안 걸린다.
    assert cache.fresh_prompt == 200 and cache.fresh_completion == 20


def test_offline_and_judge_repeat_cannot_be_combined(monkeypatch):
    """`--offline` 은 캐시 미적중 호출을 금지하고 반복 회차는 정의상 우회다. 첫 회차에서
    `RuntimeError` 로 죽는 대신 인자 단계에서 이유를 말하고 멈춘다."""
    from scripts import ablation as runner

    monkeypatch.setattr(runner, "_utf8_console", lambda: None)
    with pytest.raises(SystemExit, match="offline"):
        runner.main(["--arm", "A8", "--offline", "--judge-repeat", "3"])


def test_judge_sigma_names_the_question_that_flipped(tmp_path, monkeypatch):
    """**어느 문항이 흔들리는가가 핵심 산출이다.** 정답 수만 남기면 6 → 5 → 6 을
    만든 문항을 못 찾고, 그러면 다음 라운드에서 같은 잡음을 또 Δ 로 읽는다."""
    from scripts import ablation as runner

    monkeypatch.setattr(runner, "OUT_DIR", tmp_path)
    base = tmp_path / "A8"
    cases = [dict(_OPEN_CASE, question_id="Q-901"),
             dict(_OPEN_CASE, question_id="Q-902")]
    for case in cases:
        _seed_response(base, case["question_id"])

    def _put(folder: _Path, qid: str, mark: bool, tag: str) -> None:
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{qid}.json").write_text(json.dumps({
            "question_id": qid, "correct": mark, "covers_key_points": mark,
            "grounded": True, "no_fabrication": True, "reason": tag,
            "prompt_tokens": 1000, "completion_tokens": 50}), encoding="utf-8")

    # Q-901 만 2회차에 뒤집힌다 -> 정답 수 2 → 1 → 2 (밸류체인 6 → 5 → 6 의 축소판)
    for k, mark in enumerate((True, False, True), start=1):
        folder = base / runner.JUDGE_REPEAT_DIRNAME / f"round_{k}"
        _put(folder, "Q-901", mark, f"r{k}")
        _put(folder, "Q-902", True, f"r{k}")
    _put(base / "judge", "Q-901", True, "정본")
    _put(base / "judge", "Q-902", True, "정본")

    out = runner.judge_sigma("A8", cases, {}, cohort="valuechain")

    assert out["correct_counts"] == [2, 1, 2]
    assert out["min"] == 1 and out["max"] == 2
    assert out["mean"] == pytest.approx(1.667, abs=1e-3)
    assert out["sigma_sample"] == pytest.approx(0.577, abs=1e-3)
    assert out["split_questions"] == ["Q-901"]               # 범인을 지목한다
    assert out["questions"]["Q-901"]["rounds"] == [True, False, True]
    assert out["questions"]["Q-902"]["agreed"] is True
    assert out["baseline_correct"]["correct"] == 2
    # 산출물로도 남는다. 보고서는 이 파일을 인용한다.
    assert json.loads((base / "judge_sigma.json").read_text(
        encoding="utf-8"))["split_questions"] == ["Q-901"]


def test_judge_sigma_is_zero_when_the_judge_is_deterministic(tmp_path, monkeypatch):
    """편차가 0 으로 나오는 것도 유효한 결과다. 그때 결론은 "judge 는 결정적이다" 이고
    ±1문항 흔들림의 원인을 **다른 곳에서** 찾아야 한다."""
    from scripts import ablation as runner

    monkeypatch.setattr(runner, "OUT_DIR", tmp_path)
    base = tmp_path / "A8"
    cases = [dict(_OPEN_CASE, question_id="Q-901")]
    _seed_response(base, "Q-901")
    for k in (1, 2, 3):
        folder = base / runner.JUDGE_REPEAT_DIRNAME / f"round_{k}"
        folder.mkdir(parents=True)
        (folder / "Q-901.json").write_text(json.dumps({
            "question_id": "Q-901", "correct": True, "covers_key_points": True,
            "grounded": True, "no_fabrication": True, "reason": f"r{k}"}),
            encoding="utf-8")

    out = runner.judge_sigma("A8", cases, {}, cohort="valuechain")
    assert out["sigma_sample"] == 0.0 and out["range"] == 0
    assert out["split_questions"] == [] and out["agreement_rate"] == 1.0


def test_judge_sigma_restores_the_canonical_judge_directory(tmp_path, monkeypatch):
    """집계는 `ea.JUDGE_DIR` 을 회차 폴더로 **갈아 끼운다.** 되돌리지 않으면 뒤이어
    도는 `score_arm` 이 3회차 판정으로 정본 `report.json` 을 쓴다."""
    from scripts import ablation as runner
    from scripts import error_analysis as ea

    monkeypatch.setattr(runner, "OUT_DIR", tmp_path)
    base = tmp_path / "A8"
    cases = [dict(_OPEN_CASE, question_id="Q-901")]
    _seed_response(base, "Q-901")
    folder = base / runner.JUDGE_REPEAT_DIRNAME / "round_1"
    folder.mkdir(parents=True)
    (folder / "Q-901.json").write_text(json.dumps({
        "question_id": "Q-901", "correct": True, "covers_key_points": True,
        "grounded": True, "no_fabrication": True, "reason": "r1"}), encoding="utf-8")

    runner.judge_sigma("A8", cases, {}, cohort="valuechain")
    assert ea.JUDGE_DIR == base / "judge"


def test_the_arm_without_repeat_rounds_has_nothing_to_measure(tmp_path, monkeypatch):
    """회차가 없으면 `None` 이다. 없는 σ 를 0 으로 적으면 "judge 는 결정적이다" 가
    측정 없이 산출물에 들어간다."""
    from scripts import ablation as runner

    monkeypatch.setattr(runner, "OUT_DIR", tmp_path)
    _seed_response(tmp_path / "A8", "Q-901")
    assert runner.judge_sigma("A8", [dict(_OPEN_CASE)], {}, cohort="valuechain") is None


def test_the_summary_says_the_spend_came_from_a_deliberate_bypass(tmp_path):
    """`--judge-repeat` 실행은 **미적중 0 인데 지출이 있다** (실측 v3 full: 미적중 0 ·
    1,266.0원). 우회 호출 수가 필드로 안 남으면 그 조합이 자기모순으로 읽히고, 다음 사람이
    지출 기록 자체를 의심하게 된다."""
    from scripts import ablation as runner

    class FakeCache:
        hits = misses = 0
        bypassed = 90
        fresh_prompt, fresh_completion = 170_000, 10_339
        fresh_won = 1266.0

    meta = runner._spend_meta(tmp_path, FakeCache(), live={"arm": "A8"})
    assert meta["cache_misses"] == 0 and meta["cache_bypassed"] == 90
    assert meta["this_process_spent_won"] == 1266.0


# --- ② A8-lean, 프롬프트 규칙 다이어트 (R2+ 판정으로 세운 곁가지) ----------------
# 이 절이 지키는 성질 넷.
#   ① 플래그 기본값이 **종전 동작**(규칙 전량)이다. 운영 경로가 안 바뀐다
#   ② `A8` 과 **그 플래그 하나만** 다르다. Δ 가 규칙 8·9 에만 귀속된다
#   ③ 규칙 1~4(채점 직결)가 **두 경로에서 모두** 살아 있다
#   ④ `calc_values` 없이 켤 수 없다. 그 조합은 Δ 가 조용히 0 이다 (실측 확인)

def test_the_lean_rules_flag_is_off_by_default():
    """기본값이 곧 운영 경로다. 참이면 제출 구성이 규칙 8·9 없이 답한다."""
    assert ablation.Arm("X").lean_rules is False
    assert ablation.A8.lean_rules is False
    assert ablation.current().lean_rules is False
    assert ablation.enabled("lean_rules") is False    # 오타는 KeyError 다
    assert "lean_rules" in FLAGS                      # `FLAG_NAMES` 가 자동으로 잡는다


def test_a8_lean_differs_from_a8_by_exactly_the_lean_rules_flag():
    """단일 변수 조작. 하나라도 더 바뀌면 Δ 가 '규칙 8·9' 의 것이 아니다."""
    a8, lean = ablation.flags("A8"), ablation.flags("A8-lean")
    assert {k for k in lean if lean[k] != a8[k]} == {"lean_rules"}
    assert lean["lean_rules"] is True


def test_a8_lean_is_a_side_branch_not_a_rung_and_not_a_loo():
    """사다리에 넣으면 누적 곡선의 Δ(직전) 이 **두 arm 의 차이가 아닌 것**을 가리킨다.
    LOO 표에 넣으면 '뺀 것' 칸이 검색 구성요소와 프롬프트 규칙을 섞는다.
    `A2`/`A2F`/`ORACLE` 과 같은 곁가지다."""
    assert "A8-lean" not in ablation.LADDER
    assert "A8-lean" not in ablation.LOO_REMOVED
    assert "A8-lean" in ablation.ARMS and "A8-lean" in ablation.RUN_ORDER
    # 사다리·LOO 를 다 확보한 뒤, dense·진단 arm 보다 먼저다 (채택 후보이므로).
    order = list(ablation.RUN_ORDER)
    assert order.index("A8-lean") > order.index("A0")
    assert order.index("A8-lean") < order.index("A2F") < order.index("ORACLE")


def test_side_arms_are_derived_not_hand_written():
    """곁가지 목록을 손으로 적으면 새 arm 이 러너의 요약·게이트에서 조용히 빠진다.
    돈을 쓰고도 산출물 문서에 한 글자도 안 남는다 (`ORACLE` 1,747원이 그랬다)."""
    assert set(ablation.SIDE_ARMS) == {a for a in ablation.ARMS
                                       if a not in ablation.LADDER
                                       and a not in ablation.LOO_REMOVED
                                       and a not in ablation.DIAGNOSTIC_ARMS}
    assert "A8-lean" in ablation.SIDE_ARMS
    # 진단 arm 과 겹치면 게이트가 같은 행을 두 번 찍는다.
    assert not set(ablation.SIDE_ARMS) & set(ablation.DIAGNOSTIC_ARMS)


def test_lean_rules_cannot_be_turned_on_without_calc_values():
    """`calc_values=False` 는 `- 계약금액: …원 (49,290억원)` 의 괄호를 떼므로
    `split_trillion_eok` 이 변환할 자리가 없다. 규칙 8·9 가 **애초에 안 붙고** 그것을
    빼는 조작은 Δ 가 조용히 0 이다. 저장 응답 전수로 확인한 의존이다."""
    assert ablation.FLAG_DEPENDENCIES["calc_values"] == ("lean_rules",)
    with pytest.raises(ValueError, match="lean_rules"):
        replace(ablation.A8, name="A8-broken", calc_values=False, lean_rules=True)
    # 사다리의 `calc_values=False` arm 들은 기본값이라 그대로 성립한다.
    for name in ("A0", "A1", "A1b", "A4"):
        assert ablation.ARMS[name].lean_rules is False


def test_shaping_for_a_calc_values_off_arm_leaves_no_trillion_bracket(tmp_path):
    """위 의존의 **근거를 실측으로** 고정한다. 저장 응답의 근거를 `calc_values=False` 로
    성형하면 조 단위 `억원` 괄호가 한 건도 남지 않아야 한다. 남으면 그 문항에서는
    `lean_rules` 가 실효를 갖고, 그때는 의존 선언이 과잉 제동이 된다."""
    from argus.agent import prompts

    root = _Path(__file__).resolve().parents[1] / "artifacts/ablation/v3/A8/responses"
    if not root.is_dir():
        pytest.skip(f"{root} 이 없다 (gitignore 산출물)")
    stripped = replace(ablation.A8, name="probe", calc_values=False)
    left = []
    for path in sorted(root.glob("Q-*.json")):
        response = json.loads(path.read_text(encoding="utf-8"))["response"]
        shaped = ablation.shape_evidence(response["retrieved_context"] or "", stripped)
        if prompts.split_trillion_eok(shaped)[1]:
            left.append(response["question_id"])
    assert left == [], f"괄호가 남는 문항이 있다 — 의존 선언을 재고하라: {left}"


#: 실측 `Q-018`, 이 arm 의 유일한 겨냥. 근거에 조 단위 금액이 둘 실려
#: `split_trillion_eok` 가 2회 발화하고, 그래서 규칙 8·9 가 붙는다.
LEAN_Q = "HD현대중공업이 2023년 7월에 공시한 FPU 1기 공급계약은 정정공시로 계약금액이 얼마나 증가했는가?"

#: 규칙 8·9 의 지문. 둘 중 하나라도 프롬프트에 남으면 규칙이 안 빠진 것이다.
MONEY_RULE_MARKS = ("원 단위 원값", "차이·합계·평균·비교는")


def _capture_answer_prompts(monkeypatch, arm: str, question: str) -> tuple[str, str]:
    """**스텁 없이 진짜 경로를 탄다.** `answer_question` 을 그대로 돌려 라우터·검색·
    근거 성형·프롬프트 조립을 전부 실행하고, 생성 호출의 **메시지만** 가로챈다.

    `build_answer_messages(...)` 는 `chat(...)` 의 인자로 **먼저 평가**되므로, 인자명을
    한 글자 틀리면 여기서 `TypeError` 가 난다. `prompts` 를 직접 부르는 검사는 그
    오타를 못 잡는다 (`tools.execute` 가 `TypeError` 를 `{"error": …}` 로 삼켜 우리가
    이미 한 번 겪은 실패 양식이다).
    """
    from argus.clova import ChatResult

    seen: list[list[dict]] = []

    def _capture(messages, *args, **kwargs):
        if kwargs.get("tag") == "answer":
            seen.append(messages)
        # 근거 표기를 담은 답을 돌려줘 critic 호출 가지로 안 들어가게 한다.
        return ChatResult(text="증가액은 275,900,000,000원입니다.\n\n"
                               "(근거: [기재정정] [공급계약 체결] HD현대중공업, "
                               "2024-12-23, 접수번호 20241223800052, 주요내용)",
                          prompt_tokens=1, completion_tokens=1, latency_s=0.0,
                          finish="stop", tag=kwargs.get("tag", ""))

    monkeypatch.setattr(pipeline, "chat", _capture)
    with ablation.use(arm):
        pipeline.answer_question("Q-018", question)
    assert len(seen) == 1, f"생성 호출이 {len(seen)}회다 — 프롬프트를 가를 수 없다"
    return seen[0][0]["content"], seen[0][1]["content"]


def test_a8_lean_removes_only_the_money_rule_from_the_real_pipeline(monkeypatch):
    """진짜 경로 대조. **system 만 갈리고 user(근거)는 바이트 동일**해야 한다.
    근거가 함께 움직이면 Δ 가 규칙의 것이 아니다."""
    if not (_Path(__file__).resolve().parents[1]
            / "artifacts/exchange_bm25.json").exists():
        pytest.skip("검색 인덱스 산출물이 없다 (gitignore)")

    full_system, full_user = _capture_answer_prompts(monkeypatch, "A8", LEAN_Q)
    lean_system, lean_user = _capture_answer_prompts(monkeypatch, "A8-lean", LEAN_Q)

    # ① 규칙 8·9 가 A8 에는 붙고 A8-lean 에는 안 붙는다.
    for mark in MONEY_RULE_MARKS:
        assert mark in full_system, f"A8 에서 규칙 8·9 가 사라졌다 — 겨냥이 없어진다: {mark}"
        assert mark not in lean_system, f"A8-lean 이 규칙 8·9 를 못 뗐다: {mark}"
    # ② 근거는 한 바이트도 다르지 않다 (`split_trillion_eok` 은 그대로 돈다).
    assert lean_user == full_user
    assert "조 " in full_user and "억원)" in full_user, "괄호 가르기가 안 돌았다"
    # ③ 규칙 1~4 는 두 경로에 모두 있고 순서도 같다 (채점 직결).
    from tests.test_prompt_rules import SCORED_RULE_TEXTS
    for system in (full_system, lean_system):
        at = [system.index(t) for t in SCORED_RULE_TEXTS]   # 없으면 ValueError
        assert at == sorted(at), f"채점 규칙 넷의 순서가 바뀌었다: {at}"
    # ④ 뺀 것은 규칙 8·9 뿐이다. 길이 차가 정확히 그 문자열이다.
    from argus.agent import prompts
    assert len(full_system) - len(lean_system) == len(prompts.MONEY_UNIT_RULE)


# --- A22, 다중 허브 결속 `W=1→3` (v4 신설) --------------------------------------
# 이 arm 은 **정답률이 아니라 근거 회수**를 올린다. 그래서 여기 검사도 '정답' 이
# 아니라 '허브가 실제로 몇 개 결속됐는가 · 근거 칸이 얼마나 늘었는가' 를 본다.
#
# 그리고 이 arm 이 깨면 안 되는 불변이 하나 있다.
# `test_router.py::test_disclosing_company_is_never_bound_as_counterparty`.
# `R1-11 ①` 이 라우터 결속을 오탐 3건으로 기각한 자리이고, `A22` 는 **근거의
# `계약상대:` 필드**에서 허브를 뽑아 그 기각을 비껴간다.

MULTI_HUB_Q = "두산에너빌리티가 2025년 12월에 공시한 원자력 주기기 공급계약의 계약상대는 누구인가?"


def test_a22_arm_differs_from_a8_by_exactly_the_multi_hub_flag():
    diff = [f for f in FLAGS
            if getattr(ablation.ARMS["A22"], f) != getattr(ablation.A8, f)]
    assert diff == ["multi_hub"], diff
    assert "A22" in ablation.SIDE_ARMS          # 사다리도 LOO 도 진단도 아니다
    assert "A22" not in ablation.LADDER and "A22" not in ablation.LOO_REMOVED


def test_multi_hub_cannot_be_on_without_the_counterparty_hop():
    """추가 허브는 발주처 차선 **안**에서만 결속된다. 그 hop 이 꺼진 arm 에서 켜면
    코드가 도달하지 않아 Δ 가 조용히 0 이 된다."""
    assert "multi_hub" in ablation.FLAG_DEPENDENCIES["counterparty_hop"]
    with pytest.raises(ValueError):
        replace(ablation.A7, name="A7-multi", multi_hub=True)
    with pytest.raises(ValueError):
        replace(ablation.ORACLE, name="ORACLE-multi", multi_hub=True)


def test_evidence_hubs_only_takes_graph_nodes_and_skips_the_question_side():
    """허브 후보는 **근거의 `계약상대:`** 뿐이고, 셋을 버린다.
    질문이 지목한 허브(중복) · 질문이 지목한 기업(공시한 쪽일 수 있다) · 노드 아닌 이름."""
    picked = [
        {"markdown": "## 접수번호 1\n- 계약상대: 한국수력원자력(주)\n- 계약금액: 1원"},
        {"markdown": "## 접수번호 2\n- 계약상대: 한국중부발전(주)\n- 계약금액: 2원"},
        {"markdown": "## 접수번호 3\n- 계약상대: 두산에너빌리티\n- 계약금액: 3원"},
        {"markdown": "## 접수번호 4\n- 계약상대: 글로벌 대형기업\n- 계약금액: 4원"},
        {"markdown": "## 접수번호 5\n- 계약금액: 5원"},
    ]
    hubs = pipeline._evidence_hubs(picked, primary=None, companies=())
    assert "한국수력원자력" in hubs and "한국중부발전" in hubs
    assert not any("글로벌" in h for h in hubs), "비실명은 노드가 아니다"

    # 질문이 지목한 허브는 이미 결속돼 있다. 두 번 세지 않는다.
    assert "한국수력원자력" not in pipeline._evidence_hubs(
        picked, primary="한국수력원자력", companies=())
    # 질문이 지목한 기업은 **공시한 쪽**일 수 있다 (`R1-11 ①` 의 기각 근거).
    assert "두산에너빌리티" not in pipeline._evidence_hubs(
        picked, primary=None, companies=("두산에너빌리티",))


def test_evidence_hubs_orders_by_frequency_then_first_appearance():
    picked = [{"markdown": "- 계약상대: 한국중부발전(주)"},
              {"markdown": "- 계약상대: 한국수력원자력(주)"},
              {"markdown": "- 계약상대: 한국수력원자력㈜"}]
    assert pipeline._evidence_hubs(picked, None, ()) == ["한국수력원자력", "한국중부발전"]


def _counterparty_lines(arm: str, question: str) -> list[str]:
    with ablation.use(arm):
        _, trace = pipeline._gather_evidence(route(question), question, Budget())
    return [line for line in trace if "strat=counterparty" in line]


def test_a22_binds_hubs_from_the_evidence_where_a8_binds_none():
    """진짜 경로. 질문이 발주처를 지목하지 않아 `A8` 은 hop 이 아예 안 서는데,
    `A22` 는 근거의 `계약상대:` 에서 허브를 찾아 선다."""
    if not (_Path(__file__).resolve().parents[1]
            / "artifacts/exchange_bm25.json").exists():
        pytest.skip("검색 인덱스 산출물이 없다 (gitignore)")

    assert route(MULTI_HUB_Q).counterparty is None, "이 문항은 라우터 결속이 없어야 한다"
    assert _counterparty_lines("A8", MULTI_HUB_Q) == []
    lines = _counterparty_lines("A22", MULTI_HUB_Q)
    assert lines, "A22 가 근거에서 허브를 못 찾았다 — 조용한 no-op 이다"
    assert len(lines) <= pipeline.MAX_COUNTERPARTY_HUBS, "W=3 상한이 안 걸렸다"


def test_a22_never_exceeds_the_hub_cap_or_the_evidence_cap():
    """회귀 방어 둘. 허브 수는 `W=3` 이하이고, 발주처 근거 칸은 첫 허브 6 +
    추가 허브당 2 를 넘지 않는다. 상한 없이 열면 검색 근거를 프롬프트 뒤로 민다."""
    if not (_Path(__file__).resolve().parents[1]
            / "artifacts/exchange_bm25.json").exists():
        pytest.skip("검색 인덱스 산출물이 없다 (gitignore)")

    import re

    cap = (pipeline.MAX_COUNTERPARTY_EVIDENCE
           + pipeline.MAX_EXTRA_HUB_EVIDENCE * (pipeline.MAX_COUNTERPARTY_HUBS - 1))
    for question in (MULTI_HUB_Q, CHAIN_Q, HOLDING_Q, SPLIT_Q):
        lines = _counterparty_lines("A22", question)
        assert len(lines) <= pipeline.MAX_COUNTERPARTY_HUBS, (question, lines)
        taken = sum(int(m.group(1)) for line in lines
                    if (m := re.search(r"\bn=(\d+)", line)))
        assert taken <= cap, (question, taken, cap)


def test_w1_arms_call_the_counterparty_hop_with_the_old_arguments():
    """`A22` 를 안 켠 arm 은 **호출 인자가 종전과 같아야** 한다. 기본 인자가
    움직이면 v1~v4 측정이 전부 무효다."""
    seen = []

    def _spy(hub, question, budget, trace, take, **kwargs):
        seen.append((hub, kwargs))
        return 0

    question = "한국수력원자력이 발주한 계약을 공시한 상장사는?"
    assert route(question).counterparty == "한국수력원자력"
    with ablation.use("A8"), pytest.MonkeyPatch.context() as mp:
        mp.setattr(pipeline, "_gather_counterparty", _spy)
        pipeline._gather_evidence(route(question), question, Budget())
    assert len(seen) == 1, seen
    assert seen[0] == ("한국수력원자력", {"limit": pipeline.MAX_COUNTERPARTY_EVIDENCE,
                                        "prefer": ()})


# --- A10, V7-1: the two arms allowed to move the temperature control -------
# 09-ablation §I-3 이 "온도 0.0" 을 고정으로 못박았고 v3 까지 그 값은 파이프라인에
# **하드코딩**돼 있었다. `Arm` 으로 옮기는 조작의 위험은 하나다. **기본값이 0.0 이
# 아니면 v1~v4 측정이 전부 무효가 된다.** A10 applies self-consistency to every
# question, while V7-1 narrows it to the scope the paper states (a fixed
# answer set and a parser). These are the only two arms that move this
# control, so the first check below covers both.

def test_temperature_moved_to_the_arm_but_the_default_is_still_zero():
    assert "temperature" in FLAGS and "sc_k" in FLAGS
    for name, arm in ablation.ARMS.items():
        if name in ("A10", "V7-1"):
            continue
        assert arm.temperature == 0.0, f"{name} 의 온도가 0.0 이 아니다 — 통제가 깨졌다"
        assert arm.sc_k == 1, f"{name} 이 표본을 둘 이상 뽑는다"
    assert ablation.current().temperature == 0.0        # 스위치를 안 건드린 운영 경로


def test_a10_moves_only_sc_k_and_temperature_from_a8():
    diff = [f for f in FLAGS
            if getattr(ablation.ARMS["A10"], f) != getattr(ablation.A8, f)]
    assert sorted(diff) == ["sc_k", "temperature"], diff
    assert ablation.ARMS["A10"].sc_k == 3
    assert ablation.ARMS["A10"].temperature > 0.0
    assert "A10" in ablation.SIDE_ARMS
    assert "A10" not in ablation.LADDER and "A10" not in ablation.LOO_REMOVED


def test_v7_1_narrows_self_consistency_to_the_papers_scope():
    diff = [f for f in FLAGS
            if getattr(ablation.ARMS["V7-1"], f) != getattr(ablation.A42, f)]
    assert sorted(diff) == ["sc_figures_only", "sc_k", "temperature"], diff
    assert ablation.ARMS["V7-1"].sc_k == 3
    assert ablation.ARMS["V7-1"].temperature > 0.0
    assert ablation.ARMS["V7-1"].sc_figures_only is True
    assert "V7-1" in ablation.SIDE_ARMS
    assert "V7-1" not in ablation.LADDER and "V7-1" not in ablation.LOO_REMOVED


def test_v7_5_pairs_the_relevance_allocation_with_the_region_field():
    diff = [f for f in FLAGS
            if getattr(ablation.ARMS["V7-5"], f) != getattr(ablation.A42, f)]
    assert diff == ["counterparty_relevance"], diff
    assert ablation.ARMS["V7-5"].counterparty_relevance is True
    assert "V7-5" in ablation.SIDE_ARMS
    assert "V7-5" not in ablation.LADDER and "V7-5" not in ablation.LOO_REMOVED


def test_v7_2_adopts_regeneration_on_a_strict_subset_of_failure_kinds():
    diff = [f for f in FLAGS
            if getattr(ablation.ARMS["V7-2"], f) != getattr(ablation.A42, f)]
    assert diff == ["regenerate_partial"], diff
    assert ablation.ARMS["V7-2"].regenerate_partial is True
    assert "V7-2" in ablation.SIDE_ARMS
    assert "V7-2" not in ablation.LADDER and "V7-2" not in ablation.LOO_REMOVED


def test_v7_3_turns_on_only_the_comparison_line():
    diff = [f for f in FLAGS
            if getattr(ablation.ARMS["V7-3"], f) != getattr(ablation.A42, f)]
    assert diff == ["compare_line"], diff
    assert ablation.ARMS["V7-3"].compare_line is True
    assert "V7-3" in ablation.SIDE_ARMS
    assert "V7-3" not in ablation.LADDER and "V7-3" not in ablation.LOO_REMOVED


def test_v7_4_turns_on_only_the_unit_line():
    diff = [f for f in FLAGS
            if getattr(ablation.ARMS["V7-4"], f) != getattr(ablation.A42, f)]
    assert diff == ["unit_line"], diff
    assert ablation.ARMS["V7-4"].unit_line is True
    assert "V7-4" in ablation.SIDE_ARMS
    assert "V7-4" not in ablation.LADDER and "V7-4" not in ablation.LOO_REMOVED


def test_self_consistency_at_temperature_zero_is_rejected():
    """온도 0 에서 k 를 올리면 표본이 전부 같아 다수결이 정의상 만장일치가 된다.
    그 0 을 "SC 는 효과가 없다" 로 읽는 것이 이 저장소가 막으려는 사고다."""
    with pytest.raises(ValueError):
        replace(ablation.A8, name="A8-sc0", sc_k=3)
    with pytest.raises(ValueError):
        replace(ablation.A8, name="A8-sc-", sc_k=0)


def test_the_answer_call_uses_the_arm_temperature(monkeypatch):
    """배선 검사. 하드코딩이 남아 있으면 `A10` 이 조용한 no-op 이 된다."""
    seen = []

    class _R:
        text = "답변입니다 [접수번호 20250101000001]"
        prompt_tokens = completion_tokens = 1
        latency_s = 0.0
        finish = "stop"
        tool_calls = None
        tag = "answer"

    def _chat(messages, max_tokens=512, temperature=0.0, tag="", **kw):
        seen.append((tag, temperature))
        return _R()

    monkeypatch.setattr(pipeline, "chat", _chat)
    monkeypatch.setattr(pipeline, "_gather_evidence",
                        lambda r, q, b, *a: ("## 접수번호 20250101000001\n- 계약금액: 1원", []))
    monkeypatch.setattr(pipeline, "_verify", lambda *a, **kw: a[1])
    # critic 은 이 검사의 축이 아니다. 근거 표기가 있다고 보고 건너뛴다.
    monkeypatch.setattr(pipeline, "quick_check", lambda answer: {"has_citation": True})

    for arm, want_calls, want_temp in (("A8", 1, 0.0), ("A10", 3, 0.7)):
        seen.clear()
        with ablation.use(arm):
            pipeline.answer_question("Q-001", "삼성전자의 2025년 계약금액은 얼마인가?")
        answers = [t for t in seen if t[0].startswith("answer")]
        assert len(answers) == want_calls, (arm, seen)
        assert all(temp == want_temp for _, temp in answers), (arm, seen)


def test_v7_1_call_site_gates_on_whether_the_first_sample_states_a_figure(monkeypatch):
    """Wiring check for the V7-1 scope gate at the call site
    (`if arm.sc_k > 1 and _sc_applies(answer):`). If the `_sc_applies`
    conjunct is ever dropped, a narrative first answer would draw the extra
    samples too, which silently reverts V7-1 to A10's previously-rejected
    all-questions shape while the flag still reads as scoped.
    """
    seen = []

    class _R:
        prompt_tokens = completion_tokens = 1
        latency_s = 0.0
        finish = "stop"
        tool_calls = None
        tag = "answer"

        def __init__(self, text):
            self.text = text

    def _run(first_answer):
        seen.clear()

        def _chat(messages, max_tokens=512, temperature=0.0, tag="", **kw):
            seen.append(tag)
            return _R(first_answer)

        monkeypatch.setattr(pipeline, "chat", _chat)
        monkeypatch.setattr(pipeline, "_gather_evidence",
                            lambda r, q, b, *a: ("## 접수번호 20250101000001\n- 계약금액: 1원", []))
        monkeypatch.setattr(pipeline, "_verify", lambda *a, **kw: a[1])
        # The critic is not this test's axis: assume a citation is present.
        monkeypatch.setattr(pipeline, "quick_check", lambda answer: {"has_citation": True})
        with ablation.use("V7-1"):
            pipeline.answer_question("Q-001", "삼성전자의 2025년 계약금액은 얼마인가?")
        return [t for t in seen if t.startswith("answer")]

    # (a) No figure in the first sample: the gate stays closed and only the
    # first sample is generated.
    narrative_calls = _run("서술만 있는 답변입니다.")
    assert len(narrative_calls) == 1, narrative_calls

    # (b) An amount in the first sample: the gate opens and all sc_k=3
    # samples are drawn.
    figure_calls = _run("계약금액은 1,102,000,000,000원입니다.")
    assert len(figure_calls) == 3, figure_calls


def test_self_consistency_picks_the_majority_and_falls_back_to_the_first_sample():
    """다수결의 두 갈래를 다 본다. 다수가 있으면 그것, 동수면 **먼저 나온 것**."""
    from argus.agent.budget import Budget as _B

    class _Arm:
        sc_k = 3
        temperature = 0.7

    def _run(texts):
        trace = []
        pool = list(texts[1:])

        class _R:
            prompt_tokens = completion_tokens = 1

            def __init__(self, text):
                self.text = text

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(pipeline, "chat",
                       lambda *a, **kw: _R(pool.pop(0)))
            picked = pipeline._self_consistent([], 300, _Arm(), texts[0], _B(), trace)
        return picked, " | ".join(trace)

    # ① 2:1 다수결, 소수인 첫 표본을 버린다.
    picked, trace = _run(["금액은 1,000원입니다.", "금액은 2,000원입니다.",
                          "총 2,000원이었습니다."])
    assert "2,000" in picked and "votes=2" in trace and "k=3/3" in trace
    # ② 전부 다르면 첫 표본 (동수 → 먼저 나온 키).
    picked, trace = _run(["금액은 1,000원입니다.", "금액은 2,000원입니다.",
                          "금액은 3,000원입니다."])
    assert picked == "금액은 1,000원입니다." and "keys=3" in trace and "picked=0" in trace


def test_self_consistency_keeps_the_first_answer_when_the_budget_runs_out():
    """예산이 끊겼다고 이미 받아 둔 정답을 버리면 안 된다. 긴 근거 문항에서
    토큰 상한 20,000 이 3회차에 실제로 물린다."""
    from argus.agent.budget import Budget as _B

    class _Arm:
        sc_k = 3
        temperature = 0.7

    budget = _B()
    budget.spend_llm(19_999, 0)                     # 다음 호출은 토큰 상한을 넘는다
    trace = []

    class _R:
        text = "다른 답 2,000원"
        prompt_tokens = 500
        completion_tokens = 10

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(pipeline, "chat", lambda *a, **kw: _R())
        picked = pipeline._self_consistent([], 300, _Arm(), "첫 답 1,000원", budget, trace)
    assert picked == "첫 답 1,000원"
    assert "sc stop=budget" in " | ".join(trace)


def test_sc_samples_after_the_first_bypass_the_request_cache():
    """프롬프트도 온도도 같아 **캐시 키가 하나**다. 우회하지 않으면 표본이 전부
    같아지고 다수결이 공짜 만장일치가 된다. `A10` 의 Δ 가 조용히 0 이 된다."""
    from argus import clova
    from argus.agent.budget import Budget as _B

    class _Arm:
        sc_k = 3
        temperature = 0.7

    bypassed = []

    class _R:
        text = "답 1,000원"
        prompt_tokens = completion_tokens = 1

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(pipeline, "chat",
                   lambda *a, **kw: (bypassed.append(clova.cache_bypassed()), _R())[1])
        pipeline._self_consistent([], 300, _Arm(), "답 1,000원", _B(), [])
    assert bypassed == [True, True], bypassed        # 2·3회차만 부른다. 둘 다 우회
    assert not clova.cache_bypassed(), "블록을 나온 뒤에도 우회가 켜져 있다"


# --- 상류 장애 가드 (v4 A1) ---------------------------------------------------
# 러너가 사람이 읽는 `생성 실패:` 문구를 긁는 동안, 그 문구를 안 쓰는 상류 장애 경로
#    셋이 조용히 캐시됐다. 실측으로 `v4/L6` 88문항 중 3건이 critic 을 건너뛴 채 굳었고
#    **어느 산출물에도 안 찍혔다.** 아래가 그 두 구멍(제동·표시)을 각각 고정한다.


def _run_case_with_trace(tmp_path, monkeypatch, trace: str):
    """`run_case` 를 저 트레이스를 내는 가짜 파이프라인에 물려 한 번 돌린다. LLM 0회."""
    from scripts import error_analysis as ea

    monkeypatch.setattr(ea, "RESPONSE_DIR", tmp_path / "responses")

    def _fake_answer(question_id, question):
        return {"question_id": question_id, "question": question,
                "retrieved_context": "", "think_trace": trace, "answer": "1,000원"}

    monkeypatch.setattr(pipeline, "answer_question", _fake_answer)
    return ea.run_case({"question_id": "Q-999", "question": "얼마인가"})


@pytest.mark.parametrize("stage,trace", [
    ("generate", "upstream_error stage=generate status=429 retryable=True "
                 "생성 실패: CLOVA 호출 실패 429"),
    # 아래 둘이 종전 프로즈 가드를 통과하던 자리다. critic 은 실제로 발화했고
    #    (v4/L6 Q-049·Q-054·Q-090), regenerate 는 **운영 기본값 A20r 전용 경로**다.
    ("critic", "upstream_error stage=critic status=429 retryable=True "
               "critic 생략: CLOVA 호출 실패 429"),
    ("regenerate", "upstream_error stage=regenerate status=429 retryable=True "
                   "verify regen=skip_error kept=original: CLOVA 호출 실패 429"),
])
def test_the_runner_refuses_to_cache_an_upstream_failure(tmp_path, monkeypatch,
                                                         stage, trace):
    from scripts import error_analysis as ea

    with pytest.raises(RuntimeError) as caught:
        _run_case_with_trace(tmp_path, monkeypatch, trace)
    assert f"stage={stage}" in str(caught.value)
    # 문구에 429 가 남아야 `with_retry` 가 재시도한다. 종전에는 러너가 이 429 를
    #    끝내 보지 못했다 (파이프라인이 5키 계약을 지키려고 예외를 삼키므로).
    assert ea._is_rate_limit(caught.value)
    assert not (tmp_path / "responses" / "Q-999.json").exists(), "실패가 캐시로 굳었다"


def test_a_normal_answer_still_caches(tmp_path, monkeypatch):
    """가드가 넓어졌다고 정상 응답까지 막으면 안 된다."""
    record, cached = _run_case_with_trace(
        tmp_path, monkeypatch, "route=closed_lookup | hop=0 verdict=sufficient")
    assert cached is False and record["response"]["answer"] == "1,000원"
    assert (tmp_path / "responses" / "Q-999.json").exists()


def test_a_client_error_is_also_refused(tmp_path, monkeypatch):
    """`retryable=False` 도 우리 답이 아니다. 종전 생성 경로와 같은 판단이다."""
    with pytest.raises(RuntimeError) as caught:
        _run_case_with_trace(
            tmp_path, monkeypatch,
            "upstream_error stage=generate status=400 retryable=False 생성 실패: 400")
    assert "retryable=False" in str(caught.value)


def test_upstream_note_reads_the_marked_paths_and_the_unmarked_degraded_one():
    """조사 표에 없던 **다섯 번째 경로**를 고정한다.

    `pipeline._self_consistent` 는 상류가 죽으면 `sc skip=upstream` 만 적고
    `_upstream_mark` 를 안 붙인다. `v4/A10/responses/Q-049.json` 이 실물이다.
    러너 제동은 안 걸되(표식 없는 저하 경로까지 막으면 저장된 arm 의 재실행이 달라진다)
    **산출물에는 찍혀야 한다.**
    """
    from scripts import error_analysis as ea

    marked = ea.upstream_note(
        "upstream_error stage=critic status=429 retryable=True critic 생략: …")
    assert marked == {"stage": "critic", "status": "429",
                      "retryable": True, "marked": True}

    degraded = ea.upstream_note(
        "generate tokens=1+1 | sc skip=upstream k=2/3 (CLOVA 호출 실패 429: …)")
    assert degraded == {"stage": "sc", "status": "429",
                        "retryable": True, "marked": False}
    # 그리고 이것은 `upstream_failure()` 로는 안 잡힌다. 그래서 갈래를 나눴다.
    assert pipeline.upstream_failure(
        "generate | sc skip=upstream k=2/3 (CLOVA 호출 실패 429)") is None

    assert ea.upstream_note("route=closed_lookup | hop=0 verdict=sufficient") is None
    assert ea.upstream_note("") is None


def test_the_report_names_the_questions_that_hit_an_upstream_failure():
    """제동만으로는 **이미 굳은** 3건이 안 보인다. 산출물이 이름을 대야 한다."""
    from scripts import error_analysis as ea

    rows = [
        {"question_id": "Q-001", "upstream_failure": None},
        {"question_id": "Q-049", "upstream_failure": {"stage": "critic", "status": "429",
                                                      "retryable": True, "marked": True}},
    ]
    assert ea.upstream_failures(rows) == {
        "Q-049": {"stage": "critic", "status": "429", "retryable": True, "marked": True}}
    assert ea.upstream_failures([{"question_id": "Q-001",
                                  "upstream_failure": None}]) == {}


def test_the_error_matrix_carries_the_upstream_warning_on_its_own():
    """arm 폴더만 복사돼 홀로 여행할 수 있으므로 제동도 그 파일 안에 있어야 한다."""
    from scripts import error_analysis as ea

    report = ea.build_report([], [])
    report["arm"] = "L6"
    report["upstream_failures"] = {"Q-049": {"stage": "critic", "status": "429",
                                             "retryable": True, "marked": True}}
    empty = ea.build_report([], [])
    empty["arm"] = "A20r"
    with_warning = ea.render_matrix([], report)
    assert "상류 장애 1건" in with_warning and "Q-049" in with_warning
    assert "상류 장애" not in ea.render_matrix([], empty)


# --- 게이트 보고서의 절 분해 (v4 B1) ------------------------------------------


def test_the_gate_report_is_assembled_from_section_functions():
    """`render_gate` 는 933줄짜리 함수 하나였다. 다시 그렇게 자라지 않게 고정한다.

    합격 조건은 미학이 아니다. **절 함수가 순수해야** 절 단위 테스트가 가능하고,
    **순서가 문서의 순서여야** 게이트 문서의 목차가 코드에서 읽힌다.
    """
    import inspect

    from scripts import ablation as runner

    order = [
        runner._gate_arm_flags, runner._gate_cost, runner._gate_cumulative,
        runner._gate_by_axis, runner._gate_by_track, runner._gate_loo,
        runner._gate_diagnostic_arms, runner._gate_valuechain,
        runner._gate_failure_stages, runner._gate_flipped, runner._gate_targeting,
        runner._gate_targeting_vs_loo, runner._gate_paper_comparison,
        runner._gate_knee, runner._gate_measured42, runner._gate_reading_notes,
        runner._gate_heldout,
    ]
    body = inspect.getsource(runner.render_gate)
    seen = [name for name in (f.__name__ for f in order) if f"{name}(c" in body]
    assert seen == [f.__name__ for f in order], "절의 호출 순서가 목록과 다르다"

    # 어느 절도 문서를 **직접 쓰지 않는다**. 줄 목록만 돌려준다. 이것이 깨지면
    #    절 하나가 몰래 디스크를 만지기 시작한 것이다.
    for fn in order:
        source = inspect.getsource(fn)
        for forbidden in ("write_text(", "mkdir(", "print(", "open("):
            assert forbidden not in source, f"{fn.__name__} 이 {forbidden} 을 쓴다"
        assert source.rstrip().endswith("return out") or "return out," in source

    # 절이 읽는 공용 상태는 **얼려 둔다.** 절이 그것을 고치면 뒤 절의 표가 조용히
    #    달라지고 게이트 문서는 그 사실을 드러낼 방법이 없다 (숫자만 남는다).
    assert runner._GateContext.__dataclass_params__.frozen

    # `render_gate` 자체는 이제 조립만 한다. 933줄로 되돌아가면 여기서 걸린다.
    assert len(body.splitlines()) < 120, "render_gate 가 다시 자랐다"


def test_every_gate_section_is_reachable_from_the_real_v2_artifacts():
    """실물로 한 번 관통시킨다. 절 목록이 맞아도 인자가 어긋나면 여기서 죽는다."""
    from scripts import ablation as runner

    root = _Path(__file__).resolve().parents[1] / "artifacts" / "ablation" / "v2"
    if not (root / "summary.json").exists():
        pytest.skip(f"{root}/summary.json 이 없다 (gitignore 산출물)")
    text = runner.render_gate(root, budget_cap=runner.BUDGET_WON)
    # 절 머리(`## N.`)가 열넷 다 있어야 한다. 하나가 조용히 빠지면 여기서 걸린다.
    for heading in ("## 0.", "## 1.", "## 2.", "## 3.", "## 4.", "## 5.", "## 6.",
                    "## 7.", "## 8.", "## 9.", "### 9b.", "## 10.", "## 11.",
                    "## 12.", "## 13.", "## 14."):
        assert heading in text, f"{heading} 절이 사라졌다"
    # 두 번 렌더해도 같은 문서다. 절 함수가 공용 상태를 고치면 여기서 갈린다.
    assert text == runner.render_gate(root, budget_cap=runner.BUDGET_WON)
