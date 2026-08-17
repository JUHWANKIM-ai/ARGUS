from datetime import date

import pytest

from argus.corpus.dates import duration_ym, format_duration, parse_date


@pytest.mark.parametrize(
    "text,expected",
    [
        ("2025-07-24", date(2025, 7, 24)),
        ("2025.07.24", date(2025, 7, 24)),
        ("20250724", date(2025, 7, 24)),
        ("2025년 7월 24일", date(2025, 7, 24)),
        ("해당없음", None),
    ],
)
def test_parse_date(text, expected):
    assert parse_date(text) == expected


def test_duration_matches_gold_answer():
    # HCX 가 Direct/CoT 모두 틀린 문항, 정답은 8년 5개월
    assert duration_ym(date(2025, 7, 24), date(2033, 12, 31)) == (8, 5)


def test_format_duration():
    assert format_duration(date(2025, 7, 24), date(2033, 12, 31)) == "8년 5개월"


def test_duration_same_month_is_zero():
    assert duration_ym(date(2025, 7, 1), date(2025, 7, 20)) == (0, 0)
