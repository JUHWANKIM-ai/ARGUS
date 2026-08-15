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


#: 🔴 도구를 내미는 arm 전부. 여기 없는 arm 이 도구를 내밀면 그것은 배선 사고다.
TOOL_ARMS = ("A24", "A24b", "A24b+A20")


def test_function_calling_is_a_known_switch_and_off_everywhere_else():
    assert "function_calling" in FLAGS            # `FLAG_NAMES` 가 자동으로 잡는다
    for name, arm in ablation.ARMS.items():
        if name in TOOL_ARMS:
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


# =============================================================================
# `A24b` — 🔴 도구 결과를 모델이 옮겨 적게 하지 않는다
# =============================================================================
# `A24` 는 **도구를 안 불러서** 진 것이 아니다 (37/88 이 불렀다). 기각 사유 셋을 하나씩
# 겨냥한 재시도가 `A24b` 이고, 이 절은 셋이 **실제로 배선됐는지**만 본다 — 배선이 풀리면
# Δ 가 조용히 0 이 되고 우리는 그 0 을 "치환도 소용없다" 로 읽는다.
#
#   ① 전사 오류      substitute_tool_results — 도구 결과로 답변의 수를 갈아 끼운다
#   ② 근거 희석      calc_tools_only         — 계산 7종만 내민다
#   ③ 길이 상한 소실  calc_queries_only       — 계산 질의에만 도구를 연다

from argus.agent import critic, router                            # noqa: E402

A24B = "A24b"
A24BS = "A24b+A20"


# --- arm 정의 -----------------------------------------------------------------

def test_a24b_arms_exist_and_never_hide_from_the_summary():
    for name in (A24B, A24BS):
        assert name in ablation.ARMS and name in ablation.RUN_ORDER
        assert name in ablation.SIDE_ARMS       # 없으면 돈을 쓰고도 문서에 안 남는다
        assert name not in ablation.LADDER
        assert name not in ablation.LOO_REMOVED
        assert name not in ablation.DIAGNOSTIC_ARMS


def test_a24b_runs_right_after_a24_so_the_pair_is_never_split():
    order = list(ablation.RUN_ORDER)
    assert order[order.index("A24") + 1] == A24B
    assert order[order.index(A24B) + 1] == A24BS
    for later in ("A2F", "A2", "ORACLE"):
        assert order.index(A24BS) < order.index(later), later


def test_a24_is_not_deleted_and_still_differs_from_a8_by_one_flag():
    """🔴 `A24` 는 기각 판정의 **물증**이다. 지우면 그 판정의 출처가 사라진다."""
    diff = [f for f in FLAGS
            if getattr(ablation.ARMS["A24"], f) != getattr(ablation.A8, f)]
    assert diff == ["function_calling"], diff


def test_a24b_differs_from_a8_by_the_three_tool_options():
    diff = {f for f in FLAGS
            if getattr(ablation.ARMS[A24B], f) != getattr(ablation.A8, f)}
    assert diff == {"function_calling", "calc_tools_only", "calc_queries_only",
                    "substitute_tool_results"}, sorted(diff)


def test_a24b_plus_a20_differs_from_a24b_by_exactly_the_a20_flag():
    """🔴 그래야 `Δ(A24b → A24b+A20)` 이 `A20` 의 몫으로 읽힌다."""
    diff = [f for f in FLAGS
            if getattr(ablation.ARMS[A24BS], f) != getattr(ablation.ARMS[A24B], f)]
    assert diff == ["substitute_verified"], diff
    assert ablation.ARMS[A24BS].substitute_verified is True


def test_the_sub_options_are_unreachable_without_function_calling():
    """🔴 부모가 꺼진 채로 켜면 코드가 도달하지 않고 Δ 가 조용히 0 이 된다."""
    children = ablation.FLAG_DEPENDENCIES["function_calling"]
    assert set(children) == {"calc_tools_only", "calc_queries_only",
                             "substitute_tool_results"}
    for child in children:
        with pytest.raises(ValueError):
            ablation.replace(ablation.A8, name="bad", **{child: True})


def test_the_new_switches_are_off_everywhere_else():
    for name, arm in ablation.ARMS.items():
        if name in (A24B, A24BS):
            continue
        for flag in ("calc_tools_only", "calc_queries_only", "substitute_tool_results"):
            assert getattr(arm, flag) is False, (name, flag)
    current = ablation.current()          # 🔴 운영 기본값이 켜고 있으면 배포가 다르다
    assert not (current.calc_tools_only or current.calc_queries_only
                or current.substitute_tool_results)


# --- ② 근거 희석 — 계산 도구만 내민다 ---------------------------------------------

