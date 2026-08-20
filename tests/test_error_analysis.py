"""Phase H 귀속 로직 회귀. **LLM 0회**, conftest 의 block_clova_network 를 우회하지 않는다."""
import json
from collections import Counter
from pathlib import Path

import pytest

from scripts import error_analysis as ea
from scripts import validate_evalset as V


# --- 입력 가드 ---------------------------------------------------------------

def test_only_dev_split_is_loaded():
    """dev 문항 수는 **그리드 규격에서 유도**한다. 리터럴 42 를 박아 두지 않는다.

    02 B-9 가 셀당 dev 문항을 2 → 4 로 올리면서 42 → 78 이 됐다. 예전처럼 숫자를
    적어 두면 증설할 때마다 손으로 고쳐야 하고, 고치는 순간 "왜 그 값인지" 를
    아무도 다시 확인하지 않는다. 여기서는 셀 18개가 **전부 균등하게** 차 있는지를
    먼저 확인하고 그 결과로 총수를 계산하므로, 한 셀만 늘린 증설은 통과하지 못한다.
    """
    cases = ea.load_dev_cases()
    assert {c["split"] for c in cases} == {"dev"}

    grid = [c for c in cases if c["track"] == "grid"]
    info = [c for c in cases if c["track"] == "info_limit"]
    cells = Counter((c["task_type"], c["answer_mode"], c["difficulty"]) for c in grid)
    assert len(cells) == V.N_CELLS, f"18셀이 아니라 {len(cells)}셀이다"
    assert set(cells.values()) == {V.DEV_PER_CELL}, dict(cells)
    assert len(info) == V.INFO_LIMIT_N

    assert len(cases) == V.DEV_PER_CELL * V.N_CELLS + V.INFO_LIMIT_N == V.DEV_N


def test_heldout_path_is_never_referenced():
    """heldout 은 최종 구성에서 딱 1회다. 스크립트가 경로를 알면 안 된다."""
    source = (ea.ROOT / "scripts" / "error_analysis.py").read_text(encoding="utf-8")
    assert "heldout.jsonl" not in source
    assert ea.DEV_PATH.name == "questions.jsonl"


# --- 코호트 (v1 42문항 ⊂ v2 88문항) -------------------------------------------

def test_cohort_flag_selects_the_question_set():
    """`measured` 는 v1 이 실제로 잰 42문항, `full` 은 dev 78 + 회귀 10 = 88."""
    measured = ea.load_cohort("measured")
    full = ea.load_cohort("full")
    assert len(measured) == 42
    assert len(full) == V.DEV_N + 10 == 88
    # 두 효과(문항 증가 · 시스템 개선)를 분리하려면 measured 가 full 의 부분집합이어야 한다.
    assert {q["question_id"] for q in measured} <= {q["question_id"] for q in full}


def test_valuechain_cohort_is_separate_from_the_grid():
    """밸류체인은 그리드 밖이다. 같이 세면 정답률이 오염된다."""
    vc = ea.load_cohort("valuechain")
    assert len(vc) == 12
    assert {q["track"] for q in vc} == {"valuechain"}
    assert not ({q["question_id"] for q in vc}
                & {q["question_id"] for q in ea.load_cohort("full")})


def test_unknown_cohort_is_rejected():
    with pytest.raises(SystemExit):
        ea.load_cohort("heldout")


def test_v1_artifact_is_not_overwritten():
    """error_analysis.json 은 v1 비교 기준선이라 덮으면 안 된다."""
    import inspect
    src = inspect.getsource(ea)
    assert "error_analysis_v2.json" in src
    with pytest.raises(SystemExit):
        ea.resolve_out(str(ea.OUT_DIR / "error_analysis.json"))
    assert ea.resolve_out("").name == "error_analysis_v2.json"


def test_v2_cache_is_a_separate_folder_from_v1():
    """v1 응답은 A6 시절 답이다. 같은 폴더에 쌓으면 무엇을 잰 것인지 알 수 없게 된다."""
    assert ea.V2_RESPONSE_DIR != ea.RESPONSE_DIR
    assert ea.V2_JUDGE_DIR != ea.JUDGE_DIR


def test_assert_dev_only_rejects_heldout():
    with pytest.raises(SystemExit):
        ea._assert_dev_only([{"split": "dev"}, {"split": "heldout"}])


# --- 채점 --------------------------------------------------------------------

def test_number_won_accepts_unit_scaled_answer():
    case = {"answer_form": "number_won", "answer": "97,000,000,000", "tolerance": 0}
    assert ea.score_closed(case, "계약금액은 97,000,000,000원입니다")["correct"]
    assert ea.score_closed(case, "계약금액은 970억원입니다")["correct"]
    assert not ea.score_closed(case, "계약금액은 96,000,000,000원입니다")["correct"]


def test_number_pct_uses_tolerance():
    case = {"answer_form": "number_pct", "answer": "3.42", "tolerance": 0}
    assert ea.score_closed(case, "보유비율은 3.42% 낮아졌습니다")["correct"]
    assert not ea.score_closed(case, "보유비율은 4.10% 낮아졌습니다")["correct"]


def test_boolean_reads_the_conclusion_not_the_first_polarity_word():
    case = {"answer_form": "boolean", "answer": "있다", "tolerance": 0}
    assert ea.score_closed(case, "정정공시가 있습니다")["correct"]
    assert not ea.score_closed(case, "정정공시가 없습니다")["correct"]


def test_boolean_lead_yes_wins_over_later_wording():
    """실측 Q-025, '네, … 정정공시를 했습니다' 는 명백한 예다."""
    assert ea.boolean_verdict("네, 삼성전자는 이후 정정공시를 했습니다.") is True
    assert ea.boolean_verdict("아니오, 정정된 바 없습니다.") is False


def test_boolean_takes_the_last_polarity_when_both_appear():
    """실측 Q-047, 'A가 B보다 큽니다 … 따라서 B는 A보다 작습니다' 의 결론은 '작다'."""
    text = ("삼성전자의 매출액이 SK하이닉스보다 큽니다. "
            "따라서 SK하이닉스의 매출액은 삼성전자보다 작습니다.")
    assert ea.boolean_verdict(text) is False
    case = {"answer_form": "boolean", "answer": "아니오", "tolerance": 0}
    assert ea.score_closed(case, text)["correct"]


