"""Filing tables to normalised records, following the schema in the convention.

Tables look different per document group, and flattening them into one schema
empties whole fields. The extraction paths are therefore split by group.
See docs/design-rationale.md#table-row-shapes
"""
from __future__ import annotations

import re
from pathlib import Path

from argus.config import settings
from argus.corpus.dates import parse_date
from argus.corpus.html_doc import extract_rows, extract_title, read_document

_NUM = re.compile(r"-?[\d,]+(?:\.\d+)?")
_TITLE_DATE = re.compile(r"^\(\d{4}[.\-]\d{2}[.\-]\d{2}\)\s*")
_WS_ALL = re.compile(r"\s+")

# Subtypes whose extraction depends on labels specific to the contract form.
CONTRACT_SUBTYPES = {"단일판매공급계약체결", "단일판매공급계약해지"}
# Subtypes whose amount is recovered by widening the label set. One carries an
# explicit investment amount cell throughout; the other keeps the amount inside
# free text.
INVESTMENT_SUBTYPES = {"신규시설투자등", "투자판단관련주요경영사항"}

# --- Free-text amounts ------------------------------------------------------
# This form has no amount cell; the amount sits inside a sentence.
# See docs/design-rationale.md#freetext-amounts
_MAGNITUDE = {"조": 10**12, "억": 10**8, "천만": 10**7, "백만": 10**6, "만": 10**4}
_FREE_AMOUNT = re.compile(
    r"(계약|공급|수주|투자|취득|처분|양수|양도|거래|매매|발행|조달|출자|인수)?\s*"
    r"(?:금\s*액|규모|대금|가액)"
    r"[^0-9<>\n]{0,10}?"
    r"(\d[\d,]*(?:\.\d+)?)\s*"
    r"(조|억|천만|백만|만)?\s*"
    r"(원|달러|USD|\$)?"
)
# A currency symbol before the figure marks a foreign amount too. Looking only
# after the figure misses those.
_FOREIGN_BEFORE = re.compile(r"(?i)(?:\$|USD|US\s*\$|달러|유로|EUR|엔|위안)\s*$")
_FOREIGN_AFTER = {"달러", "USD", "$"}
# Below a million, a figure is more likely a quantity or a count than an
# amount.
_MIN_AMOUNT = 10**6

# A compound figure writing a large unit and a smaller one in sequence.
# Stopping at the first unit drops the remainder entirely.
_AMOUNT_TAIL = re.compile(r"\s*(\d[\d,]*(?:\.\d+)?)\s*(억|천만|백만|만)")
_WON_TAIL = re.compile(r"\s*원")


def _compound_tail(cell: str, pos: int, magnitude: str) -> float:
    """Convert the trailing part of a compound figure to base units, else 0.

    It is appended only when both conditions hold: the units descend, and the
    final piece ends in the currency word. Without the second, a share count
    following an amount would be added as money, which is worse than an empty
    field.
    See docs/design-rationale.md#freetext-amounts
    """
    extra, scale, cursor = 0.0, _MAGNITUDE[magnitude], pos
    while (part := _AMOUNT_TAIL.match(cell, cursor)) is not None:
        step = _MAGNITUDE[part.group(2)]
        if step >= scale:
            return 0.0
        try:
            extra += float(part.group(1).replace(",", "")) * step
        except ValueError:
            return 0.0
        scale, cursor = step, part.end()
        if _WON_TAIL.match(cell, cursor):
            return extra
    return 0.0


def free_text_amount(cells: list[str]) -> tuple[int, str] | None:
    """Extract a domestic-currency amount from free text, as (amount, kind), or
    None.

    Cells are read from the end, because a revision table runs (item, before,
    after) and reading forward picks the pre-revision figure.

    Foreign amounts are discarded: reading one as domestic makes the figure
    wholly wrong, which is worse than an empty field.
    See docs/design-rationale.md#freetext-amounts
    """
    for cell in reversed(cells):
        for match in _FREE_AMOUNT.finditer(cell):
            kind, number, magnitude, currency = match.groups()
            currency = (currency or "").strip()
            if currency in _FOREIGN_AFTER:
                continue
            if _FOREIGN_BEFORE.search(cell[:match.start(2)][-12:]):
                continue
            if not magnitude and currency != "원":
                continue          # 단위가 없으면 금액이라 단정할 수 없다
            try:
                value = float(number.replace(",", ""))
            except ValueError:
                continue
            if magnitude:
                value = value * _MAGNITUDE[magnitude] + _compound_tail(
                    cell, match.end(3), magnitude)
            if value < _MIN_AMOUNT:
                continue
            return int(value), f"{kind or ''}금액"
    return None


def _to_number(text: str) -> float | None:
    match = _NUM.search(text or "")
    if not match:
        return None
    try:
        return float(match.group(0).replace(",", ""))
    except ValueError:
        return None


def _clean(value: str | None) -> str | None:
    """Source tables fill an empty value with a dash. Normalise it to None."""
    if value is None:
        return None
    stripped = value.strip()
    return None if stripped in {"-", "–", "—", ""} else stripped


def _labels(row: list[str]) -> str:
    """The label portion of a row, excluding the final value cell, with
    whitespace removed.

    Searching the whole row for a keyword mistakes a prose value for a label,
    which emptied a large number of records. Whitespace is removed because
    forms differ in how they space the same label.
    See docs/design-rationale.md#table-row-shapes
    """
    return _WS_ALL.sub("", " ".join(row[:-1]))


def _rows_matching(rows: list[list[str]], *keywords: str):
    """Rows whose label contains every keyword, in order."""
    for row in rows:
        if len(row) < 2:
            continue  # 값 칸이 없는 서술 행
        if all(_WS_ALL.sub("", k) in _labels(row) for k in keywords):
            yield row


