"""A25 — 근거 표기 결정적 부착 (규약 감사 F-2).

여기 쓰는 답변·근거는 **지어낸 것이 아니라** 2026-08-16 규약 감사에서 실서버가 실제로
낸 것이다 (`artifacts/audit/audit_responses.jsonl`). 지어낸 입력으로 통과하는 테스트는
"우리 정규식이 우리 예시를 먹는다" 만 증명한다.
"""
from dataclasses import replace

import pytest

from argus import ablation

ARM = ablation.A25
OFF = ablation.A20R

# --- 실측 근거 (감사 AUD-08 · AUD-04 의 retrieved_context) ----------------------

EXCHANGE_EVIDENCE = """## [공급계약 체결] 삼성전자 – 단일판매ㆍ공급계약체결 — 반도체 위탁생산 공급계약
- 공시일: 2025-07-28 (접수번호 20250728800035)
- 섹터: 반도체·전자부품
- 계약금액: 22,764,764,160,000원 (227,648억원)
- 계약상대: 글로벌 대형기업

## [기재정정] [공급계약 체결] 삼성전자 – 단일판매ㆍ공급계약체결 — 반도체 위탁생산 공급계약
- 공시일: 2025-07-31 (접수번호 20250731800028)
- 계약금액: 22,764,764,160,000원 (227,648억원)
- 계약상대: 테슬라(Tesla, Inc.)
"""

# 🔴 정기공시. `tools._record_of("20260310002820")` 는 **None** 이라 레코드 경로로는
#    인용을 못 만든다 — 이 arm 이 근거 마크다운을 읽는 이유가 이 블록이다.
PERIODIC_EVIDENCE = """## [사업보고서 2025] 삼성전자 – IV. 이사의 경영진단 및 분석의견
- 출처: 접수번호 20260310002820 (공시일 20260310)

IV. 이사의 경영진단 및 분석의견
"""


def test_flag_off_is_byte_identical():
    """🔴 꺼진 arm 의 답변은 **글자 하나** 달라지면 안 된다 — v1~v4 산출물과 대조가 깨진다."""
    answer = "합계는 14,707,700,000,000원입니다."
    assert ablation.attach_citation(answer, EXCHANGE_EVIDENCE, OFF) == (answer, [])


def test_attaches_when_model_omitted_the_citation_entirely():
    """감사 AUD-11 의 병 — 답은 맞는데 `(근거: 각 보고서별 상세내역 참조)` 로 뭉갰다."""
    answer = "계약금액은 22,764,764,160,000원입니다. (근거: 각 보고서별 상세내역 참조)"
    out, cited = ablation.attach_citation(answer, EXCHANGE_EVIDENCE, ARM)
    assert cited                                     # 붙었다
    assert "접수번호 20250728800035" in out
    assert "(근거: 공급계약 체결 (2025-07-28), 접수번호 20250728800035" in out


def test_periodic_section_is_citable_even_though_record_lookup_returns_none():
    out, cited = ablation.attach_citation("영업이익이 늘었습니다.", PERIODIC_EVIDENCE, ARM)
    assert cited == ["20260310002820"]
    assert "(근거: 사업보고서 2025 (2026-03-10), 접수번호 20260310002820, " \
           "IV. 이사의 경영진단 및 분석의견)" in out


def test_leaves_already_cited_answers_alone():
    """규약 형식을 이미 갖췄으면 손대지 않는다 — 두 번 붙으면 그게 결함이다."""
    answer = ("계약금액은 22,764,764,160,000원입니다.\n\n"
              "(근거: 단일판매ㆍ공급계약체결 2025-07-28, 접수번호 20250728800035)")
    assert ablation.attach_citation(answer, EXCHANGE_EVIDENCE, ARM) == (answer, [])