def test_boolean_no_verdict_is_wrong_but_labelled():
    case = {"answer_form": "boolean", "answer": "있다", "tolerance": 0}
    out = ea.score_closed(case, "추가 자료를 확인해 보시기 바랍니다.")
    assert not out["correct"] and out["by"] == "boolean:no_verdict"


def test_boolean_ignores_the_citation_tail():
    assert ea.boolean_verdict("정정공시는 없습니다. (근거: … 크다 큽니다 20230131800162)") is False


# --- C/I-4: 정규식으로 한국어 부정을 판정하는 접근의 한계선 ---------------------

def test_double_negation_is_not_judged_instead_of_being_judged_backwards():
    """이중부정은 마지막 극성 표지만 보면 **뒤집힌다**. 판정하지 않는다.

    `"없지 않습니다"` = 있다 인데 마지막 표지가 `없습니다` 라 종전에는 `False` 가 나왔다.
    뒤집힌 답은 오답을 정답으로도 만들 수 있어 `no_verdict` 보다 나쁘다.
    `no_verdict` 는 채점상 오답이지만 `by="boolean:no_verdict"` 로 **판정 불가임이
    산출물에 남는다**(정답률을 조용히 부풀리지 않는다).
    """
    for text in ("없지 않습니다",
                 "정정공시가 없다고 볼 수 없습니다",
                 "해당 사실이 없다고 할 수 없습니다",
                 "정정 이력이 없지 않은 것으로 보입니다"):
        assert ea.boolean_verdict(text) is None, text
    # `있지 않습니다`(= 없다)는 뒤집히지 않으므로 **판정한다**. 과잉 수정 방지 회귀.
    assert ea.boolean_verdict("있지 않습니다") is False
    # 판정 불가는 채점상 오답이지만 라벨로 드러난다.
    case = {"answer_form": "boolean", "answer": "있다", "tolerance": 0}
    out = ea.score_closed(case, "없지 않습니다")
    assert not out["correct"] and out["by"] == "boolean:no_verdict"


def test_conditional_clauses_are_not_read_as_factual_assertions():
    """조건절은 **사실 주장이 아니다**. `있었` 하나로 긍정을 찍으면 안 된다."""
    for text in ("만약 정정공시가 있었다면 공시했을 것입니다",
                 "정정공시가 있었다면 접수번호가 부여되었을 것입니다",
                 "만일 해당 계약이 변경되었다면 재공시 대상입니다"):
        assert ea.boolean_verdict(text) is None, text


def test_the_measured_boolean_cases_do_not_regress():
    """실측 사고 문장은 종전과 같은 판정을 유지해야 한다.

    이력: 과거형(`있었습니다`·`없었습니다`)을 빠뜨려 **정확한 답이 `no_verdict` 로 샌**
    사고가 있었다(Q-026). 한계선을 그으면서 그 커버리지를 다시 잃으면 안 된다.
    실측: v2 전 13 arm 에서 boolean 채점 변경 **0건**을 확인했다.
    """
    assert ea.boolean_verdict("… 정정공시는 없었습니다.") is False          # Q-026
    assert ea.boolean_verdict("네, … 정정공시를 했습니다.") is True          # Q-025
    assert ea.boolean_verdict("A가 B보다 큽니다. 따라서 B는 A보다 작습니다.") is False  # Q-047
    assert ea.boolean_verdict("정정공시가 있었습니다.") is True


def test_the_deciding_sentence_is_the_last_one_with_a_polarity_marker():
    """어형 판정은 **결론 문장**에서 한다. 앞쪽 조건절 설명이 명확한 결론을 무효화하면
    안 된다(과잉 수정 방지).
    """
    text = ("만약 정정공시가 있었다면 접수번호가 부여됩니다. "
            "확인 결과 정정공시는 없었습니다.")
    assert ea.boolean_verdict(text) is False


def test_refusal_form_matches_limitation_phrase():
    case = {"answer_form": "refusal", "answer": "제공된 공시 데이터에서 확인되지 않습니다."}
    assert ea.score_closed(case, "공시에서 확인되지 않습니다.")["correct"]
    assert not ea.score_closed(case, "매출액은 10조원입니다")["correct"]


def test_refusal_accepts_the_models_own_wording():
    """실측 Q-040, 고정 문구가 아니어도 거절은 거절이다. 환각으로 세면 안 된다."""
    case = {"answer_form": "refusal", "answer": "제공된 공시 데이터에서 확인되지 않습니다."}
    said = "공시된 자료에는 2026년 반기보고서에 기재될 매출액 정보가 포함되어 있지 않습니다."
    assert ea.score_closed(case, said)["correct"]


def test_duration_ignores_whitespace():
    case = {"answer_form": "duration", "answer": "1년 9개월", "tolerance": 0}
    assert ea.score_closed(case, "계약기간은 1년9개월입니다")["correct"]
    assert not ea.score_closed(case, "계약기간은 2년 9개월입니다")["correct"]


# --- hop 귀속 ----------------------------------------------------------------

_TRACE = (
    'route=closed_lookup difficulty=low cot=False sc=False | '
    'hop=0 strat=narrow tool=search_chunks corp="HD현대일렉트릭" period=ym:202301 '
    'top_k=5 q="배전변압기" n=2 top_score=18.500 '
    'ids=exchange_20230131800162,exchange_20230201800111 | '
    'hop=0 verdict=sufficient evidence=2 | generate tokens=900+120 | '
    'budget steps=1 hops=0 llm=1 tokens=1020'
)


def test_parse_trace_extracts_route_and_budget():
    facts = ea.parse_trace(_TRACE)
    assert facts["route"] == "closed_lookup"
    assert facts["corps"] == ["HD현대일렉트릭"]
    assert (facts["n_steps"], facts["n_hops"], facts["n_llm"]) == (1, 0, 1)
    assert facts["critic_supported"] is None


def test_golden_positions_reports_rank():
    case = {"evidence": [{"rcept_no": "20230201800111"}]}
    pos = ea.golden_positions(case, ea.parse_trace(_TRACE)["hops"])
    assert pos["rank"] == {"20230201800111": 2}
    assert pos["top3"] == ["20230201800111"]


