"""골든셋 회귀 테스트 — docs/pipeline/02-golden-set.md B-7.

scripts/validate_evalset.py 의 검사를 pytest 로 고정한다.
문항이 늘어도(§B-7 갱신 규칙) 그리드 균형과 홀드아웃 분리가 깨지지 않아야 한다.

게이트 H-6 으로 **회귀 트랙**(`evalset/regression.jsonl`)이 생겼다. 3축 그리드 밖이고,
Phase H·Ablation v1 이 실측으로 찾은 실패 유형을 하나씩 재현한다.

03-graph-schema C-0b 재측정으로 **밸류체인 트랙**(`evalset/valuechain.jsonl`)이 생겼다.
역시 그리드 밖이고, `counterparty` 를 노드로 승격해야만 풀리는 문항만 담는다 (B-10).

v4 `R1-7` 로 **안전성 트랙**(`evalset/safety.jsonl`)이 생겼다. 평가 8지표의
「안전성·신뢰성」을 재는 문항이 **0개**였다. 정답이 '거절 또는 한계 고지' 라 전부
`answerable=false` 이고, 문항마다 겨냥하는 코드 경로를 선언한다.
"""
from __future__ import annotations

from collections import Counter

import pytest

from scripts import validate_evalset as V

#: 셀당 기대 문항 수. **리터럴을 여기 두지 않는다** — 스크립트의 그리드 상수에서 가져온다.
#: 예전에는 `PER_CELL = 3` 이 박혀 있어 증설할 때마다 손으로 고쳐야 했고,
#: 고치는 순간 "왜 3인지" 를 아무도 다시 확인하지 않았다.
PER_CELL = V.PER_CELL_DEFAULT           # dev 4 + heldout 1 = 5



@pytest.fixture(scope="module")
def dev():
    return V.load_jsonl(V.DEV_PATH)


@pytest.fixture(scope="module")
def heldout():
    return V.load_jsonl(V.HELDOUT_PATH)


@pytest.fixture(scope="module")
def regression():
    return V.load_jsonl(V.REGRESSION_PATH)


@pytest.fixture(scope="module")
def valuechain():
    return V.load_jsonl(V.VALUECHAIN_PATH)


@pytest.fixture(scope="module")
def rows(dev, heldout, regression, valuechain):
    return dev + heldout + regression + valuechain


@pytest.fixture(scope="module")
def sectors():
    return V.load_sectors()


def test_files_are_not_empty(dev, heldout, regression, valuechain):
    assert len(dev) >= V.DEV_N
    assert len(heldout) >= V.HELDOUT_N
    assert len(regression) >= 10
    assert len(valuechain) >= V.VALUECHAIN_MIN


def test_grid_file_lengths_follow_the_grid_spec(dev, heldout):
    """파일 길이는 **그리드 규격에서 유도된다** — 손으로 적은 상수가 아니다.

    dev  = 셀 18 × dev 셀당 4 + 정보한계 6 = 78
    held = 셀 18 × heldout 셀당 1          = 18

    셀당 문항을 올릴 때 18셀을 **동시에** 올리지 않으면 여기서 먼저 깨진다.
    회귀 문항을 `questions.jsonl` 에 섞어 넣어도 마찬가지다 —
    그러면 arm Δ 와 Recall 분모가 같이 움직여 측정이 오염된다(02 B-8).
    """
    assert len(dev) == V.DEV_N == 78
    assert len(heldout) == V.HELDOUT_N == 18
    assert {r["split"] for r in dev} == {"dev"}
    assert {r["track"] for r in dev} == {"grid", "info_limit"}


def test_gate_d_cohort_evidence_count_is_untouched(dev):
    """게이트 D 를 실제로 잰 문항 42건의 골든 근거 60건은 **그대로여야 한다**.

    04-retriever 게이트 D 의 Recall@5 분모가 이 값이다. 골든셋이 늘어도(B-9)
    이미 잰 코호트의 근거 수가 변하지 않으면 그 게이트는 유효하다.
    코호트는 `artifacts/eval/error_analysis.json` 의 실행 산출물이 정의한다 —
    즉 "왜 60인가" 가 파일에서 자동으로 따라온다.
    """
    cohort = V.measured_dev_cohort()
    measured = [r for r in dev if r["question_id"] in cohort]
    assert len(measured) == len(cohort), "측정 코호트 문항이 questions.jsonl 에서 사라졌다"
    assert sum(len(r["evidence"]) for r in measured) == 60


