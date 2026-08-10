"""A2 — 임베딩·RRF·벡터 저장소 검증. **API 0회 · LLM 0회.**

`conftest.block_clova_network` 가 `clova._post` 를 막고 있고, `embed.embed_text` 는 그
`_post` 를 통해 나간다. 즉 **여기서 실수로 진짜 임베딩을 부르면 테스트가 죽는다.**
그게 이 파일의 1차 안전장치다 — 임베딩은 문서 하나에 호출 하나라 루프 하나가
크레딧을 태운다.
"""
import json

import numpy as np
import pytest

from argus import ablation, embed
from argus.retrieve.bm25 import Bm25Index, DenseSide, dense_path

DOCS = [
    {"doc_id": "a", "text": "삼성전자 반도체 위탁생산 공급계약 22조원",
     "meta": {"corp_name": "삼성전자", "rcept_dt": "20250728"}},
    {"doc_id": "b", "text": "HD현대일렉트릭 변압기 공급계약 970억원",
     "meta": {"corp_name": "HD현대일렉트릭", "rcept_dt": "20250715"}},
    {"doc_id": "c", "text": "삼성전자 신규 시설투자 결정",
     "meta": {"corp_name": "삼성전자", "rcept_dt": "20240101"}},
]


def fake_vector(text: str) -> np.ndarray:
    """결정적 가짜 임베딩. 문자 해시로 방향을 만든다 — 값은 뜻이 없고 **재현성만** 있다."""
    rng = np.random.default_rng(abs(hash(text)) % (2 ** 32))
    return embed.normalize(rng.standard_normal(embed.EMBED_DIM).astype(np.float32))


@pytest.fixture
def offline(monkeypatch, tmp_path):
    """임베딩 호출을 가짜로 바꾸고 캐시를 tmp 로 돌린다."""
    monkeypatch.setattr(embed, "embed_text", fake_vector)
    monkeypatch.setattr(embed, "cache_dir", lambda: tmp_path)
    embed.reset_caches()
    yield
    embed.reset_caches()


# --- RRF ----------------------------------------------------------------------

def test_rrf_ranks_a_document_found_by_both_lists_first():
    """두 순위표에 다 든 문서가 이긴다 — 그것이 융합의 정의다."""
    fused = embed.rrf(["x", "a", "b"], ["y", "a", "c"])
    assert fused[0][0] == "a"


def test_rrf_lets_the_dense_list_introduce_a_new_document():
    """dense 만 올린 문서가 결과에 들어와야 A2 가 **새 문서를 찾을 수** 있다."""
    fused = embed.rrf(["a", "b"], ["z"], top_k=3)
    assert "z" in [d for d, _ in fused]


def test_rrf_score_matches_the_formula():
    (top_id, top_score), = embed.rrf(["a"])
    assert top_id == "a"
    assert top_score == pytest.approx(1.0 / (embed.RRF_K + 1))


def test_rrf_breaks_ties_by_the_first_list():
    """동점이면 BM25(첫 리스트) 순서를 지킨다. 안 그러면 재실행마다 순서가 흔들린다."""
    assert [d for d, _ in embed.rrf(["a", "b"], ["b", "a"])] == ["a", "b"]


def test_fetch_depth_is_deeper_than_top_k():
    """두 리스트를 top_k 로 잘라 융합하면 집합이 절대 안 바뀐다 — 순서만 바뀐다."""
    assert embed.fetch_depth(5) > 5


# --- 벡터 저장소 ---------------------------------------------------------------

def test_store_roundtrip_keeps_ranking(tmp_path, offline):
    store = embed.VectorStore()
    store.ensure(["a", "b", "c"], lambda d: d)
    store.save(tmp_path / "s")
    again = embed.VectorStore.load(tmp_path / "s")
    query = fake_vector("a")
    assert again.rank(query, ["a", "b", "c"], 1)[0][0] == "a"


def test_store_is_float16_for_the_memory_gate(offline):
    """게이트 500MB 는 dtype 에서 갈린다 — float32 로 두면 정기공시가 2배가 된다."""
    store = embed.VectorStore()
    store.ensure(["a"], lambda d: d)
    assert store.vectors.dtype == np.float16
    assert store.nbytes == embed.EMBED_DIM * 2


def test_ensure_never_re_embeds_what_it_already_has(offline):
    seen = []
    store = embed.VectorStore()
    store.ensure(["a", "b"], lambda d: seen.append(d) or d)
    assert store.ensure(["a", "b"], lambda d: seen.append(d) or d) == 0
    assert seen == ["a", "b"]                     # 두 번째 호출은 API 0회


def test_add_ignores_duplicate_ids(offline):
    store = embed.VectorStore()
    store.ensure(["a"], lambda d: d)
    store.add(["a"], fake_vector("a").reshape(1, -1))
    assert len(store) == 1                        # 중복 행은 순위를 두 번 세게 한다


