"""`A35`, 답변의 **괄호 환산**을 우리가 다시 쓴다.

실측(2026-08-16 · 저장 답변 132건 전수): 괄호 환산 67개 중 **4개(3문항)** 가 틀렸다.
본문 수치는 근거에서 옮겨 적으니 맞고, 괄호는 **자기가 나눠야 하니** 틀린다.
v4 의 발견(*"모델이 숫자를 손에 쥐는 순간 틀린다"*)이 여기서 또 나온다.

    Q-097  207,684,631,210원 → "(20,768억원)"     맞는 값 2,077억원   **10배**
    Q-061    345,173,200,000원 → "(3400억원)"       맞는 값 3,452억원
    Q-061  1,728,230,200,000원 → "(1조 6,549억원)"   맞는 값 1조 7,282억원
    Q-115  1,759,596,802,000원 → "(1조 4,772억원)"   맞는 값 1조 7,595억원

`Q-115` 는 **우리가 만든 것**이다. `substitute_aggregate` 가 본 수치만 갈아 끼우고
모델이 제 오답에서 계산한 괄호를 그대로 뒀다. 그래서 이 처분은 치환 뒤에 선다.
"""
from __future__ import annotations

from argus import ablation
from argus.agent import aggregate

#: 실측 넷. 값이 바뀌면 코퍼스나 생성이 움직인 것이다.
MEASURED = [
    ("**207,684,631,210원**(20,768억원)", "2,077억원"),
    ("345,173,200,000원(3400억원)", "3,452억원"),
    ("1,728,230,200,000원 (1조 6,549억원)", "1조 7,282억원"),
    ("1,759,596,802,000원(1조 4,772억원)", "1조 7,595억원"),
]


def test_the_measured_four_are_repaired():
    for text, right in MEASURED:
        fixed, changed = aggregate.fix_unit_parens(text)
        assert changed, text
        assert right in fixed, f"{text} -> {fixed}"


def test_a_correct_parenthesis_is_left_alone():
    """맞는 괄호를 건드리면 그 자체가 새 결함이다."""
    for text in ("971,400,000,000원(9,714억원)",
                 "2,492,215,574,520원 (24,922억원)",
                 "**1,102,000,000,000원** (1조 1,020억원)"):
        assert aggregate.fix_unit_parens(text) == (text, [])


def test_rounding_is_not_an_error():
    """억 표기는 1억 미만을 버린다. 그만큼은 오차가 아니다."""
    assert aggregate.fix_unit_parens("207,684,631,210원(2,077억원)")[1] == []


def test_a_non_conversion_parenthesis_is_never_touched():
    """환산이 아닌 괄호가 답변에 흔하다. 건드리면 문장을 망가뜨린다."""
    for text in ("1,316,492,000,000원(지분율 30%)",
                 "1,000,000,000,000원(주당 1,000원 기준)",
                 "2,492,215,574,520원(2023-10-26 공시)",
                 "1,234,567,890원(추정)"):
        assert aggregate.fix_unit_parens(text) == (text, [])


def test_the_format_matches_the_corpus():
    """근거와 답변이 다른 형식을 쓰면 모델이 같은 값을 다르게 읽는다.
    `prompts._split_jo` 를 그대로 쓴다. 여기서 형식을 새로 만들지 않는다."""
    from argus.agent.prompts import _split_jo

    fixed, _ = aggregate.fix_unit_parens("1,728,230,200,000원(1조 6,549억원)")
    assert _split_jo(1_728_230_200_000 / 10 ** 8) in fixed


def test_the_flag_is_opt_in():
    assert ablation.A34.fix_unit_parens is False
    assert ablation.A35.fix_unit_parens is True


def test_it_runs_after_substitution():
    """순서가 뒤집히면 `Q-115` 가 안 잡힌다. 치환이 만든 불일치이기 때문이다."""
    import inspect

    from argus.agent import pipeline

    src = inspect.getsource(pipeline.answer_question)
    assert src.index("substitute_aggregate") < src.index("fix_unit_parens")
