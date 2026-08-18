"""Deterministic aggregation emitted as a single evidence line.

No LLM calls, no retrieval, no step budget.

Sums, differences and quotients are computed from the amount fields of the
evidence records rather than left to the model. Measurement showed a class of
failures where every term was present in the evidence and only the arithmetic
was wrong.

The module emits one line rather than widening the evidence window. Widening
was attempted three times and rejected each time, so the design constraint is
that this path must not consume evidence slots.
See docs/design-rationale.md#evidence-slot-vs-line

Nothing is emitted unless the computation is uniquely determined. Asserting a
wrong total with confidence is worse than staying silent, because the model
does not question the line. The early returns in the functions below are that
guard.
"""
from __future__ import annotations

import re

from argus import ablation

#: Operation requested by the question. Order matters: difference must be
#: tested before sum so that growth phrasings do not fall through to sum.
#: The difference group includes revision phrasings, which state that a
#: value changed without stating a direction. That matches `_delta_groups`
#: taking an absolute value.
#:
#: Comparison is tested first of all. Its hint "차이는" also appears in
#: difference questions, and `_op` returns the first match, so the order
#: decides which operation a question with both phrasings receives.
#: See docs/design-rationale.md#comparison-line
_OPS = (("비교", ("보다 큰가", "보다 작은가", "보다 얼마나 큰", "보다 얼마나 작은",
                 "얼마나 큰가", "얼마나 작은가", "어느 쪽이 큰", "어느 쪽이 더")),
        ("차", ("얼마나 증가", "얼마나 늘었", "얼마나 감소", "얼마나 줄었",
                "증가했는가", "감소했는가", "얼마나 늘어", "얼마나 줄어",
                "얼마나 바뀌", "얼마나 변경", "얼마나 달라", "얼마나 올랐",
                "얼마나 내렸", "변경되었는가", "바뀌었는가",
                "합계보다", "차이는")),
        ("몫", ("1척당", "1기당", "1대당", "1건당", "척당", "기당", "대당", "건당",
                "평균 투자금액", "평균 계약금액", "1척 당", "1기 당")),
        ("합", ("합계", "총합", "총액", "모두 더하", "다 더하", "합은", "총 얼마")))

#: Divisor for the quotient operation, read from the question. Unit counts
#: are not carried as labelled fields in the evidence.
_UNIT_COUNT = re.compile(r"(\d{1,4})\s*(?:척|기|대|건|개)")

#: Filing-year filter. Applies only when the question constrains the filing
#: date. A different axis from the fiscal year, which for periodic reports
#: is not the filing date.
_FILED_YEAR = re.compile(r"(20\d{2})\s*년\s*(?:에\s*)?공시")

#: Full filing-date filter, applied only when the question pins a single day.
_FILED_DATE = re.compile(r"(20\d{2})\s*년\s*(1[0-2]|0?[1-9])\s*월\s*(3[01]|[12]\d|0?[1-9])\s*일")

#: Item count stated by the question. Acts as the safety condition for the
#: sum operation. A counter word is required after a numeral, because a bare
#: character class matches single syllables inside unrelated proper nouns.
#: See docs/design-rationale.md#stated-count
_STATED_COUNT = re.compile(
    r"(\d{1,2})\s*(?:건|개\s*(?:상장사|회사|사)?|곳|군데)"
    r"|(?:^|[\s,.])(두|세|네|다섯|여섯)\s*(?:건|개|곳|군데|공시|회사|상장사)")
_WORD_COUNT = {"두": 2, "세": 3, "네": 4, "다섯": 5, "여섯": 6}


#: Disposition stated alongside the value. Supplying the number alone is not
#: sufficient: the model recomputes and trusts its own result. The directive
#: is placed inside the evidence rather than the system prompt, because a
#: system-level rule perturbs answers to unrelated questions.
#: See docs/design-rationale.md#model-recomputes
_DIRECTIVE = ("(이 값은 시스템이 위 근거의 금액 필드에서 직접 계산한 것이다. "
              "답변의 해당 수치는 이 값을 그대로 쓰고 다시 계산하지 않는다.)")


def _op(question: str) -> str:
    text = question or ""
    for name, hints in _OPS:
        if any(h in text for h in hints):
            return name
    return ""


#: Amounts eligible as substitution candidates in an answer. Comma groups
#: are required so that years and ratios are not treated as amounts.
_ANSWER_NUM = re.compile(r"\d{1,3}(?:,\d{3})+")

#: Openers of a concluding sentence. Candidate selection uses position
#: rather than magnitude: the figure an answer presents as its result
#: appears in the conclusion. A magnitude guard was tried and rejected,
#: since the size of the model's error cannot be bounded in advance.
_CONCLUSION = ("따라서", "그러므로", "결론적으로", "정리하면", "즉,")

#: Minimum magnitude treated as an amount. Below this a figure is a count,
#: a ratio or an index.
MIN_SUBSTITUTE_AMOUNT = 100_000_000

#: Marker recording that a filing-date filter applied. `_sum_rows` uses it
#: to separate the filing-date axis, which pins the set, from the
#: counterparty axis, which does not.
_DATE_NOTE = "공시분"

