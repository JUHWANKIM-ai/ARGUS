"""Re-score A43's stored responses under the strict judge.

Usage: python scripts/v8_rejudge.py [--round N] [cohort ...]

The judge changed, so every v1-v7 number was produced by a different scorer
and none of them compares to a v8 number. This runner rebuilds the baseline
from the answers we already have: it reads the stored responses and calls the
judge over them, spending one LLM call per open question and regenerating
nothing. Regenerating would draw new samples and the comparison would then
measure the generator and the scorer at once.

Closed questions are skipped. They are scored by normalised exact match with
no LLM call, and the judge change does not touch them.

Source:  scratch/ablation/v6/server_<cohort>[_r<n>]/A42/responses/
Output:  scratch/ablation/v8/rejudge_<cohort>_r<n>/A43/
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, "src")
sys.path.insert(0, ".")
from scripts import error_analysis as ea  # noqa: E402

COHORTS = ("full", "safety", "valuechain")


def source_dir(cohort: str, round_no: int) -> Path:
    name = cohort if round_no == 1 else f"{cohort}_r{round_no}"
    return Path(f"scratch/ablation/v6/server_{name}/A42/responses")


def out_dir(cohort: str, round_no: int) -> Path:
    return Path(f"scratch/ablation/v8/rejudge_{cohort}_r{round_no}/A43")


def stored_response(responses: Path, qid: str) -> dict:
    """The stored response body, or FileNotFoundError.

    A missing file is raised rather than skipped: a silently short cohort
    would report a baseline over a different denominator than the arm it is
    compared against.
    """
    path = responses / f"{qid}.json"
    if not path.exists():
        raise FileNotFoundError(f"저장된 응답이 없습니다: {path}")
    return json.loads(path.read_text(encoding="utf-8"))["response"]


def open_cases(cases: list[dict]) -> list[dict]:
    return [c for c in cases if c.get("answer_mode") == "open"]


def rejudge(cohort: str, round_no: int) -> tuple[int, int]:
    responses = source_dir(cohort, round_no)
    target = out_dir(cohort, round_no)
    ea.RESPONSE_DIR = responses
    ea.JUDGE_DIR = target / "judge"
    ea.OUT_DIR = target
    ea.JUDGE_DIR.mkdir(parents=True, exist_ok=True)
    cases = ea.load_cohort(cohort)
    judged = 0
    for index, case in enumerate(open_cases(cases), 1):
        body = stored_response(responses, case["question_id"])
        ea.with_retry(ea.run_judge, case, body, force=True)
        judged += 1
        print("  %-11s %3d %-8s" % (cohort, index, case["question_id"]), flush=True)
        time.sleep(ea.PACE_S)
    rows = ea.analyse(cases, ea._load_records())
    report = ea.build_report(cases, rows, cohort=cohort)
    report["arm"] = "A43"
    (target / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    correct = sum(1 for r in rows if r["correct"])
    return len(rows), correct


def main(argv) -> int:
    rest = list(argv)
    round_no = 1
    if "--round" in rest:
        i = rest.index("--round")
        round_no = int(rest[i + 1])
        del rest[i:i + 2]
    total = right = 0
    for cohort in (rest or list(COHORTS)):
        n, ok = rejudge(cohort, round_no)
        total += n
        right += ok
        print("%s r%d: %d/%d" % (cohort, round_no, ok, n), flush=True)
    print("합계 r%d: %d/%d" % (round_no, right, total))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