def test_golden_positions_empty_when_absent():
    case = {"evidence": [{"rcept_no": "99999999999999"}]}
    pos = ea.golden_positions(case, ea.parse_trace(_TRACE)["hops"])
    assert pos["in_candidates"] == []


# --- 근거 적중은 정기공시에서 **섹션 단위**다 -----------------------------------

_CTX = (
    "## [사업보고서 2023] HMM – III. 재무에 관한 사항 > 6. 배당에 관한 사항\n"
    "- 출처: 접수번호 20240320001402 (공시일 20240320)\n\n표...\n\n"
    "## [사업보고서 2023] HMM – II. 사업의 내용 > 7. 기타 참고사항\n"
    "- 출처: 접수번호 20240320001402 (공시일 20240320)\n\n표..."
)


def test_evidence_hit_requires_the_golden_section_for_periodic():
    right = {"evidence": [{"rcept_no": "20240320001402",
                           "section": "III. 재무에 관한 사항 > 6. 배당에 관한 사항"}]}
    wrong = {"evidence": [{"rcept_no": "20240320001402",
                           "section": "III. 재무에 관한 사항 > 1. 요약재무정보"}]}
    assert ea.evidence_hit(right, _CTX)["all"]
    miss = ea.evidence_hit(wrong, _CTX)
    assert not miss["any"] and miss["doc_only"] == ["20240320001402"]


def test_wrong_section_is_ranking_not_generation():
    """문서는 맞히고 섹션을 틀린 것은 순위 문제다. GENERATION 으로 세면 병목이 숨는다."""
    case = {"answerable": True, "corp_name": ["A"], "doc_group": "periodic"}
    stage = _attr(case, positions={"golden": ["g"], "in_candidates": ["g"], "rank": {"g": 1}},
                  hit={"any": False, "all": False, "present": [], "doc_only": ["g"]},
                  trace_facts=dict(_FACTS, group="periodic"))
    assert stage == "RANKING"


def test_evidence_hit_falls_back_to_document_when_no_section_given():
    case = {"evidence": [{"rcept_no": "20240320001402", "section": None}]}
    assert ea.evidence_hit(case, _CTX)["all"]


# --- 데이터 상한 --------------------------------------------------------------

def test_withheld_only_blocks_when_question_asks_for_the_value():
    records = {"A": {"withheld": True, "amount": None}}
    asked = {"question": "계약금액은 얼마인가?", "evidence": [{"rcept_no": "A"}]}
    other = {"question": "계약상대는 어디인가?", "evidence": [{"rcept_no": "A"}]}
    assert ea.data_limit_flags(asked, records)["blocking"]
    assert not ea.data_limit_flags(other, records)["blocking"]


def test_partial_withheld_is_not_a_data_limit():
    """골든 중 하나만 유보면 나머지로 답이 나온다. 상한으로 세면 실패가 숨는다."""
    records = {"A": {"withheld": True, "amount": None}, "B": {"withheld": False, "amount": 5}}
    case = {"question": "계약금액은 얼마인가?",
            "evidence": [{"rcept_no": "A"}, {"rcept_no": "B"}]}
    assert not ea.data_limit_flags(case, records)["blocking"]


def test_shell_only_periodic_doc_is_a_data_limit():
    case = {"question": "매출액은 얼마인가?", "evidence": [{"rcept_no": "20240514001522"}]}
    assert ea.data_limit_flags(case, {})["blocking"]


# --- 계산 검증 (skipped 과 mismatches 를 섞지 않는다) ---------------------------

def test_calc_mismatch_detected_from_golden_record():
    records = {"A": {"amount": 97_000_000_000}}
    case = {"evidence": [{"rcept_no": "A"}]}
    bad = ea.verify_answer_calc(case, "계약금액은 88,000,000,000원입니다", records)
    assert bad["mismatch"] and "amount" in bad["checked"]
    good = ea.verify_answer_calc(case, "계약금액은 97,000,000,000원입니다", records)
    assert not good["mismatch"]


def test_calc_skipped_is_not_a_mismatch():
    """답변이 수치를 아예 말하지 않으면 검증 대상이 없는 것이지 틀린 게 아니다."""
    records = {"A": {"amount": 97_000_000_000}}
    case = {"evidence": [{"rcept_no": "A"}]}
    out = ea.verify_answer_calc(case, "공시에서 확인되지 않습니다.", records)
    assert not out["mismatch"] and "amount" in out["skipped"]


def test_calc_passes_when_any_golden_record_explains_the_answer():
    """정정 체인은 원공시와 정정본의 금액이 다르다. 정정본과 맞으면 통과다."""
    records = {"orig": {"amount": 227_500_000_000}, "fix": {"amount": 114_800_000_000}}
    case = {"evidence": [{"rcept_no": "orig"}, {"rcept_no": "fix"}]}
    out = ea.verify_answer_calc(case, "정정 후 114,800,000,000원입니다", records)
    assert not out["mismatch"]


# --- 첫 실패 귀속 -------------------------------------------------------------

_FACTS = {"route": "closed_lookup", "corps": ["A"], "group": "exchange",
          "critic_supported": None}
_NO_LIMIT = {"blocking": False, "withheld": [], "shell_only": []}
_CLEAN_CALC = {"mismatch": False, "checked": [], "skipped": [], "reasons": []}


def _attr(case, **kw):
    args = {"trace_facts": dict(_FACTS), "correct": False, "limits": dict(_NO_LIMIT),
            "positions": {"golden": ["g"], "in_candidates": [], "rank": {}},
            "hit": {"any": False, "all": False, "present": []},
            "calc": dict(_CLEAN_CALC)}
    args.update(kw)
    return ea.attribute(case, **args)["stage"]


def test_correct_answer_has_no_failure_stage():
    assert _attr({"answerable": True}, correct=True) is None


def test_hallucination_beats_every_other_stage():
    """거절해야 할 질문에 답을 지어낸 것은 데이터 상한이 아니라 진짜 실패다."""
    case = {"answerable": False}
    assert _attr(case, limits={"blocking": True, "withheld": ["x"], "shell_only": []}) \
        == ea.HALLUCINATION


