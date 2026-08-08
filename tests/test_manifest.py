from argus.corpus.manifest import filter_docs, load_manifest, load_universe, resolve_company


def test_manifest_has_4204_records():
    assert len(load_manifest()) == 4204


def test_universe_has_70_companies():
    assert len(load_universe()) == 70


def test_resolve_company_handles_common_name():
    # universe 의 corp_name 은 현대자동차, 통용명은 현대차
    rec = resolve_company("현대차")
    assert rec is not None
    assert rec["corp_name"] == "현대자동차"
    assert rec["corp_code"] == "00164742"


def test_resolve_company_handles_official_name():
    assert resolve_company("삼성전자")["corp_code"] == "00126380"


def test_filter_by_group_and_subtype():
    docs = filter_docs(doc_group="exchange", doc_subtype="단일판매공급계약체결")
    assert len(docs) == 1106


def test_filter_can_exclude_corrections():
    all_ex = filter_docs(doc_group="exchange")
    no_corr = filter_docs(doc_group="exchange", include_corrections=False)
    assert len(all_ex) == 1469
    assert len(all_ex) - len(no_corr) == 631
