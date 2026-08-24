r"""`A86` — 배포본 검토에서 손으로 찾은 결함 셋을 고정한다 (2026-08-23).

## 🔴 셋 다 성적에는 안 걸리던 것들이다

사용자가 `scratch/audit/answers_a84_server.json` 을 읽다 찾았다. 자동 채점 8축이 전부
만점(근거 완전성만 167/168)인 상태에서 나온 결함이므로, **판정기가 못 보는 자리에 결함이
살 수 있다**는 것이 이 팔의 교훈이고 이 시험의 존재 이유다.

    Q-005  `result_value_op`    「얼마로 바뀌었는가」를 차로 읽었다
    Q-012  `holding_pair`       주식 수를 스스로 빼다 틀렸고 자기모순 문장을 냈다
    23건   `quiet_verify_note`  검증 각주가 답변 본문에 실려 배달됐다
"""
import json
import re

import pytest

from argus import ablation
from argus.agent import aggregate

# --- ① `result_value_op` — 결과값을 묻는 질문을 차로 읽지 않는다 ------------------

Q005 = ("삼성중공업이 2023년 6월에 공시한 원유운반선 2척 공급계약의 계약금액은 "
        "이후 정정공시에서 얼마로 바뀌었는가?")


def test_얼마로_바뀌었는가는_차가_아니다():
    """🔴 이 한 글자가 골든 정답과 우리가 덧붙인 값을 갈랐다.

    `A84` 에서 `_op` 가 「차」를 돌려주면 `_total` 이 원공시와의 차 112,700,000,000 을
    계산하고 `state_total` 이 그것을 *"질문이 물은 값"* 이라며 답변에 붙였다. 골든은
    114,800,000,000 이고 **모델 본문은 그것을 맞게 적고 있었다.**
    """
    with ablation.use("A84"):
        assert aggregate._op(Q005) == "차", "종전 거동이 안 움직였다는 뜻이다"
    with ablation.use("A86"):
        assert aggregate._op(Q005) == ""


@pytest.mark.parametrize("question", [
    "HD현대중공업이 2023년 7월에 공시한 FPU 1기 공급계약은 정정공시로 계약금액이 얼마나 증가했는가?",
    "최종 정정공시 기준 조달 총액은 최초 공시 대비 얼마나 줄었는가?",
    "정정 전후 계약금액이 얼마나 바뀌었는가?",
    "두 회사의 계약금액 차이는 얼마인가?",
])
def test_진짜_변동폭_질문은_그대로_차다(question):
    """🔴 이 시험이 죽으면 수정이 겨냥 밖을 깎은 것이다.

    지운 두 힌트(`바뀌었는가` · `변경되었는가`)는 바로 윗줄의 `얼마나 바뀌` ·
    `얼마나 변경` 과 겹친다. 그래서 「얼마나」 형은 한 문항도 안 잃어야 한다.
    """
    for arm in ("A84", "A86"):
        with ablation.use(arm):
            assert aggregate._op(question) == "차"


def test_지운_힌트는_얼마나형과_겹치는_것뿐이다():
    """지운 문자열이 무엇인지 한 자리에 못 박는다."""
    assert aggregate._BARE_DELTA_HINTS == ("변경되었는가", "바뀌었는가")
    delta_hints = dict(aggregate._OPS)["차"]
    for bare in aggregate._BARE_DELTA_HINTS:
        assert any(h != bare and h.endswith(bare[:2]) or "얼마나" in h
                   for h in delta_hints), "겹치는 「얼마나」 형이 없으면 겨냥을 잃는다"


def test_골든_112문항에서_갈리는_것은_Q_005_하나다():
    """영향 범위를 산출물로 못 박는다. 문항이 늘면 이 시험이 먼저 깨진다."""
    path = "scratch/audit/golden_questions.json"
    try:
        cases = json.load(open(path, encoding="utf-8"))["문항"]
    except FileNotFoundError:                       # 산출물 없는 기계
        pytest.skip("%s 가 없다 — scratch/dump_golden_questions.py 로 만든다" % path)
    moved = []
    for case in cases:
        with ablation.use("A84"):
            before = aggregate._op(case["question"])
        with ablation.use("A86"):
            after = aggregate._op(case["question"])
        if before != after:
            moved.append(case["question_id"])
    assert moved == ["Q-005"]


