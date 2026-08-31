r"""처음 보는 문항 54개를 **골든셋과 같은 잣대로** 채점한다.

왜 새로 재는가. README 가 실전 기대값으로 인용하는 49/54 는 세 가지가 지금과 다르다.

  ① 코드판   newset·heldout_v2 는 08-16 판, heldout_v3 는 08-17 판. 배포본은 A49 다.
  ② 판정기   v8 에서 부분 정답을 없앴다. 골든셋에서 이 변경만으로 3점이 깎였다.
  ③ 🔴 잣대  `scripts/run_heldout_v3.py` 는 **judge 를 아예 안 불렀다.** 요점은
             "숫자 하나라도 겹치거나 첫 낱말이 있으면 통과", 거절은 문구 포함만 보고
             유출 검사도 없다. 골든셋의 요점별 과반 초과 + 금액 커버리지 + 수치
             자기일관성과는 비교가 안 되게 느슨하다.

셋 중 ③이 가장 크다. 그래서 이 스크립트는 채점을 다시 만들지 않고 **골든셋이 쓰는
함수를 그대로 호출한다** (`error_analysis.analyse`). 같은 검사를 두 곳에 따로 두면 한쪽만
고쳐지는 것이 이 저장소가 반복해 겪은 실패 양식이다.

## 모양 맞추기 -- 채점 규칙은 손대지 않는다

heldout 파일들은 위 ③의 전용 채점기에 맞춰 만들어졌고, 그래서 골든셋과 모양이 다르다.

  골든 safety 12문항      track=safety      answer_mode=None  -> score_safety
  골든 info_limit 6문항   track=info_limit  answer_mode=None
                          answer_form=refusal                 -> score_closed(refusal)

  heldout 의 대응 10문항  track=safety/info_limit
                          answer_mode=open (6건) 또는 None (4건)
                          answer_form 없음

모양을 안 맞추면 그 열 문항이 요점 없는 개방형으로 판정기에 들어가고,
`covers_key_points` 가 `sum(())*2 > 0` = False 라 **무조건 오답**이 된다. 채점
인공물이지 오답이 아니다. 그래서 다음 둘만 맞춘다. `evalset/` 파일은 건드리지 않는다.

  track in (safety, info_limit)  -> answer_mode 를 뗀다 (판정기로 가지 않게)
  track == info_limit            -> answer_form=refusal (골든과 같은 분기로 가게)

🔴 **`answer_form` 을 가진 heldout 문항이 하나도 없다.** 골든셋은 닫힌 문항 전부가
갖고 있다(number_won 34 · number_pct 7 · refusal 7 · boolean 5 · list 2 · duration 2).
그대로 채점하면 닫힌 28문항이 전부 `score_closed` 의 마지막 분기, 즉 생짜 부분문자열
대조로 가서 조직적으로 낮게 나온다. 골든의 `number_won` 은 콤마를 접고 허용오차를
쓰는데 부분문자열은 골든 문자열이 글자 그대로 있어야 한다.

그래서 **콤마로 묶인 금액만** 기계적으로 `number_won` 으로 본다. 28개 중 24개다.

  ^\d{1,3}(,\d{3})+$  ->  answer_form=number_won   (24건)
  그 밖                ->  손대지 않는다 -- 골든셋의 기본 분기와 같다 (4건)

남는 넷은 어느 쪽으로도 못 믿는다. 그래서 총계에 조용히 섞지 않고 따로 적는다.

  H-014 · K-014  골든이 한 자리 수("5"·"3")다. 부분문자열이면 답변에 그 숫자가
                 어디든 있으면 통과한다 -- **너무 느슨하다**
  N-005 · N-006  골든이 문자열이다. N-006 은 괄호 주석까지 붙은 긴 문장이라 맞는
                 답변도 글자 그대로는 안 담는다 -- **너무 엄격하다**

Usage:
    python scripts/score_heldout.py [--arm A49]
"""
import io
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, ".")
sys.path.insert(0, "src")
from scripts import error_analysis as ea  # noqa: E402

BASE = Path("scratch/ablation/v6")

#: 벌 이름 -> (문항 파일, 예전에 기록된 성적, 그때의 코드판). 예전 성적은 나란히
#: 보여 주기만 한다 -- 잣대가 달라 차이를 그대로 "떨어졌다" 고 읽으면 안 된다.
SUBSETS = [
    ("heldout_v2", "evalset/heldout_v2.jsonl", "17/18", "08-16 판"),
    ("heldout_v3", "evalset/heldout_v3.jsonl", "15/16", "08-17 판"),
    ("newset", "evalset/newset_20260816.jsonl", "17/20", "08-16 판"),
]

REFUSAL_TRACKS = ("safety", "info_limit")

#: 콤마로 세 자리씩 묶인 금액. 이것만 골든의 number_won 분기로 보낸다.
_AMOUNT = re.compile(r"^\d{1,3}(,\d{3})+$")

