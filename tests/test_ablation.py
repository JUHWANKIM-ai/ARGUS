"""Phase I — arm 스위치 검증. **LLM 0회.**

09-ablation §I-3 은 "arm 외 모든 것을 고정한다" 고만 말하지만, 진짜 위험은 그 반대편에
있다 — **끈 줄 알았는데 안 꺼진 것**이다. 그러면 Δ 는 0 에 가깝게 나오고 우리는 그것을
"기여가 없다" 로 읽는다. 스위치가 실제로 동작하는지 확인할 값싼 수단은 `think_trace` 다.
검색 hop 은 전부 key=value 로 남으므로 크레딧 한 푼 없이 되읽을 수 있다.

여기 테스트는 전부 `_gather_evidence` / `shape_evidence` 를 직접 부른다. 생성은 타지
않으므로 CLOVA 호출이 0 이고, `conftest.block_clova_network` 가 그걸 강제한다.
"""
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
    assert ablation.RUN_ORDER[0] == "A6"          # 가장 비싼 구성을 먼저 확보한다


#: 모든 스위치. 새 플래그를 넣고 여기 안 적으면 아래 계약 검사들이 그 플래그를 못 본다.
FLAGS = ("retrieval", "schema_fields", "dense", "hop_ladder", "chain_hop",
         "counterparty_hop", "split_query", "calc_values", "verifier")


def test_cumulative_ladder_never_turns_a_component_off():
    """A0 → A6 은 **누적 추가**다. 중간에 꺼지는 플래그가 있으면 Δ 의 귀속이 깨진다."""
    flags = [f for f in FLAGS if f != "retrieval"]
    depth = {"meta": 0, "exchange": 1, "all": 2}
    for lower, upper in zip(ablation.LADDER, ablation.LADDER[1:]):
        a, b = ablation.ARMS[lower], ablation.ARMS[upper]
        assert depth[b.retrieval] >= depth[a.retrieval], (lower, upper)
        for flag in flags:
            assert getattr(b, flag) >= getattr(a, flag), (lower, upper, flag)


def test_loo_arms_differ_from_a6_by_exactly_one_flag():
    for name, flag in (("L1", "chain_hop"), ("L2", "schema_fields")):
        arm = ablation.ARMS[name]
        diff = [f for f in FLAGS if getattr(arm, f) != getattr(ablation.A6, f)]
        assert diff == [flag], (name, diff)


# --- A2 — 임베딩 하이브리드는 사다리 **밖**에 있다 (G1 격리) ---------------------

def test_the_v1_ladder_never_turns_dense_on():
    """G1 이 불허로 확정되면 A2 만 빼고 곡선이 성립해야 한다 (§I-2).

    사다리 arm 하나라도 `dense=True` 면 A4·A5·A6 이 전부 재측정 대상이 되고,
    v1 이 이미 쓴 3,708원어치 결과가 통째로 무효가 된다.
    """
    for name in (*ablation.LADDER, "L1", "L2"):
        assert ablation.ARMS[name].dense is False, name
    assert set(ablation.DENSE_ARMS) == {a for a in ablation.ARMS
                                        if ablation.ARMS[a].dense}


def test_a2_arms_differ_from_their_baseline_by_exactly_the_dense_flag():
    """단일 변수 조작. 하나라도 더 바뀌면 Δ 가 임베딩의 것이 아니다."""
    for name, baseline in (("A2", ablation.A1B), ("A2F", ablation.A6)):
        diff = [f for f in FLAGS
                if getattr(ablation.ARMS[name], f) != getattr(baseline, f)]
        assert diff == ["dense"], (name, diff)


def test_default_arm_is_the_full_configuration():
    """스위치를 안 건드린 운영 경로가 곧 A6 여야 '기본값 = 제출 구성' 이 성립한다."""
    assert ablation.current() == ablation.A6


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


# --- A0 — 메타 프리필터만 -----------------------------------------------------

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


