r"""골든 요점이 **질문 범위를 넘어서** 오답을 만든 자리를 찾는다. LLM 호출 0회.

## 지금까지 본 것과 다른 질문

앞서 검증한 것은 「골든이 요구한 값이 원문에 있나」였고 답은 대체로 "있다" 였다.
여기서 묻는 것은 다르다 — **「질문이 그것을 물었나」**, 그리고 **「안 물은 것 때문에 답이
오답으로 읽혔나」**다.

## 왜 이것이 실제로 일어나는가

`covers_key_points` 는 **과반 초과**다(`sum(covered)*2 > len(covered)`). 요점이 적은 문항은
한둘만 미달해도 뒤집힌다.

    요점 4개 · covered 2개  ->  4 > 4 가 거짓  ->  오답
    요점 3개 · covered 2개  ->  4 > 3 이 참    ->  정답

그래서 **질문이 안 물은 요점 하나가 분모에 있는 것만으로 답이 오답이 될 수 있다.**

## 무엇을 의심하는가

요점 칸에 들어간 **인용 요구**다. `근거는 …` · `근거 접수번호 …` 같은 요점은 답의 내용이
아니라 표기 요구이고, 질문이 근거를 요구하지 않았으면 범위 밖이다. 2026-08-21 에 걷어낸
셋(`Q-092`·`Q-104` 요점4·5)과 같은 갈래다.

## 무엇을 내는가

요점별로 (범위 밖 의심 여부 · 판정기 covered · 그것을 뺐을 때 판정이 뒤집히는가)를 낸다.
**뒤집히는 것이 곧 「골든이 투머치라서 오답으로 읽힌」 자리다.**

Usage: python scratch/golden_overspec.py
"""
import io
import json
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

BASE = Path("scratch/ablation/v6")
COHORTS = ("full", "valuechain", "safety")
ARM = "A65"
OUT = Path("scratch/audit/golden_overspec.json")

#: 요점 칸에 들어간 **인용 요구**. 답의 내용이 아니라 표기 요구다.
_CITE_POINT = re.compile(r"^\s*근거(는|\s*접수번호)|근거는\s|^\s*근거\s")
#: 질문이 근거·출처를 요구하는가. 요구했다면 인용 요점이 범위 안이다.
_ASKS_SOURCE = re.compile(r"근거|출처|접수번호|어느\s*공시|무슨\s*공시")


def load_cases() -> dict:
    out = {}
    for name in ("questions", "regression", "valuechain", "safety"):
        for line in io.open("evalset/%s.jsonl" % name, encoding="utf-8"):
            if line.strip():
                row = json.loads(line)
                out[row["question_id"]] = row
    return out


def verdicts(qid: str) -> dict:
    """회차 -> (covered, covers_key_points, correct)."""
    out = {}
    for round_no, suffix in ((1, ""), (2, "_r2"), (3, "_r3")):
        for cohort in COHORTS:
            base = BASE / ("server_%s%s" % (cohort, suffix)) / ARM
            judge = base / "judge" / ("%s.json" % qid)
            if not judge.exists():
                continue
            doc = json.loads(judge.read_text(encoding="utf-8"))
            row = next((r for r in json.loads(
                (base / "report.json").read_text(encoding="utf-8"))["rows"]
                if r["question_id"] == qid), {})
            out[round_no] = (list(doc.get("covered") or []),
                             doc.get("covers_key_points"), row.get("correct"))
            break
    return out


def majority(covered: list) -> bool:
    return sum(bool(c) for c in covered) * 2 > len(covered)


def main() -> int:
    cases = load_cases()
    rows, flipped = [], []
    for qid, case in sorted(cases.items()):
        if case.get("answer_mode") != "open":
            continue
        points = case.get("answer_key_points") or []
        if not points:
            continue
        asks_source = bool(_ASKS_SOURCE.search(case["question"]))
        suspect = [i for i, p in enumerate(points) if _CITE_POINT.search(p)]
        if not suspect:
            continue
        seen = verdicts(qid)
        for round_no, (covered, ok, correct) in seen.items():
            if len(covered) != len(points):
                continue
            kept = [c for i, c in enumerate(covered) if i not in set(suspect)]
            after = majority(kept)
            row = {"문항": qid, "회차": round_no, "요점수": len(points),
                   "의심요점": [i + 1 for i in suspect],
                   "질문이_근거를_요구하나": asks_source,
                   "covered": covered, "지금": ok, "뺀_뒤": after,
                   "correct": correct,
                   "뒤집힘": bool(ok is False and after is True and not asks_source)}
            rows.append(row)
            if row["뒤집힘"]:
                flipped.append(row)

    print("요점 칸에 인용 요구가 든 문항 — 그것을 빼면 판정이 뒤집히는가\n")
    print("  %-8s %-4s %-5s %-22s %-8s %-8s %s"
          % ("문항", "회차", "요점", "covered", "지금", "뺀 뒤", "질문이 근거 요구"))
    print("  " + "-" * 88)
    for row in rows:
        mark = "  🔴 뒤집힘" if row["뒤집힘"] else ""
        print("  %-8s r%-3d %-5d %-22s %-8s %-8s %s%s"
              % (row["문항"], row["회차"], row["요점수"],
                 "".join("O" if c else "X" for c in row["covered"]),
                 row["지금"], row["뺀_뒤"],
                 "예" if row["질문이_근거를_요구하나"] else "아니오", mark))

    print("\n\n🔴 골든이 투머치라서 오답으로 읽힌 자리: %d건" % len(flipped))
    for row in flipped:
        case = cases[row["문항"]]
        print("\n  %s r%d — 요점 %d개 중 %d번이 인용 요구다"
              % (row["문항"], row["회차"], row["요점수"], row["의심요점"][0]))
        print("     질문: %s" % case["question"][:100])
        print("     의심 요점: %s"
              % case["answer_key_points"][row["의심요점"][0] - 1][:100])
        print("     빼면 %s -> %s" % (row["지금"], row["뺀_뒤"]))
    if not flipped:
        print("  ✅ 없다 — 인용 요구가 분모에 있어도 판정을 뒤집지는 않는다")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"생성": "scratch/golden_overspec.py",
                               "행": rows, "뒤집힘": flipped},
                              ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print("\n%s 에 적었다" % OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
