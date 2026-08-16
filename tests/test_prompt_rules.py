"""조건부 프롬프트 규칙 (Q-047 단위 · Q-098 연결/별도) — LLM 0회.

`tests/test_prompts.py` 는 규칙 1~9 와 조 단위 괄호 재설계를 붙들고 있다. 그 구조를
건드리지 않으려고 새 규칙은 여기에 쓴다.

🔴 **이 파일이 지키는 성질은 두 가지다.**
  ① 조건이 안 서면 프롬프트가 **한 바이트도** 안 바뀐다 — 재측정 범위를 묶는 조건
  ② 노출 문항이 실측과 같다 — 저장 응답 전수로 센다 (LLM 0회 · 지출 0원)
"""
import json
import pathlib
import re

import pytest

from argus.agent import prompts

_ROOT = pathlib.Path(__file__).resolve().parents[1]

Q047_EVIDENCE = """## [사업보고서 2025] SK하이닉스 – II. 사업의 내용 > 4. 매출 및 수주상황
(단위: 백만원)

| 구분 | 제78기 |
|---|---|
| 합계 | 97,146,675 |

## [사업보고서 2025] 삼성전자 – II. 사업의 내용 > 4. 매출 및 수주상황
(단위 : 억원)

| 구분 | 제57기 |
|---|---|
| 합계 | 3,336,059 |"""

Q047_QUESTION = ("SK하이닉스가 2025년 사업보고서에 기재한 제78기 연결 매출액은 "
                 "삼성전자가 2025년 사업보고서에 기재한 제57기 연결 매출액보다 큰가?")

Q098_EVIDENCE = """## [반기보고서 2025] SK텔레콤 – III. 재무에 관한 사항 > 1. 요약재무정보
(단위: 백만원)
| 자본총계 | 11,262,987 |

## [반기보고서 2025] SK텔레콤 – III. 재무에 관한 사항 > 4. 재무제표 > 4-1. 재무상태표
(단위: 원)
| 자본총계 | 11,262,987,000,000 |

## [반기보고서 2025] SK텔레콤 – III. 재무에 관한 사항 > 2. 연결재무제표 > 2-1. 연결 재무상태표
(단위: 원)
| 자본총계 | 21,000,000,000,000 |"""

Q098_QUESTION = ("SK텔레콤 2025년 반기보고서의 요약연결재무정보 기준, 제42기 반기말 "
                 "자본총계는 제41기말 대비 백만원 단위로 얼마 늘었는가?")


# --- ① UNIT_COMPARE_RULE — 단위 미정규화 (Q-047) -------------------------------

def test_unit_rule_fires_on_the_q047_shape():
    """실측 Q-047. `97,146,675백만원`(97.1조) 을 `3,336,059억원`(333조) 보다 크다고 답했다."""
    assert prompts.needs_unit_rule(Q047_QUESTION, Q047_EVIDENCE) is True
    system = prompts.build_answer_messages(Q047_QUESTION, Q047_EVIDENCE)[0]["content"]
    assert "원 단위로 환산" in system and "자릿수만 보고 크기를 판단하지" in system


def test_unit_rule_needs_two_won_scales():
    """단위가 한 종류면 환산할 것이 없다."""
    one = Q047_EVIDENCE.replace("(단위 : 억원)", "(단위: 백만원)")
    assert prompts.needs_unit_rule(Q047_QUESTION, one) is False


def test_unit_rule_ignores_axes_that_are_not_money():
    """`%`·`건`·`명`·`백만 달러` 는 이 규칙이 말하는 축이 아니다."""
    assert prompts.won_unit_declarations(
        "(단위 : 백만원, %)\n(단위 : 백만 달러)\n(단위 : 건)\n(단위: 명, %)") == {"백만원"}


def test_unit_rule_needs_a_comparison_question():
    """🔴 이 조건이 노출을 그리드 9문항(정답 5)에서 2문항(정답 0)으로 줄인다."""
    assert prompts.needs_unit_rule("SK하이닉스의 제78기 매출액은 얼마인가?",
                                   Q047_EVIDENCE) is False