def _find(rows: list[list[str]], *keywords: str) -> str | None:
    """Value cell of a matching row, continuing to the next candidate when the
    value is empty."""
    for row in _rows_matching(rows, *keywords):
        value = _clean(row[-1])
        if value is not None:
            return value
    return None


def _find_number(rows: list[list[str]], *keywords: str) -> float | None:
    """First row whose value parses as a number, skipping the rest."""
    for row in _rows_matching(rows, *keywords):
        number = _to_number(_clean(row[-1]) or "")
        if number is not None:
            return number
    return None


def _find_date(rows: list[list[str]], *keywords: str):
    """First row whose value parses as a date."""
    for row in _rows_matching(rows, *keywords):
        parsed = parse_date(_clean(row[-1]) or "")
        if parsed is not None:
            return parsed
    return None


_PURE_NUMBER = re.compile(r"^-?\d[\d,]*(?:\.\d+)?$")


def _find_loose(rows: list[list[str]], *keywords: str) -> str | None:
    """Fallback for rows whose value cell is not the last one. Matches on the
    whole row and returns the purely numeric cell.

    A row split by a sub-heading can end in a date rather than the figure, and
    taking the last cell there makes the value wholly wrong.
    See docs/design-rationale.md#table-row-shapes
    """
    normalized = [_WS_ALL.sub("", k) for k in keywords]
    for row in rows:
        joined = _WS_ALL.sub("", " ".join(row))
        if not all(k in joined for k in normalized):
            continue
        for cell in row:
            text = _clean(cell)
            if text and _PURE_NUMBER.match(text):
                return text
    return None


_LABEL_TAIL = re.compile(r"(?:[(（][^)）]*[)）]|[:：])+$")


def _find_beside(rows: list[list[str]], keyword: str) -> str | None:
    """Read the cell immediately after a label in a row that alternates labels
    and values.

    Taking the last cell in such a row assigns the wrong value to the first
    label. Unit suffixes and colons are stripped from the label before matching
    exactly.
    See docs/design-rationale.md#table-row-shapes
    """
    key = _LABEL_TAIL.sub("", _WS_ALL.sub("", keyword))
    for row in rows:
        for index, cell in enumerate(row[:-1]):
            if _LABEL_TAIL.sub("", _WS_ALL.sub("", cell)) == key:
                value = _clean(row[index + 1])
                if value is not None:
                    return value
    return None


def _find_after(rows: list[list[str]], anchor: tuple[str, ...],
                keywords: tuple[str, ...], span: int = 3) -> str | None:
    """Find a value in the few rows immediately after an anchor row, for
    siblings split off by a row span.

    A sibling row carries no heading label, so the label path cannot reach it,
    and searching for the sibling label alone would match anywhere in the
    document.
    See docs/design-rationale.md#table-row-shapes
    """
    normalized_anchor = [_WS_ALL.sub("", k) for k in anchor]
    normalized = [_WS_ALL.sub("", k) for k in keywords]
    for index, row in enumerate(rows):
        if len(row) < 2 or not all(k in _labels(row) for k in normalized_anchor):
            continue
        for sibling in rows[index + 1: index + 1 + span]:
            if len(sibling) >= 2 and all(k in _labels(sibling) for k in normalized):
                return _clean(sibling[-1])
    return None


def _doc_path(doc: dict) -> Path:
    folder = settings().corpus_root / doc["file_path"]
    xml = sorted(folder.glob("*.xml"))
    if not xml:
        raise FileNotFoundError(f"XML 없음: {folder}")
    return xml[0]


#: Cap on a single field value. A prose cell carried whole would blow up the
#: evidence.
FIELD_MAX_CHARS = 90
#: Cap on fields one record emits as markdown.
FIELD_MAX_COUNT = 12


def _shorten(value: str | None) -> str | None:
    if value is None:
        return None
    return value if len(value) <= FIELD_MAX_CHARS else value[:FIELD_MAX_CHARS].rstrip() + "…"


def _collect(rows: list[list[str]], spec, limit: int = FIELD_MAX_COUNT) -> dict[str, str]:
    """Collect values per (display label, keywords). Absent fields are omitted.

    Filling an absent field with a dash only lengthens the markdown and makes
    the model read it as a value that exists.
    """
    fields: dict[str, str] = {}
    for label, keywords in spec:
        if len(fields) >= limit:
            break
        value = _find(rows, *keywords)
        if value is not None:
            fields[label] = _shorten(value)
    return fields


def _base_record(doc: dict, rows: list[list[str]]) -> dict:
    """Record skeleton with the same keys regardless of document group, since
    consumers assume those keys."""
    return {
        "doc_id": doc["doc_id"],
        "rcept_no": doc["rcept_no"],
        "rcept_dt": doc["rcept_dt"],
        "corp_code": doc["corp_code"],
        "corp_name": doc["corp_name"],
        "sector": doc["sector"],
        "doc_subtype": doc["doc_subtype"],
        "is_correction": bool(doc["is_correction"]),
        "amount": None,
        "amount_type": None,
        "ratio": None,
        "ratio_base": None,
        "counterparty": None,
        "period_start": None,
        "period_end": None,
        "event_date": None,
        "reserved_reason": None,
        "note": None,
        "related": [],
        "form": None,
        "fields": {},
        "corrections": [],
        "schema_matched": False,
        "withheld": False,
        "raw_rows": rows[:40],
    }


#: First item of an exchange form body, where the revision table ends. The
#: body-start marker used for the other groups must not be reused: it appears
#: in no exchange form, so the range would run to the end of the document and
#: the body would be lost entirely.
_KIND_ITEM1 = re.compile(r"^\s*1\s*[.．]\s*\S")


