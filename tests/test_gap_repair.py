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


# --- Finding 1: the marker is pinned against every real writer, not a literal
#
# A hand-typed literal in this file (HOLDING_LINE, above) cannot detect a
# writer's wording drifting from DECISIVE_MARK, because both sides of that
# comparison would be author-written. Each of the five writers is called here
# with the smallest real fixture that makes it speak.

def _sum_line_records():
    """Three unrelated records with a stated count of three — the minimal
    fixture that makes `line`'s "합" branch speak."""
    return [
        {"rcept_no": "1", "rcept_dt": "20250101", "amount": 1_000_000_000,
         "related": [], "corp_name": "가"},
        {"rcept_no": "2", "rcept_dt": "20250102", "amount": 2_000_000_000,
         "related": [], "corp_name": "나"},
        {"rcept_no": "3", "rcept_dt": "20250103", "amount": 3_000_000_000,
         "related": [], "corp_name": "다"},
    ]


def _revision_chain_records():
    """Two records joined through `related` — the smallest chain
    `revision_series` will lay out."""
    return [
        {"rcept_no": "10", "rcept_dt": "20250101", "amount": 500_000_000,
         "related": ["11"], "corp_name": "가"},
        {"rcept_no": "11", "rcept_dt": "20250201", "amount": 600_000_000,
         "related": ["10"], "corp_name": "가"},
    ]


def _holding_chain_records():
    """Two filings by one reporter — the smallest series `holding_series`
    will lay out."""
    return [
        {"reporter": "두산", "rcept_no": "1", "rcept_dt": "20250101",
         "ratio_current": "10.00", "shares_current": "1000"},
        {"reporter": "두산", "rcept_no": "2", "rcept_dt": "20250201",
         "ratio_current": "9.00", "shares_current": "900"},
    ]


def test_the_marker_matches_what_the_writers_actually_emit():
    """`DECISIVE_MARK` is a read-side constant: the five functions that place
    a line write the literal. Each is called here with a minimal real
    fixture, so a writer's wording drifting from the literal is caught —
    comparing two hand-typed strings, as the previous version of this test
    did for `holding_series` via `HOLDING_LINE`, cannot catch that drift.
    """
    lines = [
        aggregate.line(_sum_line_records(),
                        "가 나 다 3건의 계약금액 총액은 얼마인가?", None),
        aggregate.revision_series(_revision_chain_records(),
                                   "이 계약의 정정 과정을 알려줘"),
        aggregate.partner_profile({
            "가": [{"title": "공급계약"}],
            "나": [{"title": "공급계약"}, {"title": "공급계약2"}]}),
        aggregate.counterparty_branch({
            "두산에너빌리티": [{"rcept_no": "1"}],
            "한국수력원자력": [{"rcept_no": "2"}]}),
        aggregate.holding_series(_holding_chain_records(), "보유비율 추이를 알려줘"),
    ]
    for line in lines:
        assert line, "a writer produced nothing for its own minimal fixture"
        assert line.startswith(aggregate.DECISIVE_MARK)


def test_the_flag_is_off_by_default():
    assert ablation.Arm("X").gap_repair is False
    assert "gap_repair" in ablation.FLAG_NAMES


# --- Finding 2: the half-coverage boundary itself, not just the extremes
#
# Every case above sits at an extreme (0/7, 1/7, 0/3, or full coverage). The
# rule is strictly-fewer-than-half fires, half-or-more is silent, and an
# off-by-one at that comparison (`>` vs `>=`) would pass every test above.
# An even figure count is used here so "exactly half" is a distinct case from
# "one fewer than half".

BOUNDARY_LINE = ("- [결정적 집계] 경계 시험용 네 값 (4건): "
                  "10,000,000 · 20,000,000 · 30,000,000 · 40,000,000 "
                  "(이 값은 시스템이 계산한 것이다.)")

BOUNDARY_EVIDENCE = BOUNDARY_LINE + "\n\n## 근거"


def test_exactly_half_present_is_silent():
    half = "금액은 10,000,000원과 20,000,000원이 확인됩니다."
    out, reason = aggregate.repair_gap(half, BOUNDARY_EVIDENCE)
    assert out == half and reason == ""


