"""`sum_operand_fix` — 답변의 열거를 결정적 명세로 갈아 끼운다 (팔 `A61`).

계획서 `docs/superpowers/plans/2026-08-21-paper-grounded-repairs.md` 에 없던 스위치다.
`A59` 3회 측정이 세웠다.

## 왜 이것이 필요했나

`A59`(`aggregate_items`)는 근거에 **가산 항목 명세**를 실었다. 그것으로 안 됐다 — 명세를
절반만 따른다. `Q-115` 3회 실측: ERP 항목은 접수번호까지 그대로 옮기고 hCloud 는
떨어뜨렸는데, 근거에 `완성차 해상운송계약` 이라는 **같은 계약명이 여러 건** 실려 있어
명세가 접수번호로 지목한 것 대신 다른 것을 골랐다.

**근거에 정답을 놓아 주는 것으로는 안 된다.** SWE-Lancer 의 판단 45% vs 생성 26%, QA.pdf 의
*"정답 근거가 있는데도 자신 있게 틀리는 것"* 이 같은 자리를 가리킨다.

## `repair_gap` 과 방향이 반대다

`repair_gap` 은 결정적 줄의 숫자 중 **절반 미만**이 답변에 있을 때 모자란 것을 붙인다.
`Q-115` 는 셋 중 둘(피가수 하나와 총액)이 있어 그 검사를 통과한다 — **없어야 할 금액이 더
들어 있는** 결함이라 그 짝이 필요하다.

## 수리 방향이 정해지는 이유

**총액은 맞고 피가수가 틀렸다.** 총액은 우리가 계산해 명세에 실은 값이고 답변이 그것을
베꼈다. 눈감고 재계산하면(피가수를 더해 총액을 갈면) 맞는 답을 틀리게 만든다. 갈아야
하는 쪽은 피가수이고 옳은 집합을 명세가 이미 안다.

## 사정거리 (`A59` 산출물 4문항 × 3회 · `A56` 저장 답변 336개)

    Q-107   3/3 발화 — NSSS 계약을 490,881,723,100원 으로 적었다. 실제는 4,928,968,079,043원
    Q-115   3/3 발화 — 명세 밖 공시 둘을 열거했다
    Q-034   발화 안 함 (열거가 명세와 일치)
    Q-109   발화 안 함
    A56 336개  발화 0 — 명세가 없으면 읽을 것이 없다
"""
import pytest

from argus import ablation
from argus.agent import aggregate

#: 🔴 `summand_roster` 가 쓰는 문면 그대로. 이 모양이 깨지면 `_roster_of` 가 빈 목록을
#: 돌려주고 처방은 조용한 무동작이 된다.
EVIDENCE = (
    "- [결정적 집계] 위 근거 2건의 금액 합계 (2025년 공시분 · 계약상대 현대자동차): "
    "106,796,802,000 + 1,652,800,000,000 = **1,759,596,802,000원** (이 값은 …)\n"
    "  · 현대오토에버 차세대 ERP 아태 및 유럽 롤인 프로젝트 계약 — 106,796,802,000원 "
    "(접수번호 20251128800125)\n"
    "  · 현대글로비스 완성차 해상운송계약 — 1,652,800,000,000원 (접수번호 20250117800487)\n"
    "  (가산 대상은 위 2건뿐이다. …)\n")

#: `Q-115` r1 의 실제 답변 모양.
STRAY = (
    "1. 현대오토에버의 경우\n"
    "- 차세대 ERP 아태 및 유럽 롤인 프로젝트 계약: 106,796,802,000원 "
    "(접수번호 20251128800125)\n\n"
    "2. 현대글로비스의 경우\n"
    "- 완성차 해상운송계약: 890,200,000,000원 (접수번호 20250227800164)\n"
    "- 완성차 해상운송계약: 1,345,900,000,000원 (접수번호 20250117800494)\n\n"
    "따라서 세 건의 금액을 합하면 다음과 같습니다.\n"
    "- 106,796,802,000 + 890,200,000,000 + 1,345,900,000,000 = **1,759,596,802,000원**")


def test_the_fixture_parses_as_a_roster():
    """🔴 픽스처가 런타임과 같은 모양인지 시험이 먼저 확인한다."""
    roster = aggregate._roster_of(EVIDENCE)
    assert len(roster) == 2
    assert {r["rcept"] for r in roster} == {"20251128800125", "20250117800487"}
    assert {r["amount"] for r in roster} == {"106,796,802,000", "1,652,800,000,000"}


