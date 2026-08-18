from argus import ablation
from argus.agent import units

SECTION = ("## [사업보고서 2025] 삼성전자 - II. 사업의 내용\n"
           "(단위 : 억원)\n"
           "| 합 계 |  |  | 3,336,059 | 3,008,709 |\n")


def test_unit_line_states_the_multiplier():
    out = units.unit_line(SECTION)
    assert "억원" in out and "100,000,000" in out


def test_unit_line_is_empty_without_a_unit_header():
    assert units.unit_line("## 제목\n| 합 계 | 3,336,059 |\n") == ""


def test_unit_line_lists_each_distinct_unit_once():
    text = SECTION + "\n(단위: 백만원)\n| 매출액 | 97,146,675 |\n" + SECTION
    out = units.unit_line(text)
    assert out.count("억원") == 1 and out.count("백만원") == 1


def test_an_unknown_unit_is_skipped():
    assert units.unit_line("(단위 : 주)\n| 수량 | 100 |\n") == ""


def test_the_unit_line_flag_defaults_to_false_but_the_promoted_arm_turns_it_on():
    # The dataclass default did not move: a fresh Arm still has it off. The
    # operational arm is A43, which turns this one flag on over A42.
    # See docs/design-rationale.md#operational-default-history
    assert ablation.Arm("X").unit_line is False
    assert ablation.current().unit_line is True
    assert ablation.enabled("unit_line") is True
    assert "unit_line" in ablation.FLAG_NAMES
