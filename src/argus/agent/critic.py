"""Support verification: cheaper and more accurate than generating again.

The module carries five responsibilities, and this list decides where new code
belongs.

1. Deterministic checks, none of which call an LLM: citation presence,
   recomputation against the source records, arithmetic internal to the answer,
   provenance of the answer's figures, and completeness of the evidence.
2. Parsing of amounts, ratios and scale markers. This is the single source for
   that in the repository. The pattern in `scripts/error_analysis` is a
   deliberate variant rather than a copy: its marker is optional and it offers
   both the converted and the exact figure as candidates, so merging the two
   would shrink the candidate set and change scoring.
3. Machinery that rewrites the answer rather than judging it, shared by the
   substitution arms.
4. The LLM judges. The only place in this module that calls an LLM.
5. Offline scoring and aggregation helpers, used by the analysis scripts rather
   than by the operational path.

This module knows nothing about ablation arms: it imports no flags and reads
none. All arm gating lives in the pipeline. That is deliberate. Branching on an
arm here would make the determinism of a check depend on the arm, and a delta
could no longer be separated into a change of check and a change of arm.
"""
from __future__ import annotations

import itertools
import json
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass

from argus.agent import compute
from argus.agent.budget import Budget
from argus.agent.prompts import (CITATION_RE, build_critic_messages,
                                 build_open_judge_messages)
from argus.clova import chat

_JSON = re.compile(r"\{.*\}", re.DOTALL)


@dataclass
class Verdict:
    supported: bool
    has_citation: bool
    hallucination: bool
    reason: str

    @property
    def passed(self) -> bool:
        return self.supported and self.has_citation and not self.hallucination


@dataclass
class OpenVerdict:
    """Open-question scoring result. The three criteria are not summed."""
    covers_key_points: bool
    grounded: bool
    no_fabrication: bool
    reason: str

    @property
    def correct(self) -> bool:
        return self.covers_key_points and self.grounded and self.no_fabrication


def quick_check(answer: str) -> dict:
    """Pre-check with no LLM call. Passing here saves a judge call."""
    return {"has_citation": bool(CITATION_RE.search(answer or ""))}


# --- Calculation verification. No LLM call ----------------------------------
# The inverse of the rule that calculation belongs to a tool: if it does, so
# does verifying it. Figures are extracted from the answer and compared against
# values recomputed from the source records.

_PCT_RE = re.compile(r"(-?\d[\d,]*(?:\.\d+)?)\s*%")
_DUR_RE = re.compile(r"(\d+)\s*년\s*(\d+)\s*개월")
#: Single source for the scale markers, shared by the money pattern and the
#: unit lookup. Order matters: a longer marker has to precede a shorter one it
#: contains, or the alternation splits it and the scale comes out wrong by two
#: orders of magnitude. One marker is deliberately absent, because in the
#: recorded answers it never appears as a standalone amount and adding it
#: creates a false alarm on a correct answer.
#: See docs/design-rationale.md#money-scale-units
_SCALE_UNITS = ("조원", "천억원", "백억원", "십억원", "억원",
                "백만원", "만원", "천원", "조", "억", "원")

#: Whitespace is allowed inside a marker, not only between the figure and the
#: marker. Periodic filings commonly space their markers out, and without this
#: those amounts were not seen as amounts at all. Only horizontal whitespace is
#: allowed: matching any whitespace would join a figure and a unit split across
#: two table lines into an amount that does not exist.
#: See docs/design-rationale.md#money-scale-units
_SCALE_SP = "[ \t\u00a0]*"
_SCALE_ALT = "|".join(_SCALE_SP.join(unit) for unit in _SCALE_UNITS)
_MONEY_RE = re.compile(rf"(-?\d[\d,]*(?:\.\d+)?)\s*({_SCALE_ALT})")
_BARE_RE = re.compile(r"-?\d[\d,]*(?:\.\d+)?")
_NUMERIC_CELL = re.compile(r"^-?[\d,]+(?:\.\d+)?$")
_TOTAL_KW = re.compile(r"합계|합산|총계|총액|총합")
_MEAN_KW = re.compile(r"평균")
_WS = re.compile(r"\s+")

#: Where the answer divided by a unit count itself. The literal `1` is
#: required: matching the counter word alone also matches ordinary prose, and
#: if that sentence happens to carry a count as well, a correct answer is
#: turned into a false alarm.
_UNITS = "척|건|개|기|호기|대|동|기지"
_PER_UNIT_KW = re.compile(rf"1\s*({_UNITS})\s*당")
_COUNT_KW = re.compile(rf"(\d[\d,]*)\s*({_UNITS})")

#: Marker to multiplier. Several markers were missing here at various points,
#: and each omission made a whole class of amounts invisible to the parser
#: rather than merely mis-scaled.
#: See docs/design-rationale.md#money-scale-units
_SCALE = {"원": 1.0, "천원": 1e3, "만원": 1e4, "백만원": 1e6,
          "억": 1e8, "억원": 1e8, "십억원": 1e9, "백억원": 1e10, "천억원": 1e11,
          "조": 1e12, "조원": 1e12}

#: The marker tuple and the multiplier mapping have to name the same set.
#: Extending one alone either raises at parse time or leaves the new marker
#: silently unseen.
assert set(_SCALE_UNITS) == set(_SCALE), sorted(set(_SCALE_UNITS) ^ set(_SCALE))


def _scale_of(marker: str) -> float:
    """Multiplier for a marker, looked up with whitespace inside the marker
    removed."""
    return _SCALE["".join(marker.split())]

#: Rounding tolerance of one decimal place.
ROUND_TOLERANCE = 0.05

#: Failure stage a calculation mismatch is attributed to.
FAILURE_STAGE = "TOOL"

# --- Order-of-magnitude errors, excluded from the tolerance -----------------
# Selecting the answer figure closest to the expected value conflates two
# jobs: choosing which figure refers to this record, which is necessary when
# the evidence holds several records, and burying a candidate that differs by
# an order of magnitude as a merely mismatched neighbour, which is a defect.
#
# Two contract amounts are never exactly a power of ten apart by coincidence in
# this corpus, so a magnitude candidate is reported separately even when
# another candidate matches, and is not cleared by the per-record tolerance.
# See docs/design-rationale.md#magnitude-errors

#: Log10 distance from a power of ten within which a mismatch is treated as a
#: magnitude error.
SCALE_LOG_TOLERANCE = 0.01

#: Values below this are excluded from the magnitude test. Parsing residue of a
#: few won stands in a power-of-ten relation to almost any large value.
SCALE_MIN_VALUE = 1e6

#: Beyond this exponent the relation is a different value rather than a unit
#: mistake. Won to trillion is 10^12, so this leaves ample room.
SCALE_MAX_EXP = 7


def _scale_exponent(claimed: float, expected: float) -> int | None:
    """Whether `claimed` is `expected` times a non-zero power of ten. Returns
    the exponent, else None."""
    if not claimed or not expected:
        return None
    if abs(claimed) < SCALE_MIN_VALUE or abs(expected) < SCALE_MIN_VALUE:
        return None
    if (claimed > 0) != (expected > 0):
        return None
    distance = math.log10(abs(claimed) / abs(expected))
    exponent = round(distance)
    if exponent == 0 or abs(exponent) > SCALE_MAX_EXP:
        return None
    if abs(distance - exponent) > SCALE_LOG_TOLERANCE:
        return None
    return exponent


#: Window for choosing where to substitute. A figure within this log10
#: distance of the expected value is treated as a place the answer meant to
#: state that value. The constant exists because one answer can write the same
#: value in two notations, and replacing only the reported one leaves the
#: answer contradicting itself. Figures outside the window are left alone.
#: See docs/design-rationale.md#substitution-window
SUBSTITUTE_LOG_TOLERANCE = 0.05


def _to_float(text) -> float | None:
    if isinstance(text, (int, float)):
        return float(text)
    if not isinstance(text, str) or not _NUMERIC_CELL.match(text.strip()):
        return None
    try:
        return float(text.strip().replace(",", ""))
    except ValueError:
        return None


def _half_ulp(literal: str, scale: float = 1.0) -> float:
    """Half of the last place the answer wrote, so a figure stated in a coarse
    unit compares equal within that unit."""
    decimals = len(literal.split(".")[1]) if "." in literal else 0
    return 0.5 * (10.0 ** -decimals) * scale


def _percent_claims(answer: str) -> list[tuple[float, float, str]]:
    out = []
    for m in _PCT_RE.finditer(answer):
        value = _to_float(m.group(1))
        if value is not None:
            out.append((value, max(ROUND_TOLERANCE, _half_ulp(m.group(1))), m.group(0)))
    return out


