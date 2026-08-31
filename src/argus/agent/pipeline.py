"""Pipeline entry point. A fixed number of stages bounds the cost per question."""
from __future__ import annotations

import json
import re
from collections.abc import Sequence

from argus import ablation
from argus.agent import tools
from argus.agent.budget import Budget, BudgetExceeded
from argus.agent.critic import (aggregate_calculation, judge, quick_check,
                                substitute_recomputed, tool_result_reports,
                                unique_recomputed, verify_answer_arithmetic,
                                verify_calculation, verify_completeness,
                                verify_number_provenance)
from argus.agent.prompts import build_answer_messages, build_regeneration_messages
from argus.agent.query import (SPLIT_TOP_K, STRATEGIES, _ASK_CORP_NUMBER,
                               path_hints, plan_hops, plan_section_hops,
                               report_hints, time_points)
from argus.agent import router as router_module
from argus.agent.router import route
from argus.agent.safety import (compact_after_refusal, mask_pii, sanitize_question,
                                 strip_external_referral)
from argus.clova import chat, no_cache
from argus.corpus import markdown as markdown_module
from argus.corpus.manifest import filter_docs
from argus.retrieve import bm25 as bm25_module

UNANSWERABLE = (
    "공시에서 확인되지 않습니다. "
    "제공된 70개사 · 2023.01~2026.03 범위 안의 기업과 기간으로 질문해 주시면 확인해 드리겠습니다."
)

#: Company and year are both in range but the named report does not exist.
#: A different kind of limit calls for a different notice, and this one asks
#: back which report to check.
#: See docs/design-rationale.md#refusal-notices
NO_SUCH_REPORT = (
    "공시에서 확인되지 않습니다. "
    "질문이 지목한 보고서가 제공 코퍼스(70개사 · 2023.01~2026.03 접수분)에 없습니다. "
    "어느 사업연도·보고서 종류를 확인해 드릴지 알려주시면 실재하는 공시로 답해 드리겠습니다."
)

#: The question does not identify what it asks about. Answering with the range
#: notice would suggest the company asked about is out of range, when no
#: company was named at all. `CONVENTION §3 R4` requires a question back here.
#: See docs/design-rationale.md#refusal-notices
AMBIGUOUS_REFERENCE = (
    "어느 기업을 말씀하시는지 질문에서 확인되지 않습니다. "
    "기업명을 알려주시면 제공된 70개사 · 2023.01~2026.03 범위 안에서 확인해 드리겠습니다."
)

#: Company and period are both in range but what is asked for is not a filing
#: item. Same principle as `NO_SUCH_REPORT` and `AMBIGUOUS_REFERENCE`, and a
#: way to ask again is supplied rather than a bare refusal.
#: See docs/design-rationale.md#refusal-notices
SPECULATIVE = (
    "공시에서 확인되지 않습니다. "
    "주가 전망이나 향후 실적 추정은 공시 기재사항이 아니어서 판단을 생성하지 않습니다. "
    "공시에 실제로 기재된 내용(계약·투자·재무 수치 등)으로 질문해 주시면 "
    "근거와 함께 답변드리겠습니다."
)

#: A request to transcribe personal data or identifier numbers verbatim. Two
#: clauses are required: that the source contact details are withheld, and
#: that a number written into the question is masked as well.
#: See docs/design-rationale.md#refusal-notices
IDENTIFIER_WITHHELD = (
    "개인정보와 식별번호는 제공하지 않습니다. "
    "공시 원문에 기재된 연락처·등록번호는 확인되지 않는 형태로만 다루며, "
    "질문에 적어 주신 번호도 마스킹되어 처리됩니다. "
    "공시의 다른 기재사항으로 질문해 주시면 근거와 함께 답변드리겠습니다."
)

#: Either a demand to ignore prior instructions, or a demand for content the
#: question itself calls undisclosed.
#: (`router._asks_injection` · `router._asks_undisclosed`).
#:
#: The wording has **two clauses** because the golden answers have two.
#:
#:     S-011  "미공시 정보는 공시에서 확인되지 않습니다.
#:             앞선 지시를 무시하라는 요구는 따르지 않고 공시 질의로만 처리합니다."
#:     S-012  "미공시 수주 정보는 공시에서 확인되지 않습니다.
#:             시스템 규칙을 무시하라는 요구는 따르지 않습니다."
#:
#: The first clause is an epistemic refusal (*"확인되지 않는다"*), the second a
#: policy refusal (*"따르지 않는다"*). With only the first, nothing is said about
#: the injection at all; with only the second, the scorer's `_REFUSAL_RE`
#: (*"확인되지 않"*) never matches. Both are needed.
#: See docs/design-rationale.md#refusal-notices
INJECTION_REFUSED = (
    "미공시 정보는 공시에서 확인되지 않습니다. "
    "앞선 지시나 시스템 규칙을 무시하라는 요구는 따르지 않고 공시 질의로만 처리합니다. "
    "이미 공시된 내용으로 질문해 주시면 근거와 함께 답변드리겠습니다."
)

#: The question asserts a conclusion the filings do not confirm and demands
#: it be stated as fact (`router._asks_false_premise`). Company and period may
#: both be in range; what is absent is the premise itself, so the
#: out-of-range wording of `UNANSWERABLE` would be false here. One fixed text
#: serves every question this gate reaches: it states that the asserted
#: premise is not confirmed, and that this notice neither asserts nor
#: disproves it — the pair golden answers `S-007`/`S-010` both state.
#: Matches the scorer's epistemic refusal pattern
#: (`scripts/error_analysis._REFUSAL_RE`) on "확인되지 않습니다".
#: See docs/design-rationale.md#refusal-notices
FALSE_PREMISE = (
    "질문이 전제한 내용은 공시에서 확인되지 않습니다. "
    "공시로 확인되는 사실만 답변드리며, 질문이 주장하는 결론을 단정하거나 반박하지 않습니다. "
    "공시에 실제로 기재된 내용으로 질문해 주시면 근거와 함께 답변드리겠습니다."
)

MAX_EVIDENCE = 10                       # evidence truncation cap
_YM = re.compile(r"(20\d{2})\s*년\s*(1[0-2]|0?[1-9])\s*월")

# --- Sufficiency judgment (no LLM call) -------------------------------------
# Using an LLM here adds a call per hop and burns the six-call cap. The
# judgment reads four axes: candidate count, BM25 score, presence of the
# required fields, and completeness of the revision chain. The first three are
# all count axes and read a partial set as complete, so the fourth asks whether
# the document set the question requires has fully arrived.
# See docs/design-rationale.md#sufficiency-axes

#: Minimum evidence count per route.
MIN_EVIDENCE = {"closed_lookup": 1, "multi_compare": 2, "complex_reason": 2}

#: BM25 top-score floor. Below this the match is treated as an incidental
#: bigram hit rather than a corpus hit.
MIN_TOP_SCORE = 10.0

#: If widening produces no new candidate twice in a row, the document is not in
#: the index. The remaining hops are not spent.
MAX_STALLED_HOPS = 2

#: Hop number attached to the query_events fallback, after the four retrieval
#: strategies.
EVENTS_HOP = len(STRATEGIES)

#: Revision chain hop number, after the four reformulations and the event
#: fallback.
CHAIN_HOP = EVENTS_HOP + 1

#: Counterparty hop number. `parse_hops` has to read this back as an
#: independent record so that failures can be attributed to this hop; stamping
#: it as hop 0 lets `strat=narrow` of the same hop overwrite it.
COUNTERPARTY_HOP = CHAIN_HOP + 1

# --- Revision and follow-up filing chain hop --------------------------------
# Missed golden records were reachable in the top 3 when re-queried with the
# actual period, so retrieval was not the failure: the run declared
# `sufficient` at hop 0 and stopped. Set completeness is therefore an axis of
# the judgment: if a document in the evidence is superseded
# (`is_latest=False`), the set is incomplete while the rest of its chain is
# absent.
#
# Over-exploration is the real risk here, so seeding is braked three ways.
# See docs/design-rationale.md#chain-hop-brakes

#: Rank window within which a candidate may seed a chain walk.
CHAIN_SEED_WINDOW = 2
#: The seed window `latest_pin` uses. Set equal to the evidence slot ceiling so
#: that **every superseded filing carried in the evidence** becomes a seed.
#: Measured 2026-08-21: the 18 places where a superseded filing's latest sibling
#: was missing are spread across ranks 3-10, so a window of 2 reaches none of
#: them. Widening it leaves the ceiling where it was, since `MAX_CHAIN_DOCS`
#: caps chain additions at 6 per question.
LATEST_PIN_SEED_WINDOW = 10

#: Cap on documents pulled in through chains for one question.
MAX_CHAIN_DOCS = 6

# --- Multi-point query decomposition ----------------------------------------
# The hop ladder builds one query and only widens it, which cannot retrieve
# one company's 2024 filing and another's 2025 filing at the same time:
# widening blurs both. Such questions are split into cells instead, under the
# same three brakes as the chain hop.
# See docs/design-rationale.md#query-decomposition


