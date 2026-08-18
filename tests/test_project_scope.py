"""The project filter axis on the sum operation.

`_sum_rows` already recorded the gap it closes: "A counterparty filter on its
own does not pin the set... What such a question narrows is the project, and
that axis is not available here."

The vocabulary comes from the corpus, not from a heuristic: a token counts as
a project token only when it appears in a `supply_region` of the evidence AND
in the question. Q-107's four filings supply `체코`, and the question says 체코.
See docs/design-rationale.md#project-scope
"""
import pytest

from argus import ablation
from argus.agent import aggregate


class _Route:
    def __init__(self, counterparty=None):
        self.counterparty = counterparty
        self.companies = []


#: Q-107's four real filings. The fourth is the trap: same project, but the
#: counterparty is 두산에너빌리티 rather than 한국수력원자력, so it is excluded.
DUKOVANY = [
    {"rcept_no": "20251216801147", "rcept_dt": "20251216", "related": [],
     "amount": 4_928_968_079_043, "counterparty": "한국수력원자력(주)",
     "supply_region": "체코 남부 Dukovany Site",
     "title": "단일판매ㆍ공급계약체결 — Dukovany 5&6 NSSS Contract"},
    {"rcept_no": "20251216801163", "rcept_dt": "20251216", "related": [],
     "amount": 711_106_288_572, "counterparty": "한국수력원자력(주)",
     "supply_region": "체코 남부 Dukovany Site",
     "title": "단일판매ㆍ공급계약체결 — Dukovany 5&6 Turbine Generator Supply Contract"},
    {"rcept_no": "20251212801320", "rcept_dt": "20251212", "related": [],
     "amount": 1_250_850_298_678, "counterparty": "한국수력원자력(주)",
     "supply_region": "체코",
     "title": "단일판매ㆍ공급계약체결 — 체코 두코바니 5&6호기 종합설계용역"},
    {"rcept_no": "20251224800396", "rcept_dt": "20251224", "related": [],
     "amount": 373_449_426_066, "counterparty": "두산에너빌리티(주)",
     "supply_region": "체코",
     "title": "단일판매ㆍ공급계약체결 — 체코 두코바니 5&6호기 원자로계통설계용역"},
]

#: A filing from an unrelated project by the same filer, the kind of record
#: retrieval mixes in and the reason summing the whole evidence set overshoots.
SINHANUL = {
    "rcept_no": "20230330800844", "rcept_dt": "20230330", "related": [],
    "amount": 181_608_900_000, "counterparty": "두산에너빌리티",
    "supply_region": "대한민국",
    "title": "단일판매ㆍ공급계약체결 — 신한울원자력발전소 3,4호기 원자로계통 설계용역",
}

QUESTION = ("체코 두코바니 5·6호기 원자력발전소 사업과 관련해 한국수력원자력을 "
            "계약상대로 하여 공시된 단일판매·공급계약의 계약금액 합계는 얼마인가?")

GOLDEN_TOTAL = 6_890_924_666_293


@pytest.fixture
def project_arm():
    with ablation.use(ablation.replace(ablation.current(), name="T",
                                       project_scope=True)):
        yield


def test_the_total_matches_the_golden_answer(project_arm):
    total = aggregate._total(DUKOVANY + [SINHANUL], QUESTION,
                             _Route("한국수력원자력"))
    assert total == GOLDEN_TOTAL
    assert total == 4_928_968_079_043 + 711_106_288_572 + 1_250_850_298_678


def test_the_wrong_counterparty_filing_is_excluded(project_arm):
    """Same project, different orderer. Adding it because it shares the
    project is exactly the mistake the golden notes call out."""
    picked, _ = aggregate.select(DUKOVANY, QUESTION, _Route("한국수력원자력"), "합")
    assert [r["rcept_no"] for r in picked] == [
        "20251216801147", "20251216801163", "20251212801320"]


def test_the_note_records_the_project(project_arm):
    _, note = aggregate.select(DUKOVANY, QUESTION, _Route("한국수력원자력"), "합")
    assert "체코" in note and aggregate._PROJECT_NOTE in note


