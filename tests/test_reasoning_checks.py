"""`agent/reasoning`, 「추론 논리성」의 ⑥ 순서 오류 · ⑦ 자기부정.

**검출만 하고 처분하지 않는 모듈이다.** 자기부정이나 순서 오류를 코드가 고치려면
문장을 지우거나 다시 써야 하는데, 그것은 답변의 뜻을 바꾸는 일이라 결정적 규칙으로
감당할 범위를 넘는다.

기준과 시험은 `docs/reasoning-metric-2026-08-16.md`. 실측(A38 · 112문항): 둘 다 발화 0건.
"""
from __future__ import annotations

from argus.agent import reasoning


# --- ⑦ 자기부정 ------------------------------------------------------------------

def test_refusal_and_a_figure_in_one_body_is_flagged():
    hit = reasoning.self_negating(
        "공시에서 확인되지 않습니다. 계약금액은 532,040,000,000원입니다.")
    assert not hit["ok"] and "거절" in hit["why"]


def test_a_plain_refusal_is_clean():
    assert reasoning.self_negating(
        "공시에서 확인되지 않습니다. 제공된 70개사 범위로 질문해 주세요.")["ok"]


def test_a_plain_answer_is_clean():
    assert reasoning.self_negating("계약금액은 532,040,000,000원입니다.")["ok"]


def test_our_own_citation_block_is_not_the_model_speaking():
    """각주·근거 표기의 금액은 **우리가 붙인 것**이다. 본문만 본다."""
    answer = ("공시에서 확인되지 않습니다.\n\n"
              "(근거: 공급계약 체결 (2023-03-29), 접수번호 20230329801112, "
              "계약금액 532,040,000,000원)")
    assert reasoning.self_negating(answer)["ok"]


# --- ⑥ 순서 오류 -----------------------------------------------------------------

def test_a_shuffled_timeline_is_flagged():
    hit = reasoning.out_of_order(
        "2024-03-22 443,500,000,000원 → 2023-12-22 1,102,000,000,000원 "
        "→ 2025-06-05 905,200,000,000원")
    assert not hit["ok"]


def test_ascending_is_clean():
    assert reasoning.out_of_order(
        "2023-12-22 1,102,000,000,000원 → 2024-03-22 443,500,000,000원 "
        "→ 2025-06-05 905,200,000,000원")["ok"]


def test_descending_is_clean():
    """내림차순도 정당한 서술이다. 뒤섞인 것만 잡는다."""
    assert reasoning.out_of_order(
        "2025-06-05 905,200,000,000원, 2024-03-22 443,500,000,000원, "
        "2023-12-22 1,102,000,000,000원")["ok"]


def test_fewer_than_three_pairs_is_not_judged():
    """둘은 어느 쪽으로도 정렬돼 있어 판정에 뜻이 없다."""
    out = reasoning.out_of_order(
        "2024-03-22 443,500,000,000원 → 2023-12-22 1,102,000,000,000원")
    assert out["ok"] and out.get("skipped")


# --- 계약 -------------------------------------------------------------------------

def test_check_all_returns_only_firings():
    assert reasoning.check_all("계약금액은 532,040,000,000원입니다.") == []
    fired = reasoning.check_all(
        "확인되지 않습니다. 금액은 532,040,000,000원입니다.")
    assert [r["kind"] for r in fired] == ["self_negating"]


def test_the_module_has_no_disposition():
    """검출만 한다. 답변을 고치는 함수가 있으면 안 된다."""
    import inspect

    src = inspect.getsource(reasoning)
    assert ".replace(" not in src and "def fix" not in src
