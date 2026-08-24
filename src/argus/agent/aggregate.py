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


#: 「차」 힌트 중 **부사가 없는** 둘. 결과값을 묻는 질문과 변동폭을 묻는 질문을 못 가른다.
#:
#:     얼마**나** 바뀌었는가   변동폭   ← `얼마나 바뀌` 가 이미 잡는다
#:     얼마**로** 바뀌었는가   결과값   ← 이 둘이 잘못 잡는다
#:
#: 🔴 `Q-005` 실측(서버 3회 전부): 골든 114,800,000,000 인데 원공시와의 차
#: 112,700,000,000 을 계산해 근거에 싣고 `state_total` 이 그것을 *"질문이 물은 값"* 으로
#: 답변에 덧붙였다. **모델 본문은 맞았고 덧붙인 문장이 틀렸다.** 판정기는 본문에서 골든
#: 수를 찾아 정답으로 세므로 8축 어디에도 안 잡혔다.
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

    #: 머리글이 두 가지다 — `pipeline` 이 대체가 일어났을 때는 "재계산값으로 정정했습니다"
    #: 를 쓰고 아닐 때는 "결함이 발견되었습니다" 를 쓴다(`pipeline.py:3186`). 첫 판은 뒤쪽만
    #: 받아서 `fix_stray_summands` 가 대체를 되돌렸을 때 꼬리를 못 다듬었다 — 실측 `Q-107`.
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
#: 🔴 2026-08-21 `달라` 를 더했다. `Q-012` 가 *"지분 보유 상황이 어떻게 **달라졌는지**"* 를
#: 묻는데 이 목록에 그 낱말이 없어 **조용한 무동작**이었다. 이 튜플은 위 주석대로 느슨해도
#: 되는 쪽이다 — 강한 전제는 아래 레코드 조건(한 보고자의 대량보유 2건 이상)이고 `Q-012`
#: 는 그 조건을 만족한다((주)두산 → 두산에너빌리티 2건).
#:
#: 사정거리 실측: 이 줄이 지금 도는 문항은 `Q-106` 하나뿐이고, `달라` 를 더해 늘어나는
#: 것은 `Q-012` 하나다(112문항 전수 대조).
_HOLDING_ASK = ("추이", "변해", "변화", "변동", "바뀌", "시간순", "시계열", "어떻게 변")
#: 플래그로 가르는 추가 낱말. 상수 튜플은 플래그를 못 읽으므로 따로 둔다.
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
        #: 🔴 이 줄이 답하는 질문은 *"각 보고서를 **근거로** 설명해줘"* 다(`Q-106`). 날짜와
        #: 비율만 내면 어느 보고서인지 말할 수 없다. 골든 요점1~4 가 걸음마다 접수번호를
        #: 짝지어 요구하는데(`20250707000299` · `20260227007254`) 그 번호는 근거 블록에도
        #: 없다 — 체인은 `holding_chain` 이 들고 있고 근거로 실리는 것은 일부다. 그러니
        #: 이 줄이 내지 않으면 답변이 알 길이 아예 없다.
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
    #: 🔴 지시 줄(`답변은 걸음마다 그 접수번호를 함께 적는다`)을 붙여 봤으나 3회에 1/3
    #: 그대로였다. `counterparty_pair` 에서 배운 것과 같다 — 지시 줄은 늘 듣지 않는다.
    #: 그래서 안 붙인다.
    return (f"- [결정적 집계] {reporter} 보고 보유비율 추이 ({count_note}): "
            f"{' → '.join(steps)} · 누적 {-moved:+.2f}%p "
            f"(이 값은 시스템이 위 근거의 비율 필드에서 직접 읽은 것이다. "
            f"답변의 해당 수치는 이 값을 그대로 쓴다.)"
            + _pledged_series(sorted(
                evidence_chain,
                key=lambda r: (str(r.get("rcept_dt", "")), r.get("rcept_no", "")))))


#: 대량보유 보고서의 `주요계약체결` 두 칸. 값은 `직전 A주 → 이번 B주` 꼴의 **문자열**이라
#: 「이번」 쪽을 따로 뽑아야 한다. 골든이 잇는 것은 각 보고서의 「이번」 값이다 —
#: `Q-012` 요점3 은 2025-03 의 이번(84,282,066) 과 2026-03 의 이번(92,270,920) 을 잇는다.
_PLEDGED_SHARES_KEY = "주요계약체결 주식등의 수"
_PLEDGED_RATIO_KEY = "주요계약체결 비율"
#: `이번` 뒤의 값. `직전` 쪽을 잡으면 한 보고서 안의 변동을 보고서 사이의 변동으로 오인한다.
_CURRENT_IN_FIELD = re.compile(r"이번\s*([\d,]+(?:\.\d+)?)")


def _pledged_current(record: dict, key: str) -> str:
    """그 보고서의 `주요계약체결` 「이번」 값. 없으면 ""."""
    hit = _CURRENT_IN_FIELD.search(str((record.get("fields") or {}).get(key) or ""))
    return hit.group(1) if hit else ""


def _pledged_series(chain: list[dict]) -> str:
    """보유비율 추이 줄에 덧붙이는 **주요계약체결 추이**. 아니면 "".

    ## 왜 따로 붙이나

    `Q-012` 요점3 은 `84,282,066주(13.15%)` → `92,270,920주(14.40%)` 를 요구한다. 그 두
    수는 `shares_current` 가 아니라 `fields["주요계약체결 주식등의 수"]` 안의 문자열
    (`직전 A주 → 이번 B주`)에 있고 보유비율 추이 줄은 그 필드를 안 읽는다.

    ## 🔴 실측이 말한 것 — 처음 적은 설명은 뒤집혀 있었다

    첫 커밋에 *"뒤 수가 근거 본문에만 있어 안 들어왔다"* 라고 적었는데 회차별로 다시 세어
    보니 반대였다.

        A65        84,282,066 없음 · 92,270,920 3/3     (추이 줄이 아예 안 뜬다)
        A68 · A70  84,282,066 3/3  · 92,270,920 0~1/3
        A71        둘 다 3/3

    `84,282,066` 은 **근거 본문(그 필드의 문자열)에서 들어왔다.** 못 들어온 것은 뒤
    수였고 그것을 이 줄이 채운다. 그러니 「본문의 수는 안 들어온다」는 규칙으로 설명할
    자리가 아니다 — 규칙은 그렇게 딱 떨어지지 않는다. 이 줄이 하는 일은 **짝을 명시해
    주는 것**이고, 그것으로 두 수가 함께 들어온다.

    ## 🔴 짝은 **근거로 실린 두 벌**에서 뽑는다 — 체인 전체가 아니다

    첫 판은 `holding_chain` 이 돌려준 전체 체인의 양 끝을 읽었다. `Q-012` 는 체인이
    13건이라 `2023-02-17 40,939,844주 (6.41%)` 가 앞 끝이 되어 **골든의 짝과 달랐다**.
    (답변이 그 수를 쓰지는 않았으나 그것은 운이다.)

    비율 추이 줄이 체인 전체를 훑는 것은 「추이」를 보이는 것이 일이기 때문이다. 이 줄은
    「무엇에서 무엇으로」라는 **한 짝**을 말하므로, 답변이 실제로 읽는 두 벌 — 검색이
    근거로 실어 준 그것 — 을 가리켜야 한다.

    ## 🔴 언제 침묵하는가

    양 끝 둘 다 그 필드를 가질 때만 낸다. 코퍼스 실측: 대량보유 1,083건 중 507건만 이
    필드를 갖는다. 한쪽만 있으면 「무엇에서 무엇으로」가 성립하지 않으므로 침묵한다 —
    반쪽 추이를 내는 것은 없는 변동을 말하는 것이다.

    양 끝만 적는 것은 위 줄의 주식수 규칙과 같다. 중간값을 다 실으면 질문이 묻지 않은
    값으로 줄이 두 배가 된다.
    """
    if not ablation.enabled("pledged_series") or len(chain) < 2:
        return ""
    first, last = chain[0], chain[-1]
    shares = [_pledged_current(r, _PLEDGED_SHARES_KEY) for r in (first, last)]
    if not all(shares):
        return ""
    ratios = [_pledged_current(r, _PLEDGED_RATIO_KEY) for r in (first, last)]
    if shares[0] == shares[1] and ratios[0] == ratios[1]:
        #: 두 끝의 **값**이 같으면 변동이 없다. `추이` 라고 적으면 거짓이다.
        #: 🔴 날짜를 붙인 뒤에 견주면 안 된다 — 날짜는 늘 다르므로 이 문이 죽는다.
        return ""
    ends = []
    for record, share, ratio in zip((first, last), shares, ratios):
        tail = f" ({ratio}%)" if ratio else ""
        ends.append(f"{_ymd(record)} {share}주{tail}")
    return (f"\n- [결정적 집계] 같은 보고의 주요계약체결 주식등 추이: "
            f"{' → '.join(ends)} "
            f"(이 값은 시스템이 위 근거의 `주요계약체결` 칸에서 직접 읽은 것이다. "
            f"답변의 해당 수치는 이 값을 그대로 쓴다.)")