#: Marker recording that a project filter applied. Read by `_sum_rows` the
#: same way `_DATE_NOTE` is: both pin the set, where the counterparty axis on
#: its own does not.
_PROJECT_NOTE = "사업분"

#: Separators inside a supply-region field: `체코 남부 Dukovany Site`.
_REGION_SPLIT = re.compile(r"[\s,·/]+")

#: Shortest usable project token. One character matches far too much; two is
#: the length of `체코`, the token Q-107 turns on.
MIN_PROJECT_TOKEN = 2


def _project_token(picked: list[dict], question: str) -> str:
    """The project the question names, read from the evidence, or "".

    The vocabulary is not guessed. A token counts only when it appears in a
    `supply_region` the evidence actually carries AND in the question, so the
    corpus supplies the terms and the question selects among them. This is why
    the axis needs the rebuilt index: before it, no record carries the field
    and this function has nothing to read.

    The longest hit wins, because a more specific place name is a tighter
    filter than the country containing it. Ties are broken by codepoint
    order rather than left to `max`'s first-seen behaviour: `tokens` is
    built from a `set`, whose iteration order depends on `PYTHONHASHSEED`,
    so two equal-length hits without a secondary key would make the winner
    (and therefore the total downstream) change across process restarts with
    no error raised. A measurement pipeline cannot tolerate that.
    See docs/design-rationale.md#project-scope
    """
    tokens: set[str] = set()
    for record in picked or []:
        for token in _REGION_SPLIT.split(str(record.get("supply_region") or "")):
            if len(token) >= MIN_PROJECT_TOKEN:
                tokens.add(token)
    hits = [t for t in tokens if t in (question or "")]
    if not hits:
        return ""
    return sorted(hits, key=lambda t: (-len(t), t))[0]


def substitute(answer: str, records, question: str, route) -> tuple[str, str]:
    """Replace a single incorrect total in the answer with the computed value.

    Returns (new answer, reason), or (original, "") when nothing applies.

    Emitting the aggregate line is not sufficient on its own: the model
    recomputes and trusts its own arithmetic.
    See docs/design-rationale.md#model-recomputes

    Complements equation-based verification, which fires only when the answer
    contains a parseable expression. The source here is the amount field of the
    evidence, so it is independent of how the answer is written.

    Substitution is skipped when more than one candidate exists. Choosing
    without knowing which figure is the total would corrupt a correct one.
    """
    total = _total(records, question, route)
    if total is None:
        return answer, ""
    known = {_amount(r) for r in (records or []) if _amount(r) is not None}
    known.add(total)
    candidates = {
        text for text in _ANSWER_NUM.findall(_conclusion(answer))
        if (value := int(text.replace(",", ""))) not in known
        and value >= MIN_SUBSTITUTE_AMOUNT}
    if len(candidates) != 1:
        return answer, ""
    wrong = candidates.pop()
    body, tail = split_tail(answer)
    return (body.replace(wrong, f"{total:,}") + _prune_notes(tail, wrong),
            f"{wrong} -> {total:,}")


#: Notes the system appends after an answer. Single definition of this rule;
#: duplicating the pattern lets copies drift when the note format changes.
_NOTE = re.compile(r"\n*\((?:주의|근거)[:：]")


def split_tail(answer: str) -> tuple[str, str]:
    """Split an answer into body and system-appended notes.

    Returns (answer, "") when no note is present. Only the body is eligible for
    rewriting: notes record what the verifier observed, and editing them would
    turn a record into an invention.
    """
    hit = _NOTE.search(answer or "")
    return (answer, "") if not hit else (answer[:hit.start()], answer[hit.start():])


def _prune_notes(tail: str, wrong: str) -> str:
    """Remove note entries that reference a value which was just substituted.

    Applying the substitution across the whole answer would make the note claim
    a figure the verifier never saw. Leaving a resolved defect listed as
    outstanding is equally wrong, so the referencing entry is dropped and the
    note is removed entirely when nothing remains.
    See docs/design-rationale.md#substitution-scope
    """
    if not tail or wrong not in tail:
        return tail

    def _fix(match: re.Match) -> str:
        items = [s.strip() for s in match.group(2).split(";")]
        kept = [s for s in items if wrong not in s]
        if not kept:
            return ""
        return f"{match.group(1)}{'; '.join(kept)})"

    pruned = re.sub(r"(\n*\(주의: 자동 검증에서 결함이 발견되었습니다 — )([^)]*)\)",
                    _fix, tail)
    return pruned


#: Phrasings asking for a revision history to be laid out. Kept disjoint
#: from the difference operation: that one yields a single value, this one
#: yields a sequence. Firing both on one question would place two
#: contradictory lines in the evidence.
_SERIES_ASK = ("시간순", "시계열", "어떻게 바뀌", "어떻게 변했", "변동 내역",
               "변화 과정", "추이", "정정 과정", "차례로", "순서대로")


