import pytest

from argus.agent.budget import Budget, BudgetExceeded, should_terminate


def test_step_budget_enforced():
    b = Budget(max_steps=2)
    b.spend_step()
    b.spend_step()
    with pytest.raises(BudgetExceeded):
        b.spend_step()


def test_llm_call_budget_enforced():
    b = Budget(max_llm_calls=1)
    b.spend_llm(10, 5)
    with pytest.raises(BudgetExceeded):
        b.spend_llm(10, 5)


def test_token_budget_enforced():
    b = Budget(max_tokens=100)
    b.spend_llm(60, 30)
    with pytest.raises(BudgetExceeded):
        b.spend_llm(20, 20)


def test_remaining_reports_all_axes():
    b = Budget(max_steps=8, max_hops=3, max_llm_calls=6, max_tokens=20000)
    b.spend_step()
    r = b.remaining()
    assert r["steps"] == 7 and r["llm_calls"] == 6


@pytest.mark.parametrize("text,expected", [
    ("작업을 마쳤습니다 TERMINATE", True),
    ("아직 더 찾아야 합니다", False),
    ("terminate", True),
])
def test_should_terminate(text, expected):
    assert should_terminate(text) is expected
