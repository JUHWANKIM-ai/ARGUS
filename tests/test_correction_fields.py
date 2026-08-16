"""`A30` — 정정사유·주요내용을 **런타임 근거**에 덧붙인다.

🔴 R0 실측(2026-08-16) — 답이 레코드에 있는데 마크다운이 버린다:

    20241114800538 (이마트 정정본)   "올림푸스제일차"           레코드 O · 마크다운 X (156자)
    20260306801011 (크래프톤 정정본)  "출자일자 확정에 따른 정정"  레코드 O · 마크다운 X (150자)

원인은 거꾸로다 — `schema_matched=True` 면 넉 줄 요약을 내고 `raw_rows` 19행을 버리고,
매칭이 **안 되면** 원문을 실어 752자가 나온다. **정형 매칭이 정보를 지운다.**

🔴 **`A17` 과 가르는 선이 이 파일의 존재 이유다.** `A17` 은 `corpus/markdown` 의 게이트를
열어 코퍼스 3,150건 중 1,588건(50.4%)을 다시 쓰는 조작이었고 그래서 기각됐다. 이 arm 은
`A18`(`nary`)이 연 런타임 이음매에만 얹는다 — 색인 파일은 손대지 않는다.
"""
from __future__ import annotations

import inspect
import json

from argus import ablation
from argus.agent import tools


def test_the_answer_is_in_the_record_but_not_in_the_markdown():
    """전제. 이게 깨지면 이 arm 의 설계가 통째로 틀린 것이다."""
    rec = tools._group_records_raw("exchange")["exchange_20241114800538"]
    assert "올림푸스" in json.dumps(rec, ensure_ascii=False)
    assert "올림푸스" not in rec["markdown"]
    assert len(rec["markdown"]) < 200, "요약이 넉 줄이라는 전제"


def test_the_flag_is_opt_in():
    """🔴 v5 에서 채택돼 운영 기본값(`A34`)에 들어갔다. 그래도 `A28`(v4 기본값)로
    되돌리면 근거가 **한 바이트도** 안 바뀌어야 한다 — 그것이 성립해야 v1~v4 산출물의
    설명이 안 낡는다."""
    assert ablation.A28.correction_fields is False
    assert ablation.current().correction_fields is True, "A34 가 기본값이어야 한다"
    with ablation.use("A28"):
        live = tools._group_records("exchange")["exchange_20241114800538"]["markdown"]
    raw = tools._group_records_raw("exchange")["exchange_20241114800538"]["markdown"]
    assert live == raw


def test_the_buyer_appears_when_armed():
    with ablation.use("A30"):
        md = tools._group_records("exchange")["exchange_20241114800538"]["markdown"]
        assert "올림푸스" in md


def test_the_correction_reason_appears_when_armed():
    with ablation.use("A30"):
        md = tools._group_records("exchange")["exchange_20260306801011"]["markdown"]
        assert "출자일자 확정에 따른 정정" in md
        assert "49.49" in md, "출자비율도 `2. 주요내용` 안에 있다"


def test_the_two_caches_are_separate():
    """🔴 사전을 하나만 캐시하면 먼저 부른 arm 의 것이 재생돼 Δ 가 조용히 0 이 된다
    (`_group_records_nary` 가 같은 이유로 따로 캐시한다)."""
    plain = tools._group_records_raw("exchange")["exchange_20260306801011"]["markdown"]
    with ablation.use("A30"):
        tools._group_records("exchange")
    assert tools._group_records_raw(
        "exchange")["exchange_20260306801011"]["markdown"] == plain


def test_the_source_file_is_never_written():
    """🔴 `artifacts/*_records.jsonl` 을 고치면 BM25 색인이 갈려 v1~v4 가 무효가 된다."""
    src = inspect.getsource(tools._group_records_fields)
    src += inspect.getsource(tools._correction_lines)
    assert "open(" not in src and "write" not in src


