"""P-A② · `roster_items` — 결정적 조회 줄이 항목마다 출처를 나른다 (팔 `A60`).

계획서 `docs/superpowers/plans/2026-08-21-paper-grounded-repairs.md` 의 P-A 뒤쪽.

## 무엇이 문제였나

`Q-110` 의 결정적 조회 줄은 **회사명 6곳만** 실었다.

    [결정적 조회] 한국수력원자력(주) 를 계약상대로 공시한 상장사는 6곳이다:
    대우건설 · 두산에너빌리티 · 두산퓨얼셀 · 우리기술 · 한전기술 · 현대건설

그래서 답변이 항목마다 근거를 못 달고(오픈이슈 v2 §4.1) 항목별 금액도 못 적었다.
`cite_used` 가 이 문항에 못 닿는 이유도 같다 — 답변이 금액을 하나도 안 쓰니 붙일 근거를
고를 수 없다.

## 🔴 왜 종전 판이 접수번호를 안 실었나 — 지표 부풀리기

`scripts/error_analysis._blocks` 가 근거를 `^## ` 로 쪼갠다. **첫 머리글 위의 모든 것이 한
블록으로 세어지고**, `evidence_hit` 은 거기 있는 접수번호를 「실린 골든」으로 읽는다.
근거에 없는 회사의 접수번호를 이 줄에 적으면 **아무것도 싣지 않고 우리 근거 완전성만
올린다.** `Q-110` 의 두산퓨얼셀 골든(`20240329800342`)이 정확히 그 경우다 — 어떤 hop
후보에도 안 닿는다.

그래서 **근거에 실린 공시만** 접수번호를 받는다. `_gather_counterparty` 가 `taken` 을 채운
**뒤에** 줄을 만든다. 부풀리기가 원리적으로 불가능해진다.

## 측정 (서버 3회 · `scratch/audit/gate_a60.json`)

    Q-110   정답 0/3 → 3/3 · 근거 표시 0/3 → 3/3
    Q-031 Q-109 Q-114 Q-116   변화 없음
    근거 적재   다섯 문항 모두 A56 과 글자 그대로 같다 (부풀리기 0)
"""
import pytest

from argus import ablation
from argus.agent import pipeline

PARTNERS = ("대우건설", "두산에너빌리티", "두산퓨얼셀", "한전기술")
#: 근거에 **실린** 공시만. 두산퓨얼셀은 일부러 빠져 있다 — 그 회사의 골든이 회수되지
#: 않는 것이 `Q-110` 의 실제 상태다.
LOADED = {
    "두산에너빌리티": {"rcept_no": "20230329801108",
                 "subject": "신한울 원자력 발전소 3,4호기 원자로설비 공급 계약",
                 "amount": "2,338,117,950,000원"},
    "한전기술": {"rcept_no": "20230417800192",
              "subject": "2023년도 가동원전 계통설계 기술용역",
              "amount": "25,605,030,000원"},
}


def test_off_carries_names_only():
    """`A56` 의 동작. 이 시험이 초록인 동안은 기준선이 안 움직였다는 뜻이다."""
    with ablation.use("A56"):
        out = pipeline._roster_line("한국수력원자력(주)", PARTNERS, LOADED)
    assert "20230329801108" not in out
    assert "2,338,117,950,000" not in out
    for name in PARTNERS:
        assert name in out


def test_on_carries_the_receipt_and_the_amount():
    with ablation.use("A60"):
        out = pipeline._roster_line("한국수력원자력(주)", PARTNERS, LOADED)
    assert "20230329801108" in out
    assert "2,338,117,950,000원" in out
    assert "20230417800192" in out
    assert "25,605,030,000원" in out


def test_a_partner_with_no_loaded_filing_gets_no_receipt():
    """🔴 이것이 지표 부풀리기를 막는 단정이다.

    근거에 안 실린 회사의 접수번호를 적으면 `evidence_hit` 이 그것을 실린 골든으로
    읽는다. 실측으로 확인했다 — `Q-110` 의 근거 적재가 `A56` 과 같은 5/6 이고 명세 줄에서만
    잡히는 골든은 없다.
    """
    with ablation.use("A60"):
        out = pipeline._roster_line("한국수력원자력(주)", PARTNERS, LOADED)
    assert "두산퓨얼셀" in out, "이름은 목록에 남는다 — 답변이 빠짐없이 적어야 한다"
    #: 두산퓨얼셀 뒤에 접수번호가 붙은 줄이 없다.
    for line in out.splitlines():
        if line.strip().startswith("· 두산퓨얼셀"):
            pytest.fail(f"실리지 않은 회사에 항목 줄이 붙었다: {line}")
    assert "지어내지 않는다" in out, "그 사실을 근거에 밝힌다"


def test_on_with_nothing_loaded_falls_back_to_names():
    with ablation.use("A60"):
        out = pipeline._roster_line("한국수력원자력(주)", PARTNERS, {})
    assert "접수번호" not in out
    assert "6곳" not in out
    assert "4곳이다" in out


def test_the_count_and_the_directive_survive():
    """목록의 경계와 처분은 두 팔에서 같아야 한다 — 그 문장이 답변을 묶는다."""
    for arm in ("A56", "A60"):
        with ablation.use(arm):
            out = pipeline._roster_line("한국수력원자력(주)", PARTNERS, LOADED)
        assert "4곳이다" in out
        assert "빠짐없이 적고 임의로 줄이지 않는다" in out


