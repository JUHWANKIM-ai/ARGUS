r"""`examples/test.py` 가 뽑은 답변 꾸러미 하나를 골든셋·미공개셋 잣대로 채점한다.

    PYTHONIOENCODING=utf-8 python scripts/score_bundle.py examples/answers2.json --tag B2

## 왜 따로 필요한가

`test.py` 는 166문항(골든 112 + 미공개 54)을 **한 파일**에 담는다. 반면 채점기들은
`scratch/ablation/v6/server_<코호트>/<팔>/responses/<문항>.json` 배치를 읽는다. 이 도구는
꾸러미를 그 배치로 펼치기만 하고, **채점은 기존 함수를 그대로 부른다.**

    골든셋   scripts/error_analysis.analyse + build_report
    미공개셋  scripts/score_heldout.score

같은 검사를 두 곳에 만들면 한쪽만 고쳐지는 것이 이 저장소의 반복 실패다. 그래서 규칙은
한 줄도 옮겨 적지 않는다.

## 🔴 `--tag` 는 팔 이름이 아니다

꾸러미가 어느 구성에서 나왔는지는 파일 안에 안 적혀 있다(`host` 와 `fetched` 뿐이다).
그래서 이 도구는 팔을 **추측하지 않고** 사용자가 준 꼬리표를 그대로 디렉터리 이름으로
쓴다. 산출물에도 `host`·`fetched` 를 함께 적어, 나중에 어느 실행의 값인지 되짚을 수
있게 한다.
"""
import argparse
import importlib.util
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, ".")
sys.path.insert(0, "src")
sys.stdout.reconfigure(encoding="utf-8")

from scripts import error_analysis as ea                          # noqa: E402

_spec = importlib.util.spec_from_file_location("_sh", "scripts/score_heldout.py")
_sh = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_sh)

_ss = importlib.util.spec_from_file_location("_ss", "scripts/score_server.py")
_score_server = importlib.util.module_from_spec(_ss)
_ss.loader.exec_module(_score_server)

BASE = Path("scratch/ablation/v6")

#: 골든셋 코호트 -> 그 코호트가 읽는 평가셋 파일들. `error_analysis.load_cohort` 와
#: 같은 출처를 쓴다.
GOLDEN = {"full": ("questions", "regression"),
          "safety": ("safety",), "valuechain": ("valuechain",)}


def _evalset(name: str) -> list[dict]:
    out = []
    for line in io.open("evalset/%s.jsonl" % name, encoding="utf-8"):
        if line.strip():
            out.append(json.loads(line))
    return out


def spread(bundle: dict, tag: str) -> dict[str, int]:
    """꾸러미를 코호트별 `responses/` 로 펼친다. 어디에 몇 건을 놓았는지 돌려준다."""
    answers = bundle["answers"]
    placed: dict[str, int] = {}
    seen: set[str] = set()

    where = {}
    for cohort, names in GOLDEN.items():
        for name in names:
            for case in _evalset(name):
                where[case["question_id"]] = cohort
    for cohort, path, _, _ in _sh.SUBSETS:
        for case in _sh.load(path):
            where[case["question_id"]] = cohort

    for qid, payload in answers.items():
        cohort = where.get(qid)
        if cohort is None:
            continue
        out = BASE / ("server_%s" % cohort) / tag / "responses"
        out.mkdir(parents=True, exist_ok=True)
        (out / ("%s.json" % qid)).write_text(json.dumps({
            "question_id": qid,
            "response": {k: payload.get(k) for k in
                         ("question_id", "question", "retrieved_context",
                          "think_trace", "answer")},
            "calls": [], "prompt_tokens": 0, "completion_tokens": 0,
            "llm_calls": 0, "latency_s": payload.get("latency_s", 0.0),
            "ts": bundle.get("fetched", ""),
        }, ensure_ascii=False, indent=1), encoding="utf-8")
        placed[cohort] = placed.get(cohort, 0) + 1
        seen.add(qid)

    missing = sorted(set(answers) - seen)
    if missing:
        print("🔴 평가셋에서 못 찾은 문항 %d개: %s" % (len(missing), missing[:8]))
    return placed


