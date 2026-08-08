from argus.corpus.manifest import filter_docs
from argus.corpus.normalize import normalize_exchange


def _doc(rcept_no: str) -> dict:
    return next(d for d in filter_docs(doc_group="exchange") if d["rcept_no"] == rcept_no)


def test_normalize_extracts_amount_and_ratio():
    rec = normalize_exchange(_doc("20230131800162"))
    assert rec["amount"] == 97_000_000_000
    assert rec["ratio"] == 5.37
    assert rec["ratio_base"] == "매출액"


def test_normalize_extracts_counterparty_and_period():
    rec = normalize_exchange(_doc("20230131800162"))
    assert "Hyundai Electric America" in rec["counterparty"]
    assert rec["period_start"] == "2023-01-30"
    assert rec["period_end"] == "2024-10-31"


def test_normalize_carries_manifest_identity():
    rec = normalize_exchange(_doc("20230131800162"))
    assert rec["doc_id"] == "exchange_20230131800162"
    assert rec["corp_name"] == "HD현대일렉트릭"
    assert rec["is_correction"] is False
    assert rec["related"] == []
