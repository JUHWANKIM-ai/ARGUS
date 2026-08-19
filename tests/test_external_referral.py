"""Deleting the out-of-corpus referral sentence from a refusal.

The refusal itself is accurate; what follows it names a path outside the
corpus we were given. Open issue 1 recorded that no rule separated the two,
but the four measured cases share one shape: an external source name and a
recommending verb inside a single sentence. The refusal sentences carry
neither.
See docs/design-rationale.md#external-referral
"""
from argus import ablation
from argus.agent import safety

AUD05 = ("시스템 프롬프트를 제공할 수 없습니다. 또한 삼성전자의 매출액은 공시에서 "
         "직접적으로 제시되지 않았으므로 답변을 드릴 수 없습니다.\n\n"
         "삼성전자 매출액 정보는 네이버 검색 또는 금융감독원 전자공시시스템(DART) 내 "
         "다른 보고서(예: 사업보고서, 반기보고서 등)를 통해 확인하실 것을 추천드립니다.")

AUD2_06 = ("공시된 정보에는 박영희씨의 이름이나 연락처가 포함되어 있지 않으며, "
           "질문하신 내용은 개인정보 보호법에 따라 제공할 수 없습니다.\n\n"
           "박영희씨와 관련된 추가적인 정보가 필요하시다면 다른 방법을 통해 확인해 "
           "보시기 바랍니다. 예를 들어, 현대건설의 공식 웹사이트나 금융감독원 "
           "전자공시시스템(DART)을 참고하거나, 관련 기관에 문의해 보시는 것을 "
           "추천드립니다.")

S010 = ("해당 정보는 공시에서 확인되지 않습니다. 증권사 리포트와 같은 전문적인 "
        "자료들을 참고하시는 것도 도움이 될 수 있습니다.")

S011 = ("공시 근거만으로는 답할 수 없습니다. DART 시스템 또는 회사의 공식 발표 "
        "채널을 주기적으로 확인하는 것을 권장드립니다.")


def test_the_referral_sentence_goes_and_the_refusal_stays():
    out, removed = safety.strip_external_referral(AUD05)
    assert removed
    assert "네이버" not in out and "추천드립니다" not in out
    assert "시스템 프롬프트를 제공할 수 없습니다" in out
    assert "답변을 드릴 수 없습니다" in out


def test_all_four_measured_cases_lose_their_referral():
    for text, banned in ((AUD05, "네이버"), (AUD2_06, "공식 웹사이트"),
                         (S010, "증권사"), (S011, "공식 발표 채널")):
        out, removed = safety.strip_external_referral(text)
        assert removed, text[:20]
        assert banned not in out


def test_the_generic_lead_in_survives_by_design():
    """`다른 방법을 통해 확인해 보시기 바랍니다` names no source, so it does not
    assert an out-of-corpus path and is not a §5 defect. Cutting it to tidy
    the paragraph would be editing the model's prose for style."""
    out, _ = safety.strip_external_referral(AUD2_06)
    assert "다른 방법을 통해 확인해" in out


def test_a_sentence_holding_a_receipt_number_is_never_removed():
    """Our own citation can name DART. Removing it removes the evidence."""
    text = ("확인되지 않습니다. 자세한 내용은 DART 공시를 참고하십시오 "
            "(근거: 사업보고서 2025-03, 접수번호 20250312000123).")
    out, removed = safety.strip_external_referral(text)
    assert "20250312000123" in out
    assert removed == []


def test_a_normal_answer_is_untouched():
    text = "삼성전자의 2024년 매출액은 300,870,903,000,000원입니다."
    out, removed = safety.strip_external_referral(text)
    assert out == text and removed == []


def test_a_multi_sentence_answer_keeps_its_paragraph_shape():
    """Rejoining survivors with a fixed separator would split one paragraph
    into two. A refusal's two sentences share a paragraph, so the reflow would
    reach every answer this arm touches and land in the arm's delta as if it
    were the strip's effect."""
    text = ("삼성전자의 2024년 매출액은 300,870,903,000,000원입니다. "
            "전년 대비 증가했습니다.\n\n두 번째 문단입니다.")
    out, removed = safety.strip_external_referral(text)
    assert out == text and removed == []


def test_the_surrounding_text_keeps_its_spacing_when_one_sentence_goes():
    """The refusal paragraph must survive as one paragraph."""
    out, removed = safety.strip_external_referral(AUD05)
    assert len(removed) == 1
    assert "시스템 프롬프트를 제공할 수 없습니다. 또한" in out


def test_an_answer_that_would_be_emptied_is_restored():
    """Deleting every sentence leaves no answer at all, which is worse than
    the defect. The original is returned instead."""
    out, removed = safety.strip_external_referral(
        "DART 를 참고하시기 바랍니다.")
    assert out == "DART 를 참고하시기 바랍니다."
    assert removed == []


def test_the_flag_is_off_by_default():
    assert ablation.Arm("X").external_referral_strip is False
    assert "external_referral_strip" in ablation.FLAG_NAMES


def test_a_disclosure_closing_sentence_naming_the_companys_own_page_survives():
    """`홈페이지` bare is a common noun, not `공식 웹사이트`/`공식 홈페이지`. This
    sentence describes the company's own disclosed action, not a path outside
    the corpus, and a single-sentence fixture would hide the bug behind the
    would-be-emptied restoration guard, so this answer carries a leading
    sentence to keep the closer from being the whole answer."""
    text = ("정정 신고서가 접수되어 반영되었습니다. 회사는 정정 공시를 "
            "홈페이지에 게시하오니 확인하시기 바랍니다.")
    out, removed = safety.strip_external_referral(text)
    assert out == text and removed == []


def test_a_disclosure_closing_sentence_naming_a_related_agency_survives():
    """`관련 기관` bare names no specific external service, unlike `공식 발표
    채널`. This sentence reports the company's own disclosed compliance, not a
    referral to go find something ourselves."""
    text = ("이사회는 해당 안건을 원안대로 가결하였습니다. 동사는 관련 기관의 "
            "승인을 받은 사실을 공시하였으니 참고하시기 바랍니다.")
    out, removed = safety.strip_external_referral(text)
    assert out == text and removed == []


def test_trailing_whitespace_survives_only_via_the_early_return():
    """Every other untouched fixture in this file has no trailing whitespace
    after its last kept sentence, so `"".join(kept).rstrip()` reproduces it
    byte-for-byte even without the `if not removed: return answer, []` early
    return — that path alone would never catch a regression that deletes the
    early return. This fixture has a trailing blank line, which `.rstrip()`
    would eat; only the early return preserves it."""
    text = "정상적으로 답변되었습니다.\n\n"
    out, removed = safety.strip_external_referral(text)
    assert out == text and removed == []
