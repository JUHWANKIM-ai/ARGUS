"""A26 — 지시대상 불명을 '범위 밖' 과 가른다 (규약 감사 F-3).

🔴 이 arm 이 지켜야 할 가장 중요한 성질은 **`쿠팡` 을 뺏지 않는 것**이다. 지금 맞는
판정(범위 밖)을 역질문으로 가져가면 고치는 것보다 깨는 것이 많다.
"""
import pytest

from argus import ablation
from argus.agent import pipeline
from argus.agent.router import route


@pytest.fixture
def gate_on():
    with ablation.use(ablation.A26):
        yield


# --- 갈래를 끈 arm 은 종전 그대로 -----------------------------------------------

def test_flag_off_keeps_the_old_verdict():
    """🔴 꺼진 arm 의 판정은 v1~v4 산출물과 바이트 동일이어야 한다.

    운영 기본값은 2026-08-16 에 `A25+A26` 이 됐으므로 **명시적으로 끄고** 확인한다.
    """
    with ablation.use(ablation.A20R):
        assert route("그 회사의 작년 실적 좀 알려줘").reason == "out_of_universe"


# --- 켜면 갈린다 ---------------------------------------------------------------

@pytest.mark.parametrize("question", [
    "그 회사의 작년 실적 좀 알려줘",          # 감사 AUD-15 원문
    "해당 기업의 매출액은 얼마인가",
    "이 회사가 체결한 계약을 정리해줘",
    "위 기업의 시설투자 규모는",
])
def test_dangling_reference_asks_back(gate_on, question):
    assert route(question).reason == "ambiguous_reference"


@pytest.mark.parametrize("question", [
    "쿠팡의 2025년 연결기준 매출액은 얼마인가?",     # 감사 AUD-01 — 진짜 범위 밖
    "네이버웹툰의 계약 규모는?",
])
def test_named_but_unknown_company_is_still_out_of_universe(gate_on, question):
    """🔴 기업 토큰이 **있는데** universe 밖인 것은 범위 밖이 맞다. 뺏으면 안 된다."""
    assert route(question).reason == "out_of_universe"


def test_known_company_is_untouched(gate_on):
    r = route("삼성전자의 2025년 매출액은?")
    assert r.kind != "unanswerable" and r.companies == ["삼성전자"]


def test_dangling_word_inside_a_named_question_does_not_hijack(gate_on):
    """기업이 지목돼 있으면 지시어가 있어도 답할 수 있다 — 갈래를 타면 안 된다."""
    r = route("삼성전자와 SK하이닉스 중 그 회사가 더 큰 계약을 했나?")
    assert r.kind != "unanswerable"


# --- 파이프라인이 다른 문구를 낸다 ----------------------------------------------

def test_pipeline_asks_back_without_calling_the_model(gate_on):
    out = pipeline.answer_question("T-1", "그 회사의 작년 실적 좀 알려줘")
    assert out["answer"] == pipeline.AMBIGUOUS_REFERENCE
    assert "어느 기업을 말씀하시는지" in out["answer"]
    # 🔴 종전 문구를 쓰면 안 된다 — 그것이 이 arm 이 고치는 결함 자체다.
    assert "범위 안의 기업과 기간으로" not in out["answer"]
    assert "지시대상 불명" in out["think_trace"]
    assert "llm=0 tokens=0" in out["think_trace"]        # 크레딧 0원


def test_pipeline_keeps_out_of_range_wording_for_real_out_of_scope(gate_on):
    out = pipeline.answer_question("T-2", "쿠팡의 2025년 매출액은?")
    assert out["answer"] == pipeline.UNANSWERABLE


# --- arm 등록 -----------------------------------------------------------------

def test_a26_differs_from_baseline_by_one_flag():
    diff = [f for f in ablation.FLAG_NAMES
            if getattr(ablation.A26, f) != getattr(ablation.A20R, f)]
    assert diff == ["ambiguous_gate"]


def test_baseline_adopted_it():
    """🔴 재고 채택했다 — 그리드 66/72 동률 (`artifacts/ablation/v4/repro_0816/`)."""
    assert ablation.current().ambiguous_gate is True