# --- ② CONSOLIDATED_RULE — 연결/별도 오선택 (Q-098) ----------------------------

def test_consolidated_rule_fires_on_the_q098_shape():
    """실측 Q-098. 질문이 '요약연결재무정보 기준' 인데 별도 재무상태표에서 읽었다."""
    assert prompts.needs_consolidated_rule(Q098_QUESTION, Q098_EVIDENCE) is True
    system = prompts.build_answer_messages(Q098_QUESTION, Q098_EVIDENCE)[0]["content"]
    assert "질문이 지목한 기준의 표에서 값을 읽으십시오" in system


def test_consolidated_rule_needs_both_tables():
    """연결 표만 오면 고를 것이 없다 — 규칙을 붙일 이유가 없다."""
    only_conso = """## [반기보고서 2025] SK텔레콤 – 2. 연결재무제표 > 2-1. 연결 재무상태표
| 자본총계 | 21,000,000,000,000 |

## [반기보고서 2025] SK텔레콤 – 2. 연결재무제표 > 2-2. 연결 포괄손익계산서
| 매출액 | 18,000,000,000,000 |"""
    assert prompts.needs_consolidated_rule(Q098_QUESTION, only_conso) is False


def test_consolidated_rule_needs_the_question_to_name_the_basis():
    assert prompts.needs_consolidated_rule("SK텔레콤의 자본총계는 얼마인가?",
                                           Q098_EVIDENCE) is False


# --- ③ 붙지 않을 때는 한 바이트도 안 바뀐다 -----------------------------------

@pytest.mark.parametrize("use_cot", [False, True])
def test_prompt_is_byte_identical_when_no_condition_holds(use_cot):
    base = prompts.SYSTEM_ANSWER_COT if use_cot else prompts.SYSTEM_ANSWER
    msgs = prompts.build_answer_messages(
        "한화오션 잠수함 계약의 계약금액은 얼마인가?",
        "- 계약금액: 575,000,000,000원", use_cot=use_cot)
    assert msgs[0]["content"] == base


def test_a_comparison_question_alone_does_not_add_the_rule():
    """질문만 비교형이고 근거에 단위 선언이 없으면 붙지 않는다."""
    msgs = prompts.build_answer_messages(
        "삼성중공업 계약금액이 한화오션보다 큰가?", "- 계약금액: 575,000,000,000원")
    assert msgs[0]["content"] == prompts.SYSTEM_ANSWER


# --- ④ 번호가 이어진다 --------------------------------------------------------
# 🔴 `MONEY_UNIT_RULE`(8·9)은 조건이 달라 따로 붙을 수도 함께 붙을 수도 있다. 그래서
# 조건부 규칙은 번호를 **붙일 때** 매긴다. 이 저장소가 다섯 번 겪은 실패 양식이 정확히
# "한쪽만 갱신되는 짝" 이다.

_NUMBERED = re.compile(r"^\s*(\d+)\.", re.M)

Q065_EVIDENCE = "- 계약금액: 4,928,968,079,043원 (49,290억원)"


@pytest.mark.parametrize("question,evidence", [
    ("q", "e"),
    ("q", Q065_EVIDENCE),
    (Q047_QUESTION, Q047_EVIDENCE),
    (Q098_QUESTION, Q098_EVIDENCE),
    (Q047_QUESTION, Q047_EVIDENCE + "\n" + Q065_EVIDENCE),
    (Q098_QUESTION + " 어느 쪽이 더 큰가?", Q098_EVIDENCE + "\n" + Q065_EVIDENCE),
])
def test_rule_numbers_run_from_one_without_a_gap(question, evidence):
    """겹치는 것도 비는 것도 결함이다 — `7` 다음이 `10` 이면 규칙이 잘린 것처럼 읽힌다."""
    system = prompts.build_answer_messages(question, evidence)[0]["content"]
    numbers = [int(n) for n in _NUMBERED.findall(system)]
    assert numbers == list(range(1, len(numbers) + 1)), f"규칙 번호가 어긋났다: {numbers}"


