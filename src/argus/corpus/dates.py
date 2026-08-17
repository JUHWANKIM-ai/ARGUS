"""Date and period arithmetic, computed deterministically."""
from __future__ import annotations

import re
from datetime import date

_PATTERNS = (
    re.compile(r"(\d{4})[-./](\d{1,2})[-./](\d{1,2})"),
    re.compile(r"(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일"),
    re.compile(r"\b(\d{4})(\d{2})(\d{2})\b"),
)


def parse_date(text: str) -> date | None:
    if not text:
        return None
    for pattern in _PATTERNS:
        match = pattern.search(text)
        if match:
            year, month, day = (int(g) for g in match.groups())
            try:
                return date(year, month, day)
            except ValueError:
                return None
    return None


def duration_ym(start: date, end: date) -> tuple[int, int]:
    """Whole years and months between two dates. Residual days are dropped."""
    months = (end.year - start.year) * 12 + (end.month - start.month)
    if end.day < start.day:
        months -= 1
    months = max(months, 0)
    return months // 12, months % 12


def format_duration(start: date, end: date) -> str:
    years, months = duration_ym(start, end)
    return f"{years}년 {months}개월"