def test_an_off_project_filing_is_dropped(project_arm):
    picked, _ = aggregate.select(DUKOVANY + [SINHANUL], QUESTION,
                                 _Route("한국수력원자력"), "합")
    assert "20230330800844" not in [r["rcept_no"] for r in picked]


def test_records_without_a_supply_region_supply_no_vocabulary(project_arm):
    """The index that predates the `supply_region` render carries no such
    field. The axis then has nothing to read and stays silent, which is why
    Task 6 rebuilds the index."""
    stripped = [{k: v for k, v in r.items() if k != "supply_region"}
                for r in DUKOVANY]
    assert aggregate._project_token(stripped, QUESTION) == ""
    assert aggregate._total(stripped, QUESTION, _Route("한국수력원자력")) is None


def test_a_question_naming_no_project_leaves_the_axis_silent(project_arm):
    plain = "한국수력원자력을 계약상대로 한 계약금액 합계는 얼마인가?"
    assert aggregate._project_token(DUKOVANY, plain) == ""


def test_the_project_filter_never_empties_the_set(project_arm):
    """Pins the real invariant the revert-on-empty guard exists to protect,
    rather than a record count that would hold even with the guard deleted.
    `select`'s comment on the guard explains why it is unreachable today
    (the token's own record always survives its own vocabulary source) and
    why it stays anyway (a future divergence between the vocabulary source
    and the narrow-check target should fail safe, not empty the set).

    For every input where `_project_token` finds a token, running the
    project filter through `select` must never yield an empty set."""
    cases = (
        (DUKOVANY, QUESTION, _Route("한국수력원자력")),
        (DUKOVANY + [SINHANUL], QUESTION, _Route("한국수력원자력")),
        (DUKOVANY, QUESTION, _Route(None)),
        ([dict(r, supply_region="체코",
               title="단일판매ㆍ공급계약체결 — 무관한 계약") for r in DUKOVANY],
         "체코 사업의 계약금액 합계는?", _Route(None)),
    )
    for records, question, route in cases:
        token = aggregate._project_token(records, question)
        if token:
            picked, _ = aggregate.select(records, question, route, "합")
            assert len(picked) > 0


def test_the_project_axis_alone_keeps_the_trap_filing(project_arm):
    """Isolates which axis removes the trap filing. With no counterparty on
    the route, the project axis alone must keep all four Dukovany filings --
    including the trap, whose orderer differs -- because narrowing by
    project is not this axis's job. Only the counterparty axis afterward
    removes it. `test_the_wrong_counterparty_filing_is_excluded` checks the
    combined result, which alone does not show which axis did the work."""
    picked, note = aggregate.select(DUKOVANY, QUESTION, _Route(None), "합")
    assert [r["rcept_no"] for r in picked] == [r["rcept_no"] for r in DUKOVANY]
    assert aggregate._PROJECT_NOTE in note


def test_the_tie_break_is_deterministic(project_arm):
    """Two equal-length region tokens, both present in the question. Without
    a secondary sort key, `hits` -- built from a `set` -- could return either
    one depending on `PYTHONHASHSEED`: proven to flip between seed 0 and seed
    3 before this fix. The secondary key `(-len(t), t)` breaks length ties by
    codepoint order, which does not depend on the hash seed, so the winner
    (and the total that follows from it) stays stable across restarts."""
    records = [{"supply_region": "체코 남부"}]
    question = "체코 남부 지역 사업의 계약금액 합계는?"
    assert aggregate._project_token(records, question) == "남부"


def test_the_axis_is_off_when_the_flag_is_off():
    """Without the flag the behaviour is byte-identical to A43: the
    counterparty filter alone does not pin the set, so there is no total."""
    with ablation.use(ablation.ARMS["A43"]):
        assert aggregate._total(DUKOVANY, QUESTION, _Route("한국수력원자력")) is None


def test_the_flag_is_off_by_default():
    assert ablation.Arm("X").project_scope is False
    assert "project_scope" in ablation.FLAG_NAMES
