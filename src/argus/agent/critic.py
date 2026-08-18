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
    """Open-question scoring result. The three criteria are not summed.

    `covered` holds one verdict per golden key point. `covers_key_points` is a
    strict majority over that array rather than one judgement over all points
    together: the single boolean let an answer covering three of six points
    score as correct. See docs/open-issues.md item 8.
    """
    covered: tuple[bool, ...]
    grounded: bool
    no_fabrication: bool
    reason: str

    @property
    def covers_key_points(self) -> bool:
        """Strict majority. Five points need three, six need four.

        A tie fails. An empty array fails: an open question with no golden
        points cannot be scored, and reporting it as covered would score the
        absence of a standard as a pass.
        """
        if not self.covered:
            return False
        return sum(self.covered) * 2 > len(self.covered)

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
#:
#: The third place is optional and carries the same reason. A figure written to
#: the ten-thousand place left its last group outside the compound match, so the
#: pair of notations the restatement check compares was one part short and the
#: check never ran on the notation the prompt asks for.
_COMPOUND_MONEY_RE = re.compile(
    r"(-?\d[\d,]*)\s*조\s*(\d[\d,]*)\s*억(?:\s*(\d[\d,]*)\s*만)?\s*원?")


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
        tolerance = _half_ulp(m.group(2), 1e8)
        # The tolerance follows the smallest place the figure states, so a
        # figure written to a finer place is held to a tighter tolerance.
        if m.group(3) is not None:
            man = _to_float(m.group(3))
            if man is None:
                continue
            value += man * 1e4
            tolerance = _half_ulp(m.group(3), 1e4)
        out.append((m.start(), m.end(), value, tolerance, m.group(0)))
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
            continue                          # a matching candidate, left alone
        if not value or not expected or (value > 0) != (expected > 0):
            continue
        if abs(math.log10(abs(value) / abs(expected))) > SUBSTITUTE_LOG_TOLERANCE:
            continue                          # a different figure, such as a total
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
            return                       # already reported as a magnitude error
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
#: notations, and so can a connective that announces the bracket as a
#: restatement. Requiring the figure to sit immediately after the bracket
#: missed every pair written the way the answers actually write them, which is
#: with a connective. The list is closed: a bracket opening with anything else
#: may hold a different quantity, such as a part of the figure or a comparison,
#: and reading those as restatements would report mismatches on correct
#: answers.
#: See docs/design-rationale.md#answer-internal-consistency
_RESTATE_OPEN = re.compile(r"^[\s*_]*\(\s*(?:즉[,]?|약|=|바꿔\s*말하면|환산\s*시)?\s*$")
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

#: Digit-count floor the gate looks at. Shorter figures are years, counts and
#: percentages, which are legitimately absent from the evidence and would only
#: add false positives. Contract amounts in this domain run to eleven to
#: thirteen digits.
PROVENANCE_MIN_DIGITS = 12

PROVENANCE_KIND = "provenance"

#: A provenance defect is attributed to the generation stage rather than the
#: tool stage: it is a failure to transcribe a figure that is present, not work
#: a calculator should have done.
PROVENANCE_FAILURE_STAGE = "GENERATION"

#: Where the answer said it divided on that line. A figure so marked is a
#: quotient the system produced and is legitimately absent from the evidence.
#: Unlike the per-unit pattern this does not require the leading numeral, since
#: answers write both forms; instead the scope is narrowed to the same line
#: ahead of the figure, because searching the whole answer would exempt every
#: figure in it from one dividing paragraph.
#: See docs/design-rationale.md#provenance-gate
_QUOTIENT_LEAD = re.compile(rf"({_UNITS})\s*당")

#: Term count allowed when explaining a figure by recomputation. Recorded
#: answers sum at most three terms; allowing more makes the combinations
#: explode, coincidental explanations rise, and the gate quietly become
#: toothless.
PROVENANCE_MAX_TERMS = 3

#: Tolerance for a recomputation match. Both sides are integral, so this only
#: absorbs floating-point residue. It must not be widened: the addition error
#: of the question this gate targets is smaller than the errors of the false
#: alarms it leaves behind, so no window clears those while keeping the
#: target.
#: See docs/design-rationale.md#provenance-gate
PROVENANCE_TOLERANCE = 1.0

