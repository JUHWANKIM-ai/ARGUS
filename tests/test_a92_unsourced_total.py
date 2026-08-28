r"""`A92` — 근거에 없고 스스로도 안 맞는 총액 문장을 지운다 (2026-08-28).

## 왜 생겼나

미공개셋 `H-005` 는 2024-04-17 공시 **한 건**의 계약상대와 금액을 묻는다. 답변은 골든
요점 둘을 다 적고도, 같은 날 다른 계약을 자진해 덧붙여 총액으로 결론을 맺었다.

    - 계약 금액: 160,038,036,031원
    - 계약 금액: 291,548,177,545원
    따라서 ... 총 **452,586,203,566원** 규모의 계약을 체결하였습니다.

    160,038,036,031 + 291,548,177,545 = 451,586,213,576   ← 실제
                                        452,586,203,566   ← 답변 (999,989,990 차이)

세 회차 문면이 사실상 같다. 뽑기가 아니라 구조적이다.

## 🔴 왜 고쳐 쓰지 않고 지우는가

`fix_broken_equation` 과 같은 판단이다. 바른 합 451,586,213,576 은 **근거 어디에도 글자로
없다.** 써 넣으면 우리가 만든 수를 답변에 넣는 일이 된다. 거짓 문장을 지우는 것은 언제나
안전하고, 남는 것은 근거에서 옮겨 온 금액 둘뿐이다.

## 🔴 조인 자리 — 오발화 둘을 실측으로 잡았다

첫 판은 골든셋 `Q-051` 을 3/3 오발화시켰다(*"연결 자산**총**계와 별도 자산총계의 차이는
12,646,848백만 원"*). 표지가 `자산총계` 안의 `총` 을 물었고, 뺄셈을 안 봤다. 둘째 판은
미공개 `N-008` 을 3/3 오발화시켰다(*"합하면 총 2,636**억**여원"* 을 원 단위 금액과 견줬다).

조인 뒤 실측: 골든셋 672응답(`A91`·`A84`) 발화 0, 미공개셋 162응답 중 `H-005` 3건뿐.
"""
import pytest

from argus import ablation
from argus.agent import aggregate

# --- ① 표적 — `H-005` -------------------------------------------------------

H005 = (
    "삼성E&A가 2024년 4월 17일에 공시한 '단일판매ㆍ공급계약 체결(자율공시)'에는 "
    "다음과 같이 정리할 수 있습니다.\n\n"
    "1. 계약 상대방 및 계약 금액\n"
    "- 계약 상대방: Aramco Asia Japan\n"
    "- 계약 금액: 160,038,036,031원 (1,600억원)\n\n"
    "또한 같은 날 공시된 다른 계약 건에 대해서도 알려드리겠습니다.\n\n"
    "- 계약 상대방: Aramco Asia Japan\n"
    "- 계약 금액: 291,548,177,545원 (2,915억원)\n\n"
    "따라서, 2024년 4월 17일 기준으로 삼성E&A는 Aramco Asia Japan과의 계약을 통해 "
    "총 **452,586,203,566원**(약 **4,526억원**) 규모의 계약을 체결하였습니다.\n"
)
H005_EV = (
    "## [공급계약 체결] 삼성E&A – 단일판매ㆍ공급계약 체결(자율공시)\n"
    "- 계약금액: 160,038,036,031원\n"
    "## [공급계약 체결] 삼성E&A – 단일판매ㆍ공급계약체결 — Fadhili Gas\n"
    "- 계약금액: 291,548,177,545원\n"
)


def test_근거에_없고_안_맞는_총액_문장을_지운다():
    with ablation.use("A92"):
        fixed, notes = aggregate.drop_unsourced_total(H005, H005_EV)
    assert "452,586,203,566" not in fixed, "거짓 총액이 남았다"
    assert notes, "무엇을 지웠는지 안 적었다"


def test_지운_뒤에도_골든_요점은_남는다():
    """🔴 지우는 것이 답을 없애면 안 된다. 골든 요점 둘이 살아야 한다."""
    with ablation.use("A92"):
        fixed, _ = aggregate.drop_unsourced_total(H005, H005_EV)
    assert "Aramco Asia Japan" in fixed
    assert "160,038,036,031" in fixed


def test_A91_에서는_아무것도_안_한다():
    with ablation.use("A91"):
        fixed, notes = aggregate.drop_unsourced_total(H005, H005_EV)
    assert fixed == H005 and notes == []


# --- ② 침묵해야 하는 자리 ----------------------------------------------------

