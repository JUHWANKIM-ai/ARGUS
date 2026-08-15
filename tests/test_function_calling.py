"""`A24` — `chat(tools=…)` 을 실제로 켜는 arm 과 그 왕복.

🔴 **게이트 F 의 유일한 미완이 여기였다.** `TOOL_SPECS` 15종은 v1 부터 등록돼 있었는데
`tools=` 가 `chat()` 에 **한 번도 전달된 적이 없다**(`ablation.py` 가 그 사실을 스스로
적어 뒀다). 이 파일은 그 배선을 못 박는다.

🔴 **배선이 없으면 Δ 가 조용히 0 이 되고, 우리는 그 0 을 "function calling 은 효과가
없다" 로 읽는다** — `ablation.py` 전체가 막으려는 바로 그 사고다. 그래서 여기서는
플래그가 존재하는지가 아니라 **요청 본문이 실제로 달라지는지**를 본다.
"""
from __future__ import annotations

import json

import pytest

from argus import ablation, clova
from argus.agent import pipeline, tools
from argus.agent.budget import Budget, BudgetExceeded

FLAGS = ablation.FLAG_NAMES


# --- arm 정의 -----------------------------------------------------------------

def test_a24_is_a_side_branch_and_never_hides_from_the_summary():
    assert "A24" in ablation.ARMS and "A24" in ablation.RUN_ORDER
    assert "A24" not in ablation.LADDER
    assert "A24" not in ablation.LOO_REMOVED
    assert "A24" not in ablation.DIAGNOSTIC_ARMS
    # 🔴 여기 안 잡히면 돈을 쓰고도 산출물 문서에 한 글자도 안 남는다 (`ORACLE` 사고).
    assert "A24" in ablation.SIDE_ARMS


def test_a24_runs_after_a18_and_before_the_dense_and_diagnostic_arms():
    order = list(ablation.RUN_ORDER)
    assert order.index("A24") > order.index("A18")
    for later in ("A2F", "A2", "ORACLE"):
        assert order.index("A24") < order.index(later), later


def test_a24_differs_from_a8_by_exactly_the_function_calling_flag():
    diff = [f for f in FLAGS
            if getattr(ablation.ARMS["A24"], f) != getattr(ablation.A8, f)]
    assert diff == ["function_calling"], diff


def test_function_calling_is_a_known_switch_and_off_everywhere_else():
    assert "function_calling" in FLAGS            # `FLAG_NAMES` 가 자동으로 잡는다
    for name, arm in ablation.ARMS.items():
        if name == "A24":
            continue
        assert arm.function_calling is False, name
    # 🔴 운영 기본값이 이것을 켜고 있으면 배포한 서버가 다른 것을 돌리고 있는 것이다.
    assert ablation.current().function_calling is False


def test_the_flag_is_reachable_on_every_baseline_so_delta_is_never_silently_zero():
    """🔴 도달 불가 목록에 넣으면 안 된다 — 생성 호출은 `_gather_meta` 조기 반환
    위에서도 골든 주입 위에서도 똑같이 선다. 넣었다면 아래가 예외로 죽는다."""
    assert "function_calling" not in ablation.META_UNREACHABLE_FLAGS
    assert "function_calling" not in ablation.ORACLE_UNREACHABLE_FLAGS
    assert "function_calling" not in ablation.META_UNREACHABLE_AXES
    ablation.replace(ablation.ARMS["A0"], name="a0+fc", function_calling=True)
    ablation.replace(ablation.ARMS["ORACLE"], name="oracle+fc", function_calling=True)


def test_function_calling_has_no_parent_dependency():
    """`calc_values`(A5)와 축이 다르다 — 근거의 괄호를 떼도 도구는 그대로 불린다."""
    for children in ablation.FLAG_DEPENDENCIES.values():
        assert "function_calling" not in children


# --- 요청 본문 — 🔴 여기가 게이트 F 의 실제 알맹이다 -------------------------------

def _payload(content: str = "답변", tool_calls: list | None = None,
             finish: str = "stop") -> dict:
    message: dict = {"role": "assistant", "content": content}
    if tool_calls is not None:
        message["toolCalls"] = tool_calls
    return {"result": {"message": message, "finishReason": finish,
                       "usage": {"promptTokens": 10, "completionTokens": 5,
                                 "totalTokens": 15}}}


