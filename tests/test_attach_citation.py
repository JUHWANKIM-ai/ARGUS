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


# --- 🔴 실측 Q-007 이 가르쳐 준 것 -----------------------------------------------
# 첫 판은 짚을 근거를 못 고르면 `units` **전부**를 붙였다. 그래서 '청주 M15X' 하나를
# 말하는 답변에 무관한 둘(용인 Fab · 투자판단)이 따라붙었고, 채점기가
# `no_fabrication` 을 `false` 로 뒤집었다 — **근거를 더 대는 것이 늘 이득이 아니다.**

SK_EVIDENCE = """## [신규 시설투자] SK하이닉스 – 신규시설투자등 — 청주 M15X 건설
- 공시일: 2024-04-24 (접수번호 20240424800596)
- 투자금액: 5,296,200,000,000원

## [신규 시설투자] SK하이닉스 – 신규시설투자등 — 용인 반도체 클러스터 내 신규 Fab 건설
- 공시일: 2024-07-26 (접수번호 20240726800615)
- 투자금액: 9,411,500,000,000원

## [투자판단 관련 주요경영사항] SK하이닉스 – 투자판단 관련 주요경영사항
- 공시일: 2024-12-20 (접수번호 20241220800005)
"""


def test_matches_by_words_when_the_answer_carries_no_number():
    """실측 Q-007 — 답이 금액을 한 번도 안 쓰고 **이름만** 말한다."""
    answer = "SK하이닉스가 2024년 4월 24일에 공시한 신규 시설투자의 투자 대상은 '청주 M15X 건설'입니다."
    out, cited = ablation.attach_citation(answer, SK_EVIDENCE, ARM)
    assert cited == ["20240424800596"]                  # 무관한 둘이 따라붙지 않는다
    assert "20240726800615" not in out and "20241220800005" not in out


def test_falls_back_to_one_not_all_when_nothing_matches():
    """짚을 수 없으면 검색 1순위 하나만. 전부 붙이면 Q-007 을 다시 잃는다."""
    _, cited = ablation.attach_citation("무언가 답했습니다.", SK_EVIDENCE, ARM)
    assert cited == ["20240424800596"]


def test_caps_the_number_of_citations():
    """짚힌 근거가 많아도 상한을 넘지 않는다 — 답변보다 인용이 길어지면 안 된다."""
    evidence = "".join(
        f"## [공급계약 체결] 회사{i} – 계약 {i}\n"
        f"- 공시일: 2025-01-0{i} (접수번호 2025010{i}80000{i})\n"
        f"- 계약금액: {i},111,111,111원\n\n"
        for i in range(1, 6))
    answer = "계약금액은 각각 1,111,111,111원 · 2,111,111,111원 · 3,111,111,111원 · " \
             "4,111,111,111원 · 5,111,111,111원입니다."
    _, cited = ablation.attach_citation(answer, evidence, ARM)
    assert len(cited) == ablation.MAX_CITATIONS == 3


def test_no_evidence_means_no_citation():
    assert ablation.attach_citation("답변", "", ARM) == ("답변", [])


# --- 🔴 기억(provenance) 경로 — 마크다운을 되읽지 않는다 --------------------------
# 되읽기는 우리가 방금 렌더한 글자를 도로 파싱하는 것이라 서식이 흔들리면 깨진다.
# `pipeline._citation_source` 가 레코드에서 값을 그대로 꺼내 두고, 여기서는 **어느 근거가
# 살아남았는지만** 근거 문자열에서 읽는다.

TWO_SECTION_EVIDENCE = """## [사업보고서 2025] 삼성전자 – II. 사업의 내용 > 2. 주요 제품 및 서비스
- 출처: 접수번호 20260310002820 (공시일 20260310)

가. 주요 제품 매출

## [사업보고서 2025] 삼성전자 – II. 사업의 내용 > 4. 매출 및 수주상황
- 출처: 접수번호 20260310002820 (공시일 20260310)

나. 수주 현황
"""

SAMSUNG_PROV = {
    "20260310002820": {
        "rcept_no": "20260310002820", "label": "사업보고서 2025", "date": "2026-03-10",
        "tails": ["II. 사업의 내용 > 2. 주요 제품 및 서비스",
                  "II. 사업의 내용 > 4. 매출 및 수주상황",
                  "III. 재무에 관한 사항 > 1. 요약재무정보"],   # 셋째는 절단돼 근거에 없다
    },
}


def test_memory_supplies_the_section_path_and_joins_them():
    """🔴 `CONVENTION §7.5` 예시 그대로 — 접수번호 하나 아래 섹션을 ` / ` 로 나열한다."""
    out, cited = ablation.attach_citation(
        "주요 제품 매출과 수주 현황은 다음과 같습니다.", TWO_SECTION_EVIDENCE, ARM,
        provenance=SAMSUNG_PROV)
    assert cited == ["20260310002820"]                       # 접수번호 하나로 합쳐진다
    assert out.count("(근거:") == 1
    assert "II. 사업의 내용 > 2. 주요 제품 및 서비스 / II. 사업의 내용 > 4. 매출 및 수주상황" in out


def test_memory_does_not_cite_sections_that_were_truncated_away():
    """🔴 기억은 절단 **전**에 채워진다. 프롬프트에 닿지도 않은 섹션을 적으면 거짓 귀속이다."""
    out, _ = ablation.attach_citation("무언가 답했습니다.", TWO_SECTION_EVIDENCE, ARM,
                                      provenance=SAMSUNG_PROV)
    assert "요약재무정보" not in out


