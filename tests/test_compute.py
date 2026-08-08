import pytest

from argus.agent.compute import (compare, fmt_won, pct_change, period_text, share, to_eok, total)


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
