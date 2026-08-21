"""P-C · `refusal_needs_gap` — 거절 판정에 외부 신호를 곱한다.

계획서 `docs/superpowers/plans/2026-08-21-paper-grounded-repairs.md` 의 P-C, 팔 `A57`.

## 무엇이 문제였나

`attach_citation` 은 답변 문면에 `_CITE_REFUSAL` 의 어구가 있고 강한 연결(접수번호·금액)이
없으면 **거절**로 보고 표기를 하나도 안 붙인다. 그 규칙은 옳다 — 거절에 근거를 달면 없는
출처를 지목하는 것이다.

그런데 두 가지가 같은 어휘를 쓴다.

    답변   "이 보고서에는 그 항목이 없다"      질문에 답한 것이다
    거절   "답을 못 찾겠다"                   답하기를 그만둔 것이다

`Q-053` 이 앞쪽인데 뒤쪽으로 판정됐다. 서버 3회 모두 접수번호가 하나도 안 붙었고,
그것이 「근거 표시(표기 유무) 92/93」의 그 하나다.

## 근거는 논문이다

Self-Consistency 는 모델의 정규화 조건확률이 정답/오답 간 거의 차이가 없다고 보고한다
(`paper/base/base.md` §3). 그래서 정보한계 판정을 **모델 출력에 맡기면 안 되고 외부
신호를 써야 한다.** 여기서 외부 신호는 근거다 — 질문이 이름을 부른 섹션이 실려 있으면
그 답변은 그것을 읽고 쓴 것이다.

🔴 어구 목록을 넓히는 쪽은 v4 에서 측정 후 기각했다. 목록은 한 글자도 안 건드린다.

## 픽스처

여기 쓰는 근거와 답변은 지어낸 것이 아니라 **`A56` 서버 실행이 실제로 낸 것**이다
(`scratch/ablation/v6/server_full/A56/responses/Q-053.json`). 그리고 이 시험은 픽스처가
런타임과 **같은 모양**인지를 스스로 확인한다 — `period_label` 첫 판이 픽스처를 다른 딕트
모양으로 만들어 조용한 무동작인데도 초록이었다.
"""
import pytest

from argus import ablation

ON = ablation.ARMS["A57"]
OFF = ablation.ARMS["A56"]

QUESTION = ("삼성전자 2026년 1분기 분기보고서에서 'V. 회계감사인의 감사의견 등 > "
            "2. 내부통제에 관한 사항' 에 어떤 내용이 기재돼 있는지 근거와 함께 설명해줘.")

#: `Q-053` 근거의 앞 두 블록. 접수번호가 같은 한 보고서의 두 섹션이라 `_citation_units`
#: 가 하나로 묶고 `tails` 에 경로 둘을 담는다 — 그 모양이 이 처방의 입력이다.
EVIDENCE = """## [분기보고서 2026 1분기] 삼성전자 – V. 회계감사인의 감사의견 등 > 2. 내부통제에 관한 사항
- 출처: 접수번호 20260515002181 (공시일 20260515)

2. 내부통제에 관한 사항

내부통제에 관한 사항은 기업공시서식 작성기준에 따라 분기보고서에 기재하지 않습니다.(반기ㆍ사업보고서에 기재 예정)

## [분기보고서 2026 1분기] 삼성전자 – V. 회계감사인의 감사의견 등 > 1. 외부감사에 관한 사항
- 출처: 접수번호 20260515002181 (공시일 20260515)

1. 외부감사에 관한 사항

가. 회계감사인의 감사의견 등
삼정회계법인은 당사의 제56기 및 제57기 감사를 수행하였다.
"""

#: 실제 답변. 마지막 문장의 `찾을 수 없으며` 가 거절 판정을 일으킨 낱말이다.
ANSWER = (
    "삼성전자 2026년 1분기 분기보고서의 'V. 회계검사인의 감사의견 등 > 2. 내부통제에 관한 "
    "사항' 섹션에는 다음과 같은 내용이 기재되어 있습니다:\n\n"
    "- 내부통제에 관한 사항은 기업공시서식 작성기준에 따라 분기보고서에 기재하지 않는다는 "
    "내용이 포함되어 있습니다. 이는 반기 또는 사업보고서에 기재될 예정입니다.\n\n"
    "따라서, 해당 섹션에서는 내부통제에 관한 구체적인 내용을 찾을 수 없으며, 대신 "
    "분기보고서에서는 이를 다루지 않고 반기 혹은 사업보고서에서 다룰 것임을 명시하고 "
    "있습니다.")


def test_fixture_has_the_runtime_shape():
    """🔴 픽스처가 런타임과 같은 모양인지 시험이 먼저 확인한다.

    `_question_names_loaded_section` 은 `_citation_units` 의 `tails` 를 읽는다. 머리글
    형식이 바뀌어 `tails` 가 비면 이 처방은 조용히 무동작이 되는데, 그때 이 단정이
    먼저 깨져야 한다. 처방을 검사하는 단정만 있으면 무동작을 통과로 읽는다.
    """
    units = ablation._citation_units(EVIDENCE, None)
    assert len(units) == 1, "접수번호가 같은 두 섹션은 한 인용으로 묶인다"
    assert units[0]["rcept_no"] == "20260515002181"
    assert "V. 회계감사인의 감사의견 등 > 2. 내부통제에 관한 사항" in units[0]["tails"]
    assert ANSWER.count("찾을 수 없") == 1, "거절 낱말이 픽스처에 실제로 있다"
    assert "20260515002181" not in ANSWER, "답변은 접수번호를 스스로 적지 않았다"