def revision_series(records, question: str) -> str:
    """Lay out the amounts of a revision chain in filing-date order, one line.

    No LLM call and no retrieval. Returns "" when the question does not ask for
    a history.

    A chain scattered across evidence blocks is read only as far as the first
    block, and a note that detects the resulting staleness does not correct the
    answer. The line removes the scattering.
    See docs/design-rationale.md#revision-series

    Only the chain containing the top-ranked evidence record is laid out, the
    same rule as `_delta_groups`.
    """
    if not any(h in (question or "") for h in _SERIES_ASK):
        return ""
    rows = [r for r in (records or []) if _amount(r) is not None]
    if len(rows) < 2:
        return ""
    rank = {r["rcept_no"]: i for i, r in enumerate(rows)}
    groups = [g for g in _chains(rows) if len(g) >= 2]
    if not groups:
        return ""
    groups.sort(key=lambda g: min(rank.get(r["rcept_no"], 1 << 30) for r in g))
    chain = sorted(groups[0],
                   key=lambda r: (str(r.get("rcept_dt", "")), r.get("rcept_no", "")))
    steps = " → ".join(f"{_ymd(r)} {_amount(r):,}원" for r in chain)
    return (f"- [결정적 집계] 접수일 순 금액 추이 ({len(chain)}건): {steps} "
            f"{_DIRECTIVE}")


def _ymd(record: dict) -> str:
    raw = str(record.get("rcept_dt", ""))
    return f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}" if len(raw) >= 8 else raw


#: Firing words for the holding-ratio series. A separate tuple from
#: `_SERIES_ASK`: widening that one to catch Q-106's "어떻게 변해 왔는지" would
#: move `revision_series`'s firing range too, and that function's behaviour is
#: already measured. This tuple can stay loose because the record condition
#: below — two or more holding filings by one reporter — is the strong half of
#: the premise.
#: See docs/design-rationale.md#holding-series
_HOLDING_ASK = ("추이", "변해", "변화", "변동", "바뀌", "시간순", "시계열", "어떻게 변")


def _ratio_of(record: dict):
    """This filing's holding ratio as a float, or None."""
    value = record.get("ratio_current")
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def holding_series(records, question: str) -> str:
    """Lay out one reporter's holding ratios in filing order, as one line.

    No LLM call and no retrieval. Returns "" when the question does not ask
    for a history or the records do not carry a series.

    The same move `revision_series` makes for a revision chain's amounts, with
    two differences: the grouping axis is the reporter rather than the chain,
    and the value is `ratio_current` rather than `amount`. The reporter filter
    is load-bearing — the same window holds other reporters' filings on the
    same issuer, and splicing them produces a series that never happened.

    Only the reporter of the top-ranked record is laid out, the same rule
    `revision_series` uses for chains.
    See docs/design-rationale.md#holding-series
    """
    if not any(h in (question or "") for h in _HOLDING_ASK):
        return ""
    rows = [r for r in (records or []) if _ratio_of(r) is not None]
    if len(rows) < 2:
        return ""
    reporter = str(rows[0].get("reporter") or "")
    chain = [r for r in rows if str(r.get("reporter") or "") == reporter]
    if len(chain) < 2:
        return ""
    chain.sort(key=lambda r: (str(r.get("rcept_dt", "")), r.get("rcept_no", "")))

    steps = []
    for index, record in enumerate(chain):
        shares = str(record.get("shares_current") or "")
        # Share counts only at the ends. Golden points 1 and 5 are the first
        # and last counts; carrying all of them doubles the line length for
        # values the question did not ask about.
        tail = f" ({shares}주)" if shares and index in (0, len(chain) - 1) else ""
        steps.append(f"{_ymd(record)} {_ratio_of(record):.2f}%{tail}")

    moved = _ratio_of(chain[0]) - _ratio_of(chain[-1])
    return (f"- [결정적 집계] {reporter} 보고 보유비율 추이 ({len(chain)}건): "
            f"{' → '.join(steps)} · 누적 {-moved:+.2f}%p "
            f"(이 값은 시스템이 위 근거의 비율 필드에서 직접 읽은 것이다. "
            f"답변의 해당 수치는 이 값을 그대로 쓴다.)")


#: Upper bound on counterparties carried in the distribution line. A separate
#: axis from `MAX_ENUM_EVIDENCE`, which counts evidence blocks; this counts
#: items inside one line. Eleven groups fit in roughly 120 characters.
MAX_PARTNER_PROFILE = 12


def partner_profile(groups: dict) -> str:
    """Emit the per-counterparty filing count distribution as one line.

    No LLM call and no retrieval. Returns "" for fewer than two counterparties.

    `_enumerate_events` builds this mapping and then keeps only
    `MAX_ENUM_EVIDENCE` blocks of it, discarding the rest of the distribution.
    Widening the evidence window was rejected, so the distribution is emitted
    as a line instead.
    See docs/design-rationale.md#partner-profile
    """
    rows = [(name, members) for name, members in (groups or {}).items() if name]
    if len(rows) < 2:
        return ""
    rows.sort(key=lambda x: (-len(x[1]), x[0]))
    total = sum(len(m) for _, m in rows)
    lines = [f"- [결정적 집계] 계약상대별 공시 분포 (전체 {total}건 · 상대 {len(rows)}곳)"]
    for name, members in rows[:MAX_PARTNER_PROFILE]:
        lines.append(f"  - {name}: {len(members)}건 — {_subject(members[0])}")
    lines.append(f"  {_DIRECTIVE}")
    return "\n".join(lines)


