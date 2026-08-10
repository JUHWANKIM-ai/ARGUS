"""시스템 프롬프트와 few-shot exemplar."""
from __future__ import annotations

import re

CITATION_RE = re.compile(r"근거\s*:.*?접수번호\s*\d{14}", re.DOTALL)

_BASE_RULES = """당신은 DART 전자공시 원문만을 근거로 답하는 공시 분석 전문가입니다.

반드시 지킬 규칙:
1. 제공된 공시 근거에 있는 내용만 사용하십시오. 근거에 없으면 추측하지 마십시오.
2. 근거로 확인할 수 없으면 "공시에서 확인되지 않습니다" 라고 명시하고,
   무엇이 더 필요한지 역질문하십시오.
3. 모든 답변 끝에 근거를 다음 형식으로 표기하십시오.
   (근거: {보고서명} {기준연월}, 접수번호 {14자리}, {섹션경로})
4. 정정공시를 근거로 썼으면 정정본임을 밝히십시오.
5. 어느 시점의 값을 쓸지는 **질문이 정합니다.** 근거에 정정 체인이 함께 실려 있으면
   질문이 지목한 시점의 공시를 값의 출처로 삼으십시오.
   - "정정 후"·"정정 반영 후"·"최종"·"최신"·"얼마로 바뀌었는가" → 체인의 최신본
   - "최초 공시 당시"·"원공시"·"정정 전" → 체인의 원공시(1번)
   - 증감·차이·시간순 정리처럼 두 시점을 함께 묻는 질문 → 해당 시점 값을 모두 쓰십시오
   - 정정을 언급하지 않고 특정 시점(결의일·공시일·제출일)만 지목한 질문
     → 그 시점 공시에 기재된 값을 답하고, 이후 정정이 있었다는 사실을 덧붙이십시오
   - 시점 표현이 없으면 최신본 값을 쓰고 정정 이력을 밝히십시오
6. 미래 예측이나 투자 의견을 생성하지 마십시오.
7. 개인정보(주민번호·전화번호·이메일)는 출력하지 마십시오."""

_DIRECT = "\n\n답변은 간결하게, 질문이 요구한 것만 답하십시오."
_COT = """

먼저 단계적으로 근거를 확인하고 계산 과정을 적으십시오.
그 다음 마지막 문단에 최종 답변과 근거 표기를 쓰십시오."""

SYSTEM_ANSWER = _BASE_RULES + _DIRECT
SYSTEM_ANSWER_COT = _BASE_RULES + _COT


# --- 조 단위 `억원` 괄호 재설계 ------------------------------------------------
# `corpus/markdown._amount_line` 은 금액을 이렇게 싣는다.
#
#     - 계약금액: 4,928,968,079,043원 (49,290억원)
#
# 실측 Q-065 (`docs/total_plan.md §0b ③`). **우리 환산은 정확하다** —
# 4,928,968,079,043 / 1e8 = 49,289.68 → `49,290억원` 이 맞다. 그런데 모델이 그 값을
# 다시 쓸 때 `492,909억 원` 으로 옮겼다. 나눗셈을 틀린 것이 아니다 —
#
#     49,290  ->  492,909          세 자리 콤마 그룹이 한 자리 밀렸다 (배율 10.0002)
#
# 같은 답변의 신한울 `23,381억원` 은 정확히 옮겼고, 차이 계산(492,909 − 23,381 =
# 469,528)도 자기 값 안에서는 맞다. **틀린 것은 옮겨 적기 한 번뿐인데 답 전체가 10배로
# 나간다.** 괄호를 뗀 A4 는 원 단위 16자리를 그대로 복사해 정답이었다 (A5 가 오답).
#
# 그래서 처방은 '모델에게 조심하라고 말하기' 가 아니라 **밀릴 콤마 자리를 없애는 것**이다.
# 1조원 = 10,000억원이므로 억원 값이 5자리가 되는 순간 조·억으로 가른다.
#
#     (49,290억원)   ->   (4조 9,290억원)
#
# 이제 콤마가 한 자리 밀리면 `4조 92,909억원` 처럼 **눈에 보이게** 깨지고, 무엇보다
# 옮겨 적을 숫자가 4자리(9,290)라 밀 자리가 없다.
#
# 이 변환은 **프롬프트를 만들 때만** 한다. `markdown._amount_line` 을 고치면 근거
# 문자열이 통째로 바뀌어 `critic._figures`·`verify_completeness` 의 수치 대조와
# `ablation._EOK_SUFFIX`(A4 의 괄호 제거)까지 같이 흔들린다. 모델이 읽는 자리에서만
# 바꾸는 것이 이 결함에 필요한 최소 변경이다.

#: 1조원 = 10,000억원. 이 미만은 억원 값이 4자리 이하라 밀릴 콤마가 없다.
EOK_PER_JO = 10_000

#: `…원 (49,290억원)` — **우리가 붙인 주석**만 잡는다. `원` 을 앞에 두는 lookbehind 가
#: 그 조건이다. 원문 본문에 실린 `(3,500억원)` 같은 표기는 건드리지 않는다.
_EOK_ANNOTATION = re.compile(r"(?<=원)(\s*)\(\s*(-?[\d,]+)\s*억원\s*\)")


def _split_jo(eok: float) -> str:
    """`49290` -> `4조 9,290억원`. 나머지가 없으면 `4조원`."""
    sign = "-" if eok < 0 else ""
    jo, rest = divmod(int(abs(eok)), EOK_PER_JO)
    if not rest:
        return f"{sign}{jo:,}조원"
    return f"{sign}{jo:,}조 {rest:,}억원"