def exchange_correction_span(rows: list[list[str]]) -> tuple[int, int] | None:
    """Span of the revision table in an exchange filing, or None.

    Every revision carries the before/after header row and no original filing
    does, so detection is exact. The body start is searched after that header,
    because a revision-metadata row that also begins with a numeral sits before
    it.
    """
    for index, row in enumerate(rows):
        flat = _WS_ALL.sub("", "".join(row))
        if "정정전" in flat and "정정후" in flat:
            end = next((i for i in range(index + 1, len(rows))
                        if rows[i] and _KIND_ITEM1.match(rows[i][0])), len(rows))
            return index, end
    return None


def exchange_body_rows(rows: list[list[str]]) -> list[list[str]]:
    """Form body with the revision table removed. Exchange field extraction has
    to happen here.

    A revision row is (item, before, after) and its value cells are whole prose
    blocks. Treating everything but the last cell as the label makes the
    before block the label, so a keyword matches inside it and the value taken
    is the first number of the after block.

    Amounts go wrong on the same row in the other direction: reading cells from
    the end reaches the after value first, but when that cell omits the
    currency word the value is rejected and the reading falls through to the
    before cell.
    See docs/design-rationale.md#table-row-shapes
    """
    span = exchange_correction_span(rows)
    return rows if span is None else rows[:span[0]] + rows[span[1]:]


# --- Free-text body for forms the schema does not match ---------------------
# An empty structured cell does not mean the value is absent; it means the
# value is inside prose. One form has no amount cell at all, and a contract
# form that withholds its amount pushes the real content into a later
# free-text item.
#
# Only three labels are read, since those are the only places this form puts
# prose. The rest merely lengthen the evidence.
# See docs/design-rationale.md#freetext-amounts
_FREETEXT_LABEL = re.compile(r"^\s*\d+\s*[.．]\s*(제\s*목|주요\s*내용|기타\s*\S*)")

#: Cap on one free-text cell, deliberately larger than the structured field
#: cap: this cell is body text rather than a summary, and target amounts have
#: been measured near its end.
#:
#: Raising it further would recover one more question, and it was measured and
#: rejected: the record in question is gated elsewhere anyway, and the increase
#: rewrites the markdown of most affected records. That is half a corpus
#: rewrite for one question.
#: See docs/design-rationale.md#evidence-slot-vs-line
FREETEXT_MAX_CHARS = 300
#: Cap on free-text cells one record carries, to keep the evidence bounded.
FREETEXT_MAX_CELLS = 4


def free_text_items(rows: list[list[str]]) -> list[tuple[str, str]]:
    """Free-text body as (label, value) pairs, with the revision table removed.

    A label row comes in two shapes: the value on the same row, or a label
    whose value is on the next row. Both occur often enough that both have to
    be accepted.

    The revision table is removed first for the reason given there: reading it
    directly puts the superseded value into the evidence.
    """
    body = exchange_body_rows(rows)
    items: list[tuple[str, str]] = []
    used: set[int] = set()
    for index, row in enumerate(body):
        if len(items) >= FREETEXT_MAX_CELLS:
            break
        if index in used or not row or not _FREETEXT_LABEL.match(row[0]):
            continue
        label = _WS_ALL.sub(" ", _FREETEXT_LABEL.sub(r"\1", row[0])).strip()
        values = [c for c in row[1:] if _clean(c)]
        if not values:
            nxt = body[index + 1] if index + 1 < len(body) else None
            if not nxt or _FREETEXT_LABEL.match(nxt[0] or ""):
                continue
            values = [c for c in nxt if _clean(c)]
            used.add(index + 1)
        for value in values[:1]:            # 라벨 하나에 값 하나. 짝이 늘면 근거가 부푼다
            items.append((label, _clip_free(value)))
    return items


def _clip_free(value: str) -> str:
    text = _WS_ALL.sub(" ", value).strip()
    return text if len(text) <= FREETEXT_MAX_CHARS else text[:FREETEXT_MAX_CHARS] + "…"


def normalize_exchange(doc: dict) -> dict:
    html = read_document(_doc_path(doc))
    rows = extract_rows(html)
    body = exchange_body_rows(rows)           # 정정 표는 필드 추출에서 반드시 뺀다
    title = extract_title(html)

    # The termination form and the investment form each label their amount
    # differently from the conclusion form. The label difference alone left a
    # group of records entirely empty.
    amount, amount_type = None, "계약금액"
    for label in ("계약금액", "해지금액", "투자금액"):
        found = _find_number(body, label)
        if found is not None:
            amount, amount_type = found, label
            break

    # Still absent, so read the free text.
    if amount is None:
        for row in body:
            free = free_text_amount(row)
            if free is not None:
                amount, amount_type = free
                break

    # The two forms take the ratio against different bases, so the recorded
    # base has to change with the label; otherwise one ratio is read as the
    # other.
    ratio, ratio_base = _find_number(body, "매출액대비"), "매출액"
    if ratio is None:
        equity_ratio = _find_number(body, "자기자본대비")
        if equity_ratio is not None:
            ratio, ratio_base = equity_ratio, "자기자본"
    start = _find_date(body, "시작일")
    end = _find_date(body, "종료일")
    # The date label differs between the conclusion and termination forms.
    # With neither present, fall back to the receipt date.
    event = (_find_date(body, "계약", "일자")
             or _find_date(body, "수주", "일자")
             or _find_date(body, "해지일자")
             or parse_date(doc["rcept_dt"]))

    # The document title holds only the form name and is useless as a search
    # term. The substance is in the contract-name row, which is promoted to the
    # title so that a query naming the goods can match.
    subject = (_find(body, "체결계약명")
               or _find(body, "해지계약명")
               or _find(body, "계약명")
               or _find(body, "투자대상")
               or _find(body, "사고발생내용"))

    raw_title = title.split("/")[-1].strip() if title else doc["report_nm"]
    form_name = _TITLE_DATE.sub("", raw_title).strip() or doc["report_nm"]
    clean_title = f"{form_name} — {subject}" if subject else form_name

    # An amount cell present but dashed with a withholding note means the
    # filer withheld the value. That is the source lacking a value rather than
    # the parser failing to read one, so it is flagged separately and must not
    # be recorded as a schema match.
    reserved_reason = _find(body, "유보사유")
    withheld = amount is None and reserved_reason is not None

    record = _base_record(doc, rows)
    record.update({
        "title": clean_title,
        "form": doc["doc_subtype"],
        "amount": int(amount) if amount is not None else None,
        # No amount found means no label either. The loop initialiser used to
        # survive, leaving a form with no amount cell carrying an amount label
        # for an amount that does not exist. The markdown is unaffected, since
        # the amount line returns early on a missing amount.
        "amount_type": amount_type if amount is not None else None,
        "ratio": ratio,
        "ratio_base": ratio_base,
        "counterparty": _find(body, "계약상대"),
        "period_start": start.isoformat() if start else None,
        "period_end": end.isoformat() if end else None,
        "event_date": event.isoformat() if event else None,
        "reserved_reason": reserved_reason,
        "schema_matched": (doc["doc_subtype"] in CONTRACT_SUBTYPES | INVESTMENT_SUBTYPES
                           and amount is not None),
        # Distinguish why a value is absent: withheld means the source has
        # none, while an unmatched schema without withholding is a genuine
        # extraction miss.
        "withheld": withheld,
    })
    return record


