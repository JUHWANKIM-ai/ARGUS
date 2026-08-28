r"""미공개셋 3회를 **아홉 축**으로 센다. 골든셋의 `scratch/axis_compare.py` 의 짝이다.

    PYTHONIOENCODING=utf-8 python scratch/axis_heldout.py --arm A91h

## 이 실행이 무엇을 사지 않는가

**서버를 새로 부르지 않는다.** 이미 사 둔 응답(`server_<코호트>[_rN]/<팔>/responses`)과
판정을 다시 읽을 뿐이다. 그러므로 미공개셋 독립성을 더 쓰지 않는다.

## 근거 완전성 축의 분모가 골든셋과 다르다

골든셋은 `golden_evidence_required_A49.json` 이 근거마다 **필수/선택**을 표시해 두어
필수분만 센다(168칸). 미공개 54문항에는 그 주석이 없다. 그래서 여기서는 문항에 적힌
**골든 근거 전량**을 필수로 놓고 센다 — 골든셋보다 **엄한** 잣대다. 두 수를 나란히
놓고 비교하면 안 된다.

축 정의는 `scratch/failure_histogram_a56.py::axis_failures` 를 그대로 부른다.
채점은 `scratch/score_heldout.py::score` 를 그대로 부른다. 어느 쪽도 옮겨 적지 않는다.
"""
import argparse
import importlib.util
import json
import sys
from pathlib import Path

sys.path.insert(0, ".")
sys.path.insert(0, "src")
sys.path.insert(0, "scratch")
sys.stdout.reconfigure(encoding="utf-8")

from scripts import error_analysis as ea                          # noqa: E402
import failure_histogram_a56 as hist                              # noqa: E402

AXES = ("정확성", "근거 완전성", "요구사항 충족", "근거 기반", "추론 논리성",
        "안전성·신뢰성", "정보한계 대응", "근거 표시(골든 일치)", "근거 표시(표기 유무)")

_spec = importlib.util.spec_from_file_location("_score_heldout",
                                               "scratch/score_heldout.py")
_sh = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_sh)

SUFFIX = ("", "_r2", "_r3")
BASE = Path("scratch/ablation/v6")
#: 팔 이름을 파일에 넣는다. 한 파일에 쓰면 다음 팔이 앞 팔의 기록을 덮는다
#: (2026-08-28 에 `A91h` 를 실제로 덮었다).
OUT = Path("scratch/audit/axis_heldout.json")   # main() 에서 팔 이름을 붙인다


def collect(arm: str, suffix: str, records: dict):
    """(문항 -> 행, 문항 -> 판정, 문항 -> 답변, 문항 -> 사례)"""
    rows, judges, answers, cases_all = {}, {}, {}, {}
    for cohort, path, _, _ in _sh.SUBSETS:
        name = cohort + suffix
        arm_dir = BASE / ("server_%s" % name) / arm
        if not (arm_dir / "responses").exists():
            return None
        _, got = _sh.score(name, path, records, arm)
        for row in got:
            rows[row["question_id"]] = row
        for case in _sh.load(path):
            cases_all[case["question_id"]] = case
        for p in (arm_dir / "judge").glob("*.json"):
            doc = json.loads(p.read_text(encoding="utf-8"))
            judges[doc["question_id"]] = doc
        for p in (arm_dir / "responses").glob("*.json"):
            doc = json.loads(p.read_text(encoding="utf-8"))
            answers[doc["question_id"]] = doc["response"]["answer"]
    return rows, judges, answers, cases_all


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", default="A91h")
    ap.add_argument("--rounds", type=int, default=3)
    args = ap.parse_args()

    records = ea._load_records()
    per_round, union = [], {ax: set() for ax in AXES}
    evidence_ratio = []

    for index, suffix in enumerate(SUFFIX[:args.rounds], start=1):
        got = collect(args.arm, suffix, records)
        if got is None:
            print("r%d 산출물 없음 — 건너뜀" % index)
            continue
        rows, judges, answers, cases_all = got
        #: 미공개셋에는 필수/선택 주석이 없다. 전량을 필수로 놓는다.
        required = {q: {e["rcept_no"] for e in (c.get("evidence") or [])
                        if e.get("rcept_no")}
                    for q, c in cases_all.items()}
        required = {q: s for q, s in required.items() if s}

        fails = hist.axis_failures(rows, judges, answers, cases_all, required)
        for ax in AXES:
            union[ax] |= fails.get(ax, set())

        hit = sum(len(set(r.get("golden_in_evidence") or [])) for r in rows.values())
        need = sum(len(required.get(q, ())) for q in rows)
        evidence_ratio.append("%d/%d" % (hit, need))
        ok = sum(1 for r in rows.values() if r["correct"])
        per_round.append({
            "r": index,
            "정답": "%d/%d" % (ok, len(rows)),
            "근거 전량 실림": "%d/%d" % (hit, need),
            "축": {ax: sorted(fails.get(ax, set())) for ax in AXES},
        })

    label = max((len(a) for a in AXES), default=0)
    print()
    print("미공개셋 아홉 축 — 팔 %s" % args.arm)
    print("%-*s  %s" % (label, "축", "  ".join("r%d" % p["r"] for p in per_round)))
    print("-" * (label + 4 * len(per_round) + 2))
    for ax in AXES:
        cells = []
        for p in per_round:
            n = len(p["축"][ax])
            cells.append("%3d" % n if n else "  ·")
        print("%-*s %s" % (label, ax, " ".join(cells)))
    print("-" * (label + 4 * len(per_round) + 2))
    print("%-*s %s" % (label, "정답", " ".join("%3s" % p["정답"].split("/")[0]
                                             for p in per_round)))
    print()
    for ax in AXES:
        if union[ax]:
            per = ["%s(%d회)" % (q, sum(1 for p in per_round if q in p["축"][ax]))
                   for q in sorted(union[ax])]
            print("  %s  %s" % (ax, " ".join(per)))
    print()
    print("  근거 전량 실림 (필수 주석 없어 전량 기준) ", " ".join(evidence_ratio))

    out_path = OUT.with_name("axis_heldout_%s.json" % args.arm)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({
        "생성": "scratch/axis_heldout.py",
        "팔": args.arm,
        "축 정의": "scratch/failure_histogram_a56.py::axis_failures 를 그대로 씀",
        "채점": "scratch/score_heldout.py::score 를 그대로 씀",
        "🔴 근거 완전성 분모": "미공개셋에는 필수/선택 주석이 없어 골든 근거 전량을 "
                        "필수로 놓았다. 골든셋의 168칸과 나란히 놓으면 안 된다.",
        "회차별": per_round,
        "합집합": {ax: sorted(union[ax]) for ax in AXES},
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n%s 에 적었다" % OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
