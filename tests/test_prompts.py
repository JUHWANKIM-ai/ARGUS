import pytest

from argus.agent.prompts import (CITATION_RE, MONEY_UNIT_RULE, SYSTEM_ANSWER,
                                 SYSTEM_ANSWER_COT, build_answer_messages,
                                 split_trillion_eok)


def test_system_prompt_states_hard_rules():
    for rule in ["공시에서 확인되지 않", "접수번호", "추측"]:
        assert rule in SYSTEM_ANSWER


# --- A7 ② 시점 지정 질의 -------------------------------------------------------
# 체인 hop 이 붙은 뒤 파이프라인이 **항상 최신값으로 수렴**한다. 실측 Q-022 는
# '2025년 3월에 결의한' 으로 시점을 못박았는데 체인 최신본 값을 답했고, 채점기의
# 이유가 그대로다 — "제출답변에는 최초 공시가 아닌 최종 수정된 공시 내용만 포함".
#
# 🔴 반대 방향이 더 위험하다. Q-005·Q-006·Q-052 는 **같은 문장 안에 날짜가 있는데
# 정답이 최신값**이다 ("2023년 6월에 공시한 … 정정공시에서 얼마로 바뀌었는가"). 그래서
# 규칙의 축은 '질문에 날짜가 있는가' 가 아니라 **질문이 어느 시점의 값을 요구하는가** 다.


def test_time_anchor_rule_is_in_both_prompt_variants():
    for system in (SYSTEM_ANSWER, SYSTEM_ANSWER_COT):
        assert "최초 공시 당시" in system          # 원공시를 요구하는 표현
        assert "정정 반영 후" in system            # 최신본을 요구하는 표현
        assert "최신본" in system


def test_time_anchor_rule_keeps_latest_as_the_default():
    """시점 표현이 없으면 최신본이다 — 종전 동작을 규칙으로 못박아 둔다."""
    assert "시점 표현이 없으면" in SYSTEM_ANSWER


def test_time_anchor_rule_covers_two_point_questions():
    """증감·차이 질의는 두 시점이 다 필요하다. '최신본만' 으로 뭉개면 Q-048·Q-061 이 깨진다."""
    assert "증감" in SYSTEM_ANSWER or "차이" in SYSTEM_ANSWER


def test_build_messages_shape():
    msgs = build_answer_messages("질문", "근거", use_cot=False)
    assert msgs[0]["role"] == "system"
    assert msgs[-1]["role"] == "user"
    assert "근거" in msgs[-1]["content"]


def test_cot_flag_changes_instruction():
    plain = build_answer_messages("q", "e", use_cot=False)[0]["content"]
    cot = build_answer_messages("q", "e", use_cot=True)[0]["content"]
    assert cot != plain
    assert "단계" in cot


# --- 조 단위 `억원` 괄호 재설계 (Q-065 실측) -----------------------------------
# `docs/total_plan.md §0b ③`. 우리 환산은 정확했다 — 4,928,968,079,043 / 1e8 =
# 49,289.68 → `49,290억원`. 모델이 그 5자리를 다시 쓸 때 콤마 그룹을 한 자리 밀어
# `492,909억 원` 으로 옮겼다 (배율 10.0002). 괄호를 뗀 A4 는 원 단위 16자리를 그대로
# 복사해 정답이었고 A5 가 오답이다 — **arm 이 원인**이라 처방이 뒤집힌 자리다.

Q065_EVIDENCE = ("## [공급계약 체결] 두산에너빌리티 – Dukovany 5&6 NSSS Contract\n"
                 "- 계약금액: 4,928,968,079,043원 (49,290억원)\n"
                 "- 기간: 2025-12-15 ~ 2038-04-18 (12년 4개월)\n"
                 "- 계약금액: 2,338,117,950,000원 (23,381억원)\n"
                 "- 계약금액: 711,106,288,572원 (7,111억원)")


def test_trillion_eok_is_split_so_no_comma_can_slip():
    body, hits = split_trillion_eok(Q065_EVIDENCE)
    assert hits == 2
    assert "(4조 9,290억원)" in body and "(2조 3,381억원)" in body
    # 밀릴 콤마 자리가 남아 있으면 재설계가 아니다
    assert "49,290억원" not in body and "23,381억원" not in body


def test_sub_trillion_eok_is_left_alone():
    """1조 미만은 억원 값이 4자리 이하라 밀릴 콤마가 없다. 건드리지 않는다."""
    body, hits = split_trillion_eok("- 계약금액: 711,106,288,572원 (7,111억원)")
    assert hits == 0 and body.endswith("(7,111억원)")


def test_the_period_parenthesis_is_untouched():
    body, _ = split_trillion_eok(Q065_EVIDENCE)
    assert "(12년 4개월)" in body


