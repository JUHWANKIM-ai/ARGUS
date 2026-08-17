"""Query term extraction and per-hop reformulation. No LLM calls; all rules."""
from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field

# Terms common to every document and therefore without discriminating power,
# chosen by measured document frequency rather than by hand.
# See docs/design-rationale.md#query-stopwords
STOPWORDS = {
    "공급계약", "단일판매", "판매공급계약", "계약금액", "체결",
    "매출액대비", "매출액", "최근매출액", "계약상대", "계약기간",
    "주요경영사항", "투자판단", "신규시설투자", "공시", "보고서",
    "얼마", "무엇", "어디", "언제", "누구", "인가", "인지", "이며",
    "얼마인가", "정리해줘", "설명해줘", "알려줘", "존재하는가",
}
_TOKEN = re.compile(r"[0-9]+(?:[,.][0-9]+)*|[가-힣]+|[A-Za-z]+")
_JOSA = re.compile(r"(이|가|은|는|을|를|의|에|에서|으로|로|와|과|도|만|이라|라)$")


def _strip_josa(word: str) -> str:
    if len(word) <= 2:
        return word
    return _JOSA.sub("", word)


def extract_terms(question: str) -> list[str]:
    terms: list[str] = []
    for raw in _TOKEN.findall(question or ""):
        word = _strip_josa(raw)
        if len(word) < 2:
            continue
        if word in STOPWORDS:
            continue
        # Also drop compounds ending in a stopword, left over from tokenising.
        if any(word.endswith(s) and word != s for s in STOPWORDS if len(s) >= 4):
            continue
        if word not in terms:
            terms.append(word)
    return terms


# --- Query reformulation ----------------------------------------------------
# When retrieval falls short, conditions are released one at a time. What
# widens is the filters; the query terms narrow instead, because the noise the
# period and company filters were absorbing lands on the query once they go.
# See docs/design-rationale.md#query-stopwords

#: Strategy per hop. Hop 0 is the initial search and spends no budget; the
#: three reformulations each spend one hop.
STRATEGIES = ("narrow", "relax_period", "corp_only", "global")

#: Depth per hop. Releasing filters widens the candidate set, with no reason to
#: exceed the evidence cap.
TOP_K = (5, 8, 10, 10)

#: Terms whose measured document frequency exceeds 0.30, which cannot separate
#: documents even when present. Numeric tokens are removed by the pattern
#: below.
#: See docs/design-rationale.md#query-stopwords
COMMON_TERMS = frozenset({"계약", "상대", "금액", "매출", "대비", "정정"})

_NUMERIC = re.compile(r"^[0-9][0-9,.]*$")
#: Verb endings left on a token. Stripping them routes each word to the
#: stopword set or the discriminating set correctly.
_TAIL = re.compile(r"(하는|되는|이는|한|된|인|는|나|줘|해)$")


def _stem(word: str) -> str:
    if len(word) <= 2:
        return word
    stem = _TAIL.sub("", word)
    return stem if len(stem) >= 2 else word


def core_terms(terms: Sequence[str]) -> list[str]:
    """Keep only the core terms used when filters are released. An empty result
    stays empty rather than guessing."""
    out: list[str] = []
    for raw in terms:
        if _NUMERIC.match(raw):
            continue
        word = _stem(raw)
        if word in STOPWORDS or word in COMMON_TERMS:
            continue
        if word not in out:
            out.append(word)
    return out


def relax_period(period: dict) -> dict:
    """Widen the period filter by one step: year-month, then year, then none."""
    if "ym" in period:
        return {"year": int(str(period["ym"])[:4])}
    return {}


# --- Multi-point query decomposition ----------------------------------------
# The hop ladder only widens one query, and widening cannot retrieve one
# company's filing for one period and another's for a different period at the
# same time. Such questions are split into cells, and each cell takes fewer
# results because the golden record ranks first within its own cell.
# See docs/design-rationale.md#time-point-split

#: Cap on time-point axes. Questions with more than one always have exactly
#: two; three is headroom, and above it the cells multiply by the company count
#: and exceed the step cap.
MAX_TIME_POINTS = 3

#: Depth per decomposition cell. The golden record ranks first within a cell,
#: so this suffices and the evidence does not grow with the cell count.
SPLIT_TOP_K = 3

_YM_ALL = re.compile(r"(20\d{2})\s*년\s*(1[0-2]|0?[1-9])\s*월")


