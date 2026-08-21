"""P-A① · `aggregate_items` — 결정적 집계 줄이 가산 항목을 밝힌다.

계획서 `docs/superpowers/plans/2026-08-21-paper-grounded-repairs.md` 의 P-A, 팔 `A59`.

## 무엇이 문제였나

합계 줄은 총액과 피가수의 **값**만 실었다. *어느 공시가 그 피가수인지*는 안 실었다.
`Q-115` 서버 3회가 전부 같은 모양이다.

    결정적 집계   위 근거 2건의 금액 합계 (2025년 공시분 · 계약상대 현대자동차):
                 106,796,802,000 + 1,652,800,000,000 = 1,759,596,802,000원
    답변          계약 넷을 열거하고 → = 1,759,596,802,000원

**총액은 우리 줄에서 그대로 베끼고 가산 항목은 자기가 다시 골랐다.** 「2025년 공시분 ·
계약상대 현대자동차」 조건을 근거 블록 다섯에 처음부터 다시 적용해야 했고 틀렸다. 답변
안에서 열거와 합계가 안 맞으므로 심사자가 네 수를 더해 보면 그것이 보인다.

## 근거는 논문이다

Graph of Thoughts — CoT 는 사슬이고 ToT 는 트리이며 **둘 다 여러 갈래를 합치는 연산이
없다**(`paper/idea/idea.md` §3.1). 우리는 병합을 이미 결정적으로 했으므로 새로 계산할 것이
없고 **한 것을 보여 주기만** 하면 된다.

재생성으로는 못 고친다. `Q-115` 는 3회 모두 재생성이 돌고 3회 모두 실패 종류가 그대로라
버려졌다(`kept=original`). 판단이 생성보다 쉽다는 SWE-Lancer 의 관측과 같은 방향이다.

## 픽스처

레코드는 지어낸 딕트가 아니라 **`_load_records()` 가 내는 실제 레코드**를 쓴다.
`period_label` 첫 판이 픽스처를 다른 딕트 모양으로 만들어 조용한 무동작인데도 초록이었다.
"""
import pytest

from argus import ablation
from argus.agent import aggregate

#: `Q-115` 가 실제로 합산하는 두 공시.
ERP = "20251128800125"
SHIP = "20250117800487"


@pytest.fixture(scope="module")
def rows():
    """🔴 런타임이 내는 레코드 모양 그대로. 손으로 만든 딕트를 쓰지 않는다."""
    from scripts import error_analysis as ea

    records = ea._load_records()
    picked = [records.get(ERP), records.get(SHIP)]
    assert all(picked), "두 접수번호가 레코드에 있다"
    assert all("title" in r and "amount" in r and "corp_name" in r for r in picked), \
        "명세가 읽는 세 필드가 런타임 레코드에 있다"
    return picked


def test_off_emits_nothing(rows):
    """플래그가 꺼지면 빈 문자열이라 합계 줄이 종전과 글자 하나 다르지 않다."""
    with ablation.use("A56"):
        assert aggregate.summand_roster(rows) == ""


def test_on_names_every_summand(rows):
    with ablation.use("A59"):
        out = aggregate.summand_roster(rows)
    assert out, "🔴 빈 문자열이면 조용한 무동작이다"
    for record in rows:
        assert record["rcept_no"] in out, "항목마다 접수번호를 나른다"
        assert f"{record['amount']:,}원" in out, "항목마다 금액을 나른다"
        assert record["corp_name"] in out
    assert "가산 대상은 위 2건뿐이다" in out


def test_the_roster_carries_the_contract_title(rows):
    """회사명만으로는 어느 계약인지 못 가린다.

    `Q-115` 의 근거에는 현대글로비스의 해상운송계약이 여러 건 실린다. 답변이 그중
    엉뚱한 것을 골랐으므로 계약명이 없으면 명세가 문제를 안 고친다.
    """
    with ablation.use("A59"):
        out = aggregate.summand_roster(rows)
    assert "완성차 해상운송계약" in out
    assert "차세대 ERP" in out


def test_the_sum_line_keeps_its_total(rows):
    """명세를 붙여도 합계 줄의 총액은 안 건드린다.

    `Q-115` 의 총액은 이미 맞다(골든과 일치한다). 틀린 것은 답변의 열거이므로 총액을
    다시 계산하면 답을 더 나쁘게 만든다.
    """
    with ablation.use("A59"):
        out = aggregate.summand_roster(rows)
    total = sum(r["amount"] for r in rows)
    assert f"{total:,}" not in out, "명세는 총액을 다시 적지 않는다 — 합계 줄이 적는다"


def test_the_flag_needs_its_parent():
    """부모가 꺼지면 `pipeline` 이 `aggregate.line` 을 아예 안 부른다."""
    from dataclasses import replace

    with pytest.raises(ValueError, match="aggregate_items"):
        replace(ablation.ARMS["A0"], name="TEST", aggregate_items=True)


def test_the_line_changes_for_the_sum_operation_only():
    """`합` 연산만 명세를 받는다.

    실측(서버 3회 · 112문항): `[결정적 집계]` 는 15문항에 실리는데 `A59` 가 근거를 바꾸는
    것은 **4문항**이다 — `Q-034` `Q-107` `Q-109` `Q-115`. 나머지는 비교·차·몫 연산이고
    그 줄들은 이미 회사명을 적는다. 사정거리를 시험에 못 박아 둔다.
    """
    with ablation.use("A59"):
        assert aggregate.summand_roster([]) != ""      # 빈 목록도 꼬리를 낸다
    #: 🔴 `line()` 을 통과시키는 시험은 레코드 집합과 라우터가 필요해 여기 두지 않는다.
    #: 사정거리 4문항은 `scratch/gate_a59.py` 가 실행으로 확인한다.
