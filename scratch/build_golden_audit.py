r"""문제 문항의 질문·답변·골든을 한 파일로 모은다. 골든 검증은 손으로 채운다.

## 왜 이 파일이 필요한가

남은 문제가 둘로 갈린다 — **계측기가 잡는 것**(축에 나타난다)과 **안 잡는 것**(요점
미기재 · 인용 품질). 어느 쪽이든 **골든이 옳다는 전제**에 서 있다. 그 전제를 먼저 검증하지
않으면 답변을 고치는 일이 헛일이 될 수 있다 — 2026-08-21 에 골든 요점 셋이 질문 범위를
넘은 것을 이미 한 번 찾았다.

## 모으는 것

    질문 · A65 서버 3회 답변 · 골든 답변(또는 요점) · 골든 근거
    걸리는 축 · 판정기 covered · 누락 토큰

`골든_검증` 은 **비워 둔다.** 코퍼스에 대고 확인한 뒤 손으로 채운다 — 기계가 채우면
"우리 정규식이 우리 예시를 먹는다" 를 확인하는 것에 그친다.

Usage: python scratch/build_golden_audit.py
"""
import importlib.util
import io
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, ".")
sys.path.insert(0, "src")
sys.stdout.reconfigure(encoding="utf-8")

def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


#: `collect(round, arm)` 는 a65 쪽, `axis_failures` 는 a56 쪽에 있다. 정의를 옮겨 적지
#: 않는다 — 두 곳에 만들면 한쪽만 고쳐진다.
_new = _load("_hist65", "scratch/failure_histogram_a65.py")
_hist = _load("_hist56", "scratch/failure_histogram_a56.py")

ARM = "A65"
BASE = Path("scratch/ablation/v6")
COHORTS = ("full", "valuechain", "safety")
OUT = Path("scratch/audit/golden_audit_A65_2.json")
_RCEPT = re.compile(r"\d{14}")

#: 축에 나타나는 것 + 요점 미기재 + 인용 품질. 합집합이다.
#: 2026-08-21 갱신. `Q-054` 가 빠졌다 — 범위 밖 요점4 를 걷어내 3회 모두 정답이 됐고
#: 요점 미기재 목록에서도 사라졌다.
TARGETS = ("Q-010", "Q-012", "Q-047", "Q-068", "Q-080", "Q-082", "Q-094",
           "Q-101", "Q-104", "Q-106", "Q-110", "Q-112", "Q-113", "Q-114",
           "Q-116")


def load_cases() -> dict:
    out = {}
    for name in ("questions", "regression", "valuechain", "safety"):
        for line in io.open("evalset/%s.jsonl" % name, encoding="utf-8"):
            if line.strip():
                row = json.loads(line)
                out[row["question_id"]] = row
    return out


def rounds_of(qid: str):
    """(회차 -> {답변, 인용, correct, covered}) 세 회차."""
    out = {}
    for round_no, suffix in ((1, ""), (2, "_r2"), (3, "_r3")):
        for cohort in COHORTS:
            base = BASE / ("server_%s%s" % (cohort, suffix)) / ARM
            path = base / "responses" / ("%s.json" % qid)
            if not path.exists():
                continue
            doc = json.loads(path.read_text(encoding="utf-8"))["response"]
            row = next((r for r in json.loads(
                (base / "report.json").read_text(encoding="utf-8"))["rows"]
                if r["question_id"] == qid), {})
            judge = base / "judge" / ("%s.json" % qid)
            verdict = json.loads(judge.read_text(encoding="utf-8")) \
                if judge.exists() else {}
            out["r%d" % round_no] = {
                "답변": doc["answer"],
                "인용": sorted(set(_RCEPT.findall(doc["answer"]))),
                "correct": row.get("correct"),
                "covered": verdict.get("covered"),
                "covers_key_points": verdict.get("covers_key_points"),
                "numeric_failed_kinds": row.get("numeric_failed_kinds"),
                "golden_in_evidence": row.get("golden_in_evidence")}
            break
    return out


def keypoint_gaps() -> dict:
    """문항 -> [{요점, 종류, 누락 토큰}]. 계측기 산출물을 읽는다."""
    path = Path("scratch/audit/keypoint_literal_server.json")
    if not path.exists():
        return {}
    out = {}
    for row in json.loads(path.read_text(encoding="utf-8"))["행"]:
        if row.get("어긋남"):
            out.setdefault(row["question_id"], []).append(
                {"요점": row["요점"], "종류": row["종류"], "누락토큰": row["토큰"],
                 "요점문": row["point"]})
    return out


def main() -> int:
    cases = load_cases()
    gaps = keypoint_gaps()
    axes = {}
    ev = json.loads(io.open("scratch/audit/golden_evidence_required_A49.json",
                            encoding="utf-8").read())
    required = {q: {rc for rc, m in d["evidence"].items() if m["required"]}
                for q, d in ev["문항"].items()}
    for round_no in (1, 2, 3):
        rows, judges, answers = _new.collect(round_no, ARM)
        axes[round_no] = _hist.axis_failures(rows, judges, answers, cases, required)

    doc = {"생성": "scratch/build_golden_audit.py", "팔": ARM,
           "정본": "scratch/ablation/v6/server_{full,valuechain,safety}[_rN]/A65",
           "🔴 골든_검증은 손으로 채운다":
               "기계가 채우면 우리 정규식이 우리 예시를 먹는다는 것만 확인한다",
           "문항": {}}
    for qid in TARGETS:
        case = cases[qid]
        on_axes = sorted({name for rnd in axes for name, qs in axes[rnd].items()
                          if qid in qs})
        doc["문항"][qid] = {
            "질문": case["question"],
            "answer_mode": case.get("answer_mode"),
            "answer_form": case.get("answer_form"),
            "track": case.get("track"),
            "골든_답변": case.get("answer"),
            "골든_요점": case.get("answer_key_points"),
            "골든_근거": [{"rcept_no": e["rcept_no"], "section": e.get("section"),
                       "why": e.get("why")} for e in (case.get("evidence") or [])],
            "must_cite": case.get("must_cite"),
            "notes": case.get("notes"),
            "걸리는_축": on_axes,
            "요점_미기재": gaps.get(qid, []),
            "회차": rounds_of(qid),
            "문제_정의": "",
            "골든_검증": "",
        }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n",
                   encoding="utf-8")
    print("문항 %d개 · %s 에 적었다\n" % (len(TARGETS), OUT))
    print("  %-8s %-30s %s" % ("문항", "걸리는 축", "요점 미기재"))
    print("  " + "-" * 74)
    for qid in TARGETS:
        d = doc["문항"][qid]
        print("  %-8s %-30s %s"
              % (qid, ",".join(a.replace("근거 표시", "표시") for a in d["걸리는_축"])
                 or "없음", len(d["요점_미기재"])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
