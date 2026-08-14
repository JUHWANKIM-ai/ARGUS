"""정규화 레코드 -> LLM 입력용 마크다운 (CONVENTION §7.3 형식).

**레코드에 값을 넣어도 여기서 안 실으면 LLM 은 못 본다.** Phase H 가 major·holding
을 GENERATION 실패로 1차 귀속했던 4건이 정확히 이 지점이었다 — 근거 블록에
권면총액·전환가액·보유비율이 한 줄도 없었다.

반대로 다 실으면 문항당 토큰이 그대로 비용이 된다. 그래서 값이 **있는 필드만**
한 줄씩 쓰고, 필드 수와 값 길이는 normalize 단계에서 이미 잘라 둔다.

R2C 가 같은 지점을 반대쪽에서 다시 만났다 — 정형 칸이 **하나도 안 채워진**
서식(`schema_matched=False`)에서는 위 규칙이 곧 "아무것도 안 싣는다" 가 되어
근거가 머리글뿐이 된다(실측 Q-101: 108자 · 숫자 0개). `_free_text_lines` 가
그 경우에만 원문 서술 본문을 싣는다.
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

#: 정정 표는 근거로는 결정적이지만(Q-030 '철회') 길다. 앞의 몇 건만 싣는다.
MAX_CORRECTION_LINES = 3
MAX_CORRECTION_CHARS = 60


def _amount_line(record: dict) -> str | None:
    amount = record.get("amount")
    if amount is None:
        return None
    eok = amount / 100_000_000
    return f"- {record.get('amount_type') or '금액'}: {amount:,}원 ({eok:,.0f}억원)"


def _label(record: dict) -> str:
    """머리글 대괄호. major 는 doc_subtype 이 전건 None 이라 서식명(form)을 쓴다."""
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

    # 서식 고유 필드 — major 의 자금용도별 금액·전환가액, holding 의 보유비율이 여기 실린다.
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
    """🔴 **`schema_matched=True` 경로는 한 바이트도 지나가지 않는다.**

    그 경로가 근거의 대부분(3,150건 중 2,912건)이고, 한 글자라도 바뀌면 BM25 색인
    문자열이 바뀌어 **측정된 arm 이 전부 무효**가 된다. 그래서 게이트는 하나다.

    반대쪽(238건)에서는 정형 칸이 전부 비어 근거가 머리글뿐이다. 실측 v3/A8 의
    근거 레코드 208건 중 10건이 이 상태이고 **10건 전부 원문에 금액이 있다.**
    `_amount_line` 은 `amount is None` 이면 아무것도 쓰지 않으므로, 이 자리가
    아니면 그 숫자가 LLM 에 닿는 경로가 **없다**.
    """
    if record.get("schema_matched"):
        return []
    head = "\n".join(lines)
    out = []
    for label, value in free_text_items(record.get("raw_rows") or []):
        if value in head:                 # 제목이 머리글로 이미 올라간 서식
            continue
        out.append(f"- {label}: {value}")
    return out


def _fmt_date(raw: str | None) -> str:
    if raw and len(raw) == 8 and raw.isdigit():
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:]}"
    return raw or ""
