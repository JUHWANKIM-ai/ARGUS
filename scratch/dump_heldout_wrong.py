r"""미공개셋에서 한 번이라도 틀린 문항을 물음·답변·골든·원인으로 묶어 낸다.

    PYTHONIOENCODING=utf-8 python scratch/dump_heldout_wrong.py --arm A92h

답변은 이미 사 둔 산출물에서 그대로 옮긴다. 원인 진단(`판정`)만 손으로 쓴 것이고,
관측(회차별 정답 여부·골든·답변)과 갈라 둔다 — 어느 쪽이 측정이고 어느 쪽이 해석인지
섞이면 다음 사람이 못 가른다.

틀린 문항 목록은 `scratch/audit/heldout_<팔>_rounds.json` 에서 읽는다. 손으로 적으면
채점과 갈라진다. 앞 팔에서 틀렸다가 지금 팔에서 안 틀리는 문항은 따로 싣는다 —
무엇이 움직였는지가 오답 목록만큼 중요하다.
"""
import argparse
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

WHERE = {"heldout_v2": "evalset/heldout_v2.jsonl",
         "heldout_v3": "evalset/heldout_v3.jsonl",
         "newset": "evalset/newset_20260816.jsonl"}

_RECORDS: dict = {}

#: 손으로 쓴 진단. 관측과 갈라 둔다.
VERDICT = {
    "N-006": {
        "갈래": "평가셋 쪽 (골든 과잉 명세)",
        "한 줄": "계약상대 이름은 정확한데 골든이 괄호 주석까지 요구한다",
        "왜 틀렸나": [
            "골든은 'Orsted Hornsea Project Four Limited (덴마크/Orsted A/S의 SPC)' 다.",
            "답변은 'Orsted Hornsea Project Four Limited' 로 상호를 정확히 맞혔다.",
            "괄호 안은 국적과 SPC 라는 주석이지 계약상대 이름의 일부가 아니다.",
            "미공개 문항 파일에는 답 형식 지정이 없어 골든 문자열 전체를 부분문자열로"
            " 대조한다. 그래서 주석이 빠지면 오답으로 떨어진다.",
        ],
        "고칠 대상": "모델이 아니라 채점 규칙 또는 골든 문자열. 오픈이슈에 기록만 한다.",
        "팔을 건너뛴 관측": "A84 · A86c · A91h · A92h 에서 모두 3/3 오답이다.",
    },
    "N-009": {
        "갈래": "흔들림 (물음이 증감분을 묻는데 결과값으로 맺는다)",
        "한 줄": "증감분을 본문에 적어 놓고 마지막 줄은 결과값으로 결론을 맺는다",
        "왜 틀렸나": [
            "물음은 '계약금액이 얼마나 바뀌었는가' — 증감분을 묻는다."
            " 골든은 706,042,154,000 이다.",
            "회차마다 본문에는 그 값이 적혀 있다. 정보는 찾는다.",
            "갈리는 것은 마지막 줄이다. 증감분으로 맺으면 정답, 결과값"
            " (2,389,104,200,000)으로 맺으면 오답이다.",
            "골든이 콤마 세 자리 금액이라 채점이 숫자 분기로 가고, 답변에서 뽑은 값이"
            " 결과값이면 증감분과 안 맞는다.",
        ],
        "회귀인가": [
            "A86 의 result_value_op('바뀌었는가' 를 증감 지시로 안 읽는다)이 원인인지"
            " 의심해 팔별로 3회씩 대조했다.",
            "A84 X O O · A86c X O X · A91h X O X.",
            "고치기 전인 A84 도 r1 에서 틀린다. 이 실패는 result_value_op 보다 앞선다.",
            "다만 1/3 대 2/3 라는 차이는 3회로는 뽑기와 못 가른다. 회귀가 아니라고"
            " 단정하지도, 맞다고 하지도 않는다.",
        ],
    },
    "H-005": {
        "갈래": "우리 결함 (A91 까지 3/3 오답) → A92 에서 3/3 정답",
        "한 줄": "묻지 않은 계약을 덧붙이고 그 합계를 틀리게 냈다",
        "왜 틀렸나": [
            "물음은 2024-04-17 공시 한 건의 계약상대와 금액이다.",
            "골든 요점 둘(Aramco Asia Japan · 160,038,036,031원)은 답변에 다 있었다.",
            "그런데 같은 날 다른 계약(291,548,177,545원)을 자진해 덧붙이고 총액으로"
            " 결론을 맺었다. 160,038,036,031 + 291,548,177,545 = 451,586,213,576 인데"
            " 답변은 452,586,203,566 을 적었다 (999,989,990 차이).",
        ],
        "🔴 A92 에서 왜 정답이 됐나": [
            "겨냥해 넣은 drop_unsourced_total 이 지워서가 아니다. 그 규칙은"
            " 498응답(골든 336 · 미공개 162)에서 한 번도 발화하지 않았다.",
            "모델이 이번에는 합계를 아예 안 썼다. 세 회차 문면이 A91 답변에서 한 문장을"
            " 뺀 모양이 아니라 구조가 다른 별개의 초안이다.",
            "r1·r3 은 재생성 흔적('앞서 제공한 답변 중 일부 오류가 발견되었습니다')을"
            " 달고 있다. 규칙은 검증 앞에서 도니, 규칙이 지웠다면 검증이 걸릴 것도 없어"
            " 재생성 자체가 안 일어난다.",
            "그러므로 이 정답을 A92 에 귀속시킬 수 없다.",
        ],
    },
    "K-015": {
        "갈래": "흔들림 (A91 r2 한 번) → A92 에서 3/3 정답",
        "한 줄": "r2 가 이웃 칸에 실린 다른 공시의 금액을 최초 계약금액으로 집었다",
        "왜 틀렸나": [
            "골든 요점은 셋 — 정정 3회 · 최초 1,457,049,721,180원 ·"
            " 최종 1,481,561,966,798원.",
            "A91 r2 만 최초 금액을 1,558,826,903,000원으로 적었다. 지어낸 수가 아니라"
            " 같은 회사의 다른 공시(현대건설 대전 도안2-2지구 공동주택 신축공사,"
            " 2024-04-15, 접수번호 20240415800078)의 계약금액이고, 그 칸이 근거에"
            " 같이 실려 있었다.",
            "근거 맨 앞의 결정적 집계 줄은 올바른 추이를 코드로 계산해 적어 두었다."
            " 모델이 그 줄을 두고 옆 칸 금액을 집었다 — 회수 실패가 아니라 칸 사이"
            " 혼선이다.",
        ],
        "🔴 성적에 안 잡힌 결함": [
            "A91 r1 은 정답 처리됐지만 '최종적으로 약 24억원이 증가' 라고 적었다.",
            "실제 증가분은 1,481,561,966,798 − 1,457,049,721,180 = 24,512,245,618원,"
            " 즉 약 245억원이다. 자릿수가 열 배 틀렸다.",
            "A92 세 회차를 직접 읽었다 — 그 줄이 없다. 1,558,826,903,000 도 안 나오고,"
            " 맺음말이 숫자 없이 '금액이 증가한 것을 알 수 있습니다' 로만 끝난다.",
            "🔴 그러나 이것도 A92 에 귀속시킬 수 없다. drop_unsourced_total 은"
            " 발화하지 않았고, 이 문항의 답변은 그냥 다시 뽑힌 것이다.",
        ],
    },
}