#: 질문이 짚는 보고서 날짜. `2025년 3월 10일` 과 `2025년 3월` 둘 다 받는다 — `Q-020` 은
#: 일자까지, `Q-012` 는 월까지만 적는다. 일이 있는 쪽을 먼저 시도해야 `2025년 3월 10일` 이
#: `2025년 3월` 로 잘려 두 날짜가 한 달로 뭉개지지 않는다.
_PAIR_DAY = re.compile(r"(20\d{2})\s*년\s*(1[0-2]|0?[1-9])\s*월\s*(3[01]|[12]\d|0?[1-9])\s*일")
_PAIR_MONTH = re.compile(r"(20\d{2})\s*년\s*(1[0-2]|0?[1-9])\s*월(?!\s*\d{1,2}\s*일)")


def _pair_keys(question: str) -> list[str]:
    """질문이 짚는 날짜를 적힌 차례대로. `YYYYMMDD` 또는 `YYYYMM`."""
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
    """그 날짜의 보고서 **하나**. 없거나 둘 이상이면 None — 침묵이 기본값이다."""
    hits = [r for r in chain if str(r.get("rcept_dt", "")).startswith(key)]
    return hits[0] if len(hits) == 1 else None


def _pair_delta(before: str, after: str, unit: str) -> str:
    """두 값의 증감을 낱말로. 못 읽으면 ""."""
    try:
        gap = int(str(after).replace(",", "")) - int(str(before).replace(",", ""))
    except (TypeError, ValueError):
        return ""
    if gap == 0:
        return "변동 없음"
    return f"{abs(gap):,}{unit} {'증가' if gap > 0 else '감소'}"


