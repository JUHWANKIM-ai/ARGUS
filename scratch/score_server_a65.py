r"""`A65` 서버 스윕을 채점한다. 회차별로 따로 돌 수 있다.

`scratch/server_sweep.py` 는 `responses/` 만 쓴다. 판정(개방형)과 `report.json` 은
`scripts/error_analysis.py` 의 경로를 그 폴더에 걸어서 만든다 — 채점 코드를 다시 쓰지
않는다. 같은 검사를 두 곳에 만들면 한쪽만 고쳐지는 것이 이 저장소의 반복 실패다.

    python scratch/score_server_a65.py --round 1
    python scratch/score_server_a65.py --round 1 --round 2 --round 3

이미 판정 파일이 있으면 다시 부르지 않는다(`force=False`) — 판정은 유료다.

Usage: python scratch/score_server_a65.py [--round N ...] [--arm A65] [--force]
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, ".")
sys.path.insert(0, "src")
sys.stdout.reconfigure(encoding="utf-8")

from scripts import error_analysis as ea                          # noqa: E402

BASE = Path("scratch/ablation/v6")
COHORTS = ("full", "valuechain", "safety")


def run_dir(cohort: str, round_no: int, arm: str) -> Path:
    name = "server_%s" % (cohort if round_no == 1 else "%s_r%d" % (cohort, round_no))
    return BASE / name / arm


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--round", type=int, action="append", default=None)
    ap.add_argument("--arm", default="A65")
    ap.add_argument("--force", action="store_true", help="판정을 다시 부른다 (재과금)")
    args = ap.parse_args()
    rounds = args.round or [1, 2, 3]

    ea._use_v2_dirs()
    records = ea._load_records()
    for round_no in rounds:
        for cohort in COHORTS:
            base = run_dir(cohort, round_no, args.arm)
            if not (base / "responses").exists():
                print("🔴 없다: %s" % (base / "responses"))
                continue
            ea.RESPONSE_DIR = base / "responses"
            ea.JUDGE_DIR = base / "judge"
            ea.OUT_DIR = base
            ea.JUDGE_DIR.mkdir(parents=True, exist_ok=True)
            cases = ea.load_cohort(cohort)
            started = time.time()
            judged = 0
            for case in cases:
                if case.get("answer_mode") != "open":
                    continue
                stored = ea.JUDGE_DIR / ("%s.json" % case["question_id"])
                if stored.exists() and not args.force:
                    continue
                path = ea.RESPONSE_DIR / ("%s.json" % case["question_id"])
                if not path.exists():
                    continue
                run = json.loads(path.read_text(encoding="utf-8"))
                ea.with_retry(ea.run_judge, case, run["response"], force=True)
                judged += 1
                time.sleep(ea.PACE_S)
            rows = ea.analyse(cases, records)
            report = ea.build_report(cases, rows, cohort=cohort)
            report["arm"] = args.arm
            (base / "report.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
            ok = sum(1 for r in rows if r["correct"])
            wrong = sorted(r["question_id"] for r in rows if not r["correct"])
            print("r%d %-11s %2d/%-2d · 판정 %2d회 · %.0f초 · 오답 %s"
                  % (round_no, cohort, ok, len(rows), judged, time.time() - started,
                     " ".join(wrong) or "없음"), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