def rounds_audit(arm: str) -> dict:
    path = "scratch/audit/heldout_%s_rounds.json" % arm.lower()
    return json.loads(io.open(path, encoding="utf-8").read())


def ever_wrong(audit: dict) -> set:
    out = set()
    for r in audit["회차별"]:
        for s in r["부분집합"]:
            out |= set(s["오답"])
    return out


def collect(arm: str, qids: set) -> dict:
    """문항 -> {코호트, 사례, 회차별}. 지금 팔의 답변으로 싣는다."""
    out: dict = {}
    if not qids:
        return out
    for cohort, path in WHERE.items():
        cases = {c["question_id"]: c for c in _sh.load(path)}
        want = set(cases) & qids
        if not want:
            continue
        for index, suffix in enumerate(("", "_r2", "_r3"), start=1):
            arm_dir = Path("scratch/ablation/v6") / ("server_%s%s" % (cohort, suffix)) / arm
            if not (arm_dir / "responses").exists():
                continue
            ea.RESPONSE_DIR = arm_dir / "responses"
            ea.JUDGE_DIR = arm_dir / "judge"
            ea.OUT_DIR = arm_dir
            rows = {r["question_id"]: r for r in ea.analyse(_sh.load(path), _RECORDS)}
            for qid in sorted(want):
                answer = json.loads((arm_dir / "responses" / ("%s.json" % qid))
                                    .read_text(encoding="utf-8"))["response"]["answer"]
                slot = out.setdefault(qid, {"코호트": cohort, "사례": cases[qid],
                                            "회차별": []})
                slot["회차별"].append({"r": index,
                                     "정답": bool(rows[qid]["correct"]),
                                     "답변": answer})
    return out


