"""Phase H 귀속 로직 회귀. **LLM 0회** — conftest 의 block_clova_network 를 우회하지 않는다."""
import json
from collections import Counter

import pytest

from scripts import error_analysis as ea
from scripts import validate_evalset as V


# --- 입력 가드 ---------------------------------------------------------------

def test_only_dev_split_is_loaded():
    """dev 문항 수는 **그리드 규격에서 유도**한다 — 리터럴 42 를 박아 두지 않는다.

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
    """밸류체인은 그리드 밖이다 — 같이 세면 정답률이 오염된다."""
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
    """실측 Q-025 — '네, … 정정공시를 했습니다' 는 명백한 예다."""
    assert ea.boolean_verdict("네, 삼성전자는 이후 정정공시를 했습니다.") is True
    assert ea.boolean_verdict("아니오, 정정된 바 없습니다.") is False


def test_boolean_takes_the_last_polarity_when_both_appear():
    """실측 Q-047 — 'A가 B보다 큽니다 … 따라서 B는 A보다 작습니다' 의 결론은 '작다'."""
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


def test_refusal_form_matches_limitation_phrase():
    case = {"answer_form": "refusal", "answer": "제공된 공시 데이터에서 확인되지 않습니다."}
    assert ea.score_closed(case, "공시에서 확인되지 않습니다.")["correct"]
    assert not ea.score_closed(case, "매출액은 10조원입니다")["correct"]


def test_refusal_accepts_the_models_own_wording():
    """실측 Q-040 — 고정 문구가 아니어도 거절은 거절이다. 환각으로 세면 안 된다."""
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
    """골든 중 하나만 유보면 나머지로 답이 나온다 — 상한으로 세면 실패가 숨는다."""
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


# --- 근본 원인 세분 ------------------------------------------------------------

def test_chain_not_traversed_when_missing_golden_is_a_known_correction():
    """related 로 이미 연결된 정정본을 못 찾은 것은 검색력 문제가 아니다 — 안 밟은 것이다."""
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
    # 접수번호가 겹친다고 '값이 있다'가 되면 안 된다 — 그러면 A1b 병목이 통째로 사라진다.
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
    """🔴 회귀·밸류체인을 그리드에 섞으면 v1 과 비교할 축이 사라진다.

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
    # 마지막 판정이 이긴다 — 확인 과정을 서술한 뒤 부정으로 끝나는 형태
    assert ea.boolean_verdict("변동이 있었는지 확인했으나 없었습니다.") is False
