r"""미공개셋에서 한 번이라도 틀린 문항을 물음·답변·골든·원인으로 묶어 낸다.

    PYTHONIOENCODING=utf-8 python scratch/dump_heldout_wrong.py

답변은 이미 사 둔 산출물에서 그대로 옮긴다. 원인 진단(`판정`)만 손으로 쓴 것이고,
관측(회차별 정답 여부·골든·답변)과 갈라 둔다 — 어느 쪽이 측정이고 어느 쪽이 해석인지
섞이면 다음 사람이 못 가른다.
"""
import importlib.util
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, ".")
sys.path.insert(0, "src")
sys.stdout.reconfigure(encoding="utf-8")

from scripts import error_analysis as ea                          # noqa: E402

_spec = importlib.util.spec_from_file_location("_sh", "scratch/score_heldout.py")
_sh = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_sh)

ARM = "A91h"
OUT = Path("scratch/audit/heldout_wrong_A91h.json")
WHERE = {"H-005": ("heldout_v2", "evalset/heldout_v2.jsonl"),
         "K-015": ("heldout_v3", "evalset/heldout_v3.jsonl"),
         "N-006": ("newset", "evalset/newset_20260816.jsonl"),
         "N-009": ("newset", "evalset/newset_20260816.jsonl")}

#: 손으로 쓴 진단. 관측과 갈라 둔다.
VERDICT = {
    "H-005": {
        "갈래": "우리 결함",
        "한 줄": "묻지 않은 계약을 덧붙이고, 그 합계를 틀리게 낸다",
        "왜 틀렸나": [
            "물음은 2024-04-17 공시 **한 건**의 계약상대와 금액이다.",
            "골든 요점 둘(Aramco Asia Japan · 160,038,036,031원)은 답변에 다 들어 있다.",
            "그런데 답변이 같은 날 다른 계약(291,548,177,545원)을 자진해 덧붙이고,"
            " 마지막 줄에서 둘을 더한 총액으로 결론을 맺는다.",
            "그 합계가 산술로 안 맞는다 — 160,038,036,031 + 291,548,177,545 ="
            " 451,586,213,576 인데 답변은 452,586,203,566 을 적었다 (999,989,990 차이).",
            "즉 물음이 하나를 물었는데 둘을 합쳐 답했고, 합치는 과정도 틀렸다.",
        ],
        "3회 모두 같은 문면인가": "그렇다 — 세 회차 답변이 사실상 동일하다. 뽑기가 아니라 구조적이다.",
        "왜 지금 규칙에 안 걸리나":
            "A91 의 roster_sum_ban(한 회사의 여러 공시를 더한 합계는 내지 않는다)은"
            " 로스터 줄에만 붙는다. H-005 는 그리드 문항이라 그 줄이 안 생긴다.",
        "골든셋의 같은 갈래": "Q-115 (계약 넷을 열거하고 둘만 합산)",
    },
    "N-006": {
        "갈래": "평가셋 쪽 (골든 과잉 명세)",
        "한 줄": "계약상대 이름은 정확한데 골든이 괄호 주석까지 요구한다",
        "왜 틀렸나": [
            "골든은 'Orsted Hornsea Project Four Limited (덴마크/Orsted A/S의 SPC)' 다.",
            "답변은 'Orsted Hornsea Project Four Limited' 로 상호를 정확히 맞혔다.",
            "괄호 안은 국적과 SPC 라는 **주석**이지 계약상대 이름의 일부가 아니다.",
            "미공개 문항 파일에는 답 형식 지정이 없어 골든 문자열 전체를 부분문자열로"
            " 대조한다. 그래서 주석이 빠지면 오답으로 떨어진다.",
        ],
        "3회 모두 같은 문면인가": "그렇다 — 세 회차 답변이 동일하다.",
        "고칠 대상": "모델이 아니라 채점 규칙 또는 골든 문자열. 오픈이슈에 기록만 한다.",
    },
    "N-009": {
        "갈래": "흔들림 (물음이 증감분을 묻는데 결과값으로 맺는다)",
        "한 줄": "증감분을 본문에 적어 놓고 마지막 줄은 결과값으로 결론을 맺는다",
        "왜 틀렸나": [
            "물음은 '계약금액이 얼마나 바뀌었는가' — 증감분을 묻는다. 골든은"
            " 706,042,154,000 이다.",
            "세 회차 모두 본문에 706,042,154,000 을 적었다. 정보는 다 찾았다.",
            "갈린 것은 **마지막 줄**이다. r2 는 '706,042,154,000원만큼 감소하였습니다' 로"
            " 맺어 정답, r1 은 '2,389,104,200,000원으로 변경되었습니다' 로 맺어 오답,"
            " r3 은 결론 줄 없이 '-706,042,154,000원' 만 남겨 오답이다.",
            "골든이 콤마 세 자리 금액이라 채점이 숫자 분기로 가고, 답변에서 뽑은 값이"
            " 결과값(2,389,104,200,000)이면 증감분과 안 맞는다.",
        ],
        "3회 모두 같은 문면인가": "아니다 — r1 X · r2 O · r3 X 로 갈린다.",
        "회귀인가": [
            "A86 에서 넣은 result_value_op('바뀌었는가' 를 증감 지시로 안 읽는다)이"
            " 원인인지 의심해 팔별로 3회씩 대조했다.",
            "A84 X O O · A86c X O X · A91h X O X.",
            "고치기 전인 A84 도 r1 에서 틀린다. 즉 이 실패는 result_value_op 보다"
            " 앞선다 — 그 변경이 만든 것이 아니다.",
            "다만 1/3 대 2/3 라는 차이는 3회로는 뽑기와 못 가른다. 회귀가 아니라고"
            " 단정하지도, 맞다고 하지도 않는다.",
        ],
    },
    "K-015": {
        "갈래": "흔들림 (r2 한 번)",
        "한 줄": "r2 가 이웃 칸에 실린 다른 공시의 금액을 최초 계약금액으로 집는다",
        "왜 틀렸나": [
            "골든 요점은 셋 — 정정 3회 · 최초 1,457,049,721,180원 ·"
            " 최종 1,481,561,966,798원.",
            "r1 과 r3 는 최초 금액을 맞게 적어 정답이다.",
            "r2 만 최초 금액을 1,558,826,903,000원으로 적었다.",
            "그 값은 지어낸 수가 아니다 — 같은 회사의 **다른 공시**"
            "(현대건설 대전 도안2-2지구 공동주택 신축공사, 2024-04-15,"
            " 접수번호 20240415800078)의 계약금액이고, r2 근거 안에 그 칸이 같이 실려 있었다.",
            "정작 근거 맨 앞에는 결정적 집계 줄이 올바른 추이를 코드로 계산해"
            " 적어 두었다 — '2024-04-30 1,457,049,721,180원 → … → 2025-10-13"
            " 1,481,561,966,798원'. 모델이 그 줄을 두고 옆 칸 금액을 집었다.",
            "즉 회수 실패가 아니라 **근거 칸 사이 혼선**이다. 같은 회사의 무관한 공시가"
            " 이웃 칸에 있으면 결정적 줄이 있어도 밀릴 수 있다.",
            "게다가 r2 는 정정 단계마다 1,558,826,903,000원이 유지됐다고 적고"
            " 결론에서는 1,481,561,966,798원으로 확정됐다고 해 스스로 모순된다.",
            "정정 횟수도 본문은 셋을 나열하고 결론은 '총 네 번' 이라고 적는다.",
        ],
        "3회 모두 같은 문면인가": "아니다 — r1 O · r2 X · r3 O.",
        "🔴 성적에 안 잡힌 결함": [
            "r1 은 정답 처리됐지만 마지막 줄에 '최종적으로 약 24억원이 증가' 라고 적는다.",
            "실제 증가분은 1,481,561,966,798 − 1,457,049,721,180 = 24,512,245,618원,"
            " 즉 약 245억원이다. 자릿수가 열 배 틀렸다.",
            "골든 요점 셋이 다 실려 있어 채점은 통과했다. 축 검사도 이 줄은 못 잡는다.",
        ],
    },
}


