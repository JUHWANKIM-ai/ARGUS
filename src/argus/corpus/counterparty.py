"""Normalisation and classification of counterparty strings.

Separates names that can be promoted to graph nodes from those that cannot.
Anonymous descriptors such as "글로벌 대형기업" must not become nodes, because
distinct entities would collapse into one and corrupt the graph.

The normalisation and anonymity rules are the single source shared with
evalset validation. Variant folding covers five observed forms: country
prefix, legal-form suffix and prefix, whitespace, and parenthesised
abbreviation. `tests/test_evalset.py::COUNTERPARTY_VARIANTS` pins them.
"""
from __future__ import annotations

import re
from functools import lru_cache

from argus.corpus.manifest import load_universe

#: Variant folding. Single source shared with evalset validation.
_PAREN = re.compile(r"\([^)]*\)")
#: Korean legal-form markers. Deliberately unanchored: they attach as prefix
#: or suffix and bind directly to the proper noun without a particle.
_SUFFIX_KO = re.compile(r"(주식회사|\(주\)|㈜|\(유\))")
#: Latin legal-form markers. Letter boundaries are required so the pattern
#: never matches inside a word. Without them "Corporation" and "Lincoln"
#: were corrupted, and a false merge of two companies is the worst defect
#: here because it fabricates a value-chain link.
_SUFFIX_EN = re.compile(
    r"(?<![A-Za-z])(?:Co\.,?\s*Ltd\.?|Corp(?:oration)?\.?|Inc(?:orporated)?\.?"
    r"|Limited|LLC|S\.A\.)(?![A-Za-z])", re.I)
_PREFIX = re.compile(r"^대한민국\s*")

#: Anonymous counterparties. The source text does not name the entity, so it
#: cannot be promoted to a node. "글로벌" is bounded by whitespace or string
#: edges so that it fires only as a leading modifier, never as the tail of a
#: proper noun such as "청주글로벌".
ANON_COUNTERPARTY = re.compile(
    r"(지역\s*선주|지역\s*선사|소재\s*선사|(?:^|\s)글로벌(?:\s|$)|익명|비공개|"
    r"대형기업|중견기업|중소기업|업체$|제약사$|고객사|완성차)")

#: Overseas hint. Three or more consecutive Latin letters.
_OVERSEAS = re.compile(r"[A-Za-z]{3,}")

#: Domestic public institutions. These sit outside the listed-company
#: universe, so `classify` cannot reach them through universe matching.
#: Matched by prefix, which also covers names carrying a sub-organisation
#: suffix such as "산림청산림항공본부".
PUBLIC_HINTS = frozenset({
    "방위사업청", "한국수력원자력", "한국중부발전", "한국남부발전", "한국동서발전",
    "한국서부발전", "한국남동발전", "한국전력공사", "한국가스공사", "조달청",
    "국방부", "산업통상자원부", "한국도로공사", "한국철도공사",
    "국방과학연구소", "한국토지주택공사", "대한무역투자진흥공사", "국가철도공단",
    "한국항공우주연구원", "국방기술진흥연구소", "산림청",
})


def canonical(s: str) -> str:
    """Fold notation variants into one comparison key. Not for display.

    If folding consumes the entire string, the unfolded form is restored.
    An empty key would merge unrelated names and would make the prefix match
    in `classify` meaningless. Short results are not filtered, since "기아"
    and "sr" are both legitimate company names.
    """
    folded = _PAREN.sub("", s.strip())
    folded = _SUFFIX_KO.sub("", folded)
    folded = _SUFFIX_EN.sub("", folded)
    folded = _PREFIX.sub("", folded)
    out = re.sub(r"[\s·,\.]+", "", folded)
    if not out:
        out = re.sub(r"\s+", "", s.strip())
    return out.lower()


@lru_cache(maxsize=1)
def _universe_canonical() -> frozenset[str]:
    """Canonical keys of the listed-company universe. Cached per process."""
    return frozenset(canonical(name) for name in load_universe())


def classify(s: str) -> str:
    """`universe` | `public` | `overseas` | `vague` | `other`."""
    if ANON_COUNTERPARTY.search(s):
        return "vague"
    key = canonical(s)
    # Prefix match rather than exact match, to cover names carrying a
    # sub-organisation suffix. Foreign institutions take the form
    # "<country><institution>", so they do not collide with these prefixes.
    if any(key.startswith(canonical(h)) for h in PUBLIC_HINTS):
        return "public"
    if key in _universe_canonical():
        return "universe"
    if _OVERSEAS.search(s):
        return "overseas"
    return "other"