#: Minimum amount usable as an operand, for the same reason the magnitude test
#: has one: parsing residue of a few won lets any value be explained.
PROVENANCE_MIN_OPERAND = SCALE_MIN_VALUE

_ANY_NUMBER = re.compile(r"\d[\d,]*")


def _digits(text) -> str:
    return re.sub(r"\D", "", str(text or ""))


def _evidence_text(evidence) -> str:
    """Evidence flattened to one string, whatever shape it arrived in.

    Shared by callers that read the evidence as a whole rather than by block,
    such as `_evidence_digits` and the amount count in `verify_completeness`.
    See docs/design-rationale.md#amount-coverage
    """
    return evidence if isinstance(evidence, str) else str(evidence or "")


def _evidence_digits(evidence) -> set[str]:
    """Digit strings of the numbers present in the evidence, with separators
    removed to absorb notation differences.

    Numbers are compared whole. Matching as a substring would find a
    thirteen-digit amount inside a fourteen-digit receipt number by coincidence
    and open the gate silently.
    """
    return {_digits(m.group(0)) for m in _ANY_NUMBER.finditer(_evidence_text(evidence))}


def _provenance_operands(spans, evidence_digits: set[str]) -> list[float]:
    """Operands for the recomputation: only amounts the answer states that are
    present in the evidence verbatim.

    Admitting an operand that is itself absent from the evidence makes the
    target disappear silently, because an answer can then explain one
    out-of-evidence figure with another. Restricting the operands breaks that
    self-reference.
    """
    out = {value for _, _, value, _, literal in spans
           if value >= PROVENANCE_MIN_OPERAND and _digits(literal) in evidence_digits}
    return sorted(out)


