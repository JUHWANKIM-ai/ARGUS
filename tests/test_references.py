import json
import subprocess
import sys

from argus.config import settings
from argus.corpus.manifest import filter_docs
from argus.corpus.references import (
    CORPUS_START,
    Reference,
    extract_references,
    parse_reference_cell,
)


def _doc(group: str, rcept_no: str) -> dict:
    return next(d for d in filter_docs(doc_group=group) if d["rcept_no"] == rcept_no)


# --- 셀 파싱 ----------------------------------------------------------------


def test_parse_single_reference():
    assert parse_reference_cell("2022-09-06 장래사업ㆍ경영 계획(공정공시)") == [
        ("20220906", "장래사업ㆍ경영 계획(공정공시)")
    ]


def test_parse_multiple_references_in_one_cell():
    """한 칸에 '날짜 서식명' 이 공백으로 이어 붙는다. 날짜 위치로 잘라야 한다."""
    cell = ("2022-07-06 조회공시 요구(풍문 또는 보도) "
            "2022-08-05 조회공시 요구(풍문 또는 보도)에 대한 답변(미확정) "
            "2023-01-27 단일판매ㆍ공급계약체결")
    assert parse_reference_cell(cell) == [
        ("20220706", "조회공시 요구(풍문 또는 보도)"),
        ("20220805", "조회공시 요구(풍문 또는 보도)에 대한 답변(미확정)"),
        ("20230127", "단일판매ㆍ공급계약체결"),
    ]


def test_parse_empty_reference_cell():
    assert parse_reference_cell("-") == []
    assert parse_reference_cell("") == []


def test_parse_inline_form_then_date():
    """주요사항보고서는 '서식명(2024.07.11)' 처럼 순서가 뒤집힌다."""
    got = parse_reference_cell("특수관계인으로부터의 주식의 취득(2024.07.11)")
    assert got == [("20240711", "특수관계인으로부터의 주식의 취득")]


# --- 문서 -> 참조 -----------------------------------------------------------


def test_extract_references_from_termination_disclosure():
    """해지 공시는 원계약을 `※관련공시` 로 **명시**한다 — 추측 매칭이 아니다."""
    refs = extract_references(_doc("exchange", "20251217800800"))
    assert len(refs) == 1
    ref = refs[0]
    assert isinstance(ref, Reference)
    assert ref.target_date == "20241015"
    assert "단일판매" in ref.form_name
    assert ref.resolved is True
    assert "20241015800258" in ref.targets


def test_reference_before_corpus_start_is_marked_not_silently_dropped():
    """2023-01-02 이전 참조는 구조적 상한이다. 조용히 버리면 상한이 안 보인다."""
    refs = extract_references(_doc("exchange", "20240424800596"))
    assert len(refs) == 1
    assert refs[0].target_date == "20220906"
    assert refs[0].resolved is False
    assert refs[0].reason == "before_corpus"
    assert refs[0].target_date < CORPUS_START


def test_unresolved_references_record_a_reason():
    for doc in filter_docs(doc_group="exchange")[:250]:
        for ref in extract_references(doc):
            assert ref.reason in {"resolved", "before_corpus", "not_found"}
            assert ref.resolved is (ref.reason == "resolved")
            assert bool(ref.targets) is ref.resolved


def test_references_never_point_at_the_source_itself():
    for doc in filter_docs(doc_group="exchange")[:250]:
        for ref in extract_references(doc):
            assert doc["rcept_no"] not in ref.targets


def test_reference_targets_stay_within_the_same_company():
    for doc in filter_docs(doc_group="exchange")[:250]:
        by_no = {d["rcept_no"]: d for d in filter_docs(doc_group=doc["doc_group"])}
        for ref in extract_references(doc):
            for target in ref.targets:
                if target in by_no:
                    assert by_no[target]["corp_name"] == doc["corp_name"]


def test_document_without_related_row_yields_nothing():
    refs = extract_references(_doc("exchange", "20230131800162"))
    assert refs == []


def test_correction_header_is_not_mistaken_for_a_reference():
    """`1. 정정관련 공시서류` 는 정정 대상 표기지 `※ 관련공시` 가 아니다.

    라벨을 '관련공시' 부분일치로 잡으면 이 행이 걸리고, 옆 칸의
    `2. 정정관련 공시서류제출일` 날짜가 서식명 없는 가짜 참조로 새어 들어온다.
    """
    refs = extract_references(_doc("exchange", "20250731800028"))
    assert refs == []


def test_extracted_references_always_carry_a_form_name():
    for doc in filter_docs(doc_group="exchange")[:400]:
        for ref in extract_references(doc):
            assert ref.form_name, f"{doc['rcept_no']} 서식명이 빈 참조"


def test_build_references_writes_only_to_out_dir(tmp_path):
    """실제 artifacts/ 를 덮지 않는다 — 과거에 테스트가 인덱스를 날린 사고가 있었다."""
    subprocess.run(
        [sys.executable, "scripts/build_references.py",
         "--limit", "40", "--out-dir", str(tmp_path)],
        cwd=settings().repo_root, check=True,
    )
    written = tmp_path / "references.jsonl"
    assert written.exists()
    for line in written.read_text(encoding="utf-8").splitlines():
        if line.strip():
            record = json.loads(line)
            for key in ("source_rcept_no", "target_date", "resolved",
                        "reason", "targets", "form_matched"):
                assert key in record
