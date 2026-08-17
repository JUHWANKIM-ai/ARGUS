"""Reasoning-quality checks on the argumentative form of an answer.

No LLM calls, no retrieval. Complements `critic`, which validates numeric
correctness; this module validates internal consistency of the statement.

Detection only. No disposition is applied, because repairing a self-negating
or misordered statement requires rewriting prose, which changes meaning.

Covered failure modes:

    numeric self-consistency   aggregate.fix_self_contradiction
    invalid equation           critic.verify_answer_arithmetic
    ungrounded figure          critic.verify_number_provenance
    ordering error             out_of_order (this module)
    self negation              self_negating (this module)

Two modes are not covered: missing premise and non-sequitur. Both require
reading the content of the argument.

See docs/design-rationale.md and docs/reasoning-metric-2026-08-16.md.
"""
from __future__ import annotations

import re

from argus.agent import aggregate

#: Refusal markers. Covers both the pipeline notice constants and the
#: paraphrases the model produces on its own.
_REFUSAL = ("확인되지 않습니다", "제공하지 않습니다", "제공해 드릴 수 없습니다",
            "판단을 생성하지 않습니다", "어느 기업을 말씀하시는지",
            "제공할 수 없습니다", "답변을 드릴 수 없습니다")

#: Monetary amount treated as a substantive answer. Requires at least two
#: comma groups so that years and ratios are excluded.
_MONEY = re.compile(r"\d{1,3}(?:,\d{3}){2,}\s*원")

#: Date paired with an amount in the same span, e.g. `2023-12-22 1,102,000,000,000원`.
_DATED = re.compile(r"(20\d{2})[.\-/년]\s*(1[0-2]|0?[1-9])[.\-/월]?\s*"
                    r"(?:(3[01]|[12]\d|0?[1-9])\s*일?)?[^0-9]{0,40}?"
                    r"(\d{1,3}(?:,\d{3}){2,})\s*원")


def body_of(answer: str) -> str:
    """Return the answer body with system-appended notes removed.

    Figures inside those notes are written by the system, not claimed by the
    model. The splitting rule is defined once in `aggregate.split_tail`.
    """
    return aggregate.split_tail(answer or "")[0]


def self_negating(answer: str) -> dict:
    """Detect a refusal and a substantive amount in the same answer body.

    An answer that declares a limit and then supplies a figure exceeding that
    limit contradicts itself. Only the body is examined; system-appended
    citations may legitimately contain amounts.

    Partial refusal is a valid form, so this check reports without applying a
    disposition. Measured firing rate on the 112-question evaluation set: 0.
    """
    text = body_of(answer)
    refusal = [k for k in _REFUSAL if k in text]
    money = _MONEY.findall(text)
    hit = bool(refusal and money)
    return {"ok": not hit, "kind": "self_negating",
            "why": (f"거절({refusal[0]})과 금액({money[0]})이 한 본문에 있다"
                    if hit else "")}


def out_of_order(answer: str) -> dict:
    """Detect a date-amount series that is neither ascending nor descending.

    Descending order is a valid narrative choice, so only shuffled sequences
    are reported. Fewer than three pairs yields no verdict, since two elements
    are ordered under either interpretation.
    """
    pairs = []
    for match in _DATED.finditer(body_of(answer)):
        year, month, day, _amount = match.groups()
        pairs.append(int(f"{year}{int(month):02d}{int(day or 1):02d}"))
    if len(pairs) < 3:
        return {"ok": True, "kind": "out_of_order", "why": "",
                "skipped": "날짜·금액 짝이 셋 미만"}
    ascending = all(a <= b for a, b in zip(pairs, pairs[1:]))
    descending = all(a >= b for a, b in zip(pairs, pairs[1:]))
    hit = not (ascending or descending)
    return {"ok": not hit, "kind": "out_of_order",
            "why": (f"날짜가 오름도 내림도 아니다: {pairs}" if hit else "")}


def check_all(answer: str) -> list[dict]:
    """Run every check in this module and return only the ones that fired."""
    return [r for r in (self_negating(answer), out_of_order(answer))
            if not r["ok"]]
