import pytest

from argus.agent import critic

# 실측 레코드(exchange_20250728800035)의 원값. HCX-005 가 기간 산정을 틀린 문항이다.
SAMSUNG = {
    "rcept_no": "20250728800035",
    "corp_name": "삼성전자",
    "amount": 22_764_764_160_000,
    "ratio": 7.6,
    "ratio_base": "매출액",
    "period_start": "2025-07-24",
    "period_end": "2033-12-31",
    "raw_rows": [
        ["2. 계약내역", "계약금액(원)", "22,764,764,160,000"],
        ["최근매출액(원)", "300,870,903,000,000"],
        ["매출액대비(%)", "7.6"],
    ],
}


def _fake_chat(result_json):
    class R:
        text = result_json
        prompt_tokens = 100
        completion_tokens = 30
    return lambda *a, **k: R()


def test_quick_check_is_free_and_detects_missing_citation():
    assert critic.quick_check("매출액은 333조원입니다")["has_citation"] is False


def test_quick_check_accepts_valid_citation():
    text = "매출액은 333조원입니다 (근거: 사업보고서 2025.12, 접수번호 20260310002820, III-1)"
    assert critic.quick_check(text)["has_citation"] is True


def test_judge_parses_json(monkeypatch):
    monkeypatch.setattr(critic, "chat", _fake_chat(
        '{"supported": true, "has_citation": true, "hallucination": false, "reason": "일치"}'))
    v = critic.judge("근거", "답변")
    assert v.supported and v.passed


def test_judge_fails_on_hallucination(monkeypatch):
    monkeypatch.setattr(critic, "chat", _fake_chat(
        '{"supported": false, "has_citation": true, "hallucination": true, "reason": "근거 없음"}'))
    assert critic.judge("근거", "답변").passed is False


def test_judge_survives_malformed_json(monkeypatch):
    monkeypatch.setattr(critic, "chat", _fake_chat("판정 불가"))
    v = critic.judge("근거", "답변")
    assert v.passed is False and "파싱" in v.reason


# --- G. verify_calculation — LLM 0회 ---