def test_a24b_offers_only_the_seven_calc_tools(monkeypatch):
    """🔴 `A24` 실측: 모델이 부른 47건 중 26건이 검색이었고 이미 실린 근거를 다시 읽어
    희석시켰다 (회귀 8건 중 7건이 도구 호출 문항)."""
    post, seen = _recorder([_payload("바로 답한다 [1].")])
    monkeypatch.setattr(clova, "_post", post)

    pipeline._answer_with_tools([{"role": "user", "content": "합계는?"}], 300,
                                ablation.ARMS[A24B], Budget(), [])
    names = {s["function"]["name"] for s in seen[0]["tools"]}
    assert names == set(tools.CALC_TOOLS)
    assert names.isdisjoint(tools.RETRIEVAL_TOOLS)
    assert len(names) == 7


def test_calc_specs_is_the_calc_half_of_tool_specs():
    assert tools.CALC_SPECS == [s for s in tools.TOOL_SPECS
                                if s["function"]["name"] in tools.CALC_TOOLS]


# --- ③ 길이 상한 — 계산 질의에만 도구를 연다 ---------------------------------------

@pytest.mark.parametrize("question,kind", [
    # 🔴 겨냥 셋이 전부 열려야 한다.
    ("삼성전자 DX 부문과 DS 부문의 매출액 합계는 억원 단위로 얼마인가?", "합계·평균"),
    ("정정공시로 계약금액이 얼마나 증가했는가?", "차이·비교"),
    ("12척 선대 확보 투자에서 1척당 평균 투자금액은 얼마인가?", "단가"),
    ("주요 5대 매출처에 대한 매출 비중은 약 몇 %인가?", "비율·증감률"),
    ("공급계약의 계약기간은 몇 년 몇 개월인가?", "기간"),
    ("제63기 연결 자산총계와 별도 자산총계의 차이는 몇 백만원인가?", "합계·평균"),
])
def test_calc_questions_open_the_gate(question, kind):
    assert router.needs_calculation(question)
    assert router.calc_question_kind(question) == kind


@pytest.mark.parametrize("question", [
    "정정으로 확정된 매수인은 누구인가?",
    "2024년 6월에 공시한 장비 공급계약의 계약상대는 어디인가?",
    "이 공시의 접수번호는 무엇인가?",
    "사모 전환사채 발행의 표면이자율은 얼마인가?",
])
def test_non_calc_questions_keep_the_gate_closed(question):
    """🔴 **'금액'·'얼마' 자체는 신호가 아니다.** 이 코퍼스는 대부분 금액을 묻는데
    그 대부분은 옮겨 적기이고 계산이 아니다 — 도구는 그 축을 못 건드린다."""
    assert not router.needs_calculation(question)
    assert router.calc_question_kind(question) == ""


#: 두 질의는 **기업명이 있어야** 라우터가 살려 보낸다 (없으면 `out_of_universe`).
_PLAIN_Q = "삼성전자가 2024년 정정공시로 확정한 매수인은 누구인가?"
_CALC_Q = "삼성전자의 2024년 DX 부문과 DS 부문 매출액 합계는 얼마인가?"
_CITE = "근거: 삼성전자 계약 공시 접수번호 20240101000001"


def _wire_answer_question(monkeypatch, script):
    post, seen = _recorder(script)
    monkeypatch.setattr(clova, "_post", post)
    monkeypatch.setattr(pipeline, "_gather_evidence",
                        lambda r, q, b: ("## 근거\n- 계약금액: 1,000원", []))
    monkeypatch.setattr(pipeline, "_verify",
                        lambda question, answer, evidence, r, trace, budget=None: answer)
    return seen


def test_a24b_keeps_the_length_cap_on_a_non_calc_question(monkeypatch):
    """🔴 이것이 사유 ③의 배선이다. `A24` 는 도구를 한 번도 안 부른 `Q-106` 까지
    길이 상한 소실만으로 잃었다 — 그 대가를 계산 질의로 한정한다."""
    assert not router.needs_calculation(_PLAIN_Q)
    seen = _wire_answer_question(monkeypatch, [_payload(f"직접 답\n{_CITE}")])
    with ablation.use(A24B):
        out = pipeline.answer_question("Q-x", _PLAIN_Q)
    assert "tools" not in seen[0] and seen[0]["maxTokens"] == 300
    assert "fc gate=closed" in out["think_trace"]
    assert len(seen) == 1


def test_a24b_opens_tools_on_a_calc_question(monkeypatch):
    seen = _wire_answer_question(monkeypatch, [_payload(f"합계는 1,000원이다\n{_CITE}")])
    with ablation.use(A24B):
        out = pipeline.answer_question("Q-x", _CALC_Q)
    assert "tools" in seen[0] and "maxTokens" not in seen[0]
    assert "fc gate=open:합계·평균" in out["think_trace"]


