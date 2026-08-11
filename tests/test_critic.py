import inspect
import json
import pathlib
import re

import pytest

from argus.agent import critic

# 실측 레코드(exchange_20250728800035)의 원값. HCX-005 가 기간 산정을 틀린 문항이다.
SAMSUNG = {
    "rcept_no": "20250728800035",
    "corp_name": "삼성전자",
    "amount": 22_764_764_160_000,
    "ratio": 7.6,
    "ratio_base": "매출액",
    "period_start": "2025-07-24",
    "period_end": "2033-12-31",
    "raw_rows": [
        ["2. 계약내역", "계약금액(원)", "22,764,764,160,000"],
        ["최근매출액(원)", "300,870,903,000,000"],
        ["매출액대비(%)", "7.6"],
    ],
}


def _fake_chat(result_json):
    class R:
        text = result_json
        prompt_tokens = 100
        completion_tokens = 30
    return lambda *a, **k: R()


def test_quick_check_is_free_and_detects_missing_citation():
    assert critic.quick_check("매출액은 333조원입니다")["has_citation"] is False


def test_quick_check_accepts_valid_citation():
    text = "매출액은 333조원입니다 (근거: 사업보고서 2025.12, 접수번호 20260310002820, III-1)"
    assert critic.quick_check(text)["has_citation"] is True


def test_judge_parses_json(monkeypatch):
    monkeypatch.setattr(critic, "chat", _fake_chat(
        '{"supported": true, "has_citation": true, "hallucination": false, "reason": "일치"}'))
    v = critic.judge("근거", "답변")
    assert v.supported and v.passed


def test_judge_fails_on_hallucination(monkeypatch):
    monkeypatch.setattr(critic, "chat", _fake_chat(
        '{"supported": false, "has_citation": true, "hallucination": true, "reason": "근거 없음"}'))
    assert critic.judge("근거", "답변").passed is False


def test_judge_survives_malformed_json(monkeypatch):
    monkeypatch.setattr(critic, "chat", _fake_chat("판정 불가"))
    v = critic.judge("근거", "답변")
    assert v.passed is False and "파싱" in v.reason


# --- G. verify_calculation — LLM 0회 ---

