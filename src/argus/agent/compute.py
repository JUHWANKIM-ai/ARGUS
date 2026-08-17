"""Deterministic computation for numeric and period arithmetic."""
from __future__ import annotations

from argus.corpus.dates import duration_ym, parse_date


def pct_change(before: float, after: float) -> float:
    if before == 0:
        raise ValueError("기준값이 0이면 증감률을 정의할 수 없습니다")
    return (after - before) / before * 100.0


def cagr(begin: float, end: float, years: float) -> float:
    """Compound annual growth rate as a percentage.

    Returns None when the base value is non-positive or the end value is
    negative, since real exponentiation is undefined there.
    """
    if begin <= 0:
        raise ValueError("기초값이 0 이하이면 CAGR 을 정의할 수 없습니다")
    if end < 0:
        raise ValueError("결과값이 음수이면 CAGR 을 정의할 수 없습니다")
    if years <= 0:
        raise ValueError("기간(년)은 0보다 커야 합니다")
    return ((end / begin) ** (1.0 / years) - 1.0) * 100.0


def share(part: float, whole: float) -> float:
    if whole == 0:
        raise ValueError("전체값이 0이면 비중을 정의할 수 없습니다")
    return part / whole * 100.0


def total(values: list[float]) -> float:
    return float(sum(values))


def mean(values: list[float]) -> float:
    """Arithmetic mean."""
    if not values:
        raise ValueError("빈 목록의 평균은 정의할 수 없습니다")
    return float(sum(values)) / len(values)


def compare(a: float, b: float) -> dict:
    return {
        "larger": "a" if a > b else ("b" if b > a else "tie"),
        "diff": abs(a - b),
        "ratio": (a / b) if b else None,
    }


def fmt_num(value: float) -> str:
    """Format a number for display, avoiding scientific notation."""
    number = float(value)
    return f"{int(number):,}" if number.is_integer() and abs(number) < 1e18 else f"{number:,}"


def to_eok(won: float) -> float:
    """Convert won to 억원 (hundred millions)."""
    return won / 100_000_000


def fmt_won(won: int) -> str:
    return f"{won:,}원 ({to_eok(won):,.0f}억원)"


def period_parts(start: str, end: str) -> tuple[int, int] | None:
    """Whole years and months between two dates, or None if unparseable."""
    sd, ed = parse_date(start), parse_date(end)
    if not (sd and ed):
        return None
    return duration_ym(sd, ed)


def period_text(start: str, end: str) -> str:
    parts = period_parts(start, end)
    if parts is None:
        return ""
    return f"{parts[0]}년 {parts[1]}개월"
