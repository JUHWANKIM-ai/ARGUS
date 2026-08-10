"""periodic 섹션 청킹 — 04-retriever D-2.

기준점은 CONVENTION §7.3 슬라이드에 인쇄된 삼성전자 2025 사업보고서
(periodic_20260310002820) 의 'II. 사업의 내용 > 2. 주요 제품 및 서비스' 청크다.
슬라이드가 `path` · `text` 앞부분 · 표 마크다운을 그대로 보여 주므로,
우리 파서 출력이 거기에 맞는지가 유일한 외부 정답이다.
"""
import json
import subprocess
import sys

import pytest

from argus.config import settings
from argus.corpus import sections
from argus.corpus.manifest import filter_docs

SLIDE_DOC = "periodic_20260310002820"
SLIDE_PATH = "II. 사업의 내용 > 2. 주요 제품 및 서비스"


@pytest.fixture(scope="module")
def slide_doc():
    docs = [d for d in filter_docs(doc_group="periodic") if d["doc_id"] == SLIDE_DOC]
    if not docs:
        pytest.skip("코퍼스에 슬라이드 예시 문서가 없습니다")
    return docs[0]


@pytest.fixture(scope="module")
def slide_records(slide_doc):
    return list(sections.iter_doc_sections(slide_doc))


# --- 텍스트화 ---------------------------------------------------------------
def test_plain_text_rejoins_split_spans():
    """색·굵기가 바뀔 때마다 SPAN 이 갈린다. 태그를 공백으로 바꾸면 조사가 떨어진다."""
    frag = "<P><SPAN>스마트폰</SPAN>\n<SPAN>은</SPAN>\n<SPAN> 상승했다.</SPAN></P>"
    assert sections.plain_text(frag) == "스마트폰은 상승했다."


def test_plain_text_breaks_line_before_bold_subheading():
    """USERMARK 의 B 는 소제목이다. 안 끊으면 '주요 제품 매출당사는' 으로 엉긴다."""
    frag = '<P><SPAN USERMARK="F-14 B">가. 주요 제품 매출</SPAN>당사는 TV를 만든다.</P>'
    assert sections.plain_text(frag) == "가. 주요 제품 매출\n당사는 TV를 만든다."


def test_colspan_keeps_numbers_in_their_column():
    """병합 셀을 안 펼치면 매출액이 '주요 제품' 열로 밀려 재무 수치가 어긋난다."""
    table = (
        '<TABLE BORDER="1"><TBODY>'
        "<TR><TD>부 문</TD><TD>주요 제품</TD><TD>매출액</TD></TR>"
        '<TR><TD COLSPAN="2">총 계</TD><TD>3,336,059</TD></TR>'
        "</TBODY></TABLE>"
    )
    assert sections.render(table).splitlines()[-1] == "| 총 계 |  | 3,336,059 |"


def test_layout_table_becomes_plain_text():
    """BORDER=0 상자는 단위 표기를 얹는 용도다. 표로 만들면 '(단위 : 억원)' 이 뭉갠다."""
    frag = '<TABLE BORDER="0"><TBODY><TR><TD>(단위 : 억원, %)</TD></TR></TBODY></TABLE>'
    assert sections.render(frag) == "(단위 : 억원, %)"


def test_pipe_in_cell_is_escaped():
    table = ('<TABLE BORDER="1"><TBODY><TR><TD>a|b</TD><TD>c</TD></TR>'
             "<TR><TD>1</TD><TD>2</TD></TR></TBODY></TABLE>")
    assert r"a\|b" in sections.render(table)


# --- 목차 파싱 ---------------------------------------------------------------
def test_parse_toc_uses_section_nesting_for_level():
    xml = (
        '<SECTION-1><TITLE ATOC="Y">II. 사업의 내용</TITLE>'
        '<SECTION-2><TITLE ATOC="Y">1. 사업의 개요</TITLE><P>본문</P></SECTION-2>'
        "</SECTION-1>"
    )
    nodes = sections.parse_toc(xml)
    assert [(n["level"], n["path"]) for n in nodes] == [
        (1, "II. 사업의 내용"),
        (2, "II. 사업의 내용 > 1. 사업의 개요"),
    ]
    assert nodes[0]["has_children"] and not nodes[1]["has_children"]