def test_data_limit_precedes_pipeline_stages():
    case = {"answerable": True, "corp_name": [], "doc_group": "exchange"}
    assert _attr(case, limits={"blocking": True, "withheld": ["x"], "shell_only": []}) \
        == ea.DATA_LIMIT


def test_routing_when_answerable_question_is_short_circuited():
    facts = dict(_FACTS, route="unanswerable")
    case = {"answerable": True, "corp_name": ["A"], "doc_group": "exchange"}
    assert _attr(case, trace_facts=facts) == "ROUTING"


def test_retrieval_when_golden_absent_from_all_hops():
    case = {"answerable": True, "corp_name": ["A"], "doc_group": "exchange"}
    assert _attr(case) == "RETRIEVAL"


def test_ranking_when_golden_in_candidates_but_not_in_evidence():
    case = {"answerable": True, "corp_name": ["A"], "doc_group": "exchange"}
    assert _attr(case, positions={"golden": ["g"], "in_candidates": ["g"],
                                  "rank": {"g": 7}}) == "RANKING"


def test_partial_golden_coverage_is_a_retrieval_failure_not_generation():
    """골든 2건 중 1건만 실리면 답이 성립하지 않는다. GENERATION 으로 세면 병목이 숨는다."""
    case = {"answerable": True, "corp_name": ["A"], "doc_group": "exchange"}
    assert _attr(case, positions={"golden": ["g1", "g2"], "in_candidates": ["g1"],
                                  "rank": {"g1": 1}},
                 hit={"any": True, "all": False, "present": ["g1"]}) == "RETRIEVAL"


_FOUND = {"golden": ["g"], "in_candidates": ["g"], "rank": {"g": 1}}
_IN_PROMPT = {"any": True, "all": True, "present": ["g"]}


def test_tool_when_recomputation_mismatches():
    case = {"answerable": True, "corp_name": ["A"], "doc_group": "exchange"}
    assert _attr(case, hit=dict(_IN_PROMPT), positions=dict(_FOUND),
                 calc={"mismatch": True, "checked": ["amount"], "skipped": [],
                       "reasons": ["amount 불일치"]}) == "TOOL"


def test_verify_when_critic_passed_a_wrong_answer():
    case = {"answerable": True, "corp_name": ["A"], "doc_group": "exchange"}
    assert _attr(case, trace_facts=dict(_FACTS, critic_supported=True),
                 hit=dict(_IN_PROMPT), positions=dict(_FOUND)) == "VERIFY"


def test_generation_when_evidence_is_right_but_answer_is_wrong():
    case = {"answerable": True, "corp_name": ["A"], "doc_group": "exchange"}
    assert _attr(case, hit=dict(_IN_PROMPT), positions=dict(_FOUND)) == "GENERATION"


# --- B/I-7: VERIFY 가 만능 버킷이 되지 않는다 -----------------------------------

def test_verify_no_longer_swallows_rows_that_generation_can_explain():
    """"A6 병목 1위" 를 두 번 만든 기전의 회귀 고정.

    `critic` 은 파이프라인의 **마지막** 게이트다. 따라서 critic 통과는 '첫 실패' 가 될
    수 없다. 앞에서 이미 무언가 틀렸다. 종전에는 ⑦ VERIFY 가 ⑧ GENERATION 보다
    앞에 있어 `critic_supported is True` 인 모든 오답을 삼켰고, `root_cause` 의 분기
    (INDEX_FIELD_MISSING / REASONING)가 가려졌다. 실측: A6 3건 과대 · A5 0건
    (수동 원문 대조는 A6 1 · A5 4).
    """
    case = {"answerable": True, "corp_name": ["A"], "doc_group": "exchange"}
    critic_ok = dict(_FACTS, critic_supported=True)
    # 근거에 골든 수치가 없다 → 인덱스 커버리지 문제로 갈라야 한다 (실측 Q-101)
    assert _attr(case, trace_facts=critic_ok, hit=dict(_IN_PROMPT),
                 positions=dict(_FOUND), has_gold_value=False) == "GENERATION"
    # 근거에 값이 있는데 답이 틀렸다 → 추론 문제다
    assert _attr(case, trace_facts=critic_ok, hit=dict(_IN_PROMPT),
                 positions=dict(_FOUND), has_gold_value=True) == "GENERATION"
    # 대조할 수치가 아예 없어 생성 단계로는 아무 말도 못 할 때만 VERIFY 다 (실측 Q-087)
    assert _attr(case, trace_facts=critic_ok, hit=dict(_IN_PROMPT),
                 positions=dict(_FOUND), has_gold_value=None) == "VERIFY"


def test_critic_pass_is_recorded_even_when_the_stage_is_not_verify():
    """VERIFY 버킷을 좁혔으므로 이 신호가 사라지면 A6 을 볼 방법이 없어진다.

    귀속 단계와 **무관하게** 남는다. `attribute` 의 `why` 와 행의
    `critic_passed_wrong_answer`, 그리고 보고서의 대조표가 각각 들고 있다.
    """
    case = {"answerable": True, "corp_name": ["A"], "doc_group": "exchange"}
    out = ea.attribute(case, dict(_FACTS, critic_supported=True), False,
                       dict(_NO_LIMIT), dict(_FOUND), dict(_IN_PROMPT),
                       dict(_CLEAN_CALC), has_gold_value=False)
    assert out["stage"] == "GENERATION"
    assert "critic" in out["why"] and "supported=True" in out["why"]


def test_the_arm_map_does_not_claim_a6_for_the_verify_bucket():
    """arm 을 지목하는 것은 원문 대조가 끝난 표의 몫이다.

    `verify_calculation` 이 답변 수치 중 **최유리 값**을 골라 비교하므로
    (`critic.py:208` 의 `min(claims, key=...)`) 계산 실패가 이 버킷으로 샌다.
    자동 히스토그램에 `A6` 을 박아 두면 그 수가 문서로 옮겨 적힌다.
    """
    label = ea.ARM_BY_ROOT_CAUSE["VERIFY/VERIFY"]
    # arm 을 **지목**하지 않는다. `**A6**` 형태의 겨냥 표기가 없어야 한다.
    assert ea._arm_of({"first_failure": "VERIFY", "root_cause": "VERIFY"}) == "미분류"
    assert "**A6**" not in label
    assert "원문 대조" in label
    # 계산 답이 A1b 로 오라벨될 수 있다는 경고는 유지돼야 한다.
    assert "⚠" in ea.ARM_BY_ROOT_CAUSE["GENERATION/INDEX_FIELD_MISSING"]


