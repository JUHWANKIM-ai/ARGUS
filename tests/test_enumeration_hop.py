"""열거 hop (`EVENTS_HOP` `strat=enumerate`) — v3 R2 ③.

`Q-117` 5건의 처방이다. 원안(`total_plan §5 #4` "노드 이름 인식")은 `router.py`
262–296 이 실측 3근거로 **기각**하고 처방을 여기로 재정의했다 — 한전기술은 그 질문에서
**공시한 쪽**이라 발주처로 결속하면 근거가 뒤집힌다.

🔴 그래서 이 파일은 **불변을 함께 지킨다**: 열거 hop 이 생겨도 라우터는 여전히 공시한
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
    """🔴 `Q-114` 경계 — 그 기업이 **발주한 쪽**이면 열거는 정반대 문서를 준다.

    `query_events(corp_name='삼성전자')` 는 삼성전자 **자신의** 공시를 주는데, 질문이
    묻는 것은 삼성전자를 **계약상대로 한** 상장사들의 공시다. 방향이 뒤집힌다.
    """
    q = "삼성전자를 계약상대로 하는 공급계약을 공시한 상장사의 공정 분담은 어떻게 나뉘는가"
    assert pipeline._wants_enumeration(_Route(["삼성전자"], "삼성전자"), q, "exchange") is False
    # 발주처가 다른 이름이면 그 기업의 열거는 여전히 성립한다.
    assert pipeline._wants_enumeration(_Route(["한전기술"], "한국수력원자력"), q,
                                       "exchange") is True


def test_parallel_lookup_words_do_not_open_the_enumeration_hop():
    """`각각`·`모두` 를 표지에서 뺀 이유 — 100문항에서 9건이 걸리고 8건이 열거가 아니다."""
    r = _Route(["LG이노텍"])
    assert pipeline._wants_enumeration(
        r, "LG이노텍이 2024년과 2025년에 각각 공시한 계약금액은?", "exchange") is False
    assert pipeline._wants_enumeration(
        r, "LG이노텍이 공시한 계약을 모두 알려줘", "exchange") is False


def test_form_words_pick_the_document_subtype_and_termination_wins():
    """`해지` 를 먼저 본다 — 뒤에 두면 '공급계약 해지' 가 '체결' 로 샌다.

    `doc_group` 이 '보고서' 를 뒤에 두는 것과 같은 순서 규율이다.
    """
    assert pipeline._event_subtype("공급계약 해지 공시의 갈래") == "단일판매공급계약해지"
    assert pipeline._event_subtype("단일판매·공급계약의 갈래") == "단일판매공급계약체결"
    assert pipeline._event_subtype("신규시설투자 유형") == "신규시설투자등"
    assert pipeline._event_subtype("계약의 유형은?") is None


# --- `query_events` 의 limit 축 -------------------------------------------------

def test_query_events_limit_defaults_to_the_old_page_size():
    """기본값이면 종전과 글자 하나 안 바뀐다 — 조용한 Δ 방지."""
    out = tools.query_events()
    if not out["count"]:
        pytest.skip("artifacts/exchange_records.jsonl 없음")
    assert len(out["events"]) == min(out["count"], tools.EVENTS_PAGE)
    # 🔴 `assert tools.EVENTS_PAGE == 20` 을 뺐다 (R2F). 위 줄이 이미 파생으로 같은 성질을
    #    재고, 리터럴은 페이지 크기를 바꾸려는 다음 사람을 **아무 신호 없이** 막는다 —
    #    이 저장소가 `== 13` 세 곳에서 겪은 사고와 같은 모양이다.
    wide = tools.query_events(limit=tools.EVENTS_PAGE * 10)
    assert wide["count"] == out["count"]          # count 는 필터 통과 전량이다
    assert len(wide["events"]) >= len(out["events"])
    assert len(tools.query_events(limit=0)["events"]) == 0


# --- 겨냥 실측 ------------------------------------------------------------------

@pytest.mark.skipif(not _VC.exists(), reason="evalset/valuechain.jsonl 없음")
def test_q117_recovers_two_of_the_five_targeted_golden():
    """🔴 겨냥 5건 중 **실측 회수는 2건**이다. 그 사실을 테스트로 고정한다.

    남은 3건은 같은 발주처 그룹의 **두 번째 건**이라(한수원 2건 · 두산 2건 중 각각
    1건씩) '그룹당 1건' 규칙과 정면으로 충돌한다 — 사유는 `_enumerate_events`
    독스트링에 정책 6종 비교와 함께 적어 두었다.
    """
    from argus.agent.budget import Budget

    row = _rows()["Q-117"]
    r = route(row["question"])
    assert r.counterparty is None          # 🔴 불변 — 공시한 쪽은 결속되지 않는다
    evidence, trace = pipeline._gather_evidence(r, row["question"], Budget())
    if not evidence:
        pytest.skip("artifacts/exchange_records.jsonl 없음")
    enum = [line for line in trace if "strat=enumerate" in line]
    assert enum
    # 🔴 `tools.execute` 는 `fn(**args)` 이고 `TypeError` 를 `{"error": …}` 로 **삼킨다.**
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