# --- ② `holding_pair` — 짚은 두 보고서를 칸마다 대조한다 ------------------------

#: `(주)두산` → `두산에너빌리티` 체인에서 질문이 짚는 네 보고서. 코퍼스 실측값이다.
CHAIN = [
    {"reporter": "(주)두산", "corp_name": "두산에너빌리티", "rcept_dt": "20250310",
     "rcept_no": "20250310000839", "ratio_current": "30.67",
     "shares_current": "196,488,145",
     "fields": {"주요계약체결 주식등의 수": "직전 84,634,380 → 이번 84,282,066",
                "주요계약체결 비율": "직전 13.21 → 이번 13.15"}},
    {"reporter": "(주)두산", "corp_name": "두산에너빌리티", "rcept_dt": "20250514",
     "rcept_no": "20250514001078", "ratio_current": "30.67",
     "shares_current": "196,488,145",
     "fields": {"주요계약체결 주식등의 수": "직전 84,282,066 → 이번 84,282,066",
                "주요계약체결 비율": "직전 13.15 → 이번 13.16"}},
    {"reporter": "(주)두산", "corp_name": "두산에너빌리티", "rcept_dt": "20260305",
     "rcept_no": "20260305001006", "ratio_current": "30.68",
     "shares_current": "196,506,795",
     "fields": {"주요계약체결 주식등의 수": "직전 92,270,920 → 이번 92,270,920",
                "주요계약체결 비율": "직전 14.40 → 이번 14.40"}},
]
Q012 = ("㈜두산이 두산에너빌리티에 대해 2025년 3월과 2026년 3월에 제출한 "
        "대량보유상황보고서를 비교해 지분 보유 상황이 어떻게 달라졌는지 설명해줘.")
Q020 = ("㈜두산이 두산에너빌리티에 대해 2025년 3월 10일과 2025년 5월 14일에 제출한 "
        "두 대량보유상황보고서에서 보유비율이 달라졌는지 근거와 함께 설명해줘.")


def pair(question, records=None, arm="A86"):
    with ablation.use(arm):
        return aggregate.holding_pair(records if records is not None else CHAIN,
                                      question, lambda *_: CHAIN)


def test_주식수_차를_코드가_뺀다():
    """🔴 이것이 이 팔의 핵심 단정이다.

    196,506,795 − 196,488,145 = **18,650**. `A84` 3회는 18,607 · 18,652 · 18,652 를
    냈고 셋 다 틀렸다. 이 자리에 결정적 줄이 없어 모델이 직접 뺐기 때문이다.
    """
    out = pair(Q012)
    assert "196,488,145주 → 196,506,795주 (18,650주 증가)" in out
    for wrong in ("18,607", "18,652"):
        assert wrong not in out


def test_증감을_낱말로_적는다():
    """🔴 값만 주면 모델이 방향을 스스로 고르고, 그때 틀렸다.

    근거에는 범위가 다른 두 참말이 있다 — 보고서 **사이**의 증가와 한 보고서 **안**의
    변동 없음. `A84` 3회가 후자의 서술을 전자의 수에 붙여
    *"동일한 수치인 92,270,920주"* 를 냈다. 골든 요점3 은 「증가」다.
    """
    out = pair(Q012)
    assert "84,282,066주(13.15%) → 92,270,920주(14.40%) (7,988,854주 증가)" in out
    assert "동일" not in out


def test_기간을_달로_못_박는다():
    """`A84` 3회가 12개월 사이를 *"약 18개월"* 이라 적었다."""
    assert "약 12개월" in pair(Q012)


