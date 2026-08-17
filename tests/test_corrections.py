import copy
import json
from functools import lru_cache

from argus.config import settings
from argus.corpus.corrections import (
    _drop_cycles,
    attach_related,
    latest_of,
    parse_submit_clue,
)
from argus.corpus.corrections import build_chains as _build_chains


@lru_cache(maxsize=None)
def _chains_once(doc_group: str) -> dict[str, list[str]]:
    return _build_chains(doc_group)


def build_chains(doc_group: str = "exchange") -> dict[str, list[str]]:
    """이 파일 안에서만 쓰는 `corrections.build_chains` 의 그룹별 1회 빌드 래퍼.

    **단언은 한 글자도 바뀌지 않는다.** 이 파일은 같은 그룹의 체인을 18번 다시
    지었고(exchange 만 열둘), 한 번 짓는 데 코퍼스 전량(`filter_docs`)을 훑는다.
    전량 24.9초 중 **3.9초**가 그 재계산이었다. 결과는 세 그룹 다 결정적이다
    (`build_chains` 는 인자 하나만 보고 디스크 산출물을 읽는 순수 함수).

    **깊은 사본을 돌려준다.** 원본을 그대로 주면 한 테스트가 체인 dict 를 고쳤을 때
    뒤따르는 테스트가 오염된 값을 보고 순서 의존이 생긴다. 지금은 고치는 테스트가
    없지만, 이 래퍼가 그 취약점을 만들지 않게 사본으로 끊는다.
    """
    return copy.deepcopy(_chains_once(doc_group))


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
    """거래소 서식, 라벨 칸과 값 칸이 나뉜다."""
    assert parse_submit_clue("2. 정정관련 공시서류제출일", "2023-03-28") == "20230328"


def test_parse_submit_clue_reads_major_label_in_one_paragraph():
    """주요사항·지분 서식, 라벨과 값이 한 <P> 단락 안에 같이 있다.

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


# --- 그룹별 연결률, major/holding 은 사실상 0 이었다 ------------------------


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
    대상일 수 없다. 둘의 관계는 정정이 아니라 REFERS_TO 다.

    385 -> 381 도 회귀가 아니다. `_drop_cycles` 가 상호 참조로 생긴 순환 2쌍을 끊었다.
    빠진 4건은 **연결이 없어진 문서가 아니라 두 번 세어졌던 간선**이다. 사슬 하나가
    뿌리 둘로 갈려 `(A->B)` 와 `(B->A)` 를 동시에 세고 있었고, 그 대가로 그 사슬에
    `is_latest` 가 **한 건도 없었다**(`test_every_chain_group_has_exactly_one_latest`).
    """
    linked = sum(len(v) for v in build_chains("exchange").values())
    assert linked >= 381


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


# --- 상호 참조 순환, 최신본이 **한 건도 없는** 체인이 실재했다 ----------------


def test_drop_cycles_removes_a_mutual_pair_entirely():
    """순환 구성원 중 하나를 뿌리로 승격하지 않는다. **간선을 버린다.**

    승격하면 사슬은 서지만 상호 참조를 근거로 한 건을 '대체됨' 으로 단정하게 되고,
    그 건이 다시 `is_latest` 필터에서 사라진다. 단서가 대상을 특정하지 못한 것이므로
    연결하지 않는 것이 이 파일의 기존 원칙과 같다.
    """
    parent = {"A": "B", "B": "A"}
    dropped = _drop_cycles(parent)
    assert parent == {}
    assert sorted(dropped) == [("A", "B"), ("B", "A")]


def test_drop_cycles_keeps_edges_that_hang_off_a_cycle():
    """순환 밖 간선은 살아야 한다. 현대로템 20240712800050·053 이 그 자리다."""
    parent = {"A": "B", "B": "A", "C": "A", "D": "C"}
    _drop_cycles(parent)
    assert parent == {"C": "A", "D": "C"}


def test_drop_cycles_leaves_an_acyclic_forest_untouched():
    parent = {"C2": "C1", "C1": "R", "X": "Y"}
    assert _drop_cycles(parent) == []
    assert parent == {"C2": "C1", "C1": "R", "X": "Y"}


def test_every_chain_group_has_exactly_one_latest():
    """실측으로 깨져 있던 불변이다. exchange 2쌍(4건)이 최신본 0건이었다.

    `superseded` 가 두 사슬의 합집합이 되어 A·B 가 둘 다 False 로 떨어졌고, 그 두 건은
    `query_events(is_latest=True)` 와 BM25 `is_latest` 필터에서 통째로 사라졌다
    (삼성중공업 20240612800459·20240612800468).
    """
    for group in ("exchange", "major", "holding"):
        chains = build_chains(group)
        members = {x for root, corr in chains.items() for x in (root, *corr)}
        records = [{"rcept_no": x, "related": []} for x in sorted(members)]
        out = {r["rcept_no"]: r for r in attach_related(records, chains)}
        seen: set[tuple[str, ...]] = set()
        for record in out.values():
            grp = tuple(sorted({record["rcept_no"], *record["related"]}))
            if len(grp) == 1 or grp in seen:
                continue
            seen.add(grp)
            n_latest = sum(1 for x in grp if out[x]["is_latest"])
            assert n_latest == 1, f"{group} {grp} 최신본 {n_latest}건"


def test_mutual_same_day_corrections_are_not_linked():
    """삼성중공업 두 건은 서로의 정정이 아니다.

    `정정관련 공시서류제출일` 이 각각 2021-10-18 · 2020-11-23 로 **다른 계약**을 가리키고
    (둘 다 코퍼스 밖), 같은 날 형제를 `※ 관련공시` 에 적었을 뿐이다.
    """
    chains = build_chains("exchange")
    for a, b in (("20240612800459", "20240612800468"),
                 ("20240612800468", "20240612800459")):
        assert b not in chains.get(a, [])


