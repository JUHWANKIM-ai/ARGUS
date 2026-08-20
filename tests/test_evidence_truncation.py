"""v4 `R1-4`, `A17`(근거 절단 규칙)을 **기각한 근거**를 들고 있다. LLM 0회 · 0원.

계획서는 `Q-087`(정답 `올림푸스제일차`)을 *"정정본 블록이 절단돼 `주요내용` 필드 부재"*
로 진단하고 `pipeline.MAX_EVIDENCE`/`MAX_SECTION_CHARS` 를 겨눴다. **자리가 틀렸다.**
정답은 **잘린 것이 아니라 애초에 실리지 않았다**. 막는 것이 두 겹이고 둘 다 절단
상한이 아니다.

  ① `markdown._free_text_lines` 게이트, `schema_matched=True` 면 서술 본문을 안 싣는다
  ② `normalize.FREETEXT_MAX_CHARS`(300), 게이트를 열어도 440번째 자리의 정답이 잘린다

둘을 다 고치면 3,150건 중 **1,588건(50.4%)** 의 마크다운이 바뀐다 = BM25 색인 문자열
전면 교체 = v1·v2·v3 측정 전량 무효. 겨냥은 **1문항**이다. 그래서 취소했고, 여기서
그 수를 붙든다. 되살리려는 다음 세대가 비용을 먼저 보게 하려는 것이다.

`R1-11` 의 교훈을 그대로 따랐다: **계획서의 진단을 믿지 말고 코드를 먼저 읽는다.**

---
**v5 가 이 자리를 다른 층에서 닫았다 (2026-08-16 · `A30`).** 위 판정은 그대로 참이다.
**코퍼스 마크다운은 여전히 정답을 안 싣는다.** `A30` 이 바꾸는 것은 검색 **뒤에** 실리는
런타임 사본뿐이고(`tools._group_records_fields`), `artifacts/*_records.jsonl` 도 BM25
색인도 한 바이트가 안 바뀐다. 그래서 이 파일은 **코퍼스 층**(`_group_records_raw`)을 보고,
런타임 층의 회복은 `tests/test_correction_fields.py` 가 따로 붙든다.

두 층을 섞어 보면 둘 중 하나가 조용히 거짓이 된다. 그것이 이 파일이 원본 사전을
직접 읽는 이유다.
"""
import json
import pathlib

import pytest

from argus.agent import pipeline, tools
from argus.corpus import markdown as md
from argus.corpus import normalize as nz

_ROOT = pathlib.Path(__file__).resolve().parents[1]

#: 겨냥 문항의 두 레코드. 원공시는 금액이 없어 `schema_matched=False`, 정정본은
#: 서술문에서 매매금액이 잡혀 `True` 다. **그 비대칭이 이 실패의 전부다.**
Q087_ORIGIN = "20240604800108"
Q087_CORRECTION = "20241114800538"
Q087_GOLD = "올림푸스제일차"

#: 저장 응답 (v3 세대 · `A8`). 없으면 그 테스트만 건너뛴다.
Q087_RESPONSE = _ROOT / "scratch/ablation/v3/A8/responses/Q-087.json"


def _record(rcept_no):
    """**코퍼스 층의 레코드.** `tools._record_of` 를 쓰면 안 된다. 그것은 arm 에
    따라 런타임 사본을 돌려주고(`A30`), 그러면 이 파일이 붙드는 *"코퍼스 마크다운은
    정답을 안 싣는다"* 가 arm 에 따라 참·거짓을 오간다."""
    for group in tools.RECORD_GROUPS:
        record = tools._group_records_raw(group).get(f"{group}_{rcept_no}")
        if record is not None:
            return record
    pytest.skip(f"{rcept_no} 레코드가 코퍼스에 없다")


def test_the_answer_is_in_the_record_but_never_in_its_markdown():
    """**판정의 첫 줄.** 정답은 원문 표에 있고 근거 문자열에는 한 번도 없다."""
    record = _record(Q087_CORRECTION)
    assert record["schema_matched"] is True, "R0-1 이 확인한 전제가 바뀌었다"
    blob = json.dumps(record.get("raw_rows") or [], ensure_ascii=False)
    assert Q087_GOLD in blob, "정답이 원문 표에 없다 — 진단의 전제가 무너진다"
    assert Q087_GOLD not in (record.get("markdown") or ""), \
        "정답이 마크다운에 실렸다 — 그러면 실패의 자리가 여기가 아니다"


def test_neither_evidence_cap_is_in_the_causal_path():
    """**`A17` 의 겨냥이 틀렸다는 증거.** 절단 상한 둘 다 근처에도 안 갔다.

    `MAX_SECTION_CHARS` 는 정기공시 **섹션** 근거 전용이고(`_join_section_evidence`)
    이 문항은 exchange 레코드만 쓴다. `MAX_EVIDENCE` 는 10인데 블록이 2개다.
    """
    if not Q087_RESPONSE.exists():
        pytest.skip("v3/A8 Q-087 산출물이 없다")
    response = json.loads(Q087_RESPONSE.read_text(encoding="utf-8"))["response"]
    evidence = response["retrieved_context"]
    assert evidence.count("## ") == 2 < pipeline.MAX_EVIDENCE
    assert len(evidence) < pipeline.MAX_SECTION_CHARS
    # 정정본은 근거에 **들어와 있다**. 검색이 못 가져온 것이 아니다.
    assert Q087_CORRECTION in evidence and Q087_ORIGIN in evidence
    assert Q087_GOLD not in evidence


