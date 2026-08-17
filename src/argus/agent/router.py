"""Query router. Classification is rule based; no LLM calls."""
from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from functools import lru_cache

from argus import ablation
from argus.agent.query import report_hints
from argus.corpus.counterparty import canonical
from argus.corpus.manifest import filter_docs, load_universe

_YEAR = re.compile(r"(20\d{2})\s*년?")
# Comparison markers exclude particles that also occur in single-metric
# phrases, which would route them onto the costly reasoning path.
# Multi-company detection is handled by company count instead.
_COMPARE_HINT = ("비교", "중 어", "더 큰", "더 많", "vs")
_CALC_HINT = ("증감률", "증가율", "감소율", "비중", "몇 %", "몇 퍼센트", "합계", "총액", "차이")
_OPEN_HINT = ("정리해", "설명해", "어떻게 변화", "요약해", "알려줘")
_EXISTS_HINT = ("존재하는가", "있는가", "있나")

#: Demonstratives with no referent. A question naming no company is answered
#: with a clarifying question rather than an out-of-scope notice, since the
#: two states differ: an unlisted company is genuinely out of scope, while a
#: bare demonstrative means the subject is unknown. Only demonstratives are
#: inspected; detecting company-like tokens would require NER.
_DANGLING_REF = ("그 회사", "그 기업", "이 회사", "이 기업",
                 "해당 회사", "해당 기업", "위 회사", "위 기업", "그곳")

#: Requests for forecasts, which are not disclosure content. Fires only on
#: the product of a target term and an asking term, because the individual
#: words occur in disclosure bodies and a single signal would reject
#: answerable questions. `tests/test_router_safety_gates.py` pins zero false
#: positives across the evaluation set.
#: See docs/design-rationale.md#gate-conjunction
_SPEC_TARGET = ("주가", "주식 가격", "목표주가", "투자의견", "다음 분기", "다음분기",
                "내년", "향후", "앞으로", "미래")
_SPEC_ASK = ("예측", "전망해", "전망을", "오를 가능성", "내릴 가능성", "상승할",
             "하락할", "늘어날지", "줄어들지", "오를까", "내릴까", "오를지", "내릴지")

#: Expressions that fire without a paired target term. Each reads as a
#: request for a judgement absent from any filing. The list stays narrow:
#: widening it removes the conjunction that protects answerable questions.
_SPEC_ALONE = ("목표주가", "매수 의견", "매도 의견", "투자의견", "투자 의견",
               "적정 매수", "매수 시점", "매도 시점", "수익이 날", "수익이 나",
               "사도 될까", "팔아야 할까")

#: Requests to transcribe identifiers or contact details verbatim. Gating at
#: routing complements output masking rather than replacing it: masking
#: guards figures arriving inside evidence, this guards the request itself.
#: Corporate and business registration numbers are absent because they are
#: not personal data.
#: See docs/design-rationale.md#corporate-identifier
_IDENTIFIER_TARGET = ("전화번호", "휴대전화", "휴대폰", "연락처",
                      "주민등록번호", "주민번호", "계좌번호",
                      "이메일", "메일 주소", "이메일주소")
_IDENTIFIER_ASK = ("옮겨 적", "옮겨적", "그대로 적", "알려줘", "알려주", "뭐야",
                   "무엇인", "찾아줘", "정리해", "나열해")


def _asks_speculation(question: str) -> bool:
    """Whether the question asks for a forecast absent from any filing."""
    text = question or ""
    if any(a in text for a in _SPEC_ALONE):
        return True
    return (any(t in text for t in _SPEC_TARGET)
            and any(a in text for a in _SPEC_ASK))


def _asks_identifier(question: str) -> bool:
    """Whether the question asks for personal data or identifiers verbatim."""
    text = question or ""
    return (any(t in text for t in _IDENTIFIER_TARGET)
            and any(a in text for a in _IDENTIFIER_ASK))

#: Corpus period, 2023.01 to 2026.03 (CONVENTION section 2, G7).
CORPUS_YEARS = range(2023, 2027)

#: Relative time expressions. Their presence suppresses the out-of-range
#: block, so a query whose reference point alone falls outside the corpus
#: remains answerable. The retrieval stage calls the same predicate, since
#: two independent copies would diverge.
_RELATIVE_HINT = ("최근", "작년", "올해", "지난", "전년", "직전", "현재", "이후", "까지", "부터")