def test_자산총계의_총은_표지가_아니다():
    """골든셋 `Q-051` 오발화. 한글에 붙은 `총` 은 총액 표지가 아니다."""
    answer = ("- 연결 자산총계: 27,513,223백만원\n"
              "- 별도 자산총계: 14,866,375백만원\n"
              "따라서 연결 자산총계와 별도 자산총계의 차이는 12,646,848백만 원입니다.")
    evidence = "자산총계 27,513,223 / 자산총계 14,866,375"
    with ablation.use("A92"):
        assert aggregate.drop_unsourced_total(answer, evidence) == (answer, [])


def test_뺄셈으로_닿는_값은_안_지운다():
    """부호 있는 조합으로 닿으면 답변이 스스로 유도한 값이다."""
    answer = ("- 갑: 27,513,223원\n- 을: 14,866,375원\n"
              "두 값의 합계는 12,646,848원 차이가 납니다.")
    evidence = "27,513,223원 14,866,375원"
    with ablation.use("A92"):
        assert aggregate.drop_unsourced_total(answer, evidence) == (answer, [])


def test_배수_표지가_다르면_안_견준다():
    """미공개 `N-008` 오발화. `2,636억` 을 원 단위 금액과 견주면 안 된다."""
    answer = ("- 1,417,130,000원\n- 1,218,731,800원\n"
              "위 두 계약을 합하면 총 2,636억여원이 됩니다.")
    evidence = "1,417,130,000원 1,218,731,800원 2,635,861,800원"
    with ablation.use("A92"):
        assert aggregate.drop_unsourced_total(answer, evidence) == (answer, [])


def test_총액이_근거에_있으면_안_지운다():
    answer = ("- 100,000,000원\n- 200,000,000원\n합계 350,000,000원입니다.")
    evidence = "100,000,000원 200,000,000원 350,000,000원"
    with ablation.use("A92"):
        assert aggregate.drop_unsourced_total(answer, evidence) == (answer, [])


def test_나열이_하나뿐이면_안_지운다():
    """견줄 것이 없으면 총액이 틀렸는지 알 수 없다."""
    answer = "- 100,000,000원\n총 450,000,000원 규모입니다."
    evidence = "100,000,000원"
    with ablation.use("A92"):
        assert aggregate.drop_unsourced_total(answer, evidence) == (answer, [])


def test_나열에_없는_금액은_근거에_있어도_안_센다():
    """앞 블록이 나열하지 않은 값을 끌어와 합을 맞추면 발화가 흐려진다."""
    answer = "- 100,000,000원\n- 200,000,000원\n총 900,000,000원 규모입니다."
    evidence = "100,000,000원 200,000,000원 600,000,000원"
    with ablation.use("A92"):
        _, notes = aggregate.drop_unsourced_total(answer, evidence)
    assert notes, "300,000,000 이 아닌 900,000,000 은 지워져야 한다"


def test_부분보다_작은_총액은_단위가_다르다():
    answer = "- 1,417,130,000원\n- 1,218,731,800원\n총 2,636,000원입니다."
    evidence = "1,417,130,000원 1,218,731,800원"
    with ablation.use("A92"):
        assert aggregate.drop_unsourced_total(answer, evidence) == (answer, [])


# --- ③ 꼬리는 건드리지 않는다 -------------------------------------------------

def test_시스템이_붙인_꼬리는_안_건드린다():
    """`split_tail` 의 규율. 註는 검증기가 관찰한 기록이다."""
    tail = "\n\n(근거: 공급계약 체결 (2024-04-17), 접수번호 20240417800519)"
    with ablation.use("A92"):
        fixed, notes = aggregate.drop_unsourced_total(H005 + tail, H005_EV)
    assert notes
    assert fixed.endswith(tail), "꼬리가 잘렸다"


# --- ④ 팔 등록 --------------------------------------------------------------

def test_A92_는_A91_에_한_칸만_더한다():
    import dataclasses

    a91 = {f.name: getattr(ablation.ARMS["A91"], f.name)
           for f in dataclasses.fields(ablation.Arm)}
    a92 = {f.name: getattr(ablation.ARMS["A92"], f.name)
           for f in dataclasses.fields(ablation.Arm)}
    moved = {k for k in a91 if a91[k] != a92[k]}
    assert moved == {"name", "drop_unsourced_total"}


@pytest.mark.parametrize("arm", ["A84", "A86", "A89", "A91"])
def test_종전_팔은_안_켜져_있다(arm):
    with ablation.use(arm):
        assert not ablation.enabled("drop_unsourced_total")