def time_points(question: str, years: Sequence[int] = ()) -> list[dict]:
    """Distinct points in time the question names, or an empty list below two,
    in which case nothing is split.

    Year-month takes priority: collapsing two months of different years into
    years puts both reports in one cell, where ranking buries one of them
    again. With a single year-month present, the year axis is used instead.
    See docs/design-rationale.md#time-point-split
    """
    yms = list(dict.fromkeys(f"{m.group(1)}{int(m.group(2)):02d}"
                             for m in _YM_ALL.finditer(question or "")))
    if len(yms) >= 2:
        return [{"ym": v} for v in yms[:MAX_TIME_POINTS]]
    picked = list(dict.fromkeys(int(y) for y in years))
    if len(picked) >= 2:
        return [{"year": y} for y in picked[:MAX_TIME_POINTS]]
    return []


@dataclass(frozen=True)
class HopPlan:
    """Retrieval plan for one hop, built by rules with no LLM call."""

    hop: int
    strategy: str
    terms: tuple[str, ...] = ()
    period: dict = field(default_factory=dict)
    use_corp: bool = True
    top_k: int = 5
    #: Decomposition cells. Empty means a single search on the period.
    periods: tuple[dict, ...] = ()

    @property
    def query(self) -> str:
        return " ".join(self.terms)


def plan_hops(question: str, period: dict,
              fallback_terms: Sequence[str] = (),
              periods: Sequence[dict] = ()) -> list[HopPlan]:
    """Four hops, narrowest first.

    hop 0  full vocabulary, period filter and company filter
    hop 1  period filter widened by one step
    hop 2  period filter released, company filter kept, query reduced to core
           terms
    hop 3  company filter released as well, the last resort

    With more than one period, only hop 0 is split into cells. Multiplying the
    widening hops as well would exceed the step cap, and once widened the
    period leaves the filter anyway, which makes the split pointless.
    See docs/design-rationale.md#time-point-split
    """
    terms = list(extract_terms(question))
    core = core_terms(terms)
    fallback = [t for t in fallback_terms if t]
    if not core:
        # No content words remain in the question; the company name is the
        # only signal left.
        core = fallback or terms
        terms = terms + [t for t in fallback if t not in terms]
    if not terms:
        terms = core
    cells = tuple(dict(p) for p in periods) if len(periods) >= 2 else ()
    head_k = SPLIT_TOP_K if cells else TOP_K[0]
    return [
        HopPlan(0, "narrow", tuple(terms), dict(period), True, head_k, cells),
        HopPlan(1, "relax_period", tuple(terms), relax_period(period), True, TOP_K[1]),
        HopPlan(2, "corp_only", tuple(core), {}, True, TOP_K[2]),
        HopPlan(3, "global", tuple(core), {}, False, TOP_K[3]),
    ]


# --- Periodic filing query plan ---------------------------------------------
# Periodic filings retrieve on different axes from exchange filings: the fiscal
# year is not the receipt year, the bodies of a company's quarterly, half-year
# and annual reports are nearly identical so the report subtype does the
# discriminating, and the table of contents is common across companies, which
# makes choosing a path deterministically possible.
# See docs/design-rationale.md#periodic-query-axes

#: Report wording mapped to a document subtype.
REPORT_SUBTYPES = (
    ("annual", re.compile(r"사업\s*보고서")),
    ("half", re.compile(r"반기\s*보고서")),
    ("quarter", re.compile(r"분기\s*보고서")),
)

#: Only a year attached directly to the report phrase counts as the fiscal
#: year. Taking the filing year instead retrieves the opposite document.
_REPORT_YEAR = re.compile(
    r"(20\d{2})\s*년?\s*(?:도)?\s*(?:\d\s*분기\s*)?(?:사업|반기|분기)\s*보고서")
#: Submission or receipt wording, which belongs to the receipt-date axis.
_FILED = re.compile(r"(20\d{2})\s*년\s*(1[0-2]|0?[1-9])\s*월"
                    r"(?:\s*\d{1,2}\s*일)?\s*(?:에\s*)?(?:제출|공시|접수)")

#: Which quarter. The year pattern above skips this position without capturing
#: it, which is where the defect began: without the quarter number, two
#: quarters of the same year are indistinguishable and a question naming a
#: quarter that does not exist passes as one that does.
#: See docs/design-rationale.md#periodic-query-axes
_QUARTER = re.compile(r"([1-4])\s*분기")

