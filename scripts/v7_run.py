# scripts/v7_run.py
"""Run one arm of the v7 generation for one round, writing one report.json per cohort.

Usage: python scripts/v7_run.py <arm> <round> [cohort ...]

See docs/design-rationale.md#fresh-generation-measurement
"""
from __future__ import annotations

import hashlib
import json
import socket
import ssl
import sys
import time
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from argus import ablation                                    # noqa: E402
from scripts import error_analysis as ea                      # noqa: E402

COHORTS = ("full", "safety", "valuechain")
ROOT = Path("artifacts/ablation/v7")

#: Transport-level faults worth retrying: socket timeouts, connection resets
#: and aborts, TLS failures, DNS resolution failures, and other urllib-level
#: connection errors. Deliberately narrow: it must not include FileNotFoundError,
#: PermissionError or other OSError subclasses that signal a real defect rather
#: than a transient network fault.
#: See docs/design-rationale.md#fresh-generation-measurement
TRANSPORT_ERRORS = (
    TimeoutError,
    ConnectionError,
    ssl.SSLError,
    socket.gaierror,
    urllib.error.URLError,
)
#: Bounded attempts and the base of the widening wait between them.
TRANSPORT_RETRY_ATTEMPTS = 4
TRANSPORT_RETRY_BASE_S = 5.0


def with_transport_retry(fn, *args,
                          attempts: int = TRANSPORT_RETRY_ATTEMPTS,
                          base_wait_s: float = TRANSPORT_RETRY_BASE_S,
                          sleep=time.sleep, **kwargs):
    """Call fn(*args, **kwargs), retrying only on transport failures.

    Retries exceptions in TRANSPORT_ERRORS with a widening wait between
    attempts (base_wait_s doubled each time) and prints one line per retry.
    Any other exception, including a programming error, a missing file, a
    KeyboardInterrupt, or a budget or assertion error, is not caught here and
    propagates on the first raise.

    When every attempt fails, the last transport error propagates so the
    caller fails loudly instead of silently recording a hole.

    sleep is injectable so a test can exercise the retry loop without
    waiting in real time.
    """
    for attempt in range(attempts):
        try:
            return fn(*args, **kwargs)
        except TRANSPORT_ERRORS as exc:
            if attempt == attempts - 1:
                raise
            wait_s = base_wait_s * (2 ** attempt)
            print(f"    전송 오류 {exc!r} — {wait_s:.0f}s 대기 후 재시도 "
                  f"({attempt + 1}/{attempts - 1})")
            sleep(wait_s)
    raise RuntimeError("unreachable")  # pragma: no cover


#: The payload V7-5's index rebuild replaces. Hashed into every report so a
#: later comparison can tell whether two arms were measured against the
#: same index.
#: See docs/design-rationale.md#v7-index-rebuild
INDEX_FILE = Path("artifacts/exchange_bm25.json")


def out_dir(arm_name: str, cohort: str, round_no: int) -> Path:
    return ROOT / ("%s_%s_r%d" % (arm_name, cohort, round_no)) / arm_name


def index_fingerprint() -> str:
    """Short digest of the exchange BM25 index in place at run time.

    Empty when the index file is absent, rather than raising: a fresh
    checkout without artifacts/ built yet must still be able to run.

    Only the V7-5 arm rebuilds this index, so a differing fingerprint
    between two report.json files is what marks their runs as not
    comparable in one table.
    See docs/design-rationale.md#v7-index-rebuild
    """
    if not INDEX_FILE.exists():
        return ""
    return hashlib.sha256(INDEX_FILE.read_bytes()).hexdigest()[:12]


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
                run, _ = with_transport_retry(ea.with_retry, ea.run_case, case, force=True)
                if case.get("answer_mode") == "open":
                    with_transport_retry(
                        ea.with_retry, ea.run_judge, case, run["response"], force=True)
                print("  %-11s r%d %3d/%-3d %-8s llm=%s"
                      % (cohort, round_no, index, len(cases),
                         case["question_id"], run["llm_calls"]), flush=True)
                time.sleep(ea.PACE_S)
        rows = ea.analyse(cases, records)
        report = ea.build_report(cases, rows, cohort=cohort)
        report["arm"] = arm_name
        report["round"] = round_no
        report["index_fingerprint"] = index_fingerprint()
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