def test_the_report_reconciles_the_automatic_and_manual_arm_allocations():
    """두 배분표가 **대조 없이** 한 보고서에 공존하던 것이 오판을 두 번 만들었다.

    침묵이 최악이다. 차이와 정본을 산출물에 명시한다.
    """
    rows = [_row("Q-087", "search_extract", "closed", "중", False, "VERIFY"),
            # 자동·수동이 **일치**하는 사례. Q-023 은 GENERATION/REASONING → 양쪽 다 A4 다.
            # (종전에는 Q-094/A3 를 썼는데 그 문항이 정답이 되어 상수에서 빠졌다.
            #  일치 사례를 상수 밖 문항으로 쓰면 상수가 바뀔 때마다 이 테스트가 깨진다.)
            # A5/TOOL 행은 쓰지 않는다. 아래 `auto_grid == 0` 단언과 부딪힌다.
            _row("Q-023", "search_extract", "closed", "중", False, "GENERATION",
                 root_cause="REASONING"),
            _row("Q-001", "search_extract", "closed", "중", True)]
    rec = ea.arm_allocation_reconciliation(rows)

    assert rec["authoritative"] == "manual"
    assert "A5" in rec["note"] and "최유리" in rec["note"]
    # 분모가 다르다는 사실이 명시돼야 한다 (자동=그리드만, 수동=전 트랙).
    assert "그리드" in rec["denominator_note"] and "전 트랙" in rec["denominator_note"]
    # 자동이 arm 을 지목하지 않은 Q-087 은 수동 정본(A6)과 불일치로 잡혀야 한다.
    disagree = {d["question_id"]: d["manual_arm"] for d in rec["disagreements"]}
    assert disagree.get("Q-087") == "A6"
    # 일치하는 것은 불일치 목록에 없다.
    assert "Q-097" not in disagree
    by_arm = {i["arm"]: i for i in rec["by_arm"]}
    # 건수를 리터럴로 박지 않는다. 상수에서 센다. 종전의 `== 4` 는 겨냥이 갱신되자
    #    상수가 아니라 **테스트가** 낡음을 붙잡고 있었다.
    expected_a5 = sum(len(ids) for arm, ids, _ in ea.TARGETED_BY_MANUAL_REVIEW if arm == "A5")
    assert by_arm["A5"]["manual_all_tracks"] == expected_a5
    assert by_arm["A5"]["auto_grid"] == 0              # 자동은 구조적으로 0건


def test_the_manual_allocation_is_a_single_source_shared_with_the_gate():
    """`scripts/ablation.py::render_gate` §9 의 `targeted` 와 갈리면 안 된다.

    같은 표가 두 곳에 하드코딩돼 있으면 한쪽만 고쳐진다. 이 프로젝트가 배분표
    불일치로 이미 두 번 오판했다. 여기가 단일 원천이고, 게이트가 이 상수를 읽어야 한다.
    """
    mine = {arm: set(ids) for arm, ids, _ in ea.TARGETED_BY_MANUAL_REVIEW}
    # 건수와 arm 집합을 리터럴로 박지 않는다. 종전에는 `== 13` 과
    #    `== {"A4","A5","A1b","A6","A3"}` 가 박혀 있었고, 재측정으로 오답이 11 로 줄고
    #    A3 겨냥이 0 이 됐을 때 **상수가 아니라 이 테스트가** 낡음을 붙잡았다.
    #    총수의 정본 대조는 `test_manual_targeting_matches_the_current_wrong_set` 이 한다.
    known_arms = {"A0", "A1", "A1b", "A2", "A3", "A4", "A5", "A6", "A7", "A8", "—"}
    assert set(mine) <= known_arms, f"모르는 arm 이름: {sorted(set(mine) - known_arms)}"
    assert mine, "배분표가 비었다"
    # 문항이 두 arm 에 동시에 배분되면 안 된다 (배분표는 분할이다).
    flat = [q for ids in mine.values() for q in ids]
    assert len(flat) == len(set(flat))

    source = (ea.ROOT / "scripts" / "ablation.py").read_text(encoding="utf-8")
    assert "ea.TARGETED_BY_MANUAL_REVIEW" in source, (
        "게이트가 배분표를 다시 하드코딩했다 — 같은 표가 두 곳에 있으면 한쪽만 고쳐진다"
    )
    for arm, ids in mine.items():
        for qid in ids:
            assert f'"{qid}"' not in source, (
                f"게이트가 {qid} 를 직접 적었다 — {arm} 배분이 갈릴 수 있다"
            )


# --- ④ this_run 이 재집계 때 키째로 사라지는 사고 -------------------------------

def test_merge_this_run_carries_forward_when_recompute_has_no_live_run(tmp_path):
    """재집계(`live=None`)는 이전 실행이 남긴 지출 기록을 지우면 안 된다.

    이전 버그: `if live: report["this_run"] = live` 는 `live` 가 없으면 아무것도 안 써서
    `this_run` 이 **키째로 사라졌다**. 0 원으로 덮이는 것보다 나쁘다. 실측:
    `artifacts/eval/error_analysis.json` · `_v2.json` 둘 다 이미 이 사고를 겪었다.
    """
    out_path = tmp_path / "report.json"
    out_path.write_text(json.dumps({"this_run": {"spent_won": 1505.1}}), encoding="utf-8")

    merged = ea._merge_this_run(out_path, live=None)

    assert merged is not None and merged["spent_won"] == 1505.1
    assert "this_run_source" in merged        # 이어받았다는 사실이 필드에 남는다


def test_merge_this_run_keeps_the_previous_spend_when_a_second_live_run_happens(tmp_path):
    """두 번째 유료 실행도 앞의 지출 기록을 지우지 않는다. 새 값을 권위값으로 쓰되
    앞의 값을 `spent_won_previous` 로 남긴다 (`scripts/ablation.py::_write_run` 과 같은 패턴)."""
    out_path = tmp_path / "report.json"
    out_path.write_text(json.dumps({"this_run": {"spent_won": 1153.0}}), encoding="utf-8")

    merged = ea._merge_this_run(out_path, live={"spent_won": 352.1, "runs": []})

    assert merged["spent_won"] == 352.1               # 새 값이 권위값
    assert merged["spent_won_previous"] == 1153.0      # 앞선 지출은 지워지지 않는다