def split_trillion_eok(evidence: str) -> tuple[str, int]:
    """근거의 조 단위 `억원` 괄호를 조·억으로 가른다. (바뀐 근거, 바꾼 자리 수)."""
    hits = 0

    def _sub(match: re.Match) -> str:
        nonlocal hits
        try:
            eok = float(match.group(2).replace(",", ""))
        except ValueError:
            return match.group(0)
        if abs(eok) < EOK_PER_JO:
            return match.group(0)
        hits += 1
        return f"{match.group(1)}({_split_jo(eok)})"

    return _EOK_ANNOTATION.sub(_sub, evidence or ""), hits


#: 조 단위 금액이 근거에 실렸을 때만 붙는 규칙. 붙지 않으면 `SYSTEM_ANSWER` 그대로다.
#: 괄호를 갈라 놓아도 모델이 **원값에서 스스로 다시 환산**하면 같은 실수를 할 수 있다.
#: 규칙 8·9 가 그 경로를 막는다 — 계산은 원 단위로, 억원은 괄호 값을 그대로.
MONEY_UNIT_RULE = """
8. 금액은 `4,928,968,079,043원 (4조 9,290억원)` 처럼 **원 단위 원값**과 **조·억 환산값**이
   함께 실립니다. 두 표기는 같은 금액입니다.
9. 차이·합계·평균·비교는 **원 단위 원값으로** 계산하고 결과도 원 단위로 쓰십시오.
   조·억으로 옮겨 적을 때는 괄호 안 표기를 **그대로** 쓰고, 콤마 자리를 다시 찍거나
   원값에서 새로 환산하지 마십시오."""

#: open 문항 판정기 (02-golden-set B-5). closed 는 정규화 EM 이라 LLM 0회지만
#: 서술형은 판정기가 없으면 채점 자체가 불가능하다. 합산 점수를 만들지 않으려고
#: 3항목을 각각 0/1 로 받는다 — 어느 쪽이 나쁜지 사라지면 Phase H 가 무의미하다.
SYSTEM_OPEN_JUDGE = """당신은 공시 분석 서술형 답변을 채점하는 심사자입니다.

각각 0 또는 1 로 판정하십시오.
- covers_key_points: 모범답안 요소를 빠짐없이 포함했는가
- grounded: 모든 서술이 근거에서 확인되는가
- no_fabrication: 근거에 없는 내용을 사실처럼 쓰지 않았는가

반드시 아래 JSON 한 줄로만 답하십시오. 다른 말은 쓰지 마십시오.
{"covers_key_points":0,"grounded":0,"no_fabrication":0,"reason":"한 문장"}"""

SYSTEM_CRITIC = """당신은 공시 분석 답변의 근거를 검증하는 심사자입니다.

주어진 [근거]와 [답변]을 읽고 다음을 판정하십시오.
- supported: 답변의 모든 수치·사실이 근거에서 확인되는가
- has_citation: 접수번호를 포함한 근거 표기가 있는가
- hallucination: 근거에 없는 내용이 사실처럼 서술되었는가

반드시 아래 JSON 한 줄로만 답하십시오. 다른 말은 쓰지 마십시오.
{"supported": true/false, "has_citation": true/false, "hallucination": true/false, "reason": "한 문장"}"""


def build_answer_messages(question: str, evidence: str, use_cot: bool = False) -> list[dict]:
    """조 단위 금액이 실렸을 때만 괄호를 갈라 쓰고 규칙 8·9 를 붙인다.

    **조 단위 금액이 없으면 프롬프트는 종전과 한 바이트도 다르지 않다.** 재측정 범위를
    그 문항들로 묶기 위한 설계다 — A4·A1b 처럼 `ablation` 이 괄호를 떼는 arm 은
    변환할 대상이 아예 없으므로 프롬프트가 그대로다.
    """
    body, converted = split_trillion_eok(evidence)
    rules = _BASE_RULES + (MONEY_UNIT_RULE if converted else "")
    system = rules + (_COT if use_cot else _DIRECT)
    user = f"[공시 근거]\n{body}\n\n[질문]\n{question}"
    return [{"role": "system", "content": system},
            {"role": "user", "content": user}]


def build_critic_messages(evidence: str, answer: str) -> list[dict]:
    user = f"[근거]\n{evidence}\n\n[답변]\n{answer}"
    return [{"role": "system", "content": SYSTEM_CRITIC},
            {"role": "user", "content": user}]


#: 판정용 근거 절단 상한. 정기공시 근거는 한 문항이 12,000자라 그대로 다시 보내면
#: 판정 한 번이 생성 한 번만큼 비싸진다. 채점은 요소 대조라 앞부분으로 충분하다.
MAX_JUDGE_EVIDENCE_CHARS = 6_000


def build_open_judge_messages(question: str, evidence: str,
                              key_points: list[str] | None, answer: str) -> list[dict]:
    """02-golden-set B-5 규격 그대로. 판정이 흔들리면 측정이 무의미하다."""
    points = "\n".join(f"- {p}" for p in (key_points or [])) or "- (없음)"
    user = (f"[근거]\n{(evidence or '')[:MAX_JUDGE_EVIDENCE_CHARS]}\n\n"
            f"[질문]\n{question}\n\n[모범답안 요소]\n{points}\n\n[제출답변]\n{answer}")
    return [{"role": "system", "content": SYSTEM_OPEN_JUDGE},
            {"role": "user", "content": user}]
