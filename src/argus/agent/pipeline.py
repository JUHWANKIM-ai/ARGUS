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
from argus.agent.safety import mask_pii, sanitize_question
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
    boundary intact."""
    if not markdown:
        return markdown
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
    return max(1, min(width, MAX_EVIDENCE // 2 // width))


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


def _roster_line(display: str, partners: Sequence[str]) -> str:
    """The enumerated set as one line.

    Carries neither receipt numbers nor filing counts, for the reason given in
    `_counterparty_note`.

    Same position as `aggregate.line`: what the system knows deterministically
    goes at the head of the evidence together with an explicit disposition,
    since supplying the value alone leaves the model trusting its own judgment.
    See docs/design-rationale.md#roster-position
    """
    return (f"[결정적 조회] {display} 를 계약상대로 공시한 상장사는 "
            f"{len(partners)}곳이다: {' · '.join(partners)}\n"
            f"(이 목록은 시스템이 계약상대 그래프에서 직접 열거한 것이다. "
            f"답변은 이 {len(partners)}곳을 빠짐없이 적고 임의로 줄이지 않는다.)")


def _gather_counterparty(hub: str, question: str, budget: Budget, trace: list[str],
                         take, limit: int = MAX_COUNTERPARTY_EVIDENCE,
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

    # The boundary of the set goes at the head of the evidence. An in-block
    # note alone is not read. Fires only with two or more partners, since one
    # partner is not an enumeration.
    # See docs/design-rationale.md#roster-position
    if (roster_out is not None and len(partners) >= 2
            and any(k in (question or "") for k in _ASKS_ROSTER)):
        roster_out.append(
            _roster_line(router_module.counterparty_display(hub) or hub, partners))

    taken: list[str] = []
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

    trace.append(f'hop={COUNTERPARTY_HOP} strat=counterparty tool=query_by_counterparty '
                 f'via={hub} partners={len(partners)} '
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
    for members in groups.values():        # 그룹 안은 접수일 내림차순 = 최신 대표
        members.sort(key=lambda e: (str(e.get("rcept_dt", "")), e.get("rcept_no", "")),
                     reverse=True)
    order = sorted(groups, key=lambda key: (-len(groups[key]), key))

    # 🔴 A29c — 담을 칸이 모자라 버려지는 **분포**를 한 줄로 남긴다. `groups` 는 이미
    #    만들어져 있고(pool 27 · 그룹 11), 담기는 것은 `MAX_ENUM_EVIDENCE`(5)뿐이다.
    #    상한을 올리는 안은 `A16` 이고 `R0-3`(dac2bf0)이 이미 기각했다 — 그래서 칸이
    #    아니라 줄로 낸다.
    if extra is not None and ablation.enabled("partner_profile"):
        from argus.agent import aggregate

        profile = aggregate.partner_profile(groups)
        if profile:
            extra.append(profile)
            trace.append(f"partner_profile groups={len(groups)} chars={len(profile)}")

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


# --- F-3 hop 로그 (Phase H 입력) ---------------------------------------------
# think_trace 는 사람이 읽는 문자열이지만 Phase H 의 RETRIEVAL/RANKING 판정도 여기서 나온다.
# 그래서 hop 별 질의어·필터·후보 수·후보 doc_id 를 key=value 로 남기고 parse_hops() 로 되읽는다.

_KV = re.compile(r'(\w+)=("[^"]*"|[^\s|]*)')


# --- A2 흔적 -------------------------------------------------------------------
# "끈 줄 알았는데 안 꺼진 것" 이 ablation 의 진짜 위험이다 (tests/test_ablation 머리말).
# 반대로 **켰는데 안 켜진 것**도 같은 크기의 위험이라, 융합이 실제로 돌았는지를
# `think_trace` 에 남긴다. arm 이 꺼져 있으면 `LAST_FUSION` 이 None 이라 아무것도 안 붙는다.

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
    """think_trace 를 hop 레코드로 되돌린다. Phase H 의 RETRIEVAL/RANKING 판정 입력.

        ids = [d for h in parse_hops(resp["think_trace"]) for d in h["ids"]]
        RETRIEVAL  — 골든 rcept_no 가 ids 어디에도 없음
        RANKING    — ids 에는 있으나 상위 3위 밖

    hop 로그가 없는 trace (범위 밖 질의 등) 는 빈 목록을 돌려준다.
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


# --- 상류 장애 표식 -----------------------------------------------------------
# 게이트 F 상 `/answer` 는 어떤 경우에도 5키 스키마를 돌려줘야 한다. 그래서 429 를 받아도
# 파이프라인은 죽지 않고 한계 고지를 채워 내보낸다. 문제는 **그게 모델의 거절과 구분되지
# 않는다**는 것이다 — 호출부가 그대로 캐시하면 전송 실패가 영원히 오답으로 굳는다.
# 실측으로 그 사고가 났다 (Q-051·Q-053).
#
# 스키마는 그대로 두고 `think_trace` 에 기계가 읽는 표식을 남긴다. 호출부는
# `upstream_failure()` 로 되읽어 "우리 답" 과 "전송 실패" 를 가른다.

#: 상류 장애 표식. 사람이 읽는 `생성 실패: …` 문구는 그대로 둔다 (기존 호출부 호환).
_UPSTREAM = "upstream_error"
_UPSTREAM_RE = re.compile(r"upstream_error stage=(\w+) status=(\S+) retryable=(\w+)")
_STATUS_RE = re.compile(r"실패 (\d{3})")

#: 재시도로 넘길 수 있는 상태. 429(rate limit)·408(timeout)·5xx 는 우리 답이 아니다.
RETRYABLE_STATUS = frozenset({"408", "425", "429", "500", "502", "503", "504"})


def _upstream_mark(stage: str, exc: Exception) -> str:
    match = _STATUS_RE.search(str(exc))
    status = match.group(1) if match else "-"
    retryable = status in RETRYABLE_STATUS or status == "-"
    return f"{_UPSTREAM} stage={stage} status={status} retryable={retryable}"


def upstream_failure(think_trace: str) -> dict | None:
    """상류 장애로 한계 고지가 나갔는가. 아니면 None.

    호출부(평가 러너·API)가 **오답으로 캐시하지 않기 위해** 쓰는 값이다.
    `answer` 만 보면 모델의 정당한 거절과 전송 실패가 똑같이 생겼다.
    """
    match = _UPSTREAM_RE.search(think_trace or "")
    if not match:
        return None
    return {"stage": match.group(1), "status": match.group(2),
            "retryable": match.group(3) == "True"}


#: `fault_response` 가 쓰는 stage 이름. `generate`·`critic` 과 갈라 적어야 **상류(CLOVA)
#: 장애**와 **우리 코드의 예외**를 사후에 구분할 수 있다.
FAULT_STAGE = "api"


def fault_response(question_id: str, question: str, exc: BaseException) -> dict:
    """어떤 예외로도 5키 스키마를 만들어 낸다. **`/answer` 의 마지막 방어선이다.**

    🔴 **왜 필요한가 (주최측 답변 2026-08-12 #3).** 호출 에러 시 재시도가 **2회**다.
    5xx 를 내면 같은 질의가 최대 **3회** 들어오고, 세 번 다 실패하면 그 문항은 **0점**이다.
    반면 200 + 한계 고지는 **정보한계대응이라는 채점 항목**(#9)에 걸려 0 이 아닐 수 있다.
    비결정적 실패라면 재시도가 캐시를 못 타므로 **크레딧까지 3배**로 나간다.

    🔴 **파이프라인의 기존 방어로는 부족하다.** `answer_question` 은 생성·critic 단계의
    `RuntimeError` 만 잡는다(§상류 장애 표식). `KeyError`·`TypeError` 같은 우리 버그와
    검색·계획 단계의 예외는 그대로 올라와 FastAPI 가 500 을 낸다.

    **삼키지 않는다** — `upstream_failure()` 가 되읽는 표식을 남기므로 평가 러너는 이것을
    "우리 답" 으로 캐시하지 않는다. `stage=api` 라서 상류 장애와도 갈린다.
    """
    trace = (f"{_upstream_mark(FAULT_STAGE, exc)} "
             f"서버 예외: {type(exc).__name__}: {exc}")
    return {"question_id": question_id, "question": question,
            "retrieved_context": "", "think_trace": trace, "answer": UNANSWERABLE}


#: 섹션 근거는 한 건이 최대 8,000자다. exchange 레코드 10건과 같은 상한(MAX_EVIDENCE)을
#: 쓰면 프롬프트가 8만자가 된다 — 20,000 토큰 예산을 혼자 넘긴다.
MAX_SECTION_EVIDENCE = 4
#: 섹션 근거 총량 상한. 12,000자 ≈ HCX 7~8천 토큰으로, 생성 800 토큰과 critic 을 남긴다.
MAX_SECTION_CHARS = 12_000


