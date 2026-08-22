"""`A29a`, 결정적 집계를 근거에 한 줄로 싣는다.

아래 일곱 값은 R0(2026-08-16)이 `artifacts/*_records.jsonl` 의 `amount` 로 **직접
검산한 것**이다. 하나라도 어긋나면 구현이 틀렸거나 코퍼스가 움직인 것이다. 둘 다
그냥 지나가면 안 되는 사건이라 여기서 멈춘다.

일곱 건 다 **필터가 붙는다.** 필터를 안 걸면 `Q-115` 는 5,230,496,802,000(3배) ·
`Q-107` 은 7,264,374,092,359 가 나온다. 틀린 집계를 확신 있게 박아 주는 것은 지금보다
나쁘다. 모델은 그 줄을 의심하지 않는다.
"""
from __future__ import annotations

import io
import json
import pathlib

import pytest

from argus import ablation
from argus.agent import aggregate, router, tools

EVALSET = pathlib.Path(__file__).resolve().parents[1] / "evalset"


def _recs(*rcepts):
    return [tools._record_of("exchange_" + r) for r in rcepts]


# (문항, 질문, 근거 접수번호, 골든), 근거는 A28/A33 이 실제로 실은 것이다.
CASES = [
    ("Q-018", "HD현대중공업이 2023년 7월에 공시한 FPU 1기 공급계약은 정정공시로 "
              "계약금액이 얼마나 증가했는가?",
     ("20230703800173", "20241223800052"), 275_900_000_000),
    ("Q-097", "현대글로비스가 2023년 10월에 공시한 10,800RT급 LNG DF 자동차선 12척 "
              "선대 확보 투자에서 1척당 평균 투자금액은 얼마인가?",
     ("20231026800363",), 207_684_631_210),
    ("Q-109", "한국수력원자력이 발주한 신한울 원자력발전소 3·4호기 주설비공사는 "
              "2023년 12월 11일 두 개 상장사가 각각 공시했다. 두 공시의 계약금액 "
              "합계는 얼마인가?",
     ("20231211800611", "20231211800339"), 2_807_599_069_800),
    ("Q-034", "두산퓨얼셀이 2025년 3~4월에 공시한 계약 해지 3건의 계약상대와 해지 "
              "금액을 정리하고 해지 금액 합계를 알려줘.",
     ("20250331802527", "20250402800768", "20250402800874"), 817_900_000_000),
    ("Q-115", "현대자동차를 계약상대로 하는 공급계약을 현대글로비스와 현대오토에버가 "
              "공시했다. 이 중 2025년에 공시된 계약금액의 합계는 얼마인가?",
     ("20250117800487", "20251128800125", "20241231800103", "20241203800532"),
     1_759_596_802_000),
    ("Q-113", "삼성전자가 발주한 평택 P4 Ph4(상동) 마감공사를 삼성E&A와 삼성중공업이 "
              "각각 공시했고 두 회사 모두 2025년 9월 24일 정정공시를 냈다. 두 회사 "
              "정정 후 계약금액 합계는 최초 공시 합계보다 얼마나 늘었는가?",
     ("20250718800275", "20250924800260", "20250721800070", "20250924800262"),
     526_570_000_000),
]


@pytest.mark.parametrize("qid,question,rcepts,expected", CASES,
                         ids=[c[0] for c in CASES])
def test_the_line_carries_the_golden_value(qid, question, rcepts, expected):
    line = aggregate.line(_recs(*rcepts), question, router.route(question))
    assert line, f"{qid}: 아무것도 안 냈다"
    assert f"{expected:,}" in line, f"{qid}: {line!r}"


