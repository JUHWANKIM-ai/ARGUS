"""정규화 레코드 -> LLM 입력용 마크다운 (CONVENTION §7.3 형식)."""
from __future__ import annotations

from argus.corpus.dates import format_duration, parse_date

_SUBTYPE_LABEL = {
    "단일판매공급계약체결": "공급계약 체결",
    "단일판매공급계약해지": "공급계약 해지",
    "신규시설투자등": "신규 시설투자",
    "투자판단관련주요경영사항": "투자판단 관련 주요경영사항",
}


def _amount_line(record: dict) -> str | None:
    amount = record.get("amount")
    if amount is None:
        return None
    eok = amount / 100_000_000
    return f"- {record.get('amount_type', '금액')}: {amount:,}원 ({eok:,.0f}억원)"


def to_markdown(record: dict) -> str:
    label = _SUBTYPE_LABEL.get(record.get("doc_subtype", ""), record.get("doc_subtype", "공시"))
    prefix = "[기재정정] " if record.get("is_correction") else ""
    lines = [
        f"## {prefix}[{label}] {record['corp_name']} – {record.get('title', '')}".rstrip(" –"),
        f"- 공시일: {_fmt_date(record.get('rcept_dt'))} (접수번호 {record['rcept_no']})",
    ]
    if record.get("sector"):
        lines.append(f"- 섹터: {record['sector']}")

    amount_line = _amount_line(record)
    if amount_line:
        lines.append(amount_line)
    if record.get("ratio") is not None:
        lines.append(f"- {record.get('ratio_base', '매출액')} 대비: {record['ratio']}%")
    if record.get("counterparty"):
        lines.append(f"- 계약상대: {record['counterparty']}")

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
    return "\n".join(lines)


def _fmt_date(raw: str | None) -> str:
    if raw and len(raw) == 8 and raw.isdigit():
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:]}"
    return raw or ""
