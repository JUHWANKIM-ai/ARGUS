"""질의어 추출과 hop 별 재구성. LLM 호출 0회, 전부 규칙이다."""
from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field

# 전 문서에 공통으로 등장해 변별력이 없는 어휘 (실측 기반)
#
# '해지' 는 뺐다 — 실측 DF 0.014 (1,469건 중 21건) 로 오히려 최상위 변별 어휘다.
# 손으로 '체결'(DF 0.766) 과 나란히 넣은 것이 잘못이었다. 되살리면 두산퓨얼셀 해지 3건
# 질의의 상위 3위가 1건 → 3건으로 바뀐다 (16.9/16.2/16.1 vs 9.7/9.4/9.3).
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
        # 불용어가 접미로 붙은 복합어도 제거 ('의공급계약' 같은 잔재)
        if any(word.endswith(s) and word != s for s in STOPWORDS if len(s) >= 4):
            continue
        if word not in terms:
            terms.append(word)
    return terms


# --- F-3 질의 재구성 ----------------------------------------------------------
# 검색이 실패하면 조건을 하나씩 푼다. 넓히는 축은 **필터**이고, 질의어는 반대로 **좁힌다** —
# 기간·기업 필터가 걸러주던 잡음을 필터를 풀면 질의어가 그대로 뒤집어쓰기 때문이다.

#: hop 별 전략. hop 0 은 최초 검색이라 예산을 쓰지 않고, hop 1~3 이 Budget.spend_hop() 을 쓴다.
#: (docs/pipeline/06-agent-tool.md F-3 의 "기간 필터 완화 → 기업 필터만 → 전역")
STRATEGIES = ("narrow", "relax_period", "corp_only", "global")

#: hop 별 top_k. 필터를 풀수록 후보를 넓게 받는다. MAX_EVIDENCE(10) 를 넘길 이유는 없다.
TOP_K = (5, 8, 10, 10)

#: 실측 문서빈도(DF) 0.30 이상 — 있어도 문서를 가르지 못하는 어휘.
#: artifacts/exchange_bm25.json 1,469건 기준: 계약 0.80 · 상대 0.77 · 금액 0.73 ·
#: 매출 0.72 · 대비 0.72 · 정정 0.43. 숫자 토큰(2023 0.34 · 2025 0.41)은 아래 _NUMERIC 로 뺀다.
COMMON_TERMS = frozenset({"계약", "상대", "금액", "매출", "대비", "정정"})

_NUMERIC = re.compile(r"^[0-9][0-9,.]*$")
#: 어절 그대로 남은 서술어 꼬리. '체결한'→'체결'(불용어) · '해지된'→'해지'(변별어) 로 가른다.
_TAIL = re.compile(r"(하는|되는|이는|한|된|인|는|나|줘|해)$")


def _stem(word: str) -> str:
    if len(word) <= 2:
        return word
    stem = _TAIL.sub("", word)
    return stem if len(stem) >= 2 else word


def core_terms(terms: Sequence[str]) -> list[str]:
    """필터를 풀 때 쓸 핵심어만 남긴다. 남는 게 없으면 빈 목록 — 추측하지 않는다."""
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
    """기간 필터를 한 단계만 넓힌다. 연월 → 연도 → 없음."""
    if "ym" in period:
        return {"year": int(str(period["ym"])[:4])}
    return {}


@dataclass(frozen=True)
class HopPlan:
    """한 hop 의 검색 계획. LLM 없이 규칙으로 만든다."""

    hop: int
    strategy: str
    terms: tuple[str, ...] = ()
    period: dict = field(default_factory=dict)
    use_corp: bool = True
    top_k: int = 5

    @property
    def query(self) -> str:
        return " ".join(self.terms)


def plan_hops(question: str, period: dict,
              fallback_terms: Sequence[str] = ()) -> list[HopPlan]:
    """좁은 것부터 넓히는 hop 계획 4단.

    hop 0 `narrow`       — 전체 어휘 + 연월(또는 연도) + 기업 필터
    hop 1 `relax_period` — 기간 필터 한 단계 완화 (연월 → 연도 → 없음)
    hop 2 `corp_only`    — 기간 필터 해제, 기업 필터만. 질의는 핵심어로 축소
    hop 3 `global`       — 기업 필터까지 해제. 마지막 수단
    """
    terms = list(extract_terms(question))
    core = core_terms(terms)
    fallback = [t for t in fallback_terms if t]
    if not core:
        # 질문에 남는 내용어가 없다 — 기업명이 유일한 단서다.
        core = fallback or terms
        terms = terms + [t for t in fallback if t not in terms]
    if not terms:
        terms = core
    return [
        HopPlan(0, "narrow", tuple(terms), dict(period), True, TOP_K[0]),
        HopPlan(1, "relax_period", tuple(terms), relax_period(period), True, TOP_K[1]),
        HopPlan(2, "corp_only", tuple(core), {}, True, TOP_K[2]),
        HopPlan(3, "global", tuple(core), {}, False, TOP_K[3]),
    ]