#: Signals routing to periodic filings. The bare word for report is excluded,
#: because holding filings carry it in their form name.
_PERIODIC_HINT = (
    "사업보고서", "반기보고서", "분기보고서", "사업 보고서", "반기 보고서", "분기 보고서",
    "요약재무정보", "연결재무제표", "재무제표 주석", "재무에 관한 사항", "매출 및 수주",
    "경영진단", "감사의견", "내부통제", "회사의 개요", "사업의 내용", "주주에 관한 사항",
    "임원의 보수", "배당에 관한 사항", "연결기준", "연결 기준", "별도기준",
    # Corporate registration numbers appear only in periodic detail tables;
    # exchange records contain none.
    "법인등록번호", "사업자등록번호",
)
_PERIODIC_RE = re.compile(r"제\s*\d+\s*기|연결\s*(?:기준\s*)?(?:매출액|영업이익|당기순이익|자산총계)")

#: Holding-filing signals, evaluated before periodic signals because the
#: form names overlap.
_HOLDING_HINT = ("대량보유", "보유상황보고서", "보유비율", "보유주식", "지분 보유",
                 "소유상황", "보유 목적", "주식등의 대량", "지분율", "%p")

#: Major-report signals: capital increases, bonds, mergers, treasury stock.
_MAJOR_HINT = ("유상증자", "무상증자", "전환사채", "신주인수권부사채", "교환사채",
               "조건부자본증권", "권면", "합병", "분할", "자기주식", "감자",
               "영업양수", "영업양도", "자산양수", "자산양도", "주요사항보고서",
               "사채", "표면이자율", "전환가액", "자금조달")


def doc_group(question: str) -> str:
    """Classify a query into a document group, which selects the index.

    Evaluation order matters: holding filings carry the word for report in
    their form name, so checking periodic signals first would misroute them.
    """
    text = question or ""
    if any(h in text for h in _HOLDING_HINT):
        return "holding"
    if is_periodic(text):
        return "periodic"
    if any(h in text for h in _MAJOR_HINT):
        return "major"
    return "exchange"


@dataclass
class Route:
    kind: str
    #: Difficulty label. Recorded in the trace only; no branch depends on it.
    difficulty: str = "low"
    companies: list[str] = field(default_factory=list)
    years: list[int] = field(default_factory=list)
    use_cot: bool = False
    #: Self-consistency suitability. Computed but not consumed: the sampling
    #: arm reads its own switch instead. Do not wire this in without first
    #: re-measuring, since prior results were produced without it.
    use_sc: bool = False
    #: Provenance of the routing decision. Never assigned; retained because
    #: removing it would change the shape of `Route`, which reproduction code
    #: depends on. Do not use in new logic.
    by: str = "rule"
    group: str = "exchange"
    reason: str = ""
    #: Counterparty named by the question. A different axis from `companies`:
    #: this side awards the contract and does not file the disclosure.
    counterparty: str | None = None


def is_periodic(question: str) -> bool:
    """Whether the query should be served by periodic section search."""
    text = question or ""
    return any(h in text for h in _PERIODIC_HINT) or bool(_PERIODIC_RE.search(text))


# --- Calculation-query detection -----------------------------------------
# The model cannot accept tools and a token ceiling together, so offering
# tools removes the answer length bound. Tools are opened only for queries
# that require calculation.
#
# Asking for an amount is not itself a signal: most questions here ask for a
# figure copied from evidence rather than derived. The window covers derived
# quantities only: totals, means, ratios, growth, durations, differences and
# unit prices.