def test_schema_complete(rows):
    assert V.check_schema(rows) == []


def test_evidence_rcept_no_exists_in_manifest(rows):
    assert V.check_evidence_exists(rows, V.load_manifest_rcept()) == []


def test_answer_matches_answer_form(rows):
    assert V.check_answer_form(rows) == []


def test_unanswerable_uses_refusal_form(rows):
    assert V.check_unanswerable(rows) == []
    assert sum(1 for r in rows if r["track"] == "info_limit") == 6
    # 회귀 트랙의 환각 재현 문항도 answerable=false 다 (Q-040 재현).
    assert sum(1 for r in rows if not r["answerable"]) == 7


def test_required_tools_exist_in_tool_specs(rows):
    assert V.check_tools(rows, V.load_tool_names()) == []


def test_grid_cells_are_balanced(rows):
    failures, _ = V.check_grid(rows, PER_CELL)
    assert failures == []


def test_axis_totals_match_grid(rows):
    assert V.check_axis_totals(rows, PER_CELL) == []


def test_heldout_corps_belong_to_heldout_sectors(heldout, sectors):
    assert V.check_heldout_sectors(heldout, sectors) == []


def test_dev_and_heldout_do_not_share_companies(dev, heldout, regression, valuechain,
                                                sectors):
    """회귀·밸류체인 문항도 dev 쪽이다 — 홀드아웃 섹터를 물면 섹터 누출이다 (C7).

    밸류체인 트랙에서 이 제약이 특히 무겁다. 방위사업청 허브는 6개사를 묶는 최대 클러스터인데
    그중 4개사(한국항공우주 · 한화에어로스페이스 · 현대로템 · LIG디펜스앤에어로스페이스)가
    `방산·항공우주` = 홀드아웃 섹터다. dev 문항에 넣는 순간 여기서 걸린다.
    """
    assert V.check_split_disjoint(dev + regression + valuechain, heldout, sectors) == []


def test_hard_questions_include_correction_tracking(rows):
    assert V.check_correction_in_hard(rows) == []
    hard = [r for r in rows if r["answerable"] and r["difficulty"] == "상"]
    tracked = [r for r in hard if r["correction_tracking"]]
    # 게이트 B: 정정이 거래소공시의 43% 인데 '상' 문항이 이를 못 다루면 측정이 헛돈다.
    assert len(tracked) >= len(hard) // 2


def test_correction_evidence_matches_real_chains(rows):
    assert V.check_correction_evidence(rows, V.load_chains()) == []


def test_periodic_correction_questions_cite_a_real_pair(rows):
    """정기공시 '상' 문항의 정정 추적이 실재 원공시↔정정본 쌍인지 manifest 로 확인한다."""
    assert V.check_periodic_correction_pairs(rows, V.load_manifest()) == []
    tracked = [r for r in rows if r["doc_group"] == "periodic" and r["correction_tracking"]]
    assert len(tracked) >= 4


def test_periodic_evidence_carries_a_toc_path(rows):
    """정기공시 근거는 목차 경로를 반드시 적는다 (CONVENTION §7.3).

    접수번호만으로는 55MB 인덱스의 어느 섹션을 봐야 하는지 특정되지 않는다.
    """
    for r in rows:
        if r["doc_group"] != "periodic" or not r["answerable"]:
            continue
        for e in r["evidence"]:
            assert isinstance(e["section"], str) and e["section"].strip(), r["question_id"]


def test_open_questions_have_answer_key_points(rows):
    for r in rows:
        if r["answer_mode"] == "open":
            assert r["answer"] is None, r["question_id"]
            assert r["answer_key_points"], r["question_id"]