def test_both_conditional_rules_can_attach_together():
    """단위·연결 두 조건이 함께 서는 근거가 나올 수 있다. 번호가 갈리면 안 된다."""
    system = prompts.build_answer_messages(
        Q098_QUESTION + " 어느 쪽이 더 큰가?", Q098_EVIDENCE)[0]["content"]
    assert "원 단위로 환산" in system and "질문이 지목한 기준의 표" in system


#: 채점에 직결되는 네 규칙의 **내용**. 🔴 번호를 붙이지 않는다 (R2F) — 위
#: `test_rule_numbers_run_from_one_without_a_gap` 이 이미 *"번호는 붙일 때 매긴다"* 를
#: 지키는데, 여기에 `1. `~`4. ` 접두를 박아 두면 규칙을 하나 **앞에** 끼우는 정당한
#: 변경이 이 네 단언을 함께 깨뜨린다. 그때 고칠 것은 프롬프트가 아니라 이 리터럴이고,
#: 그것이 이 저장소가 `== 13` 에서 겪은 사고와 같은 모양이다. 순서는 아래에서 따로 본다.
SCORED_RULE_TEXTS = ("제공된 공시 근거에 있는 내용만 사용하십시오",
                     '근거로 확인할 수 없으면 "공시에서 확인되지 않습니다"',
                     "모든 답변 끝에 근거를 다음 형식으로 표기하십시오",
                     "정정공시를 근거로 썼으면 정정본임을 밝히십시오")


def test_the_scored_rules_one_to_four_are_never_touched():
    """🔴 규칙 1~4(근거만 사용 · 거절 문구 · 인용 형식 · 정정본 명시)는 채점에 직결된다.

    내용과 **상대 순서**를 지킨다. 절대 번호는 지키지 않는다 — 조건부 규칙이 붙는
    설계에서 절대 번호를 박으면 번호 규율(`붙일 때 매긴다`)과 서로 반박한다.
    """
    for question, evidence in [("q", "e"), (Q047_QUESTION, Q047_EVIDENCE),
                               (Q098_QUESTION, Q098_EVIDENCE)]:
        system = prompts.build_answer_messages(question, evidence)[0]["content"]
        at = []
        for text in SCORED_RULE_TEXTS:
            assert text in system, text
            at.append(system.index(text))
        assert at == sorted(at), f"채점 규칙 넷의 순서가 바뀌었다: {at}"
        # 🔴 넷이 **맨 앞 네 칸**을 차지하는 것까지 본다 (번호는 안 박는다).
        numbers = [int(n) for n in _NUMBERED.findall(system)]
        assert numbers[:4] == [1, 2, 3, 4], numbers
        first_conditional = system.index(SCORED_RULE_TEXTS[-1])
        assert all(system.index(t) < first_conditional or t == SCORED_RULE_TEXTS[-1]
                   for t in SCORED_RULE_TEXTS)


# --- ⑤ 노출 문항 수를 저장 응답 전수로 못 박는다 -------------------------------
# **어느 문항이 위험한지 오프라인으로 세라** (계획서 Phase 2 ③의 요구).
# 분모에 라벨을 붙인다: 그리드 88 · 밸류체인 12.

#: (응답 디렉터리, 라벨 보고서, 단위 규칙 노출, 연결/별도 규칙 노출).
RULE_EXPOSURE = {
    "그리드 88": ("artifacts/ablation/v3/A8/responses",
                  "artifacts/ablation/v3/A8/report.json",
                  {"Q-047", "Q-105"}, {"Q-098", "Q-100"}),
    "밸류체인 12": ("artifacts/ablation/v3/vc/A8/responses",
                    "artifacts/ablation/v3/vc/A8/report.json", set(), set()),
}

