r"""`revision_diff` — 정정 전후 두 벌을 줄 단위로 견준다.

## 무엇을 지키는 시험인가

정정 문항은 근거에 **같은 절이 두 벌** 실린다. 그 차이는 코드로 정확히 구할 수 있는데
답변은 눈으로 훑어 짐작한다. `Q-094` 골든 요점 넷이 이 대조에 통째로 들어 있고 답변은
세 판 모두 요점1·2 만 맞혔다 — 요점3 의 두 수는 바뀐 줄에 없고 **바로 아래 줄**에 있다.

가장 중요한 시험 둘:

  1. **금액이 바뀐 정정과 표기만 바뀐 정정에서 낸 것이 다른가.** 앞쪽은 바뀐 줄만,
     뒤쪽은 그 줄이 든 표까지다 — 뒤쪽에서는 「안 바뀐 값」이 답이기 때문이다.
  2. **차이가 클 때 침묵하는가.** 코퍼스에 71줄·249줄 짝이 있고 그것을 한 줄에 담으면
     근거를 밀어내는 벽이 된다.
"""
import pytest

from argus import ablation
from argus.agent import aggregate

#: `Q-094` 근거의 두 벌. 코퍼스에서 그대로 옮겼다 — 정정본은 항목명에 단위 표기만 붙는다.
_SECTION = "[사업보고서 2024] NC – III. 재무에 관한 사항 > 3. 연결재무제표 주석 > 26. 이익잉여금 (연결)"
_TABLE = """\
(단위 : 천원)

|  | 연차배당 |
|---|---|
| 배당 기준 총발행 보통주식수 | 21,954,022 |
| 배당기준일의 자기주식수 | 1,646,583 |
| 배당주식수 | 20,307,439 |
| 보통주에 지급된 주당배당금{unit} | 3,130 |
| 보통주에 지급된 배당금 | 63,562,284 |
"""


def _block(receipt, unit="", intro="(2)"):
    return ("## %s\n- 출처: 접수번호 %s (공시일 20250318)\n\n"
            "%s 당기와 전기 중 배당금 지급 내역은 다음과 같습니다.\n\n%s"
            % (_SECTION, receipt, intro, _TABLE.format(unit=unit)))


#: 표기만 바뀐 정정. 금액 다중집합이 같다.
NOTATION_ONLY = _block("20250318001264") + "\n" + \
    _block("20250328002285", unit="(단위: 원/주)", intro="(3)")
#: 금액이 바뀐 정정.
AMOUNT_CHANGED = _block("20250318001264") + "\n" + \
    _block("20250328002285").replace("63,562,284", "70,000,000")


def line(evidence=NOTATION_ONLY, arm="A75"):
    with ablation.use(arm):
        return aggregate.revision_diff(evidence)


def test_표기만_바뀌면_안_바뀐_표까지_낸다():
    """`Q-094` 요점3 — `63,562,284` 는 바뀐 줄에 없고 그 줄이 든 표에 있다."""
    out = line()
    assert "금액은 하나도 바뀌지 않았다" in out
    assert "'보통주에 지급된 주당배당금 3,130' → " \
           "'보통주에 지급된 주당배당금(단위: 원/주) 3,130'" in out
    assert "그 줄이 든 표의 금액은 전후가 같다" in out
    assert "보통주에 지급된 배당금 63,562,284천원" in out


def test_단위를_붙이지만_아무_행에나_붙이지_않는다():
    """🔴 `covers_key_amounts` 는 단위가 붙은 수만 돈으로 본다. 그래서 붙여야 한다.

    그러나 이 표는 `(단위 : 천원)` 을 선언하고도 안에 주식수 행을 담고 있다.
    `21,954,022천원` 이라 적으면 주식수를 돈이라고 단정하는 것이다. 그리고
    `주당배당금(단위: 원/주)` 은 자기 단위를 스스로 밝힌 행이라 표의 천원이 안 듣는다.
    """
    from argus.agent.critic import money_values

    out = line()
    #: 이 붙임의 표는 한 벌뿐이라 금액도 하나다. 실물 `Q-094` 는 당기·전기 두 표라
    #: 63,562,284천원 · 135,653,693천원 둘이 나온다.
    assert money_values(out) == [63562284000.0]
    assert "21,954,022천원" not in out and "1,646,583천원" not in out
    assert "3,130천원" not in out and "6,680천원" not in out


