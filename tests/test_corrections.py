from argus.corpus.corrections import attach_related, build_chains, latest_of


def test_chains_cover_all_corrections():
    chains = build_chains("exchange")
    linked = sum(len(v) for v in chains.values())
    assert linked > 0
    assert linked <= 631      # 정정 총건수를 넘을 수 없다


def test_latest_of_returns_self_when_no_correction():
    chains = build_chains("exchange")
    orphan = "20230131800162"
    assert latest_of(orphan, chains) == orphan


def test_attach_related_sets_is_latest():
    chains = build_chains("exchange")
    records = [
        {"rcept_no": "20230131800162", "is_correction": False, "related": []},
    ]
    out = attach_related(records, chains)
    assert out[0]["is_latest"] is True
    assert isinstance(out[0]["related"], list)
