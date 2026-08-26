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


# --- ③ `decisive_not_citable` — 결정적 줄은 공시가 아니다 -----------------------

#: 결정적 줄 + 근거 블록 하나. 실제 근거의 모양 그대로다.
_EVIDENCE = (
    "- [결정적 집계] (주)두산 보고 보유비율 추이 (13건 중 10건 표시): "
    "2023-02-17 30.78% (196,452,737주) (접수번호 20230217001569) → "
    "2026-03-05 30.68% (196,506,795주) (접수번호 20260305001006)\n"
    "\n"
    "## 대량보유상황보고서 (2025-03-10)\n"
    "- 공시일: 2025-03-10 (접수번호 20250310000839)\n"
    "- 보유주식등의 수: 직전 196,452,737주 → 이번 196,488,145주\n"
)


def _labels(arm):
    with ablation.use(arm):
        return [u["label"] for u in ablation._citation_units(_EVIDENCE)]


def test_결정적_줄은_인용_단위가_아니다():
    """🔴 `A84` 서버 3회의 `Q-020` 이 결정적 줄 **전문**을 인용 라벨로 달고 나갔다.

    블록 파서가 `##` 로만 쪼개므로 머리글 앞의 결정적 줄들이 한 블록이 되고, 그 줄에
    박힌 접수번호가 그 블록을 인용 가능하게 만든다. 공시가 아닌 것을 근거로 대는 셈이다.
    """
    assert any("결정적" in lb for lb in _labels("A84")), "종전 거동이 안 움직였다는 뜻이다"
    labels = _labels("A86")
    assert labels and not any("결정적" in lb for lb in labels)


def test_그_줄에만_있던_접수번호는_인용_후보에서_빠진다():
    """근거 블록에 없는 공시를 답변이 인용하면 안 된다. 번호는 줄 안에 이미 적혀 있다."""
    with ablation.use("A86"):
        numbers = {u["rcept_no"] for u in ablation._citation_units(_EVIDENCE)}
    assert numbers == {"20250310000839"}


def test_근거_블록의_인용은_그대로다():
    """겨냥 밖을 깎지 않았다는 단정이다."""
    with ablation.use("A86"):
        units = ablation._citation_units(_EVIDENCE)
    assert [u["label"] for u in units] == ["대량보유상황보고서 (2025-03-10)"]


# --- ④ `strip_copied_directive` — 모델에게 한 말이 배달되면 안 된다 --------------

#: `Q-020` r1 의 꼬리를 줄인 것. 모델이 결정적 줄을 통째로 옮겨 적은 모양 그대로다.
_COPIED = (
    "두 보고서 모두 동일한 보유비율을 나타냅니다.\n\n"
    "정리하면 짚은 두 보고서 대조 (2025-03-10 → 2025-05-14, 약 2개월): "
    "보유주식등의 수 196,488,145주 → 196,488,145주 (변동 없음) · "
    "보유비율 30.67% → 30.67% (변동 없음) "
    "(이 값과 증감은 시스템이 위 근거의 각 칸에서 직접 읽고 뺀 것이다. "
    "답변의 수치와 증감 방향은 이 줄을 그대로 쓰고 다시 계산하지 않는다.)"
)


def test_지시문을_뗀다():
    """🔴 서버 3회 × 네 실행에서 `Q-020` 이 3/3 으로 이것을 달고 나갔다."""
    out, n = aggregate.strip_copied_directive(_COPIED)
    assert n == 1
    assert "시스템이" not in out
    assert "그대로 쓰고 다시 계산하지 않는다" not in out


def test_수치와_서술은_남긴다():
    """지우는 것은 지시문뿐이다. 값은 결정적 줄이 계산한 참값이라 지울 이유가 없다."""
    out, _ = aggregate.strip_copied_directive(_COPIED)
    for keep in ("196,488,145주", "30.67%", "변동 없음", "2025-03-10 → 2025-05-14"):
        assert keep in out


@pytest.mark.parametrize("directive", [
    "(이 값은 시스템이 위 근거의 금액 필드에서 직접 계산한 것이다.)",
    "(이 값은 시스템이 위 근거의 비율 필드에서 직접 읽은 것이다. "
    "답변의 해당 수치는 이 값을 그대로 쓴다.)",
    "(이 값은 시스템이 위 근거의 `주요계약체결` 칸에서 직접 읽은 것이다. "
    "답변의 해당 수치는 이 값을 그대로 쓴다.)",
])
def test_지시문_판마다_다_뗀다(directive):
    """`aggregate` 가 쓰는 지시문은 한 판이 아니다. 하나만 잡으면 나머지가 샌다."""
    out, n = aggregate.strip_copied_directive("답은 100,000원이다. " + directive)
    assert n == 1 and "시스템이" not in out
    assert "100,000원" in out


def test_괄호_없는_판도_뗀다():
    """`revision_diff` 는 괄호 없이 문장으로 붙인다."""
    out, n = aggregate.strip_copied_directive(
        "정정 전 100,000원 → 정정 후 200,000원\n"
        "이 값은 시스템이 위 근거의 문면에서 그대로 옮긴 것이다 — 다시 계산하지 않는다.")
    assert n == 1 and "시스템이" not in out
    assert "200,000원" in out


def test_지시문이_없으면_손대지_않는다():
    plain = "답은 **100,000원**입니다.\n\n(근거: 공급계약 체결, 접수번호 20230131800162)"
    out, n = aggregate.strip_copied_directive(plain)
    assert n == 0 and out == plain


def test_A86_은_이_스위치를_켠다():
    assert ablation.ARMS["A86"].strip_copied_directive is True
    assert ablation.ARMS["A84"].strip_copied_directive is False


# --- ⑤ `wide_counterparty_slots` — 한 회사가 두 갈래를 맡는 자리 ----------------