def _log_section(plan, corp, path, hits: list[dict], cell: dict | None = None) -> str:
    """검색 한 번의 trace 한 줄.

    `cell` 이 있으면 **그 셀의 필터**를 적는다 — 분해된 hop 은 `plan.filters` 로 검색하지
    않으므로 그것을 적으면 잰 것과 다른 것을 적는 셈이 된다 (`parse_hops` 가 이 줄을
    되읽어 Phase H 의 실패 단계 귀속 입력으로 쓴다).
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
    """경로 하나가 한 기업에게서 가져갈 근거 칸 수.

    1순위 경로가 결정적 축이고(목차 축 실측 회수 0.679 -> 0.929) 짝 경로는 '같은 지표가
    다른 목차에 실렸을 때' 를 대비한 **보험 한 칸**이다. 그래서 칸을 반씩 나누지 않고
    짝 경로에 1칸씩만 떼어 준다. 반씩 나눴더니 실측 Q-050 의 골든(1순위 경로 안에서 3위)이
    잘렸다 — 4칸을 2/2 로 쪼개면 3위가 못 들어온다.
    """
    if n_paths <= 1:
        return per_corp
    return max(1, per_corp - (n_paths - 1)) if index == 0 else 1


def _cover_order(picked: list[dict]) -> list[dict]:
    """기업별 라운드로빈. 기업 안에서는 **도착 순서 그대로**다.

    글자 예산이 근거 칸의 진짜 제약이라 순서가 곧 회수율이다. 그런데 정렬 기준을
    잘못 잡으면 정확히 반대로 간다 — 짧은 블록부터 담아 봤더니 Q-045·Q-046 의 골든
    (3,679자 · 3,371자) 이 뒤로 밀려 잘렸다. 도착 순서는 이미 목차 순위와 BM25 점수로
    정해진 순서이므로 그것을 흔들 이유가 없다.

    바꾸는 것은 **기업 축뿐**이다. 비교 질의에서 한 기업의 큰 섹션이 예산을 먼저 먹으면
    다른 기업이 통째로 빠진다 (실측 Q-049 — 두산에너빌리티 `매출 및 수주상황` 6,649자).
    기업을 번갈아 담으면 모든 기업이 한 칸씩 먼저 확보된다. 기업이 하나면 항등 변환이다.
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
    """섹션 근거를 총량 상한 안에서 이어 붙인다. 상한은 토큰 예산에서 역산한 값이다.

    예산을 넘기는 블록은 **건너뛰고 계속 채운다**. 종전처럼 거기서 끊으면 큰 블록 하나가
    뒤에 오는 작은 블록 전부를 같이 버린다 — 상한은 그대로이므로 토큰은 늘지 않는다.
    실측 Q-049: 끊으면 3블록 12,316자에서 골든이 빠지고, 건너뛰면 3블록 11,381자에
    골든 둘이 다 든다.
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


#: 🔴 **메타 축 LOO (`L6`·`L7`·`L8`) 가 떼는 `search_chunks` 인자.** 축마다 뜻이 하나여야
#: 단일 변수 조작이 성립한다:
#:
#:   `corp_name`         → `tools.search_chunks` 의 `filters["corp_name"]`
#:   `ym`·`year`·`rcept_dt` → 셋 다 `prefix["rcept_dt"]` 로 접히므로 **같은 축**이다
#:   `group`             → `_group_index(target)` · 빠지면 `exchange` 로 떨어진다
#:
#: 🔴 `base_year`·`doc_subtype` 은 **안 넣는다.** 정기공시의 사업연도는 접수일이 아니고
#: (`query.plan_section_hops` 주석 1번 — '2023년 사업보고서' 는 2024-03 접수), 함께 떼면
#: `L7` 이 접수일 축 하나의 Δ 를 못 낸다.
_META_AXIS_KEYS: dict[str, tuple[str, ...]] = {
    "meta_corp": ("corp_name",),
    "meta_date": ("ym", "year", "rcept_dt"),
    "meta_group": ("group",)}


def _search_args(args: dict, arm=None) -> dict:
    """arm 의 메타 축 LOO 를 반영해 `search_chunks` 인자에서 축을 뗀다. LLM 0회.

    🔴 **인자를 안 넘기는 것이 조작의 전부다.** 검색 단계를 끄는 것이 아니므로 BM25 는
    그대로 돌고 후보 집합만 넓어진다 — v3 가 *"구조적으로 측정 불가"* 라고 적은 메타
    프리필터를 재는 자리가 여기다 (`ablation` 머리말 §메타 축 LOO).

    🔴 기본 arm(`A8`)에서는 **받은 사전을 그대로 돌려준다.** 한 키도 안 떼고 복사도 안
    하므로 스위치를 안 건드린 경로의 검색 인자는 바이트 하나 다르지 않다.
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
    """부른 보고서마다 한 번씩 검색하고, 근거 칸을 **공평 몫 → 남은 칸** 두 벌로 담는다.

    두 벌로 나누는 이유는 실측 Q-105 다. 셀이 3개면 공평 몫이 `4 // 3 = 1` 칸인데
    `quarter` 셀에는 답이 **두 문서에 걸쳐** 있다 — 케이티 2025 분기보고서는 1분기
    (base_month 3)와 3분기(base_month 9) 둘이고, 질문이 부른 것은 3분기다. 그런데 두
    문서의 해당 섹션은 88자 스텁으로 **글자가 똑같아서 BM25 점수가 72.711 로 완전 동점**이다.
    점수로는 영원히 못 가른다.

    가르는 축은 `base_month` 인데 그것은 `section_index.json` 의 필터 축이 아니다
    (`fields` = doc_id·corp_name·doc_subtype·rcept_dt·base_year·path). 인덱스를 다시
    굽지 않고 이 문항을 살리는 길은 **둘 다 담는 것**이다 — 근거 블록 머리에 `접수번호`와
    `공시일` 이 찍히므로 어느 쪽이 3분기인지는 생성이 가린다. 검색이 후보를 다 대고
    선택을 생성에 넘기는 것이 이 경우의 올바른 분업이다.

    남은 칸은 **1차에서 몫을 못 쓴 셀이 아니라 순서대로** 돌린다. 1차가 이미 공평을
    보장했으므로 2차는 '남으면 더 담는다' 이고, 동점 셀이 그 칸을 가져간다.
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
    for limit in (cap, MAX_SECTION_EVIDENCE):      # 1차 공평 몫 · 2차 남은 칸
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
        # 부른 보고서를 **전부** 담았으면 그 집합이 답이다 — 더 넓히면 오염된다.
        # 정기공시 차선의 유일한 '충분성 -> 정지' 간선이다 (`_sufficiency` 는
        # `_gather_sections` 가 끝난 뒤 한 번 불려 사다리를 되돌릴 수 없다).
        trace.append(f"hop={plan.hop} stop=named_reports_covered "
                     f"(부른 보고서 {len(keys)}종을 모두 회수)")
        return taken, True
    return taken, False


def _gather_sections(r, question: str, budget: Budget,
                     trace: list[str], take) -> int:
    """정기공시 섹션 ReAct 루프. LLM 0회.

    exchange 루프와 **hop 사다리가 다르다**. 목차 경로를 먼저 정하고(§7.7 ③④⑤)
    보고서 종류·사업연도로 문서를 고른 뒤 BM25 가 섹션을 고른다. 실측 근거는
    docs/pipeline/04-retriever.md D-5 — 목차 축을 빼면 골든 회수가 0.929 -> 0.679 다.

    hop 0 은 **경로 후보 여러 개를 함께** 본다 (병목 B). 같은 지표가 서로 다른 목차에
    실리는 문항이 실재하기 때문이다 — Q-049 는 한 질문 안에서 두 기업이 '연결 매출액' 을
    `매출 및 수주상황` 과 `요약재무정보` 에 나눠 실었다. 다만 경로 하나가 기업 칸을 다
    먹으면 짝 경로를 찾아 놓고도 담지 못하므로 **(기업 × 경로) 셀마다 할당량**을 둔다.
    """
    paths = path_hints(question, tools.section_paths())
    plans = plan_section_hops(question, report_hints(question), paths,
                              _fiscal_years(r, question))
    if not ablation.current().hop_ladder:
        plans = plans[:1]                       # A1b 이하 — 재검색 사다리를 태우지 않는다
    corps = r.companies[:3] or [None]
    # 비교 질의는 한쪽만 채우면 답이 안 된다. 한 기업이 근거 칸을 다 먹지 않게 나눈다.
    per_corp = max(1, MAX_SECTION_EVIDENCE // len(corps))
    quota = {corp: per_corp for corp in corps}
    named = _report_cells(question)             # 질문이 이름을 부른 보고서들
    taken = 0
    for plan in plans:
        if plan.hop > 0:
            try:
                budget.spend_hop()
            except BudgetExceeded as exc:
                trace.append(f"hop={plan.hop} stop=hop_budget ({exc})")
                break
        # 제동 ①③ — 부른 보고서가 둘 이상이고 접수일 접두가 없는 hop 0 만 쪼갠다.
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
                # 한 셀에서 같은 섹션의 조각을 걸러내는 규칙은 **넣지 않았다.** 실측이
                # 말렸다 — (문서, 목차) 중복 제거는 골든 적중을 하나도 못 올리면서
                # hop 10 -> 12, 근거 1,079자 증가만 남겼다 (Q-054 한 문항의 변화가 전부다).
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
    """A0 — 메타 프리필터만. **BM25 를 한 번도 부르지 않는다.**

    §I-1 의 실측이 이 arm 의 근거다: `corp_name` + `rcept_dt` 접두만으로 후보가
    1,469 → 1건이 되고 골든이 1위가 된다. 그러니 검색의 바닥은 Vector RAG 가 아니라
    **여기**이고, 여기를 안 재면 가장 큰 기여 요소가 측정에서 통째로 빠진다.

    순위가 없다는 것이 이 arm 의 정의다. manifest 순서 그대로 앞에서부터 담는다 —
    점수를 매기는 순간 그건 A1(BM25) 이다. 정기공시도 같은 규칙이라 목차 선택 없이
    문서의 앞 섹션을 담는다 (실측상 거의 못 맞히는데, 그게 A1b 의 Δ 로 잡히는 값이다).

    `group` 은 **호출자가 결정한 것을 받는다.** 여기서 `r.group` 을 다시 읽으면 근거는
    `holding`/`major`/`periodic` 에서 오는데 요구 필드는 `exchange` 스키마로 검사되는
    상태가 된다 (`_gather_evidence` 의 `uses_group_index` 오버라이드가 이 함수를
    비껴갔다). 실측 A0 88문항 중 46문항이 비-exchange group 이었고, 예컨대 Q-016(holding)
    이 `reason=missing_field:비율` 을 냈다 — `비율` 은 exchange 의 `대비\\s*:` 이고
    holding 근거에는 `보유비율:` 이 항상 있다. **없는 것을 없다고 한 것이 아니라 있는
    것을 다른 이름으로 찾은 것**이고, 그 라벨을 `parse_hops` → Phase H 가 되읽는다.
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


#: 정기공시 `doc_subtype` → 사람이 읽는 보고서명. 정형 레코드는 `markdown._label` 이 이미
#: 같은 일을 하므로 여기 넣지 않는다 — 규칙을 두 곳에 적으면 한쪽만 낡는다.
_PERIODIC_LABEL = {"annual": "사업보고서", "half": "반기보고서", "quarter": "분기보고서"}


def _citation_source(record: dict) -> dict | None:
    """🔴 인용에 쓸 값을 레코드에서 **그대로** 꺼낸다 — 마크다운을 되읽지 않는다.

    `ablation.attach_citation` 은 근거 마크다운을 정규식으로 되읽어 인용을 만들 수도
    있다(되먹임). 그러나 그것은 **우리가 방금 렌더한 글자를 도로 파싱하는 것**이라
    이 저장소가 반복해 데인 *"같은 값을 두 곳에 적고 한쪽만 고친다"* 와 같은 모양이다.
    실제로 취약하다 — 머리글의 `–` 는 EN DASH 라 하이픈으로 적으면 하나도 안 걸린다.

    🔴 **정기공시에서 특히 다르다.** 되읽기는 섹션경로를 머리글 문자열에서 긁어내는데,
    청크 레코드에는 `path` 가 정확히 들어 있다 (`'II. 사업의 내용 > 2. 주요 제품'`).
    """
    rcept = str(record.get("rcept_no") or "")
    if not rcept:
        found = re.search(r"\d{14}", str(record.get("doc_id") or ""))
        rcept = found.group(0) if found else ""
    if not rcept:
        return None

    # 🔴 **검색이 넘기는 청크는 가난하다.** 실측: 정형 히트는 `{doc_id, score}` 뿐이고
    #    정기 히트는 `{chunk_id, doc_id, path, score}` 다. 그대로 쓰면 인용이
    #    `(근거: 공시, 접수번호 …)` 로 빈약해져 **되읽기보다 못해진다.** 그래서 여기서
    #    정본을 조회해 채운다 — 색인·레코드 조회라 LLM 0회다.
    # 🔴 **`doc_id` 접두로 가른다.** 처음에는 `path` 유무로 갈랐는데, 정기공시가 섹션
    #    검색이 아니라 매니페스트 경로(`_gather_meta`)로 들어오면 `path` 가 없어 정형으로
    #    빠지고 `_record_of` 도 `None` 이라 보고서명이 `공시` 로 떨어졌다 (실측 Q-105).
    #    §5 「근거 표시」는 **공시명**을 요구하는데 '공시' 는 공시명이 아니다.
    doc_id = str(record.get("doc_id") or "")
    if doc_id.startswith(PERIODIC_DOC_PREFIX) or record.get("path") is not None:
        source = _periodic_meta(doc_id)
        label = _PERIODIC_LABEL.get(source.get("doc_subtype") or "", "정기보고서")
        if source.get("base_year"):
            label = f'{label} {source["base_year"]}'
        # 청크가 `path` 를 들고 오면 그것을 쓰고, 없으면 청크 id 로 색인에서 찾은 것을 쓴다.
        tail = str(record.get("path") or source.get("path") or "")
        raw = str(source.get("rcept_dt") or "")
    else:                                                   # 정형 레코드
        source = record if record.get("doc_subtype") or record.get("title") else {}
        if not source:
            source = tools._record_of(rcept) or record
        label = markdown_module._label(source)
        tail = str(source.get("title") or "")
        raw = str(source.get("rcept_dt") or "")

    # 접수번호 앞 8자리가 접수일이다 (DART 규칙). 조회가 비어도 날짜는 잃지 않는다.
    if len(raw) != 8:
        raw = rcept[:8]
    date = f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}" if len(raw) == 8 and raw.isdigit() else ""
    return {"rcept_no": rcept, "label": label, "date": date, "tail": tail}