def _recorder(script: list[dict]):
    """차례로 응답을 돌려주면서 요청 본문을 모으는 `_post` 대역."""
    seen: list[dict] = []

    def _post(url, headers, body, timeout=60):
        # 🔴 40001 재발 방지 — 실제 API 가 이 조합을 거부한다.
        assert not ("tools" in body and "maxTokens" in body), body.keys()
        seen.append(json.loads(json.dumps(body)))
        return 200, script[min(len(seen) - 1, len(script) - 1)], 0.01

    return _post, seen


def test_default_arm_still_never_sends_tools(monkeypatch):
    """🔴 스위치를 안 건드린 운영 경로는 종전과 요청 본문이 같아야 한다."""
    post, seen = _recorder([_payload("근거[1]에 따르면 답이다.")])
    monkeypatch.setattr(clova, "_post", post)
    budget = Budget()
    trace: list[str] = []
    messages = [{"role": "user", "content": "질문"}]

    clova.chat(messages, max_tokens=300, temperature=0.0, tag="answer")
    budget.spend_llm(10, 5)

    assert "tools" not in seen[0] and seen[0]["maxTokens"] == 300
    assert trace == []


def test_round_trip_executes_the_tool_and_feeds_the_result_back(monkeypatch):
    """🔴 **왕복이 이 태스크의 전부다.** 실행 결과를 되넣지 않으면 빈 답변이 나간다."""
    call = {"id": "call_1", "type": "function",
            "function": {"name": "calc_total", "arguments": {"values": [1699923, 665945]}}}
    post, seen = _recorder([_payload("", [call], finish="tool_calls"),
                            _payload("합계는 2,365,868원입니다 [1].")])
    monkeypatch.setattr(clova, "_post", post)

    budget, trace = Budget(), []
    answer = pipeline._answer_with_tools([{"role": "user", "content": "합을 구하라"}],
                                         300, ablation.ARMS["A24"], budget, trace)

    assert answer == "합계는 2,365,868원입니다 [1]."
    assert len(seen) == 2, "왕복이 일어나지 않았다 — 두 번째 호출이 없다"

    # ① 첫 호출은 도구를 내밀고 길이 상한을 포기한다 (API 제약).
    names = [s["function"]["name"] for s in seen[0]["tools"]]
    assert set(names) == {s["function"]["name"] for s in tools.TOOL_SPECS}
    assert seen[0]["toolChoice"] == "auto" and "maxTokens" not in seen[0]

    # ② 두 번째 호출에 **실행 결과가 실려 있다.** 여기가 비면 왕복이 흉내만 낸 것이다.
    tool_msgs = [m for m in seen[1]["messages"] if m["role"] == "tool"]
    assert len(tool_msgs) == 1 and tool_msgs[0]["toolCallId"] == "call_1"
    assert json.loads(tool_msgs[0]["content"])["value"] == 2_365_868
    assert [m["role"] for m in seen[1]["messages"][-2:]] == ["assistant", "tool"]

    # ③ 마지막 회차는 도구를 빼서 길이 상한을 되돌린다.
    assert "tools" not in seen[1] and seen[1]["maxTokens"] == 300


def test_budget_counts_every_generation_call(monkeypatch):
    """🔴 안 세면 게이트 F 의 `budget llm=` 이 거짓을 말한다."""
    call = {"id": "c", "type": "function",
            "function": {"name": "calc_total", "arguments": {"values": [1, 2]}}}
    post, _ = _recorder([_payload("", [call], finish="tool_calls"), _payload("3")])
    monkeypatch.setattr(clova, "_post", post)

    budget, trace = Budget(), []
    pipeline._answer_with_tools([{"role": "user", "content": "q"}], 300,
                                ablation.ARMS["A24"], budget, trace)
    assert budget.llm_calls == 2 and budget.tokens == 30
    assert any(line.startswith("generate fc=") for line in trace)
    assert any("fc tool=calc_total" in line for line in trace)