#: Strip the ordinal prefix from a table-of-contents title, including the
#: mistyped variants that occur in the source.
_TOC_NUMBER = re.compile(r"^\s*(?:[IVXLC]+|\d+(?:-\d+)?)\s*[.,)]\s*")
_TOC_WORD = re.compile(r"[가-힣A-Za-z]{2,}")

#: Fiscal-term wording. It is a fiscal-year-scale expression, so with no
#: report subtype stated it points at the annual report. An explicit subtype
#: wins over it.
#: See docs/design-rationale.md#periodic-query-axes
_GISU = re.compile(r"제\s*\d+\s*기")


def report_hints(question: str) -> dict:
    """Extract the periodic axes from a question: report subtype, fiscal year
    and receipt year-month."""
    out: dict[str, str] = {}
    for name, pattern in REPORT_SUBTYPES:
        if pattern.search(question or ""):
            out["doc_subtype"] = name
            break
    if "doc_subtype" not in out and _GISU.search(question or ""):
        out["doc_subtype"] = "annual"
    year = _REPORT_YEAR.search(question or "")
    if year:
        out["base_year"] = year.group(1)
    filed = _FILED.search(question or "")
    if filed:
        out["filed"] = f"{filed.group(1)}{int(filed.group(2)):02d}"
    quarter = _QUARTER.search(question or "")
    if quarter:
        out["quarter"] = quarter.group(1)
    return out


#: Submission and form vocabulary removed before matching a table-of-contents
#: title. Left in, the form rather than the content chooses the path, and the
#: paths it chooses are boilerplate present in every document.
#: See docs/design-rationale.md#toc-path-selection
_PATH_NOISE = re.compile(r"사업\s*보고서|반기\s*보고서|분기\s*보고서|보고서|보고자"
                         r"|공시|제출|기재|확인|작성")

#: Paired path carrying the same figure. Revenue and profit figures are split
#: across two paths, and one question has two companies putting the same figure
#: in different ones, so reading a single path loses one of them entirely.
#: See docs/design-rationale.md#toc-path-selection
SALES_PATH = "II. 사업의 내용 > 4. 매출 및 수주상황"
SUMMARY_PATH = "III. 재무에 관한 사항 > 1. 요약재무정보"
_SUMMARY_METRIC = re.compile(r"매출액|매출\s*및|영업이익|당기순이익|순이익"
                             r"|자산총계|부채총계|자본총계")

#: Heading section of a revised filing. It appears in too few documents to
#: pass the common-path threshold, yet several golden records live there, so it
#: is reinstated as a candidate for revision questions only.
CORRECTION_PATH = "정 정 신 고 (보고)"
_ASK_CORRECTION = re.compile(r"정정|수정|최종\s*금액|반영\s*후|어떻게\s*바뀌")

#: Path carrying corporate registration numbers. Word overlap can never find
#: it, because the question wording and the section title share no characters,
#: so the same paired-path device is used. Without this rule, opening the
#: identifier gate still yields no answer: the gate only releases routing, and
#: retrieval has to reach this path for the value to enter the evidence.
#: See docs/design-rationale.md#toc-path-selection
AFFILIATE_PATH = "XII. 상세표 > 2. 계열회사 현황(상세)"
_ASK_CORP_NUMBER = re.compile(r"법인등록번호|사업자등록번호")


def _twin_paths(question: str, pool) -> list[str]:
    """Paired paths reinstated by rule. Nothing is added unless the question
    asks on that axis.

    The revision heading comes first: for a revision question that section is
    itself the answer, and placing it after the figure pair pushed it out of
    the hop 0 candidates.
    See docs/design-rationale.md#toc-path-selection
    """
    out: list[str] = []
    if _ASK_CORRECTION.search(question or ""):
        out.append(CORRECTION_PATH)          # 모집단 밖이라 pool 검사를 하지 않는다
    if _ASK_CORP_NUMBER.search(question or "") and AFFILIATE_PATH in pool:
        out.append(AFFILIATE_PATH)
    if _SUMMARY_METRIC.search(question or ""):
        out += [p for p in (SALES_PATH, SUMMARY_PATH) if p in pool]
    return out