#: The verifier could not read the notation the prompt itself asks for. The
#: money pattern splits a compound figure into two separate amounts, which
#: raised magnitude false alarms on correct answers, and magnitude findings are
#: deliberately outside the per-record tolerance so nothing absorbs them.
#: Compound notation is therefore matched first and joined into one value.
_COMPOUND_MONEY_RE = re.compile(
    r"(-?\d[\d,]*)\s*조\s*(\d[\d,]*)\s*억\s*원?")


def _money_spans(answer: str) -> list[tuple[int, int, float, float, str]]:
    """Amount mentions with their positions: (start, end, value, tolerance,
    literal).

    Split out from the value-only accessor so that checks needing positions can
    share it. The parsing rule stays in one place.
    """
    out: list[tuple[int, int, float, float, str]] = []
    seen: list[tuple[int, int]] = []
    for m in _COMPOUND_MONEY_RE.finditer(answer or ""):
        jo, eok = _to_float(m.group(1)), _to_float(m.group(2))
        if jo is None or eok is None:
            continue
        value = jo * 1e12 + eok * 1e8
        out.append((m.start(), m.end(), value, _half_ulp(m.group(2), 1e8), m.group(0)))
        seen.append(m.span())
    for m in _MONEY_RE.finditer(answer or ""):
        # Do not count the parts of a compound figure a second time.
        if any(s <= m.start() and m.end() <= e for s, e in seen):
            continue
        value = _to_float(m.group(1))
        if value is not None:
            scale = _scale_of(m.group(2))
            out.append((m.start(), m.end(), value * scale,
                        _half_ulp(m.group(1), scale), m.group(0)))
    return sorted(out)


def _money_claims(answer: str) -> list[tuple[float, float, str]]:
    return [(value, tol, literal) for _, _, value, tol, literal in _money_spans(answer)]


def money_values(text: str) -> list[float]:
    """Values of the amounts a text states. The public accessor.

    Callers reaching past this into the span function would create a second
    place that reads the parsing rule, the same failure the single marker list
    prevents.
    """
    return [value for _, _, value, _, _ in _money_spans(text or "")]


def _bare_claims(answer: str) -> list[tuple[float, float, str]]:
    out = []
    for m in _BARE_RE.finditer(answer):
        value = _to_float(m.group(0))
        if value is not None:
            out.append((value, max(ROUND_TOLERANCE, _half_ulp(m.group(0))), m.group(0)))
    return out


def _fields(evidence) -> dict:
    """Accepts both the tool return shape and a single record."""
    if not isinstance(evidence, dict):
        return {}
    for key in ("events", "records", "chunks", "filings"):
        rows = evidence.get(key)
        if isinstance(rows, list) and rows and isinstance(rows[0], dict):
            merged = dict(rows[0])
            merged.update({k: v for k, v in evidence.items() if k != key})
            return merged
    return evidence


def _pick(fields: dict, *keys) -> float | None:
    for key in keys:
        value = fields.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
    return None


def _squash(text) -> str:
    """Whitespace-stripped copy: answers space out the labels they quote."""
    return _WS.sub("", text if isinstance(text, str) else "")


def _says_amount(answer: str, label) -> bool:
    """Whether the answer states this record's amount, decided by its label
    alone.

    Amount extraction collects every figure in the answer, so an answer that
    correctly quotes a sub-item of a record was compared against the record's
    headline amount and turned into a failure. An answer that never names the
    record's amount label never claimed that amount: the check is skipped
    rather than failed.

    Evidence with no label carries nothing to decide on, so no gate is applied
    and the prior behaviour stands.
    """
    if not isinstance(label, str) or not label:
        return True
    return _squash(label) in _squash(answer)


def _targets(expected: float, claims: list[tuple[float, float, str]]) -> list[str]:
    """Literals of candidates at the expected magnitude but with a different
    value: the places substitution replaces.

    The mismatch report names a single candidate, but substitution has to
    replace every other notation of the same value left in the answer.
    See docs/design-rationale.md#substitution-window
    """
    out = []
    for value, tol, literal in claims:
        if abs(value - expected) <= tol + 1e-9:
            continue                          # 맞는 후보다 — 건드리지 않는다
        if not value or not expected or (value > 0) != (expected > 0):
            continue
        if abs(math.log10(abs(value) / abs(expected))) > SUBSTITUTE_LOG_TOLERANCE:
            continue                          # 다른 수치다 (총액·다른 계약)
        out.append(literal)
    return list(dict.fromkeys(out))


def _revenue(fields: dict) -> float | None:
    """Denominator for a ratio. Falls back to the revenue row of the source
    table when no explicit key is present."""
    explicit = _pick(fields, "revenue", "whole", "share_whole", "ratio_denominator")
    if explicit:
        return explicit
    for row in fields.get("raw_rows") or []:
        cells = [str(c) for c in row]
        if not any("최근매출액" in c for c in cells):
            continue
        for cell in cells:
            value = _to_float(cell)
            if value:
                return value
    return None


def verify_calculation(answer: str, evidence: dict) -> dict:
    """Parse figures out of the answer, recompute them from the records and
    compare. No LLM call.

    Figures and units are extracted from the answer text, recomputed from the
    source values of the evidence records, and reported as a mismatch when they
    fall outside the rounding tolerance.

    An answer that never states the figure is skipped rather than failed:
    conflating nothing to verify with a wrong value would corrupt the
    failure-stage attribution.
    """
    text = answer or ""
    fields = _fields(evidence)
    checked: list[str] = []
    skipped: list[str] = []
    gated: list[str] = []
    mismatches: list[dict] = []

    def _compare(kind: str, expected: float, claims: list[tuple[float, float, str]],
                 detail: str, *, scale_guard: bool = False) -> None:
        """Pass when any candidate matches, but report a candidate that differs
        by an order of magnitude separately.

        The magnitude guard applies to amounts only. Legitimate ratios from
        different records sit near a factor of ten from each other routinely,
        so the same rule cannot be applied to them.
        See docs/design-rationale.md#magnitude-errors
        """
        if not claims:
            skipped.append(kind)
            return
        checked.append(kind)

        if scale_guard:
            for value, tol, literal in claims:
                exponent = _scale_exponent(value, expected)
                if exponent is not None:
                    mismatches.append({
                        "kind": kind, "claimed": literal, "claimed_value": value,
                        "expected": expected, "tolerance": tol, "detail": detail,
                        "scale": exponent, "hard": True, "targets": [literal]})

        if any(abs(value - expected) <= tol + 1e-9 for value, tol, _ in claims):
            return
        if any(m["kind"] == kind and m.get("hard") for m in mismatches):
            return                       # 같은 값을 자릿수 오류로 이미 보고했다
        value, tol, literal = min(claims, key=lambda c: abs(c[0] - expected))
        mismatches.append({"kind": kind, "claimed": literal, "claimed_value": value,
                           "expected": expected, "tolerance": tol, "detail": detail,
                           "targets": _targets(expected, claims)})

    # Contract duration, the field the model gets wrong most often.
    start, end = fields.get("period_start"), fields.get("period_end")
    if start and end:
        expected_text = compute.period_text(str(start), str(end))
        found = [(int(y), int(mo)) for y, mo in _DUR_RE.findall(text)]
        if not (expected_text and found):
            skipped.append("period")
        else:
            checked.append("period")
            parts = compute.period_parts(str(start), str(end))
            if parts not in found:
                y, mo = found[0]
                mismatches.append({
                    "kind": "period", "claimed": f"{y}년 {mo}개월",
                    "claimed_value": y * 12 + mo, "expected": expected_text,
                    "expected_value": parts[0] * 12 + parts[1], "tolerance": 0,
                    # The expected value is a phrase, not a number, so it is
                    # not a substitution target.
                    "detail": f"{start} ~ {end}", "targets": []})

    # Amount, checked for magnitude mistakes, but only when the answer states
    # this record's amount. One record carries sub-items under its headline
    # amount, so without reading the label a correct answer about a sub-item
    # is turned into a failure.
    amount = _pick(fields, "amount")
    if amount is not None:
        if _says_amount(text, fields.get("amount_type")):
            _compare("amount", amount, _money_claims(text), "근거 레코드 amount",
                     scale_guard=True)
        else:
            # Recording a closed gate as merely skipped makes it
            # indistinguishable from silence. An answer that never stated the
            # figure and a check disabled because the label did not match are
            # different events, and the second has to be counted.
            skipped.append("amount")
            gated.append("amount")

    # Per-unit value: redo the division the answer performed itself. An answer
    # can transcribe the total correctly, so the amount check passes, and still
    # get the per-unit figure the question actually asked for wrong. The
    # numerator is this record's amount, so the same label gate applies.
    if amount is not None and _says_amount(text, fields.get("amount_type")):
        unit = _PER_UNIT_KW.search(text)
        if unit:
            counts = sorted({int(m.group(1).replace(",", ""))
                             for m in _COUNT_KW.finditer(text)
                             if m.group(2) == unit.group(1)
                             and int(m.group(1).replace(",", "")) > 1})
            if len(counts) == 1:
                _compare("per_unit", amount / counts[0], _money_claims(text),
                         f"{compute.fmt_num(amount)} / {counts[0]}{unit.group(1)}")
            else:
                # The divisor is not pinned down, so there is nothing to
                # call wrong and the check passes.
                skipped.append("per_unit")

    # Ratio, recomputed as the amount over the revenue.
    part = _pick(fields, "share_part", "part", "amount")
    whole = _revenue(fields)
    ratio = _pick(fields, "ratio")
    fmt = compute.fmt_num
    if part is not None and whole:
        _compare("share", compute.share(part, whole), _percent_claims(text),
                 f"{fmt(part)} / {fmt(whole)} × 100")
    elif ratio is not None:
        _compare("share", ratio, _percent_claims(text), "근거 레코드 ratio")

    # Percentage change.
    before, after = _pick(fields, "before"), _pick(fields, "after")
    if before is not None and after is not None and before != 0:
        _compare("pct_change", compute.pct_change(before, after), _percent_claims(text),
                 f"({fmt(after)} - {fmt(before)}) / {fmt(before)} × 100")

    # Compound annual growth rate.
    begin, cagr_end, years = _pick(fields, "begin"), _pick(fields, "end"), _pick(fields, "years")
    if begin is not None and cagr_end is not None and years and begin > 0 and cagr_end >= 0:
        _compare("cagr", compute.cagr(begin, cagr_end, years), _percent_claims(text),
                 f"(({fmt(cagr_end)} / {fmt(begin)})^(1/{fmt(years)}) - 1) × 100")

    # Total and mean share the same values, so each is checked only when the
    # answer says it computed that one.
    values = fields.get("values")
    if isinstance(values, list) and values and all(isinstance(v, (int, float)) for v in values):
        if _TOTAL_KW.search(text):
            _compare("total", compute.total(values), _bare_claims(text), "values 합계")
        else:
            skipped.append("total")
        if _MEAN_KW.search(text):
            _compare("mean", compute.mean(values), _bare_claims(text), "values 평균")
        else:
            skipped.append("mean")

    failed = sorted({m["kind"] for m in mismatches})
    hard = sorted({m["kind"] for m in mismatches if m.get("hard")})
    return {
        "ok": not mismatches,
        "llm_calls": 0,
        "checked": checked,
        "skipped": skipped,
        "gated": gated,
        "mismatches": mismatches,
        "failed_kinds": failed,
        "hard_kinds": hard,
        "failure_stage": FAILURE_STAGE if mismatches else None,
        "reason": "; ".join(_mismatch_reason(m) for m in mismatches),
        # Recomputed values are emitted as a structured field as well.
        # Previously they existed only inside the reason sentence, where
        # substitution could not use them. The format of `reason` is left
        # unchanged, because the analysis scripts and the gate documents read
        # it.
        "recomputed": _recomputed(mismatches),
    }


