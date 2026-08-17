"""Aggregate the v7 grid and apply the adoption rule.

Usage: python scripts/v7_report.py

See docs/design-rationale.md#v7-adoption-judge-band
"""
from __future__ import annotations

import io
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from scripts.v7_run import COHORTS, out_dir                   # noqa: E402

ROUNDS = (1, 2, 3)
#: Width of the judge's own spread, measured twice at three rounds each. An
#: improvement inside it is not distinguishable from judge noise.
JUDGE_BAND = 1


def verdict(arm_rows: dict[str, list[int]], base: list[int],
            stable: set[str], flips: dict[str, set[str]],
            wrong: dict[str, set[str]],
            base_flips: set[str] = frozenset()) -> tuple[bool, list[str]]:
    """Whether an arm is adopted, with the reasons either way.

    Rejects outright unless both the arm and the baseline carry the full
    round count. A truncated arm is easier to adopt than a complete one,
    because its minimum is taken over fewer draws.

    `wrong` supplies each arm's questions wrong in at least one counted
    round (the union `wrong_sets` produces). The regression check reads this
    rather than `flips`, because `flips` is a union-minus-intersection and
    drops a question the arm gets wrong in every round: that question sits
    in both the union and the intersection, and never appears in `flips`, so
    a permanent regression on it would otherwise go unreported.

    See docs/design-rationale.md#v7-adoption-judge-band
    """
    reasons: list[str] = []
    ok = True
    for name, totals in arm_rows.items():
        if len(totals) != len(ROUNDS) or len(base) != len(ROUNDS):
            return False, ["%s 회차가 모자라 판정할 수 없다 (arm %d 회차 · 기준 %d 회차 · "
                           "%d 회차 필요)" % (name, len(totals), len(base), len(ROUNDS))]
        gain = min(totals) - min(base)
        arm_flip_count = len(flips.get(name, set()))
        if gain <= 0:
            ok = False
            reasons.append("%s 최솟값이 %d 로 기준 %d 를 넘지 못했다"
                           % (name, min(totals), min(base)))
        elif gain <= JUDGE_BAND:
            if arm_flip_count < len(base_flips):
                reasons.append("%s 최솟값 상승 %d 는 판정기 잡음 폭 %d 안이지만 "
                               "흔들린 문항이 %d 에서 %d 로 줄었다"
                               % (name, gain, JUDGE_BAND, len(base_flips), arm_flip_count))
            else:
                ok = False
                reasons.append("%s 최솟값 상승 %d 는 판정기 잡음 폭 %d 안이다"
                               % (name, gain, JUDGE_BAND))
        else:
            reasons.append("%s 최솟값 %d -> %d" % (name, min(base), min(totals)))
        broken = sorted(stable & wrong.get(name, set()))
        if broken:
            ok = False
            reasons.append("%s 가 안정 문항을 흔들었다: %s" % (name, " ".join(broken)))
    return ok, reasons


def totals(arm_name: str) -> list[int]:
    out = []
    for round_no in ROUNDS:
        got = 0
        seen = 0
        for cohort in COHORTS:
            path = out_dir(arm_name, cohort, round_no) / "report.json"
            if not path.exists():
                break
            overall = json.load(io.open(path, encoding="utf-8"))["crosstab"]["overall"]
            got += overall["correct"]
            seen += overall["n"]
        else:
            out.append(got)
    return out


def wrong_sets(arm_name: str) -> dict[int, set[str]]:
    """Wrong-question set per round, kept only for rounds `totals` also keeps.

    Mirrors `totals`' for-else exactly: the inner loop breaks the moment a
    cohort's report is missing, and the round is recorded only when every
    cohort was found. A round interrupted after the first cohort must not
    count here either, or a question wrong in every completed round of that
    round reads as a phantom flip once the interrupted round is compared
    against the others.
    """
    out: dict[int, set[str]] = {}
    for round_no in ROUNDS:
        bad: set[str] = set()
        for cohort in COHORTS:
            path = out_dir(arm_name, cohort, round_no) / "report.json"
            if not path.exists():
                break
            for row in json.load(io.open(path, encoding="utf-8"))["rows"]:
                if not row.get("correct"):
                    bad.add(row["question_id"])
        else:
            out[round_no] = bad
    return out