def _explained_by_arithmetic(value: float, operands: Sequence[float]) -> bool:
    """Whether adding and subtracting two or three grounded amounts produces
    this value. If so, the system produced it."""
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
    """Whether the long figures in an answer appear in the evidence. Reads the
    evidence but does not recompute. No LLM call.

    Same return shape as the other deterministic checks. With no figure left to
    check, the result is skipped rather than passed.

    A figure passes one of three ways: it matches the evidence exactly, the
    answer said it divided on that line, or it is exactly the sum or difference
    of two or three grounded amounts. The last two are recorded as skipped.

    The recomputed field is always empty. This check does not know what the
    correct figure would be, since it cannot tell which evidence figure the
    answer meant, so it supplies nothing to substitution and the two verdicts
    are combined by disjunction only.
    See docs/design-rationale.md#provenance-gate
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
            gated.append(literal)                      # checked and passed
            continue
        if _QUOTIENT_LEAD.search(_line_before(text, start)):
            continue                                   # a quotient we produced
        if _explained_by_arithmetic(value, operands):
            continue                                   # a sum we produced
        gated.append(literal)
        mismatches.append({
            "kind": PROVENANCE_KIND,
            "claimed": literal,
            "claimed_value": value,
            # No expected value: this check cannot tell which evidence
            # figure was meant, so the target list stays empty and the
            # collector blocks substitution on that basis.
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
        # Always empty, see the docstring above. This check supplies no
        # material to substitution.
        "recomputed": {},
    }


# --- Completeness verification. No LLM call ---------------------------------
# Support and completeness are different axes. The judge asks only whether the
# answer is supported by the evidence, so an answer taken from a pre-revision
# figure passes while the evidence itself is incomplete.
#
# Two principles: over-blocking is worse than missing a wrong answer, so an
# uncertain case passes; and only obvious defects are caught, with every rule a
# deterministic string or numeric comparison.
# See docs/design-rationale.md#completeness-checks

#: Failure stage per completeness defect, separating evidence that never
#: arrived from evidence that arrived and was chosen wrongly. The two get
#: different remedies.
COMPLETENESS_STAGES = {
    "evidence_gap": "RETRIEVAL",     # the revision record is absent entirely
    "missing_corp": "RETRIEVAL",     # a comparison is missing one company
    "stale_value": "GENERATION",     # the latest record arrived, the answer used the superseded one
    "interim_source": "GENERATION",  # the annual record arrived, the answer used the interim one
    "amount_gap": "GENERATION",      # the evidence carried amounts, the answer stated none
}

#: Whether the question touches the revision or termination axis.
_CHAIN_ASK = re.compile(r"정정|해지|철회|변경계약")

#: Whether it asks for the resulting value on that axis. The only signal
#: separating it from a question that asks only whether something happened.
_OUTCOME_ASK = re.compile(r"얼마로\s*바뀌|반영\s*후|얼마나\s*(증가|감소|줄|늘|커|작아)|"
                          r"어떻게\s*(바뀌|달라|변했|처리)|이력|무엇이\s*왜|"
                          r"몇\s*차례|여러\s*차례|과정에서")

#: Existence questions, where the absence of a revision is itself the answer.
#: The rule must never fire on these.
_EXISTENCE_ASK = re.compile(r"(정정공시|정정|해지|철회)\s*(가|이|는|은|를|을)?\s*"
                            r"(있었|없었|되었|됐|하였|했)(는가|나|는지|습니까)")

#: Where the answer itself states that the evidence does not contain
#: something. The model's own admission is a free signal.
_ADMIT = re.compile(r"근거에\s*(는\s*)?없|포함되어\s*있지\s*않|언급이\s*없|"
                    r"확인되지\s*않|확인할\s*수\s*없|답변(을)?\s*(드릴|할)\s*수\s*없|"
                    r"별도의?\s*(검색|조회|확인)이?\s*필요")

_BLOCK_SPLIT = re.compile(r"^##\s+", re.M)
#: The company name out of an evidence header. The separator is an en or em
#: dash.
_HEAD_CORP = re.compile(r"\]\s*([^\[\]–—]+?)\s*[–—]")
#: Which document a block came from. A periodic filing is carried as several
#: sections of one document.
_BLOCK_RCEPT = re.compile(r"접수번호\s*(\d{14})")
_CORRECTION_HEAD = re.compile(r"\[기재정정\]|정\s*정\s*신\s*고")
_STALE_NOTE = re.compile(r"정정체인:.*대체됨")
_LATEST_NOTE = re.compile(r"정정체인:.*최신본")
_INTERIM_HEAD = re.compile(r"반기보고서|분기보고서")
_ANNUAL_HEAD = re.compile(r"사업보고서")
_INTERIM_ASK = re.compile(r"반기|분기")

#: Five digits or more only. Counting years and item counts as values would
#: add false positives alone.
_FIGURE = re.compile(r"\d[\d,]{4,}")
#: Fourteen digits is a receipt number: an address rather than a value, so it
#: is excluded from comparison.
_RCEPT = re.compile(r"^\d{14}$")
#: An eight-digit `YYYYMMDD` is a filing date, an address for the same reason.
#: Without excluding it, a filing date written inside a citation was reported
#: as a superseded value, because it matched the date in an evidence header.
#: See docs/design-rationale.md#completeness-checks
_DATE8 = re.compile(r"^(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])$")

#: Minimum amount count the evidence has to carry for the coverage premise to
#: hold. Below this, an answer with no amount is not distinguishable from
#: evidence that simply carries too few figures to expect one.
#: See docs/design-rationale.md#amount-coverage
AMOUNT_GAP_MIN_EVIDENCE = 2

#: Substrings marking a question that asks for several figures to be reported
#: together, rather than a single value. Restricting the check to these cuts
#: the false-alarm rate the bare premise carries on its own.
#: See docs/design-rationale.md#amount-coverage
AMOUNT_GAP_SET_ASK = (
    "각각", "어디이며", "정리해", "비교", "관계", "달라진",
    "어떻게 변", "나뉜다", "갈래", "무엇이 달라",
)


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
    """Evidence as a list of blocks. Accepts a markdown string, a list of
    records, or the tool return shape."""
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
    """The (superseded, latest) split. Without the record flag, the revision
    marker in the header is used instead.

    That fallback is a per-company inference and needs a step back: blocks
    sharing a receipt number are the same document and cannot be superseded and
    latest relative to each other. Without that guard, the body block and the
    revision-notice block of one periodic filing were split apart and the body
    was labelled superseded.
    See docs/design-rationale.md#completeness-checks
    """
    corrected_corps = {b["corp"] for b in blocks if b["correction"] and b["corp"]}
    corrected_rcepts = {b["rcept"] for b in blocks if b["correction"] and b["rcept"]}
    stale = [b for b in blocks
             if b["is_latest"] is False
             or (b["is_latest"] is None and not b["correction"]
                 and b["corp"] in corrected_corps
                 and b["rcept"] not in corrected_rcepts)]
    # Removed by identity rather than by value, so a block carried twice is
    # not collapsed by the comparison.
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
                        *, companies: Sequence[str] | None = None,
                        amount_coverage: bool = False) -> dict:
    """Whether the evidence is sufficient. No LLM call.

    The return shape is not the same as the other deterministic checks, despite
    an earlier comment saying so. This one carries `gaps` where they carry
    `mismatches`, and has no recomputed values and no hard kinds. Do not merge
    them: a gap is a list of what is absent from the evidence rather than a
    recomputation, and the pipeline reads the two differently.

    This does not overlap the support axis the judge covers. Five defects are
    caught here, each with its own premise and failure-stage attribution. The
    fifth, amount coverage, is opt-in through the `amount_coverage` keyword
    rather than a module-level flag, the same way `companies` is opt-in: this
    module reads no ablation flags, so the pipeline decides the value and
    passes it in.

    A premise that does not hold is recorded as skipped rather than passed.

    An uncertain case passes. Across both recorded snapshots there are no false
    alarms on correct answers, but those counts have to be read together with
    the snapshot they came from.
    See docs/design-rationale.md#completeness-checks
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

    # Evidence gap: the question asked for a revision or termination result
    # and the answer admits the evidence is short. Existence questions are
    # excluded from the premise, since there absence is the answer.
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

    # Superseded value: the premise holds only when the whole chain arrived.
    # Better retrieval made both the pre- and post-revision amounts visible, so
    # this premise now holds far more often. That is normal in itself; what has
    # to be re-measured is whether the firings are still accurate.
    # See docs/design-rationale.md#premise-drift
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

    # Interim source: the question asked for an annual figure, the annual
    # report for the same company was present, and the answer still took a
    # figure that appears only in an interim block.
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

    # Comparison axis: runs only when a company list was supplied. No blocking
    # without that basis.
    names = [c for c in (companies or []) if c]
    if len(names) < 2 or not blocks:
        _record("missing_corp", False)
    else:
        covered = {b["corp"] for b in blocks if b["corp"]}
        missing = [c for c in names
                   if not any(c in cov or cov in c for cov in covered)]
        _record("missing_corp", True,
                f"비교 질의인데 근거에 {', '.join(missing)} 이(가) 없다" if missing else "")

    # Amount coverage: the evidence carries several monetary figures and the
    # answer states none at all. The bare premise alone fires on far more
    # correct answers than wrong ones, so it is narrowed by two more premises:
    # the question asks for a set of figures to be reported, and the answer
    # does not read as a refusal. A refusal legitimately carries no figures,
    # and `_ADMIT` is the same admission signal `evidence_gap` reads, applied
    # in the opposite direction: there an admission is the defect, here its
    # absence is a condition for one.
    #
    # The count reads the evidence as a whole rather than `blocks`, because
    # `blocks` is the reassembly the other checks in this function need and it
    # drops whatever precedes its first block heading. That is exactly where a
    # deterministic aggregate line, such as a unit legend, is written, so
    # counting through `blocks` undercounts and can miss the premise entirely
    # on evidence shaped that way. The premise asks what the model was shown,
    # which is the whole evidence text, not the reassembly.
    # See docs/design-rationale.md#amount-coverage
    asks_for_set = any(term in (question or "") for term in AMOUNT_GAP_SET_ASK)
    evidence_amounts = money_values(_evidence_text(evidence))
    if not (amount_coverage and asks_for_set
            and len(evidence_amounts) >= AMOUNT_GAP_MIN_EVIDENCE):
        _record("amount_gap", False)
    else:
        fires = not _ADMIT.search(text) and not money_values(text)
        _record("amount_gap", True,
                "근거에 금액이 여럿 있는데 답이 금액을 하나도 쓰지 않았다" if fires else "")

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


