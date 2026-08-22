r"""골든셋 112문항을 한 파일로 모은다.

## 왜 필요한가

문항이 **네 파일에 흩어져 있다** — `questions.jsonl` 78 · `regression.jsonl` 10 ·
`valuechain.jsonl` 12 · `safety.jsonl` 12. 어느 문항이 어느 파일에 있는지 외우고 다녀야
하고, 한 문항을 보려면 `grep` 으로 네 번 뒤져야 한다. 이 스크립트는 그 넷을 합쳐 읽는
용도의 한 파일로 만든다.

## 🔴 채점에 쓰지 않는다

산출물은 **읽고 고르는 용도**다. 채점은 `scripts/error_analysis` 가 `evalset/` 을 직접
읽어서 한다. 골든을 두 곳에 두면 한쪽만 고쳐지는 것이 이 저장소가 되풀이해 겪은 실패
양식이라, 이 파일을 근거로 채점하는 코드를 새로 만들면 안 된다.

## 앞선 도구와 무엇이 다른가

`scratch/build_golden_audit.py` 는 **문제 문항만** 골라 질문 · `A65` 3회 답변 · 골든 ·
걸리는 축 · 누락 토큰을 한 파일에 모으고, 그 위에서 `fill_golden_audit.py` 가 골든 검증을
손으로 채운다. 즉 **틀린 자리를 파는 도구**다. 이 스크립트는 반대로 **112문항 전부를
가리지 않고** 싣는다 — 고를 판을 먼저 펼쳐 놓는 용도다. 둘은 겹치지 않는다.

## 문항 본문은 손대지 않는다

`evalset/` 레코드를 **키 이름까지 그대로** 싣는다. 이름을 우리말로 바꾸면 `evalset` 과
`diff` 가 안 되고, 필드가 하나 늘었을 때 조용히 빠진다. 덧붙이는 것은 `cohort` 한 칸뿐이다.

Usage:
    python scratch/dump_golden_questions.py
"""
import collections
import json
import sys
from pathlib import Path

sys.path.insert(0, ".")
sys.path.insert(0, "src")
sys.stdout.reconfigure(encoding="utf-8")

from scripts import error_analysis as ea                          # noqa: E402

OUT = Path("scratch/audit/golden_questions.json")

#: 🔴 순서가 곧 문항 번호 순서가 아니다. `load_cohort("full")` 이 dev 78 + 회귀 10 을
#: 그 차례로 주고, 밸류체인·안전성이 뒤에 온다. 그 차례를 유지한다 — 채점기가 보는
#: 차례와 같아야 나중에 대조하기 쉽다.
COHORTS = ("full", "valuechain", "safety")

#: 어느 파일에서 왔는지. `full` 은 두 파일이 섞이므로 `track` 으로 다시 가른다.
SOURCE = {
    "regression": "evalset/regression.jsonl",
    "valuechain": "evalset/valuechain.jsonl",
    "safety": "evalset/safety.jsonl",
}


def source_of(case: dict, cohort: str) -> str:
    if cohort == "valuechain":
        return SOURCE["valuechain"]
    if cohort == "safety":
        return SOURCE["safety"]
    #: 회귀 문항은 `regression_of` 가 채워져 있고 track 도 regression 이다.
    if case.get("track") == "regression":
        return SOURCE["regression"]
    return "evalset/questions.jsonl"


def main() -> int:
    cases, seen = [], set()
    for cohort in COHORTS:
        for case in ea.load_cohort(cohort):
            qid = case["question_id"]
            if qid in seen:
                #: 코호트가 겹치면 먼저 온 쪽을 남긴다. 지금은 안 겹치지만 겹치는 날
                #: 조용히 두 번 세이지 않게 막아 둔다.
                continue
            seen.add(qid)
            row = dict(case)
            row["cohort"] = cohort
            row["source_file"] = source_of(case, cohort)
            cases.append(row)

    by = lambda key: dict(collections.Counter(  # noqa: E731
        c.get(key) if c.get(key) is not None else "(없음)" for c in cases).most_common())

    out = {
        "생성": "scratch/dump_golden_questions.py",
        "성격": "🔴 읽는 용도다. 채점은 `scripts/error_analysis` 가 evalset/ 을 직접 읽는다",
        "문항수": len(cases),
        "출처": sorted({c["source_file"] for c in cases}),
        "코호트별": by("cohort"),
        "트랙별": by("track"),
        "유형별": by("task_type"),
        "난이도별": by("difficulty"),
        "개방여부별": by("answer_mode"),
        "답형식별": by("answer_form"),
        "거절이_정답인_문항": sorted(c["question_id"] for c in cases
                             if not c.get("answerable")),
        "문항": cases,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

    print("%s 에 %d문항" % (OUT, len(cases)))
    for key in ("코호트별", "트랙별", "유형별", "난이도별"):
        print("  %-8s %s" % (key, out[key]))
    print("  거절이 정답  %d문항" % len(out["거절이_정답인_문항"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