#: 🔴 **규칙에 노출된 문항 중 현재 정답인 것.** 성적이 떨어지면 여기부터 되돌린다.
#:
#: 🔴 **2026-08-14 (R2D 재측정) — 이 집합의 뜻이 바뀌었다.** 재측정 전에는 노출 4문항 중
#: 정답이 `Q-100` 하나였고, 그 하나가 규칙이 잡은 **인질**이었다. 재측정 후에는 넷 다
#: 정답이다 — `Q-047`·`Q-098`·`Q-105` 는 **규칙 때문에 정답이 된 수혜자**이고
#: `Q-100` 은 규칙을 견딘 원래의 인질이다. 즉 이 집합은 이제
#: *"노출됐고 정답인 문항"* 이고 그중 **위험한 것은 `Q-100` 하나뿐**이다.
#: 되돌릴 순서를 판단할 때 셋과 하나를 섞지 마라 — 수혜자를 되돌리면 성적이 내려간다.
EXPOSED_WHILE_CORRECT = {"Q-047", "Q-098", "Q-100", "Q-105"}
#: 그중 규칙이 **이득 없이 안고 있는** 문항. 이분탐색은 여기서 시작한다.
EXPOSED_HOSTAGE_ONLY = {"Q-100"}


@pytest.mark.parametrize("cohort", sorted(RULE_EXPOSURE))
def test_rule_exposure_matches_the_stored_responses(cohort):
    resp_dir, labels, unit_ids, conso_ids = RULE_EXPOSURE[cohort]
    resp_dir, labels = _ROOT / resp_dir, _ROOT / labels
    if not (resp_dir.is_dir() and labels.exists()):
        pytest.skip(f"{cohort} 산출물이 없다")
    rows = {r["question_id"]: r
            for r in json.loads(labels.read_text(encoding="utf-8"))["rows"]}

    unit, conso, exposed_correct = set(), set(), set()
    for path in sorted(resp_dir.glob("Q-*.json")):
        resp = json.loads(path.read_text(encoding="utf-8"))["response"]
        qid, question = resp["question_id"], resp["question"]
        evidence = resp["retrieved_context"] or ""
        hit_unit = prompts.needs_unit_rule(question, evidence)
        hit_conso = prompts.needs_consolidated_rule(question, evidence)
        if hit_unit:
            unit.add(qid)
        if hit_conso:
            conso.add(qid)
        if (hit_unit or hit_conso) and rows.get(qid, {}).get("correct"):
            exposed_correct.add(qid)

    assert unit == unit_ids, f"{cohort} 단위 규칙 노출이 달라졌다: {sorted(unit)}"
    assert conso == conso_ids, f"{cohort} 연결/별도 노출이 달라졌다: {sorted(conso)}"
    assert exposed_correct <= EXPOSED_WHILE_CORRECT, (
        f"{cohort} 현재 정답인데 새로 노출된 문항: {sorted(exposed_correct)}")


def test_the_hostage_is_a_subset_of_the_exposed_and_correct_set():
    """🔴 `EXPOSED_HOSTAGE_ONLY` 는 정의만 되고 **어느 단언도 읽지 않았다** (R2F).

    R2D 가 수혜자(셋)와 인질(하나)을 갈라 두 상수를 만들었는데, 갈라 놓은 사실을
    지키는 것이 없었다 — 한쪽만 갱신되면 이분탐색이 엉뚱한 문항에서 시작한다.
    인질은 정의상 **노출됐고 정답인** 문항의 부분집합이고, 수혜자와 겹치지 않는다.
    """
    assert EXPOSED_HOSTAGE_ONLY
    assert EXPOSED_HOSTAGE_ONLY <= EXPOSED_WHILE_CORRECT, (
        f"인질이 노출·정답 집합 밖이다: {sorted(EXPOSED_HOSTAGE_ONLY - EXPOSED_WHILE_CORRECT)}")
    # 🔴 인질은 규칙에 실제로 노출돼 있어야 한다 — 상수만 남고 노출이 사라지면
    #    "되돌릴 순서" 서술이 근거를 잃는다.
    resp_dir, labels, unit_ids, conso_ids = RULE_EXPOSURE["그리드 88"]
    assert EXPOSED_HOSTAGE_ONLY <= (unit_ids | conso_ids), (
        f"인질이 어느 규칙에도 노출돼 있지 않다: {sorted(EXPOSED_HOSTAGE_ONLY)}")


