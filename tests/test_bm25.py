from argus.retrieve.bm25 import Bm25Index, tokenize

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


def test_roundtrip_save_load(tmp_path):
    index = Bm25Index.build(DOCS)
    path = tmp_path / "idx.json"
    index.save(path)
    assert Bm25Index.load(path).search("변압기", top_k=1)[0][0] == "b"