def test_closed_questions_have_answer(rows):
    for r in rows:
        if r["answerable"] and r["answer_mode"] == "closed":
            assert r["answer"], r["question_id"]
            assert r["answer_key_points"] is None, r["question_id"]


def test_all_four_heldout_sectors_are_covered(heldout, sectors):
    covered = {sectors[n] for r in heldout for n in r["corp_name"]}
    assert covered == V.HELDOUT_SECTORS


def test_doc_group_distribution_matches_b4(rows):
    """B-4 의 공시유형 배분은 **그리드 + 정보한계** 에만 적용된다.

    배분은 exchange:major:holding:periodic = 4:1:1:3 이다 (그리드 54였을 때
    24·6·6·18). 셀당 문항을 올려도 이 비율을 지키므로 기대값은 **그리드 크기에서
    유도**된다 — 숫자를 손으로 고칠 일이 없고, 한 문서군만 늘리면 여기서 깨진다.
    회귀 트랙은 겨냥하는 실패 유형이 문서군을 결정하므로 이 배분을 따르지 않는다.
    밸류체인 트랙도 마찬가지다 — `counterparty` 필드가 **거래소공시에만 있어서**
    문서군이 exchange 로 고정된다. 배분 분모에 넣으면 4:1:1:3 이 즉시 깨진다.
    """
    graded = [r for r in rows if r["track"] == "grid"]
    info = [r for r in rows if r["track"] == "info_limit"]
    counts = Counter(r["doc_group"] for r in rows
                     if r["track"] not in {"regression", "valuechain"})

    n = len(graded)
    assert n % 9 == 0, f"그리드 {n}문항은 4:1:1:3 으로 나뉘지 않는다"
    for group, share in V.DOC_GROUP_SHARE.items():
        expected = n * share // 9
        # 정보한계 6문항은 전부 정기공시를 겨냥한 질의라 periodic 으로 잡힌다 (B-0).
        if group == "periodic":
            expected += len(info)
        assert counts[group] == expected, (group, counts[group], expected)
    assert (counts["exchange"], counts["major"], counts["holding"], counts["periodic"]) \
        == (40, 10, 10, 36)


# --- 게이트 H-6 — 회귀 트랙 ---------------------------------------------------

def test_track_labels_are_consistent_with_the_file(dev, heldout, regression, valuechain):
    assert V.check_track(dev + heldout + regression + valuechain) == []
    assert V.check_track_matches_file(dev, heldout, regression, valuechain) == []


def test_regression_rows_declare_a_real_failure_mode(rows):
    assert V.check_regression_meta(rows) == []
    for r in rows:
        if r["track"] != "regression":
            continue
        assert r["source"] == "error_analysis", r["question_id"]
        assert r["split"] == "dev", r["question_id"]


def test_every_measured_failure_mode_has_a_reproducing_question(rows):
    """게이트 H-6 의 본문. 유형 하나라도 재현 문항이 없으면 고친 뒤 깨져도 모른다."""
    assert V.check_failure_mode_coverage(rows) == []
    covered = {m for r in rows for m in (r.get("regression_of") or [])}
    assert covered == V.FAILURE_MODES


def test_regression_track_reinforces_the_weak_discrimination_row(rows):
    """`multi_compare × closed` 는 세 난이도가 전부 1~2/2 로 진동했다 (H-7 · gate_I §3).

    그리드는 셀당 3으로 고정하고, 회귀 트랙이 이 행의 세 난이도를 각각 보강한다.
    """
    assert V.check_discrimination_row(rows) == []


#: 02 B-3 '난이도 재조정' 판정 결과 — 문항 → 확정 라벨.
#: Phase H(H-7)는 Q-014·Q-049 를 중으로 지목했으나 Ablation v1 A6 에서 **둘 다 정답**이 되어
#: 근거가 뒤집혔다. Q-047 은 반대로 A6 에서 오답이 되어 후보로 되돌아왔다.
#: 어느 쪽이든 라벨을 옮기려면 18셀을 동시에 올려야 하므로(02 B-8) 지금은 고정한다.
DIFFICULTY_VERDICT = {
    "Q-014": "하", "Q-049": "하", "Q-047": "하", "Q-020": "하",
    "Q-025": "하", "Q-026": "하", "Q-028": "중", "Q-034": "중",
    "Q-021": "중",
}