def test_no_tool_call_means_exactly_one_generation_call(monkeypatch):
    post, seen = _recorder([_payload("바로 답한다 [1].")])
    monkeypatch.setattr(clova, "_post", post)

    budget, trace = Budget(), []
    answer = pipeline._answer_with_tools([{"role": "user", "content": "q"}], 300,
                                         ablation.ARMS["A24"], budget, trace)
    assert answer == "바로 답한다 [1]." and len(seen) == 1 and budget.llm_calls == 1


def test_tool_rounds_are_capped_so_the_answer_stage_cannot_eat_the_budget(monkeypatch):
    """🔴 상한이 없으면 루프가 문항당 비용 상한을 지운다 (이 파일 머리말: 고정 횟수)."""
    call = {"id": "c", "type": "function",
            "function": {"name": "calc_total", "arguments": {"values": [1]}}}
    post, seen = _recorder([_payload("부분", [call], finish="tool_calls")])
    monkeypatch.setattr(clova, "_post", post)

    budget, trace = Budget(), []
    answer = pipeline._answer_with_tools([{"role": "user", "content": "q"}], 300,
                                         ablation.ARMS["A24"], budget, trace)
    # 도구를 내미는 회차 1 + 도구 없는 마지막 회차 1 = 2 가 상한이다.
    assert len(seen) == pipeline.MAX_TOOL_ROUNDS + 1 == 2
    assert "tools" not in seen[-1], "마지막 회차는 도구를 빼야 답이 나온다"
    assert answer == "부분"


def test_huge_tool_results_are_truncated(monkeypatch):
    """🔴 `read_section` 은 수만 자를 돌려준다 — 상한이 없으면 토큰 20,000 을 한 번에 태운다."""
    call = {"id": "c", "type": "function",
            "function": {"name": "read_section", "arguments": {"doc_id": "x", "path": "y"}}}
    post, seen = _recorder([_payload("", [call], finish="tool_calls"), _payload("답")])
    monkeypatch.setattr(clova, "_post", post)
    monkeypatch.setattr(tools, "execute", lambda name, args: {"text": "가" * 50_000})

    pipeline._answer_with_tools([{"role": "user", "content": "q"}], 300,
                                ablation.ARMS["A24"], Budget(), [])
    payload = [m for m in seen[1]["messages"] if m["role"] == "tool"][0]["content"]
    assert len(payload) <= pipeline.MAX_TOOL_RESULT_CHARS + len("…(잘림)")


def test_tool_errors_do_not_break_the_round_trip(monkeypatch):
    """`execute` 는 예외 대신 `{"error": …}` 를 준다. 그 사실이 프롬프트에 실려야 한다."""
    call = {"id": "c", "type": "function",
            "function": {"name": "없는도구", "arguments": {}}}
    post, seen = _recorder([_payload("", [call], finish="tool_calls"), _payload("답 [1]")])
    monkeypatch.setattr(clova, "_post", post)

    trace: list[str] = []
    answer = pipeline._answer_with_tools([{"role": "user", "content": "q"}], 300,
                                         ablation.ARMS["A24"], Budget(), trace)
    assert answer == "답 [1]"
    assert "error" in [m for m in seen[1]["messages"] if m["role"] == "tool"][0]["content"]
    assert any("fc tool=없는도구 error" in line for line in trace)


def test_arguments_accept_both_dict_and_json_string():
    """HCX 는 사전을 준다(실측). 형태 하나를 가정하면 도구가 조용히 안 불린다."""
    as_dict = {"function": {"name": "calc_total", "arguments": {"values": [1, 2]}}}
    as_text = {"function": {"name": "calc_total", "arguments": '{"values": [1, 2]}'}}
    broken = {"function": {"name": "calc_total", "arguments": "{"}}
    assert pipeline._tool_arguments(as_dict) == {"values": [1, 2]}
    assert pipeline._tool_arguments(as_text) == {"values": [1, 2]}
    assert pipeline._tool_arguments(broken) == {}


