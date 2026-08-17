"""System prompts and few-shot exemplars."""
from __future__ import annotations

import re
from collections.abc import Sequence

#: Detects a citation in an answer. The literal word for receipt number is
#: not required: the convention asks for the filing name and date, and
#: requiring our own phrasing would test prompt style rather than the rule.
#: The 14-digit run is kept, since a date is not 14 consecutive digits and
#: nothing else of that length follows a citation marker.
#: See docs/design-rationale.md#citation-detection
CITATION_RE = re.compile(r"근거\s*:.*?(?:접수번호\s*)?\d{14}", re.DOTALL)

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


# --- Splitting large 억원 parentheses into 조 and 억 --------------------------
#
# Evidence renders amounts as `4,928,968,079,043원 (49,290억원)`. The model
# copies the parenthesised value and occasionally shifts a comma group,
# turning 49,290 into 492,909 and inflating the answer tenfold. The division
# itself is correct; only the transcription fails.
#
# Splitting the value at the 조 boundary removes the digits that can shift:
# `(4조 9,290억원)` leaves a four-digit figure to copy, and a shifted comma
# becomes visibly malformed.
#
# The transform runs at prompt build time only. Changing the evidence string
# itself would alter numeric comparison in critic and the ablation switches
# that strip these parentheses.
EOK_PER_JO = 10_000

#: Matches only parentheses we appended, identified by the preceding 원.
#: Figures written in the source narrative are left untouched.
_EOK_ANNOTATION = re.compile(r"(?<=원)(\s*)\(\s*(-?[\d,]+)\s*억원\s*\)")


def _split_jo(eok: float) -> str:
    """Format an 억 value as 조 and 억, omitting 억 when the remainder is zero."""
    sign = "-" if eok < 0 else ""
    jo, rest = divmod(int(abs(eok)), EOK_PER_JO)
    if not rest:
        return f"{sign}{jo:,}조원"
    return f"{sign}{jo:,}조 {rest:,}억원"


def split_trillion_eok(evidence: str) -> tuple[str, int]:
    """Split large 억원 parentheses in the evidence.

    Returns the rewritten evidence and the number of sites changed.
    """
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


#: Appended only when the evidence carries a 조-scale amount. Splitting the
#: parentheses removes the transcription hazard, and these rules close the
#: remaining path where the model re-derives the 억 value from the raw figure.
#: Calculations are to be performed on raw won values.
MONEY_UNIT_RULE = """
8. 금액은 `4,928,968,079,043원 (4조 9,290억원)` 처럼 **원 단위 원값**과 **조·억 환산값**이
   함께 실립니다. 두 표기는 같은 금액입니다.
9. 차이·합계·평균·비교는 **원 단위 원값으로** 계산하고 결과도 원 단위로 쓰십시오.
   조·억으로 옮겨 적을 때는 괄호 안 표기를 **그대로** 쓰고, 콤마 자리를 다시 찍거나
   원값에서 새로 환산하지 마십시오."""

#: Keeps a refusal from inventing its grounds. Audited answers refused
#: correctly and then supplied legal reasoning present in no filing, or pointed
#: at sources outside the corpus.
#:
#: The rule states only what to do and names no forbidden term: an earlier
#: version listed them and tripled the very behaviour it targeted, since naming
#: a source in the system prompt is what brought it to mind.
#:
#: The number is not fixed, because the money-unit rules occupy fixed numbers
#: when present. The same dynamic numbering as the conditional rules applies.
#: See docs/design-rationale.md#refusal-rule
REFUSAL_RULE = """
{n}. 답할 수 없을 때는 **공시에서 확인되지 않는다는 사실과 무엇이 더 필요한지만**
   쓰십시오. 그 두 가지 밖의 설명·조언·근거 표기를 덧붙이지 마십시오."""

# --- Two conditional rules --------------------------------------------------
# The system prompt draws far more attention than the evidence does, so adding
# rules worsens that ratio. Both rules are therefore attached only when the
# evidence and the question actually call for them, and the questions each one
# would reach were counted before it was added.
#
# Rule numbers are assigned at attachment time. Hard-coding them would leave a
# gap whenever a conditional rule is absent, which reads as a truncated list;
# a test fixes that the numbering runs unbroken under every combination.
# See docs/design-rationale.md#conditional-prompt-rules