def test_difficulty_relabel_candidates_stay_pinned(rows):
    """재조정 후보 9건의 라벨을 못박는다.

    한 문항만 옮기면 그 셀은 2, 옆 셀은 4가 되어 C5/C5b 가 깨지고
    08 §H-5.4 · gate_I §3 의 `/12` 분모가 조용히 무효가 된다.
    옮길 때는 반드시 18셀을 함께 올려야 한다 — 이 테스트가 그 강제다.
    """
    labels = {r["question_id"]: r["difficulty"] for r in rows}
    assert {q: labels[q] for q in DIFFICULTY_VERDICT} == DIFFICULTY_VERDICT


def test_regression_questions_do_not_enter_the_grid_counts(rows):
    """회귀 문항은 3축 라벨을 갖지만 셀 카운트에는 들어가지 않는다."""
    graded = [r for r in rows if r["track"] == "grid"]
    assert len(graded) == PER_CELL * 18
    failures, grid = V.check_grid(rows, PER_CELL)
    assert failures == []
    assert sum(grid.values()) == len(graded)
    # 회귀 문항은 3축 라벨을 **버리지 않는다** — 축별 분석에 쓸 수 있어야 한다.
    labelled = [r for r in rows if r["track"] == "regression" and r["answerable"]]
    assert labelled and all(r["task_type"] and r["answer_mode"] and r["difficulty"]
                            for r in labelled)


# --- 밸류체인 트랙 (02 B-10 · 03 C-0b) ---------------------------------------

#: 실측한 `counterparty` 표기 변이. **왼쪽이 원문, 오른쪽이 노드 키다.**
#: 이 표가 `src` 쪽 정규화 구현의 규격이 된다 — 구현이 붙을 때 여기를 그대로 옮기면 된다.
#: 다섯 가지 변이 유형이 전부 실재한다는 것을 원본 1,125건에서 확인했다.
COUNTERPARTY_VARIANTS = {
    # ① 국호 접두 + 괄호 영문명 (91건, 최대 허브)
    "대한민국 방위사업청": "방위사업청",
    "방위사업청 (Defense Acquisition Program Administation)": "방위사업청",
    "방위사업청(Defense Acquisition Program Administation)": "방위사업청",
    # ② 법인격 접미 3종 — (주) · ㈜ · 주식회사, 그리고 접미가 아예 없는 형태
    "한국수력원자력": "한국수력원자력",
    "한국수력원자력(주)": "한국수력원자력",
    "한국수력원자력㈜": "한국수력원자력",
    "한국수력원자력주식회사": "한국수력원자력",
    "한국수력원자력 주식회사": "한국수력원자력",
    "삼성전자(주)": "삼성전자",
    "삼성전자㈜": "삼성전자",
    # ③ 법인격 접두 (괄호형·기호형)
    "(주)포스코이앤씨": "포스코이앤씨",
    "㈜포스코이앤씨": "포스코이앤씨",
    # ④ 공백 유무만 다른 쌍 — 유일한 차이가 띄어쓰기 한 칸이다
    "주식회사 유토개발2차": "유토개발2차",
    "주식회사유토개발2차": "유토개발2차",
    # ⑤ 괄호 약칭
    "한국토지주택공사(LH)": "한국토지주택공사",
    "한국항공우주산업㈜ (KOREA AEROSPACE INDUSTRIES, LTD.)": "한국항공우주산업",
}


def test_counterparty_normalizer_folds_the_measured_variants():
    """정규화 규칙이 **실측한 표기 변이 다섯 유형**을 전부 접는지.

    이 테스트가 곧 명세다. `counterparty` 는 아직 노드가 아니라 문자열 속성이고,
    노드로 승격하려면 이 접기 규칙이 먼저 있어야 한다. 규칙을 고치면 여기서 먼저 깨진다.
    """
    for raw, key in COUNTERPARTY_VARIANTS.items():
        assert V.normalize_counterparty(raw) == key, raw


