"""Render normalised records as markdown for LLM input (CONVENTION 7.3).

A value present in a record is invisible to the model unless it is emitted
here. Conversely, emitting everything turns token count directly into cost,
so only populated fields are written, one line each. Field count and value
length are already bounded during normalisation.

Records whose structured fields are entirely empty would otherwise reduce to
a bare header. `_free_text_lines` covers that case by emitting the narrative
body instead.
"""
from __future__ import annotations

from argus.corpus.dates import format_duration, parse_date
from argus.corpus.normalize import free_text_items

_SUBTYPE_LABEL = {
    "단일판매공급계약체결": "공급계약 체결",
    "단일판매공급계약해지": "공급계약 해지",
    "신규시설투자등": "신규 시설투자",
    "투자판단관련주요경영사항": "투자판단 관련 주요경영사항",
}

#: Correction tables are decisive evidence but long. Only the leading rows
#: are emitted.
MAX_CORRECTION_LINES = 3
MAX_CORRECTION_CHARS = 60


def _amount_line(record: dict) -> str | None:
    amount = record.get("amount")
    if amount is None:
        return None
    eok = amount / 100_000_000
    return f"- {record.get('amount_type') or '금액'}: {amount:,}원 ({eok:,.0f}억원)"


def _label(record: dict) -> str:
    """Header bracket. The major group has no doc_subtype, so the form name is used."""
    subtype = record.get("doc_subtype") or ""
    return (_SUBTYPE_LABEL.get(subtype)
            or record.get("form")
            or subtype
            or "공시")


def _clip(text: str) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= MAX_CORRECTION_CHARS else text[:MAX_CORRECTION_CHARS] + "…"


def to_markdown(record: dict) -> str:
    prefix = "[기재정정] " if record.get("is_correction") else ""
    lines = [
        f"## {prefix}[{_label(record)}] {record['corp_name']} – "
        f"{record.get('title', '')}".rstrip(" –"),
        f"- 공시일: {_fmt_date(record.get('rcept_dt'))} (접수번호 {record['rcept_no']})",
    ]
    if record.get("sector"):
        lines.append(f"- 섹터: {record['sector']}")

    amount_line = _amount_line(record)
    if amount_line:
        lines.append(amount_line)
    if record.get("ratio") is not None:
        lines.append(f"- {record.get('ratio_base') or '매출액'} 대비: {record['ratio']}%")
    if record.get("counterparty"):
        lines.append(f"- 계약상대: {record['counterparty']}")
    # Placed next to the counterparty because both come from the same part of
    # the form and a question that names one usually names the other.
    # See docs/design-rationale.md#supply-region
    if record.get("supply_region"):
        lines.append(f"- 공급지역: {record['supply_region']}")

    # Form-specific fields: use-of-proceeds amounts and conversion prices for
    # the major group, ownership ratios for the holding group.
    for label, value in (record.get("fields") or {}).items():
        lines.append(f"- {label}: {value}")

    start, end = record.get("period_start"), record.get("period_end")
    if start and end:
        line = f"- 기간: {start} ~ {end}"
        sd, ed = parse_date(start), parse_date(end)
        if sd and ed:
            line += f" ({format_duration(sd, ed)})"
        lines.append(line)

    if record.get("event_date"):
        lines.append(f"- 확정일: {record['event_date']}")
    if record.get("reserved_reason"):
        lines.append(f"- 공시유보: {record['reserved_reason']}")

    for item, before, after in (record.get("corrections") or [])[:MAX_CORRECTION_LINES]:
        name = f"({_clip(item)})" if item and item.strip() not in {"-", ""} else ""
        lines.append(f"- 정정{name}: {_clip(before)} → {_clip(after)}")

    lines += _free_text_lines(record, lines)
    return "\n".join(lines)


def _free_text_lines(record: dict, lines: list[str]) -> list[str]:
    """Emit narrative body text for records with no structured fields.

    Gated on `schema_matched` being false. Records on the matched path are not
    touched, because any change to their markdown would alter the BM25 index
    strings and invalidate every prior measurement.

    On the unmatched path the structured columns are empty, leaving only a
    header. Amounts present in the source narrative would have no route to the
    model without this function.

    See docs/design-rationale.md#evidence-slot-vs-line for why widening the
    matched path was rejected. `tests/test_evidence_truncation.py` pins the
    figures involved.
    """
    if record.get("schema_matched"):
        return []
    head = "\n".join(lines)
    out = []
    for label, value in free_text_items(record.get("raw_rows") or []):
        if value in head:                 # forms whose title is already the heading
            continue
        out.append(f"- {label}: {value}")
    return out


def _fmt_date(raw: str | None) -> str:
    if raw and len(raw) == 8 and raw.isdigit():
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:]}"
    return raw or ""