#: Calculation signals by group, kept separate so the triggering group can be
#: attributed afterwards. Distinct from `_CALC_HINT`, which decides reasoning
#: depth rather than tool availability.
CALC_QUESTION_HINTS: dict[str, str] = {
    "합계·평균": r"합계|합산|총계|총합|총액|모두\s*얼마|평균",
    "비율·증감률": (r"비중|비율|퍼센트|%|증감률|증가율|감소율|성장률|상승률|하락률|"
                r"변동률|CAGR|연평균"),
    "기간": r"기간|몇\s*년|몇\s*개월|며칠",
    "차이·비교": (r"차이|차액|증가액|감소액|증감액|몇\s*배|"
              r"얼마나\s*(?:더\s*)?(?:증가|감소|늘|줄|커|작|많|적|큰|작은|높|낮)|"
              r"보다\s*(?:얼마나\s*)?(?:더\s*)?(?:큰|작|많|적|높|낮)|"
              r"더\s*(?:큰|작은|많|적|높|낮)"),
    "단가": r"1\s*(?:척|건|개|기|호기|대|동|기지)\s*당|당\s*(?:평균|금액|단가)",
}

_CALC_QUESTION_RE = re.compile("|".join(f"(?P<g{i}>{p})" for i, p
                                        in enumerate(CALC_QUESTION_HINTS.values())))
_CALC_QUESTION_KEYS = tuple(CALC_QUESTION_HINTS)


def calc_question_kind(question: str) -> str:
    """Signal group that classified this query as a calculation, or empty.
    """
    match = _CALC_QUESTION_RE.search(question or "")
    if not match:
        return ""
    index = next(i for i, (_, v) in enumerate(match.groupdict().items()) if v)
    return _CALC_QUESTION_KEYS[index]


def needs_calculation(question: str) -> bool:
    """Whether the query asks for a derived amount, ratio, duration or total."""
    return bool(calc_question_kind(question))


def out_of_corpus(years: list[int], question: str = "") -> bool:
    """Whether every year named by the question falls outside the corpus.

    A single in-range year is enough to proceed. Relative time expressions also
    suppress the block, since a year may serve only as a reference point.
    """
    if not years or any(y in CORPUS_YEARS for y in years):
        return False
    return not any(h in (question or "") for h in _RELATIVE_HINT)


# --- Named report absent from the corpus ----------------------------------
# A hop ladder cannot distinguish not-found from does-not-exist: when a
# filtered search returns nothing the ladder relaxes the filters and answers
# from a different document. The manifest is consulted instead.
#
# Over-refusal is the real hazard, so three thresholds apply.
#   1. Report type alone does not block; it does not identify a document.
#   2. Both the fiscal-year and filing-date axes must be empty, since the
#      fiscal year is partly inferred.
#   3. With several companies, one available document is enough to proceed.
def _report_axes(hints: dict) -> dict:
    """Keep only axes that identify a document. Report type is a precondition."""
    return {k: v for k, v in hints.items() if k in ("base_year", "filed") and v}


#: Maps a named quarter onto the half-year in which it is filed. All covered
#: companies close in December, so first-quarter reports arrive in the first
#: half and third-quarter reports in the second. Half-years are used rather
#: than exact months so refiled documents are not excluded. Second and fourth
#: quarters are left untouched, since those names are ambiguous.
_QUARTER_LATE_FROM = 9


def _quarter_narrowed(docs, subtype: str, quarter: str | None):
    """Narrow to the matching half-year when the question names a quarter."""
    if subtype != "quarter" or quarter not in ("1", "3"):
        return docs
    late = quarter == "3"
    out = []
    for doc in docs:
        month = str(doc.get("rcept_dt") or "")[4:6]
        if month.isdigit() and (int(month) >= _QUARTER_LATE_FROM) == late:
            out.append(doc)
    return out


def missing_named_report(question: str, companies: Sequence[str]) -> str:
    """Whether the periodic report named by the question is absent from the manifest.

    A manifest lookup rather than a search: no step budget is consumed and no
    index is opened. Returns the reason to record in the trace, or empty.
    """
    hints = report_hints(question or "")
    subtype = hints.get("doc_subtype")
    axes = _report_axes(hints)
    if not (subtype and axes and companies):
        return ""
    # When enabled, judge against the matching half-year only.
    quarter = hints.get("quarter") if ablation.enabled("quarter_gate") else None
    for corp in list(companies)[:3]:
        docs = filter_docs(corp_name=corp, doc_group="periodic", doc_subtype=subtype)
        docs = _quarter_narrowed(docs, subtype, quarter)
        for axis, value in axes.items():
            seen = (str(d.get("base_year", "")) if axis == "base_year"
                    else str(d.get("rcept_dt", ""))[:6] for d in docs)
            if any(v == value for v in seen):
                return ""                     # one matching axis is enough to pass
    return f'{subtype}:' + ",".join(f"{k}={v}" for k, v in sorted(axes.items()))


