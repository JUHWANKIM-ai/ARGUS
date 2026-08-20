"""P3 · `cite_used` — 답변이 쓴 근거를 다 인용한다 (계획서 2026-08-21).

여기 쓰는 답변과 근거는 지어낸 것이 아니라 **`A49` 로컬/서버 실행이 실제로 낸 것**이다
(`scratch/ablation/v7/A49_valuechain_r1` · `scratch/ablation/v6/server_full`). 지어낸
입력으로 통과하는 시험은 "우리 정규식이 우리 예시를 먹는다" 만 증명한다.

## 무엇이 문제였나

`attach_citation` 은 `quick_check(answer)["has_citation"]` 이 참이면 아무것도 하지
않았다. 그 검사는 *"`근거: …14자리` 가 하나라도 있는가"* 만 묻는다. 그래서 답변이 **틀린
문서 하나를 가리키면** 조건이 충족되고 손에 쥔 나머지 근거는 인용되지 않는다.

실측(2026-08-21, `scratch/audit/keypoint_literal_local.json`): 골든이 접수번호를 요점으로
요구하는 자리가 9문항 10요점이고 전부 답변에 없다. `Q-110` 은 근거에 실린 골든 다섯을
두고 골든 아닌 문서 셋을 가리켰다.
"""
import pytest

from argus import ablation

ON = ablation.ARMS["A51"]
OFF = ablation.ARMS["A49"]

# --- 실측 근거 (`Q-110` valuechain 근거의 두 블록) ------------------------------
#
# 한전기술 블록은 답변이 **인용한** 것이고, 대우건설 블록은 답변이 **금액을 쓰고도
# 인용하지 않은** 것이다. 이 둘이 P3 의 전부다.
EVIDENCE = """## [공급계약 체결] 대우건설 – 단일판매ㆍ공급계약체결 — 홍천양수발전소 1,2호기 토건공사
- 공시일: 2025-08-29 (접수번호 20250829800405)
- 계약금액: 219,900,000,000원
- 계약상대: 한국수력원자력

## [기재정정] [공급계약 체결] 한전기술 – 단일판매ㆍ공급계약체결 — 원자로계통설계용역
- 공시일: 2025-10-27 (접수번호 20251027800516)
- 계약금액: 118,800,000,000원
- 계약상대: 한국수력원자력
"""

#: 답변이 두 금액을 다 쓰면서 한전기술 하나만 가리킨 모양. `Q-110` 의 병이다.
ANSWER_ONE_SIDED = (
    "한국수력원자력을 계약상대로 하는 계약은 다음과 같습니다.\n"
    "1. **대우건설** — 홍천양수발전소 1,2호기 토건공사 219,900,000,000원\n"
    "2. **한전기술** — 원자로계통설계용역 118,800,000,000원\n"
    "(근거: 단일판매ㆍ공급계약체결, 2025-10-27, 접수번호 20251027800516)"
)


def test_flag_off_is_byte_identical():
    """꺼진 팔의 답변은 글자 하나 달라지면 안 된다. `A49` 산출물과의 대조가 깨진다."""
    assert ablation.attach_citation(ANSWER_ONE_SIDED, EVIDENCE, OFF) == (
        ANSWER_ONE_SIDED, [])


def test_cites_the_evidence_the_answer_used_but_did_not_point_at():
    """🔴 이 시험이 P3 의 전부다. 처방을 지우면 이것이 깨진다."""
    out, attached = ablation.attach_citation(ANSWER_ONE_SIDED, EVIDENCE, ON)
    assert attached == ["20250829800405"], attached
    assert "20250829800405" in out
    #: 원래 있던 표기는 지우지 않는다. 답변이 가리킨 것을 뺏는 처방이 아니다.
    assert "20251027800516" in out


def test_does_not_repeat_a_receipt_the_answer_already_cited():
    """같은 번호를 두 번 붙이면 표기가 답변보다 길어진다."""
    answer = ANSWER_ONE_SIDED + " (근거: 단일판매ㆍ공급계약체결, 2025-08-29, 접수번호 20250829800405)"
    out, attached = ablation.attach_citation(answer, EVIDENCE, ON)
    assert attached == []
    assert out == answer


def test_counts_a_bare_receipt_as_already_cited():
    """답변은 `접수번호` 라는 낱말 없이 적는 일이 많다. 그것도 가리킨 것으로 센다.

    이 단정이 없으면 `_CITE_RCEPT`(낱말을 요구하는 쪽)를 그대로 써도 통과해 버리고,
    같은 번호가 두 번 붙는다.
    """
    answer = ANSWER_ONE_SIDED.replace("접수번호 20251027800516", "20251027800516")
    answer += " (근거: 단일판매ㆍ공급계약체결, 2025-08-29, 20250829800405)"
    out, attached = ablation.attach_citation(answer, EVIDENCE, ON)
    assert attached == []
    assert out == answer


def test_refusal_still_gets_no_citation():
    """거절에 근거를 붙이면 없는 출처를 지목하는 것이 된다. `cite_used` 가 그 규칙을 못 뚫는다."""
    answer = "공시에서 확인되지 않습니다. 제공된 70개사 범위 밖입니다."
    out, attached = ablation.attach_citation(answer, EVIDENCE, ON)
    assert attached == []
    assert "접수번호" not in out