#: **`Q-107` 은 겨냥에서 뺐다. 기각도 결과다.**
#:
#: R0 검산은 *"한수원 상대 3건만 더하면 골든"* 이었고 그것은 지금도 맞다. 그런데 살아
#: 있는 경로가 보는 것은 골든 3건이 아니라 **근거 전량**이고, 거기에는 같은 발주처의
#: **신한울** 계약이 함께 실린다. 다 더하면 8,018,130,248,423 이 나온다
#: (골든 6,890,924,666,293). 저장 답변 전수 검사(LLM 0회 · 0원)가 이것을 잡았다.
#:
#: 질문이 좁히는 축은 **사업**(체코 두코바니)인데 우리에겐 그 축이 없다. 자유문 제목
#: 매칭을 넣는 안은 세우지 않았다. 못박히지 않은 집계를 확신 있게 박아 주는 것이
#: 지금보다 나쁘다는 것이 이 모듈의 계약이다. **못박을 수 없으면 침묵한다.**
Q107 = ("체코 두코바니 5·6호기 원자력발전소 사업과 관련해 한국수력원자력을 "
        "계약상대로 하여 공시된 단일판매·공급계약의 계약금액 합계는 얼마인가?",
        ("20251216801147", "20251216801163", "20251212801320", "20251224800396"))


def test_a_counterparty_filter_alone_is_not_a_pin():
    """허브는 계약을 여럿 가진다. 계약상대만으로는 집합이 안 정해진다.

    Pinned to `A43` explicitly (`project_scope` off), not the ambient
    operational default: the v8 promotion (2026-08-20, `A43` -> `A49`) turns
    `project_scope` on there, and `project_scope` is exactly the axis that
    pins this record set the counterparty filter alone cannot -- see
    `tests/test_project_scope.py::test_the_total_matches_the_golden_answer`,
    which measures this same question with the axis on and gets the golden
    total rather than silence.
    """
    question, rcepts = Q107
    with ablation.use(ablation.A43):
        assert aggregate.line(_recs(*rcepts), question, router.route(question)) == ""
        assert aggregate._total(_recs(*rcepts), question, router.route(question)) is None


def test_the_filter_is_named_in_the_line():
    """무엇을 걸러 더한 값인지 밝히지 않으면 모델이 전량 합으로 읽는다."""
    _, question, rcepts, _ = CASES[4]                    # Q-115
    line = aggregate.line(_recs(*rcepts), question, router.route(question))
    assert "2025년 공시분" in line


def test_the_unfiltered_sum_is_never_emitted():
    """필터가 빠지면 나오는 값. 이 수가 줄에 있으면 필터가 안 걸린 것이다."""
    _, question, rcepts, _ = CASES[4]
    assert f"{5_230_496_802_000:,}" not in aggregate.line(
        _recs(*rcepts), question, router.route(question))


def test_a_decrease_is_reported_as_a_magnitude():
    """`Q-061`(지금 정답)의 345,173,200,000 을 **−345,173,200,000** 으로 갈아
    끼울 뻔했다. 질문이 *"얼마나 감소했는가"* 이므로 답은 크기이지 부호가 아니다."""
    recs = _recs("20241223800052", "20230703800173")
    total = aggregate._total(recs, "계약금액이 얼마나 감소했는가?", None)
    assert total == 275_900_000_000


def test_silence_when_the_question_asks_for_no_aggregate():
    recs = _recs("20231211800611", "20231211800339")
    assert aggregate.line(recs, "이 계약들에 대해 설명해줘.", None) == ""


def test_records_without_amount_never_enter_the_sum():
    recs = [{"rcept_no": "1", "amount": 100, "rcept_dt": "20250101", "related": []},
            {"rcept_no": "2", "amount": None, "rcept_dt": "20250102", "related": []}]
    picked, _ = aggregate.select(recs, "합계는 얼마인가?", None)
    assert [r["rcept_no"] for r in picked] == ["1"]


def test_a_sum_of_one_record_is_not_a_sum():
    recs = _recs("20231211800611")
    assert aggregate.line(recs, "계약금액 합계는 얼마인가?", None) == ""


