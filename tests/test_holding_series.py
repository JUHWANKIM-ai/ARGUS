"""The holding-ratio series line.

Q-106 asks how a reported holding ratio moved since listing. All six golden
points sit in typed fields of the holding records, but the model read only as
far as the first evidence block and dropped the 68.11% -> 50.06% collapse the
question is about. The line is the same move `revision_series` makes for a
revision chain's amounts.
See docs/design-rationale.md#holding-series
"""
from argus import ablation
from argus.agent import aggregate

#: Q-106's four real filings, reporter `(주)두산` on `두산로보틱스`.
DOOSAN = [
    {"rcept_no": "20231013000473", "rcept_dt": "20231013", "reporter": "(주)두산",
     "ratio_prev": None, "ratio_current": 68.19,
     "shares_prev": None, "shares_current": "44,200,027"},
    {"rcept_no": "20250305000647", "rcept_dt": "20250305", "reporter": "(주)두산",
     "ratio_prev": 68.19, "ratio_current": 68.15,
     "shares_prev": "44,200,027", "shares_current": "44,177,137"},
    {"rcept_no": "20250707000299", "rcept_dt": "20250707", "reporter": "(주)두산",
     "ratio_prev": 68.15, "ratio_current": 68.11,
     "shares_prev": "44,177,137", "shares_current": "44,149,065"},
    {"rcept_no": "20260227007254", "rcept_dt": "20260227", "reporter": "(주)두산",
     "ratio_prev": 68.11, "ratio_current": 50.06,
     "shares_prev": "44,149,065", "shares_current": "32,449,065"},
]

QUESTION = ("(주)두산이 두산로보틱스에 대해 보고한 보유비율이 상장 이후 어떻게 "
            "변해 왔는지 각 보고서를 근거로 설명해줘.")


def test_the_line_carries_every_ratio_in_filing_order():
    line = aggregate.holding_series(DOOSAN, QUESTION)
    assert line
    for ratio in ("68.19%", "68.15%", "68.11%", "50.06%"):
        assert ratio in line
    assert line.index("68.19%") < line.index("50.06%")


def test_the_line_carries_the_first_and_last_share_counts():
    """Golden points 1 and 5 are share counts, not ratios."""
    line = aggregate.holding_series(DOOSAN, QUESTION)
    assert "44,200,027" in line and "32,449,065" in line


def test_the_line_states_the_cumulative_change():
    """Golden point 6: 68.19 - 50.06 = 18.13 percentage points."""
    assert "18.13%p" in aggregate.holding_series(DOOSAN, QUESTION)


def test_only_the_top_ranked_reporter_is_laid_out():
    """The same window holds 국민연금공단 and 코봇홀딩스 filings on the same
    issuer. Without the reporter filter the line splices two chains."""
    others = [dict(r, rcept_no=f"nps-{i}", reporter="국민연금공단",
                   ratio_prev=None, ratio_current=9.0 + i,
                   shares_prev=None, shares_current="1,000")
              for i, r in enumerate(DOOSAN[:3])]
    line = aggregate.holding_series(DOOSAN + others, QUESTION)
    assert "(주)두산" in line and "국민연금공단" not in line
    assert "9.0%" not in line


def test_a_single_filing_is_not_a_series():
    assert aggregate.holding_series(DOOSAN[:1], QUESTION) == ""


def test_records_without_a_current_ratio_are_skipped():
    bare = [{"rcept_no": "1", "rcept_dt": "20240101", "reporter": "(주)두산"},
            {"rcept_no": "2", "rcept_dt": "20240102", "reporter": "(주)두산"}]
    assert aggregate.holding_series(bare, QUESTION) == ""


def test_a_question_that_does_not_ask_for_a_history_gets_nothing():
    assert aggregate.holding_series(DOOSAN, "두산로보틱스의 최대주주는 누구인가?") == ""


def test_the_line_carries_no_receipt_numbers():
    """Same rule as `partner_profile` and `counterparty_branch`: the citation
    is attached by code elsewhere, and receipt numbers in a summary line get
    copied into the answer as if they were the citation."""
    import re

    assert not re.search(r"\d{14}", aggregate.holding_series(DOOSAN, QUESTION))


def test_the_flag_is_off_by_default():
    assert ablation.Arm("X").holding_series is False
    assert "holding_series" in ablation.FLAG_NAMES