# =============================================================================
# Major filings, 28 forms.
# =============================================================================
# The manifest subtype is null for every one of them; the form appears only in
# the parentheses of the report name. Fields differ per form, so one schema
# cannot cover them.

_FORM_TAIL = re.compile(r"\(([^()]*)\)\s*$")
_CORRECTION_TAG = re.compile(r"^\s*\[기재정정\]\s*")


def major_form(report_nm: str | None) -> str:
    """Extract the form name from the parentheses of a report name."""
    match = _FORM_TAIL.search(report_nm or "")
    if match:
        return match.group(1).strip()
    return _CORRECTION_TAG.sub("", report_nm or "").strip()


#: Funding-purpose block shared by the bond and share-issue forms. Its six
#: sub-items are split by a row span so the heading label appears only on the
#: first row, and they are therefore found by sub-label alone.
_FUNDING = (
    ("시설자금", ("시설자금",)),
    ("영업양수자금", ("영업양수자금",)),
    ("운영자금", ("운영자금",)),
    ("채무상환자금", ("채무상환자금",)),
    ("타법인 증권취득자금", ("타법인", "취득자금")),
    ("기타자금", ("기타자금",)),
)
_FUNDING_LABELS = tuple(label for label, _ in _FUNDING)

_BOND = (
    ("사채의 종류", ("사채의종류",)),
    *_FUNDING,
    ("표면이자율", ("표면이자율",)),
    ("만기이자율", ("만기이자율",)),
    ("사채만기일", ("사채만기일",)),
    ("사채발행방법", ("사채발행방법",)),
)
_TREASURY_HOLDING = (("자기주식 보유(보통주식)", ("배당가능", "보통주식")),)