def test_chain_members_are_not_double_counted_in_a_sum():
    """같은 체인의 원공시와 정정본을 함께 더하면 조용히 두 배가 된다.

    묶고 나면 2건이므로 *"두 건"* 으로 못박아야 발화한다 (아래 침묵 테스트 참조).
    """
    recs = _recs("20230703800173", "20241223800052", "20231211800339")
    line = aggregate.line(recs, "두 건의 계약금액 합계는 얼마인가?", None)
    assert f"{1_566_300_000_000:,}" not in line, "원공시가 합에 들어갔다"
    assert f"{1_842_200_000_000 + 1_715_754_987_100:,}" in line


def test_a_sum_is_silent_when_the_target_set_is_not_pinned():
    """실측이 요구한 게이트다. 근거 전량을 더했더니 `Q-109` 에서 모델의 오기
    2,807,598,070,800 을 **4,349,583,514,756** 으로 갈아 끼웠다 (골든 …599,069,800).
    질문은 *"2023년 12월 11일 두 개 상장사"* 인데 근거에 다른 계약이 섞여 있었다.

    건수도 필터도 없으면 **침묵**이 옳다.
    """
    recs = _recs("20230703800173", "20241223800052", "20231211800339")
    assert aggregate.line(recs, "계약금액 합계는 얼마인가?", None) == ""


def test_a_stated_count_that_does_not_match_keeps_us_silent():
    recs = _recs("20230703800173", "20241223800052", "20231211800339")
    assert aggregate.line(recs, "다섯 건의 계약금액 합계는 얼마인가?", None) == ""


def test_a_place_name_is_not_read_as_a_count():
    """첫 판의 `[두세네다섯여섯]` 은 문자 클래스라 *"체코 두코바니"* 의 `두` 가
    걸려 `Q-107` 이 '2건' 으로 읽혔다 (실측)."""
    assert aggregate._stated_count("체코 두코바니 5·6호기 계약금액 합계는?") is None
    assert aggregate._stated_count("계약 해지 3건의 합계") == 3
    assert aggregate._stated_count("두 개 상장사가 각각 공시했다") == 2


def test_the_quotient_stays_silent_when_the_question_has_no_unit_count():
    recs = _recs("20231026800363")
    assert aggregate.line(recs, "1척당 평균 투자금액은 얼마인가?", None) == ""


def test_the_operation_is_read_before_the_sum():
    """`얼마나 증가` 가 `합계` 로 새면 `Q-018` 이 두 금액의 합을 받는다."""
    assert aggregate._op("정정공시로 계약금액이 얼마나 증가했는가?") == "차"
    assert aggregate._op("두 공시의 계약금액 합계는 얼마인가?") == "합"
    assert aggregate._op("1척당 평균 투자금액은 얼마인가?") == "몫"
    assert aggregate._op("어떤 계약을 체결했는가?") == ""


def test_a_mixed_sum_and_comparison_hint_routes_to_comparison_and_stays_silent():
    """`_OPS` tests comparison before sum and difference deliberately, so a
    question carrying both a comparison hint and a sum hint routes to
    comparison. With `compare_line` off (the default), that means no line
    and no computed total at all, not even the sum the question also names
    in words.

    Zero of the 112 evaluation questions carry this combination, but the
    deployed server takes arbitrary questions. This pins the current
    behaviour so the narrowing cannot drift further unnoticed. The ordering
    itself is deliberate and is not changed here.
    """
    question = "두 계약의 합계는 B사 단독보다 얼마나 큰가"
    assert aggregate._op(question) == "비교"
    _, _, rcepts, _ = CASES[2]                      # any real two-record fixture
    recs = _recs(*rcepts)
    assert aggregate.line(recs, question, None) == ""
    assert aggregate._total(recs, question, None) is None


@pytest.mark.parametrize("qid,question,rcepts,expected", CASES,
                         ids=[c[0] for c in CASES])
def test_line_and_total_never_diverge(qid, question, rcepts, expected):
    """규칙이 두 곳에 있으면 갈린다. 이 저장소가 열 번 겪은 실패 양식이다.
    `line()` 이 싣는 값과 `substitute()` 가 쓰는 값은 반드시 같아야 한다."""
    recs, route = _recs(*rcepts), router.route(question)
    total = aggregate._total(recs, question, route)
    if total is None:                       # 몫 문항은 `_total` 대상이 아니다
        assert aggregate._op(question) == "몫"
        return
    assert total == expected
    assert f"{total:,}" in aggregate.line(recs, question, route)


