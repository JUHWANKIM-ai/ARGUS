r"""`pledged_series` — 주요계약체결 추이를 결정적 줄로 낸다.

## 무엇을 지키는 시험인가

`Q-012` 요점3(`84,282,066주` → `92,270,920주`)이 세 판 내리 빠졌다. 그 수가 근거 안에는
있었다. 다만 `fields["주요계약체결 주식등의 수"]` 안의 **문자열**(`직전 A주 → 이번 B주`)로
있어 보유비율 추이 줄이 안 읽었다. 같은 문항의 요점1 은 그 줄에 실려 세 판 다 들어왔다 —
**결정적 줄에 실린 수는 답변에 오고 근거 본문에만 있는 수는 오지 않는다**의 실측 반례라
여기서 못을 박는다.

아래 시험은 세 갈래를 나눠 붙인다: 「이번」 쪽을 잡는가, 언제 침묵하는가, 종전 줄을
망가뜨리지 않는가.
"""
import pytest

from argus import ablation
from argus.agent import aggregate


def _record(ymd, rcept, shares, ratio, pledged_shares=None, pledged_ratio=None):
    """대량보유 한 건. `fields` 는 원본 그대로 `직전 … → 이번 …` 문자열이다."""
    fields = {}
    if pledged_shares is not None:
        fields["주요계약체결 주식등의 수"] = pledged_shares
    if pledged_ratio is not None:
        fields["주요계약체결 비율"] = pledged_ratio
    return {"rcept_no": rcept, "rcept_dt": ymd, "corp_name": "(주)두산",
            "report_nm": "주식등의대량보유상황보고서", "reporter": "(주)두산",
            "shares_current": shares, "ratio_current": ratio, "fields": fields}


#: `Q-012` 의 실제 두 건. 코퍼스에서 그대로 옮겼다.
Q012 = [
    _record("20250310", "20250310000839", "196,488,145", "30.67",
            "직전 84,634,380주 → 이번 84,282,066주", "직전 13.21% → 이번 13.15%"),
    _record("20260305", "20260305001006", "196,506,795", "30.68",
            "직전 92,270,920주 → 이번 92,270,920주", "직전 14.40% → 이번 14.40%"),
]
ASK = "두산의 대량보유 상황이 어떻게 달라졌는지 알려줘"


def test_이번_쪽_값을_잇는다():
    """골든이 잇는 것은 각 보고서의 「이번」 값이다.

    🔴 `직전` 쪽을 잡으면 한 보고서 **안**의 변동을 보고서 **사이**의 변동으로 오인해
    `84,634,380 → 92,270,920` 이 된다. 그것은 골든의 수가 아니다.
    """
    with ablation.use("A71"):
        line = aggregate.holding_series(Q012, ASK)
    assert "2025-03-10 84,282,066주 (13.15%) → 2026-03-05 92,270,920주 (14.40%)" in line
    assert "84,634,380" not in line, "직전 쪽을 잡았다"


def test_침묵_한쪽만_그_칸을_가지면():
    """대량보유 1,083건 중 507건만 이 칸을 갖는다. 반쪽이면 「무엇에서 무엇으로」가 없다."""
    half = [Q012[0], _record("20260305", "20260305001006", "196,506,795", "30.68")]
    with ablation.use("A71"):
        line = aggregate.holding_series(half, ASK)
    assert "주요계약체결" not in line
    assert "보유비율 추이" in line, "종전 줄은 그대로 나와야 한다"


def test_침묵_두_끝이_같으면():
    """변동이 없는데 `추이` 라 적으면 없는 변동을 말하는 것이다."""
    flat = [_record("20250310", "1", "10", "5.0", "직전 9주 → 이번 100주", "직전 1% → 이번 2%"),
            _record("20260305", "2", "11", "5.1", "직전 50주 → 이번 100주", "직전 1% → 이번 2%")]
    with ablation.use("A71"):
        line = aggregate.holding_series(flat, ASK)
    assert "주요계약체결" not in line


def test_비율_칸이_없으면_주식수만():
    """두 칸이 늘 함께 오지는 않는다. 주식수만 있으면 괄호를 안 붙인다."""
    only = [_record("20250310", "1", "10", "5.0", "직전 8주 → 이번 84,282,066주"),
            _record("20260305", "2", "11", "5.1", "직전 9주 → 이번 92,270,920주")]
    with ablation.use("A71"):
        line = aggregate.holding_series(only, ASK)
    assert "2025-03-10 84,282,066주 → 2026-03-05 92,270,920주" in line
    assert "(%" not in line and "()" not in line


@pytest.mark.parametrize("arm", ["A65", "A70"])
def test_스위치가_꺼진_팔은_안_바뀐다(arm):
    """운영 기본값(`A65`)과 직전 팔(`A70`)에서 이 줄은 없다."""
    with ablation.use(arm):
        line = aggregate.holding_series(Q012, ASK)
    assert "주요계약체결" not in line


def test_종전_줄은_그대로다():
    """덧붙이는 것이라 보유비율 추이 줄의 본문은 한 글자도 안 달라진다."""
    with ablation.use("A70"):
        before = aggregate.holding_series(Q012, ASK)
    with ablation.use("A71"):
        after = aggregate.holding_series(Q012, ASK)
    assert before and after.startswith(before)


def test_한_건이면_침묵한다():
    """체인이 하나면 이을 것이 없다."""
    with ablation.use("A71"):
        assert "주요계약체결" not in aggregate.holding_series(Q012[:1], ASK)


def test_짝은_근거_두_벌에서_뽑는다_체인_전체가_아니다():
    """🔴 첫 판은 `holding_chain` 이 돌려준 전체 체인의 양 끝을 읽었다.

    `Q-012` 는 체인이 13건이라 앞 끝이 `2023-02-17 40,939,844주 (6.41%)` 가 되어
    **골든의 짝과 달랐다**. 비율 추이 줄이 체인 전체를 훑는 것은 「추이」를 보이는 것이
    일이기 때문이고, 이 줄은 「무엇에서 무엇으로」라는 한 짝을 말하므로 답변이 실제로
    읽는 두 벌을 가리켜야 한다.
    """
    older = _record("20230217", "20230217001569", "196,452,737", "30.78",
                    "직전 39,000,000주 → 이번 40,939,844주", "직전 6.10% → 이번 6.41%")

    def chain(_reporter, _corp):
        #: 검색이 근거로 실어 준 것은 두 벌인데 체인에는 세 벌이 있다.
        return [older] + Q012

    with ablation.use("A71"):
        line = aggregate.holding_series(Q012, ASK, chain)
    #: 비율 줄은 체인 전체를 훑는다.
    assert "2023-02-17 30.78%" in line
    #: 주요계약체결 짝은 근거 두 벌이다.
    assert "2025-03-10 84,282,066주 (13.15%) → 2026-03-05 92,270,920주 (14.40%)" in line
    assert "40,939,844" not in line and "6.41%" not in line


def test_추이를_안_물으면_줄_전체가_없다():
    """`holding_series` 의 발화 조건을 우회하지 않는다."""
    with ablation.use("A71"):
        assert aggregate.holding_series(Q012, "두산의 최대주주는 누구야") == ""