#: The scale marker of a unit declaration. Units differing per table is the
#: norm in periodic filings.
_UNIT_DECL = re.compile(r"\(\s*단위\s*[:：]\s*([^)]{1,12}?)\s*\)")

#: Domestic currency units only. Percentages, counts and foreign currency are
#: not the axis this rule addresses.
WON_UNITS = ("조원", "억원", "백만원", "만원", "천원", "원")

#: Whether the question compares magnitudes. This condition cuts the exposure
#: from nine questions to two.
_COMPARE_ASK = re.compile(r"보다\s*(큰|많|작|적|높|낮)|어느\s*(쪽|회사|기업|것)|"
                          r"더\s*(큰|많|작|적|높|낮)|비교")

#: Evidence block headings belonging to the financial statements.
_FIN_HEAD = re.compile(r"^##\s+(.*(?:재무상태표|재무제표|요약재무정보|포괄손익|손익계산서).*)$",
                       re.M)
_CONSOLIDATED_ASK = re.compile(r"연결|별도")

#: Two amounts declared in different units were compared on digit count alone
#: and the conclusion inverted. Normalising the units is enough to reach the
#: golden answer even with a transcription error still present.
#: See docs/design-rationale.md#conditional-prompt-rules
UNIT_COMPARE_RULE = """
{n}. 표마다 **단위 표기가 다릅니다** (`(단위 : 억원)` · `(단위 : 백만원)`). 서로 다른 표의
    수를 비교하거나 더하기 전에 **원 단위로 환산**하십시오. 자릿수만 보고 크기를 판단하지
    마십시오 — `1,000,000백만원` 은 1조원이고 `50,000억원` 은 5조원이라 뒤가 더 큽니다."""

#: A question that pinned the consolidated basis was answered from the
#: separate statement. The arithmetic was correct; the wrong table was read.
#: See docs/design-rationale.md#conditional-prompt-rules
CONSOLIDATED_RULE = """
{n}. 근거에 **연결** 재무제표와 **별도** 재무제표가 함께 실려 있고 같은 항목이 서로 다른
    값으로 나옵니다. 질문이 지목한 기준의 표에서 값을 읽으십시오. 표 제목에 `연결` 이
    없으면 별도 재무제표입니다."""


def won_unit_declarations(evidence: str) -> set[str]:
    """Domestic currency units the evidence declares, dropping any axis
    appended after them."""
    out = set()
    for declared in _UNIT_DECL.findall(evidence or ""):
        token = declared.split(",")[0].split("/")[0].strip()
        if token in WON_UNITS:
            out.add(token)
    return out


def needs_unit_rule(question: str, evidence: str) -> bool:
    """Whether the question requires comparing figures declared in different
    units."""
    return (len(won_unit_declarations(evidence)) >= 2
            and bool(_COMPARE_ASK.search(question or "")))


def needs_consolidated_rule(question: str, evidence: str) -> bool:
    """Whether both statement bases arrived together and the question pinned
    one of them."""
    heads = _FIN_HEAD.findall(evidence or "")
    return (any("연결" in h for h in heads) and any("연결" not in h for h in heads)
            and bool(_CONSOLIDATED_ASK.search(question or "")))