#: 잣대를 못 믿는 넷. 총계와 별도로 적는다 -- 위 주석 참고.
SHAKY = {"H-014": "골든이 한 자리 수라 부분문자열이 너무 느슨하다",
         "K-014": "골든이 한 자리 수라 부분문자열이 너무 느슨하다",
         "N-005": "골든이 문자열이다",
         "N-006": "골든이 괄호 주석까지 붙은 긴 문장이라 너무 엄격하다"}


def load(path: str) -> list[dict]:
    """Read a heldout file and give its cases the golden set's shape."""
    out = []
    for line in io.open(path, encoding="utf-8"):
        if not line.strip():
            continue
        case = json.loads(line)
        if case.get("track") in REFUSAL_TRACKS:
            case.pop("answer_mode", None)
            if case.get("track") == "info_limit":
                case["answer_form"] = "refusal"
        elif case.get("answer_mode") != "open":
            gold = str(case.get("answer") or case.get("gold") or "")
            if _AMOUNT.match(gold.strip()):
                case["answer_form"] = "number_won"
        out.append(case)
    return out


def judge(cases: list[dict]) -> int:
    """Produce the verdicts `analyse` reads. Open questions only."""
    made = 0
    for case in cases:
        if case.get("answer_mode") != "open":
            continue
        path = ea.RESPONSE_DIR / ("%s.json" % case["question_id"])
        if not path.exists():
            print("  🔴 응답 없음: %s" % case["question_id"])
            continue
        run = json.loads(path.read_text(encoding="utf-8"))
        _, cached = ea.with_retry(ea.run_judge, case, run["response"])
        if not cached:
            made += 1
            time.sleep(ea.PACE_S)
    return made


def score(cohort: str, path: str, records: dict, arm: str) -> tuple[dict, list[dict]]:
    arm_dir = BASE / ("server_%s" % cohort) / arm
    if not (arm_dir / "responses").exists():
        sys.exit("🔴 수집 산출물이 없습니다: %s" % (arm_dir / "responses"))
    ea.RESPONSE_DIR = arm_dir / "responses"
    ea.JUDGE_DIR = arm_dir / "judge"
    ea.OUT_DIR = arm_dir
    cases = load(path)
    made = judge(cases)
    if made:
        print("  %s 판정 %d건 신규" % (cohort, made), flush=True)
    rows = ea.analyse(cases, records)
    report = ea.build_report(cases, rows, cohort=cohort)
    report["arm"] = arm
    (arm_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return report, rows


def main(argv) -> int:
    arm = "A49"
    argv = list(argv)
    if "--arm" in argv:
        i = argv.index("--arm")
        arm = argv[i + 1]
    records = ea._load_records()

    total_ok = total_n = 0
    every: list[tuple[str, dict]] = []
    print("%-12s %-11s %-11s %-11s %s"
          % ("벌", "지금(A49)", "예전 기록", "예전 코드판", "비고"))
    print("-" * 74)
    for cohort, path, before, when in SUBSETS:
        report, rows = score(cohort, path, records, arm)
        ok = sum(1 for r in rows if r["correct"])
        n = len(rows)
        total_ok += ok
        total_n += n
        bad = sum(1 for r in rows if r.get("upstream_failure"))
        every += [(cohort, r) for r in rows]
        print("%-12s %-11s %-11s %-11s 상류장애 %d"
              % (cohort, "%d/%d" % (ok, n), before, when, bad))
    print("-" * 74)
    print("%-12s %-11s %-11s  🔴 같은 잣대 아님 -- 본문 주석 ③"
          % ("합계", "%d/%d" % (total_ok, total_n), "49/54"))
    print("   지금 %.1f%%   (골든셋 A49 는 110/112 = 98.2%%)"
          % (100 * total_ok / total_n))

    #: 잣대를 못 믿는 넷을 뺀 분모. 이쪽이 골든셋과 견줄 수 있는 수다.
    solid = [(c, r) for c, r in every if r["question_id"] not in SHAKY]
    solid_ok = sum(1 for _, r in solid if r["correct"])
    print("   잣대 불안 4문항 제외: %d/%d = %.1f%%"
          % (solid_ok, len(solid), 100 * solid_ok / len(solid)))

    print("\n잣대 불안 4문항 (총계에 섞어 읽지 말 것)")
    for cohort, r in every:
        if r["question_id"] in SHAKY:
            print("  %-7s %-4s by=%-14s %s"
                  % (r["question_id"], "정답" if r["correct"] else "오답",
                     r.get("scored_by"), SHAKY[r["question_id"]]))

    wrong = [(c, r) for c, r in solid if not r["correct"]]
    print("\n틀린 문항 %d건 (잣대 불안 제외)" % len(wrong))
    for cohort, r in wrong:
        print("  %-8s %-7s track=%-11s mode=%-6s by=%-22s %s"
              % (cohort, r["question_id"], r.get("track"),
                 r.get("answer_mode"), r.get("scored_by"),
                 (r.get("first_failure") or "")[:26]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