def test_one_fewer_than_half_fires():
    one_short = "금액은 10,000,000원이 확인됩니다."
    out, reason = aggregate.repair_gap(one_short, BOUNDARY_EVIDENCE)
    assert reason
    assert "40,000,000" in out


# --- Finding 3: substring containment can count a different number as
# present
#
# `f in body` (naive containment) counts "68.19%" as present inside a body
# that only ever states "168.19%", or "500,000,000" as present inside a body
# that only ever states "1,500,000,000". Both are a different figure
# entirely. The effect is silent under-triggering: coverage reads higher than
# it is and the repair stays quiet when it should fire.
#
# COLLISION_LINE (three figures: 68.19%, 500,000,000, 700,000,000) is used so
# that one genuine figure plus one colliding figure crosses the naive
# check's threshold (2 of 3 counted, "silent") while the true count (1 of 3)
# is below it ("fires"). Each test isolates one collision shape: the other
# two figures of the line are entirely absent from the answer, so any
# non-zero apparent coverage beyond the one genuine figure is attributable
# only to the collision under test.

COLLISION_LINE = ("- [결정적 집계] 경계 시험용 세 값 (3건): "
                   "68.19% · 500,000,000 · 700,000,000 "
                   "(이 값은 시스템이 계산한 것이다.)")

COLLISION_EVIDENCE = COLLISION_LINE + "\n\n## 근거"


def test_a_longer_percentage_is_not_counted_as_the_figure():
    """"168.19%" must not be counted as coverage of the figure "68.19%"."""
    answer = "비율은 168.19% 였고 금액은 700,000,000원 이었습니다."
    out, reason = aggregate.repair_gap(answer, COLLISION_EVIDENCE)
    assert reason
    assert "68.19%" in out and "500,000,000" in out


def test_a_longer_integer_is_not_counted_as_the_figure():
    """"1,500,000,000" must not be counted as coverage of "500,000,000"."""
    answer = "68.19% 였고 정정 후 1,500,000,000원이 되었습니다."
    out, reason = aggregate.repair_gap(answer, COLLISION_EVIDENCE)
    assert reason
    assert "500,000,000" in out and "700,000,000" in out


# --- Finding 3, round 2: a period only matters as a decimal point
#
# The first fix over-anchored: `_FIG_D1 = r"(?![\d,.])"` blocked a following
# period unconditionally, so a figure stated right before an ordinary
# Korean sentence period ("68.19%.") was counted absent — the opposite
# error from the one just fixed, and the newer, visible kind (a redundant
# append the reader can see, rather than a silent no-op). A period is only
# a decimal continuation, and therefore only worth blocking, when a digit
# sits on its far side too.
#
# The same question was checked on the leading side rather than assumed
# fine: `_figure_present` compares space-stripped text, and stripping
# removes the space that ordinarily sits between a sentence's period and
# the figure opening the next clause. An unconditional leading period block
# was checked against that shape directly and found to reject a plainly
# stated figure the same way — so the leading side needed the same
# digit-conditional narrowing, not just the trailing side the finding named.

def test_a_sentence_final_period_does_not_block_the_trailing_figure():
    """A figure immediately followed by a Korean sentence's period (no space
    before the period, as in "68.19%.") must still count as present — that
    period is sentence punctuation, not a decimal continuation. The line has
    three figures; this states two of them (majority), so the repair must
    stay silent."""
    answer = "비율은 68.19%. 다른 금액은 700,000,000원 입니다."
    out, reason = aggregate.repair_gap(answer, COLLISION_EVIDENCE)
    assert out == answer and reason == ""


def test_a_sentence_period_with_a_stripped_space_does_not_block_the_leading_figure():
    """The presence check strips all spaces from the answer (so
    "44, 200, 027" still matches "44,200,027"), which also strips the space
    that ordinarily separates a sentence's period from the figure opening
    the next clause. An unconditional leading period block would then count
    a plainly stated figure as absent."""
    answer = "확인했다. 68.19%이고 700,000,000원 입니다."
    out, reason = aggregate.repair_gap(answer, COLLISION_EVIDENCE)
    assert out == answer and reason == ""


def test_a_decimal_continuation_is_not_counted_as_the_figure():
    """"20,000,000.50" is a different, more precise number than
    "20,000,000" and must not count as coverage of it."""
    answer = "확인된 금액은 20,000,000.50원 입니다."
    out, reason = aggregate.repair_gap(answer, BOUNDARY_EVIDENCE)
    assert reason
    assert "40,000,000" in out


