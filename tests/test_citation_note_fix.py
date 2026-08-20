"""검증 꼬리가 자기를 반박하지 않게 한다 (`citation_note_fix`, 2026-08-21).

## 계획서에 없던 결함이다

P3(`cite_used`)을 조사하다 나왔다. `Q-105` 의 답변에 14자리 접수번호가 셋 있는데 꼬리는
*"(주의: … 근거 표기가 없습니다)"* 였다.

원인은 순서다. `_verify` 가 꼬리를 만들고 **그 뒤에** `attach_citation` 이 표기를 붙인다.
그래서 표기가 붙은 답변이 "표기가 없다" 는 경고를 달고 나간다.

실측 — 로컬 336개 중 **28건**, 서버 112개 중 6건.
`Q-012` `Q-018` `Q-034` `Q-065` `Q-105` `Q-107` `Q-108` `Q-109` `Q-112` `Q-113`
`Q-114` `Q-118`.

## 왜 순서를 안 바꾸는가

`attach_citation` 은 모든 LLM 호출 **뒤에** 돌아야 생성·비평·재생성 프롬프트가 글자 그대로
유지되고 요청 캐시가 맞는다 (그 함수의 독스트링). 앞으로 옮기면 재측정이 캐시를 통째로
놓치고 크레딧을 다시 쓴다. 그래서 사유를 지우는 쪽으로 고친다.
"""
import glob
import io
import json
import re

import pytest

from argus import ablation
from argus.agent import pipeline

BODY = "답이다. (근거: 단일판매ㆍ공급계약체결, 2025-01-01, 접수번호 20250101000001)"
CITATION_REASON = "근거 표기가 없습니다"
OTHER_REASON = "답변의 일부 수치·사실이 근거에서 확인되지 않았습니다"


def _note(*reasons: str) -> str:
    return "\n\n(주의: 근거 검증을 통과하지 못했습니다 — %s)" % " · ".join(reasons)


def test_the_note_goes_when_the_citation_reason_was_the_only_one():
    """🔴 이 시험이 처방의 전부다. 지우면 깨진다."""
    out, changed = pipeline.drop_citation_warning(BODY + _note(CITATION_REASON))
    assert changed
    assert out == BODY
    assert "주의" not in out


def test_only_that_reason_is_removed_when_others_remain():
    """남은 사유는 여전히 참이다. 꼬리를 통째로 지우면 관측을 잃는다."""
    out, changed = pipeline.drop_citation_warning(
        BODY + _note(CITATION_REASON, OTHER_REASON))
    assert changed
    assert CITATION_REASON not in out
    assert OTHER_REASON in out
    assert out.startswith(BODY)
    #: 구분자가 남아 매달리지 않는다.
    assert "— · " not in out and " · )" not in out


def test_a_note_without_the_citation_reason_is_untouched():
    answer = BODY + _note(OTHER_REASON)
    assert pipeline.drop_citation_warning(answer) == (answer, False)


def test_an_answer_without_a_note_is_untouched():
    assert pipeline.drop_citation_warning(BODY) == (BODY, False)


def test_the_reason_text_is_read_from_one_place():
    """문면을 두 곳에 적으면 한쪽만 고쳐진다. 이 저장소가 다섯 번 겪은 실패 양식이다."""
    assert pipeline._CITATION_REASON == CITATION_REASON
    assert any(text == CITATION_REASON
               for _f, _b, text in pipeline._VERDICT_REASONS)


def test_flag_is_off_in_the_operational_default():
    assert ablation.current().citation_note_fix is False


def test_the_fix_cannot_change_any_measurement():
    """🔴 이 처방은 채점·판정에 **원리적으로** 영향이 없다. 그것을 전수로 증명한다.

    채점기와 판정기가 둘 다 `strip_verify_tail` 을 거친 답변을 받는다
    (`error_analysis.run_judge` 는 *"모델이 쓴 것만 채점 대상이다"* 라고 적어 두었다).
    꼬리 안의 문면을 고치는 것은 그 함수를 통과하면 사라지므로, 점수가 움직일 길이 없다.

    실측(2026-08-21): 저장 답변 726개 중 73건에서 거짓 경고가 제거되고, 채점 입력은
    **726/726 글자 그대로 같다.** 이 시험이 그 성질을 지킨다 — 나중에 꼬리 밖을 건드리는
    변경이 섞여 들어오면 여기서 깨진다.
    """
    from scripts import error_analysis as ea

    paths = (sorted(glob.glob("scratch/ablation/v7/A49_*_r*/A49/responses/*.json"))
             + sorted(glob.glob("scratch/ablation/v6/server_*/A49/responses/*.json")))
    if not paths:                                        # pragma: no cover
        pytest.skip("측정 산출물이 없는 기계")
    changed = 0
    for path in paths:
        doc = json.loads(io.open(path, encoding="utf-8").read())
        before = doc["response"]["answer"]
        after, did = pipeline.drop_citation_warning(before)
        changed += did
        assert (ea.strip_verify_tail(before).strip()
                == ea.strip_verify_tail(after).strip()), doc["question_id"]
    assert changed > 0, "고친 것이 하나도 없으면 이 시험이 지키는 것이 없다"


def test_the_defect_is_real_in_the_stored_answers():
    """실측이 이 시험의 근거다. 산출물에서 그 모양이 실제로 몇 건인지 센다.

    🔴 지어낸 입력만으로 통과하는 시험은 "우리 정규식이 우리 예시를 먹는다" 만 증명한다.
    이 시험은 결함이 **저장된 답변에 실재함**을 확인하고, 처방이 그것들을 다 잡는지도 본다.
    """
    paths = sorted(glob.glob("scratch/ablation/v7/A49_*_r1/A49/responses/*.json"))
    if not paths:                                        # pragma: no cover
        pytest.skip("측정 산출물이 없는 기계")
    hit, fixed = [], 0
    for path in paths:
        doc = json.loads(io.open(path, encoding="utf-8").read())
        answer = doc["response"]["answer"]
        if CITATION_REASON not in answer or not re.search(r"\d{14}", answer):
            continue
        hit.append(doc["question_id"])
        cleaned, changed = pipeline.drop_citation_warning(answer)
        fixed += changed
        assert CITATION_REASON not in cleaned
    assert hit, "저장된 답변에 이 모양이 하나도 없다 — 결함이 사라졌거나 검사가 틀렸다"
    assert fixed == len(hit), (fixed, len(hit))