#: Form to ((display label, label keywords), ...). A field with no value does
#: not enter the record.
MAJOR_FIELDS: dict[str, tuple[tuple[str, tuple[str, ...]], ...]] = {
    "유상증자결정": (
        ("신주의 종류와 수(보통주식)", ("신주의종류와수", "보통주식")),
        ("1주당 액면가액", ("1주당액면가액",)),
        ("증자전 발행주식총수", ("증자전발행주식총수", "보통주식")),
        *_FUNDING,
        ("증자방식", ("증자방식",)),
        ("신주배정기준일", ("신주배정기준일",)),
    ),
    "무상증자결정": (
        ("신주의 종류와 수(보통주식)", ("신주의종류와수", "보통주식")),
        ("1주당 신주배정 주식수", ("1주당신주배정",)),
        ("신주배정기준일", ("신주배정기준일",)),
    ),
    "전환사채권발행결정": (
        *_BOND,
        ("전환비율", ("전환비율",)),
        ("전환가액", ("전환가액", "원/주")),
    ),
    "교환사채권발행결정": (
        *_BOND,
        ("교환비율", ("교환비율",)),
        ("교환가액", ("교환가액", "원/주")),
    ),
    "상각형조건부자본증권발행결정": _BOND,
    "자본으로인정되는채무증권발행결정": _BOND,
    "자기주식취득결정": (
        ("취득예정주식(보통주식)", ("취득예정주식", "보통주식")),
        ("취득목적", ("취득목적",)),
        ("취득방법", ("취득방법",)),
        ("위탁투자중개업자", ("위탁투자중개업자",)),
        *_TREASURY_HOLDING,
    ),
    "자기주식처분결정": (
        ("처분예정주식(보통주식)", ("처분예정주식", "보통주식")),
        ("처분 대상 주식가격", ("처분대상주식가격", "보통주식")),
        ("처분목적", ("처분목적",)),
        ("처분방법", ("처분방법",)),
        *_TREASURY_HOLDING,
    ),
    "자기주식취득신탁계약체결결정": (
        ("계약목적", ("계약목적",)),
        ("계약체결기관", ("계약체결기관",)),
        ("계약체결 예정일자", ("계약체결예정일자",)),
    ),
    "자기주식취득신탁계약해지결정": (
        ("해지목적", ("해지목적",)),
        ("해지기관", ("해지기관",)),
        ("해지예정일자", ("해지예정일자",)),
    ),
    # The merger-method value is a statutory citation that only lengthens the
    # evidence; the merger-form row in the same table carries the substance.
    "회사합병결정": (
        ("합병형태", ("합병형태",)),
        ("합병목적", ("합병목적",)),
        ("합병비율", ("합병비율",)),
        ("합병상대회사", ("합병상대회사", "회사명")),
        ("합병신주(보통주식)", ("합병신주의종류와수", "보통주식")),
        ("합병기일", ("합병기일",)),
    ),
    "회사분할결정": (
        ("분할 후 존속회사", ("분할후존속회사", "회사명")),
        ("분할설립회사", ("분할설립회사", "회사명")),
        ("분할목적", ("분할목적",)),
        ("분할비율", ("분할비율",)),
        ("분할기일", ("분할기일",)),
    ),
    "회사분할합병결정": (
        ("합병형태", ("합병형태",)),
        ("분할합병 목적", ("분할합병목적",)),
        ("분할합병 기일", ("분할합병기일",)),
    ),
    "주식교환ㆍ이전결정": (
        ("교환ㆍ이전 대상법인", ("교환ㆍ이전대상법인", "회사명")),
        ("교환ㆍ이전 비율", ("교환ㆍ이전비율",)),
        ("교환ㆍ이전 목적", ("교환ㆍ이전목적",)),
        ("교환ㆍ이전일자", ("교환ㆍ이전일자",)),
    ),
    "감자결정": (
        ("감자주식(보통주식)", ("감자주식의종류와수", "보통주식")),
        ("감자비율(보통주식)", ("감자비율", "보통주식")),
        ("감자기준일", ("감자기준일",)),
        ("감자방법", ("감자방법",)),
        ("감자사유", ("감자사유",)),
    ),
    "타법인주식및출자증권양수결정": (
        ("발행회사", ("발행회사", "회사명")),
        ("양수주식수", ("양수주식수",)),
        ("양수후 지분비율", ("지분비율",)),
        ("양수목적", ("양수목적",)),
        ("양수예정일자", ("양수예정일자",)),
    ),
    "타법인주식및출자증권양도결정": (
        ("발행회사", ("발행회사", "회사명")),
        ("양도주식수", ("양도주식수",)),
        ("양도후 지분비율", ("지분비율",)),
        ("양도목적", ("양도목적",)),
    ),
    "유형자산양수결정": (
        ("자산명", ("자산명",)),
        ("양수목적", ("양수목적",)),
        ("양수예정일자", ("양수예정일자", "계약체결일")),
    ),
    "유형자산양도결정": (
        ("자산명", ("자산명",)),
        ("양도목적", ("양도목적",)),
        ("양도예정일자", ("양도예정일자", "계약체결일")),
    ),
    "영업양수결정": (
        ("양수영업", ("1.양수영업",)),
        ("양수영업 주요내용", ("양수영업주요내용",)),
        ("양수목적", ("양수목적",)),
    ),
    "자기전환사채매도결정": (
        ("사채의 종류", ("사채의종류",)),
        ("매도 목적", ("매도목적",)),
        ("전환가액", ("전환가액",)),
    ),
    "제3자의전환사채매수선택권행사": (
        ("사채의 종류", ("사채의종류",)),
        ("행사비율", ("행사비율",)),
        ("전환가액", ("전환가액",)),
    ),
    "소송등의제기": (
        ("사건의 명칭", ("사건의명칭",)),
        ("원고ㆍ신청인", ("원고",)),
        ("청구내용", ("청구내용",)),
        ("관할법원", ("관할법원",)),
        ("제기일자", ("제기일자",)),
    ),
    "영업정지": (
        ("영업정지 분야", ("영업정지분야",)),
        ("영업정지 내용", ("영업정지내용",)),
        ("영업정지사유", ("영업정지사유",)),
    ),
    "해외증권시장주권등상장폐지결정": (
        ("상장폐지주식(보통주식)", ("상장폐지주식", "보통주식")),
        ("상장거래소", ("상장거래소",)),
        ("폐지(예정)일자", ("폐지",)),
        ("폐지사유", ("폐지사유",)),
    ),
    "해외증권시장주권등상장폐지": (
        ("상장거래소", ("상장거래소",)),
        ("상장폐지주식(보통주식)", ("상장폐지주식의종류", "보통주식")),
        ("매매거래종료일", ("매매거래종료일",)),
        ("폐지사유", ("폐지사유",)),
    ),
    "해외증권시장주권등상장결정": (
        ("상장예정주식(보통주식)", ("상장예정주식", "보통주식")),
        ("상장거래소", ("상장거래소",)),
        ("해외상장목적", ("해외상장목적",)),
        ("상장예정일자", ("상장예정일자",)),
    ),
    "해외증권시장주권등상장": (
        ("상장주식(보통주식)", ("상장주식종류및수", "보통주식")),
        ("상장거래소", ("상장거래소",)),
        ("상장일자", ("상장일자",)),
    ),
}