def holding_pair(records, question: str, chain_lookup=None) -> str:
    """질문이 **두 보고서를 짚어 견주라** 할 때, 칸마다 두 값과 그 차를 못 박는다.

    LLM 호출 없음. 조건을 못 채우면 "" 를 돌려준다.

    ## 무엇을 고치는가 — `Q-012` 실측 (서버 3회 전부)

    `holding_series` 는 주식 수를 **체인 양 끝에만** 단다(`ends`). `Q-012` 가 짚는
    2025-03-10 은 13건 체인의 끝이 아니라서 그 줄에 주식 수가 없었다. 그래서 모델이
    **두 출처를 섞었다** — 2026 값은 결정적 줄에서, 2025 값은 원시 블록에서 가져와
    스스로 뺐고, 세 회차가 각각 18,607 · 18,652 · 18,652 를 냈다. **참값은 18,650 이다.**

    🔴 **등식을 안 쓴 산문 주장이라 어떤 검사에도 안 걸렸다.**
    `critic.verify_answer_arithmetic` 도 `fix_self_contradiction` 도 `A − B = C` 꼴이
    있어야 발화한다. *"18,652주 추가로 취득하여"* 는 그 꼴이 아니다.

    ## 왜 방향까지 적는가

    근거에는 **범위가 다른 두 참말**이 나란히 있다.

        결정적 줄(보고서 사이)   2025-03-10 84,282,066 → 2026-03-05 92,270,920   증가
        원시 블록(2026 보고 안)  직전 92,270,920 → 이번 92,270,920              변동 없음

    둘 다 참인데 재는 구간이 다르고, 모델은 **수는 앞에서 서술은 뒤에서** 가져와
    *"동일한 수치인 92,270,920주"* 라는 문장을 만들었다 — 바로 윗줄의 84,282,066 과
    모순이다. 골든 요점3 은 「증가」다. 그래서 이 줄은 값만이 아니라 **증감을 낱말로**
    적는다. 모델이 방향을 스스로 고르게 두지 않는다.

    ## 침묵이 기본값이다

    날짜가 정확히 둘이 아니거나, 한 날짜가 보고서 0건 또는 2건 이상에 걸리면 아무것도
    내지 않는다. `_sum_rows` 와 같은 판단이다 — 어느 것이 답인지 모르는 수를 내놓느니
    안 내는 편이 낫다.
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

    #: 기간은 달 수로만 적는다. `Q-012` 3회가 12개월 사이를 *"약 18개월"* 이라 적었다.
    span = ""
    a, b = str(first.get("rcept_dt", "")), str(last.get("rcept_dt", ""))
    if len(a) >= 6 and len(b) >= 6:
        months = (int(b[:4]) - int(a[:4])) * 12 + (int(b[4:6]) - int(a[4:6]))
        if months > 0:
            span = f", 약 {months}개월"
    #: 🔴 앞에 `\n` 을 붙이지 않는다. `_pledged_series` 는 **다른 줄 뒤에 이어 붙는** 것이라
    #: 줄바꿈을 스스로 달지만, 이 줄은 `_prepend` 가 홀로 세운다. 붙이면 근거 첫 줄이 빈
    #: 문자열이 되고, `attach_citation` 의 블록 파서가 그 빈 줄을 머리글로 읽어
    #: **`(근거: , 접수번호 …)`** 라는 라벨 없는 인용을 낸다 — 서버 실측으로 확인했다.
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


#: 질문이 **두 공시의 관계**를 묻는가. 열거 문항에는 안 든다 — 보류셋 실측으로 `H-011`
#: *"계약상대로 하는 …을 공시한 상장사를 빠짐없이"* · `N-014` 가 꼴에는 걸리지만 차액이
#: 답이 아니라 잡음이다.
_PAIR_ASK = re.compile(r"관계|이어받|넘어갔|같은 사업|동일한 사업|바뀐 것")
#: 1억. 골든이 `약 4,111억원` 이라 적으므로 억 단위로 내림해 적는다.
_EOK = 10 ** 8


def _bare(name: str) -> str:
    return re.sub(r"\s+", "", name or "")


def counterparty_pair(records, question: str) -> str:
    """계약상대가 같은 **두 회사의 공시**를 잇고 금액 차를 못 박는다. LLM 호출 없음.

    발화하지 않으면 "" 를 돌려준다.

    ## 왜 필요한가

    `Q-112` 는 *"대우건설이 해지하고 현대건설이 같은 사업을 체결했다 — 두 공시가 어떤
    관계인지 설명하라"* 다. 골든 요점3 이 *"약 4,111억원 더 크다"* 를 요구하는데 답변은
    세 판 모두 두 금액을 나란히 적고 **차를 계산하지 않는다.** 이 문항에는 결정적 줄이
    하나도 안 붙었다 — `[결정적 집계]` 가 통째로 침묵한 자리다.

    `line`(`_op`) 의 차 연산이 안 걸린 것은 질문이 *"차이는 얼마인가"* 가 아니라
    *"어떤 관계인가"* 이기 때문이다. 관계를 묻는 질문에 차액이 답의 일부인 꼴은 이
    문항 유형에 고유하므로 줄을 따로 만든다.

    ## 무엇으로 잇는가 — 공백만 지운 계약상대 이름

    두 공시의 계약상대는 `'주식회사 유토개발2차'` 와 `'주식회사유토개발2차'` 로 **공백만
    다르다.** 골든 요점4 가 바로 그것을 말한다(*"두 공시를 잇는 것은 계약상대 필드뿐이며
    표기가 띄어쓰기만 다르다"*). 그래서 이 줄은 두 표기를 **둘 다 적는다** — 답변이
    무엇으로 이었는지 말할 수 있어야 한다.

    ## 🔴 언제 침묵하는가

        1. 질문이 관계를 묻지 않는다
        2. 그런 쌍이 **정확히 하나**가 아니다 — 여럿이면 어느 쌍의 차인지 모른다
        3. 한 상대에 세 건 이상 붙는다 — 「차」가 어느 둘의 것인지 모른다
        4. 두 공시의 **공시 주체가 같다** — 한 회사가 같은 상대와 두 번 계약한 것은
           시공사가 바뀐 것이 아니다
        5. 어느 한쪽에 금액이 없다

    ## 🔴 차액은 아직 절반만 닿는다 — 다섯 꼴을 재고 원칙으로 골랐다

    골든은 *"약 4,111억원 더 크다"* 라 적는다. 어순을 다섯 꼴로 재 봤다(꼴마다 3회).

        `411,161,299,002원 (약 4,111억원)` 꼬리 절     → 원 3/3 · 억원 0/3
        `약 4,111억원 (411,161,299,002원)` 꼬리 절     → 원 0/3 · 억원 0/3
        `411,161,299,002원 더 크다 — 억 단위로는 …`     → 원 3/3 · 억원 2/3   ← 채택
        위 꼴 + `답변은 … 함께 적는다` 지시 줄            → 원 2/3 · 억원 1/3
        차를 별개의 등식 줄로 분리                       → 원 1/3 · 억원 0/3

    🔴 그리고 채택한 꼴을 **다시 재니 원 1/3 · 억원 1/3**, 6회로 늘려 재니 **원 3/6 ·
    억원 1/6** 이었다. 같은 꼴의 표본 간 분산이 꼴 사이의 차이보다 크다 — 이 크기의
    표본으로는 꼴을 가릴 수 없다는 뜻이다. 그래서 마지막에는 실측이 아니라 원칙으로
    골랐다: 한 줄이고(다른 줄들과 같은 꼴) 두 단위를 다 싣는다.

    합쳐 읽으면 차액이 답변에 드는 것은 대략 **원 7/12 · 억원 4/12** 다. `Q-112` 요점3
    은 이 팔로 **닫히지 않는다.** 종전이 0/3 이었으니 나아진 것은 맞지만 닫혔다고
    적으면 거짓이다.

    ## 무엇이 확실히 나아졌는가

    골든 요점4(계약상대 표기가 공백만 다르다)는 3회 모두 답변에 든다. 요점2·3 의 날짜와
    금액, 두 접수번호도 그렇다. 종전 답변은 확정일(2023-12-13)을 적었고 이 줄이 붙은
    뒤로는 공시일(2023-12-14)을 적는다.

    ## 배운 것 — 지시 줄은 늘 듣지 않는다

    `counterparty_branch` 가 쓰는 「답변은 …」 지시 줄을 여기에 붙이면 **더 나빠졌다**.
    그 꼴을 다른 자리에 옮겨 쓸 때는 재 보고 써야 한다.

    사정거리 실측: 코호트 112문항에서 꼴이 걸리는 것은 `Q-112` 하나이고, 보류셋에서
    걸리는 둘은 조건 1 에 막힌다.
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
        #: 금액이 같으면 「더 크다」가 거짓이다.
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


#: 공시가 **스스로** 적어 둔 원화 환산 문장. 코퍼스 실측 꼴:
#:
#:     상기 2. '투자금액' $58억을 원화 환산 시 8조 5,080억원이며, 이는 2025년 3월 24일
#:     서울외국환중개 매매기준율(1,466.9원/달러)을 …
#:
#: 세 조각을 한꺼번에 잡아야 뜻이 선다 — 외화 금액 · 환산액 · 그 환율. 하나라도 못 잡으면
#: 침묵한다. 환산액만 적으면 무엇을 무엇으로 환산했는지 모르고, 환율만 적으면 답이 아니다.
_FX_CONVERSION = re.compile(
    r"(?P<fx>[$＄]\s*[\d,.]+\s*[조억만]?)\s*을?\s*원화\s*환산\s*시\s*"
    r"(?P<krw>[\d,]+(?:\s*[조억만]\s*[\d,]*)*\s*원)"
    r".{0,20}?(?P<date>\d{4}\s*년\s*\d{1,2}\s*월\s*\d{1,2}\s*일)\s*"
    r"(?P<source>[^(（]{0,16}?)매매기준율\s*[(（]\s*(?P<rate>[\d,.]+)\s*원\s*/\s*달러")
#: 질문이 금액을 묻는가. 꼴 자체가 352문항에 한 자리뿐이라 이 문은 여벌이지만, 나중에
#: 같은 공시가 다른 질문에 걸릴 때 질문이 안 물은 수가 답에 끼는 것을 막는다.
_FX_ASK = re.compile(r"금액|규모|얼마|투자|환산")


def fx_conversion(evidence: str, question: str) -> str:
    """공시가 스스로 밝힌 원화 환산액과 그 환율을 한 줄로 옮긴다. LLM 호출 없음.

    발화하지 않으면 "" 를 돌려준다. **계산하지 않는다** — 문면을 그대로 옮기므로
    `[결정적 조회]` 다.

    ## 왜 필요한가

    `Q-101`(*"현대제철 … 투자금액을 근거와 함께 알려줘"*)의 골든 요점2 는 *"2025년 3월
    24일 매매기준율(1,466.9원/달러)로 환산한 8조 5,080억원이 함께 적혀 있다"* 다.

    그 문장은 근거 안에 **그대로 있다.** `- 기타 투자판단과 관련한 중요사항:` 필드의
    산문 속이다. 답변은 세 판 모두 `$58억` 만 적고 환산액도 환율도 안 옮겼다 —
    「결정적 줄에 실린 수는 답변에 오고 근거 산문에만 있는 수는 오지 않는다」의 또 다른
    자리다. 그래서 그 산문을 줄로 끌어올린다.

    ## 🔴 왜 우리가 환산하지 않는가

    `58억 달러 × 1,466.9 = 8조 5,080억원` 을 우리가 계산해서 낼 수도 있다. 하지 않는다.
    공시가 적은 환산액과 우리 계산이 어긋나면(반올림·기준일 차이로 흔히 어긋난다) 우리
    값이 **공시와 다른 수**가 되고, 그것은 심사자가 원공시와 맞춰 볼 때 곧바로 드러난다.
    공시가 스스로 적은 값이 있으면 그것을 옮기는 것이 언제나 낫다.

    ## 🔴 언제 침묵하는가

        1. 세 조각(외화 금액 · 환산액 · 환율) 중 하나라도 못 잡는다
        2. 그런 문장이 **정확히 하나**가 아니다 — 여럿이면 어느 것이 답인지 모른다
        3. 질문이 금액을 묻지 않는다

    사정거리 실측: 코호트 112 + 보류 16 + 서버 3회분을 합쳐 352건을 훑어 이 꼴이 걸리는
    것은 `Q-101` 하나다. `Q-067` 에도 `환율` 낱말이 있으나 환산 문장은 아니다.
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


# --- 정정 전후 대조 -------------------------------------------------------
#
# `정정 전후로 무엇이 달라졌는지` 를 묻는 문항은 근거에 **같은 절이 두 벌** 실린다.
# 원공시 한 벌과 정정공시 한 벌이다. 그 둘의 차이는 코드로 정확히 구할 수 있는데
# 답변은 그것을 눈으로 훑어 짐작한다 — `Q-094` 는 세 판 모두 표기 변경은 맞히고
# 「금액은 하나도 안 바뀌었다」와 그 금액들을 못 적었다.
#
# 🔴 갈림길이 하나 있다. **금액이 바뀐 정정과 표기만 바뀐 정정은 답이 다르다.**
#
#     금액이 바뀌었다 → 무엇이 무엇으로 바뀌었는가가 답이다. 바뀐 줄만 낸다.
#     금액이 안 바뀌었다 → 무엇이 **안** 바뀌었는가가 답이다. 그 줄이 든 표까지 낸다.
#
# 뒤쪽에서 표를 함께 내는 것이 `Q-094` 요점3(`63,562,284`·`135,653,693` 은 안 바뀌었다)의
# 처방이다. 그 두 수는 바뀐 줄에 없고 바뀐 줄 **바로 아래**에 있다.
# See docs/design-rationale.md#aggregate-scope

#: 근거 블록의 제목에서 조각 표시를 떼어 낸다. `[1/2 조각]` 이 붙은 것과 안 붙은 것이
#: 같은 절이다.
_PIECE_MARK = re.compile(r"\s*\[\d+/\d+ 조각\]\s*$")
#: 블록의 출처 줄. 대조에서 **빼야 한다** — 접수번호가 다른 것은 당연하고, 그것을 차이로
#: 세면 모든 짝이 최소 한 줄 다르게 나온다.
_BLOCK_SOURCE = re.compile(r"^- 출처: 접수번호 (\d{14})")
#: 바뀐 줄 짝의 상한. 이보다 많으면 침묵한다 — 코퍼스 실측으로 `Q-076` 71줄 · `Q-088`
#: 249줄 짝이 있고, 그것을 한 줄에 담으면 근거를 밀어내는 벽이 된다. 결정적 줄은
#: 200~400자를 지켜 왔다.
MAX_REVISION_PAIRS = 6
#: 「안 바뀐 표」를 낼 때의 상한. 표 두 개 · 표마다 여덟 줄.
MAX_SAME_TABLES = 2
MAX_SAME_ROWS = 8


def _revision_blocks(evidence: str) -> list[tuple[str, list[str]]]:
    """근거를 `## ` 제목으로 쪼갠다. (조각 표시를 뗀 제목, 본문 줄)."""
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
    """(절 제목, 원공시 본문, 정정공시 본문). 짝이 정확히 하나가 아니면 None.

    짝을 고르는 규칙은 **같은 제목 · 다른 접수번호 · 정확히 두 벌**이다. 셋 이상 실리면
    어느 둘이 전후인지 모르므로 침묵한다.
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
        #: 접수번호가 작은 쪽이 원공시다. 정정은 언제나 뒤에 접수된다.
        order = sorted(zip((left, right), bodies), key=lambda x: x[0])
        pairs.append((head, order[0], order[1]))
    return pairs[0] if len(pairs) == 1 else None


#: 표 위의 단위 선언. `(단위 : 천원)` · `(단위:백만원)`.
_TABLE_UNIT = re.compile(r"[(（]\s*단위\s*[:：]\s*([^)）]{1,12}?)\s*[)）]")
#: 그 행의 값이 **돈이 아닌** 것. 표가 `(단위 : 천원)` 을 선언했어도 주식수에 천원을
#: 붙이면 자신 있게 틀린 수가 된다. 표의 선언은 표의 사정이고 우리 줄은 우리 말이다.
_NOT_MONEY_LABEL = ("주식수", "주수", "수량", "인원", "건수", "비율", "율(", "주가")


def _containing_table(body: list[str], at: int) -> tuple[list[str], str]:
    """(`at` 번째 줄이 든 표의 값 행들, 그 표가 선언한 단위).

    구분 행과 머리 행은 뺀다. 단위는 표 **바로 위**에서 찾는다 — 멀리서 찾으면 다른
    표의 단위를 끌어온다.
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
    """돈 행이면 `라벨 값단위`, 아니면 "".

    ## 왜 골라 내는가

    `covers_key_amounts` 는 `money_values` 로 재고 그것은 **단위가 붙은 수**만 돈으로
    본다(`63,562,284` → 없음 · `63,562,284천원` → 있음). 그래서 단위를 붙여야 골든 요점의
    금액을 답변이 말한 것으로 센다.

    🔴 그런데 아무 행에나 붙이면 안 된다. `Q-094` 의 표는 `(단위 : 천원)` 을 선언했지만
    그 안에 `배당 기준 총발행 보통주식수 21,954,022` 가 있다. `21,954,022천원` 이라 적으면
    주식수를 돈이라고 단정하는 것이다. 그리고 `보통주에 지급된 주당배당금(단위: 원/주)` 은
    **자기 단위를 스스로 밝힌 행**이라 표의 단위가 안 듣는다.

    그래서 둘을 뺀다: 라벨이 수량·비율을 말하는 행과 라벨이 자기 단위를 밝힌 행.
    남는 것이 `보통주에 지급된 배당금 63,562,284천원` — 골든 요점3 이 요구한 그것이다.
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
    """정정 전후 두 벌을 대조해 한 줄로 못 박는다. LLM 호출 없음.

    발화하지 않으면 "" 를 돌려준다.

    ## 왜 필요한가

    `Q-094`(*"배당금 지급 내역 표에서 정정 전후로 무엇이 달라졌는지"*)의 골든 요점 넷이
    이 대조에 통째로 들어 있다.

        요점1  주당배당금 항목명에 '(단위: 원/주)' 가 추가됐다      → 바뀐 줄 짝
        요점2  3,130 · 6,680 은 전후가 같다                      → 바뀐 줄 안의 수
        요점3  63,562,284 · 135,653,693 도 바뀌지 않았다          → 그 줄이 든 표
        요점4  금액은 하나도 바뀌지 않았다                        → 금액 다중집합 동일

    답변은 세 판 모두 요점1·2 만 맞혔다. 요점3 의 두 수는 바뀐 줄에 없고 **바로 아래
    줄**에 있어, 눈으로 훑는 것으로는 「안 바뀌었다」를 말할 수 없다. 코드는 말할 수 있다.

    ## 🔴 금액이 바뀐 정정과 표기만 바뀐 정정은 답이 다르다

        금액이 바뀌었다   → 무엇이 무엇으로 바뀌었는가가 답. 바뀐 줄만 낸다.
        금액이 안 바뀌었다 → 무엇이 **안** 바뀌었는가가 답. 그 줄이 든 표까지 낸다.

    뒤쪽에서만 표를 함께 내는 이유는 앞쪽에서 내면 벽이 되기 때문이다 — 금액이 바뀐 짝은
    표가 크다(`Q-048` 178종 · `Q-052` 14종).

    ## 🔴 언제 침묵하는가

        1. 같은 제목 · 다른 접수번호인 짝이 **정확히 하나**가 아니다
        2. 다른 줄이 하나도 없다 — 말할 것이 없다
        3. 바뀐 줄 짝이 `MAX_REVISION_PAIRS` 를 넘는다 — 코퍼스 실측으로 `Q-076` 71줄
           · `Q-088` 249줄 짝이 있고, 그것을 한 줄에 담으면 근거를 밀어내는 벽이 된다

    출처 줄은 대조에서 뺀다. 접수번호가 다른 것은 당연하고, 그것을 차이로 세면 모든 짝이
    최소 한 줄 다르게 나온다.

    ## 🔴 표지는 `[결정적 집계]` 다 — 새 표지를 만들면 답변이 그것을 근거로 인용한다

    첫 판은 `[결정적 대조]` 라는 새 표지를 썼다. 답변이 *"(근거: 결정적 대조 정보)"* ·
    *"(근거: 결정적 대조, 결정적 단위)"* 라고 적었다. 생성 프롬프트는 표지를 아예 모르므로
    모델은 대괄호를 그냥 출처로 읽는다. `counterparty_branch` 가 술어 이름을 줄에서 빼
    두는 것과 같은 까닭이라, 표지는 이미 쓰이는 둘(`집계`·`조회`) 안에 머문다. 이것은
    우리가 계산한 대조이므로 `집계` 다.

    ## 🔴 안 바뀐 금액에는 단위를 붙인다 — 그러나 아무 행에나 붙이지 않는다

    `covers_key_amounts` 는 `money_values` 로 재고 그것은 **단위가 붙은 수**만 돈으로 본다.
    첫 판은 표의 값을 그대로 옮겨 `63,562,284` 라 적었고, 수리기가 그 줄을 답변에 붙였는데도
    `covers_key_amounts` 가 *"금액 표기가 하나도 없다"* 로 떨어져 3회 모두 오답이 됐다.

    그래서 표가 선언한 단위를 값에 붙인다. 🔴 다만 두 갈래를 뺀다 — 라벨이 수량·비율을
    말하는 행(`배당 기준 총발행 보통주식수 21,954,022` 에 천원을 붙이면 주식수를 돈이라고
    단정하는 것이다)과 라벨이 자기 단위를 스스로 밝힌 행(`주당배당금(단위: 원/주)` 에는
    표의 천원이 안 듣는다). 남는 것이 `보통주에 지급된 배당금 63,562,284천원` 이다.

    ## 🔴 그리고 「안 바뀌었다」만 말하면 금액이 하나도 안 남는다

    첫 판의 지시는 *"바뀐 것과 바뀌지 않은 것을 모두 이 대조대로 적는다"* 였다. 답변은
    *"…금액 등은 정정 전후로 변동이 없습니다"* 로 뭉뚱그리고 **수치를 한 개도 적지
    않았다.** `amount_gap` 이 걸려 3회 중 2회가 오답이 됐다 — 겨냥한 문항을 우리가 망친
    것이다. 그래서 지시를 「위에 적힌 금액을 그대로 적어라」로 바꿨다.
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

    #: 금액이 안 바뀐 정정에서는 「안 바뀐 값」이 답이다. 바뀐 줄이 든 표를 함께 낸다 —
    #: `Q-094` 요점3 의 두 수가 거기 있다.
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

#: 🔴 조·억 밖의 자릿수 낱말. `_EOK_TEXT` 가 못 읽는 표기는 `fix_unit_parens` 가 **손대지
#: 않고 지나간다**(그 함수의 독스트링: *"Parentheses that do not parse as a conversion are
#: left alone"*). 그 규율은 옳다 — `(주당 1,000원)` 이나 `(지분율 30%)` 를 환산으로 읽으면
#: 안 된다. 다만 **못 읽는 것과 틀린 것이 같이 통과한다**는 대가가 있었다.
#:
#: `Q-061` 실측: 답변이 `345,173,200,000원 (341십억 원)` 이라 적는다. 341십억은
#: 341,000,000,000 이고 본문 값과 4,173,200,000 이 어긋난다. `십억` 을 못 읽어 그대로
#: 지나갔고 `restatement` 관문이 **A65 서버 3회 · A77 로컬 3회 모두** 이것을 잡았다 —
#: 여섯 판 내리 같은 결함인데 v3 §4 에 없던 자리다.
#:
#: 672건 전수 대조로 `_EOK_TEXT` 가 못 읽는 환산 표기는 셋뿐이다.
#:
#:     `**2,759억원**`  마크다운 강조가 괄호 **안**에 있다 (`Q-018` 6/6, 값은 맞다)
#:     `341십억 원`     자릿수 낱말이 조·억 밖이다 (`Q-061` 6/6, 값이 틀렸다)
#:     `412억여 원`     `여`(남짓) 접미가 붙었다 (`Q-091` 5/6, 값은 맞다)
#:
#: 셋을 다 읽게 한다. 값이 맞는 둘은 그대로 통과하고 틀린 하나만 고쳐진다.
_SCALE_WORDS = (("십억", 10 ** 9), ("백만", 10 ** 6), ("천만", 10 ** 7), ("만", 10 ** 4))
_SCALE_TEXT = re.compile(r"^([\d,]+(?:\.\d+)?)\s*(십억|백만|천만|만)\s*여?\s*원?$")
#: 괄호 안에서 지울 것 — 마크다운 강조와 `여`(남짓). 값에 영향이 없다.
_PAREN_NOISE = re.compile(r"[*_]|(?<=억)\s*여|(?<=조)\s*여")

#: Rounding allowance. Unit notation drops anything below 100 million, so a
#: gap that size is not an error.
_PAREN_TOLERANCE = 10 ** 8


def _parse_paren_unit(inner: str) -> tuple[int, int] | None:
    """(적힌 값, 그 표기의 눈금). 환산 표기가 아니면 None.

    🔴 눈금을 함께 돌려주는 것이 요점이다. 종전은 허용 오차가 `10 ** 8` 하나였는데,
    `십억` 표기의 눈금은 `10 ** 9` 이라 **바른 값도 오차를 넘는다**(`345십억` vs
    345,173,200,000 은 173,200,000 차이지만 `341십억` 은 4,173,200,000 차이다). 눈금을
    모르면 맞는 것을 고치거나 틀린 것을 놓친다.
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
    """`wide_unit_parens` 가 꺼졌을 때의 판. 종전 거동을 글자 그대로 유지한다."""
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


#: 근거의 결정적 줄. 그 줄에 적힌 금액은 시스템이 직접 계산·조회한 것이라 답변의 산문보다
#: 믿을 수 있다. 표지는 이미 쓰이는 둘뿐이다(새 표지를 만들면 답변이 그것을 출처로
#: 인용한다 — `pipeline.strip_marker_citation` 참조).
_DECISIVE_LINE = re.compile(r"(?m)^.*\[결정적 (?:집계|조회)\].*$")


def _decisive_amounts(evidence: str) -> set[int]:
    """결정적 줄에 적힌 금액들. 답변의 등식 결과를 믿을지 가리는 잣대다."""
    out: set[int] = set()
    for line in _DECISIVE_LINE.findall(evidence or ""):
        for text in _ANSWER_NUM.findall(line):
            out.add(int(text.replace(",", "")))
    return out


def fix_broken_equation(answer: str, evidence: str) -> tuple[str, list[str]]:
    """피연산자가 결과를 못 만드는 등식에서 **거짓 유도를 지운다.** LLM 호출 없음.

    (고친 답변, 무엇을 고쳤는지) 를 돌려준다. 아니면 (원본, []).

    ## 왜 필요한가

    `Q-113` 은 *"두 회사 정정 후 계약금액 합계는 최초 공시 합계보다 얼마나 늘었는가"* 다.
    답변이 이렇게 적었다(`A65` 서버 r1).

        - 계산식: 1,328,800,000,000 + 499,950,000,000 = **1,828,750,000,000원**
        - 증가분 = 1,828,750,000,000 - 526,570,000,000 = **526,570,000,000원**

    앞 줄은 맞다. 뒷 줄은 **결과를 피감수 자리에 다시 쓴 것**이라 산술이 성립하지 않는다
    (`1,828,750,000,000 − 526,570,000,000 = 1,302,180,000,000`). 결과 자체는 맞다 —
    결정적 줄이 `합계 **526,570,000,000원**` 을 주었고 답변이 그것을 옮겼다.

    그래서 이 문항은 **정확성에는 안 잡히고 추론 논리성에만** 걸린다(2/3). `A61`
    (`sum_operand_fix`)은 발화 조건이 「근거에 결정적 집계 **명세**가 있다」라서 안 닿고,
    `fix_self_contradiction` 은 등식이 **맞는지 자체를 안 본다** — 다른 자리의 산문을
    결과에 맞추는 일만 한다.

    ## 🔴 왜 피연산자를 고치지 않고 유도를 지우는가

    바른 피감수는 `1,828,750,000,000 − 526,570,000,000 = 1,302,180,000,000` 이고 그것은
    최초 공시 합계다. 계산해서 써 넣을 수도 있다. 하지 않는다 — 근거 어디에도 그 수가
    글자로 적혀 있지 않으므로, 써 넣는 것은 **우리가 만든 수를 답변에 넣는 일**이다.
    이 코드베이스가 `fx_conversion` 에서 환산을 직접 계산하지 않은 것과 같은 판단이다.

    거짓 문장을 지우는 것은 언제나 안전하다. 남는 것은 `- 증가분 = **526,570,000,000원**`
    이고, 그 값은 결정적 줄이 준 것이다.

    ## 🔴 언제 침묵하는가

        1. 등식이 성립한다 — 고칠 것이 없다
        2. 결과가 결정적 줄에 없다 — **어느 쪽이 틀렸는지 모른다.** 결과가 틀렸을 수도
           있고 그때 유도를 지우면 틀린 값만 남는다
        3. 꼬리(시스템이 붙인 註)는 건드리지 않는다 — `split_tail` 의 규율이다
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
    #: 뒤에서부터 고친다 — 앞에서 고치면 뒤 구간의 자리가 밀린다.
    for match in reversed(matches):
        left = int(match.group(1).replace(",", ""))
        right = int(match.group(3).replace(",", ""))
        result = int(match.group(4).replace(",", ""))
        got = left + right if match.group(2) == "+" else left - right
        if got == result:
            continue
        if result not in trusted:
            #: 🔴 결과가 결정적 줄에 없으면 어느 쪽이 틀렸는지 모른다.
            continue
        text = match.group(0)
        kept = text[text.rfind("=") + 1:].lstrip()
        body = body[:match.start()] + kept + body[match.end():]
        fixes.append(f"{left:,} {match.group(2)} {right:,} = {result:,} "
                     f"(실제 {got:,}) 유도를 지웠다")
    return body + tail, fixes


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
    """가산 항목의 신원. 합계 줄 밑에 항목마다 회사·계약명·금액·접수번호를 적는다.
    플래그가 꺼져 있으면 빈 문자열이라 합계 줄이 종전과 글자 하나 다르지 않다.

    🔴 **왜 이것이 필요한가.** 합계 줄은 총액과 피가수의 *값*만 실었다. **어느 공시가
    그 피가수인지는 안 실었다.** `Q-115` 실측(서버 3회 전부 같은 모양):

        결정적 집계   위 근거 2건의 금액 합계: 106,796,802,000 + 1,652,800,000,000
                     = 1,759,596,802,000원
        답변          계약 넷을 열거하고 → = 1,759,596,802,000원

    총액은 이 줄에서 그대로 베끼고 가산 항목은 자기가 다시 골랐다. 「2025년 공시분 ·
    계약상대 현대자동차」 조건을 근거 블록 다섯에 처음부터 다시 적용해야 했고 틀렸다.
    답변 안에서 열거와 합계가 안 맞으므로 심사자가 네 수를 더해 보면 그것이 보인다.

    Graph of Thoughts 가 이 공백을 정의한다 — CoT 는 사슬이고 ToT 는 트리이며 **둘 다
    여러 갈래를 합치는 연산이 없다.** aggregation 변환이 그것을 메운다. 우리는 병합을
    이미 결정적으로 했으므로 새로 계산할 것이 없고, **한 것을 보여 주기만** 하면 된다.
    See paper/idea/idea.md §3.1 · docs/design-rationale.md#aggregate-scope

    재생성으로는 못 고친다. `Q-115` 는 3회 모두 재생성이 돌고 3회 모두 실패 종류가 그대로라
    버려졌다(`kept=original`). 판단이 생성보다 쉽다는 SWE-Lancer 의 관측과 같은 방향이다.
    """
    #: 빈 목록에 꼬리만 붙으면 "위 0건뿐이다" 라는 거짓 문장이 근거에 실린다. `line()` 이
    #: 먼저 막지만 여기서도 막는다 — 근거에 들어가는 문장은 두 곳에서 지킨다.
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
        #: 🔴 갈래가 둘 이상이면 **두 중간 합계도 싣는다.** 종전은 갈래별 차와 총 차만
        #: 냈고, 답변은 「정정 후 합계」와 「정정 전 합계」를 스스로 더하다 틀렸다.
        #: `Q-113` 실측 — r3 이 `499,950,000,000 + 1,328,800,000,000` 을
        #: `1,738,750,000,000` 이라 적었다(참값 `1,828,750,000,000`). 근거에 없는 수라
        #: provenance 관문에 걸려 추론 논리성이 깎였고, r1 은 아예 총 차를 피감수 자리에
        #: 다시 써서 `equation` 에 걸렸다. **두 판 다 이 두 수가 없어서 생긴 일이다.**
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

#: 스텁의 경계. 골든셋 자신이 쓰는 개념이다 — `Q-105` 요점 4 가 *"100자 미만 스텁"* 이라
#: 적었다. 분량이 아니라 **범주**를 가르는 선이므로 이 값이 규칙의 전부다.
STUB_CHARS = 100

#: 정기공시 근거 블록의 머리글. `tools._section_markdown` 이 쓰는 모양과 짝이다.
_PERIODIC_HEAD = re.compile(
    r"^\[(사업보고서|반기보고서|분기보고서(?:\s*[13]분기)?)\s*([0-9]{4})\]\s*"
    r"(.+?)\s+–\s+(.+?)(?:\s+\[\d+/\d+ 조각\])?$")


#: 구성 표의 합계 행. `| 합 계 | | | 80,318 | 100.0 | …` 처럼 **같은 행에 100(%)** 이 있는
#: 것만 받는다. 백분율 100 이 그 표가 「전체를 나눈 표」임을 말하고, 그 합계가 백분율의
#: 분모다.
_COMPOSITION_TOTAL = re.compile(
    r"(?m)^\|\s*(?:합\s*계|소\s*계)\s*\|(?=.*(?<![\d.])100(?:\.0)?(?![\d.])).+$")
#: 질문이 구성·비중을 묻는가. 이 줄은 그 질문에만 든다 — 아무 표의 합계나 내면 근거에
#: 합계 행이 열 개 넘는 문항에서 **어느 것이 답인지 모르는 수**를 내놓는다(`Q-080` 실측
#: 11개). `_sum_rows` 가 침묵을 기본값으로 둔 것과 같은 판단이다.
_COMPOSITION_ASK = re.compile(r"구성|비중|비율.*알려|어떻게.*이루")
#: 행에서 뽑을 금액. 백분율(`100.0`)과 섞이지 않게 쉼표 묶음만 받는다.
_ROW_AMOUNT = re.compile(r"\d{1,3}(?:,\d{3})+")


def composition_total(evidence: str, question: str) -> str:
    """구성을 묻는 질문에서 그 표의 합계를 한 줄로 못 박는다. LLM 호출 없음.

    발화하지 않으면 "" 를 돌려준다.

    ## 왜 필요한가

    `Q-080` 은 *"영업수익의 **구성**을 알려줘"* 이고 골든 요점1~3 이 백분율
    (91.6% · 0.9% · 7.5%)을 요구한다. 답변은 3회 모두 백분율만 적고 **그 분모(합계
    80,318백만원)를 안 밝힌다.** 백분율은 분모 없이는 뜻이 없다.

    ## 🔴 왜 아무 합계나 못 내는가

    `Q-080` 의 근거에는 합계 행이 **11개** 있다(대손충당금 · 재고 · 리스 …). 통째로 내면
    어느 것이 답인지 모르고, 하나를 골라 틀리면 **자신 있게 틀린 수**가 된다.

    그래서 두 겹으로 좁힌다.

        1. 질문이 구성·비중을 묻는다
        2. 같은 행에 백분율 100 이 있는 합계 행이 **정확히 하나**다

    112문항 전수 대조: 조건 1 만으로는 2문항(`Q-043` 6행 · `Q-080` 1행), 조건 2 까지
    걸면 **`Q-080` 하나**다. `Q-043` 은 6행이라 모호하므로 침묵한다 — 침묵이 기본값이다.

    ## 단위는 안 적는다

    `[결정적 단위]` 줄이 이미 근거에 그것을 낸다(`위 표의 금액 단위: 백만원 = 1,000,000원`).
    같은 것을 두 줄에 적으면 한쪽만 고쳐질 수 있다.
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


#: 질문이 **부문별 매출**을 묻는가. 비교·차이 문항은 안 받는다 — 부문 분해가 답이 아니고
#: 잡음이다(`Q-049` *"어느 쪽이 큰지"* · `Q-051` *"차이는 몇 백만원인가"* 가 그 예다).
_SEGMENT_ASK = re.compile(r"부문별\s*매출|사업부문.*매출|부문.*매출액")
#: 부문 소계 삼중항의 가운데·끝 행. `| 내수 | 191,737 | … |` 다음 `| 합계 | 5,198,502 | … |`.
_DOMESTIC_ROW = "내수"
#: 부문 이름이 아닌 것. 총계 행을 부문으로 세면 질문이 묻지 않은 수가 답에 들어간다
#: (`Q-104` 의 합계 요점은 질문 범위 밖이라 이미 걷어냈다).
_NOT_A_SEGMENT = ("합계", "합 계", "계", "소계", "소 계", "총계", "총 계")


def _row_cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def segment_sales(evidence: str, question: str) -> str:
    """부문별 매출을 **수출+내수로 검산해** 한 줄로 못 박는다. LLM 호출 없음.

    발화하지 않으면 "" 를 돌려준다.

    ## 왜 필요한가

    `Q-104`(*"삼성전기 제53기 사업부문별 매출을 알려줘"*)의 근거에는 같은 공시 안에 표가
    **둘** 있고 부문값이 1 씩 다르다.

        주요 제품 및 서비스   컴포넌트 5,198,501 · 광학솔루션 3,814,205   (비중 표)
        매출실적             컴포넌트 5,198,502 · 광학솔루션 3,814,204   (수출/내수 표)

    답변은 세 판 모두 앞의 표를 읽었고 골든은 뒤의 표를 쓴다. **둘 다 내적으로 맞고
    합계는 11,314,459 로 같다** — 원문 자체의 반올림 차다. 그래서 골든을 고칠 수 없고
    답변을 틀렸다고 할 수도 없다. 어느 쪽이 더 단단한지를 가려 주는 것이 처방이다.

    ## 무엇으로 가리는가 — 산술

    뒤의 표는 `수출 + 내수 = 합계` 가 세 부문 모두 정확히 맞는다.

        5,006,765 + 191,737 = 5,198,502
        2,266,771 +  34,982 = 2,301,753
        3,615,414 + 198,790 = 3,814,204

    앞의 표는 그런 검산 대상이 없다. 유도되는 쪽을 내는 것이므로 이 줄은 **취향이 아니라
    검산**이다. 그리고 검산이 안 맞는 삼중항은 아예 담지 않으므로, 이 줄이 나왔다는 것
    자체가 그 값이 표 안에서 더해진다는 뜻이다.

    ## 🔴 무엇을 안 담는가

    총계 행(`합 계 | 수출 10,888,950 | 내수 425,509`)은 부문이 아니라서 뺀다. `Q-104` 의
    합계 요점은 *"사업부문별 매출을 알려줘"* 의 범위 밖이라 이미 걷어냈으므로, 여기서
    다시 실으면 걷어낸 것을 되돌리는 셈이다.

    부문이 둘 미만이면 침묵한다 — 하나는 「부문별」이 아니다.

    ## 사정거리

    112문항 전수 대조: 표 꼴만으로는 3문항(`Q-049` · `Q-051` · `Q-104`), 질문 필터까지
    걸면 **`Q-104` 하나**다. 침묵이 기본값인 것은 `composition_total` 과 같다.
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
            #: 🔴 검산이 안 맞으면 담지 않는다. 표가 우리 짐작과 다른 꼴이라는 뜻이고,
            #: 그때 값을 내면 **자신 있게 틀린 수**가 된다.
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

    🔴 Only a **categorical** split speaks. Measured across 112 questions
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
        #: 🔴 머리글 다음의 `- 출처: 접수번호 …` 메타 줄은 본문이 아니다. 그 줄이
        #: 40자쯤 되므로 함께 세면 88자 스텁이 128자가 되어 `STUB_CHARS` 를 넘어가고,
        #: 규칙이 조용히 발화하지 않는다. 첫 판이 정확히 그렇게 틀렸다.
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
        #: 같은 종류가 여러 조각으로 실릴 수 있으므로 종류별 최대 분량으로 접는다.
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


#: 명세 한 줄: `  · 회사 계약명 — 1,234원 (접수번호 20250101000001)`
_ROSTER_ITEM = re.compile(
    r"^\s+· (?P<text>.+?) — (?P<amount>[\d,]+)원 \(접수번호 (?P<rcept>\d{14})\)$", re.M)
#: 답변이 스스로 쓴 가산식: 피가수 둘 이상과 결과. `fix_self_contradiction` 의 `_SELF_EQ`
#: 는 피가수를 정확히 둘만 받으므로 열거가 셋 이상인 이 결함에는 안 닿는다.
#: 🔴 굵게 표시가 `**1,759,596,802,000원**` 이라 닫는 `**` 가 `원` **뒤에** 온다. 첫 판이
#: `**총액**원` 을 기대해 3회 모두 식을 못 고쳤다(`eq=0`). 양쪽 표시를 따로 받는다.
_SUM_EQ = re.compile(r"(?P<terms>[\d,]{7,}(?:\s*\+\s*[\d,]{7,}){1,})\s*=\s*"
                     r"(?P<open>\*{0,2})\s*(?P<total>[\d,]{7,})\s*원(?P<close>\*{0,2})")
#: 항목 줄로 볼 최소 금액. 연도(2025)·비율·건수를 항목으로 오인하지 않는다.
MIN_ITEM_AMOUNT = 1_000_000
#: 개수를 말하는 우리말 수관형사. 여섯까지면 관측된 최대 열거 폭(6)을 덮는다.
_COUNT_WORD = {1: "한", 2: "두", 3: "세", 4: "네", 5: "다섯", 6: "여섯"}
#: 합산을 말하는 낱말. 개수 정정의 사정거리를 이 낱말이 있는 줄로 묶는다.
_SUM_WORD = re.compile(r"합계|합하|합산|더하")
#: `두 건` · `2건` 처럼 개수를 말하는 자리. 앞에 숫자·쉼표가 붙은 것은 금액의 일부다.
_COUNT_MENTION = re.compile(
    r"(?<![\d,])(" + "|".join([*map(re.escape, _COUNT_WORD.values()), r"\d"])
    + r")(\s*)건")


def _roster_of(evidence: str) -> list[dict]:
    """근거의 결정적 집계 명세를 항목 목록으로 읽는다.

    `summand_roster` 가 쓴 것을 그대로 되읽는다. 우리가 쓴 문면만 읽으므로 오탐률이
    구조적으로 묶인다 — `repair_gap` 이 같은 규율을 쓴다.
    """
    return [{"text": m.group("text").strip(), "amount": m.group("amount"),
             "rcept": m.group("rcept")}
            for m in _ROSTER_ITEM.finditer(evidence or "")]


def _item_amount(line: str) -> str:
    """그 줄이 열거한 금액, 없으면 "". 항목 줄만 골라낸다."""
    if not re.match(r"^\s*(?:[-·*]|\d+[.)])\s", line):
        return ""
    for found in re.findall(r"[\d,]{7,}", line):
        if int(found.replace(",", "")) >= MIN_ITEM_AMOUNT:
            return found
    return ""


def fix_stray_summands(answer: str, evidence: str) -> tuple[str, str]:
    """답변의 열거가 명세와 다를 때 명세로 갈아 끼운다. LLM 호출 없음.

    반환은 (새 답변, 사유). 발화하지 않으면 (원본, "").

    ## 왜 이것이 필요한가

    `repair_gap` 은 **모자란 것**을 붙인다 — 결정적 줄의 숫자 중 절반 미만이 답변에 있을
    때 발화한다. `Q-115` 는 반대다. 결정적 줄의 숫자 셋 중 둘(피가수 하나와 총액)이 답변에
    있어 그 검사를 통과하는데, **없어야 할 금액이 더 들어 있다.**

    실측(`A59` 서버 3회 · `scratch/audit/gate_a59.json`):

        명세    차세대 ERP 106,796,802,000 (20251128800125)
               완성차 해상운송계약 1,652,800,000,000 (20250117800487)
        답변    차세대 ERP 106,796,802,000 (20251128800125)     ← 그대로 따랐다
               완성차 해상운송계약 890,200,000,000 (20250227800164)
               완성차 해상운송계약 1,345,900,000,000 (20250117800494)
               합계 = 1,759,596,802,000원                        ← 우리 총액

    근거에 같은 계약명이 여러 건 실려 있고 명세가 접수번호로 하나를 지목했는데 모델이
    다른 것을 골랐다. **근거에 정답을 놓아 주는 것으로는 안 되고 판단이 해야 한다** —
    SWE-Lancer 의 판단 45% vs 생성 26%, QA.pdf 의 *"정답 근거가 있는데도 자신 있게 틀리는
    것"* 이 같은 자리를 가리킨다.

    ## 수리 방향이 정해지는 이유

    **총액은 맞고 피가수가 틀렸다.** 총액은 우리가 계산해 명세에 실은 값이고 답변이 그것을
    베꼈다. 그러므로 눈감고 재계산하면(피가수를 더해 총액을 갈면) 맞는 답을 틀리게 만든다.
    갈아야 하는 쪽은 피가수이고, **옳은 피가수 집합을 명세가 이미 안다.**

    ## 세 겹으로 좁힌 발화 조건

        1. 근거에 명세가 있다 (`aggregate_items` 가 켜져 있다)
        2. 답변이 명세의 총액을 적었다 — 우리 값을 베낀 답변만 손댄다
        3. 답변이 열거한 금액 집합이 명세와 다르다

    셋 중 하나라도 아니면 원본을 글자 그대로 돌려준다.

    See docs/design-rationale.md#aggregate-scope · paper/idea/idea.md §3.1
    """
    #: 🔴 파이프라인도 막지만 여기서도 막는다. 이 함수는 **답변 본문을 재작성하는** 유일한
    #: 자리이므로, 플래그를 빼먹은 호출자가 모든 팔의 답변을 조용히 갈아 버리는 일이
    #: 생기지 않게 한다. `summand_roster` 와 같은 규율이다.
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
        #: 우리 총액을 안 쓴 답변은 이 결함이 아니다. 손대면 다른 계산을 망친다.
        return answer, ""

    wanted = [r["amount"] for r in roster]
    lines = body.split("\n")
    listed = [_item_amount(line) for line in lines]
    if {a for a in listed if a} == set(wanted):
        return answer, ""

    #: 어긋난 항목 줄을 명세 항목으로 **제자리에서** 갈아 끼운다. 지우기만 하면 회사
    #: 머리글이 항목 없이 남는다.
    unused = [r for r in roster if r["amount"] not in listed]
    swapped, dropped = 0, 0
    removed: list[str] = []
    for index, amount in enumerate(listed):
        if not amount or amount in wanted:
            continue
        if amount.replace(",", "") == total.replace(",", ""):
            #: 🔴 **총액을 항목으로 착각해 지우던 자리다.** `_item_amount` 는 `-`·`·`·`*`
            #: ·`N.`·`N)` 로 시작하는 줄을 열거 항목으로 보는데, 답변이 결론을 목록으로
            #: 쓰면(`3. 합계: 1,759,596,802,000원`) 그 줄도 항목이 된다. 총액은 피가수
            #: 집합(`wanted`)에 없으므로 「어긋난 항목」으로 판정돼 통째로 사라졌다.
            #:
            #: `Q-115` r3 실측: `substitute` 가 정답을 넣었는데 이 자리가 그것을 지웠다.
            #: 같은 회차의 다른 실행은 결론을 **평문**으로 써서 살아남았다 — 즉 답이
            #: 목록이냐 문장이냐 하는 **문체 하나로 정답이 갈렸다.**
            #:
            #: 이 함수는 총액이 본문에 있을 때만 발화한다(위 `if total not in body`).
            #: 그 총액을 스스로 지우는 것은 자기 전제를 무너뜨리는 일이다.
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

    #: 답변이 스스로 쓴 가산식도 명세의 피가수로 다시 쓴다. 식을 그대로 두면 항목은
    #: 고쳐지고 식만 틀린 채 남아 자기 모순이 유지된다.
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

    #: 🔴 항목을 뺐으면 개수를 말하는 문장도 같이 틀린다. 실측 r2: "이제 이 **세 건**의
    #: 금액을 합하면" 뒤에 두 건이 남았다. 우리 수리가 만든 모순이므로 우리가 닫는다.
    #: 겹치는 낱말을 잘못 갈지 않도록 **수리 전 항목 수와 같은 개수만** 바꾼다.
    #: 범위는 **합산을 말하는 줄**로 묶는다. 본문 아무 곳의 `N건` 을 갈면 무관한 개수를
    #: 망친다(정정 2건 같은 것). 합계·합하·더하·합산 이 있는 줄만 본다.
    #:
    #: 🔴 첫 판은 "수리 전 항목 수와 같은 개수만" 갈게 했는데 발화하지 않았다. r2 는 항목
    #: 넷을 열거하면서 "세 건" 이라 적었다 — **우리 수리 전에 이미 틀린 문장**이었다. 우리가
    #: 만든 모순이 아니어도 우리가 손댄 답변에 남으므로 같이 닫는다.
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
    #: 🔴 꼬리는 수리 **전에** 만들어졌다. 본문에서 뺀 금액을 검증기가 결함으로 적어 두면
    #: 답변이 자기를 반박한다 — 실측 `Q-107`: 본문은 4,928,968,079,043 으로 고쳐졌는데
    #: 꼬리가 여전히 "답변 490,881,723,100원 vs 재계산" 을 말했다. `substitute` 가 쓰는
    #: `_prune_notes` 를 그대로 쓴다. 해소된 결함을 미해결로 남기는 것도 틀린 것이다.
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