def test_a24_opens_tools_on_every_question_and_writes_no_gate_line(monkeypatch):
    """🔴 `A24` 의 trace 는 **바이트 동일**이어야 한다 — 저장 산출물과 대조가 안 되면
    기각 판정의 출처 추적이 끊긴다."""
    seen = _wire_answer_question(monkeypatch, [_payload(f"직접 답\n{_CITE}")])
    with ablation.use("A24"):
        out = pipeline.answer_question("Q-x", _PLAIN_Q)
    assert "tools" in seen[0]
    assert "fc gate=" not in out["think_trace"]


# --- ① 🔴 전사(轉寫) 오류 — 도구 결과 치환. 이 절이 `A24b` 의 알맹이다 --------------
# `A24` 실측 `Q-044`:
#     calc_total([1699923, 665945]) -> 2,365,868   ← 골든과 바이트까지 같다
#     A24 의 답변                      2,366,968   ← 옮겨 적다 틀렸다
#     독립 스모크                      2,366,868   ← 또 틀렸고 틀린 자리가 다르다
# 도구는 계산을 대신해 줄 수 있어도 **옮겨 적기를 대신해 주지는 못한다.**

_TOTAL = {"id": "c", "type": "function",
          "function": {"name": "calc_total", "arguments": {"values": [1699923, 665945]}}}


def _tool_answer(monkeypatch, text, arm=A24B, call=None, result=None):
    """도구를 한 번 부르고 `text` 를 답으로 내놓는 왕복. 치환 뒤의 답과 trace 를 준다."""
    post, _ = _recorder([_payload("", [call or _TOTAL], finish="tool_calls"),
                         _payload(text)])
    monkeypatch.setattr(clova, "_post", post)
    if result is not None:
        monkeypatch.setattr(tools, "execute", lambda name, args: result)
    trace: list[str] = []
    answer = pipeline._answer_with_tools([{"role": "user", "content": "합계는?"}], 300,
                                         ablation.ARMS[arm], Budget(), trace)
    return answer, trace


def test_the_transcription_error_is_overwritten_with_the_tool_value(monkeypatch):
    """🔴 **`A24` 가 진 자리를 그대로 재현하고 뒤집는다.**"""
    answer, trace = _tool_answer(monkeypatch, "두 부문 합계는 2,366,968억 원입니다 [1].")
    assert answer == "두 부문 합계는 2,365,868억 원입니다 [1]."
    assert any("tool_substituted=tool_result:calc_total: 2,366,968 -> 2,365,868" in t
               for t in trace), trace


def test_a24_never_substitutes_so_its_measurement_stays_readable(monkeypatch):
    """🔴 `A24` 는 기각 판정의 물증이다 — 그 arm 의 답변이 여기서 바뀌면 안 된다."""
    answer, trace = _tool_answer(monkeypatch, "두 부문 합계는 2,366,968억 원입니다 [1].",
                                 arm="A24")
    assert answer == "두 부문 합계는 2,366,968억 원입니다 [1]."
    assert not any("tool_substituted" in t for t in trace)


def test_no_substitution_when_the_model_copied_the_value_correctly(monkeypatch):
    """조건 ① — 값이 이미 답변에 있으면 손댈 것이 없다."""
    text = "두 부문 합계는 2,365,868억 원입니다 [1]."
    answer, trace = _tool_answer(monkeypatch, text)
    assert answer == text
    assert "tool_substituted=- values=1" in trace


def test_no_substitution_when_two_different_candidates_share_the_magnitude(monkeypatch):
    """조건 ② — 후보의 **값**이 둘이면 어느 쪽을 말하려 한 것인지 정해지지 않는다."""
    text = "합계는 2,366,968억 원이고 다른 집계는 2,360,000억 원입니다 [1]."
    answer, trace = _tool_answer(monkeypatch, text)
    assert answer == text
    assert "tool_substituted=- values=1" in trace


def test_the_same_value_in_two_places_is_substituted_together(monkeypatch):
    """🔴 하나만 바꾸면 답변이 스스로 모순된다 (`SUBSTITUTE_LOG_TOLERANCE` 주석)."""
    answer, _ = _tool_answer(monkeypatch,
                             "합계 2,366,968억 원. 즉 2,366,968억 원이다 [1].")
    assert answer == "합계 2,365,868억 원. 즉 2,365,868억 원이다 [1]."