def test_merge_this_run_does_not_fabricate_a_value_when_the_record_was_already_lost(tmp_path):
    """이전 산출물은 있는데 `this_run` 이 이미 없다. 사고가 이미 일어난 상태다.

    없는 값을 추정해 채우지 않는다. 이번 재집계 자체는 `execute()` 를 안 부르므로
    **이번 실행의 신규 지출은 확실히 0원**이고, 그 사실과 '이전 기록 없음'만 남긴다.
    """
    out_path = tmp_path / "report.json"
    # this_run 없음
    out_path.write_text(json.dumps({"totals": {"cost_won": 2370.0}}), encoding="utf-8")

    merged = ea._merge_this_run(out_path, live=None)

    assert merged["spent_won"] == 0.0
    assert "추정" in merged["spent_won_scope"] and "유실" in merged["spent_won_scope"]


def test_merge_this_run_returns_none_for_a_brand_new_report():
    """이전 산출물 자체가 없는 최초 실행은 '유실' 이 아니다. 경고를 만들어내지 않는다."""
    assert ea._merge_this_run(Path("no/such/report.json"), live=None) is None


def test_recompute_via_main_keeps_this_run_end_to_end(tmp_path, monkeypatch):
    """`main()` 자체가 재집계 뒤에도 `this_run` 을 지우지 않는지 실제 진입점으로 확인한다.

    `--run`/`--limit`/`--only`/`--smoke` 를 전부 생략해 `live` 가 `None` 이 되게 한다
    (재집계 경로 · LLM 0회). 응답 캐시가 없는 tmp 디렉터리를 쓰므로 `analyse()` 는 모든
    문항을 건너뛰지만(`RESPONSE_DIR` 에 파일이 없다), 그것과 무관하게 `this_run` 이
    보존되는지가 이 테스트의 주장이다.
    """
    monkeypatch.setattr(ea, "V2_RESPONSE_DIR", tmp_path / "responses_v2")
    monkeypatch.setattr(ea, "V2_JUDGE_DIR", tmp_path / "judge_v2")

    out_path = tmp_path / "report.json"
    out_path.write_text(json.dumps({"this_run": {"spent_won": 42.0}}), encoding="utf-8")

    ea.main(["--cohort", "valuechain", "--out", str(out_path)])

    after = json.loads(out_path.read_text(encoding="utf-8"))
    assert after["this_run"]["spent_won"] == 42.0
    assert "this_run_source" in after["this_run"]


# --- H/6: totals.cost_won 과 this_run.spent_won 은 다른 것을 센다 ---------------

def test_totals_cost_won_is_labelled_as_cohort_replay_not_new_spend():
    """`totals["cost_won"]` 은 코호트 전 행의 누적 재생 비용이지 이번 실행의 신규
    지출이 아니다 (`scripts/ablation.py` 가 같은 필드를 `arm_cost_won` 으로 그렇게
    쓴다). 실측으로 두 수가 갈렸다. v2 전수 재집계 `totals.cost_won` 2,370.0원 vs
    그 재집계 자체의 신규 지출 `this_run.spent_won` 1,505.1원. 필드명은 호환을 위해
    유지하되(바꾸면 `ablation.py`·게이트 문서가 깨진다) 범위를 라벨로 드러낸다.
    """
    rows = [_row("Q1", "search_extract", "closed", "중", True)]
    cost = ea.totals(rows)
    assert "cost_won_scope" in cost
    assert "신규 지출이 아니다" in cost["cost_won_scope"]
    assert "this_run" in cost["cost_won_scope"]


# --- 근본 원인 세분 ------------------------------------------------------------

def test_chain_not_traversed_when_missing_golden_is_a_known_correction():
    """related 로 이미 연결된 정정본을 못 찾은 것은 검색력 문제가 아니다. 안 밟은 것이다."""
    positions = {"golden": ["orig", "fix"], "in_candidates": ["orig"], "rank": {"orig": 1}}
    cause = ea.root_cause("RETRIEVAL", {}, positions, {}, {"orig": ["fix"]}, None)
    assert cause == "CHAIN_NOT_TRAVERSED"


def test_multi_point_query_when_no_chain_links_the_missing_golden():
    positions = {"golden": ["a", "b"], "in_candidates": ["a"], "rank": {"a": 1}}
    assert ea.root_cause("RETRIEVAL", {}, positions, {}, {"a": []}, None) \
        == "MULTI_POINT_QUERY"


def test_wrong_section_is_separated_from_low_rank():
    pos = {"golden": ["g"], "in_candidates": ["g"], "rank": {"g": 9}}
    assert ea.root_cause("RANKING", {}, pos, {"doc_only": ["g"]}, {}, None) == "WRONG_SECTION"
    assert ea.root_cause("RANKING", {}, pos, {"doc_only": []}, {}, None) == "LOW_RANK"


def test_index_field_missing_is_not_a_generation_error():
    """근거에 값 자체가 없으면 생성기가 잘할 방법이 없다. A1b 문제지 A4 문제가 아니다."""
    pos = {"golden": ["g"], "in_candidates": ["g"], "rank": {"g": 1}}
    assert ea.root_cause("GENERATION", {}, pos, {}, {}, False) == "INDEX_FIELD_MISSING"
    assert ea.root_cause("GENERATION", {}, pos, {}, {}, True) == "REASONING"
    assert ea.root_cause("GENERATION", {}, pos, {}, {}, None) == "UNVERIFIED"


def test_gold_value_in_evidence_ignores_receipt_numbers_and_short_digits():
    case = {"answer": None,
            "answer_key_points": ["권면총액 50,182,840,320원", "근거 접수번호 20250522000332"]}
    ctx = "- 공시일: 2025-05-22 (접수번호 20250522000332)\n- 기간: 2026-05-30 ~ 2028-05-29"
    # 접수번호가 겹친다고 '값이 있다'가 되면 안 된다. 그러면 A1b 병목이 통째로 사라진다.
    assert ea.gold_value_in_evidence(case, ctx) is False
    assert ea.gold_value_in_evidence(case, ctx + " 권면총액 50,182,840,320원") is True