def test_anonymous_counterparties_are_recognised_as_such():
    """비실명 계약상대는 노드가 될 수 없다 — 어느 기업인지 원문에 없기 때문이다."""
    for raw in ("글로벌 대형기업", "오세아니아 지역 선주", "아시아 소재 선사",
                "유럽소재제약사", "글로벌 완성차 업체"):
        assert V.ANON_COUNTERPARTY.search(raw), raw
    for raw in ("방위사업청", "한국수력원자력", "삼성전자", "한국토지주택공사"):
        assert not V.ANON_COUNTERPARTY.search(raw), raw


def test_valuechain_hubs_are_real(rows):
    """C12 — 선언한 허브가 근거 레코드의 계약상대와 실제로 일치하는가."""
    assert V.check_valuechain_hub(rows, V.load_counterparties()) == []


def test_valuechain_closed_answers_recompute_from_source(rows):
    """C13 — 수치 정답을 원본 `amount` 로 다시 계산해 대조한다.

    B-7 절차 2("답을 먼저 알고 질문을 쓴다")의 기계 버전이다. 손으로 적은 금액이
    하나라도 어긋나면 여기서 걸린다.
    """
    assert V.check_valuechain_answer(rows, V.load_amounts()) == []


def test_valuechain_questions_stay_out_of_the_grid(rows, valuechain):
    """밸류체인 문항은 3축 라벨을 갖지만 셀 카운트에는 들어가지 않는다.

    CONVENTION §6 의 질의 6종에 '발주처·밸류체인' 이 없어 그리드에 넣을 수 없다.
    넣으면 §6 대응이 깨지고 C5/C5b 셀 균등도 무너진다 (02 B-10).
    """
    assert len([r for r in rows if r["track"] == "grid"]) == PER_CELL * 18
    assert valuechain and all(r["track"] == "valuechain" for r in valuechain)
    assert all(r["task_type"] and r["answer_mode"] and r["difficulty"] for r in valuechain)
    assert {r["split"] for r in valuechain} == {"dev"}
    assert {r["source"] for r in valuechain} == {"derived"}
    # `counterparty` 는 거래소공시에만 있는 필드다 — 다른 문서군에서는 이 트랙이 성립하지 않는다.
    assert {r["doc_group"] for r in valuechain} == {"exchange"}


def test_valuechain_questions_really_need_two_hops(valuechain):
    """기업 2곳이 한 허브를 공유(fan-in)하거나, 한 기업이 허브 2곳에 걸쳐야(fan-out) 한다.

    둘 다 아니면 '한 회사의 계약 하나를 조회' 하는 것과 다르지 않고, 그런 문항은
    counterparty 를 노드로 올려도 좋아지지 않으므로 이 트랙에 있을 이유가 없다.
    """
    members = V.hub_members()
    for r in valuechain:
        fan_in, fan_out = len(r["corp_name"]) >= 2, len(r["hub"]) >= 2
        assert fan_in or fan_out, r["question_id"]
        for h in r["hub"]:
            assert h in members, f"{r['question_id']}: 허브 {h} 가 2회 미만 등장"
            assert set(r["corp_name"]) <= members[h] or len(r["hub"]) > 1, r["question_id"]


def test_the_defence_cluster_is_locked_out_by_the_holdout_rule(sectors):
    """🔴 방위사업청은 **최대 허브인데 dev 문항으로 쓸 수 없다.**

    6개사를 묶는 코퍼스 최대 클러스터지만 그중 4개사가 `방산·항공우주` = 홀드아웃 섹터다.
    dev 문항에 넣으면 C7(기업 중복 0 · 섹터 누출 금지)이 깨지고, heldout 에 넣으면
    최종 1회용 파일을 열어야 한다. 그래서 이 클러스터는 **쓰지 않았다.**

    이 테스트는 그 판단의 근거가 여전히 사실인지 지킨다 — 홀드아웃 섹터 정의가 바뀌면
    (예: `조선·건설·방산` 으로 넓히면) 여기서 먼저 깨지고 다시 판단하게 된다.
    """
    members = V.hub_members()
    dap = members["방위사업청"]
    assert len(dap) >= 5, dap
    blocked = {c for c in dap if sectors.get(c) in V.HELDOUT_SECTORS}
    assert blocked == {"한국항공우주", "한화에어로스페이스", "현대로템",
                       "LIG디펜스앤에어로스페이스"}, blocked
    # 남는 dev 기업은 조선 2곳뿐이라 '방산 밸류체인' 이라 부를 수 없다.
    assert {c for c in dap if c not in blocked} == {"HD현대중공업", "한화오션"}


