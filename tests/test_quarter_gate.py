"""A28 — 없는 분기보고서를 잡아낸다 (규약 재감사 RF-1).

🔴 실측 결함: *"현대건설의 2026년 3분기 분기보고서에 기재된 수주잔고는?"* 을 물었더니
2026년 1분기 보고서(20260515002899)를 찾아 놓고 *"3분기 보고서에 기재되어 있지
않습니다"* 라고 **거짓 전제를 받아들이고** 그 보고서를 근거로 달았다.

원인 — `missing_named_report` 가 `doc_subtype`·`base_year` 만 봐서 "2026년 3분기" 가
"2026년 1분기" 의 존재로 통과했다. **몇 분기인지가 축에 없었다.**
"""
import pytest

from argus import ablation
from argus.agent.query import report_hints
from argus.agent.router import missing_named_report

CORP = ["현대건설"]


@pytest.fixture
def gate_on():
    with ablation.use(ablation.A28):
        yield


# --- 축이 뽑히는가 -------------------------------------------------------------

@pytest.mark.parametrize("question,expected", [
    ("현대건설의 2026년 3분기 분기보고서", "3"),
    ("2025년 1분기 분기보고서를 보여줘", "1"),
    ("2024년 2분기 실적", "2"),
    ("2024년 사업보고서", None),
])
def test_quarter_axis_is_extracted(question, expected):
    r"""🔴 `_REPORT_YEAR` 이 `(?:\d\s*분기\s*)?` 로 이 자리를 건너뛰기만 하고 값을
    안 챙긴 것이 결함의 시작이었다."""
    assert report_hints(question).get("quarter") == expected


def test_quarter_axis_does_not_disturb_the_others():
    h = report_hints("현대건설의 2026년 3분기 분기보고서")
    assert h["doc_subtype"] == "quarter" and h["base_year"] == "2026"


# --- 기본값은 종전 그대로 -------------------------------------------------------

def test_flag_off_keeps_the_old_verdict():
    """🔴 꺼진 arm 의 판정은 v1~v4 산출물과 바이트 동일이어야 한다."""
    assert missing_named_report("현대건설의 2026년 3분기 분기보고서는?", CORP) == ""


# --- 켜면 갈린다 ---------------------------------------------------------------

def test_catches_the_quarter_that_does_not_exist(gate_on):
    """코퍼스는 2026.03 접수분까지 — 2026년 3분기 보고서(2026-11 접수)는 없다."""
    assert missing_named_report("현대건설의 2026년 3분기 분기보고서는?", CORP)


@pytest.mark.parametrize("question", [
    "현대건설의 2026년 1분기 분기보고서는?",     # 20260515 로 실재한다
    "현대건설의 2025년 3분기 분기보고서는?",     # 20251114 로 실재한다
    "현대건설의 2025년 1분기 분기보고서는?",
])
def test_does_not_touch_reports_that_exist(gate_on, question):
    """🔴 이것이 깨지면 답할 수 있는 질의를 스스로 버린다."""
    assert missing_named_report(question, CORP) == ""


def test_half_year_verdict_is_unchanged(gate_on):
    """반기는 doc_subtype 이 달라 이미 갈렸다 — 이 arm 이 건드리면 안 된다."""
    assert missing_named_report("현대건설의 2026년 반기보고서는?", CORP)


@pytest.mark.parametrize("question", [
    "현대건설의 2026년 2분기 분기보고서는?",
    "현대건설의 2026년 4분기 분기보고서는?",
])
def test_second_and_fourth_quarter_are_left_alone(gate_on, question):
    """🔴 2분기·4분기는 각각 반기보고서·사업보고서다. 질문자가 그 뜻으로 썼는지
    '분기보고서' 를 잘못 부른 것인지 알 수 없으므로 판정하지 않는다."""
    assert missing_named_report(question, CORP) == ""


# --- arm 등록 -----------------------------------------------------------------

def test_a28_differs_from_baseline_by_one_flag():
    diff = [f for f in ablation.FLAG_NAMES
            if getattr(ablation.A28, f) != getattr(ablation.A25_A26, f)]
    assert diff == ["quarter_gate"]


def test_baseline_still_has_it_off():
    """🔴 측정 전에는 운영 기본값을 바꾸지 않는다."""
    assert ablation.current().quarter_gate is False
