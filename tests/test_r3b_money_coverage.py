"""v4 `R3b` — ① 배율 표지의 공백 · ② judge 의 '침묵하면 무사' 비대칭.

🔴 **둘 다 성적을 깎는 방향의 변경**이라 지킬 것이 대칭으로 둘이다.

① 못 보던 것을 보는가 — `백만 원`·`억 원`·`십억 원` 같은 띄어쓰기 표기
② 🔴 **안 봐야 할 것을 안 보는가** — 긴 표지 우선순위가 공백을 허용하면서 깨지지
   않았는가 · `천만원` 을 넣지 않아 복합 수사 꼬리(`1조 8,287억 5천만 원`)가
   별개 금액으로 갈리지 않는가 · 줄바꿈으로 갈린 표지를 붙이지 않는가

②가 이 파일의 절반이다. 저장 답변 2,449건 전수 재채점에서 정규식 수정의
**신규 발화 0 · 발화 상실 0 · 성적 변화 0** 인 것이 그 근거이고, 아래 단위 테스트가
그 근거를 코드 쪽에 고정한다.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from argus.agent import critic  # noqa: E402
from scripts import error_analysis as ea  # noqa: E402


def values(text):
    return critic.money_values(text)


# --- ① 표지 안의 공백 ----------------------------------------------------------

def test_spaced_unit_markers_are_money():
    """실측 표기다. `백만 원` 은 저장 답변 2,449건에서 608회 · 붙임형은 86회다."""
    assert values("95,822,036백만 원") == [95_822_036e6]
    assert values("5,000만 원") == [5_000e4]
    assert values("1,000 천 원") == [1_000e3]
    assert values("3조 원") == [3e12]
    assert values("2,078억 원") == [2_078e8]


def test_ten_billion_marker_is_read():
    """🔴 `십억원` 은 목록에 아예 없었다 — `454십억 원` 이 통째로 안 보였다.

    저장 답변에서 `십억 원` 36회 · 붙임형 0회. 용례는 전부 단독 금액이다.
    """
    assert values("454십억 원") == [454e9]
    assert values("2,990십억원") == [2_990e9]


def test_spaced_marker_completes_the_restatement_pair():
    """🔴 `억 원` 은 붙임 `억` 이 값을 받아 **값은 맞았다.** 그런데 표지가 잘려
    `(5,653억 원)` 의 닫는 괄호를 못 읽어 재기술 짝이 서지 못했다.

    저장 답변 전수에서 재기술 짝 478 → 961 로 늘었고 **불일치는 하나도 안 늘었다.**
    """
    assert critic._restatement_pairs("565,342,321,842원 (5,653억 원)")
    assert critic.verify_answer_arithmetic("565,342,321,842원 (5,653억 원)")["ok"]
    bad = critic.verify_answer_arithmetic("590,947,351,842원 (591억 원)")
    assert bad["failed_kinds"] == ["restatement"]


# --- ② 🔴 넓히면서 깨지 말아야 할 것 --------------------------------------------

def test_longer_markers_still_win_after_allowing_spaces():
    """🔴 `만원` 이 `백만원` 앞에 오면 `백|만원` 으로 갈려 100배를 놓친다.
    공백을 허용해도 그 순서가 유지되어야 한다."""
    assert values("1,000백만원") == [1_000e6]
    assert values("1,000백만 원") == [1_000e6]
    assert values("5천억 원") == [5e11]
    assert values("7백억 원") == [7e10]


def test_thousand_ten_thousand_marker_is_deliberately_absent():
    """🔴 `천만원` 을 넣으면 **거짓 경보가 난다.** 저장 답변의 13회가 전부 복합
    수사의 꼬리다 — 표지를 넣으면 `1조 8,287억 5천만 원` 의 꼬리가 별개의
    5천만원(5e7)이 되어 실측 `Q-113` 이 자릿수 오류로 오탐됐다.
    """
    assert "천만원" not in critic._SCALE
    assert 5e7 not in values("1조 8,287억 5천만 원")


def test_a_marker_split_across_lines_is_not_money():
    """공백은 **가로 공백만**이다. `\\s` 로 넓히면 표에서 줄이 갈린 표기가 붙어
    없던 금액이 생긴다."""
    assert values("1,000백만\n원") == []


def test_the_scale_list_has_exactly_one_source():
    """🔴 표지 목록이 두 곳에 있으면 한쪽만 고쳐진다 — 이 저장소의 반복 실패 양식.

    `_SCALE_UNITS`(순서) 와 `_SCALE`(배율) 이 같은 표지를 말해야 하고,
    채점기(`scripts/error_analysis.py`)는 자기 목록을 갖지 않고 critic 을 가리킨다.
    """
    assert set(critic._SCALE_UNITS) == set(critic._SCALE)
    assert critic._SCALE_ALT in ea._MONEY_RE.pattern
    assert all(k in ea._SCALE for k in critic._SCALE)


def test_the_scorer_reads_spaced_and_missing_markers_too():
    """채점기의 `_num_hit` 도 같은 표지를 읽는다. 후보는 **늘기만** 한다 —
    스케일값과 무단위값을 둘 다 담으므로 True 였던 판정이 False 가 되지 않는다."""
    assert ea._num_hit("매출액은 23,671,759백만 원입니다", "23671759000000", 0)
    assert ea._num_hit("계약금액은 5천억 원입니다", "500000000000", 0)
    # 🔴 종전에는 표지를 못 읽어 **무단위 값 하나만** 후보였다. 그 후보는 그대로 남는다.
    assert ea._num_hit("조달액은 1,000백만 원입니다", "1000", 0)
    assert ea._num_hit("조달액은 1,000백만 원입니다", "1000000000", 0)


# --- ③ R3b — judge 의 비대칭 ---------------------------------------------------

KEY_POINTS = ["두산에너빌리티는 신한울 3,4호기 원자로설비 2,338,117,950,000원을 공급한다",
              "한전기술은 계통설계 기술용역 25,605,030,000원을 맡는다"]


def test_silence_about_money_no_longer_passes():
    """🔴 이 파일의 알맹이다. `A8-lean` 이 `A8` 보다 높았던 기전을 닫는다.

    골든 요점이 금액을 명시하는데 답변이 금액을 **한 번도** 말하지 않으면
    `covers_key_points` 는 설 수 없다. 실측 `Q-110`/`v3 A8-lean` 의 문면이다.
    """
    case = {"answerable": True, "answer_key_points": KEY_POINTS}
    silent = "두산에너빌리티는 원자로설비를, 한전기술은 설계용역을 맡습니다."
    assert ea.covers_key_amounts(case, silent)["ok"] is False
    said = "두산에너빌리티는 원자로설비 2,338,117,950,000원을 공급합니다."
    assert ea.covers_key_amounts(case, said)["ok"] is True


def test_the_gate_does_not_require_the_golden_figure_itself():
    """🔴 **좁힌 자리다.** '골든 금액을 값으로 덮었는가' 로 세면 후보 86건 중 25건이
    현재 정답이고 그중 19건이 `Q-094` 다 — 그 문항의 **정답이** *"금액은 하나도
    바뀌지 않았다"* 라서 골든 수치를 옮겨 적지 않아도 옳다. 그래서 금액을
    **아무거나** 말했으면 통과시킨다.
    """
    case = {"answerable": True, "answer_key_points": KEY_POINTS}
    other = "정정 전후로 금액은 바뀌지 않았습니다 — 63,562,284천원 그대로입니다."
    assert ea.covers_key_amounts(case, other)["ok"] is True


def test_the_gate_stands_down_when_the_premise_is_absent():
    """전제가 안 서면 통과다 — 검사 대상이 없는 것과 결함을 섞지 않는다."""
    no_money = {"answerable": True, "answer_key_points": ["여섯 곳이다"]}
    assert ea.covers_key_amounts(no_money, "금액 없는 답")["premise"] is False
    assert ea.covers_key_amounts(no_money, "금액 없는 답")["ok"] is True
    small = {"answerable": True, "answer_key_points": ["주당배당금은 3,130원이다"]}
    assert ea.covers_key_amounts(small, "배당은 그대로다")["ok"] is True


def test_refusal_questions_are_out_of_the_premise():
    """🔴 `answerable=False` 는 **거절이 정답**이다. 금액 침묵을 결함으로 세면
    안전성·정보한계 트랙의 정답이 오답이 된다."""
    case = {"answerable": False, "answer_key_points": KEY_POINTS}
    assert ea.covers_key_amounts(case, "답변드릴 수 없습니다")["ok"] is True


def test_r3b_only_lowers_never_raises():
    """`A19` 와 같은 규율이다 — judge 가 오답이라 한 답변을 정답으로 올리지 않는다."""
    import inspect

    source = inspect.getsource(ea.analyse)
    assert 'judged_correct and numeric["ok"] and coverage["ok"]' in source


def test_overlapping_axes_are_both_counted():
    """🔴 `scored_by` 가 `judge+numeric+coverage` 로 겹칠 수 있다. 완전일치로 세면
    겹친 칸이 두 집계에서 **동시에** 사라진다."""
    rows = [{"question_id": "Q-1", "scored_by": "judge+numeric+coverage",
             "numeric_failed_kinds": ["restatement"],
             "covers_key_amounts_premise": True},
            {"question_id": "Q-2", "scored_by": "judge+coverage",
             "numeric_failed_kinds": [], "covers_key_amounts_premise": True},
            {"question_id": "Q-3", "scored_by": "judge", "numeric_failed_kinds": [],
             "covers_key_amounts_premise": False}]
    out = ea.scorer_r3_summary(rows)
    assert out["a19_overridden"] == ["Q-1"]
    assert out["r3b_overridden"] == ["Q-1", "Q-2"]
    assert out["r3b_premise"] == 2
