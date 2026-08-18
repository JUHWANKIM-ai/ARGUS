"""Answer-side repair when a decisive line did not reach the answer.

The decisive lines sit at the head of the evidence and the model has to copy
them. Two measured runs show it does not: Q-107 had all three golden filings
in the evidence and still answered wrong, and Q-117 had the branch line and
still dropped four of five golden points.

`substitute` and `state_total` already close that gap for questions with a
pinned operation. Q-106 and Q-117 have none — `_op` returns "" — so this is
the same move for the lines those two depend on.

The trigger is under-coverage of the line's own figures, not the completeness
check: an answer stating one amount escapes `amount_gap` while still missing
most of the line.
See docs/design-rationale.md#gap-repair
"""
from argus import ablation
from argus.agent import aggregate

HOLDING_LINE = (
    "- [결정적 집계] (주)두산 보고 보유비율 추이 (4건): "
    "2023-10-13 68.19% (44,200,027주) → 2025-03-05 68.15% → "
    "2025-07-07 68.11% → 2026-02-27 50.06% (32,449,065주) · 누적 -18.13%p "
    "(이 값은 시스템이 위 근거의 비율 필드에서 직접 읽은 것이다. "
    "답변의 해당 수치는 이 값을 그대로 쓴다.)")

EVIDENCE = HOLDING_LINE + "\n\n## [대량보유상황보고서] 두산로보틱스\n- 보유비율: 이번 68.19%"

THIN = "보유비율은 68.19% 에서 시작해 소폭 줄었습니다.\n\n(근거: 주식등의대량보유상황보고서 2023-10, 접수번호 20231013000473)"


def test_the_repair_appends_the_missing_figures():
    out, reason = aggregate.repair_gap(THIN, EVIDENCE)
    assert reason
    for figure in ("68.15%", "68.11%", "50.06%", "18.13%"):
        assert figure in out


def test_the_repair_strips_the_evidence_directive():
    """The directive tells the model what to do with the evidence. Carried
    into the answer it is a leak of our own instructions."""
    out, _ = aggregate.repair_gap(THIN, EVIDENCE)
    assert "이 값은 시스템이" not in out
    assert "답변의 해당 수치는" not in out


def test_the_repair_strips_the_line_marker():
    out, _ = aggregate.repair_gap(THIN, EVIDENCE)
    assert "[결정적 집계]" not in out


def test_the_citation_stays_at_the_end():
    """`split_tail` exists so an appended sentence does not land after the
    citation, where it reads as part of it."""
    out, _ = aggregate.repair_gap(THIN, EVIDENCE)
    assert out.rstrip().endswith("20231013000473)")


def test_an_answer_that_already_covers_the_figures_is_untouched():
    full = ("보유비율은 68.19% → 68.15% → 68.11% → 50.06% 로 줄어 누적 18.13%p "
            "감소했고 보유주식수는 44,200,027주에서 32,449,065주가 됐습니다.")
    out, reason = aggregate.repair_gap(full, EVIDENCE)
    assert out == full and reason == ""


def test_evidence_with_no_decisive_line_is_untouched():
    out, reason = aggregate.repair_gap(THIN, "## [보고서] 두산\n- 보유비율: 68.19%")
    assert out == THIN and reason == ""


def test_a_line_with_too_few_figures_does_not_trigger():
    """A single-value line — the aggregate total — is already handled by
    `substitute` and `state_total`. Repairing it here would append a sentence
    those two exist to avoid."""
    small = "- [결정적 집계] 합계 6,890,924,666,293원 (이 값은 시스템이 계산한 것이다.)"
    out, reason = aggregate.repair_gap("합계는 1,000원입니다.", small + "\n\n## 근거")
    assert out == "합계는 1,000원입니다." and reason == ""


def test_a_branch_line_is_repaired_too():
    branch = (
        "- [결정적 집계] 계약상대 11곳이 두 갈래로 나뉜다: 발주처 10곳 — "
        "한국수력원자력(체코 두코바니 5&6호기 종합설계용역 1,250,850,298,678원) · "
        "공급사 재수주 1곳 — 두산에너빌리티(신한울원자력발전소 3,4호기 원자로계통 "
        "설계용역 181,608,900,000원 · 체코 두코바니 5&6호기 원자로계통설계용역 "
        "373,449,426,066원) (공급사 쪽도 스스로 공시 대상 상장사다)\n"
        "  답변은 이 두 갈래로만 나누고 다른 축으로 다시 나누지 않는다.")
    thin = "발주 구조는 공기업 직접 수주와 민간 재수주로 나뉩니다."
    out, reason = aggregate.repair_gap(thin, branch + "\n\n## 근거")
    assert reason
    assert "181,608,900,000" in out
    assert "답변은 이 두 갈래로만" not in out


def test_the_marker_matches_what_the_writers_actually_emit():
    """`DECISIVE_MARK` is a read-side constant: the five functions that place
    a line write the literal. If a writer's wording drifts, the repair goes
    silent with no error, so the constant is pinned against a real line here.
    """
    line = aggregate.counterparty_branch({
        "두산에너빌리티": [{"rcept_no": "1"}],
        "한국수력원자력": [{"rcept_no": "2"}]})
    assert line.startswith(aggregate.DECISIVE_MARK)
    assert HOLDING_LINE.startswith(aggregate.DECISIVE_MARK)


def test_the_flag_is_off_by_default():
    assert ablation.Arm("X").gap_repair is False
    assert "gap_repair" in ablation.FLAG_NAMES