def test_no_substitution_when_the_same_tool_returns_two_different_values(monkeypatch):
    """조건 ③ — 값이 갈리면 `unique_recomputed` 가 `None` 을 내고 **아무것도** 안 바뀐다."""
    call2 = {"id": "d", "type": "function",
             "function": {"name": "calc_total", "arguments": {"values": [1, 2]}}}
    post, _ = _recorder([_payload("", [_TOTAL, call2], finish="tool_calls"),
                         _payload("합계는 2,366,968억 원입니다 [1].")])
    monkeypatch.setattr(clova, "_post", post)
    trace: list[str] = []
    answer = pipeline._answer_with_tools([{"role": "user", "content": "q"}], 300,
                                         ablation.ARMS[A24B], Budget(), trace)
    assert answer == "합계는 2,366,968억 원입니다 [1]."
    assert "tool_substituted=- values=2" in trace


@pytest.mark.parametrize("name,result", [
    # `calc_compare` 의 value 는 `"a"`/`"b"`/`"tie"` 이고 `calc_period` 는 `"8년 5개월"` 이다.
    ("calc_compare", {"value": "a", "diff": 2_365_868.0, "ratio": 1.5}),
    ("calc_period", {"value": "8년 5개월", "years": 8, "months": 5}),
    # 검색 도구는 `value` 키가 아예 없다 — 그래도 한 번 더 막는다.
    ("read_section", {"value": 2_365_868.0, "text": "본문"}),
])
def test_non_numeric_and_retrieval_results_are_never_substituted(monkeypatch, name,
                                                                 result):
    call = {"id": "c", "type": "function", "function": {"name": name, "arguments": {}}}
    text = "합계는 2,366,968억 원입니다 [1]."
    answer, trace = _tool_answer(monkeypatch, text, call=call, result=result)
    assert answer == text
    assert not any("tool_substituted" in t for t in trace)


def test_substitution_reuses_the_a20_machinery(monkeypatch):
    """🔴 **같은 일을 두 곳에 따로 만들면 유일성 규칙이 갈린다** — 이 저장소가 여러 번
    겪은 실패 양식이다. `A24b` 가 `A20` 의 치환 함수를 실제로 부르는지 본다."""
    seen: list[tuple] = []
    original = pipeline.substitute_recomputed

    def _spy(answer, reports, recomputed):
        seen.append((reports, recomputed))
        return original(answer, reports, recomputed)

    monkeypatch.setattr(pipeline, "substitute_recomputed", _spy)
    _tool_answer(monkeypatch, "합계는 2,366,968억 원입니다 [1].")
    assert seen, "critic.substitute_recomputed 를 안 부른다 — 치환 규칙이 갈렸다"
    reports, recomputed = seen[0]
    assert recomputed == {"tool_result:calc_total": 2_365_868.0}
    assert reports[0]["mismatches"][0]["targets"] == ["2,366,968"]


def test_tool_result_reports_match_the_verify_calculation_contract():
    """🔴 규격이 어긋나면 `unique_recomputed`·`substitute_recomputed` 가 조용히 아무것도
    안 한다 — Δ 가 0 이 되고 우리는 그 0 을 '치환은 소용없다' 로 읽는다."""
    reports = critic.tool_result_reports("합계는 2,366,968억 원입니다.",
                                         [("calc_total", 2_365_868.0)])
    reference = critic.verify_answer_arithmetic("1 + 1 = 3")
    assert set(reports[0]) == set(reference)
    assert critic.unique_recomputed(reports) == {"tool_result:calc_total": 2_365_868.0}
    assert reports[0]["failure_stage"] == critic.FAILURE_STAGE


def test_clean_answers_produce_no_reports_to_substitute():
    reports = critic.tool_result_reports("합계는 2,365,868억 원입니다.",
                                         [("calc_total", 2_365_868.0)])
    assert reports[0]["ok"] and not reports[0]["mismatches"]
    # 🔴 `None` 이 곧 '치환하지 않는다' 다 (`substitute_recomputed` 의 첫 줄).
    assert critic.unique_recomputed(reports) is None


def test_percent_tool_results_respect_the_answers_rounding():
    """🔴 `7.6%` 는 `7.63157…` 의 정당한 반올림이다 — 거짓 치환이면 정답이 뒤집힌다."""
    ok = critic.tool_result_reports("비중은 7.6%입니다.", [("calc_share", 7.63157)])
    assert ok[0]["ok"], ok[0]["mismatches"]
    bad = critic.tool_result_reports("비중은 7.9%입니다.", [("calc_share", 7.63157)])
    answer, done = critic.substitute_recomputed("비중은 7.9%입니다.", bad,
                                                critic.unique_recomputed(bad))
    assert answer == "비중은 7.6%입니다." and done