def test_only_two_labels_are_added():
    """🔴 `raw_rows` 전량 덤프는 `A17` 이 기각된 자리다. 라벨은 둘뿐이어야 한다."""
    assert tools.CORRECTION_FIELD_LABELS == ("3. 정정사유", "2. 주요내용")


def test_the_value_is_long_enough_to_reach_the_answer():
    """🔴 `Q-087` 의 정답은 `2. 주요내용`(623자)의 **408·440번째 자리**에 있다
    (v4 `R1-4` 실측). 상한이 300 이면 안 들어온다."""
    assert tools.CORRECTION_FIELD_CHARS >= 500
    with ablation.use("A30"):
        md = tools._group_records("exchange")["exchange_20241114800538"]["markdown"]
    assert md.index("올림푸스") > 400, "잘리면 이 단언이 먼저 깨진다"


def test_nary_and_correction_fields_cannot_be_armed_together():
    """🔴 `_group_records` 가 하나만 고르므로 함께 켜면 뒤의 것이 조용히 무시된다."""
    import pytest

    from argus.ablation import A28
    from dataclasses import replace

    with pytest.raises(ValueError, match="함께 켤 수 없습니다"):
        replace(A28, name="bad", nary=True, correction_fields=True)


def test_every_path_sees_the_same_record():
    """🔴 **이 함정에 실제로 빠졌다** (2026-08-16). `correction_fields` 를
    `_group_records` 에만 걸었더니 `Q-087` 의 근거가 한 글자도 안 바뀌었다 — 그
    문항의 정정본은 검색이 아니라 **체인 hop**(`get_history` → `_chain_entry`)으로
    오고, 그 경로는 `_by_rcept()` 를 지난다. 경로마다 근거가 다르면 arm 의 Δ 가
    무엇에 귀속되는지 알 수 없다.
    """
    with ablation.use("A30"):
        by_group = tools._group_records("exchange")["exchange_20241114800538"]
        by_rcept = tools._record_of("20241114800538")
        assert "올림푸스" in by_group["markdown"]
        assert "올림푸스" in by_rcept["markdown"], "체인 hop 경로가 안 갈렸다"
        assert by_group["markdown"] == by_rcept["markdown"]


def test_the_blast_radius_is_recorded():
    """🔴 인질을 세어 두고 켠다. 이 수가 움직이면 코퍼스가 바뀐 것이다."""
    base = tools._group_records_raw("exchange")
    with ablation.use("A30"):
        armed = tools._group_records("exchange")
    grown = [k for k in base
             if len(armed[k]["markdown"]) > len(base[k]["markdown"])]
    # 🔴 서식 제한 전 707 → 후 **129**. 300건인 서식 중 실제로 덧붙일 값이 있는 것만
    #    늘어난다 (나머지는 라벨이 없거나 값이 이미 마크다운에 있다).
    assert 110 <= len(grown) <= 150, f"번짐이 {len(grown)}건으로 움직였다"


def test_only_the_one_form_is_touched():
    """🔴 서식을 안 가린 첫 판이 `Q-110`(지금 정답)을 잃었다 — 밸류체인 7→6.
    겨냥 둘은 모두 `투자판단관련주요경영사항` 이고 `Q-110` 은 공급계약이다."""
    base = tools._group_records_raw("exchange")
    with ablation.use("A30"):
        armed = tools._group_records("exchange")
    for key, record in base.items():
        if len(armed[key]["markdown"]) > len(record["markdown"]):
            assert record.get("form") in tools.CORRECTION_FIELD_FORMS, key


def test_a_supply_contract_is_left_alone():
    """`Q-110` 의 근거가 실제로 안 바뀌는지 본다."""
    with ablation.use("A30"):
        armed = tools._record_of("20231211800611")
    assert armed["markdown"] == tools._group_records_raw(
        "exchange")["exchange_20231211800611"]["markdown"]