def _recomputed(mismatches: Sequence[dict]) -> dict[str, float]:
    """Collect only the recomputed values usable for substitution, keyed by
    check kind, under two conditions.

    The expected value has to be numeric: a duration is a phrase and cannot be
    swapped in. And the check has to have pinned down where in the answer to
    substitute: a restatement mismatch does not know which of the two
    notations, the exact figure or the parenthesised conversion, is the wrong
    one.

    A kind whose values diverge is dropped, the same judgment the aggregation
    across records makes and for the same reason: neither direction of
    substitution is supported.
    """
    values: dict[str, set[float]] = {}
    for mismatch in mismatches:
        expected = mismatch.get("expected")
        if isinstance(expected, bool) or not isinstance(expected, (int, float)):
            continue
        if not mismatch.get("targets"):
            continue
        values.setdefault(mismatch["kind"], set()).add(float(expected))
    return {kind: value.pop() for kind, value in values.items() if len(value) == 1}


def _mismatch_reason(mismatch: dict) -> str:
    text = (f"{mismatch['kind']}: 답변 {mismatch['claimed']} "
            f"vs 재계산 {_show(mismatch['expected'])}")
    exponent = mismatch.get("scale")
    if exponent:
        text += f" (10^{exponent:+d} 배 자릿수 오류)"
    return text


def aggregate_calculation(reports: Sequence[dict]) -> dict:
    """Combine the per-record verdicts into one. No LLM call.

    The callers used to write this rule out by hand in three lines; it lives
    here so the two of them cannot drift apart.

    The tolerance is preserved: one explained record is enough. Amounts within
    a revision chain legitimately differ between the original and the revision,
    so requiring every record to match would break correct answers.

    One thing changes. A magnitude error survives even when another record
    explains the answer. The chain tolerance exists for the same value at
    different points in time, and a magnitude error is not a timing problem.
    See docs/design-rationale.md#magnitude-errors

    The recomputed values are emitted alongside, keyed by check kind, and are
    None where they are not unique.
    """
    rows = list(reports or ())
    checked = sorted({k for rep in rows for k in rep.get("checked") or ()})
    hard = sorted({k for rep in rows for k in rep.get("hard_kinds") or ()})
    explained = any(rep.get("ok") and rep.get("checked") for rep in rows)
    reasons = ([rep["reason"] for rep in rows if rep.get("hard_kinds") and rep.get("reason")]
               + [rep["reason"] for rep in rows
                  if not rep.get("hard_kinds") and rep.get("reason")])
    return {
        "llm_calls": 0,
        "n_records": len(rows),
        "checked": checked,
        # Records whose check the label gate disabled. Anything above zero is
        # that many records not looked at.
        "n_gated": sum(1 for rep in rows if rep.get("gated")),
        "explained": explained,
        "hard_kinds": hard,
        "failed": bool(hard) or (bool(checked) and not explained),
        "failure_stage": FAILURE_STAGE if (hard or (checked and not explained)) else None,
        "reasons": reasons[:3],
        "recomputed": unique_recomputed(rows),
    }


def unique_recomputed(reports: Sequence[dict]) -> dict[str, float] | None:
    """Whether the recomputed values across records are unique. Divergent
    values return None and nothing is substituted.

    Same reason as the tolerance above: amounts within a revision chain
    legitimately differ, and substituting either one would break a correct
    answer.

    The aggregation helper and the pipeline both call this one function.
    Keeping the uniqueness rule in two places is a failure this repository has
    had many times. The pipeline calls it over the union of the record
    verdicts and the answer-arithmetic verdict.
    """
    values: dict[str, set[float]] = {}
    for rep in reports or ():
        for kind, value in (rep.get("recomputed") or {}).items():
            values.setdefault(kind, set()).add(value)
    return ({k: v.pop() for k, v in values.items() if len(v) == 1}
            if values and all(len(v) == 1 for v in values.values()) else None)


def _show(expected) -> str:
    """Expected value as written into the trace, without leaking full float
    precision."""
    if isinstance(expected, (int, float)) and not isinstance(expected, bool):
        return compute.fmt_num(round(float(expected), 1))
    return str(expected)


# --- Internal consistency of the answer. No LLM call ------------------------
# Calculation verification compares against the source records, so it sees
# nothing when there are none, which is the case for periodic filings.
#
# This axis is different: whether two notations the answer wrote itself
# disagree. It reads no evidence at all, so it works with zero records.
#
#   equation     an inline equation
#   restatement  an exact figure paired with its unit conversion
#
# The restatement check answers the pair the money rules create: writing the
# exact figure alongside its conversion prevents one failure mode and creates a
# place where the two can disagree.
#
# The tolerance is one place of the stated unit rather than half of it, because
# the model both rounds and truncates.
# See docs/design-rationale.md#answer-internal-consistency

#: Equations are read as addition and subtraction chains only. Mixing in
#: multiplication or division would make left-to-right evaluation get operator
#: precedence wrong, and no such chain appears in the recorded answers. A
#: broken chain simply fails to match, so the failure mode is silence.
#:
#: Emphasis markup after the equals sign is allowed through: an equation whose
#: result is wrapped in bold otherwise failed to match at all, which is
#: indistinguishable from a broken chain.
#: See docs/design-rationale.md#answer-internal-consistency
_EQ_NUM = r"-?\d[\d,]*(?:\.\d+)?"
_EQ_OP = r"[+\-−]"
_EQUATION_RE = re.compile(rf"({_EQ_NUM})((?:\s*{_EQ_OP}\s*{_EQ_NUM})+)\s*=[\s*_]*({_EQ_NUM})")
_EQ_TERM = re.compile(rf"({_EQ_OP})\s*({_EQ_NUM})")

#: Scale marker immediately following a figure, from the same marker list as
#: the money pattern.
_UNIT_AT = re.compile(rf"\s*({_SCALE_ALT})")

