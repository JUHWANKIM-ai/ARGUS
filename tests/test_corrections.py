from argus.corpus.corrections import (
    attach_related,
    build_chains,
    latest_of,
    parse_submit_clue,
)


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


# --- 라벨 어휘: 거래소 / 주요사항·지분이 서로 다르다 -------------------------


def test_parse_submit_clue_reads_exchange_label():
    """거래소 서식 — 라벨 칸과 값 칸이 나뉜다."""
    assert parse_submit_clue("2. 정정관련 공시서류제출일", "2023-03-28") == "20230328"


def test_parse_submit_clue_reads_major_label_in_one_paragraph():
    """주요사항·지분 서식 — 라벨과 값이 한 <P> 단락 안에 같이 있다.

    EDA 는 값이 <TU> 셀에 있다고 봤지만 실제로는 표 밖 단락이다.
    """
    text = "2. 정정대상 공시서류의 최초제출일 : 2024년 08월 02일"
    assert parse_submit_clue(text, "") == "20240802"


def test_parse_submit_clue_reads_major_label_split_across_cells():
    """같은 라벨이 표 형태로도 나온다 (major 57 / holding 20건)."""
    assert parse_submit_clue("2. 정정대상 공시서류의 최초제출일 :", "2025년 2월 18일") == "20250218"


def test_parse_submit_clue_ignores_unrelated_dates():
    """'정정사유' 같은 서술 행의 날짜를 단서로 오인하면 안 된다."""
    assert parse_submit_clue("3. 정정사유", "2024-01-01 일정 변경에 따른 기재정정") is None
    assert parse_submit_clue("정정일자", "2024-04-26") is None


# --- 그룹별 연결률 — major/holding 은 사실상 0 이었다 ------------------------


def test_major_corrections_now_link():
    """실측 baseline: major 173건 중 1건(0.6%)만 연결됐다."""
    linked = sum(len(v) for v in build_chains("major").values())
    assert linked >= 50, f"major 연결 {linked}건 — 라벨 확장이 반영되지 않았다"


def test_holding_corrections_now_link():
    """실측 baseline: holding 41건 중 0건(0%)."""
    linked = sum(len(v) for v in build_chains("holding").values())
    assert linked >= 15, f"holding 연결 {linked}건"


def test_exchange_link_rate_does_not_regress():
    """거래소는 이미 61.2%(386건)였다. 라벨을 넓히다 이걸 깨면 안 된다.

    386 -> 385 로 1건 준 것은 회귀가 아니라 **오연결 제거**다. 한화에어로스페이스
    `[기재정정]단일판매ㆍ공급계약체결`(20231018800203)이 `※관련공시` 단서를 타고
    `투자판단관련주요경영사항`(20230620800002)에 붙어 있었다. 서식이 달라 정정
    대상일 수 없다 — 둘의 관계는 정정이 아니라 REFERS_TO 다.
    """
    linked = sum(len(v) for v in build_chains("exchange").values())
    assert linked >= 385


def test_correction_does_not_link_across_report_forms():
    """같은 날 여러 건을 낸 기업에서 **다른 서식**에 붙으면 안 된다.

    두산로보틱스 `[기재정정]주요사항보고서(회사합병결정)`(20240715000355)은
    대상 제출일을 2024-07-11 로 적었는데, 코퍼스의 그 날짜 문서는
    `주요사항보고서(타법인주식및출자증권양수결정)`(20240711000344) 뿐이다.
    진짜 대상은 코퍼스 밖이므로 **연결하지 않는 것이 정답**이다.
    날짜만 맞춰 붙이면 엉뚱한 문서가 '대체됨' 으로 뒤집힌다.
    """
    chains = build_chains("major")
    assert "20240715000355" not in chains.get("20240711000344", [])


def test_links_never_cross_filers():
    """지분공시의 corp_name 은 발행회사라 보고자가 다른 문서가 같은 날 섞인다."""
    from argus.corpus.manifest import filter_docs

    for group in ("exchange", "major", "holding"):
        docs = {d["rcept_no"]: d for d in filter_docs(doc_group=group)}
        for root, corrections in build_chains(group).items():
            for rcept_no in corrections:
                assert docs[rcept_no]["flr_nm"] == docs[root]["flr_nm"], (
                    f"{group} {rcept_no} -> {root} 보고자 불일치")


# --- is_latest 회귀 고정 ----------------------------------------------------


def test_only_the_last_document_in_a_chain_is_latest():
    """체인의 마지막 1건만 True. 과거에 반대로 구현했다가 고친 이력이 있다."""
    chains = {"R": ["C1", "C2", "C3"]}
    records = [{"rcept_no": x, "related": []} for x in ("R", "C1", "C2", "C3")]
    out = {r["rcept_no"]: r for r in attach_related(records, chains)}
    assert [out[x]["is_latest"] for x in ("R", "C1", "C2")] == [False, False, False]
    assert out["C3"]["is_latest"] is True


def test_is_latest_follows_chain_position_not_amount_size():
    """정정은 금액을 **줄이기도** 한다.

    삼성바이오로직스 실제 사례: 517,856,145,079 -> 275,063,868,984.
    금액 크기로 최신본을 고르면 낡은 큰 값이 최신으로 노출된다.
    """
    chains = {"ROOT": ["CORR"]}
    records = [
        {"rcept_no": "ROOT", "amount": 517_856_145_079, "related": []},
        {"rcept_no": "CORR", "amount": 275_063_868_984, "related": []},
    ]
    out = {r["rcept_no"]: r for r in attach_related(records, chains)}
    assert out["ROOT"]["is_latest"] is False
    assert out["CORR"]["is_latest"] is True
    latest = [r for r in out.values() if r["is_latest"]]
    assert len(latest) == 1 and latest[0]["amount"] == 275_063_868_984


def test_longest_exchange_chain_exposes_exactly_one_latest():
    """최장 체인은 19본(2023-03-28 -> 2024-12-19). 규칙이 깨지면 낡은 값 18개가 노출된다."""
    chains = build_chains("exchange")
    root, corrections = max(chains.items(), key=lambda kv: len(kv[1]))
    sequence = [root, *corrections]
    assert len(sequence) >= 19
    records = [{"rcept_no": x, "related": []} for x in sequence]
    out = attach_related(records, chains)
    assert sum(1 for r in out if r["is_latest"]) == 1
    assert out[-1]["rcept_no"] == sequence[-1]


def test_chain_members_see_each_other_from_any_position():
    chains = {"R": ["C1", "C2"]}
    out = {r["rcept_no"]: r
           for r in attach_related([{"rcept_no": x, "related": []}
                                    for x in ("R", "C1", "C2")], chains)}
    assert set(out["C1"]["related"]) == {"R", "C2"}
    assert set(out["R"]["related"]) == {"C1", "C2"}