def test_칸이_6에서_8로_넓어진다():
    from argus.agent import pipeline

    with ablation.use("A86"):
        assert pipeline._counterparty_cap() == 6
    with ablation.use("A89"):
        assert pipeline._counterparty_cap() == 8


def test_파트너당_최소_두_건을_준다():
    """🔴 `Q-110` 두산퓨얼셀은 「공급 계약」과 「LTSA」 둘을 맡는다.

    골든 요점5 가 둘을 함께 말하는데 회사당 1건이면 반쪽만 실린다.
    """
    from argus.agent import pipeline

    with ablation.use("A86"):
        assert pipeline._partner_quota(6) == 1
        assert pipeline._partner_quota(3) == 1
    with ablation.use("A89"):
        assert pipeline._partner_quota(6) == 2
        assert pipeline._partner_quota(3) == 2


def test_파트너가_적으면_종전_배분이_이미_더_넓다():
    """두 곳짜리 노드는 이미 2건씩이었다. 넓힘이 그것을 줄이면 안 된다."""
    from argus.agent import pipeline

    for arm in ("A86", "A89"):
        with ablation.use(arm):
            assert pipeline._partner_quota(2) == 2


def test_상한을_8로_둔_근거를_한_자리에_박는다():
    """🔴 9·10·12 는 골든을 더 못 싣고 근거만 길어진다(오프라인 산술).

    근거는 길수록 위험하다 — `ORACLE` 에서 옆 블록을 **치웠더니** 세 문항이 정답이 됐다.
    이 수를 올리려면 그 측정을 다시 해야 한다.
    """
    from argus.agent import pipeline

    assert pipeline.WIDE_COUNTERPARTY_EVIDENCE == 8
    assert pipeline.MAX_COUNTERPARTY_EVIDENCE == 6


def test_기본_인자가_아니라_호출_시점에_푼다():
    """🔴 기본 인자로 두면 import 때 굳어 스위치가 안 닿는다."""
    import inspect

    from argus.agent import pipeline

    sig = inspect.signature(pipeline._gather_counterparty)
    assert sig.parameters["limit"].default is None


# --- ⑥ `roster_no_total` — 여러 공시를 더하지 말라고 못 박는다 -------------------

_LOADED = {
    "한전기술": {"rcept_no": "20230417800192",
              "subject": "2023년도 가동원전 계통설계 기술용역",
              "amount": "25,605,030,000원"},
    "현대건설": {"rcept_no": "20231211800339",
              "subject": "신한울 3,4호기 주설비공사",
              "amount": "1,715,754,987,100원"},
}


def _roster(arm, loaded=None):
    from argus.agent import pipeline

    with ablation.use(arm):
        return pipeline._roster_line("한국수력원자력(주)", ["한전기술", "현대건설"],
                                     _LOADED if loaded is None else loaded)


def test_합치지_말라를_붙인다():
    """🔴 칸을 8로 넓히자 모델이 한 회사의 여러 공시를 스스로 더했다.

    `Q-110` A89 r1 이 한전기술 3건을 합해 1,080,964,550,138원 을 냈고, 그것은
    **진짜 합(1,081,829,074,942)과도 다른 수**였다. 대표 계약 행이 이미 있으므로
    더할 이유가 없다는 것을 문면으로 말한다.
    """
    for arm in ("A90", "A91"):
        assert "합계는 내지 않는다" in _roster(arm)
    assert "합계는 내지 않는다" not in _roster("A89")


def test_다듬은_문면은_인용_수를_안_건드린다():
    """🔴 `A90` 의 앞절이 「한 건만 적으라」로도 읽혔다.

    `Q-114` 가 삼성중공업 두 번째 공시(20231218800177)를 3회 중 2회 안 적어
    근거 표시(골든 일치)가 0·0·0 → 1·0·1 이 됐다. 겨냥은 합계 금지 하나뿐이다.
    """
    assert "위 대표 계약을 적고" in _roster("A90")
    assert "위 대표 계약을 적고" not in _roster("A91")
    assert "한 회사의 여러 공시를 더한 합계는 내지 않는다" in _roster("A91")


def test_두_문면은_배타다():
    """둘 다 켜면 `A90` 문면이 이긴다 — 그 자리를 시험으로 못 박는다."""
    from dataclasses import replace

    both = replace(ablation.ARMS["A91"], name="BOTH", roster_no_total=True)
    with ablation.use(both):
        from argus.agent import pipeline

        out = pipeline._roster_line("한국수력원자력(주)", ["한전기술", "현대건설"], _LOADED)
    assert "위 대표 계약을 적고" in out


@pytest.mark.parametrize("arm", ["A90", "A91"])
def test_대표_계약_행이_없으면_안_붙인다(arm):
    """할 말의 전제가 **위 행**이다. 그 행이 없으면 가리킬 것이 없다."""
    out = _roster(arm, loaded={})
    assert "합계는 내지 않는다" not in out
    assert "빠짐없이 적고 임의로 줄이지 않는다" in out, "종전 문장은 남는다"


@pytest.mark.parametrize("arm", ["A90", "A91"])
def test_종전_문장을_안_지운다(arm):
    """🔴 덧붙이는 것이지 갈아 끼우는 것이 아니다. 열거 지시가 사라지면 안 된다."""
    assert "빠짐없이 적고 임의로 줄이지 않는다" in _roster(arm)


def test_부모가_꺼지면_세울_수_없다():
    from dataclasses import replace

    for flag in ("roster_no_total", "roster_sum_ban"):
        with pytest.raises(ValueError, match=flag):
            replace(ablation.ARMS["A0"], name="TEST", **{flag: True})


# --- ⑦ `quiet_verify_note` — 각주를 배달되는 문면에 안 싣는다 --------------------

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