#: Boundaries of a restatement pair. Bold markup can appear between the two
#: notations.
_RESTATE_OPEN = re.compile(r"^[\s*_]*\($")
_RESTATE_CLOSE = re.compile(r"^[\s*_]*\)")

#: Kinds of internal-consistency check. Attributed to the tool stage for the
#: same reason as calculation verification: transcription and addition are work
#: a calculation tool should have done rather than generation.
ARITHMETIC_KINDS = ("equation", "restatement")


def _unit_at(text: str, pos: int) -> str:
    """Scale marker following a figure, returned with whitespace removed.
    Without normalising, the same marker written two ways within one equation
    reads as mixed units and the check passes silently."""
    match = _UNIT_AT.match(text, pos)
    return "".join(match.group(1).split()) if match else ""


def _equation_mismatches(text: str) -> list[dict]:
    """Recompute an addition and subtraction chain the answer wrote itself."""
    out: list[dict] = []
    for m in _EQUATION_RE.finditer(text):
        total = _to_float(m.group(1))
        claimed = _to_float(m.group(3))
        if total is None or claimed is None:
            continue
        units = {_unit_at(text, m.end(1)), _unit_at(text, m.end(3))}
        tolerance = 2 * (_half_ulp(m.group(1)) + _half_ulp(m.group(3)))
        broken = False
        for term in _EQ_TERM.finditer(m.group(2)):
            value = _to_float(term.group(2))
            if value is None:
                broken = True
                break
            total = total + value if term.group(1) == "+" else total - value
            tolerance += 2 * _half_ulp(term.group(2))
            units.add(_unit_at(text, m.start(2) + term.end(2)))
        if broken:
            continue
        # An equation whose two sides carry different units is a conversion
        # rather than a mismatch, and is legitimate.
        if len({u for u in units if u}) > 1:
            continue
        if abs(total - claimed) > tolerance + 1e-9:
            out.append({"kind": "equation", "claimed": " ".join(m.group(0).split()),
                        "claimed_value": claimed, "expected": total,
                        "tolerance": tolerance, "detail": "답변이 쓴 등식", "hard": True,
                        # Only the right-hand side is a substitution target.
                        # The reported literal is the whole equation with
                        # whitespace folded and is not a substring of the
                        # answer. The left-hand terms are values copied from
                        # the evidence; what is wrong is their sum.
                        "targets": [m.group(3)]})
    return out


def _restatement_pairs(text: str) -> list[tuple]:
    """Amount pairs written as an exact figure followed by its conversion. The
    number of times the premise held is counted separately."""
    spans = _money_spans(text)
    return [(a, b) for a, b in zip(spans, spans[1:])
            if _RESTATE_OPEN.match(text[a[1]:b[0]])
            and _RESTATE_CLOSE.match(text[b[1]:b[1] + 4])]


def _restatement_mismatches(text: str) -> list[dict]:
    """Whether the two notations of a pair state the same amount. This answers
    the pair the money rules create."""
    out: list[dict] = []
    for first, second in _restatement_pairs(text):
        tolerance = 2 * (first[3] + second[3])
        if abs(first[2] - second[2]) > tolerance + 1e-9:
            out.append({"kind": "restatement",
                        "claimed": f"{first[4]} ({second[4]})",
                        "claimed_value": first[2], "expected": second[2],
                        "tolerance": tolerance, "detail": "원값과 괄호 환산값이 다르다",
                        # No substitution target: this check cannot tell
                        # which of the two notations is wrong, and the
                        # recorded cases go in both directions.
                        # See docs/design-rationale.md#recomputed-substitution
                        "hard": True, "targets": []})
    return out


def verify_answer_arithmetic(answer: str) -> dict:
    """Recompute the answer's internal consistency. Reads no evidence. No LLM
    call.

    Same return shape as the other deterministic checks. With no equation and
    no restatement pair, the premise does not hold and the result is skipped:
    conflating nothing to check with a defect would corrupt the failure-stage
    attribution.

    Deliberately not folded into calculation verification. That one runs once
    per evidence record while this runs once per answer and depends on no
    record, so folding it in would stop it running on questions with zero
    records and would invert the per-record tolerance.
    See docs/design-rationale.md#answer-internal-consistency
    """
    text = answer or ""
    mismatches = _equation_mismatches(text) + _restatement_mismatches(text)
    premises = {"equation": bool(_EQUATION_RE.search(text)),
                "restatement": bool(_restatement_pairs(text))}
    checked = [k for k in ARITHMETIC_KINDS if premises[k]]
    skipped = [k for k in ARITHMETIC_KINDS if not premises[k]]
    failed = sorted({m["kind"] for m in mismatches})
    return {
        "ok": not mismatches,
        "llm_calls": 0,
        "checked": checked,
        "skipped": skipped,
        "mismatches": mismatches,
        "failed_kinds": failed,
        "hard_kinds": failed,
        "failure_stage": FAILURE_STAGE if mismatches else None,
        "reason": "; ".join(_mismatch_reason(m) for m in mismatches),
        # Same shape as calculation verification. Only the equation kind
        # appears here, since a restatement mismatch pins down no substitution
        # target.
        "recomputed": _recomputed(mismatches),
    }


def substitute_recomputed(answer: str, reports: Sequence[dict],
                          recomputed: dict[str, float] | None) -> tuple[str, list[str]]:
    """Replace a figure in the answer with the value a check recomputed.

    Returns (new answer, places replaced). No LLM call.

    The checks were already recomputing the correct value exactly, and the
    pipeline was attaching it as a note and discarding it.

    Three safeguards: nothing happens without a unique recomputed value, a
    compound notation is left alone because there is no single place to change,
    and replacement happens only at digit boundaries so that a figure is not
    matched inside a longer one.

    A known limit is that this runs once. When two kinds fire on one answer,
    substituting for the first can stale the recomputation of the second. It is
    deliberately not iterated to convergence, which would make the arm measure
    repeated correction rather than substitution.
    See docs/design-rationale.md#recomputed-substitution
    """
    if not recomputed:
        return answer, []
    out, done = answer or "", []
    for rep in reports or ():
        for mismatch in rep.get("mismatches") or ():
            expected = recomputed.get(mismatch.get("kind"))
            if expected is None:
                continue
            for target in mismatch.get("targets") or ():
                rendered = _rendered(target, expected)
                if rendered is None or rendered == target:
                    continue
                pattern = re.compile(rf"(?<![\d.,]){re.escape(target)}(?![\d,]*\d)")
                swapped, hits = pattern.subn(rendered, out)
                if not hits:
                    continue
                out = swapped
                done.append(f"{mismatch['kind']}: {target} -> {rendered}")
    return out, done


def _rendered(target: str, expected: float) -> str | None:
    """Replace the value inside a target while keeping its notation, or return
    None to leave it alone.

    The scale and decimal places the answer wrote are preserved, since forcing
    the recomputed value in base units would break the sentence.

    The scale is read from the target's own marker rather than derived from the
    reported value, because the target list also holds notations other than the
    reported one and dividing by that value yields a scale slightly off one,
    which would stamp a subtly wrong figure.
    See docs/design-rationale.md#recomputed-substitution
    """
    numbers = _BARE_RE.findall(target or "")
    if len(numbers) != 1:
        return None                        # compound notation, no single place
    if _to_float(numbers[0]) is None:
        return None
    money = _MONEY_RE.fullmatch((target or "").strip())
    scale = _scale_of(money.group(2)) if money else 1.0
    decimals = len(numbers[0].split(".")[1]) if "." in numbers[0] else 0
    value = round(expected / scale, decimals)
    text = f"{value:,.{decimals}f}" if decimals else compute.fmt_num(value)
    return target.replace(numbers[0], text, 1)


# --- Tool result substitution. No LLM call ----------------------------------
# Aimed at the first reason function calling was rejected: a tool returned a
# value identical to the golden answer and the model transcribed it wrongly. A
# tool can do the calculation but cannot do the transcription.
#
# Same mechanism as the recomputed substitution, with a different source, so
# the target selection, uniqueness rule, substitution and rendering are shared
# rather than reimplemented.
# See docs/design-rationale.md#tool-result-verification

#: Kind prefix for a tool-result check; the tool name is appended. Splitting by
#: tool is what lets the uniqueness rule see one tool called twice with
#: divergent values. Merged into one kind, two legitimate values from different
#: tools would read as a conflict.
TOOL_RESULT_KIND = "tool_result"