# --- Counterparty detection -----------------------------------------------
# Value-chain questions ask for every listed company contracting with one
# awarding party. That set is held by the counterparty graph rather than
# recovered by search, so detection matches graph node names instead of
# guessing company names with patterns.
#
# Matching node names alone over-fires, because companies that file
# disclosures are themselves nodes. Two constraints apply.
#
#   1. The document group must be exchange, which is the graph population.
#   2. A direction marker must bind immediately after the node name.
#
# The second is a binding test rather than a vocabulary test because a
# question can name several nodes that are all on the receiving side. What
# follows the node name decides its role.
#
# Excluding every company named in the question was rejected: the awarding
# party is sometimes itself a listed company named in the question, and
# excluding those loses the cases this hop exists to serve.

#: Direction markers. Without one, a node name is just a company mention.
#: Canonicalisation removes whitespace, so spaced variants match too.
#: The third marker denotes a development sponsor, which awards work to a
#: contractor and is therefore the same direction as the others.
_COUNTERPARTY_HINT = ("계약상대", "발주", "시행")

#: What must follow a node name immediately. `match()` anchors at position
#: zero of the string it receives, so the start here is the remainder after
#: the node name rather than the start of the sentence.
#:
#: Conjunctive particles are excluded. They join clauses rather than mark
#: case, so a node preceding one is not the awarding party even though the
#: direction marker appears nearby.
#:
#: A case particle is required, and the possessive is allowed only with the
#: award marker. A possessive before an awarding-party noun reverses the
#: direction: the node then has an awarding party rather than being one.
#: False negatives degrade gracefully into ordinary search, while false
#: positives plant a reversed relationship in the evidence, and that
#: asymmetry sets the threshold.
#:
#: The sponsor marker is restricted to the nominative and must be followed
#: by one of three continuations, since the same syllables begin several
#: legal terms that carry no directional meaning.
#: See docs/design-rationale.md#gate-conjunction
_COUNTERPARTY_ROLE = re.compile(
    r"^(?:(?:을|를|이|가)(?:계약상대|발주)"      # nominative or accusative: the node orders
    r"|(?:이|가)시행(?=하|사|중)"                # nominative only: a developer awards the work
    r"|의발주(?!처|사|업체|기업))")               # genitive only before the bare verb, since the
                                              # noun forms reverse the direction

# --- Rejected extension: marker preceding the node name --------------------
# Word order placing the marker before the node name is not matched. The
# omission causes false negatives, which end in an out-of-scope response.
#
# Supporting that order was rejected after constructed counterexamples
# produced false positives. Canonicalisation removes punctuation and
# whitespace, so a marker ending one sentence becomes adjacent to a company
# name beginning the next, creating an adjacency that never existed.
@lru_cache(maxsize=1)
def _counterparty_nodes() -> tuple[str, ...]:
    """Canonical graph node names, longest first.

    Longest-first ordering ensures a longer name wins when a shorter one is a
    substring of it. Returns an empty tuple when the graph is unavailable, in
    which case the router continues on its usual path.
    """
    from argus.agent.tools import _counterparty_graph

    graph = _counterparty_graph()
    if not graph:
        return ()
    nodes = sorted(graph.get("nodes") or (),
                   key=lambda n: (-len(n.get("canonical") or ""), -(n.get("count") or 0)))
    return tuple(n["canonical"] for n in nodes if n.get("canonical"))


def counterparty_display(name: str) -> str:
    """Source form of a canonical node name, or empty when unknown.

    Callers pass the source form to tools rather than the canonical key.
    Canonicalisation removes whitespace, and several anonymity patterns depend
    on whitespace boundaries, so passing the canonical key would defeat that
    guard. Canonical keys are for comparison only.

    Not cached: pairing a cache here with the node cache would let a test clear
    one and leave the other stale. The graph itself is already cached and this
    function runs at most once per question.
    """
    from argus.agent.tools import _counterparty_graph

    graph = _counterparty_graph() or {}
    for node in graph.get("nodes") or ():
        if node.get("canonical") == name:
            return node.get("display") or ""
    return ""


