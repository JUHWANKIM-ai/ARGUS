"""W-A · `stale_note` — 대체본 블록에 폐기 표시를 각인한다 (팔 `A63`).

계획서 `docs/superpowers/plans/2026-08-21-raw-preprocessing.md` 의 과제 1.

## 무엇이 문제였나

`related` 와 `is_latest` 는 **이미 레코드에 있고 양방향이다.**

    20240722800037  amount 581,790,000,000  is_latest=False  related=["20250923800193"]
    20250923800193  amount 583,874,002,800  is_latest=True   related=["20240722800037"]

그런데 `corpus.markdown.to_markdown` 이 둘 중 어느 것도 **한 줄도 안 쓴다.** 그래서 대체본
블록에 폐기 신호가 문면에 하나도 없고, `Q-116` 은 서버 3회 모두 폐기된
581,790,000,000 을 말하며 최신값을 한 번도 안 쓴다.

런타임에는 줄이 있다 — `pipeline._chain_note` 가 `- 정정체인: n/N (원공시 · 대체됨)` 을
박는다. **그런데 체인을 걸었을 때만 붙고**, `CHAIN_SEED_WINDOW = 2` 라 근거 5번째인
`Q-116` 의 대체본은 씨앗이 안 된다.

창을 넓히는 판(`latest_pin`)은 **퇴행 2건으로 기각**됐다 — 상한 있는 창에 더하는 것은
밀어내는 것이었다. 이 팔은 창을 안 건드리고 근거 슬롯을 하나도 더 안 쓴다.

## 🔴 두 가지가 하중을 받는다

**어구.** `critic._STALE_NOTE`(`정정체인:.*대체됨`)가 이 낱말로 블록의 체인 위치를 읽어
`stale_value` 검사를 돈다. 바꾸면 그 검사의 전제가 사라진다.

**최신본 접수번호를 안 적는다.** 근거에 안 실린 공시의 14자리를 블록에 쓰면
`error_analysis._blocks` 가 그것을 **적재된 골든**으로 읽어 우리 재현율만 오른다.
`pipeline._roster_line` 이 `loaded` 로 제한한 것과 같은 근거다. 금액은 14자리가 아니라
그 회계에 안 들어간다.
"""
import pytest

from argus import ablation
from argus.agent import tools

STALE = "exchange_20240722800037"
LATEST = "exchange_20250923800193"


def test_stale_line_names_the_corrected_amount_but_no_receipt():
    record = {"rcept_no": "20240722800037", "is_latest": False,
              "amount": 581_790_000_000, "amount_type": "계약금액"}
    latest = {"rcept_no": "20250923800193", "is_latest": True,
              "amount": 583_874_002_800, "amount_type": "계약금액"}
    line = tools._stale_line(record, latest)
    assert line == ("- 정정체인: 이 공시는 정정되어 대체됨 "
                    "(최신 계약금액 583,874,002,800원)")
    assert "20250923800193" not in line, "🔴 자기 성적 부풀리기 금지"


def test_the_wording_matches_the_critic_contract():
    """`critic._STALE_NOTE` 가 이 어구로 체인 위치를 읽는다."""
    from argus.agent.critic import _STALE_NOTE

    record = {"rcept_no": "A", "is_latest": False, "amount": 100}
    assert _STALE_NOTE.search(tools._stale_line(record, None))


def test_stale_line_is_none_for_a_latest_record():
    record = {"rcept_no": "20250923800193", "is_latest": True, "amount": 1}
    assert tools._stale_line(record, None) is None


def test_stale_line_without_an_amount_delta_still_marks_supersession():
    record = {"rcept_no": "A", "is_latest": False, "amount": 100}
    latest = {"rcept_no": "B", "is_latest": True, "amount": 100}
    assert tools._stale_line(record, latest) == "- 정정체인: 이 공시는 정정되어 대체됨"


def test_arm_off_stamps_nothing():
    """🔴 계획서의 이 시험은 `_group_records_raw` 와 동일 객체를 기대했다. 틀렸다.

    `A56` 은 `correction_fields` 를 켜 두므로 `_group_records` 는 **fields 매핑**을
    돌려준다. 각인이 앞의 분기를 대체하면 이 팔이 그 분기를 조용히 잃는데, 그것이
    스위치와 무관한 근거 변화를 델타에 섞는다. 그래서 확인할 것은 「원본과 같은 객체」가
    아니라 「각인이 없다」와 「fields 분기가 살아 있다」다.
    """
    with ablation.use("A56"):
        tools._group_records_stale.cache_clear()
        mapping = tools._group_records("exchange")
    assert mapping is tools._group_records_fields("exchange")
    assert not any("정정되어 대체됨" in (r.get("markdown") or "")
                   for r in mapping.values())