# --- A1 — exchange 인덱스 1,469건만 -------------------------------------------

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


# --- A4 — Agent 루프 (사다리 · 체인 · 분해) ------------------------------------

def test_chain_hop_is_off_below_a4():
    for arm in ("A1", "A1b"):
        _, trace = gather(arm, CHAIN_Q)
        assert "strat=chain" not in trace, arm


def test_chain_hop_is_on_from_a4():
    for arm in ("A4", "A5", "A6"):
        _, trace = gather(arm, CHAIN_Q)
        assert "strat=chain" in trace, arm


def test_l1_removes_only_the_chain_hop():
    """L1 = A6 − 정정체인 hop. 사다리와 분해는 그대로 살아 있어야 한다."""
    _, trace = gather("L1", CHAIN_Q)
    assert "strat=chain" not in trace
    assert "tool=search_chunks" in trace


def test_hop_ladder_is_off_below_a4():
    _, trace = gather("A1b", CHAIN_Q)
    hops = {h["hop"] for h in pipeline.parse_hops(trace)}
    assert hops <= {0}, hops


def test_split_query_is_off_below_a4(monkeypatch):
    """다지점 분해가 꺼지면 hop 0 이 셀로 안 쪼개진다 — 검색 호출 수로 드러난다."""
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


# --- A5 — 계산 결과를 근거에 싣는가 --------------------------------------------

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


# --- L2 — major·holding 추출 필드 ----------------------------------------------

def test_l2_removes_the_holding_schema_fields():
    full, _ = gather("A6", HOLDING_Q)
    loo, _ = gather("L2", HOLDING_Q)
    assert "보유비율:" in full
    assert "보유비율:" not in loo
    assert len(loo) < len(full)


def test_l2_keeps_the_evidence_blocks_themselves():
    """필드만 뺀다. 블록이 통째로 사라지면 그건 검색 ablation 이다."""
    full, _ = gather("A6", HOLDING_Q)
    loo, _ = gather("L2", HOLDING_Q)
    assert full.count("\n## ") == loo.count("\n## ")


def test_l2_does_not_change_retrieval():
    """성형은 생성 직전에만 건다 — 트레이스가 A6 과 글자 하나까지 같아야 한다."""
    _, full = gather("A6", HOLDING_Q)
    _, loo = gather("L2", HOLDING_Q)
    assert full == loo


# --- A6 — Verifier -------------------------------------------------------------

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


# --- A2 — 끈 것이 실제로 꺼졌는가 ------------------------------------------------
# 이 파일 머리말의 계약을 dense 에도 그대로 건다. dense 는 **켜졌는지도** 확인해야 한다 —
# 임베딩 저장소가 비어 있으면 조용히 아무 일도 안 일어나고, 그 침묵이 "Δ 0" 으로 읽힌다.

DENSE_ARM_QUESTIONS = (CHAIN_Q, PERIODIC_Q, HOLDING_Q, SPLIT_Q)


def test_dense_leaves_no_trace_when_the_arm_is_off():
    """🔴 A2 를 껐을 때 `think_trace` 에 dense 흔적이 하나도 없어야 한다."""
    for arm in ("A0", "A1", "A1b", "A4", "A5", "A6", "L1", "L2"):
        for question in DENSE_ARM_QUESTIONS:
            _, trace = gather(arm, question)
            assert "fusion=" not in trace, (arm, question)
            assert "dense_n=" not in trace, (arm, question)


def test_dense_never_touches_the_embedding_api_when_the_arm_is_off(monkeypatch):
    """`conftest` 가 CLOVA 를 막고 있으므로 호출이 있었다면 **여기서 죽는다**.
    그래도 명시적으로 센다 — 크레딧은 조용히 새는 것이 가장 무섭다."""
    from argus import embed

    calls = []
    monkeypatch.setattr(embed, "query_vector",
                        lambda text: calls.append(text) or None)
    for arm in ("A1b", "A6"):
        for question in DENSE_ARM_QUESTIONS:
            gather(arm, question)
    assert calls == []