def test_gold_value_check_is_none_when_gold_has_no_number():
    assert ea.gold_value_in_evidence({"answer": "아니오"}, "아무 근거") is None


# --- 집계 --------------------------------------------------------------------

def _row(qid, task, mode, diff, correct, stage=None, **kw):
    row = {"question_id": qid, "task_type": task, "answer_mode": mode,
           "difficulty": diff, "doc_group": "exchange", "answerable": True,
           "track": "grid", "sector": "반도체·전자부품",
           "correct": correct, "first_failure": stage, "root_cause": stage or "",
           "has_citation": True,
           "n_hops": 0, "n_steps": 1, "n_llm_calls": 1, "prompt_tokens": 100,
           "completion_tokens": 10, "judge_tokens": 0, "latency_s": 1.0}
    row.update(kw)
    return row


def test_data_limit_is_excluded_from_the_denominator():
    rows = [_row("Q1", "search_extract", "closed", "하", True),
            _row("Q2", "search_extract", "closed", "하", False, ea.DATA_LIMIT)]
    rate = ea._rate(rows)
    assert (rate["n"], rate["n_scored"], rate["correct"]) == (2, 1, 1)
    assert rate["rate"] == 1.0 and rate["data_limit"] == 1


def test_crosstab_has_eighteen_cells_and_three_marginals():
    report = ea.crosstab([_row("Q1", "multi_compare", "open", "상", True)])
    assert len(report["cells"]) == 18
    assert set(report["marginals"]) == {"task_type", "answer_mode", "difficulty"}
    assert report["cells"]["multi_compare|open|상"]["rate"] == 1.0


def test_regression_and_valuechain_never_enter_the_grid_rate():
    """회귀·밸류체인을 그리드에 섞으면 v1 과 비교할 축이 사라진다.

    두 트랙은 3축 라벨을 갖고 있어(answerable=true · task_type 채움) 예전 필터
    `answerable and task_type` 로는 걸러지지 않는다. **track 으로 걸러야 한다.**
    """
    rows = [_row("Q1", "multi_compare", "closed", "상", True),
            _row("R1", "multi_compare", "closed", "상", False, "RETRIEVAL",
                 track="regression"),
            _row("V1", "multi_compare", "closed", "상", False, "RETRIEVAL",
                 track="valuechain")]
    cross = ea.crosstab(rows)
    assert cross["grid_overall"]["n"] == 1 and cross["grid_overall"]["rate"] == 1.0
    assert cross["cells"]["multi_compare|closed|상"]["n"] == 1
    assert cross["by_track"]["regression"]["n"] == 1
    assert cross["by_track"]["valuechain"]["n"] == 1


def test_root_cause_histogram_is_reported_grid_scoped_too():
    """09 의 arm 겨냥표가 이 값을 읽는다. 회귀가 섞이면 겨냥 건수가 부풀고,
    실측에서 정확히 그랬다. VERIFY 전체 4건 중 Q-065 는 회귀 트랙이라 그리드는 3건이다."""
    rows = [_row("Q1", "search_extract", "closed", "중", False, "VERIFY"),
            _row("R1", "search_extract", "closed", "중", False, "VERIFY",
                 track="regression")]
    report = ea.build_report([], rows)
    assert report["root_cause_histogram"]["VERIFY/VERIFY"] == 2
    assert report["root_cause_histogram_grid"]["VERIFY/VERIFY"] == 1
    # 렌더된 표도 그리드만 세야 한다. 문서가 이 표를 그대로 옮긴다.
    text = ea.render_matrix(rows, report)
    assert "그리드 밖 트랙" in text and "R1" in text


def test_new_sector_threshold_counts_grid_only():
    """문턱 판정 분모가 다른 arm 겨냥 수치와 같아야 한다. 표 안에서 분모가 둘이면 안 된다."""
    rows = [_row(f"Q{i}", "search_extract", "closed", "중", False, "RETRIEVAL", sector=s)
            for i, s in enumerate(("게임", "로봇"))]
    rows.append(_row("R1", "search_extract", "closed", "중", False, "RETRIEVAL",
                     sector="철강", track="regression"))
    audit = ea.new_sector_retrieval(rows)
    assert audit["n"] == 2 and audit["triggers_reranker_review"] is False
    assert audit["off_grid_n"] == 1 and audit["off_grid_question_ids"] == ["R1"]


def test_difficulty_audit_ignores_off_grid_tracks():
    rows = [_row("Q1", "search_extract", "closed", "하", True),
            _row("R1", "search_extract", "closed", "하", False, "RETRIEVAL",
                 track="regression"),
            _row("Q2", "search_extract", "closed", "상", True)]
    assert ea.difficulty_audit(rows) == []


# --- 리랭커 재검토 조건 (신규 6섹터) -------------------------------------------

def test_new_sector_retrieval_failures_are_counted_explicitly():
    """신규 6섹터에서 RETRIEVAL 실패 3건 이상이면 Phase E 보류 근거가 흔들린다."""
    rows = [_row(f"Q{i}", "search_extract", "closed", "중", False, "RETRIEVAL",
                 sector=s) for i, s in enumerate(("게임", "로봇", "철강"))]
    rows.append(_row("Q9", "search_extract", "closed", "중", False, "RETRIEVAL",
                     sector="조선"))
    audit = ea.new_sector_retrieval(rows)
    assert audit["n"] == 3 and audit["triggers_reranker_review"] is True
    assert sorted(audit["by_sector"]) == ["게임", "로봇", "철강"]
    assert ea.new_sector_retrieval(rows[:2])["triggers_reranker_review"] is False


def test_difficulty_audit_flags_low_below_high():
    rows = [_row("Q1", "search_extract", "closed", "하", False, "GENERATION"),
            _row("Q2", "search_extract", "closed", "상", True)]
    audit = ea.difficulty_audit(rows)
    assert audit and audit[0]["low"]["ids"] == ["Q1"]


