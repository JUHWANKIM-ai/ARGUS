import pytest

from argus.agent.compute import (cagr, compare, fmt_num, fmt_won, mean, pct_change, period_text,
                                 share, to_eok, total)


def test_pct_change_matches_gold():
    # 검증 문항: 6.5조 -> 8.1조 = +24.6%
    assert round(pct_change(6.5e12, 8.1e12), 1) == 24.6


def test_share_matches_gold():
    # DS부문 1,301,282 / 총 3,336,059 = 39.0%
    assert round(share(1_301_282, 3_336_059), 1) == 39.0


def test_total_sums():
    assert round(total([10.2, 0.6, 0.5]), 1) == 11.3


def test_compare_reports_larger_and_diff():
    r = compare(11.2, 8.5)
    assert r["larger"] == "a"
    assert round(r["diff"], 1) == 2.7


def test_to_eok_and_format():
    assert round(to_eok(22_764_764_160_000)) == 227_648
    assert fmt_won(22_764_764_160_000) == "22,764,764,160,000원 (227,648억원)"


def test_period_text_matches_gold():
    # LLM 이 Direct/CoT 모두 틀린 문항
    assert period_text("2025-07-24", "2033-12-31") == "8년 5개월"


def test_share_rejects_zero_whole():
    with pytest.raises(ValueError):
        share(1, 0)


# --- policy §4 명시 항목: CAGR · 평균 ---

def test_cagr_doubles_check():
    # 100 -> 121, 2년 = 연 10.0%
    assert round(cagr(100, 121, 2), 1) == 10.0


def test_cagr_matches_pct_change_over_one_year():
    """1년 CAGR 은 단순 증감률과 같아야 한다."""
    assert round(cagr(6.5e12, 8.1e12, 1), 4) == round(pct_change(6.5e12, 8.1e12), 4)


def test_cagr_three_years():
    assert round(cagr(6.5e12, 8.1e12, 3), 2) == 7.61


def test_cagr_handles_decline():
    assert round(cagr(200, 100, 1), 1) == -50.0


def test_cagr_rejects_nonpositive_begin():
    with pytest.raises(ValueError):
        cagr(0, 100, 3)


def test_cagr_rejects_nonpositive_years():
    with pytest.raises(ValueError):
        cagr(100, 121, 0)


def test_mean_averages():
    assert round(mean([10.2, 0.6, 0.5]), 1) == 3.8


def test_mean_rejects_empty():
    with pytest.raises(ValueError):
        mean([])


def test_fmt_num_never_uses_scientific_notation():
    """도구 설명(expr)이 그대로 답변에 새면 '2.27648e+13' 이 노출된다."""
    assert fmt_num(22_764_764_160_000) == "22,764,764,160,000"
    assert fmt_num(6.5e12) == "6,500,000,000,000"
    assert "e+" not in fmt_num(3.00870903e14)
    assert fmt_num(7.6) == "7.6"