def score_golden(tag: str, records: dict) -> tuple[int, int, list]:
    ok = total = 0
    bad = []
    for cohort in GOLDEN:
        arm_dir = BASE / ("server_%s" % cohort) / tag
        if not (arm_dir / "responses").exists():
            continue
        ea.RESPONSE_DIR = arm_dir / "responses"
        ea.JUDGE_DIR = arm_dir / "judge"
        ea.OUT_DIR = arm_dir
        ea.JUDGE_DIR.mkdir(parents=True, exist_ok=True)
        cases = ea.load_cohort(cohort)
        #: 🔴 `analyse` 는 저장된 판정을 **읽기만** 한다. 판정기를 먼저 안 돌리면
        #: 개방형 문항이 전부 오답이 된다. 첫 판이 그렇게 43건을 잃었고, 그 수가
        #: 판정기 적용 대상 43건과 정확히 같아 결함이 드러났다.
        made = _score_server.judge(cases)
        if made:
            print("  %s 판정 %d건 신규" % (cohort, made), flush=True)
        rows = ea.analyse(cases, records)
        report = ea.build_report(cases, rows, cohort=cohort)
        report["arm"] = tag
        (arm_dir / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
        good = sum(1 for r in rows if r["correct"])
        ok += good
        total += len(rows)
        bad += [r["question_id"] for r in rows if not r["correct"]]
        print("  %-11s %3d/%3d  %s" % (cohort, good, len(rows),
                                       [q for q in bad if q in
                                        {r["question_id"] for r in rows}] or ""))
    return ok, total, bad


def score_heldout(tag: str, records: dict) -> tuple[int, int, list]:
    ok = total = 0
    bad = []
    for cohort, path, _, _ in _sh.SUBSETS:
        if not (BASE / ("server_%s" % cohort) / tag / "responses").exists():
            continue
        _, rows = _sh.score(cohort, path, records, tag)
        good = sum(1 for r in rows if r["correct"])
        ok += good
        total += len(rows)
        wrong = [r["question_id"] for r in rows if not r["correct"]]
        bad += wrong
        print("  %-11s %3d/%3d  %s" % (cohort, good, len(rows), wrong or ""))
    return ok, total, bad


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("bundle")
    ap.add_argument("--tag", required=True,
                    help="산출물 디렉터리 이름. 팔 이름이 아니라 이 실행의 꼬리표다")
    args = ap.parse_args()

    bundle = json.loads(io.open(args.bundle, encoding="utf-8").read())
    print("꾸러미 %s" % args.bundle)
    print("  host %s · fetched %s · n %s · failures %s\n"
          % (bundle.get("host"), bundle.get("fetched"), bundle.get("n"),
             bundle.get("failures")))

    placed = spread(bundle, args.tag)
    print("펼침: %s\n" % " · ".join("%s %d" % kv for kv in sorted(placed.items())))

    records = ea._load_records()
    print("골든셋")
    g_ok, g_n, g_bad = score_golden(args.tag, records)
    print("  합계 %d/%d\n" % (g_ok, g_n))
    print("미공개셋")
    h_ok, h_n, h_bad = score_heldout(args.tag, records)
    print("  합계 %d/%d\n" % (h_ok, h_n))

    out = Path("scratch/audit/bundle_%s.json" % args.tag)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "생성": "scripts/score_bundle.py",
        "꾸러미": args.bundle,
        "꼬리표": args.tag,
        "🔴 팔": "꾸러미에 구성이 안 적혀 있다. 꼬리표는 팔 이름이 아니다.",
        "host": bundle.get("host"),
        "fetched": bundle.get("fetched"),
        "골든셋": {"정답": g_ok, "분모": g_n, "오답": sorted(set(g_bad))},
        "미공개셋": {"정답": h_ok, "분모": h_n, "오답": sorted(set(h_bad))},
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print("%s 에 적었다" % out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