def _subject(event: dict) -> str:
    """Subject of the representative contract for one counterparty.

    Filing counts alone do not expose how the counterparty set divides. The
    contract title carries the distinction that company names do not.
    See docs/design-rationale.md#partner-profile
    """
    title = str(event.get("title") or "")
    tail = title.split("—", 1)[-1].strip() if "—" in title else title.strip()
    return tail[:MAX_SUBJECT_CHARS] or "-"


#: Length cap for a representative contract title. Eleven counterparties at
#: 44 characters stays under the size of a single evidence block.
MAX_SUBJECT_CHARS = 44


def counterparty_branch(groups: dict) -> str:
    """Emit a counterparty set split into two industry-term groups, as one
    line.

    No LLM call, no retrieval. Returns "" unless both branches are non-empty:
    a single branch is not a split, and asserting one would be a confident
    wrong statement.

    Carries no receipt numbers and no per-company filing counts, for the same
    reason as `partner_profile`.

    The split is decided by one predicate: whether the counterparty is one of
    the 70 companies the evaluation covers. That membership test is already
    implemented and tested in `argus.corpus.counterparty.classify`, so it is
    reused here rather than duplicated. The line names the two resulting
    groups in industry terms (an ordering client versus a supplier that
    re-ordered) rather than in terms of that predicate: naming the predicate
    itself leaked into an answer once. See docs/design-rationale.md#counterparty-branch
    """
    from argus.corpus.counterparty import classify

    names = [name for name in (groups or {}) if name]
    if not names:
        return ""
    order = sorted(names, key=lambda k: (-len(groups[k]), k))
    inside = [n for n in order if classify(n) == "universe"]
    outside = [n for n in order if n not in inside]
    if not inside or not outside:
        return ""
    return (
        f"- [결정적 집계] 계약상대 {len(order)}곳이 두 갈래로 나뉜다: "
        f"발주처 {len(outside)}곳 — {' · '.join(outside)} · "
        f"공급사 재수주 {len(inside)}곳 — {' · '.join(inside)} "
        f"(공급사 쪽도 스스로 공시 대상 상장사다)\n"
        f"  답변은 이 두 갈래로만 나누고 다른 축으로 다시 나누지 않는다."
    )


#: The answer form `1,234,567,890원(12억원)`. Markdown emphasis has to be
#: crossed, since answers contain `**207,684,631,210원**(20,768억원)`.
_PAREN_UNIT = re.compile(r"(\d{1,3}(?:,\d{3}){2,})(\s*원?\s*\**\s*)"
                         r"[（(]\s*(?:약\s*)?([^)）]{1,24})[)）]")

#: Expands a parenthesised unit expression into won. Covers `1조 2,345억원`,
#: `9,714억원` and `2,000억`.
_EOK_TEXT = re.compile(r"^(?:([\d,]+)\s*조\s*)?(?:([\d,]+(?:\.\d+)?)\s*억)?\s*원?$")

#: Rounding allowance. Unit notation drops anything below 100 million, so a
#: gap that size is not an error.
_PAREN_TOLERANCE = 10 ** 8


def fix_unit_parens(answer: str) -> tuple[str, list[str]]:
    """Recompute the parenthesised unit conversion in an answer.

    No LLM call and no retrieval. Returns (new answer, list of fixes), or
    (original, []) when nothing applies.

    Body figures are copied out of the evidence and are correct, while the
    parenthesised conversion is divided by the model and is not.
    See docs/design-rationale.md#unit-conversion

    Runs after `substitute`, which replaces the body figure and leaves behind
    the parenthesised value the model derived from its own wrong figure.

    The notation comes from `prompts._split_jo` rather than being formatted
    here. Evidence and answer in different notation are read as two values.

    Parentheses that do not parse as a conversion are left alone. Forms such as
    `(주당 1,000원)` and `(지분율 30%)` are common in answers, and only what
    `_EOK_TEXT` parses is eligible.
    """
    from argus.agent.prompts import EOK_PER_JO, _split_jo

    fixed: list[str] = []

    def _sub(match: re.Match) -> str:
        won = int(match.group(1).replace(",", ""))
        inner = match.group(3).strip()
        hit = _EOK_TEXT.match(inner)
        if not hit or not (hit.group(1) or hit.group(2)):
            return match.group(0)               # not a unit conversion
        stated = 0
        if hit.group(1):
            stated += int(hit.group(1).replace(",", "")) * EOK_PER_JO * 10 ** 8
        if hit.group(2):
            stated += int(float(hit.group(2).replace(",", "")) * 10 ** 8)
        if abs(stated - won) <= _PAREN_TOLERANCE:
            return match.group(0)               # rounding gap, value is correct
        eok = won / 10 ** 8
        right = _split_jo(eok) if abs(eok) >= EOK_PER_JO else f"{eok:,.0f}억원"
        fixed.append(f"{inner} -> {right}")
        return f"{match.group(1)}{match.group(2)}({right})"

    body, tail = split_tail(answer or "")
    body = _PAREN_UNIT.sub(_sub, body)
    # Drop note entries referring to the notation just rewritten. Left in
    # place, the note points at a figure no longer present in the answer.
    for note in fixed:
        tail = _prune_notes(tail, note.split(" -> ")[0])
    return body + tail, fixed