def test_only_our_own_annotation_is_rewritten():
    """원문 본문에 실린 표기는 고치지 않는다 — 앞에 `원` 이 붙은 주석만 잡는다."""
    raw = "투자 규모는 (46,000억원) 수준으로 알려졌다."
    body, hits = split_trillion_eok(raw)
    assert hits == 0 and body == raw


@pytest.mark.parametrize("evidence,shown", [
    ("- 금액: 4,928,968,079,043원 (49,290억원)", "(4조 9,290억원)"),
    ("- 금액: 22,764,764,160,000원 (227,648억원)", "(22조 7,648억원)"),
    ("- 금액: 4,000,000,000,000원 (40,000억원)", "(4조원)"),        # 나머지가 없다
    ("- 금액: 4,012,300,000,000원 (40,123억원)", "(4조 123억원)"),
    ("- 금액: -4,928,968,079,043원 (-49,290억원)", "(-4조 9,290억원)"),
])
def test_split_shapes(evidence, shown):
    body, hits = split_trillion_eok(evidence)
    assert hits == 1 and shown in body


def test_money_rule_is_added_only_when_a_trillion_figure_is_present():
    hit = build_answer_messages("q", Q065_EVIDENCE)[0]["content"]
    miss = build_answer_messages("q", "- 계약금액: 711,106,288,572원 (7,111억원)")[0]["content"]
    assert MONEY_UNIT_RULE in hit
    assert miss == SYSTEM_ANSWER          # 한 바이트도 다르지 않다
    assert MONEY_UNIT_RULE not in miss


def test_prompt_is_byte_identical_without_a_trillion_figure():
    """재측정 범위를 조 단위 문항으로 묶기 위한 조건이다.

    `ablation` 이 괄호를 떼는 arm(A0·A1·A1b·A4)은 변환 대상이 아예 없으므로
    프롬프트가 종전 그대로다 — 그 arm 들은 재측정이 필요하지 않다.
    """
    for cot, base in ((False, SYSTEM_ANSWER), (True, SYSTEM_ANSWER_COT)):
        msgs = build_answer_messages("질문", "- 계약금액: 575,000,000,000원", use_cot=cot)
        assert msgs[0]["content"] == base
        assert msgs[1]["content"] == "[공시 근거]\n- 계약금액: 575,000,000,000원\n\n[질문]\n질문"


def test_money_rule_pins_arithmetic_to_the_won_figure():
    """조·억으로 답하면 채점기(`_num_hit`)가 `2조 5,909억원` 을 못 읽는다.

    `scripts/error_analysis._MONEY_RE` 는 숫자 하나 + 단위 하나만 읽으므로
    `2조 5,909억원` 은 2조와 5,909억으로 쪼개진다. Q-065 의 정답
    2,590,850,129,043 은 `tolerance=0` 이라 원 단위 표기만 적중한다.
    """
    system = build_answer_messages("q", Q065_EVIDENCE)[0]["content"]
    assert "원 단위 원값으로" in system and "결과도 원 단위로" in system


def test_citation_regex_matches_reference_format():
    text = "매출액은 333조원입니다 (근거: 사업보고서 2025.12, 접수번호 20260310002820, III-1 요약재무정보)"
    assert CITATION_RE.search(text)


def test_citation_regex_rejects_missing_receipt():
    assert not CITATION_RE.search("매출액은 333조원입니다 (근거: 사업보고서)")


# --- 근거 표기 검사는 규약을 재지 우리 문안을 재지 않는다 (2026-08-17) ----------
#
# 🔴 실측 4건(Q-030·Q-052·Q-087·Q-101)이 라벨 없이 14자리만 적었고 전부 추적 가능한
#    정당한 표기인데 '표기 없음' 으로 세어졌다. 아래 문자열은 Q-030 의 실제 답변 꼬리다.

def test_citation_without_the_label_still_counts():
    assert CITATION_RE.search(
        "(근거: [기정정] [유상증자결정] 고려아연 – 주요사항보고서(유상증자결정), "
        "2024-11-14, 20241114000001)")


def test_citation_with_the_label_still_counts():
    assert CITATION_RE.search(
        "(근거: 단일판매ㆍ공급계약체결, 2023-01-31, 접수번호 20230131800162)")


def test_a_date_alone_is_not_a_citation():
    """🔴 문턱을 낮추다 날짜가 걸리면 표기 없는 답변이 통과한다."""
    assert not CITATION_RE.search("(근거: 사업보고서 2024, 2024-11-14)")


def test_a_bare_amount_is_not_a_citation():
    assert not CITATION_RE.search("계약금액은 22,764,764,160,000원입니다")