#: 서식 -> (표시할 금액 이름, 라벨 키워드, 형제 행 키워드).
#: 형제 행은 `보통주식/기타주식` 처럼 rowspan 으로 갈라진 같은 항목이라 **더한다**.
#: 여기 없는 서식은 금액 칸이 애초에 없다 (합병·분할·소송·상장폐지 등).
MAJOR_AMOUNT: dict[str, tuple[str, tuple[str, ...], tuple[str, ...] | None]] = {
    "전환사채권발행결정": ("권면(전자등록)총액", ("권면", "총액"), None),
    "교환사채권발행결정": ("권면(전자등록)총액", ("권면", "총액"), None),
    "상각형조건부자본증권발행결정": ("권면(전자등록)총액", ("권면", "총액"), None),
    "자본으로인정되는채무증권발행결정": ("권면(전자등록)총액", ("권면", "총액"), None),
    "자기전환사채매도결정": ("매도금액", ("8.매도금액",), None),
    "제3자의전환사채매수선택권행사": ("행사대가", ("7.행사대가",), None),
    "자기주식취득결정": ("취득예정금액", ("취득예정금액", "보통주식"), ("기타주식",)),
    "자기주식처분결정": ("처분예정금액", ("처분예정금액", "보통주식"), ("기타주식",)),
    "자기주식취득신탁계약체결결정": ("계약금액", ("1.계약금액",), None),
    # 해지 서식의 '해지 후' 칸은 43건 전부 '-' 다(계약이 끝났으니 당연하다).
    # 의미 있는 금액은 '해지 전' 이다.
    "자기주식취득신탁계약해지결정": ("해지 전 계약금액", ("1.계약금액", "해지전"), None),
    "타법인주식및출자증권양수결정": ("양수금액", ("양수금액",), None),
    "타법인주식및출자증권양도결정": ("양도금액", ("양도금액",), None),
    "유형자산양수결정": ("양수금액", ("양수금액",), None),
    "유형자산양도결정": ("양도금액", ("양도금액",), None),
    "영업양수결정": ("양수가액", ("양수가액",), None),
    "영업정지": ("영업정지금액", ("영업정지금액",), None),
}

#: 금액 대비 비율. exchange 와 같은 뜻이라 ratio/ratio_base 로 올린다.
_MAJOR_RATIO = (("자기자본대비", "자기자본"), ("자산총액대비", "자산총액"),
                ("매출액대비", "매출액"), ("총자산대비", "총자산"))

#: 기간 칸이 있는 서식만. 없는 서식에서 아무 `시작일/종료일` 이나 주우면 청약예정일이
#: '기간' 으로 나간다 — 유상증자 근거에 '기간: 2025-05-27 ~ 2025-05-27' 이 그 사고다.
_MAJOR_PERIOD = {
    "자기주식취득결정": "취득예상기간",
    "자기주식처분결정": "처분예정기간",
    "자기주식취득신탁계약체결결정": "2.계약기간",
    "자기주식취득신탁계약해지결정": "해지전계약기간",
    "전환사채권발행결정": "전환청구기간",
    "교환사채권발행결정": "교환청구기간",
}


#: 정정 표가 끝나고 서식 본문이 시작되는 지점의 표지. **첫 칸만** 본다 —
#: 정정 표의 '정정 후' 칸은 블록을 통째로 담고 있어서 그 안에도 '금융위원회 귀중'
#: 이 들어 있다. 행 전체를 보면 정정 표의 첫 행에서 본문이 시작했다고 오판한다.
_BODY_START = ("귀중", "회 사 명")


def _is_body_start(row: list[str]) -> bool:
    return bool(row) and any(mark in row[0] for mark in _BODY_START)


def _correction_span(rows: list[list[str]]) -> tuple[int, int] | None:
    """기재정정 표의 `(머리행, 끝)` 구간. 없으면 None.

    머리행 위치는 문서마다 다르다 — 앞에 '제출이력' 표가 붙으면 8행이 아니라 9행째다
    (삼성SDI 20250409001154). 그래서 고정 창이 아니라 **본문 시작 전까지**를 찾는다.
    """
    body = next((i for i, row in enumerate(rows) if _is_body_start(row)), len(rows))
    for index, row in enumerate(rows[:min(body, 25)]):
        flat = _WS_ALL.sub("", "".join(row))
        if "정정전" in flat and "정정후" in flat:
            end = next((i for i in range(index + 1, len(rows)) if _is_body_start(rows[i])),
                       len(rows))
            return index, end
    return None


def correction_pairs(rows: list[list[str]]) -> list[tuple[str, str, str]]:
    """기재정정 표의 `(항목, 정정 전, 정정 후)`. 없으면 빈 목록.

    Q-030 이 정확히 이 표에 걸린다 — 원공시(유상증자결정)만 보면 '증자 진행 중'으로
    답하게 되고, '철회' 라는 판정은 정정본의 '정정 후' 칸에만 있다.

    정정사유가 rowspan 으로 합쳐지면 칸 수가 4 -> 3 으로 줄어든다. 그래서 항목은
    맨 앞 칸, 값은 **끝 두 칸**으로 읽는다. 칸 위치를 고정하면 절반이 어긋난다.
    """
    span = _correction_span(rows)
    if span is None:
        return []

    pairs: list[tuple[str, str, str]] = []
    for row in rows[span[0] + 1: span[1]]:
        if len(row) < 3 or _is_body_start(row):
            continue
        before, after = _clean(row[-2]), _clean(row[-1])
        if before is None and after is None or before == after:
            continue          # 바뀐 게 없으면 근거가 아니다
        pairs.append((row[0].strip(), before or "-", after or "-"))
    return pairs


def body_rows(rows: list[list[str]]) -> list[list[str]]:
    """정정 표를 뺀 서식 본문. **필드 추출은 반드시 여기서 해야 한다.**

    정정 표의 한 행이 블록을 통째로 담는 경우가 있다.

        ['4. 자금조달의 목적',
         '시설자금 (원) 1,200,052,500,000 영업양수자금 (원) - 운영자금 (원) - …',
         '시설자금 (원) 700,020,800,000 영업양수자금 (원) - 운영자금 (원) - …']

    이 한 행에 6개 자금용도 라벨이 전부 들어 있어서, 라벨로 찾으면 6개 필드가
    **모두 같은 덩어리**를 값으로 집는다. 실측: 한화에어로스페이스 20250408000012 의
    조달금액이 7,000억 × 6 = 4조 2,001억으로 부풀었다.
    """
    span = _correction_span(rows)
    return rows if span is None else rows[:span[0]] + rows[span[1]:]