def test_verify_calculation_never_calls_llm(monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("verify_calculation 이 LLM 을 호출했습니다")
    monkeypatch.setattr(critic, "chat", _boom)
    out = critic.verify_calculation("계약기간은 9년 5개월입니다", SAMSUNG)
    assert out["llm_calls"] == 0


def test_verify_calculation_catches_hcx_period_error():
    """실측: 2025-07-24 ~ 2033-12-31 을 Direct·CoT 모두 9년 5개월로 답했다."""
    out = critic.verify_calculation("해당 계약의 기간은 9년 5개월입니다.", SAMSUNG)
    assert out["ok"] is False
    assert out["failure_stage"] == "TOOL"
    bad = [m for m in out["mismatches"] if m["kind"] == "period"]
    assert bad and bad[0]["expected"] == "8년 5개월" and bad[0]["claimed"] == "9년 5개월"


def test_verify_calculation_passes_correct_period():
    out = critic.verify_calculation("계약기간은 2025-07-24 부터 2033-12-31 까지 8년 5개월입니다.",
                                    SAMSUNG)
    assert out["ok"] is True
    assert out["failure_stage"] is None
    assert "period" in out["checked"]


def test_verify_calculation_skips_period_when_answer_states_none():
    out = critic.verify_calculation("계약상대는 글로벌 대형기업입니다.", SAMSUNG)
    assert out["ok"] is True
    assert "period" in out["skipped"]
    assert "period" not in out["checked"]


def test_verify_calculation_recomputes_share_from_raw_rows():
    """매출액 대비 비율을 계약금액/최근매출액으로 다시 계산한다."""
    out = critic.verify_calculation("매출액 대비 8.6% 규모입니다.", SAMSUNG)
    assert out["ok"] is False
    bad = [m for m in out["mismatches"] if m["kind"] == "share"]
    assert bad and round(bad[0]["expected"], 1) == 7.6


def test_verify_calculation_accepts_rounded_share():
    out = critic.verify_calculation("매출액 대비 7.6% 규모입니다.", SAMSUNG)
    assert out["ok"] is True
    assert "share" in out["checked"]


def test_verify_calculation_accepts_won_and_eok_forms():
    answer = "계약금액은 22,764,764,160,000원 (227,648억원) 입니다."
    out = critic.verify_calculation(answer, SAMSUNG)
    assert out["ok"] is True
    assert "amount" in out["checked"]


def test_verify_calculation_catches_amount_scale_error():
    out = critic.verify_calculation("계약금액은 2,276,476,416,000원입니다.", SAMSUNG)
    assert out["ok"] is False
    assert any(m["kind"] == "amount" for m in out["mismatches"])


def test_verify_calculation_catches_pct_change_error():
    ev = {"before": 6.5e12, "after": 8.1e12}
    assert critic.verify_calculation("매출이 16.0% 증가했습니다.", ev)["ok"] is False
    assert critic.verify_calculation("매출이 24.6% 증가했습니다.", ev)["ok"] is True


def test_verify_calculation_checks_cagr():
    ev = {"begin": 100, "end": 121, "years": 2}
    assert critic.verify_calculation("연평균 10.5% 성장했습니다.", ev)["ok"] is False
    out = critic.verify_calculation("연평균 10.0% 성장했습니다.", ev)
    assert out["ok"] is True and "cagr" in out["checked"]


def test_verify_calculation_checks_total_only_when_answer_sums():
    ev = {"values": [10.2, 0.6, 0.5]}
    assert critic.verify_calculation("합계는 11.3조원입니다.", ev)["ok"] is True
    assert critic.verify_calculation("합계는 12.3조원입니다.", ev)["ok"] is False
    # 합계를 말하지 않은 답변에는 합계 검사를 걸지 않는다
    out = critic.verify_calculation("가장 큰 항목은 10.2조원입니다.", ev)
    assert out["ok"] is True and "total" in out["skipped"]


def test_verify_calculation_checks_mean_when_answer_averages():
    ev = {"values": [10.2, 0.6, 0.5]}
    assert critic.verify_calculation("평균은 3.8조원입니다.", ev)["ok"] is True
    assert critic.verify_calculation("평균은 5.0조원입니다.", ev)["ok"] is False


def test_verify_calculation_accepts_tool_output_shape():
    """query_events 의 반환 형태를 그대로 넘겨도 동작한다."""
    out = critic.verify_calculation("계약기간은 9년 5개월입니다.",
                                    {"count": 1, "events": [SAMSUNG]})
    assert out["ok"] is False and out["failure_stage"] == "TOOL"


def test_verify_calculation_is_noop_without_evidence():
    out = critic.verify_calculation("계약기간은 9년 5개월입니다.", {})
    assert out["ok"] is True and out["checked"] == [] and out["failure_stage"] is None


@pytest.mark.parametrize("answer", ["", None])
def test_verify_calculation_survives_empty_answer(answer):
    out = critic.verify_calculation(answer, SAMSUNG)
    assert out["ok"] is True and out["mismatches"] == []


def test_verify_calculation_reason_is_readable():
    """reason 은 think_trace 에 그대로 실린다 — 지수표기·유효숫자 17자리는 곤란하다."""
    out = critic.verify_calculation("매출액 대비 9.9% 규모입니다.", SAMSUNG)
    assert out["reason"] == "share: 답변 9.9% vs 재계산 7.6"
    detail = out["mismatches"][0]["detail"]
    assert "e+" not in detail and "22,764,764,160,000" in detail


def test_verify_calculation_reports_every_mismatch():
    answer = "계약기간 9년 5개월, 매출액 대비 8.6%, 계약금액 2,276,476,416,000원입니다."
    kinds = {m["kind"] for m in critic.verify_calculation(answer, SAMSUNG)["mismatches"]}
    assert kinds == {"period", "share", "amount"}


# --- 최유리 선택을 뺀다 — 자릿수(10^k) 후보는 무해한 이웃이 아니다 --------------
# 종전 `min(claims, key=|c-expected|)` 은 답변 수치 중 기대값에 **가장 가까운 하나**만
# 보고 통과를 판정했다. 실측 Q-065 가 그 구멍으로 나갔다 (`docs/total_plan.md §0b ③`).

#: v2 A8 실측. Dukovany 원값과 답변이 옮긴 값. 배율 10.0002 — 콤마가 한 자리 밀렸다.
DUKOVANY = {"rcept_no": "20251216801147", "corp_name": "두산에너빌리티",
            "amount": 4_928_968_079_043, "amount_type": "계약금액", "is_latest": True}
#: 같은 답변에 정확히 실린 다른 계약. 이것이 종전 판정을 통과시킨 '최유리 값' 이다.
SHINHANUL = {"rcept_no": "20230329801108", "corp_name": "두산에너빌리티",
             "amount": 2_338_117_950_000, "amount_type": "계약금액", "is_latest": True}
Q065_ANSWER = ("Dukovany 5&6 NSSS 계약 금액은 492,909억 원이고, 신한울 원자력 발전소 "
               "3,4호기 원자로 설비 공급 계약 금액은 23,381억 원이므로, 차이는 "
               "**469,528억 원**입니다.")


def test_unit_error_is_reported_even_when_another_figure_matches():
    """Q-065. `23,381억` 이 정확해도 `492,909억` 은 10배 오류다 — 둘은 다른 사건이다."""
    out = critic.verify_calculation(Q065_ANSWER, DUKOVANY)
    assert out["ok"] is False
    assert out["hard_kinds"] == ["amount"]
    bad = [m for m in out["mismatches"] if m.get("scale")]
    assert len(bad) == 1
    assert bad[0]["claimed"] == "492,909억" and bad[0]["scale"] == 1
    assert "자릿수" in out["reason"]


def test_a_derived_difference_is_not_a_unit_error():
    """`469,528억`(차이)은 원값의 9.53배다. 10배 창 밖이므로 자릿수 오류가 아니다."""
    bad = [m for m in critic.verify_calculation(Q065_ANSWER, DUKOVANY)["mismatches"]
           if m.get("scale")]
    assert [m["claimed"] for m in bad] == ["492,909억"]


def test_unit_error_does_not_fire_on_a_record_the_answer_quoted_correctly():
    """신한울 레코드로 보면 통과다 — 그래서 레코드 단위 관용이 이 오류를 지웠다."""
    assert critic.verify_calculation(Q065_ANSWER, SHINHANUL)["ok"] is True


def test_scale_guard_ignores_tiny_amounts():
    """실측 파싱 잔재 `amount=8`·`amount=1`. 아무 큰 값과도 10^k 관계가 성립한다."""
    out = critic.verify_calculation("계약금액은 800,000,000,000원입니다.",
                                    {"amount": 8, "amount_type": "계약금액"})
    assert out["hard_kinds"] == []


def test_scale_guard_leaves_a_nearby_contract_amount_alone():
    """같은 근거의 다른 계약이 8~9배쯤 되는 것은 흔하다. 자릿수 오류로 세면 안 된다."""
    other = {"amount": 532_040_000_000, "amount_type": "계약금액", "is_latest": True}
    out = critic.verify_calculation("계약금액은 4,928,968,079,043원입니다.", other)
    assert out["hard_kinds"] == [] and out["ok"] is False


@pytest.mark.parametrize("claimed,expected,exponent", [
    (4.92909e13, 4_928_968_079_043, 1),        # Q-065 실측 (10.0002배)
    (3.3636059e15, 3.336059e14, 1),            # Q-047 실측 (10.08배)
    (2_276_476_416_000, 22_764_764_160_000, -1),
    (2.3381e12, 2_338_117_950_000, None),      # 같은 값 — k=0 은 자릿수 오류가 아니다
    (4.69528e13, 4_928_968_079_043, None),     # 9.53배 — 파생값
])
def test_scale_exponent_window(claimed, expected, exponent):
    assert critic._scale_exponent(claimed, expected) == exponent


# --- 레코드 단위 집계 — 체인 관용은 지키고 자릿수 오류만 뺀다 ------------------

def test_aggregate_keeps_the_correction_chain_leniency():
    """정정 체인은 원공시와 정정본의 금액이 다른 것이 정상이다 (227,500 vs 114,800).

    `tests/test_error_analysis.py::test_calc_passes_when_any_golden_record_explains_the_answer`
    가 지키는 성질이다. 전건 일치를 요구하면 정당한 통과가 깨진다.
    """
    answer = "정정 후 114,800,000,000원입니다"
    reports = [critic.verify_calculation(answer, {"amount": 227_500_000_000}),
               critic.verify_calculation(answer, {"amount": 114_800_000_000})]
    out = critic.aggregate_calculation(reports)
    assert out["explained"] is True and out["failed"] is False
    assert out["hard_kinds"] == []


def test_aggregate_keeps_the_leniency_when_the_answer_states_both_chain_values():
    answer = "원공시 227,500,000,000원에서 정정 후 114,800,000,000원으로 바뀌었습니다"
    reports = [critic.verify_calculation(answer, {"amount": 227_500_000_000}),
               critic.verify_calculation(answer, {"amount": 114_800_000_000})]
    assert critic.aggregate_calculation(reports)["failed"] is False


def test_aggregate_does_not_excuse_a_unit_error():
    """Q-065. 신한울이 설명해도 Dukovany 10배 오류는 살아남는다."""
    reports = [critic.verify_calculation(Q065_ANSWER, DUKOVANY),
               critic.verify_calculation(Q065_ANSWER, SHINHANUL)]
    out = critic.aggregate_calculation(reports)
    assert out["explained"] is True         # 관용은 그대로 성립한다
    assert out["hard_kinds"] == ["amount"]  # 그런데 자릿수 오류가 그것을 넘는다
    assert out["failed"] is True and out["failure_stage"] == "TOOL"
    assert "자릿수" in out["reasons"][0]     # 단위 오류를 먼저 보여 준다


def test_aggregate_is_a_noop_without_records():
    out = critic.aggregate_calculation([])
    assert out["failed"] is False and out["llm_calls"] == 0 and out["checked"] == []


def test_aggregate_matches_the_old_rule_when_nothing_is_a_unit_error():
    """종전 규칙(`bool(checked) and not explained`)과 갈리는 것은 자릿수 오류뿐이다."""
    reports = [critic.verify_calculation("계약금액은 999,999,999원입니다.",
                                         {"amount": 227_500_000_000})]
    out = critic.aggregate_calculation(reports)
    assert out["checked"] == ["amount"] and out["explained"] is False
    assert out["failed"] is True and out["hard_kinds"] == []


# --- per_unit — 답변이 스스로 한 나눗셈을 다시 한다 (Q-097 실측) ----------------

GLOVIS = {"rcept_no": "20231026800363", "corp_name": "현대글로비스",
          "amount": 2_492_215_574_520, "amount_type": "투자금액", "is_latest": True}


def test_per_unit_average_is_recomputed():
    """Q-097. 총액은 정확히 옮겼는데 1척당 값만 틀렸다 — 종전에 이 축이 없었다."""
    answer = ("총 투자 금액: 2,492,215,574,520원, 선박 척수: 12척. "
              "따라서 1척 당 평균 투자금액은 약 207,838,797,710원이 됩니다.")
    out = critic.verify_calculation(answer, GLOVIS)
    assert out["ok"] is False
    assert "per_unit" in out["checked"] and "amount" in out["checked"]
    bad = [m for m in out["mismatches"] if m["kind"] == "per_unit"]
    assert bad and round(bad[0]["expected"]) == 207_684_631_210


def test_per_unit_accepts_the_right_average():
    answer = ("총 투자 금액 2,492,215,574,520원을 12척으로 나누면 "
              "1척당 207,684,631,210원입니다.")
    out = critic.verify_calculation(answer, GLOVIS)
    assert out["ok"] is True and "per_unit" in out["checked"]


def test_per_unit_accepts_a_rounded_average():
    """`약 2,077억원` 처럼 반올림해 쓴 답을 틀렸다고 하면 안 된다."""
    answer = "총 투자금액 2,492,215,574,520원, 12척이므로 1척당 약 2,077억원입니다."
    assert critic.verify_calculation(answer, GLOVIS)["ok"] is True


def test_per_unit_is_skipped_when_the_divisor_is_ambiguous():
    """나눌 수가 여러 개면 무엇으로 나눌지 못 박을 수 없다 — 통과시킨다."""
    answer = ("투자금액 2,492,215,574,520원. 1차 4척, 2차 8척으로 "
              "1척당 금액은 별도 산정합니다.")
    out = critic.verify_calculation(answer, GLOVIS)
    assert "per_unit" in out["skipped"] and "per_unit" not in out["checked"]


def test_per_unit_is_silent_when_the_answer_states_no_ratio():
    out = critic.verify_calculation("투자금액은 2,492,215,574,520원입니다.", GLOVIS)
    assert out["ok"] is True
    assert "per_unit" not in out["checked"] and "per_unit" not in out["skipped"]


# --- amount 는 답변이 그 금액을 말했을 때만 대조한다 (A6 Q-014 회귀) -----------
# 유상증자 레코드 20250314000027 실측. `조달금액` 밑에 `시설자금`·`타법인 증권취득자금`
# 이 하위 항목으로 같이 실린다. 질문이 물은 것은 하위 항목이었다.

FUNDING = {
    "rcept_no": "20250314000027",
    "corp_name": "삼성SDI",
    "amount": 2_000_113_200_000,
    "amount_type": "조달금액",
    "fields": {
        "시설자금": "454,100,000,000",
        "타법인 증권취득자금": "1,546,013,200,000",
    },
}


def test_verify_calculation_skips_amount_when_answer_names_a_sub_item():
    """A6 실측 거짓 경보. 답은 조달금액이 아니라 **시설자금**을 정확히 옮겼다.

    레이블을 안 보면 `_money_claims` 가 답변의 금액 표기를 전부 긁어 amount 와
    대조하므로, 하위 항목을 말한 정답이 `TOOL` 오류로 뒤집힌다.
    """
    out = critic.verify_calculation(
        "삼성SDI가 2025년 3월에 결의한 유상증자의 시설자금은 454,100,000,000원입니다.",
        FUNDING)
    assert out["ok"] is True, out["reason"]
    assert "amount" in out["skipped"] and "amount" not in out["checked"]


def test_verify_calculation_checks_amount_when_the_label_is_spaced_out():
    """레이블 게이트가 띄어쓰기·굵게 표기에 걸리면 검사가 통째로 죽는다.

    실측 답변들은 `**총 조달 금액**` · `**투자 금액**` 처럼 레이블을 띄어 쓴다.
    """
    out = critic.verify_calculation("총 조달 금액은 1,000,000,000원입니다.", FUNDING)
    assert out["ok"] is False and "amount" in out["checked"]


def test_verify_calculation_checks_amount_without_a_label():
    """`amount_type` 이 없으면 게이트를 세울 재료가 없다 — 종전대로 검사한다."""
    out = critic.verify_calculation("계약금액은 2,276,476,416,000원입니다.", SAMSUNG)
    assert out["ok"] is False and "amount" in out["checked"]


# --- G. verify_completeness — LLM 0회 -----------------------------------------
# 지지(support)와 완전성(completeness)은 다른 축이다. 아래 근거는 전부 Phase H 가
# 저장한 dev 42문항 실제 응답(artifacts/eval/responses)에서 그대로 따왔다.

#: Q-005 실측. 근거에 원본만 왔고 정정본이 없다.
Q005_EVIDENCE = """## [공급계약 체결] 삼성중공업 – 단일판매ㆍ공급계약체결 — 원유운반선 2척
- 공시일: 2023-06-02 (접수번호 20230602800079)
- 계약금액: 227,500,000,000원 (2,275억원)
- 기간: 2023-06-02 ~ 2026-02-28 (2년 8개월)"""

#: Q-026 실측. 질문 형태는 Q-005 와 같은 '정정' 이지만 **존재 여부**만 묻는다.
Q026_EVIDENCE = """## [공급계약 체결] HD현대일렉트릭 – 단일판매ㆍ공급계약체결 — 2500kVA 배전변압기 등
- 공시일: 2023-01-31 (접수번호 20230131800162)
- 계약금액: 97,000,000,000원 (970억원)"""

#: 정정 체인이 근거에 통째로 들어온 상태. 다른 트랙이 골든 적중을 38→49 로 올리면서
#: 정정 전·후 금액이 **둘 다** 보이게 됐다. 대체본을 쓰면 여기서 걸려야 한다.
CHAIN_EVIDENCE = """## [공급계약 체결] 한화오션 – 단일판매ㆍ공급계약체결 — 잠수함 1척
- 정정체인: 1/2 (대체됨)
- 공시일: 2023-12-22 (접수번호 20231222800809)
- 계약금액: 1,102,000,000,000원 (11,020억원)

## [기재정정] [공급계약 체결] 한화오션 – 단일판매ㆍ공급계약체결 — 잠수함 1척
- 정정체인: 2/2 (최신본)
- 공시일: 2024-05-10 (접수번호 20240510800100)
- 계약금액: 1,350,000,000,000원 (13,500억원)"""

#: Q-049 실측. 같은 기업·같은 절의 반기보고서와 사업보고서가 함께 왔고
#: 모델이 **반기** 수치(11,830,403)를 연간 값으로 답했다.
Q049_EVIDENCE = """## [반기보고서 2025] LG에너지솔루션 – II. 사업의 내용 > 4. 매출 및 수주상황
- 출처: 접수번호 20250814001590 (공시일 20250814)
| 합 계 | 11,830,403 | 25,619,585 | 33,745,470 |

## [사업보고서 2025] LG에너지솔루션 – II. 사업의 내용 > 4. 매출 및 수주상황
- 출처: 접수번호 20260312000217 (공시일 20260312)
| 합 계 | 23,671,759 | 25,619,585 | 33,745,470 |"""


def test_verify_completeness_never_calls_llm(monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("verify_completeness 가 LLM 을 호출했습니다")
    monkeypatch.setattr(critic, "chat", _boom)
    out = critic.verify_completeness(
        "삼성중공업 원유운반선 2척 공급계약의 계약금액은 이후 정정공시에서 얼마로 바뀌었는가?",
        "정정공시에 대한 언급이 없으므로 227,500,000,000원으로 유지됩니다.", Q005_EVIDENCE)
    assert out["llm_calls"] == 0


def test_verify_completeness_catches_missing_correction_chain():
    """Q-005 회귀. critic 은 supported=True 로 통과시켰다 — 근거가 불완전한 줄 몰랐다."""
    out = critic.verify_completeness(
        "삼성중공업이 2023년 6월에 공시한 원유운반선 2척 공급계약의 계약금액은 "
        "이후 정정공시에서 얼마로 바뀌었는가?",
        "정정공시에 대한 언급이 없으므로, 해당 계약 금액은 변동 없이 "
        "227,500,000,000원으로 유지됩니다.", Q005_EVIDENCE)
    assert out["ok"] is False
    assert out["failed_kinds"] == ["evidence_gap"]
    assert out["failure_stage"] == "RETRIEVAL"
    assert "evidence_gap" in out["checked"]


def test_verify_completeness_catches_admitted_gap_on_delta_question():
    """Q-018 회귀. '얼마나 증가했는가' 는 존재 질의가 아니라 값 요구다."""
    out = critic.verify_completeness(
        "HD현대중공업이 2023년 7월에 공시한 FPU 1기 공급계약은 정정공시로 "
        "계약금액이 얼마나 증가했는가?",
        "제공된 공시 근거에는 FPU 1기 공급계약의 금액 변경에 대한 정보가 "
        "포함되어 있지 않으므로, 해당 질문에 대해 답변할 수 없습니다.", Q005_EVIDENCE)
    assert out["ok"] is False and out["failure_stage"] == "RETRIEVAL"


def test_verify_completeness_does_not_flag_existence_question():
    """Q-026 회귀 — **거짓 경보 방지**. 표면은 Q-005 와 같지만 정답이다.

    '정정공시가 있었는가' 는 정정본의 부재 자체가 답이다. 여기서 차단하면
    지금 맞히는 문항이 깨진다.
    """
    out = critic.verify_completeness(
        "HD현대일렉트릭이 2023년 1월 31일 공시한 공급계약은 이후 정정공시가 있었는가?",
        "공시된 내용에 따르면 정정공시는 없습니다. (근거: 20230131800162)", Q026_EVIDENCE)
    assert out["ok"] is True
    assert out["failure_stage"] is None
    assert "evidence_gap" in out["skipped"]


def test_verify_completeness_does_not_flag_refusal_question():
    """정보한계 6문항 회귀 — 거절이 정답인 질문에 자백 신호만으로 걸면 안 된다."""
    out = critic.verify_completeness(
        "포스코퓨처엠의 2025년 연결기준 매출액은 얼마인가?",
        "공시에서 확인되지 않습니다. 제공된 범위 밖입니다.", "")
    assert out["ok"] is True and out["failure_stage"] is None


def test_verify_completeness_does_not_flag_fully_answered_correction():
    """Q-048 회귀 — 정정 전·후를 둘 다 말한 정답에는 걸리지 않는다."""
    out = critic.verify_completeness(
        "고려아연 제50기 1분기 지배주주순이익은 정정으로 얼마나 줄었는가?",
        "정정 전 216,407,387,195원, 정정 후 139,850,216,050원이므로 "
        "차이는 76,557,171,145원입니다.", Q005_EVIDENCE)
    assert out["ok"] is True


# --- A6 스냅샷 거짓 경보 2건의 정체 -------------------------------------------
# 07-verifier 의 7/7 은 **Phase H 응답 기준**이었다. 그 뒤 검색이 골든 근거 38/60 →
# 59/60 으로 좋아지자 `stale_value` 의 전제(대체본+최신본 공존)가 훨씬 자주 서면서
# 두 결함이 드러났다. 규칙이 틀린 게 아니라 규칙이 가정한 세계가 바뀐 것이다.

#: Q-048 A6 실측(발췌). 정기공시는 원공시와 정정본이 **같은 `path`** 를 갖고 값만
#: 다르고, 정정본의 본문 블록과 `정 정 신 고` 블록은 **접수번호가 같다** — 같은 문서다.
Q048_EVIDENCE = """## [분기보고서 2023] 고려아연 – III. 재무에 관한 사항 > 1. 요약재무정보
- 출처: 접수번호 20230515001843 (공시일 20230515)
| 지배주주순이익 | 216,407,387,195 |
| 자본총계 | 9,062,739,185,674 |

## [분기보고서 2023] 고려아연 – III. 재무에 관한 사항 > 1. 요약재무정보
- 출처: 접수번호 20230518000337 (공시일 20230518)
| 지배주주순이익 | 139,850,216,050 |
| 자본총계 | 9,062,739,185,674 |
| 투자자산(별도) | 3,381,074,815,289 |

## [분기보고서 2023] 고려아연 – 정 정 신 고 (보고)
- 출처: 접수번호 20230518000337 (공시일 20230518)
| 지배주주순이익(제50기 1분기) | 기재 오류 | 216,407,387,195 | 139,850,216,050 |"""

Q048_QUESTION = ("고려아연이 2023년 5월 15일 제출한 제50기 1분기 분기보고서는 사흘 뒤 "
                 "정정됐다. 연결 요약재무정보의 제50기 1분기 지배주주순이익은 정정으로 "
                 "얼마나 줄었는가?")


def test_verify_completeness_does_not_count_a_disclosure_date_as_a_value():
    """A6 거짓 경보의 정체 ①. 발화한 '대체본 값' 은 **20,230,515** 였다 —
    답이 인용에 적은 공시일 `20230515` 다. 8자리 `YYYYMMDD` 는 14자리 접수번호와
    같은 이유로 값이 아니라 **주소**이고, 근거 머리글의 `(공시일 …)` 와 짝이 맞는다.
    """
    evidence = """## [공급계약 체결] 한화오션 – 단일판매ㆍ공급계약체결 — 잠수함 1척
- 정정체인: 1/2 (대체됨)
- 출처: 접수번호 20231222800809 (공시일 20231222)
- 계약금액: 1,102,000,000,000원

## [기재정정] [공급계약 체결] 한화오션 – 단일판매ㆍ공급계약체결 — 잠수함 1척
- 정정체인: 2/2 (최신본)
- 출처: 접수번호 20240510800100 (공시일 20240510)
- 계약금액: 1,350,000,000,000원"""
    out = critic.verify_completeness(
        "한화오션 잠수함 1척 공급계약은 정정 과정에서 계약금액이 어떻게 바뀌었는가?",
        "정정으로 계약금액이 248,000,000,000원 늘었습니다. "
        "(근거: 접수번호 20231222800809, 20231222)", evidence)
    assert out["ok"] is True, out["reason"]
    assert "stale_value" in out["checked"]


def test_verify_completeness_does_not_treat_one_filing_as_its_own_predecessor():
    """A6 거짓 경보의 정체 ②(Q-048). 정정본 **본문** 블록과 `정 정 신 고` 블록은
    접수번호가 같다. 기업 단위 추정이 이 둘을 갈라 정정본 본문을 대체본으로 몰았다.
    """
    out = critic.verify_completeness(
        Q048_QUESTION,
        "정정 후 지배주주순이익은 139,850,216,050원이고 자본총계는 "
        "9,062,739,185,674원입니다.", Q048_EVIDENCE)
    assert out["ok"] is True, out["reason"]


def test_verify_completeness_does_not_flag_q048_on_the_a6_snapshot():
    """Q-048 A6 회귀 전체. 답은 정정 전·후를 둘 다 말한 **정답**이다.

    07-verifier 는 이 문항을 '전제는 서되 발화하지 않는다' 로 적어 뒀는데, Phase H
    응답 기준이었다. A6 응답에서는 위 두 결함이 겹쳐 실제로 발화했다.
    """
    out = critic.verify_completeness(
        Q048_QUESTION,
        "지배주주순이익은 정정으로 76,557,171,145원 줄었습니다. "
        "정정 전 216,407,387,195원, 정정 후 139,850,216,050원입니다. "
        "(근거: 접수번호 20230515001843, 20230515; 20230518000337, 20230518)",
        Q048_EVIDENCE)
    assert out["ok"] is True, out["reason"]
    assert "stale_value" in out["checked"]


def test_verify_completeness_catches_superseded_amount():
    """체인이 통째로 왔을 때 정정 **전** 금액을 답하면 걸린다."""
    out = critic.verify_completeness(
        "한화오션 잠수함 1척 공급계약은 정정 과정에서 계약금액이 어떻게 바뀌었는가?",
        "계약금액은 1,102,000,000,000원입니다.", CHAIN_EVIDENCE)
    assert out["ok"] is False
    assert out["failed_kinds"] == ["stale_value"]
    assert out["failure_stage"] == "GENERATION"


def test_verify_completeness_accepts_latest_amount():
    out = critic.verify_completeness(
        "한화오션 잠수함 1척 공급계약은 정정 과정에서 계약금액이 어떻게 바뀌었는가?",
        "정정 후 계약금액은 1,350,000,000,000원입니다.", CHAIN_EVIDENCE)
    assert out["ok"] is True and "stale_value" in out["checked"]


def test_verify_completeness_reads_is_latest_from_records():
    """근거를 레코드 리스트로 넘겨도 대체본을 알아본다."""
    records = [
        {"corp": "한화오션", "is_latest": False,
         "markdown": "## [공급계약] 한화오션 – 잠수함\n- 계약금액: 1,102,000,000,000원"},
        {"corp": "한화오션", "is_latest": True,
         "markdown": "## [기재정정] [공급계약] 한화오션 – 잠수함\n- 계약금액: 1,350,000,000,000원"},
    ]
    out = critic.verify_completeness(
        "한화오션 잠수함 계약금액은 정정으로 어떻게 바뀌었는가?",
        "계약금액은 1,102,000,000,000원입니다.", records)
    assert out["ok"] is False and out["failed_kinds"] == ["stale_value"]


def test_verify_completeness_catches_interim_report_used_as_annual():
    """Q-049 회귀. 근거에 연간값이 있는데 답이 반기값(11,830,403)을 썼다."""
    out = critic.verify_completeness(
        "LG에너지솔루션의 제6기(2025년) 연결 매출액은 얼마인가?",
        "제6기(2025년) 연결 매출액은 11,830,403백만원입니다.", Q049_EVIDENCE)
    assert out["ok"] is False
    assert out["failed_kinds"] == ["interim_source"]
    assert out["failure_stage"] == "GENERATION"


def test_verify_completeness_accepts_annual_figure():
    out = critic.verify_completeness(
        "LG에너지솔루션의 제6기(2025년) 연결 매출액은 얼마인가?",
        "제6기(2025년) 연결 매출액은 23,671,759백만원입니다.", Q049_EVIDENCE)
    assert out["ok"] is True and "interim_source" in out["checked"]


def test_verify_completeness_allows_interim_when_question_asks_for_it():
    out = critic.verify_completeness(
        "LG에너지솔루션의 2025년 반기 연결 매출액은 얼마인가?",
        "반기 연결 매출액은 11,830,403백만원입니다.", Q049_EVIDENCE)
    assert out["ok"] is True and "interim_source" in out["skipped"]


def test_verify_completeness_missing_corp_is_opt_in():
    """비교 축 검사는 기업 목록을 넘겼을 때만 돈다 — 근거 없는 차단을 하지 않는다."""
    question = "LG에너지솔루션과 두산에너빌리티 중 어느 쪽 매출이 큰가?"
    answer = "LG에너지솔루션이 큽니다."
    assert critic.verify_completeness(question, answer, Q049_EVIDENCE)["ok"] is True
    out = critic.verify_completeness(question, answer, Q049_EVIDENCE,
                                     companies=["LG에너지솔루션", "두산에너빌리티"])
    assert out["ok"] is False
    assert out["failed_kinds"] == ["missing_corp"]
    assert out["failure_stage"] == "RETRIEVAL"


def test_verify_completeness_passes_when_both_corps_present():
    out = critic.verify_completeness(
        "LG에너지솔루션과 두산에너빌리티 중 어느 쪽 매출이 큰가?", "LG가 큽니다.",
        Q049_EVIDENCE + "\n\n## [사업보고서 2025] 두산에너빌리티 – II. 사업의 내용\n- 합계 7,788,916",
        companies=["LG에너지솔루션", "두산에너빌리티"])
    assert "missing_corp" not in out["failed_kinds"]


def test_verify_completeness_is_noop_without_evidence():
    out = critic.verify_completeness("계약금액은?", "97,000,000,000원입니다.", "")
    assert out["ok"] is True and out["failed_kinds"] == [] and out["failure_stage"] is None


@pytest.mark.parametrize("answer", ["", None])
def test_verify_completeness_survives_empty_answer(answer):
    out = critic.verify_completeness("정정공시로 얼마나 증가했는가?", answer, CHAIN_EVIDENCE)
    assert out["ok"] is True and out["gaps"] == []


def test_verify_completeness_reason_is_readable():
    out = critic.verify_completeness(
        "한화오션 잠수함 계약금액은 정정 과정에서 어떻게 바뀌었는가?",
        "계약금액은 1,102,000,000,000원입니다.", CHAIN_EVIDENCE)
    assert out["reason"].startswith("stale_value:")
    assert "1,102,000,000,000" in out["reason"]


def test_verify_completeness_matches_verify_calculation_contract():
    """Phase I A6 은 두 판정을 같은 모양으로 집계한다."""
    out = critic.verify_completeness("계약금액은?", "970억원입니다.", Q026_EVIDENCE)
    for key in ("ok", "llm_calls", "checked", "skipped", "failed_kinds",
                "failure_stage", "reason"):
        assert key in out, key
    assert isinstance(out["checked"], list) and isinstance(out["skipped"], list)


# --- 하위 호환 — pipeline.py 가 부르는 두 함수의 시그니처는 못 바꾼다 ---

def test_public_signatures_are_unchanged():
    """pipeline.py 를 다른 트랙이 고치는 중이다. 시그니처가 바뀌면 충돌한다."""
    def shape(fn):
        sig = inspect.signature(fn)
        return [(name, p.kind, p.default is inspect.Parameter.empty)
                for name, p in sig.parameters.items()]

    assert shape(critic.quick_check) == [
        ("answer", inspect.Parameter.POSITIONAL_OR_KEYWORD, True)]
    assert shape(critic.judge) == [
        ("evidence", inspect.Parameter.POSITIONAL_OR_KEYWORD, True),
        ("answer", inspect.Parameter.POSITIONAL_OR_KEYWORD, True),
        ("budget", inspect.Parameter.POSITIONAL_OR_KEYWORD, False)]
    assert shape(critic.verify_calculation) == [
        ("answer", inspect.Parameter.POSITIONAL_OR_KEYWORD, True),
        ("evidence", inspect.Parameter.POSITIONAL_OR_KEYWORD, True)]
    # 새 기능은 전부 새 함수·키워드 전용 인자로만 더했다
    assert critic.verify_completeness("q", "a", "")["llm_calls"] == 0


# --- 저장된 응답 전수 회귀 — **두 스냅샷 모두**에서 거짓 경보 0 을 고정한다 ------
#
# 두 스냅샷의 응답은 서로 다르다. 07-verifier 의 7/7 은 Phase H 응답 기준이었고,
# 그 뒤 다른 트랙이 골든 근거 적중을 38/60 → 59/60 으로 올리면서 `stale_value` 의
# 전제가 1회 → 7회로 늘었다. **한쪽만 고정하면 다음 검색 개선에서 또 낡는다.**
# CLOVA 는 한 번도 부르지 않는다 — 저장된 실제 HCX 응답만 읽는다.

_ROOT = pathlib.Path(__file__).resolve().parents[1]

#: Phase H 가 in-pipeline critic 으로 `supported=True` 통과시킨 **오답** 5건.
CRITIC_PASSED_WRONG = {"Q-005", "Q-006", "Q-018", "Q-035", "Q-049"}

#: 스냅샷별 기대 적중. 정답 문항 거짓 경보는 **어느 쪽이든 0** 이다.
SNAPSHOT_CAUGHT = {
    "phase_h": {"Q-005", "Q-006", "Q-018", "Q-023", "Q-035", "Q-049", "Q-052"},
    "ablation_a6": {"Q-023"},
    # A7 — 답변이 바뀌어도 정밀도는 1.00 이다. 발화 1건이 그대로 오답 문항이고
    # 정답 문항 거짓 경보는 0건이다 (정답이 36 -> 38 로 늘어난 뒤에도).
    "ablation_a7": {"Q-023"},
}

_EVIDENCE_RCEPT = re.compile(r"접수번호\s*(\d{14})")


def _snapshot(name):
    """(응답 dict, 라벨 행 dict). 둘 중 하나라도 없으면 None."""
    spec = critic.COMPLETENESS_SNAPSHOTS[name]
    resp_dir, labels = _ROOT / spec["responses"], _ROOT / spec["labels"]
    if not (resp_dir.is_dir() and labels.exists()):
        return None
    rows = {r["question_id"]: r
            for r in json.loads(labels.read_text(encoding="utf-8"))["rows"]}
    responses = {}
    for path in sorted(resp_dir.glob("Q-*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        responses[payload["question_id"]] = payload["response"]
    return responses, rows


def _calc_reports(resp):
    from argus.agent import tools

    records = [tools._record_of(rcept) for rcept in
               dict.fromkeys(_EVIDENCE_RCEPT.findall(resp["retrieved_context"] or ""))]
    return [critic.verify_calculation(resp["answer"], r) for r in records if r]


def _verify_like_pipeline(resp, companies):
    """`pipeline._verify` 의 판정을 저장 응답으로 재현한다. LLM 0회, 네트워크 0회.

    A6 이 실제로 발화한 3건 중 하나(Q-014)는 완전성이 아니라 **계산** 검증이었다.
    완전성만 재현하면 그 거짓 경보가 안 보인다 — 그래서 둘 다 돌린다.

    🔴 **집계는 아직 `pipeline._verify` 의 종전 세 줄 그대로다** (레코드 하나라도
    설명하면 통과). `critic.aggregate_calculation` 으로 바꾸는 것은 `pipeline.py` 를
    가진 트랙의 한 줄 수정이라 이 스냅샷은 종전 판정을 계속 지킨다. 새 집계 쪽은
    `test_new_aggregation_on_the_a8_snapshot` 이 따로 고정한다.
    """
    reports = _calc_reports(resp)
    checked = sorted({k for rep in reports for k in rep["checked"]})
    explained = any(rep["ok"] and rep["checked"] for rep in reports)
    complete = critic.verify_completeness(resp["question"], resp["answer"],
                                          resp["retrieved_context"], companies=companies)
    kinds = sorted({*(["calculation"] if checked and not explained else []),
                    *complete["failed_kinds"]})
    return kinds, complete


@pytest.mark.parametrize("snapshot", sorted(SNAPSHOT_CAUGHT))
def test_no_false_alarm_on_stored_dev_responses(snapshot):
    """정답 문항에 거짓 경보가 뜨면 지금 맞히는 문항이 깨진다. 오답 놓치는 것보다 나쁘다."""
    loaded = _snapshot(snapshot)
    if loaded is None:
        pytest.skip(f"{snapshot} 산출물이 없다")
    responses, rows = loaded

    false_alarms, caught = [], set()
    for qid, resp in sorted(responses.items()):
        row = rows.get(qid, {})
        kinds, _ = _verify_like_pipeline(resp, row.get("route_corps") or [])
        if not kinds:
            continue
        if row.get("correct"):
            false_alarms.append((qid, kinds))
        else:
            caught.add(qid)

    assert false_alarms == [], f"{snapshot} 정답 문항 거짓 경보: {false_alarms}"
    assert caught == SNAPSHOT_CAUGHT[snapshot], f"{snapshot} 적중이 달라졌다: {sorted(caught)}"
    if snapshot == "phase_h":
        # `supported=True` 로 새어 나갔던 5건은 여전히 전부 잡는다.
        assert CRITIC_PASSED_WRONG <= caught


# --- 새 집계를 v2 A8(제출 구성) 저장 응답 전수에 태운 결과 ----------------------
# W1 의 오프라인 실측을 코드가 붙들고 있게 한다. **유료 재측정이 아니라 재채점이다** —
# 저장된 HCX 응답만 읽으므로 LLM 0회 · 지출 0원이다.
#
# 발화 3건이 전부 오답 문항이다. `verify_calculation` 단독으로는 Q-066(종전에도 잡음) ·
# Q-097(`per_unit` 신설)이 잡히고, **Q-065 는 `aggregate_calculation` 이 있어야 잡힌다** —
# 신한울 금액이 정확해서 레코드 단위 관용이 Dukovany 10배 오류를 지우고 있었다.

A8_SNAPSHOT = "artifacts/ablation/v2/A8"

#: 🔴 **W1 이 든 뒤 A8 스냅샷이 재생성되면서 이 집합이 바뀌었다.**
#:
#:     구분        W1 이전(직전 A8)          W1 이후(현행 A8)
#:     caught      {Q-065, Q-066, Q-097}     {Q-023, Q-066, Q-097}
#:     hard        {Q-065}                   {Q-023}
#:
#: Q-065 는 자릿수 가드가 생성 단계에서 먼저 막아 **정답으로 뒤집혔고**(X→O,
#: 469,528억원 → 2,590,850,129,043원), 정답이 된 뒤로는 발화하지 않는다 — 발화하면
#: 그것이 거짓 경보다. 빈자리에 Q-023 이 섰다(답변 1,020억원 vs 재계산
#: 1,013,700,000,000, `10^-1 배 자릿수 오류`). **자릿수 적중이 사라진 게 아니라 옮겨간
#: 것**이고, 옛 실패 양식 자체는 `test_the_digit_guard_still_fires_on_the_pre_w1_q065`
#: 가 W1 이전 저장 응답으로 계속 붙들고 있다.
A8_CALC_CAUGHT = {"Q-023", "Q-066", "Q-097"}

#: 그중 자릿수 오류로 잡히는 것. W1 이전에는 {"Q-065"} 였다.
A8_CALC_HARD = {"Q-023"}

#: W1 이전 Q-065 의 저장 응답 — 유료 측정 산출물이라 옛 실패 양식을 원문 그대로
#: 붙들고 있다(답변 "492,909억", 실제 4,928,968,079,043 → 10^+1 배).
PRE_W1_Q065 = "artifacts/eval/responses_v2/Q-065.json"


def _a8_snapshot():
    resp_dir, labels = _ROOT / A8_SNAPSHOT / "responses", _ROOT / A8_SNAPSHOT / "report.json"
    if not (resp_dir.is_dir() and labels.exists()):
        return None
    rows = {r["question_id"]: r
            for r in json.loads(labels.read_text(encoding="utf-8"))["rows"]}
    responses = {}
    for path in sorted(resp_dir.glob("Q-*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        responses[payload["question_id"]] = payload["response"]
    return responses, rows


def test_new_aggregation_on_the_a8_snapshot():
    """정답 문항 거짓 경보 0 · 자릿수 적중 Q-023. 두 조건을 함께 고정한다.

    W1 이전에는 자릿수 적중이 Q-065 였다 (`A8_CALC_CAUGHT` 주석의 대조표).
    """
    loaded = _a8_snapshot()
    if loaded is None:
        pytest.skip("v2/A8 산출물이 없다")
    responses, rows = loaded

    caught, hard, false_alarms = set(), set(), []
    for qid, resp in sorted(responses.items()):
        out = critic.aggregate_calculation(_calc_reports(resp))
        if not out["failed"]:
            continue
        (false_alarms.append((qid, out["hard_kinds"])) if rows.get(qid, {}).get("correct")
         else caught.add(qid))
        if out["hard_kinds"]:
            hard.add(qid)

    assert false_alarms == [], f"정답 문항 거짓 경보: {false_alarms}"
    assert caught == A8_CALC_CAUGHT, f"적중이 달라졌다: {sorted(caught)}"
    assert hard == A8_CALC_HARD, f"자릿수 적중이 달라졌다: {sorted(hard)}"


def test_the_digit_guard_still_fires_on_the_pre_w1_q065():
    """🔴 회귀 감시 — **옛 실패 양식이 다시 통과하기 시작하면 여기서 알아야 한다.**

    종전에는 현행 A8 스냅샷의 Q-065 로 이 대조를 했다. W1 이 든 뒤 그 문항이 정답으로
    뒤집혀 스냅샷에서 10배 오류가 사라졌고, 그 자리에서 재면 "가드가 없어도 통과한다"와
    "가드가 있어서 안 잡는다"를 **구별할 수 없다.** 그래서 대조 대상을 W1 **이전에 저장된
    유료 측정 응답**(`artifacts/eval/responses_v2/Q-065.json`)으로 옮긴다 — 그 파일은
    옛 실패 양식(답변 "492,909억", 실제 4,928,968,079,043)을 원문 그대로 얼려 둔 것이라
    가드가 약해지는 순간 이 테스트가 먼저 깨진다.
    """
    path = _ROOT / PRE_W1_Q065
    if not path.exists():
        pytest.skip(f"{PRE_W1_Q065} 이 없다 (W1 이전 저장 응답)")
    resp = json.loads(path.read_text(encoding="utf-8"))["response"]
    assert "469,528억" in resp["answer"], "이 파일은 W1 이전 응답이어야 한다"

    reports = _calc_reports(resp)
    assert any(rep["ok"] and rep["checked"] for rep in reports)     # 종전 규칙: 통과
    out = critic.aggregate_calculation(reports)
    assert out["failed"] is True                                    # 지금: 발화
    assert out["hard_kinds"] == ["amount"]                          # 자릿수로 잡는다
    assert any("자릿수" in r for r in out["reasons"])


def test_w1_removed_the_q065_digit_error_from_the_a8_snapshot():
    """🔴 같은 사실의 반대편 — 현행 A8 에서 Q-065 는 **정답이고 발화하지 않는다.**

    두 테스트를 함께 읽어야 W1 의 효과가 보인다: 옛 응답에서는 가드가 발화하고
    (`test_the_digit_guard_still_fires_on_the_pre_w1_q065`), 새 응답에서는 발화할 것이
    없다. 여기서 `failed` 가 True 로 돌아오면 10배 오류가 되살아난 것이다.
    """
    loaded = _a8_snapshot()
    if loaded is None:
        pytest.skip("v2/A8 산출물이 없다")
    responses, rows = loaded
    assert rows["Q-065"]["correct"] is True, "Q-065 가 다시 오답이 됐다 — W1 회귀"
    out = critic.aggregate_calculation(_calc_reports(responses["Q-065"]))
    assert out["failed"] is False, f"정답 문항에 거짓 경보: {out['reasons']}"
    assert out["hard_kinds"] == []


# --- 규칙이 낡는 것을 드러내는 계기 ------------------------------------------
# 발화 수만 보면 세계가 바뀐 것이 안 보인다. `checked`(전제가 선 횟수)를 따로 세야
# 보인다 — 이번 일이 정확히 그 사례다.

def test_completeness_audit_separates_premise_from_firing():
    """전제가 두 번 서고 한 번 발화했으면 `checked` 2 · `fired` 1 이다."""
    question = "한화오션 잠수함 계약금액은 정정 과정에서 어떻게 바뀌었는가?"
    reports = [
        critic.verify_completeness(question, "계약금액은 1,102,000,000,000원입니다.",
                                   CHAIN_EVIDENCE),
        critic.verify_completeness(question, "정정 후 1,350,000,000,000원입니다.",
                                   CHAIN_EVIDENCE),
    ]
    audit = critic.completeness_audit(reports)
    assert audit["n"] == 2
    assert audit["checked"]["stale_value"] == 2
    assert audit["fired"]["stale_value"] == 1
    assert audit["fire_rate"]["stale_value"] == 0.5
    # 전제가 안 선 규칙은 분모가 0 이다 — 0.0 으로 적으면 '잘 돌고 있다' 로 읽힌다.
    assert audit["fire_rate"]["missing_corp"] is None


@pytest.mark.parametrize("snapshot", sorted(SNAPSHOT_CAUGHT))
def test_completeness_premise_counts_match_the_recorded_snapshot(snapshot):
    """07-verifier 에 적힌 수치가 **어느 스냅샷 것인지** 코드가 붙들고 있게 한다.

    이번 혼선의 직접적 원인은 표에 스냅샷 표기가 없었다는 것이다.
    """
    loaded = _snapshot(snapshot)
    if loaded is None:
        pytest.skip(f"{snapshot} 산출물이 없다")
    responses, rows = loaded
    reports = [_verify_like_pipeline(r, rows.get(q, {}).get("route_corps") or [])[1]
               for q, r in sorted(responses.items())]

    spec = critic.COMPLETENESS_SNAPSHOTS[snapshot]
    audit = critic.completeness_audit(reports)
    assert audit["n"] == spec["n"]
    assert audit["checked"] == spec["checked"]
    assert audit["fired"] == spec["fired"]


def test_completeness_audit_flags_a_premise_that_started_standing_far_more_often():
    """검색이 좋아지면 전제가 더 자주 선다 — 그게 규칙이 낡는 기전이다.

    `stale_value` 는 Phase H 에서 1회, A6 에서 7회 섰다. 발화는 1회로 같다.
    다음 트랙이 Recall 을 더 올리면 이 계기가 먼저 어긋난다.
    """
    loaded = _snapshot("ablation_a6")
    if loaded is None:
        pytest.skip("A6 산출물이 없다")
    responses, rows = loaded
    reports = [_verify_like_pipeline(r, rows.get(q, {}).get("route_corps") or [])[1]
               for q, r in sorted(responses.items())]

    audit = critic.completeness_audit(reports, baseline="phase_h")
    assert audit["drifted"] == ["stale_value"]
    drift = audit["drift"]["stale_value"]
    assert drift["checked_then"] == 1 and drift["checked_now"] == 7
    assert drift["factor"] >= critic.PREMISE_DRIFT_FACTOR
    # 발화는 그대로다 — 발화만 보면 이 변화가 안 보인다.
    assert drift["fired_then"] == drift["fired_now"] == 1


def test_completeness_snapshots_name_the_responses_they_were_measured_on():
    """수치만 적고 출처를 안 적으면 다음 사람이 또 같은 착각을 한다."""
    for name, spec in critic.COMPLETENESS_SNAPSHOTS.items():
        assert spec["responses"].startswith("artifacts/"), name
        assert spec["labels"].endswith(".json"), name
        assert spec["golden_evidence"], name
        assert set(spec["checked"]) == set(critic.COMPLETENESS_STAGES), name
        assert set(spec["fired"]) == set(critic.COMPLETENESS_STAGES), name