def test_off_keeps_treating_it_as_a_refusal():
    """`A56` 의 동작. 이 시험이 초록인 동안은 기준선이 안 움직였다는 뜻이다."""
    out, cited = ablation.attach_citation(ANSWER, EVIDENCE, arm=OFF, question=QUESTION)
    assert cited == []
    assert "20260515002181" not in out


def test_on_attaches_because_the_question_named_a_loaded_section():
    out, cited = ablation.attach_citation(ANSWER, EVIDENCE, arm=ON, question=QUESTION)
    assert cited == ["20260515002181"]
    assert "20260515002181" in out
    assert "근거" in out


def test_on_without_the_question_changes_nothing():
    """질문을 못 받으면 종전 동작으로 돌아간다.

    `attach_citation` 을 질문 없이 부르는 자리가 시험에 33곳 있다. 기본값이 종전
    동작이라야 그 자리들이 이 처방 때문에 뜻이 바뀌지 않는다.
    """
    out, cited = ablation.attach_citation(ANSWER, EVIDENCE, arm=ON)
    assert cited == []
    assert "20260515002181" not in out


def test_on_still_refuses_when_the_question_names_nothing_loaded():
    """진짜 거절은 그대로 거절이다.

    실측(`scratch/w1_risk_sizing.py`): 지금 거절로 판정되는 18문항 중 안전성 10 ·
    정보한계 6 은 이 처방이 건드리지 않는다. 그것이 이 단정의 내용이다.
    """
    other = "삼성전자의 2026년 배당 정책은 무엇인가?"
    out, cited = ablation.attach_citation(ANSWER, EVIDENCE, arm=ON, question=other)
    assert cited == []
    assert "20260515002181" not in out


@pytest.mark.parametrize("segment", ["개요", "현황", "요약"])
def test_short_segments_are_not_a_signal(segment):
    """흔한 낱말로는 판정하지 않는다.

    `개요` 는 수백 건의 공시에 있다. 그것이 질문에 있다는 것만으로 거절을 뒤집으면
    이 처방은 어구 목록을 넓힌 것과 같아진다 — v4 에서 기각한 그 길이다.
    """
    evidence = EVIDENCE.replace("2. 내부통제에 관한 사항", f"2. {segment}")
    question = f"삼성전자 분기보고서의 {segment} 에 무엇이 있는가?"
    out, cited = ablation.attach_citation(ANSWER, evidence, arm=ON, question=question)
    assert cited == []
    assert "20260515002181" not in out


def test_the_flag_needs_its_parent():
    """부모가 꺼지면 코드가 그 분기에 못 닿으므로 `__post_init__` 이 막아야 한다."""
    from dataclasses import replace

    with pytest.raises(ValueError, match="refusal_needs_gap"):
        replace(ablation.ARMS["A0"], name="TEST", refusal_needs_gap=True)


# --- 같은 섹션이 여러 해에 실릴 때 ---------------------------------------------
#
# `Q-053` 의 실제 근거에는 2026년 1분기와 **2023년 1분기**의 같은 섹션이 둘 다 실린다.
# 답변은 2026 만 말하므로 2023 을 근거로 다는 것은 거짓 귀속이다. 좁히기 전 실측
# (`scratch/verify_a57_offline.py`): 표기가 `['20260515002181', '20230515002335']` 로
# 둘 붙었다. 선택이 `titled`(제목 낱말 겹침)로 흘렀기 때문이고, 그 겹침은 모든 블록에
# 회사명이 있어 거의 항상 맞는다.
EVIDENCE_TWO_YEARS = EVIDENCE + """
## [분기보고서 2023 1분기] 삼성전자 – V. 회계감사인의 감사의견 등 > 2. 내부통제에 관한 사항
- 출처: 접수번호 20230515002335 (공시일 20230515)

2. 내부통제에 관한 사항

내부통제에 관한 사항은 기업공시서식 작성기준에 따라 분기보고서에 기재하지 않습니다.
"""


def test_two_years_of_the_same_section_cite_only_the_asked_year():
    units = ablation._citation_units(EVIDENCE_TWO_YEARS, None)
    assert {u["rcept_no"] for u in units} == {"20260515002181", "20230515002335"}, \
        "픽스처가 같은 섹션의 두 해를 담고 있다"

    out, cited = ablation.attach_citation(ANSWER, EVIDENCE_TWO_YEARS, arm=ON,
                                         question=QUESTION)
    assert cited == ["20260515002181"], "질문이 말한 해만 붙는다"
    assert "20230515002335" not in out


def test_a_question_with_no_year_keeps_both():
    """연도를 안 물었으면 좁히지 않는다.

    좁혀서 빈 손이 되면 표기가 아예 안 붙어 종전 동작으로 돌아가는데, 그것은 이 처방이
    겨냥한 것과 반대다. 판정할 수 없는 것을 침묵으로 처리하지 않는다.
    """
    question = "삼성전자 분기보고서의 '내부통제에 관한 사항' 에 무엇이 기재돼 있는가?"
    _out, cited = ablation.attach_citation(ANSWER, EVIDENCE_TWO_YEARS, arm=ON,
                                          question=question)
    assert set(cited) == {"20260515002181", "20230515002335"}