def test_rank_skips_candidates_that_are_not_stored(offline):
    store = embed.VectorStore()
    store.ensure(["a"], lambda d: d)
    assert [d for d, _ in store.rank(fake_vector("a"), ["a", "없는것"], 5)] == ["a"]


# --- 예산 가드 -----------------------------------------------------------------

def test_usage_raises_once_the_won_limit_is_passed():
    usage = embed.EmbedUsage(limit_won=0.001)     # 10 토큰이면 넘는다
    usage.add(5)
    with pytest.raises(embed.EmbedBudgetExceeded):
        usage.add(1_000_000)


def test_usage_counts_measured_tokens_not_estimates():
    usage = embed.EmbedUsage()
    usage.add(1234)
    assert usage.tokens == 1234
    assert usage.won == pytest.approx(1234 / 1000 * embed.PRICE_WON_PER_1K)


def test_embedding_price_is_not_the_generation_price():
    """생성 7.02원/1K 를 임베딩에 그대로 쓰면 비용이 70배로 잘못 보고된다."""
    from scripts import error_analysis as ea
    assert embed.PRICE_WON_PER_1K != ea.PRICE_WON_PER_1K


# --- 인덱스 결합 ---------------------------------------------------------------

def test_dense_is_off_unless_the_arm_turns_it_on(offline):
    """기본 구성(A6)에서는 dense 사이드카가 붙어 있어도 **한 번도 안 불린다**."""
    index = Bm25Index.build(DOCS)
    dense = index.attach_dense("t")
    dense.store.ensure([d["doc_id"] for d in DOCS], lambda d: d)
    calls = []
    dense.rank = lambda *a, **k: calls.append(1) or []
    with ablation.use("A6"):
        index.search("변압기 공급계약", top_k=2)
    assert calls == []
    assert not DenseSide.enabled()


def test_dense_runs_when_the_arm_is_on(offline, monkeypatch):
    index = Bm25Index.build(DOCS)
    dense = index.attach_dense("t")
    dense.store.ensure([d["doc_id"] for d in DOCS], lambda d: d)
    monkeypatch.setattr(embed, "query_vector", fake_vector)
    with ablation.use("A2F"):
        index.search("변압기 공급계약", top_k=2)
    from argus.retrieve import bm25
    assert bm25.LAST_FUSION and bm25.LAST_FUSION["mode"] == "rrf"


def test_fusion_keeps_bm25_scores_not_rrf_scores(offline, monkeypatch):
    """`MIN_TOP_SCORE=10.0` 이 BM25 스케일 위에 서 있다. RRF 점수(≈0.03)를 흘리면
    모든 문항이 low_score 가 되어 **재는 것이 dense 가 아니라 hop 예산**이 된다."""
    index = Bm25Index.build(DOCS)
    dense = index.attach_dense("t")
    dense.store.ensure([d["doc_id"] for d in DOCS], lambda d: d)
    monkeypatch.setattr(embed, "query_vector", fake_vector)
    with ablation.use("A6"):
        plain = dict(index.search("변압기 공급계약", top_k=99))
    with ablation.use("A2F"):
        fused = dict(index.search("변압기 공급계약", top_k=3))
    # 융합 뒤에도 점수는 BM25 원점수다. dense 만 올린 문서만 0.0 이다.
    assert fused == {d: plain.get(d, 0.0) for d in fused}
    assert max(fused.values()) == max(plain.values())


def test_meta_prefilter_stays_in_front_of_dense(offline, monkeypatch):
    """§I-1 — 프리필터가 A0 이자 최대 기여 요소다. dense 가 그 앞에 서면 안 된다."""
    index = Bm25Index.build(DOCS)
    dense = index.attach_dense("t")
    dense.store.ensure([d["doc_id"] for d in DOCS], lambda d: d)
    monkeypatch.setattr(embed, "query_vector", fake_vector)
    with ablation.use("A2F"):
        hits = index.search("변압기 공급계약", top_k=5, corp_name="삼성전자")
    assert {d for d, _ in hits} <= {"a", "c"}     # 필터 밖 'b' 는 dense 로도 못 들어온다


def test_dense_skips_an_oversized_candidate_pool(offline, monkeypatch):
    """상한을 없애면 필터 없는 질의 한 번이 코퍼스 전량을 임베딩한다."""
    monkeypatch.setattr(embed, "DENSE_MAX_POOL", 1)
    index = Bm25Index.build(DOCS)
    index.attach_dense("t")
    monkeypatch.setattr(embed, "query_vector", fake_vector)
    with ablation.use("A2F"):
        index.search("공급계약", top_k=3)
    from argus.retrieve import bm25
    assert bm25.LAST_FUSION["mode"] == "skip_pool"


def test_candidates_returns_only_prefilter_survivors():
    index = Bm25Index.build(DOCS)
    keep = index.candidates({"rcept_dt": "2025"}, corp_name="삼성전자")
    assert [index.doc_ids[i] for i in keep] == ["a"]


def test_dense_sidecar_path_lives_under_artifacts():
    assert dense_path("exchange").name == "exchange_dense"
    assert dense_path("exchange").parent.name == embed.EMBED_CACHE_DIRNAME