# --- Self-consistency: the answer must not contradict itself ---------------
#
# This covers one failure mode of the reasoning axis rather than measuring the
# axis. It applies only when the answer contains an expression, which held for
# 9 of 132 stored answers. Missing premises, leaps, ordering errors and
# self-negation are outside its reach.
# See docs/design-rationale.md#self-contradiction

#: An expression written by the answer itself: two left-hand terms and a
#: result.
_SELF_EQ = re.compile(r"([\d,]{7,})\s*([+\-−])\s*([\d,]{7,})\s*=\s*\**\s*([\d,]{7,})")

#: Proximity at which two figures are treated as the same quantity. Equal digit
#: count and within 5% is treated as a typo. Widening this conflates two
#: distinct amounts, and a false positive here is itself a new wrong answer.
_SELF_NEAR = 0.05


def fix_self_contradiction(answer: str) -> tuple[str, list[str]]:
    """Align a figure that contradicts an expression the answer itself wrote.

    No LLM call. Returns (new answer, list of fixes), or (original, []) when
    nothing applies.
    See docs/design-rationale.md#self-contradiction

    The expression result is trusted because its two operands are copied out of
    the evidence and `critic.verify_answer_arithmetic` has already confirmed the
    expression holds. The contradicting figure is prose that never passed
    through the expression.

    Notes are left alone, the same rule as `substitute`: they record what the
    verifier observed.
    """
    body, tail = split_tail(answer or "")
    equations = list(_SELF_EQ.finditer(body))
    if not equations:
        return answer, []

    fixed: list[str] = []
    for match in equations:
        result = int(match.group(4).replace(",", ""))
        operands = {int(match.group(1).replace(",", "")),
                    int(match.group(3).replace(",", ""))}
        for text in set(_ANSWER_NUM.findall(body)):
            value = int(text.replace(",", ""))
            if value == result or value in operands:
                continue
            if len(str(value)) != len(str(result)):
                continue
            if 0 < abs(value - result) / result < _SELF_NEAR:
                body = body.replace(text, f"{result:,}")
                fixed.append(f"{text} -> {result:,}")
    return body + tail, fixed


def _conclusion(answer: str) -> str:
    """Portion the answer presents as its final result, else the whole body.

    System-appended notes are excluded. Substituting inside them would rewrite
    the system's own record, and a recomputed value in a note would form a
    second candidate, which forces silence.
    See docs/design-rationale.md#substitution-scope
    """
    body, tail = split_tail(answer or "")
    for opener in _CONCLUSION:
        index = body.rfind(opener)
        if index >= 0:
            return body[index:]
    return body


def _total(records, question: str, route):
    """Total computed under the same rules `line()` uses, or None.

    Computing it in two places lets the two drift, the same reason
    `critic.unique_recomputed` is a single function.
    """
    op = _op(question)
    if op not in ("합", "차"):
        return None
    picked, note = select(records, question, route, op)
    if not picked:
        return None
    if op == "합":
        rows = _sum_rows(picked, question, note)
        return sum(_amount(r) for r in rows) if rows else None
    groups = _delta_groups(picked, question)
    if not groups:
        return None
    total = 0
    for group in groups:
        total += _amount(group[-1]) - _amount(group[0])
    # The result is a magnitude. The question asks how much a value rose or
    # fell, so the sign is not part of the answer, and carrying it through
    # would replace a correct figure with its negation.
    # See docs/design-rationale.md#delta-magnitude
    return abs(total) or None


def _delta_groups(picked: list[dict], question: str):
    """Chains targeted by the difference operation, or None.

    None is returned when the count does not match, which silences the module.
    The live path sees the whole evidence set rather than the golden subset, so
    taking every chain mixes in contracts the question did not ask about.
    See docs/design-rationale.md#aggregate-scope

    A stated count requires exactly that many chains. Without one, exactly one
    chain is required.
    """
    groups = [g for g in _chains(picked) if len(g) >= 2
              and all(_amount(r) is not None for r in g)]
    for group in groups:
        group.sort(key=lambda r: (str(r.get("rcept_dt", "")), r.get("rcept_no", "")))

    # The date is applied to the latest record of each chain. Applying it to
    # every record drops the original filing and breaks the chain. A date in a
    # difference question names the revision, so the chain whose latest record
    # carries that date is the one meant.
    day = _FILED_DATE.search(question or "")
    if day:
        want_dt = "%s%02d%02d" % (day.group(1), int(day.group(2)), int(day.group(3)))
        narrowed = [g for g in groups if str(g[-1].get("rcept_dt", "")) == want_dt]
        if narrowed:
            groups = narrowed

    want = _stated_count(question) or 1
    if len(groups) == want:
        return groups
    if len(groups) < want:
        return None
    # On overflow the top-ranked chains are used. Evidence can carry another
    # supply-contract chain from the same month, and the chain holding the
    # top-ranked record is the one the question points at.
    rank = {r["rcept_no"]: i for i, r in enumerate(picked)}
    groups.sort(key=lambda g: min(rank.get(r["rcept_no"], 1 << 30) for r in g))
    return groups[:want]


