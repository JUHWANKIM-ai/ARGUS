"""결정적 계산. LLM 이 신뢰할 수 없는 수치·기간 연산을 담당한다."""
from __future__ import annotations

from argus.corpus.dates import format_duration, parse_date


def pct_change(before: float, after: float) -> float:
    if before == 0:
        raise ValueError("기준값이 0이면 증감률을 정의할 수 없습니다")
    return (after - before) / before * 100.0


def share(part: float, whole: float) -> float:
    if whole == 0:
        raise ValueError("전체값이 0이면 비중을 정의할 수 없습니다")
    return part / whole * 100.0


def total(values: list[float]) -> float:
    return float(sum(values))


def compare(a: float, b: float) -> dict:
    return {
        "larger": "a" if a > b else ("b" if b > a else "tie"),
        "diff": abs(a - b),
        "ratio": (a / b) if b else None,
    }


def to_eok(won: float) -> float:
    """원 -> 억원."""
    return won / 100_000_000


def fmt_won(won: int) -> str:
    return f"{won:,}원 ({to_eok(won):,.0f}억원)"


def period_text(start: str, end: str) -> str:
    sd, ed = parse_date(start), parse_date(end)
    if not (sd and ed):
        return ""
    return format_duration(sd, ed)
