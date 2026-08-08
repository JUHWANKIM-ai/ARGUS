"""질의 라우터. 규칙으로 갈리면 LLM 을 쓰지 않는다."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from argus.corpus.manifest import load_universe, resolve_company

_YEAR = re.compile(r"(20\d{2})\s*년?")
_COMPARE_HINT = ("비교", "중 어", "더 큰", "더 많", "대비", "vs", "와 ", "과 ")
_CALC_HINT = ("증감률", "증가율", "감소율", "비중", "몇 %", "몇 퍼센트", "합계", "총액", "차이")
_OPEN_HINT = ("정리해", "설명해", "어떻게 변화", "요약해", "알려줘")
_EXISTS_HINT = ("존재하는가", "있는가", "있나")


@dataclass
class Route:
    kind: str
    difficulty: str = "low"
    companies: list[str] = field(default_factory=list)
    years: list[int] = field(default_factory=list)
    use_cot: bool = False
    use_sc: bool = False
    by: str = "rule"


def _find_companies(question: str) -> list[str]:
    """universe 의 모든 별칭을 긴 것부터 매칭한다 (부분문자열 오탐 방지)."""
    found: list[str] = []
    aliases: list[tuple[str, str]] = []
    for corp_name, row in load_universe().items():
        for alias in (corp_name, row.get("listed_name"), row.get("corp_eng_name")):
            if alias and len(alias) >= 2:
                aliases.append((alias, corp_name))
    for alias, corp_name in sorted(aliases, key=lambda x: -len(x[0])):
        if alias in question and corp_name not in found:
            found.append(corp_name)
    return found


def route(question: str) -> Route:
    companies = _find_companies(question)
    years = sorted({int(y) for y in _YEAR.findall(question)})

    if not companies:
        return Route(kind="unanswerable", companies=[], years=years)

    is_open = any(h in question for h in _OPEN_HINT)
    is_calc = any(h in question for h in _CALC_HINT)
    is_compare = len(companies) >= 2 or any(h in question for h in _COMPARE_HINT)
    is_exists = any(h in question for h in _EXISTS_HINT)

    if is_open and (len(years) >= 2 or "변화" in question):
        return Route(kind="complex_reason", difficulty="high", companies=companies,
                     years=years, use_cot=True, use_sc=False)
    if is_compare or is_calc:
        return Route(kind="multi_compare", difficulty="high", companies=companies,
                     years=years, use_cot=True, use_sc=not is_open)
    if is_exists:
        return Route(kind="complex_reason", difficulty="high", companies=companies,
                     years=years, use_cot=True, use_sc=True)
    if is_open:
        return Route(kind="closed_lookup", difficulty="high", companies=companies,
                     years=years, use_cot=True, use_sc=False)
    return Route(kind="closed_lookup", difficulty="low", companies=companies,
                 years=years, use_cot=False, use_sc=False)