def test_substitute_replaces_the_single_wrong_total():
    """`Q-109` 실측, 모델이 2,807,598,070,800 이라 썼다 (골든 …599,069,800)."""
    _, question, rcepts, expected = CASES[2]
    recs, route = _recs(*rcepts), router.route(question)
    answer = ("현대건설의 계약금액: 1,715,754,987,100원\n"
              "두산에너빌리티의 계약금액: 1,091,844,082,700원\n"
              "따라서 합계는 2,807,598,070,800원입니다.")
    fixed, why = aggregate.substitute(answer, recs, question, route)
    assert f"{expected:,}" in fixed and why
    assert "2,807,598,070,800" not in fixed
    assert "1,715,754,987,100" in fixed, "근거에 있는 항은 건드리면 안 된다"


def test_substitution_never_rewrites_our_own_footnote():
    """**각주는 검사기가 무엇을 봤는가의 기록이다.** 뒤에서 갈아 끼우면 창작이 된다.

    실측(2026-08-16): 치환이 `answer.replace()` 를 통째로 걸어서 각주가
    *"답변 526,570,000,000원 vs 재계산 …"* 이라 말했다. 검사기는 그 수를 본 적이 없다.
    """
    _, question, rcepts, expected = CASES[2]
    recs, route = _recs(*rcepts), router.route(question)
    answer = ("따라서 합계는 2,807,598,070,800원입니다.\n\n"
              "(주의: 자동 검증에서 결함이 발견되었습니다 — "
              "amount: 답변 2,807,598,070,800원 vs 재계산 1,715,754,987,100; "
              "missing_corp: 비교 질의인데 근거에 한국수력원자력 이(가) 없다)")
    fixed, why = aggregate.substitute(answer, recs, question, route)
    assert why and f"{expected:,}" in fixed
    # 치환한 값을 말하던 항목은 사라지고, 무관한 항목은 남는다.
    assert "amount: 답변" not in fixed, "고친 결함이 미해결로 남았다"
    assert "missing_corp" in fixed, "무관한 결함까지 지웠다"
    assert "vs 재계산" not in fixed.split("missing_corp")[0]


def test_the_footnote_disappears_when_every_item_was_repaired():
    _, question, rcepts, expected = CASES[2]
    recs, route = _recs(*rcepts), router.route(question)
    answer = ("합계는 2,807,598,070,800원입니다.\n\n"
              "(주의: 자동 검증에서 결함이 발견되었습니다 — "
              "amount: 답변 2,807,598,070,800원 vs 재계산 1,715,754,987,100)")
    fixed, _why = aggregate.substitute(answer, recs, question, route)
    assert "주의" not in fixed
    assert f"{expected:,}" in fixed


def test_substitute_is_silent_when_the_answer_is_already_right():
    _, question, rcepts, expected = CASES[2]
    recs, route = _recs(*rcepts), router.route(question)
    answer = f"합계는 {expected:,}원입니다."
    assert aggregate.substitute(answer, recs, question, route) == (answer, "")


def test_substitute_is_silent_when_two_candidates_compete():
    """어느 것이 합계인지 모르는 채로 고르면 멀쩡한 수를 망가뜨린다."""
    _, question, rcepts, _ = CASES[2]
    recs, route = _recs(*rcepts), router.route(question)
    answer = "합계는 2,807,598,070,800원 또는 2,807,500,000,000원입니다."
    assert aggregate.substitute(answer, recs, question, route)[1] == ""


def test_substitute_never_touches_a_number_of_another_magnitude():
    _, question, rcepts, _ = CASES[2]
    recs, route = _recs(*rcepts), router.route(question)
    answer = "합계는 2,807,598,070,800원이고 착수금은 12,000,000원이다."
    fixed, _why = aggregate.substitute(answer, recs, question, route)
    assert "12,000,000" in fixed