def test_answer_that_used_nothing_gets_nothing():
    """근거의 어느 수도 안 쓴 답변에 붙이면 거짓 귀속이다."""
    answer = "해당 계약의 계약상대는 한국수력원자력입니다. (근거: 단일판매ㆍ공급계약체결, 2025-10-27, 접수번호 20251027800516)"
    out, attached = ablation.attach_citation(answer, EVIDENCE, ON)
    #: 한전기술 블록만 `used` 이고 이미 인용돼 있다. 대우건설 금액은 답변에 없다.
    assert attached == []
    assert out == answer


def test_citation_lands_before_the_verification_note():
    """채점기는 `(주의:` 부터 뒤를 잘라낸다. 뒤에 붙으면 채점 전에 사라진다."""
    answer = ANSWER_ONE_SIDED + "\n\n(주의: 자동 검증에서 결함이 발견되었습니다)"
    out, _ = ablation.attach_citation(answer, EVIDENCE, ON)
    assert out.index("20250829800405") < out.index("(주의:")


def test_cap_is_higher_than_the_base_cap_and_bounded():
    """열거형은 여섯 갈래를 세는 일이 있어 3 으로는 못 담는다. 그래도 무한은 아니다."""
    assert ablation.MAX_CITATIONS_USED > ablation.MAX_CITATIONS
    #: 근거 칸이 10건까지 담으므로 그보다 크면 상한이 아니다.
    assert ablation.MAX_CITATIONS_USED <= 10


def test_cap_actually_truncates():
    """상한이 상한인지 센다. 값만 크게 적어 두고 안 쓰이면 상한이 아니다."""
    blocks, amounts = [], []
    for i in range(ablation.MAX_CITATIONS_USED + 3):
        rcept = "2025010%s00000%d" % (str(i).zfill(2), i)
        amount = "%d,000,000,000" % (100 + i)
        amounts.append(amount)
        blocks.append(
            "## [공급계약 체결] 회사%d – 단일판매ㆍ공급계약체결 — 계약%d\n"
            "- 공시일: 2025-01-02 (접수번호 %s)\n- 계약금액: %s원\n"
            % (i, i, rcept, amount))
    evidence = "\n".join(blocks)
    answer = ("금액은 " + " · ".join("%s원" % a for a in amounts) + "입니다. "
              "(근거: 단일판매ㆍ공급계약체결, 2025-01-02, 접수번호 20250100000000)")
    _, attached = ablation.attach_citation(answer, evidence, ON)
    assert len(attached) == ablation.MAX_CITATIONS_USED, len(attached)


#: 실측 근거 (`Q-102` 의 근거 두 블록). 5월 계약과 11월 계약의 금액이 **같다**.
#: 답변이 500억을 말했을 때 어느 쪽을 썼는지 그 수로는 못 가린다.
AMBIGUOUS_EVIDENCE = """## [자기주식취득 신탁계약 체결] 시프트업 – 주요사항보고서
- 공시일: 2025-05-12 (접수번호 20250512000665)
- 계약금액: 50,000,000,000원

## [자기주식취득 신탁계약 체결] 시프트업 – 주요사항보고서
- 공시일: 2025-11-12 (접수번호 20251112000393)
- 계약금액: 50,000,000,000원
"""


def test_a_figure_two_blocks_share_is_not_a_link():
    """🔴 유일성 규칙. 이것이 C1 과 C1u 를 가른 자리다.

    실측(2026-08-21): 유일성 없이 수 겹침만 보면 `Q-102` 의 5월 질문에 11월 공시가
    붙는다 — 둘 다 500억이라서다. 112문항에서 골든 아닌 부착이 23건에서 4건으로
    줄어든 것이 이 조건 하나 때문이다.

    README §4.3 이 집계에 대해 적은 규칙과 같다 — 유일하게 결정되지 않으면 싣지 않는다.
    """
    answer = ("계약금액은 50,000,000,000원입니다. "
              "(근거: 주요사항보고서, 2025-05-12, 접수번호 20250512000665)")
    out, attached = ablation.attach_citation(answer, AMBIGUOUS_EVIDENCE, ON)
    assert attached == [], attached
    assert "20251112000393" not in out
    assert out == answer


def test_a_figure_only_one_block_carries_is_a_link():
    """유일한 수는 연결이다. 위 시험의 짝 — 조건을 뒤집으면 붙어야 한다.

    이 짝이 없으면 `_cite_missing_used` 가 아무것도 안 붙이도록 망가져도 위 시험은
    통과한다.
    """
    evidence = AMBIGUOUS_EVIDENCE.replace(
        "- 계약금액: 50,000,000,000원\n\n## [자기주식취득",
        "- 계약금액: 50,000,000,000원\n\n## [자기주식취득", 1).replace(
        "- 공시일: 2025-11-12 (접수번호 20251112000393)\n- 계약금액: 50,000,000,000원",
        "- 공시일: 2025-11-12 (접수번호 20251112000393)\n- 계약금액: 70,000,000,000원")
    answer = ("계약금액은 50,000,000,000원과 70,000,000,000원입니다. "
              "(근거: 주요사항보고서, 2025-05-12, 접수번호 20250512000665)")
    out, attached = ablation.attach_citation(answer, evidence, ON)
    assert attached == ["20251112000393"], attached


def test_flag_is_off_in_the_operational_default():
    """스위치는 켠 팔에서만 돈다. 기본값을 바꾸는 것은 측정 뒤의 별도 결정이다."""
    assert ablation.current().cite_used is False
