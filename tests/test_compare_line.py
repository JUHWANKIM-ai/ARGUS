from argus import ablation
from argus.agent import aggregate


def _record(rcept_no, corp, amount, title):
    return {"rcept_no": rcept_no, "doc_id": "exchange_%s" % rcept_no,
            "corp_name": corp, "amount": amount, "title": title,
            "is_latest": True, "related": [], "rcept_dt": "20251216",
            "markdown": "## %s %s" % (corp, title)}


PAIR = [_record("1", "두산에너빌리티", 4_928_968_079_043, "Dukovany NSSS"),
        _record("2", "두산에너빌리티", 2_338_117_950_000, "신한울 원자로설비")]


def test_comparison_is_silent_by_default():
    with ablation.use(ablation.ARMS["A42"]):
        assert aggregate.line(PAIR, "A 계약이 B 계약보다 얼마나 큰가", None) == ""


def test_comparison_states_both_values_and_the_difference():
    arm = ablation.replace(ablation.ARMS["A42"], name="probe", compare_line=True)
    with ablation.use(arm):
        out = aggregate.line(PAIR, "A 계약이 B 계약보다 얼마나 큰가", None)
    assert "4,928,968,079,043" in out
    assert "2,338,117,950,000" in out
    assert "2,590,850,129,043" in out


def test_comparison_names_which_side_is_larger():
    # Distinct corp_names are required here: PAIR shares one corp_name across
    # both records, so a literal match on the side clause cannot tell a
    # correct pick from an inverted one. This asserts the exact clause naming
    # the larger record's own company, not merely that some verdict fired.
    arm = ablation.replace(ablation.ARMS["A42"], name="probe", compare_line=True)
    distinct = [_record("1", "두산에너빌리티", 4_928_968_079_043, "Dukovany NSSS"),
                _record("2", "한전기술", 1_250_850_298_678, "종합설계")]
    with ablation.use(arm):
        out = aggregate.line(distinct, "A 가 B 보다 큰가", None)
    assert "두산에너빌리티 쪽이 더 크다" in out
    assert "한전기술 쪽이 더 크다" not in out


def test_comparison_states_equality_instead_of_a_side_when_amounts_match():
    """When the two amounts are equal, the line must not name either side as
    larger. Naming one anyway would assert a confident wrong verdict, which
    this module's own docstring rules is worse than staying silent."""
    arm = ablation.replace(ablation.ARMS["A42"], name="probe", compare_line=True)
    tied = [_record("1", "두산에너빌리티", 2_338_117_950_000, "Dukovany NSSS"),
            _record("2", "한전기술", 2_338_117_950_000, "종합설계")]
    with ablation.use(arm):
        out = aggregate.line(tied, "A 가 B 보다 큰가", None)
    assert "차이 **0원**" in out
    assert "더 크다" not in out
    assert "두 금액은 같다" in out


def test_three_records_are_not_a_pair():
    """A comparison is only defined for exactly two records. With three,
    which two the question means is not determined."""
    arm = ablation.replace(ablation.ARMS["A42"], name="probe", compare_line=True)
    trio = PAIR + [_record("3", "한전기술", 1_250_850_298_678, "종합설계")]
    with ablation.use(arm):
        assert aggregate.line(trio, "A 가 B 보다 큰가", None) == ""