# --- Finding 4: the repair reaches more than the narrative lines, on purpose
#
# `substitute` and `state_total` both read `_total`, which returns None for
# the "비교" (comparison) and "몫" (quotient) operations — so neither ever
# fires for those two, even though `line()` still writes a DECISIVE_MARK'd
# block for them, gated by its own separate flag. `gap_repair` is the only
# answer-side path for those two operations.
#
# A single-chain "차" (difference) line carries three figures — older, newer,
# delta — while `state_total` inserts only the delta (`_total`'s return
# value). That is 1/3 coverage, under the threshold, so `gap_repair` reaches
# what `state_total` left behind there too.
#
# This is deliberate, not overreach: the repair appends deterministic content
# computed from the evidence, which is the behaviour wanted wherever the
# answer dropped it, regardless of which writer produced the line. Narrowing
# the read side to only the narrative writers would require `repair_gap` to
# know which function produced a line, which is exactly the coupling
# `DECISIVE_MARK` exists to avoid.

def test_the_repair_reaches_a_comparison_line_state_total_cannot():
    records = [
        {"rcept_no": "1", "rcept_dt": "20251216", "amount": 4_928_968_079_043,
         "related": [], "corp_name": "두산에너빌리티"},
        {"rcept_no": "2", "rcept_dt": "20251216", "amount": 2_338_117_950_000,
         "related": [], "corp_name": "한전기술"},
    ]
    question = "두산에너빌리티가 한전기술보다 얼마나 큰가"
    # _total returns None for "비교": substitute/state_total never fire here.
    assert aggregate._total(records, question, None) is None
    with ablation.use(ablation.Arm("probe", compare_line=True)):
        line = aggregate.line(records, question, None)
    assert line.startswith(aggregate.DECISIVE_MARK)

    thin = "두산에너빌리티 쪽 계약금액이 더 큽니다."
    out, reason = aggregate.repair_gap(thin, line + "\n\n## 근거")
    assert reason
    assert "4,928,968,079,043" in out and "2,338,117,950,000" in out


def test_a_single_chain_difference_line_is_completed_beyond_state_total():
    records = [
        {"rcept_no": "1", "rcept_dt": "20250101", "amount": 1_000_000_000,
         "related": ["2"], "corp_name": "가"},
        {"rcept_no": "2", "rcept_dt": "20250201", "amount": 1_300_000_000,
         "related": ["1"], "corp_name": "가"},
    ]
    question = "정정 후 계약금액은 얼마나 증가했는가?"
    line = aggregate.line(records, question, None)
    assert line.startswith(aggregate.DECISIVE_MARK)

    # state_total adds only the delta (300,000,000) — the other two figures
    # of the line, the older and newer amounts, are still absent afterward.
    answer, stated = aggregate.state_total(
        "계약금액이 정정되어 늘었습니다.", records, question, None)
    assert stated

    out, reason = aggregate.repair_gap(answer, line + "\n\n## 근거")
    assert reason
    assert "1,000,000,000" in out and "1,300,000,000" in out


# --- Minor: two qualifying lines in one evidence stay in evidence order

def test_two_qualifying_lines_are_appended_in_evidence_order():
    branch = (
        "- [결정적 집계] 계약상대 11곳이 두 갈래로 나뉜다: 발주처 10곳 — "
        "한국수력원자력(체코 두코바니 5&6호기 종합설계용역 1,250,850,298,678원) · "
        "공급사 재수주 1곳 — 두산에너빌리티(신한울원자력발전소 3,4호기 원자로계통 "
        "설계용역 181,608,900,000원 · 체코 두코바니 5&6호기 원자로계통설계용역 "
        "373,449,426,066원) (공급사 쪽도 스스로 공시 대상 상장사다)\n"
        "  답변은 이 두 갈래로만 나누고 다른 축으로 다시 나누지 않는다.")
    evidence = HOLDING_LINE + "\n\n" + branch + "\n\n## 근거"
    out, reason = aggregate.repair_gap(THIN, evidence)
    assert reason == "repaired 2"
    holding_pos = out.index("68.15%")
    branch_pos = out.index("181,608,900,000")
    assert holding_pos < branch_pos
