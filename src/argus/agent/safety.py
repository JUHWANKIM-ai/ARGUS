"""Safety layer for the safety and reliability evaluation axis."""
from __future__ import annotations

import re

# Digit-only boundaries instead of `\b`. Python's `\w` includes Hangul, so a
# phone number followed by a Korean particle has no word boundary between the
# final digit and the particle. Blocking adjacent digits is sufficient: it
# prevents matching inside a longer digit run while treating Hangul and
# punctuation as boundaries.
_D0, _D1 = r"(?<!\d)", r"(?!\d)"

_RRN = re.compile(_D0 + r"\d{6}\s*[-–]\s*[1-4]\d{6}" + _D1)
_PHONE = re.compile(_D0 + r"0\d{1,2}\s*[-–]\s*\d{3,4}\s*[-–]\s*\d{4}" + _D1)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
_ACCOUNT = re.compile(_D0 + r"\d{2,3}-\d{2,6}-\d{2,6}" + _D1)

#: Table-cell boundary preceding a corporate registration number. Affiliate
#: tables render as `| SK이노베이션㈜ | 110111-3710385 |`, placing the number in
#: the cell right after a corporate marker. A corporate marker appearing inside
#: running prose does not match.
#: See docs/design-rationale.md#rrn-vs-corp-number
_CORP_CELL = re.compile(r"(?:㈜|\(주\)|주식회사|유한회사|Co\.|Ltd|Inc)[^|｜\n]{0,12}[|｜]\s*$")

#: Explicit resident-registration-number label. Takes precedence over every
#: retention rule below.
_RRN_LABEL = re.compile(r"주민(?:등록)?번호[^0-9\n]{0,6}$")

#: Explicit corporate or business registration number label. Serves the same
#: role as `_CORP_CELL` for prose, where no table cell boundary exists.
#: See docs/design-rationale.md#rrn-vs-corp-number
_CORP_LABEL = re.compile(r"(?:법인|사업자)등록번호[^0-9\n]{0,8}$")

_INJECTION = (
    "이전 지시", "앞의 지시", "지시를 무시", "규칙을 무시", "시스템 프롬프트",
    "ignore all previous", "ignore previous", "disregard", "system prompt",
    "너의 프롬프트", "역할을 잊", "developer mode",
)


def _mask_rrn(match: re.Match) -> str:
    """Mask a resident registration number, retaining corporate identifiers.

    Resolution order is fixed and must not be reordered. An explicit resident
    number label always masks. Otherwise a corporate label or table cell
    retains the value. Anything else masks, because a false negative leaks
    personal data while a false positive only damages a business figure.
    """
    before = match.string[:match.start()]
    if _RRN_LABEL.search(before):
        return "******-*******"
    if _CORP_LABEL.search(before) or _CORP_CELL.search(before):
        return match.group(0)
    return "******-*******"


def mask_pii(text: str) -> str:
    """Mask personal data only. Amounts and receipt numbers are preserved."""
    if not text:
        return text
    out = _RRN.sub(_mask_rrn, text)
    out = _PHONE.sub("***-****-****", out)
    out = _EMAIL.sub("****@****", out)
    return _ACCOUNT.sub("***-***-***", out)


def detect_injection(question: str) -> bool:
    """Return True if the question contains a known prompt-injection marker."""
    lowered = (question or "").lower()
    return any(marker.lower() in lowered for marker in _INJECTION)


def sanitize_question(question: str) -> tuple[str, list[str]]:
    """Mask personal data in the question and report injection warnings."""
    warnings: list[str] = []
    if detect_injection(question):
        warnings.append("프롬프트 인젝션 시도 감지 — 공시 질의로만 처리")
    return mask_pii(question or ""), warnings
