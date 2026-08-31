r"""배포 구성 `A56` 의 평가 서버 3회에서 무엇이 몇 번 실패했는지 전수로 센다.

## 왜 3회로 세는가

단일 회차의 오답 목록은 뽑기와 결함이 섞여 있다. 실측(2026-08-21): 근거가 글자 그대로
같은 문항 104개 중 56개(54%)가 답변 문면이 다르고 1개는 정답 여부까지 뒤집힌다. 그래서
**3회 중 몇 회 틀렸는가**가 결함인지 뽑기인지를 가르는 첫 신호다.

  3/3   상시 결함 — 구조적이다
  2/3   결함 쪽이지만 경계에 있다
  1/3   뽑기 쪽이지만 경계에 있다

## 축 정의

`docs/compliance-audit-v5-A38-2026-08-17.md` §5 를 그대로 쓴다. 정의를 바꾸면서 세면
종전 값과 비교할 수 없다.

Usage: python scripts/failure_histogram_a56.py
"""
import collections
import io
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, ".")
sys.path.insert(0, "src")
sys.stdout.reconfigure(encoding="utf-8")

ARM = "A56"
ROUNDS = (1, 2, 3)
COHORTS = ("full", "valuechain", "safety")
EVALSETS = ("questions", "regression", "valuechain", "safety")
EXEMPT_TRACKS = ("safety", "info_limit")
BASE = Path("scratch/ablation/v6")
OUT = Path("scratch/audit/failure_histogram_A56.json")
_RCEPT = re.compile(r"\d{14}")


def run_dir(cohort: str, round_no: int) -> Path:
    name = "server_%s" % (cohort if round_no == 1 else "%s_r%d" % (cohort, round_no))
    return BASE / name / ARM


def load_cases() -> dict:
    out = {}
    for name in EVALSETS:
        for line in io.open("evalset/%s.jsonl" % name, encoding="utf-8"):
            if line.strip():
                row = json.loads(line)
                out[row["question_id"]] = row
    return out


def collect(round_no: int) -> tuple[dict, dict, dict]:
    """(문항 -> 행, 문항 -> 판정, 문항 -> 답변)"""
    rows, judges, answers = {}, {}, {}
    for cohort in COHORTS:
        base = run_dir(cohort, round_no)
        for row in json.loads((base / "report.json").read_text(encoding="utf-8"))["rows"]:
            rows[row["question_id"]] = row
        for path in (base / "judge").glob("*.json"):
            doc = json.loads(path.read_text(encoding="utf-8"))
            judges[doc["question_id"]] = doc
        for path in (base / "responses").glob("*.json"):
            doc = json.loads(path.read_text(encoding="utf-8"))
            answers[doc["question_id"]] = doc["response"]["answer"]
    return rows, judges, answers


def axis_failures(rows: dict, judges: dict, answers: dict, cases: dict,
                  required: dict) -> dict[str, set[str]]:
    """축별로 어느 문항이 실패했는가. A38 감사 §5 의 정의를 그대로 쓴다."""
    out: dict[str, set[str]] = {}
    out["정확성"] = {q for q, r in rows.items() if not r["correct"]}

    #: 근거 완전성은 문항 단위가 아니라 근거 단위로 세는 축이라, 여기서는 '필수 근거가
    #: 하나라도 안 실린 문항' 을 낸다. 비율은 별도로 적는다.
    out["근거 완전성"] = {q for q, need in required.items()
                    if need - set((rows.get(q) or {}).get("golden_in_evidence") or [])}

    bad = set()
    for q, r in rows.items():
        if r.get("covers_key_amounts_premise") and not r.get("covers_key_amounts_ok"):
            bad.add(q)
        j = judges.get(q)
        if j is not None and not j.get("covers_key_points"):
            bad.add(q)
    out["요구사항 충족"] = bad

    out["근거 기반"] = {q for q, j in judges.items() if not j.get("no_fabrication")}

    flawed = set()
    for q, r in rows.items():
        if set(r.get("numeric_failed_kinds") or []) & {"equation", "provenance"}:
            flawed.add(q)
    out["추론 논리성"] = flawed

    out["안전성·신뢰성"] = {q for q, r in rows.items()
                     if r.get("track") == "safety" and not r["correct"]}
    out["정보한계 대응"] = {q for q, r in rows.items()
                     if r.get("track") == "info_limit" and not r["correct"]}

    need = [q for q, r in rows.items()
            if r.get("track") not in EXEMPT_TRACKS
            and cases.get(q, {}).get("answerable") is not False]
    out["근거 표시(골든 일치)"] = {q for q in need if not rows[q].get("has_citation")}
    out["근거 표시(표기 유무)"] = {
        q for q in need
        if not (re.search(r"근거", answers.get(q, ""))
                and _RCEPT.search(answers.get(q, "")))}
    return out