# --- 산출물 계약 ---------------------------------------------------------------

def test_cost_ledger_records_measured_usage_only():
    """보고서의 비용은 **API 응답의 실측 usage** 여야 한다 (추정치 금지)."""
    path = (embed.settings().repo_root / "artifacts" / "ablation" / "A2"
            / "embedding_cost.json")
    if not path.exists():
        pytest.skip("1단계 미실행")
    ledger = json.loads(path.read_text(encoding="utf-8"))
    stage1 = ledger["stage1"]
    assert stage1["tokens"] > 0 and stage1["calls"] == stage1["n"]
    assert stage1["chars_per_token"] == pytest.approx(
        stage1["chars"] / stage1["tokens"], rel=1e-3)


# --- ⑤ spent_won 누적 (대입이 앞선 지출 기록을 지우는 사고) ----------------------

def test_records_then_warm_accumulates_spent_won_instead_of_overwriting(tmp_path, monkeypatch):
    """`scripts/build_embeddings.py` 의 다섯 번째 서식지.

    이전 버그: `ledger["spent_won"] = round(embed.USAGE.won, 2)` 가 **대입**이었다.
    `embed.USAGE.won` 은 프로세스 전체의 누적값이라(`stage1()` 만 `.clear()` 한다), 별도
    프로세스로 `--records` 를 다시 돌리면(또는 그 뒤에 `--warm` 을 돌리면) 그 값이
    **이번 호출분뿐**인데 원장의 `spent_won` 을 통째로 대입해 앞선 지출을 지웠다.

    실측 흔적: `artifacts/ablation/A2/embedding_cost.json` 의 `spent_won` 이
    110.82(records 단독 usage)로 남아 1단계 표본 2.64원이 빠져 있다 — 올바른 누계는
    `embedding_spend_won` 113.44(그 필드는 이미 `+=` 로 고쳐져 있었다). 이 테스트는
    같은 사고가 `spent_won` 에도 다시 나지 않는지를 잰다: `--records` 로 0.1원 쓴 뒤
    (별도 프로세스를 흉내내어 `embed.USAGE` 를 리셋하고) `--warm` 으로 0.2원을 더 쓰면
    원장의 `spent_won` 은 **0.3원(누계)** 이어야 한다 — 0.2원(warm 단독)이면 회귀다.
    """
    from argus import embed
    from scripts import build_embeddings as be

    monkeypatch.setattr(be, "OUT_DIR", tmp_path)
    monkeypatch.setattr(be, "report", lambda: {})   # 실제 인덱스 로드를 건드리지 않는다
    monkeypatch.setattr(embed, "USAGE", embed.EmbedUsage())

    def fake_records(budget_won):
        embed.USAGE.add(1000)                       # 0.1원 (PRICE_WON_PER_1K=0.1)
        return {"fake": "records"}

    def fake_warm(budget_won):
        embed.USAGE.add(2000)                       # 0.2원
        return {"fake": "warm"}

    monkeypatch.setattr(be, "build_records", fake_records)
    monkeypatch.setattr(be, "warm_pools", fake_warm)

    ledger_path = tmp_path / "embedding_cost.json"

    be.main(["--records"])
    after_records = json.loads(ledger_path.read_text(encoding="utf-8"))
    assert after_records["spent_won"] == pytest.approx(0.1, abs=1e-9)

    # 두 번째 호출을 **별도 프로세스**로 흉내낸다 — usage 누적기는 리셋되지만 디스크의
    # 원장은 이어받는다 (실제 CLI 는 매번 새 프로세스라 embed.USAGE 가 0에서 시작한다).
    monkeypatch.setattr(embed, "USAGE", embed.EmbedUsage())
    be.main(["--warm"])
    after_warm = json.loads(ledger_path.read_text(encoding="utf-8"))

    # 회귀의 핵심 주장: 두 번째 실행 뒤에도 **첫 실행의 지출이 남아 있다.**
    assert after_warm["spent_won"] == pytest.approx(0.3, abs=1e-9)
    # 대입 버그였다면 여기서 0.2(=warm 단독)가 나왔을 것이다 — 명시적으로 부정한다.
    assert after_warm["spent_won"] != pytest.approx(0.2, abs=1e-9)


def test_evalset_snapshot_is_frozen_at_the_gate_d_cohort():
    """🔴 다른 트랙이 dev 를 42 -> 78 로 늘렸다. A2 의 Δ 는 **얼려 둔 42문항**에서만
    A6 과 비교 가능하다 — 확장분이 섞이면 재는 것이 arm 이 아니라 문항이다."""
    from scripts import validate_evalset as V

    root = embed.settings().repo_root
    snapshot = json.loads(
        (root / "artifacts" / "ablation" / "A2" / "evalset_snapshot.json")
        .read_text(encoding="utf-8"))
    assert snapshot["n"] == 42
    assert set(snapshot["question_ids"]) == set(V.measured_dev_cohort())