# --- Detecting when a completeness rule goes stale --------------------------
# A rule does not become wrong; the world it assumed changes. The completeness
# checks were precise when written, and improving retrieval elsewhere made one
# premise hold far more often, which produced false alarms. Counting firings
# alone does not show that.
#
# So how often a premise held and how often the rule fired are counted
# separately. Firings flat while the premise count jumps means the rule is
# going stale.
# See docs/design-rationale.md#premise-drift

#: Multiple of the baseline premise count at which a rule is flagged for
#: review.
PREMISE_DRIFT_FACTOR = 2.0

#: These numbers mean nothing apart from the snapshot they were measured on.
#: Applying a precision figure from one response set to another was the direct
#: cause of an earlier misreading, so each snapshot names its response set and
#: the tests re-verify it exhaustively.
#:
#: `amount_gap` did not exist when these three snapshots were measured, and the
#: check stays off unless `amount_coverage=True` is passed in, so it is
#: recorded here as never checked and never fired rather than left out. Leaving
#: it out would break the invariant that a snapshot names every kind in
#: `COMPLETENESS_STAGES`.
COMPLETENESS_SNAPSHOTS = {
    "phase_h": {
        "responses": "artifacts/eval/responses",
        "labels": "artifacts/eval/error_analysis.json",
        "golden_evidence": "38/60",
        "n": 42, "correct": 25,
        "checked": {"evidence_gap": 7, "stale_value": 1,
                    "interim_source": 4, "missing_corp": 3, "amount_gap": 0},
        "fired": {"evidence_gap": 5, "stale_value": 1,
                  "interim_source": 1, "missing_corp": 0, "amount_gap": 0},
    },
    "ablation_a6": {
        "responses": "artifacts/ablation/A6/responses",
        "labels": "artifacts/ablation/A6/report.json",
        "golden_evidence": "59/60",
        "n": 42, "correct": 36,
        "checked": {"evidence_gap": 7, "stale_value": 7,
                    "interim_source": 3, "missing_corp": 3, "amount_gap": 0},
        "fired": {"evidence_gap": 0, "stale_value": 1,
                  "interim_source": 0, "missing_corp": 0, "amount_gap": 0},
    },
    # Same configuration as the snapshot above, with no flag different. What
    # changed is the answers: the refusal gate and the as-of rule altered the
    # answer text, which moved the premise counts.
    #
    # No rule was changed. The ratios stay inside the drift factor, so no
    # warning is raised, but the numbers are recorded afresh because they only
    # mean something alongside their snapshot. Precision is unchanged.
    "ablation_a7": {
        "responses": "artifacts/ablation/A7/responses",
        "labels": "artifacts/ablation/A7/report.json",
        "golden_evidence": "59/60",
        "n": 42, "correct": 38,
        "checked": {"evidence_gap": 7, "stale_value": 6,
                    "interim_source": 2, "missing_corp": 3, "amount_gap": 0},
        "fired": {"evidence_gap": 0, "stale_value": 1,
                  "interim_source": 0, "missing_corp": 0, "amount_gap": 0},
    },
}