def test_arm_on_keeps_the_correction_fields_branch():
    """각인은 `correction_fields` 를 **겹치고** 대체하지 않는다.

    `correction_fields` 는 exchange 129개 레코드에 `- 주요내용:` 을 더한다. 각인 팔에서
    그것이 사라지면 이 팔의 델타에 남의 것이 섞인다.
    """
    fields = tools._group_records_fields("exchange")
    with_notes = [k for k, r in fields.items()
                  if "- 주요내용:" in (r.get("markdown") or "")]
    assert with_notes, "픽스처 전제 — fields 분기가 실제로 줄을 더한다"
    with ablation.use("A63"):
        tools._group_records_stale.cache_clear()
        merged = tools._group_records("exchange")
    for key in with_notes:
        assert "- 주요내용:" in merged[key]["markdown"], f"{key} 에서 fields 줄이 사라졌다"


def test_arm_on_stamps_the_superseded_record_only():
    with ablation.use("A63"):
        tools._group_records_stale.cache_clear()
        merged = tools._group_records_stale("exchange")
        assert "정정되어 대체됨" in merged[STALE]["markdown"]
        assert "583,874,002,800" in merged[STALE]["markdown"]
        assert "정정되어 대체됨" not in merged[LATEST]["markdown"]


def test_stamped_line_sits_directly_under_the_header():
    with ablation.use("A63"):
        tools._group_records_stale.cache_clear()
        lines = tools._group_records_stale("exchange")[STALE]["markdown"].split("\n")
        assert lines[0].startswith("## ")
        assert lines[1].startswith("- 정정체인:")


def test_the_raw_mapping_is_never_mutated():
    """같은 프로세스에서 다른 팔을 돌리면 그 팔의 근거가 조용히 오염된다.

    `_group_records_nary` 와 같은 규율이다.
    """
    before = tools._group_records_raw("exchange")[STALE]["markdown"]
    with ablation.use("A63"):
        tools._group_records_stale.cache_clear()
        tools._group_records_stale("exchange")
    assert tools._group_records_raw("exchange")[STALE]["markdown"] == before
    assert "정정되어 대체됨" not in before


def test_no_receipt_number_is_added_anywhere_in_the_corpus():
    """🔴 전수 단정 — 각인 줄이 14자리를 하나도 안 늘린다.

    이것이 「자기 성적을 부풀리지 않는다」의 기계적 확인이다. 문항 하나로는 못 지킨다.
    """
    import re

    rcept = re.compile(r"\d{14}")
    with ablation.use("A63"):
        tools._group_records_stale.cache_clear()
        for group in tools.RECORD_GROUPS:
            raw = tools._group_records_raw(group)
            merged = tools._group_records_stale(group)
            for doc_id, record in merged.items():
                before = len(rcept.findall(raw[doc_id].get("markdown") or ""))
                after = len(rcept.findall(record.get("markdown") or ""))
                assert after == before, f"{doc_id} 에 14자리가 늘었다"


def test_the_flag_needs_no_parent():
    """부모가 없다 — `_group_records` 분기가 플래그를 직접 읽는다."""
    from dataclasses import replace

    arm = replace(ablation.ARMS["A0"], name="TEST", stale_note=True)
    assert arm.stale_note is True


# --- `_annotate` 가 줄을 두 번 넣지 않는다 -------------------------------------


def test_annotate_replaces_a_line_with_the_same_prefix():
    """각인과 런타임 체인 주석이 같은 블록에서 만나면 두 줄이 된다.

    둘이 같은 것을 다르게 말하면 모델이 어느 쪽을 믿을지가 미정이 된다. 런타임 줄이
    체인 위치(`n/N`)까지 아는 쪽이라 그것이 남는다.
    """
    from argus.agent.pipeline import _annotate

    stamped = ("## [공급계약 체결] 두산에너빌리티 – 계약\n"
               "- 정정체인: 이 공시는 정정되어 대체됨 (최신 계약금액 1,000원)\n"
               "- 계약금액: 900원")
    runtime = "- 정정체인: 1/2 (원공시 · 대체됨)"
    out = _annotate(stamped, runtime)
    assert out.count("- 정정체인:") == 1
    assert runtime in out
    assert "최신 계약금액" not in out
    assert "- 계약금액: 900원" in out, "본문은 안 건드린다"


def test_annotate_still_inserts_when_no_such_line_exists():
    from argus.agent.pipeline import _annotate

    block = "## [공급계약 체결] 두산에너빌리티 – 계약\n- 계약금액: 900원"
    out = _annotate(block, "- 발주처 관계: …")
    assert out.split("\n")[1] == "- 발주처 관계: …"
    assert out.split("\n")[2] == "- 계약금액: 900원"
