"""정기공시 섹션 인덱스, 2차 분할 · 메타 축 · 빌드 스크립트.

**모든 빌드는 `tmp_path` 로 격리한다.** 과거에 테스트가 실제 인덱스를 덮어쓴 사고가 있었다.
`scripts/build_section_index.py` 는 `--out-dir`/`--source-dir` 없이 부르지 않는다.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.build_section_index import OUT_STEM, iter_sections, main  # noqa: E402
from argus.corpus.sections import BLOB_NAME, INDEX_NAME               # noqa: E402
from argus.retrieve.bm25 import (SectionBm25Index, iter_section_parts,  # noqa: E402
                                 norm_path, split_section)

TABLE = "\n".join(["| 부문 | 매출액 | 비중 |", "|---|---|---|"]
                  + [f"| 부문{i} | {i:,}000 | {i}.0% |" for i in range(1, 40)])


def _section(chunk_id, doc_id, corp, path, text, *, rcept_dt="20260310",
             base_year=2025, subtype="annual", stub=False):
    return {"chunk_id": chunk_id, "doc_id": doc_id, "corp_code": "0001",
            "corp_name": corp, "sector": "섹터", "doc_subtype": subtype,
            "base_year": base_year, "base_month": 12, "rcept_dt": rcept_dt,
            "is_correction": False, "path": path, "level": path.count(" > ") + 1,
            "n_chars": len(text), "n_tables": 1, "is_stub": stub,
            "is_container": False, "text": text}


@pytest.fixture
def corpus(tmp_path):
    """작은 섹션 코퍼스를 오프셋 블롭 형식으로 만든다 (실제 산출물과 같은 모양)."""
    rows = [
        _section("d1#000", "periodic_1", "삼성전자",
                 "II. 사업의 내용 > 4. 매출 및 수주상황",
                 "4. 매출 및 수주상황\n\n반도체 부문 매출액은 다음과 같다.\n\n" + TABLE),
        _section("d1#001", "periodic_1", "삼성전자",
                 "III. 재무에 관한 사항 > 1. 요약재무정보",
                 "1. 요약재무정보\n\n연결 자산총계 1,234,567 백만원"),
        _section("d1#002", "periodic_1", "삼성전자",
                 "V. 회계감사인의 감사의견 등 > 2. 내부통제에 관한 사항",
                 "해당사항 없음", stub=True),
        # 목차 오타, 마침표가 아니라 쉼표 (HMM 20240514001580 실물)
        _section("d2#000", "periodic_2", "HMM",
                 "III. 재무에 관한 사항 > 3. 연결재무제표 주석 > 12, 관계기업투자 (연결)",
                 "12, 관계기업투자 (연결)\n\n관계기업 지분 평가액 9,876 백만원",
                 rcept_dt="20240514", base_year=2024, subtype="quarter"),
        _section("d3#000", "periodic_3", "HMM",
                 "II. 사업의 내용 > 4. 매출 및 수주상황",
                 "4. 매출 및 수주상황\n\n컨테이너 부문 매출액 7,777 백만원",
                 rcept_dt="20250514", base_year=2024, subtype="half"),
    ]
    source = tmp_path / "src"
    source.mkdir()
    offset = 0
    with (source / BLOB_NAME).open("wb") as blob, \
            (source / INDEX_NAME).open("w", encoding="utf-8") as index:
        for row in rows:
            payload = row.pop("text").encode("utf-8")
            blob.write(payload + b"\n\x0c\n")
            row["text_offset"], row["text_bytes"] = offset, len(payload)
            offset += len(payload) + 3
            index.write(json.dumps(row, ensure_ascii=False) + "\n")
    return source


def _build(source, out_dir, *extra):
    argv = ["build_section_index.py", "--source-dir", str(source),
            "--out-dir", str(out_dir), *extra]
    old, sys.argv = sys.argv, argv
    try:
        main()
    finally:
        sys.argv = old
    return SectionBm25Index.load(out_dir / OUT_STEM)


# --- 2차 분할 ------------------------------------------------------------------

def test_split_keeps_short_section_whole():
    assert split_section("짧은 본문", 100) == ["짧은 본문"]


def test_split_never_cuts_inside_a_table():
    """표 문자비가 0.82 다. 표 중간에서 자르면 값-라벨 매핑이 통째로 깨진다."""
    parts = split_section("머리말\n\n" + TABLE, 300)
    assert len(parts) > 1
    for part in parts:
        for line in part.split("\n"):
            if line.startswith("|") and not line.startswith("|---"):
                assert line.endswith("|"), "행 중간에서 잘렸다"


def test_split_repeats_the_table_header_on_every_piece():
    """헤더를 안 얹으면 두 번째 조각부터 열 의미가 사라진다. 숫자만 남은 표는 근거가 아니다."""
    parts = split_section(TABLE, 300)
    assert len(parts) > 1
    assert all(p.startswith("| 부문 | 매출액 | 비중 |") for p in parts)


def test_split_bounds_every_piece():
    """카카오 2023 사업보고서 연결재무제표 주석은 한 섹션이 255,691자다."""
    body = "\n\n".join(f"문단 {i} " + "가" * 500 for i in range(200))
    assert all(len(p) <= 8000 for p in split_section(body, 8000))


def test_section_parts_are_deterministic_and_numbered():
    record = {"chunk_id": "c#000", "n_chars": 20000, "is_stub": False}
    text = "\n\n".join("가" * 900 for _ in range(20))
    first = list(iter_section_parts(record, text, 4000))
    assert [r["chunk_id"] for r, _ in first][:2] == ["c#000/p0", "c#000/p1"]
    assert all(r["n_parts"] == len(first) for r, _ in first)
    assert all(r["section_chars"] == 20000 for r, _ in first)
    # 결정적이어야 블롭 오프셋 없이 part 번호만으로 같은 조각을 되찾을 수 있다
    assert [t for _, t in iter_section_parts(record, text, 4000)] == [t for _, t in first]


# --- 메타 축 -------------------------------------------------------------------

def test_stubs_are_indexed_by_default(corpus, tmp_path):
    """스텁 자체가 답인 문항이 있다. '기재 생략' 임을 짚는 것이 정답이다 (Q-053·Q-059).

    색인에서 빼면 도구가 그 섹션을 열 수조차 없다. 배제는 `list_sections` 가 한다 (§7.7 ④).
    """
    index = _build(corpus, tmp_path / "out")
    assert len(index) >= 5
    assert any(index.record(c)["is_stub"] for c in index.chunk_ids)


def test_stubs_can_be_excluded_explicitly(corpus, tmp_path):
    index = _build(corpus, tmp_path / "out", "--exclude-stubs")
    assert not any(index.record(c)["is_stub"] for c in index.chunk_ids)


def test_path_prefix_narrows_candidates(corpus, tmp_path):
    index = _build(corpus, tmp_path / "out")
    assert index.search("자산총계", top_k=10, prefix={"path": "II. 사업의 내용"}) == []
    hits = index.search("자산총계", top_k=10, prefix={"path": "III. 재무에 관한 사항"})
    assert [index.record(c)["path"] for c, _ in hits] == [
        "III. 재무에 관한 사항 > 1. 요약재무정보"]


def test_path_prefix_ignores_punctuation(corpus, tmp_path):
    """제출인 오타가 목차에 그대로 들어온다. '12, 관계기업투자' vs '12. 관계기업투자'."""
    index = _build(corpus, tmp_path / "out")
    hits = index.search("관계기업", top_k=5, prefix={
        "path": "III. 재무에 관한 사항 > 3. 연결재무제표 주석 > 12. 관계기업투자 (연결)"})
    assert [index.record(c)["doc_id"] for c, _ in hits] == ["periodic_2"]


def test_meta_axes_combine(corpus, tmp_path):
    """§7.7 의 축을 겹쳐 걸 수 있어야 목차 기반 결정적 선택이 성립한다."""
    index = _build(corpus, tmp_path / "out")
    hits = index.search("컨테이너", top_k=10, corp_name="HMM", doc_subtype="half",
                        prefix={"rcept_dt": "2025", "path": "II. 사업의 내용"})
    assert [index.record(c)["doc_id"] for c, _ in hits] == ["periodic_3"]
    # 축 하나만 어긋나도 후보가 사라져야 필터가 실제로 걸린 것이다
    assert index.search("컨테이너", top_k=10, corp_name="HMM", doc_subtype="annual") == []
    assert index.search("컨테이너", top_k=10, prefix={"rcept_dt": "2024"}) == []


def test_base_year_is_the_fiscal_year_not_the_filing_year(corpus, tmp_path):
    """'2024년 반기보고서' 는 2025년에 접수된다. 두 축을 섞으면 골든이 필터에서 빠진다."""
    index = _build(corpus, tmp_path / "out")
    record = index.record(index.search("컨테이너", top_k=1)[0][0])
    assert record["base_year"] == "2024" and record["rcept_dt"].startswith("2025")


def test_unknown_meta_axis_is_rejected(corpus, tmp_path):
    index = _build(corpus, tmp_path / "out")
    with pytest.raises(KeyError):
        index.search("매출액", top_k=1, 없는축="x")


# --- 저장·격리 -----------------------------------------------------------------

def test_build_writes_only_into_out_dir(corpus, tmp_path):
    """--out-dir 밖으로는 한 바이트도 쓰지 않는다. 실제 인덱스를 덮으면 복구가 안 된다."""
    out = tmp_path / "out"
    _build(corpus, out)
    assert {p.name for p in out.iterdir()} == {f"{OUT_STEM}.npz", f"{OUT_STEM}.json"}
    assert not list(corpus.glob(f"{OUT_STEM}*"))


def test_roundtrip_preserves_scores_and_meta(corpus, tmp_path):
    index = _build(corpus, tmp_path / "out")
    reloaded = SectionBm25Index.load(tmp_path / "out" / OUT_STEM)
    assert reloaded.chunk_ids == index.chunk_ids
    assert reloaded.tokenizer == index.tokenizer
    assert reloaded.search("매출액", top_k=3) == index.search("매출액", top_k=3)


def test_limit_option_caps_the_build(corpus, tmp_path):
    index = _build(corpus, tmp_path / "out", "--limit", "2")
    assert len(index) == 2


def test_iter_sections_reads_text_from_the_blob(corpus):
    rows = list(iter_sections(corpus / INDEX_NAME, corpus / BLOB_NAME))
    assert rows and all(text for _, text in rows)
    assert "요약재무정보" in dict((r["chunk_id"], t) for r, t in rows)["d1#001"]


def test_missing_source_fails_loudly(tmp_path):
    argv = ["build_section_index.py", "--source-dir", str(tmp_path / "nope"),
            "--out-dir", str(tmp_path / "out")]
    old, sys.argv = sys.argv, argv
    try:
        with pytest.raises(SystemExit):
            main()
    finally:
        sys.argv = old


def test_norm_path_folds_punctuation_and_case():
    assert norm_path("12, 관계기업투자 (연결)") == norm_path("12. 관계기업투자 (연결)")
    assert norm_path("III. 재무에 관한 사항") != norm_path("II. 사업의 내용")
