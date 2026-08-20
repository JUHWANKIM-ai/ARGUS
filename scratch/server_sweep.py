"""Fetch every evaluation answer from the deployed API and store the results.

Usage: python scratch/server_sweep.py <host> [--round N] [--arm NAME] [--pause S] [cohort ...]

`--pause` is the wait between questions. The evaluators take none, so a
measurement meant to predict their result takes none either. The default
stays above zero because a collection run that is not predicting anything
should not spend the upstream window it does not need.

Generation is sampled, so one pass measures one draw. `--round N` writes to its
own folders so several passes can be scored separately and the spread read off.
Round 1 keeps the unsuffixed names.

`--arm NAME` names the deployed arm being swept (default `A42`, the arm every
prior sweep used). It only changes where results land and the tag recorded
alongside them; it does not touch what is requested from the API.

Two outputs are written per cohort:

    scratch/ablation/v6/server_<cohort>/<arm>/responses/<qid>.json
        the runner's record format, so the runner scores these with its own
        code path instead of a reimplementation

    eval_server/<cohort>.json
        one tracked file per cohort holding the five-key answers, for reading
        without the runner

Requests are serial with a pause between them. The upstream enforces a
per-minute request limit, and exceeding it returns the limitation notice with
an upstream marker rather than an answer, which would otherwise be scored as a
wrong answer. Any response carrying that marker is retried with a widening
wait.
"""
import io
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, "src")
from argus.agent.pipeline import upstream_failure  # noqa: E402

COHORTS = {
    "full": ("evalset/questions.jsonl", "evalset/regression.jsonl"),
    "safety": ("evalset/safety.jsonl",),
    "valuechain": ("evalset/valuechain.jsonl",),
    #: 처음 보는 문항 54개. 세 벌을 따로 두는 이유는 각각 서로 다른 코드판에서 한 번
    #: 측정된 기록(17/20 · 17/18 · 15/16)이 있어, 벌 단위로 대조할 수 있어야 하기
    #: 때문이다. 🔴 이 셋은 재는 데만 쓴다 -- 여기서 드러난 실패를 고치면 그 순간
    #: 처음 보는 문항이 아니게 된다.
    "heldout_v2": ("evalset/heldout_v2.jsonl",),
    "heldout_v3": ("evalset/heldout_v3.jsonl",),
    "newset": ("evalset/newset_20260816.jsonl",),
}
TIMEOUT_S = 300
#: Pause between requests. The upstream limit is per minute, and a question can
#: spend several calls, so this stays well under one request per second.
PAUSE_S = 2.0
RETRIES = 5
#: Wait after a rate-limited answer. The limit is a fixed window, so waiting for
#: the window to turn over is what clears it.
BACKOFF_S = (20, 40, 70, 100, 140)


def cases(cohort: str) -> list[dict]:
    out = []
    for path in COHORTS[cohort]:
        for line in io.open(path, encoding="utf-8"):
            if line.strip():
                out.append(json.loads(line))
    return out


def ask_once(host: str, case: dict) -> tuple[dict, float]:
    url = "http://%s/answer?%s" % (host, urllib.parse.urlencode(
        {"question_id": case["question_id"], "question": case["question"]},
        encoding="utf-8"))
    started = time.time()
    with urllib.request.urlopen(url, timeout=TIMEOUT_S) as response:
        body = json.loads(response.read().decode("utf-8"))
    return body, time.time() - started


def ask(host: str, case: dict) -> tuple[dict, float, int]:
    for attempt in range(RETRIES + 1):
        body, elapsed = ask_once(host, case)
        if not upstream_failure(body.get("think_trace", "")):
            return body, elapsed, attempt
        if attempt == RETRIES:
            return body, elapsed, attempt
        time.sleep(BACKOFF_S[min(attempt, len(BACKOFF_S) - 1)])
    raise AssertionError("unreachable")


def sweep(host: str, cohort: str, round_no: int = 1, arm: str = "A42") -> tuple[int, int]:
    rows = cases(cohort)
    name = cohort if round_no == 1 else "%s_r%d" % (cohort, round_no)
    out = Path("scratch/ablation/v6/server_%s/%s/responses" % (name, arm))
    out.mkdir(parents=True, exist_ok=True)
    tracked = Path("eval_server")
    tracked.mkdir(exist_ok=True)
    answers = {}
    failed = 0
    for i, case in enumerate(rows, 1):
        body, elapsed, retries = ask(host, case)
        bad = bool(upstream_failure(body.get("think_trace", "")))
        failed += bad
        record = {
            "question_id": case["question_id"],
            "response": body,
            "calls": [],
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "llm_calls": 0,
            "latency_s": round(elapsed, 3),
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        (out / ("%s.json" % case["question_id"])).write_text(
            json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
        answers[case["question_id"]] = body
        print("  %-11s %3d/%-3d %-8s %5.1fs%s%s" % (
            cohort, i, len(rows), case["question_id"], elapsed,
            "  재시도 %d" % retries if retries else "",
            "  상류장애" if bad else ""), flush=True)
        time.sleep(PAUSE_S)
    (tracked / ("%s.json" % name)).write_text(
        json.dumps({"host": host, "cohort": cohort, "arm": arm,
                    "round": round_no,
                    "fetched": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "n": len(rows), "upstream_failures": failed,
                    "answers": answers}, ensure_ascii=False, indent=1),
        encoding="utf-8")
    return len(rows), failed


def main(argv) -> int:
    host = argv[0]
    rest = list(argv[1:])
    round_no = 1
    arm = "A42"
    if "--round" in rest:
        i = rest.index("--round")
        round_no = int(rest[i + 1])
        del rest[i:i + 2]
    if "--arm" in rest:
        i = rest.index("--arm")
        arm = rest[i + 1]
        del rest[i:i + 2]
    if "--pause" in rest:
        global PAUSE_S
        i = rest.index("--pause")
        PAUSE_S = float(rest[i + 1])
        del rest[i:i + 2]
    total = bad = 0
    for cohort in (rest or list(COHORTS)):
        n, f = sweep(host, cohort, round_no, arm)
        total += n
        bad += f
        print("%s: %d건 · 상류 장애 %d" % (cohort, n, f), flush=True)
    print("합계 %d건 · 상류 장애 %d" % (total, bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
