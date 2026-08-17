import numpy as np
from rank_bm25 import BM25Okapi

from argus.retrieve.bm25 import (Bm25Index, SectionBm25Index, section_index_text,
                                 tokenize, tokenize_words)

DOCS = [
    {"doc_id": "a", "text": "삼성전자 반도체 위탁생산 공급계약 22조원", "meta": {"corp_name": "삼성전자"}},
    {"doc_id": "b", "text": "HD현대일렉트릭 변압기 공급계약 970억원", "meta": {"corp_name": "HD현대일렉트릭"}},
    {"doc_id": "c", "text": "삼성전자 신규 시설투자 결정", "meta": {"corp_name": "삼성전자"}},
]


def test_tokenize_splits_korean_and_numbers():
    tokens = tokenize("계약금액 22,764,764,160,000원")
    assert "계약금액" in tokens


def test_search_ranks_relevant_doc_first():
    index = Bm25Index.build(DOCS)
    hits = index.search("변압기 공급계약", top_k=2)
    assert hits[0][0] == "b"


def test_meta_filter_restricts_candidates():
    index = Bm25Index.build(DOCS)
    hits = index.search("공급계약", top_k=5, corp_name="삼성전자")
    assert {h[0] for h in hits} <= {"a", "c"}


def test_prefix_filter_narrows_by_period():
    docs = [
        {"doc_id": "a", "text": "변압기 공급", "meta": {"rcept_dt": "20230131"}},
        {"doc_id": "b", "text": "변압기 공급", "meta": {"rcept_dt": "20240215"}},
    ]
    index = Bm25Index.build(docs)
    hits = index.search("변압기", top_k=5, prefix={"rcept_dt": "2023"})
    assert [d for d, _ in hits] == ["a"]


def test_search_is_unchanged_when_no_dense_sidecar_is_attached():
    """A2 배선이 **기본 경로를 건드리지 않았다**는 회귀 가드.

    `search()` 안에 융합 분기가 생겼다. dense 가 없거나 arm 이 꺼져 있으면 결과가
    종전과 같아야 한다. 여기가 흔들리면 A0~A6 곡선의 3,708원이 통째로 무효가 된다.
    """
    index = Bm25Index.build(DOCS)
    assert index.dense is None                       # build() 는 사이드카를 안 붙인다
    hits = index.search("공급계약", top_k=3)
    assert [d for d, _ in hits] == ["b", "a"]
    assert all(score > 0 for _, score in hits)       # 점수는 BM25 원점수 그대로다


def test_candidates_and_search_agree_on_the_prefilter():
    """dense 는 `candidates()` 가 돌려준 집합 안에서만 순위를 만든다.
    두 경로의 필터가 갈라지면 프리필터 밖 문서가 dense 로 새어 든다."""
    index = Bm25Index.build(DOCS)
    keep = {index.doc_ids[i] for i in index.candidates(corp_name="삼성전자")}
    hits = {d for d, _ in index.search("공급계약", top_k=5, corp_name="삼성전자")}
    assert hits <= keep == {"a", "c"}


def test_roundtrip_save_load(tmp_path):
    index = Bm25Index.build(DOCS)
    path = tmp_path / "idx.json"
    index.save(path)
    assert Bm25Index.load(path).search("변압기", top_k=1)[0][0] == "b"


# --- SectionBm25Index (CSR 역색인) ---------------------------------------------
# rank_bm25 는 문서마다 dict 를 하나씩 든다. 88,654 섹션 전문이면 실측 2,308MB 로
# 게이트 500MB 의 4.6배다. 같은 BM25Okapi 수식을 CSR 로 다시 구현한 이유가 그것이므로,
# **수식이 같다는 것**이 이 클래스의 계약이다.

def _section_docs(n=60):
    words = ["매출액", "변압기", "공급계약", "요약재무정보", "배당", "자본금", "차입금"]
    records, texts = [], []
    for i in range(n):
        records.append({"chunk_id": f"c{i}", "doc_id": f"d{i // 5}",
                        "corp_name": f"기업{i % 4}", "doc_subtype": "annual",
                        "rcept_dt": f"2025{i % 12 + 1:02d}10", "base_year": 2025,
                        "path": f"III. 재무에 관한 사항 > {i % 7}. {words[i % 7]}",
                        "part": 0, "n_parts": 1, "is_stub": 0, "section_chars": 100,
                        "n_tables": 0, "level": 2, "text_offset": 0, "text_bytes": 0})
        texts.append(" ".join(words[(i + k) % 7] for k in range(i % 5 + 1))
                     + f" 항목 {i} 금액 {i},000원")
    return records, texts


def test_section_scores_match_rank_bm25_exactly():
    """CSR 구현이 BM25Okapi 와 다른 점수를 내면 exchange 실측이 재현되지 않는다."""
    records, texts = _section_docs()
    index = SectionBm25Index.build(records, texts, cap=0)
    reference = BM25Okapi([tokenize(section_index_text(r, t, 0))
                           for r, t in zip(records, texts)])
    for query in ("매출액", "요약재무정보 배당", "차입금 자본금"):
        ours = index.scores(query)
        theirs = np.asarray(reference.get_scores(tokenize(query)))
        assert np.abs(ours - theirs).max() < 1e-9, query


def test_section_search_returns_descending_scores():
    records, texts = _section_docs()
    index = SectionBm25Index.build(records, texts, cap=0)
    hits = index.search("차입금", top_k=5)
    assert hits and [s for _, s in hits] == sorted((s for _, s in hits), reverse=True)


def test_section_index_does_not_hold_the_body_text():
    """게이트 500MB 는 본문을 안 안는다는 전제 위에 있다. 오프셋만 들고 있어야 한다."""
    records, texts = _section_docs()
    index = SectionBm25Index.build(records, texts, cap=0)
    assert not hasattr(index, "texts")
    assert set(index.record("c0")) >= {"text_offset", "text_bytes", "part", "n_parts"}


def test_word_tokenizer_drops_bigrams():
    assert tokenize_words("배전변압기") == ["배전변압기"]
    assert "변압" in tokenize("배전변압기")


def test_tokenizer_choice_is_persisted(tmp_path):
    """bigram 으로 지은 색인을 어절 질의로 때리면 조용히 리콜이 죽는다."""
    records, texts = _section_docs()
    SectionBm25Index.build(records, texts, cap=0, tokenizer="word").save(tmp_path / "ix")
    assert SectionBm25Index.load(tmp_path / "ix").tokenizer == "word"
