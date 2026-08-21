r"""질문이 묻지 않은 골든 요점을 걷어낸다.

## 왜 이 스크립트가 있는가

골든 편집은 지금까지 **덧붙이기만** 했다(`scripts/refresh_golden_chain_evidence.py` 는
never deletes). 요점 삭제는 우리 잣대를 **느슨하게** 만드는 방향이라 손으로 하면 안 된다.
무엇을 왜 뺐는지가 코드에 남아야 한다.

## 무엇을 빼는가

`answer_key_points` 중 **질문이 묻지 않은 것**만 뺀다. 답변이 못 적은 것은 안 뺀다.
그 둘을 섞으면 성적을 올리려고 자를 줄인 것이 된다.

🔴 **뺄 수 없는 것.** `Q-080` 요점4(합계 80,318)는 남긴다. 요점1~3 이 백분율
(91.6% · 0.9% · 7.5%)을 요구하고 합계가 그 분모라서, *"구성을 알려줘"* 에 정당하게 든다.
답변이 백분율만 적고 분모를 안 밝힌 것은 진짜 결함이다.

## 채점에 영향이 없다

셋 다 3회 모두 정답이고 `covers_key_points`(과반 초과)가 여유를 두고 통과한다. 요점을
빼면 분모가 줄지만 covered 도 같이 줄어 판정이 안 바뀐다 — `--check` 가 그것을 확인한다.

## 저장된 판정 산출물은 안 맞게 된다

`scratch/ablation/v6/**/judge/Q-092.json` 의 `covered` 는 5칸이고 이 편집 뒤 요점은 4개다.
2026-08-21 서버 3회 측정은 편집 **전** 값이며 그것을 다시 계산하지 않는다. 다음 측정부터
W1 어긋남이 24 → 22 로 줄어든다.

Usage: python scripts/amend_golden_key_points.py [--check]
"""
import argparse
import io
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

PATH = Path("evalset/questions.jsonl")

#: (문항, 걷어낼 요점의 앞머리, 왜)
DROP = [
    ("Q-092", "전체 합계는 63,555억원이다",
     "질문은 '부문별 매출을 비교하고 1위가 2위보다 얼마나 큰지' 만 물었다. "
     "합계는 비교에도 차이 계산에도 쓰이지 않는다"),
    ("Q-104", "전체 합계는 11,314,459백만원",
     "질문은 '사업부문별 매출을 알려줘' 다. 합계를 묻지 않았고 비교도 없다"),
    ("Q-104", "연결재무제표 기준이며 제51기는 중단사업",
     "질문이 회계 기준도 재작성 사유도 묻지 않았다. 3회 모두 uncovered 인데 "
     "그것은 답변의 흠이 아니라 요점이 질문 범위를 넘은 것이다"),
]


ROUNDS = (1, 2, 3)


def verdict_after(qid: str, drop_at: list[int]) -> str:
    """저장된 3회 판정에서 그 칸들을 **한꺼번에** 빼고 과반 초과를 다시 계산한다.

    이것이 「채점에 영향이 없다」의 유일한 진짜 검증이다. `len*2 > len` 같은 항등식은
    아무것도 확인하지 않는다.

    🔴 한 문항에서 두 칸을 뺄 때는 합쳐서 재야 한다. 한 칸씩 따로 재면 각각은 통과하는데
    합치면 뒤집히는 경우를 놓친다.
    """
    out = []
    for round_no in ROUNDS:
        name = "server_full" if round_no == 1 else "server_full_r%d" % round_no
        path = Path("scratch/ablation/v6/%s/A56/judge/%s.json" % (name, qid))
        if not path.exists():
            return "판정 산출물 없음"
        covered = list(json.loads(path.read_text(encoding="utf-8"))["covered"])
        before = sum(covered) * 2 > len(covered)
        kept = [c for i, c in enumerate(covered) if i not in set(drop_at)]
        after = sum(kept) * 2 > len(kept)
        out.append("r%d %d/%d → %d/%d %s→%s"
                   % (round_no, sum(covered), len(covered), sum(kept), len(kept),
                      "O" if before else "X", "O" if after else "X"))
    return " · ".join(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="바꾸지 않고 대조만 한다")
    args = ap.parse_args()

    rows = [json.loads(line) for line in io.open(PATH, encoding="utf-8") if line.strip()]
    index = {row["question_id"]: row for row in rows}

    #: 저장된 판정의 칸 번호는 **편집 전** 요점 순서에 매인다. 먼저 다 찾아 두고 나서 뺀다.
    original = {qid: list(index[qid]["answer_key_points"]) for qid, _h, _w in DROP}

    hit = 0
    dropped_at: dict[str, list[int]] = {}
    for qid, head, why in DROP:
        points = index[qid]["answer_key_points"]
        keep = [p for p in points if not p.startswith(head)]
        if len(keep) == len(points):
            print("  %s  이미 없다 — %s" % (qid, head[:34]))
            continue
        hit += 1
        at = next(i for i, p in enumerate(original[qid]) if p.startswith(head))
        dropped_at.setdefault(qid, []).append(at)
        print("  %s  편집 전 %d번 칸을 뺀다" % (qid, at + 1))
        print("     %s" % head)
        print("     왜: %s" % why)
        index[qid]["answer_key_points"] = keep

    print("\n문항별 판정 재계산 — 뺀 칸을 합쳐서 본다\n")
    for qid, ats in dropped_at.items():
        print("  %s  요점 %d개 → %d개" % (qid, len(original[qid]),
                                     len(index[qid]["answer_key_points"])))
        print("     %s" % verdict_after(qid, ats))

    if args.check:
        print("\n--check 라 파일은 안 건드렸다. 해당 %d건." % hit)
        return 0
    PATH.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    print("\n%s 에 %d건 반영했다." % (PATH, hit))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