def test_값이_같으면_변동_없음이라_적는다():
    """`Q-020` 골든 요점1·2 — *"두 보고서 모두 30.67%"* · *"196,488,145주로 같다"*."""
    out = pair(Q020)
    assert "196,488,145주 → 196,488,145주 (변동 없음)" in out
    assert "보유비율 30.67% → 30.67% (변동 없음)" in out
    assert "증가" not in out and "감소" not in out


def test_이력_전체를_묻는_질문에는_침묵한다():
    """`Q-106` 은 짚은 두 보고서가 없다. 추이 줄이 답하는 질문이라 이 줄은 안 든다."""
    assert pair("(주)두산이 두산로보틱스에 대해 보고한 보유비율이 상장 이후 "
                "어떻게 변해 왔는지 각 보고서를 근거로 설명해줘.") == ""


@pytest.mark.parametrize("question", [
    #: 날짜가 하나뿐
    "㈜두산이 2025년 3월에 제출한 대량보유상황보고서의 보유비율은 얼마인가?",
    #: 날짜가 셋
    "㈜두산이 2025년 3월과 2025년 5월과 2026년 3월에 제출한 보고서의 보유비율을 비교해줘.",
])
def test_짚은_날짜가_둘이_아니면_침묵한다(question):
    """🔴 침묵이 기본값이다 — 어느 것이 답인지 모르는 수를 내놓느니 안 내는 편이 낫다."""
    assert pair(question) == ""


def test_그_달에_보고서가_둘이면_침묵한다():
    """한 달에 두 건이면 어느 쪽을 짚었는지 모른다."""
    twin = CHAIN + [dict(CHAIN[0], rcept_dt="20250325", rcept_no="20250325000001")]
    with ablation.use("A86"):
        assert aggregate.holding_pair(twin, Q012, lambda *_: twin) == ""


def test_줄이_줄바꿈으로_시작하지_않는다():
    """🔴 서버 실측으로 잡은 결함이다.

    `_prepend` 가 이 줄을 홀로 세우므로 앞에 줄바꿈을 붙이면 근거 첫 줄이 빈 문자열이
    되고, `attach_citation` 의 블록 파서가 그 빈 줄을 머리글로 읽어
    **`(근거: , 접수번호 …)`** 라는 라벨 없는 인용을 낸다. `A84` 336응답에는 0건이던
    모양이라 이 줄이 만든 것이 분명했다.
    """
    out = pair(Q012)
    assert out and not out.startswith("\n")
    assert out.startswith("- [결정적 집계]")


def test_스위치가_꺼지면_침묵한다():
    assert pair(Q012, arm="A84") == ""


def test_부모가_꺼지면_세울_수_없다():
    from dataclasses import replace

    with pytest.raises(ValueError, match="holding_pair"):
        replace(ablation.ARMS["A0"], name="TEST", holding_pair=True)


# --- ③ `quiet_verify_note` — 각주를 배달되는 문면에 안 싣는다 --------------------

_NOTE = re.compile(r"\(주의:")


def test_A86_은_각주_스위치를_켠다():
    assert ablation.ARMS["A86"].quiet_verify_note is True
    assert ablation.ARMS["A84"].quiet_verify_note is False


def test_채점기가_각주를_떼므로_성적은_안_움직인다():
    """🔴 이 스위치가 안전한 이유가 이것이다.

    `error_analysis` 가 **채점 전에** 이 꼬리를 뗀다(*"모델이 쓴 것만 채점 대상"*).
    그러므로 각주를 안 붙여도 채점되는 문면은 글자까지 같다. 이 시험이 죽으면 그
    전제가 깨진 것이고, 그때는 스위치를 다시 판단해야 한다.
    """
    from scripts import error_analysis as ea

    body = "답이다. (근거: 접수번호 20230131800162)"
    tail = "\n\n(주의: 자동 검증에서 결함이 발견되었습니다 — amount: 답변 1원 vs 재계산 2)"
    assert ea.strip_verify_tail(body + tail) == ea.strip_verify_tail(body)
