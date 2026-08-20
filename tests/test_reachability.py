"""'도달 불가' 표시가 **눈대중이 아니라 기준의 산물**임을 잠근다. LLM 0회.

이 표시는 **우리 성적을 좋아 보이게 만드는 방향**의 변경이다. 그래서 상수를 믿지
않고 기준을 매 실행마다 다시 돌려 대조한다. 기준·코퍼스·회수 결과 중 하나라도
바뀌면 여기가 먼저 깨진다. 그때 고칠 것은 테스트가 아니라 **상수와 보고서**다.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import ablation as ab
from scripts import error_analysis as ea

V3 = Path(ea.ROOT) / "scratch" / "ablation" / "v3"
#: 오라클은 골든을 주입하므로 '도달 가능의 증거' 에서 뺀다.
#: R2E 에서 `L1`·`L2`·`L3` 이 측정됐으므로 증거에 **더한다.** 증거가 늘면 관문 G2 가
#: 더 많이 걸러 표시는 **줄어들 수만 있다**. 실측 결과는 3건 불변이었다(감도 곡선 ⑤).
#: 반대로 증거를 줄이면 표시가 늘어난다(`vc/A8` 하나만 쓰면 4건, `Q-064` 가 붙는다).
#: 그래서 이 목록은 **가능한 만큼 넓어야** 하고, 좁히는 방향의 수정은 성적을 부풀린다.
#: R2G 에서 `A8-lean`·`vc/A8-lean` 이 측정됐으므로 증거에 **더한다.** 이 arm 은
#: 검색 플래그가 `A8` 과 같고 프롬프트만 다르므로 회수 합집합이 **바뀔 수 없고**, 실제로
#: 표시는 3건 불변이었다 (`test_the_frozen_marking_is_exactly_what_the_criterion_produces`
#: 가 그것을 매 실행 재확인한다). 그래도 목록에 넣는 이유는 위 규율이다. 넓히는 것은
#: 언제나 안전하고, 빠뜨리면 다음 세대에서 조용히 성적이 부풀려진다.
NON_ORACLE_ARMS = ("A8", "L1", "L2", "L3", "L4", "A8-lean",
                   "vc/A8", "vc/L3", "vc/A8-lean")


def _stored_arm_dirs() -> tuple[str, ...]:
    """저장된 v3 arm 산출물 **전수**, `V3/<arm>` 과 `V3/vc/<arm>`.

    목록을 손으로 적지 않는다 (R2F). 이 파일에는 arm 이름 목록이 **둘** 있었고
    (`NON_ORACLE_ARMS` 와 100% 초과 검사의 리터럴) 구성이 서로 달랐다.
    한쪽만 갱신되면 감사 범위가 조용히 좁아진다.
    """
    found = [p.parent.name for p in sorted(V3.glob("*/report.json"))]
    found += [f"vc/{p.parent.name}" for p in sorted((V3 / "vc").glob("*/report.json"))]
    return tuple(found)


def _retrieved() -> dict[str, set]:
    out: dict[str, set] = {}
    for rel in NON_ORACLE_ARMS:
        path = V3 / rel / "report.json"
        if not path.exists():
            continue
        for row in json.loads(path.read_text(encoding="utf-8"))["rows"]:
            out.setdefault(row["question_id"], set()).update(
                row.get("golden_in_evidence") or [])
    return out


def _cases():
    return ab.load_cases("full", "") + ab.load_cases("valuechain", "")


needs_artifacts = pytest.mark.skipif(
    not (V3 / "A8" / "report.json").exists(),
    reason="v3 산출물이 없으면 회수 증거를 못 읽는다")


@needs_artifacts
def test_the_frozen_marking_is_exactly_what_the_criterion_produces():
    """상수 `UNREACHABLE_GOLDEN` == 기준을 다시 돌린 결과."""
    rows = ea.unreachable_golden_candidates(_cases(), ea._load_records(), _retrieved())
    derived: dict[str, tuple] = {}
    for row in rows:
        if row["marked"]:
            derived.setdefault(row["question_id"], []).append(row["rcept_no"])
    derived = {k: tuple(v) for k, v in derived.items()}
    assert derived == ea.UNREACHABLE_GOLDEN, (
        "기준을 다시 돌린 결과가 동결 상수와 다르다. 상수와 R2D 보고서를 고쳐라 — "
        f"기준={derived} 상수={ea.UNREACHABLE_GOLDEN}")


@needs_artifacts
def test_a_golden_that_was_actually_retrieved_is_never_marked():
    """회수는 도달 가능성의 증명이다. 회수된 골든을 표시하면 분모 조작이 된다."""
    retrieved = _retrieved()
    for qid, marked in ea.UNREACHABLE_GOLDEN.items():
        for rcept_no in marked:
            assert rcept_no not in retrieved.get(qid, set()), (
                f"{qid} {rcept_no} 는 실제로 회수됐는데 '도달 불가' 로 표시돼 있다")


def test_the_marking_never_touches_the_accuracy_denominator():
    """정답률 분모(`_rate`)는 이 표시를 읽지 않는다."""
    rows = [
        {"first_failure": None, "correct": True,
         "golden": ["A"], "golden_in_evidence": [], "golden_unreachable": ["A"]},
        {"first_failure": None, "correct": False,
         "golden": ["B"], "golden_in_evidence": [], "golden_unreachable": []},
    ]
    assert ea._rate(rows)["n_scored"] == 2
    assert ea._rate(rows)["correct"] == 1


def test_both_denominators_are_reported_side_by_side():
    """조정값만 남기지 않는다. 원 분모가 산출물에 함께 있어야 한다."""
    rows = [{"golden": ["A", "B", "C"], "golden_in_evidence": ["A"],
             "golden_unreachable": ["C"]}]
    got = ea.golden_recall_denominators(rows)
    assert got["hit"] == 1
    assert got["denominator_raw"] == 3
    assert got["unreachable"] == 1
    assert got["denominator_adjusted"] == 2


def test_a_marked_golden_that_this_arm_did_retrieve_is_not_taken_off_its_denominator():
    """회수율이 100% 를 넘지 않는다. 오라클에서 `46/43` 이 찍혔던 결함.

    표시는 비-오라클 arm 의 회수 합집합으로 판정한 동결 상수라, 골든을 주입하는
    `ORACLE` 은 표시된 골든까지 전건 회수한다. 표시 건수를 그대로 빼면 분자가 분모를
    넘는다. 회수된 표시분은 **도달 가능이 증명된 것**이라 뺄 근거가 없다.
    """
    rows = [{"golden": ["A", "B", "C"], "golden_in_evidence": ["A", "B", "C"],
             "golden_unreachable": ["C"]}]
    got = ea.golden_recall_denominators(rows)
    assert got["hit"] == 3
    assert got["denominator_raw"] == 3
    assert got["unreachable"] == 1, "표시 건수 자체는 그대로 보고해야 감사가 된다"
    assert got["unreachable_missed"] == 0
    assert got["denominator_adjusted"] == 3, "회수한 표시분을 분모에서 빼면 100% 를 넘는다"


@needs_artifacts
def test_no_stored_arm_reports_a_recall_above_one_hundred_percent():
    """산출물 전수로 같은 불변을 건다. 단위 테스트만으로는 렌더 경로를 못 잠근다."""
    checked = 0
    for rel in _stored_arm_dirs():
        path = V3 / rel / "report.json"
        rows = json.loads(path.read_text(encoding="utf-8"))["rows"]
        checked += 1
        got = ea.golden_recall_denominators(rows)
        assert got["hit"] <= got["denominator_adjusted"], (
            f'{rel} 회수율이 100% 를 넘는다: '
            f'{got["hit"]}/{got["denominator_adjusted"]}')


@needs_artifacts
def test_every_stored_non_oracle_arm_is_counted_as_reachability_evidence():
    """**좁히는 방향의 누락이 성적을 부풀린다** (R2F 신설).

    `_retrieved()` 는 없는 보고서를 조용히 건너뛴다. 그래서 새 arm 을 재고
    `NON_ORACLE_ARMS` 에 **안 넣으면** 회수 증거가 좁아지고, 관문 G2 가 덜 걸러
    '도달 불가' 표시가 늘고, 조정 분모가 작아져 **회수율이 올라간다.** R2E 가
    `L1`·`L2`·`L3`·`vc/L3` 를 손으로 넣어야 했던 자리다. 그 손을 감시로 바꾼다.
    """
    from argus import ablation as arms

    diagnostic = set(arms.DIAGNOSTIC_ARMS)
    missing = [rel for rel in _stored_arm_dirs()
               if rel.rsplit("/", 1)[-1] not in diagnostic
               and rel not in NON_ORACLE_ARMS]
    assert not missing, (
        f"측정된 비-진단 arm 이 회수 증거 목록에 없다: {missing}. "
        "빠뜨리면 '도달 불가' 표시가 늘어 회수율이 부풀려진다 — "
        "`NON_ORACLE_ARMS` 에 넣고 표시 건수를 다시 확인하라.")


def test_periodic_section_goldens_are_left_in_the_denominator():
    """정기공시 골든은 근거 단위가 섹션이라 판정하지 않고 분모에 남긴다 (관문 G1)."""
    rows = ea.unreachable_golden_candidates(
        [{"question_id": "Q-x", "question": "아무 질문",
          "evidence": [{"rcept_no": "99999999999999"}]}], {}, {})
    assert rows == [{"question_id": "Q-x", "rcept_no": "99999999999999",
                     "marked": False, "gate": "G1",
                     "why": "정형 레코드가 아니다(정기공시 섹션)"}]


def test_a_small_pool_is_reachable_by_enumeration():
    """관문 G3, 후보군이 근거 상한 이하면 어휘 연결이 없어도 도달 가능하다."""
    records = {f"exchange_2222222222222{i}": {
        "doc_id": f"exchange_2222222222222{i}", "corp_name": "가가",
        "doc_subtype": "나나", "counterparty": "다다",
        "markdown": f"본문 {i}"} for i in range(3)}
    records["exchange_11111111111111"] = {
        "doc_id": "exchange_11111111111111", "corp_name": "가가",
        "doc_subtype": "나나", "counterparty": "다다", "markdown": "본문 골든"}
    rows = ea.unreachable_golden_candidates(
        [{"question_id": "Q-y", "question": "전혀 겹치지 않는 낱말 라라마마",
          "evidence": [{"rcept_no": "11111111111111"}]}], records, {})
    assert rows[0]["gate"] == "G3"
    assert rows[0]["marked"] is False
