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
#:
#: 🔴 **`A8-lean`(`ablation.lean_rules`)이 떼는 것이 정확히 이 문자열이다.**
#: 이 규칙이 지키던 것과 그 방어선이 어디까지 남는지:
#:
#:   지키던 것 ①  `Q-065` 콤마 한 자리 밀림(`49,290억` -> `492,909억` · 답이 10배).
#:                🔴 **1차 방어선은 규칙이 아니라 `split_trillion_eok` 이고 그것은 남는다** —
#:                괄호가 `(4조 9,290억원)` 이라 옮겨 적을 숫자가 4자리이고 밀 자리가 없다.
#:                규칙 8·9 는 "원값에서 스스로 다시 환산" 경로를 막는 2차 방어선이다.
#:   지키던 것 ②  `차이·합계는 원 단위 원값으로` — 조·억 단위로 계산하고 되돌리는 과정에서
#:                단위를 잃는 경로를 막는다. 🔴 이쪽은 규칙밖에 없다. 뺐을 때 무너지면
#:                `number_won` 문항의 `calc_mismatch`(`equation`·`restatement`)로 나타난다.
#:
#: 🔴 **인질 12건** — 규칙이 붙는 문항은 전체 88 중 16(밸류체인 12 중 10)이고 그중 현재
#: 정답이 12건(밸류체인 5)이다. 겨냥은 `Q-018` 1건이다. 되돌릴 순서는
#: `tests/test_prompt_rules.py::MONEY_RULE_EXPOSED_WHILE_CORRECT` 가 든다.
MONEY_UNIT_RULE = """
8. 금액은 `4,928,968,079,043원 (4조 9,290억원)` 처럼 **원 단위 원값**과 **조·억 환산값**이
   함께 실립니다. 두 표기는 같은 금액입니다.
9. 차이·합계·평균·비교는 **원 단위 원값으로** 계산하고 결과도 원 단위로 쓰십시오.
   조·억으로 옮겨 적을 때는 괄호 안 표기를 **그대로** 쓰고, 콤마 자리를 다시 찍거나
   원값에서 새로 환산하지 마십시오."""

# --- 조건부 규칙 두 개 (Phase 2 ②·③) ------------------------------------------
# 🔴 **KDD '26 `paper/idea/QA.pdf` 의 attention 배분을 앞에 두고 읽어야 한다** —
# system 36.9% · output 34.5% · **근거 14.0%** · question 10.8%. 시스템 프롬프트가 근거보다
# 2.6배 무겁다. 규칙을 늘리면 그 비율이 더 나빠진다. 그래서 두 규칙 다 **근거와 질문이 그
# 규칙을 실제로 필요로 할 때만** 붙는다 — `MONEY_UNIT_RULE` 이 이미 쓰던 설계다.
#
# 노출 문항을 LLM 0회로 세어 두고 붙였다 (v3 저장 응답, 분모는 그리드 88 · 밸류체인 12).
#
#   단위 규칙     원 계열 단위 2종 이상 + 질문이 비교형 → 그리드 **2문항**(Q-047·Q-105)
#                 **둘 다 현재 오답**이다. 정답 문항 노출 0
#                 (질문 조건을 빼면 9문항으로 늘고 그중 5문항이 현재 정답이다 — 그래서 뺀다)
#   연결/별도     연결·별도 표가 함께 실림 + 질문이 그 축을 지목 → 그리드 **2문항**
#                 (Q-098 오답 · Q-100 **정답**). 🔴 정답 1문항이 노출된다 — 성적이 떨어지면
#                 이분탐색에서 이 규칙을 먼저 되돌려라
#   밸류체인 12   두 규칙 다 노출 0문항
#
# 🔴 **번호는 붙일 때 매긴다.** `MONEY_UNIT_RULE`(8·9)은 조건이 달라 함께 붙을 수도 따로
# 붙을 수도 있다. 고정 번호를 박으면 그것이 빠졌을 때 `7` 다음이 `10` 이 되어 규칙이 잘린
# 것처럼 읽힌다. `tests/test_prompt_rules.py` 가 어떤 조합에서도 번호가 `1..N` 로 이어짐을
# 지킨다 — 겹치는 것도 비는 것도 결함이다.

#: `(단위 : 억원)` 선언에서 배율 표지만. 표마다 단위가 다른 것이 정기공시의 기본값이다.
_UNIT_DECL = re.compile(r"\(\s*단위\s*[:：]\s*([^)]{1,12}?)\s*\)")

#: 원 계열만 본다. `%`·`건`·`명`·`백만 달러` 는 이 규칙이 말하는 축이 아니다.
WON_UNITS = ("조원", "억원", "백만원", "만원", "천원", "원")

#: 질문이 **수의 크기를 맞대는가**. 이 조건이 노출을 9문항에서 2문항으로 줄인다.
_COMPARE_ASK = re.compile(r"보다\s*(큰|많|작|적|높|낮)|어느\s*(쪽|회사|기업|것)|"
                          r"더\s*(큰|많|작|적|높|낮)|비교")

#: 근거 블록 제목 중 재무제표 계열.
_FIN_HEAD = re.compile(r"^##\s+(.*(?:재무상태표|재무제표|요약재무정보|포괄손익|손익계산서).*)$",
                       re.M)