def normalize_major(doc: dict) -> dict:
    html = read_document(_doc_path(doc))
    rows = extract_rows(html)
    body = body_rows(rows)                    # 정정 표는 필드 추출에서 반드시 뺀다
    form = major_form(doc["report_nm"])

    fields = _collect(body, MAJOR_FIELDS.get(form, ()))

    # 유상증자는 총 조달 규모 칸이 없다. 자금용도별 금액의 합이 그 값이다(Q-022).
    amount, amount_type = None, None
    spec = MAJOR_AMOUNT.get(form)
    if spec is not None:
        amount_type, keywords, sibling = spec
        amount = _find_number(body, *keywords)
        if amount is not None and sibling is not None:
            extra = _to_number(_find_after(body, keywords, sibling) or "")
            if extra is not None:
                amount += extra
    else:
        parts = [n for k in _FUNDING_LABELS
                 if (n := _to_number(fields.get(k) or "")) is not None]
        if parts:
            amount, amount_type = sum(parts), "조달금액"

    # 값 칸이 마지막이 아닌 서식(발행가액)은 행 전체를 훑는 폴백으로 건진다.
    if form in {"유상증자결정", "무상증자결정"}:
        price = _find_loose(body, "발행가", "보통주식")
        if price:
            fields["신주 발행가액"] = price
    for anchor, label in (("전환에따라발행할주식", "전환에 따라 발행할 주식수"),
                          ("교환대상", "교환에 따라 발행할 주식수")):
        shares = _find_after(body, (anchor,), ("주식수",))
        if shares:
            fields[label] = shares

    ratio, ratio_base = None, None
    for keyword, base in _MAJOR_RATIO:
        found = _find_number(body, keyword)
        if found is not None:
            ratio, ratio_base = found, base
            break

    # 기간은 서식이 실제로 기간 칸을 가질 때만. 없는 서식에서 아무 시작일이나 주우면
    # 청약예정일이 계약기간처럼 나간다.
    start = end = None
    header = _MAJOR_PERIOD.get(form)
    if header:
        start = _find_date(body, header, "시작일")
        end = parse_date(_find_after(body, (header, "시작일"), ("종료일",)) or "")

    event = (_find_date(body, "이사회결의일")
             or _find_date(body, "결정일")
             or _find_date(body, "확인일자")
             or parse_date(doc["rcept_dt"]))

    reserved_reason = _find(body, "유보사유")
    title = _CORRECTION_TAG.sub("", doc["report_nm"] or "").strip() or form

    record = _base_record(doc, rows)
    record.update({
        "title": title,
        "form": form,
        "fields": fields,
        "corrections": correction_pairs(rows) if doc["is_correction"] else [],
        "amount": int(amount) if amount is not None else None,
        "amount_type": amount_type,
        "ratio": ratio,
        "ratio_base": ratio_base,
        "period_start": start.isoformat() if start else None,
        "period_end": end.isoformat() if end else None,
        "event_date": event.isoformat() if event else None,
        "reserved_reason": reserved_reason,
        # 서식을 알고 그 서식의 값을 실제로 뽑았을 때만 True.
        # 금액 칸이 있는 서식은 금액이, 없는 서식(합병·소송·상장폐지)은 고유 필드가 기준이다.
        "schema_matched": bool(
            form in MAJOR_FIELDS
            and (amount is not None if amount_type else bool(fields))
        ),
        "withheld": amount is None and reserved_reason is not None,
    })
    return record


# =============================================================================
# 지분공시(holding) — 대량보유상황보고서 요약정보
# =============================================================================
# 앞선 트랙은 '보유비율·주식수는 라벨로 안 나온다'고 봤다. 셀을 못 읽던 파서 기준의
# 관측이었다. 지금 실측하면 1,083건 중 99.7% 에 `직전/이번 보고서` 행이 있다.
#
# 다만 이 표는 **라벨:값이 아니라 격자**다.
#
#     ['보유주식등의 수 및 보유비율', '보유주식등의 수', '보유비율']   <- 머리행
#     ['직전 보고서', '196,452,737', '30.67']
#     ['이번 보고서', '196,488,145', '30.67']
#
# 값의 이름은 행이 아니라 **머리행의 열 위치**에 있다. row[-1] 을 값으로 쓰는
# 라벨:값 계약으로는 비율만 잡히고 주식수가 통째로 사라진다.

_SUMMARY_START = "요약정보"
#: 요약정보 블록의 끝. 이 확인문 뒤로는 같은 이름('직전보고서')의 상세표가 또 나오는데,
#: 그쪽은 칸이 9개라 위치가 다르다. 경계를 안 그으면 상세표 값을 요약값으로 집는다.
_SUMMARY_END = ("보고자 본인은", "회사코드", "법인구분")
_PREV, _CURRENT = "직전보고서", "이번보고서"


def summary_rows(rows: list[list[str]]) -> list[list[str]]:
    """요약정보 블록만 잘라낸다."""
    start = 0
    for index, row in enumerate(rows[:20]):
        if row and _WS_ALL.sub("", row[0]) == _SUMMARY_START:
            start = index + 1
            break
    block: list[list[str]] = []
    for row in rows[start:]:
        if any(mark in "".join(row) for mark in _SUMMARY_END):
            break
        block.append(row)
    return block


