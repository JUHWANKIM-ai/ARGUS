"""계약 상대(counterparty) 문자열의 정규화와 분류.

노드로 승격할 수 있는 것과 없는 것을 가른다. 비실명(`"글로벌 대형기업"`)을 노드로
만들면 서로 다른 실체가 한 노드로 뭉쳐 그래프가 오염된다.

정규화·비실명 판정 규칙은 여기서 새로 만든 것이 아니라 원래 `scripts/validate_evalset.py`
(`normalize_counterparty` · `ANON_COUNTERPARTY`)에 있던 것을 그대로 옮긴 것이다.
03-graph-schema C-0b 가 실측한 표기 변이 5유형(국호 접두 · 법인격 접미/접두 · 공백 ·
괄호 약칭)을 겨냥해 만들어졌고, `tests/test_evalset.py::COUNTERPARTY_VARIANTS` 가 그
규칙을 고정한다. `scripts/validate_evalset.py` 는 이제 이 모듈을 얇게 감싼 별칭만 남긴다
— 규칙이 두 곳으로 갈리면 문항 검증(C12)과 런타임 분류가 서로 어긋난다.
"""
from __future__ import annotations

import re
from functools import lru_cache

from argus.corpus.manifest import load_universe

#: 표기 변이 접기 — scripts/validate_evalset.py 의 _CP_PAREN/_CP_SUFFIX/_CP_PREFIX 와
#: 동일 규칙(실측 정본). 여기가 이제 단일 원천이다.
_PAREN = re.compile(r"\([^)]*\)")
_SUFFIX = re.compile(
    r"(주식회사|\(주\)|㈜|\(유\)|Co\.,?\s*Ltd\.?|Corp\.?|Inc\.?|Limited|LLC|S\.A\.)", re.I)
_PREFIX = re.compile(r"^대한민국\s*")

#: 비실명 계약상대 — 어느 기업인지 원문에 없어 노드로 못 올린다.
#: scripts/validate_evalset.py 의 ANON_COUNTERPARTY 와 동일 규칙(실측 정본).
ANON_COUNTERPARTY = re.compile(
    r"(지역\s*선주|소재\s*선사|글로벌|익명|비공개|대형기업|중견기업|중소기업|"
    r"업체$|제약사$|고객사|완성차)")

#: 해외 법인 힌트 — 로마자 3자 이상 연속. 국문 표기가 아예 없는 이름을 잡는다.
_OVERSEAS = re.compile(r"[A-Za-z]{3,}")

#: 국내 공공기관. universe.csv(상장사 유니버스) 밖이라 `classify` 의 `universe` 판정으로는
#: 안 잡히는 것들을 손으로 추가한다. artifacts/exchange_records.jsonl 의 counterparty 실측
#: (Task 2, 2026-08-10)으로 확인해 6곳을 보강했다 — 국방과학연구소(12건) · 한국토지주택공사
#: (8건) · 대한무역투자진흥공사(2건) · 국가철도공단(1건) · 한국항공우주연구원(1건) ·
#: 국방기술진흥연구소(1건). `국방부`·`산업통상자원부`·`한국도로공사`는 같은 실측에서 0건이라
#: 죽은 항목이지만, 향후 데이터에서 나올 수 있는 정당한 공공기관명이라 남겨 둔다.
PUBLIC_HINTS = frozenset({
    "방위사업청", "한국수력원자력", "한국중부발전", "한국남부발전", "한국동서발전",
    "한국서부발전", "한국남동발전", "한국전력공사", "한국가스공사", "조달청",
    "국방부", "산업통상자원부", "한국도로공사", "한국철도공사",
    "국방과학연구소", "한국토지주택공사", "대한무역투자진흥공사", "국가철도공단",
    "한국항공우주연구원", "국방기술진흥연구소",
})


def canonical(s: str) -> str:
    """표기 변이를 하나로 접는다. 비교 전용이며 표시용이 아니다."""
    out = _PAREN.sub("", s.strip())
    out = _SUFFIX.sub("", out)
    out = _PREFIX.sub("", out)
    out = re.sub(r"[\s·,\.]+", "", out)
    return out.lower()


@lru_cache(maxsize=1)
def _universe_canonical() -> frozenset[str]:
    """상장사 유니버스 이름의 canonical 집합. `classify` 를 문항마다 불러도 매번
    universe.csv 전체를 다시 접지 않도록 한다. `load_universe()` 자체도 이미
    `lru_cache` 라 파일 재읽기는 없으니 이건 그 위에 얹는 방어적 캐시일 뿐이다.
    """
    return frozenset(canonical(name) for name in load_universe())


def classify(s: str) -> str:
    """`universe` | `public` | `overseas` | `vague` | `other`."""
    if ANON_COUNTERPARTY.search(s):
        return "vague"
    key = canonical(s)
    if any(canonical(h) == key for h in PUBLIC_HINTS):
        return "public"
    if key in _universe_canonical():
        return "universe"
    if _OVERSEAS.search(s):
        return "overseas"
    return "other"