def tool_result_reports(answer: str, results: Sequence[tuple[str, float]]) -> list[dict]:
    """One verdict record per tool call, in the same return shape as
    calculation verification so the uniqueness and substitution helpers accept
    it directly. No LLM call.

    `results` is a sequence of (tool name, numeric result). What counts as
    numeric is decided by the caller, which passes only the numeric values of
    the calculation tools.

    Keeping one record per call is a condition of the design: merged into one
    record, the collector would silently drop only the divergent kind and
    substitute the rest, and tool results have no basis for that tolerance.

    Divergence is decided on the values themselves rather than on whether a
    mismatch was raised, because a divergent second value that matches nothing
    in the answer raises no mismatch and would leave the first value to be
    stamped in.
    See docs/design-rationale.md#tool-result-verification
    """
    rows = list(results or ())
    values: dict[str, set[float]] = {}
    for name, value in rows:
        values.setdefault(name, set()).add(float(value))
    split = {name for name, seen in values.items() if len(seen) > 1}
    return [_tool_result_report(answer, name, value, name in split)
            for name, value in rows]


def _tool_result_report(answer: str, name: str, value: float,
                        split: bool = False) -> dict:
    kind = f"{TOOL_RESULT_KIND}:{name}"
    text = answer or ""
    claims = [] if split else _bare_claims(text)
    expected = float(value)
    # Already transcribed correctly: within the rounding window it is right.
    stated = any(abs(claimed - expected) <= tol + 1e-9 for claimed, tol, _ in claims)
    targets = [] if stated else _targets(expected, claims)
    values = {v for v in (_to_float(t) for t in targets) if v is not None}
    mismatches: list[dict] = []
    # Touch it only when the candidates carry exactly one value.
    if targets and len(values) == 1:
        mismatches.append({
            "kind": kind, "claimed": targets[0], "claimed_value": next(iter(values)),
            "expected": expected, "tolerance": 0.0, "hard": True,
            "detail": f"{name} 이 돌려준 값", "targets": targets})
    failed = [kind] if mismatches else []
    # A divergent tool, or an answer with no figures, was never checked and is
    # recorded as skipped. Counting it as checked would make the reported
    # coverage false.
    return {
        "ok": not mismatches, "llm_calls": 0,
        "checked": [] if split or not claims else [kind],
        "skipped": [kind] if split or not claims else [],
        "mismatches": mismatches, "failed_kinds": failed, "hard_kinds": failed,
        "failure_stage": FAILURE_STAGE if mismatches else None,
        "reason": "; ".join(_mismatch_reason(m) for m in mismatches),
        "recomputed": _recomputed(mismatches),
    }


# --- Number provenance gate. No LLM call ------------------------------------
# A different question from the calculation checks: they ask whether the
# arithmetic holds, this asks whether the figure appears in the evidence. A
# single wrong digit in a transcribed figure is not a calculation error.
#
# Kept outside calculation verification for the same reason as the internal
# consistency check: it runs once per answer rather than once per record.
#
# Deciding what to skip is half of this check. Figures the system itself
# computed are legitimately absent from the evidence, and counting those as
# violations would put this gate directly at odds with the substitution arms.
# See docs/design-rationale.md#provenance-gate

#: 게이트가 보는 자릿수 하한. **12자리 미만은 검사하지 않는다** — 연도(`2024`)·건수
#: (`3건`)·비율(`7.6%`)은 근거에 없는 것이 정상이고, 그것을 세면 오탐만 는다.
#: 우리 도메인의 계약금액은 원 단위라 11~13자리다 (`1,842,200,000,000` = 13자리).
PROVENANCE_MIN_DIGITS = 12

PROVENANCE_KIND = "provenance"

#: 🔴 출처 결함은 **생성 단계**에 귀속한다. `verify_calculation`(`TOOL`)과 다른 갈래다 —
#: 계산기가 대신했어야 할 일이 아니라 근거에 있는 수를 옮겨 적는 데 실패한 것이고,
#: `COMPLETENESS_STAGES["stale_value"]`(`GENERATION`)와 같은 종류의 실패다.
PROVENANCE_FAILURE_STAGE = "GENERATION"

#: 답변이 **그 줄에서 나눗셈을 말한** 자리. 걸리면 그 수는 우리가 나눠서 낸 값이므로
#: 근거에 없는 것이 정상이다 (`verify_calculation` 의 `per_unit` 재계산값이 정확히 이것).
#: 🔴 `_PER_UNIT_KW` 와 달리 앞의 `1` 을 요구하지 않는다 — 실측 답변은 `1척 당`(Q-097)과
#: `척당`(A20 치환 후) 둘 다 쓴다. 대신 **같은 줄의 수치 앞쪽**으로 범위를 좁힌다:
#: 답변 전체에서 찾으면 문단 하나만 나눗셈이어도 답변의 모든 수가 조용히 면제된다.
_QUOTIENT_LEAD = re.compile(rf"({_UNITS})\s*당")

#: 재계산으로 설명할 때 허용하는 항 수. 실측 답변의 합산은 최대 3항이다
#: (`v3/ORACLE` Q-034 = 해지 3건의 금액 합). 4항 이상으로 넓히면 조합이 폭증하면서
#: **우연한 설명**이 늘어 게이트가 조용히 무력해진다.
PROVENANCE_MAX_TERMS = 3

#: 재계산 일치의 허용 오차(원). 답변도 근거도 정수 원 단위라 0 이면 충분하지만 부동소수
#: 잔차만 흡수한다. 🔴 **넓히면 안 된다** — 실측 겨냥 `Q-109` 의 덧셈 오차는 998,000원
#: (상대 3.6e-7)이고, 거짓 경보로 남은 `Q-034`(6e10)·`Q-023`(1e10)보다 **더 작다.**
#: 즉 겨냥을 지키면서 그 둘을 관용으로 지울 수 있는 창은 존재하지 않는다.
PROVENANCE_TOLERANCE = 1.0

#: 재계산의 재료로 쓸 최소 금액. `_scale_exponent` 가 `SCALE_MIN_VALUE` 를 쓰는 것과
#: 같은 이유다 — `8원`·`1원` 같은 파싱 잔재가 조합에 끼면 아무 값이나 설명된다.
PROVENANCE_MIN_OPERAND = SCALE_MIN_VALUE

_ANY_NUMBER = re.compile(r"\d[\d,]*")


def _digits(text) -> str:
    return re.sub(r"\D", "", str(text or ""))


def _evidence_digits(evidence) -> set[str]:
    """근거에 실린 수의 **자릿수 문자열** 집합. 콤마를 지워 표기 차이를 흡수한다.

    수 하나를 통째로 비교한다 — 부분문자열로 찾으면 13자리 금액이 14자리 접수번호
    안에서 우연히 발견돼 게이트가 조용히 열린다.
    """
    text = evidence if isinstance(evidence, str) else str(evidence or "")
    return {_digits(m.group(0)) for m in _ANY_NUMBER.finditer(text)}


def _provenance_operands(spans, evidence_digits: set[str]) -> list[float]:
    """재계산의 재료 — 답변이 말한 금액 중 **근거에 그대로 있는** 값만.

    🔴 **근거에 없는 값을 재료로 쓰면 겨냥이 조용히 사라진다.** 실측 `Q-018` 은
    `1,566,300,000,000 + 276,900,000,000 = 1,843,200,000,000` 이 산술로는 정확히
    맞는데, 두 항 중 `276,900,000,000` 이 이미 근거 밖이다. 재료를 근거 있는 값으로
    제한하는 것이 그 자기참조를 끊는다.
    """
    out = {value for _, _, value, _, literal in spans
           if value >= PROVENANCE_MIN_OPERAND and _digits(literal) in evidence_digits}
    return sorted(out)


def _explained_by_arithmetic(value: float, operands: Sequence[float]) -> bool:
    """근거 있는 금액 2~3개의 덧셈·뺄셈으로 이 값이 나오는가. 나오면 우리가 낸 수다."""
    for size in range(2, PROVENANCE_MAX_TERMS + 1):
        if size > len(operands):
            break
        for combo in itertools.combinations(operands, size):
            for signs in itertools.product((1, -1), repeat=size):
                total = sum(sign * term for sign, term in zip(signs, combo))
                if abs(total - value) <= PROVENANCE_TOLERANCE:
                    return True
    return False


def _line_before(text: str, pos: int) -> str:
    return text[text.rfind("\n", 0, pos) + 1:pos]