class _StubDense:
    """dense 순위를 결정적으로 흉내낸다. **API 0회** — 배선만 검증한다."""

    added = 0
    lazy = False

    def __init__(self):
        self.seen = []

    def rank(self, query, candidates, top_k):
        self.seen.append((query, tuple(candidates)))
        return list(reversed(candidates))[:top_k]       # BM25 와 최대한 다른 순서


def test_dense_arm_actually_fuses(monkeypatch):
    """켰는데 안 켜진 것도 같은 크기의 위험이다 — 침묵이 'Δ 0' 으로 읽힌다."""
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
    """§I-1 — 메타 프리필터가 A0 이자 최대 기여 요소다. dense 가 그 앞에 서면 안 된다."""
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
    """🔴 게이트 회귀. 얼려 둔 42문항에서 A6 은 v1 과 같은 수를 내야 한다 —
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
    """문서군별 인덱스 구조를 dense 도 지킨다 — 사이드카가 남의 문서를 담으면
    라우터가 고른 인덱스 밖 문서가 근거로 새어 든다."""
    from argus.agent import tools

    for group in GROUPS:
        index = tools._group_index(group)
        store = index.dense.store if index and index.dense else None
        if not store or not len(store):
            continue
        assert set(store.ids) <= set(index.doc_ids), group


def test_dense_store_covers_the_record_groups():
    """산출물 계약 — 정형 3종은 **전량** 담긴다 (3,150건, float16 6.2MB).

    빌드가 끝나기 전에는 건너뛴다 — 부분 상태를 실패로 세면 빌드 중 테스트가 못 돈다.
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
    assert len(calls) == 1                             # 생성 1회뿐 — critic 도 안 탄다


# --- 발주처 2-hop 은 **독립 플래그**다 -----------------------------------------
# `hop_ladder` 에 묶어 두면 A0·A1b 에서 함께 꺼져 이 hop 만의 Δ 를 분리해서 잴 수 없다.
# `chain_hop` 을 따로 둔 것과 같은 이유다. 여기서 재는 것은 **끈 것이 실제로 꺼지는가** 다.

COUNTERPARTY_Q = ("한국수력원자력을 계약상대로 하는 단일판매·공급계약을 "
                  "공시한 상장사는 어디인가?")


def test_counterparty_hop_is_off_below_a4():
    for arm in ("A1", "A1b"):
        _, trace = gather(arm, COUNTERPARTY_Q)
        assert "strat=counterparty" not in trace, arm


def test_counterparty_hop_is_on_from_a4():
    for arm in ("A4", "A5", "A6"):
        _, trace = gather(arm, COUNTERPARTY_Q)
        assert "strat=counterparty" in trace, arm


def test_counterparty_hop_off_removes_the_evidence_too(monkeypatch):
    """표식만 사라지고 근거는 남으면 '끈 줄 알았는데 안 꺼진 것' 이다."""
    seen = []
    original = pipeline.tools.execute
    monkeypatch.setattr(
        pipeline.tools, "execute",
        lambda name, args: seen.append(name) or original(name, args))

    off = replace(ablation.A6, name="A6-cp-off", counterparty_hop=False)
    with ablation.use(off):
        evidence, _ = pipeline._gather_evidence(route(COUNTERPARTY_Q),
                                                COUNTERPARTY_Q, Budget())
    assert "query_by_counterparty" not in seen
    assert "발주처 관계:" not in evidence


def test_counterparty_hop_can_be_removed_alone():
    """L3(= A6 − 발주처 hop) 이 성립하는가 — 한 플래그만 달라야 한다."""
    loo = replace(ablation.A6, name="L3", counterparty_hop=False)
    diff = [f for f in FLAGS if getattr(loo, f) != getattr(ablation.A6, f)]
    assert diff == ["counterparty_hop"]