def test_the_gate_is_the_first_of_two_barriers():
    """① 게이트. 원공시는 열리고 정정본은 닫힌다. 금액을 찾은 쪽이 닫힌다."""
    origin, correction = _record(Q087_ORIGIN), _record(Q087_CORRECTION)
    assert origin["schema_matched"] is False and correction["schema_matched"] is True
    # 원공시는 서술 본문이 실렸다. 정정본은 같은 라벨이 통째로 없다.
    assert "- 주요내용:" in (origin.get("markdown") or "")
    assert "- 주요내용:" not in (correction.get("markdown") or "")
    # 게이트를 열면 실을 것이 실제로 있다 (빈 것이 아니라 막힌 것이다).
    assert list(nz.free_text_items(correction.get("raw_rows") or []))
    assert md._free_text_lines(correction, []) == [], "게이트가 열려 있다"


def test_opening_the_gate_alone_does_not_recover_the_answer():
    """② 상한. 게이트만 열어도 정답은 안 들어온다. 두 겹이라는 것이 판정의 핵심이다.

    한쪽만 고치면 "고쳤는데 Δ 가 0" 이 되고, 그 0 을 *"이 진단이 틀렸다"* 로 읽게 된다.
    """
    record = _record(Q087_CORRECTION)
    rows = record.get("raw_rows") or []
    assert nz.FREETEXT_MAX_CHARS == 300, "상한이 바뀌었다 — 아래 수를 다시 재라"
    assert not any(Q087_GOLD in str(value) for _, value in nz.free_text_items(rows))

    original = nz.FREETEXT_MAX_CHARS
    try:
        nz.FREETEXT_MAX_CHARS = 400
        assert not any(Q087_GOLD in str(v) for _, v in nz.free_text_items(rows)), \
            "400 이면 들어온다 — 필요한 상한을 다시 재라"
        nz.FREETEXT_MAX_CHARS = 500
        assert any(Q087_GOLD in str(v) for _, v in nz.free_text_items(rows)), \
            "500 으로도 안 들어온다 — 원문 표가 바뀌었다"
    finally:
        nz.FREETEXT_MAX_CHARS = original


def test_the_blast_radius_is_half_the_corpus():
    """**이것이 기각의 판정 근거다.** 겨냥 1문항에 마크다운 절반이 다시 쓰인다.

    수를 리터럴로 박지 않고 **비율의 부등식**으로 쓴다. 코퍼스가 다시 빌드되면 절대
    수는 흔들리지만 *"절반쯤 바뀐다"* 는 결론은 그것으로 안 흔들린다.
    """
    total = matched = gate_gain = clip_gain = 0
    original = nz.FREETEXT_MAX_CHARS
    try:
        for group in tools.RECORD_GROUPS:
            for record in tools._group_records_raw(group).values():
                total += 1
                rows = record.get("raw_rows") or []
                head = record.get("markdown") or ""
                if record.get("schema_matched"):
                    matched += 1
                    nz.FREETEXT_MAX_CHARS = original
                    if any(value not in head for _, value in nz.free_text_items(rows)):
                        gate_gain += 1        # 게이트를 열면 이 레코드의 근거가 바뀐다
                    continue
                nz.FREETEXT_MAX_CHARS = original
                before = list(nz.free_text_items(rows))
                nz.FREETEXT_MAX_CHARS = 500
                if list(nz.free_text_items(rows)) != before:
                    clip_gain += 1            # 상한을 올리면 이 레코드의 근거가 바뀐다
    finally:
        nz.FREETEXT_MAX_CHARS = original

    if not total:
        pytest.skip("코퍼스 레코드가 없다")
    changed = gate_gain + clip_gain
    assert matched > total * 0.8, f"schema_matched 비중이 바뀌었다: {matched}/{total}"
    assert gate_gain > matched * 0.3, \
        f"게이트를 열 때 바뀌는 레코드가 줄었다: {gate_gain}/{matched}"
    assert changed > total * 0.3, \
        f"🔴 A17 의 대가가 줄었다면 기각을 다시 재라: {changed}/{total}"


def test_v5_closed_it_at_the_runtime_layer_without_touching_the_corpus():
    """**두 층이 함께 참이어야 한다.**

    코퍼스 마크다운은 여전히 정답을 안 싣고(위 판정 유지), 런타임 사본은 싣는다.
    이 둘이 같아지는 순간 `A30` 은 `A17` 이 되고 BM25 색인이 갈린다.
    """
    from argus import ablation

    raw = _record(Q087_CORRECTION)["markdown"]
    assert Q087_GOLD not in raw, "코퍼스가 바뀌었다 — A30 이 A17 이 됐다"
    with ablation.use("A30"):
        assert Q087_GOLD in tools._record_of(Q087_CORRECTION)["markdown"]
    # 사본을 만든 뒤에도 원본 사전은 그대로다.
    assert _record(Q087_CORRECTION)["markdown"] == raw