# --- ⑥ `A8-lean` — 규칙 8·9 를 빼는 arm 이 무엇을 걸고 있는가 --------------------
# 🔴 이 절이 지키는 성질 셋.
#   ① `lean_rules=True` 는 **규칙 8·9 만** 뗀다 (조건부 두 규칙과 번호 규율은 그대로)
#   ② 규칙 1~4 는 **두 경로에서 모두** 살아 있다
#   ③ 🔴 **인질을 저장 응답 전수로 센다** — 이 arm 은 겨냥 1건에 인질 12건이다.
#      성적이 떨어지면 되돌릴 순서를 이 상수가 든다.

#: 규칙 8·9 가 붙는 문항. `split_trillion_eok` 이 발화하는 문항과 정의상 같다.
#: (응답 디렉터리, 라벨 보고서, 노출 문항, **그중 현재 정답**).
MONEY_RULE_EXPOSURE = {
    "전체 88": ("artifacts/ablation/v3/A8/responses",
                "artifacts/ablation/v3/A8/report.json",
                {"Q-007", "Q-014", "Q-018", "Q-022", "Q-023", "Q-025", "Q-030",
                 "Q-031", "Q-033", "Q-035", "Q-061", "Q-062", "Q-065", "Q-083",
                 "Q-087", "Q-097"},
                {"Q-007", "Q-014", "Q-022", "Q-025", "Q-030", "Q-031", "Q-033",
                 "Q-035", "Q-061", "Q-062", "Q-065", "Q-083"}),
    "밸류체인 12": ("artifacts/ablation/v3/vc/A8/responses",
                    "artifacts/ablation/v3/vc/A8/report.json",
                    {"Q-107", "Q-108", "Q-109", "Q-110", "Q-111", "Q-112",
                     "Q-113", "Q-114", "Q-115", "Q-117"},
                    {"Q-108", "Q-110", "Q-111", "Q-112", "Q-114"}),
}

#: 🔴 이 arm 의 **유일한 겨냥** (R2+ §4 · 기전까지 재확인됐다). 규칙 8·9 가 답변 어휘를
#: `계약금액` → `공시 금액` 으로 바꿔 `critic._says_amount` 게이트가 닫혔고, 그래서 13자리
#: 전사 오류를 `amount` 검사가 못 잡았다. 정답값은 근거에 **있다**.
MONEY_RULE_TARGET = "Q-018"

#: 🔴 규칙이 **지키고 있던** 문항. `MONEY_UNIT_RULE` 은 이 문항의 콤마 밀림(답이 10배)을
#: 막으려고 생겼다. 되돌릴 순서의 첫 칸이다.
MONEY_RULE_GUARDS = "Q-065"


@pytest.mark.parametrize("cohort", sorted(MONEY_RULE_EXPOSURE))
def test_the_money_rule_hostages_match_the_stored_responses(cohort):
    """🔴 **겨냥 1건 대 인질 12건.** 이 비대칭이 `A8-lean` 판정의 전부다 — 상수가 낡으면
    되돌릴 순서가 엉뚱한 문항에서 시작한다."""
    resp_dir, labels, exposed_ids, correct_ids = MONEY_RULE_EXPOSURE[cohort]
    resp_dir, labels = _ROOT / resp_dir, _ROOT / labels
    if not (resp_dir.is_dir() and labels.exists()):
        pytest.skip(f"{cohort} 산출물이 없다")
    rows = {r["question_id"]: r
            for r in json.loads(labels.read_text(encoding="utf-8"))["rows"]}

    exposed, correct = set(), set()
    for path in sorted(resp_dir.glob("Q-*.json")):
        resp = json.loads(path.read_text(encoding="utf-8"))["response"]
        if not prompts.split_trillion_eok(resp["retrieved_context"] or "")[1]:
            continue
        exposed.add(resp["question_id"])
        if rows.get(resp["question_id"], {}).get("correct"):
            correct.add(resp["question_id"])

    assert exposed == exposed_ids, f"{cohort} 규칙 8·9 노출이 달라졌다: {sorted(exposed)}"
    assert correct == correct_ids, (
        f"{cohort} 노출·정답 집합이 달라졌다 — 인질 수가 바뀌었다: {sorted(correct)}")
    assert correct <= exposed


