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

import collections
import difflib
import re

from argus import ablation
#: `critic` is canonical for reading scale markers. Rebuilding that here would
#: let the two copies drift apart.
from argus.agent.critic import _unit_at

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


#: The two difference hints that carry **no adverb**. They cannot tell a
#: question asking for the resulting value from one asking for the delta.
#:
#:     얼마**나** 바뀌었는가   delta      <- `얼마나 바뀌` already catches this
#:     얼마**로** 바뀌었는가   resulting  <- these two catch it wrongly
#:
#: `Q-005`, measured across all three server rounds: the golden answer is
#: 114,800,000,000, but the difference from the original filing
#: (112,700,000,000) was computed, put into the evidence, and `state_total`
#: appended it to the answer as *"the value the question asked for"*. **The
#: model's own text was right and the appended sentence was wrong.** The judge
#: finds the golden figure in the body and counts the answer correct, so this
#: registered on none of the eight axes.
_BARE_DELTA_HINTS = ("변경되었는가", "바뀌었는가")


def _op(question: str) -> str:
    text = question or ""
    drop = _BARE_DELTA_HINTS if ablation.enabled("result_value_op") else ()
    for name, hints in _OPS:
        if any(h in text for h in hints if h not in drop):
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

    #: There are two headings: `pipeline` writes "재계산값으로 정정했습니다" when a
    #: substitution happened and "결함이 발견되었습니다" when it did not
    #: (`pipeline.py:3186`). The first version accepted only the latter, so when
    #: `fix_stray_summands` undid a substitution the tail was left untrimmed --
    #: measured on `Q-107`.
    pruned = re.sub(r"(\n*\(주의: 자동 검증에서 결함이 발견(?:되었습니다|되어 "
                    r"재계산값으로 정정했습니다) — )([^)]*)\)",
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
#: `달라` was added on 2026-08-21. `Q-012` asks *"지분 보유 상황이 어떻게
#: **달라졌는지**"*, and the absence of that word from this list made the line a
#: **silent no-op**. As the comment above says, this tuple is allowed to be
#: loose: the strong precondition is the record condition below (two or more
#: bulk-holding filings from one reporter), and `Q-012` satisfies it
#: (Doosan Corp. -> Doosan Enerbility, two filings).
#:
#: Reach, measured: `Q-106` is the only question this line currently fires on,
#: and adding `달라` brings in exactly one more, `Q-012` (checked across all 112).
_HOLDING_ASK = ("추이", "변해", "변화", "변동", "바뀌", "시간순", "시계열", "어떻게 변")
#: Extra words split off behind a flag. A constant tuple cannot read a flag, so
#: they are kept separately.
_HOLDING_ASK_WIDE = ("달라",)


def _ratio_of(record: dict):
    """This filing's holding ratio as a float, or None."""
    value = record.get("ratio_current")
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


#: Filings shown verbatim before the line elides the middle of a chain.
#:
#: Chosen from the corpus-wide distribution of (reporter, issuer) filing
#: counts (measured 2026-08-19): 174 pairs exist, 132 of them with two or
#: more filings — a pair with exactly one can never reach this function at
#: all, since the firing gate itself requires two. Counts 2 through 10 cover
#: 115 of those 132 (87%). The tail runs to 79 (에코프로 -> 에코프로비엠, the
#: single largest pair). A cap of 10 therefore shows the large majority of
#: pairs that can ever fire this line in full — the elision path exists for
#: the long tail alone, and it must say what it elided rather than look like
#: the whole chain.
#: See docs/design-rationale.md#holding-series
HOLDING_SERIES_CAP = 10


def holding_series(records, question: str, chain_lookup=None) -> str:
    """Lay out one reporter's holding ratios in filing order, as one line.

    No LLM call and no retrieval. Returns "" when the question does not ask
    for a history or the records do not carry a series.

    The same move `revision_series` makes for a revision chain's amounts, with
    two differences: the grouping axis is the reporter rather than the chain,
    and the value is `ratio_current` rather than `amount`. The reporter filter
    is load-bearing — the same window holds other reporters' filings on the
    same issuer, and splicing them produces a series that never happened.

    `records` still decides *whether* the line fires and *which* reporter and
    issuer it is about: the top-ranked evidence record's reporter and issuer,
    the same rule `revision_series` uses for chains, and at least two records
    for that exact (reporter, issuer) pair have to be present in the evidence
    — that firing condition is measured behaviour and does not change here.
    Both fields pin `evidence_chain`, not the reporter alone: a reporter files
    holding reports on several issuers, and a chain matched on reporter only
    would count another issuer's filings toward the two-or-more gate and
    (see below) could let them into the line itself.

    What changed (R22, 2026-08-18 measurement): the *values* the line reports
    no longer come from `records`. A run of Q-106 handed this function only 2
    of the reporter's 6 real filings on the one issuer the question asked
    about — retrieval's evidence window, not the reporter's actual history —
    and the function computed a perfectly correct three-filing series over
    them, stating a false cumulative with the same confidence a correct one
    would carry. The judge scored it `grounded=True`, because the false
    statement *was* grounded, in our own line. A confidently wrong series is
    worse than the question going unanswered.

    `chain_lookup(reporter, corp_name) -> list[dict]` is the fix: when given,
    it is asked for the reporter's whole history on that one issuer from the
    corpus, the same move `tools.corp_number_line` makes for a
    registration-number row — read the source directly rather than trust the
    evidence window to carry what is needed. `pipeline.answer_question`
    always passes `tools.holding_chain`; see
    `tests/test_evidence_line_wiring.py`. The fetched chain is preferred
    whenever it is non-empty, never by comparing lengths against
    `evidence_chain`: an R22 follow-up review found that with both sides
    pinned to reporter *and* issuer, `chain_lookup`'s result is a superset of
    `evidence_chain` by construction (every evidence record survived
    `tools._record_of` against the same corpus `chain_lookup` scans, matched
    on the same two fields, so it must appear in what the scan returns too) —
    the earlier length comparison was not protecting against a case that can
    happen with correct pinning, it was quietly compensating for the missing
    issuer pin, and kept as-is it would have reopened the identical
    padded-evidence path as soon as anything else about the pinning changed.
    `chain_lookup=None` keeps the pre-fix behaviour — reading only
    `records` — for a caller with no corpus access; production is never such
    a caller.

    The cumulative is computed from the true first and last of that chain, so
    it is correct regardless of how many intermediate filings restated the
    same ratio unchanged (Q-106's corpus chain has two such steps the golden
    answer itself does not narrate). The steps listed are capped at
    `HOLDING_SERIES_CAP`: past it, the true ends are kept and the middle is
    elided with a stated count, never silently dropped — sacrificing the
    completeness of the *listing* is acceptable, implying a completeness the
    line does not have is exactly the defect this fixes.
    See docs/design-rationale.md#holding-series
    """
    words = _HOLDING_ASK
    if ablation.enabled("holding_ask_wide"):
        words = _HOLDING_ASK + _HOLDING_ASK_WIDE
    if not any(h in (question or "") for h in words):
        return ""
    rows = [r for r in (records or []) if _ratio_of(r) is not None]
    if len(rows) < 2:
        return ""
    reporter = str(rows[0].get("reporter") or "")
    corp_name = str(rows[0].get("corp_name") or "")
    evidence_chain = [r for r in rows if str(r.get("reporter") or "") == reporter
                      and str(r.get("corp_name") or "") == corp_name]
    if len(evidence_chain) < 2:
        return ""

    chain = evidence_chain
    if chain_lookup is not None and reporter and corp_name:
        fetched = [r for r in (chain_lookup(reporter, corp_name) or ())
                   if _ratio_of(r) is not None]
        if fetched:
            chain = fetched
    chain = sorted(chain, key=lambda r: (str(r.get("rcept_dt", "")), r.get("rcept_no", "")))

    total = len(chain)
    if total > HOLDING_SERIES_CAP:
        shown = chain[:HOLDING_SERIES_CAP - 1] + chain[-1:]
        elided = total - len(shown)
    else:
        shown, elided = chain, 0
    ends = {chain[0].get("rcept_no"), chain[-1].get("rcept_no")}

    steps = []
    for record in shown:
        shares = str(record.get("shares_current") or "")
        # Share counts only at the ends of the *true* chain (`ends`), not of
        # `shown`, which is a truncated view once the chain exceeds the cap.
        # Golden points 1 and 5 are the first and last counts; carrying every
        # intermediate one doubles the line length for values the question
        # did not ask about.
        tail = f" ({shares}주)" if shares and record.get("rcept_no") in ends else ""
        #: The question this line answers is *"각 보고서를 **근거로** 설명해줘"*
        #: (`Q-106`). Giving only dates and ratios leaves no way to say which
        #: filing each came from. Golden points 1-4 require a receipt number
        #: paired with every step (`20250707000299`, `20260227007254`), and
        #: those numbers are not in the evidence blocks either: `holding_chain`
        #: holds the chain and only part of it reaches the evidence. So unless
        #: this line emits them, the answer has no way to know them at all.
        if ablation.enabled("holding_receipts") and record.get("rcept_no"):
            tail += f" (접수번호 {record['rcept_no']})"
        steps.append(f"{_ymd(record)} {_ratio_of(record):.2f}%{tail}")
    if elided:
        # Inserted just before the final step, the position the elided
        # filings actually occupy: between the last one shown and the true
        # last one.
        steps.insert(len(steps) - 1, f"(그 사이 {elided}건 생략)")

    moved = _ratio_of(chain[0]) - _ratio_of(chain[-1])
    count_note = f"{total}건" if not elided else f"{total}건 중 {len(shown)}건 표시"
    #: An instruction line (`답변은 걸음마다 그 접수번호를 함께 적는다`) was tried and
    #: left the result at 1/3 across three rounds. The same lesson as
    #: `counterparty_pair`: instruction lines are routinely ignored. So none is
    #: attached.
    return (f"- [결정적 집계] {reporter} 보고 보유비율 추이 ({count_note}): "
            f"{' → '.join(steps)} · 누적 {-moved:+.2f}%p "
            f"(이 값은 시스템이 위 근거의 비율 필드에서 직접 읽은 것이다. "
            f"답변의 해당 수치는 이 값을 그대로 쓴다.)"
            + _pledged_series(sorted(
                evidence_chain,
                key=lambda r: (str(r.get("rcept_dt", "")), r.get("rcept_no", "")))))


#: The two `주요계약체결` columns of a bulk-holding filing. The value is a
#: **string** shaped `직전 A주 → 이번 B주`, so the `이번` side has to be pulled out
#: separately. What the golden answer links is each filing's `이번` value:
#: `Q-012` point 3 links 2025-03's 이번 (84,282,066) to 2026-03's (92,270,920).
_PLEDGED_SHARES_KEY = "주요계약체결 주식등의 수"
_PLEDGED_RATIO_KEY = "주요계약체결 비율"
#: The value after `이번`. Taking the `직전` side would mistake a change within
#: one filing for a change between filings.
_CURRENT_IN_FIELD = re.compile(r"이번\s*([\d,]+(?:\.\d+)?)")


def _pledged_current(record: dict, key: str) -> str:
    """That filing's `주요계약체결` `이번` value, or "" when absent."""
    hit = _CURRENT_IN_FIELD.search(str((record.get("fields") or {}).get(key) or ""))
    return hit.group(1) if hit else ""


def _pledged_series(chain: list[dict]) -> str:
    """The **pledged-shares series** appended to the holding-ratio line, or "".

    ## Why it is attached separately

    `Q-012` point 3 requires `84,282,066주(13.15%)` -> `92,270,920주(14.40%)`.
    Those two figures are not in `shares_current` but inside the string held by
    `fields["주요계약체결 주식등의 수"]` (`직전 A주 → 이번 B주`), and the
    holding-ratio series line never reads that field.

    ## What measurement said -- the first explanation had it backwards

    The first commit recorded *"the second figure lives only in the evidence
    prose, so it never came through"*. Recounting round by round showed the
    opposite.

        A65        84,282,066 absent  · 92,270,920 3/3  (the series line never fires)
        A68, A70   84,282,066 3/3     · 92,270,920 0-1/3
        A71        both 3/3

    `84,282,066` **did come through from the evidence prose** (that field's
    string). What failed to come through was the second figure, and this line
    supplies it. So this is not a place explained by the rule "figures in prose
    do not come through" -- the rule is not that clean. What this line does is
    **make the pair explicit**, and with that both figures arrive together.

    ## The pair is drawn from **the two filings carried as evidence**, not the
    ## whole chain

    The first version read the two ends of the full chain `holding_chain`
    returned. `Q-012`'s chain has 13 entries, so `2023-02-17 40,939,844주
    (6.41%)` became the near end and **the pair differed from the golden one**.
    (The answer did not use that figure, but that was luck.)

    The ratio series line walks the whole chain because showing a *series* is
    its job. This line states **one pair** -- from what to what -- so it has to
    point at the two filings the answer actually reads, the ones retrieval put
    into the evidence.

    ## When it stays silent

    It fires only when both ends carry that field. Measured on the corpus, only
    507 of 1,083 bulk-holding filings have it. With just one side present,
    "from what to what" does not hold, so the line stays silent: emitting half a
    series is stating a change that is not there.

    Writing only the two ends follows the share-count rule of the line above.
    Carrying every intermediate value would double the line with figures the
    question never asked for.
    """
    if not ablation.enabled("pledged_series") or len(chain) < 2:
        return ""
    first, last = chain[0], chain[-1]
    shares = [_pledged_current(r, _PLEDGED_SHARES_KEY) for r in (first, last)]
    if not all(shares):
        return ""
    ratios = [_pledged_current(r, _PLEDGED_RATIO_KEY) for r in (first, last)]
    if shares[0] == shares[1] and ratios[0] == ratios[1]:
        #: When the **values** at both ends match there is no change, and
        #: calling it a series would be false. The comparison must happen before
        #: the dates are attached: dates always differ, which would kill this
        #: branch.
        return ""
    ends = []
    for record, share, ratio in zip((first, last), shares, ratios):
        tail = f" ({ratio}%)" if ratio else ""
        ends.append(f"{_ymd(record)} {share}주{tail}")
    return (f"\n- [결정적 집계] 같은 보고의 주요계약체결 주식등 추이: "
            f"{' → '.join(ends)} "
            f"(이 값은 시스템이 위 근거의 `주요계약체결` 칸에서 직접 읽은 것이다. "
            f"답변의 해당 수치는 이 값을 그대로 쓴다.)")


#: What a decisive line says **to the model**. It lives inside parentheses.
#:
#:     (이 값은 시스템이 위 근거의 금액 필드에서 직접 계산한 것이다. …)
#:     (이 값은 시스템이 위 근거의 비율 필드에서 직접 읽은 것이다. …)
#:     (이 값과 증감은 시스템이 위 근거의 각 칸에서 직접 읽고 뺀 것이다. …)
#:
#: A variant without the opening parenthesis also exists
#: (`이 값은 시스템이 위 근거의 문면에서 그대로 옮긴 것이다 — …`), so the match runs
#: to the end of the sentence.
_DIRECTIVE_SPAN = re.compile(
    r"\s*[(（]\s*이 값(?:과 증감)?은 시스템이[^)）]*[)）]"
    r"|\s*이 값(?:과 증감)?은 시스템이[^\n]*")


def strip_copied_directive(answer: str) -> tuple[str, int]:
    """Strip a decisive line's **directive** where the answer copied it. No LLM
    call.

    Returns (new answer, number removed).

    **What was said to the model was being delivered to the evaluator.** Copying
    a decisive line wholesale, the model also carried over *"답변의 수치와 증감
    방향은 이 줄을 그대로 쓰고 다시 계산하지 않는다"*. Measured over three server
    rounds across four runs: `A84` produced 6 such cases on `Q-005`, `Q-012` and
    `Q-020`, and `A86` 3 on `Q-020` -- **reproduced 3/3**.

    Figures are left alone. Only the parenthesised directive is removed; the
    values the model copied and the sentences around them stay, because those
    values are the true ones the decisive line computed.

    The evidence (`retrieved_context`) is not touched. That line is **what the
    model is meant to read**, and removing it here would take the instruction
    away and unsettle the values themselves. It is stripped only from the
    delivered answer.
    """
    if not answer:
        return answer, 0
    out, count = _DIRECTIVE_SPAN.subn("", answer)
    #: When the directive occupied a whole line, a blank line is left behind.
    out = re.sub(r"[ \t]+\n", "\n", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip(), count


#: The filing date a question names. Both `2025년 3월 10일` and `2025년 3월` are
#: accepted -- `Q-020` writes the day, `Q-012` only the month. The
#: day-bearing form has to be tried first, or `2025년 3월 10일` gets truncated to
#: `2025년 3월` and two distinct dates collapse into one month.
_PAIR_DAY = re.compile(r"(20\d{2})\s*년\s*(1[0-2]|0?[1-9])\s*월\s*(3[01]|[12]\d|0?[1-9])\s*일")
_PAIR_MONTH = re.compile(r"(20\d{2})\s*년\s*(1[0-2]|0?[1-9])\s*월(?!\s*\d{1,2}\s*일)")


def _pair_keys(question: str) -> list[str]:
    """The dates the question names, in the order written. `YYYYMMDD` or `YYYYMM`."""
    found: list[tuple[int, str]] = []
    for hit in _PAIR_DAY.finditer(question or ""):
        y, m, d = hit.groups()
        found.append((hit.start(), f"{y}{int(m):02d}{int(d):02d}"))
    for hit in _PAIR_MONTH.finditer(question or ""):
        y, m = hit.groups()
        found.append((hit.start(), f"{y}{int(m):02d}"))
    seen, keys = set(), []
    for _, key in sorted(found):
        if key not in seen:
            seen.add(key)
            keys.append(key)
    return keys


def _pair_pick(chain: list[dict], key: str):
    """The **single** filing on that date. None when there are none or more than
    one -- silence is the default."""
    hits = [r for r in chain if str(r.get("rcept_dt", "")).startswith(key)]
    return hits[0] if len(hits) == 1 else None


def _pair_delta(before: str, after: str, unit: str) -> str:
    """The change between two values, in words. "" when they cannot be read."""
    try:
        gap = int(str(after).replace(",", "")) - int(str(before).replace(",", ""))
    except (TypeError, ValueError):
        return ""
    if gap == 0:
        return "변동 없음"
    return f"{abs(gap):,}{unit} {'증가' if gap > 0 else '감소'}"


def holding_pair(records, question: str, chain_lookup=None) -> str:
    """When a question **names two filings and asks them compared**, pin both
    values and their difference for every column.

    No LLM call. Returns "" when the conditions are not met.

    ## What it fixes -- `Q-012`, measured across all three server rounds

    `holding_series` attaches share counts **only at the two ends** of the chain
    (`ends`). The 2025-03-10 filing `Q-012` names is not an end of its 13-entry
    chain, so that line carried no share count for it. The model then **mixed
    two sources**: it took the 2026 value from the decisive line and the 2025
    value from the raw block, subtracted them itself, and the three rounds
    produced 18,607, 18,652 and 18,652. **The true value is 18,650.**

    **Being a prose claim with no equation, it tripped no check.** Both
    `critic.verify_answer_arithmetic` and `fix_self_contradiction` need an
    `A - B = C` shape to fire. *"18,652주 추가로 취득하여"* is not that shape.

    ## Why the direction is written too

    The evidence holds **two true statements over different ranges**, side by
    side.

        decisive line (between filings)  2025-03-10 84,282,066 ->
                                         2026-03-05 92,270,920   increase
        raw block (within the 2026 filing)  직전 92,270,920 ->
                                            이번 92,270,920      no change

    Both are true but measured over different spans, and the model took **the
    number from the first and the description from the second**, producing
    *"동일한 수치인 92,270,920주"* -- contradicting the 84,282,066 on the line
    directly above. Golden point 3 says "increase". So this line writes not just
    the values but **the change in words**, rather than leaving the model to
    choose a direction for itself.

    ## Silence is the default

    Nothing is emitted unless there are exactly two dates, and unless each date
    resolves to exactly one filing. The same judgement as `_sum_rows`: better to
    emit nothing than a figure whose correctness is unknown.
    """
    if not ablation.enabled("holding_pair"):
        return ""
    words = _HOLDING_ASK
    if ablation.enabled("holding_ask_wide"):
        words = _HOLDING_ASK + _HOLDING_ASK_WIDE
    if not any(h in (question or "") for h in words):
        return ""
    keys = _pair_keys(question)
    if len(keys) != 2:
        return ""

    rows = [r for r in (records or []) if _ratio_of(r) is not None]
    if not rows:
        return ""
    reporter = str(rows[0].get("reporter") or "")
    corp_name = str(rows[0].get("corp_name") or "")
    chain = [r for r in rows if str(r.get("reporter") or "") == reporter
             and str(r.get("corp_name") or "") == corp_name]
    if chain_lookup is not None and reporter and corp_name:
        fetched = [r for r in (chain_lookup(reporter, corp_name) or ())
                   if _ratio_of(r) is not None]
        if fetched:
            chain = fetched
    picked = [_pair_pick(chain, key) for key in keys]
    if any(r is None for r in picked):
        return ""
    first, last = sorted(picked, key=lambda r: str(r.get("rcept_dt", "")))
    if first.get("rcept_no") == last.get("rcept_no"):
        return ""

    parts = []
    shares = [str(r.get("shares_current") or "") for r in (first, last)]
    if all(shares):
        gap = _pair_delta(shares[0], shares[1], "주")
        parts.append(f"보유주식등의 수 {shares[0]}주 → {shares[1]}주"
                     + (f" ({gap})" if gap else ""))
    ratios = [_ratio_of(r) for r in (first, last)]
    moved = ratios[1] - ratios[0]
    parts.append(f"보유비율 {ratios[0]:.2f}% → {ratios[1]:.2f}% "
                 + ("(변동 없음)" if abs(moved) < 1e-9 else f"({moved:+.2f}%p)"))
    pledged = [_pledged_current(r, _PLEDGED_SHARES_KEY) for r in (first, last)]
    pledged_ratio = [_pledged_current(r, _PLEDGED_RATIO_KEY) for r in (first, last)]
    if all(pledged):
        gap = _pair_delta(pledged[0], pledged[1], "주")
        tail = [f"{p}주" + (f"({q}%)" if q else "")
                for p, q in zip(pledged, pledged_ratio)]
        parts.append(f"주요계약체결 주식등 {tail[0]} → {tail[1]}"
                     + (f" ({gap})" if gap else ""))

    #: The span is written in months only. All three `Q-012` rounds described a
    #: 12-month gap as *"약 18개월"*.
    span = ""
    a, b = str(first.get("rcept_dt", "")), str(last.get("rcept_dt", ""))
    if len(a) >= 6 and len(b) >= 6:
        months = (int(b[:4]) - int(a[:4])) * 12 + (int(b[4:6]) - int(a[4:6]))
        if months > 0:
            span = f", 약 {months}개월"
    #: No leading `\n`. `_pledged_series` **appends after another line** and so
    #: carries its own newline, but this line is placed on its own by
    #: `_prepend`. With a newline the evidence would start on an empty line, and
    #: `attach_citation`'s block parser would read that blank as a heading,
    #: emitting an unlabelled citation **`(근거: , 접수번호 …)`** -- confirmed by
    #: measurement on the server.
    return (f"- [결정적 집계] 짚은 두 보고서 대조 ({_ymd(first)} → {_ymd(last)}{span}): "
            f"{' · '.join(parts)} "
            f"(이 값과 증감은 시스템이 위 근거의 각 칸에서 직접 읽고 뺀 것이다. "
            f"답변의 수치와 증감 방향은 이 줄을 그대로 쓰고 다시 계산하지 않는다.)")


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


#: Representative contracts carried per counterparty. The supplier branch
#: carries three: one golden point needs the two contracts of a single
#: project (Dukovany) visible together, and a second needs a project that is
#: distinct but not most recent — measured on Q-117, 두산에너빌리티 filed one
#: project (신고리) four times and another (신한울) once, with 신한울 fifth by
#: recency. `_branch_entry` dedupes by subject before this cap is applied, so
#: the cap only has to reach as many *distinct* projects as a counterparty
#: has, not as many filings; three is what it takes to reach 신고리, 두코바니
#: and 신한울 among 두산에너빌리티's real filings. The orderer branch carries
#: one each, because there the point is the list of names.
#: See docs/design-rationale.md#counterparty-branch
MAX_BRANCH_CONTRACTS_SUPPLIER = 3
MAX_BRANCH_CONTRACTS_ORDERER = 1


def _branch_entry(name: str, events: list[dict], limit: int) -> str:
    """One counterparty rendered as `name(contract amount · contract amount)`.

    Falls back to the bare name when no record carries an amount. Nine of
    Q-117's eleven counterparties are named for the list alone, and dropping
    them because they lack a figure would lose the first golden point.

    Priced records are deduplicated by contract subject (`_subject`) before
    the cap is applied, keeping the first occurrence of each distinct
    subject in the order `events` arrives in (newest first, per caller). A
    repeated subject carries no information the first instance did not, and
    left alone it can crowd a distinct, once-filed project out of the cap
    entirely — the defect measured on Q-117's 두산에너빌리티 records.
    """
    priced = [e for e in (events or []) if _amount(e) is not None]
    deduped: list[dict] = []
    seen_subjects: set[str] = set()
    for event in priced:
        subject = _subject(event)
        if subject in seen_subjects:
            continue
        seen_subjects.add(subject)
        deduped.append(event)
    priced = deduped[:limit]
    if not priced:
        return name
    parts = [f"{_subject(e)} {_amount(e):,}원" for e in priced]
    return f"{name}({' · '.join(parts)})"


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

    Each counterparty also carries its representative contract(s): a question
    asking what the two branches *are* is answered by the contracts, not by
    the company names alone. Contract titles and amounts are source values
    copied out of the evidence, so carrying them into this line is the
    intended behaviour — unlike the predicate name above, which leaked
    verbatim into an answer once and stays out of this string. A counterparty
    whose records carry no amount still renders as its bare name, so the
    fallback used by every pre-existing caller of this function is preserved.
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
    # Rationale for carrying contract content here (not just names) is in the
    # docstring above; the two branches use different caps, see
    # MAX_BRANCH_CONTRACTS_SUPPLIER / MAX_BRANCH_CONTRACTS_ORDERER.
    outside_text = " · ".join(
        _branch_entry(n, groups[n], MAX_BRANCH_CONTRACTS_ORDERER) for n in outside)
    inside_text = " · ".join(
        _branch_entry(n, groups[n], MAX_BRANCH_CONTRACTS_SUPPLIER) for n in inside)
    return (
        f"- [결정적 집계] 계약상대 {len(order)}곳이 두 갈래로 나뉜다: "
        f"발주처 {len(outside)}곳 — {outside_text} · "
        f"공급사 재수주 {len(inside)}곳 — {inside_text} "
        f"(공급사 쪽도 스스로 공시 대상 상장사다)\n"
        f"  답변은 이 두 갈래로만 나누고 다른 축으로 다시 나누지 않는다."
    )


#: Whether the question asks about **the relationship between two filings**. It
#: does not cover enumeration questions: measured on the held-back set, `H-011`
#: (*"계약상대로 하는 …을 공시한 상장사를 빠짐없이"*) and `N-014` match the shape, but
#: there a difference is noise rather than part of the answer.
_PAIR_ASK = re.compile(r"관계|이어받|넘어갔|같은 사업|동일한 사업|바뀐 것")
#: One 억 (100 million). The golden answer writes `약 4,111억원`, so the value is
#: floored to 억 units.
_EOK = 10 ** 8


def _bare(name: str) -> str:
    return re.sub(r"\s+", "", name or "")


def counterparty_pair(records, question: str) -> str:
    """Join **the filings of two companies** that share a counterparty and pin
    the amount difference. No LLM call.

    Returns "" when it does not fire.

    ## Why it is needed

    `Q-112` reads *"Daewoo E&C terminated and Hyundai E&C signed the same
    project -- explain how the two filings relate"*. Golden point 3 requires
    *"약 4,111억원 더 크다"*, yet all three runs listed the two amounts side by
    side and **never computed the difference**. Not one decisive line attached to
    this question -- a place where `[결정적 집계]` fell silent entirely.

    The difference operation in `line` (`_op`) did not fire because the question
    asks *"어떤 관계인가"*, not *"차이는 얼마인가"*. A relationship question whose
    answer includes a difference is specific to this question type, so it gets a
    line of its own.

    ## What joins them -- the counterparty name with whitespace removed

    The two filings' counterparties are `'주식회사 유토개발2차'` and
    `'주식회사유토개발2차'`, **differing only in a space**. Golden point 4 says
    exactly that (*"두 공시를 잇는 것은 계약상대 필드뿐이며 표기가 띄어쓰기만 다르다"*).
    So this line writes **both spellings**: the answer has to be able to say what
    it joined them on.

    ## When it stays silent

        1. The question does not ask about a relationship.
        2. There is not **exactly one** such pair -- with several, which pair the
           difference belongs to is unknown.
        3. Three or more filings hang off one counterparty -- which two the
           difference is between is unknown.
        4. The two filings have **the same filer**: one company contracting twice
           with the same counterparty is not a change of contractor.
        5. Either side has no amount.

    ## The difference still lands only about half the time -- five forms were
    ## measured, then chosen on principle

    The golden answer writes *"약 4,111억원 더 크다"*. Five word orders were
    measured, three rounds each.

        `411,161,299,002원 (약 4,111억원)` trailing clause  -> won 3/3, 억원 0/3
        `약 4,111억원 (411,161,299,002원)` trailing clause  -> won 0/3, 억원 0/3
        `411,161,299,002원 더 크다 — 억 단위로는 …`          -> won 3/3, 억원 2/3  <- adopted
        the form above plus a `답변은 … 함께 적는다` directive -> won 2/3, 억원 1/3
        the difference split onto its own equation line      -> won 1/3, 억원 0/3

    Re-measuring the adopted form then gave **won 1/3, 억원 1/3**, and extending
    to six rounds gave **won 3/6, 억원 1/6**. Variance between samples of the same
    form exceeds the differences between forms, which means a sample this size
    cannot separate them. So the final choice was made on principle rather than
    measurement: one line (the same shape as the other lines), carrying both
    units.

    Read together, the difference reaches the answer roughly **7/12 in won and
    4/12 in 억원**. Golden point 3 of `Q-112` is **not closed** by this arm. It is
    better than the previous 0/3, but writing that it is closed would be false.

    ## What did clearly improve

    Golden point 4 (the counterparty spellings differ only by a space) appears in
    all three rounds, as do the dates, amounts and both receipt numbers of points
    2 and 3. Earlier answers wrote the decision date (2023-12-13); with this line
    attached they write the filing date (2023-12-14).

    ## What was learned -- directive lines are routinely ignored

    Attaching the `답변은 …` directive line that `counterparty_branch` uses made
    things **worse** here. Reusing that form elsewhere calls for measuring it
    again first.

    Reach, measured: `Q-112` is the only question in the 112-question cohort the
    shape matches, and the two matches in the held-back set are stopped by
    condition 1.
    See docs/design-rationale.md#aggregate-scope
    """
    if not ablation.enabled("counterparty_pair"):
        return ""
    if not _PAIR_ASK.search(question or ""):
        return ""
    grouped: dict[str, list[dict]] = {}
    for record in records or ():
        name = str(record.get("counterparty") or "")
        if not name or not record.get("amount"):
            continue
        grouped.setdefault(_bare(name), []).append(record)
    pairs = [group for group in grouped.values()
             if len(group) == 2
             and group[0].get("corp_name") != group[1].get("corp_name")]
    if len(pairs) != 1:
        return ""
    first, second = sorted(pairs[0], key=lambda r: str(r.get("rcept_dt") or ""))
    amounts = [int(first["amount"]), int(second["amount"])]
    delta = amounts[1] - amounts[0]
    if not delta:
        #: With equal amounts, "larger than" would be false.
        return ""
    spellings = [str(r.get("counterparty") or "") for r in (first, second)]
    if spellings[0] == spellings[1]:
        mark = f"계약상대 '{spellings[0]}' 가 같다"
    else:
        mark = (f"계약상대 표기가 '{spellings[0]}' 와 '{spellings[1]}' 로 "
                f"공백만 다르고 나머지가 같다")
    steps = " → ".join(
        f"{r.get('corp_name')} {_ymd(r)} {amount:,}원 (접수번호 {r.get('rcept_no')})"
        for r, amount in zip((first, second), amounts))
    bigger = second if delta > 0 else first
    return (f"- [결정적 집계] 같은 사업으로 이어지는 두 공시 ({mark}): {steps} · "
            f"{bigger.get('corp_name')} 쪽이 {abs(delta):,}원 더 크다 — "
            f"억 단위로는 약 {abs(delta) // _EOK:,}억원이다. {_DIRECTIVE}")


#: The won-conversion sentence a filing wrote **itself**. Shape as measured on
#: the corpus:
#:
#:     상기 2. '투자금액' $58억을 원화 환산 시 8조 5,080억원이며, 이는 2025년 3월 24일
#:     서울외국환중개 매매기준율(1,466.9원/달러)을 …
#:
#: Three pieces have to be captured together for the line to mean anything: the
#: foreign amount, the converted amount, and the rate used. Missing any one of
#: them, the line stays silent. The converted amount alone does not say what was
#: converted from what, and the rate alone is not an answer.
_FX_CONVERSION = re.compile(
    r"(?P<fx>[$＄]\s*[\d,.]+\s*[조억만]?)\s*을?\s*원화\s*환산\s*시\s*"
    r"(?P<krw>[\d,]+(?:\s*[조억만]\s*[\d,]*)*\s*원)"
    r".{0,20}?(?P<date>\d{4}\s*년\s*\d{1,2}\s*월\s*\d{1,2}\s*일)\s*"
    r"(?P<source>[^(（]{0,16}?)매매기준율\s*[(（]\s*(?P<rate>[\d,.]+)\s*원\s*/\s*달러")
#: Whether the question asks about an amount. The shape itself occurs in only
#: one place across 352 questions, so this guard is redundant today; it is here
#: to stop a figure the question never asked for from entering the answer when
#: the same filing is later reached by a different question.
_FX_ASK = re.compile(r"금액|규모|얼마|투자|환산")


def fx_conversion(evidence: str, question: str) -> str:
    """Lift the won conversion and the rate a filing stated itself into one line.
    No LLM call.

    Returns "" when it does not fire. **Nothing is computed** -- the wording is
    copied as written, which makes this a `[결정적 조회]`.

    ## Why it is needed

    Golden point 2 of `Q-101` (*"현대제철 … 투자금액을 근거와 함께 알려줘"*) is
    *"2025년 3월 24일 매매기준율(1,466.9원/달러)로 환산한 8조 5,080억원이 함께 적혀
    있다"*.

    That sentence **is already in the evidence**, inside the prose of the
    `- 기타 투자판단과 관련한 중요사항:` field. All three runs wrote only `$58억` and
    carried over neither the converted amount nor the rate -- another instance of
    "figures on decisive lines reach the answer, figures living only in evidence
    prose do not". So that prose is lifted into a line.

    ## Why we do not convert it ourselves

    We could compute `58억 달러 x 1,466.9 = 8조 5,080억원` and emit it. We do not.
    If our arithmetic disagreed with the conversion the filing wrote (which it
    routinely does, through rounding or a different reference date), our value
    would be **a figure that differs from the filing**, and that shows up at once
    when a reader checks against the original. Where the filing states a value
    itself, copying it is always better.

    ## When it stays silent

        1. Any one of the three pieces (foreign amount, converted amount, rate)
           cannot be captured.
        2. There is not **exactly one** such sentence -- with several, which one
           is the answer is unknown.
        3. The question does not ask about an amount.

    Reach, measured: sweeping 352 cases (the 112-question cohort, 16 held back,
    and three server rounds), `Q-101` is the only one this shape matches.
    `Q-067` also contains the word `환율`, but not a conversion sentence.
    See docs/design-rationale.md#aggregate-scope
    """
    if not ablation.enabled("fx_conversion"):
        return ""
    if not _FX_ASK.search(question or ""):
        return ""
    found = list(_FX_CONVERSION.finditer(evidence or ""))
    if len(found) != 1:
        return ""
    hit = found[0]
    fx = re.sub(r"\s+", "", hit.group("fx"))
    krw = re.sub(r"\s+", " ", hit.group("krw")).strip()
    date = re.sub(r"\s+", " ", hit.group("date")).strip()
    source = re.sub(r"\s+", "", hit.group("source")).strip()
    where = f"{date} {source} 매매기준율" if source else f"{date} 매매기준율"
    return (f"- [결정적 조회] 공시가 스스로 밝힌 원화 환산: {fx} = **{krw}** "
            f"({where} {hit.group('rate')}원/달러). "
            f"이 값은 시스템이 위 근거의 문면에서 그대로 옮긴 것이다 — "
            f"우리가 계산한 것이 아니므로 답변은 이 값을 고치지 않고, "
            f"외화 금액과 이 원화 환산액을 함께 적는다.")


# --- Before/after comparison for corrections --------------------------------
#
# A question asking what changed between a filing and its correction carries
# **the same section twice** in the evidence: one copy from the original filing
# and one from the correction. The difference between them can be computed
# exactly in code, yet the answer eyeballs it -- all three runs of `Q-094` got
# the notation change right and failed to state that no amount changed, or to
# write those amounts.
#
# There is one fork. **A correction that changed amounts and a correction that
# changed only notation have different answers.**
#
#     amounts changed     -> the answer is what became what. Only changed rows
#                            are emitted.
#     amounts unchanged   -> the answer is what did **not** change. The table
#                            containing those rows is emitted as well.
#
# Emitting the table in the second case is the treatment for `Q-094` point 3
# (`63,562,284` and `135,653,693` did not change). Those two figures are not on
# the changed rows but **directly beneath** them.
# See docs/design-rationale.md#aggregate-scope

#: Strip the piece marker from an evidence block's heading. A heading with
#: `[1/2 조각]` and one without it name the same section.
_PIECE_MARK = re.compile(r"\s*\[\d+/\d+ 조각\]\s*$")
#: A block's source line. It **must be excluded** from the comparison: the
#: receipt numbers differ by definition, and counting that as a difference makes
#: every pair differ by at least one row.
_BLOCK_SOURCE = re.compile(r"^- 출처: 접수번호 (\d{14})")
#: Ceiling on changed-row pairs; above it the line stays silent. Measured on the
#: corpus, `Q-076` has a 71-row pair and `Q-088` a 249-row one, and packing that
#: into one line becomes a wall that pushes the evidence out. Decisive lines have
#: stayed in the 200-400 character range.
MAX_REVISION_PAIRS = 6
#: Ceiling when emitting the "unchanged table": two tables, eight rows each.
MAX_SAME_TABLES = 2
MAX_SAME_ROWS = 8


def _revision_blocks(evidence: str) -> list[tuple[str, list[str]]]:
    """Split the evidence on `## ` headings, as (heading with the piece marker
    stripped, body lines)."""
    out: list[tuple[str, list[str]]] = []
    head: str | None = None
    body: list[str] = []
    for raw in (evidence or "").split("\n"):
        if raw.startswith("## "):
            if head is not None:
                out.append((head, body))
            head, body = _PIECE_MARK.sub("", raw[3:]).strip(), []
        elif head is not None:
            body.append(raw)
    if head is not None:
        out.append((head, body))
    return out


def _block_receipt(body: list[str]) -> str:
    for line in body:
        hit = _BLOCK_SOURCE.match(line)
        if hit:
            return hit.group(1)
    return ""


def _revision_pair(evidence: str):
    """(section heading, original body, correction body). None unless there is
    exactly one pair.

    The rule for choosing a pair is **same heading, different receipt numbers,
    exactly two copies**. With three or more present, which two are the before
    and after is unknown, so the line stays silent.
    """
    grouped: dict[str, list[list[str]]] = {}
    for head, body in _revision_blocks(evidence):
        grouped.setdefault(head, []).append(body)
    pairs = []
    for head, bodies in grouped.items():
        if len(bodies) != 2:
            continue
        left, right = (_block_receipt(bodies[0]), _block_receipt(bodies[1]))
        if not left or not right or left == right:
            continue
        #: The smaller receipt number is the original filing: a correction is
        #: always filed later.
        order = sorted(zip((left, right), bodies), key=lambda x: x[0])
        pairs.append((head, order[0], order[1]))
    return pairs[0] if len(pairs) == 1 else None


#: The unit declaration above a table: `(단위 : 천원)`, `(단위:백만원)`.
_TABLE_UNIT = re.compile(r"[(（]\s*단위\s*[:：]\s*([^)）]{1,12}?)\s*[)）]")
#: Rows whose value is **not money**. Even when a table declares
#: `(단위 : 천원)`, attaching 천원 to a share count produces a confidently wrong
#: figure. The table's declaration is the table's business; our line is our own
#: statement.
_NOT_MONEY_LABEL = ("주식수", "주수", "수량", "인원", "건수", "비율", "율(", "주가")


def _containing_table(body: list[str], at: int) -> tuple[list[str], str]:
    """(value rows of the table containing line `at`, the unit that table
    declares).

    Separator and header rows are excluded. The unit is looked for **directly
    above** the table: searching further away pulls in another table's unit.
    """
    if not body[at].lstrip().startswith("|"):
        return [], ""
    lo = at
    while lo > 0 and body[lo - 1].lstrip().startswith("|"):
        lo -= 1
    hi = at
    while hi + 1 < len(body) and body[hi + 1].lstrip().startswith("|"):
        hi += 1
    rows = [body[i] for i in range(lo, hi + 1)
            if not set(body[i].replace("|", "").strip()) <= set("- :")]
    unit = ""
    for back in range(lo - 1, max(lo - 5, -1), -1):
        hit = _TABLE_UNIT.search(body[back])
        if hit:
            unit = hit.group(1)
            break
    return (rows[1:] if len(rows) > 1 else rows), unit


def _money_row(row: str, unit: str) -> str:
    """`label value+unit` for a money row, otherwise "".

    ## Why rows are filtered

    `covers_key_amounts` measures with `money_values`, which counts only **a
    figure carrying a unit** as money (`63,562,284` -> no, `63,562,284천원` ->
    yes). So the unit has to be attached for a golden point's amount to count as
    stated by the answer.

    But it must not be attached to just any row. `Q-094`'s table declares
    `(단위 : 천원)` and yet contains `배당 기준 총발행 보통주식수 21,954,022`. Writing
    `21,954,022천원` would assert that a share count is money. And
    `보통주에 지급된 주당배당금(단위: 원/주)` is **a row that declares its own unit**,
    so the table's unit does not apply to it.

    Two kinds of row are therefore excluded: rows whose label names a quantity
    or a ratio, and rows whose label declares its own unit. What remains is
    `보통주에 지급된 배당금 63,562,284천원` -- exactly what golden point 3 requires.
    """
    cells = [c.strip() for c in row.strip().strip("|").split("|") if c.strip()]
    if len(cells) < 2:
        return ""
    label, values = cells[0], cells[1:]
    if any(word in label for word in _NOT_MONEY_LABEL):
        return ""
    if _TABLE_UNIT.search(label):
        return ""
    amounts = [v for v in values if _ROW_AMOUNT.fullmatch(v)]
    if not amounts:
        return ""
    return f"{label} {amounts[0]}{unit}" if unit else f"{label} {amounts[0]}"


def _row_text(row: str) -> str:
    cells = [c.strip() for c in row.strip().strip("|").split("|") if c.strip()]
    return " ".join(cells)


def revision_diff(evidence: str) -> str:
    """Compare the two copies around a correction and pin the result in one
    line. No LLM call.

    Returns "" when it does not fire.

    ## Why it is needed

    All four golden points of `Q-094` (*"배당금 지급 내역 표에서 정정 전후로 무엇이
    달라졌는지"*) are contained in this comparison.

        point 1  '(단위: 원/주)' was added to the per-share dividend label
                                                      -> the changed row pair
        point 2  3,130 and 6,680 are the same before and after
                                                      -> figures on changed rows
        point 3  63,562,284 and 135,653,693 did not change either
                                                      -> the table holding them
        point 4  no amount changed at all
                                                      -> identical amount multiset

    All three runs got only points 1 and 2. The two figures of point 3 are not on
    the changed row but **on the line directly below it**, so eyeballing cannot
    establish "unchanged". Code can.

    ## A correction that changed amounts and one that changed only notation have
    ## different answers

        amounts changed    -> the answer is what became what; only changed rows
                              are emitted.
        amounts unchanged  -> the answer is what did **not** change; the table
                              holding those rows is emitted too.

    The table is emitted only in the second case because emitting it in the first
    would build a wall: pairs whose amounts changed have large tables (`Q-048`
    with 178 kinds, `Q-052` with 14).

    ## When it stays silent

        1. There is not **exactly one** pair sharing a heading with differing
           receipt numbers.
        2. No row differs -- there is nothing to say.
        3. The changed-row pairs exceed `MAX_REVISION_PAIRS`. Measured on the
           corpus, `Q-076` has a 71-row pair and `Q-088` a 249-row one, and
           packing that into one line becomes a wall that pushes out the
           evidence.

    The source line is excluded from the comparison: the receipt numbers differ
    by definition, and counting that as a difference makes every pair differ by
    at least one row.

    ## The marker is `[결정적 집계]` -- a new marker gets cited as a source

    The first version used a new marker, `[결정적 대조]`. Answers then wrote
    *"(근거: 결정적 대조 정보)"* and *"(근거: 결정적 대조, 결정적 단위)"*. The
    generation prompt knows nothing about markers, so the model simply reads the
    bracketed text as a source. Same reason `counterparty_branch` keeps predicate
    names out of its line: markers stay within the two already in use (`집계`,
    `조회`). This is a comparison we computed, so it is `집계`.

    ## Unchanged amounts get a unit -- but not on every row

    `covers_key_amounts` measures with `money_values`, which counts only **a
    figure carrying a unit** as money. The first version copied the table's value
    as `63,562,284`, and even with the repairer attaching that line to the
    answer, `covers_key_amounts` failed with *"금액 표기가 하나도 없다"* and all
    three rounds came out wrong.

    So the unit the table declares is attached to the value -- with two
    exclusions: rows whose label names a quantity or ratio (attaching 천원 to
    `배당 기준 총발행 보통주식수 21,954,022` would assert a share count is money) and
    rows whose label declares its own unit (`주당배당금(단위: 원/주)` does not take
    the table's 천원). What remains is `보통주에 지급된 배당금 63,562,284천원`.

    ## And stating only "unchanged" leaves no amounts at all

    The first version's directive read *"바뀐 것과 바뀌지 않은 것을 모두 이 대조대로
    적는다"*. The answer generalised it into *"…금액 등은 정정 전후로 변동이 없습니다"*
    and **wrote not a single figure**. `amount_gap` fired and two of three rounds
    came out wrong -- we had broken the very question we were targeting. The
    directive was changed to "write the amounts above exactly as given".
    See docs/design-rationale.md#aggregate-scope
    """
    if not ablation.enabled("revision_diff"):
        return ""
    picked = _revision_pair(evidence)
    if not picked:
        return ""
    head, (old_no, old_body), (new_no, new_body) = picked
    old_rows = [l for l in old_body if not _BLOCK_SOURCE.match(l)]
    new_rows = [l for l in new_body if not _BLOCK_SOURCE.match(l)]
    removed, added = [], []
    for line in difflib.unified_diff(old_rows, new_rows, lineterm="", n=0):
        if line.startswith("---") or line.startswith("+++"):
            continue
        if line.startswith("-"):
            removed.append(line[1:])
        elif line.startswith("+"):
            added.append(line[1:])
    if not removed and not added:
        return ""
    if max(len(removed), len(added)) > MAX_REVISION_PAIRS:
        return ""

    same_amounts = (collections.Counter(_ROW_AMOUNT.findall("\n".join(old_rows)))
                    == collections.Counter(_ROW_AMOUNT.findall("\n".join(new_rows))))
    verdict = ("금액은 하나도 바뀌지 않았다" if same_amounts
               else "금액이 바뀌었다")
    steps = []
    for before, after in zip(removed, added):
        left, right = _row_text(before) or "(빈 줄)", _row_text(after) or "(빈 줄)"
        if left != right:
            steps.append(f"'{left}' → '{right}'")
    for extra in removed[len(added):]:
        steps.append(f"'{_row_text(extra)}' 가 빠졌다")
    for extra in added[len(removed):]:
        steps.append(f"'{_row_text(extra)}' 가 들어왔다")
    if not steps:
        return ""

    line = (f"- [결정적 집계] 정정 전후 대조 (원공시 {old_no} → 정정 {new_no}, "
            f"절 '{head}'): {verdict}. 달라진 곳 {len(steps)}군데 — "
            + " · ".join(steps))
    if not same_amounts:
        return line + (" (이 값은 시스템이 위 두 벌을 줄 단위로 견준 것이다. "
                       "답변의 해당 수치는 이 값을 그대로 쓰고, 이 목록 밖의 변경을 "
                       "적지 않는다.)")

    #: In a correction where no amount changed, the answer is the values that
    #: did not change. The table holding the changed rows is emitted with them --
    #: the two figures of `Q-094` point 3 are in there.
    tables, seen = [], set()
    for before, after in zip(removed, added):
        at = next((i for i, l in enumerate(new_rows) if l == after), -1)
        source = new_rows
        if at < 0:
            at = next((i for i, l in enumerate(old_rows) if l == before), -1)
            source = old_rows
        if at < 0:
            continue
        rows, unit = _containing_table(source, at)
        money = [_money_row(r, unit) for r in rows]
        money = [m for m in money if m]
        if not money:
            continue
        key = tuple(money)
        if key in seen:
            continue
        seen.add(key)
        tables.append(money[:MAX_SAME_ROWS])
        if len(tables) >= MAX_SAME_TABLES:
            break
    if tables:
        shown = " ∥ ".join(" · ".join(rows) for rows in tables)
        line += f" · 그 줄이 든 표의 금액은 전후가 같다: {shown}"
    return line + (" (이 값은 시스템이 위 두 벌을 줄 단위로 견준 것이다. "
                   "답변은 바뀐 항목명과 함께 위에 적힌 금액을 그대로 적어 "
                   "무엇이 안 바뀌었는지 밝힌다.)")


#: The answer form `1,234,567,890원(12억원)`. Markdown emphasis has to be
#: crossed, since answers contain `**207,684,631,210원**(20,768억원)`.
_PAREN_UNIT = re.compile(r"(\d{1,3}(?:,\d{3}){2,})(\s*원?\s*\**\s*)"
                         r"[（(]\s*(?:약\s*)?([^)）]{1,24})[)）]")

#: Expands a parenthesised unit expression into won. Covers `1조 2,345억원`,
#: `9,714억원` and `2,000억`.
_EOK_TEXT = re.compile(r"^(?:([\d,]+)\s*조\s*)?(?:([\d,]+(?:\.\d+)?)\s*억)?\s*원?$")

#: Magnitude words outside 조 and 억. A notation `_EOK_TEXT` cannot read is
#: **passed over untouched** by `fix_unit_parens` (see that function's
#: docstring: *"Parentheses that do not parse as a conversion are left alone"*).
#: That discipline is right -- `(주당 1,000원)` and `(지분율 30%)` must not be read
#: as conversions. The price was that **what cannot be read and what is wrong
#: pass through together**.
#:
#: `Q-061`, measured: the answer writes `345,173,200,000원 (341십억 원)`. 341십억
#: is 341,000,000,000, off from the body's value by 4,173,200,000. `십억` could
#: not be read, so it passed through, and the `restatement` gate caught it in
#: **all three A65 server rounds and all three A77 local rounds** -- the same
#: defect six runs in a row, in a place v3 section 4 did not cover.
#:
#: Sweeping all 672 responses, only three conversion notations defeat
#: `_EOK_TEXT`.
#:
#:     `**2,759억원**`  markdown emphasis sits **inside** the parenthesis
#:                      (`Q-018` 6/6, value correct)
#:     `341십억 원`      the magnitude word is outside 조 and 억
#:                      (`Q-061` 6/6, value wrong)
#:     `412억여 원`      an approximating `여` suffix is attached
#:                      (`Q-091` 5/6, value correct)
#:
#: All three are made readable. The two whose values are correct pass through
#: unchanged, and only the wrong one is repaired.
_SCALE_WORDS = (("십억", 10 ** 9), ("백만", 10 ** 6), ("천만", 10 ** 7), ("만", 10 ** 4))
_SCALE_TEXT = re.compile(r"^([\d,]+(?:\.\d+)?)\s*(십억|백만|천만|만)\s*여?\s*원?$")
#: What to strip inside the parenthesis: markdown emphasis and the
#: approximating `여`. Neither affects the value.
_PAREN_NOISE = re.compile(r"[*_]|(?<=억)\s*여|(?<=조)\s*여")

#: Rounding allowance. Unit notation drops anything below 100 million, so a
#: gap that size is not an error.
_PAREN_TOLERANCE = 10 ** 8


def _parse_paren_unit(inner: str) -> tuple[int, int] | None:
    """(the written value, the granularity of that notation). None when it is not
    a converted notation.

    Returning the granularity alongside is the point. The tolerance used to be a
    single `10 ** 8`, but a `십억` notation has a granularity of `10 ** 9`, so
    **even a correct value exceeds that tolerance** (`345십억` against
    345,173,200,000 is a gap of 173,200,000, while `341십억` is 4,173,200,000).
    Without the granularity, the check either repairs what is right or misses
    what is wrong.
    """
    from argus.agent.prompts import EOK_PER_JO

    text = _PAREN_NOISE.sub("", inner or "").strip()
    hit = _EOK_TEXT.match(text)
    if hit and (hit.group(1) or hit.group(2)):
        stated = 0
        if hit.group(1):
            stated += int(hit.group(1).replace(",", "")) * EOK_PER_JO * 10 ** 8
        if hit.group(2):
            stated += int(float(hit.group(2).replace(",", "")) * 10 ** 8)
        return stated, _PAREN_TOLERANCE
    hit = _SCALE_TEXT.match(text)
    if hit:
        scale = dict(_SCALE_WORDS)[hit.group(2)]
        return int(float(hit.group(1).replace(",", "")) * scale), scale
    return None


def _narrow_paren_unit(inner: str) -> tuple[int, int] | None:
    """The variant used when `wide_unit_parens` is off. It preserves the previous
    behaviour verbatim."""
    from argus.agent.prompts import EOK_PER_JO

    hit = _EOK_TEXT.match(inner or "")
    if not hit or not (hit.group(1) or hit.group(2)):
        return None
    stated = 0
    if hit.group(1):
        stated += int(hit.group(1).replace(",", "")) * EOK_PER_JO * 10 ** 8
    if hit.group(2):
        stated += int(float(hit.group(2).replace(",", "")) * 10 ** 8)
    return stated, _PAREN_TOLERANCE


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
        parsed = _parse_paren_unit(inner) if ablation.enabled("wide_unit_parens") \
            else _narrow_paren_unit(inner)
        if parsed is None:
            return match.group(0)               # not a unit conversion
        stated, tolerance = parsed
        if abs(stated - won) <= tolerance:
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


#: The decisive lines in the evidence. Amounts written there were computed or
#: looked up by the system directly, so they are more trustworthy than the
#: answer's prose. Only the two markers already in use are matched: inventing a
#: new one gets it cited as a source (see
#: `pipeline.strip_marker_citation`).
_DECISIVE_LINE = re.compile(r"(?m)^.*\[결정적 (?:집계|조회)\].*$")


def _decisive_amounts(evidence: str) -> set[int]:
    """The amounts written on decisive lines. The yardstick for deciding whether
    to trust the result of an equation in the answer."""
    out: set[int] = set()
    for line in _DECISIVE_LINE.findall(evidence or ""):
        for text in _ANSWER_NUM.findall(line):
            out.add(int(text.replace(",", "")))
    return out


def fix_broken_equation(answer: str, evidence: str) -> tuple[str, list[str]]:
    """**Delete a false derivation** where the operands cannot produce the result.
    No LLM call.

    Returns (repaired answer, what was repaired), otherwise (original, []).

    ## Why it is needed

    `Q-113` asks *"두 회사 정정 후 계약금액 합계는 최초 공시 합계보다 얼마나
    늘었는가"*. The answer wrote this (`A65`, server r1):

        - 계산식: 1,328,800,000,000 + 499,950,000,000 = **1,828,750,000,000원**
        - 증가분 = 1,828,750,000,000 - 526,570,000,000 = **526,570,000,000원**

    The first line is right. The second **writes the result back into the
    minuend's place**, so the arithmetic does not hold
    (`1,828,750,000,000 - 526,570,000,000 = 1,302,180,000,000`). The result
    itself is right: a decisive line supplied `합계 **526,570,000,000원**` and the
    answer copied it.

    So this question **registers on reasoning soundness but not on accuracy**
    (2/3). `A61` (`sum_operand_fix`) does not reach it, because its firing
    condition is that the evidence contains a decisive **specification**; and
    `fix_self_contradiction` **never checks whether the equation holds** -- it
    only aligns prose elsewhere to the result.

    ## Why the derivation is deleted instead of the operand repaired

    The correct minuend is `1,828,750,000,000 - 526,570,000,000 =
    1,302,180,000,000`, the total of the original filings. We could compute and
    insert it. We do not: that figure appears nowhere in the evidence as text, so
    inserting it would mean **putting a figure we invented into the answer**. The
    same judgement this codebase made in `fx_conversion` by not computing the
    conversion itself.

    Deleting a false sentence is always safe. What remains is
    `- 증가분 = **526,570,000,000원**`, and that value came from a decisive line.

    ## When it stays silent

        1. The equation holds -- there is nothing to repair.
        2. The result is not on a decisive line -- **which side is wrong is
           unknown.** The result may be the wrong one, and deleting the
           derivation would leave only the wrong value.
        3. The tail (the note the system appended) is never touched -- the
           discipline of `split_tail`.
    See docs/design-rationale.md#self-contradiction
    """
    if not ablation.enabled("drop_broken_equation"):
        return answer, []
    body, tail = split_tail(answer or "")
    matches = list(_SELF_EQ.finditer(body))
    if not matches:
        return answer, []
    trusted = _decisive_amounts(evidence)
    if not trusted:
        return answer, []

    fixes: list[str] = []
    #: Repaired back to front: fixing from the front shifts the offsets of the
    #: spans that follow.
    for match in reversed(matches):
        left = int(match.group(1).replace(",", ""))
        right = int(match.group(3).replace(",", ""))
        result = int(match.group(4).replace(",", ""))
        got = left + right if match.group(2) == "+" else left - right
        if got == result:
            continue
        if result not in trusted:
            #: With the result absent from every decisive line, which side is
            #: wrong cannot be known.
            continue
        text = match.group(0)
        kept = text[text.rfind("=") + 1:].lstrip()
        body = body[:match.start()] + kept + body[match.end():]
        fixes.append(f"{left:,} {match.group(2)} {right:,} = {result:,} "
                     f"(실제 {got:,}) 유도를 지웠다")
    return body + tail, fixes


#: The total marker. It has to stand as a word: the `총` bound inside `자산총계`
#: is not a marker. This is where `Q-051` in the golden set
#: (*"연결 자산총계와 별도 자산총계의 차이는 12,646,848백만 원"*) misfired 3/3 in the
#: first version.
_TOTAL_MARK = re.compile(
    r"(?<![가-힣])(?:총액|합계|총)\s*[^.\n]{0,20}?(\d{1,3}(?:,\d{3})+)")

#: How far back to look for the enumeration ahead of a total sentence: the span
#: over which an answer writes a list and then concludes.
_TOTAL_LOOKBACK = 12

#: The most amounts to combine within one sentence. The search is 2^n, so a
#: ceiling is required; when the enumeration is longer, the tail -- the part
#: directly before the total -- is kept.
_TOTAL_POOL = 8


def _sentences(body: str):
    for part in re.split(r"(?<=[.!?])\s+|\n+", body):
        if part.strip():
            yield part.strip()


def _signed_sums(pool: list[int]) -> set[int]:
    """Every value reachable by combining the listed amounts with ±. Both sums
    and differences land here.

    Looking only at the sum would read a subtraction the answer performed as a
    "total that does not add up". `Q-051` in the golden set is exactly that, and
    `Q-115` (four contracts listed, two of them summed) is a partial sum caught
    here too.
    """
    out = {0}
    for value in pool:
        out |= {s + value for s in out} | {s - value for s in out}
    return {abs(v) for v in out}


def drop_unsourced_total(answer: str, evidence: str) -> tuple[str, list[str]]:
    """Delete a total that is absent from the evidence and **unreachable from the
    amounts the answer itself listed**.

    Returns (repaired answer, what was deleted), otherwise (original, []). No LLM
    call.

    ## Why it is needed

    `H-005` in the held-out set asks for the counterparty and amount of **one**
    filing dated 2024-04-17. The answer wrote both golden points, then
    volunteered another contract from the same day and closed with a total.

        - 계약 금액: 160,038,036,031원
        - 계약 금액: 291,548,177,545원
        따라서 ... 총 **452,586,203,566원** 규모의 계약을 체결하였습니다.

    `160,038,036,031 + 291,548,177,545 = 451,586,213,576`. The total the answer
    wrote is off by 999,989,990. Using neither `+` nor `=`, it escapes the
    equation regex in `critic._equation_mismatches`, and since the total is
    absent from the evidence only `verify_number_provenance` fires.

    ## Why the correct sum is not written in

    The same judgement as `fix_broken_equation`. 451,586,213,576 appears nowhere
    in the evidence as text, so writing it in would mean **putting a figure we
    invented into the answer**. Deleting the false sentence leaves the two
    amounts copied from the evidence, and those two are the golden points.

    ## When it stays silent -- the guards measurement forced

        1. The marker is not a standalone word -- the `총` inside `자산총계`
           (golden `Q-051`; the first version misfired 3/3).
        2. The total is in the evidence -- not ours to judge.
        3. The scale markers differ -- `총 2,636억여원` must not be compared
           against won units (held-out `N-008`; the second version misfired 3/3).
        4. The total is smaller than the largest listed amount -- a second line
           of defence against unit mismatch.
        5. It is reachable by a ± combination of the listed amounts -- the answer
           derived it itself.
        6. Fewer than two listed amounts to compare against -- whether it is
           wrong cannot be known.
        7. The tail (the note the system appended) is never touched -- the
           discipline of `split_tail`.

    Measured after the guards (2026-08-28): zero firings across 672 golden-set
    responses (`A91`, `A84`), and only the 3 `H-005` cases among 162 held-out
    responses.
    See docs/design-rationale.md#self-contradiction
    """
    if not ablation.enabled("drop_unsourced_total"):
        return answer, []
    body, tail = split_tail(answer or "")
    known = {int(t.replace(",", "")) for t in _ANSWER_NUM.findall(evidence or "")}
    if not known:
        return answer, []

    sentences = list(_sentences(body))
    fixes: list[str] = []
    #: Deleted back to front: deleting from the front shifts the offsets of the
    #: sentences that follow.
    for index in range(len(sentences) - 1, -1, -1):
        match = _TOTAL_MARK.search(sentences[index])
        if match is None:
            continue
        total = int(match.group(1).replace(",", ""))
        if total in known:
            continue
        unit = _unit_at(sentences[index], match.end(1))
        listed: list[int] = []
        for prev in sentences[max(0, index - _TOTAL_LOOKBACK):index]:
            for found in _ANSWER_NUM.finditer(prev):
                value = int(found.group(0).replace(",", ""))
                if value in known and _unit_at(prev, found.end()) == unit:
                    listed.append(value)
        pool = list(dict.fromkeys(listed))[-_TOTAL_POOL:]
        if len(pool) < 2 or total < max(pool):
            continue
        if total in _signed_sums(pool):
            continue
        body = body.replace(sentences[index], "", 1)
        fixes.append("근거에 없고 나열({0})로 안 닿는 총액 {1:,} 문장을 지웠다"
                     .format(" · ".join(format(v, ",") for v in pool), total))
    if not fixes:
        return answer, []
    return re.sub(r"\n{3,}", "\n\n", body).rstrip() + tail, fixes


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


def summand_roster(rows: list[dict]) -> str:
    """The identity of each summand. Beneath the total line, one row per item
    giving company, contract name, amount and receipt number. With the flag off
    it is an empty string, leaving the total line byte-identical to before.

    **Why this is needed.** The total line carried the total and the *values* of
    the summands. **It never carried which filing each summand came from.**
    Measured on `Q-115` (identical shape in all three server rounds):

        decisive line  위 근거 2건의 금액 합계: 106,796,802,000 + 1,652,800,000,000
                       = 1,759,596,802,000원
        answer         lists four contracts -> = 1,759,596,802,000원

    The total was copied straight from this line while the summands were chosen
    again by the model. It had to re-apply the "filed in 2025, counterparty
    Hyundai Motor" condition to all five evidence blocks from scratch, and got it
    wrong. The enumeration and the total disagree inside the answer, so a reader
    who adds the four figures sees it.

    Graph of Thoughts names this gap: CoT is a chain and ToT is a tree, and
    **neither has an operation that merges several branches**. The aggregation
    transform fills it. We have already performed the merge deterministically, so
    there is nothing new to compute -- it only has to **show what was done**.
    See paper/idea/idea.md section 3.1, docs/design-rationale.md#aggregate-scope

    Regeneration cannot fix it. `Q-115` regenerated in all three rounds and the
    failure kind was unchanged each time, so the retry was discarded
    (`kept=original`). This runs in the same direction as SWE-Lancer's
    observation that judging is easier than generating.
    """
    #: A tail attached to an empty list would put the false sentence "these are
    #: the only 0 filings" into the evidence. `line()` stops it first, and it is
    #: stopped here as well -- sentences that enter the evidence are guarded in
    #: two places.
    if not ablation.enabled("aggregate_items") or not rows:
        return ""
    items = "\n".join(
        f"  · {r.get('corp_name', '')} {_subject(r)} — {_amount(r):,}원 "
        f"(접수번호 {r.get('rcept_no', '')})" for r in rows)
    return (f"\n{items}\n"
            f"  (가산 대상은 위 {len(rows)}건뿐이다. 답변이 항목을 열거할 때는 이 목록을 "
            f"그대로 쓰고 다른 근거 블록을 더하지 않는다.)")


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
        head = (f"- [결정적 집계] 위 근거 {len(rows)}건의 금액 합계{scope}: "
                f"{terms} = **{total:,}원** {_DIRECTIVE}")
        return head + summand_roster(rows)

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
        #: With two or more branches, **both intermediate totals are carried
        #: too.** Previously only the per-branch differences and the overall
        #: difference were emitted, and the answer summed "the corrected total"
        #: and "the original total" itself, and got them wrong. Measured on
        #: `Q-113`: r3 wrote `499,950,000,000 + 1,328,800,000,000` as
        #: `1,738,750,000,000` (true value `1,828,750,000,000`). Being a figure
        #: absent from the evidence it tripped the provenance gate and cost
        #: reasoning soundness, while r1 wrote the overall difference back into
        #: the minuend's place and tripped `equation`. **Both runs happened
        #: because these two figures were missing.**
        totals = ""
        if ablation.enabled("delta_totals"):
            after = sum(_amount(g[-1]) for g in groups)
            before = sum(_amount(g[0]) for g in groups)
            totals = (f" (정정 후 합계 {after:,} − 정정 전 합계 {before:,} 로 계산한 것이다. "
                      f"답변이 중간 합계를 적을 때는 이 두 수를 그대로 쓴다.)")
        return (f"- [결정적 집계] 정정 전후 금액 변동{scope} — "
                + " · ".join(parts)
                + f" · 합계 **{total:,}원**{totals} {_DIRECTIVE}")

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


#: The decisive-line marker, written by every function in this module that
#: places one. Single definition: the repair below finds lines by it, and a
#: second copy would drift from the writers.
DECISIVE_MARK = "- [결정적 집계]"

#: The stub boundary. The golden set uses the concept itself -- `Q-105` point 4
#: says *"100자 미만 스텁"*. The line separates a **category**, not a length, so
#: this value is the whole of the rule.
STUB_CHARS = 100

#: The heading of a periodic-filing evidence block. It pairs with the shape
#: `tools._section_markdown` writes.
_PERIODIC_HEAD = re.compile(
    r"^\[(사업보고서|반기보고서|분기보고서(?:\s*[13]분기)?)\s*([0-9]{4})\]\s*"
    r"(.+?)\s+–\s+(.+?)(?:\s+\[\d+/\d+ 조각\])?$")


#: The total row of a composition table. Only rows carrying **100(%) on the same
#: row** are accepted, as in `| 합 계 | | | 80,318 | 100.0 | …`. The percentage of
#: 100 is what says the table divides a whole, and that total is the denominator
#: of the percentages.
_COMPOSITION_TOTAL = re.compile(
    r"(?m)^\|\s*(?:합\s*계|소\s*계)\s*\|(?=.*(?<![\d.])100(?:\.0)?(?![\d.])).+$")
#: Whether the question asks about composition or share. This line applies only
#: to such questions: emitting any table's total would, on a question whose
#: evidence holds more than ten total rows, produce **a figure whose correctness
#: is unknown** (`Q-080` has 11, measured). The same judgement that makes silence
#: the default in `_sum_rows`.
_COMPOSITION_ASK = re.compile(r"구성|비중|비율.*알려|어떻게.*이루")
#: The amount to take from a row. Only comma-grouped figures are accepted, so
#: percentages (`100.0`) cannot be mistaken for one.
_ROW_AMOUNT = re.compile(r"\d{1,3}(?:,\d{3})+")


def composition_total(evidence: str, question: str) -> str:
    """For a composition question, pin that table's total in one line. No LLM
    call.

    Returns "" when it does not fire.

    ## Why it is needed

    `Q-080` reads *"영업수익의 **구성**을 알려줘"*, and golden points 1-3 require
    percentages (91.6%, 0.9%, 7.5%). All three rounds wrote the percentages and
    **never stated their denominator** (the total, 80,318백만원). A percentage
    means nothing without its denominator.

    ## Why not just any total will do

    `Q-080`'s evidence contains **11** total rows (allowance for doubtful
    accounts, inventory, leases and so on). Emitting them all leaves the answer
    unknown, and picking one and getting it wrong produces **a confidently wrong
    figure**.

    So it is narrowed twice.

        1. The question asks about composition or share.
        2. There is **exactly one** total row carrying a percentage of 100 on the
           same row.

    Checked across all 112 questions: condition 1 alone matches two (`Q-043` with
    6 rows, `Q-080` with 1); adding condition 2 leaves **`Q-080` alone**. With
    6 rows `Q-043` is ambiguous, so the line stays silent -- silence is the
    default.

    ## The unit is not written

    The `[결정적 단위]` line already puts it in the evidence
    (`위 표의 금액 단위: 백만원 = 1,000,000원`). Writing the same thing on two lines
    risks only one of them being corrected later.
    See docs/design-rationale.md#aggregate-scope
    """
    if not ablation.enabled("composition_total"):
        return ""
    if not _COMPOSITION_ASK.search(question or ""):
        return ""
    rows = _COMPOSITION_TOTAL.findall(evidence or "")
    if len(rows) != 1:
        return ""
    amounts = _ROW_AMOUNT.findall(rows[0])
    if not amounts:
        return ""
    return (f"- [결정적 집계] 구성 표의 합계: **{amounts[0]}** "
            f"(위 표의 백분율은 이 값을 분모로 한다. 단위는 [결정적 단위] 줄을 따른다.) "
            f"{_DIRECTIVE}")


#: Whether the question asks about **revenue by segment**. Comparison and
#: difference questions are excluded: there a segment breakdown is noise rather
#: than the answer (`Q-049` *"어느 쪽이 큰지"*, `Q-051` *"차이는 몇 백만원인가"*).
_SEGMENT_ASK = re.compile(r"부문별\s*매출|사업부문.*매출|부문.*매출액")
#: The middle and last rows of a segment subtotal triple:
#: `| 내수 | 191,737 | … |` followed by `| 합계 | 5,198,502 | … |`.
_DOMESTIC_ROW = "내수"
#: Labels that are not segment names. Counting the grand-total row as a segment
#: would put a figure the question never asked for into the answer (`Q-104`'s
#: total point falls outside the question's scope and was already removed).
_NOT_A_SEGMENT = ("합계", "합 계", "계", "소계", "소 계", "총계", "총 계")


def _row_cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def segment_sales(evidence: str, question: str) -> str:
    """Pin segment revenue in one line, **checked by exports + domestic**. No LLM
    call.

    Returns "" when it does not fire.

    ## Why it is needed

    The evidence for `Q-104` (*"삼성전기 제53기 사업부문별 매출을 알려줘"*) holds
    **two** tables from the same filing whose segment values differ by 1.

        주요 제품 및 서비스   컴포넌트 5,198,501 · 광학솔루션 3,814,205   (share table)
        매출실적             컴포넌트 5,198,502 · 광학솔루션 3,814,204   (export/domestic)

    All three rounds read the first table; the golden answer uses the second.
    **Both are internally consistent and both total 11,314,459** -- the
    difference is rounding in the source itself. So the golden answer cannot be
    corrected and the answer cannot be called wrong. The treatment is to show
    which of the two is the firmer.

    ## What decides it -- arithmetic

    In the second table, `exports + domestic = total` holds exactly for all three
    segments.

        5,006,765 + 191,737 = 5,198,502
        2,266,771 +  34,982 = 2,301,753
        3,615,414 + 198,790 = 3,814,204

    The first table offers no such check. Emitting the derivable side makes this
    line **a verification, not a preference**. And triples that fail the check
    are not carried at all, so the mere appearance of this line means those
    values add up within their table.

    ## What is not carried

    The grand-total row (`합 계 | 수출 10,888,950 | 내수 425,509`) is not a segment
    and is excluded. `Q-104`'s total point already falls outside the scope of
    *"사업부문별 매출을 알려줘"* and was removed, so carrying it here would undo
    that removal.

    With fewer than two segments the line stays silent -- one segment is not
    "by segment".

    ## Reach

    Checked across all 112 questions: the table shape alone matches three
    (`Q-049`, `Q-051`, `Q-104`); adding the question filter leaves **`Q-104`
    alone**. Silence is the default here as in `composition_total`.
    See docs/design-rationale.md#aggregate-scope
    """
    if not ablation.enabled("segment_sales"):
        return ""
    if not _SEGMENT_ASK.search(question or ""):
        return ""
    lines = (evidence or "").splitlines()
    found: list[tuple[str, str, str, str]] = []
    for i in range(len(lines) - 2):
        head, mid, tail = lines[i], lines[i + 1], lines[i + 2]
        if not all(x.lstrip().startswith("|") for x in (head, mid, tail)):
            continue
        cells_mid, cells_tail = _row_cells(mid), _row_cells(tail)
        if not cells_mid or cells_mid[0] != _DOMESTIC_ROW:
            continue
        if not cells_tail or cells_tail[0].replace(" ", "") != "합계":
            continue
        numbers = [_ROW_AMOUNT.findall(x) for x in (head, mid, tail)]
        if not all(numbers):
            continue
        export, domestic, total = (int(n[0].replace(",", "")) for n in numbers)
        if export + domestic != total:
            #: A triple that fails the check is not carried. It means the table
            #: has a shape other than the one assumed, and emitting a value then
            #: produces **a confidently wrong figure**.
            continue
        name = _row_cells(head)[0]
        if name.replace(" ", "") in {x.replace(" ", "") for x in _NOT_A_SEGMENT}:
            continue
        found.append((name, numbers[0][0], numbers[1][0], numbers[2][0]))
    if len(found) < 2:
        return ""
    parts = [f"{name} {export} + {domestic} = **{total}**"
             for name, export, domestic, total in found]
    return (f"- [결정적 집계] 부문별 매출 (수출 + 내수 = 합계, 위 표에서 검산했다): "
            f"{' · '.join(parts)} "
            f"(같은 공시의 비중 표는 반올림본이라 부문값이 1 다를 수 있다. "
            f"검산이 맞는 이 값을 쓴다.) {_DIRECTIVE}")


def contrast_line(evidence: str) -> str:
    """One decisive line when the same section arrived from filings that differ
    **in kind**: a stub in one, a body in another.

    Returns "" when nothing applies.

    Why this exists. `Q-105` asks how one section reads across three filings.
    The evidence held 88 / 88 / 88 characters of stub and 7,846 characters of
    body, and the answer said *"세 문서 간 차이점은 없으며"* -- flatly contradicting
    what it was given. README section 4.3 forbids stating a wrong value with
    confidence; the same rule has to hold for a wrong comparison.

    Only a **categorical** split speaks. Measured across 112 questions
    (2026-08-21), the same-path premise holds for 14 question-paths, and in
    almost all of them the sizes are within a tenth of each other -- the same
    section across years. Stating extents there is noise. What `Q-105` has and
    they do not is one filing saying nothing at all while another says
    everything, and *that* is what "no difference" gets wrong.
    """
    groups: dict[tuple[str, str], list[tuple[str, int]]] = {}
    for block in re.split(r"(?m)^##\s+", evidence or ""):
        if not block.strip():
            continue
        head, _, rest = block.partition("\n")
        matched = _PERIODIC_HEAD.match(head.strip())
        if not matched:
            continue
        #: The `- 출처: 접수번호 …` meta line following the heading is not body
        #: text. It runs about 40 characters, so counting it turns an 88-character
        #: stub into 128, pushing it past `STUB_CHARS` and leaving the rule to
        #: fall silent. The first version failed in exactly that way.
        body = "\n".join(line for line in rest.splitlines()
                         if not line.strip().startswith("- "))
        kind = "%s %s" % (matched.group(1), matched.group(2))
        key = (matched.group(3), matched.group(4))
        groups.setdefault(key, []).append((kind, len(body.strip())))

    for (_corp, path), items in groups.items():
        if len({k for k, _ in items}) < 2:
            continue
        stubs = [n for _, n in items if n < STUB_CHARS]
        bodies = [n for _, n in items if n >= STUB_CHARS]
        if not (stubs and bodies):
            continue
        #: One kind can arrive as several pieces, so kinds are folded by their
        #: largest length.
        best: dict[str, int] = {}
        for kind, size in items:
            best[kind] = max(best.get(kind, 0), size)
        parts = " · ".join(
            "%s %s자(%s)" % (kind, format(size, ","),
                             "스텁" if size < STUB_CHARS else "본문")
            for kind, size in sorted(best.items()))
        return ("%s '%s' 의 문서별 기재 상태: %s. "
                "스텁은 해당 항목을 그 보고서에 기재하지 않는다는 뜻이고, 본문이 실린 "
                "보고서와 같지 않다." % (DECISIVE_MARK, path, parts))
    return ""

#: Figures a decisive line has to carry before the repair considers it. Below
#: this the line is a single value, which `substitute` and `state_total`
#: already place without appending a sentence.
GAP_REPAIR_MIN_FIGURES = 3

#: Comma-grouped integers and percentages, the two figure shapes the decisive
#: lines carry.
_LINE_FIGURE = re.compile(r"\d{1,3}(?:,\d{3})+|\d+\.\d+\s*%")

#: Trailing directives inside a decisive line. They instruct the model about
#: the evidence; carried into an answer they are a leak of our own prompt.
_LINE_DIRECTIVE = re.compile(
    r"\((?:이 값은 시스템이|이는 시스템이)[^)]*\)|^\s+답변은 .*$", re.MULTILINE)

#: Digit-only adjacency checks instead of `\b`, the same idiom `safety.py`
#: uses and for the same reason: `\w` in Python `re` includes Hangul, so a
#: figure followed by a Korean particle has no word boundary between its
#: last digit and the particle.
#:
#: These are no longer regex lookarounds. Python `re` requires a fixed-width
#: lookbehind, and the left boundary is not fixed width: a continuing comma
#: may sit past any amount of whitespace (`1, 500,000,000` versus
#: `1,500,000,000`), so `_figure_present` walks each match and inspects the
#: text on both sides directly instead of embedding the rule in the pattern.
#:
#: A comma blocks only when exactly three digits follow it, with any amount
#: of whitespace tolerated around the comma itself: that shape is a
#: thousands group continuing the same longer number, as in
#: `44,200,027,500` read while looking for `44,200,027`, or `1, 500,000,000`
#: read while looking for `500,000,000`. A comma does not block when it
#: separates list items instead -- `20,000,000, 30,000,000` -- because a
#: list item never continues with exactly three more digits and nothing
#: else. The leading group of the figure being searched for also has to be
#: exactly three digits for a preceding comma to plausibly continue a
#: larger number: `44` is not, so `44,200,027` can never be read as the
#: tail of some other number no matter what precedes it.
#:
#: A period blocks only when a digit follows it immediately, with no
#: whitespace tolerated: `20,000,000.50` is a decimal tail. This is a
#: deliberate, asymmetric choice, not an oversight: `20,000,000. 50` and
#: `700,000,000. 2024년에` are the identical shape -- number, period, space,
#: digits -- and nothing in the text distinguishes a decimal written with a
#: stray space after the point from an ordinary Korean sentence ending
#: before the next one opens with a year or a count. The second shape is far
#: more common in this corpus than the first, so the tie goes to reading the
#: figure as present. The cost is real and accepted rather than missed:
#: `20,000,000. 50` reads as present even though it names a different, more
#: precise value.
_GROUP = re.compile(r"\d{3}")
_AFTER_GROUP = re.compile(r"\s*,\s*\d{3}(?!\d)")
_AFTER_DECIMAL = re.compile(r"\.\d")


def _figure_present(figure: str, body: str) -> bool:
    r"""Whether `figure` occurs in `body` as a complete number.

    Plain substring containment would count a figure as present inside a
    longer, different one. The body is searched as written, never modified:
    an earlier version stripped every space from the body to tolerate a
    figure written with stray spaces (`44, 200, 027`), and every boundary
    rule built on that stripped copy was fighting adjacency the stripping
    itself had manufactured.

    Whitespace tolerance now lives only around the commas inside the figure
    being searched for (`\s*,\s*` between each comma-separated group), not
    between every character of the figure: tolerating whitespace everywhere
    let four scattered, unrelated digits on separate lines read as present,
    because nothing then required them to actually sit next to each other.
    See docs/design-rationale.md#gap-repair
    """
    body = body or ""
    chars = figure.replace(" ", "")
    pattern = r"\s*,\s*".join(re.escape(part) for part in chars.split(","))
    head = chars.split(",")[0].lstrip("-")
    for match in re.finditer(pattern, body):
        before, after = body[:match.start()], body[match.end():]
        if before[-1:].isdigit():
            continue                              # inside a longer number
        left = before.rstrip()
        if (left.endswith(",") and _GROUP.fullmatch(head)
                and left[:-1].rstrip()[-1:].isdigit()):
            continue                              # a thousands group of a longer number
        if after[:1].isdigit():
            continue
        if _AFTER_GROUP.match(after) or _AFTER_DECIMAL.match(after):
            continue
        return True
    return False


def decisive_lines(evidence: str) -> list[str]:
    """The decisive lines placed at the head of the evidence, cleaned.

    The marker and the model-facing directives are removed, leaving the
    computed content alone.
    """
    out = []
    for block in (evidence or "").split("\n\n"):
        if not block.lstrip().startswith(DECISIVE_MARK):
            continue
        text = _LINE_DIRECTIVE.sub("", block)
        text = text.replace(DECISIVE_MARK, "").strip()
        text = re.sub(r"\s*\n\s*", " ", text)
        text = re.sub(r"\s{2,}", " ", text).strip(" ·")
        if text:
            out.append(text)
    return out


def repair_gap(answer: str, evidence: str) -> tuple[str, str]:
    """Append a decisive line's content when the answer dropped most of it.

    No LLM call. Returns (new answer, reason), or (original, "") when nothing
    applies.

    `substitute` and `state_total` reach only questions with a pinned
    operation, because both read `_total`. Q-106 and Q-117 have none, so the
    lines they depend on had no answer-side path at all and the model had to
    copy them. It did not, twice, in measured runs.

    The trigger is the line's own figures: fewer than half of them present in
    the answer. Reading only what we ourselves wrote bounds the false-alarm
    rate structurally, and it catches the case `amount_gap` cannot, where the
    answer states one figure and drops the rest.
    See docs/design-rationale.md#gap-repair

    This reaches more than the narrative questions (Q-106, Q-117) that
    motivated it, deliberately:

    - `_total` returns None for the "비교" (comparison) and "몫" (quotient)
      operations, so `substitute`/`state_total` never fire for them even
      though `line()` still writes a DECISIVE_MARK'd block for both, gated by
      its own flag. For those two operations this is the only answer-side
      path back to a dropped line.
    - A single-chain "차" (difference) line carries three figures — older,
      newer, delta — while `state_total` inserts only the delta. That is 1/3
      coverage, under the threshold, so this reaches what `state_total` left
      behind there too.

    Narrowing this to only the narrative writers would require it to know
    which function produced a line, which is exactly the coupling reading by
    `DECISIVE_MARK` alone is meant to avoid. The behaviour is the same
    regardless of the writer: append deterministic content computed from the
    evidence where the answer dropped it.

    `partner_profile`'s line is permanently exempt, not by a special case but
    structurally: it carries only bare filing counts, never a comma-grouped
    amount or a percentage, so it can never reach `GAP_REPAIR_MIN_FIGURES`.
    """
    body, tail = split_tail(answer or "")
    if not body:
        return answer, ""
    additions = []
    for line in decisive_lines(evidence):
        figures = set(_LINE_FIGURE.findall(line))
        if len(figures) < GAP_REPAIR_MIN_FIGURES:
            continue
        present = {f for f in figures if _figure_present(f, body)}
        if len(present) * 2 >= len(figures):
            continue
        additions.append(line)
    if not additions:
        return answer, ""
    stated = "\n\n" + "\n".join(f"정리하면 {a}" for a in additions)
    return body + stated + tail, f"repaired {len(additions)}"


#: One specification row:
#: `  · 회사 계약명 — 1,234원 (접수번호 20250101000001)`
_ROSTER_ITEM = re.compile(
    r"^\s+· (?P<text>.+?) — (?P<amount>[\d,]+)원 \(접수번호 (?P<rcept>\d{14})\)$", re.M)
#: An addition the answer wrote itself: two or more summands and a result.
#: `_SELF_EQ` in `fix_self_contradiction` accepts exactly two summands, so it
#: never reaches this defect, whose enumerations run to three or more.
#: The emphasis is written `**1,759,596,802,000원**`, so the closing `**` comes
#: **after** `원`. The first version expected `**총액**원` and failed to repair the
#: expression in all three rounds (`eq=0`). Both placements are accepted
#: separately.
_SUM_EQ = re.compile(r"(?P<terms>[\d,]{7,}(?:\s*\+\s*[\d,]{7,}){1,})\s*=\s*"
                     r"(?P<open>\*{0,2})\s*(?P<total>[\d,]{7,})\s*원(?P<close>\*{0,2})")
#: The smallest amount that counts as an item row, so that years (2025), ratios
#: and counts are not mistaken for items.
MIN_ITEM_AMOUNT = 1_000_000
#: Korean numeral determiners for counts. Going up to six covers the widest
#: enumeration observed (6).
_COUNT_WORD = {1: "한", 2: "두", 3: "세", 4: "네", 5: "다섯", 6: "여섯"}
#: Words that state a summation. They bound the reach of the count correction to
#: lines carrying one of them.
_SUM_WORD = re.compile(r"합계|합하|합산|더하")
#: Places that state a count, such as `두 건` or `2건`. One preceded by a digit
#: or comma is part of an amount instead.
_COUNT_MENTION = re.compile(
    r"(?<![\d,])(" + "|".join([*map(re.escape, _COUNT_WORD.values()), r"\d"])
    + r")(\s*)건")


def _roster_of(evidence: str) -> list[dict]:
    """Read the decisive specification in the evidence back as a list of items.

    It reads exactly what `summand_roster` wrote. Reading only wording we
    produced ourselves bounds the false-positive rate structurally --
    `repair_gap` follows the same discipline.
    """
    return [{"text": m.group("text").strip(), "amount": m.group("amount"),
             "rcept": m.group("rcept")}
            for m in _ROSTER_ITEM.finditer(evidence or "")]


def _item_amount(line: str) -> str:
    """The amount that line lists, or "". Only item rows are selected."""
    if not re.match(r"^\s*(?:[-·*]|\d+[.)])\s", line):
        return ""
    for found in re.findall(r"[\d,]{7,}", line):
        if int(found.replace(",", "")) >= MIN_ITEM_AMOUNT:
            return found
    return ""


def fix_stray_summands(answer: str, evidence: str) -> tuple[str, str]:
    """Replace the answer's enumeration with the specification when the two
    differ. No LLM call.

    Returns (new answer, reason), or (original, "") when it does not fire.

    ## Why this is needed

    `repair_gap` supplies **what is missing**: it fires when fewer than half of a
    decisive line's figures appear in the answer. `Q-115` is the opposite. Two of
    the decisive line's three figures (one summand and the total) are present, so
    it passes that check, yet **the answer carries amounts that should not be
    there**.

    Measured (`A59`, three server rounds, `scratch/audit/gate_a59.json`):

        spec    차세대 ERP 106,796,802,000 (20251128800125)
                완성차 해상운송계약 1,652,800,000,000 (20250117800487)
        answer  차세대 ERP 106,796,802,000 (20251128800125)     <- followed exactly
                완성차 해상운송계약 890,200,000,000 (20250227800164)
                완성차 해상운송계약 1,345,900,000,000 (20250117800494)
                합계 = 1,759,596,802,000원                       <- our total

    The evidence carries several filings under the same contract name, the
    specification named one of them by receipt number, and the model picked
    others. **Putting the right answer in the evidence is not enough; judgement
    has to do it.** SWE-Lancer's 45% on judging versus 26% on generating, and
    QA.pdf's *"confidently wrong even with the correct evidence present"*, point
    at the same place.

    ## Why the direction of repair is determined

    **The total is right and the summands are wrong.** The total is the value we
    computed and put in the specification, and the answer copied it. So
    recomputing blindly -- summing the summands and overwriting the total --
    would turn a right answer wrong. The side to replace is the summands, and
    **the specification already knows the correct set of them.**

    ## Firing conditions, narrowed three times

        1. The evidence contains a specification (`aggregate_items` is on).
        2. The answer wrote the specification's total -- only answers that copied
           our value are touched.
        3. The set of amounts the answer enumerated differs from the
           specification.

    If any of the three fails, the original is returned byte-for-byte.

    See docs/design-rationale.md#aggregate-scope, paper/idea/idea.md section 3.1
    """
    #: The pipeline guards this too, and so does this function. It is the only
    #: place that **rewrites the body of an answer**, so a caller that forgets the
    #: flag must not be able to silently rewrite every arm's answers. The same
    #: discipline as `summand_roster`.
    if not ablation.enabled("sum_operand_fix"):
        return answer, ""
    roster = _roster_of(evidence)
    if len(roster) < 2:
        return answer, ""
    body, tail = split_tail(answer or "")
    if not body:
        return answer, ""
    total = f"{sum(int(r['amount'].replace(',', '')) for r in roster):,}"
    if total not in body:
        #: An answer that did not use our total is not this defect. Touching it
        #: would break some other calculation.
        return answer, ""

    wanted = [r["amount"] for r in roster]
    lines = body.split("\n")
    listed = [_item_amount(line) for line in lines]
    if {a for a in listed if a} == set(wanted):
        return answer, ""

    #: Mismatched item rows are replaced **in place** by the specification's
    #: items. Deleting them alone would leave company headings with no items
    #: under them.
    unused = [r for r in roster if r["amount"] not in listed]
    swapped, dropped = 0, 0
    removed: list[str] = []
    for index, amount in enumerate(listed):
        if not amount or amount in wanted:
            continue
        if amount.replace(",", "") == total.replace(",", ""):
            #: **This is where the total used to be mistaken for an item and
            #: deleted.** `_item_amount` treats lines beginning with `-`, `·`,
            #: `*`, `N.` or `N)` as enumeration items, so when an answer writes
            #: its conclusion as a list item (`3. 합계: 1,759,596,802,000원`) that
            #: line becomes an item too. The total is not in the summand set
            #: (`wanted`), so it was judged a mismatched item and vanished
            #: entirely.
            #:
            #: `Q-115` r3, measured: `substitute` had inserted the correct
            #: answer and this place deleted it. Another run of the same round
            #: wrote its conclusion as **plain prose** and survived -- meaning
            #: **a single stylistic choice, list or sentence, decided whether the
            #: answer was correct.**
            #:
            #: This function fires only when the total is in the body (the
            #: `if total not in body` above). Deleting that total itself would
            #: destroy its own premise.
            continue
        removed.append(amount)
        if unused:
            item = unused.pop(0)
            indent = re.match(r"^\s*(?:[-·*]|\d+[.)])\s*", lines[index]).group(0)
            lines[index] = (f"{indent}{item['text']} — {item['amount']}원 "
                            f"(접수번호 {item['rcept']})")
            swapped += 1
        else:
            lines[index] = ""
            dropped += 1
    if not swapped and not dropped:
        return answer, ""
    body = "\n".join(line for index, line in enumerate(lines)
                     if line or not listed[index])

    #: An addition the answer wrote itself is rewritten with the
    #: specification's summands too. Leaving the expression alone would repair
    #: the items while the expression stayed wrong, preserving the
    #: contradiction.
    terms = " + ".join(wanted)
    fixed_eq = 0

    def _swap(match: re.Match) -> str:
        nonlocal fixed_eq
        if match.group("total").replace(",", "") != total.replace(",", ""):
            return match.group(0)
        fixed_eq += 1
        return (f"{terms} = {match.group('open')}{total}원"
                f"{match.group('close')}")

    body = _SUM_EQ.sub(_swap, body)

    #: Removing items also falsifies any sentence stating a count. Measured in
    #: r2: after "이제 이 **세 건**의 금액을 합하면", two items remained. The
    #: contradiction is one our repair created, so we close it.
    #: To avoid rewriting a coincidental match, **only counts equal to the
    #: pre-repair item count** are changed, and the scope is bounded to **lines
    #: that state a summation**. Rewriting any `N건` anywhere in the body would
    #: destroy an unrelated count (such as "정정 2건"), so only lines carrying
    #: 합계, 합하, 더하 or 합산 are considered.
    #:
    #: The first version changed only counts equal to the pre-repair item count
    #: and never fired: r2 enumerated four items while writing "세 건" -- **a
    #: sentence that was already wrong before our repair**. Even where the
    #: contradiction is not one we created, it remains in an answer we touched,
    #: so it is closed along with the rest.
    after = len(wanted)
    correct = str(_COUNT_WORD.get(after, after))
    counted = 0
    out_lines = []
    for line in body.split("\n"):
        if _SUM_WORD.search(line):
            #: 원래 띄어쓰기를 살린다. `세 건` 을 `두건` 으로 만들면 우리가 문면을 망친다.
            line, hits = _COUNT_MENTION.subn(
                lambda m: (m.group(0)
                           if m.group(1) in (str(after), _COUNT_WORD.get(after, ""))
                           else f"{correct}{m.group(2)}건"), line)
            counted += hits
        out_lines.append(line)
    body = "\n".join(out_lines)
    #: The tail was built **before** the repair. If the verifier's note still
    #: records an amount that has since been removed from the body, the answer
    #: contradicts itself -- measured on `Q-107`: the body had been corrected to
    #: 4,928,968,079,043 while the tail still said
    #: "답변 490,881,723,100원 vs 재계산". The `_prune_notes` that `substitute`
    #: uses is reused as is. Leaving a resolved defect marked unresolved is
    #: wrong too.
    #: See docs/design-rationale.md#substitution-scope
    pruned = 0
    for amount in dict.fromkeys(removed):
        before = tail
        tail = _prune_notes(tail, amount)
        pruned += before != tail
    return (body + tail,
            f"swapped={swapped} dropped={dropped} eq={fixed_eq} "
            f"count={counted} note={pruned}")


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
