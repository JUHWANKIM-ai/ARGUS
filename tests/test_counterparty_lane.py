"""발주처 레인 정렬 (`_partner_order`), v3 R2 ①.

`T5-a` 가 밸류체인 미회수 18건 중 **8건**을 "허브 레인 절단"((iii′))으로 갈랐다.
기전은 `_partner_order` 가 **질문을 안 보는 것**이었다. 최신본 우선 + 도착순이라
같은 허브·같은 파트너면 어떤 질문이 와도 순서가 같았다.

**상한을 올리는 것은 처방이 아니다** (`T5-a` §4 실측: 8건을 다 회수하려면 한 문항에
발주처 근거 57블록 = 현행 6칸의 9.5배). 그래서 후보 집합도 `MAX_COUNTERPARTY_EVIDENCE`
도 `_partner_quota` 도 건드리지 않고 **같은 칸에 무엇이 들어가는가**만 바꾼다.

**리랭커가 아니다.** `A3` 는 겨냥 0 으로 최종 기각됐다(`05 §E-4b`). 여기는 LLM 0회 ·
네트워크 0회 · 크레딧 0원이고, 점수는 레인 안에서 계산되는 BM25 다.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from argus.agent import pipeline

_ROOT = Path(__file__).resolve().parents[1]
_RECORDS = _ROOT / "evalset/valuechain.jsonl"


def _record(rcept_no: str, text: str, is_latest: bool = True) -> dict:
    return {"rcept_no": rcept_no, "doc_id": f"exchange_{rcept_no}",
            "corp_name": "테스트건설", "is_latest": is_latest,
            "markdown": f"## [공급계약 체결] 테스트건설 — {text}\n"
                        f"- 공시일: 2025-01-01 (접수번호 {rcept_no})\n"
                        f"- 계약상대: 한국수력원자력(주)\n"}


def test_empty_question_keeps_the_old_order_byte_for_byte():
    """질문이 없으면 종전 동작 그대로, 최신본 우선 + 도착순.

    호출부가 질문을 안 넘기는 경로가 새로 생겨도 **조용히 성능이 바뀌지 않는다**.
    이 저장소가 최악으로 보는 것이 그 조용한 Δ 다.
    """
    records = [_record("1", "체코 두코바니 원전", is_latest=False),
               _record("2", "평택 반도체 공장"),
               _record("3", "기흥 연구라인")]
    assert [r["rcept_no"] for r in pipeline._partner_order(records, "")] == ["2", "3", "1"]


def test_question_relevance_reorders_within_the_lane():
    """레인 안에서 질문에 맞는 것이 앞으로 온다. 이것이 (iii′) 8건의 처방이다."""
    records = [_record("1", "평택 반도체 공장 골조공사"),
               _record("2", "기흥 연구라인 마감공사"),
               _record("3", "체코 두코바니 원자력발전소 주설비공사")]
    order = pipeline._partner_order(
        records, "체코 두코바니 원자력발전소 사업의 계약금액 합계는 얼마인가?")
    assert order[0]["rcept_no"] == "3"


def test_is_latest_still_wins_over_relevance():
    """1순위 키는 그대로 `is_latest` 다.

    대체된 공시로 파트너를 대표시키면 열거가 옛 값을 말한다. `query_events` 가
    `is_latest=True` 를 기본으로 두는 것과 같은 이유다. 적합도가 아무리 높아도
    그 규칙을 뒤집지 않는다 (뒤집으면 `Q-113` 1건을 얻고 열거의 전제를 잃는다).
    """
    records = [_record("1", "무관한 창고 신축공사"),
               _record("2", "체코 두코바니 원자력발전소 주설비공사", is_latest=False)]
    order = pipeline._partner_order(records, "체코 두코바니 원자력발전소 계약")
    assert order[0]["rcept_no"] == "1"


def test_lane_scores_survive_empty_and_singleton_lanes():
    """빈 레인·1건 레인·빈 본문에서 `BM25Okapi` 가 0으로 나눈다. 죽지 않는다."""
    assert pipeline._lane_scores("질문", []) == []
    assert pipeline._lane_scores("질문", [_record("1", "x")]) == [0.0]
    blank = [{"rcept_no": "1", "markdown": ""}, {"rcept_no": "2", "markdown": ""}]
    assert pipeline._lane_scores("질문", blank) == [0.0, 0.0]
    assert [r["rcept_no"] for r in pipeline._partner_order(blank, "질문")] == ["1", "2"]


def test_common_lane_tokens_do_not_decide_the_order():
    """레인 전원이 가진 토큰(발주처 이름·서식명)은 IDF 가 0 이라 순서를 못 바꾼다.

    이게 성립해야 '불용어 목록' 을 손으로 관리하지 않아도 된다. 모집단을 레인으로
    좁힌 것이 곧 불용어 처리다.
    """
    records = [_record("1", "창고 신축"), _record("2", "창고 신축")]
    # 두 레코드에 다 있는 낱말만으로 물으면 점수가 같아 도착순이 유지된다.
    scores = pipeline._lane_scores("한국수력원자력 계약상대 공급계약", records)
    assert scores[0] == scores[1]


@pytest.mark.skipif(not _RECORDS.exists(), reason="evalset/valuechain.jsonl 없음")
def test_q107_lane_now_reaches_the_dukovany_contract():
    """겨냥 실측, `Q-107` 이 두코바니 계약을 레인에서 건져 온다.

    `T5-a` 표 #2: `20251216801163` 은 두산에너빌리티 레인 **5/5번째**이고 quota=1 이라
    도착순으로는 영원히 도달 불가였다. 상한을 안 건드리고 순서만 바꿔 회수한다.

    **겨냥 8건 중 실측 회수는 1건이다.** 나머지는 정렬 밖에서 막힌다. 사유는
    `pipeline._partner_order` 독스트링에 실측표와 함께 적어 두었다.
    """
    from argus.agent.budget import Budget
    from argus.agent.router import route

    rows = {json.loads(line)["question_id"]: json.loads(line)
            for line in _RECORDS.open(encoding="utf-8") if line.strip()}
    row = rows["Q-107"]
    r = route(row["question"])
    if r.counterparty is None:
        pytest.skip("counterparty_graph.json 없음 (gitignore 된 산출물)")
    evidence, _ = pipeline._gather_evidence(r, row["question"], Budget())
    if not evidence:
        pytest.skip("artifacts/exchange_records.jsonl 없음")
    assert "20251216801163" in evidence


# --- 관련도 배분 (`counterparty_relevance`), v6 ------------------------------
# 레인 정렬은 파트너 **안**에서만 순서를 바꾼다. 슬롯이 파트너당 하나씩 나가므로
# 한 파트너가 관련 공시를 둘 가지고 있어도 하나만 실린다. Q-107 이 그 문항이고,
# 두코바니 계약은 두산에너빌리티 2건 · 한전기술 1건이라 넓이 배분으로는 3건이
# 절대 못 모인다. 실측은 `docs/design-rationale.md#partner-quota` 에 있다.

def test_relevance_allocation_is_off_by_default():
    """기본 arm 은 넓이 배분을 유지한다. 켜는 것은 측정으로만 정한다."""
    from argus import ablation

    assert ablation.ARMS["A42"].counterparty_relevance is False
    assert pipeline._spends_by_relevance("아무 질문", asks_roster=False) is False


def test_relevance_allocation_yields_to_a_roster_question():
    """열거를 묻는 질문에서는 켜져 있어도 넓이를 지킨다. 파트너를 빠뜨리면
    답이 곧 틀린다."""
    from argus import ablation

    with ablation.use(ablation.replace(ablation.ARMS["A42"], name="probe",
                                       counterparty_relevance=True)):
        assert pipeline._spends_by_relevance("상장사는 어디인가", asks_roster=True) is False
        assert pipeline._spends_by_relevance("합계는 얼마인가", asks_roster=False) is True
        assert pipeline._spends_by_relevance("", asks_roster=False) is False


def test_relevance_order_scores_partners_against_one_another():
    """레인 점수는 레인 안에서 계산돼 파트너끼리 비교할 수 없다. 합집합을 다시
    채점해야 순위가 의미를 가진다.

    무관한 레코드를 넉넉히 둔다. 문서 넷 중 둘이 어휘를 나누면 BM25 의 IDF 가
    정확히 0 이 되어 어떤 정렬도 관측되지 않는다.
    """
    lanes = {
        "가": [_record("1", "체코 두코바니 종합설계"), _record("2", "국내 배관 정비"),
              _record("5", "사옥 관리 용역")],
        "나": [_record("3", "체코 두코바니 원자로 공급"), _record("4", "사택 신축"),
              _record("6", "도로 포장 공사")],
    }
    order = pipeline._relevance_order("체코 두코바니", ["가", "나"], lanes)
    assert [r["rcept_no"] for r in order[:2]] == ["1", "3"]


def test_relevance_order_survives_an_empty_lane_set():
    assert pipeline._relevance_order("무엇이든", [], {}) == []