def test_difficulty_audit_silent_when_order_is_sane():
    rows = [_row("Q1", "search_extract", "closed", "하", True),
            _row("Q2", "search_extract", "closed", "상", False, "GENERATION")]
    assert ea.difficulty_audit(rows) == []


def test_cost_uses_measured_price():
    assert ea.cost_won(1000, 0) == pytest.approx(7.02)
    assert ea.cost_won(500, 500) == pytest.approx(7.02)


def test_render_matrix_marks_data_limit_and_hallucination():
    rows = [_row("Q1", "search_extract", "closed", "하", False, ea.DATA_LIMIT),
            _row("Q2", "search_extract", "closed", "중", False, ea.HALLUCINATION)]
    text = ea.render_matrix(rows, ea.build_report([], rows))
    assert "DATA_LIMIT" in text and "HALLUCINATION" in text
    assert "정답률 분모에서 제외" in text


def test_report_is_json_serialisable():
    rows = [_row("Q1", "complex_reason", "open", "상", True)]
    json.dumps(ea.build_report([], rows), ensure_ascii=False)


def test_boolean_verdict_reads_past_tense_forms():
    """`없었습니다`·`있었습니다` 를 빠뜨리면 정확한 답이 no_verdict 로 샌다.

    실측: Q-026 답변 "… 정정공시는 없었습니다." 가 어느 대안에도 안 걸려
    A7 전수 회귀에서 오답으로 집계됐다. A6 답변이 우연히 `없습니다` 로
    끝났을 뿐 시스템 회귀가 아니었다.
    """
    assert ea.boolean_verdict("해당 기간에 정정공시는 없었습니다.") is False
    assert ea.boolean_verdict("정정공시가 2건 있었습니다.") is True
    # 마지막 판정이 이긴다. 확인 과정을 서술한 뒤 부정으로 끝나는 형태
    assert ea.boolean_verdict("변동이 있었는지 확인했으나 없었습니다.") is False


# --- 겨냥 상수의 신선도 --------------------------------------------------------
# `TARGETED_BY_MANUAL_REVIEW` 는 Task 7 시점에 굳어 **넉 달을 낡은 채 살았다.**
# 재측정으로 오답이 13 → 11 로 줄었는데 표는 그대로였고, `gate_I §5·§9` 가 이미 정답이 된
# 문항(Q-094)을 겨냥으로 렌더했다. **사람이 기억으로 갱신하는 상수는 다시 낡는다**.
# 그래서 최신 세대 산출물과 기계적으로 대조한다.

def _latest_final_arm_report() -> Path | None:
    """가장 최근 ablation 세대의 최종 arm 보고서. **경로를 손으로 박지 않는다.**

    `v2` → `v3` 처럼 세대가 올라가면 자동으로 따라간다(문자열 정렬이라 `v9` 까지).
    **최종 arm 이 `A8` 이 아니게 되면 이 glob 을 갱신해야 한다**. 그때는 매치가
    0건이 되어 아래 테스트가 skip 되고, skip 사유가 그 사실을 말한다.
    """
    found = sorted(Path("artifacts/ablation").glob("v*/A8/report.json"))
    return found[-1] if found else None


def test_manual_targeting_matches_the_current_wrong_set():
    """겨냥 상수 = 최신 세대의 오답 집합. **어긋나면 겨냥이 낡은 것이다.**

    두 방향 다 결함이다:
      * 상수에만 있는 문항 → **이미 정답인 것을 겨냥**하고 있다 (Q-094 가 그랬다)
      * 산출물에만 있는 문항 → **배분을 빠뜨렸다.** 배분할 arm 이 없다고 판단했다면
        빈 arm(`,`)으로 **명시**한다 (Q-093 이 그렇다). 침묵과 구분되어야 한다
    """
    report = _latest_final_arm_report()
    if report is None:
        pytest.skip("최종 arm 보고서가 없다 — 최종 arm 이 A8 이 아니면 glob 을 갱신하라")

    rows = json.loads(report.read_text(encoding="utf-8"))["rows"]
    wrong = {r["question_id"] for r in rows if r.get("first_failure")}
    claimed = {q for _, ids, _ in ea.TARGETED_BY_MANUAL_REVIEW for q in ids}

    assert claimed == wrong, (
        f"{report}\n"
        f"  상수에만 (이미 정답인데 겨냥 중): {sorted(claimed - wrong)}\n"
        f"  산출물에만 (배분 누락):          {sorted(wrong - claimed)}")


def test_manual_targeting_has_no_duplicate_or_empty_groups():
    """한 문항이 두 arm 에 걸리면 `by_arm` 합이 분모를 넘는다. 빈 행도 금지."""
    ids = [q for _, group, _ in ea.TARGETED_BY_MANUAL_REVIEW for q in group]
    assert len(ids) == len(set(ids)), f"중복 겨냥: {sorted({q for q in ids if ids.count(q) > 1})}"
    for arm, group, label in ea.TARGETED_BY_MANUAL_REVIEW:
        assert group, f"{arm} 행이 비었다 — 겨냥이 0 이면 행을 지워라 (A3 가 그랬다)"
        assert label, f"{arm} 행에 사유가 없다"


def test_stored_judge_records_carry_the_per_point_array(tmp_path, monkeypatch):
    """Which point was missed is half the value of the strict judge. Storing
    only the rolled-up boolean throws that away."""
    import json as _json

    from argus.agent import critic
    from scripts import error_analysis as ea

    monkeypatch.setattr(
        ea, "judge_open",
        lambda *a, **k: critic.OpenVerdict((True, False, True), True, True, "r"))
    monkeypatch.setattr(ea, "_call_rows", lambda *a, **k: [])
    ea.JUDGE_DIR = tmp_path
    case = {"question_id": "Q-901", "question": "q",
            "answer_key_points": ["a", "b", "c"]}
    ea.run_judge(case, {"answer": "x", "retrieved_context": "e"}, force=True)

    stored = _json.loads((tmp_path / "Q-901.json").read_text(encoding="utf-8"))
    assert stored["covered"] == [True, False, True]
    assert stored["covers_key_points"] is True
    assert stored["correct"] is True


def test_judge_fields_includes_the_array():
    from scripts import ablation as ab

    assert "covered" in ab.JUDGE_FIELDS
