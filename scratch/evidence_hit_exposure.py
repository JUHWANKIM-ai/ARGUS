r"""`evidence_hit` 이 「본문 없이 적재로 세는」 자리를 전수로 찾는다. LLM 호출 0회.

## 무엇이 문제인가

`error_analysis._blocks` 는 근거를 `^## ` 로 쪼갠다. 그래서 두 가지가 블록으로 세어진다.

  1. 첫 머리글 **위**의 영역 — 결정적 줄들(`[결정적 집계]` · `[결정적 조회]` · `[결정적 단위]`).
     이것은 문서가 아니라 **우리가 계산한 요약**이다.
  2. 블록 **안**의 포인터 줄 — 다른 문서를 가리키는 줄(`- 정정 대상 원공시: 접수번호 …`).

`evidence_hit` 은 블록 안에 접수번호가 있으면 그 문서가 실렸다고 센다. 위 두 자리의
14자리는 **그 문서의 본문이 하나도 안 실렸어도** 적재로 세어진다.

## 이 스크립트가 재는 것

저장된 산출물마다 세 값을 낸다.

    지금        `_blocks` 그대로
    머리 제외    첫 머리글 위 영역을 블록에서 뺀다
    둘 다 제외   그 위에 포인터 줄까지 뺀다

셋이 같으면 계측기를 고쳐도 **과거 측정이 안 움직인다** — 그때만 안전하게 고칠 수 있다.

Usage: python scratch/evidence_hit_exposure.py
"""
import io
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, ".")
sys.path.insert(0, "src")
sys.stdout.reconfigure(encoding="utf-8")

from scripts import error_analysis as ea                          # noqa: E402

OUT = Path("scratch/audit/evidence_hit_exposure.json")
#: 다른 문서를 가리키는 줄. 이 블록이 담은 값이 아니다.
_POINTER = re.compile(r"(?m)^\s*-\s*정정 대상 원공시\s*:.*$")
#: 결정적 줄. `^## ` 앞 영역에 놓이므로 `_blocks` 가 블록으로 센다.
_DECISIVE = re.compile(r"\[결정적 [^\]]+\]")

RUNS = [("A49", "scratch/ablation/v6/server_%s/A49"),
        ("A56", "scratch/ablation/v6/server_%s/A56"),
        ("A65", "scratch/ablation/v6/server_%s/A65")]
COHORTS = ("full", "valuechain", "safety")


def strip_head(text: str) -> str:
    """첫 `## ` 머리글 앞을 버린다. 결정적 줄은 문서가 아니다."""
    index = text.find("\n## ")
    if text.startswith("## "):
        return text
    return text[index + 1:] if index >= 0 else ""


def main() -> int:
    cases = {}
    for name in ("questions", "regression", "valuechain", "safety"):
        for line in io.open("evalset/%s.jsonl" % name, encoding="utf-8"):
            if line.strip():
                row = json.loads(line)
                cases[row["question_id"]] = row
    ev = json.loads(io.open("scratch/audit/golden_evidence_required_A49.json",
                            encoding="utf-8").read())
    required = {q: {rc for rc, m in d["evidence"].items() if m["required"]}
                for q, d in ev["문항"].items()}

    print("`evidence_hit` 노출 — 저장 산출물 전수\n")
    print("  %-6s %-6s %-12s %-12s %-12s %s"
          % ("팔", "회차", "지금", "머리 제외", "둘 다 제외", "차이"))
    print("  " + "-" * 74)
    doc, flagged = {}, []
    for arm, template in RUNS:
        for round_no, suffix in ((1, ""), (2, "_r2"), (3, "_r3")):
            got = [0, 0, 0]
            total = 0
            missing = False
            for qid, need in required.items():
                ctx = None
                for cohort in COHORTS:
                    path = (Path(template % (cohort + suffix)) / "responses"
                            / ("%s.json" % qid))
                    if path.exists():
                        ctx = json.loads(path.read_text(encoding="utf-8"))[
                            "response"]["retrieved_context"]
                        break
                if ctx is None:
                    missing = True
                    continue
                total += len(need)
                variants = (ctx, strip_head(ctx), _POINTER.sub("", strip_head(ctx)))
                for index, variant in enumerate(variants):
                    got[index] += len(need & set(
                        ea.evidence_hit(cases[qid], variant)["present"]))
            if missing and total == 0:
                continue
            gap = got[0] - got[2]
            if gap:
                flagged.append((arm, round_no, gap))
            print("  %-6s r%-5d %-12s %-12s %-12s %s"
                  % (arm, round_no, "%d/%d" % (got[0], total),
                     "%d/%d" % (got[1], total), "%d/%d" % (got[2], total),
                     ("🔴 %+d" % -gap) if gap else "✅ 0"))
            doc["%s_r%d" % (arm, round_no)] = {"지금": got[0], "머리제외": got[1],
                                               "둘다제외": got[2], "분모": total}

    print()
    if flagged:
        print("🔴 과거 측정이 움직인다: %s" % flagged)
        print("   계측기를 고치면 그만큼 근거 완전성이 내려간다. 그 사실을 문서에 적어야 한다.")
    else:
        print("✅ 세 값이 전부 같다 — 계측기를 고쳐도 **과거 측정이 한 건도 안 움직인다.**")
        print("   지금 부풀림이 0 이라는 뜻이고, 고치는 것은 앞으로를 막는 일이다.")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"생성": "scratch/evidence_hit_exposure.py",
                               "회차별": doc, "움직이는 것": flagged},
                              ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print("\n%s 에 적었다" % OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
