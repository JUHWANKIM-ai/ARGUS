"""열거 hop (`EVENTS_HOP` `strat=enumerate`), v3 R2 ③.

`Q-117` 5건의 처방이다. 원안(`total_plan §5 #4` "노드 이름 인식")은 `router.py`
262–296 이 실측 3근거로 **기각**하고 처방을 여기로 재정의했다. 한전기술은 그 질문에서
**공시한 쪽**이라 발주처로 결속하면 근거가 뒤집힌다.

그래서 이 파일은 **불변을 함께 지킨다**: 열거 hop 이 생겨도 라우터는 여전히 공시한
쪽을 결속하지 않는다. `tests/test_router.py::test_disclosing_company_is_never_bound_as_counterparty`
가 원본 계약이고 여기서는 그 계약이 살아 있는 채로 회수가 되는지를 본다.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from argus.agent import pipeline, tools
from argus.agent.router import route

_VC = Path(__file__).resolve().parents[1] / "evalset/valuechain.jsonl"


def _rows():
    return {json.loads(line)["question_id"]: json.loads(line)
            for line in _VC.open(encoding="utf-8") if line.strip()}


class _Route:
    def __init__(self, companies, counterparty=None):
        self.companies = companies
        self.counterparty = counterparty


# --- 제동 ①~④ ------------------------------------------------------------------

def test_enumeration_needs_the_exchange_group():
    """`query_events` 모집단은 거래소공시다 (§7.7 ②). 다른 문서군에 붙이면 오답 근거다."""
    r = _Route(["한전기술"])
    q = "한전기술이 공시한 공급계약의 발주 구조는 두 갈래로 나뉜다"
    assert pipeline._wants_enumeration(r, q, "exchange") is True
    assert pipeline._wants_enumeration(r, q, "periodic") is False
    assert pipeline._wants_enumeration(r, q, "major") is False


def test_enumeration_needs_exactly_one_company():
    """열거의 모집단은 '한 기업의 한 서식' 이다. 둘 이상이면 무엇을 열거할지 정의되지 않는다."""
    q = "두 회사가 공시한 공급계약의 발주 구조는 두 갈래로 나뉜다"
    assert pipeline._wants_enumeration(_Route([]), q, "exchange") is False
    assert pipeline._wants_enumeration(_Route(["A", "B"]), q, "exchange") is False


def test_the_ordering_party_is_not_enumerated():
    """`Q-114` 경계, 그 기업이 **발주한 쪽**이면 열거는 정반대 문서를 준다.

    `query_events(corp_name='삼성전자')` 는 삼성전자 **자신의** 공시를 주는데, 질문이
    묻는 것은 삼성전자를 **계약상대로 한** 상장사들의 공시다. 방향이 뒤집힌다.
    """
    q = "삼성전자를 계약상대로 하는 공급계약을 공시한 상장사의 공정 분담은 어떻게 나뉘는가"
    assert pipeline._wants_enumeration(_Route(["삼성전자"], "삼성전자"), q, "exchange") is False
    # 발주처가 다른 이름이면 그 기업의 열거는 여전히 성립한다.
    assert pipeline._wants_enumeration(_Route(["한전기술"], "한국수력원자력"), q,
                                       "exchange") is True


def test_parallel_lookup_words_do_not_open_the_enumeration_hop():
    """`각각`·`모두` 를 표지에서 뺀 이유, 100문항에서 9건이 걸리고 8건이 열거가 아니다."""
    r = _Route(["LG이노텍"])
    assert pipeline._wants_enumeration(
        r, "LG이노텍이 2024년과 2025년에 각각 공시한 계약금액은?", "exchange") is False
    assert pipeline._wants_enumeration(
        r, "LG이노텍이 공시한 계약을 모두 알려줘", "exchange") is False


def test_form_words_pick_the_document_subtype_and_termination_wins():
    """`해지` 를 먼저 본다. 뒤에 두면 '공급계약 해지' 가 '체결' 로 샌다.

    `doc_group` 이 '보고서' 를 뒤에 두는 것과 같은 순서 규율이다.
    """
    assert pipeline._event_subtype("공급계약 해지 공시의 갈래") == "단일판매공급계약해지"
    assert pipeline._event_subtype("단일판매·공급계약의 갈래") == "단일판매공급계약체결"
    assert pipeline._event_subtype("신규시설투자 유형") == "신규시설투자등"
    assert pipeline._event_subtype("계약의 유형은?") is None


# --- `query_events` 의 limit 축 -------------------------------------------------

def test_query_events_limit_defaults_to_the_old_page_size():
    """기본값이면 종전과 글자 하나 안 바뀐다. 조용한 Δ 방지."""
    out = tools.query_events()
    if not out["count"]:
        pytest.skip("artifacts/exchange_records.jsonl 없음")
    assert len(out["events"]) == min(out["count"], tools.EVENTS_PAGE)
    # `assert tools.EVENTS_PAGE == 20` 을 뺐다 (R2F). 위 줄이 이미 파생으로 같은 성질을
    #    재고, 리터럴은 페이지 크기를 바꾸려는 다음 사람을 **아무 신호 없이** 막는다.
    #    이 저장소가 `== 13` 세 곳에서 겪은 사고와 같은 모양이다.
    wide = tools.query_events(limit=tools.EVENTS_PAGE * 10)
    assert wide["count"] == out["count"]          # count 는 필터 통과 전량이다
    assert len(wide["events"]) >= len(out["events"])
    assert len(tools.query_events(limit=0)["events"]) == 0


# --- 겨냥 실측 ------------------------------------------------------------------

@pytest.mark.skipif(not _VC.exists(), reason="evalset/valuechain.jsonl 없음")
def test_q117_recovers_two_of_the_five_targeted_golden():
    """겨냥 5건 중 **실측 회수는 2건**이다. 그 사실을 테스트로 고정한다.

    남은 3건은 같은 발주처 그룹의 **두 번째 건**이라(한수원 2건 · 두산 2건 중 각각
    1건씩) '그룹당 1건' 규칙과 정면으로 충돌한다. 사유는 `_enumerate_events`
    독스트링에 정책 6종 비교와 함께 적어 두었다.
    """
    from argus.agent.budget import Budget

    row = _rows()["Q-117"]
    r = route(row["question"])
    assert r.counterparty is None          # 불변, 공시한 쪽은 결속되지 않는다
    evidence, trace = pipeline._gather_evidence(r, row["question"], Budget())
    if not evidence:
        pytest.skip("artifacts/exchange_records.jsonl 없음")
    enum = [line for line in trace if "strat=enumerate" in line]
    assert enum
    # `tools.execute` 는 `fn(**args)` 이고 `TypeError` 를 `{"error": …}` 로 **삼킨다.**
    #    그러면 이 hop 은 `skip=tool_error` 를 남기고 0건을 돌려주는데, 그 줄에도
    #    `strat=enumerate` 가 들어 있어 발화 단언만으로는 인자명 오타가 통과한다.
    #    `_enumerate_events` 가 넘기는 키 넷(`corp_name`·`is_latest`·`limit`·`doc_subtype`)
    #    중 하나만 틀려도 Δ 가 **조용히 0** 이 되므로 여기서 함께 막는다.
    assert not any("skip=tool_error" in line for line in enum), enum
    for rcept_no in ("20250729800143", "20251212801320"):
        assert rcept_no in evidence, rcept_no


@pytest.mark.skipif(not _VC.exists(), reason="evalset/valuechain.jsonl 없음")
def test_the_enumeration_hop_does_not_fire_for_q114():
    """`Q-114` 는 발주처가 자기 기업이라 열거가 안 열린다 (제동 ③ 의 실문항 확인)."""
    from argus.agent.budget import Budget

    row = _rows()["Q-114"]
    r = route(row["question"])
    if r.counterparty is None:
        pytest.skip("counterparty_graph.json 없음")
    _, trace = pipeline._gather_evidence(r, row["question"], Budget())
    assert not any("strat=enumerate" in line for line in trace)


# --- v4 R1-11 ③ 기각, 하중 상수가 `depth` 가 아니다 ----------------------------

def test_the_evidence_cap_binds_before_the_group_depth():
    """`R1-11` ③ 의 기각을 고정한다. `depth < 1` 을 올려도 집합이 안 바뀐다.

    `R1-6` 보고서는 `Q-117` 미회수 3건의 원인을 `_enumerate_events` 의 `depth < 1`
    하드코딩(*"그룹당 1건"*)으로 짚었다. 실측하면 **하중 상수는 그것이 아니다**:
    계약상대 그룹이 11개인데 담을 칸(`MAX_ENUM_EVIDENCE`)이 5개뿐이라 `depth 0`
    한 바퀴에서 이미 상한이 찬다. `depth` 를 올려도 두 번째 바퀴가 **아예 안 돈다.**

    셋을 회수하려면 `MAX_EVIDENCE`(10)를 올려야 하고 그것은 `A16` 이라
    `R0-3`(`dac2bf0`)이 이미 기각했다. 사유는 `_enumerate_events` 독스트링에 있다.
    """
    from argus.corpus.counterparty import canonical

    out = tools.execute("query_events",
                        {"corp_name": "한전기술", "is_latest": True,
                         "doc_subtype": "단일판매공급계약체결",
                         "limit": pipeline.ENUM_EVENT_POOL})
    if "error" in out or not (out.get("events") or ()):
        pytest.skip("artifacts/exchange_records.jsonl 없음")

    groups: dict[str, list[dict]] = {}
    for event in out["events"]:
        groups.setdefault(canonical(event.get("counterparty") or ""), []).append(event)

    # 이 부등식이 기각의 전부다. 뒤집히면(그룹 < 상한) depth 가 하중이 되고
    #    그때는 `R1-11` ③ 을 다시 재야 한다.
    assert len(groups) > pipeline.MAX_ENUM_EVIDENCE, (
        f"그룹 {len(groups)} <= 상한 {pipeline.MAX_ENUM_EVIDENCE} — depth 가 하중이 됐다")

    def taken_at(max_depth: int) -> list[str]:
        order = sorted(groups, key=lambda key: (-len(groups[key]), key))
        taken: list[str] = []
        depth = 0
        while len(taken) < pipeline.MAX_ENUM_EVIDENCE and depth < max_depth:
            for key in order:
                if len(taken) >= pipeline.MAX_ENUM_EVIDENCE:
                    break
                if depth < len(groups[key]):
                    taken.append(str(groups[key][depth].get("rcept_no")))
            depth += 1
        return taken

    assert taken_at(1) == taken_at(2) == taken_at(3), "depth 를 올렸더니 집합이 바뀌었다"
    assert len(taken_at(1)) == pipeline.MAX_ENUM_EVIDENCE

    # 미회수 3건이 어디 있는지도 못 박는다. 하나는 **1건짜리 그룹**이라 depth 와
    # 무관하고(순위가 상한 밖), 둘은 같은 그룹의 2·4번째다.
    order = sorted(groups, key=lambda key: (-len(groups[key]), key))
    assert len(groups["한국남부발전"]) == 1
    assert order.index("한국남부발전") >= pipeline.MAX_ENUM_EVIDENCE
    doosan = [str(e.get("rcept_no")) for e in sorted(
        groups["두산에너빌리티"],
        key=lambda e: (str(e.get("rcept_dt", "")), e.get("rcept_no", "")), reverse=True)]
    assert doosan.index("20251224800396") == 1
    assert doosan.index("20230330800844") == 3


def test_the_enumeration_hop_fires_on_exactly_one_question_in_the_corpus():
    """위 기각이 `Q-117` 하나에만 걸린 판정이 아니라는 것을 못 박는다.

    열거 hop 이 발화하는 문항은 전체 88 + 밸류체인 12 = 100문항 중 **하나뿐**이다
    (저장 응답의 질문만 읽는다. LLM 0회 · `evalset/` 미접촉). 이 수가 늘면 그때는
    그룹 수가 상한보다 작은 문항이 생겼을 수 있으므로 `depth` 를 다시 재야 한다.
    """
    import json

    root = Path(__file__).resolve().parents[1] / "artifacts/ablation/v3"
    questions: dict[str, str] = {}
    for arm in ("A8", "vc/A8"):
        directory = root / arm / "responses"
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("Q-*.json")):
            payload = json.loads(path.read_text(encoding="utf-8"))["response"]
            questions.setdefault(payload["question_id"], payload["question"])
    if len(questions) < 100:
        pytest.skip("v3 저장 응답이 없다")

    # 문서군을 `"exchange"` 로 **박지 않는다**. 그러면 제동 ① 을 건너뛴다.
    #    호출부(`_gather_evidence`)가 넘기는 것은 라우터가 정한 `r.group` 이고,
    #    박아 두면 `Q-076`(정기공시)처럼 어휘만 걸리는 문항이 발화한 것처럼 보인다.
    fired = []
    for qid, question in sorted(questions.items()):
        r = route(question)
        if pipeline._wants_enumeration(r, question, r.group):
            fired.append(qid)
    assert fired == ["Q-117"], fired