def _periodic_meta(doc_id: str) -> dict:
    """정기공시 청크 id 로 **문서 메타 + 그 섹션의 목차 경로**를 찾는다. LLM 0회.

    🔴 **`_take` 가 받는 것은 문서 id 가 아니라 청크 id 다** — 실측
    `periodic_20250814003695#109` · `periodic_20250321001834#119/p0`. 처음엔 그대로
    `_sections_of` 에 넘겨 빈 결과를 받았고, 그래서 보고서명이 `정기보고서` 로,
    섹션경로가 빈칸으로 떨어졌다 (실측 Q-105).

    문서 메타(종류·사업연도·접수일)는 섹션마다 같으므로 첫 행에서 읽고, **목차 경로만
    청크 id 로 골라낸다.**
    """
    base = doc_id.split("#", 1)[0]
    index = tools._section_index()
    rows = tools._sections_of(index, base) if index else []
    if not rows:
        return {}
    chunk = doc_id.split("/", 1)[0]                 # `#119/p0` → `#119`
    hit = (next((row for row in rows if row.get("chunk_id") == chunk), None)
           # 조각이 더 쪼개진 형태(`#119/p0`)는 정확히 안 맞을 수 있다. 접두로 한 번 더 본다.
           or next((row for row in rows
                    if str(row.get("chunk_id") or "").startswith(chunk)), None))
    return {**rows[0], "path": (hit or {}).get("path", "")}


def _gather_evidence(r, question: str = "", budget: Budget | None = None,
                     provenance: dict[str, dict] | None = None) -> tuple[str, list[str]]:
    """ReAct 반복 루프. 검색 → 충분성 판정 → 부족하면 질의 재구성 후 재검색. LLM 0회.

        검색 → 근거 충분? ─ 예 → 생성
                  └─ 아니오 → 질의 재구성 → 재검색 (hop ≤ 3, 총 스텝 ≤ 8)

    hop 0 은 최초 검색이라 예산을 쓰지 않고, 재검색 3회가 `Budget.spend_hop()` 을 쓴다
    (ReAct 논문: 정답 궤적 중 7스텝을 쓴 것은 0.84% — 깊이를 더 줘도 얻는 게 없다).
    도구 호출은 `Budget.spend_step()` 으로 총 8스텝을 지킨다 (주최 측 참조 아키텍처).
    상한을 넘기면 예외를 밖으로 던지지 않고 **그 시점 근거로 진행**한다.

    도구가 error 를 돌려주면 (예: periodic_sections.jsonl 이 없어 list_sections 실패)
    조용히 건너뛰고 다음 수단으로 넘어간다. 파이프라인은 죽지 않는다.
    """
    arm = ablation.current()
    budget = budget or Budget()
    trace: list[str] = []
    picked: list[dict] = []
    seen: set[str] = set()
    period = _period_args(r, question)
    corps = r.companies[:3] or [None]
    group = getattr(r, "group", "exchange")
    # A1 은 exchange 인덱스 1,469건만 본다. 라우터가 major·holding·periodic 으로
    # 갈라도 검색은 exchange 로 되돌린다 — 그것이 A1b(인덱스 커버리지) 의 Δ 다.
    #
    # 🔴 **`uses_bm25` 가 참인 arm 에만 건다.** `uses_group_index` 는 `retrieval == "all"`
    # 이라 A0(`meta`)에도 False 인데, A0 은 인덱스를 아예 열지 않고 manifest 만 본다 —
    # 되돌릴 '인덱스' 가 없다. 그런데도 이 줄이 걸려서 A0 은 근거를 holding/major/periodic
    # 매니페스트에서 가져오면서 요구 필드는 exchange 스키마로 검사했다(실측 46/88 문항이
    # 틀린 `missing_field` 라벨). 답변은 안 바뀐다 — A0 은 판정을 소비하지 않는다 —
    # 그러나 `parse_hops` 가 그 사유를 되읽어 Phase H 의 RETRIEVAL/INDEX_FIELD_MISSING
    # 귀속 입력으로 쓴다. 즉 **잰 것과 다른 것을 잰 것**이다.
    if arm.uses_bm25 and not arm.uses_group_index:
        group = "exchange"
    required = _required_fields(question, group)
    stalled = 0
    out_of_steps = False
    chain_done = False
    chain_gap: list[str] = []
    chain_ids: set = set()

    # 발주처 차선. `seen` 을 **공유하지 않는다** — 공유하면 발주처 레코드가 검색 상위와
    # 겹칠 때 검색 차선의 도착 순서가 밀리고, 그러면 체인 씨앗(상위 2건)이 바뀐다.
    # 차선을 완전히 나눠야 검색 경로가 종전과 비트 단위로 같다.
    cp_picked: list[dict] = []
    #: 🔴 A29c — 근거 **칸을 안 먹는** 한 줄들. 검색이 담지 못하고 버린 것을 남긴다.
    extra_lines: list[str] = []
    cp_seen: set[str] = set()
    #: 발주처 차선이 최종 근거에 실을 수 있는 칸. `A22` 가 추가 허브를 결속할 때만
    #: 허브당 `MAX_EXTRA_HUB_EVIDENCE` 씩 늘어난다 — `W=1` 에서는 종전 상수 그대로다.
    cp_cap = MAX_COUNTERPARTY_EVIDENCE

    def _remember(record: dict | None) -> None:
        """🔴 근거로 삼은 레코드를 기억한다 (`A25`). **모든 근거가 이 길목을 지난다** —
        섹션 근거도 `_gather_meta` 가 `_take` 를 통해 넣으므로 여기 한 곳이면 된다.

        절단(`MAX_EVIDENCE`·`_join_section_evidence`)에서 탈락한 것도 여기 남지만
        상관없다 — **살아남았는지는 근거 문자열에서 읽는다.** 기억은 '값' 만 준다.
        """
        if provenance is None or not record:
            return
        unit = _citation_source(record)
        if not unit:
            return
        # 🔴 **정기공시는 한 보고서에 섹션이 여럿**이고 접수번호는 하나다. 먼저 온 것만
        #    남기면 나머지 섹션이 인용에서 사라진다. 주최측 예시(`CONVENTION §7.5`)가
        #    바로 이 경우를 *"접수번호 20260515002181, II-3 원재료 및 생산설비 /
        #    II-6 연구개발활동"* 으로 적는다 — 한 접수번호 아래 섹션을 나열한다.
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
        """검색 차선 먼저, 발주처 차선은 그 뒤. 검색 칸(MAX_EVIDENCE)은 그대로 남는다.

        중복 제거는 **절단 후 집합**과 대조한다. `seen` 은 검색 차선이 담은 전부이므로
        그것과 대조하면 `picked` 의 11번째 이후와 겹치는 cp 레코드가 **양쪽에서 다 빠진다** —
        그런데 `_gather_counterparty` 의 trace 는 그 doc_id 를 `ids=` 에 적고 `n=` 에 세므로
        Phase H 는 '근거에 실렸다' 로 읽는다. 근거 소실과 자기 성적표 부풀림이 동시에 난다
        (`_chain_note`·`_counterparty_note` 가 피하려고 애쓴 사고의 다른 입구다).
        기록된 밸류체인 8건에서는 미발화다(중복으로 빠진 cp 레코드가 전부 상위 10 안에
        있었다). `picked` 가 10을 넘는 문항(Q-113 실측 14블록)에서 발화한다.

        🔴 `cp_cap` 은 `A22` 가 **결속한 추가 허브 수만큼만** 늘어난다. 여기 상한을
        `cp_picked` 전량으로 열면 허브가 셋일 때 발주처 근거가 18블록까지 부풀어
        검색 근거를 프롬프트 뒤로 밀어낸다. `W=1` arm 에서는 종전 값 그대로다.
        """
        head = picked[:MAX_EVIDENCE]
        kept = {p["doc_id"] for p in head}
        extra = [p for p in cp_picked if p["doc_id"] not in kept]
        return "\n\n".join(p["markdown"] for p in head + extra[:cp_cap])

    if not arm.uses_bm25:                      # A0 — 검색 없이 메타 프리필터만
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

    # 정기공시는 검색 축이 다르다 — 접수일이 아니라 사업연도, 그리고 목차 경로.
    # 같은 사다리를 태우면 골든이 필터에서 통째로 빠진다 (04-retriever D-5).
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
        # 섹션에서 아무것도 못 찾으면 정형 레코드로 내려간다 (거래소공시에도 실려 있을 수 있다).
        group = "exchange"

    cells = time_points(question, r.years) if arm.split_query else []
    plans = plan_hops(question, period, fallback_terms=r.companies, periods=cells)
    if not arm.hop_ladder:
        plans = plans[:1]                       # A1b 이하 — 최초 검색 한 번으로 끝낸다
    for plan in plans:
        # 기업이 특정된 질의에서 이미 근거가 있으면 기업 필터를 풀지 않는다.
        # 다른 회사 공시가 섞이면 요약·비교가 통째로 오염된다 — 넓히는 것이 늘 이득은 아니다.
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
                    # 필터가 하나도 없는 검색은 점수만이 관련성의 보증이다.
                    # 잡음 바닥(MIN_TOP_SCORE) 밑을 담으면 무관한 회사 공시 10건이 근거가 된다.
                    hits = [h for h in hits
                            if isinstance(h.get("score"), (int, float))
                            and h["score"] >= MIN_TOP_SCORE]
                trace.append(_log_search(plan, corp, hits, cell))
                taken_here = 0
                for chunk in hits:
                    if taken_here >= quota:            # 제동 ② — 셀별 할당량
                        break
                    if _take(chunk.get("doc_id"), chunk.get("markdown"),
                             chunk.get("score"), corp, record=chunk):
                        taken_here += 1

        # 집합 완결성. 후보가 대체된 문서면 체인의 나머지가 와야 근거가 완결된다.
        # **판정 전에** 한 번만 밟는다 — 판정 뒤에 두면 hop 0 이 `sufficient` 를 내고
        # 멈춰서 정정본이 영영 안 온다 (Phase H 실패 7건의 정확한 기전).
        if arm.chain_hop and not chain_done and picked and not out_of_steps:
            chain_done = True
            chain_gap, chain_ids = _expand_chain(picked, seen, budget, trace, _take,
                                                 _as_of_ym(question), question)

        ok, reasons = _sufficiency(r, picked, required, chain_gap)
        trace.append(_log_verdict(plan.hop, ok, len(picked), reasons))
        if ok or out_of_steps:
            break
        if chain_gap and all(x.startswith("chain_open:") for x in reasons):
            # 못 닫은 링크는 재검색으로 안 닫힌다 (도구 오류이거나 스텝이 마른 것이다).
            # 남은 hop 을 태우지 않는다.
            trace.append(f"hop={CHAIN_HOP} stop=chain_unresolved "
                         f"(체인 링크 {len(chain_gap)}건 미해소 — 재검색 대상이 아니다)")
            break
        # 근거가 하나도 없을 때는 수렴이 아니라 못 찾은 것이다 — 계속 넓힌다.
        stalled = stalled + 1 if picked and len(picked) == before else 0
        if stalled >= MAX_STALLED_HOPS:
            trace.append(f"hop={plan.hop} stop=stalled "
                         f"({MAX_STALLED_HOPS}회 연속 새 후보 없음)")
            break

    # 마지막 수단: 검색이 아무것도 못 찾으면 정형 이벤트 열거로 보완.
    # 연도가 둘 이상이면 query_events 에 기간 조건을 걸 수 없으므로 범위 밖 질의는 여기서도 막는다.
    # `query_events` 모집단은 거래소공시다 (§7.7 ②) — 다른 문서군 질의에 붙이면 오답 근거다.
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

    # 열거 hop. 위 폴백과 **배타**다 (저쪽은 `not picked`, 이쪽은 `picked`) — 같은 hop
    # 번호를 쓰므로 배타여야 `parse_hops` 가 두 종류를 한 레코드로 섞어 읽지 않는다.
    # 사다리가 넓히기만 하고 열거하지 않아 `Q-117` 이 둘째 갈래를 지어냈다 (제동은 위 별항).
    elif (arm.hop_ladder and picked and len(picked) < MAX_EVIDENCE
            and not _out_of_corpus_period(r, question)
            and _wants_enumeration(r, question, group)):
        _enumerate_events(r, question, budget, trace, _take, extra_lines)

    # 발주처 hop. 사다리 **밖의 스텝 1개**이고, 검색이 예산을 다 쓴 뒤에 남는 것으로만
    # 움직인다 (제동 ④). 순서를 뒤로 미뤄도 담기는 레코드는 같다 — 그래프 조회라
    # 검색 상태에 의존하지 않는다.
    #
    # 🔴 `A22`(`multi_hub`)만 허브를 둘 이상 결속한다. 플래그가 꺼져 있으면 아래는
    # `hubs = [r.counterparty]` 한 칸이라 종전과 호출도 인자도 같다.
    if arm.counterparty_hop:
        primary = getattr(r, "counterparty", None)
        hubs = [primary] if primary else []
        if arm.multi_hub and len(hubs) < MAX_COUNTERPARTY_HUBS:
            room = MAX_COUNTERPARTY_HUBS - len(hubs)
            extra_hubs = _evidence_hubs(picked, primary, r.companies)[:room]
            hubs += extra_hubs
            cp_cap += MAX_EXTRA_HUB_EVIDENCE * len(extra_hubs)
        for index, hub in enumerate(hubs):
            # 첫 허브가 질문이 지목한 것이면 종전 상한·순서를 그대로 쓴다. 근거에서
            # 나온 허브는 칸이 둘이고, 질문이 부른 기업을 파트너 앞줄에 세운다.
            from_question = index == 0 and hub == primary
            # 🔴 **꺼진 arm 에는 인자를 넘기지 않는다.** `roster_out=None` 을 넘기기만
            #    해도 `test_w1_arms_call_the_counterparty_hop_with_the_old_arguments`
            #    가 걸린다 — 그 가드가 지키는 것은 "기본 인자가 움직이면 v1~v4 측정이
            #    무효" 라는 사실이고, 그 말이 맞다.
            extra = ({"roster_out": extra_lines}
                     if ablation.enabled("roster_line") and from_question else {})
            _gather_counterparty(
                hub, question, budget, trace, _take_cp,
                limit=MAX_COUNTERPARTY_EVIDENCE if from_question else MAX_EXTRA_HUB_EVIDENCE,
                prefer=() if from_question else tuple(r.companies or ()), **extra)

    # 근거 절단은 체인을 먼저 살린다. 한화오션 잠수함은 검색 5건 + 체인 6건 = 11건이라
    # 도착 순서대로 자르면 **체인의 마지막 정정본**(=정답)이 잘려 나간다.
    if chain_ids:
        picked.sort(key=lambda p: 0 if p["doc_id"] in chain_ids else 1)

    # 🔴 A29c — 검색이 **담지 못하고 버린 것**을 한 줄로 남긴다. 근거 칸을 안 먹으므로
    #    `A16`(칸 확대)·열거 depth 확대가 기각된 사유에 걸리지 않는다. 머리에 둔다 —
    #    꼬리에 두면 안 읽힌다는 것을 `Q-023` 이 실측으로 보여 줬다(`_prepend`).
    body = _joined()
    if extra_lines and body:
        body = "\n\n".join(extra_lines) + "\n\n" + body
    return body, trace