def test_valuechain_corps_are_all_dev_sector(valuechain, sectors):
    for r in valuechain:
        for name in r["corp_name"]:
            assert sectors.get(name) not in V.HELDOUT_SECTORS, (r["question_id"], name)


# ── 안전성 트랙 (v4 R1-7) ───────────────────────────────────────────────────
# 🔴 **새 분모다.** 그리드 72 · 전체 88 · 밸류체인 12 · 회귀 10 · 정보한계 6 은 문서 수십
# 군데에 인용돼 있으므로, 안전성 12가 그중 어디에도 섞이지 않는 것을 여기서 고정한다.
# `rows` 픽스처에 **일부러 넣지 않았다** — 넣으면 `test_doc_group_share` 의 4:1:1:3 이
# 즉시 깨진다 (회귀·밸류체인을 그 분모에서 뺀 것과 같은 이유).

@pytest.fixture(scope="module")
def safety():
    return V.load_jsonl(V.SAFETY_PATH)


def test_safety_track_is_a_new_denominator_that_leaves_the_others_alone(
        safety, dev, regression, valuechain):
    """안전성 12문항이 기존 네 분모를 한 건도 건드리지 않는다."""
    assert len(safety) == 12
    assert all(r["track"] == "safety" for r in safety)
    assert {r["split"] for r in safety} == {"dev"}
    # 기존 분모 — 이 수가 움직이면 문서 수십 군데의 인용이 동시에 거짓이 된다.
    assert sum(1 for r in dev if r["track"] == "grid") == V.DEV_PER_CELL * V.N_CELLS
    assert sum(1 for r in dev if r["track"] == "info_limit") == V.INFO_LIMIT_N
    assert len(regression) == 10
    assert len(valuechain) == 12
    assert not ({r["question_id"] for r in safety}
                & {r["question_id"] for r in dev + regression + valuechain})


def test_safety_questions_are_refusal_shaped_like_the_info_limit_six(safety):
    """정답이 '거절 또는 한계 고지' 라 3축 라벨이 비고 `answer_form=refusal` 이다."""
    for r in safety:
        assert r["answerable"] is False, r["question_id"]
        assert (r["task_type"], r["answer_mode"], r["difficulty"]) == (None, None, None)
        assert r["answer_form"] == "refusal"
        assert r["answer_key_points"] is None
        assert V.REFUSAL_MARK in r["answer"], r["question_id"]
        assert r["evidence"] == []          # 골든 근거가 없다 — 인용할 것이 없는 것이 정답
    assert V.check_track(safety) == []
    assert V.check_unanswerable(safety) == []
    assert V.check_answer_form(safety) == []


def test_safety_questions_declare_the_code_path_they_exercise(safety):
    """C14 — 범주·겨냥 경로 선언이 없으면 그 문항의 겨냥은 0이다."""
    assert V.check_safety_meta(safety, V.load_manifest_rcept()) == []
    assert V.check_safety_coverage(safety) == []
    counts = Counter(r["safety_category"] for r in safety)
    assert set(counts) == V.SAFETY_CATEGORIES
    assert all(n >= V.SAFETY_PER_CATEGORY_MIN for n in counts.values()), counts


def test_safety_gold_answers_are_scored_correct_by_the_real_scorer(safety):
    """🔴 모범답안이 채점기의 거절 정규식에 걸려야 한다.

    `answer_form=refusal` 은 `error_analysis.score_closed` 가 `_REFUSAL_RE` 로 채점한다.
    모범답안 자체가 그 정규식에 안 걸리면 **어떤 답변도 정답이 될 수 없는 문항**이 되고,
    그것은 측정이 아니라 0점 제조다.
    """
    from scripts import error_analysis as ea

    for r in safety:
        assert ea.score_closed(r, r["answer"])["correct"], r["question_id"]


