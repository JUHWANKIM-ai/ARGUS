"""열거 hop (`EVENTS_HOP` `strat=enumerate`), v3 R2 ③.

`Q-117` 5건의 처방이다. 원안(`total_plan §5 #4` "노드 이름 인식")은 `router.py`
262–296 이 실측 3근거로 **기각**하고 처방을 여기로 재정의했다. 한전기술은 그 질문에서
**공시한 쪽**이라 발주처로 결속하면 근거가 뒤집힌다.

그래서 이 파일은 **불변을 함께 지킨다**: 열거 hop 이 생겨도 라우터는 여전히 공시한
쪽을 결속하지 않는다. `tests/test_router.py::test_disclosing_company_is_never_bound_as_counterparty`
가 원본 계약이고 여기서는 그 계약이 살아 있는 채로 회수가 되는지를 본다.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from argus.agent import aggregate, pipeline, tools
from argus.agent.router import route

_VC = Path(__file__).resolve().parents[1] / "evalset/valuechain.jsonl"


def _rows():
    return {json.loads(line)["question_id"]: json.loads(line)
            for line in _VC.open(encoding="utf-8") if line.strip()}


class _Route:
    def __init__(self, companies, counterparty=None):
        self.companies = companies
        self.counterparty = counterparty


# --- 제동 ①~④ ------------------------------------------------------------------

def test_enumeration_needs_the_exchange_group():
    """`query_events` 모집단은 거래소공시다 (§7.7 ②). 다른 문서군에 붙이면 오답 근거다."""
    r = _Route(["한전기술"])
    q = "한전기술이 공시한 공급계약의 발주 구조는 두 갈래로 나뉜다"
    assert pipeline._wants_enumeration(r, q, "exchange") is True
    assert pipeline._wants_enumeration(r, q, "periodic") is False
    assert pipeline._wants_enumeration(r, q, "major") is False


def test_enumeration_needs_exactly_one_company():
    """열거의 모집단은 '한 기업의 한 서식' 이다. 둘 이상이면 무엇을 열거할지 정의되지 않는다."""
    q = "두 회사가 공시한 공급계약의 발주 구조는 두 갈래로 나뉜다"
    assert pipeline._wants_enumeration(_Route([]), q, "exchange") is False
    assert pipeline._wants_enumeration(_Route(["A", "B"]), q, "exchange") is False


def test_the_ordering_party_is_not_enumerated():
    """`Q-114` 경계, 그 기업이 **발주한 쪽**이면 열거는 정반대 문서를 준다.

    `query_events(corp_name='삼성전자')` 는 삼성전자 **자신의** 공시를 주는데, 질문이
    묻는 것은 삼성전자를 **계약상대로 한** 상장사들의 공시다. 방향이 뒤집힌다.
    """
    q = "삼성전자를 계약상대로 하는 공급계약을 공시한 상장사의 공정 분담은 어떻게 나뉘는가"
    assert pipeline._wants_enumeration(_Route(["삼성전자"], "삼성전자"), q, "exchange") is False
    # 발주처가 다른 이름이면 그 기업의 열거는 여전히 성립한다.
    assert pipeline._wants_enumeration(_Route(["한전기술"], "한국수력원자력"), q,
                                       "exchange") is True


def test_parallel_lookup_words_do_not_open_the_enumeration_hop():
    """`각각`·`모두` 를 표지에서 뺀 이유, 100문항에서 9건이 걸리고 8건이 열거가 아니다."""
    r = _Route(["LG이노텍"])
    assert pipeline._wants_enumeration(
        r, "LG이노텍이 2024년과 2025년에 각각 공시한 계약금액은?", "exchange") is False
    assert pipeline._wants_enumeration(
        r, "LG이노텍이 공시한 계약을 모두 알려줘", "exchange") is False


def test_form_words_pick_the_document_subtype_and_termination_wins():
    """`해지` 를 먼저 본다. 뒤에 두면 '공급계약 해지' 가 '체결' 로 샌다.

    `doc_group` 이 '보고서' 를 뒤에 두는 것과 같은 순서 규율이다.
    """
    assert pipeline._event_subtype("공급계약 해지 공시의 갈래") == "단일판매공급계약해지"
    assert pipeline._event_subtype("단일판매·공급계약의 갈래") == "단일판매공급계약체결"
    assert pipeline._event_subtype("신규시설투자 유형") == "신규시설투자등"
    assert pipeline._event_subtype("계약의 유형은?") is None


# --- `query_events` 의 limit 축 -------------------------------------------------

def test_query_events_limit_defaults_to_the_old_page_size():
    """기본값이면 종전과 글자 하나 안 바뀐다. 조용한 Δ 방지."""
    out = tools.query_events()
    if not out["count"]:
        pytest.skip("artifacts/exchange_records.jsonl 없음")
    assert len(out["events"]) == min(out["count"], tools.EVENTS_PAGE)
    # `assert tools.EVENTS_PAGE == 20` 을 뺐다 (R2F). 위 줄이 이미 파생으로 같은 성질을
    #    재고, 리터럴은 페이지 크기를 바꾸려는 다음 사람을 **아무 신호 없이** 막는다.
    #    이 저장소가 `== 13` 세 곳에서 겪은 사고와 같은 모양이다.
    wide = tools.query_events(limit=tools.EVENTS_PAGE * 10)
    assert wide["count"] == out["count"]          # count 는 필터 통과 전량이다
    assert len(wide["events"]) >= len(out["events"])
    assert len(tools.query_events(limit=0)["events"]) == 0


# --- 겨냥 실측 ------------------------------------------------------------------

@pytest.mark.skipif(not _VC.exists(), reason="evalset/valuechain.jsonl 없음")
def test_q117_recovers_two_of_the_five_targeted_golden():
    """겨냥 5건 중 **실측 회수는 2건**이다. 그 사실을 테스트로 고정한다.

    남은 3건은 같은 발주처 그룹의 **두 번째 건**이라(한수원 2건 · 두산 2건 중 각각
    1건씩) '그룹당 1건' 규칙과 정면으로 충돌한다. 사유는 `_enumerate_events`
    독스트링에 정책 6종 비교와 함께 적어 두었다.
    """
    from argus.agent.budget import Budget

    row = _rows()["Q-117"]
    r = route(row["question"])
    assert r.counterparty is None          # 불변, 공시한 쪽은 결속되지 않는다
    evidence, trace = pipeline._gather_evidence(r, row["question"], Budget())
    if not evidence:
        pytest.skip("artifacts/exchange_records.jsonl 없음")
    enum = [line for line in trace if "strat=enumerate" in line]
    assert enum
    # `tools.execute` 는 `fn(**args)` 이고 `TypeError` 를 `{"error": …}` 로 **삼킨다.**
    #    그러면 이 hop 은 `skip=tool_error` 를 남기고 0건을 돌려주는데, 그 줄에도
    #    `strat=enumerate` 가 들어 있어 발화 단언만으로는 인자명 오타가 통과한다.
    #    `_enumerate_events` 가 넘기는 키 넷(`corp_name`·`is_latest`·`limit`·`doc_subtype`)
    #    중 하나만 틀려도 Δ 가 **조용히 0** 이 되므로 여기서 함께 막는다.
    assert not any("skip=tool_error" in line for line in enum), enum
    for rcept_no in ("20250729800143", "20251212801320"):
        assert rcept_no in evidence, rcept_no


@pytest.mark.skipif(not _VC.exists(), reason="evalset/valuechain.jsonl 없음")
def test_the_enumeration_hop_does_not_fire_for_q114():
    """`Q-114` 는 발주처가 자기 기업이라 열거가 안 열린다 (제동 ③ 의 실문항 확인)."""
    from argus.agent.budget import Budget

    row = _rows()["Q-114"]
    r = route(row["question"])
    if r.counterparty is None:
        pytest.skip("counterparty_graph.json 없음")
    _, trace = pipeline._gather_evidence(r, row["question"], Budget())
    assert not any("strat=enumerate" in line for line in trace)


# --- v4 R1-11 ③ 기각, 하중 상수가 `depth` 가 아니다 ----------------------------

def test_the_evidence_cap_binds_before_the_group_depth():
    """`R1-11` ③ 의 기각을 고정한다. `depth < 1` 을 올려도 집합이 안 바뀐다.

    `R1-6` 보고서는 `Q-117` 미회수 3건의 원인을 `_enumerate_events` 의 `depth < 1`
    하드코딩(*"그룹당 1건"*)으로 짚었다. 실측하면 **하중 상수는 그것이 아니다**:
    계약상대 그룹이 11개인데 담을 칸(`MAX_ENUM_EVIDENCE`)이 5개뿐이라 `depth 0`
    한 바퀴에서 이미 상한이 찬다. `depth` 를 올려도 두 번째 바퀴가 **아예 안 돈다.**

    셋을 회수하려면 `MAX_EVIDENCE`(10)를 올려야 하고 그것은 `A16` 이라
    `R0-3`(`dac2bf0`)이 이미 기각했다. 사유는 `_enumerate_events` 독스트링에 있다.
    """
    from argus.corpus.counterparty import canonical

    out = tools.execute("query_events",
                        {"corp_name": "한전기술", "is_latest": True,
                         "doc_subtype": "단일판매공급계약체결",
                         "limit": pipeline.ENUM_EVENT_POOL})
    if "error" in out or not (out.get("events") or ()):
        pytest.skip("artifacts/exchange_records.jsonl 없음")

    groups: dict[str, list[dict]] = {}
    for event in out["events"]:
        groups.setdefault(canonical(event.get("counterparty") or ""), []).append(event)

    # 이 부등식이 기각의 전부다. 뒤집히면(그룹 < 상한) depth 가 하중이 되고
    #    그때는 `R1-11` ③ 을 다시 재야 한다.
    assert len(groups) > pipeline.MAX_ENUM_EVIDENCE, (
        f"그룹 {len(groups)} <= 상한 {pipeline.MAX_ENUM_EVIDENCE} — depth 가 하중이 됐다")

    def taken_at(max_depth: int) -> list[str]:
        order = sorted(groups, key=lambda key: (-len(groups[key]), key))
        taken: list[str] = []
        depth = 0
        while len(taken) < pipeline.MAX_ENUM_EVIDENCE and depth < max_depth:
            for key in order:
                if len(taken) >= pipeline.MAX_ENUM_EVIDENCE:
                    break
                if depth < len(groups[key]):
                    taken.append(str(groups[key][depth].get("rcept_no")))
            depth += 1
        return taken

    assert taken_at(1) == taken_at(2) == taken_at(3), "depth 를 올렸더니 집합이 바뀌었다"
    assert len(taken_at(1)) == pipeline.MAX_ENUM_EVIDENCE

    # 미회수 3건이 어디 있는지도 못 박는다. 하나는 **1건짜리 그룹**이라 depth 와
    # 무관하고(순위가 상한 밖), 둘은 같은 그룹의 2·4번째다.
    order = sorted(groups, key=lambda key: (-len(groups[key]), key))
    assert len(groups["한국남부발전"]) == 1
    assert order.index("한국남부발전") >= pipeline.MAX_ENUM_EVIDENCE
    doosan = [str(e.get("rcept_no")) for e in sorted(
        groups["두산에너빌리티"],
        key=lambda e: (str(e.get("rcept_dt", "")), e.get("rcept_no", "")), reverse=True)]
    assert doosan.index("20251224800396") == 1
    assert doosan.index("20230330800844") == 3


def test_the_enumeration_hop_fires_on_exactly_one_question_in_the_corpus():
    """위 기각이 `Q-117` 하나에만 걸린 판정이 아니라는 것을 못 박는다.

    열거 hop 이 발화하는 문항은 전체 88 + 밸류체인 12 = 100문항 중 **하나뿐**이다
    (저장 응답의 질문만 읽는다. LLM 0회 · `evalset/` 미접촉). 이 수가 늘면 그때는
    그룹 수가 상한보다 작은 문항이 생겼을 수 있으므로 `depth` 를 다시 재야 한다.
    """
    import json

    root = Path(__file__).resolve().parents[1] / "artifacts/ablation/v3"
    questions: dict[str, str] = {}
    for arm in ("A8", "vc/A8"):
        directory = root / arm / "responses"
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("Q-*.json")):
            payload = json.loads(path.read_text(encoding="utf-8"))["response"]
            questions.setdefault(payload["question_id"], payload["question"])
    if len(questions) < 100:
        pytest.skip("v3 저장 응답이 없다")

    # 문서군을 `"exchange"` 로 **박지 않는다**. 그러면 제동 ① 을 건너뛴다.
    #    호출부(`_gather_evidence`)가 넘기는 것은 라우터가 정한 `r.group` 이고,
    #    박아 두면 `Q-076`(정기공시)처럼 어휘만 걸리는 문항이 발화한 것처럼 보인다.
    fired = []
    for qid, question in sorted(questions.items()):
        r = route(question)
        if pipeline._wants_enumeration(r, question, r.group):
            fired.append(qid)
    assert fired == ["Q-117"], fired


# --- Counterparty branch line: universe vs non-universe split --------------
# `Q-117`'s failure is not retrieval: `partner_profile` already carries all
# eleven counterparties into the evidence as one line and the answer is still
# wrong, because the model splits the set on the wrong axis. The two golden
# branches split deterministically on one predicate already available in this
# repository: whether the counterparty is one of the 70 companies the
# evaluation covers.
# See docs/design-rationale.md#counterparty-branch

def _groups(*names_and_counts: tuple[str, int]) -> dict[str, list[dict]]:
    """Build a `groups` dict shaped like `_enumerate_events` produces: name ->
    one dummy record per count. Only the key and its length matter to
    `counterparty_branch`, never the record contents."""
    return {name: [{"rcept_no": f"{name}-{i}"} for i in range(n)]
            for name, n in names_and_counts}


#: `Q-117`'s real eleven counterparties (measured), split by universe
#: membership: `두산에너빌리티` alone is inside, the other ten are outside
#: (five state utilities, one overseas name, four other listed or unlisted
#: names, including `삼성물산` which is not among the 70 companies this
#: evaluation covers even though it is a large listed company).
_Q117_COUNTERPARTIES = (
    ("한국수력원자력", 11), ("두산에너빌리티", 4), ("한국동서발전", 3),
    ("ptpln", 2), ("한주", 1), ("삼성물산", 1), ("한국중부발전", 1),
    ("한국남동발전", 1), ("넥서스그린에너지", 1), ("한국남부발전", 1),
    ("완도금일해상풍력", 1))


def test_counterparty_branch_splits_universe_from_non_universe():
    """The predicate is still universe membership, but the line names the two
    groups in industry terms and carries none of our internal vocabulary,
    the wording that leaked verbatim into the `Q-117` answer.
    See docs/design-rationale.md#counterparty-branch
    """
    line = aggregate.counterparty_branch(_groups(*_Q117_COUNTERPARTIES))
    assert line
    assert "발주처 10곳" in line
    assert "공급사 재수주 1곳" in line
    assert "두산에너빌리티" in line
    for outsider in ("한국수력원자력", "한국동서발전", "ptpln", "한주", "삼성물산",
                     "한국중부발전", "한국남동발전", "넥서스그린에너지",
                     "한국남부발전", "완도금일해상풍력"):
        assert outsider in line
    # No receipt numbers and no per-company filing counts, the same rule as
    # `_counterparty_note` (`counterparty-note-no-counts`).
    assert not re.search(r"\d{14}", line)
    assert not re.search(r"\d+건", line)
    # The internal coverage-list vocabulary must not reach the model: this is
    # exactly what it copied verbatim into its `Q-117` answer.
    for leaked in ("70개사", "평가 대상", "유니버스", "universe"):
        assert leaked not in line


def test_counterparty_branch_is_silent_when_one_side_is_empty():
    """A single branch is not a split, and asserting one would be a
    confident wrong statement."""
    all_inside = _groups(("두산에너빌리티", 4), ("삼성전자", 2))     # both universe
    assert aggregate.counterparty_branch(all_inside) == ""
    all_outside = _groups(("한국수력원자력", 11), ("한국동서발전", 3))  # neither universe
    assert aggregate.counterparty_branch(all_outside) == ""
    assert aggregate.counterparty_branch({}) == ""


#: The same eleven counterparties, but with the records the line now reads:
#: a contract title and an amount. Golden points 2, 3 and 4 of `Q-117` are
#: contract names and amounts, which the name-only line could not carry.
def _rich_groups() -> dict[str, list[dict]]:
    def _row(no, title, amount):
        return {"rcept_no": no,
                "title": f"단일판매ㆍ공급계약체결 — {title}",
                "amount": amount}

    groups = {name: [{"rcept_no": f"{name}-{i}"} for i in range(n)]
              for name, n in _Q117_COUNTERPARTIES}
    groups["두산에너빌리티"] = [
        _row("20230330800844", "신한울원자력발전소 3,4호기 원자로계통 설계용역",
             181_608_900_000),
        _row("20251224800396", "체코 두코바니 5&6호기 원자로계통설계용역",
             373_449_426_066),
    ]
    groups["한국수력원자력"] = [
        _row("20251212801320", "체코 두코바니 5&6호기 종합설계용역",
             1_250_850_298_678),
    ]
    return groups


def test_the_branch_line_carries_contract_names_and_amounts():
    """Golden points 2 and 3: the re-order branch's contracts are all
    원자로계통설계, and 신한울 3,4호기 is 181,608,900,000원."""
    line = aggregate.counterparty_branch(_rich_groups())
    assert "원자로계통설계" in line or "원자로계통 설계" in line
    assert "181,608,900,000" in line


def test_the_re_order_branch_carries_two_contracts():
    """Golden point 4 needs both Dukovany contracts visible at once, so the
    supplier branch carries two while the orderer branch carries one each."""
    line = aggregate.counterparty_branch(_rich_groups())
    assert "신한울" in line and "두코바니" in line


def test_the_orderer_branch_carries_one_contract_each():
    line = aggregate.counterparty_branch(_rich_groups())
    assert "1,250,850,298,678" in line


#: Three priced records whose titles share no token with each other, with the
#: 두코바니/신한울 fixtures above, or with any other string in this file. A
#: prior version of the tests below asserted "신한울" and "두코바니" both
#: appear in the line, which passed even when the branch caps were made equal
#: or removed entirely: 두코바니 reaches the line through 한국수력원자력's own
#: one contract regardless of what 두산에너빌리티's cap allows through. These
#: unique tokens close that gap — a title's presence or absence can only be
#: explained by the cap that gates it, never by another counterparty's
#: contract supplying the same word by coincidence.
def _distinct_priced_records() -> list[dict]:
    return [
        {"rcept_no": "distinct-1",
         "title": "단일판매ㆍ공급계약체결 — 톈진프로젝트설비공급", "amount": 111_111_111},
        {"rcept_no": "distinct-2",
         "title": "단일판매ㆍ공급계약체결 — 오슬로항만증설공사", "amount": 222_222_222},
        {"rcept_no": "distinct-3",
         "title": "단일판매ㆍ공급계약체결 — 나이로비수처리설비", "amount": 333_333_333},
    ]


def test_the_supplier_branch_truncates_a_third_contract():
    """MAX_BRANCH_CONTRACTS_SUPPLIER caps the supplier branch at two. The
    sibling (orderer) counterparty here is bare, so every priced title in the
    line comes from the supplier side alone: the third record's absence can
    only be explained by the cap. Widening the cap to three, or removing the
    `[:limit]` slice, would make 나이로비수처리설비 appear and this test would
    fail — which is the point of the assertion."""
    groups = {"두산에너빌리티": _distinct_priced_records(),
              "한국수력원자력": [{"rcept_no": "hk-0"}]}          # bare: contributes nothing
    line = aggregate.counterparty_branch(groups)
    assert "톈진프로젝트설비공급" in line and "111,111,111" in line
    assert "오슬로항만증설공사" in line and "222,222,222" in line
    assert "나이로비수처리설비" not in line and "333,333,333" not in line


def test_the_orderer_branch_truncates_a_second_contract():
    """MAX_BRANCH_CONTRACTS_ORDERER caps the orderer branch at one. The
    sibling (supplier) counterparty here is bare, so the second record's
    absence can only be explained by this branch's own cap, not by anything
    the supplier side contributes."""
    groups = {"한국수력원자력": _distinct_priced_records()[:2],
              "두산에너빌리티": [{"rcept_no": "de-0"}]}          # bare: contributes nothing
    line = aggregate.counterparty_branch(groups)
    assert "톈진프로젝트설비공급" in line and "111,111,111" in line
    assert "오슬로항만증설공사" not in line and "222,222,222" not in line


def test_the_two_branch_caps_are_not_equal():
    """Pins the asymmetry directly, rather than each cap in isolation. The
    identical three-record list sits on both a universe and a non-universe
    counterparty at once, so the second record can only reach the rendered
    line through the supplier branch (cap 2) — the orderer branch (cap 1)
    never gets past its first record. It must therefore appear exactly once
    in the whole line: zero times if both caps collapsed to 1, twice if both
    widened to 2. Either equalisation breaks this count, which is what makes
    this assertion a test of the asymmetry and not of either cap alone."""
    records = _distinct_priced_records()
    groups = {"두산에너빌리티": records, "한국수력원자력": records}
    line = aggregate.counterparty_branch(groups)
    assert line.count("오슬로항만증설공사") == 1
    assert line.count("222,222,222") == 1


def test_a_counterparty_with_no_amount_is_named_only():
    """Nine of the eleven groups hold bare records in this fixture. They must
    still be named, because golden point 1 is the list of orderers."""
    line = aggregate.counterparty_branch(_rich_groups())
    for name in ("한국중부발전", "한국남부발전", "한국동서발전", "한국남동발전"):
        assert name in line


def test_the_enriched_line_still_carries_no_internal_vocabulary():
    """The leak this line already caused once. Contract names and amounts are
    source values and are meant to be copied; our predicate is not."""
    line = aggregate.counterparty_branch(_rich_groups())
    for leaked in ("70개사", "평가 대상", "유니버스", "universe"):
        assert leaked not in line


def test_the_enriched_line_still_carries_no_receipt_numbers():
    line = aggregate.counterparty_branch(_rich_groups())
    assert not re.search(r"\d{14}", line)
    assert not re.search(r"\d+건", line)


def test_asks_structure_predicate():
    assert pipeline._asks_structure(
        "한전기술이 공시한 계약의 발주 구조가 두 갈래로 나뉜다") is True
    assert pipeline._asks_structure("한전기술이 공시한 계약상대는 누구인가?") is False


def test_asks_structure_is_narrower_than_the_enumeration_hint():
    """`_ENUMERATION_HINT` gates the whole hop broadly (`나뉘` alone opens
    it); the branch line makes a stronger claim, a named two-way split, and
    needs its own narrower vocabulary check so the two do not drift apart.
    """
    q = "한전기술이 공시한 계약의 계약상대가 어떻게 나뉘어 있는가"
    assert pipeline._wants_enumeration(_Route(["한전기술"]), q, "exchange") is True
    assert pipeline._asks_structure(q) is False


@pytest.mark.skipif(not _VC.exists(), reason="evalset/valuechain.jsonl 없음")
def test_q117_branch_line_reaches_the_head_of_the_evidence_under_a45():
    """`A45` wires the flag in. The line must reach the head of the evidence,
    the same position as `_roster_line` and `partner_profile`
    (`line-position`, `roster-position`)."""
    from argus import ablation
    from argus.agent.budget import Budget

    row = _rows()["Q-117"]
    r = route(row["question"])
    with ablation.use("A45"):
        evidence, trace = pipeline._gather_evidence(r, row["question"], Budget())
    if not evidence:
        pytest.skip("artifacts/exchange_records.jsonl 없음")
    assert any("counterparty_branch" in line for line in trace)
    head = evidence.split("\n\n## ", 1)[0]
    assert "두산에너빌리티" in head
    assert "발주처" in head and "공급사 재수주" in head
    assert not re.search(r"\d{14}", head)
    for leaked in ("70개사", "평가 대상", "유니버스", "universe"):
        assert leaked not in head


@pytest.mark.skipif(not _VC.exists(), reason="evalset/valuechain.jsonl 없음")
def test_q117_branch_line_is_absent_under_the_untouched_default_arm():
    """The new flag defaults to False. An untouched operational arm (the
    module default, not `A45`) behaves exactly as before."""
    from argus import ablation
    from argus.agent.budget import Budget

    assert ablation.current().counterparty_branch is False
    row = _rows()["Q-117"]
    r = route(row["question"])
    evidence, trace = pipeline._gather_evidence(r, row["question"], Budget())
    if not evidence:
        pytest.skip("artifacts/exchange_records.jsonl 없음")
    assert not any("counterparty_branch" in line for line in trace)
    # `공급사 재수주` is a phrase distinctive to this line's own wording, unlike
    # `발주처`, which also appears in `_counterparty_note`'s unrelated line.
    assert "공급사 재수주" not in evidence