# --- ORACLE — 골든 근거 주입 (🔴 진단 전용 · 제출 구성이 될 수 없다) --------------
# `A0`~`A8` 은 전부 "검색 + 생성" 의 합성 성능이라 **근거를 고정한 생성 능력을 잰 적이
# 없다.** 이 경로는 검색 차선 전체를 골든 근거로 갈아치워 그 축을 하나 만든다.
#
# 🔴 **R2E 재측정이 R1 의 계약을 깼다 (2026-08-14).** 종전 두 줄
# (`Δ = 검색이 아직 못 가져온 몫` · `잔여 오답 = 순수 생성 실패`)을 **그대로 인용하면 안 된다.**
# 주입은 근거를 **더하지 않고 교체**하므로 근거가 줄어든다(전체 88 에서 −47%). 정오가 갈린
# 7문항 전수에서 골든 회수가 `A8` 과 **동일**했고 오라클 근거만 더 작았다.
#
#     이 경로가 재는 것    "근거를 골든만으로 한정했을 때 답이 서는가"
#     Δ(A8 → ORACLE)     🔴 검색 부족분 + 보조근거 제거 손실의 합. 한 항으로 못 읽는다
#     잔여 오답 = 생성 실패 🔴 근거가 A8 과 바이트 동일한 문항에서만 정의상 성립
#
# 살아 있는 판정: **골든을 전건 주입해도 정답이 안 늘면 남은 실패는 검색으로 살 수 없다.**
# 근거는 `src/argus/ablation.py` 머리말 §ORACLE · `09-ablation.md` §I-5f ④.
#
# 🔴 **평가 시점에는 골든이 없다.** 이 경로로 나온 정답률은 성적이 아니라 진단값이다.
#
# 🔴 **형식·순서·절단 규칙을 검색 경로와 같게 맞춘다.** 다르면 Δ 에 '형식 효과' 가 섞여
# 검색 몫과 구별되지 않는다. 그래서 정형 레코드는 `record["markdown"]` 을 `MAX_EVIDENCE`
# 까지, 정기공시는 `tools._section_markdown` 블록을 `_join_section_evidence` 로 —
# 둘 다 검색 경로가 쓰는 **같은 함수·같은 상한**이다.

#: 정기공시 문서 식별자 접두. 골든은 접수번호만 주므로 여기서 만든다
#: (`filter_docs(doc_group="periodic")` 의 `doc_id` 와 같은 형태다).
PERIODIC_DOC_PREFIX = "periodic_"

#: 골든 목차 하나에서 볼 조각 후보 수. 상한은 `MAX_SECTION_EVIDENCE` 가 다시 조인다.
#: 넉넉히 잡는 이유는 골든 `section` 이 **대목 전체**를 가리키는 경우가 있어서다 —
#: 실측 Q-088 의 `III. 재무에 관한 사항 > 3. 연결재무제표 주석` 은 조각 57개 · 231,624자다.
ORACLE_SECTION_POOL = 8

#: `parse_hops` 가 되읽을 hop 번호. 검색 hop 과 겹치지 않게 발주처 hop 다음에 둔다 —
#: 같은 번호를 쓰면 Phase H 가 두 종류의 후보를 한 레코드로 합쳐 읽는다.
ORACLE_HOP = COUNTERPARTY_HOP + 1


def _oracle_section_blocks(rcept_no: str, section: str, question: str, cap: int,
                           budget: Budget, trace: list[str]) -> list[dict]:
    """골든이 지목한 (문서, 목차) 안에서 근거 조각을 고른다. LLM 0회.

    🔴 **오라클의 한계가 여기 있다.** 골든 `section` 은 목차 '대목' 이라 조각 수십 개로
    풀리는데 프롬프트 예산(`MAX_SECTION_CHARS` 12,000자)은 4조각쯤만 받는다. 그래서
    문서 순서대로 앞에서 자르면 오라클이 아니라 '검색 없음' 이 된다 (Q-088 은 앞 4조각이
    일반사항·회계정책이고 골든 표는 뒤에 있다). **문서와 목차는 골든으로 못박고, 그 안의
    조각 선택만 기존 랭커에 맡긴다** — 즉 이 arm 이 완벽하게 고정하는 것은 *문서·대목*
    이지 *문단* 이 아니다. 보고서가 이 사실을 함께 적어야 Δ 를 옳게 읽는다.

    BM25 가 한 조각도 못 고르면(질의어가 하나도 안 겹치면) 문서 순서로 되돌린다 —
    빈 근거로 내려가면 그 문항이 '생성 실패' 로 오독된다.
    """
    doc_id = f"{PERIODIC_DOC_PREFIX}{rcept_no}"
    index = tools._section_index()
    wanted = bm25_module.norm_path(section)
    rows = [r for r in (tools._sections_of(index, doc_id) if index else [])
            if bm25_module.norm_path(r["path"]) == wanted
            or bm25_module.norm_path(r["path"]).startswith(wanted)]
    rows.sort(key=lambda r: (r["chunk_id"].split("/")[0], r["part"]))
    # 기업 축은 인덱스 레코드에서 되찾는다. `search_chunks` 는 `corp_name` 을 안 돌려주는데,
    # `_join_section_evidence` 가 그 축으로 라운드로빈한다 — None 으로 두면 비교 질의에서
    # 한 기업이 근거 칸을 다 먹는다 (`_cover_order` 주석의 Q-049 사고와 같은 기전).
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
        # 질의어가 한 조각과도 안 겹쳤다 (BM25 는 점수 0 을 후보에서 뺀다). 문서 순서로
        # 되돌린다 — `read_section` 과 같은 정렬이다 (조각 번호 → part).
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
    """골든 근거만으로 프롬프트 근거를 만든다. 🔴 진단 전용 (`ablation.ORACLE`).

    주입이 없으면 `ablation.oracle_golden_for` 가 **예외를 던진다** — 조용히 빈 근거로
    내려가면 그것이 "오라클을 줘도 생성이 다 틀렸다" 로 굳는다.
    """
    golden = ablation.oracle_golden_for(question_id)
    trace: list[str] = [
        f"oracle=golden n_golden={len(golden)} "
        "🔴 진단 전용 — 검색을 골든으로 대체했다 (제출 구성이 될 수 없다)"]

    # 🔴 골든 근거마다 **공평 몫**을 준다. `_gather_sections` 가 (기업 × 경로) 셀에 할당량을
    #    두는 것과 같은 이유다 — 안 두면 조각이 많은 골든 하나가 근거 칸 4개를 다 먹고
    #    나머지 골든이 통째로 빠진다 (실측 Q-054·Q-088·Q-094 — 대목 전체를 가리키는
    #    `연결재무제표 주석` 이 같은 문항의 `정정 신고` 를 밀어냈다). `_cover_order` 는
    #    기업 축으로만 나누므로 **같은 기업의 골든 둘**은 그것만으로 안 갈린다.
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
    # 🔴 `parse_hops` 가 되읽는 줄. 골든 문서를 `ids=` 에 적어야 Phase H 의 실패 귀속이
    #    이 arm 에서도 성립한다 — 안 적으면 골든이 '어떤 hop 후보에도 없다' 가 되어
    #    전건이 RETRIEVAL 로 귀속되고, 그것이 이 측정의 결론을 통째로 뒤집는다.
    corps = sorted({p["corp"] for p in picked if p.get("corp")})
    trace.append(
        f'hop={ORACLE_HOP} strat=oracle tool=golden_evidence '
        f'corp="{",".join(corps) or "-"}" period=none '
        + ("group=periodic " if sections else "")
        + f'n={len(picked)} top_score=- '
        f'ids={",".join(str(p["doc_id"]) for p in picked) or "-"}')
    if missing:
        # 골든이 코퍼스에서 안 열리는 경우. **삼키지 않는다** — 근거가 준 것보다 적다.
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