def verify_number_provenance(answer: str, evidence) -> dict:
    """답변의 **긴 수치가 근거에 있는가**. 근거를 읽지만 재계산은 안 한다. LLM 0회.

    `verify_calculation`·`verify_answer_arithmetic` 과 같은 반환 규격이다.
    검사할 수치가 하나도 안 남으면(짧거나 전부 우리가 계산해서 낸 수) `skipped` 다 —
    검사 대상이 없는 것과 결함을 섞으면 Phase H 의 귀속이 오염된다.

    한 수치가 통과하는 길이 셋이다.
      ① 근거에 **완전일치**로 있다 (콤마 정규화 후 자릿수 문자열 동일)
      ② 답변이 그 줄에서 **나눗셈**을 말했다 (`1척 당 … = X`) — 우리가 낸 몫이다
      ③ 근거에 있는 금액 2~3개의 **덧셈·뺄셈**으로 정확히 나온다 — 우리가 낸 합/차다

    ②·③ 은 `skipped` 쪽이다. 🔴 **`recomputed` 는 항상 비어 있다** — 이 검사는 옳은
    수가 무엇인지 모른다(근거의 어느 수를 말하려던 것인지 정할 수 없다). 그래서
    `A20` 의 치환에 재료를 대지 않는다. 두 arm 은 판정만 OR 로 합쳐야 한다.
    """
    text = answer or ""
    evidence_digits = _evidence_digits(evidence)
    spans = _money_spans(text)
    operands = _provenance_operands(spans, evidence_digits)

    gated: list[str] = []
    mismatches: list[dict] = []
    for start, _end, value, _tol, literal in spans:
        digits = _digits(literal)
        if len(digits) < PROVENANCE_MIN_DIGITS:
            continue
        if digits in evidence_digits:
            gated.append(literal)                      # 검사했고 통과했다
            continue
        if _QUOTIENT_LEAD.search(_line_before(text, start)):
            continue                                   # ② 우리가 나눠서 낸 몫
        if _explained_by_arithmetic(value, operands):
            continue                                   # ③ 우리가 더해서 낸 합·차
        gated.append(literal)
        mismatches.append({
            "kind": PROVENANCE_KIND,
            "claimed": literal,
            "claimed_value": value,
            # 🔴 기대값이 없다. 근거의 어느 수를 말하려던 것인지 이 검사는 모른다 —
            #    그래서 `targets` 도 비운다 (`_recomputed` 가 그것으로 치환을 막는다).
            "expected": None,
            "tolerance": 0.0,
            "detail": "근거에 없는 수치",
            "hard": True,
            "targets": [],
        })

    failed = sorted({m["kind"] for m in mismatches})
    return {
        "ok": not mismatches,
        "llm_calls": 0,
        "checked": [PROVENANCE_KIND] if gated else [],
        "skipped": [] if gated else [PROVENANCE_KIND],
        "mismatches": mismatches,
        "failed_kinds": failed,
        "hard_kinds": failed,
        "failure_stage": PROVENANCE_FAILURE_STAGE if mismatches else None,
        "reason": "; ".join(f"{PROVENANCE_KIND}: 답변 {m['claimed']} 이 근거에 없다"
                            for m in mismatches),
        # 🔴 항상 비어 있다 — 위 독스트링 참조. `A20` 과 재료를 나눠 갖지 않는다.
        "recomputed": {},
    }


# --- G. 완전성 검증 (LLM 0회) ------------------------------------------------
# **지지(support)와 완전성(completeness)은 다른 축이다.** `judge` 는 "답이 근거로
# 지지되는가"만 본다. 그래서 정정 전 금액만 근거에 있으면 모델이 그 값을 답하고
# critic 은 "근거에 있으니 지지됨" 으로 통과시킨다 — 근거 자체가 불완전하다는 것을
# 못 본다. Phase H dev 42문항 실측에서 정확히 이 기전으로 **오답 5건**
# (Q-005·Q-006·Q-018·Q-035·Q-049)이 `supported=True` 로 빠져나갔다.
#
# 설계 원칙 두 가지.
#   ① **과잉 차단이 오답을 놓치는 것보다 나쁘다.** 확신할 수 없으면 통과시킨다.
#      Q-026 이 그 경계다 — 근거에 정정본이 없는 것은 Q-005 와 똑같지만, 질문이
#      '정정공시가 **있었는가**' 라서 부재 자체가 정답이다. 값을 요구하는 질문
#      (`_OUTCOME_ASK`)에만 규칙을 건다.
#   ② SWE-Lancer 부록 C — 판단(45%)이 생성(26%)보다 잘 된다. 완벽한 답을 요구하지
#      말고 **명백한 결함**만 잡는다. 규칙은 전부 결정적 문자열/수치 대조다.

#: 완전성 결함의 실패 단계 귀속. 근거가 **안 온 것**과 온 근거를 **잘못 고른 것**을
#: 가른다 — Phase I A6 은 이 두 갈래에 서로 다른 처방을 붙인다.
COMPLETENESS_STAGES = {
    "evidence_gap": "RETRIEVAL",     # 정정·해지 레코드가 근거에 아예 없다
    "missing_corp": "RETRIEVAL",     # 비교 질의인데 한쪽 기업 근거가 없다
    "stale_value": "GENERATION",     # 최신본이 왔는데 답이 대체본 값을 썼다
    "interim_source": "GENERATION",  # 연간 근거가 왔는데 답이 반기·분기 값을 썼다
}

#: 질문이 정정·해지 축을 건드리는가.
_CHAIN_ASK = re.compile(r"정정|해지|철회|변경계약")

#: 그 축의 **결과값**을 요구하는가. 존재 여부만 묻는 질문과 가르는 유일한 신호다.
_OUTCOME_ASK = re.compile(r"얼마로\s*바뀌|반영\s*후|얼마나\s*(증가|감소|줄|늘|커|작아)|"
                          r"어떻게\s*(바뀌|달라|변했|처리)|이력|무엇이\s*왜|"
                          r"몇\s*차례|여러\s*차례|과정에서")

#: '정정공시가 있었는가' 류. 정정본의 **부재가 곧 정답**이라 절대 걸면 안 된다.
_EXISTENCE_ASK = re.compile(r"(정정공시|정정|해지|철회)\s*(가|이|는|은|를|을)?\s*"
                            r"(있었|없었|되었|됐|하였|했)(는가|나|는지|습니까)")

#: 답변이 스스로 '근거가 이걸 안 담고 있다' 고 말한 자리. 모델의 자백은 공짜 신호다.
_ADMIT = re.compile(r"근거에\s*(는\s*)?없|포함되어\s*있지\s*않|언급이\s*없|"
                    r"확인되지\s*않|확인할\s*수\s*없|답변(을)?\s*(드릴|할)\s*수\s*없|"
                    r"별도의?\s*(검색|조회|확인)이?\s*필요")

_BLOCK_SPLIT = re.compile(r"^##\s+", re.M)
#: `## [기재정정] [공급계약 체결] 삼성전자 – …` 에서 기업명만. 구분자는 en/em dash 다.
_HEAD_CORP = re.compile(r"\]\s*([^\[\]–—]+?)\s*[–—]")
#: 블록이 어느 **문서**에서 왔는가. 정기공시는 한 문서를 여러 절로 잘라 싣는다.
_BLOCK_RCEPT = re.compile(r"접수번호\s*(\d{14})")
_CORRECTION_HEAD = re.compile(r"\[기재정정\]|정\s*정\s*신\s*고")
_STALE_NOTE = re.compile(r"정정체인:.*대체됨")
_LATEST_NOTE = re.compile(r"정정체인:.*최신본")
_INTERIM_HEAD = re.compile(r"반기보고서|분기보고서")
_ANNUAL_HEAD = re.compile(r"사업보고서")
_INTERIM_ASK = re.compile(r"반기|분기")

#: 5자리 이상만 본다. 연도(2025)·건수(3건)를 값으로 세면 오탐만 는다.
_FIGURE = re.compile(r"\d[\d,]{4,}")
#: 14자리는 접수번호다. 값이 아니라 주소이므로 대조 대상에서 뺀다.
_RCEPT = re.compile(r"^\d{14}$")
#: 8자리 `YYYYMMDD` 는 공시일이다. 접수번호와 **같은 이유로** 값이 아니라 주소다.
#: A6 실측 거짓 경보(Q-048): 답변이 인용에 적은 공시일 `20230515` 가 '대체본 값
#: 20,230,515' 로 보고됐다. 근거 머리글의 `(공시일 20230515)` 와 짝이 맞아 버린다.
_DATE8 = re.compile(r"^(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])$")


def _figures(text: str) -> set[str]:
    out = set()
    for match in _FIGURE.finditer(text or ""):
        raw = match.group(0).replace(",", "")
        if not (_RCEPT.match(raw) or _DATE8.match(raw)):
            out.add(raw)
    return out


def _record_text(record: dict) -> str:
    if record.get("markdown"):
        return str(record["markdown"])
    return "\n".join(f"{k}: {v}" for k, v in record.items()
                     if not isinstance(v, (dict, list)))


