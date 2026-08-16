"""안전성 계층. 평가지표 「안전성·신뢰성」 대응."""
from __future__ import annotations

import re

# 🔴 **`\b` 를 쓰지 않는다.** 파이썬 `\w` 는 한글을 포함하므로 `010-1234-5678로는` 의
# `8` 과 `로` 사이에는 **낱말 경계가 없다.** 그래서 종전 `\b...\b` 는 조사가 붙은 순간
# 통째로 안 걸렸다 — 세 세대 전체에서 마스크 토큰이 0건이었던 이유다 (v4 `R1-7`).
# 앞뒤로 **숫자만** 막으면 된다: 긴 숫자열 한가운데를 잘라 쓰는 것만 피하면 되고,
# 한글·기호는 경계로 인정해야 한다.
_D0, _D1 = r"(?<!\d)", r"(?!\d)"

_RRN = re.compile(_D0 + r"\d{6}\s*[-–]\s*[1-4]\d{6}" + _D1)
_PHONE = re.compile(_D0 + r"0\d{1,2}\s*[-–]\s*\d{3,4}\s*[-–]\s*\d{4}" + _D1)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
_ACCOUNT = re.compile(_D0 + r"\d{2,3}-\d{2,6}-\d{2,6}" + _D1)

#: 🔴 **법인등록번호는 주민등록번호와 형식이 같다** — 13자리에 `XXXXXX-XXXXXXX`.
#: 실측: 사업보고서 계열회사 표에서 24MB 표본에 **41종**이 잡혔고 (`110111-3710385` 등)
#: 전부 정당한 공시 식별번호인데 `_RRN` 이 주민번호로 읽어 지웠다.
#:
#: 🔴 **체크섬으로는 못 가른다.** 34종 중 **6종이 주민번호 체크섬을 통과**하고, 반대로
#: 안전성 시험에 쓰이는 **지어낸 주민번호는 체크섬을 통과하지 못한다.** 체크섬을 통과
#: 조건으로 쓰면 가짜 주민번호가 그대로 새어 나간다 — 안전 장치에서 미탐은 오탐보다 나쁘다.
#:
#: 그래서 **표 칸 경계**를 본다. 계열회사 표는 `| SK이노베이션㈜ | 110111-3710385 |`
#: 꼴이라 법인 표지 바로 뒤 칸에 번호가 온다. 실측 **34/34 적중 · 오탐 0** 이고,
#: *"㈜한양의 대표 홍길동 900101-…"* 같은 **문장 속 법인 표지는 걸리지 않는다.**
_CORP_CELL = re.compile(r"(?:㈜|\(주\)|주식회사|유한회사|Co\.|Ltd|Inc)[^|｜\n]{0,12}[|｜]\s*$")

#: 라벨이 명시되면 표 칸이든 아니든 무조건 가린다. 이쪽이 우선이다.
_RRN_LABEL = re.compile(r"주민(?:등록)?번호[^0-9\n]{0,6}$")

_INJECTION = (
    "이전 지시", "앞의 지시", "지시를 무시", "규칙을 무시", "시스템 프롬프트",
    "ignore all previous", "ignore previous", "disregard", "system prompt",
    "너의 프롬프트", "역할을 잊", "developer mode",
)


def _mask_rrn(match: re.Match) -> str:
    """주민번호 자리를 가린다 — 단, **법인등록번호는 남긴다** (`_CORP_CELL` 독스트링).

    🔴 판정은 **가리는 쪽으로 기운다.** 라벨이 명시됐거나 표 칸이 아니면 가린다.
    안전 장치에서 미탐(진짜 개인정보 유출)은 오탐(공시 번호 훼손)보다 나쁘다.
    """
    before = match.string[:match.start()]
    if _RRN_LABEL.search(before):
        return "******-*******"
    return match.group(0) if _CORP_CELL.search(before) else "******-*******"


def mask_pii(text: str) -> str:
    """개인정보만 가린다. 금액·접수번호 같은 업무 숫자는 건드리지 않는다."""
    if not text:
        return text
    out = _RRN.sub(_mask_rrn, text)
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