# --- A10 — 자기일관성 `k=3` (🔴 온도 통제 변수를 깨는 유일한 arm) ------------------
# 표본을 뽑고 **다수결**한다. 논문(Self-Consistency, 2023)의 전제는 *"여러 갈래로 추론해도
# 옳은 답으로 수렴한다"* 이고, 그 전제가 **우리 코퍼스에서 성립하는가**가 이 arm 의 측정
# 대상이다. 채택 여부와 무관하게 그 판정이 산출물이다.
#
# 🔴 **두 함정을 먼저 닫는다 — 둘 다 Δ 를 조용히 0 으로 만든다.**
#   ① 요청 캐시    프롬프트도 온도도 같아 k 개 표본의 **캐시 키가 하나**다. 2회차부터
#                 `clova.no_cache()` 로 우회하지 않으면 표본이 전부 같아진다
#   ② 온도 0.0     `Arm.__post_init__` 이 `sc_k>1 && temperature==0` 을 예외로 막는다


def _sc_key(answer: str) -> str:
    """표본을 묶는 키 — 자기일관성의 '최종 답' 에 해당하는 것.

    우리 답변에서 그것은 **금액과 비율**이다: 닫힌 문항의 채점 축이
    `em:number_won`(원 단위 완전일치)이고, 열린 문항의 judge 도 수치 일치를 먼저 본다.
    🔴 **정규식을 새로 쓰지 않는다** — `critic._money_spans` 와 `critic._PCT_RE` 가
    이 저장소의 단일 원천이고, 여기 사본을 두면 한쪽만 고쳐진다(이미 다섯 번 겪은 실패).

    🔴 **수치가 없는 답변은 다수결이 성립하지 않는다.** 그 경우 공백만 정규화한 원문을
    키로 쓰는데, 온도 0.7 에서 서술문이 글자까지 같을 확률은 낮다 — 즉 표본이 전부
    다른 키가 되고 **첫 표본이 선택된다.** 이것은 결함이 아니라 이 조작의 한계이고,
    트레이스의 `keys=` 가 그 사실을 문항마다 남긴다.
    """
    from argus.agent import critic

    text = answer or ""
    values = [f"{value:.10g}" for _, _, value, _, _ in critic._money_spans(text)]
    values += [f'{m.group(1).replace(",", "")}%' for m in critic._PCT_RE.finditer(text)]
    return "|".join(values) if values else " ".join(text.split())


def _self_consistent(messages: list[dict], max_tokens: int, arm, first: str,
                     budget: Budget, trace: list[str]) -> str:
    """표본 `k` 개 중 다수결로 고른 답변. 첫 표본은 이미 뽑혀 있으므로 `k-1` 번 더 부른다.

    🔴 **예산이 끊기거나 상류가 죽으면 그때까지의 표본으로 다수결한다.** 여기서
    `BudgetExceeded` 를 위로 던지면 호출부가 `UNANSWERABLE` 로 빠져 **이미 받아 둔
    정답을 버린다** — 토큰 상한 20,000 은 긴 근거 문항에서 3회차에 실제로 물린다
    (실측: 프롬프트 6,800 토큰 문항이 3회차에서 20,400).

    🔴 동수면 **먼저 나온 키**가 이긴다. 그래야 `k` 를 늘려도 1회차가 기준선으로 남는다.
    """
    samples = [first]
    for index in range(1, arm.sc_k):
        try:
            # 🔴 캐시 우회는 **2회차부터**다. 1회차를 캐시에 남겨야 재실행이 그만큼 싸고,
            #    `--offline` 재집계가 최소한 그 한 벌은 되읽을 수 있다.
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


# --- A24 — `chat(tools=…)` 왕복 (🔴 게이트 F 의 유일한 미완이었다) -----------------
# `TOOL_SPECS` 15종은 v1 부터 등록돼 있었는데 **`tools=` 가 `chat()` 에 한 번도 전달된
# 적이 없다** — `ablation.py` 가 그 사실을 스스로 문서화해 뒀고 게이트 F 가 `[~]` 로
# 남아 있었다. 여기가 그 미완을 닫는 자리다.
#
# 🔴 **왕복이 필수다.** 모델이 `toolCalls` 를 돌려주면 그 응답의 `content` 는 보통
# 비어 있다. 실행 결과를 다시 넣어 한 번 더 부르지 않으면 **빈 답변이 나가고**, 우리는
# 그 0점을 "function calling 은 효과가 없다" 로 읽는다 — 이 저장소가 최악으로 보는
# **조용한 Δ** 의 정확히 같은 종류다.
#
# 🔴 **`tools` 와 `maxTokens` 는 동시 지정 불가다** (40001 · `docs/CONVENTION.md`).
# 그래서 도구를 내미는 회차에는 길이 상한이 **없고**, 마지막 회차는 도구를 빼서 상한을
# 되돌린다. 이것은 우리 선택이 아니라 API 제약이고, `A24` 의 Δ 에 그 몫이 섞인다.

#: 도구를 내미는 회차의 상한. 마지막에 **도구 없는** 회차가 하나 더 붙으므로 생성
#: 호출은 최대 `MAX_TOOL_ROUNDS + 1` = **2회**다.
#:
#: 🔴 **루프를 열어 두지 않는다** — `Budget` 의 호출 상한 6 과 토큰 상한 20,000 을 답변
#: 단계 혼자 태우면 critic·검증이 굶고, 그것이 arm 효과로 오독된다 (이 파일 머리말:
#: 고정 횟수 단계라 질문당 비용 상한이 계산된다).
#:
#: 🔴 **1 로 두면 도구 연쇄(`calc_total` → `calc_share`)는 못 한다.** 그것이 이 값의
#: 유일한 대가이고, 대신 얻는 것이 둘이다: ① 마지막 회차에 도구를 빼므로 `maxTokens`
#: 가 돌아와 **답변 길이 상한이 `A8` 과 같아진다**(40001 제약: `tools` 와 `maxTokens`
#: 동시 지정 불가) ② 문항당 생성 호출이 정확히 1~2회로 못 박힌다.
MAX_TOOL_ROUNDS = 1

#: 도구 결과 한 건을 프롬프트에 실을 때의 글자 상한. `read_section`·`search_chunks` 는
#: 수만 자를 돌려줄 수 있어 상한이 없으면 **토큰 상한 20,000 을 한 번에 태운다** —
#: 그러면 `BudgetExceeded` 로 `UNANSWERABLE` 이 나가고 그것이 측정으로 굳는다.
MAX_TOOL_RESULT_CHARS = 2_000


def _tool_arguments(call: dict) -> dict:
    """`toolCalls[i].function.arguments` 를 사전으로. HCX-005 는 **사전**을 돌려주지만
    (실측 `notebooks/clova_verify.ipynb`), OpenAI 호환 서버는 JSON 문자열을 준다.
    양쪽을 다 받는다 — 형태 하나를 가정하면 도구가 조용히 안 불린다."""
    args = (call.get("function") or {}).get("arguments")
    if isinstance(args, str):
        try:
            args = json.loads(args or "{}")
        except json.JSONDecodeError:
            return {}
    return args if isinstance(args, dict) else {}


# --- A24b — 🔴 도구 결과를 모델이 옮겨 적게 하지 않는다 ---------------------------
# `A24` 는 **도구를 안 불러서** 진 것이 아니다 (37/88 이 불렀다). 진 이유 셋 중 첫째가
# 이것이다 — `calc_total` 이 골든과 바이트까지 같은 `2,365,868` 을 돌려줬는데 답변은
# `2,366,968` 을 썼다. **우리 도메인의 병목은 산술이 아니라 전사(轉寫)** 이고,
# function calling 은 그 축을 건드리지 않는다.
#
# 🔴 처방은 `A20` 이 이미 실측으로 이겨 둔 것과 같다 — **값을 코드가 문자열에 박는다.**
# 규율도 코드도 `critic` 것을 그대로 쓴다 (`critic.tool_result_reports` 머리말).


#: 🔴 **도구가 실제로 계산했다고 보는 최소 인자 수.** 이 아래면 도구는 계산한 것이
#: 아니라 **모델이 타이핑한 수를 그대로 되돌려준 것**이고, 그 값은 독립된 근거가 아니다.
#:
#: 🔴 **실측이 이 상수를 만들었다** (`A24b` 1차 · `Q-047`). 모델이 근거의
#: `97,146,675백만원` 을 **도구 인자에** `97,146,765` 로 잘못 옮겨 적고
#: `calc_mean({"values": [97146765]})` 를 불렀다. 평균의 인자가 하나이므로 도구는
#: 그 수를 그대로 돌려줬고, 치환이 **답변의 옳은 `97,146,675` 를 그 오타로 덮었다** —
#: 🔴 **정답이 오답이 됐다.**
#:
#: 즉 전사 오류는 사라지지 않고 **답변에서 도구 인자로 자리를 옮길 뿐**이다. 도구
#: 결과가 답변보다 믿을 만한 것은 **도구가 무언가를 계산했을 때뿐**이다.
MIN_TOOL_OPERANDS = 2


def _tool_operands(args: dict) -> list[float]:
    """도구 인자에 실린 수치들. 목록 인자(`values`)는 펼친다."""
    out: list[float] = []
    for value in (args or {}).values():
        for item in (value if isinstance(value, (list, tuple)) else [value]):
            if isinstance(item, bool) or not isinstance(item, (int, float)):
                continue
            out.append(float(item))
    return out


def _numeric_tool_results(results: list[tuple[str, dict, dict]]) -> list[tuple[str, float]]:
    """치환에 쓸 수 있는 `(도구, 값)` 만. 문턱이 셋이다.

    ① **계산 도구의 수치 `value`** 뿐이다. `tools._num_result` 를 쓰는 5종
       (`calc_pct_change`·`calc_share`·`calc_total`·`calc_mean`·`calc_cagr`)만 `value`
       가 수치다. `calc_compare` 는 `"a"`/`"b"`/`"tie"`, `calc_period` 는 `"8년 5개월"`
       이고 검색 도구에는 `value` 키가 아예 없다.
       🔴 **`CALC_TOOLS` 로 한 번 더 거른다** — 검색 도구가 언젠가 `value` 키를 갖게
       되면(자유형 반환이다) 그 값이 조용히 답변에 박힌다.
    ② 🔴 **수치 인자가 `MIN_TOOL_OPERANDS` 미만이면 계산이 아니다** (실측 `Q-047`).
    ③ 🔴 **결과가 인자 중 하나와 같으면 계산한 값이 아니라 통과시킨 값이다.**
       `calc_total({"values": [x, 0]})` 같은 자리다. ②만으로는 안 막힌다.
       대가를 안다 — `calc_pct_change(100, 200) = 100%` 처럼 **정당한 계산이 우연히
       인자와 같은** 경우도 함께 막는다. 답변을 고쳐 쓰는 조작이라 **확신할 수 없으면
       손대지 않는 쪽**을 기본값으로 둔다.
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
    """도구가 돌려준 값으로 답변의 그 수치를 갈아 끼운다. LLM 0회.

    🔴 **발화하지 않았을 때도 trace 에 적는다.** 안 적으면 "치환이 몇 번 설 수 있었는데
    몇 번 섰는가" 를 사후에 못 세고, 그러면 Δ 를 무엇에 귀속할지가 흐려진다.
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
    """`A24`/`A24b` — 도구를 내밀고, 모델이 부르면 실행해 결과를 되먹여 다시 부른다.

    🔴 `substitute_tool_results`(A24b)가 켜져 있으면 **마지막에 도구 결과로 답변의
    수치를 치환한다.** 꺼져 있으면(`A24`) 아래 한 줄은 종전과 글자 하나 다르지 않다.
    """
    results: list[tuple[str, dict, dict]] = []
    answer = _tool_round_trip(messages, max_tokens, arm, budget, trace, results)
    if arm.substitute_tool_results:
        answer = _substitute_tool_results(answer, results, trace)
    return answer