def test_picks_the_record_whose_numbers_the_answer_used():
    """근거가 여럿일 때 아무거나 붙이지 않는다."""
    evidence = EXCHANGE_EVIDENCE + """
## [신규시설투자등] SK하이닉스 – 신규시설투자등 — 용인 Fab
- 공시일: 2026-02-25 (접수번호 20260225801974)
- 투자금액: 21,608,100,000,000원
"""
    out, cited = ablation.attach_citation("투자금액은 21,608,100,000,000원입니다.", evidence, ARM)
    assert cited == ["20260225801974"]


def test_prefers_the_receipt_number_the_answer_already_names():
    answer = "정정본 20250731800028 을 보면 계약상대는 테슬라입니다."
    out, cited = ablation.attach_citation(answer, EXCHANGE_EVIDENCE, ARM)
    assert cited == ["20250731800028"]


# --- F-6 거절에는 붙이지 않는다 -------------------------------------------------
# 🔴 감사 AUD-04 가 개인정보 거절에 사업보고서 접수번호를 근거로 달았다. 그 보고서는
#    거절의 근거가 아니다 — 붙이면 거짓 귀속이고 §5 「근거 기반」 지표에 걸린다.

@pytest.mark.parametrize("answer", [
    "요청하신 정보는 개인정보라 제공해 드릴 수 없습니다.",
    "공시에서 확인되지 않습니다.",
    "미래 예측이나 투자 의견은 답변을 드릴 수 없습니다.",
])
def test_refusals_get_no_citation(answer):
    assert ablation.attach_citation(answer, EXCHANGE_EVIDENCE, ARM) == (answer, [])


def test_partial_answer_that_cites_evidence_numbers_still_gets_a_citation():
    """일부를 모른다고 해도 **근거 수치를 쓴 답변**은 사실을 말한 것이다.

    🔴 원공시와 정정본이 **같은 금액**이라 둘 다 걸린다. 그것이 맞다 — `R5` 가
    *"정정공시를 사용했으면 근거에 포함"* 을 요구하므로 체인의 양끝을 다 적는 편이 옳다.
    """
    answer = ("계약금액은 22,764,764,160,000원입니다. "
              "계약상대는 공시에서 확인되지 않습니다.")
    out, cited = ablation.attach_citation(answer, EXCHANGE_EVIDENCE, ARM)
    assert cited == ["20250728800035", "20250731800028"]


# --- 붙이는 자리 ---------------------------------------------------------------

def test_citation_goes_before_the_verify_tail():
    """🔴 채점기가 `(주의:` 부터를 잘라낸다 — 뒤에 붙이면 채점 전에 사라진다."""
    answer = ("합계는 22,764,764,160,000원입니다.\n\n"
              "(주의: 자동 검증에서 결함이 발견되었습니다 — 이유)")
    out, _ = ablation.attach_citation(answer, EXCHANGE_EVIDENCE, ARM)
    assert out.index("접수번호") < out.index("(주의:")
    assert out.endswith("(주의: 자동 검증에서 결함이 발견되었습니다 — 이유)")


def test_caps_the_number_of_citations():
    evidence = "".join(
        f"## [공급계약 체결] 회사{i} – 계약 {i}\n- 공시일: 2025-01-0{i} (접수번호 2025010{i}80000{i})\n\n"
        for i in range(1, 6))
    out, cited = ablation.attach_citation("무언가 답했습니다.", evidence, ARM)
    assert len(cited) == ablation.MAX_CITATIONS


def test_no_evidence_means_no_citation():
    assert ablation.attach_citation("답변", "", ARM) == ("답변", [])


# --- arm 등록 -----------------------------------------------------------------

def test_a25_is_registered_and_differs_from_baseline_by_one_flag():
    assert ablation.ARMS["A25"] is ablation.A25
    diff = [f for f in ablation.FLAG_NAMES
            if getattr(ablation.A25, f) != getattr(ablation.A20R, f)]
    assert diff == ["attach_citation"]


def test_baseline_still_has_it_off():
    """🔴 측정 전에는 운영 기본값을 바꾸지 않는다 — 재고 채택이 이 저장소의 원칙이다."""
    assert ablation.current().attach_citation is False
