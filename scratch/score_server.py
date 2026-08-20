"""Score the answers collected from the deployed API against the golden set.

Usage: python scratch/score_server.py [--rounds N] [--arm NAME] [cohort ...]

Generation is sampled, so one pass is one draw. `--rounds N` scores every round
collected by `server_sweep.py --round` and reports the spread, which is what
decides whether a difference between two configurations is a signal.

`--arm NAME` must match whatever `server_sweep.py --arm NAME` was run with
(default `A42`); it only selects which arm subfolder is read/written under
each server run, not the local comparison column (see `LOCAL` below).

The stored responses are read through the runner's own scorer, so closed
questions go through `score_closed` and open questions through the LLM judge
exactly as a local run would. The judge cache is written to the server arm's
own folder, because the local cache keys on question id alone and would hand
back verdicts passed on a different answer.

Also prints the matching local run so the two columns can be read side by side.
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, ".")
sys.path.insert(0, "src")
from scripts import error_analysis as ea  # noqa: E402

#: The local run each server cohort is compared against. Fixed to the a42_*
#: local runs regardless of which arm is being swept on the server (`--arm`):
#: this is the one stable yardstick every server sweep is read against, not a
#: record of which arm is currently deployed. Each of these local runs stores
#: its own answers under its own arm subfolder (`a42_full/A42/`), so this
#: comparison keeps reading `A42` even when `--arm` names something else.
LOCAL = {"full": "a42_full", "safety": "a42_safety", "valuechain": "a42_valuechain"}
BASE = Path("artifacts/ablation/v6")


def judge(cases: list[dict]) -> int:
    """Produce the open-question verdicts that `analyse` reads.

    `analyse` only reads stored verdicts, so an arm folder with no `judge/`
    scores every open question wrong. Verdicts are keyed by question id alone,
    which is why the server arm keeps its own folder instead of sharing the
    local one.
    """
    made = 0
    for case in cases:
        if case.get("answer_mode") != "open":
            continue
        path = ea.RESPONSE_DIR / ("%s.json" % case["question_id"])
        if not path.exists():
            continue
        run = json.loads(path.read_text(encoding="utf-8"))
        _, cached = ea.with_retry(ea.run_judge, case, run["response"])
        if not cached:
            made += 1
            time.sleep(ea.PACE_S)
    return made


def score(run: str, cohort: str, records: dict, arm: str = "A42") -> dict | None:
    arm_dir = BASE / run / arm
    if not (arm_dir / "responses").exists():
        return None
    ea.RESPONSE_DIR = arm_dir / "responses"
    ea.JUDGE_DIR = arm_dir / "judge"
    ea.OUT_DIR = arm_dir
    cases = ea.load_cohort(cohort)
    made = judge(cases)
    if made:
        print("    %s 판정 %d건 신규" % (run, made), flush=True)
    rows = ea.analyse(cases, records)
    report = ea.build_report(cases, rows, cohort=cohort)
    report["arm"] = arm
    arm_dir.mkdir(parents=True, exist_ok=True)
    (arm_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return report


def cell(report: dict | None) -> str:
    if not report:
        return "%-26s" % "없음"
    overall = report.get("crosstab", {}).get("overall", {})
    rows = report.get("rows", [])
    n = overall.get("n", len(rows))
    bad = sum(1 for r in rows if r.get("upstream_failure"))
    return "%3d/%-3d %5.1f%% 인용%4.0f%% 장애%d" % (
        overall.get("correct", 0), n, 100 * overall.get("rate", 0.0),
        100 * (report.get("citation_rate") or 0.0), bad)


def verdicts(report: dict | None) -> dict:
    return {r["question_id"]: r for r in (report or {}).get("rows", [])}


def run_name(cohort: str, round_no: int) -> str:
    return "server_%s" % (cohort if round_no == 1 else "%s_r%d" % (cohort, round_no))


def main(argv) -> int:
    argv = list(argv)
    rounds = 1
    arm = "A42"
    if "--rounds" in argv:
        i = argv.index("--rounds")
        rounds = int(argv[i + 1])
        del argv[i:i + 2]
    if "--arm" in argv:
        i = argv.index("--arm")
        arm = argv[i + 1]
        del argv[i:i + 2]
    cohorts = argv or list(LOCAL)
    records = ea._load_records()

    #: {cohort: {round: report}}. Missing rounds stay absent rather than zero,
    #: so a partial collection is visible instead of dragging the mean down.
    got: dict[str, dict[int, dict]] = {}
    for cohort in cohorts:
        got[cohort] = {}
        for round_no in range(1, rounds + 1):
            report = score(run_name(cohort, round_no), cohort, records, arm)
            if report:
                got[cohort][round_no] = report

    print("\n%-12s %s  %s" % ("코호트",
          " ".join("%-12s" % ("서버 %d회" % r) for r in range(1, rounds + 1)), "로컬"))
    want = {c: (got[c][1]["crosstab"]["overall"]["n"] if got[c].get(1) else 0)
            for c in cohorts}
    for cohort in cohorts:
        line = " ".join("%-14s" % _score_of(got[cohort].get(r), want[cohort])
                        for r in range(1, rounds + 1))
        print("%-12s %s  %s" % (cohort, line,
              _score_of(score(LOCAL[cohort], cohort, records))))

    n = sum(want.values())
    full_rounds = [r for r in range(1, rounds + 1)
                   if all(got[c].get(r)
                          and got[c][r]["crosstab"]["overall"]["n"] == want[c]
                          for c in cohorts)]
    totals = [(r, sum(got[c][r]["crosstab"]["overall"]["correct"] for c in cohorts))
              for r in full_rounds]
    print("\n전체 %d문항 · 완료된 회차: %s"
          % (n, " · ".join("%d회 %d" % (r, t) for r, t in totals) or "없음"))
    if len(totals) > 1:
        values = [t for _, t in totals]
        print("  최소 %d · 최대 %d · 평균 %.1f (%.1f%%)"
              % (min(values), max(values), sum(values) / len(values),
                 100 * sum(values) / len(values) / n))

    print("\n회차마다 판정이 흔들린 문항")
    shaky = 0
    for cohort in cohorts:
        seen = [verdicts(got[cohort].get(r)) for r in range(1, rounds + 1)]
        seen = [v for v in seen if v]
        if len(seen) < 2:
            continue
        for qid in sorted(seen[0]):
            #: A round that has not collected this question yet is absent, not
            #: wrong. Reading a missing row as wrong turns an unfinished sweep
            #: into a cohort-wide regression.
            if any(qid not in v for v in seen):
                continue
            marks = [v.get(qid, {}).get("correct") for v in seen]
            if len(set(marks)) > 1:
                shaky += 1
                print("    %-8s %-11s %s" % (qid, cohort,
                      " ".join("정답" if m else "오답" for m in marks)))
    if not shaky:
        print("    없음")
    return 0


def _score_of(report: dict | None, expected: int = 0) -> str:
    """A round still collecting is marked rather than shown as a rate.

    A partial round reads as a perfect score otherwise: fourteen of fourteen
    looks like 100 percent when it means the sweep has reached question
    fourteen of eighty-eight.
    """
    if not report:
        return "-"
    overall = report["crosstab"]["overall"]
    if expected and overall["n"] < expected:
        return "%d/%d 수집중" % (overall["correct"], overall["n"])
    return "%d/%d %.1f%%" % (overall["correct"], overall["n"],
                             100 * overall["rate"])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