def _tool_round_trip(messages: list[dict], max_tokens: int, arm, budget: Budget,
                     trace: list[str], results: list[tuple[str, dict, dict]]) -> str:
    """왕복 본체. 실행한 `(도구, 인자, 결과)` 를 `results` 에 **순서대로 쌓는다**.

    🔴 **인자도 함께 쌓는다** — `A24b` 가 *"도구가 실제로 계산했는가"* 를 인자 수로
    판정한다 (`MIN_TOOL_OPERANDS` · 실측 `Q-047`).

    🔴 **첫 호출이 죽으면 그대로 올려보낸다** — `A8` 과 같은 경로(`UNANSWERABLE` +
    표식)로 나가야 상류 장애와 arm 효과가 섞이지 않는다. 두 번째 회차부터는 이미 받아
    둔 본문이 있으면 그것을 지키고 멈춘다 (`_self_consistent` 와 같은 규약).
    """
    # 🔴 `A24b` 는 계산 7종만 내민다 — `A24` 실측에서 모델이 부른 47건 중 26건이
    #    검색이었고 이미 실린 근거를 다시 읽어 희석시켰다 (`tools.CALC_SPECS` 주석).
    offered = tools.CALC_SPECS if arm.calc_tools_only else tools.TOOL_SPECS
    convo = list(messages)
    best = ""
    for index in range(MAX_TOOL_ROUNDS + 1):
        offer = index < MAX_TOOL_ROUNDS
        if index and budget.remaining()["llm_calls"] <= 0:
            # 🔴 **부르기 전에** 본다. 부르고 나서 세면 크레딧은 이미 나갔다.
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

        # 🔴 어시스턴트 회차를 **그대로** 되넣는다. `toolCalls` 를 빼면 뒤따르는
        #    `role="tool"` 메시지가 붙을 자리를 잃는다.
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
    """이 문항에 도구를 열 것인가. **`function_calling` 이 꺼져 있으면 무조건 아니다.**

    🔴 `calc_queries_only`(A24b)가 켜져 있으면 **계산 질의에만** 연다 — `tools` 와
    `maxTokens` 가 배타(40001)라 도구를 내미는 순간 길이 상한이 사라지고, `A24` 는
    도구를 한 번도 안 부른 `Q-106` 까지 그 이유로 잃었다 (`router.needs_calculation`).

    🔴 **판정을 trace 에 남긴다.** 안 남기면 어느 문항이 열렸는지 사후에 셀 수 없다.
    플래그가 꺼진 arm 의 trace 는 **바이트 동일**이다.
    """
    if not arm.function_calling:
        return False
    if not arm.calc_queries_only:
        return True
    kind = router_module.calc_question_kind(question)
    trace.append(f'fc gate={"open:" + kind if kind else "closed"}')
    return bool(kind)


#: critic 의 불리언 -> 사람이 읽을 사유. 🔴 **모델의 산문을 우리 확인서에 옮겨 적지
#: 않는다.** 2026-08-17 감사 32문항 실측에서 각주 8건 중 **6건이 망가져 있었다**:
#:
#:     AUD-11   (주의: 근거 검증을 통과하지 못했습니다 — 모든 투자 금액은 제공된
#:               근거에서 확인할 수 있으며, 이를 합산한 결과도 정확합니다.)
#:                        ↑ 머리는 "실패", 사유는 "문제 없음" — 서로 어긋난다
#:     AUD2-06  (주의: 근거 검증을 통과하지 못했습니다 — )
#:                        ↑ 사유가 비었다
#:
#: 원인은 critic 이 불리언과 산문을 **따로** 낸다는 것이다. 우리가 판정은 불리언으로
#: 하고 설명은 산문으로 실으니, 둘이 어긋나면 우리 각주가 자기를 반박한다. 각주는
#: **검사기가 무엇을 봤는가의 기록**이라(`aggregate._prune_notes` 와 같은 사유) 기록이
#: 자기와 싸우면 안 읽느니만 못하다.
#:
#: v4·v5 의 발견을 각주에 그대로 적용한 것이다 — *"모델이 손에 쥐면 틀린다."* 값은
#: 우리가 계산하고, 사유도 우리가 짓는다. `verdict.reason` 은 `trace` 에 그대로 남으니
#: 진단 정보가 사라지지도 않는다.
_VERDICT_REASONS = (
    ("has_citation", False, "근거 표기가 없습니다"),
    ("supported", False, "답변의 일부 수치·사실이 근거에서 확인되지 않았습니다"),
    ("hallucination", True, "근거에 없는 내용이 사실처럼 서술되었습니다"),
)


