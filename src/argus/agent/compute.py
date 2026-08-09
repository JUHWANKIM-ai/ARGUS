"""결정적 계산. LLM 이 신뢰할 수 없는 수치·기간 연산을 담당한다."""
from __future__ import annotations

from argus.corpus.dates import duration_ym, parse_date


def pct_change(before: float, after: float) -> float:
    if before == 0:
        raise ValueError("기준값이 0이면 증감률을 정의할 수 없습니다")
    return (after - before) / before * 100.0


def cagr(begin: float, end: float, years: float) -> float:
    """연평균 성장률(%). policy §4 명시 항목.

    기초값이 0 이하이거나 결과값이 음수면 실수 거듭제곱이 정의되지 않는다.
    LLM 이 흔히 (end/begin - 1)/years 로 잘못 계산하는 지점이라 도구로 고정한다.
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
    """산술평균. policy §4 명시 항목."""
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
    """설명용 숫자 표기. 지수표기(`2.27648e+13`)가 답변이나 trace 로 새면 곤란하다."""
    number = float(value)
    return f"{int(number):,}" if number.is_integer() and abs(number) < 1e18 else f"{number:,}"


def to_eok(won: float) -> float:
    """원 -> 억원."""
    return won / 100_000_000


def fmt_won(won: int) -> str:
    return f"{won:,}원 ({to_eok(won):,.0f}억원)"


def period_parts(start: str, end: str) -> tuple[int, int] | None:
    """시작~종료의 만(滿) 년·개월. 날짜를 못 읽으면 None."""
    sd, ed = parse_date(start), parse_date(end)
    if not (sd and ed):
        return None
    return duration_ym(sd, ed)


def period_text(start: str, end: str) -> str:
    parts = period_parts(start, end)
    if parts is None:
        return ""
    return f"{parts[0]}년 {parts[1]}개월"