def test_first_call_failure_falls_through_to_the_a8_path(monkeypatch):
    """🔴 상류 장애를 arm 효과로 오독하지 않는다 — 첫 호출이 죽으면 그대로 올려보낸다."""
    def _boom(url, headers, body, timeout=60):
        return 500, {"raw": "upstream"}, 0.01

    monkeypatch.setattr(clova, "_post", _boom)
    with pytest.raises(RuntimeError):
        pipeline._answer_with_tools([{"role": "user", "content": "q"}], 300,
                                    ablation.ARMS["A24"], Budget(), [])


def test_budget_exhaustion_after_a_partial_answer_keeps_that_answer(monkeypatch):
    """이미 받아 둔 본문을 버리고 `UNANSWERABLE` 로 나가면 그것이 측정으로 굳는다."""
    call = {"id": "c", "type": "function",
            "function": {"name": "calc_total", "arguments": {"values": [1]}}}
    post, _ = _recorder([_payload("부분 답", [call], finish="tool_calls")])
    monkeypatch.setattr(clova, "_post", post)

    budget = Budget()
    for _ in range(budget.max_llm_calls - 1):
        budget.spend_llm(0, 0)                    # 남은 호출 1회
    trace: list[str] = []
    answer = pipeline._answer_with_tools([{"role": "user", "content": "q"}], 300,
                                         ablation.ARMS["A24"], budget, trace)
    assert answer == "부분 답"
    assert any("fc stop=budget_calls" in line for line in trace)


def test_budget_exhaustion_with_nothing_in_hand_raises(monkeypatch):
    call = {"id": "c", "type": "function",
            "function": {"name": "calc_total", "arguments": {"values": [1]}}}
    post, _ = _recorder([_payload("", [call], finish="tool_calls")])
    monkeypatch.setattr(clova, "_post", post)

    budget = Budget()
    for _ in range(budget.max_llm_calls - 1):
        budget.spend_llm(0, 0)
    with pytest.raises(BudgetExceeded):
        pipeline._answer_with_tools([{"role": "user", "content": "q"}], 300,
                                    ablation.ARMS["A24"], budget, [])


# --- 배선 — arm 을 켰을 때 정말 저 함수를 타는가 ----------------------------------

def test_answer_question_routes_through_the_tool_loop_only_for_a24(monkeypatch):
    """🔴 플래그가 배선 안 되면 조용한 no-op 이고 Δ 가 0 으로 보고된다."""
    cite = "근거: 삼성전자 계약 공시 접수번호 20240101000001"
    seen: list[str] = []
    monkeypatch.setattr(pipeline, "_answer_with_tools",
                        lambda *a, **k: seen.append("fc") or f"도구로 만든 답\n{cite}")
    monkeypatch.setattr(pipeline, "_gather_evidence",
                        lambda r, q, b: ("## 근거\n- 계약금액: 1,000원", []))
    monkeypatch.setattr(pipeline, "_verify",
                        lambda question, answer, evidence, r, trace, budget=None: answer)
    post, _ = _recorder([_payload(f"직접 답\n{cite}")])
    monkeypatch.setattr(clova, "_post", post)

    question = "삼성전자의 2024년 계약금액은 얼마인가?"
    with ablation.use("A8"):
        assert pipeline.answer_question("Q-x", question)["answer"] == f"직접 답\n{cite}"
    assert seen == []
    with ablation.use("A24"):
        out = pipeline.answer_question("Q-x", question)["answer"]
    assert out == f"도구로 만든 답\n{cite}"
    assert seen == ["fc"]


# --- G1 — 🔴 도구가 외부 모델을 부르지 않는가 --------------------------------------

def test_tools_never_reach_another_model(monkeypatch):
    """🔴 **G1 판단의 근거다.** function calling 은 HCX-005 자신이 도구를 부르는 것이라
    보조 모델을 답변 경로에 끼우는 조작이 아니다 — 다만 *도구가* 다른 모델을 부르면
    그 순간 위반이 된다. `agent/tools.py` 는 LLM·임베딩 모듈을 import 하지 않는다.
    """
    import inspect

    source = inspect.getsource(tools)
    for banned in ("from argus import clova", "from argus.clova", "argus.embed",
                   "import embed"):
        assert banned not in source, banned
    assert not any(name in tools.__dict__ for name in ("chat", "embed_text"))
