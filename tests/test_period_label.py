"""P4 · `period_label` — 분기보고서 표지에 분기를 적는다 (계획서 2026-08-21).

## 진단

`Q-105` 는 *"2025년 3분기 분기보고서"* 를 지목했고 골든 근거도 `20251114002201` 인데
답변은 `20250515002428`(1분기)을 인용했다. 두 문서가 **둘 다 근거에 실려 있었다.**

회수도 순위도 아니다. 두 블록의 머리글이 **글자 그대로 같았다.**

    ## [분기보고서 2025] 케이티 – IV. 이사의 경영진단 및 분석의견   <- 1분기
    ## [분기보고서 2025] 케이티 – IV. 이사의 경영진단 및 분석의견   <- 3분기

근거 안에 둘을 가를 것이 없었으므로 어느 쪽을 인용해도 근거상 같은 선택이다.

`_section_markdown` 의 독스트링은 머리글이 *"report and period"* 를 담는다고 적었는데
연도만 담고 있었다. 이 스위치가 그 말을 사실로 만든다.
"""
import io
import json

import pytest

from argus import ablation
from argus.agent import tools

ON = "A53"
#: 🔴 `A52` 가 아니라 `A51` 이다. `A52`(`latest_pin`)는 2026-08-21 측정에서 퇴행 2건으로
#: 기각됐고, 기각된 스위치 위에 쌓으면 그 퇴행을 물려받는다.
OFF = "A51"

#: 실측 레코드 넷 (케이티 · `Q-105` 의 근거). 지어내지 않는다 — 지어낸 입력으로 통과하는
#: 시험은 "우리 사전이 우리 예시를 먹는다" 만 증명한다.
WANT = ("20251114002201", "20250515002428", "20250814003695", "20250321001834")


@pytest.fixture(scope="module")
def records():
    """🔴 **런타임이 쓰는 딕트 모양으로 시험한다.**

    첫 판은 `artifacts/periodic_sections.jsonl` 의 줄을 읽어 시험했고 통과했다. 그런데
    근거를 만드는 경로는 그 파일이 아니라 **섹션 색인**이고, 색인의 `record_at` 은
    `base_month` 를 담지 않는다. 그래서 시험은 초록인데 112문항 전수에서 표지가 하나도
    바뀌지 않았다 — 조용한 무동작이었다.

    시험이 통과한 이유가 처방이 아니라 **다른 딕트 모양**이었던 것이다. 이 저장소가
    스무 번 겪은 실패 양식의 한 판이다.
    """
    from argus.agent import tools

    index = tools._section_index()
    if index is None:                              # pragma: no cover
        pytest.skip("섹션 색인이 없는 기계")
    out = {}
    for rcept in WANT:
        rows = tools._sections_of(index, "periodic_%s" % rcept)
        if rows:
            out[rcept] = rows[0]
    if len(out) != len(WANT):                      # pragma: no cover
        pytest.skip("색인에서 실측 레코드를 다 못 찾았다: %s"
                    % sorted(set(WANT) - set(out)))
    #: 전제를 못 박는다 — 색인 레코드에 기준월이 없다는 것이 이 처방의 난점이다.
    assert "base_month" not in out[WANT[0]], (
        "색인이 base_month 를 담게 됐다면 `_base_month_by_rcept` 우회가 필요 없어졌다")
    return out


def test_flag_off_keeps_the_header_byte_identical(records):
    """꺼진 팔의 표지는 글자 하나 달라지면 안 된다. `A49`~`A52` 산출물 대조가 깨진다."""
    with ablation.use(OFF):
        got = {rc: tools._period_label(r) for rc, r in records.items()}
    assert got["20250515002428"] == "분기보고서 2025"
    assert got["20251114002201"] == "분기보고서 2025"
    assert got["20250814003695"] == "반기보고서 2025"
    assert got["20250321001834"] == "사업보고서 2024"


def test_two_quarterlies_of_one_year_stop_colliding(records):
    """🔴 이 시험이 P4 의 전부다. 처방을 지우면 이것이 깨진다."""
    with ablation.use(ON):
        first = tools._period_label(records["20250515002428"])
        third = tools._period_label(records["20251114002201"])
    assert first == "분기보고서 2025 1분기"
    assert third == "분기보고서 2025 3분기"
    assert first != third


def test_annual_and_half_are_left_alone(records):
    """분기만 토큰을 얻는다. 연·반기는 한 해에 하나뿐이라 붙일 이유가 없고,
    붙이면 모든 정기공시 문항의 근거 문면이 바뀐다."""
    with ablation.use(ON):
        assert tools._period_label(records["20250814003695"]) == "반기보고서 2025"
        assert tools._period_label(records["20250321001834"]) == "사업보고서 2024"


def test_the_quarter_map_covers_the_whole_corpus():
    """`base_month` 가 3·9 두 값뿐이라는 전제를 못 박는다.

    이 단정이 없으면 코퍼스가 바뀌어 6월 분기보고서가 들어와도 조용히 `분기보고서` 로
    떨어지고, 그러면 P4 가 겨냥한 충돌이 되살아난다.
    """
    assert set(tools._QUARTER_NAME) == {"3", "9"}


def test_the_header_puts_the_quarter_after_the_year(records):
    """어순. 첫 판은 `[분기보고서 1분기 2025]` 를 냈다 — 표지가 연도를 뒤에 붙이는데
    분기를 종류에 붙였기 때문이다. 근거 머리글은 모델이 읽는 문면이라 어순이 장식이 아니다."""
    with ablation.use(ON):
        head = tools._section_markdown(records["20251114002201"]).splitlines()[0]
    assert "[분기보고서 2025 3분기]" in head, head


def test_the_quarter_is_looked_up_from_the_ledger():
    """색인에 없는 기준월을 대장에서 찾는다. 그 우회가 이 처방의 전부다.

    접수일로 유도하지 않는다 — 실측(2026-08-21)에서 분기보고서의 접수월 05·02 가 두
    분기에 걸쳐(정정본이 늦게 접수된다) 유일하게 결정되지 않는다.
    """
    table = tools._base_month_by_rcept()
    assert table, "대장 조회 표가 비었다"
    assert table.get("20251114002201") == "9"
    assert table.get("20250515002428") == "3"


def test_unknown_base_month_falls_back_instead_of_guessing():
    """모르는 기준월은 이름을 지어내지 않고 종류만 적는다."""
    with ablation.use(ON):
        assert tools._period_label(
            {"doc_subtype": "quarter", "base_month": "6",
             "base_year": "2025"}) == "분기보고서 2025"


def test_flag_is_on_in_the_operational_default():
    """2026-08-21 채택. 측정을 통과해 기본값에 들어갔다.

    시험 이름을 `..._is_off_...` 에서 바꿔 적는다 — 채택 뒤에도 옛 이름을 두면 참이 아닌
    문장이 시험 이름으로 남는다.
    """
    assert ablation.current().period_label is True


def test_a53_is_a51_plus_one_flag():
    """팔 하나에 스위치 하나. 그리고 **기각된 팔 위에 쌓지 않는다.**"""
    import dataclasses
    a53, a51 = ablation.ARMS["A53"], ablation.ARMS["A51"]
    differ = [f.name for f in dataclasses.fields(a53)
              if getattr(a53, f.name) != getattr(a51, f.name)]
    assert differ == ["name", "period_label"], differ
    assert ablation.ARMS["A53"].latest_pin is False, "기각된 latest_pin 을 물려받았다"