_CONSOLIDATED_ASK = re.compile(r"연결|별도")

#: 실측 Q-047. `97,146,675백만원`(97.1조) 과 `33,636,059억원`(3,363.6조)을 자릿수만 보고
#: 비교해 결론이 뒤집혔다. **단위만 정규화해도 답이 뒤집힌다** — 전사 오류(10.08배)가
#: 남아 있어도 3,363.6조 > 97.1조 라 골든("아니오")에 닿는다.
UNIT_COMPARE_RULE = """
{n}. 표마다 **단위 표기가 다릅니다** (`(단위 : 억원)` · `(단위 : 백만원)`). 서로 다른 표의
    수를 비교하거나 더하기 전에 **원 단위로 환산**하십시오. 자릿수만 보고 크기를 판단하지
    마십시오 — `1,000,000백만원` 은 1조원이고 `50,000억원` 은 5조원이라 뒤가 더 큽니다."""

#: 실측 Q-098. 질문이 '요약연결재무정보 기준' 을 못박았는데 **별도** 재무상태표에서 읽어
#: 302,133 을 냈다(골든 143,764). 연산은 정확했다 — 표를 잘못 골랐을 뿐이다.
CONSOLIDATED_RULE = """
{n}. 근거에 **연결** 재무제표와 **별도** 재무제표가 함께 실려 있고 같은 항목이 서로 다른
    값으로 나옵니다. 질문이 지목한 기준의 표에서 값을 읽으십시오. 표 제목에 `연결` 이
    없으면 별도 재무제표입니다."""


def won_unit_declarations(evidence: str) -> set[str]:
    """근거가 선언한 **원 계열** 단위. `(단위 : 백만원, %)` 처럼 뒤에 덧붙는 축은 자른다."""
    out = set()
    for declared in _UNIT_DECL.findall(evidence or ""):
        token = declared.split(",")[0].split("/")[0].strip()
        if token in WON_UNITS:
            out.add(token)
    return out


def needs_unit_rule(question: str, evidence: str) -> bool:
    """서로 다른 단위의 수를 맞대야 하는 질문인가."""
    return (len(won_unit_declarations(evidence)) >= 2
            and bool(_COMPARE_ASK.search(question or "")))


def needs_consolidated_rule(question: str, evidence: str) -> bool:
    """연결·별도 표가 **함께** 왔고 질문이 그 축을 지목했는가."""
    heads = _FIN_HEAD.findall(evidence or "")
    return (any("연결" in h for h in heads) and any("연결" not in h for h in heads)
            and bool(_CONSOLIDATED_ASK.search(question or "")))


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


def build_answer_messages(question: str, evidence: str, use_cot: bool = False,
                          lean_rules: bool = False) -> list[dict]:
    """근거·질문이 요구할 때만 조건부 규칙을 붙인다.

    **아무 조건도 안 서면 프롬프트는 종전과 한 바이트도 다르지 않다.** 재측정 범위를
    해당 문항으로 묶기 위한 설계다 — A4·A1b 처럼 `ablation` 이 괄호를 떼는 arm 은
    변환할 대상이 아예 없으므로 프롬프트가 그대로다.

    | 규칙 | 조건 | 겨냥 |
    |---|---|---|
    | `MONEY_UNIT_RULE` (8·9) | 조 단위 `억원` 괄호를 갈랐다 | Q-065 콤마 밀림 |
    | `UNIT_COMPARE_RULE` | 원 계열 단위 2종 + 비교형 질문 | Q-047 |
    | `CONSOLIDATED_RULE` | 연결·별도 표 공존 + 그 축을 지목한 질문 | Q-098 |

    🔴 `lean_rules` 는 **`A8-lean` arm 의 스위치**다 (`ablation.Arm.lean_rules`). 참이면
    `MONEY_UNIT_RULE` 을 붙이지 않고, 그러면 조건부 번호가 다시 `8` 부터 시작한다.
    **기본값 False 가 규칙 전량 = 종전 동작**이고, 스위치를 읽는 것은 파이프라인이다
    (`golden_oracle`·`shape_evidence` 와 같은 규약 — 이 모듈은 `ablation` 을 모른다).
    🔴 `split_trillion_eok` 은 이 플래그와 **무관하게** 그대로 돈다 — 괄호 가르기는
    근거 성형(`calc_values` 축)이고 규칙 8·9 는 그 위의 프롬프트 보강이다. 함께 떼면
    단일 변수 조작이 아니게 되고 `Q-065` 의 1차 방어선까지 사라진다.
    """
    body, converted = split_trillion_eok(evidence)
    money_rule = bool(converted) and not lean_rules
    rules = _BASE_RULES + (MONEY_UNIT_RULE if money_rule else "")
    number = 10 if money_rule else 8         # 8·9 는 MONEY_UNIT_RULE 이 쓴다
    for rule, needed in ((UNIT_COMPARE_RULE, needs_unit_rule(question, body)),
                         (CONSOLIDATED_RULE, needs_consolidated_rule(question, body))):
        if needed:
            rules += rule.format(n=number)
            number += 1
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
