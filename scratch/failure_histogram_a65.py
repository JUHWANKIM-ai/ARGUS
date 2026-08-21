r"""배포 구성 `A65` 의 평가 서버 3회를 전수로 센다. `A56` 과 나란히 놓는다.

## 왜 3회로 세는가

단일 회차의 오답 목록은 결함과 뽑기가 섞여 있다. 실측(2026-08-21): 근거가 글자 그대로
같은 문항 104개 중 56개(54%)가 답변 문면이 다르고 1개는 정답 여부까지 뒤집힌다.

  3/3   상시 결함 — 구조적이다
  2/3   결함 쪽이지만 경계에 있다
  1/3   뽑기 쪽이지만 경계에 있다

## 축 정의를 옮겨 적지 않는다

`scratch/failure_histogram_a56.py` 의 `axis_failures` 와 `collect` 를 **가져다 쓴다**.
정의를 옮겨 적으면 한쪽만 고쳐지고 그러면 `A56` 값과 비교할 수 없다.

Usage: python scratch/failure_histogram_a65.py
"""
import collections
import importlib.util
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, ".")
sys.path.insert(0, "src")
sys.stdout.reconfigure(encoding="utf-8")

_spec = importlib.util.spec_from_file_location(
    "_hist", "scratch/failure_histogram_a56.py")
_hist = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_hist)

OUT = Path("scratch/audit/failure_histogram_A65.json")


def collect(round_no: int, arm: str):
    """`_hist.collect` 와 같은 규칙, 팔만 바꾼다."""
    rows, judges, answers = {}, {}, {}
    for cohort in _hist.COHORTS:
        name = "server_%s" % (cohort if round_no == 1
                              else "%s_r%d" % (cohort, round_no))
        base = _hist.BASE / name / arm
        for row in json.loads((base / "report.json").read_text(encoding="utf-8"))["rows"]:
            rows[row["question_id"]] = row
        for path in (base / "judge").glob("*.json"):
            doc = json.loads(path.read_text(encoding="utf-8"))
            judges[doc["question_id"]] = doc
        for path in (base / "responses").glob("*.json"):
            doc = json.loads(path.read_text(encoding="utf-8"))
            answers[doc["question_id"]] = doc["response"]["answer"]
    return rows, judges, answers


#: 손으로 쓴 갈래. 관측(몇 회 · 어느 축)과 갈라 둔다.
KIND = {
    "Q-054": ("해결", "범위 밖 요점4 를 걷어내 3회 모두 정답 — 요점3 미달은 남는다"),
    "Q-010": ("생성 흔들림", "r2 만 골든 아닌 공시 하나를 인용한다"),
    "Q-047": ("생성 흔들림", "같은 프롬프트에서 회차마다 갈린다"),
    "Q-065": ("생성 흔들림", "같은 프롬프트에서 회차마다 갈린다"),
    "Q-094": ("생성 흔들림", "같은 프롬프트에서 금액을 빼고 서술한다"),
    "Q-110": ("회수", "골든 근거 하나가 어떤 hop 후보에도 안 닿는다 — 근거 완전성만 막는다"),
    "Q-113": ("자기 산술", "답변이 스스로 쓴 등식이 성립하지 않는다"),
    "Q-116": ("선택", "정정 체인의 폐기된 원본을 인용한다"),
    "Q-053": ("해결", "`refusal_needs_gap` 으로 닫힘"),
    "Q-115": ("해결", "`aggregate_items`+`sum_operand_fix` 로 닫힘"),
}


def main() -> int:
    cases = _hist.load_cases()
    ev = json.loads(io.open("scratch/audit/golden_evidence_required_A49.json",
                            encoding="utf-8").read())
    required = {q: {rc for rc, m in d["evidence"].items() if m["required"]}
                for q, d in ev["문항"].items()}

    per, axes = {}, {}
    old_axes = {}
    for rnd in _hist.ROUNDS:
        rows, judges, answers = collect(rnd, "A65")
        per[rnd] = rows
        axes[rnd] = _hist.axis_failures(rows, judges, answers, cases, required)
        o = _hist.collect(rnd)
        old_axes[rnd] = _hist.axis_failures(*o, cases, required)

    print("배포 구성 A65 · 평가 서버 3회\n")
    print("  회차별 정답 %s" % " · ".join(
        "%d/112" % sum(1 for r in per[n].values() if r["correct"]) for n in _hist.ROUNDS))
    print("  A56 (종전)  111 · 109 · 110\n")

    wrong = collections.Counter()
    for rnd in _hist.ROUNDS:
        for q, r in per[rnd].items():
            if not r["correct"]:
                wrong[q] += 1
    print("오답 히스토그램\n")
    print("  %-8s %-6s %-12s %-14s %s" % ("문항", "빈도", "트랙", "갈래", "무엇인가"))
    print("  " + "-" * 92)
    for q, n in sorted(wrong.items(), key=lambda kv: (-kv[1], kv[0])):
        kind, why = KIND.get(q, ("미분류", ""))
        print("  %-8s %-6s %-12s %-14s %s"
              % (q, "%d/3" % n, per[_hist.ROUNDS[0]][q].get("track"), kind, why))
    if not wrong:
        print("  ✅ 오답이 없다")

    print("\n\n8축 — A56 vs A65 (실패 문항 수)\n")
    names = list(axes[1])
    print("  %-22s %-16s %-16s %s" % ("축", "A56", "A65", "A65 합집합"))
    print("  " + "-" * 88)
    doc_axes = {}
    for name in names:
        a = [len(old_axes[n][name]) for n in _hist.ROUNDS]
        b = [len(axes[n][name]) for n in _hist.ROUNDS]
        union = sorted(set().union(*[axes[n][name] for n in _hist.ROUNDS]))
        mark = "" if a == b else ("  ✅" if sum(b) < sum(a) else "  🔴")
        print("  %-22s %-16s %-16s %s%s"
              % (name, " · ".join(map(str, a)), " · ".join(map(str, b)),
                 " ".join(union) or "없음", mark))
        doc_axes[name] = {"A56": a, "A65": b, "합집합": union}

    by_kind = collections.defaultdict(set)
    for name, v in doc_axes.items():
        for q in v["합집합"]:
            by_kind[KIND.get(q, ("미분류", ""))[0]].add(q)
    print("\n\n갈래별 집계 (3회 합집합)\n")
    for kind, qs in sorted(by_kind.items(), key=lambda kv: -len(kv[1])):
        print("  %-14s %d문항  %s" % (kind, len(qs), " ".join(sorted(qs))))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(
        {"생성": "scratch/failure_histogram_a65.py", "팔": "A65",
         "정본": "scratch/ablation/v6/server_{full,valuechain,safety}[_rN]/A65",
         "축 정의": "docs/compliance-audit-v5-A38-2026-08-17.md §5",
         "회차별 정답": {("r%d" % n): sum(1 for r in per[n].values() if r["correct"])
                    for n in _hist.ROUNDS},
         "오답 히스토그램": dict(sorted(wrong.items(), key=lambda kv: (-kv[1], kv[0]))),
         "축": doc_axes,
         "갈래": {k: sorted(v) for k, v in by_kind.items()},
         "갈래 설명": {q: {"갈래": k, "무엇인가": w} for q, (k, w) in KIND.items()}},
        ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print("\n%s 에 적었다" % OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
