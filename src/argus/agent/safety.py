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


#: Names of paths outside the corpus we were given. Open issue 1 measured
#: these four shapes; listing them in the system prompt made the model produce
#: them more often (A27, 1 case -> 3), so the list lives here in post
#: processing where it cannot reach the model.
#:
#: This list carries the discrimination, not the verb list below: a referral
#: names an external *service*, while ordinary disclosure prose names a place
#: the company itself acted. Kept narrow on purpose: an earlier draft also
#: listed the bare common nouns `홈페이지` and `관련 기관`. Both matched
#: ordinary closing sentences that describe the company's *own* disclosed
#: action — "회사는 정정 공시를 홈페이지에 게시하오니 확인하시기 바랍니다."
#: and "동사는 관련 기관의 승인을 받은 사실을 공시하였으니 참고하시기
#: 바랍니다." — neither of which sends the user anywhere outside the corpus.
#: A bare common noun is not an external service; only the qualified `공식
#: 웹사이트`/`공식 홈페이지`/`공식 발표 채널` name one unambiguously. Widen
#: this list only against a case that still fails with the qualified forms in
#: place.
#: See docs/design-rationale.md#external-referral
_EXTERNAL_SOURCE = (
    "네이버", "구글", "증권사", "전자공시시스템", "DART", "다트",
    "공식 웹사이트", "공식 홈페이지", "공식 발표 채널",
)

#: Recommending verbs. Both halves have to be in the same sentence: the
#: refusal sentences carry a source name at most, never a recommendation.
#:
#: A referral is *directed at the reader*; a self-citation naming the same
#: source is *descriptive* of what we did. Korean marks that difference in
#: the verb ending, not the stem: the honorific `-시-`/`-십-` puts the reader
#: in the subject position (`참고하시기`, `참고하십시오`, `확인하실`), while
#: the plain stem describes an action already taken (`참고하여`,
#: `참고하였습니다`, `참조하였습니다`). Two measured sentences taught this —
#: "본 답변은 전자공시시스템에 공시된 자료를 참고하여 작성되었습니다." and
#: "위 수치는 DART에 공시된 사업보고서를 참조하였습니다." — both name a kept
#: source and use the bare stem, but both describe our own evidence use, and
#: bare `참고하`/`참조하` swept them in.
#:
#: `참고하십시오` needs its own entry alongside `참고하시`: its fourth
#: syllable is `십`, not `시`, so `참고하시` does not match it. Same for
#: `참조하십`. This is a case where reading the string is not enough — run it.
#:
#: Bare `바랍니다` is gone too, but for a different reason than the stems
#: above: it carries no stem at all to be directed or descriptive about, and
#: every measured directed case that used to reach it also carries one of the
#: honorific forms below in the same sentence (`...하시기 바랍니다.` always
#: pairs a kept honorific stem with `바랍니다`), so dropping the bare form
#: costs no recall.
#: See docs/design-rationale.md#external-referral
_REFERRAL_VERB = (
    "추천", "권장", "권해", "권합니다",
    "참고하시", "참고하십", "참조하시", "참조하십",
    "확인해 보시", "확인하시", "확인하실",
    "문의해 보시", "알아보시", "도움이 될",
)

#: Sentence boundary. A period followed by whitespace, or a line break. The
#: separator is captured so the surviving text can be rebuilt with its
#: original spacing: a refusal's two sentences share one paragraph, and
#: rejoining survivors with a fixed separator would split it in two.
_SENTENCE = re.compile(r"((?<=[.!?])\s+|\n+)")

#: A citation the pipeline attached. Never removed: our own citation may name
#: DART, and cutting it removes the evidence.
_HAS_RECEIPT = re.compile(r"\d{14}")


def strip_external_referral(answer: str) -> tuple[str, list[str]]:
    """Remove sentences that recommend a path outside the corpus.

    Returns (new answer, removed sentences). No LLM call.

    A sentence goes only when it names an external source AND recommends
    acting on it. Open issue 1 held that the referral could not be separated
    from the refusal it trails, but the refusal sentences satisfy neither half
    of that conjunction.

    Two sentences are never removed: one carrying a receipt number, because
    that is our own citation, and every sentence, because an emptied answer is
    worse than the defect it fixes.
    See docs/design-rationale.md#external-referral
    """
    if not answer:
        return answer, []
    # Alternating sentence / separator, because the pattern captures. Keeping
    # the separators is what lets an untouched paragraph stay one paragraph.
    parts = _SENTENCE.split(answer)
    kept, removed = [], []
    for index in range(0, len(parts), 2):
        sentence = parts[index]
        separator = parts[index + 1] if index + 1 < len(parts) else ""
        names_source = any(s in sentence for s in _EXTERNAL_SOURCE)
        recommends = any(v in sentence for v in _REFERRAL_VERB)
        if (sentence.strip() and names_source and recommends
                and not _HAS_RECEIPT.search(sentence)):
            removed.append(sentence)
        else:
            kept.append(sentence + separator)
    if not removed:
        return answer, []
    rebuilt = "".join(kept).rstrip()
    if not rebuilt.strip():
        return answer, []
    return rebuilt, removed