def _base_stable(base_name: str, base_rounds: dict[int, set[str]]) -> set[str]:
    """Questions the baseline never got wrong across its own available rounds.

    Substitutes for the spec's named population (v6, correct across all six
    runs): the v6 per-question record is not guaranteed to be on disk, so
    this reads the baseline's own v7 rounds instead.
    See docs/design-rationale.md#v7-adoption-judge-band

    The question universe is enumerated from whichever round has a complete
    report, rather than pinning round 1: pinning it means a missing round 1
    silently empties this set even with rounds 2 and 3 present, which turns
    condition 2 off without any error.
    """
    if not base_rounds:
        return set()
    ever = set().union(*base_rounds.values())
    any_round = next(iter(base_rounds))
    stable: set[str] = set()
    for cohort in COHORTS:
        path = out_dir(base_name, cohort, any_round) / "report.json"
        if path.exists():
            for row in json.load(io.open(path, encoding="utf-8"))["rows"]:
                if row["question_id"] not in ever:
                    stable.add(row["question_id"])
    return stable


def fingerprints(arms: list[str]) -> dict[str, str | None]:
    """Each arm's index fingerprint, read from its first available report.

    None when the arm has no report yet, which is an absence rather than a
    disagreement and must not trip the mismatch check below.
    See docs/design-rationale.md#v7-index-rebuild
    """
    out: dict[str, str | None] = {}
    for name in arms:
        found = None
        for round_no in ROUNDS:
            for cohort in COHORTS:
                path = out_dir(name, cohort, round_no) / "report.json"
                if path.exists():
                    found = json.load(io.open(path, encoding="utf-8")).get("index_fingerprint")
                    break
            if found is not None:
                break
        out[name] = found
    return out


def fingerprint_mismatch(fps: dict[str, str | None]) -> str | None:
    """Reason to refuse the comparison table, or None when every measured arm
    shares one index fingerprint.

    Only V7-5 rebuilds the index (see docs/design-rationale.md#v7-index-rebuild),
    so its fingerprint differs from every other arm once measured, and its
    delta must never be summed with theirs into one table. An arm with no
    report yet contributes no fingerprint and is not part of the comparison.
    """
    measured = {name: fp for name, fp in fps.items() if fp}
    if len({*measured.values()}) <= 1:
        return None
    groups: dict[str, list[str]] = defaultdict(list)
    for name, fp in measured.items():
        groups[fp].append(name)
    parts = ["%s: %s" % (fp, ", ".join(sorted(names)))
             for fp, names in sorted(groups.items())]
    return "arm 이 서로 다른 인덱스로 측정됐다 — 한 표에 합칠 수 없다: " + " | ".join(parts)


def main(argv) -> int:
    arms = argv or ["A42", "V7-1", "V7-2", "V7-3", "V7-4", "V7-5"]
    base_name = arms[0]

    mismatch = fingerprint_mismatch(fingerprints(arms))
    if mismatch:
        print(mismatch)
        return 1

    grid = {name: totals(name) for name in arms}
    print("%-8s %s" % ("arm", "회차별 정답 (112 만점)"))
    for name in arms:
        print("  %-8s %s   최소 %s" % (name, grid[name] or "-",
              min(grid[name]) if grid[name] else "-"))

    flips: dict[str, set[str]] = {}
    wrong_ever: dict[str, set[str]] = {}
    for name in arms:
        rounds = wrong_sets(name)
        union = set().union(*rounds.values()) if rounds else set()
        inter = set.intersection(*rounds.values()) if rounds else set()
        flips[name] = union - inter
        wrong_ever[name] = union

    print("\narm 별 흔들린 문항")
    for name in arms:
        print("  %-8s %d문항  %s" % (name, len(flips[name]),
              " ".join(sorted(flips[name])) or "-"))

    base_stable = _base_stable(base_name, wrong_sets(base_name))

    print("\n채택 판정 (기준 %s)" % base_name)
    for name in arms[1:]:
        ok, why = verdict({name: grid[name]}, grid[base_name], base_stable, flips,
                          wrong_ever, flips.get(base_name, set()))
        print("  %-8s %s" % (name, "채택" if ok else "기각"))
        for line in why:
            print("      %s" % line)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
