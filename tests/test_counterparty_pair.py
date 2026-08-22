r"""`counterparty_pair` — 계약상대가 같은 두 회사의 공시를 잇고 금액 차를 낸다.

## 무엇을 지키는 시험인가

`Q-112`(*"두 공시가 어떤 관계인지 설명하라"*)에는 결정적 줄이 **하나도** 안 붙었다.
골든 요점3 이 *"약 4,111억원 더 크다"* 를 요구하는데 답변은 세 판 모두 두 금액을 나란히
적고 차를 계산하지 않는다. `line`(`_op`) 의 차 연산이 안 걸린 것은 질문이 「차이는
얼마인가」가 아니라 「어떤 관계인가」이기 때문이다.

아래 시험은 세 가지를 지킨다: 억 단위 내림이 골든과 맞는가, 두 표기를 둘 다 적는가,
그리고 **모호할 때 침묵하는가**.
"""
import pytest

from argus import ablation
from argus.agent import aggregate

ASK = ("대전 도안 2-2지구 공동주택 신축공사는 2023년 12월 대우건설이 계약 해지를 "
       "공시했고, 2024년 4월 현대건설이 같은 사업의 계약 체결을 공시했다. "
       "두 공시가 어떤 관계인지 설명하라.")


def _record(rcept, ymd, corp, amount, counterparty):
    return {"rcept_no": rcept, "rcept_dt": ymd, "corp_name": corp,
            "amount": amount, "counterparty": counterparty}


#: `Q-112` 근거의 7건 중 값이 있는 것들. 코퍼스에서 그대로 옮겼다.
Q112 = [
    _record("20231211800339", "20231211", "현대건설", 1715754987100, "한국수력원자력 주식회사"),
    _record("20231218800101", "20231218", "현대건설", 1534560268460, "ARAMCO"),
    _record("20231214801332", "20231214", "대우건설", 1147665603998, "주식회사 유토개발2차"),
    _record("20231207800431", "20231207", "대우건설", 790860000000, "한남2재정비촉진구역 조합"),
    _record("20240415800078", "20240415", "현대건설", 1558826903000, "주식회사유토개발2차"),
    _record("20240418800570", "20240418", "현대건설", 469344351611, "MPA"),
]


#: 🔴 `None` 을 「기본값」으로 쓰면 `line(None)` 이 기본 레코드를 넘겨 버린다. 파수꾼을 둔다.
_DEFAULT = object()


def line(records=_DEFAULT, question=ASK, arm="A73"):
    with ablation.use(arm):
        return aggregate.counterparty_pair(
            Q112 if records is _DEFAULT else records, question)


def test_골든의_차액을_억_단위로_낸다():
    """1,558,826,903,000 − 1,147,665,603,998 = 411,161,299,002 → 약 4,111억원."""
    out = line()
    #: 🔴 다섯 꼴을 재서 이것으로 정했다. 독스트링의 실측표를 보라 — 꼴 사이의 차이보다
    #: 같은 꼴의 표본 간 분산이 커서, 마지막에는 원칙으로 골랐다(한 줄 · 두 단위 다 실음).
    assert "411,161,299,002원 더 크다 — 억 단위로는 약 4,111억원이다." in out
    assert "현대건설 쪽이" in out, "어느 쪽이 큰지 말해야 한다"


def test_두_표기를_둘_다_적는다():
    """골든 요점4 — 두 공시를 잇는 것은 계약상대 필드뿐이고 띄어쓰기만 다르다.

    답변이 무엇으로 이었는지 말할 수 있어야 하므로 한쪽만 적으면 안 된다.
    """
    out = line()
    assert "'주식회사 유토개발2차'" in out and "'주식회사유토개발2차'" in out
    assert "공백만 다르고" in out


def test_날짜와_접수번호는_공시일_기준이다():
    """골든은 2023-12-14 · 2024-04-15 다. 답변이 쓴 확정일(2023-12-13)이 아니다."""
    out = line()
    assert "2023-12-14" in out and "20231214801332" in out
    assert "2024-04-15" in out and "20240415800078" in out


def test_한_줄이다():
    """등식을 별개 줄로 떼 봤으나 더 나빴다(원 1/3 · 억원 0/3). 한 줄로 유지한다."""
    assert len(line().splitlines()) == 1


def test_시간순으로_잇는다():
    """레코드 순서를 뒤집어도 해지가 먼저 온다."""
    assert line(list(reversed(Q112))) == line()


@pytest.mark.parametrize("question", [
    "한국항공우주산업㈜을 계약상대로 하는 단일판매·공급계약을 공시한 상장사를 빠짐없이 알려줘.",
    "서부광역메트로 주식회사를 계약상대로 하는 단일판매·공급계약을 공시한 상장사는 어디인가?",
])
def test_열거_문항에는_안_든다(question):
    """보류셋 `H-011`·`N-014` 실측 — 꼴은 걸리지만 차액이 답이 아니라 잡음이다."""
    assert line(question=question) == ""


def test_공시_주체가_같으면_침묵한다():
    """한 회사가 같은 상대와 두 번 계약한 것은 시공사가 바뀐 것이 아니다."""
    same = [dict(r, corp_name="현대건설") for r in Q112]
    assert line(same) == ""


def test_한_상대에_세_건이면_침묵한다():
    """「차」가 어느 둘의 것인지 모른다."""
    third = Q112 + [_record("20250101800001", "20250101", "GS건설",
                            2000000000000, "주식회사  유토개발2차")]
    assert line(third) == ""


def test_쌍이_둘이면_침묵한다():
    """어느 쌍의 차인지 모른다."""
    two = Q112 + [
        _record("20250101800001", "20250101", "GS건설", 500000000000, "ARAMCO"),
    ]
    #: `ARAMCO` 가 현대건설·GS건설 두 건이 되어 쌍이 둘이다.
    assert line(two) == ""


def test_금액이_같으면_침묵한다():
    """「더 크다」가 거짓이 된다."""
    flat = [dict(r, amount=1147665603998) if r["counterparty"].startswith("주식회사")
            else r for r in Q112]
    assert line(flat) == ""


def test_금액이_없는_레코드는_쌍이_안_된다():
    stripped = [dict(r, amount=None) if r["rcept_no"] == "20240415800078" else r
                for r in Q112]
    assert line(stripped) == ""


@pytest.mark.parametrize("arm", ["A65", "A71", "A72"])
def test_스위치가_꺼진_팔은_침묵한다(arm):
    assert line(arm=arm) == ""


def test_레코드가_없으면_침묵한다():
    assert line([]) == ""
    assert line(None) == ""
