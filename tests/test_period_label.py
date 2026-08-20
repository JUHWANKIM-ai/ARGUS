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
OFF = "A52"

#: 실측 레코드 넷 (케이티 · `Q-105` 의 근거). 지어내지 않는다 — 지어낸 입력으로 통과하는
#: 시험은 "우리 사전이 우리 예시를 먹는다" 만 증명한다.
WANT = ("20251114002201", "20250515002428", "20250814003695", "20250321001834")


@pytest.fixture(scope="module")
def records():
    out = {}
    path = "artifacts/periodic_sections.jsonl"
    try:
        handle = io.open(path, encoding="utf-8")
    except FileNotFoundError:                      # pragma: no cover
        pytest.skip("전처리 산출물이 없는 기계")
    with handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            rcept = str(row.get("doc_id", "")).split("_")[-1]
            if rcept in WANT and rcept not in out:
                out[rcept] = row
            if len(out) == len(WANT):
                break
    if len(out) != len(WANT):                      # pragma: no cover
        pytest.skip("실측 레코드를 다 못 찾았다: %s" % sorted(set(WANT) - set(out)))
    return out


def test_flag_off_keeps_the_header_byte_identical(records):
    """꺼진 팔의 표지는 글자 하나 달라지면 안 된다. `A49`~`A52` 산출물 대조가 깨진다."""
    with ablation.use(OFF):
        got = {rc: tools._period_label(r) for rc, r in records.items()}
    assert got["20250515002428"] == "분기보고서"
    assert got["20251114002201"] == "분기보고서"
    assert got["20250814003695"] == "반기보고서"
    assert got["20250321001834"] == "사업보고서"


def test_two_quarterlies_of_one_year_stop_colliding(records):
    """🔴 이 시험이 P4 의 전부다. 처방을 지우면 이것이 깨진다."""
    with ablation.use(ON):
        first = tools._period_label(records["20250515002428"])
        third = tools._period_label(records["20251114002201"])
    assert first == "분기보고서 1분기"
    assert third == "분기보고서 3분기"
    assert first != third


def test_annual_and_half_are_left_alone(records):
    """분기만 토큰을 얻는다. 연·반기는 한 해에 하나뿐이라 붙일 이유가 없고,
    붙이면 모든 정기공시 문항의 근거 문면이 바뀐다."""
    with ablation.use(ON):
        assert tools._period_label(records["20250814003695"]) == "반기보고서"
        assert tools._period_label(records["20250321001834"]) == "사업보고서"


def test_the_quarter_map_covers_the_whole_corpus():
    """`base_month` 가 3·9 두 값뿐이라는 전제를 못 박는다.

    이 단정이 없으면 코퍼스가 바뀌어 6월 분기보고서가 들어와도 조용히 `분기보고서` 로
    떨어지고, 그러면 P4 가 겨냥한 충돌이 되살아난다.
    """
    assert set(tools._QUARTER_NAME) == {"3", "9"}


def test_unknown_base_month_falls_back_instead_of_guessing():
    """모르는 기준월은 이름을 지어내지 않고 종류만 적는다."""
    with ablation.use(ON):
        assert tools._period_label(
            {"doc_subtype": "quarter", "base_month": "6"}) == "분기보고서"


def test_flag_is_off_in_the_operational_default():
    assert ablation.current().period_label is False


def test_a53_is_a52_plus_one_flag():
    import dataclasses
    a53, a52 = ablation.ARMS["A53"], ablation.ARMS["A52"]
    differ = [f.name for f in dataclasses.fields(a53)
              if getattr(a53, f.name) != getattr(a52, f.name)]
    assert differ == ["name", "period_label"], differ
