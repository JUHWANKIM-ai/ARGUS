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


# --- 꼬리가 자기를 반박하지 않게 한다 ------------------------------------------
#
# 꼬리는 수리 **전에** 만들어진다. 본문에서 뺀 금액을 검증기가 결함으로 적어 두면 답변이
# 자기를 반박한다. `substitute` 가 쓰는 `_prune_notes` 를 그대로 쓴다.
#
# 🔴 머리글이 두 가지다 — 대체가 일어났으면 "재계산값으로 정정했습니다", 아니면 "결함이
# 발견되었습니다"(`pipeline.py:3186`). 첫 판이 뒤쪽만 받아서 `Q-107` 의 꼬리를 못 다듬었다.
SUBSTITUTED_TAIL = ("\n\n(주의: 자동 검증에서 결함이 발견되어 재계산값으로 정정했습니다 — "
                    "amount: 답변 890,200,000,000원 vs 재계산 12,345 (10^+1 배 자릿수 오류))")
PLAIN_TAIL = ("\n\n(주의: 자동 검증에서 결함이 발견되었습니다 — "
              "amount: 답변 890,200,000,000원 vs 재계산 12,345)")


@pytest.mark.parametrize("tail", [SUBSTITUTED_TAIL, PLAIN_TAIL])
def test_a_note_about_a_removed_figure_is_dropped(tail):
    with ablation.use("A61"):
        out, why = aggregate.fix_stray_summands(STRAY + tail, EVIDENCE)
    assert "890,200,000,000" not in out, "본문에서 뺀 금액이 꼬리에도 안 남는다"
    assert "note=1" in why


def test_a_note_about_a_figure_we_kept_survives():
    """해소되지 않은 결함을 지우면 관측을 잃는다. `substitute` 와 같은 규칙이다."""
    tail = ("\n\n(주의: 자동 검증에서 결함이 발견되었습니다 — "
            "provenance: 답변 1,652,800,000,000원 이 근거에 없다)")
    with ablation.use("A61"):
        out, why = aggregate.fix_stray_summands(STRAY + tail, EVIDENCE)
    assert "provenance" in out, "우리가 안 건드린 사유는 남는다"
    assert "note=0" in why


# --- 🔴 총액을 항목으로 착각해 지우던 결함 (2026-08-22 실측) -------------------
#
# `_item_amount` 는 `-`·`·`·`*`·`N.`·`N)` 로 시작하는 줄을 열거 항목으로 본다. 답변이
# 결론을 목록으로 쓰면(`3. 합계: 1,759,596,802,000원`) 그 줄도 항목이 되고, 총액은
# 피가수 집합에 없으므로 「어긋난 항목」으로 판정돼 **통째로 사라졌다.**
#
# `Q-115` r3 실측: `substitute` 가 정답을 넣었는데(`substitute=996,996,802,000 ->
# 1,759,596,802,000`) 이 함수가 그것을 지웠다. 같은 회차의 다른 실행은 결론을 평문으로
# 써서 살아남았다 — **답이 목록이냐 문장이냐 하는 문체 하나로 정답이 갈렸다.**
#
# 이 함수는 총액이 본문에 있을 때만 발화한다. 그 총액을 스스로 지우는 것은 자기 전제를
# 무너뜨리는 일이다.

_TOTAL = "1,759,596,802,000"


@pytest.mark.parametrize("shape,body", [
    ("번호 목록",
     "1. 현대오토에버\n"
     "   - 차세대 ERP 106,796,802,000원 (접수번호 20251128800125)\n"
     "2. 현대글로비스\n"
     "   - 완성차 해상운송계약 1,652,800,000,000원 (접수번호 20250117800487)\n"
     "3. 합계: %s원" % _TOTAL),
    ("글머리 목록",
     "- 차세대 ERP 106,796,802,000원\n"
     "- 완성차 해상운송계약 1,652,800,000,000원\n"
     "- 합계 %s원" % _TOTAL),
    ("평문",
     "- 차세대 ERP 106,796,802,000원\n"
     "- 완성차 해상운송계약 1,652,800,000,000원\n\n"
     "따라서 합계는 %s원입니다." % _TOTAL),
])
def test_총액은_어떤_문체에서도_안_지워진다(shape, body):
    with ablation.use("A81"):
        out, why = aggregate.fix_stray_summands(body, EVIDENCE)
    assert _TOTAL in out, f"{shape}: 총액이 사라졌다"
    assert why == "", f"{shape}: 열거가 명세와 같은데 손댔다"


def test_어긋난_피가수는_여전히_갈아_끼운다():
    """총액을 지키는 것이 이 함수를 무력화하는 것은 아니다."""
    body = ("- 차세대 ERP 106,796,802,000원\n"
            "- 완성차 해상운송계약 890,200,000,000원\n"
            "- 합계 %s원" % _TOTAL)
    with ablation.use("A81"):
        out, why = aggregate.fix_stray_summands(body, EVIDENCE)
    assert "swapped=1" in why
    assert "890,200,000,000" not in out, "어긋난 피가수가 남았다"
    assert "1,652,800,000,000" in out, "명세 항목으로 안 갈렸다"
    assert _TOTAL in out, "총액이 사라졌다"