def test_the_target_and_the_guarded_question_are_both_exposed():
    """겨냥도 인질도 **노출 집합 안**이어야 한다. 상수만 남고 노출이 사라지면 판정 서술이
    근거를 잃는다."""
    exposed = MONEY_RULE_EXPOSURE["전체 88"][2]
    correct = MONEY_RULE_EXPOSURE["전체 88"][3]
    assert MONEY_RULE_TARGET in exposed and MONEY_RULE_TARGET not in correct
    assert MONEY_RULE_GUARDS in exposed and MONEY_RULE_GUARDS in correct


@pytest.mark.parametrize("use_cot", [False, True])
def test_lean_rules_removes_exactly_the_money_rule(use_cot):
    """조 단위 괄호가 있어도 `lean_rules=True` 면 프롬프트가 **기본 규칙 그대로**다."""
    base = prompts.SYSTEM_ANSWER_COT if use_cot else prompts.SYSTEM_ANSWER
    full = prompts.build_answer_messages("q", Q065_EVIDENCE, use_cot=use_cot)
    lean = prompts.build_answer_messages("q", Q065_EVIDENCE, use_cot=use_cot,
                                         lean_rules=True)
    assert full[0]["content"] != base                      # A8 은 규칙 8·9 가 붙는다
    assert lean[0]["content"] == base                      # A8-lean 은 안 붙는다
    # 🔴 근거는 그대로 갈라진다 — 괄호 가르기는 `calc_values` 축이고 규칙이 아니다.
    assert lean[1]["content"] == full[1]["content"]
    assert "(4조 9,290억원)" in lean[1]["content"]


def test_lean_rules_does_nothing_when_the_money_rule_would_not_attach():
    """🔴 조건이 안 서면 두 arm 의 프롬프트가 **한 바이트도** 다르지 않다 — 재측정
    범위가 노출 문항으로 묶이는 근거다 (전체 88 중 72문항이 여기 해당한다)."""
    for question, evidence in [("q", "e"), (Q047_QUESTION, Q047_EVIDENCE),
                               (Q098_QUESTION, Q098_EVIDENCE),
                               ("계약금액은 얼마인가?", "- 계약금액: 575,000,000,000원")]:
        full = prompts.build_answer_messages(question, evidence)
        lean = prompts.build_answer_messages(question, evidence, lean_rules=True)
        assert full == lean, question


@pytest.mark.parametrize("question,evidence", [
    ("q", Q065_EVIDENCE),
    (Q047_QUESTION, Q047_EVIDENCE + "\n" + Q065_EVIDENCE),
    (Q098_QUESTION + " 어느 쪽이 더 큰가?", Q098_EVIDENCE + "\n" + Q065_EVIDENCE),
])
def test_rule_numbers_still_run_from_one_without_a_gap_under_lean_rules(question, evidence):
    """🔴 8·9 를 뗀 자리에서 조건부 규칙 번호가 **8 부터** 다시 시작해야 한다.
    `10.` 이 남으면 규칙이 잘린 것처럼 읽힌다 — 번호는 붙일 때 매긴다."""
    system = prompts.build_answer_messages(question, evidence,
                                           lean_rules=True)[0]["content"]
    numbers = [int(n) for n in _NUMBERED.findall(system)]
    assert numbers == list(range(1, len(numbers) + 1)), f"규칙 번호가 어긋났다: {numbers}"


def test_the_scored_rules_survive_the_lean_arm():
    """🔴 규칙 1~4 는 채점 항목에 직결된다 (근거만 사용 · 거절 문구 · 인용 형식 ·
    정정본 명시). `A8-lean` 이 그중 하나라도 건드리면 다른 지표가 무너진다."""
    for question, evidence in [("q", Q065_EVIDENCE), (Q047_QUESTION, Q047_EVIDENCE),
                               (Q098_QUESTION, Q098_EVIDENCE)]:
        system = prompts.build_answer_messages(question, evidence,
                                               lean_rules=True)[0]["content"]
        at = []
        for text in SCORED_RULE_TEXTS:
            assert text in system, text
            at.append(system.index(text))
        assert at == sorted(at), f"채점 규칙 넷의 순서가 바뀌었다: {at}"
        numbers = [int(n) for n in _NUMBERED.findall(system)]
        assert numbers[:4] == [1, 2, 3, 4], numbers
    # 🔴 규칙 5(시점 지정 · `restatement` 축)와 6·7(안전)도 살아 있다 — 이 arm 이 뗀 것은
    #    8·9 하나뿐이고, 나머지를 뺀 것으로 오독되면 안 된다.
    system = prompts.build_answer_messages("q", Q065_EVIDENCE,
                                           lean_rules=True)[0]["content"]
    assert "어느 시점의 값을 쓸지는" in system
    assert "미래 예측이나 투자 의견" in system and "개인정보" in system


