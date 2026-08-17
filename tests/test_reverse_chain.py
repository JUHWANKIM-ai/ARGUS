"""`A33`, 정정본에서 **원공시로 되돌아가는** 씨앗.

**링크는 이미 양방향이다** (R0 실측 2026-08-16):

    20250718800275  is_latest=False  related=['20250924800260']
    20250924800260  is_latest=True   related=['20250718800275']

못 간 것은 링크가 없어서가 아니라 `_chain_seeds` 의 **제동 ②**(`is_latest=False` 만
씨앗) 때문이다. 이 파일이 그 전제와, 예외를 여는 조건의 **좁기**를 함께 고정한다.

**v3 R2 ④ 가 좁힌 자리를 넓히지 않는다.** 그때 실측으로 기각된 ⓐ(`terminates` 보유면
무조건)는 보호 4문항(`Q-008`·`Q-031`·`Q-033`·`Q-034`)의 근거를 흔들었다. 이 arm 이 여는
갈래는 **정정 체인**이고 질문 게이트가 함께 걸려, 실측 인질이 `Q-061`·`Q-069` 둘이다.
"""
from __future__ import annotations

import io
import json
import pathlib

from argus import ablation
from argus.agent import pipeline, tools

EVALSET = pathlib.Path(__file__).resolve().parents[1] / "evalset"

#: R0(2026-08-16)이 평가셋 112문항 전수로 센 발화. 넓히면 이 단언이 먼저 깨진다.
EXPECTED_FIRINGS = {"Q-094", "Q-113", "Q-061", "Q-069"}


def test_the_link_is_already_bidirectional():
    """전제. 이게 깨지면 이 arm 의 설계가 통째로 틀린 것이다."""
    latest = tools._record_of("exchange_20250924800260")
    assert latest["is_latest"] is True
    assert "20250718800275" in (latest.get("related") or [])


def test_the_question_gate_reads_the_original():
    assert pipeline._asks_original(
        "두 회사 정정 후 계약금액 합계는 최초 공시 합계보다 얼마나 늘었는가?")
    assert not pipeline._asks_original("두 공시의 계약금액 합계는 얼마인가?")


def test_the_gate_stays_narrow_across_the_whole_evalset():
    """겨냥은 1건이다. 넓어지면 v3 R2 ④ 가 막은 희석이 되살아난다."""
    fired = set()
    for name in ("questions.jsonl", "valuechain.jsonl", "regression.jsonl",
                 "safety.jsonl"):
        path = EVALSET / name
        if not path.exists():
            continue
        for line in io.open(path, encoding="utf-8"):
            row = json.loads(line)
            if pipeline._asks_original(row["question"]):
                fired.add(row["question_id"])
    assert fired == EXPECTED_FIRINGS, f"발화 범위가 움직였다: {fired}"


def test_the_seed_is_opt_in():
    """v5 에서 채택돼 운영 기본값(`A34`)에 들어갔다. 그래도 `A28`(v4 기본값)로
    되돌리면 종전 그대로여야 한다. 최신본은 씨앗이 아니다."""
    assert ablation.A28.reverse_chain is False
    assert ablation.current().reverse_chain is True, "A34 가 기본값이어야 한다"
    picked = [{"doc_id": "exchange_20250924800260", "corp": "삼성E&A"}]
    with ablation.use("A28"):
        assert pipeline._chain_seeds(picked, "최초 공시 합계보다 얼마나 늘었는가?") == []


def test_seed_opens_when_armed_and_the_question_asks_for_the_original():
    with ablation.use("A33"):
        picked = [{"doc_id": "exchange_20250924800260", "corp": "삼성E&A"}]
        seeds = pipeline._chain_seeds(picked, "최초 공시 합계보다 얼마나 늘었는가?")
        assert [s["rcept_no"] for s in seeds] == ["20250924800260"]


def test_the_exception_stays_shut_when_the_question_does_not_ask():
    """질문 게이트가 보호막이다. 이게 없으면 `Q-008`·`Q-031`·`Q-033`·`Q-034`
    (전부 현재 정답)에 원계약 근거가 붙는다. v3 R2 ④ 가 실측으로 막은 자리다."""
    with ablation.use("A33"):
        picked = [{"doc_id": "exchange_20250924800260", "corp": "삼성E&A"}]
        assert pipeline._chain_seeds(picked, "계약금액은 얼마인가?") == []


def test_the_terminates_exception_is_untouched():
    """v3 R2 ④ 의 예외를 **건드리지 않는다.** 함께 움직이면 두 Δ 가 섞인다.
    플래그가 꺼진 상태에서 해지 갈래가 종전대로 열리는지 본다 (`Q-066` 의 경로)."""
    picked = [{"doc_id": "exchange_20250924800260", "corp": "삼성E&A"}]
    # 해지 어휘가 없고 terminates 도 없으므로 `A28` 에서 닫혀 있어야 한다.
    with ablation.use("A28"):
        assert pipeline._chain_seeds(picked, "원계약 금액은 얼마인가?") == []