def test_parse_toc_skips_atoc_n():
    """'목 차' 표제는 ATOC=N 이다. 이걸 섹션으로 잡으면 목차 전체가 청크가 된다."""
    xml = '<SECTION-1><TITLE ATOC="N">목 차</TITLE><TITLE ATOC="Y">I. 회사의 개요</TITLE></SECTION-1>'
    assert [n["title"] for n in sections.parse_toc(xml)] == ["I. 회사의 개요"]


def test_second_title_in_same_section_descends():
    """연결재무제표 주석 1~34 는 SECTION 없이 TITLE 만 나열된다."""
    xml = (
        '<SECTION-1><TITLE ATOC="Y">III. 재무</TITLE>'
        '<SECTION-2><TITLE ATOC="Y">3. 주석</TITLE><P>a</P>'
        '<TITLE ATOC="Y">1. 일반적 사항</TITLE><P>b</P></SECTION-2></SECTION-1>'
    )
    assert [n["path"] for n in sections.parse_toc(xml)][-1] == (
        "III. 재무 > 3. 주석 > 1. 일반적 사항"
    )


def test_own_text_excludes_child_sections():
    """상위 목차가 하위 본문까지 담으면 같은 문장이 두 청크에 중복된다."""
    xml = (
        '<SECTION-1><TITLE ATOC="Y">II. 사업의 내용</TITLE><P>머리말</P>'
        '<SECTION-2><TITLE ATOC="Y">1. 사업의 개요</TITLE><P>자식본문</P></SECTION-2>'
        "</SECTION-1>"
    )
    parent = sections.parse_toc(xml)[0]
    body = sections.render(xml[parent["start"]:parent["end"]])
    assert "머리말" in body and "자식본문" not in body


def test_parse_toc_html_recovers_levels_from_numbering():
    """대체 수집본(pdf+html)은 SECTION 마크업이 없어 번호 표기로 계층을 되짚는다."""
    html = (
        "<A name='toc1'>III. 재무에 관한 사항</A>"
        "<A name='toc2'>2. 연결재무제표</A>"
        "<A name='toc3'>2-1. 연결 재무상태표</A>"
        "<A name='toc4'>3. 연결재무제표 주석</A>"
        "<A name='toc5'>1. 일반사항 (연결)</A>"
    )
    assert [n["path"] for n in sections.parse_toc_html(html)] == [
        "III. 재무에 관한 사항",
        "III. 재무에 관한 사항 > 2. 연결재무제표",
        "III. 재무에 관한 사항 > 2. 연결재무제표 > 2-1. 연결 재무상태표",
        "III. 재무에 관한 사항 > 3. 연결재무제표 주석",
        "III. 재무에 관한 사항 > 3. 연결재무제표 주석 > 1. 일반사항 (연결)",
    ]


# --- 슬라이드 대조 -------------------------------------------------------------
def test_slide_chunk_matches_convention_7_3(slide_records):
    """CONVENTION §7.3 에 인쇄된 text 앞부분·표·n_tables 를 그대로 재현하는가."""
    hit = [r for r in slide_records if r["path"] == SLIDE_PATH]
    assert len(hit) == 1
    record = hit[0]
    assert record["text"].startswith(
        "2. 주요 제품 및 서비스\n\n가. 주요 제품 매출\n당사는 TV, 냉장고, 세탁기, 에어컨, "
        "스마트폰 등 완제품과 DRAM, NAND F"
    )
    assert "| 총 계 |  | 3,336,059 | 100.00% |" in record["text"]
    assert "| DX 부문 | TV, 모니터, 냉장고, 세탁기, 에어컨,스마트폰, 네트워크시스템, PC 등 | 1,879,673 | 56.3% |" in record["text"]
    assert record["n_tables"] == 3
    assert record["n_chars"] == len(record["text"])
    assert not record["is_stub"]


def test_slide_record_has_schema_keys(slide_records):
    for key in ("chunk_id", "doc_id", "corp_code", "corp_name", "sector",
                "doc_subtype", "base_year", "base_month", "rcept_dt",
                "path", "n_chars", "n_tables", "text"):
        assert key in slide_records[0], key
    assert slide_records[0]["chunk_id"].startswith(SLIDE_DOC + "#")
    assert slide_records[0]["corp_name"] == "삼성전자"
    assert slide_records[0]["base_year"] == 2025