def test_chain_fragmentation_is_repaired():
    """현대로템: 순환 때문에 4본 사슬이 2본으로 덮여 있었다.

    `member_chain` 이 dict 순회 순서에 딸려 덮이므로 재빌드마다 달라질 수 있었다.
    """
    chains = build_chains("exchange")
    roots = [root for root, corr in chains.items()
             if "20240712800053" in (root, *corr)]
    assert len(roots) == 1
    sequence = [roots[0], *chains[roots[0]]]
    assert "20240712800050" in sequence and "20240712800053" in sequence
    # 순환 쌍의 두 건이 같은 사슬에 함께 들어오면 안 된다
    assert not {"20240514801290", "20240514801320"} <= set(sequence)


# --- 해지 -> 원계약 (`terminates`) -------------------------------------------
# 해지 공시는 `is_correction=False` 라 `build_chains` 루프에 아예 들어오지 않는다.
# 그래서 `related` 가 영원히 비고(실측 20건 중 0건), 유일한 다리가 `※ 관련공시` 다.


def _exchange_records() -> list[dict]:
    path = settings().artifacts_root / "exchange_records.jsonl"
    if not path.exists():                      # 인덱스 없는 클린 클론
        return []
    with path.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def test_termination_links_to_the_original_contract():
    """회귀 Q-066 이 겨냥하는 다리. 대우건설 해지 -> 원계약."""
    records = _exchange_records()
    if not records:
        import pytest
        pytest.skip("artifacts/exchange_records.jsonl 없음 (gitignore 된 인덱스 산출물)")
    out = {r["rcept_no"]: r for r in attach_related(records, build_chains("exchange"))}
    assert out["20241115800529"]["terminates"] == ["20230228801277"]


def test_termination_link_carries_the_target_chain():
    """원계약이 이미 대체본인 경우가 실재한다. 한 건만 주면 정정 전 금액을 답한다."""
    records = _exchange_records()
    if not records:
        import pytest
        pytest.skip("artifacts/exchange_records.jsonl 없음")
    out = {r["rcept_no"]: r for r in attach_related(records, build_chains("exchange"))}
    links = out["20250331802527"]["terminates"]
    assert "20240930800764" in links            # 원문이 가리킨 대체본
    assert "20250331803174" in links            # 그 체인의 최신본


def test_termination_links_are_forward_only_and_additive():
    """`related`·`is_latest` 를 건드리지 않는다. 역방향은 `tools._followup_index` 몫이다.

    양방향으로 넣으면 `get_history(원계약)` 의 `latest` 가 해지 공시로 옮겨 가고
    `followups` 에서 같은 건이 빠진다. 지금 도는 경로의 동작이 바뀐다.
    """
    records = _exchange_records()
    if not records:
        import pytest
        pytest.skip("artifacts/exchange_records.jsonl 없음")
    out = {r["rcept_no"]: r for r in attach_related(records, build_chains("exchange"))}
    linked = [r for r in out.values() if r["terminates"]]
    assert len(linked) == 11, f"해지 연결 {len(linked)}건 (실측 20건 중 11건이 사정권)"
    for record in linked:
        assert record["doc_subtype"] == "단일판매공급계약해지"
        assert record["related"] == []          # 해지는 정정 체인에 없다
        assert record["is_latest"] is True
        for target in record["terminates"]:
            assert out[target]["terminates"] == []      # 역방향 없음


def test_non_termination_documents_get_an_empty_terminates():
    """`※ 관련공시` 순방향 전체를 붙이는 안은 기각했다 (강한 엣지 출처 453건 중 해지 11건).

    계약체결 공시도 선행 공시를 적어 두지만 그건 정정도 해지도 아니다.
    """
    records = _exchange_records()
    if not records:
        import pytest
        pytest.skip("artifacts/exchange_records.jsonl 없음")
    out = attach_related(records, build_chains("exchange"))
    others = [r for r in out
              if r["doc_subtype"] != "단일판매공급계약해지" and r["terminates"]]
    assert others == []


def test_termination_link_requires_the_form_to_agree():
    """서식이 안 맞으면 붙이지 않는다. 조회공시 요구가 원계약으로 둔갑한다."""
    records = [
        {"rcept_no": "T", "corp_name": "가나", "rcept_dt": "20240301",
         "doc_subtype": "단일판매공급계약해지", "related": [],
         "raw_rows": [["관련공시", "2023-02-28 단일판매ㆍ공급계약체결"]]},
        {"rcept_no": "O", "corp_name": "가나", "rcept_dt": "20230228",
         "doc_subtype": "조회공시요구(풍문또는보도)", "related": [], "raw_rows": []},
    ]
    out = {r["rcept_no"]: r for r in attach_related(records, {})}
    assert out["T"]["terminates"] == []


def test_termination_link_ignores_the_correction_label_row():
    """`1. 정정관련 공시서류` 를 참조 라벨로 오인하면 같은 날 아무 문서에나 붙는다."""
    records = [
        {"rcept_no": "T", "corp_name": "가나", "rcept_dt": "20240301",
         "doc_subtype": "단일판매공급계약해지", "related": [],
         "raw_rows": [["1. 정정관련 공시서류", "2023-02-28 단일판매ㆍ공급계약체결"]]},
        {"rcept_no": "O", "corp_name": "가나", "rcept_dt": "20230228",
         "doc_subtype": "단일판매공급계약체결", "related": [], "raw_rows": []},
    ]
    out = {r["rcept_no"]: r for r in attach_related(records, {})}
    assert out["T"]["terminates"] == []