def test_the_gate_stays_narrow_across_the_whole_evalset():
    """겨냥은 7건이다. 발화가 넓어지면 근거가 안 바뀌던 문항까지 흔들린다."""
    fired = []
    for name in ("questions.jsonl", "valuechain.jsonl", "regression.jsonl",
                 "safety.jsonl"):
        path = EVALSET / name
        if not path.exists():
            continue
        for row in (json.loads(l) for l in io.open(path, encoding="utf-8")):
            if aggregate._op(row["question"]):
                fired.append(row["question_id"])
    assert len(fired) <= 30, f"발화 {len(fired)}문항 — 너무 넓다: {fired}"
    for qid, *_ in CASES:
        assert qid in fired, f"{qid} 를 놓쳤다"


# --- `delta_totals` — 차 줄이 두 중간 합계도 싣는다 -----------------------------
#
# `Q-113` 은 추론 논리성 2/3 인데 두 회차의 사유가 다르고, **둘 다 같은 결핍**에서 나온다 —
# 차 줄이 갈래별 차와 총 차만 주고 「정정 후 합계」·「정정 전 합계」를 안 줬다.
#
#     r1  1,828,750,000,000 - 526,570,000,000 = 526,570,000,000   → equation
#         (정정 전 합계 1,302,180,000,000 을 몰라 총 차를 피감수 자리에 다시 썼다)
#     r3  499,950,000,000 + 1,328,800,000,000 = 1,738,750,000,000  → provenance
#         (참값 1,828,750,000,000 을 스스로 더하다 틀렸고 근거에 없는 수가 됐다)

Q113 = ("삼성전자가 발주한 평택 P4 Ph4(상동) 마감공사를 삼성E&A와 삼성중공업이 각각 "
        "공시했고 두 회사 모두 2025년 9월 24일 정정공시를 냈다. 두 회사 정정 후 계약금액 "
        "합계는 최초 공시 합계보다 얼마나 늘었는가?")
#: 두 갈래 각각의 원본·정정본. 🔴 `20250721800070` 이 삼성중공업의 원본이다 — 첫 판은
#: 이 자리에 없는 접수번호를 적어 갈래가 하나로 줄어 줄이 아예 안 났다.
Q113_RCEPTS = ("20250718800275", "20250924800260",   # 삼성E&A  909,590 → 1,328,800
               "20250721800070", "20250924800262")   # 삼성중공업 392,590 →   499,950


def _q113_line(arm):
    with ablation.use(arm):
        return aggregate.line(_recs(*Q113_RCEPTS), Q113, None)


def test_delta_totals_두_중간_합계를_싣는다():
    out = _q113_line("A80")
    assert "정정 후 합계 1,828,750,000,000 − 정정 전 합계 1,302,180,000,000" in out
    #: 🔴 답변이 스스로 더하다 틀린 값은 실리지 않는다.
    assert "1,738,750,000,000" not in out


def test_delta_totals_총_차는_안_바뀐다():
    """골든은 `526,570,000,000` 이다. 덧붙이는 것이라 종전 본문이 그대로다."""
    before, after = _q113_line("A79"), _q113_line("A80")
    assert "합계 **526,570,000,000원**" in before
    assert "합계 **526,570,000,000원**" in after
    assert before.split(" · 합계 ")[0] == after.split(" · 합계 ")[0]


def test_delta_totals_두_합계의_차가_총_차와_같다():
    """구성상 반드시 같다. 안 같으면 갈래를 잘못 모은 것이다."""
    out = _q113_line("A80")
    after = int(out.split("정정 후 합계 ")[1].split(" ")[0].replace(",", ""))
    before = int(out.split("정정 전 합계 ")[1].split(" ")[0].replace(",", ""))
    total = int(out.split("합계 **")[1].split("원")[0].replace(",", ""))
    assert after - before == total


def test_delta_totals_스위치가_꺼지면_안_실린다():
    assert "정정 후 합계" not in _q113_line("A79")
    assert "정정 후 합계" not in _q113_line("A65")