def test_verify_calculation_never_calls_llm(monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("verify_calculation 이 LLM 을 호출했습니다")
    monkeypatch.setattr(critic, "chat", _boom)
    out = critic.verify_calculation("계약기간은 9년 5개월입니다", SAMSUNG)
    assert out["llm_calls"] == 0


def test_verify_calculation_catches_hcx_period_error():
    """실측: 2025-07-24 ~ 2033-12-31 을 Direct·CoT 모두 9년 5개월로 답했다."""
    out = critic.verify_calculation("해당 계약의 기간은 9년 5개월입니다.", SAMSUNG)
    assert out["ok"] is False
    assert out["failure_stage"] == "TOOL"
    bad = [m for m in out["mismatches"] if m["kind"] == "period"]
    assert bad and bad[0]["expected"] == "8년 5개월" and bad[0]["claimed"] == "9년 5개월"


def test_verify_calculation_passes_correct_period():
    out = critic.verify_calculation("계약기간은 2025-07-24 부터 2033-12-31 까지 8년 5개월입니다.",
                                    SAMSUNG)
    assert out["ok"] is True
    assert out["failure_stage"] is None
    assert "period" in out["checked"]


def test_verify_calculation_skips_period_when_answer_states_none():
    out = critic.verify_calculation("계약상대는 글로벌 대형기업입니다.", SAMSUNG)
    assert out["ok"] is True
    assert "period" in out["skipped"]
    assert "period" not in out["checked"]


def test_verify_calculation_recomputes_share_from_raw_rows():
    """매출액 대비 비율을 계약금액/최근매출액으로 다시 계산한다."""
    out = critic.verify_calculation("매출액 대비 8.6% 규모입니다.", SAMSUNG)
    assert out["ok"] is False
    bad = [m for m in out["mismatches"] if m["kind"] == "share"]
    assert bad and round(bad[0]["expected"], 1) == 7.6


def test_verify_calculation_accepts_rounded_share():
    out = critic.verify_calculation("매출액 대비 7.6% 규모입니다.", SAMSUNG)
    assert out["ok"] is True
    assert "share" in out["checked"]


def test_verify_calculation_accepts_won_and_eok_forms():
    answer = "계약금액은 22,764,764,160,000원 (227,648억원) 입니다."
    out = critic.verify_calculation(answer, SAMSUNG)
    assert out["ok"] is True
    assert "amount" in out["checked"]


def test_verify_calculation_catches_amount_scale_error():
    out = critic.verify_calculation("계약금액은 2,276,476,416,000원입니다.", SAMSUNG)
    assert out["ok"] is False
    assert any(m["kind"] == "amount" for m in out["mismatches"])


def test_verify_calculation_catches_pct_change_error():
    ev = {"before": 6.5e12, "after": 8.1e12}
    assert critic.verify_calculation("매출이 16.0% 증가했습니다.", ev)["ok"] is False
    assert critic.verify_calculation("매출이 24.6% 증가했습니다.", ev)["ok"] is True


def test_verify_calculation_checks_cagr():
    ev = {"begin": 100, "end": 121, "years": 2}
    assert critic.verify_calculation("연평균 10.5% 성장했습니다.", ev)["ok"] is False
    out = critic.verify_calculation("연평균 10.0% 성장했습니다.", ev)
    assert out["ok"] is True and "cagr" in out["checked"]


def test_verify_calculation_checks_total_only_when_answer_sums():
    ev = {"values": [10.2, 0.6, 0.5]}
    assert critic.verify_calculation("합계는 11.3조원입니다.", ev)["ok"] is True
    assert critic.verify_calculation("합계는 12.3조원입니다.", ev)["ok"] is False
    # 합계를 말하지 않은 답변에는 합계 검사를 걸지 않는다
    out = critic.verify_calculation("가장 큰 항목은 10.2조원입니다.", ev)
    assert out["ok"] is True and "total" in out["skipped"]


def test_verify_calculation_checks_mean_when_answer_averages():
    ev = {"values": [10.2, 0.6, 0.5]}
    assert critic.verify_calculation("평균은 3.8조원입니다.", ev)["ok"] is True
    assert critic.verify_calculation("평균은 5.0조원입니다.", ev)["ok"] is False


def test_verify_calculation_accepts_tool_output_shape():
    """query_events 의 반환 형태를 그대로 넘겨도 동작한다."""
    out = critic.verify_calculation("계약기간은 9년 5개월입니다.",
                                    {"count": 1, "events": [SAMSUNG]})
    assert out["ok"] is False and out["failure_stage"] == "TOOL"


def test_verify_calculation_is_noop_without_evidence():
    out = critic.verify_calculation("계약기간은 9년 5개월입니다.", {})
    assert out["ok"] is True and out["checked"] == [] and out["failure_stage"] is None


@pytest.mark.parametrize("answer", ["", None])
def test_verify_calculation_survives_empty_answer(answer):
    out = critic.verify_calculation(answer, SAMSUNG)
    assert out["ok"] is True and out["mismatches"] == []


def test_verify_calculation_reason_is_readable():
    """reason 은 think_trace 에 그대로 실린다 — 지수표기·유효숫자 17자리는 곤란하다."""
    out = critic.verify_calculation("매출액 대비 9.9% 규모입니다.", SAMSUNG)
    assert out["reason"] == "share: 답변 9.9% vs 재계산 7.6"
    detail = out["mismatches"][0]["detail"]
    assert "e+" not in detail and "22,764,764,160,000" in detail


def test_verify_calculation_reports_every_mismatch():
    answer = "계약기간 9년 5개월, 매출액 대비 8.6%, 계약금액 2,276,476,416,000원입니다."
    kinds = {m["kind"] for m in critic.verify_calculation(answer, SAMSUNG)["mismatches"]}
    assert kinds == {"period", "share", "amount"}