#: 손으로 쓴 갈래. 관측(몇 회 · 어느 축)과 갈라 둔다.
KIND = {
    "Q-110": ("회수", "골든 근거 하나가 어떤 hop 후보에도 안 닿는다 (오픈이슈 2)"),
    "Q-094": ("생성 흔들림", "같은 프롬프트에서 답변 본문이 금액을 빼고 서술한다"),
    "Q-065": ("생성 흔들림", "같은 프롬프트에서 회차마다 갈린다"),
    "Q-116": ("선택", "정정 체인의 폐기된 원본을 인용한다"),
    "Q-114": ("입도", "집계로 답할 것을 개별 계약 나열로 답한다"),
    "Q-054": ("표기", "접수번호 자리에 접수일 8자리를 적는다 (회차마다 흔들린다)"),
    "Q-030": ("평가셋", "골든이 정정 체인의 최신본을 안 지목했다 — 2026-08-21 보강"),
    "Q-053": ("표기", "스텁 서술의 '찾을 수 없으며' 가 거절로 읽혀 표기가 안 붙는다"),
    "Q-115": ("자기 모순", "계약 넷을 열거하고 둘만 합산한다 (합계는 골든과 일치)"),
    "Q-113": ("자기 산술", "답변이 스스로 쓴 등식이 성립하지 않는다"),
    "Q-105": ("해결", "2026-08-21 `period_label`+`contrast_line` 으로 닫힘"),
}


def main() -> int:
    cases = load_cases()
    ev = json.loads(io.open("scratch/audit/golden_evidence_required_A49.json",
                            encoding="utf-8").read())
    required = {q: {rc for rc, m in d["evidence"].items() if m["required"]}
                for q, d in ev["문항"].items()}

    per_round, axis_per_round = {}, {}
    for round_no in ROUNDS:
        rows, judges, answers = collect(round_no)
        per_round[round_no] = rows
        axis_per_round[round_no] = axis_failures(rows, judges, answers, cases, required)

    print("배포 구성 %s · 평가 서버 3회 — 오답 히스토그램\n" % ARM)
    wrong = collections.Counter()
    for round_no in ROUNDS:
        for q, r in per_round[round_no].items():
            if not r["correct"]:
                wrong[q] += 1
    print("회차별 오답 수: %s" % " · ".join(
        "r%d %d건" % (n, sum(1 for r in per_round[n].values() if not r["correct"]))
        for n in ROUNDS))
    print()
    print("%-8s %-6s %-12s %-14s %s" % ("문항", "빈도", "트랙", "갈래", "무엇인가"))
    print("-" * 96)
    for q, n in sorted(wrong.items(), key=lambda kv: (-kv[1], kv[0])):
        row = per_round[ROUNDS[0]][q]
        kind, why = KIND.get(q, ("미분류", ""))
        print("%-8s %-6s %-12s %-14s %s" % (q, "%d/3" % n, row.get("track"), kind, why))

    print("\n\n축별 실패 — 3회 각각\n")
    names = list(axis_per_round[ROUNDS[0]])
    print("%-22s %-9s %-9s %-9s %s" % ("축", "r1", "r2", "r3", "합집합"))
    print("-" * 84)
    axis_union: dict[str, set[str]] = {}
    for name in names:
        sets = [axis_per_round[n][name] for n in ROUNDS]
        union = set().union(*sets)
        axis_union[name] = union
        print("%-22s %-9s %-9s %-9s %s" % (
            name, "%d건" % len(sets[0]), "%d건" % len(sets[1]), "%d건" % len(sets[2]),
            " ".join(sorted(union)) or "없음"))

    print("\n\n갈래별 집계 (3회 합집합)\n")
    by_kind = collections.defaultdict(set)
    for name, union in axis_union.items():
        for q in union:
            by_kind[KIND.get(q, ("미분류", ""))[0]].add(q)
    for kind, qs in sorted(by_kind.items(), key=lambda kv: -len(kv[1])):
        print("  %-14s %d문항  %s" % (kind, len(qs), " ".join(sorted(qs))))

    doc = {"생성": "scripts/failure_histogram_a56.py", "팔": ARM,
           "정본": "scratch/ablation/v6/server_{full,valuechain,safety}[_rN]/%s" % ARM,
           "축 정의": "docs/compliance-audit-v5-A38-2026-08-17.md §5",
           "회차별 오답 수": {("r%d" % n): sum(
               1 for r in per_round[n].values() if not r["correct"]) for n in ROUNDS},
           "오답 히스토그램": {q: n for q, n in sorted(wrong.items(),
                                             key=lambda kv: (-kv[1], kv[0]))},
           "축별 실패": {name: {("r%d" % n): sorted(axis_per_round[n][name])
                           for n in ROUNDS} for name in names},
           "축별 합집합": {name: sorted(union) for name, union in axis_union.items()},
           "갈래": {k: sorted(v) for k, v in by_kind.items()},
           "갈래 설명": {q: {"갈래": k, "무엇인가": w} for q, (k, w) in KIND.items()}}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n",
                   encoding="utf-8")
    print("\n%s 에 적었다" % OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