def test_off_changes_nothing():
    with ablation.use("A59"):
        out, why = aggregate.fix_stray_summands(STRAY, EVIDENCE)
    assert why == ""
    assert out == STRAY


def test_on_replaces_the_stray_items_and_the_equation():
    with ablation.use("A61"):
        out, why = aggregate.fix_stray_summands(STRAY, EVIDENCE)
    assert why, "🔴 발화하지 않으면 조용한 무동작이다"
    assert "890,200,000,000" not in out, "명세 밖 금액이 사라진다"
    assert "1,345,900,000,000" not in out
    assert "1,652,800,000,000" in out, "명세의 금액이 들어온다"
    assert "20250117800487" in out, "명세의 접수번호가 들어온다"
    assert "106,796,802,000 + 1,652,800,000,000 = **1,759,596,802,000원**" in out


def test_the_total_is_never_recomputed():
    """총액은 우리가 계산해 명세에 실은 값이고 답변이 베꼈다. 갈면 맞는 답이 틀려진다."""
    with ablation.use("A61"):
        out, _why = aggregate.fix_stray_summands(STRAY, EVIDENCE)
    assert out.count("1,759,596,802,000") == STRAY.count("1,759,596,802,000")


def test_the_count_word_follows_the_roster():
    """항목을 뺐으면 개수를 말하는 문장도 같이 틀린다. 띄어쓰기를 살린다."""
    with ablation.use("A61"):
        out, why = aggregate.fix_stray_summands(STRAY, EVIDENCE)
    assert "두 건의 금액을 합하면" in out
    assert "세 건" not in out
    assert "count=1" in why


def test_a_count_outside_a_summation_sentence_is_left_alone():
    """사정거리를 합산을 말하는 줄로 묶었다.

    본문 아무 곳의 `N건` 을 갈면 무관한 개수를 망친다. 이 단정이 그 경계를 지킨다.
    """
    answer = STRAY + "\n\n한편 이 계약은 2025년에 세 건이 정정됐다."
    with ablation.use("A61"):
        out, _why = aggregate.fix_stray_summands(answer, EVIDENCE)
    assert "세 건이 정정됐다" in out, "합산 문장이 아닌 개수는 안 건드린다"


def test_an_answer_that_matches_the_roster_is_untouched():
    answer = ("- 차세대 ERP: 106,796,802,000원\n"
              "- 완성차 해상운송계약: 1,652,800,000,000원\n"
              "- 합계 106,796,802,000 + 1,652,800,000,000 = **1,759,596,802,000원**")
    with ablation.use("A61"):
        out, why = aggregate.fix_stray_summands(answer, EVIDENCE)
    assert why == ""
    assert out == answer


def test_an_answer_without_our_total_is_untouched():
    """우리 총액을 안 쓴 답변은 이 결함이 아니다. 손대면 다른 계산을 망친다."""
    answer = STRAY.replace("1,759,596,802,000", "2,342,896,802,000")
    with ablation.use("A61"):
        out, why = aggregate.fix_stray_summands(answer, EVIDENCE)
    assert why == ""
    assert out == answer


def test_no_roster_means_no_action():
    """명세가 없으면 읽을 것이 없다. `A56` 저장 답변 336개에서 발화 0 을 실측했다."""
    bare = EVIDENCE.split("\n")[0]
    with ablation.use("A61"):
        out, why = aggregate.fix_stray_summands(STRAY, bare)
    assert why == ""
    assert out == STRAY


def test_the_verification_tail_is_preserved():
    """꼬리는 검증기가 관측한 것이라 손대지 않는다 — `substitute` 와 같은 규칙."""
    tail = "\n\n(주의: 자동 검증에서 결함이 발견되었습니다 — amount: …)"
    with ablation.use("A61"):
        out, _why = aggregate.fix_stray_summands(STRAY + tail, EVIDENCE)
    assert out.endswith(tail)


def test_the_flag_needs_its_parent():
    from dataclasses import replace

    with pytest.raises(ValueError, match="sum_operand_fix"):
        replace(ablation.ARMS["A0"], name="TEST", sum_operand_fix=True)