def holding_summary(rows: list[list[str]]) -> dict[str, tuple[str | None, str | None]]:
    """요약정보 격자 -> `{열 이름: (직전, 이번)}`.

    머리행(3칸 이상, 첫 칸이 항목명)을 만나면 그 아래 `직전/이번 보고서` 행을
    열 위치로 읽는다.

    같은 열 이름이 두 번 나온다 — `보유비율` 은 '보유주식등' 블록과 '의결권' 블록에
    모두 있다. **먼저 나온 블록만** 남긴다. 실측 사례(HMM 20250918000358)에서
    의결권 블록은 직전·이번이 둘 다 `-` 라, 덮어쓰면 앞 블록의 36.02/32.60 이
    통째로 지워진다.
    """
    out: dict[str, tuple[str | None, str | None]] = {}
    claimed: set[str] = set()
    columns: list[str | None] = []
    for row in summary_rows(rows):
        head = _WS_ALL.sub("", row[0]) if row else ""
        if head in (_PREV, _CURRENT):
            slot = 0 if head == _PREV else 1
            for offset, name in enumerate(columns):
                if name is None:
                    continue
                value = _clean(row[offset + 1]) if offset + 1 < len(row) else None
                pair = out.get(name, (None, None))
                out[name] = (value, pair[1]) if slot == 0 else (pair[0], value)
            claimed.update(n for n in columns if n)
            continue
        if len(row) >= 3 and "보고서" not in head:
            # 머리행: 첫 칸은 블록 이름, 나머지가 열 이름. 이미 쓴 이름은 비워 둔다.
            columns = [None if (n := c.strip()) in claimed else n for c in row[1:]]
    return out


#: 요약정보 블록의 단순 `라벨 | 값` 행들. (`발행회사명` 은 4칸 행이라 _find_beside 로 읽는다)
HOLDING_FIELDS = (
    ("보고구분", ("보고구분",)),
    ("보고사유", ("보고사유",)),
    ("보유목적", ("보유목적",)),
)
#: 라벨 옆 칸에서 읽는 필드.
HOLDING_BESIDE = (("발행회사명", "발행회사명"),
                  ("발행회사와의 관계", "발행회사와의 관계"),
                  ("의결권있는 발행주식 총수", "의결권있는 발행주식 총수(주)"))
#: 보고자 이름의 상한. 정정본은 요약 블록이 통째로 한 칸에 들어가는 경우가 있어,
#: 이름 자리에 문서 전문이 들어가면 제목이 폭주한다(실측 20250919000067, 891자).
_REPORTER_MAX = 40
#: 요약 격자에서 마크다운으로 올릴 열. 의결권 블록은 보유주식 블록과 값이 거의 같아
#: 근거만 길어지므로 싣지 않는다.
_STAKE_COLUMNS = (
    ("보유주식등의 수", "보유주식등의 수", "주"),
    ("보유비율", "보유비율", "%"),
    ("주요계약체결 주식등의 수", "주식등의 수", "주"),
    ("주요계약체결 비율", "비율", "%"),
)


def _pair_line(pair: tuple[str | None, str | None], unit: str) -> str | None:
    """`(직전, 이번)` -> `직전 A → 이번 B`. 한쪽만 있으면 있는 쪽만 쓴다."""
    prev, current = pair
    parts = []
    if prev:
        parts.append(f"직전 {prev}{unit}")
    if current:
        parts.append(f"이번 {current}{unit}")
    return " → ".join(parts) or None


def normalize_holding(doc: dict) -> dict:
    html = read_document(_doc_path(doc))
    rows = extract_rows(html)
    # 정정본은 앞에 정정 표가 붙고 그 안에 '요약정보' 라는 **항목명**이 있다.
    # 그대로 두면 요약 블록의 시작을 정정 표에서 잘못 잡는다.
    body = body_rows(rows)
    block = summary_rows(body)
    summary = holding_summary(body)

    reporter = _find_beside(body[:8], "보고자")
    if reporter and len(reporter) > _REPORTER_MAX:
        reporter = None

    fields = _collect(block, HOLDING_FIELDS)
    for label, keyword in HOLDING_BESIDE:
        value = _find_beside(body[:30], keyword)
        if value:
            fields[label] = _shorten(value)
    for label, column, unit in _STAKE_COLUMNS:
        line = _pair_line(summary.get(column, (None, None)), unit)
        if line:
            fields[label] = line

    shares_prev, shares_current = summary.get("보유주식등의 수", (None, None))
    ratio_prev, ratio_current = summary.get("보유비율", (None, None))

    title = doc["report_nm"]
    if reporter:
        title = f"{title} · 보고자 {reporter}"

    record = _base_record(doc, rows)
    record.update({
        "title": title,
        "form": doc["doc_subtype"] or "대량보유상황보고서",
        "reporter": reporter,
        "fields": fields,
        "corrections": correction_pairs(rows) if doc["is_correction"] else [],
        "shares_prev": shares_prev,
        "shares_current": shares_current,
        "ratio_prev": _to_number(ratio_prev or ""),
        "ratio_current": _to_number(ratio_current or ""),
        # ratio 는 일부러 비운다. 지분공시 질의는 '직전 대비 몇 %p' 처럼 **차이**를
        # 묻는 경우가 있어서, 이번 보유비율을 ratio 로 올리면 critic 이 정답인
        # 차이값(3.42)을 보유비율(32.60)과 대조해 틀렸다고 판정한다(Q-016).
        # 두 값은 이미 '보유비율' 필드 한 줄에 직전·이번이 다 들어 있다.
        "event_date": (_d.isoformat() if (_d := parse_date(doc["rcept_dt"])) else None),
        # 지분공시의 핵심 값은 '이번 보고서'의 주식수와 비율이다. 둘 다 있어야 매칭.
        "schema_matched": bool(shares_current and ratio_current),
    })
    return record


# =============================================================================


def normalize_doc(doc: dict) -> dict:
    """문서군에 맞는 정규화기로 보낸다. 모르는 군은 거래소 서식으로 시도한다."""
    return {"major": normalize_major,
            "holding": normalize_holding}.get(doc.get("doc_group"), normalize_exchange)(doc)
