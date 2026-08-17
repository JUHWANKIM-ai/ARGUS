"""Aggregate the v7 grid and apply the adoption rule.

Usage: python scripts/v7_report.py

The rule is in code rather than in a reader's head because it has three parts
and a hand check drops one. A run is a single draw, so the minimum across
rounds is what a single evaluation is expected to produce; a higher mean with
an unchanged minimum buys nothing.
See docs/superpowers/specs/2026-08-17-ablation-v7-design.md
"""
from __future__ import annotations

import io
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from scripts.v7_run import out_dir                            # noqa: E402

COHORTS = ("full", "safety", "valuechain")
ROUNDS = (1, 2, 3)
#: Width of the judge's own spread, measured twice at three rounds each. An
#: improvement inside it is not distinguishable from judge noise.
JUDGE_BAND = 1


def verdict(arm_rows: dict[str, list[int]], base: list[int],
            stable: set[str], flips: dict[str, set[str]],
            base_flips: set[str] = frozenset()) -> tuple[bool, list[str]]:
    """Whether an arm is adopted, with the reasons either way.

    A gain inside the judge band is not automatically a rejection: it is
    still adopted when the arm's flip count is strictly lower than the
    baseline's, because a steadier set of flipping questions is itself the
    result this generation targets. A gain of zero or less is rejected
    regardless of flips, since the minimum is what a single evaluation is
    expected to produce and a smaller flip count does not change that.
    """
    reasons: list[str] = []
    ok = True
    for name, totals in arm_rows.items():
        if not totals or not base:
            return False, ["회차가 모자라 판정할 수 없다"]
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
        broken = sorted(stable & flips.get(name, set()))
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
    out: dict[int, set[str]] = {}
    for round_no in ROUNDS:
        bad: set[str] = set()
        found = False
        for cohort in COHORTS:
            path = out_dir(arm_name, cohort, round_no) / "report.json"
            if not path.exists():
                continue
            found = True
            for row in json.load(io.open(path, encoding="utf-8"))["rows"]:
                if not row.get("correct"):
                    bad.add(row["question_id"])
        # `found` records whether any cohort for this round had a report,
        # rather than reusing the last `path` checked in the loop above:
        # a round with real data but zero wrong answers must still be kept,
        # and the last cohort tried is not necessarily the one that answered.
        if found:
            out[round_no] = bad
    return out


def main(argv) -> int:
    arms = argv or ["A42", "V7-1", "V7-2", "V7-3", "V7-4", "V7-5"]
    base_name = arms[0]
    grid = {name: totals(name) for name in arms}
    print("%-8s %s" % ("arm", "회차별 정답 (112 만점)"))
    for name in arms:
        print("  %-8s %s   최소 %s" % (name, grid[name] or "-",
              min(grid[name]) if grid[name] else "-"))

    flips: dict[str, set[str]] = {}
    counts: dict[str, int] = defaultdict(int)
    for name in arms:
        rounds = wrong_sets(name)
        union = set().union(*rounds.values()) if rounds else set()
        inter = set.intersection(*rounds.values()) if rounds else set()
        flips[name] = union - inter
        for qid in union:
            counts[qid] += 1
    stable = {qid for qid in counts if counts[qid] == 0}

    print("\narm 별 흔들린 문항")
    for name in arms:
        print("  %-8s %d문항  %s" % (name, len(flips[name]),
              " ".join(sorted(flips[name])) or "-"))

    base_stable = set()
    base_rounds = wrong_sets(base_name)
    if base_rounds:
        ever = set().union(*base_rounds.values())
        for cohort in COHORTS:
            path = out_dir(base_name, cohort, 1) / "report.json"
            if path.exists():
                for row in json.load(io.open(path, encoding="utf-8"))["rows"]:
                    if row["question_id"] not in ever:
                        base_stable.add(row["question_id"])

    print("\n채택 판정 (기준 %s)" % base_name)
    for name in arms[1:]:
        ok, why = verdict({name: grid[name]}, grid[base_name], base_stable, flips,
                          flips.get(base_name, set()))
        print("  %-8s %s" % (name, "채택" if ok else "기각"))
        for line in why:
            print("      %s" % line)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