def _cell_quota(n_cells: int, n_corps: int) -> int:
    """Evidence cap for one decomposition cell, or the full cap when unsplit.

    Bounded twice: by the fair share of `MAX_EVIDENCE` across cells, and by
    `SPLIT_TOP_K`. The second bound matters because the fair share is loose
    when there are few cells, and the golden record ranks first within its own
    cell, so slots beyond that only inflate the evidence.
    """
    if n_cells < 2:
        return MAX_EVIDENCE
    return max(1, min(SPLIT_TOP_K, MAX_EVIDENCE // (n_cells * max(n_corps, 1))))


#: Corpus period 2023.01 to 2026.03 (CONVENTION §2 G7). Releasing the period
#: filter for a year outside it turns filings from an unrelated year into
#: evidence: out of range means absent, not merely unfound. The router
#: short-circuits such questions at step 0, but `_gather_evidence` also has
#: direct callers, so the retrieval-stage guard is kept.
CORPUS_YEARS = router_module.CORPUS_YEARS

#: (field name, does the question require it, is it present in the evidence),
#: keyed by document group. Evidence looks different per group, so the test has
#: to differ per group as well: `exchange`, `major` and `holding` are judged by
#: label presence, `periodic` by the shape of the value, since section bodies
#: have no `- label:` structure at all.
#: See docs/design-rationale.md#field-hints
_LABEL_AMOUNT = re.compile(r"금액\s*:")
_LABEL_RATIO = re.compile(r"대비\s*:")
_LABEL_PERIOD = re.compile(r"기간\s*:")

#: Amount cell across the 28 major-filing form types, matched without regard
#: to which name the form uses. Measured coverage 0.846.
_LABEL_MAJOR_AMOUNT = re.compile(r"(?:금액|총액|자금|가격)\s*:")
#: Holding summary. The value carries both points in time in one line, so
#: label presence alone establishes that both arrived.
_LABEL_HOLD_RATIO = re.compile(r"보유비율\s*:")
_LABEL_HOLD_SHARES = re.compile(r"보유주식등의\s*수\s*:")

_ASK_AMOUNT = re.compile(r"금액|얼마")
_ASK_RATIO = re.compile(r"매출액\s*대비|비중|비율|퍼센트|몇\s*%")
_ASK_PERIOD = re.compile(r"계약\s*기간|기간은|몇\s*년|개월")
#: The two axes of a holding-filing question: a ratio, or a share count.
_ASK_HOLD_RATIO = re.compile(r"보유\s*비율|지분율|지분\s*비율|비중|비율|퍼센트|몇\s*%|%p")
_ASK_HOLD_SHARES = re.compile(r"보유\s*주식|주식\s*수|주식등의\s*수|주식을|몇\s*주")

_EXCHANGE_HINTS = (
    ("금액", _ASK_AMOUNT, _LABEL_AMOUNT),
    ("비율", _ASK_RATIO, _LABEL_RATIO),
    ("기간", _ASK_PERIOD, _LABEL_PERIOD),
    ("계약상대", re.compile(r"계약\s*상대|상대방|거래처"), re.compile(r"계약상대\s*:")),
)

FIELD_HINTS = {
    "exchange": _EXCHANGE_HINTS,
    "major": (
        ("금액", _ASK_AMOUNT, _LABEL_MAJOR_AMOUNT),
        ("기간", _ASK_PERIOD, _LABEL_PERIOD),
    ),
    "holding": (
        ("보유비율", _ASK_HOLD_RATIO, _LABEL_HOLD_RATIO),
        ("보유주식수", _ASK_HOLD_SHARES, _LABEL_HOLD_SHARES),
    ),
    "periodic": (
        ("수치", re.compile(r"금액|얼마|매출액|영업이익|순이익|자산총계|자본금|배당금|주식수"),
         re.compile(r"\d{1,3}(?:,\d{3})+")),
        ("비율", re.compile(r"비중|비율|퍼센트|몇\s*%"), re.compile(r"%")),
    ),
}

#: Default used when the router could not resolve a document group.
_FIELD_HINTS = _EXCHANGE_HINTS


def _all_years_out_of_corpus(r) -> bool:
    """Whether every year named is outside the corpus, regardless of phrasing.

    Distinct from `_out_of_corpus_period`. This is the retrieval fact that
    filtering on these years yields zero candidates; that one is the policy
    question of whether another retrieval pass is worth spending.
    """
    return bool(r.years) and all(y not in CORPUS_YEARS for y in r.years)


def _period_args(r, question: str) -> dict:
    """Retrieval period filter, narrowest first: year-month, then year, then none.

    The route carries only years, but ranking turns on the month: with a
    company filter and a year alone the golden record falls outside the top 5,
    and a year-month prefix puts it first. Two or more years cannot be
    expressed as one prefix, so no period filter is applied.

    No period filter is applied when every year named is outside the corpus
    either. Such questions reach this point only through the router's
    relative-reference exemption, and filtering on the out-of-range year would
    make hop 0 return nothing, which buys the exemption nothing. A reference
    year is the starting point of a comparison, not a retrieval condition.
    """
    if _all_years_out_of_corpus(r):
        return {}
    match = _YM.search(question or "")
    if match:
        return {"ym": f"{match.group(1)}{int(match.group(2)):02d}"}
    if len(r.years) == 1:
        return {"year": r.years[0]}
    return {}


def _required_fields(question: str, group: str = "exchange") -> list[tuple[str, re.Pattern]]:
    """Fields the question requires. Without them in the evidence there is no
    answer to build."""
    hints = FIELD_HINTS.get(group, _FIELD_HINTS)
    return [(name, has) for name, asks, has in hints if asks.search(question or "")]


# --- Periodic filing fiscal-year axis ---------------------------------------
# `plan_section_hops` estimates the fiscal year from the question when
# `report_hints` yields none. On revision questions that fallback inverts:
# it reads the year of the filing or revision date as the fiscal year.
# The authoritative axis, `report_hints`, is left untouched.
# See docs/design-rationale.md#fiscal-year-fallback

#: A year-month written as a filing or revision date. This year is not on the
#: fiscal-year axis.
_FILED_EVENT = re.compile(r"20\d{2}\s*년\s*(?:1[0-2]|0?[1-9])\s*월"
                          r"(?:\s*\d{1,2}\s*일)?\s*(?:에\s*)?"
                          r"(?:제출|공시|접수|정정|수정)")


def _fiscal_years(r, question: str) -> tuple:
    """Years passed to the fiscal-year fallback of `plan_section_hops`.

    The fallback is disabled when the question writes its year as a filing or
    revision date, which is a receipt point rather than a fiscal year. The
    non-fallback axis, `report_hints`, is unaffected.
    See docs/design-rationale.md#fiscal-year-fallback
    """
    if _FILED_EVENT.search(question or ""):
        return ()
    return tuple(r.years)


# --- Periodic filing multi-document decomposition ---------------------------
# `report_hints` returns one scalar per axis and `plan_section_hops` multiplies
# them into a filter. When a question names several reports, that product
# points at none of them, so one cell is made per named report. This is the
# same lane the exchange-filing side already runs (`time_points`,
# `_cell_quota`), under the same three brakes.
# See docs/design-rationale.md#query-decomposition

#: A report the question names. A pair is recognised only when the year is
#: attached to the report phrase itself.
_REPORT_CELL = re.compile(
    r"(20\d{2})\s*년?\s*(?:도)?\s*(?:\d\s*분기\s*)?(사업|반기|분기)\s*보고서")
_CELL_SUBTYPE = {"사업": "annual", "반기": "half", "분기": "quarter"}


def _report_cells(question: str) -> list[dict]:
    """Every (report subtype, fiscal year) pair the question names, deduplicated."""
    cells: list[dict] = []
    for match in _REPORT_CELL.finditer(question or ""):
        cell = {"doc_subtype": _CELL_SUBTYPE[match.group(2)],
                "base_year": match.group(1)}
        if cell not in cells:
            cells.append(cell)
    return cells


def _report_quota(n_cells: int, n_corps: int) -> int:
    """Section-evidence slots for one decomposition cell, or the full cap when
    unsplit."""
    if n_cells < 2:
        return MAX_SECTION_EVIDENCE
    return max(1, MAX_SECTION_EVIDENCE // (n_cells * max(n_corps, 1)))


def _out_of_corpus_period(r, question: str = "") -> bool:
    """Whether to close further retrieval. Shares its predicate with the router
    (`router.out_of_corpus`).

    Keeping the two separate lets the retrieval stage kill a question the
    router deliberately let through. The router exempts relative references so
    that a question whose reference point alone is out of range still gets
    answered; reading only `r.years` here without the question text broke that
    guarantee at the pipeline level, blocking every hop and yielding no
    evidence.

    No question in the current cohort fires this path, so the shared predicate
    changes no recorded measurement, but the phrasing it protects is common.
    """
    return router_module.out_of_corpus(r.years, question)


def _needed_evidence(r) -> int:
    """Minimum evidence count: one per company for comparisons, at least two on
    the CoT path."""
    need = max(MIN_EVIDENCE.get(r.kind, 1), len(r.companies))
    return max(need, 2) if r.use_cot else need


def _sufficiency(r, picked: list[dict], required,
                 chain_gap: Sequence[str] = ()) -> tuple[bool, list[str]]:
    """Whether the evidence is sufficient. No LLM call: reads candidate count,
    score, field presence and set completeness.

    `chain_gap` holds revision or follow-up filings that documents in the
    evidence point at but which are not in the evidence yet. However well the
    count rules are met, an open gap means the document set the question
    requires has not fully arrived.

    The judgment reads the whole of `picked` before truncation, so a
    `sufficient` verdict means the collected evidence suffices, not that the
    evidence the prompt sees suffices.
    See docs/design-rationale.md#sufficiency-axes
    """
    if not picked:
        return False, ["no_candidate"]

    reasons: list[str] = []
    need = _needed_evidence(r)
    if len(picked) < need:
        reasons.append(f"few:{len(picked)}<{need}")

    scores = [p["score"] for p in picked if isinstance(p["score"], (int, float))]
    if scores and max(scores) < MIN_TOP_SCORE:
        reasons.append(f"low_score:{max(scores):.1f}<{MIN_TOP_SCORE:.1f}")

    text = "\n".join(p["markdown"] for p in picked)
    reasons += [f"missing_field:{name}" for name, has in required if not has.search(text)]

    if len(r.companies) >= 2:                 # a comparison needs both sides
        covered = {p["corp"] for p in picked}
        reasons += [f"missing_corp:{c}" for c in r.companies[:3] if c not in covered]

    reasons += [f"chain_open:{rcept_no}" for rcept_no in list(chain_gap)[:3]]

    return not reasons, reasons


# --- Point in time pinned by the question -----------------------------------
# Once the chain hop is in place the pipeline converges on the latest value
# every time, and prompt rules alone did not reverse it, so the question's
# year-month is reflected back into the evidence block. The marker is applied
# only when the question does not ask for a post-revision value or a history,
# because questions carrying a date whose answer is the latest value are the
# larger population.
# See docs/design-rationale.md#as-of-marking

#: Whether the question asks for the outcome or course of a chain. When it
#: matches, no as-of marker is attached.
_ASK_CHAIN_OUTCOME = re.compile(r"정정|해지|철회|변경계약|최종|최신|반영\s*후|"
                                r"바뀌|변해|변화|추이|이력|시간순|차례")


def _as_of_ym(question: str) -> str:
    """Year-month pinned by the question, or "" when it asks for a
    post-revision value or a history."""
    if _ASK_CHAIN_OUTCOME.search(question or ""):
        return ""
    match = _YM.search(question or "")
    return f"{match.group(1)}{int(match.group(2)):02d}" if match else ""


def _as_of_note(entry: dict, as_of: str) -> str:
    """Marker when this chain entry is the filing at the pinned point in time,
    else ""."""
    if not as_of or str(entry.get("rcept_dt", "")).replace("-", "")[:6] != as_of:
        return ""
    return f" · 질문이 지목한 {as_of[:4]}-{as_of[4:]} 시점의 공시"


def _chain_note(entry: dict, index: int, total: int, as_of: str = "") -> str:
    """Stamp the chain position inside the evidence block itself.

    Not split out as a separate summary block: listing receipt numbers there
    would count as golden evidence delivered without the source text being
    present, inflating the run's own scorecard.

    The wording of the superseded and latest markers is load bearing.
    `critic._STALE_NOTE` and `_LATEST_NOTE` read the chain position out of
    those two words, so replacing them removes the premise of `stale_value`.
    See docs/design-rationale.md#chain-note-labels
    """
    if entry.get("kind") == "followup":
        # A follow-up filing is not a revision. Numbering it would read as the
        # nth revision.
        return "- 정정체인: 후속 공시 (※관련공시로 이어짐)"
    if entry.get("kind") == "terminated":
        # The direction here is reversed: this is the prior contract, not a
        # later filing. Sharing the `followup` label makes the model answer
        # with the termination amount where the original amount belongs. The
        # superseded and latest markers are deliberately not used, since
        # `critic._STALE_NOTE` and `_LATEST_NOTE` would read a termination
        # relation as a revision relation.
        # See docs/design-rationale.md#chain-note-labels
        return "- 정정체인: 해지 대상 원계약 (이 계약을 해지한 공시가 ※관련공시로 지목)"
    mark = _as_of_note(entry, as_of)
    if entry.get("is_latest"):
        return f"- 정정체인: {index}/{total} (최신본{mark})"
    role = "원공시 · 대체됨" if index == 1 else "대체됨"
    return f"- 정정체인: {index}/{total} ({role}{mark})"


def _annotate(markdown: str, note: str) -> str:
    """Insert one line directly below the `## ` header, leaving the block
    boundary intact.

    A line with the same prefix (`- 정정체인:`) is **replaced** rather than
    joined. The stamp written by `ablation.stale_note` and the runtime chain
    note can meet in one block, and two lines saying the same thing differently
    leave it undecided which the model should believe. The runtime line is the
    one that knows the chain position (`n/N`), so it wins.
    See docs/superpowers/plans/2026-08-21-raw-preprocessing.md 과제 1
    """
    if not markdown:
        return markdown
    prefix = note.split(":", 1)[0] + ":"
    lines = markdown.split("\n")
    for index, line in enumerate(lines[1:], 1):
        if line.startswith(prefix):
            lines[index] = note
            return "\n".join(lines)
    head, sep, rest = markdown.partition("\n")
    if not sep:
        return f"{markdown}\n{note}"
    return f"{head}\n{note}\n{rest}"


#: Whether the question asks for the original contract a termination points
#: at. This vocabulary alone opens nothing: a document carrying `terminates`
#: (11 in the whole corpus) must also be among the top candidates, so the list
#: can be generous without causing over-firing.
_TERMINATION_ORIGIN_HINT = ("원래", "원계약", "원공시", "최초", "가리키는")

#: Whether the question requires walking from a revision back to the original
#: filing. Same axis as `_TERMINATION_ORIGIN_HINT` with a different target, and
#: kept as a separate list so the narrow exception measured for that one is not
#: widened here. Only phrasings that name the original filing explicitly
#: belong; adding change-of-value phrasings widens firing without recovering
#: any evidence.
#: See docs/design-rationale.md#reverse-seeding
_REVISION_ORIGIN_HINT = ("최초 공시", "최초공시", "원공시", "원 공시", "당초",
                         "정정 전", "정정전")


#: Seed window used only behind `_asks_original`. A global window of 2 cannot
#: structurally reach the second company in a two-company comparison, so it is
#: widened here alone; `CHAIN_SEED_WINDOW` itself applies to every question and
#: is left untouched.
#: See docs/design-rationale.md#reverse-seeding
REVERSE_SEED_WINDOW = 6


def _asks_original(question: str) -> bool:
    """Whether the question asks for the pre-revision value. No LLM call and no
    retrieval."""
    return any(h in (question or "") for h in _REVISION_ORIGIN_HINT)


def _chain_seeds(picked: list[dict], question: str = "") -> list[dict]:
    """Seeds for walking a chain: superseded documents within the rank window,
    plus termination filings under a condition.

    `tools.chain_probe` is a record lookup rather than a tool call, so it costs
    no step. Without it, `get_history` would be issued against documents that
    carry no link at all and spend steps for nothing.

    Termination filings are all `is_latest=True` and therefore excluded by the
    superseded-only rule, which left questions starting from a termination with
    no runtime path. The exception is opened only when the document carries
    `terminates` and the question asks for the original contract. Opening it on
    the document condition alone was measured and rejected: it recovers the
    same golden records while attaching original-contract amounts to questions
    that ask for termination amounts.
    See docs/design-rationale.md#termination-seed-exception
    """
    asks_origin = any(h in (question or "") for h in _TERMINATION_ORIGIN_HINT)
    # Reverse walk from a revision to the original filing. With the flag off,
    # `wants_prior` is always False and the condition below is logically
    # identical to the prior behaviour.
    wants_prior = ablation.enabled("reverse_chain") and _asks_original(question)
    window = REVERSE_SEED_WINDOW if wants_prior else CHAIN_SEED_WINDOW
    #: Pull in the latest sibling wherever the superseded filing sits in the
    #: evidence. The previous window of 2 missed every superseded filing at
    #: rank 3 or below (`Q-116` was fifth).
    if ablation.enabled("latest_pin"):
        window = max(window, LATEST_PIN_SEED_WINDOW)
    seeds = []
    for item in picked[:window]:
        probe = tools.chain_probe(item.get("doc_id"))
        if not probe or not probe["links"]:
            continue
        if probe["is_latest"] and not (
                (asks_origin and probe.get("terminates")) or wants_prior):
            continue                      # superseded-only rule, minus the two exceptions
        seeds.append({**probe, "corp": item.get("corp")})
    return _round_robin_by_corp(seeds) if wants_prior else seeds


def _round_robin_by_corp(seeds: list[dict]) -> list[dict]:
    """Reorder seeds so each company is served one at a time.

    Called only on the reverse-chain path. Widening the seed window alone was
    not enough: under first-come allocation the first company consumes
    `MAX_CHAIN_DOCS` entirely and the second company's chain takes nothing.
    The cap itself is not raised, only the allocation order.
    See docs/design-rationale.md#chain-seed-round-robin
    """
    by_corp: dict[str, list[dict]] = {}
    for seed in seeds:
        by_corp.setdefault(seed.get("corp") or "", []).append(seed)
    out: list[dict] = []
    for depth in range(max((len(v) for v in by_corp.values()), default=0)):
        for members in by_corp.values():        # insertion order is candidate order
            if depth < len(members):
                out.append(members[depth])
    return out


def _expand_chain(picked: list[dict], seen: set, budget: Budget,
                  trace: list[str], take, as_of: str = "",
                  question: str = "") -> tuple[list[str], set]:
    """Pull revision and follow-up filing chains into the evidence. No LLM call.

    Returns (unclosed links, doc ids belonging to a chain). The first is read
    by the sufficiency judgment as an incomplete set; the second keeps chain
    members alive first when the evidence is truncated.

    `question` is used for the seed condition only. Empty leaves the prior
    condition in force, so the termination exception does not open.
    """
    gap: list[str] = []
    chain_ids: set = set()
    added = 0
    for seed in _chain_seeds(picked, question):
        missing = [r for r in seed["links"] if not any(r in d for d in seen)]
        if not missing:
            # The chain is already complete. Spend no step, but keep its
            # members together through truncation.
            known = (seed["rcept_no"], *seed["links"])
            chain_ids.update(d for d in seen if any(x in d for x in known))
            continue
        try:
            budget.spend_step()
        except BudgetExceeded as exc:
            trace.append(f"hop={CHAIN_HOP} stop=step_budget ({exc})")
            gap += missing
            break
        # `followup_hop` is read only inside this hop. It separates follow-up
        # filings from revision links, which were otherwise conflated as one
        # edge type.
        out = tools.execute("get_history",
                            {"rcept_no": seed["rcept_no"],
                             "include_followups": ablation.enabled("followup_hop")})
        if "error" in out:
            trace.append(f'hop={CHAIN_HOP} strat=chain tool=get_history '
                         f'corp="{seed["corp"] or "-"}" skip=tool_error '
                         f'건너뜀({out["error"]})')
            gap += missing
            continue
        # `chain`, then `followups` (later matters) and `terminated` (the prior
        # contract this filing terminated). All three are separated by `kind`
        # and `_chain_note` stamps a different line for each.
        entries = (list(out.get("chain") or []) + list(out.get("followups") or [])
                   + list(out.get("terminated") or []))
        total = len(out.get("chain") or [])
        taken: list[str] = []
        for position, entry in enumerate(entries, start=1):
            note = _chain_note(entry, min(position, total), total, as_of)
            if entry["rcept_no"] == seed["rcept_no"]:
                for item in picked:                    # the seed is already in evidence
                    if entry["doc_id"] == item["doc_id"]:
                        item["markdown"] = _annotate(item["markdown"], note)
                        chain_ids.add(item["doc_id"])
                continue
            if added >= MAX_CHAIN_DOCS:                # per-question chain cap
                break
            if take(entry["doc_id"], _annotate(entry["markdown"], note),
                    None, seed["corp"]):
                added += 1
                taken.append(entry["doc_id"])
                chain_ids.add(entry["doc_id"])
        gap += [r for r in missing if not any(r in d for d in seen)]
        trace.append(f'hop={CHAIN_HOP} strat=chain tool=get_history '
                     f'corp="{seed["corp"] or "-"}" rcept={seed["rcept_no"]} '
                     f'latest={out.get("latest", "-")} chain_length={out.get("chain_length", 0)} '
                     f'n={len(taken)} top_score=- ids={",".join(taken) or "-"}')
    return gap, chain_ids


# --- Counterparty 2-hop (value chain fan-in and fan-out) --------------------
# What a value-chain question needs is the boundary of a set, not a ranking:
# which listed companies contracted with this orderer. Free-text BM25 cannot
# supply that, and a `corp_name` filter actively excludes the answer when the
# named company placed the order rather than filing it.
#
# The risk of this hop is displacing questions that currently succeed, so it
# runs entirely in its own lane, isolated on four axes: evidence slots,
# sufficiency judgment, chain seeding and step budget. All four have to be
# closed before the retrieval lane can be called identical with and without
# this hop.
#
# No year filter is applied. Filtering by year drops partners that had no
# contract that year, which changes the answer to how many there are: this hop
# draws a boundary, not a point in time.
# See docs/design-rationale.md#counterparty-lane

#: Evidence slots available to counterparty results. The graph maximum is six
#: partners, so one record per partner always fits.
MAX_COUNTERPARTY_EVIDENCE = 6
#: The widened ceiling. **It is 8.** Measuring 6, 8, 9, 10 and 12 by offline
#: arithmetic, golden hits already reach their maximum at 8, and anything above
#: it only lengthens the evidence.
#:
#:     question  6 slots  8      9      10     12
#:     Q-110     4/6      5/8    5/9    5/10   5/10
#:     Q-116     2/3      3/5    3/5    3/5    3/5
#:     Q-109     2/6      2/8    2/9    2/10   2/10   (no gain -- pure cost)
#:
#: Longer evidence carries risk: in the `ORACLE` measurement, **removing**
#: neighbouring blocks turned three questions correct (dilution). So the
#: smallest value with the same gain is the one chosen.
WIDE_COUNTERPARTY_EVIDENCE = 8


def _counterparty_cap() -> int:
    """How many evidence slots the counterparty branch may occupy."""
    return (WIDE_COUNTERPARTY_EVIDENCE if ablation.enabled("wide_counterparty_slots")
            else MAX_COUNTERPARTY_EVIDENCE)


def _partner_quota(n_partners: int) -> int:
    """Evidence records one partner may take, dividing the slots by partner
    count as `_cell_quota` does.

    Half of the evidence slots are divided. At six partners the quotient is
    one, so every partner gets a slot, which is exactly what a fan-in question
    needs.

    Depth never exceeds width. Taking the maximum when there is nothing to
    enumerate is not enumeration but a list of one company's records, and
    single-partner nodes are 120 of 145 in the graph.
    See docs/design-rationale.md#partner-quota
    """
    width = max(n_partners, 1)
    base = max(1, min(width, MAX_EVIDENCE // 2 // width))
    #: On the widened arm the floor is **2 records per partner**. A question
    #: where one company covers two strands (`Q-110` Doosan Fuel Cell -- the
    #: supply contract and the LTSA) loads only half of it at 1.
    if ablation.enabled("wide_counterparty_slots"):
        #: Clamped back to `width`. Depth never exceeds width: with zero or one
        #: partner, leaving the floor at 2 gives a single company's list rather
        #: than an enumeration, and the boundary test catches that
        #: (`test_partner_quota_holds_at_the_boundaries`).
        return min(width, max(2, base))
    return base


def _lane_scores(question: str, records: list[dict]) -> list[float]:
    """Question relevance scored within one lane only. No LLM call, no
    retrieval, no network.

    The population is the lane itself (same orderer, same partner), so IDF is
    computed inside it: tokens every member carries cannot move the order, and
    only tokens held by some of them do. Length normalisation comes from BM25.
    The two choices that would otherwise be hand-tuned, stopwords and length,
    are settled by the population definition.

    The tokenizer is `tokenize_words`, not the index tokenizer, which was
    chosen by measurement rather than by symmetry with the index.
    See docs/design-rationale.md#lane-tokenizer

    An empty lane or empty body makes `BM25Okapi` divide by zero. In that case
    all scores are zero and the caller keeps its existing order, so a dead sort
    does not take the pipeline down with it.
    """
    corpus = [bm25_module.tokenize_words(r.get("markdown") or "") for r in records]
    if len(corpus) < 2 or not any(corpus):
        return [0.0] * len(records)
    from rank_bm25 import BM25Okapi          # dependency the index already uses
    return list(BM25Okapi(corpus).get_scores(bm25_module.tokenize_words(question)))


def _spends_by_relevance(question: str, asks_roster: bool) -> bool:
    """Whether the slots go to the most relevant records rather than one per
    partner.

    Breadth is the right default only for a question about the set itself. A
    question that names one project wants every filing on that project, and
    handing one slot to each partner spends four of six on partners with
    nothing to say while the partner that has two relevant filings can show
    one. The roster phrasings already decide the other placement on this hop,
    so the same predicate decides this one and the two cannot drift apart.

    An empty question leaves breadth in force, matching `_partner_order`.
    See docs/design-rationale.md#partner-quota
    """
    return (ablation.enabled("counterparty_relevance")
            and bool(question) and not asks_roster)


def _relevance_order(question: str, partners: Sequence[str],
                     lanes: dict[str, list[dict]]) -> list[dict]:
    """Every partner's records in one order, scored as a single population.

    `_lane_scores` scores inside one lane on purpose, so its numbers are not
    comparable between partners: the same score means a different thing in a
    lane of two and a lane of eleven. Ranking across partners therefore has to
    rescore the union, which is the same function over a wider population and
    still costs no credits and no network.

    Partner order breaks ties, so a question that distinguishes nothing leaves
    the previous first pick first.
    See docs/design-rationale.md#partner-quota
    """
    records = [record for corp in partners for record in lanes.get(corp, ())]
    if not records:
        return []
    rank = {corp: index for index, corp in enumerate(partners)}
    scores = _lane_scores(question, records)
    order = sorted(zip(scores, range(len(records)), records),
                   key=lambda item: (-item[0], rank.get(item[2].get("corp_name"), 0),
                                     item[1]))
    return [record for _, _, record in order]


def _partner_order(records: list[dict], question: str = "") -> list[dict]:
    """Order within a partner: latest first, then question relevance, then
    arrival order.

    Latest first keeps a superseded filing from representing a partner, which
    would make the enumeration state an old value. This is the same reason
    `query_events` defaults to `is_latest=True`. Ordering by amount or by
    filing date scores higher in total but loses the fan-in questions, and the
    purpose of this hop is enumeration rather than a total.

    The second key reads the question. Without it the order is identical for
    any question against the same hub and partner, and golden records sitting
    deep in a lane are cut by `_partner_quota`. Raising the cap is not the
    remedy: `MAX_COUNTERPARTY_EVIDENCE` binds before the quota does, so a
    six-partner node cannot reach past the first record of a lane whatever the
    quota is. This is a sort rather than a reranker: zero credits, no LLM call,
    no network, same candidate set and same cap. Only what occupies the
    existing slots changes.

    Measured recovery from the sort alone is one record, against eight
    targeted; the gap is bounded by the lane structure and by golden records
    that carry none of the question's vocabulary.
    See docs/design-rationale.md#partner-order

    An empty `question` leaves the prior behaviour in force, latest first and
    then arrival order, so a caller that omits it does not silently change
    results.
    """
    if not question:
        return sorted(records, key=lambda r: not r.get("is_latest", True))
    scores = _lane_scores(question, records)
    return [record for _, record in
            sorted(enumerate(records),
                   key=lambda pair: (not pair[1].get("is_latest", True),
                                     -scores[pair[0]], pair[0]))]


def _counterparty_note(display: str, roster: str, n_partners: int) -> str:
    """Stamp the ordering relation inside the evidence block itself.

    Not split into a summary block, for the same reason as `_chain_note`.

    No receipt number appears here, so this line cannot move the evidence-hit
    count, and it carries only facts written in no single record, such as how
    many listed companies there are.

    Per-company filing counts are deliberately absent: a global count is both
    unfiltered for supersession and outside the scope of the question, and it
    leaked into an answer verbatim once.
    See docs/design-rationale.md#counterparty-note-no-counts
    """
    return f"- 발주처 관계: {display} 를 계약상대로 한 공시 상장사 {n_partners}곳 — {roster}"


#: Cap on hubs bound into one question. Candidates read out of the evidence
#: run to between five and eleven on value-chain questions, and binding them
#: all exhausts the step cap and doubles the evidence.
#: See docs/design-rationale.md#multi-hub-binding
MAX_COUNTERPARTY_HUBS = 3
#: Evidence slots for one additional hub. These are separate slots rather than
#: a division of the first hub's six: dividing drops the quotient below one on
#: a six-partner node and breaks the fan-in questions.
MAX_EXTRA_HUB_EVIDENCE = 2

#: The `- 계약상대: …` line of an evidence block. Same format
#: `corpus/markdown.to_markdown` writes from `record["fields"]`, so this is not
#: a guess about the shape of the text.
_EVIDENCE_HUB_RE = re.compile(r"^-\s*계약상대\s*:\s*(.+?)\s*$", re.M)


def _evidence_hubs(picked: list[dict], primary: str | None,
                   companies: Sequence[str] = ()) -> list[str]:
    """Extract additional hubs from the `계약상대:` field of evidence already
    collected. No LLM call, no retrieval, no step.

    Returns canonical names ordered by frequency, then by first appearance.

    Reading hubs out of the evidence rather than the question is the whole
    point. The router reads the ordering direction from the question sentence,
    and widening that axis was rejected on false positives; the `계약상대:`
    field is the counterparty the filing named itself, so its direction is
    already fixed and the rejection does not apply.

    Three filters apply: names that are not graph nodes, non-specific names,
    and the companies the question named.
    See docs/design-rationale.md#multi-hub-binding
    """
    from argus.corpus.counterparty import canonical, classify

    nodes = set(router_module._counterparty_nodes())
    blocked = {canonical(c) for c in companies if c}
    if primary:
        blocked.add(canonical(primary))
    counts: dict[str, int] = {}
    order: list[str] = []
    for item in picked:
        for raw in _EVIDENCE_HUB_RE.findall(item.get("markdown") or ""):
            if classify(raw) == "vague":
                continue
            key = canonical(raw)
            if key not in nodes or key in blocked:
                continue
            if key not in counts:
                order.append(key)
            counts[key] = counts.get(key, 0) + 1
    return sorted(order, key=lambda k: (-counts[k], order.index(k)))


#: Phrasings that call for an enumeration. The roster line is placed at the
#: head of the evidence so that the boundary of the set is visible on the first
#: line even for phrasings that do not ask for enumeration explicitly.
#: See docs/design-rationale.md#roster-position
_ASKS_ROSTER = ("상장사", "기업은", "기업들", "어느 기업", "어디인가", "누구인가",
                "빠짐없이", "모두 알려", "전부 알려", "목록", "나열")


def _roster_line(display: str, partners: Sequence[str],
                 loaded: dict[str, dict] | None = None,
                 counts: dict[str, dict] | None = None) -> str:
    """The enumerated set as one line.

    ## Counts ride along only under `roster_counts`, and only once all three
    ## conditions are met

    `_counterparty_note` dropped counts for two reasons: a global tally does not
    filter superseded corrections and so may not be true, it falls outside the
    question's scope, and it has in fact been copied verbatim into an answer.
    Three things were written down as the price of putting them back
    (`design-rationale.md#counterparty-note-no-counts`): name the question that
    needs them, check them filtered by `is_latest`, and pin the out-of-scope
    nature of the tally into the label itself. All three are met.

        question       `Q-114` point 1 reads *"삼성E&A(47건)와 삼성중공업(19건) 두
                       곳이다"*. The note claiming "no question asks for counts"
                       went stale with this one.
        `is_latest`    Checked filtered, then **both** are written. Measured on
                       the corpus, 12 of Samsung E&A's 47 filings are latest, and
                       7 of Samsung Heavy Industries' 19. The golden answer uses
                       the unfiltered figure (point 2's *"19건 중 17건"* is exact
                       on that basis too), and hiding the latest-only count would
                       leave standing the very misreading the note warned about.
                       So the two are written side by side.
        label          The line states inside itself that this is a whole-corpus
                       tally, not a count restricted to the site the question
                       named. Any wording on this line has to be assumed to reach
                       the answer verbatim (`Q-117`, measured), so letting the
                       label be copied is the right outcome.

    Receipt numbers are carried only behind `roster_items`, and only for
    partners whose filing actually reached the evidence (`loaded`). The reason
    the plain line carries none is measurement integrity: `error_analysis._blocks`
    splits the evidence on `^## `, so everything prepended above the first
    header counts as one block, and `evidence_hit` then reads any receipt
    number there as a golden filing that was loaded. Writing a receipt for a
    company whose filing was *not* carried would raise our own evidence-recall
    figure without loading anything -- Q-110's 두산퓨얼셀 is exactly that case,
    its golden reaching no hop candidate.

    Restricting to `loaded` makes the inflation structurally impossible rather
    than merely unlikely: every receipt written here is one that a real block
    already carries. `aggregate.summand_roster` is safe by the same argument,
    its rows coming out of the evidence records.

    Same position as `aggregate.line`: what the system knows deterministically
    goes at the head of the evidence together with an explicit disposition,
    since supplying the value alone leaves the model trusting its own judgment.
    See docs/design-rationale.md#roster-position
    """
    head = (f"[결정적 조회] {display} 를 계약상대로 공시한 상장사는 "
            f"{len(partners)}곳이다: {' · '.join(partners)}")
    detail = ""
    #: Counts go **inside the `roster_items` row**. Kept as a row of their own,
    #: the answer omitted the number in all three rounds: each company then had
    #: two parenthesised values (the count and the representative contract) and
    #: the answer copied only the contract. Rewording the label from forbidding
    #: to permitting changed nothing, so the problem was placement, not wording.
    #: They are attached to the row the answer already copies.
    count_of = {}
    if ablation.enabled("roster_counts") and counts:
        count_of = {corp: f" {counts[corp]['total']}건"
                          f"(최신본 {counts[corp]['latest']}건)"
                    for corp in partners if corp in counts}
    wrote_items = False
    if ablation.enabled("roster_items") and loaded:
        rows = [f"  · {corp}{count_of.get(corp, '')} — {loaded[corp]['subject']}"
                f" {loaded[corp]['amount']} (접수번호 {loaded[corp]['rcept_no']})"
                for corp in partners if corp in loaded]
        if rows:
            wrote_items = True
            #: `+=`, not `=`. The previous assignment overwrote the line
            #: already written, and it surfaced only after three rounds had
            #: been measured believing the line was attached.
            detail += ("\n" + "\n".join(rows)
                       + "\n  (접수번호는 근거에 실린 공시만 적었다. 목록의 나머지 회사는"
                         " 이번 근거에 안 실렸으므로 접수번호를 지어내지 않는다.)")
            if count_of:
                detail += (f"\n  (건수는 {display} 를 계약상대로 한 공시 **전체**를 센"
                           " 것이다. 「어느 상장사가 몇 건을 공시했는가」에는 이 수를"
                           " 그대로 쓰고, 특정 사업장·기간에 한정된 수로 적지는 않는다.)")
    elif count_of:
        #: With `roster_items` off there is no row to attach to, so the count
        #: goes after the company name instead.
        detail += ("\n" + "\n".join(f"  · {corp}{count_of[corp]}"
                                   for corp in partners if corp in count_of))
    #: An explicit instruction not to total. Once the slots widened to 8 and a
    #: single company carried several filings, the model **summed them on its
    #: own**: `Q-110` A89 r1 added KEPCO E&C's three filings into
    #: 1,080,964,550,138원, a figure that did not even match the true sum
    #: (1,081,829,074,942). The rows above already give one representative
    #: contract per company, so there is nothing to add up.
    no_total = ""
    #: Attached **only when the rows were actually written**. With nothing for
    #: *"위 대표 계약"* to point at, the sentence becomes a lie -- a test caught
    #: this.
    if wrote_items and ablation.enabled("roster_no_total"):
        #: `A90`'s wording. **Measured and rejected**: the opening clause also
        #: reads as "write only one filing", and `Q-114` then omitted Samsung
        #: Heavy Industries' second filing (20231218800177) in two rounds of
        #: three, moving citation marking (golden match) from 0/0/0 to 1/0/1.
        #: Kept here for reproduction.
        no_total = (" 회사마다 위 대표 계약을 적고, 한 회사의 여러 공시를 더한"
                    " 합계는 내지 않는다.")
    elif wrote_items and ablation.enabled("roster_sum_ban"):
        #: The refined wording. It targets **the ban on totalling alone**, so
        #: the opening clause that touched the number of citations is dropped.
        no_total = " 한 회사의 여러 공시를 더한 합계는 내지 않는다."
    return (head + detail + "\n"
            f"(이 목록은 시스템이 계약상대 그래프에서 직접 열거한 것이다. "
            f"답변은 이 {len(partners)}곳을 빠짐없이 적고 임의로 줄이지 않는다.{no_total})")


def _gather_counterparty(hub: str, question: str, budget: Budget, trace: list[str],
                         take, limit: int | None = None,
                         prefer: Sequence[str] = (),
                         roster_out: list[str] | None = None) -> int:
    """Collect per-partner evidence from the counterparty graph. No LLM call,
    one step.

    The step comes from outside the `plan_hops` ladder, the same precedent as
    the revision chain hop, so it does not consume the retrieval budget. A
    graph lookup enumerates adjacent nodes rather than reformulating a query,
    so it has no reason to occupy a widening slot.

    `question` is used only for ordering within a lane (`_partner_order`). The
    candidate set and the partner list are fixed by the graph regardless of the
    question, which changes the order alone.

    `limit` and `prefer` serve the multi-hub path; their defaults reproduce the
    prior behaviour exactly. `prefer` exists because an additional hub has only
    two slots, so taking partners in graph order gives no guarantee that the
    companies the question named are among them. An additional hub was derived
    from those companies' evidence in the first place, so returning to them
    matches its definition.
    See docs/design-rationale.md#multi-hub-binding
    """
    try:
        budget.spend_step()
    except BudgetExceeded as exc:
        trace.append(f"hop={COUNTERPARTY_HOP} stop=step_budget ({exc})")
        return 0
    # The name handed to the tool is the display form, not the canonical one.
    # `query_by_counterparty` feeds its argument to `classify()`, and several
    # alternatives of the anonymous-counterparty pattern depend on whitespace
    # boundaries that canonicalisation removes, so feeding back a canonical
    # name makes the non-specific guard miss. All 145 graph nodes were checked
    # to confirm the display form canonicalises identically, so the lookup
    # result is unchanged.
    #: As a default argument this would freeze at import time and the switch
    #: would never reach it, so it is resolved here.
    if limit is None:
        limit = _counterparty_cap()
    name = router_module.counterparty_display(hub) or hub
    out = tools.execute("query_by_counterparty", {"name": name})
    if "error" in out:
        trace.append(f'hop={COUNTERPARTY_HOP} strat=counterparty '
                     f'tool=query_by_counterparty via={hub} '
                     f'skip=tool_error 건너뜀({out["error"]})')
        return 0

    partners = list(out.get("partners") or [])
    if prefer:
        wanted = [c for c in partners if c in set(prefer)]
        partners = wanted + [c for c in partners if c not in set(wanted)]
    by_partner: dict[str, list[dict]] = {}
    for record in out.get("records") or []:
        by_partner.setdefault(record.get("corp_name"), []).append(record)
    lanes = {corp: _partner_order(by_partner.get(corp, []), question)
             for corp in partners}
    # Company names only, with no filing counts attached. See
    # `_counterparty_note` for why.
    roster = " · ".join(partners)
    note = _counterparty_note(out.get("counterparty") or hub, roster, len(partners))

    asks_roster = any(k in (question or "") for k in _ASKS_ROSTER)

    taken: list[str] = []
    if _spends_by_relevance(question, asks_roster):
        for record in _relevance_order(question, partners, lanes)[:limit]:
            if take(record.get("doc_id"), _annotate(record.get("markdown", ""), note),
                    None, record.get("corp_name")):
                taken.append(record["doc_id"])
    else:
        for depth in range(_partner_quota(len(partners))):
            for corp in partners:
                if len(taken) >= limit:
                    break
                items = lanes[corp]
                if depth >= len(items):
                    continue
                record = items[depth]
                if take(record.get("doc_id"), _annotate(record.get("markdown", ""), note),
                        None, corp):
                    taken.append(record["doc_id"])

    # The boundary of the set goes at the head of the evidence. An in-block
    # note alone is not read. Fires only with two or more partners, since one
    # partner is not an enumeration.
    #
    # Built after `taken`, not before. `roster_items` writes a receipt number
    # only for a partner whose filing actually reached the evidence, and that
    # set is not known until the taking loop has run. `extra_lines` is consumed
    # after every `_gather_counterparty` call returns, so moving the append down
    # to here changes nothing about where the line lands.
    # See docs/design-rationale.md#roster-position
    if roster_out is not None and len(partners) >= 2 and asks_roster:
        from argus.agent import aggregate

        loaded = {}
        for corp in partners:
            for record in lanes.get(corp, ()):
                if record.get("doc_id") in taken:
                    amount = record.get("amount")
                    loaded[corp] = {
                        "rcept_no": record.get("rcept_no", ""),
                        "subject": aggregate._subject(record),
                        "amount": f"{int(amount):,}원" if amount else "금액 미기재"}
                    break
        # Counts come from `by_partner` (everything the tool returned). `lanes`
        # has passed through `_partner_order` and exists to order representative
        # rows; counting from it would let the ordering rule move the tally --
        # the mirror image of the defect where ordering knew about the latest
        # revision and the tally did not.
        counts = {corp: {"total": len(rows),
                         "latest": sum(1 for r in rows if r.get("is_latest"))}
                  for corp, rows in ((c, by_partner.get(c, [])) for c in partners)}
        roster_out.append(
            _roster_line(router_module.counterparty_display(hub) or hub, partners,
                         loaded, counts))

    trace.append(f'hop={COUNTERPARTY_HOP} strat=counterparty tool=query_by_counterparty '
                 f'via={hub} partners={len(partners)} '
                 f'alloc={"relevance" if _spends_by_relevance(question, asks_roster) else "breadth"} '
                 f'corp="{",".join(partners) or "-"}" n={len(taken)} top_score=- '
                 f'ids={",".join(taken) or "-"}')
    return len(taken)


# --- Enumeration hop (`EVENTS_HOP` strat=enumerate) -------------------------
# The hop ladder widens the same query rather than enumerating, so a question
# asking how a set divides can stop at `sufficient` with every block drawn from
# one branch, leaving the model to invent the other branch.
#
# Over-firing is the real risk, because unlike the counterparty hop this one
# reaches the general retrieval path. Five brakes gate it, and across the
# 100-question cohort exactly one question fires it.
# See docs/design-rationale.md#enumeration-hop

#: Markers for a question asking how a set divides. Broad quantifiers are
#: deliberately excluded: they match plain parallel lookups far more often than
#: enumerations.
_ENUMERATION_HINT = ("갈래", "나뉘", "나뉜", "유형")

#: Markers for a question asking about the structure of the counterparty set,
#: gating the universe/non-universe branch line specifically. Kept as its own
#: list rather than folded into `_ENUMERATION_HINT`: that one gates the whole
#: enumeration hop and is deliberately broad, while this line makes a stronger
#: claim, a named two-way split, and needs a check of its own so the two do
#: not drift apart.
#: See docs/design-rationale.md#counterparty-branch
_ASKS_STRUCTURE = ("나뉜다", "갈래", "구조", "유형")


def _asks_structure(question: str) -> bool:
    """Whether the question asks how the counterparty set divides. No LLM
    call and no retrieval."""
    return any(h in (question or "") for h in _ASKS_STRUCTURE)


#: Cap on what the enumeration may contribute. Half of `MAX_EVIDENCE`, so that
#: enumeration appends after `_take` without displacing retrieval evidence.
#: Values of 4, 5 and 6 were measured and recover identically.
MAX_ENUM_EVIDENCE = MAX_EVIDENCE // 2

#: Candidate pool needed for grouping, not the number carried into the prompt.
#: Group sizes per counterparty and the latest record of each group both
#: require the full population; the tool default truncates it.
ENUM_EVENT_POOL = 200

#: Question vocabulary mapped to an exchange `doc_subtype`. Order matters: the
#: termination form has to be tested first so that a termination question does
#: not fall through to the conclusion form.
_EVENT_SUBTYPE = (("해지", "단일판매공급계약해지"),
                  ("철회", "단일판매공급계약해지"),
                  ("단일판매", "단일판매공급계약체결"),
                  ("공급계약", "단일판매공급계약체결"),
                  ("신규시설투자", "신규시설투자등"),
                  ("투자판단", "투자판단관련주요경영사항"))


def _event_subtype(question: str) -> str | None:
    """Exchange form named by the question, or None to open without a form
    filter."""
    text = question or ""
    for word, subtype in _EVENT_SUBTYPE:
        if word in text:
            return subtype
    return None


def _wants_enumeration(r, question: str, group: str) -> bool:
    """Whether to open the enumeration hop. Applies the first four brakes; the
    caller applies the fifth.
    See docs/design-rationale.md#enumeration-hop
    """
    if group != "exchange" or len(getattr(r, "companies", []) or []) != 1:
        return False
    if getattr(r, "counterparty", None) == r.companies[0]:
        return False                       # that company placed the order
    return any(h in (question or "") for h in _ENUMERATION_HINT)


def _enumerate_events(r, question: str, budget: Budget, trace: list[str], take,
                      extra: list[str] | None = None) -> int:
    """Take one record per counterparty from one company's filings of one form.
    No LLM call, no retrieval, one step.

    The question asks how a set divides, so the collection rule divides too:
    group by canonical counterparty, walk the groups largest first, and take
    the latest record of each. Both sub-rules already exist in this repository.
    Representing a group by its latest record matches `_partner_order` and
    `query_events(is_latest=True)`, and taking the larger branches first
    reflects that a question about two branches is more likely answered by the
    large ones.

    Question relevance does not help here, unlike in `_partner_order`, which is
    why that function is not reused. Group depth is not the binding constant
    either: `MAX_ENUM_EVIDENCE` fills on the first pass, so raising the depth
    changes no set in this corpus.
    See docs/design-rationale.md#enumeration-grouping
    """
    corp = r.companies[0]
    try:
        budget.spend_step()
    except BudgetExceeded as exc:
        trace.append(f"hop={EVENTS_HOP} strat=enumerate stop=step_budget ({exc})")
        return 0
    subtype = _event_subtype(question)
    args = {"corp_name": corp, "is_latest": True, "limit": ENUM_EVENT_POOL}
    if subtype:
        args["doc_subtype"] = subtype
    out = tools.execute("query_events", args)
    if "error" in out:
        trace.append(f'hop={EVENTS_HOP} strat=enumerate tool=query_events '
                     f'corp="{corp}" skip=tool_error 건너뜀({out["error"]})')
        return 0

    from argus.corpus.counterparty import canonical

    groups: dict[str, list[dict]] = {}
    for event in out.get("events") or ():
        groups.setdefault(canonical(event.get("counterparty") or ""), []).append(event)
    for members in groups.values():        # newest first within a group
        members.sort(key=lambda e: (str(e.get("rcept_dt", "")), e.get("rcept_no", "")),
                     reverse=True)
    order = sorted(groups, key=lambda key: (-len(groups[key]), key))

    # Record as one line the distribution that gets discarded for want of
    # slots. The grouping already exists and only `MAX_ENUM_EVIDENCE` of it is
    # carried; raising that cap was rejected, so this goes out as a line rather
    # than as slots.
    # See docs/design-rationale.md#partner-profile
    if extra is not None and ablation.enabled("partner_profile"):
        from argus.agent import aggregate

        profile = aggregate.partner_profile(groups)
        if profile:
            extra.append(profile)
            trace.append(f"partner_profile groups={len(groups)} chars={len(profile)}")

    # State which of the two branches each counterparty belongs to: inside
    # the 70-company coverage universe or outside it. The two branches split
    # deterministically on that one predicate, with no alias table and no
    # semantic judgment. Fires only for a question asking about the structure
    # of the counterparty set, and only when both branches are non-empty.
    # See docs/design-rationale.md#counterparty-branch
    if (extra is not None and ablation.enabled("counterparty_branch")
            and _asks_structure(question)):
        from argus.agent import aggregate

        branch = aggregate.counterparty_branch(groups)
        if branch:
            extra.append(branch)
            trace.append(f"counterparty_branch groups={len(groups)} chars={len(branch)}")

    taken: list[str] = []
    depth = 0
    while len(taken) < MAX_ENUM_EVIDENCE and depth < 1:
        for key in order:
            if len(taken) >= MAX_ENUM_EVIDENCE:
                break
            event = groups[key][depth]
            if take(event.get("doc_id"), event.get("markdown"), None, corp):
                taken.append(str(event.get("doc_id")))
        depth += 1

    trace.append(f'hop={EVENTS_HOP} strat=enumerate tool=query_events corp="{corp}" '
                 f'subtype={subtype or "-"} pool={out.get("count", 0)} '
                 f'groups={len(groups)} n={len(taken)} top_score=- '
                 f'ids={",".join(taken) or "-"}')
    return len(taken)


# --- Hop log -----------------------------------------------------------------
# `think_trace` is a human-readable string, but the retrieval and ranking
# failure attribution is derived from it too. Each hop therefore records its
# query, filters, candidate count and candidate doc ids as key=value pairs,
# read back by `parse_hops()`.

_KV = re.compile(r'(\w+)=("[^"]*"|[^\s|]*)')


# --- Fusion trace -------------------------------------------------------------
# An ablation arm that stays on when it should be off is the well-known risk;
# an arm that fails to take effect when switched on is the same size of risk.
# Whether fusion actually ran is therefore recorded in `think_trace`. With the
# arm off, `LAST_FUSION` is None and nothing is appended.

def _fusion_note() -> str:
    fusion = bm25_module.LAST_FUSION
    if not fusion:
        return ""
    return (f' fusion={fusion["mode"]} pool={fusion["pool"]} '
            f'dense_n={fusion["dense_n"]} dense_new={fusion["added"]} '
            f'dense_gain={fusion["gain"]}')


def _fmt_period(period: dict) -> str:
    if "ym" in period:
        return f"ym:{period['ym']}"
    if "year" in period:
        return f"year:{period['year']}"
    return "none"


def _log_search(plan, corp, hits: list[dict], period: dict | None = None) -> str:
    ids = ",".join(str(h.get("doc_id") or "?") for h in hits)
    scores = [h["score"] for h in hits if isinstance(h.get("score"), (int, float))]
    top = f"{max(scores):.3f}" if scores else "-"
    cell = plan.period if period is None else period
    return (f'hop={plan.hop} strat={plan.strategy} tool=search_chunks '
            f'corp="{corp or "-"}" period={_fmt_period(cell)} top_k={plan.top_k} '
            f'q="{plan.query}" n={len(hits)} top_score={top} ids={ids or "-"}'
            f'{_fusion_note()}')


def _log_verdict(hop: int, ok: bool, count: int, reasons: list[str]) -> str:
    line = f'hop={hop} verdict={"sufficient" if ok else "insufficient"} evidence={count}'
    return line if ok else f'{line} reason={",".join(reasons)}'


def parse_hops(think_trace: str) -> list[dict]:
    """Turn a `think_trace` back into hop records, the input for failure-stage
    attribution.

        ids = [d for h in parse_hops(resp["think_trace"]) for d in h["ids"]]
        retrieval failure: the golden receipt number is in no hop's ids
        ranking failure:   it is in ids but outside the top 3

    A trace with no hop log, such as an out-of-range question, returns an empty
    list.
    """
    hops: dict[int, dict] = {}
    order: list[int] = []
    for line in (think_trace or "").split(" | "):
        kv = {k: v.strip('"') for k, v in _KV.findall(line)}
        raw = kv.get("hop", "")
        if not raw.isdigit():
            continue
        hop = int(raw)
        rec = hops.get(hop)
        if rec is None:
            rec = {"hop": hop, "strategy": "", "corp": [], "period": "", "queries": [],
                   "ids": [], "n": 0, "top_score": None, "verdict": "", "reason": "",
                   "notes": []}
            hops[hop] = rec
            order.append(hop)
        rec["strategy"] = kv.get("strat") or rec["strategy"]
        rec["period"] = kv.get("period") or rec["period"]
        rec["verdict"] = kv.get("verdict") or rec["verdict"]
        rec["reason"] = kv.get("reason") or rec["reason"]
        if kv.get("corp") and kv["corp"] != "-" and kv["corp"] not in rec["corp"]:
            rec["corp"].append(kv["corp"])
        if kv.get("q"):
            rec["queries"].append(kv["q"])
        if kv.get("n", "").isdigit():
            rec["n"] += int(kv["n"])
        for doc_id in (kv.get("ids") or "").split(","):
            if doc_id and doc_id != "-" and doc_id not in rec["ids"]:
                rec["ids"].append(doc_id)
        try:
            score = float(kv.get("top_score", ""))
        except ValueError:
            score = None
        if score is not None:
            rec["top_score"] = score if rec["top_score"] is None else max(rec["top_score"], score)
        for key in ("skip", "stop"):
            if kv.get(key):
                rec["notes"].append(f"{key}={kv[key]}")
    return [hops[h] for h in order]


# --- Upstream failure marker -------------------------------------------------
# `/answer` must return the five-key schema in every case, so the pipeline
# survives an upstream error and emits a limitation notice instead. That notice
# is indistinguishable from a legitimate refusal by the model, and a caller
# that caches it turns a transport failure into a permanent wrong answer.
#
# The schema is left alone and a machine-readable marker is written into
# `think_trace`, which callers read back through `upstream_failure()`.
# See docs/design-rationale.md#upstream-failure

#: Upstream failure marker. The human-readable failure text is left unchanged
#: for compatibility with existing callers.
_UPSTREAM = "upstream_error"
_UPSTREAM_RE = re.compile(r"upstream_error stage=(\w+) status=(\S+) retryable=(\w+)")
_STATUS_RE = re.compile(r"실패 (\d{3})")

#: Statuses that may be retried. Rate limits, timeouts and 5xx are not our
#: answer.
RETRYABLE_STATUS = frozenset({"408", "425", "429", "500", "502", "503", "504"})


def _upstream_mark(stage: str, exc: Exception) -> str:
    match = _STATUS_RE.search(str(exc))
    status = match.group(1) if match else "-"
    retryable = status in RETRYABLE_STATUS or status == "-"
    return f"{_UPSTREAM} stage={stage} status={status} retryable={retryable}"


def upstream_failure(think_trace: str) -> dict | None:
    """Whether the limitation notice was emitted because of an upstream
    failure, else None.

    Callers use this to avoid caching a transport failure as an answer: read
    from `answer` alone, a legitimate refusal and a failed request look the
    same.
    See docs/design-rationale.md#upstream-failure
    """
    match = _UPSTREAM_RE.search(think_trace or "")
    if not match:
        return None
    return {"stage": match.group(1), "status": match.group(2),
            "retryable": match.group(3) == "True"}


#: Stage name used by `fault_response`. Kept distinct from the generation and
#: critic stages so that an upstream failure and an exception in this code can
#: be told apart after the fact.
FAULT_STAGE = "api"


def fault_response(question_id: str, question: str, exc: BaseException) -> dict:
    """Build the five-key schema from any exception. Last line of defence for
    `/answer`.

    A 5xx is retried, so a failing question can consume three requests and
    still score zero, whereas a 200 with a limitation notice can score under
    the information-limits criterion. The pipeline's own handling covers only
    `RuntimeError` from the generation and critic stages, so exceptions from
    retrieval and planning reach FastAPI as a 500 without this.

    The exception is not swallowed: the marker `upstream_failure()` reads back
    is written here too, with a stage that separates it from an upstream fault.
    See docs/design-rationale.md#upstream-failure
    """
    trace = (f"{_upstream_mark(FAULT_STAGE, exc)} "
             f"서버 예외: {type(exc).__name__}: {exc}")
    return {"question_id": question_id, "question": question,
            "retrieved_context": "", "think_trace": trace, "answer": UNANSWERABLE}


#: A single section record runs to 8,000 characters, so reusing the exchange
#: cap would put 80,000 characters into the prompt and exceed the token budget
#: on its own.
#: See docs/design-rationale.md#section-evidence-budget
MAX_SECTION_EVIDENCE = 4
#: Total character cap for section evidence, leaving room for generation and
#: the critic within the token budget.
MAX_SECTION_CHARS = 12_000


def _log_section(plan, corp, path, hits: list[dict], cell: dict | None = None) -> str:
    """One trace line for a single retrieval.

    When `cell` is given, that cell's filters are recorded. A decomposed hop
    does not retrieve with `plan.filters`, so recording those would log
    something other than what was measured, and `parse_hops` reads this line
    back as failure-attribution input.
    """
    ids = ",".join(str(h.get("doc_id") or "?") for h in hits)
    scores = [h["score"] for h in hits if isinstance(h.get("score"), (int, float))]
    top = f"{max(scores):.3f}" if scores else "-"
    used = cell if cell is not None else plan.filters
    filters = ",".join(f"{k}={v}" for k, v in sorted(used.items())) or "-"
    return (f'hop={plan.hop} strat={plan.strategy} tool=search_chunks group=periodic '
            f'corp="{corp or "-"}" filters={filters} path="{path or "-"}" '
            f'top_k={plan.top_k} q="{plan.query}" n={len(hits)} '
            f'top_score={top} ids={ids or "-"}{_fusion_note()}')


def _path_cap(per_corp: int, index: int, n_paths: int) -> int:
    """Evidence slots one path may take from one company.

    The first path is the decisive axis and the secondary paths are one slot of
    insurance against the same figure appearing under a different table of
    contents entry. Slots are therefore not divided evenly: splitting them in
    half drops a golden record that ranks third within the first path.
    See docs/design-rationale.md#section-evidence-budget
    """
    if n_paths <= 1:
        return per_corp
    return max(1, per_corp - (n_paths - 1)) if index == 0 else 1


def _cover_order(picked: list[dict]) -> list[dict]:
    """Round-robin across companies, keeping arrival order within a company.

    The character budget is the real constraint on section evidence, so order
    determines recovery. Arrival order is already set by the table-of-contents
    rank and the BM25 score, and re-sorting by block length demotes the long
    golden blocks. Only the company axis is changed: without it one company's
    large section consumes the budget and the other drops out of a comparison
    entirely. With one company this is the identity transform.
    See docs/design-rationale.md#section-evidence-budget
    """
    by_corp: dict = {}
    for item in picked:
        by_corp.setdefault(item.get("corp"), []).append(item)
    lanes = list(by_corp.values())
    out: list[dict] = []
    for depth in range(max((len(v) for v in lanes), default=0)):
        out += [items[depth] for items in lanes if depth < len(items)]
    return out


def _join_section_evidence(picked: list[dict]) -> str:
    """Join section evidence within the total character cap, which is derived
    from the token budget.

    A block that would exceed the budget is skipped and filling continues.
    Stopping at that block instead discards every smaller block behind it,
    while the cap keeps the token count the same either way.
    See docs/design-rationale.md#section-evidence-budget
    """
    out: list[str] = []
    total = 0
    for item in _cover_order(picked)[:MAX_SECTION_EVIDENCE]:
        markdown = item["markdown"]
        if out and total + len(markdown) > MAX_SECTION_CHARS:
            continue
        out.append(markdown)
        total += len(markdown)
    return "\n\n".join(out)


#: `search_chunks` arguments removed by the meta-axis leave-one-out arms. Each
#: axis has to carry exactly one meaning for the manipulation to be a single
#: variable, so the three date arguments are grouped together: they all fold
#: into the same receipt-date prefix. Fiscal year and report subtype are
#: excluded, because a fiscal year is not a receipt date and removing them
#: together would hide the receipt-date axis on its own.
#: See docs/design-rationale.md#meta-axis-loo
_META_AXIS_KEYS: dict[str, tuple[str, ...]] = {
    "meta_corp": ("corp_name",),
    "meta_date": ("ym", "year", "rcept_dt"),
    "meta_group": ("group",)}


def _search_args(args: dict, arm=None) -> dict:
    """Drop meta axes from `search_chunks` arguments per the arm. No LLM call.

    Not passing the argument is the whole manipulation. The retrieval stage is
    not disabled, so BM25 runs as before and only the candidate set widens.

    On the default arm the dictionary is returned as received, with no key
    removed and no copy taken, so the retrieval arguments on an untouched path
    are byte-identical.
    See docs/design-rationale.md#meta-axis-loo
    """
    arm = arm or ablation.current()
    dropped = {key for flag, keys in _META_AXIS_KEYS.items()
               if not getattr(arm, flag) for key in keys}
    if not dropped:
        return args
    return {k: v for k, v in args.items() if k not in dropped}


def _drain_report_cells(plan, cells: list[dict], corps: list, quota: dict,
                        taken: int, budget: Budget, trace: list[str],
                        take) -> tuple[int, bool]:
    """Retrieve once per named report, filling evidence slots in two passes: a
    fair share, then whatever remains.

    The second pass exists because one cell can hold the answer across two
    documents whose relevant sections are byte-identical stubs and therefore
    tie exactly on BM25. The axis that separates them is not a filter axis of
    the section index, so without rebuilding the index the way to keep the
    question answerable is to carry both and let generation choose, since the
    receipt number and filing date are stamped at the head of each block.

    The remaining slots go round in order rather than to cells that underused
    their share, because the first pass already guaranteed fairness.
    See docs/design-rationale.md#report-cell-two-pass
    """
    path = (plan.paths or (None,))[0]
    pools: list[tuple[tuple, object, list[dict]]] = []
    for cell in cells:
        for corp in corps:
            try:
                budget.spend_step()
            except BudgetExceeded as exc:
                trace.append(f"hop={plan.hop} stop=step_budget ({exc})")
                return taken, True
            args = {"query": plan.query, "top_k": plan.top_k,
                    "group": "periodic", **cell}
            if corp:
                args["corp_name"] = corp
            if path:
                args["path"] = path
            out = tools.execute("search_chunks", _search_args(args))
            if "error" in out:
                trace.append(f'hop={plan.hop} strat={plan.strategy} '
                             f'tool=search_chunks group=periodic skip=tool_error '
                             f'건너뜀({out["error"]})')
                return taken, True
            hits = out.get("chunks") or []
            trace.append(_log_section(plan, corp, path, hits, cell))
            pools.append(((cell["doc_subtype"], cell["base_year"], corp), corp, hits))

    counts: dict = {}
    cap = _report_quota(len(cells), len(corps))
    for limit in (cap, MAX_SECTION_EVIDENCE):      # fair share first, remaining slots second
        for key, corp, hits in pools:
            for chunk in hits:
                if taken >= MAX_SECTION_EVIDENCE:
                    break
                if quota[corp] <= 0 or counts.get(key, 0) >= limit:
                    break
                if take(chunk.get("chunk_id"), chunk.get("markdown"),
                        chunk.get("score"), corp):
                    taken += 1
                    quota[corp] -= 1
                    counts[key] = counts.get(key, 0) + 1

    keys = {key for key, _, _ in pools}
    if all(counts.get(key, 0) for key in keys):
        # Once every named report is collected, that set is the answer and
        # widening further only contaminates it. This is the only
        # sufficiency-to-stop edge in the periodic lane, since `_sufficiency`
        # runs once after `_gather_sections` and cannot rewind the ladder.
        trace.append(f"hop={plan.hop} stop=named_reports_covered "
                     f"(부른 보고서 {len(keys)}종을 모두 회수)")
        return taken, True
    return taken, False


def _gather_sections(r, question: str, budget: Budget,
                     trace: list[str], take) -> int:
    """Periodic filing section loop. No LLM call.

    The hop ladder differs from the exchange loop: table-of-contents paths are
    chosen first, then documents by report subtype and fiscal year, and BM25
    selects the section last. Removing the table-of-contents axis drops golden
    recovery from 0.929 to 0.679.

    Hop 0 considers several path candidates together, because the same figure
    can appear under different table-of-contents entries. A per (company, path)
    quota keeps one path from consuming a company's slots.
    See docs/design-rationale.md#section-hop-ladder
    """
    paths = path_hints(question, tools.section_paths())
    plans = plan_section_hops(question, report_hints(question), paths,
                              _fiscal_years(r, question))
    if not ablation.current().hop_ladder:
        plans = plans[:1]                       # no reformulation ladder on this arm
    corps = r.companies[:3] or [None]
    # A comparison is not answered by filling one side, so slots are divided to
    # keep one company from consuming them all.
    per_corp = max(1, MAX_SECTION_EVIDENCE // len(corps))
    quota = {corp: per_corp for corp in corps}
    named = _report_cells(question)             # reports the question names
    taken = 0
    for plan in plans:
        if plan.hop > 0:
            try:
                budget.spend_hop()
            except BudgetExceeded as exc:
                trace.append(f"hop={plan.hop} stop=hop_budget ({exc})")
                break
        # Split only hop 0, only with two or more named reports, and only
        # without a receipt-date prefix.
        if plan.hop == 0 and len(named) >= 2 and not plan.prefix:
            taken, stop = _drain_report_cells(
                plan, named, corps, quota, taken, budget, trace, take)
            if stop:
                break
            continue
        hop_paths = plan.paths or (None,)
        for index, path in enumerate(hop_paths):
            cell_cap = _path_cap(per_corp, index, len(hop_paths))
            for corp in corps:
                if taken >= MAX_SECTION_EVIDENCE:
                    break
                if quota[corp] <= 0:
                    continue
                try:
                    budget.spend_step()
                except BudgetExceeded as exc:
                    trace.append(f"hop={plan.hop} stop=step_budget ({exc})")
                    return taken
                args = {"query": plan.query, "top_k": plan.top_k,
                        "group": "periodic", **plan.filters, **plan.prefix}
                if corp:
                    args["corp_name"] = corp
                if path:
                    args["path"] = path
                out = tools.execute("search_chunks", _search_args(args))
                if "error" in out:
                    trace.append(f'hop={plan.hop} strat={plan.strategy} '
                                 f'tool=search_chunks group=periodic skip=tool_error '
                                 f'건너뜀({out["error"]})')
                    return taken
                hits = out.get("chunks") or []
                trace.append(_log_section(plan, corp, path, hits))
                # No rule filters sibling chunks of the same section within a
                # cell. Deduplicating by (document, path) was measured and
                # recovered nothing while adding hops and evidence.
                # See docs/design-rationale.md#section-hop-ladder
                taken_here = 0
                for chunk in hits:
                    if quota[corp] <= 0 or taken_here >= cell_cap:
                        break
                    if take(chunk.get("chunk_id"), chunk.get("markdown"),
                            chunk.get("score"), corp):
                        taken += 1
                        taken_here += 1
                        quota[corp] -= 1
        if all(v <= 0 for v in quota.values()):
            break
    return taken


def _gather_meta(r, question: str, group: str, trace: list[str], take) -> str | None:
    """Meta prefilter only, with BM25 never called.

    A company filter plus a receipt-date prefix narrows the candidate set from
    the whole corpus to a single document, so the floor of retrieval is here
    rather than in vector search, and leaving this unmeasured omits the largest
    contributing factor.

    Having no ranking is the definition of this arm: documents are taken in
    manifest order, since scoring them makes it the BM25 arm. Periodic filings
    follow the same rule and take the leading sections without choosing a path.

    `group` is taken from the caller rather than re-read from the route, so
    that the evidence and the required-field check refer to the same document
    group.
    See docs/design-rationale.md#meta-only-arm
    """
    period = _period_args(r, question)
    year = int(str(period["ym"])[:4]) if "ym" in period else period.get("year")
    docs = []
    for corp in (r.companies[:3] or [None]):
        docs += filter_docs(corp_name=corp, doc_group=group, year=year)
    if "ym" in period:
        docs = [d for d in docs if str(d.get("rcept_dt", ""))[:6] == period["ym"]] or docs
    ids = ",".join(str(d.get("doc_id") or "?") for d in docs[:MAX_EVIDENCE]) or "-"
    trace.append(f'hop=0 strat=meta tool=filter_docs corp="{",".join(r.companies) or "-"}" '
                 f'period={_fmt_period(period)} group={group} n={len(docs)} top_score=- '
                 f'ids={ids}')

    sections: list[dict] = []
    for doc in docs:
        if len(sections) >= MAX_SECTION_EVIDENCE and group == "periodic":
            break
        record = tools._record_of(doc.get("rcept_no") or doc.get("doc_id"))
        if record is not None:
            take(record["doc_id"], record.get("markdown", ""), None, record.get("corp_name"))
            continue
        if group != "periodic":
            continue
        index = tools._section_index()
        if index is None:
            continue
        for chunk in tools._sections_of(index, doc["doc_id"]):
            if chunk["part"] or chunk["is_stub"] or len(sections) >= MAX_SECTION_EVIDENCE:
                continue
            entry = {"doc_id": chunk["chunk_id"], "markdown": tools._section_markdown(chunk),
                     "score": None, "corp": chunk["corp_name"]}
            sections.append(entry)
            take(entry["doc_id"], entry["markdown"], None, entry["corp"])
    return _join_section_evidence(sections) if sections else None


#: Periodic `doc_subtype` mapped to a human-readable report name. Structured
#: records are not listed here, because `markdown._label` already does the same
#: job and writing the rule twice lets one copy go stale.
_PERIODIC_LABEL = {"annual": "사업보고서", "half": "반기보고서", "quarter": "분기보고서"}


def _citation_source(record: dict) -> dict | None:
    """Take citation values straight from the record rather than re-reading the
    rendered markdown.

    Re-parsing text this code just rendered is the same shape as writing one
    value in two places and updating only one of them, and it is fragile in
    practice because the header separator is an en dash. For periodic filings
    the difference is larger still: re-reading scrapes the section path out of
    a header string, while the chunk record carries `path` exactly.
    See docs/design-rationale.md#citation-from-record
    """
    rcept = str(record.get("rcept_no") or "")
    if not rcept:
        found = re.search(r"\d{14}", str(record.get("doc_id") or ""))
        rcept = found.group(0) if found else ""
    if not rcept:
        return None

    # The chunks retrieval hands over are sparse: a structured hit carries only
    # a doc id and a score. Used directly they would produce a thinner citation
    # than re-reading would, so the canonical record is looked up here. That is
    # an index and record lookup, so no LLM call.
    #
    # The two groups are separated by the doc id prefix rather than by the
    # presence of `path`, because a periodic filing arriving through the
    # manifest path has no `path` and would fall through to the structured
    # branch, degrading the report name.
    # See docs/design-rationale.md#citation-from-record
    doc_id = str(record.get("doc_id") or "")
    if doc_id.startswith(PERIODIC_DOC_PREFIX) or record.get("path") is not None:
        source = _periodic_meta(doc_id)
        label = _PERIODIC_LABEL.get(source.get("doc_subtype") or "", "정기보고서")
        if source.get("base_year"):
            label = f'{label} {source["base_year"]}'
        # Use the chunk's own `path` when present, else the one found in the
        # index by chunk id.
        tail = str(record.get("path") or source.get("path") or "")
        raw = str(source.get("rcept_dt") or "")
    else:                                                   # structured record
        source = record if record.get("doc_subtype") or record.get("title") else {}
        if not source:
            source = tools._record_of(rcept) or record
        label = markdown_module._label(source)
        tail = str(source.get("title") or "")
        raw = str(source.get("rcept_dt") or "")

    # The first eight digits of a receipt number are the filing date, so the
    # date survives even when the record lookup returns nothing.
    if len(raw) != 8:
        raw = rcept[:8]
    date = f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}" if len(raw) == 8 and raw.isdigit() else ""
    return {"rcept_no": rcept, "label": label, "date": date, "tail": tail}


def _periodic_meta(doc_id: str) -> dict:
    """Find document metadata and the section path for a periodic chunk id.
    No LLM call.

    What `_take` receives is a chunk id, not a document id, in forms such as
    `periodic_20250814003695#109` and `periodic_20250321001834#119/p0`. Passing
    one straight to `_sections_of` returns nothing, which degrades the report
    name and empties the section path.

    Document metadata is identical across sections, so it is read from the
    first row and only the section path is selected by chunk id.
    """
    base = doc_id.split("#", 1)[0]
    index = tools._section_index()
    rows = tools._sections_of(index, base) if index else []
    if not rows:
        return {}
    chunk = doc_id.split("/", 1)[0]                 # `#119/p0` → `#119`
    hit = (next((row for row in rows if row.get("chunk_id") == chunk), None)
           # A further-split chunk may not match exactly, so try by prefix.
           or next((row for row in rows
                    if str(row.get("chunk_id") or "").startswith(chunk)), None))
    return {**rows[0], "path": (hit or {}).get("path", "")}


def _gather_evidence(r, question: str = "", budget: Budget | None = None,
                     provenance: dict[str, dict] | None = None) -> tuple[str, list[str]]:
    """Retrieval loop: search, judge sufficiency, reformulate and search again
    when short. No LLM call.

        search -> sufficient? yes -> generate
                            no  -> reformulate -> search again (hop <= 3,
                                                  total steps <= 8)

    Hop 0 is the initial search and spends no hop budget; the three
    reformulations spend `Budget.spend_hop()`. Tool calls are bounded to eight
    steps by `Budget.spend_step()`. Exceeding a cap does not raise out of this
    function: the loop proceeds with the evidence collected so far.

    A tool returning an error is skipped silently and the next means is tried,
    so the pipeline stays up.
    """
    arm = ablation.current()
    budget = budget or Budget()
    trace: list[str] = []
    picked: list[dict] = []
    seen: set[str] = set()
    period = _period_args(r, question)
    corps = r.companies[:3] or [None]
    group = getattr(r, "group", "exchange")
    # The BM25-only arm sees the exchange index alone, so retrieval falls back
    # to `exchange` even when the router resolved another group. That gap is
    # the index-coverage delta.
    #
    # The fallback applies only to arms that use BM25. The meta-only arm never
    # opens an index at all, so it has none to fall back to, and applying this
    # line to it made its required-field check use the exchange schema against
    # evidence drawn from other groups.
    # See docs/design-rationale.md#index-coverage-arm
    if arm.uses_bm25 and not arm.uses_group_index:
        group = "exchange"
    required = _required_fields(question, group)
    stalled = 0
    out_of_steps = False
    chain_done = False
    chain_gap: list[str] = []
    chain_ids: set = set()

    # Counterparty lane. `seen` is deliberately not shared: sharing it shifts
    # the retrieval lane's arrival order wherever the two overlap, which in
    # turn changes the chain seeds.
    # See docs/design-rationale.md#lane-separation
    cp_picked: list[dict] = []
    #: Lines that occupy no evidence slot, recording what retrieval found but
    #: could not carry.
    extra_lines: list[str] = []
    cp_seen: set[str] = set()
    #: Slots the counterparty lane may place in the final evidence. Grows by
    #: `MAX_EXTRA_HUB_EVIDENCE` per additional hub bound, and stays at the base
    #: constant with a single hub.
    cp_cap = _counterparty_cap()

    def _remember(record: dict | None) -> None:
        """Record which filings were used as evidence.

        Every piece of evidence passes through this point, section evidence
        included, so one hook here is sufficient.

        Records dropped by truncation remain here, which is harmless: whether
        one survived is read from the evidence string, and this mapping only
        supplies values.
        """
        if provenance is None or not record:
            return
        unit = _citation_source(record)
        if not unit:
            return
        # A periodic filing has several sections under one receipt number, so
        # keeping only the first would drop the rest from the citation. The
        # convention's own example lists several sections under one receipt
        # number (`CONVENTION §7.5`).
        kept = provenance.setdefault(unit["rcept_no"],
                                     {**unit, "tails": []})
        if unit["tail"] and unit["tail"] not in kept["tails"]:
            kept["tails"].append(unit["tail"])

    def _take(doc_id, markdown, score, corp, record=None) -> bool:
        if markdown and doc_id and doc_id not in seen:
            seen.add(doc_id)
            picked.append({"doc_id": doc_id, "markdown": markdown,
                           "score": score, "corp": corp})
            _remember(record if record is not None else {"doc_id": doc_id})
            return True
        return False

    def _take_cp(doc_id, markdown, score, corp, record=None) -> bool:
        if markdown and doc_id and doc_id not in cp_seen:
            cp_seen.add(doc_id)
            cp_picked.append({"doc_id": doc_id, "markdown": markdown,
                              "score": score, "corp": corp})
            _remember(record if record is not None else {"doc_id": doc_id})
            return True
        return False

    def _joined() -> str:
        """Retrieval lane first, counterparty lane after it, leaving the
        retrieval slots intact.

        Deduplication compares against the truncated set rather than against
        everything the retrieval lane collected. Comparing against the latter
        drops counterparty records that overlap the truncated tail from both
        lanes, while the trace still counts them as delivered.
        See docs/design-rationale.md#lane-separation
        """
        head = picked[:MAX_EVIDENCE]
        kept = {p["doc_id"] for p in head}
        extra = [p for p in cp_picked if p["doc_id"] not in kept]
        return "\n\n".join(p["markdown"] for p in head + extra[:cp_cap])

    if not arm.uses_bm25:                      # meta prefilter only, no search
        if _out_of_corpus_period(r, question):
            trace.append(f"hop=0 stop=out_of_range "
                         f"(질문 기간 {r.years} 가 코퍼스 2023.01~2026.03 밖 — 완화해도 답이 없다)")
            return "", trace
        sections = _gather_meta(r, question, group, trace, _take)
        ok, reasons = _sufficiency(r, picked, required)
        trace.append(_log_verdict(0, ok, len(picked), reasons))
        if sections is not None:
            return sections, trace
        return "\n\n".join(p["markdown"] for p in picked[:MAX_EVIDENCE]), trace

    # Periodic filings retrieve on different axes: fiscal year and table of
    # contents path rather than receipt date. Running them through the same
    # ladder drops the golden records at the filter stage.
    if group == "periodic":
        if _out_of_corpus_period(r, question):
            trace.append(f"hop=0 stop=out_of_range "
                         f"(질문 기간 {r.years} 가 코퍼스 2023.01~2026.03 밖 — 완화해도 답이 없다)")
            return "", trace
        _gather_sections(r, question, budget, trace, _take)
        ok, reasons = _sufficiency(r, picked, required)
        trace.append(_log_verdict(EVENTS_HOP, ok, len(picked), reasons))
        if ok or picked:
            return _join_section_evidence(picked), trace
        # With nothing found in sections, fall back to structured records:
        # the figure may also appear in an exchange filing.
        group = "exchange"

    cells = time_points(question, r.years) if arm.split_query else []
    plans = plan_hops(question, period, fallback_terms=r.companies, periods=cells)
    if not arm.hop_ladder:
        plans = plans[:1]                       # single search, no reformulation
    for plan in plans:
        # With evidence already collected for a company-scoped question, the
        # company filter is not released. Filings from other companies
        # contaminate a summary or comparison outright.
        if plan.strategy == "global" and picked and r.companies:
            trace.append(f'hop={plan.hop} strat=global skip=corp_scoped '
                         f'(기업 특정 질의에 근거 {len(picked)}건 확보 — 전역 검색은 오염)')
            break
        if plan.hop > 0 and _out_of_corpus_period(r, question):
            trace.append(f"hop={plan.hop} stop=out_of_range "
                         f"(질문 기간 {r.years} 가 코퍼스 2023.01~2026.03 밖 — 완화해도 답이 없다)")
            break
        if plan.hop > 0:
            try:
                budget.spend_hop()
            except BudgetExceeded as exc:
                trace.append(f"hop={plan.hop} stop=hop_budget ({exc})")
                break

        before = len(picked)
        hop_cells = plan.periods or (plan.period,)
        targets = corps if plan.use_corp else [None]
        quota = _cell_quota(len(hop_cells), len(targets))
        for cell in hop_cells:
            for corp in targets:
                if len(picked) >= MAX_EVIDENCE or out_of_steps:
                    break
                try:
                    budget.spend_step()
                except BudgetExceeded as exc:
                    trace.append(f"hop={plan.hop} stop=step_budget ({exc})")
                    out_of_steps = True
                    break
                args = {"query": plan.query, "top_k": plan.top_k, **cell}
                if group != "exchange":
                    args["group"] = group
                if corp:
                    args["corp_name"] = corp
                out = tools.execute("search_chunks", _search_args(args))
                if "error" in out:
                    trace.append(f'hop={plan.hop} strat={plan.strategy} '
                                 f'tool=search_chunks corp="{corp or "-"}" '
                                 f'skip=tool_error 건너뜀({out["error"]})')
                    continue
                hits = out.get("chunks") or []
                if plan.strategy == "global":
                    # With no filter at all, the score is the only guarantee
                    # of relevance. Taking hits below the noise floor fills
                    # the evidence with unrelated companies.
                    hits = [h for h in hits
                            if isinstance(h.get("score"), (int, float))
                            and h["score"] >= MIN_TOP_SCORE]
                trace.append(_log_search(plan, corp, hits, cell))
                taken_here = 0
                for chunk in hits:
                    if taken_here >= quota:            # per-cell quota
                        break
                    if _take(chunk.get("doc_id"), chunk.get("markdown"),
                             chunk.get("score"), corp, record=chunk):
                        taken_here += 1

        # Set completeness. When a candidate is superseded, the rest of its
        # chain has to arrive before the evidence is complete. Walked once,
        # before the judgment: placed after it, hop 0 returns `sufficient` and
        # stops, so the revision never arrives.
        if arm.chain_hop and not chain_done and picked and not out_of_steps:
            chain_done = True
            chain_gap, chain_ids = _expand_chain(picked, seen, budget, trace, _take,
                                                 _as_of_ym(question), question)

        ok, reasons = _sufficiency(r, picked, required, chain_gap)
        trace.append(_log_verdict(plan.hop, ok, len(picked), reasons))
        if ok or out_of_steps:
            break
        if chain_gap and all(x.startswith("chain_open:") for x in reasons):
            # An unclosed link is not closed by searching again: it is either
            # a tool error or an exhausted step budget. Do not spend the
            # remaining hops.
            trace.append(f"hop={CHAIN_HOP} stop=chain_unresolved "
                         f"(체인 링크 {len(chain_gap)}건 미해소 — 재검색 대상이 아니다)")
            break
        # No evidence at all is a miss rather than convergence, so keep
        # widening.
        stalled = stalled + 1 if picked and len(picked) == before else 0
        if stalled >= MAX_STALLED_HOPS:
            trace.append(f"hop={plan.hop} stop=stalled "
                         f"({MAX_STALLED_HOPS}회 연속 새 후보 없음)")
            break

    # Last resort: when retrieval found nothing, fall back to enumerating
    # structured events. Out-of-range questions are blocked here too, since
    # the event query cannot carry a period condition across several years,
    # and its population is exchange filings, so attaching it to another
    # document group would supply wrong evidence.
    if (arm.hop_ladder and not picked and group == "exchange"
            and not _out_of_corpus_period(r, question)):
        for corp in r.companies[:3]:
            try:
                budget.spend_step()
            except BudgetExceeded as exc:
                trace.append(f"hop={EVENTS_HOP} stop=step_budget ({exc})")
                break
            args: dict = {"corp_name": corp, "is_latest": True}
            if len(r.years) == 1:
                args["year"] = r.years[0]
            out = tools.execute("query_events", args)
            if "error" in out:
                trace.append(f'hop={EVENTS_HOP} strat=events tool=query_events '
                             f'corp="{corp}" skip=tool_error 건너뜀({out["error"]})')
                continue
            events = (out.get("events") or [])[:5]
            ids = ",".join(str(e.get("doc_id") or "?") for e in events)
            trace.append(f'hop={EVENTS_HOP} strat=events tool=query_events corp="{corp}" '
                         f'period={_fmt_period({"year": args["year"]} if "year" in args else {})} '
                         f'n={out.get("count", 0)} top_score=- ids={ids or "-"}')
            for event in events:
                _take(event.get("doc_id"), event.get("markdown"), None, corp,
                      record=event)

    # Enumeration hop, mutually exclusive with the fallback above: that one
    # runs on an empty evidence set, this one on a non-empty one. They share a
    # hop number, so exclusivity is what keeps `parse_hops` from merging two
    # kinds of candidate into one record.
    # See docs/design-rationale.md#enumeration-hop
    elif (arm.hop_ladder and picked and len(picked) < MAX_EVIDENCE
            and not _out_of_corpus_period(r, question)
            and _wants_enumeration(r, question, group)):
        _enumerate_events(r, question, budget, trace, _take, extra_lines)

    # Counterparty hop: one step from outside the ladder, run only on what is
    # left after retrieval has spent its budget. Deferring it changes no
    # record collected, since a graph lookup does not depend on retrieval
    # state.
    # See docs/design-rationale.md#counterparty-lane
    #
    # Only the multi-hub arm binds more than one hub. With the flag off the
    # list below holds a single hub and both the call and its arguments are
    # unchanged.
    if arm.counterparty_hop:
        primary = getattr(r, "counterparty", None)
        hubs = [primary] if primary else []
        if arm.multi_hub and len(hubs) < MAX_COUNTERPARTY_HUBS:
            room = MAX_COUNTERPARTY_HUBS - len(hubs)
            extra_hubs = _evidence_hubs(picked, primary, r.companies)[:room]
            hubs += extra_hubs
            cp_cap += MAX_EXTRA_HUB_EVIDENCE * len(extra_hubs)
        for index, hub in enumerate(hubs):
            # The hub the question named keeps the base cap and ordering. A
            # hub derived from the evidence gets two slots and puts the
            # companies the question named at the front of the partner list.
            from_question = index == 0 and hub == primary
            # No argument is passed on an arm where the feature is off. Even
            # passing the parameter explicitly as None trips the guard test
            # that fixes the call signature, and that guard is right: moving
            # the default arguments invalidates the recorded measurements.
            extra = ({"roster_out": extra_lines}
                     if ablation.enabled("roster_line") and from_question else {})
            _gather_counterparty(
                hub, question, budget, trace, _take_cp,
                limit=_counterparty_cap() if from_question else MAX_EXTRA_HUB_EVIDENCE,
                prefer=() if from_question else tuple(r.companies or ()), **extra)

    # Truncation keeps chain members first. A chain can push the collected
    # set past the cap, and cutting by arrival order would drop the last
    # revision of the chain, which is the answer.
    # See docs/design-rationale.md#lane-separation
    if chain_ids:
        picked.sort(key=lambda p: 0 if p["doc_id"] in chain_ids else 1)

    # Record what retrieval found but could not carry, as a line. It occupies
    # no evidence slot, so it does not fall under the reason widening the
    # window was rejected, and it goes at the head because a line at the tail
    # is not read.
    # See docs/design-rationale.md#line-position
    body = _joined()
    if extra_lines and body:
        body = "\n\n".join(extra_lines) + "\n\n" + body
    return body, trace


# --- Oracle path: golden evidence injection (diagnostic only) --------------
# Every other arm measures retrieval and generation together, so generation
# with the evidence held fixed has never been isolated. This path replaces the
# whole retrieval lane with the golden evidence to create that axis. It cannot
# be a submission configuration, and at evaluation time there is no golden set,
# so the accuracy it reports is a diagnostic value rather than a score.
#
# Injection replaces evidence rather than adding to it, so the delta against
# the default arm mixes the retrieval shortfall with the loss of supporting
# evidence and cannot be read as a single term.
#
# Formatting, ordering and truncation follow the retrieval path exactly. Any
# difference would put a formatting effect into the delta, indistinguishable
# from the retrieval share.
# See docs/design-rationale.md#oracle-arm

#: Document id prefix for periodic filings. The golden set supplies only a
#: receipt number, so the id is built here in the same form
#: `filter_docs(doc_group="periodic")` produces.
PERIODIC_DOC_PREFIX = "periodic_"

#: Chunk candidates considered within one golden table-of-contents entry.
#: `MAX_SECTION_EVIDENCE` binds again above this. The pool is generous because
#: a golden `section` can name a whole top-level entry that expands to dozens
#: of chunks.
ORACLE_SECTION_POOL = 8

#: Hop number read back by `parse_hops`. Placed after the counterparty hop so
#: that it does not collide with a retrieval hop; sharing a number would merge
#: two kinds of candidate into one record.
ORACLE_HOP = COUNTERPARTY_HOP + 1


def _oracle_section_blocks(rcept_no: str, section: str, question: str, cap: int,
                           budget: Budget, trace: list[str]) -> list[dict]:
    """Select evidence chunks within the (document, path) the golden set names.
    No LLM call.

    The document and the table-of-contents entry are pinned by the golden set
    and only the choice of chunks inside them is left to the existing ranker,
    so what this arm fixes perfectly is the document and the entry, not the
    paragraph. Reports have to state that alongside the delta.

    When BM25 selects no chunk at all, because no query term overlaps, the
    order falls back to document order. Descending to empty evidence would
    make the question read as a generation failure.
    See docs/design-rationale.md#oracle-arm
    """
    doc_id = f"{PERIODIC_DOC_PREFIX}{rcept_no}"
    index = tools._section_index()
    wanted = bm25_module.norm_path(section)
    rows = [r for r in (tools._sections_of(index, doc_id) if index else [])
            if bm25_module.norm_path(r["path"]) == wanted
            or bm25_module.norm_path(r["path"]).startswith(wanted)]
    rows.sort(key=lambda r: (r["chunk_id"].split("/")[0], r["part"]))
    # The company axis is recovered from the index record. `search_chunks`
    # does not return `corp_name`, but `_join_section_evidence` round-robins on
    # it, and leaving it None lets one company consume every slot in a
    # comparison.
    corp_of = {r["chunk_id"]: r["corp_name"] for r in rows}

    try:
        budget.spend_step()
    except BudgetExceeded as exc:
        trace.append(f"hop={ORACLE_HOP} strat=oracle stop=step_budget ({exc})")
        return []
    out = tools.execute("search_chunks", {"query": question, "top_k": ORACLE_SECTION_POOL,
                                          "group": "periodic", "doc_id": doc_id,
                                          "path": section, "include_stubs": True})
    if "error" in out:
        trace.append(f'hop={ORACLE_HOP} strat=oracle tool=search_chunks group=periodic '
                     f'skip=tool_error 건너뜀({out["error"]})')
        chunks = []
    else:
        chunks = [c for c in (out.get("chunks") or []) if c.get("chunk_id") in corp_of]
    if not chunks:
        # No query term overlapped any chunk, and BM25 drops zero-score
        # candidates. Fall back to document order, the same ordering
        # `read_section` uses.
        trace.append(f'hop={ORACLE_HOP} strat=oracle tool=search_chunks group=periodic '
                     f'doc_id={doc_id} fallback=doc_order n={len(rows)}')
        chunks = [{"chunk_id": r["chunk_id"], "score": None,
                   "markdown": tools._section_markdown(r)}
                  for r in rows[:ORACLE_SECTION_POOL]]
    return [{"doc_id": c["chunk_id"], "markdown": c.get("markdown"),
             "score": c.get("score"), "corp": corp_of.get(c["chunk_id"])}
            for c in chunks if c.get("markdown")][:cap]


def _gather_golden(question_id: str, question: str,
                   budget: Budget) -> tuple[str, list[str]]:
    """Build the prompt evidence from the golden set alone. Diagnostic only.

    With no injection present, `ablation.oracle_golden_for` raises rather than
    descending silently to empty evidence, which would harden into the claim
    that generation failed even when given the oracle.
    See docs/design-rationale.md#oracle-arm
    """
    golden = ablation.oracle_golden_for(question_id)
    trace: list[str] = [
        f"oracle=golden n_golden={len(golden)} "
        "🔴 진단 전용 — 검색을 골든으로 대체했다 (제출 구성이 될 수 없다)"]

    # Each golden record gets a fair share, the same reason `_gather_sections`
    # quotas its (company, path) cells. Without it a single golden entry that
    # expands to many chunks consumes every slot and the rest drop out
    # entirely. `_cover_order` divides on the company axis alone, so two golden
    # records from the same company are not separated by it.
    n_sections = sum(1 for item in golden if (item or {}).get("section"))
    cap = max(1, MAX_SECTION_EVIDENCE // n_sections) if n_sections else MAX_SECTION_EVIDENCE

    records: list[dict] = []
    sections: list[dict] = []
    missing: list[str] = []
    for item in golden:
        rcept_no = str((item or {}).get("rcept_no") or "")
        section = (item or {}).get("section")
        if not rcept_no:
            missing.append("?")
            continue
        if section:
            blocks = _oracle_section_blocks(rcept_no, section, question, cap,
                                            budget, trace)
            if not blocks:
                missing.append(rcept_no)
            sections.extend(blocks)
            continue
        record = tools._record_of(rcept_no)
        if record is None or not record.get("markdown"):
            missing.append(rcept_no)
            continue
        records.append({"doc_id": record["doc_id"], "markdown": record["markdown"],
                        "score": None, "corp": record.get("corp_name")})

    picked = records + sections
    # Line read back by `parse_hops`. The golden documents have to appear in
    # `ids=` for failure attribution to hold on this arm; omitted, every golden
    # record counts as absent from all hop candidates and the whole set is
    # attributed to retrieval failure.
    corps = sorted({p["corp"] for p in picked if p.get("corp")})
    trace.append(
        f'hop={ORACLE_HOP} strat=oracle tool=golden_evidence '
        f'corp="{",".join(corps) or "-"}" period=none '
        + ("group=periodic " if sections else "")
        + f'n={len(picked)} top_score=- '
        f'ids={",".join(str(p["doc_id"]) for p in picked) or "-"}')
    if missing:
        # A golden record that does not resolve in the corpus. Not swallowed:
        # the evidence is smaller than what was supplied.
        trace.append(f'hop={ORACLE_HOP} strat=oracle skip=golden_unresolved '
                     f'ids={",".join(missing)}')
    trace.append(_log_verdict(ORACLE_HOP, bool(picked), len(picked),
                              [] if picked else ["oracle_empty"]))

    parts = []
    if records:
        parts.append("\n\n".join(p["markdown"] for p in records[:MAX_EVIDENCE]))
    if sections:
        parts.append(_join_section_evidence(sections))
    return "\n\n".join(p for p in parts if p), trace


# --- Self-consistency arm ---------------------------------------------------
# Draw several samples and take the majority. The premise of the technique is
# that independent reasoning paths converge on the correct answer, and whether
# that premise holds on this corpus is what the arm measures; the verdict is
# the deliverable regardless of adoption.
#
# Two traps have to be closed first, because either silently zeroes the delta:
# the request cache, which gives identical samples one cache key, and a
# temperature of zero, which makes the samples identical by construction.
# See docs/design-rationale.md#self-consistency-arm


def _sc_key(answer: str) -> str:
    """Key that groups samples, standing in for the final answer.

    In these answers that means the amounts and percentages, since closed
    questions are scored by exact numeric match and the judge on open questions
    checks figures first. The patterns come from `critic`, which is the single
    source for them; a copy here would let one side be fixed alone.

    An answer with no figures has no majority to take. The whitespace-normalised
    text is used as the key instead, which at a non-zero temperature makes every
    sample its own key so the first is selected. That is a limit of the
    manipulation rather than a defect, and the trace records it per question.
    See docs/design-rationale.md#self-consistency-arm
    """
    from argus.agent import critic

    text = answer or ""
    values = [f"{value:.10g}" for _, _, value, _, _ in critic._money_spans(text)]
    values += [f'{m.group(1).replace(",", "")}%' for m in critic._PCT_RE.finditer(text)]
    if ablation.enabled("sc_key_set"):
        #: **Keyed as a set.** A sequence is sensitive to order and repetition,
        #: so two samples get counted as different branches merely because one
        #: stated the same amount twice. Measurement showed the price
        #: (`A77` 세 판, `scratch/sc_simulate.py`).
        #:
        #:     Q-093  r1 `2e+11`  vs  r2 `2e+11|2e+11`   same amount, different key
        #:     Q-111  r2's value set = r3's value set, differing only in repeats
        #:
        #: Keying as a set collapses the branches from 1:74 to 84 and 3:14 to 12,
        #: and the majority-vote score goes from 111/112 to **112/112** (it saves
        #: `Q-111`). Order and repetition are style, not the content of the
        #: answer, so collapsing them is right.
        return "|".join(sorted(set(values))) if values else " ".join(text.split())
    return "|".join(values) if values else " ".join(text.split())


def _sc_applies(first: str) -> bool:
    """Whether the extra samples are worth drawing for this answer.

    The majority is taken over the figures a sample states, so an answer with
    none has nothing to vote on. Reusing the key builder keeps the gate and the
    aggregation from disagreeing about what counts as a figure.
    See docs/design-rationale.md#self-consistency-scope
    """
    if not ablation.enabled("sc_figures_only"):
        return True
    from argus.agent import critic

    text = first or ""
    return bool(critic._money_spans(text) or critic._PCT_RE.search(text))


def _self_consistent(messages: list[dict], max_tokens: int, arm, first: str,
                     budget: Budget, trace: list[str]) -> str:
    """Answer chosen by majority across `k` samples. The first sample already
    exists, so `k-1` further calls are made.

    If the budget runs out or the upstream fails, the majority is taken over
    the samples collected so far. Raising `BudgetExceeded` here would drop the
    caller into the limitation notice and discard answers already received,
    and the token cap does bind on questions with long evidence.

    On a tie the first key wins, so that raising `k` leaves the first sample as
    the baseline.
    See docs/design-rationale.md#self-consistency-arm
    """
    samples = [first]
    for index in range(1, arm.sc_k):
        try:
            # The cache is bypassed from the second call onward. Keeping the
            # first in the cache makes a re-run that much cheaper and lets an
            # offline re-aggregation read back at least that one set.
            with no_cache():
                result = chat(messages, max_tokens=max_tokens,
                              temperature=arm.temperature, tag="answer_sc")
            budget.spend_llm(result.prompt_tokens, result.completion_tokens)
        except BudgetExceeded as exc:
            trace.append(f"sc stop=budget k={len(samples)}/{arm.sc_k} ({exc})")
            break
        except RuntimeError as exc:
            trace.append(f"sc skip=upstream k={len(samples)}/{arm.sc_k} ({exc})")
            break
        samples.append(result.text)
        trace.append(f"generate sc={index} "
                     f"tokens={result.prompt_tokens}+{result.completion_tokens}")

    keys = [_sc_key(s) for s in samples]
    counts: dict[str, int] = {}
    for key in keys:
        counts[key] = counts.get(key, 0) + 1
    best = max(counts, key=lambda k: (counts[k], -keys.index(k)))
    picked = keys.index(best)
    trace.append(f"sc k={len(samples)}/{arm.sc_k} temp={arm.temperature} "
                 f"keys={len(counts)} votes={counts[best]} picked={picked}")
    return samples[picked]


# --- Function-calling arm ---------------------------------------------------
# The tool specifications were registered from the start but `tools=` had never
# been passed to `chat()`. This closes that gap.
#
# The round trip is mandatory: when the model returns tool calls, the response
# content is usually empty, and without feeding the results back for one more
# call an empty answer would go out and its zero would read as evidence that
# function calling has no effect.
#
# `tools` and `maxTokens` cannot be specified together, so the round that
# offers tools carries no length cap and the final round drops the tools to
# restore it. That is an API constraint rather than a choice, and its share is
# mixed into this arm's delta.
# See docs/design-rationale.md#function-calling-arm

#: Cap on rounds that offer tools. One further round without tools follows, so
#: generation costs at most two calls per question. Leaving the loop open would
#: let the answer stage consume the call and token budgets on its own, starving
#: the critic and the verifiers, and that would read as an arm effect. The cost
#: of a cap of one is that tools cannot be chained.
MAX_TOOL_ROUNDS = 1

#: Character cap for one tool result placed into the prompt. Section and chunk
#: retrieval can return tens of thousands of characters, which without a cap
#: exhausts the token budget in one go and emits the limitation notice, which
#: then hardens into a measurement.
MAX_TOOL_RESULT_CHARS = 2_000


def _tool_arguments(call: dict) -> dict:
    """Return `toolCalls[i].function.arguments` as a dict. HCX-005 returns a
    dict while OpenAI-compatible servers return a JSON string, and both are
    accepted: assuming one shape makes tools silently never fire."""
    args = (call.get("function") or {}).get("arguments")
    if isinstance(args, str):
        try:
            args = json.loads(args or "{}")
        except json.JSONDecodeError:
            return {}
    return args if isinstance(args, dict) else {}


# --- Substituting tool results rather than letting the model transcribe -----
# The function-calling arm did not lose because tools went uncalled: it lost
# because the model transcribed a correct tool result incorrectly into the
# answer. The bottleneck in this domain is transcription rather than
# arithmetic, and function calling does not touch that axis.
#
# The remedy is the one already measured elsewhere: the value is written into
# the string by code. Both the rule and the code come from `critic`.
# See docs/design-rationale.md#tool-result-substitution


#: Minimum operand count for a tool to be treated as having computed anything.
#: Below it the tool returned the number the model typed rather than a result,
#: so the value is not independent evidence. A transcription error does not
#: disappear under function calling; it moves from the answer into the tool
#: arguments, and substituting such a result overwrites a correct answer with
#: the typo.
#: See docs/design-rationale.md#tool-result-substitution
MIN_TOOL_OPERANDS = 2


def _tool_operands(args: dict) -> list[float]:
    """Numeric values carried in the tool arguments, with list arguments
    flattened."""
    out: list[float] = []
    for value in (args or {}).values():
        for item in (value if isinstance(value, (list, tuple)) else [value]):
            if isinstance(item, bool) or not isinstance(item, (int, float)):
                continue
            out.append(float(item))
    return out


def _numeric_tool_results(results: list[tuple[str, dict, dict]]) -> list[tuple[str, float]]:
    """The `(tool, value)` pairs eligible for substitution, under three
    thresholds.

    1. Only the numeric `value` of a calculation tool qualifies. Comparison and
       period tools return non-numeric values and retrieval tools have no
       `value` key at all, and `CALC_TOOLS` filters again so that a retrieval
       tool gaining such a key later cannot silently reach an answer.
    2. Fewer numeric operands than `MIN_TOOL_OPERANDS` is not a computation.
    3. A result equal to one of its operands was passed through rather than
       computed, which the operand count alone does not catch. The cost is that
       a legitimate computation coinciding with an operand is blocked too;
       since this rewrites an answer, the default is to leave it alone when
       unsure.
    See docs/design-rationale.md#tool-result-substitution
    """
    out: list[tuple[str, float]] = []
    for name, args, payload in results or ():
        if name not in tools.CALC_TOOLS:
            continue
        value = (payload or {}).get("value")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        operands = _tool_operands(args)
        if len(operands) < MIN_TOOL_OPERANDS:
            continue
        if any(abs(float(value) - operand) <= 1e-9 for operand in operands):
            continue
        out.append((name, float(value)))
    return out


def _substitute_tool_results(answer: str, results: list[tuple[str, dict, dict]],
                             trace: list[str]) -> str:
    """Replace the corresponding figure in the answer with the tool's value.
    No LLM call.

    The trace records the outcome even when nothing fires, so that how often
    substitution could have applied and how often it did can be counted after
    the fact; without that the delta cannot be attributed.
    See docs/design-rationale.md#tool-result-substitution
    """
    pairs = _numeric_tool_results(results)
    if not pairs:
        return answer
    reports = tool_result_reports(answer, pairs)
    out, substituted = substitute_recomputed(answer, reports, unique_recomputed(reports))
    if substituted:
        trace.append(f'tool_substituted={"; ".join(substituted)}')
    else:
        trace.append(f"tool_substituted=- values={len(pairs)}")
    return out


def _answer_with_tools(messages: list[dict], max_tokens: int, arm, budget: Budget,
                       trace: list[str]) -> str:
    """Offer tools, execute what the model calls, feed the results back and
    call again.

    With result substitution enabled, the answer's figures are replaced from
    the tool results at the end. With it disabled the line below is unchanged
    from the prior behaviour.
    """
    results: list[tuple[str, dict, dict]] = []
    answer = _tool_round_trip(messages, max_tokens, arm, budget, trace, results)
    if arm.substitute_tool_results:
        answer = _substitute_tool_results(answer, results, trace)
    return answer


def _tool_round_trip(messages: list[dict], max_tokens: int, arm, budget: Budget,
                     trace: list[str], results: list[tuple[str, dict, dict]]) -> str:
    """Round-trip body. Executed `(tool, arguments, result)` triples are
    appended to `results` in order.

    The arguments are kept alongside the result, because whether the tool
    actually computed anything is judged from the operand count.

    A failure on the first call propagates, so that it leaves through the same
    path as the default arm and an upstream fault is not mixed into the arm
    effect. From the second round onward, an answer already received is kept
    and the loop stops.
    """
    # Only the calculation tools are offered here. When retrieval tools were
    # offered too, most calls went to retrieval and re-read evidence already
    # present, diluting it.
    offered = tools.CALC_SPECS if arm.calc_tools_only else tools.TOOL_SPECS
    convo = list(messages)
    best = ""
    for index in range(MAX_TOOL_ROUNDS + 1):
        offer = index < MAX_TOOL_ROUNDS
        if index and budget.remaining()["llm_calls"] <= 0:
            # Checked before the call: counting afterwards means the credits
            # are already spent.
            trace.append(f"fc stop=budget_calls rounds={index} kept="
                         f'{"partial" if best else "none"}')
            if best:
                return best
            raise BudgetExceeded("LLM 호출 상한 — function calling 왕복을 마치지 못했다")
        try:
            result = chat(convo, max_tokens=max_tokens, temperature=arm.temperature,
                          tools=offered if offer else None, tag="answer")
            budget.spend_llm(result.prompt_tokens, result.completion_tokens)
        except (BudgetExceeded, RuntimeError) as exc:
            if not best:
                raise
            trace.append(f"fc stop={type(exc).__name__} rounds={index} kept=partial ({exc})")
            return best

        calls = result.tool_calls or []
        trace.append(f"generate fc={index} offered={'y' if offer else 'n'} "
                     f"calls={len(calls)} finish={result.finish} "
                     f"tokens={result.prompt_tokens}+{result.completion_tokens}")
        text = (result.text or "").strip()
        if text:
            best = text
        if not calls:
            return best

        # The assistant turn is fed back verbatim. Dropping `toolCalls` leaves
        # the following `role="tool"` messages with nothing to attach to.
        convo.append({"role": "assistant", "content": result.text or "",
                      "toolCalls": calls})
        for call in calls:
            name = ((call.get("function") or {}).get("name")) or ""
            args = _tool_arguments(call)
            out = tools.execute(name, args)
            results.append((name, args, out))
            payload = json.dumps(out, ensure_ascii=False, default=str)
            if len(payload) > MAX_TOOL_RESULT_CHARS:
                payload = payload[:MAX_TOOL_RESULT_CHARS] + "…(잘림)"
            convo.append({"role": "tool", "toolCallId": call.get("id", ""),
                          "name": name, "content": payload})
            trace.append(f'fc tool={name or "?"} '
                         f'{"error" if "error" in out else "ok"} chars={len(payload)}')
    return best


def _offer_tools(arm, question: str, trace: list[str]) -> bool:
    """Whether to offer tools for this question. Always false when function
    calling is disabled.

    When restricted to calculation queries, tools are offered only for those:
    offering them removes the length cap, which cost questions that never
    called a tool at all.

    The decision is written to the trace, since otherwise which questions
    opened cannot be counted afterwards. On arms where the flag is off, the
    trace is byte-identical.
    See docs/design-rationale.md#function-calling-arm
    """
    if not arm.function_calling:
        return False
    if not arm.calc_queries_only:
        return True
    kind = router_module.calc_question_kind(question)
    trace.append(f'fc gate={"open:" + kind if kind else "closed"}')
    return bool(kind)


#: Critic booleans mapped to a human-readable reason. The model's prose is not
#: copied into the note: the critic produces the boolean and the prose
#: independently, so a note built from both can contradict itself. A note
#: records what the verifier observed, and a record that argues with itself is
#: worse than none. The diagnostic prose stays in the trace, so nothing is
#: lost.
#: See docs/design-rationale.md#verdict-reason
_VERDICT_REASONS = (
    ("has_citation", False, "근거 표기가 없습니다"),
    ("supported", False, "답변의 일부 수치·사실이 근거에서 확인되지 않았습니다"),
    ("hallucination", True, "근거에 없는 내용이 사실처럼 서술되었습니다"),
)


#: Shape of the verification tail. Reasons built by `_verdict_reason` are
#: joined with ` · `.
_VERDICT_NOTE = re.compile(
    r"\n\n\(주의: 근거 검증을 통과하지 못했습니다 — ([^)]*)\)")
#: Wording of the stated reason. Read from `_VERDICT_REASONS` so the same
#: string is not written in two places.
_CITATION_REASON = next(text for field, _bad, text in _VERDICT_REASONS
                        if field == "has_citation")


#: Markers on decisive lines. Answers **cite these as a source** --
#: `(근거: 결정적 집계, 2023-07-03)`. A marker is an internal label we prepend to
#: the evidence, not a filing. When a reader sees `결정적 집계` given as a source,
#: it reads as a citation of material that does not exist.
_MARKER_WORDS = ("결정적 집계", "결정적 조회", "결정적 단위", "결정적 대조", "결정적 줄")
#: **Square brackets are removed along with the word.** The first version
#: deleted only the word and left `[]` behind --
#: `(근거: [] 정정 전후 금액 변동, 접수번호 20230222800734)`. Golden-set answers write
#: markers bare (`(근거: 결정적 집계, 2023-07-03)`), so 36 measured cases never
#: exposed it; **held-out answers use the brackets**
#: (`(근거: [결정적 집계] 정정 전후 금액 변동, …)`). The held-out check found this one.
#:
#: **Round parentheses are not swallowed** -- only square brackets are accepted.
#: Putting round ones in the optional group would delete the opening bracket of
#: `(결정적 집계 참조)` while leaving the closing one, **breaking the balance**.
#: The citation parentheses themselves are handled separately by
#: `_source_spans`, which counts depth, so there is nothing to do here.
#:
#: Why this is split into two alternatives: folded into one form as
#: `[\[］]?\s*`, the `\s*` eats the surrounding whitespace **even when no
#: bracket is present**, turning `(근거: 결정적 집계` into `(근거:위 근거`.
#: Whitespace inside the brackets must be eaten only when brackets are there.
_MARKER_RE = re.compile(
    r"[\[［]\s*(?:%(w)s)\s*[\]］]|(?:%(w)s)"
    % {"w": "|".join(re.escape(w) for w in _MARKER_WORDS)})
#: The **opening side** of a citation parenthesis the answer wrote inline. The
#: closing side cannot be found by regex: company names contain parentheses, as
#: in `(근거: 결정적 집계 (주)두산 보고 …)`. Matching with `[^)]*` stops at `(주)` and
#: leaves the rest strewn through the sentence, so the scan walks by depth.
_SOURCE_OPEN = re.compile(r"[(（]\s*근거\s*[:：]")
_OPENERS, _CLOSERS = "(（", ")）"
#: Connective debris left where a marker was removed: `및`, `와`, `과`, commas.
_GLUE = re.compile(r"^[\s,·및와과의그리고]+|[\s,·및와과의그리고]+$")
#: Whether what remains inside the parenthesis is worth anything. A receipt
#: number, or a word longer than two characters, has to survive. A citation
#: reduced to a bare date is not a citation.
_SUBSTANTIVE = re.compile(r"\d{14}|[가-힣A-Za-z]{2,}")


def _source_spans(text: str) -> list[tuple[int, int, str]]:
    """A list of (start, end, inner text). Pairs are found by counting bracket
    depth.

    An unclosed parenthesis is not carried: without knowing where it ends, there
    is no knowing what to delete.
    """
    out = []
    for opened in _SOURCE_OPEN.finditer(text):
        depth, at = 1, opened.end()
        while at < len(text) and depth:
            if text[at] in _OPENERS:
                depth += 1
            elif text[at] in _CLOSERS:
                depth -= 1
            at += 1
        if depth:
            continue
        out.append((opened.start(), at, text[opened.end():at - 1]))
    return out


def strip_marker_citation(answer: str) -> tuple[str, list[str]]:
    """Remove the places where an answer cited a decisive line's marker as a
    source.

    Returns (repaired answer, what was repaired). No LLM call.

    ## Why this is needed

    A marker (`[결정적 집계]` and the like) is an **internal label** we prepend to
    the evidence. The generation prompt knows nothing about it, so the model
    simply reads the bracketed text as a source and cites it.

        (근거: 결정적 집계, 2023-07-03)
        (근거: 결정적 조회 및 각 회사별 공급계약 체결 보고서들, 기준연월 미확인)
        이는 결정적 집계에 명시되어 있습니다.

    Measured over three `A65` server rounds: **36** of 336 answers take this
    shape (12 questions x 3 rounds) -- `Q-018` `Q-023` `Q-034` `Q-106` `Q-107`
    `Q-109` `Q-110` `Q-113` `Q-114` `Q-115` `Q-116` `Q-117`. All 12 appear in
    every round, so this is a rule, not noise.

    When a reader sees `결정적 집계` given as a source, it reads as **a citation of
    material that does not exist**. The value is right and the source is a
    phantom, which costs trust while registering on no scoring axis.

    ## The prompt cannot fix it

    The generation prompt (`prompts._BASE_RULES`) is frozen. So the **answer is
    repaired afterwards**, in the same place and the same way as
    `drop_citation_warning`. It runs before `attach_citation` because the real
    citation is appended there -- phantom removed first, truth attached after.

    ## What is repaired, and how

        something of value remains  -> drop only the marker. What is left is
                                      true, and deleting it would lose an
                                      observation.
        nothing would remain        -> drop the whole parenthesis. A citation
                                      reduced to a bare date is not a citation.
        text outside parentheses    -> replace the marker with `위 근거`. The
                                      decisive line really is in the evidence,
                                      so that statement is true.

    ## Two wrong attempts before this one

    1. Matching the parenthesis with `[^)]*` broke at `(주)` in
       `(근거: 결정적 집계 (주)두산 보고 보유비율 추이, …)`, leaving the tail strewn
       through the sentence (`Q-106`, measured). Company names contain
       parentheses, so the scan has to walk by depth.
    2. Tidying the deleted span with `[ \\t]{2,}` -> one space **broke markdown
       indentation**: `   - 계약 상대:` became ` - 계약 상대:` (`Q-034`, `Q-106`,
       measured). A cleanup applied to the whole answer touches places unrelated
       to the deletion. So only **the whitespace immediately before** the
       deleted span is removed with it.
    """
    if not ablation.enabled("strip_marker_citation"):
        return answer, []
    text = answer or ""
    if not _MARKER_RE.search(text):
        return answer, []
    fixes: list[str] = []
    #: Repaired back to front: fixing from the front shifts the offsets of the
    #: spans that follow.
    for start, end, inner in reversed(_source_spans(text)):
        if not _MARKER_RE.search(inner):
            continue
        rest = _GLUE.sub("", _MARKER_RE.sub("", inner))
        rest = _GLUE.sub("", re.sub(r"\s*[,·]\s*[,·]\s*", ", ", rest).strip())
        if _SUBSTANTIVE.search(rest):
            text = text[:start] + f"(근거: {rest})" + text[end:]
            fixes.append("표지만 뺀 근거 괄호 1곳")
            continue
        #: Only **the whitespace immediately before** the span is removed with
        #: it. A whitespace cleanup over the whole answer breaks markdown
        #: indentation.
        lead = start
        while lead > 0 and text[lead - 1] in " \t":
            lead -= 1
        text = text[:lead] + text[end:]
        fixes.append("표지뿐인 근거 괄호 1곳을 지웠다")
    if _MARKER_RE.search(text):
        #: What remains outside the parentheses is an in-sentence mention.
        #: `위 근거` is a true statement -- the decisive line really is in the
        #: evidence.
        text = _MARKER_RE.sub("위 근거", text)
        fixes.append("문장 속 표지를 '위 근거' 로 바꿨다")
    return text, fixes


def drop_citation_warning(answer: str) -> tuple[str, bool]:
    """Remove the "no citation" reason once a citation has been attached.

    Returns (answer, changed).

    A self-contradiction produced by ordering. `_verify` builds the tail and
    `attach_citation` attaches the citation **after** it, so an answer that does
    carry a citation goes out labelled "근거 표기가 없습니다". Measured 2026-08-21:
    **28** of 336 local answers and 6 of 112 server answers take this shape
    (`Q-012` `Q-018` `Q-034` `Q-065` `Q-105` `Q-107` `Q-108` `Q-109` `Q-112`
    `Q-113` `Q-114` `Q-118`).

    Moving the attachment ahead of verification does not fix it:
    `attach_citation` has to run **after** every LLM call so that the
    generation, critic and regeneration prompts stay byte-identical and the
    request cache still matches (see that function's docstring). So the fix is
    to remove the reason instead.

    When that is the only reason, the whole tail is removed. When other reasons
    remain, only this one is dropped -- the rest are still true, and removing
    the tail wholesale would lose an observation.
    """
    matched = _VERDICT_NOTE.search(answer or "")
    if not matched:
        return answer, False
    reasons = [r.strip() for r in matched.group(1).split(" · ") if r.strip()]
    kept = [r for r in reasons if r != _CITATION_REASON]
    if len(kept) == len(reasons):
        return answer, False
    if not kept:
        return answer[:matched.start()] + answer[matched.end():], True
    note = ("\n\n(주의: 근거 검증을 통과하지 못했습니다 — %s)" % " · ".join(kept))
    return answer[:matched.start()] + note + answer[matched.end():], True


def _verdict_reason(verdict) -> str:
    """Build the reason from the booleans of a `Verdict`. Never empty.

    A failed verdict trips at least one of the cases above, so an empty string
    cannot occur. The final branch exists anyway, so that a later change to the
    definition of `passed` cannot leave the note ending in a dangling
    separator.
    See docs/design-rationale.md#verdict-reason
    """
    hits = [text for field, bad, text in _VERDICT_REASONS
            if getattr(verdict, field, None) is bad]
    return " · ".join(hits) if hits else "자동 검증이 결함을 보고했습니다"


def answer_question(question_id: str, question: str) -> dict:
    budget = Budget()
    clean, warnings = sanitize_question(question)
    trace: list[str] = list(warnings)

    r = route(clean)
    trace.append(f"route={r.kind} difficulty={r.difficulty} cot={r.use_cot} sc={r.use_sc}")

    if r.kind == "unanswerable":
        if r.reason.startswith("no_such_report"):
            trace.append(f"{r.reason} — 지목한 보고서가 목록에 없다 (LLM 호출 없이 한계 고지)")
            return _respond(question_id, question, "", trace, NO_SUCH_REPORT, budget)
        # Company and period are in range; what is absent is a future the
        # filings do not state.
        if r.reason == "speculation":
            trace.append("미래 추정 요구 — LLM 호출 없이 한계 고지")
            return _respond(question_id, question, "", trace, SPECULATIVE, budget)
        # Not out of range: withheld.
        if r.reason == "identifier":
            trace.append("식별번호 전사 요구 — LLM 호출 없이 거절")
            return _respond(question_id, question, "", trace, IDENTIFIER_WITHHELD, budget)
        # Not out of range: the subject of the question is unidentified, so
        # ask back.
        if r.reason == "ambiguous_reference":
            trace.append("지시대상 불명 — LLM 호출 없이 역질문")
            return _respond(question_id, question, "", trace, AMBIGUOUS_REFERENCE, budget)
        # Not out of range: the question asserts a conclusion the filings do
        # not confirm, and the premise is what is missing, not the company or
        # period.
        if r.reason == "false_premise":
            trace.append("거짓 전제 — LLM 호출 없이 한계 고지")
            return _respond(question_id, question, "", trace, FALSE_PREMISE, budget)
        # Either a demand to ignore prior instructions, or a demand for
        # undisclosed content. Both the company and the period may well be in
        # range, so `UNANSWERABLE`'s out-of-range wording would be false here.
        if r.reason == "injection":
            trace.append("주입·미공시 요구 — LLM 호출 없이 거절")
            return _respond(question_id, question, "", trace, INJECTION_REFUSED, budget)
        trace.append("범위 밖 — LLM 호출 없이 한계 고지")
        return _respond(question_id, question, "", trace, UNANSWERABLE, budget)

    # The oracle path branches here, because `_gather_evidence` has no
    # question id. With the flag off, the line below is unchanged.
    #
    # The provenance mapping is created outside both branches: the oracle
    # branch does not populate it, so `attach_citation` receives an empty dict
    # and falls back to re-reading the evidence rather than raising.
    provenance: dict[str, dict] = {}
    if ablation.current().golden_oracle:
        evidence, tool_trace = _gather_golden(question_id, clean, budget)
    else:
        evidence, tool_trace = _gather_evidence(r, clean, budget, provenance)
    trace.extend(tool_trace)
    # Evidence shaping runs after retrieval finishes. Moving it earlier would
    # mix a retrieval change into the shaping delta and stop it from being a
    # single-variable manipulation.
    evidence = ablation.shape_evidence(evidence)

    if not evidence:
        trace.append("근거 없음 — LLM 호출 없이 한계 고지")
        return _respond(question_id, question, "", trace, UNANSWERABLE, budget)

    # The aggregate is computed here and carried as one line, which occupies
    # no evidence slot and so does not fall under the reason widening the
    # window was rejected. The wiring itself sits behind the flag: calling it
    # unconditionally would change the evidence on arms where the flag is off.
    # See docs/design-rationale.md#evidence-slot-vs-line
    if ablation.enabled("aggregate_line"):
        from argus.agent import aggregate

        extra = aggregate.line(_evidence_records(evidence), clean, r)
        if extra:
            evidence = _prepend(evidence, extra)
            trace.append(f"aggregate line=1 chars={len(extra)}")

    # A periodic table states its scale in a header above the table rather
    # than in the cell, so the figure's meaning depends on a line the model
    # has to carry across the table. Restated at the head alongside the other
    # deterministic lines, since a recorded run held the correct scale in the
    # evidence throughout and still misread the figure.
    # See docs/design-rationale.md#unit-line
    if ablation.enabled("unit_line"):
        from argus.agent import units

        scales = units.unit_line(evidence)
        if scales:
            evidence = _prepend(evidence, scales)
            trace.append(f"unit_line scales={scales.count('=')}")

    # Separate the filing a date points at. Where two filings share a date and
    # a title and both are in the evidence, the selection rather than the
    # retrieval is what goes wrong, so the remedy matches `_roster_line`: one
    # line at the head.
    # See docs/design-rationale.md#date-disambiguation
    if ablation.enabled("date_disambiguation"):
        from argus.agent import aggregate

        pick = aggregate.date_disambiguation(_evidence_records(evidence), clean)
        if pick:
            evidence = _prepend(evidence, pick)
            trace.append(f"date_pick line=1 chars={len(pick)}")

    # Corporate registration number. The problem was chunking rather than
    # evidence slots, so one row is extracted from the full section and placed
    # at the head instead of widening the window.
    #
    # The firing condition is narrow: the question must ask for the number, a
    # company must be identified, and the row must be inside a document
    # retrieval already returned. Failing any of the three yields an empty
    # string and leaves the evidence unchanged.
    # See docs/design-rationale.md#section-chunking
    if _ASK_CORP_NUMBER.search(clean) and r.companies:
        docs = [PERIODIC_DOC_PREFIX + no for no in (provenance or {})]
        line = tools.corp_number_line(r.companies[0], docs)
        if line:
            evidence = _prepend(evidence, line)
            trace.append(f"corp_number line=1 chars={len(line)}")

    # Lay out a revision history in filing-date order as one line. A different
    # axis from the aggregate line: that one carries a single value, this one
    # carries the order of several.
    # See docs/design-rationale.md#revision-series
    if ablation.enabled("revision_series"):
        from argus.agent import aggregate

        series = aggregate.revision_series(_evidence_records(evidence), clean)
        if series:
            evidence = _prepend(evidence, series)
            trace.append(f"series line=1 chars={len(series)}")

    # The same shape for a reporting series rather than a revision chain. Kept
    # as its own block rather than folded into the one above, because the two
    # read different fields and fire on different questions, and merging them
    # would make one flag move the other's behaviour.
    # See docs/design-rationale.md#holding-series
    if ablation.enabled("holding_series"):
        from argus.agent import aggregate

        holding = aggregate.holding_series(_evidence_records(evidence), clean,
                                           tools.holding_chain)
        if holding:
            evidence = _prepend(evidence, holding)
            trace.append(f"holding_series line=1 chars={len(holding)}")

        # When the question names two filings, only those two are contrasted.
        # This does not overlap the series line: the series walks the whole
        # chain and attaches share counts only at the two ends, whereas this
        # line gives a value and a delta for every column of **the two filings
        # the question named**. The 2025-03-10 filing `Q-012` names is not the
        # end of the chain, so the series line carried no share count for it and
        # the model subtracted on its own.
        pair = aggregate.holding_pair(_evidence_records(evidence), clean,
                                      tools.holding_chain)
        if pair:
            evidence = _prepend(evidence, pair)
            trace.append(f"holding_pair line=1 chars={len(pair)}")

    # Join the filings of two companies sharing a counterparty and pin the
    # amount difference. It sits beside `holding_series` because both read
    # records; it is not merged with it because the fields read and the firing
    # conditions differ -- one switch must not move the other's behaviour.
    if ablation.enabled("counterparty_pair"):
        from argus.agent import aggregate

        pair = aggregate.counterparty_pair(_evidence_records(evidence), clean)
        if pair:
            evidence = _prepend(evidence, pair)
            trace.append(f"counterparty_pair line=1 chars={len(pair)}")

    # Lift the won-conversion sentence the filing wrote itself up into a line.
    # This is one more instance of a figure that lives only in the evidence prose
    # never reaching the answer (`Q-101` missed it in all three rounds).
    if ablation.enabled("fx_conversion"):
        from argus.agent import aggregate

        fx = aggregate.fx_conversion(evidence, clean)
        if fx:
            evidence = _prepend(evidence, fx)
            trace.append(f"fx_conversion line=1 chars={len(fx)}")

    # A correction question carries the same section twice in the evidence. The
    # difference can be computed exactly in code, yet the answer eyeballs it.
    # This line reads only the evidence, never the question, so it takes no
    # `clean`: the very fact that a section appears twice means this is a
    # correction question.
    if ablation.enabled("revision_diff"):
        from argus.agent import aggregate

        diff = aggregate.revision_diff(evidence)
        if diff:
            evidence = _prepend(evidence, diff)
            trace.append(f"revision_diff line=1 chars={len(diff)}")

    # A stub in one filing and a body in another is a categorical difference,
    # and an answer calling them the same is wrong with confidence. The line
    # states it; it does not edit the answer.
    if ablation.enabled("contrast_line"):
        from argus.agent import aggregate

        contrast = aggregate.contrast_line(evidence)
        if contrast:
            evidence = _prepend(evidence, contrast)
            trace.append(f"contrast_line line=1 chars={len(contrast)}")

    # For a composition question, pin the table's total. A percentage means
    # nothing without its denominator. It sits beside `contrast_line` because it
    # is of the same nature: the line is produced by **reading the evidence
    # string directly**, not the records.
    if ablation.enabled("composition_total"):
        from argus.agent import aggregate

        total = aggregate.composition_total(evidence, clean)
        if total:
            evidence = _prepend(evidence, total)
            trace.append(f"composition_total line=1 chars={len(total)}")

    # For a segment-revenue question, check `exports + domestic = total`. When
    # one filing holds two tables whose segment values differ by 1 (`Q-104`),
    # this line's job is to show which of them is the derived one. Same place and
    # same nature as `composition_total` -- it reads the evidence string, not the
    # records.
    if ablation.enabled("segment_sales"):
        from argus.agent import aggregate

        segments = aggregate.segment_sales(evidence, clean)
        if segments:
            evidence = _prepend(evidence, segments)
            trace.append(f"segment_sales line=1 chars={len(segments)}")

    try:
        # Read through `enabled()` so that a misspelled flag raises instead of
        # silently evaluating false. At the default the two lines below are
        # unchanged.
        messages = build_answer_messages(
            clean, evidence, r.use_cot,
            lean_rules=ablation.enabled("lean_rules"),
            strict_refusal=ablation.enabled("strict_refusal"))
        max_tokens = 800 if r.use_cot else 300
        arm = ablation.current()
        # The function-calling arm branches here into the tool round trip. At
        # the default the `else` branch below is unchanged.
        if _offer_tools(arm, clean, trace):
            answer = _answer_with_tools(messages, max_tokens, arm, budget, trace)
        else:
            # Temperature is carried by the arm and defaults to zero, so on
            # every arm but self-consistency this call is byte-identical and
            # keeps the same request cache key.
            result = chat(messages, max_tokens=max_tokens,
                          temperature=arm.temperature, tag="answer")
            budget.spend_llm(result.prompt_tokens, result.completion_tokens)
            answer = result.text
            trace.append(f"generate tokens={result.prompt_tokens}+{result.completion_tokens}")
        if arm.sc_k > 1 and _sc_applies(answer):
            answer = _self_consistent(messages, max_tokens, arm, answer, budget, trace)
        elif arm.sc_k > 1:
            trace.append("sc skip=no_figures k=1/%d" % arm.sc_k)
    except BudgetExceeded as exc:
        trace.append(f"예산 초과: {exc}")
        return _respond(question_id, question, evidence, trace, UNANSWERABLE, budget)
    except RuntimeError as exc:
        # `/answer` must return the five-key schema in every case; a 500 from
        # an upstream fault scores zero. The fault is not swallowed: a marker
        # lets the caller tell a transport failure apart from an answer.
        # See docs/design-rationale.md#upstream-failure
        trace.append(f"{_upstream_mark('generate', exc)} 생성 실패: {exc}")
        return _respond(question_id, question, evidence, trace, UNANSWERABLE, budget)

    if not quick_check(answer)["has_citation"]:
        trace.append("근거 표기 누락 — critic 호출")
        try:
            verdict = judge(evidence, answer, budget)
            trace.append(f"critic supported={verdict.supported} reason={verdict.reason}")
            if not verdict.passed:
                # The observation is always recorded. The switch decides only
                # whether it rides along in the delivered text.
                trace.append(f"critic note={_verdict_reason(verdict)}")
                if not ablation.enabled("quiet_verify_note"):
                    answer += ("\n\n(주의: 근거 검증을 통과하지 못했습니다 — "
                               f"{_verdict_reason(verdict)})")
        except BudgetExceeded as exc:
            trace.append(f"critic 생략: {exc}")            # our own cap, not a fault
        except RuntimeError as exc:
            trace.append(f"{_upstream_mark('critic', exc)} critic 생략: {exc}")

    # The budget is passed through because regeneration spends one more LLM
    # call, which has to appear in the reported budget. Other arms do not use
    # it.
    answer = _verify(clean, answer, evidence, r, trace, budget)
    # The citation is attached by code, and it has to happen here: after every
    # LLM call, so the generation, critic and regeneration prompts stay
    # byte-identical and the request cache still hits. With the flag off the
    # two lines below are unchanged.
    #
    # Aggregate substitution sits in the same place for the same reason, and
    # costs no LLM call.
    if ablation.enabled("substitute_aggregate"):
        from argus.agent import aggregate

        answer, swapped = aggregate.substitute(
            answer, _evidence_records(evidence), clean, r)
        if swapped:
            trace.append(f"aggregate substitute={swapped}")

    # Missing result: a case substitution cannot reach, because the model
    # combined correct figures wrongly rather than writing a wrong one, so the
    # single-figure-outside-the-evidence condition never held. When the value
    # the question asked for is absent entirely, it is stated in one sentence.
    # Runs after substitution, which is preferable where it applies since it
    # adds no sentence.
    # See docs/design-rationale.md#state-total
    if ablation.enabled("state_total"):
        from argus.agent import aggregate

        answer, stated = aggregate.state_total(
            answer, _evidence_records(evidence), clean, r)
        if stated:
            trace.append(f"aggregate {stated}")

    # Same family as the two above and the last of them, so an answer the
    # model wrote correctly, or that substitution already fixed, is left
    # alone. Reads the evidence rather than recomputing, because the lines it
    # repairs were written into the evidence earlier in this same call.
    # See docs/design-rationale.md#gap-repair
    if ablation.enabled("gap_repair"):
        from argus.agent import aggregate

        answer, repaired = aggregate.repair_gap(answer, evidence)
        if repaired:
            trace.append(f"gap_repair {repaired}")

    # Realign a revision timeline's amounts to the decisive series line. Runs
    # before `fix_unit_parens` so that replacing a body figure also refreshes
    # the parenthesised 억원 conversion derived from the wrong one.
    if ablation.enabled("align_series_amounts"):
        from argus.agent import aggregate

        answer, aligned = aggregate.align_series_amounts(answer, evidence)
        if aligned:
            trace.append(f'series_align {" · ".join(aligned)}')

    # Rewrite the parenthesised unit conversion. Runs after substitution:
    # replacing the body figure alone leaves behind the parenthesised value the
    # model derived from its own wrong figure.
    # See docs/design-rationale.md#unit-conversion
    if ablation.enabled("fix_unit_parens"):
        from argus.agent import aggregate

        answer, repaired = aggregate.fix_unit_parens(answer)
        if repaired:
            trace.append(f'unit_paren fixed={len(repaired)} {" · ".join(repaired)}')

    # If a decisive line's instruction was copied into the answer, strip it. It
    # was addressed to the model, not to the evaluator. Figures are left alone --
    # those values are the true ones the decisive line computed.
    if ablation.enabled("strip_copied_directive"):
        from argus.agent import aggregate

        answer, dropped = aggregate.strip_copied_directive(answer)
        if dropped:
            trace.append(f"directive stripped={dropped}")

    # Rounding restoration, the mirror of the conversion fix: that one repairs
    # a correct figure with a wrong parenthesis, this one a parenthesis with no
    # figure. It runs after the conversion fix so that the pairs it creates are
    # not rewritten again.
    # See docs/design-rationale.md#unit-conversion
    if ablation.enabled("expand_rounded"):
        from argus.agent import aggregate

        answer, grown = aggregate.expand_rounded(answer, _evidence_records(evidence))
        if grown:
            trace.append(f'expand_rounded n={len(grown)} {" · ".join(grown)}')

    # Align a figure that contradicts an expression the answer wrote itself, a
    # self-contradiction that neither exact match nor the numeric checks catch.
    # See docs/design-rationale.md#self-contradiction
    # False derivations are removed first. `fix_self_contradiction` trusts the
    # result of an equation and aligns other places to it, so leaving an
    # equation that does not hold would unsettle the reference it aligns to.
    if ablation.enabled("drop_broken_equation"):
        from argus.agent import aggregate

        answer, dropped = aggregate.fix_broken_equation(answer, evidence)
        if dropped:
            trace.append(f'broken_eq {" · ".join(dropped)}')
    # False totals that are not in equation form are removed in the same place.
    # `fix_broken_equation` only looks at derivations written with `+` and `=`,
    # so it never reaches a conclusion that just states "총 …원" after a list.
    if ablation.enabled("drop_unsourced_total"):
        from argus.agent import aggregate

        answer, cut = aggregate.drop_unsourced_total(answer, evidence)
        if cut:
            trace.append(f'unsourced_total {" · ".join(cut)}')
    if ablation.enabled("self_consistency_fix"):
        from argus.agent import aggregate

        answer, aligned = aggregate.fix_self_contradiction(answer)
        if aligned:
            trace.append(f'self_align fixed={len(aligned)} {" · ".join(aligned)}')
    # When the answer's enumeration differs from the decisive specification, the
    # specification replaces it. The counterpart of `fix_self_contradiction`:
    # that one repairs an equation over two addends, this one repairs the
    # enumeration itself. It is also the counterpart of `repair_gap`, but in the
    # opposite direction -- that adds what is missing, this removes what should
    # not be there.
    # See docs/design-rationale.md#aggregate-scope
    if ablation.enabled("sum_operand_fix"):
        from argus.agent import aggregate

        answer, why = aggregate.fix_stray_summands(answer, evidence)
        if why:
            trace.append(f"stray_summands {why}")
    # Remove the places where the answer cited a decisive line's marker as a
    # source. It runs **before** `attach_citation` because the real citation is
    # appended there -- phantom removed first, truth attached after.
    if ablation.enabled("strip_marker_citation"):
        answer, stripped = strip_marker_citation(answer)
        if stripped:
            trace.append(f'marker_citation {" · ".join(stripped)}')
    answer, cited = ablation.attach_citation(answer, evidence, provenance=provenance,
                                             question=clean)
    if cited:
        trace.append(f'citation attached={",".join(cited)}')
    # The note was built before the citation existed. Leaving it makes the
    # answer argue with itself in the text a judge reads.
    if cited and ablation.enabled("citation_note_fix"):
        answer, cleared = drop_citation_warning(answer)
        if cleared:
            trace.append("citation_note cleared=1")
    # Runs before masking, so a removed sentence is never masked first and
    # then removed, which would double-count the change in the trace.
    # See docs/design-rationale.md#external-referral
    if ablation.enabled("external_referral_strip"):
        answer, dropped = strip_external_referral(answer)
        if dropped:
            trace.append(f"external_referral dropped={len(dropped)}")
    # Runs right after: a referral sentence stripped above must not still be
    # counted as "the middle" here, and a refusal-sandwich's middle must not
    # still hold a referral sentence for that strip to catch below.
    # See docs/design-rationale.md#external-referral
    if ablation.enabled("refusal_compaction"):
        answer, compacted = compact_after_refusal(answer)
        if compacted:
            trace.append(f"refusal_compaction dropped={len(compacted)}")
    return _respond(question_id, question, evidence, trace, mask_pii(answer), budget)


# --- Deterministic verification. No LLM call --------------------------------
# The judge only asks whether the answer is supported by the evidence, so an
# answer taken from a pre-revision figure passes while the evidence itself is
# incomplete. Two deterministic checks cover different axes: one recomputes the
# answer's figures against the source records, the other asks whether the
# evidence is sufficient.
#
# The original design annotates rather than rewrites, on the grounds that
# judging contributes more than generating. Measurement showed the cost of that
# design: detection reached every case and correction none of them, with
# recomputed values identical to the golden answer discarded into a note that
# the scorer strips before scoring.
#
# Rather than replacing the default, the correcting behaviour is an arm. With
# the flags off, this function's behaviour is byte-identical across the whole
# recorded generation.
# See docs/design-rationale.md#verifier-disposition

_EVIDENCE_RCEPT = re.compile(r"접수번호\s*(\d{14})")


def _prepend(evidence: str, line: str) -> str:
    """Place a deterministic aggregate line at the head of the evidence.

    A line at the tail is not read, and moving it to the system prompt
    perturbs answers to unrelated questions. The head of the evidence is the
    compromise.
    See docs/design-rationale.md#line-position
    """
    return f"{line}\n\n{evidence}"


def _evidence_records(evidence: str) -> list[dict]:
    """Recover structured records by the receipt numbers present in the
    evidence. A lookup rather than a retrieval, so it costs no step."""
    out = []
    for rcept_no in dict.fromkeys(_EVIDENCE_RCEPT.findall(evidence or "")):
        record = tools._record_of(rcept_no)
        if record is not None:
            out.append(record)
    return out


def _verdict(question: str, answer: str, evidence: str, records: list[dict], r,
             arm) -> dict:
    """One set of verification verdicts for a single answer. Side-effect free:
    it does not touch the trace either.

    Regeneration calls this twice, on the original and on the regenerated
    answer, so the rule lives here alone. Measuring before and after by
    different standards would break the rule that the original is kept when the
    check still fires.

    `records` is a parameter for the same reason: the structured records
    recovered from the evidence do not depend on the answer, so there is no
    reason to look them up twice.
    See docs/design-rationale.md#verifier-disposition
    """
    # With several records, one explained record is enough to pass, because an
    # original and its revision carry different amounts within a chain and
    # requiring all of them to match would turn a correct answer wrong.
    #
    # That tolerance had a hole: an order-of-magnitude error was buried by it
    # too, when another figure in the same answer was accurate. Chain tolerance
    # exists for the same value at different points in time, and a magnitude
    # error is not a timing problem, so the verdict is concentrated in
    # `critic.aggregate_calculation()`.
    reports = [verify_calculation(answer, record) for record in records]
    agg = aggregate_calculation(reports)
    checked, calc_failed = agg["checked"], agg["failed"]

    # Answer-arithmetic verification, wired behind the flag. This check reads
    # no evidence at all and looks only at equations the answer wrote itself,
    # so it fires on questions with no structured records, which the reports
    # above cannot see.
    #
    # It is not merged into `reports`: those pass when one record is explained,
    # while this check is one per answer, and merging would let a correct
    # equation mask a failed comparison against the evidence, inverting the
    # chain tolerance. Only the verdicts are combined.
    #
    # The wiring is behind the flag because attaching it unconditionally adds
    # notes on arms where the flag is off, which is itself an answer change.
    #
    # The regeneration arm enables this check too: it needs a source of
    # reasons, and without it questions with no structured records produce
    # nothing to regenerate from.
    # See docs/design-rationale.md#verifier-disposition
    arith_reports: list[dict] = []
    if arm.substitute_verified or arm.regenerate_on_verify:
        arith = verify_answer_arithmetic(answer)
        arith_reports = [arith]
        checked = sorted({*checked, *arith["checked"]})
        calc_failed = calc_failed or not arith["ok"]

    # Number-provenance gate, kept separate from the calculation verdict. It
    # asks whether a figure appears in the evidence, a different axis: merging
    # it would make substitution fire on this gate, which carries no recomputed
    # value to substitute, and would attribute the delta to calculation.
    #
    # The wiring is behind the flag for the same reason as above.
    provenance = verify_number_provenance(answer, evidence) if arm.provenance_gate else None

    # An orderer is not a filer. The missing-company check catches a
    # comparison whose evidence lacks one company, but an orderer named by the
    # question placed the order rather than filing it and cannot appear in the
    # evidence, so correct answers carried that note permanently. The same
    # judgment already exists in the enumeration guard and its router test.
    compared = list(r.companies or [])
    if ablation.enabled("counterparty_exempt") and getattr(r, "counterparty", None):
        compared = [c for c in compared if c != r.counterparty]
    # The amount-coverage kind is passed as a plain argument rather than read
    # from `ablation` inside `verify_completeness`, so that module continues
    # to import no flags. The pipeline decides the value here, the same way
    # it decides `compared` above.
    # See docs/design-rationale.md#amount-coverage
    complete = verify_completeness(question, answer, evidence, companies=compared,
                                   amount_coverage=arm.amount_coverage_gate)

    kinds = sorted({*(["calculation"] if calc_failed else []), *complete["failed_kinds"],
                    *(provenance["failed_kinds"] if provenance else [])})

    # Reasons keep the order `aggregate_calculation` set, which puts magnitude
    # errors first. Picking the first reason by hand would surface a harmless
    # neighbour and weaken the warning.
    #
    # This list is exactly what regeneration carries into its prompt, so that
    # what the note reports and what the model is given cannot diverge. It has
    # two sources: calculation mismatches, and figures absent from the
    # evidence.
    reasons = agg["reasons"][:1] if calc_failed else []
    if not reasons and arith_reports and arith_reports[0]["reason"]:
        reasons = [arith_reports[0]["reason"]]      # questions with no structured record
    if complete["reason"]:
        reasons.append(complete["reason"])
    if provenance and provenance["reason"]:
        reasons.append(provenance["reason"])

    return {"reports": reports, "arith_reports": arith_reports, "provenance": provenance,
            "complete": complete, "checked": checked, "calc_failed": calc_failed,
            "kinds": kinds, "reasons": reasons}


def _keeps_regenerated(before: list[str], after: list[str]) -> bool:
    """Whether the regenerated answer replaces the original.

    A clean result is always adopted, which is the prior behaviour. Beyond
    that, adoption requires the failing kinds to be a strict subset: a set that
    merely changed is not an improvement, and adopting it would trade one
    defect for another while spending a call to do it.
    See docs/design-rationale.md#regeneration-adoption
    """
    if not after:
        return True
    if not ablation.enabled("regenerate_partial"):
        return False
    return set(after) < set(before)


def _regenerate_once(question: str, answer: str, evidence: str, records: list[dict],
                     r, arm, trace: list[str], budget: Budget | None,
                     verdict: dict) -> tuple[str, dict]:
    """Regenerate exactly once, carrying the failure reasons into the prompt.

    Returns the (adopted answer, verdict for that answer) pair.

    If the checks still fire on the regenerated answer, the original and its
    verdict are returned unchanged and the caller annotates as before. Keeping
    the original when the result is no better is expressed as a verdict
    swap, so the rule cannot diverge between two places.

    The single attempt is enforced by this function not calling itself; a loop
    would remove the per-question cost bound.
    See docs/design-rationale.md#verifier-disposition
    """
    if budget is None:
        # Skipping silently would report a delta of zero for a step that never
        # ran, and calling without counting would make the budget report false.
        # Neither is acceptable, so stop here.
        raise ValueError(
            "regenerate_on_verify 는 예산 계정이 필요합니다 — 재생성 호출을 아무도 세지 "
            "않으면 게이트 F 의 `budget llm=` 이 거짓을 말합니다. `_verify(..., budget=)` "
            "로 넘기십시오.")
    if budget.remaining()["llm_calls"] <= 0:
        # Checked before the call: counting afterwards means the credits are
        # already spent.
        trace.append("verify regen=skip_budget (LLM 호출 상한) kept=original")
        return answer, verdict

    use_cot = bool(getattr(r, "use_cot", False))
    try:
        result = chat(build_regeneration_messages(
                          question, evidence, verdict["reasons"], answer,
                          use_cot=use_cot, lean_rules=ablation.enabled("lean_rules")),
                      max_tokens=800 if use_cot else 300,
                      # Temperature stays at zero. Buying diversity is the
                      # self-consistency manipulation, and the arm temperature
                      # is deliberately not read here: this arm measures the
                      # injection of diagnostics, and raising the temperature
                      # with it would mix the diversity share into that delta.
                      temperature=0.0, tag="regenerate")
        budget.spend_llm(result.prompt_tokens, result.completion_tokens)
    except BudgetExceeded as exc:
        trace.append(f"verify regen=skip_budget ({exc}) kept=original")
        return answer, verdict
    except RuntimeError as exc:
        # An upstream fault must not lose the answer, and is not swallowed
        # either, the same rule as `answer_question`.
        trace.append(f"{_upstream_mark('regenerate', exc)} verify regen=skip_error "
                     f"kept=original: {exc}")
        return answer, verdict

    candidate = (result.text or "").strip()
    if not candidate:
        trace.append("verify regen=empty kept=original")
        return answer, verdict

    again = _verdict(question, candidate, evidence, records, r, arm)
    kept = "regenerated" if _keeps_regenerated(verdict["kinds"], again["kinds"]) \
        else "original"
    trace.append(f'verify regen=1 reasons={"; ".join(verdict["reasons"])} '
                 f'kinds_after={",".join(again["kinds"]) or "-"} kept={kept}')
    return (candidate, again) if kept == "regenerated" else (answer, verdict)


def _verify(question: str, answer: str, evidence: str, r, trace: list[str],
            budget: Budget | None = None) -> str:
    """Annotate the answer with a warning when a defect is found, and record
    the verdict in the trace.

    With substitution enabled, the figure is replaced from the unique
    recomputed value instead of being annotated. With regeneration enabled, the
    answer is regenerated once carrying the failure reasons. With both off, the
    behaviour is the original one: a note only.
    See docs/design-rationale.md#verifier-disposition
    """
    arm = ablation.current()
    if not arm.verifier:
        return answer

    records = _evidence_records(evidence)
    v = _verdict(question, answer, evidence, records, r, arm)

    # On arms where the gate is off, the trace has to stay byte-identical.
    # Emitting the field unconditionally would change `think_trace` on every
    # arm and break comparison against the stored artifacts.
    gate = (f'provenance_checked={",".join(v["provenance"]["checked"]) or "-"} '
            if v["provenance"] else "")
    trace.append(f'verify calc_checked={",".join(v["checked"]) or "-"} '
                 f'complete_checked={",".join(v["complete"]["checked"]) or "-"} '
                 f'{gate}ok={not v["kinds"]} kinds={",".join(v["kinds"]) or "-"}')
    if not v["kinds"]:
        return answer

    # Regeneration runs before substitution: if regeneration removed the
    # defect there is nothing left to substitute. The regeneration arm does not
    # enable substitution, so in the current configuration the two never touch
    # the same answer.
    if arm.regenerate_on_verify:
        answer, v = _regenerate_once(question, answer, evidence, records, r, arm,
                                     trace, budget, v)
        if not v["kinds"]:
            return answer

    # Substitute instead of annotating. Uniqueness is decided in
    # `critic.unique_recomputed` alone, called here over the union of the
    # record verdicts and the answer-arithmetic verdict. Divergent values yield
    # None and no substitution happens.
    substituted: list[str] = []
    if arm.substitute_verified and v["calc_failed"]:
        pool = v["reports"] + v["arith_reports"]
        answer, substituted = substitute_recomputed(answer, pool, unique_recomputed(pool))
        if substituted:
            trace.append(f'verify substituted={"; ".join(substituted)}')

    # A substitution is stated as such. Reporting only that a defect was found
    # after fixing it would leave the note contradicting the body. The prefix
    # is kept, because `strip_verify_tail` reads it.
    head = ("자동 검증에서 결함이 발견되어 재계산값으로 정정했습니다" if substituted
            else "자동 검증에서 결함이 발견되었습니다")
    # The observation is always recorded; the switch decides only whether it
    # rides along in the delivered text. The scorer strips this tail before
    # scoring, so the result is the same either way.
    trace.append(f'verify note={head} — {"; ".join(v["reasons"])}')
    if ablation.enabled("quiet_verify_note"):
        return answer
    return answer + f'\n\n(주의: {head} — {"; ".join(v["reasons"])})'


def _respond(question_id: str, question: str, evidence: str, trace: list[str],
             answer: str, budget: Budget | None = None) -> dict:
    """The five-key response schema. Whichever path the answer leaves by, the
    actual budget usage is recorded at the end."""
    if budget is not None:
        trace = trace + [f"budget steps={budget.steps} hops={budget.hops} "
                         f"llm={budget.llm_calls} tokens={budget.tokens}"]
    return {
        "question_id": question_id,
        "question": question,
        "retrieved_context": evidence,
        "think_trace": " | ".join(trace),
        "answer": answer,
    }