def test_paths_are_unique_within_document(slide_records):
    """read_section 이 path 로 찾는 이상 중복 path 는 잘못된 근거를 준다."""
    paths = [r["path"] for r in slide_records]
    assert len(paths) == len(set(paths))


def test_audit_attachment_is_prefixed(slide_records):
    """감사보고서·연결감사보고서는 목차 제목이 같아 접두어 없이는 충돌한다."""
    prefixed = {r["path"] for r in slide_records if r["path"].startswith("[")}
    assert "[감사보고서] 독립된 감사인의 감사보고서" in prefixed
    assert "[연결감사보고서] 독립된 감사인의 감사보고서" in prefixed


def test_stub_flag_marks_omitted_sections():
    """분기보고서는 '기재하지 않습니다' 스텁이 많다. §7.7 이 배제 기준으로 쓴다."""
    docs = [d for d in filter_docs(doc_group="periodic")
            if d["doc_id"] == "periodic_20230515002335"]
    if not docs:
        pytest.skip("표본 문서 없음")
    records = list(sections.iter_doc_sections(docs[0]))
    stubs = [r for r in records if r["is_stub"]]
    assert stubs, "분기보고서에 스텁이 하나도 없을 수는 없다"
    assert all(r["n_chars"] < sections.STUB_CHARS for r in stubs)
    assert any("기재하지 않습니다" in r["text"] for r in stubs)


# --- 인덱스 · 툴 -------------------------------------------------------------
@pytest.fixture(scope="module")
def built(tmp_path_factory):
    out = tmp_path_factory.mktemp("sections")
    subprocess.run(
        [sys.executable, "scripts/build_sections.py", "--limit", "3", "--out-dir", str(out)],
        cwd=settings().repo_root, check=True,
    )
    return out


def test_build_writes_index_and_blob(built):
    """실제 artifacts/ 를 덮지 않는다 — 덮으면 API 가 3건만 서빙하게 된다."""
    index = built / sections.INDEX_NAME
    blob = built / sections.BLOB_NAME
    assert index.exists() and blob.exists()
    record = json.loads(index.read_text(encoding="utf-8").splitlines()[0])
    assert "text" not in record
    assert record["text_bytes"] > 0


def test_offsets_point_at_the_right_text(built):
    """오프셋이 한 칸이라도 어긋나면 근거가 다른 섹션 본문이 된다."""
    lines = (built / sections.INDEX_NAME).read_text(encoding="utf-8").splitlines()
    raw = (built / sections.BLOB_NAME).read_bytes()
    for line in lines[:200]:
        record = json.loads(line)
        text = raw[record["text_offset"]:record["text_offset"] + record["text_bytes"]].decode("utf-8")
        assert len(text) == record["n_chars"]
        assert text.startswith(record["path"].split(" > ")[-1])


def test_list_and_read_section_use_the_built_index(built, monkeypatch):
    monkeypatch.setattr(sections, "load_index", lambda: tuple(
        json.loads(line) for line in
        (built / sections.INDEX_NAME).read_text(encoding="utf-8").splitlines()))
    sections._by_doc.cache_clear()
    monkeypatch.setattr(sections, "section_text", lambda r: _blob_text(built, r))
    try:
        doc_id = json.loads((built / sections.INDEX_NAME)
                            .read_text(encoding="utf-8").splitlines()[0])["doc_id"]
        listed = sections.list_sections(doc_id)
        assert listed and all(not r["is_stub"] for r in listed)
        assert all(r["n_chars"] >= sections.STUB_CHARS for r in listed)
        assert len(sections.list_sections(doc_id, include_stubs=True)) > len(listed)

        target = listed[0]["path"]
        got = sections.read_section(doc_id, target)
        assert got and got["n_chars"] > 0
        assert sections.read_section(doc_id, "없는 목차") is None

        # 상위 목차로 부르면 하위 섹션을 이어 붙인다 (parent-child)
        chapter = sections.read_section(doc_id, target.split(" > ")[0])
        assert chapter and chapter["n_sections"] >= 1
    finally:
        sections._by_doc.cache_clear()


def _blob_text(out_dir, record):
    with (out_dir / sections.BLOB_NAME).open("rb") as fh:
        fh.seek(record["text_offset"])
        return fh.read(record["text_bytes"]).decode("utf-8")
