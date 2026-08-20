"""P5 · `contrast_line` — 스텁과 본문의 범주 차이를 결정적으로 적는다 (계획서 2026-08-21).

## 진단

`Q-105` 는 한 섹션이 세 보고서에서 어떻게 기재됐는지 비교하라고 묻는다. 근거에 실린 것은
이랬다.

    반기보고서 2025      88자   (스텁)
    분기보고서 2025      88자   (스텁)
    분기보고서 2025      88자   (스텁)
    사업보고서 2024   7,846자   (본문)

답변은 *"따라서 세 문서 간 차이점은 없으며"* 라고 적었다. **89배 차이를 손에 쥐고** 같다고
말한 것이다. README §4.3 은 잘못된 값을 확신 있게 제시하는 것을 금하는데, 잘못된 비교도
서술 축에서 같은 결함이다.

## 왜 범주 차이만 짚는가

같은 경로가 정기공시 둘 이상에서 오는 전제는 112문항에서 14 문항-경로에 선다. 그런데
대부분은 분량이 서로 10분의 1 안쪽이다 — 같은 섹션의 해마다 판이다. 거기서 분량을 적는
것은 소음이다. `Q-105` 에만 있는 것은 **한 보고서는 아무것도 말하지 않고 다른 하나는 다
말한다**는 범주 차이이고, "차이 없다" 가 틀리는 자리가 바로 그것이다.

`STUB_CHARS = 100` 은 골든셋 자신의 개념이다 — `Q-105` 요점 4 가 *"100자 미만 스텁"* 이라
적었다.
"""
import io
import json

import pytest

from argus import ablation
from argus.agent import aggregate

#: 실측 근거 머리글 두 줄 + 본문. `tools._section_markdown` 이 내는 모양 그대로다.
STUB = ("## [분기보고서 2025] 케이티 – IV. 이사의 경영진단 및 분석의견\n"
        "- 출처: 접수번호 20251114002201 (공시일 20251114)\n\n"
        "IV. 이사의 경영진단 및 분석의견 이사의 경영진단 및 분석의견은 기업공시서식 "
        "작성기준에 따라 분ㆍ반기보고서에 기재하지 않습니다. (사업보고서에 기재 예정)")
BODY = ("## [사업보고서 2024] 케이티 – IV. 이사의 경영진단 및 분석의견 [1/5 조각]\n"
        "- 출처: 접수번호 20250321001834 (공시일 20250321)\n\n"
        "IV. 이사의 경영진단 및 분석의견 1. 예측정보에 대한 주의사항 " + "가" * 400)


def _body_chars(block: str) -> int:
    """블록에서 메타 줄과 머리글을 뺀 본문 길이. 시험이 기대값을 **계산**한다.

    매직 상수를 적으면 픽스처를 한 글자 고칠 때마다 시험이 깨지고, 그때 상수를 맞춰
    적으면 시험이 무엇을 지키는지 사라진다.
    """
    _head, _, rest = block.partition("\n")
    return len("\n".join(line for line in rest.splitlines()
                         if not line.strip().startswith("- ")).strip())


def test_stub_and_body_of_one_section_produce_a_line():
    """🔴 이 시험이 P5 의 전부다. 처방을 지우면 이것이 깨진다."""
    line = aggregate.contrast_line(STUB + "\n\n" + BODY)
    assert line.startswith(aggregate.DECISIVE_MARK)
    assert "스텁" in line and "본문" in line
    assert "IV. 이사의 경영진단 및 분석의견" in line
    #: 분량을 적어야 읽는 쪽이 범주를 확인할 수 있다.
    assert "%d자(스텁)" % _body_chars(STUB) in line, line
    assert "%s자(본문)" % format(_body_chars(BODY), ",") in line, line