def completeness_audit(reports: Sequence[dict], *, baseline=None) -> dict:
    """Count premises held and rules fired separately across a set of
    completeness results. No LLM call.

    The fire rate divides firings by premises held rather than by the question
    count: questions whose premise never held were not under test, and putting
    them in the denominator makes the rule look better than it is.

    Given a baseline, the ratio of premise counts against it is reported too,
    and any kind beyond the drift factor is listed as drifted, meaning its
    premise now holds in a different world and its false alarms have to be
    measured again.
    See docs/design-rationale.md#premise-drift
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
    """Score an open-ended answer. One LLM call; closed questions are scored by
    normalised exact match and never reach here.

    A parse failure is a scoring failure rather than a wrong answer: `covered`
    is emptied, both remaining axes are set false, and the reason records what
    happened. A `covered` array whose length differs from the point count is
    the same kind of failure — the judge did not count the points — and
    padding it would turn that into a wrong answer.
    """
    points = list(key_points or [])
    result = chat(build_open_judge_messages(question, evidence, key_points, answer),
                  max_tokens=300, temperature=0.0, tag="judge_open")
    if budget:
        budget.spend_llm(result.prompt_tokens, result.completion_tokens)

    match = _JSON.search(result.text or "")
    if not match:
        return OpenVerdict((), False, False, "judge 응답 파싱 실패")
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return OpenVerdict((), False, False, "judge 응답 파싱 실패")

    covered = data.get("covered")
    if not isinstance(covered, list) or len(covered) != len(points):
        got = len(covered) if isinstance(covered, list) else "없음"
        return OpenVerdict((), False, False,
                           f"judge 요점 판정 길이 불일치 (받은 것 {got} · 요점 {len(points)})")

    return OpenVerdict(
        covered=tuple(bool(c) for c in covered),
        grounded=bool(data.get("grounded")),
        no_fabrication=bool(data.get("no_fabrication")),
        reason=str(data.get("reason", "")),
    )