#: Judge for open questions. Closed ones are scored by normalised exact match
#: with no LLM call, but an open answer cannot be scored without a judge. The
#: three criteria are taken separately rather than summed: a combined score
#: would hide which of them failed.
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
                          lean_rules: bool = False,
                          strict_refusal: bool = False) -> list[dict]:
    """Attach the conditional rules only where the evidence and question call
    for them.

    With no condition met the prompt is byte-identical to the unconditional
    one, which confines any re-measurement to the questions that actually
    changed.

    `lean_rules` switches off the money-unit rules, after which the conditional
    numbering starts one step earlier. Its default leaves the full rule set,
    and the flag is read by the pipeline rather than here, since this module
    knows nothing about ablation arms.

    The evidence-side splitting of large parenthesised amounts runs regardless
    of that flag: splitting is evidence shaping while the rules are prompt
    reinforcement on top of it, and removing both together would stop being a
    single-variable manipulation.
    See docs/design-rationale.md#conditional-prompt-rules
    """
    body, converted = split_trillion_eok(evidence)
    money_rule = bool(converted) and not lean_rules
    rules = _BASE_RULES + (MONEY_UNIT_RULE if money_rule else "")
    number = 10 if money_rule else 8         # 8 and 9 belong to the money rules
    # With the strict-refusal flag off, the three lines below are inert and
    # the prompt is unchanged.
    if strict_refusal:
        rules += REFUSAL_RULE.format(n=number)
        number += 1
    for rule, needed in ((UNIT_COMPARE_RULE, needs_unit_rule(question, body)),
                         (CONSOLIDATED_RULE, needs_consolidated_rule(question, body))):
        if needed:
            rules += rule.format(n=number)
            number += 1
    system = rules + (_COT if use_cot else _DIRECT)
    user = f"[공시 근거]\n{body}\n\n[질문]\n{question}"
    return [{"role": "system", "content": system},
            {"role": "user", "content": user}]


#: Feedback instruction for the regeneration arm. Calling the same prompt
#: again at temperature zero is deterministic and returns the same answer, so
#: the reasons are what make the retry meaningful.
#:
#: No new evidence is supplied. Answering again from the same evidence is what
#: keeps this arm measuring the injection of diagnostics alone; adding evidence
#: would mix in a retrieval manipulation.
#: See docs/design-rationale.md#regeneration-prompt
REGENERATE_INSTRUCTION = """앞선 답변에 대해 자동 검증이 아래 결함을 찾았습니다.

{reasons}

같은 [공시 근거]만 보고 다시 답하십시오.
- 지적된 수치는 근거에서 다시 확인해 옮겨 적으십시오.
- 근거에 없는 수치를 쓰지 마십시오.
- 계산이 필요하면 근거의 원값으로 다시 계산해 식과 결과를 함께 적으십시오.
- 검증이 지적하지 않은 부분은 그대로 두십시오."""


def build_regeneration_messages(question: str, evidence: str, reasons: Sequence[str],
                                previous_answer: str, use_cot: bool = False,
                                lean_rules: bool = False) -> list[dict]:
    """Prompt for one regeneration carrying the failure reasons. Those reasons
    are the whole of this function.

    The first two messages are byte-identical to the initial generation. The
    previous answer and the verification reasons are appended as conversation
    turns, so the model knows what to fix and the prompt actually differs,
    which is what keeps a deterministic retry from being a no-op.

    The reasons come from two sources, ordered by the caller: calculation
    mismatches, and figures absent from the evidence. The second has no delta
    of its own, and this is the place that consumes its verdict.
    See docs/design-rationale.md#regeneration-prompt
    """
    reason_text = "\n".join(f"- {reason}" for reason in reasons if reason) or "- (사유 없음)"
    return build_answer_messages(question, evidence, use_cot, lean_rules) + [
        {"role": "assistant", "content": previous_answer or ""},
        {"role": "user", "content": REGENERATE_INSTRUCTION.format(reasons=reason_text)}]


def build_critic_messages(evidence: str, answer: str) -> list[dict]:
    user = f"[근거]\n{evidence}\n\n[답변]\n{answer}"
    return [{"role": "system", "content": SYSTEM_CRITIC},
            {"role": "user", "content": user}]


#: Evidence truncation cap for judging. Periodic evidence runs long enough
#: that resending it whole would make one judgment cost as much as one
#: generation, and scoring compares elements, for which the opening suffices.
MAX_JUDGE_EVIDENCE_CHARS = 6_000


def build_open_judge_messages(question: str, evidence: str,
                              key_points: list[str] | None, answer: str) -> list[dict]:
    """Built to the fixed scoring specification: a judgment that drifts makes
    the measurement meaningless."""
    points = "\n".join(f"- {p}" for p in (key_points or [])) or "- (없음)"
    user = (f"[근거]\n{(evidence or '')[:MAX_JUDGE_EVIDENCE_CHARS]}\n\n"
            f"[질문]\n{question}\n\n[모범답안 요소]\n{points}\n\n[제출답변]\n{answer}")
    return [{"role": "system", "content": SYSTEM_OPEN_JUDGE},
            {"role": "user", "content": user}]