def path_hints(question: str, paths, limit: int = 3) -> list[str]:
    """Choose candidate paths by word overlap between the question and the
    table-of-contents titles.

    Only whole words of the title are matched against the question, with a
    trailing particle stripped. Submission and form vocabulary is removed
    first.

    Overlap alone cannot solve a figure split across paths, so paired paths are
    inserted by rule immediately after the top candidate, which does not
    displace it.
    See docs/design-rationale.md#toc-path-selection
    """
    text = _PATH_NOISE.sub(" ", question or "")
    scored: list[tuple[float, int, str]] = []
    for path in paths:
        title = _TOC_NUMBER.sub("", path.split(" > ")[-1])
        words = _TOC_WORD.findall(title)
        if not words:
            continue
        hit: list[str] = []
        for word in words:
            for candidate in (word, word[:-1] if len(word) > 2 else None):
                if candidate and candidate in text:
                    hit.append(candidate)
                    break
        if not hit:
            continue
        span = max(sum(len(w) for w in words), 1)
        scored.append((sum(len(w) for w in hit) / span, sum(len(w) for w in hit), path))
    scored.sort(key=lambda x: (-x[0], -x[1], len(x[2])))
    out = [p for _, _, p in scored[:limit]]
    for offset, twin in enumerate(_twin_paths(question, paths)):
        if twin not in out:
            out.insert(min(1 + offset, len(out)), twin)
    return out


#: A hop carrying a path filter already has a narrow candidate set, so taking
#: more of it is cheap.
SECTION_TOP_K = (8, 10, 8, 10)
#: Position names for the tuple above. It has no code reference and looks
#: dead, but without it that tuple is four unexplained numbers and the hop
#: sequence cannot be read here. The order has to match the strategy names
#: used when the plans are built.
SECTION_STRATEGIES = ("toc_narrow", "toc_corp", "doc_narrow", "corp_only")

#: Paths hop 0 considers at once. Two suffices, since the questions that need
#: a pair need exactly two. Three splits one company's evidence slots below one
#: per path, cutting a golden record that ranks second within its cell.
#: See docs/design-rationale.md#toc-path-selection
MAX_SECTION_PATHS = 2


@dataclass(frozen=True)
class SectionPlan:
    """Retrieval plan for one periodic hop. Its filters split into exact-match
    axes and prefix axes."""

    hop: int
    strategy: str
    terms: tuple[str, ...] = ()
    filters: dict = field(default_factory=dict)
    prefix: dict = field(default_factory=dict)
    paths: tuple[str, ...] = ()
    top_k: int = 8

    @property
    def query(self) -> str:
        return " ".join(self.terms)


def plan_section_hops(question: str, hints: dict, paths: Sequence[str],
                      years: Sequence[int] = ()) -> list[SectionPlan]:
    """Four periodic hops, narrowest first.

    hop 0  two path candidates, report subtype and fiscal year, split into
           cells when two axes are present
    hop 1  every path candidate, period and subtype released
    hop 2  paths released, subtype and fiscal year kept, for a wrong path guess
    hop 3  company filter only, the last resort
    """
    terms = list(extract_terms(question))
    core = core_terms(terms) or terms
    doc_filters: dict[str, str] = {}
    if hints.get("doc_subtype"):
        doc_filters["doc_subtype"] = hints["doc_subtype"]
    base_year = hints.get("base_year") or (str(years[0]) if len(years) == 1 else None)
    if base_year:
        doc_filters["base_year"] = base_year

    # A receipt date stated in the question is the safest axis. The fiscal
    # year carries an inference, and a question that names only the submission
    # year would filter to the opposite document.
    # See docs/design-rationale.md#periodic-query-axes
    filed = {"rcept_dt": hints["filed"]} if hints.get("filed") else {}
    primary = ({}, filed) if filed else (doc_filters, {})

    # The fiscal-year axis is not split. A question can name a fiscal term
    # whose figures live in a later report, with no report for that year in the
    # corpus at all, so splitting by year empties one cell entirely. The same
    # question recovers both golden records from the subtype and the path
    # alone.
    # See docs/design-rationale.md#periodic-query-axes
    return [
        SectionPlan(0, "toc_narrow", tuple(terms), dict(primary[0]), dict(primary[1]),
                    tuple(paths[:MAX_SECTION_PATHS]), SECTION_TOP_K[0]),
        SectionPlan(1, "toc_corp", tuple(terms), {}, {}, tuple(paths[:3]),
                    SECTION_TOP_K[1]),
        SectionPlan(2, "doc_narrow", tuple(core), dict(doc_filters), {}, (),
                    SECTION_TOP_K[2]),
        SectionPlan(3, "corp_only", tuple(core), {}, {}, (), SECTION_TOP_K[3]),
    ]