def find_counterparty(question: str, group: str = "exchange") -> str:
    """Canonical name of the node designated as awarding party, or empty.

    Canonicalisation folds notation variants, so all spellings of a name reach
    the same key. Names come from the graph; only the role is read from the
    sentence. Company names are never guessed with patterns.
    """
    if group != "exchange":
        return ""
    key = canonical(question or "")
    if not any(h in key for h in _COUNTERPARTY_HINT):
        return ""
    for node in _counterparty_nodes():          # longest name first
        start = 0
        while (index := key.find(node, start)) >= 0:
            if _COUNTERPARTY_ROLE.match(key[index + len(node):]):
                return node
            start = index + 1
    return ""


def _find_companies(question: str) -> list[str]:
    """Match every universe alias, longest first, to avoid substring hits."""
    found: list[str] = []
    aliases: list[tuple[str, str]] = []
    for corp_name, row in load_universe().items():
        for alias in (corp_name, row.get("listed_name"), row.get("corp_eng_name")):
            if alias and len(alias) >= 2:
                aliases.append((alias, corp_name))
    for alias, corp_name in sorted(aliases, key=lambda x: -len(x[0])):
        if alias in question and corp_name not in found:
            found.append(corp_name)
    return found


def route(question: str) -> Route:
    companies = _find_companies(question)
    years = sorted({int(y) for y in _YEAR.findall(question)})
    group = doc_group(question)
    counterparty = find_counterparty(question, group) or None

    is_open = any(h in question for h in _OPEN_HINT)
    is_calc = any(h in question for h in _CALC_HINT)
    is_compare = len(companies) >= 2 or any(h in question for h in _COMPARE_HINT)
    is_exists = any(h in question for h in _EXISTS_HINT)

    def made(kind, difficulty="low", cot=False, sc=False, reason="") -> Route:
        return Route(kind=kind, difficulty=difficulty, companies=companies, years=years,
                     use_cot=cot, use_sc=sc, group=group, reason=reason,
                     counterparty=counterparty)

    # Forecast requests are gated before company resolution. A named company
    # would otherwise route to lookup, which is too late to refuse.
    if ablation.enabled("speculation_gate") and _asks_speculation(question):
        return made("unanswerable", reason="speculation")

    # Identifier transcription is refused even when company and report are
    # in scope.
    # Complements output masking: that layer relies on patterns, this one
    # inspects the request.
    if ablation.enabled("identifier_gate") and _asks_identifier(question):
        return made("unanswerable", reason="identifier")

    if not companies and not counterparty:
        # An existence question is corpus-wide even without a company name.
        # Short-circuiting it would refuse a question the evidence answers.
        if is_exists:
            return made("complex_reason", "high", cot=True, sc=True)
        # A demonstrative with no referent means the subject is unknown, not
        # that it is out of scope.
        if ablation.enabled("ambiguous_gate") and any(k in question for k in _DANGLING_REF):
            return made("unanswerable", reason="ambiguous_reference")
        return made("unanswerable", reason="out_of_universe")

    # An awarding party may lie outside the listed universe; public
    # institutions are the largest hubs. Short-circuiting on the absence of a
    # listed company would refuse questions whose partners are all listed.

    # A query naming only out-of-range years cannot be rescued by search:
    # relaxing the period filter substitutes a disclosure from another year.
    # Short-circuiting here avoids spending steps before reporting the limit.
    if out_of_corpus(years, question):
        return made("unanswerable", reason=f"out_of_range:{years}")

    # The question names a document that the manifest does not contain.
    # Relaxing the filter would substitute another report from the same
    # company, so the request is short-circuited here.
    if group == "periodic":
        missing = missing_named_report(question, companies)
        if missing:
            return made("unanswerable", reason=f"no_such_report:{missing}")

    if is_open and (len(years) >= 2 or "변화" in question):
        return made("complex_reason", "high", cot=True)
    if is_compare or is_calc:
        return made("multi_compare", "high", cot=True, sc=not is_open)
    if is_exists:
        return made("complex_reason", "high", cot=True, sc=True)
    if is_open:
        return made("closed_lookup", "high", cot=True)
    return made("closed_lookup", "low")
