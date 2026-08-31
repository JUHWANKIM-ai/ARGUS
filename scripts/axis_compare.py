r"""여러 팔의 **9축 실패**를 나란히 낸다. 호출 0회.

## 축 정의를 옮겨 적지 않는다

`scripts/failure_histogram_a56.py` 의 `axis_failures` 를 **가져다 쓴다**. 정의를 옮겨
적으면 한쪽만 고쳐지고, 그러면 `A56`·`A65` 값과 견줄 수 없게 된다. 그 파일이 정본이다.

## 두 가지 산출물 배치를 다 읽는다

    서버 3회      `scratch/ablation/v6/server_{코호트}[_rN]/{팔}`   — `report.json` 이 있다
    로컬 전수      `scratch/ablation/v9/full_{팔}_rN/{팔}`          — 없다, `ea.analyse` 로 만든다

🔴 뒤쪽은 `report.json` 을 안 남긴다(`scripts/full_run.py` 가 메모리에서 채점하고 버린다).
그래서 여기서 다시 계산한다 — 같은 `ea.analyse` 이므로 값이 갈리지 않는다.

Usage:
    python scripts/axis_compare.py A65:server A81:local
    python scripts/axis_compare.py A65:server A77:local A81:local
"""
import importlib.util
import io
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, ".")
sys.path.insert(0, "src")
sys.stdout.reconfigure(encoding="utf-8")

from scripts import error_analysis as ea                          # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "_hist", "scripts/failure_histogram_a56.py")
_hist = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_hist)

import unicodedata

#: 회차. 종전에는 `(1, 2, 3)` 으로 박혀 있어 4회차를 재고도 조용히 빠졌다.
#: `ARGUS_ROUNDS` 로 늘린다 — `scripts/score_heldout_rounds.py` 와 같은 결함이었다.
ROUNDS = tuple(range(1, int(os.environ.get("ARGUS_ROUNDS", "3")) + 1))


def _pad(text: str, width: int) -> str:
    """한글은 두 칸을 차지한다. `%-*s` 는 글자를 세므로 열이 붙는다."""
    shown = sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in text)
    return text + " " * max(0, width - shown)
AXES = ("정확성", "근거 완전성", "요구사항 충족", "근거 기반", "추론 논리성",
        "안전성·신뢰성", "정보한계 대응", "근거 표시(골든 일치)", "근거 표시(표기 유무)")
OUT = Path("scratch/audit/axis_compare.json")


def collect_server(arm: str, round_no: int):
    rows, judges, answers = {}, {}, {}
    for cohort in _hist.COHORTS:
        name = "server_%s" % (cohort if round_no == 1
                              else "%s_r%d" % (cohort, round_no))
        base = Path("scratch/ablation/v6") / name / arm
        if not base.exists():
            return None
        for row in json.loads((base / "report.json").read_text(encoding="utf-8"))["rows"]:
            rows[row["question_id"]] = row
        for path in (base / "judge").glob("*.json"):
            doc = json.loads(path.read_text(encoding="utf-8"))
            judges[doc["question_id"]] = doc
        for path in (base / "responses").glob("*.json"):
            doc = json.loads(path.read_text(encoding="utf-8"))
            answers[doc["question_id"]] = doc["response"]["answer"]
    return rows, judges, answers


def collect_local(arm: str, round_no: int, cases_all: list, records: dict):
    base = Path("scratch/ablation/v9") / ("full_%s_r%d" % (arm, round_no)) / arm
    if not (base / "responses").exists():
        return None
    ea.RESPONSE_DIR, ea.JUDGE_DIR, ea.OUT_DIR = (base / "responses",
                                                 base / "judge", base)
    rows = {r["question_id"]: r for r in ea.analyse(cases_all, records)}
    judges, answers = {}, {}
    for path in (base / "judge").glob("*.json"):
        doc = json.loads(path.read_text(encoding="utf-8"))
        judges[doc["question_id"]] = doc
    for path in (base / "responses").glob("*.json"):
        doc = json.loads(path.read_text(encoding="utf-8"))
        answers[doc["question_id"]] = doc["response"]["answer"]
    return rows, judges, answers


def main() -> int:
    targets = []
    for spec in sys.argv[1:] or ["A65:server", "A81:local"]:
        arm, _, where = spec.partition(":")
        targets.append((arm, where or "server"))

    cases = _hist.load_cases()
    ev = json.loads(io.open("scratch/audit/golden_evidence_required_A49.json",
                            encoding="utf-8").read())
    required = {q: {rc for rc, m in d["evidence"].items() if m["required"]}
                for q, d in ev["문항"].items()}

    ea._use_v2_dirs()
    seen, cases_all = set(), []
    for cohort in _hist.COHORTS:
        for case in ea.load_cohort(cohort):
            if case["question_id"] not in seen:
                seen.add(case["question_id"])
                cases_all.append(case)
    records = ea._load_records()

    table, scores, detail = {}, {}, {}
    for arm, where in targets:
        label = "%s %s" % (arm, "서버" if where == "server" else "로컬")
        per_round, ok = {}, []
        for round_no in ROUNDS:
            got = (collect_server(arm, round_no) if where == "server"
                   else collect_local(arm, round_no, cases_all, records))
            if got is None:
                continue
            rows, judges, answers = got
            per_round[round_no] = _hist.axis_failures(rows, judges, answers,
                                                      cases, required)
            ok.append("%d/%d" % (sum(1 for r in rows.values() if r["correct"]),
                                 len(rows)))
        if not per_round:
            print("🔴 %s 산출물이 없습니다 — 건너뜁니다\n" % label)
            continue
        table[label] = per_round
        scores[label] = ok
        detail[label] = {a: sorted(set().union(*(per_round[n].get(a, set())
                                                 for n in per_round)))
                         for a in AXES}

    if not table:
        return 1
    width = max(sum(2 if unicodedata.east_asian_width(c) in "WF" else 1
                    for c in k) for k in table) + 3
    print("정답 — 회차마다\n")
    for label, ok in scores.items():
        print("  %s %s" % (_pad(label, width), " · ".join(ok)))

    print("\n\n9축 실패 문항 수 — 회차마다\n")
    print("  %s %s" % (_pad("축", 24), "".join(_pad(k, width) for k in table)))
    print("  " + "-" * (25 + width * len(table)))
    for axis in AXES:
        cells = []
        for label, per_round in table.items():
            cells.append(_pad(" · ".join(
                str(len(per_round[n].get(axis, set()))) for n in sorted(per_round)),
                width))
        print("  %s %s" % (_pad(axis, 24), "".join(cells)))

    print("\n\n합집합 — 한 번이라도 실패한 문항\n")
    for axis in AXES:
        line = []
        for label in table:
            got = detail[label][axis]
            line.append("%s: %s" % (label, " ".join(got) or "없음"))
        if any(detail[label][axis] for label in table):
            print("  %s %s" % (_pad(axis, 24), " | ".join(line)))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(
        {"생성": "scripts/axis_compare.py",
         "축 정의": "scripts/failure_histogram_a56.py::axis_failures 를 그대로 씀",
         "정답": scores,
         "회차별": {label: {str(n): {a: sorted(per_round[n].get(a, set()))
                                  for a in AXES}
                        for n in sorted(per_round)}
                 for label, per_round in table.items()},
         "합집합": detail}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print("\n%s 에 적었다" % OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
