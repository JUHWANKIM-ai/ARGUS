"""안전성 계층. 평가지표 「안전성·신뢰성」 대응."""
from __future__ import annotations

import re

_RRN = re.compile(r"\b\d{6}\s*[-–]\s*[1-4]\d{6}\b")
_PHONE = re.compile(r"\b0\d{1,2}\s*[-–]\s*\d{3,4}\s*[-–]\s*\d{4}\b")
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b")
_ACCOUNT = re.compile(r"\b\d{2,3}-\d{2,6}-\d{2,6}\b")

_INJECTION = (
    "이전 지시", "앞의 지시", "지시를 무시", "규칙을 무시", "시스템 프롬프트",
    "ignore all previous", "ignore previous", "disregard", "system prompt",
    "너의 프롬프트", "역할을 잊", "developer mode",
)


def mask_pii(text: str) -> str:
    """개인정보만 가린다. 금액·접수번호 같은 업무 숫자는 건드리지 않는다."""
    if not text:
        return text
    out = _RRN.sub("******-*******", text)
    out = _PHONE.sub("***-****-****", out)
    out = _EMAIL.sub("****@****", out)
    return _ACCOUNT.sub("***-***-***", out)


def detect_injection(question: str) -> bool:
    lowered = (question or "").lower()
    return any(marker.lower() in lowered for marker in _INJECTION)


def sanitize_question(question: str) -> tuple[str, list[str]]:
    warnings: list[str] = []
    if detect_injection(question):
        warnings.append("프롬프트 인젝션 시도 감지 — 공시 질의로만 처리")
    return mask_pii(question or ""), warnings