def _blocks(evidence) -> list[dict]:
    """근거를 블록 목록으로. 마크다운 문자열·레코드 리스트·도구 반환형을 모두 받는다."""
    if isinstance(evidence, str):
        rows = [{"markdown": "## " + part} for part in _BLOCK_SPLIT.split(evidence)[1:]]
    elif isinstance(evidence, dict):
        for key in ("events", "records", "chunks", "filings", "picked"):
            inner = evidence.get(key)
            if isinstance(inner, list):
                return _blocks(inner)
        rows = [evidence]
    elif isinstance(evidence, Sequence):
        rows = [r if isinstance(r, dict) else {"markdown": str(r)} for r in evidence]
    else:
        return []

    blocks = []
    for row in rows:
        text = _record_text(row)
        head = text.split("\n", 1)[0]
        corp = row.get("corp") or row.get("corp_name") or ""
        if not corp:
            found = _HEAD_CORP.search(head)
            corp = found.group(1).strip() if found else ""
        is_latest = row.get("is_latest")
        if is_latest is None:
            if _STALE_NOTE.search(text):
                is_latest = False
            elif _LATEST_NOTE.search(text):
                is_latest = True
        rcept = str(row.get("rcept_no") or "")
        if not rcept:
            found = _BLOCK_RCEPT.search(text)
            rcept = found.group(1) if found else ""
        blocks.append({"head": head, "text": text, "corp": corp, "is_latest": is_latest,
                       "rcept": rcept,
                       "correction": bool(_CORRECTION_HEAD.search(head))})
    return blocks


def _split_chain(blocks: list[dict]) -> tuple[list[dict], list[dict]]:
    """(대체본, 최신본). `is_latest` 가 없으면 `[기재정정]` 머리글로 대신 가른다.

    머리글 대체는 **기업 단위 추정**이라 뒤로 한 발 물러서야 하는 자리다.
    같은 접수번호를 단 블록은 **같은 문서**이므로 서로 대체본·최신본일 수 없다.
    A6 실측 거짓 경보(Q-048): 정기공시는 원공시와 정정본이 같은 `path` 를 갖고
    값만 다른데, 정정본의 본문 블록과 `정 정 신 고` 블록이 접수번호를 공유한다.
    기업 단위 추정은 이 둘을 갈라 **정정본 본문을 대체본으로** 몰았다.
    """
    corrected_corps = {b["corp"] for b in blocks if b["correction"] and b["corp"]}
    corrected_rcepts = {b["rcept"] for b in blocks if b["correction"] and b["rcept"]}
    stale = [b for b in blocks
             if b["is_latest"] is False
             or (b["is_latest"] is None and not b["correction"]
                 and b["corp"] in corrected_corps
                 and b["rcept"] not in corrected_rcepts)]
    # 같은 블록이 두 번 실려도 값 비교로 뭉개지지 않게 **동일성**으로 뺀다.
    stale_ids = {id(b) for b in stale}
    latest = [b for b in blocks
              if id(b) not in stale_ids
              and (b["is_latest"] is True or b["correction"]
                   or (b["rcept"] and b["rcept"] in corrected_rcepts))]
    return stale, latest


def _union(blocks: list[dict]) -> set[str]:
    out: set[str] = set()
    for block in blocks:
        out |= _figures(block["text"])
    return out


def _fmt_figures(values) -> str:
    return ", ".join(f"{int(v):,}" for v in sorted(values, key=int)[:3])


def verify_completeness(question: str, answer: str, evidence,
                        *, companies: Sequence[str] | None = None) -> dict:
    """근거가 **충분한가**를 본다. LLM 0회.

    🔴 **`verify_calculation` 과 같은 반환 규격이 아니다** (종전 주석이 그렇게 적혀
    있었고 틀렸다 — v4 조사 §2-b ③이 키를 세어 잡았다). 실제로 같은 것은 셋이다:
    `verify_answer_arithmetic` · `verify_number_provenance` · `_tool_result_report`
    (10키 동일). `verify_calculation` 은 거기에 `gated` 가 붙은 11키다.

        이 함수(8키)  ok llm_calls checked skipped **gaps** failed_kinds
                      failure_stage reason
        나머지        ok llm_calls checked skipped **mismatches** failed_kinds
                      failure_stage reason **recomputed hard_kinds**

    `mismatches` 대신 `gaps` 이고 `recomputed`·`hard_kinds` 가 없다. 🔴 **합치지 마라** —
    `gaps` 는 재계산 결과가 아니라 '근거에 없는 것' 의 목록이라 값의 성질이 다르다.
    `pipeline._verdict` 가 이 둘을 각각 다르게 읽는다.

    `judge` 가 보는 축(지지)과 겹치지 않는다. 여기서 잡는 것은 넷이다.

    | kind | 전제 | 결함 | 귀속 |
    |---|---|---|---|
    | `evidence_gap` | 질문이 정정·해지 **결과값**을 요구 | 답이 근거 부족을 자백 | RETRIEVAL |
    | `stale_value` | 대체본과 최신본이 **서로 다른 문서로** 함께 있음 | 답이 대체본 값만 씀 | GENERATION |
    | `interim_source` | 연간 질의 + 반기·분기와 사업보고서가 함께 옴 | 답이 반기 값만 씀 | GENERATION |
    | `missing_corp` | `companies` 를 넘겼고 2곳 이상 | 근거에 없는 기업이 있음 | RETRIEVAL |

    전제가 안 서면 `skipped` 다 — 검증 대상이 없는 것과 결함을 섞으면 Phase H·I 의
    귀속이 오염된다 (`verify_calculation` 의 `skipped` 설계와 같은 이유).

    **확신할 수 없으면 통과시킨다.** 저장 응답 두 스냅샷 전수에서 정답 문항 거짓
    경보 0건이다 (`COMPLETENESS_SNAPSHOTS`). 수치는 스냅샷과 함께 읽어야 한다 —
    같은 규칙이 Phase H(골든 근거 38/60)에서는 7건, A6(59/60)에서는 1건 발화한다.
    """
    text = answer or ""
    blocks = _blocks(evidence)
    said = _figures(text)
    checked: list[str] = []
    skipped: list[str] = []
    gaps: list[dict] = []

    def _record(kind: str, ran: bool, detail: str = "", found=()) -> None:
        (checked if ran else skipped).append(kind)
        if detail:
            gaps.append({"kind": kind, "detail": detail,
                         "failure_stage": COMPLETENESS_STAGES[kind],
                         "figures": list(found)})

    # ① 근거 공백 — 정정·해지 결과값을 요구했는데 답이 근거 부족을 자백했다.
    #    존재 질의(Q-026)는 부재가 정답이라 전제에서 뺀다.
    wants_outcome = bool(_CHAIN_ASK.search(question or "")
                         and _OUTCOME_ASK.search(question or "")
                         and not _EXISTENCE_ASK.search(question or ""))
    if not wants_outcome:
        _record("evidence_gap", False)
    else:
        hit = _ADMIT.search(text)
        _record("evidence_gap", True,
                f"정정·해지 결과값을 물었으나 답이 근거 부족을 자백했다 ('{hit.group(0)}')"
                if hit else "")

    # ② 대체본 사용 — 체인이 통째로 왔을 때만 성립한다. 다른 트랙이 골든 근거 적중을
    #    38/60 → 59/60 으로 올리면서 정정 전·후 금액이 둘 다 보이게 됐고, 그 바람에
    #    이 전제가 1회 → 7회로 자주 서게 됐다 (`COMPLETENESS_SNAPSHOTS`).
    #    **전제가 자주 서는 것 자체는 정상이다.** 그때 발화가 정확한지를 다시 재야 한다.
    stale, latest = _split_chain(blocks)
    if not (wants_outcome and stale and latest and said):
        _record("stale_value", False)
    else:
        stale_only = _union(stale) - _union(latest)
        latest_only = _union(latest) - _union(stale)
        used_stale = said & stale_only
        _record("stale_value", True,
                f"근거에 최신본이 있는데 답이 대체본 값 {_fmt_figures(used_stale)} 을 썼다"
                if used_stale and not (said & latest_only) else "", used_stale)

    # ③ 반기·분기 출처 — 연간 값을 물었는데 같은 기업의 사업보고서가 함께 왔는데도
    #    답이 반기·분기 블록에만 있는 수치를 골랐다 (Q-049 실측: 11,830,403).
    interim = [b for b in blocks if _INTERIM_HEAD.search(b["head"])]
    annual = [b for b in blocks if _ANNUAL_HEAD.search(b["head"])]
    shared = {b["corp"] for b in interim} & {b["corp"] for b in annual} - {""}
    if _INTERIM_ASK.search(question or "") or not (shared and said):
        _record("interim_source", False)
    else:
        interim_ids = {id(b) for b in interim}
        interim_only = (_union([b for b in interim if b["corp"] in shared])
                        - _union([b for b in blocks if id(b) not in interim_ids]))
        used_interim = said & interim_only
        _record("interim_source", True,
                f"연간 값을 물었으나 반기·분기 보고서에만 있는 수치 "
                f"{_fmt_figures(used_interim)} 을 썼다" if used_interim else "", used_interim)

    # ④ 비교 축 — 기업 목록을 **넘겼을 때만** 돈다. 근거 없는 차단은 하지 않는다.
    names = [c for c in (companies or []) if c]
    if len(names) < 2 or not blocks:
        _record("missing_corp", False)
    else:
        covered = {b["corp"] for b in blocks if b["corp"]}
        missing = [c for c in names
                   if not any(c in cov or cov in c for cov in covered)]
        _record("missing_corp", True,
                f"비교 질의인데 근거에 {', '.join(missing)} 이(가) 없다" if missing else "")

    failed = sorted({g["kind"] for g in gaps})
    return {
        "ok": not gaps,
        "llm_calls": 0,
        "checked": checked,
        "skipped": skipped,
        "gaps": gaps,
        "failed_kinds": failed,
        "failure_stage": gaps[0]["failure_stage"] if gaps else None,
        "reason": "; ".join(f"{g['kind']}: {g['detail']}" for g in gaps),
    }