def main() -> int:
    records = ea._load_records()
    out = []
    for qid, (cohort, path) in WHERE.items():
        case = next(json.loads(l) for l in io.open(path, encoding="utf-8")
                    if l.strip() and json.loads(l)["question_id"] == qid)
        rounds = []
        for index, suffix in enumerate(("", "_r2", "_r3"), start=1):
            arm_dir = Path("scratch/ablation/v6") / ("server_%s%s" % (cohort, suffix)) / ARM
            ea.RESPONSE_DIR, ea.JUDGE_DIR, ea.OUT_DIR = (arm_dir/"responses",
                                                        arm_dir/"judge", arm_dir)
            row = next(r for r in ea.analyse(_sh.load(path), records)
                       if r["question_id"] == qid)
            answer = json.loads((arm_dir / "responses" / ("%s.json" % qid))
                                .read_text(encoding="utf-8"))["response"]["answer"]
            rounds.append({"r": index, "정답": bool(row["correct"]), "답변": answer})
        out.append({
            "question_id": qid,
            "코호트": cohort,
            "track": case.get("track"),
            "물음": case["question"],
            "골든": {"answer": case.get("answer") or None,
                   "answer_key_points": case.get("answer_key_points"),
                   "evidence": [e.get("rcept_no") for e in (case.get("evidence") or [])]},
            "회차별": rounds,
            "몇 회 틀렸나": "%d/3" % sum(1 for r in rounds if not r["정답"]),
            "판정": VERDICT[qid],
        })

    out.sort(key=lambda d: (-int(d["몇 회 틀렸나"][0]), d["question_id"]))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "생성": "scratch/dump_heldout_wrong.py",
        "팔": ARM,
        "성적": "51/54 · 51/54 · 51/54 (평가 서버 3회, 2026-08-27)",
        "출처": "scratch/audit/heldout_a91h_rounds.json · scratch/audit/axis_heldout.json",
        "읽는 법": "「회차별」·「골든」·「몇 회 틀렸나」는 산출물에서 그대로 옮긴 관측이고,"
                " 「판정」만 손으로 쓴 해석이다.",
        "문항": out,
    }, ensure_ascii=False, indent=1), encoding="utf-8")

    for d in out:
        print("%-7s %-11s %s  %s" % (d["question_id"], d["코호트"],
                                     d["몇 회 틀렸나"], d["판정"]["한 줄"]))
    print("\n%s 에 적었다 (%.1f KB)" % (OUT, OUT.stat().st_size / 1024))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