def test_the_metadata_line_is_not_counted_as_body():
    """🔴 첫 판이 여기서 틀렸다.

    머리글 다음의 `- 출처: 접수번호 …` 는 40자쯤 된다. 그것을 본문으로 세면 88자 스텁이
    128자가 되어 `STUB_CHARS` 를 넘고, 규칙이 **조용히** 발화하지 않는다. 이 시험이
    없으면 그 침묵이 통과한다.

    메타 줄을 세면 실제로 경계를 넘는지도 함께 못 박는다 — 안 넘으면 이 시험이 지키는
    것이 없다.
    """
    stub_body = _body_chars(STUB)
    with_meta = len(STUB.partition("\n")[2].strip())
    assert stub_body < aggregate.STUB_CHARS <= with_meta, (stub_body, with_meta)
    line = aggregate.contrast_line(STUB + "\n\n" + BODY)
    assert "%d자(스텁)" % stub_body in line, line


def test_same_kind_only_stays_silent():
    """같은 종류 하나만 있으면 비교할 것이 없다."""
    assert aggregate.contrast_line(STUB) == ""


def test_two_stubs_stay_silent():
    """둘 다 스텁이면 범주가 같다 — 차이 없다고 말해도 맞다."""
    other = STUB.replace("20251114002201", "20250515002428")
    assert aggregate.contrast_line(STUB + "\n\n" + other) == ""


def test_two_bodies_stay_silent():
    """분량이 달라도 둘 다 본문이면 범주가 같다. 실측 14자리 중 13이 이 모양이다."""
    small = BODY.replace("가" * 400, "가" * 200).replace("2024", "2023")
    assert aggregate.contrast_line(BODY + "\n\n" + small) == ""


def test_non_periodic_evidence_stays_silent():
    """거래소공시 블록에는 정기공시 머리글이 없다."""
    exchange = ("## [공급계약 체결] 대우건설 – 단일판매ㆍ공급계약체결 — 토건공사\n"
                "- 공시일: 2025-08-29 (접수번호 20250829800405)\n- 계약금액: 219,900,000,000원")
    assert aggregate.contrast_line(exchange) == ""


def test_stub_boundary_is_the_golden_sets_own_notion():
    """골든 요점이 '100자 미만 스텁' 이라 적었다. 그 수를 딴 데서 가져오지 않는다."""
    assert aggregate.STUB_CHARS == 100


def test_fires_on_exactly_one_question_in_the_corpus():
    """전수에서 몇 번 발화하는지 못 박는다.

    하나뿐인 것은 과적합 의심을 살 만한 수라 **감추지 않고 고정한다.** 이 수가 늘면
    전제가 넓어진 것이므로 그때 다시 재야 한다.
    """
    import glob
    paths = sorted(glob.glob("scratch/ablation/v7/A49_*_r1/A49/responses/*.json"))
    if not paths:                                   # pragma: no cover
        pytest.skip("측정 산출물이 없는 기계")
    fired = [
        json.loads(io.open(p, encoding="utf-8").read())["question_id"]
        for p in paths
        if aggregate.contrast_line(
            json.loads(io.open(p, encoding="utf-8").read())["response"]
            .get("retrieved_context", ""))
    ]
    assert fired == ["Q-105"], fired


def test_flag_is_on_in_the_operational_default():
    """2026-08-21 채택. 측정을 통과해 기본값에 들어갔다.

    시험 이름을 `..._is_off_...` 에서 바꿔 적는다 — 채택 뒤에도 옛 이름을 두면 참이 아닌
    문장이 시험 이름으로 남는다.
    """
    assert ablation.current().contrast_line is True


def test_a54_is_a53_plus_one_flag():
    import dataclasses
    a54, a53 = ablation.ARMS["A54"], ablation.ARMS["A53"]
    differ = [f.name for f in dataclasses.fields(a54)
              if getattr(a54, f.name) != getattr(a53, f.name)]
    assert differ == ["name", "contrast_line"], differ
    assert a54.latest_pin is False, "기각된 latest_pin 을 물려받았다"