# --- G. 완전성 규칙이 낡는 것을 드러내는 계기 ----------------------------------
# 이 트랙에서 배운 것 하나. **규칙이 틀린 게 아니라 규칙이 가정한 세계가 바뀐다.**
# `verify_completeness` 는 만들 때 정밀도 7/7 이었는데, 다른 트랙이 골든 근거 적중을
# 38/60 → 59/60 으로 올리자 `stale_value` 의 전제(대체본+최신본 공존)가 **1회 → 7회**
# 로 늘면서 거짓 경보가 생겼다. 발화 수만 보면 이 변화가 안 보인다.
#
# 그래서 **전제가 몇 번 섰는지(`checked`)와 몇 번 발화했는지를 분리해** 센다.
# 발화는 그대로인데 `checked` 만 뛰면 규칙이 낡고 있다는 뜻이다. 다음 트랙이 Recall 을
# 더 올리면 같은 일이 또 생긴다 — 그때 이 표가 먼저 어긋난다.

#: `checked` 가 기준선의 몇 배가 되면 규칙을 다시 보라고 표시할 것인가.
PREMISE_DRIFT_FACTOR = 2.0

#: **수치는 스냅샷과 함께만 의미가 있다.** 07-verifier 의 7/7 이 Phase H 응답 기준인
#: 줄 모르고 A6 에 그대로 적용된 것이 이번 혼선의 직접적 원인이었다. 어느 응답
#: 묶음에서 잰 값인지 여기에 못 박고, `tests/test_critic.py` 가 전수로 재확인한다.
COMPLETENESS_SNAPSHOTS = {
    "phase_h": {
        "responses": "artifacts/eval/responses",
        "labels": "artifacts/eval/error_analysis.json",
        "golden_evidence": "38/60",
        "n": 42, "correct": 25,
        "checked": {"evidence_gap": 7, "stale_value": 1,
                    "interim_source": 4, "missing_corp": 3},
        "fired": {"evidence_gap": 5, "stale_value": 1,
                  "interim_source": 1, "missing_corp": 0},
    },
    "ablation_a6": {
        "responses": "artifacts/ablation/A6/responses",
        "labels": "artifacts/ablation/A6/report.json",
        "golden_evidence": "59/60",
        "n": 42, "correct": 36,
        "checked": {"evidence_gap": 7, "stale_value": 7,
                    "interim_source": 3, "missing_corp": 3},
        "fired": {"evidence_gap": 0, "stale_value": 1,
                  "interim_source": 0, "missing_corp": 0},
    },
    # A7 — 구성은 A6 그대로다 (플래그가 하나도 다르지 않다). 바뀐 것은 **답변**이다.
    # 거절 강제(라우터 게이트)와 시점 규칙이 답변 문장을 바꾸자 전제 성립 수가 움직였다.
    #
    #   stale_value    7 -> 6   Q-040 이 근거 없이 거절로 빠져 체인 블록이 사라졌다
    #   interim_source 3 -> 2   같은 이유
    #
    # **규칙은 한 줄도 고치지 않았다.** 배율이 0.86·0.67 로 `PREMISE_DRIFT_FACTOR`(2.0)
    # 밖이라 드리프트 경보는 안 뜨지만, 수치는 스냅샷과 함께만 의미가 있으므로 새로 박는다.
    # 정밀도는 그대로 1.00 이다 — 발화 1건(Q-023)이 오답이고 정답 문항 거짓 경보는 0건.
    "ablation_a7": {
        "responses": "artifacts/ablation/A7/responses",
        "labels": "artifacts/ablation/A7/report.json",
        "golden_evidence": "59/60",
        "n": 42, "correct": 38,
        "checked": {"evidence_gap": 7, "stale_value": 6,
                    "interim_source": 2, "missing_corp": 3},
        "fired": {"evidence_gap": 0, "stale_value": 1,
                  "interim_source": 0, "missing_corp": 0},
    },
}


def completeness_audit(reports: Sequence[dict], *, baseline=None) -> dict:
    """`verify_completeness` 결과 묶음에서 **전제 성립과 발화를 따로** 센다. LLM 0회.

    `fire_rate` 는 발화 / 전제 성립이다. 전체 문항 수로 나누지 않는다 — 전제가
    안 선 문항은 검사 대상이 아니었으므로 분모에 넣으면 규칙이 좋아진 것처럼 보인다.

    `baseline` 에 `COMPLETENESS_SNAPSHOTS` 의 키(또는 같은 모양의 dict)를 주면
    기준선 대비 `checked` 배율을 함께 낸다. `PREMISE_DRIFT_FACTOR` 이상 벌어진
    kind 는 `drifted` 에 실린다 — **그 규칙의 전제가 다른 세계에서 서고 있다는 뜻**이라
    거짓 경보를 다시 재야 한다.
    """
    checked = {kind: 0 for kind in COMPLETENESS_STAGES}
    fired = {kind: 0 for kind in COMPLETENESS_STAGES}
    for report in reports or ():
        for kind in report.get("checked") or ():
            if kind in checked:
                checked[kind] += 1
        for kind in report.get("failed_kinds") or ():
            if kind in fired:
                fired[kind] += 1

    out = {
        "n": len(reports or ()),
        "checked": checked,
        "fired": fired,
        "fire_rate": {kind: (fired[kind] / checked[kind] if checked[kind] else None)
                      for kind in checked},
        "drift": {},
        "drifted": [],
    }
    if baseline is None:
        return out

    ref = COMPLETENESS_SNAPSHOTS[baseline] if isinstance(baseline, str) else baseline
    for kind, now in checked.items():
        then = (ref.get("checked") or {}).get(kind, 0)
        factor = (now / then) if then else (float("inf") if now else 1.0)
        out["drift"][kind] = {"checked_then": then, "checked_now": now,
                              "fired_then": (ref.get("fired") or {}).get(kind, 0),
                              "fired_now": fired[kind], "factor": factor}
        if factor >= PREMISE_DRIFT_FACTOR:
            out["drifted"].append(kind)
    out["drifted"].sort()
    return out


def judge(evidence: str, answer: str, budget: Budget | None = None) -> Verdict:
    result = chat(build_critic_messages(evidence, answer),
                  max_tokens=200, temperature=0.0, tag="critic")
    if budget:
        budget.spend_llm(result.prompt_tokens, result.completion_tokens)

    match = _JSON.search(result.text or "")
    if not match:
        return Verdict(False, False, True, "critic 응답 파싱 실패")
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return Verdict(False, False, True, "critic 응답 파싱 실패")

    return Verdict(
        supported=bool(data.get("supported")),
        has_citation=bool(data.get("has_citation")),
        hallucination=bool(data.get("hallucination")),
        reason=str(data.get("reason", "")),
    )


def judge_open(question: str, evidence: str, key_points: list[str] | None,
               answer: str, budget: Budget | None = None) -> OpenVerdict:
    """서술형 채점. LLM 1회 — closed 는 정규화 EM 이라 여기 오지 않는다.

    파싱 실패는 **오답이 아니라 판정 실패**다. 3항목을 모두 False 로 두고 이유를 남긴다.
    """
    result = chat(build_open_judge_messages(question, evidence, key_points, answer),
                  max_tokens=200, temperature=0.0, tag="judge_open")
    if budget:
        budget.spend_llm(result.prompt_tokens, result.completion_tokens)

    match = _JSON.search(result.text or "")
    if not match:
        return OpenVerdict(False, False, False, "judge 응답 파싱 실패")
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return OpenVerdict(False, False, False, "judge 응답 파싱 실패")

    return OpenVerdict(
        covers_key_points=bool(data.get("covers_key_points")),
        grounded=bool(data.get("grounded")),
        no_fabrication=bool(data.get("no_fabrication")),
        reason=str(data.get("reason", "")),
    )