# --- ⑥ A27b 거절 규칙 (규약 감사 F-6) -------------------------------------------
# 🔴 이 arm 은 **프롬프트를 바꾸는 유일한 채택 후보**다. A25(부착)·A26(라우팅)은 생성
# 전후라 프롬프트가 바이트 동일이었지만 여기는 캐시가 통째로 빗나간다. 그래서 꺼졌을 때의
# 바이트 동일성이 특히 중요하다 — 깨지면 v1~v4 산출물 전부와 대조가 끊긴다.

_REFUSAL_CASES = [
    ("q", "e"),
    ("q", Q065_EVIDENCE),
    (Q047_QUESTION, Q047_EVIDENCE),
    (Q098_QUESTION, Q098_EVIDENCE),
    (Q098_QUESTION + " 어느 쪽이 더 큰가?", Q098_EVIDENCE + chr(10) + Q065_EVIDENCE),
]


@pytest.mark.parametrize("question,evidence", _REFUSAL_CASES)
def test_refusal_rule_off_is_byte_identical(question, evidence):
    assert (prompts.build_answer_messages(question, evidence, strict_refusal=False)
            == prompts.build_answer_messages(question, evidence))


@pytest.mark.parametrize("question,evidence", _REFUSAL_CASES)
def test_refusal_rule_keeps_numbers_contiguous(question, evidence):
    """🔴 `MONEY_UNIT_RULE` 이 8·9 를 쓴다. 정적으로 `8.` 을 적었으면 여기서 깨진다."""
    system = prompts.build_answer_messages(
        question, evidence, strict_refusal=True)[0]["content"]
    numbers = [int(n) for n in _NUMBERED.findall(system)]
    assert numbers == list(range(1, len(numbers) + 1)), f"규칙 번호가 어긋났다: {numbers}"


def test_refusal_rule_on_adds_exactly_one_rule():
    off = prompts.build_answer_messages("q", "e")[0]["content"]
    on = prompts.build_answer_messages("q", "e", strict_refusal=True)[0]["content"]
    assert "확인되지 않는다는 사실과 무엇이 더 필요한지만" in on
    assert "확인되지 않는다는 사실과 무엇이 더 필요한지만" not in off
    assert len(_NUMBERED.findall(on)) == len(_NUMBERED.findall(off)) + 1


def test_refusal_rule_names_no_forbidden_source():
    """🔴 **금지어를 적지 않는다.** 첫 판(`A27`)이 `DART`·`증권사` 를 나열했다가 안전성
    12문항에서 외부 출처 안내를 **1건 → 3건**으로 늘렸다 (`S-010` 증권사 리포트 ·
    `S-011` DART 시스템). 프롬프트에 적는 순간 그 말이 떠오른다 — 시스템 프롬프트가
    attention 의 36.9% 를 먹는다는 KDD '26 실측과 같은 방향이다.

    🔴 **규칙 문자열만 본다.** 기본 프롬프트 첫 줄이 원래 `DART 전자공시 원문만을…` 이라
    시스템 프롬프트 전체로 보면 이 단언은 성립할 수 없다.
    """
    rule = prompts.REFUSAL_RULE
    for forbidden in ("DART", "증권사", "금융기관", "법령", "홈페이지"):
        assert forbidden not in rule, f"금지어를 적으면 오히려 떠오른다: {forbidden}"
    assert "확인되지 않는다는 사실과 무엇이 더 필요한지만" in rule
