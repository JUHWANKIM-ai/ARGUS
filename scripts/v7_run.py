# scripts/v7_run.py
"""Run one arm of the v7 generation for one round.

Usage: python scripts/v7_run.py <arm> <round> [cohort ...]

Every round generates afresh. Rescoring stored answers cannot see a pipeline
change, and the question this generation asks is how much the answer moves
between runs, which only a fresh generation shows. The request cache stays on,
so a prompt that is byte-identical to one already sent replays for free and only
the questions a manipulation actually touches are paid for.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from argus import ablation                                    # noqa: E402
from scripts import error_analysis as ea                      # noqa: E402

COHORTS = ("full", "safety", "valuechain")
ROOT = Path("artifacts/ablation/v7")


def out_dir(arm_name: str, cohort: str, round_no: int) -> Path:
    return ROOT / ("%s_%s_r%d" % (arm_name, cohort, round_no)) / arm_name


def run_arm(arm_name: str, round_no: int,
            cohorts: list[str] | None = None) -> dict:
    records = ea._load_records()
    out: dict[str, dict] = {}
    for cohort in (cohorts or list(COHORTS)):
        base = out_dir(arm_name, cohort, round_no)
        ea.RESPONSE_DIR = base / "responses"
        ea.JUDGE_DIR = base / "judge"
        ea.OUT_DIR = base
        ea.RESPONSE_DIR.mkdir(parents=True, exist_ok=True)
        cases = ea.load_cohort(cohort)
        started = time.time()
        with ablation.use(arm_name):
            for index, case in enumerate(cases, 1):
                run, _ = ea.with_retry(ea.run_case, case, force=True)
                if case.get("answer_mode") == "open":
                    ea.with_retry(ea.run_judge, case, run["response"], force=True)
                print("  %-11s r%d %3d/%-3d %-8s llm=%s"
                      % (cohort, round_no, index, len(cases),
                         case["question_id"], run["llm_calls"]), flush=True)
                time.sleep(ea.PACE_S)
        rows = ea.analyse(cases, records)
        report = ea.build_report(cases, rows, cohort=cohort)
        report["arm"] = arm_name
        report["round"] = round_no
        (base / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
        overall = report["crosstab"]["overall"]
        out[cohort] = {"correct": overall["correct"], "n": overall["n"]}
        print("%s r%d %s %d/%d %.0f초" % (arm_name, round_no, cohort,
              overall["correct"], overall["n"], time.time() - started), flush=True)
    return out


def main(argv) -> int:
    arm_name, round_no = argv[0], int(argv[1])
    result = run_arm(arm_name, round_no, argv[2:] or None)
    total = sum(v["correct"] for v in result.values())
    n = sum(v["n"] for v in result.values())
    print("%s r%d 합계 %d/%d" % (arm_name, round_no, total, n))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