def test_filing_counts_are_never_carried():
    """건수는 두 팔 어디에도 안 싣는다.

    `_counterparty_note` 가 이유를 적는다 — 전역 건수는 정정 반영이 안 됐고 질문 범위
    밖이며, 한 번은 답변에 그대로 새어 나갔다. `roster_items` 는 그 결정을 안 되돌린다.
    """
    with ablation.use("A60"):
        out = pipeline._roster_line("한국수력원자력(주)", PARTNERS, LOADED)
    assert "건)" not in out
    assert "공시 건수" not in out


def test_the_flag_needs_its_parent():
    """부모가 꺼지면 `roster_out` 자체를 안 넘긴다."""
    from dataclasses import replace

    with pytest.raises(ValueError, match="roster_items"):
        replace(ablation.ARMS["A0"], name="TEST", roster_items=True)


# --- `roster_counts` — 상대별 건수를 세 조건을 채워 싣는다 --------------------

_COUNTS = {"삼성E&A": {"total": 47, "latest": 12},
           "삼성중공업": {"total": 19, "latest": 7}}
_PARTNERS = ["삼성E&A", "삼성중공업"]


def _counted(arm="A78"):
    """`roster_items` 가 실을 행이 없을 때. 그때는 회사 이름 뒤에 건수만 붙는다."""
    from argus.agent.pipeline import _roster_line

    with ablation.use(arm):
        return _roster_line("삼성전자(주)", _PARTNERS, None, _COUNTS)


_LOADED = {
    "삼성E&A": {"rcept_no": "20230517800369", "subject": "설계 및 마감공사",
              "amount": "462,660,000,000원"},
    "삼성중공업": {"rcept_no": "20231121800248", "subject": "[P3 Ph2]FAB동 마감공사",
               "amount": "390,787,100,000원"},
}


def test_roster_counts_골든의_두_수를_낸다():
    """`Q-114` 요점1 — *"삼성E&A(47건)와 삼성중공업(19건) 두 곳이다"*."""
    out = _counted()
    assert "삼성E&A 47건" in out and "삼성중공업 19건" in out


def test_roster_counts_는_대표계약_행_안에_들어간다():
    """🔴 별도 행으로 두었을 때 답변이 3회 모두 그 수를 안 썼다.

    회사마다 괄호가 둘(건수 · 대표계약)이 되고 답변은 대표계약 쪽만 베꼈다. 라벨 문면을
    금지에서 허가로 바꿔도 그대로였으니 문면이 아니라 **자리** 문제였다.
    """
    from argus.agent.pipeline import _roster_line

    with ablation.use("A78"):
        out = _roster_line("삼성전자(주)", _PARTNERS, _LOADED, _COUNTS)
    assert ("  · 삼성E&A 47건(최신본 12건) — 설계 및 마감공사 462,660,000,000원"
            " (접수번호 20230517800369)") in out
    #: 건수만 있는 행이 따로 있으면 안 된다.
    assert "  · 삼성E&A 47건(최신본 12건)\n" not in out


def test_roster_counts_최신본_수를_숨기지_않는다():
    """🔴 건수를 뺐던 이유가 「전역 집계가 정정 대체를 안 걸러 참이 아닐 수 있다」였다.

    코퍼스 실측으로 삼성E&A 47건 중 최신본은 12건뿐이다. 골든이 쓰는 수는 전체 쪽이지만
    최신본을 숨기면 그 지적이 그대로 남는다. 그래서 나란히 적는다.
    """
    out = _counted()
    assert "최신본 12건" in out and "최신본 7건" in out


def test_roster_counts_범위를_라벨에_박는다():
    """🔴 이 줄에 적은 낱말은 답변에 복사된다고 가정해야 한다(`Q-117` 실측).

    그러니 「전역 집계다」라는 사실도 복사되게 두는 것이 맞다.
    """
    out = _counted()
    #: 🔴 첫 판은 *"코퍼스 전체 집계이고 질문이 지목한 사업장에 한정한 수가 아니다"* 였다.
    #: 모델이 그것을 「이 질문에 쓰지 마라」로 읽고 3회 모두 47·19 를 안 썼다 — 라벨이
    #: 참이고 모델의 판단도 옳았다. 금지가 아니라 **범위를 정한 허가**로 바꿨다.
    from argus.agent.pipeline import _roster_line

    with ablation.use("A78"):
        out = _roster_line("삼성전자(주)", _PARTNERS, _LOADED, _COUNTS)
    assert "를 계약상대로 한 공시 **전체**를 센 것이다" in out
    assert "「어느 상장사가 몇 건을 공시했는가」에는 이 수를 그대로 쓰고" in out


def test_roster_counts_스위치가_꺼지면_건수가_없다():
    out = _counted("A77")
    assert "47건" not in out and "최신본" not in out
    assert "삼성E&A · 삼성중공업" in out, "목록 자체는 그대로다"


def test_roster_counts_건수가_없으면_줄이_안_바뀐다():
    from argus.agent.pipeline import _roster_line

    with ablation.use("A78"):
        assert "건" not in _roster_line("삼성전자(주)", _PARTNERS, None, None) \
            .split("곳이다:")[1]


def test_roster_counts_와_roster_items_가_함께_실린다():
    """🔴 `roster_items` 블록이 `detail` 을 **대입**해 건수 줄을 통째로 덮었다.

    줄이 붙은 줄 알고 3회를 잰 뒤에야 드러났다 — 답변에 47·19 가 하나도 없어서
    거슬러 올라간 끝에 찾았다. 둘은 같은 `detail` 에 쌓이므로 `+=` 여야 한다.
    """
    from argus.agent.pipeline import _roster_line

    with ablation.use("A78"):
        out = _roster_line("삼성전자(주)", _PARTNERS, _LOADED, _COUNTS)
    assert "삼성E&A 47건" in out, "건수가 덮였다"
    assert "20230517800369" in out, "접수번호 줄이 사라졌다"
