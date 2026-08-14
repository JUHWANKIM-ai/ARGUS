"""해지 -> 원계약 순방향 엣지 (`terminates`) — v3 R2 ④.

`corrections.attach_related` 가 `terminates` 를 인덱스에 실어 두고 *"아직 소비자가
없다"* 로 남겨 뒀다. 소비자를 여기서 붙인다. 회귀 `Q-066` 과 계획서의 `A9`(후속공시 씨앗
확장)는 **같은 코드의 같은 제동**(`_chain_seeds` 의 `is_latest=False`)을 겨냥하므로
한 태스크다.

🔴 **위험이 명시돼 있었다** — 제동 ② 를 열면 현재 정답 4문항(`Q-008`·`Q-031`·`Q-033`
·`Q-034`)에 근거가 붙는다. `corrections.py` 독스트링이 붙이기 전에 7문항 근거 diff 를
LLM 0회로 뜨라고 못박았고(`1d008f0`), 그 절차를 먼저 밟은 결과가 `pipeline._chain_seeds`
독스트링의 3안 비교표다. 이 파일은 그 판정을 스위트에 고정한다.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from argus.agent import pipeline, tools

_VC = Path(__file__).resolve().parents[1] / "evalset"


def _rows():
    out = {}
    for name in ("questions.jsonl", "regression.jsonl", "valuechain.jsonl"):
        path = _VC / name
        if not path.exists():
            continue
        for line in path.open(encoding="utf-8"):
            if line.strip():
                row = json.loads(line)
                out[row["question_id"]] = row
    return out


# --- 엣지 세 종류를 갈라서 돌려준다 ----------------------------------------------

def test_chain_probe_separates_the_three_edge_kinds():
    """`links` 는 합집합이고 `followups`·`terminates` 는 따로 실린다.

    씨앗 조건이 **어느 엣지 때문에** 열렸는지 호출부가 알아야 제동 ②' 를 걸 수 있다.
    """
    probe = tools.chain_probe("20241115800529")     # 대우건설 해지 (Q-066 의 출발점)
    if not probe:
        pytest.skip("artifacts/exchange_records.jsonl 없음")
    assert probe["is_latest"] is True               # 해지 공시는 전건 최신본이다
    assert "20230228801277" in probe["terminates"]  # 순방향 — 이 공시가 해지한 원계약
    assert "20230228801277" in probe["links"]       # 합집합에도 들어 있다
    assert probe["followups"] == ()                 # 역방향은 해지에서 출발하면 빈다


def test_the_reverse_index_is_empty_from_the_termination_side():
    """🔴 W4 가 놓쳤던 방향 문제 — `_followup_index` 는 **역방향 전용**이다.

    `chain_probe(원계약)` 에는 해지가 보이는데 `chain_probe(해지)` 에는 원계약이 안
    보인다. `Q-066` 은 해지에서 출발하므로 역방향만으로는 런타임 대체 경로가 **없다.**
    """
    origin = tools.chain_probe("20230228801277")
    if not origin:
        pytest.skip("artifacts/exchange_records.jsonl 없음")
    assert "20241115800529" in origin["followups"]  # 역방향은 원계약 쪽에서만 보인다
    assert origin["terminates"] == ()


def test_get_history_returns_the_terminated_original_under_its_own_key():
    """`followups` 와 **합치지 않는다** — 시간 방향이 반대다."""
    out = tools.get_history("20241115800529")
    if "error" in out:
        pytest.skip("artifacts/exchange_records.jsonl 없음")
    assert [e["rcept_no"] for e in out["terminated"]] == ["20230228801277"]
    assert all(e["kind"] == "terminated" for e in out["terminated"])
    assert out["followups"] == []


def test_the_termination_edge_is_gated_by_the_followup_flag():
    """🔴 `L4` 의 정의를 지킨다.

    `terminates` 도 출처가 `※ 관련공시` 셀이라 같은 `REFERS_TO` 엣지 가족이다. 따로
    열어 두면 `L4`(= `followup_hop=False`)가 "관련공시 기여를 다 뺀 구성" 이 아니게 되어
    이미 낸 **−1.4%p** 의 정의가 바뀐다.
    """
    out = tools.get_history("20241115800529", include_followups=False)
    if "error" in out:
        pytest.skip("artifacts/exchange_records.jsonl 없음")
    assert out["terminated"] == []


# --- 근거 라벨 — 방향을 틀리면 오답을 굳힌다 ---------------------------------------

def test_the_terminated_original_gets_its_own_note():
    """🔴 `followup` 라벨을 재사용하면 모델이 원계약 금액 자리에 해지 금액을 답한다.

    `Q-066` 의 v2 A8 오답이 정확히 그 값이다 — 원계약 351,882,914,000 대신 해지금액
    362,307,038,000. 그리고 `대체됨`·`최신본` 을 쓰면 `critic._STALE_NOTE` /
    `_LATEST_NOTE` 가 해지 관계를 정정 관계로 오독한다.
    """
    note = pipeline._chain_note({"kind": "terminated"}, 1, 1)
    assert "해지 대상 원계약" in note
    assert "대체됨" not in note and "최신본" not in note
    assert "후속" not in note

    from argus.agent import critic
    assert not critic._STALE_NOTE.search(note)
    assert not critic._LATEST_NOTE.search(note)


# --- 제동 ②' — 문서 조건 **과** 질문 조건이 함께일 때만 열린다 ---------------------

def _picked(doc_id):
    return [{"doc_id": doc_id, "markdown": "x", "score": 1.0, "corp": "대우건설"}]


def test_a_latest_filing_without_terminates_is_still_not_a_seed():
    """제동 ② 의 본체는 그대로다 — 최신본은 씨앗이 아니다."""
    probe = tools.chain_probe("20230228801277")
    if not probe:
        pytest.skip("artifacts/exchange_records.jsonl 없음")
    assert probe["is_latest"] is True and probe["followups"]
    assert pipeline._chain_seeds(_picked("exchange_20230228801277"),
                                 "원래 계약금액은 얼마인가") == []


def test_the_termination_exception_needs_the_question_to_ask_for_the_original():
    """🔴 문서 쪽만으로는 안 연다.

    `terminates` 를 무조건 씨앗으로 받으면 근거가 바뀌는 문항이 2 → 6 이 되고 그중
    넷이 현재 정답이다. `Q-031`·`Q-034` 는 **해지 금액**을 묻는데 원계약 금액 블록이
    +1,100자씩 붙는다 — `corrections.attach_related` 가 경고한 사고의 거울상이고,
    `ORACLE` 이 실측한 근거 희석과 같은 방향이다. 골든은 셋 다 같으므로(161/179)
    **더 바꾸는 쪽을 고를 이유가 없다.**
    """
    picked = _picked("exchange_20241115800529")
    if not tools.chain_probe("20241115800529"):
        pytest.skip("artifacts/exchange_records.jsonl 없음")
    assert pipeline._chain_seeds(picked, "해지 금액이 얼마인가") == []
    assert pipeline._chain_seeds(picked, "") == []
    seeds = pipeline._chain_seeds(picked, "해지가 가리키는 원래 공급계약의 계약금액은?")
    assert [s["rcept_no"] for s in seeds] == ["20241115800529"]


# --- 겨냥 실측 ------------------------------------------------------------------

@pytest.mark.skipif(not (_VC / "regression.jsonl").exists(), reason="evalset 없음")
def test_q066_reaches_the_original_contract():
    """겨냥 — 회귀 `Q-066` 이 원계약을 근거에 싣는다 (1/2 → 2/2)."""
    from argus.agent.budget import Budget
    from argus.agent.router import route

    row = _rows()["Q-066"]
    r = route(row["question"])
    evidence, _ = pipeline._gather_evidence(r, row["question"], Budget())
    if not evidence:
        pytest.skip("artifacts/exchange_records.jsonl 없음")
    assert "20241115800529" in evidence and "20230228801277" in evidence
    assert "해지 대상 원계약" in evidence


@pytest.mark.skipif(not (_VC / "questions.jsonl").exists(), reason="evalset 없음")
def test_the_protected_questions_do_not_get_the_original_contract():
    """🔴 회귀 감시 — 해지 금액을 묻는 문항에는 원계약이 안 붙는다.

    `Q-008`·`Q-031`·`Q-034` 는 근거가 **바이트 단위로 종전과 같다**(측정: 710·1,258·
    1,060자 불변). 여기서는 그 사실의 관측 가능한 대리로 '해지 대상 원계약 블록이
    실리지 않았는가' 를 본다.
    """
    from argus.agent.budget import Budget
    from argus.agent.router import route

    rows = _rows()
    for qid in ("Q-008", "Q-031", "Q-034"):
        row = rows[qid]
        r = route(row["question"])
        evidence, _ = pipeline._gather_evidence(r, row["question"], Budget())
        if not evidence:
            pytest.skip("artifacts/exchange_records.jsonl 없음")
        assert "해지 대상 원계약" not in evidence, qid
        golden = [str(e["rcept_no"]) for e in row["evidence"]]
        assert all(g in evidence for g in golden), qid