def _verdict_reason(verdict) -> str:
    """`Verdict` 의 불리언에서 사유를 짓는다. **절대 비지 않는다.**

    `passed` 가 거짓이면 위 셋 중 최소 하나가 걸리므로 빈 문자열이 나올 수 없다.
    그래도 마지막 갈래를 둔 것은, 나중에 `passed` 의 정의가 바뀌어도 각주가
    `— )` 로 끝나지 않게 하기 위해서다.
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
        # 🔴 A31 — 기업도 기간도 범위 안이고, 없는 것은 **공시에 안 적힌 미래**다.
        if r.reason == "speculation":
            trace.append("미래 추정 요구 — LLM 호출 없이 한계 고지")
            return _respond(question_id, question, "", trace, SPECULATIVE, budget)
        # 🔴 A32 — 범위 밖이 아니라 **내주지 않는** 것이다.
        if r.reason == "identifier":
            trace.append("식별번호 전사 요구 — LLM 호출 없이 거절")
            return _respond(question_id, question, "", trace, IDENTIFIER_WITHHELD, budget)
        # 🔴 A26 — 범위 밖이 아니라 **무엇을 묻는지 모르는** 것이다. 역질문으로 되묻는다.
        if r.reason == "ambiguous_reference":
            trace.append("지시대상 불명 — LLM 호출 없이 역질문")
            return _respond(question_id, question, "", trace, AMBIGUOUS_REFERENCE, budget)
        trace.append("범위 밖 — LLM 호출 없이 한계 고지")
        return _respond(question_id, question, "", trace, UNANSWERABLE, budget)

    # 🔴 ORACLE 은 여기서 갈린다 — `_gather_evidence` 안에는 `question_id` 가 없다.
    #    플래그가 꺼져 있으면(기본값) 아래 한 줄은 종전과 글자 하나 다르지 않다.
    # 🔴 두 가지 밖에서 만든다 — `ORACLE` 가지에서는 안 채워지므로 `attach_citation` 이
    #    빈 사전을 받고 근거 되읽기로 되돌아간다. 진단용 arm 하나 때문에 `NameError` 를
    #    내면 안 된다.
    provenance: dict[str, dict] = {}
    if ablation.current().golden_oracle:
        evidence, tool_trace = _gather_golden(question_id, clean, budget)
    else:
        evidence, tool_trace = _gather_evidence(r, clean, budget, provenance)
    trace.extend(tool_trace)
    # 근거 성형은 **검색이 끝난 뒤**다. 여기 위로 올리면 A5·L2 의 Δ 에 검색 변화가
    # 섞여 단일 변수 조작이 아니게 된다 (`ablation.shape_evidence` 주석).
    evidence = ablation.shape_evidence(evidence)

    if not evidence:
        trace.append("근거 없음 — LLM 호출 없이 한계 고지")
        return _respond(question_id, question, "", trace, UNANSWERABLE, budget)

    # 🔴 A29a — 집계를 우리가 계산해 **한 줄**로 싣는다. 근거 칸을 안 먹으므로
    #    `A16`(칸 확대)·`A17`(블록 확대)이 기각된 사유에 걸리지 않는다.
    #    배선 자체를 플래그 뒤에 둔다 — 무조건 부르면 플래그를 끈 arm 의 근거도
    #    바뀔 수 있고, 그것이 `A20` 에서 이미 겪은 사고다.
    if ablation.enabled("aggregate_line"):
        from argus.agent import aggregate

        extra = aggregate.line(_evidence_records(evidence), clean, r)
        if extra:
            evidence = _prepend(evidence, extra)
            trace.append(f"aggregate line=1 chars={len(extra)}")

    # 🔴 날짜가 지목하는 공시를 가른다. `K-004` 에서 같은 날 같은 제목의 공시가 둘이고
    #    **둘 다 근거에 있었는데** 모델이 정정본을 골랐다. 검색이 아니라 선택이 틀린
    #    자리라, 집합의 경계를 밝히는 `_roster_line` 과 같은 처방을 쓴다 — 머리에 한 줄.
    if ablation.enabled("date_disambiguation"):
        from argus.agent import aggregate

        pick = aggregate.date_disambiguation(_evidence_records(evidence), clean)
        if pick:
            evidence = _prepend(evidence, pick)
            trace.append(f"date_pick line=1 chars={len(pick)}")

    # 🔴 법인등록번호 — 조각이 문제였지 근거 칸이 문제가 아니었다 (2026-08-17).
    #    목차를 `AFFILIATE_PATH` 로 좁히면 검색은 맞는 보고서를 고르는데, 그 섹션이
    #    34,122자라 6조각으로 쪼개지고 BM25 가 회사명이 빽빽한 4번 조각을 고른다.
    #    정작 찾는 행은 703자 지점(1번 조각)에 있다. 칸을 늘리는 것은 `A16`·`A17` 이
    #    기각된 방향이라, **섹션 전문에서 행 하나만** 뽑아 머리에 둔다.
    #
    #    발화 조건이 좁다: 질문이 법인등록번호를 묻고, 기업이 지목됐고, 검색이 이미
    #    가져온 문서 안에 그 행이 있어야 한다. 셋 중 하나라도 어긋나면 빈 문자열이라
    #    근거가 종전과 글자 하나 다르지 않다.
    if _ASK_CORP_NUMBER.search(clean) and r.companies:
        docs = [PERIODIC_DOC_PREFIX + no for no in (provenance or {})]
        line = tools.corp_number_line(r.companies[0], docs)
        if line:
            evidence = _prepend(evidence, line)
            trace.append(f"corp_number line=1 chars={len(line)}")

    # 🔴 A29b — 정정 이력을 접수일 순으로 펴서 한 줄로 싣는다. `aggregate_line` 과
    #    **축이 다르다**: 저쪽은 값 하나(합·차·몫)이고 이쪽은 여러 값의 순서다.
    if ablation.enabled("revision_series"):
        from argus.agent import aggregate

        series = aggregate.revision_series(_evidence_records(evidence), clean)
        if series:
            evidence = _prepend(evidence, series)
            trace.append(f"series line=1 chars={len(series)}")

    try:
        # 🔴 `A8-lean` 은 여기서 갈린다. `enabled()` 로 읽는 이유는 오타가 조용히
        #    False 가 되지 않게 하기 위해서다 — 없는 플래그 이름은 `KeyError` 다.
        #    기본값(False)에서는 아래 두 줄이 종전과 글자 하나 다르지 않다.
        messages = build_answer_messages(
            clean, evidence, r.use_cot,
            lean_rules=ablation.enabled("lean_rules"),
            strict_refusal=ablation.enabled("strict_refusal"))
        max_tokens = 800 if r.use_cot else 300
        arm = ablation.current()
        # 🔴 `A24` 는 여기서 갈린다 — 도구를 내밀고 왕복한다. 기본값(False)에서는 아래
        #    `else` 가지가 종전과 글자 하나 다르지 않다.
        if _offer_tools(arm, clean, trace):
            answer = _answer_with_tools(messages, max_tokens, arm, budget, trace)
        else:
            # 🔴 온도는 이제 `Arm` 이 들고 있다. **기본값이 0.0** 이라 `A10` 이외의 arm 에서
            #    이 호출은 종전과 본문이 바이트 동일이고, 요청 캐시 키도 그대로다.
            result = chat(messages, max_tokens=max_tokens,
                          temperature=arm.temperature, tag="answer")
            budget.spend_llm(result.prompt_tokens, result.completion_tokens)
            answer = result.text
            trace.append(f"generate tokens={result.prompt_tokens}+{result.completion_tokens}")
        if arm.sc_k > 1:
            answer = _self_consistent(messages, max_tokens, arm, answer, budget, trace)
    except BudgetExceeded as exc:
        trace.append(f"예산 초과: {exc}")
        return _respond(question_id, question, evidence, trace, UNANSWERABLE, budget)
    except RuntimeError as exc:
        # /answer 는 어떤 경우에도 5키 스키마를 돌려줘야 한다. 상류 장애로 500 을 내면 0점이다.
        # 다만 **삼키지는 않는다** — 표식을 남겨 호출부가 전송 실패를 가려낼 수 있게 한다.
        trace.append(f"{_upstream_mark('generate', exc)} 생성 실패: {exc}")
        return _respond(question_id, question, evidence, trace, UNANSWERABLE, budget)

    if not quick_check(answer)["has_citation"]:
        trace.append("근거 표기 누락 — critic 호출")
        try:
            verdict = judge(evidence, answer, budget)
            trace.append(f"critic supported={verdict.supported} reason={verdict.reason}")
            if not verdict.passed:
                answer += f"\n\n(주의: 근거 검증을 통과하지 못했습니다 — {_verdict_reason(verdict)})"
        except BudgetExceeded as exc:
            trace.append(f"critic 생략: {exc}")            # 우리 상한이다 — 장애가 아니다
        except RuntimeError as exc:
            trace.append(f"{_upstream_mark('critic', exc)} critic 생략: {exc}")

    # 🔴 `budget` 을 넘긴다 — `A20r` 의 재생성이 LLM 호출을 하나 더 쓰고, 그것이
    #    게이트 F 의 `budget llm=` 에 잡혀야 한다. 나머지 arm 에서는 안 쓰인다.
    answer = _verify(clean, answer, evidence, r, trace, budget)
    # 🔴 A25 — 근거 표기를 우리가 붙인다. **여기여야 한다**: 모든 LLM 호출이 끝난 뒤라
    #    생성·critic·재생성 프롬프트가 바이트 동일로 남고 `RequestCache` 가 적중한다.
    #    플래그가 꺼져 있으면(기본값) 아래 두 줄은 종전과 글자 하나 다르지 않다.
    # 🔴 A29s — 결정적 집계값으로 답변의 **틀린 합계 하나**를 갈아 끼운다.
    #    `A25`(부착)와 같은 자리다 — 모든 LLM 호출이 끝난 뒤라 생성·critic·재생성
    #    프롬프트가 바이트 동일로 남고 `RequestCache` 가 적중한다. LLM 0회.
    if ablation.enabled("substitute_aggregate"):
        from argus.agent import aggregate

        answer, swapped = aggregate.substitute(
            answer, _evidence_records(evidence), clean, r)
        if swapped:
            trace.append(f"aggregate substitute={swapped}")

    # 🔴 결과값 부재 — 치환이 못 잡는 자리다. `H-008` 에서 모델은 **틀린 수를 쓴 게
    #    아니라 맞는 수를 틀리게 엮었고**, 그래서 "근거 밖 수 하나" 라는 치환의 발화
    #    조건에 안 걸렸다. 질문이 부른 값이 답변에 아예 없으면 한 문장으로 적는다.
    #    🔴 `substitute` **뒤**다 — 치환할 수 있으면 치환이 낫다(문장이 안 는다).
    if ablation.enabled("state_total"):
        from argus.agent import aggregate

        answer, stated = aggregate.state_total(
            answer, _evidence_records(evidence), clean, r)
        if stated:
            trace.append(f"aggregate {stated}")

    # 🔴 A35 — 괄호 환산을 우리가 다시 쓴다. **치환 뒤에 서야 한다** — `substitute` 가
    #    본 수치만 갈아 끼우면 모델이 제 오답에서 계산한 괄호가 남는다(`Q-115` 실측).
    if ablation.enabled("fix_unit_parens"):
        from argus.agent import aggregate

        answer, repaired = aggregate.fix_unit_parens(answer)
        if repaired:
            trace.append(f'unit_paren fixed={len(repaired)} {" · ".join(repaired)}')

    # 🔴 반올림 복원 — `fix_unit_parens` 의 **반대 자리**다. 저쪽은 "원값은 맞고 괄호가
    #    틀린" 것을 고치고, 이쪽은 "괄호만 있고 원값이 없는" 것을 고친다. `N-011` 실측:
    #    근거에 257,732,228,908 과 725,693,799,075 가 둘 다 있는데 답변이 "2,577억원" ·
    #    "7,257억원" 으로만 적었다. 값은 맞고 **정밀도만 잃었다.**
    #    🔴 `fix_unit_parens` **뒤**에 선다 — 저쪽이 괄호를 고친 뒤라야 이쪽이 만든
    #    `원값 (억)` 짝을 저쪽이 다시 건드리지 않는다.
    if ablation.enabled("expand_rounded"):
        from argus.agent import aggregate

        answer, grown = aggregate.expand_rounded(answer, _evidence_records(evidence))
        if grown:
            trace.append(f'expand_rounded n={len(grown)} {" · ".join(grown)}')

    # 🔴 A38 — 답변이 **스스로 쓴 식과 어긋나는 수**를 맞춘다. 규약 「추론 논리성」.
    #    closed judge 진단이 이 자리를 찾았다 — EM 도 수치검사도 못 잡는 자기모순이다.
    if ablation.enabled("self_consistency_fix"):
        from argus.agent import aggregate

        answer, aligned = aggregate.fix_self_contradiction(answer)
        if aligned:
            trace.append(f'self_align fixed={len(aligned)} {" · ".join(aligned)}')
    answer, cited = ablation.attach_citation(answer, evidence, provenance=provenance)
    if cited:
        trace.append(f'citation attached={",".join(cited)}')
    return _respond(question_id, question, evidence, trace, mask_pii(answer), budget)


# --- A6 Verifier — 결정적 검증 2종. LLM 0회 ------------------------------------
# `judge` 는 "답이 근거로 지지되는가" 만 본다. 그래서 정정 전 금액만 근거에 있으면
# 모델이 그 값을 답하고 critic 은 통과시킨다 — 근거 자체가 불완전하다는 것을 못 본다
# (07-verifier G-4, 실측 오답 5건). 두 검증은 축이 다르다.
#
#   verify_calculation  — 답변 수치를 근거 레코드 원값으로 재계산 대조   (TOOL)
#   verify_completeness — 근거가 충분한가                              (RETRIEVAL·GENERATION)
#
# **답을 갈아엎지 않는다.** 결함을 찾아도 재생성하지 않고 경고만 붙인다 — 재생성은
# 호출을 하나 더 쓰는 별개의 조작이고, 이 arm 이 재는 것은 '판정' 의 기여다
# (SWE-Lancer 부록 C: 판단 44.9% > 생성 26.2%).
#
# 🔴 **그 설계의 대가를 v4 R0 가 실측했다 — 탐지 100% · 교정 0%.** `Q-097` 의 `per_unit`
# 재계산값 207,684,631,210 은 골든과 바이트까지 같은데 각주로만 붙고 버려졌다. 채점기는
# 그 각주를 일부러 잘라내고 채점한다(`error_analysis.strip_verify_tail`) — 즉 **우리는
# 정답을 계산해 놓고 버리고 있었다.**
#
# 🔴 그래서 기본값을 바꾸지 않고 **arm 으로 만들었다** (`ablation.Arm.substitute_verified`
# = `A20`). 위 설계에는 근거가 있으니 갈아엎지 말고 켰다 껐다 잰다. 플래그가 꺼져 있으면
# 이 함수의 동작은 v3 세대 664 답변 전수에서 **바이트 동일**이다.

_EVIDENCE_RCEPT = re.compile(r"접수번호\s*(\d{14})")


def _prepend(evidence: str, line: str) -> str:
    """결정적 집계 줄을 근거 **머리**에 둔다.

    🔴 **꼬리에 두면 안 읽힌다** (2026-08-16 실측). `Q-023` 에 정정 금액 7건의 시계열을
    꼬리에 실었는데 답은 여전히 *"계약금액은 변동 없이 유지되었습니다"* 였다 — 블록
    열 개를 지나온 뒤라 마지막 한 줄이 묻힌다. KDD '26 실측의 주의 배분(system 36.9% ·
    output 34.5% · 근거 14.0%)에서 그 14% 안의 **자리**도 균등하지 않다는 뜻이다.

    🔴 시스템 프롬프트로 올리지 않는다 — 거기는 36.9% 를 먹는 자리라 집계와 무관한
    문항의 답까지 흔든다 (`A27` 이 그렇게 역효과를 냈다). 근거 안의 맨 앞이 절충이다.
    """
    return f"{line}\n\n{evidence}"


def _evidence_records(evidence: str) -> list[dict]:
    """근거에 실린 접수번호로 정형 레코드를 되찾는다. 검색이 아니라 조회다 (스텝 0)."""
    out = []
    for rcept_no in dict.fromkeys(_EVIDENCE_RCEPT.findall(evidence or "")):
        record = tools._record_of(rcept_no)
        if record is not None:
            out.append(record)
    return out


def _verdict(question: str, answer: str, evidence: str, records: list[dict], r,
             arm) -> dict:
    """답변 **하나**에 대한 검증 판정 한 벌. 부작용이 없다 — trace 도 안 건드린다.

    🔴 `A20r` 이 이것을 **두 번** 부른다 (원본 · 재생성본). 그래서 규칙을 여기 한 곳에만
    둔다 — 재생성 전후를 다른 잣대로 재면 *"여전히 발화하면 원본을 쓴다"* 가 성립하지
    않는다 (`critic.unique_recomputed` 를 한 곳에 둔 것과 같은 이유).

    `records` 를 인자로 받는 것도 같은 이유다. 근거에서 되찾는 정형 레코드는 답변과
    무관하므로 두 번 조회할 일이 없다.
    """
    # 레코드가 여러 건이면 **하나라도 설명되면 통과**다. 체인 안에서 원공시와 정정본의
    # 금액이 갈리므로(227,500 vs 114,800) 전건 일치를 요구하면 정답이 오답이 된다.
    #
    # 🔴 그 관용에 구멍이 있었다 — **자릿수 오류도 함께 묻혔다.** Q-065 는 Dukovany 금액을
    # 10배로 틀렸는데 같은 답변의 신한울 금액이 정확해서 `explained=True` 로 통과했다.
    # 체인 관용은 '같은 값의 다른 시점' 을 위한 것이고 자릿수 오류는 시점 문제가 아니다.
    # 판정을 `critic.aggregate_calculation()` 한 곳으로 모은다 — 규칙이 두 곳에 있으면 갈린다
    # (`scripts/error_analysis.verify_answer_calc` 도 같은 함수를 쓴다).
    reports = [verify_calculation(answer, record) for record in records]
    agg = aggregate_calculation(reports)
    checked, calc_failed = agg["checked"], agg["failed"]

    # 🔴 A20 — `verify_answer_arithmetic` 를 라이브 경로에 배선한다. **플래그 뒤에 둔다.**
    # 이 검사는 근거를 한 줄도 안 읽고 답변이 스스로 쓴 등식·재기술쌍만 본다. 그래서
    # 정형 레코드가 0건인 문항(실측 Q-044·Q-047)에서도 서고, 그런 문항이 위 `reports` 로는
    # 통째로 안 보인다 — v3 까지 이 함수는 `scripts/error_analysis.py` 만 썼다.
    #
    # 🔴 **`reports` 에 합치지 않는다.** `aggregate_calculation` 의 `explained` 는
    # "레코드 하나라도 설명하면 통과" 인데, 이 검사는 레코드가 아니라 답변당 하나다.
    # 안에 넣으면 등식이 맞는 답변이 근거 대조 실패까지 덮어 **체인 관용이 뒤집힌다**
    # (`critic.verify_answer_arithmetic` 머리말 ②). 판정만 OR 로 합친다.
    #
    # 🔴 무조건 배선하면 플래그를 **끈** 상태에서도 각주가 늘어난다 — v3 세대 664 답변
    # 전수 대조로 확인했다(Step 0b). 그 자체가 답변 변화이고 `A20` 의 Δ 와 섞이므로
    # 배선도 플래그 뒤에 둔다.
    #
    # 🔴 **`A20r` 도 이 검사를 켠다.** 재생성에 실을 **사유의 원천**이 필요하고, 실측
    # `Q-044` 는 정형 레코드가 0건이라 이 검사 없이는 발화조차 없다 — 그러면 `A20r` 의
    # 겨냥이 그 문항에서 조용히 사라진다. `A20`·`A20r` 이 같은 판정을 읽고 **처분만**
    # 갈리는 것이 두 arm 을 나란히 재는 설계의 전부다.
    arith_reports: list[dict] = []
    if arm.substitute_verified or arm.regenerate_on_verify:
        arith = verify_answer_arithmetic(answer)
        arith_reports = [arith]
        checked = sorted({*checked, *arith["checked"]})
        calc_failed = calc_failed or not arith["ok"]

    # 🔴 A21 — 수치 출처 게이트. **`calc_failed` 에 합치지 않는다.** 이 검사는 *"이 숫자가
    # 근거에 있는가"* 를 묻고 계산 판정과 축이 다르다 — 합치면 ① `A20` 의 치환이 이 게이트
    # 때문에 발화하고(이 검사는 `recomputed` 가 항상 비어 있어 치환할 값이 없다) ② Δ 가
    # `calculation` 으로 귀속돼 두 arm 의 사유가 섞인다. 별개 `kind` 로 세운다.
    #
    # 🔴 배선을 플래그 뒤에 둔 것은 `A20` 과 같은 이유다 — 무조건 배선하면 플래그를 끈
    # arm 에서도 각주가 늘어나고 그 자체가 답변 변화다 (v3 세대 664 답변 중 23건 발화).
    provenance = verify_number_provenance(answer, evidence) if arm.provenance_gate else None

    # 🔴 A36 — **발주처는 공시자가 아니다.** `missing_corp` 은 *"비교 질의인데 근거에
    #    그 기업이 없다"* 를 잡는데, 질문이 지목한 발주처는 그 계약을 **공시한 쪽이
    #    아니라 발주한 쪽**이라 근거에 있을 수가 없다. 실측(Q-113·Q-115): 정답인
    #    답변에 *"근거에 삼성전자 이(가) 없다"* 각주가 영구히 붙는다.
    #    저장소가 이미 아는 사실이다 — `_wants_enumeration` 의 발주처 가드와
    #    `test_disclosing_company_is_never_bound_as_counterparty` 가 같은 판단이다.
    compared = list(r.companies or [])
    if ablation.enabled("counterparty_exempt") and getattr(r, "counterparty", None):
        compared = [c for c in compared if c != r.counterparty]
    complete = verify_completeness(question, answer, evidence, companies=compared)

    kinds = sorted({*(["calculation"] if calc_failed else []), *complete["failed_kinds"],
                    *(provenance["failed_kinds"] if provenance else [])})

    # 사유도 `aggregate_calculation` 이 정한 순서를 쓴다 — 자릿수 오류가 앞에 온다.
    # 손으로 첫 사유를 뽑으면 무해한 이웃이 먼저 실려 경고가 약해진다.
    #
    # 🔴 **이 목록이 `A20r` 이 재생성에 싣는 것 그대로다.** 각주와 프롬프트가 같은
    # 목록을 읽어야 "각주로 본 것" 과 "모델에게 준 것" 이 갈리지 않는다. 원천이 둘이다 —
    # ① 계산 불일치(`aggregate_calculation`·`verify_answer_arithmetic`)와
    # ② 🔴 근거에 없는 수(`verify_number_provenance` = `A21` 의 판정).
    reasons = agg["reasons"][:1] if calc_failed else []
    if not reasons and arith_reports and arith_reports[0]["reason"]:
        reasons = [arith_reports[0]["reason"]]      # 레코드가 0건인 문항 (실측 Q-044)
    if complete["reason"]:
        reasons.append(complete["reason"])
    if provenance and provenance["reason"]:
        reasons.append(provenance["reason"])

    return {"reports": reports, "arith_reports": arith_reports, "provenance": provenance,
            "complete": complete, "checked": checked, "calc_failed": calc_failed,
            "kinds": kinds, "reasons": reasons}


def _regenerate_once(question: str, answer: str, evidence: str, records: list[dict],
                     r, arm, trace: list[str], budget: Budget | None,
                     verdict: dict) -> tuple[str, dict]:
    """🔴 `A20r` — 실패 사유를 실어 **딱 한 번** 다시 생성한다. 돌려주는 것은
    (채택한 답변, 그 답변의 판정) 짝이다.

    🔴 **재생성본에 검사가 여전히 발화하면 원본과 원본의 판정을 그대로 돌려준다** —
    그러면 호출부가 종전대로 각주를 붙인다. '더 나빠지면 원본' 을 판정 교체로 표현한
    것이라 두 자리에서 규칙이 갈릴 여지가 없다.

    🔴 **1회로 못 박는다.** 여기서 자기 자신을 다시 부르지 않는 것이 그 상한의 전부다 —
    루프를 두면 문항당 비용 상한이 사라진다 (이 파일 머리말: 고정 횟수 단계).
    """
    if budget is None:
        # 🔴 조용히 건너뛰면 **조용한 Δ 0** 이고, 세지 않고 부르면 게이트 F 가 거짓을
        #    말한다. 둘 다 안 되므로 여기서 멈춘다.
        raise ValueError(
            "regenerate_on_verify 는 예산 계정이 필요합니다 — 재생성 호출을 아무도 세지 "
            "않으면 게이트 F 의 `budget llm=` 이 거짓을 말합니다. `_verify(..., budget=)` "
            "로 넘기십시오.")
    if budget.remaining()["llm_calls"] <= 0:
        # 🔴 **부르기 전에** 본다. 부르고 나서 세면 크레딧은 이미 나갔다.
        trace.append("verify regen=skip_budget (LLM 호출 상한) kept=original")
        return answer, verdict

    use_cot = bool(getattr(r, "use_cot", False))
    try:
        result = chat(build_regeneration_messages(
                          question, evidence, verdict["reasons"], answer,
                          use_cot=use_cot, lean_rules=ablation.enabled("lean_rules")),
                      max_tokens=800 if use_cot else 300,
                      # 🔴 온도 0.0 을 유지한다 — 다양성을 사는 것은 `A10` 의 조작이다.
                      #    `Arm.temperature` 가 생긴 뒤에도 **여기서는 읽지 않는다**:
                      #    `A20r` 이 재는 것은 진단 주입이고, 온도를 함께 올리면 그 Δ 에
                      #    다양성의 몫이 섞여 두 arm 을 나란히 비교할 수 없게 된다.
                      temperature=0.0, tag="regenerate")
        budget.spend_llm(result.prompt_tokens, result.completion_tokens)
    except BudgetExceeded as exc:
        trace.append(f"verify regen=skip_budget ({exc}) kept=original")
        return answer, verdict
    except RuntimeError as exc:
        # 상류 장애로 답변을 잃지 않는다. 다만 **삼키지는 않는다** (`answer_question` 과 같은 규약).
        trace.append(f"{_upstream_mark('regenerate', exc)} verify regen=skip_error "
                     f"kept=original: {exc}")
        return answer, verdict

    candidate = (result.text or "").strip()
    if not candidate:
        trace.append("verify regen=empty kept=original")
        return answer, verdict

    again = _verdict(question, candidate, evidence, records, r, arm)
    kept = "regenerated" if not again["kinds"] else "original"
    trace.append(f'verify regen=1 reasons={"; ".join(verdict["reasons"])} '
                 f'kinds_after={",".join(again["kinds"]) or "-"} kept={kept}')
    return (candidate, again) if kept == "regenerated" else (answer, verdict)


def _verify(question: str, answer: str, evidence: str, r, trace: list[str],
            budget: Budget | None = None) -> str:
    """A6. 결함이 있으면 답변에 경고를 붙이고 trace 에 판정을 남긴다.

    🔴 `substitute_verified`(A20)가 켜져 있으면 경고를 붙이는 대신 **검사가 낸 유일한
    재계산값으로 답변의 그 수치를 치환**한다.
    🔴 `regenerate_on_verify`(A20r)가 켜져 있으면 **실패 사유를 실어 한 번 재생성**한다.
    둘 다 꺼져 있으면 종전 동작 그대로다 — 각주만 붙인다.
    """
    arm = ablation.current()
    if not arm.verifier:
        return answer

    records = _evidence_records(evidence)
    v = _verdict(question, answer, evidence, records, r, arm)

    # 🔴 게이트가 꺼진 arm 의 트레이스는 **바이트 동일**이어야 한다 — 필드를 무조건 찍으면
    #    v3 세대 전 arm 의 `think_trace` 가 달라지고 저장 산출물과 대조가 안 된다.
    gate = (f'provenance_checked={",".join(v["provenance"]["checked"]) or "-"} '
            if v["provenance"] else "")
    trace.append(f'verify calc_checked={",".join(v["checked"]) or "-"} '
                 f'complete_checked={",".join(v["complete"]["checked"]) or "-"} '
                 f'{gate}ok={not v["kinds"]} kinds={",".join(v["kinds"]) or "-"}')
    if not v["kinds"]:
        return answer

    # 🔴 A20r — 각주 대신 **사유를 실어 재생성 1회.** 치환보다 먼저 선다: 재생성이
    #    결함을 지웠으면 치환할 것이 남아 있지 않다. `A20r` 은 `substitute_verified`
    #    를 켜지 않으므로 현 구성에서 둘이 한 답변에 함께 손대는 일은 없다.
    if arm.regenerate_on_verify:
        answer, v = _regenerate_once(question, answer, evidence, records, r, arm,
                                     trace, budget, v)
        if not v["kinds"]:
            return answer

    # 🔴 A20 — 각주 대신 치환한다. 유일성 판정은 `critic.unique_recomputed` 한 곳에만 있고
    # (`aggregate_calculation` 도 같은 함수를 쓴다) 여기서는 **레코드 판정 + 답변 산술
    # 판정의 합집합**으로 부른다. 값이 갈리면 `None` 이 오고 치환은 일어나지 않는다.
    substituted: list[str] = []
    if arm.substitute_verified and v["calc_failed"]:
        pool = v["reports"] + v["arith_reports"]
        answer, substituted = substitute_recomputed(answer, pool, unique_recomputed(pool))
        if substituted:
            trace.append(f'verify substituted={"; ".join(substituted)}')

    # 🔴 치환했으면 그렇게 적는다. 고쳐 놓고 "결함이 발견되었습니다" 라고만 쓰면 각주가
    # 본문과 어긋난다. 접두사 `(주의:` 는 유지한다 — `strip_verify_tail` 이 그것을 읽는다.
    head = ("자동 검증에서 결함이 발견되어 재계산값으로 정정했습니다" if substituted
            else "자동 검증에서 결함이 발견되었습니다")
    return answer + f'\n\n(주의: {head} — {"; ".join(v["reasons"])})'


def _respond(question_id: str, question: str, evidence: str, trace: list[str],
             answer: str, budget: Budget | None = None) -> dict:
    """5키 스키마. 어떤 경로로 나가든 마지막에 예산 실사용량을 남긴다 (게이트 F)."""
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