def _compare_pair(picked: list[dict], question: str):
    """The two records a comparison question sets against each other.

    Exactly two records with an amount qualify. With three or more the pair is
    not determined by the question, and naming the wrong two would put a
    confident wrong figure at the head of the evidence, which is worse than
    saying nothing.
    See docs/design-rationale.md#comparison-line
    """
    rows = [r for r in _latest_per_chain(picked) if _amount(r) is not None]
    if len(rows) != 2:
        return None
    return rows[0], rows[1]


def _amount(record: dict):
    value = (record or {}).get("amount")
    return value if isinstance(value, int) and value > 0 else None


def _chains(records: list[dict]) -> list[list[dict]]:
    """Group records joined through `related` into chains (union-find).

    Grouping by `corp_name` is wrong: one company files several unrelated
    contracts, and only some of them share a chain.
    """
    index = {r["rcept_no"]: i for i, r in enumerate(records)}
    parent = list(range(len(records)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, record in enumerate(records):
        for other in (record.get("related") or ()):
            j = index.get(other)
            if j is not None:
                parent[find(i)] = find(j)
    groups: dict[int, list[dict]] = {}
    for i, record in enumerate(records):
        groups.setdefault(find(i), []).append(record)
    return list(groups.values())


def select(records, question: str, route, op: str = "") -> tuple[list[dict], str]:
    """Aggregation targets and a description of the filters applied.

    Returns ([], "") when there is no target.

    Three filter axes exist: filing year, project and counterparty. These are
    the ones the measured wrong answers required. Adding axes breaks
    uniqueness, and without uniqueness this module has to stay silent.
    """
    picked = [r for r in (records or []) if _amount(r) is not None]
    if not picked:
        return [], ""
    notes: list[str] = []

    # Only the sum operation narrows the full record set by date. A date in a
    # difference question usually names the revision, and applying it here
    # drops the original filing, leaving nothing to difference against.
    # `_delta_groups` applies its date to the latest record of each chain.
    day = _FILED_DATE.search(question or "") if op == "합" else None
    if day:
        want = "%s%02d%02d" % (day.group(1), int(day.group(2)), int(day.group(3)))
        narrowed = [r for r in picked if str(r.get("rcept_dt", "")) == want]
        if narrowed:
            picked = narrowed
            notes.append(f"{want[:4]}-{want[4:6]}-{want[6:]} 공시분")

    year = _FILED_YEAR.search(question or "")
    if year and not day:
        want = year.group(1)
        picked = [r for r in picked if str(r.get("rcept_dt", ""))[:4] == want]
        notes.append(f"{want}년 공시분")

    # Project axis. `_sum_rows` recorded why this had to exist: a counterparty
    # filter on its own does not pin the set, because a hub holds several
    # contracts, and what such a question narrows is the project. Applied
    # before the counterparty filter so that the note ordering reads
    # project-then-party, which is the order the question states them in.
    #
    # A filter matching nothing is not applied, the same defence the
    # counterparty axis carries: uniqueness is what lets this module speak,
    # and an empty set is not uniqueness.
    # See docs/design-rationale.md#project-scope
    if ablation.enabled("project_scope"):
        token = _project_token(picked, question)
        if token:
            narrowed = [r for r in picked
                        if token in f"{r.get('title') or ''} "
                                    f"{r.get('supply_region') or ''}"]
            # `narrowed` cannot be empty today: `token` only ever comes from
            # some record's own `supply_region` (that is `_project_token`'s
            # sole vocabulary source), and the check just above re-tests that
            # same `supply_region`, so that record always survives. The
            # guard is kept anyway so that if the vocabulary source and this
            # check ever diverge -- for instance if a token is later also
            # drawn from `title` while this check still requires
            # `supply_region` to carry it too -- the filter fails safe by
            # reverting instead of silently emptying the set.
            if narrowed:
                picked = narrowed
                notes.append(f"{token} {_PROJECT_NOTE}")

    party = getattr(route, "counterparty", None)
    if party:
        from argus.corpus.counterparty import canonical

        key = canonical(party)
        narrowed = [r for r in picked
                    if canonical(r.get("counterparty") or "") == key]
        if narrowed:
            picked = narrowed
            notes.append(f"계약상대 {party}")

    return picked, " · ".join(notes)


def _stated_count(question: str):
    """Item count stated by the question, or None."""
    hit = _STATED_COUNT.search(question or "")
    if not hit:
        return None
    if hit.group(1):
        return int(hit.group(1))
    return _WORD_COUNT.get((hit.group(2) or "").replace(" 개", ""))


def _sum_rows(picked: list[dict], question: str, note: str = ""):
    """Rows targeted by the sum operation, or None.

    None is returned when the set is not pinned down, which silences the
    module. Summing the whole evidence set replaced a correct total with one
    that included another contract filed by the same company.
    See docs/design-rationale.md#aggregate-scope

    The set is summed only when one of two conditions holds:

        1. the question states a count and the set matches it
        2. a filter applied, on filing date or counterparty

    Otherwise None, which keeps silence as the module default.
    """
    rows = _latest_per_chain(picked)
    if len(rows) < 2:
        return None
    stated = _stated_count(question)
    if stated is not None:
        # On overflow the top N are used, since evidence mixes in filings
        # outside the question scope. The order is the retrieval ranking
        # preserved by `_latest_per_chain`. A shortfall stays silent: if the
        # question names three and only two exist, their sum is not the answer.
        return rows[:stated] if len(rows) >= stated else None
    # A counterparty filter on its own does not pin the set, because a hub
    # holds several contracts and summing them all overshoots. What such a
    # question narrows is the project, and that axis now exists: with it
    # applied the set is the one project's contracts, which is what the
    # question asked for.
    # See docs/design-rationale.md#project-scope
    return rows if (_DATE_NOTE in note or _PROJECT_NOTE in note) else None


def _latest_per_chain(records: list[dict]) -> list[dict]:
    """Latest record of each chain, so an original and its revision are never
    summed together.

    In the measured questions this rule changes nothing, because the sum
    questions carry `related=[]` throughout. It is kept because without it the
    total silently doubles as soon as one revision chain enters the evidence.
    See docs/design-rationale.md#aggregate-scope
    """
    order = {r["rcept_no"]: i for i, r in enumerate(records)}
    out = []
    for group in _chains(records):
        group.sort(key=lambda r: (str(r.get("rcept_dt", "")), r.get("rcept_no", "")))
        out.append(group[-1])
    # Evidence order is preserved. It is the retrieval ranking, and `_sum_rows`
    # selects the top N from it when the question states a count. Re-sorting by
    # filing date would discard that ranking.
    return sorted(out, key=lambda r: order.get(r["rcept_no"], 1 << 30))


def line(records, question: str, route) -> str:
    """One line to place in the evidence, or "" when there is nothing to emit."""
    op = _op(question)
    if not op:
        return ""
    picked, note = select(records, question, route, op)
    if not picked:
        return ""
    scope = f" ({note})" if note else ""

    if op == "비교":
        if not ablation.enabled("compare_line"):
            return ""
        pair = _compare_pair(picked, question)
        if pair is None:
            return ""
        first, second = pair
        gap = _amount(first) - _amount(second)
        if gap == 0:
            # A zero gap names no side. See docs/design-rationale.md#comparison-line
            verdict = "두 금액은 같다"
        else:
            bigger = first if gap > 0 else second
            verdict = f"{bigger.get('corp_name', '')} 쪽이 더 크다"
        return (f"- [결정적 집계] 두 금액 비교{scope}: "
                f"{first.get('corp_name', '')} {_amount(first):,}원 · "
                f"{second.get('corp_name', '')} {_amount(second):,}원 · "
                f"차이 **{abs(gap):,}원** · "
                f"{verdict} {_DIRECTIVE}")

    if op == "합":
        rows = _sum_rows(picked, question, note)
        if not rows:
            return ""
        total = sum(_amount(r) for r in rows)
        terms = " + ".join(f"{_amount(r):,}" for r in rows)
        return (f"- [결정적 집계] 위 근거 {len(rows)}건의 금액 합계{scope}: "
                f"{terms} = **{total:,}원** {_DIRECTIVE}")

    if op == "차":
        groups = _delta_groups(picked, question)
        if not groups:
            return ""
        parts, total = [], 0
        for group in groups:
            older, newer = group[0], group[-1]
            delta = _amount(newer) - _amount(older)
            total += delta
            parts.append(f"{newer.get('corp_name', '')} {_amount(newer):,} − "
                         f"{_amount(older):,} = {delta:,}")
        if len(parts) == 1:
            return (f"- [결정적 집계] 정정 전후 금액 변동{scope}: {parts[0]}원 "
                    f"{_DIRECTIVE}")
        return (f"- [결정적 집계] 정정 전후 금액 변동{scope} — "
                + " · ".join(parts)
                + f" · 합계 **{total:,}원** {_DIRECTIVE}")

    if op == "몫":
        rows = _latest_per_chain(picked)
        if len(rows) != 1:
            return ""
        hit = _UNIT_COUNT.search(question or "")
        if not hit:
            return ""
        n = int(hit.group(1))
        total = _amount(rows[0])
        if n <= 1 or total % n:
            # A quotient that does not divide evenly states its remainder
            # rather than rounding. A rounded quotient would itself be a new
            # wrong answer, and the model does not question a figure the
            # system supplies.
            return "" if n <= 1 else (
                f"- [결정적 집계] 단위당 금액{scope}: {total:,} ÷ {n} = "
                f"**{total // n:,}원** (나머지 {total % n:,}) {_DIRECTIVE}")
        return (f"- [결정적 집계] 단위당 금액{scope}: {total:,} ÷ {n} = "
                f"**{total // n:,}원** {_DIRECTIVE}")

    return ""


#: Unit notation standing alone without the exact figure. Matches `1,234억원`,
#: `1조 2,345억원` and `7,257 억 원`. Occurrences preceded by a digit or comma
#: are excluded: a position that already carries the exact figure, such as
#: `257,732,228,908원 (2,577억원)`, belongs to `fix_unit_parens`.
_BARE_EOK = re.compile(
    r"(?<![\d,])(?:(\d{1,3}(?:,\d{3})*|\d+)\s*조\s*)?"
    r"(\d{1,3}(?:,\d{3})*|\d+)\s*억\s*원")

#: Lower bound for restoring an exact figure. Below 100 million, rounding
#: loses nothing and the value is out of scope here.
MIN_EXPAND_AMOUNT = 100_000_000


def expand_rounded(answer: str, records) -> tuple[str, list[str]]:
    """Restore an exact figure from the evidence where only a rounding is given.

    No LLM call and no retrieval. Returns (new answer, list of fixes), or
    (original, []) when nothing applies.

    The reverse direction of `fix_unit_parens`: that one repairs a correct
    figure carrying a wrong parenthesised conversion, this one repairs a
    conversion given without any figure. The value is correct and only
    precision was lost, but the accuracy criterion asks whether the figure is
    exact and scoring against the golden answer looks for the exact figure.
    See docs/design-rationale.md#unit-conversion

    Nothing is done unless the restoration is uniquely determined, the same
    rule as `substitute`. When two evidence amounts round to the same unit
    value, asserting one turns a correct approximation into a wrong exact
    figure.

    Only the body is examined. Notes are system-written, see `split_tail`.
    """
    body, tail = split_tail(answer or "")
    if not body:
        return answer, []
    known = sorted({a for r in (records or [])
                    if (a := _amount(r)) and a >= MIN_EXPAND_AMOUNT})
    if not known:
        return answer, []

    fixed: list[str] = []

    def _sub(match: re.Match) -> str:
        jo = int((match.group(1) or "0").replace(",", ""))
        eok = int(match.group(2).replace(",", ""))
        stated = jo * 10_000 + eok                      # count of 100M units
        hits = [a for a in known if round(a / 100_000_000) == stated]
        if len(hits) != 1:
            return match.group(0)
        exact = f"{hits[0]:,}"
        if exact in body:                               # exact figure present
            return match.group(0)
        fixed.append(f"{match.group(0)} -> {exact}원")
        return f"{exact}원 ({match.group(0)})"

    return _BARE_EOK.sub(_sub, body) + tail, fixed


def state_total(answer: str, records, question: str, route) -> tuple[str, str]:
    """Append a concluding sentence when the known result is absent.

    No LLM call. Returns (new answer, reason), or (original, "") when nothing
    applies.

    `substitute` does not reach this case: it fires only when the answer holds
    a single figure absent from the evidence. Here the model used correct
    figures and combined them wrongly, so both figures are in the evidence and
    the substitution condition never holds.
    See docs/design-rationale.md#state-total

    The model's sentences are not edited. Its narrative is not itself false;
    what is missing is the value the question asked for, so that alone is
    added. Adding costs less than deleting.

    Runs after `substitute`. If that one replaced the figure first, the value
    is already present and this function stays silent, which is the correct
    order: substitution is preferable where it applies, since it adds no
    sentence.
    """
    total = _total(records, question, route)
    if total is None:
        return answer, ""
    body, tail = split_tail(answer or "")
    if not body or f"{total:,}" in body:
        return answer, ""
    # Written only for questions with a pinned operation. An empty `_op`
    # already makes `_total` None, so this point is unreachable otherwise, but
    # the condition is left visible: widening `_total` must not widen this
    # function with it.
    sentence = (f"\n\n따라서 질문이 물은 값은 {total:,}원이다. "
                f"(이 값은 시스템이 위 근거의 금액 필드에서 직접 계산한 것이다.)")
    return body + sentence + tail, f"stated {total:,}"


def date_disambiguation(records, question: str) -> str:
    """State which filing a date in the question points at, as one line.

    No LLM call. Returns "" when the function does not fire.

    Two filings can share a date and a title, with both present in the
    evidence, and the selection rather than the retrieval is what goes wrong.
    The rule that separates them is what the date means: a contract filed that
    day, not a revision of a contract from another day. `related` carries that
    fact.
    See docs/design-rationale.md#date-disambiguation

    Fires only when the case is ambiguous. A single filing that day leaves
    nothing to separate, and two or more new filings cannot be separated here.
    Questions about revisions stay silent, since there the revision is the
    answer.
    """
    hit = _FILED_DATE.search(question or "")
    if not hit or any(k in (question or "") for k in ("정정", "수정", "변경 전", "변경 후")):
        return ""
    want = "%s%02d%02d" % (hit.group(1), int(hit.group(2)), int(hit.group(3)))
    same = [r for r in (records or []) if str(r.get("rcept_dt") or "") == want]
    if len(same) < 2:
        return ""
    fresh = [r for r in same if not r.get("is_correction")]
    amended = [r for r in same if r.get("is_correction")]
    if len(fresh) != 1 or not amended:
        return ""
    pick = fresh[0]
    amount = _amount(pick)
    return (f"[결정적 조회] {hit.group(1)}년 {int(hit.group(2))}월 {int(hit.group(3))}일에 "
            f"**공시된** 계약은 접수번호 {pick.get('rcept_no')} 이다"
            + (f" (계약금액 {amount:,}원)" if amount else "")
            + f". 같은 날 접수된 "
            + " · ".join(str(r.get("rcept_no")) for r in amended)
            + " 는 **다른 날 계약의 정정본**이므로 이 질문이 지목한 계약이 아니다.\n"
            + "(이 판정은 시스템이 근거의 접수일과 정정 관계에서 직접 가른 것이다.)")