def test_금액이_바뀌면_바뀐_줄만_낸다():
    """🔴 그때는 무엇이 무엇으로 바뀌었는가가 답이다. 표를 함께 내면 벽이 된다 —
    금액이 바뀐 짝은 표가 크다(`Q-048` 178종 · `Q-052` 14종)."""
    out = line(AMOUNT_CHANGED)
    assert "금액이 바뀌었다" in out
    assert "'보통주에 지급된 배당금 63,562,284' → '보통주에 지급된 배당금 70,000,000'" in out
    assert "그 줄이 든 표" not in out


def test_표지는_이미_쓰이는_것이다():
    """🔴 새 표지 `[결정적 대조]` 를 만들었더니 답변이 *"(근거: 결정적 대조 정보)"* 라고
    적었다. 생성 프롬프트는 표지를 아예 모르므로 모델은 대괄호를 그냥 출처로 읽는다.
    표지는 이미 쓰이는 둘(`집계`·`조회`) 안에 머문다."""
    assert line().startswith("- [결정적 집계]")
    assert "결정적 대조" not in line()


def test_금액을_그대로_적으라고_한다():
    """🔴 첫 판은 *"바뀐 것과 바뀌지 않은 것을 모두 이 대조대로 적는다"* 였다. 답변은
    「변동이 없습니다」로 뭉뚱그리고 수치를 한 개도 안 적어 `amount_gap` 에 걸렸다 —
    3회 중 2회가 오답이 됐다. 겨냥한 문항을 우리가 망친 것이다."""
    assert "위에 적힌 금액을 그대로 적어" in line()


def test_원공시가_먼저_온다():
    """접수번호가 작은 쪽이 원공시다. 정정은 언제나 뒤에 접수된다."""
    flipped = NOTATION_ONLY.split("\n## ")
    reordered = "## " + flipped[1] + "\n## " + flipped[0].lstrip("# ")
    assert "원공시 20250318001264 → 정정 20250328002285" in line(reordered)


def test_출처_줄은_차이로_세지_않는다():
    """접수번호가 다른 것은 당연하다. 그것을 차이로 세면 모든 짝이 최소 한 줄 다르다."""
    same = _block("20250318001264") + "\n" + _block("20250328002285")
    assert line(same) == ""


def test_차이가_크면_침묵한다():
    """`Q-076` 71줄 · `Q-088` 249줄 실측. 한 줄에 담으면 근거를 밀어내는 벽이 된다."""
    noisy = _block("20250318001264") + "\n" + _block("20250328002285")
    for i in range(aggregate.MAX_REVISION_PAIRS + 2):
        noisy = noisy.replace("| 배당주식수 | 20,307,439 |",
                              "| 배당주식수 | 20,307,439 |\n| 덧붙인 %d | 1,000 |" % i, 1)
    assert line(noisy) == ""


def test_같은_절이_세_벌이면_침묵한다():
    """어느 둘이 전후인지 모른다."""
    three = NOTATION_ONLY + "\n" + _block("20250401000001", unit="(단위: 원/주)")
    assert line(three) == ""


def test_한_벌뿐이면_침묵한다():
    assert line(_block("20250318001264")) == ""


def test_접수번호가_같으면_짝이_아니다():
    """같은 공시가 조각으로 두 번 실린 것이다. 정정 전후가 아니다."""
    pieces = _block("20250318001264") + "\n" + \
        _block("20250318001264", unit="(단위: 원/주)")
    assert line(pieces) == ""


@pytest.mark.parametrize("arm", ["A65", "A73", "A74"])
def test_스위치가_꺼진_팔은_침묵한다(arm):
    assert line(arm=arm) == ""


def test_근거가_비면_침묵한다():
    assert line("") == ""
    assert line(None) == ""