def test_safety_pii_questions_target_a_document_that_really_carries_pii(safety):
    """🔴 `R1-10` 의 함정을 막는다 — *"마스킹이 한 번도 발화하지 않았다"*.

    그 침묵은 코퍼스에 PII 가 없어서가 아니라 **발화 조건이 오지 않아서**였다.
    개인정보 문항이 지목한 문서를 원문에서 열어 `argus.agent.safety` 의 패턴이 실제로
    걸리는지 본다. 안 걸리면 그 문항은 `mask_pii` 를 시험하지 못한다.
    """
    import json
    from pathlib import Path

    from argus.agent import safety as S

    manifest = {r["rcept_no"]: r for r in V.load_jsonl(V.MANIFEST_PATH)}
    targets = [r for r in safety if r["safety_category"] == "pii_request"]
    assert targets, "개인정보 범주가 비어 있다"
    for r in targets:
        rcept = r["pii_source"]
        doc = manifest[rcept]
        path = V.ROOT / "data" / "dart-disclosures" / doc["file_path"] / f"{rcept}.xml"
        assert path.exists(), (r["question_id"], str(path))
        text = path.read_text(encoding="utf-8", errors="ignore")
        fired = [name for name in r["forbidden_patterns"]
                 if getattr(S, name).search(text)]
        assert fired, (f'{r["question_id"]}: {rcept} 원문에 '
                       f'{r["forbidden_patterns"]} 가 하나도 없다 — 마스킹을 시험할 수 없다')
        # 마스킹이 실제로 값을 바꾸는지까지 본다 (패턴 존재 ≠ 치환).
        assert S.mask_pii(text) != text, r["question_id"]
        assert json.dumps(r, ensure_ascii=False)          # 직렬화 가능해야 jsonl 로 남는다


def test_safety_is_its_own_cohort_and_stays_out_of_the_grid_rate(safety):
    """러너·채점기가 같은 출처를 읽고, 그 트랙이 그리드 정답률에 섞이지 않는다."""
    from scripts import ablation as runner
    from scripts import error_analysis as ea

    assert "safety" in ea.COHORTS
    assert "safety" in ea.OFF_GRID_TRACKS
    ids = [c["question_id"] for c in ea.load_cohort("safety")]
    assert ids == [r["question_id"] for r in safety]
    assert [c["question_id"] for c in runner.load_cases("safety")] == ids
    # 🔴 그리드 모집단에 한 건도 들어가지 않는다.
    assert ea.grid_rows([dict(r, correct=False) for r in safety]) == []


def test_legacy_keys_kept_for_eval_e2e(dev):
    """scripts/eval_e2e.py 는 이 파일을 읽는다. 키 이름을 바꾸면 하네스가 깨진다."""
    for r in dev:
        assert {"question_id", "question", "type"} <= set(r)
        assert "gold" in r and "must_cite" in r


def test_eval_e2e_can_load_and_score():
    from scripts.eval_e2e import load_cases, score

    cases = load_cases()
    assert len(cases) >= 30
    first = next(c for c in cases if c["question_id"] == "Q-001")
    result = score(
        {"question_id": "Q-001", "question": "q", "retrieved_context": "c",
         "think_trace": "t",
         "answer": "계약금액은 97,000,000,000원입니다 (접수번호 20230131800162)"},
        first,
    )
    assert result["has_schema"] and result["answer_match"] and result["has_citation"]


def test_heldout_file_is_not_referenced_by_eval_harness():
    """홀드아웃은 기본 평가 경로에서 제외돼야 한다 (B-6)."""
    from scripts.eval_e2e import load_cases

    ids = {c["question_id"] for c in load_cases()}
    heldout_ids = {r["question_id"] for r in V.load_jsonl(V.HELDOUT_PATH)}
    assert ids & heldout_ids == set()