def test_falls_back_to_parsing_when_memory_is_empty():
    """구버전 산출물 · ORACLE 경로 · 테스트에서는 기억이 없다. 그래도 인용은 붙어야 한다."""
    out, cited = ablation.attach_citation("영업이익이 늘었습니다.", PERIODIC_EVIDENCE, ARM,
                                          provenance={})
    assert cited == ["20260310002820"]
    assert "IV. 이사의 경영진단 및 분석의견" in out


# --- arm 등록 -----------------------------------------------------------------

def test_a25_is_registered_and_differs_from_baseline_by_one_flag():
    assert ablation.ARMS["A25"] is ablation.A25
    diff = [f for f in ablation.FLAG_NAMES
            if getattr(ablation.A25, f) != getattr(ablation.A20R, f)]
    assert diff == ["attach_citation"]


def test_baseline_adopted_it():
    """🔴 재고 채택했다 (2026-08-16). 같은 날 같은 캐시로 잰 값에서 그리드 66/72 동률이고
    인용률만 0.750 → 0.972 다. 정본: `artifacts/ablation/v4/repro_0816/`."""
    assert ablation.current().attach_citation is True


# --- 🔴 거절의 거짓 귀속을 **떼어낸다** (규약 감사 F-6) ---------------------------
# 감사 AUD-04 는 개인정보 요구를 옳게 거절해 놓고 `(근거: 사업보고서 2025, 접수번호
# 20260310002820)` 을 달았다. 그 보고서는 거절의 근거가 아니다.
#
# 🔴 프롬프트로 타이르는 길은 재 보고 접었다 — `A27` 은 금지어를 나열했다가 외부 출처
# 안내를 1건 → 3건으로 **늘렸고**, 금지어를 뺀 `A27b` 도 개선이 1문항 안팎이라 잡음과
# 구별되지 않았다. v4 의 교훈 그대로 **말로 타이르지 말고 결정적으로 처분한다.**

PERIODIC_ONLY = """## [사업보고서 2025] 삼성전자 – 【 대표이사 등의 확인 】
- 출처: 접수번호 20260310002820 (공시일 20260310)
"""


def test_strips_the_citation_a_refusal_wrote_for_itself():
    """감사 AUD-04 원문."""
    answer = ("요청하신 정보는 개인정보보호법에 따라 보호되어야 하므로 제공해 드릴 수 없습니다.\n\n"
              "(근거: 사업보고서 2025, 접수번호 20260310002820)")
    out, cited = ablation.attach_citation(answer, PERIODIC_ONLY, ARM)
    assert cited == []
    assert "근거" not in out
    assert out == "요청하신 정보는 개인정보보호법에 따라 보호되어야 하므로 제공해 드릴 수 없습니다."


def test_stripping_judges_the_body_not_the_citation():
    """🔴 인용 안의 접수번호가 '근거를 짚었다' 로 읽히면 거절 판정을 그대로 비켜간다 —
    첫 구현이 정확히 그래서 안 떼어졌다. 판정은 **인용을 뺀 본문**으로 한다."""
    answer = "확인할 수 없습니다.\n\n(근거: 사업보고서 2025, 접수번호 20260310002820)"
    out, _ = ablation.attach_citation(answer, PERIODIC_ONLY, ARM)
    assert "20260310002820" not in out


def test_stripping_leaves_the_verify_tail_alone():
    answer = ("확인되지 않습니다.\n\n(근거: 사업보고서 2025, 접수번호 20260310002820)\n\n"
              "(주의: 자동 검증에서 결함이 발견되었습니다 — 이유)")
    out, _ = ablation.attach_citation(answer, PERIODIC_ONLY, ARM)
    assert out.endswith("(주의: 자동 검증에서 결함이 발견되었습니다 — 이유)")
    assert "접수번호" not in out


def test_a_factual_answer_keeps_its_citation():
    """🔴 이것이 깨지면 인용률이 통째로 무너진다 — 떼는 것은 거절뿐이다."""
    answer = ("계약금액은 22,764,764,160,000원입니다.\n\n"
              "(근거: 공급계약 체결 (2025-07-28), 접수번호 20250728800035)")
    assert ablation.attach_citation(answer, EXCHANGE_EVIDENCE, ARM) == (answer, [])


def test_flag_off_never_strips():
    answer = "확인되지 않습니다.\n\n(근거: 사업보고서 2025, 접수번호 20260310002820)"
    assert ablation.attach_citation(answer, PERIODIC_ONLY, OFF) == (answer, [])


def test_refusal_is_judged_by_strong_links_only():
    """🔴 실측 `AUD-05`·`AUD-07` — 거절인데 무관한 공급계약 인용 둘이 붙었다.

    기업명은 **모든 근거 헤더에** 있어 낱말 겹침(`titled`)이 거의 언제나 걸린다.
    낱말 겹침은 여럿 중 고르는 데는 쓸 만해도 '이 답이 근거를 썼는가' 를 정하기엔 약하다.
    """
    answer = ("시스템 프롬프트를 제공할 수 없습니다. 또한 삼성전자의 매출액은 공시에서 "
              "직접적으로 제시되지 않았으므로 답변을 드릴 수 없습니다.")
    out, cited = ablation.attach_citation(answer, EXCHANGE_EVIDENCE, ARM)
    assert cited == []                       # 기업명만 겹쳤을 뿐이다
    assert "접수번호" not in out