def entry(qid: str, got: dict) -> dict:
    case = got["사례"]
    rounds = sorted(got["회차별"], key=lambda d: d["r"])
    return {
        "question_id": qid,
        "코호트": got["코호트"],
        "track": case.get("track"),
        "물음": case["question"],
        "골든": {"answer": case.get("answer") or None,
               "answer_key_points": case.get("answer_key_points"),
               "evidence": [e.get("rcept_no") for e in (case.get("evidence") or [])]},
        "회차별": rounds,
        "몇 회 틀렸나": "%d/%d" % (sum(1 for r in rounds if not r["정답"]), len(rounds)),
        "판정": VERDICT.get(qid, {"갈래": "미진단",
                               "한 줄": "이 팔에서 새로 나온 오답이다"}),
    }


def main() -> int:
    global _RECORDS
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", default="A92h")
    ap.add_argument("--before", default="A91h", help="견줄 앞 팔")
    args = ap.parse_args()
    _RECORDS = ea._load_records()

    audit = rounds_audit(args.arm)
    before = rounds_audit(args.before)
    wrong = ever_wrong(audit)
    fixed = ever_wrong(before) - wrong

    now = collect(args.arm, wrong)
    was = collect(args.arm, fixed)

    out = Path("scratch/audit/heldout_wrong_%s.json" % args.arm)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "생성": "scratch/dump_heldout_wrong.py",
        "팔": args.arm,
        "성적": " · ".join(r["합계"] for r in audit["회차별"]),
        "견줌": "%s 은 %s" % (args.before,
                          " · ".join(r["합계"] for r in before["회차별"])),
        "읽는 법": "「회차별」·「골든」·「몇 회 틀렸나」는 산출물에서 그대로 옮긴 관측이고,"
                " 「판정」만 손으로 쓴 해석이다.",
        "틀린 문항": [entry(q, now[q]) for q in sorted(now)],
        "앞 팔에서 틀렸다가 지금은 안 틀리는 문항": [entry(q, was[q]) for q in sorted(was)],
    }, ensure_ascii=False, indent=1), encoding="utf-8")

    print("팔 %s   성적 %s" % (args.arm, " · ".join(r["합계"] for r in audit["회차별"])))
    print("틀린 문항")
    for q in sorted(now):
        print("  %-7s %-11s %s  %s" % (q, now[q]["코호트"], entry(q, now[q])["몇 회 틀렸나"],
                                       VERDICT.get(q, {}).get("한 줄", "미진단")))
    print("앞 팔(%s)에서 틀렸다가 지금은 안 틀리는 문항" % args.before)
    for q in sorted(was) or ["  없음"]:
        if q.strip() == "없음":
            print(q)
        else:
            print("  %-7s %-11s %s" % (q, was[q]["코호트"], entry(q, was[q])["몇 회 틀렸나"]))
    print("\n%s 에 적었다 (%.1f KB)" % (out, out.stat().st_size / 1024))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
