"""답변의 내적 일관성 검사 (`critic.verify_answer_arithmetic`) — LLM 0회.

`tests/test_critic.py` 는 **근거 레코드 대조**(`verify_calculation`)의 회귀 감시를
들고 있고 옛 수가 상수로 박혀 있다. 그 구조를 건드리지 않으려고 새 축은 여기에 쓴다
(계획서 `Phase 2` Files — `tests/test_calc_checks.py(신설)`).

여기서 고정하는 것은 두 가지다.
  ① 겨냥 — Q-044(등식)·Q-018(원값↔환산값)을 실제 저장 응답 원문으로 잡는다
  ② 회귀 — 저장 응답 전수 재채점에서 **정답 문항 거짓 경보가 몇 건인지**를 박는다
"""
import json
import pathlib

import pytest

from argus.agent import critic

_ROOT = pathlib.Path(__file__).resolve().parents[1]


# --- ① 등식 재검산 (Q-044) ----------------------------------------------------

def test_arithmetic_never_calls_llm(monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("verify_answer_arithmetic 가 LLM 을 호출했습니다")
    monkeypatch.setattr(critic, "chat", _boom)
    assert critic.verify_answer_arithmetic("1 + 1 = 3")["llm_calls"] == 0


def test_equation_catches_the_q044_sum():
    """실측 Q-044. 오차가 정확히 −1,000,000 이고 표 단위가 이미 억원이라 단위 문제는 0."""
    out = critic.verify_answer_arithmetic(
        r"$$1,699,923 + 665,945 = 1,365,868 \text{억원}$$")
    assert out["ok"] is False
    assert out["failed_kinds"] == ["equation"]
    assert out["failure_stage"] == "TOOL"
    bad = out["mismatches"][0]
    assert bad["expected"] == 2_365_868 and bad["claimed_value"] == 1_365_868


def test_equation_accepts_a_correct_sum():
    out = critic.verify_answer_arithmetic(
        "총합계 = 398,800,000,000 + 72,200,000,000 + 346,900,000,000 = 817,900,000,000원")
    assert out["ok"] is True and "equation" in out["checked"]


def test_equation_accepts_a_correct_subtraction():
    """실측 Q-048. 정정 전·후 차이를 스스로 적은 정답에는 걸리면 안 된다."""
    assert critic.verify_answer_arithmetic(
        "216,407,387,195 - 139,850,216,050 = 76,557,171,145원")["ok"] is True


def test_equation_survives_a_missing_thousands_comma():
    """실측 Q-051. `12,646848` 처럼 콤마가 하나 빠져도 값은 같다 — 틀린 게 아니다."""
    assert critic.verify_answer_arithmetic(
        "27,513,223 - 14,866,375 = 12,646848")["ok"] is True


def test_equation_is_skipped_when_units_are_mixed():
    """`100억 + 50억 = 15,000,000,000원` 은 환산이다. 좌·우변이 달라 보여도 정당하다."""
    out = critic.verify_answer_arithmetic("100억 + 50억 = 15,000,000,000원")
    assert out["ok"] is True


def test_equation_ignores_chains_with_multiplication():
    """×·÷ 가 섞이면 좌→우 계산이 연산 우선순위를 틀린다 — **조용히 안 본다.**"""
    out = critic.verify_answer_arithmetic("(2,589,355 - 3,022,314) / 3,022,314 × 100 = -14.3")
    assert out["ok"] is True


def test_equation_premise_is_counted_separately_from_firing():
    """전제(등식이 있었나)와 발화(틀렸나)를 섞으면 규칙이 낡는 것을 못 본다."""
    silent = critic.verify_answer_arithmetic("공시에서 확인되지 않습니다.")
    assert silent["checked"] == [] and set(silent["skipped"]) == set(critic.ARITHMETIC_KINDS)


# --- ② 원값 ↔ 괄호 환산값 (Q-018) ---------------------------------------------

def test_restatement_catches_the_q018_transcription_error():
    """🔴 실측 Q-018. 괄호는 **옳게 읽었고**(1조 8,422억원) 원값 13자리에서 한 자리를 틀렸다.

    `_compare` 는 후보 중 하나만 맞으면 통과시키므로 맞는 쪽(`1조 8,422억원`)이 틀린 쪽을
    가려 준다. 답변 안에서 둘을 직접 맞대야 보이는 실패다.
    """
    out = critic.verify_answer_arithmetic(
        "- 최종 정정 금액: 1,843,200,000,000원 (1조 8,422억원)")
    assert out["ok"] is False
    assert out["failed_kinds"] == ["restatement"]
    assert out["mismatches"][0]["expected"] == 1_842_200_000_000


def test_restatement_accepts_a_rounded_conversion():
    """우리 근거가 붙이는 표기 그대로다 — 49,289.68억 → `4조 9,290억원`."""
    assert critic.verify_answer_arithmetic(
        "계약금액은 4,928,968,079,043원 (4조 9,290억원)입니다.")["ok"] is True


def test_restatement_accepts_a_truncated_conversion():
    """🔴 실측 Q-065(**정답**). 25,908.50억을 절사해 `2조 5,908억원` 으로 썼다.

    허용 오차가 표기 단위의 **반 칸**이면 여기서 거짓 경보가 난다. 모델은 반올림도
    절사도 하므로 한 칸을 준다.
    """
    assert critic.verify_answer_arithmetic(
        "차이는 2,590,850,129,043원 (2조 5,908억원)입니다.")["ok"] is True


def test_restatement_catches_a_digit_transposition():
    """실측 Q-109. 2조 8,076억을 `2조 7,078억` 으로 옮겼다 — 약 998억 차이다."""
    out = critic.verify_answer_arithmetic("합계는 2,807,598,070,800원 (2조 7,078억원)입니다.")
    assert out["ok"] is False and out["failed_kinds"] == ["restatement"]


def test_restatement_reads_bold_markup_between_the_pair():
    """실측 답변은 `**276,900,000,000원**(2,769억원)` 처럼 굵게 표기를 끼워 넣는다."""
    assert "restatement" in critic.verify_answer_arithmetic(
        "**276,900,000,000원**(2,769억원) 증가했습니다.")["checked"]


def test_two_unrelated_amounts_are_not_a_restatement_pair():
    """괄호로 묶여 있지 않으면 짝이 아니다 — 서로 다른 계약의 금액을 맞대면 안 된다."""
    out = critic.verify_answer_arithmetic(
        "A 계약은 1,102,000,000,000원이고 B 계약은 1,350,000,000,000원입니다.")
    assert out["ok"] is True and "restatement" in out["skipped"]


@pytest.mark.parametrize("answer", ["", None])
def test_arithmetic_survives_an_empty_answer(answer):
    out = critic.verify_answer_arithmetic(answer)
    assert out["ok"] is True and out["mismatches"] == []


def test_arithmetic_matches_the_other_verifiers_contract():
    """Phase I 는 세 판정을 같은 모양으로 집계한다."""
    out = critic.verify_answer_arithmetic("1 + 1 = 2")
    for key in ("ok", "llm_calls", "checked", "skipped", "failed_kinds",
                "hard_kinds", "failure_stage", "reason"):
        assert key in out, key


# --- ③ 저장 응답 전수 재채점 — 겨냥과 거짓 경보를 함께 박는다 ------------------
# **유료 재측정이 아니라 재채점이다.** 저장된 HCX 응답만 읽으므로 LLM 0회 · 지출 0원이다.
# 분모에 라벨을 붙인다: 그리드 88 · 밸류체인 12.

#: 스냅샷별 (응답 디렉터리, 라벨 보고서, 발화할 문항).
#: 🔴 **정답 문항 거짓 경보는 어느 스냅샷에서든 0 이어야 한다** — 아래 단언이 그것이다.
#: 다만 `phase_h`·`A6` 의 Q-034 는 예외로 명시한다. 그 답변의 합계는 **실제로 틀렸고**
#: (817,100 vs 817,900) 서술형 판정기가 통과시킨 것이라 검사가 옳다. 현 세대(v3)에는
#: 그 답변이 없다 — v3 에서는 거짓 경보가 0 건이다.
ARITHMETIC_SNAPSHOTS = {
    "phase_h(42)": ("artifacts/eval/responses",
                    "artifacts/eval/error_analysis.json", {"Q-034"}),
    "A6(42)": ("artifacts/ablation/A6/responses",
               "artifacts/ablation/A6/report.json", {"Q-034", "Q-044"}),
    "A7(42)": ("artifacts/ablation/A7/responses",
               "artifacts/ablation/A7/report.json", {"Q-044"}),
    "v2/A8(88)": ("artifacts/ablation/v2/A8/responses",
                  "artifacts/ablation/v2/A8/report.json", {"Q-018", "Q-044"}),
    "v2/vc(12)": ("artifacts/ablation/v2/vc/A8/responses",
                  "artifacts/ablation/v2/vc/A8/report.json", {"Q-109", "Q-115"}),
    "v3/A8(88)": ("artifacts/ablation/v3/A8/responses",
                  "artifacts/ablation/v3/A8/report.json", {"Q-018", "Q-044"}),
    "v3/L4(88)": ("artifacts/ablation/v3/L4/responses",
                  "artifacts/ablation/v3/L4/report.json", {"Q-018", "Q-044"}),
    "v3/ORACLE(88)": ("artifacts/ablation/v3/ORACLE/responses",
                      "artifacts/ablation/v3/ORACLE/report.json", set()),
    "v3/vc/A8(12)": ("artifacts/ablation/v3/vc/A8/responses",
                     "artifacts/ablation/v3/vc/A8/report.json", {"Q-109", "Q-115"}),
    "v3/vc/OR(12)": ("artifacts/ablation/v3/vc/ORACLE/responses",
                     "artifacts/ablation/v3/vc/ORACLE/report.json", {"Q-109"}),
}

#: 검사가 옳은데 판정기가 통과시킨 문항. **묵인이 아니라 명시다** — 목록이 늘면
#: 그때는 검사가 아니라 채점을 봐야 한다.
JUDGED_CORRECT_BUT_ARITHMETIC_IS_WRONG = {"Q-034"}


def _snapshot(name):
    resp_dir, labels, _ = ARITHMETIC_SNAPSHOTS[name]
    resp_dir, labels = _ROOT / resp_dir, _ROOT / labels
    if not (resp_dir.is_dir() and labels.exists()):
        return None
    rows = {r["question_id"]: r
            for r in json.loads(labels.read_text(encoding="utf-8"))["rows"]}
    responses = {}
    for path in sorted(resp_dir.glob("Q-*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        responses[payload["question_id"]] = payload["response"]
    return responses, rows


@pytest.mark.parametrize("snapshot", sorted(ARITHMETIC_SNAPSHOTS))
def test_arithmetic_firing_on_the_stored_responses(snapshot):
    """겨냥과 거짓 경보를 **한 단언 안에서** 고정한다. 한쪽만 보면 규칙이 낡는다."""
    loaded = _snapshot(snapshot)
    if loaded is None:
        pytest.skip(f"{snapshot} 산출물이 없다")
    responses, rows = loaded
    expected = ARITHMETIC_SNAPSHOTS[snapshot][2]

    fired, false_alarms = set(), []
    for qid, resp in sorted(responses.items()):
        out = critic.verify_answer_arithmetic(resp.get("answer") or "")
        if out["ok"]:
            continue
        fired.add(qid)
        if rows.get(qid, {}).get("correct") \
                and qid not in JUDGED_CORRECT_BUT_ARITHMETIC_IS_WRONG:
            false_alarms.append((qid, out["reason"]))

    assert false_alarms == [], f"{snapshot} 정답 문항 거짓 경보: {false_alarms}"
    assert fired == expected, f"{snapshot} 발화가 달라졌다: {sorted(fired)}"


def test_the_current_generation_has_no_false_alarm_at_all():
    """🔴 현 세대(v3 그리드 88 × 3 arm = 264 답변)에는 예외조차 없다.

    옛 세대의 예외 하나(Q-034)가 면죄부로 굳지 않게, 현 세대는 따로 0 을 박는다.
    """
    for snapshot in ("v3/A8(88)", "v3/L4(88)", "v3/ORACLE(88)"):
        loaded = _snapshot(snapshot)
        if loaded is None:
            pytest.skip(f"{snapshot} 산출물이 없다")
        responses, rows = loaded
        for qid, resp in sorted(responses.items()):
            if rows.get(qid, {}).get("correct"):
                out = critic.verify_answer_arithmetic(resp.get("answer") or "")
                assert out["ok"], f"{snapshot} {qid} 정답 문항 거짓 경보: {out['reason']}"


def test_q044_and_q018_are_invisible_to_record_based_verification():
    """🔴 **왜 새 검사가 필요한지**를 산출물로 붙든다.

    Q-044 는 근거 접수번호가 2건인데 정형 레코드가 **0건**이라 `verify_calculation` 이
    아예 서지 못한다(정기공시). Q-018 은 레코드가 4건인데도 통과한다 — 답변이 쓴
    `1조 8,422억원` 이 근거와 정확히 같아서 `_compare` 가 그 후보를 골라 준다.
    """
    import re

    from argus.agent import tools

    loaded = _snapshot("v3/A8(88)")
    if loaded is None:
        pytest.skip("v3/A8 산출물이 없다")
    responses, _ = loaded
    rcept = re.compile(r"접수번호\s*(\d{14})")

    def _reports(resp):
        records = [tools._record_of(no) for no in
                   dict.fromkeys(rcept.findall(resp["retrieved_context"] or ""))]
        return [critic.verify_calculation(resp["answer"], r) for r in records if r]

    assert _reports(responses["Q-044"]) == [], "Q-044 에 정형 레코드가 생겼다 — 전제 재확인"
    q018 = critic.aggregate_calculation(_reports(responses["Q-018"]))
    assert q018["failed"] is False, "레코드 대조가 Q-018 을 잡기 시작했다 — 전제 재확인"

    for qid in ("Q-044", "Q-018"):
        assert critic.verify_answer_arithmetic(responses[qid]["answer"])["ok"] is False


# --- ④ I-2 — 게이트가 검사를 끈 것을 **세어서** 남긴다 -------------------------
# 종전에는 `skipped` 에만 실려 "답변이 그 수치를 말하지 않았다"(정상)와 구별되지 않았다.

def test_a_closed_label_gate_is_counted_not_just_skipped():
    record = {"amount": 2_000_113_200_000, "amount_type": "조달금액"}
    out = critic.verify_calculation("시설자금은 454,100,000,000원입니다.", record)
    assert out["gated"] == ["amount"] and "amount" in out["skipped"]
    assert critic.aggregate_calculation([out])["n_gated"] == 1


def test_an_answer_that_simply_says_nothing_is_not_counted_as_gated():
    record = {"amount": 2_000_113_200_000, "amount_type": "조달금액"}
    out = critic.verify_calculation("총 조달 금액은 확인되지 않습니다.", record)
    assert out["gated"] == []


def test_the_gate_blind_spot_is_measured_on_the_current_generation():
    """🔴 I-2 의 실측 근거를 코드가 붙들고 있게 한다. **이 수가 뛰면 다시 재라.**

    v3/A8 그리드 88 의 근거 레코드 중 `amount` 를 가진 것은 149건이고, 그중
    레이블 게이트가 금액 검사를 끄는 것이 64건이다. 게이트를 열어 보는 A/B 는
    R2B 보고서에 있다 — **새 적중 0 · Q-066 적중 상실 1** 이라 열지 않았다.
    """
    import re

    from argus.agent import tools

    loaded = _snapshot("v3/A8(88)")
    if loaded is None:
        pytest.skip("v3/A8 산출물이 없다")
    responses, _ = loaded
    rcept = re.compile(r"접수번호\s*(\d{14})")

    with_amount = gated = 0
    for _, resp in sorted(responses.items()):
        for no in dict.fromkeys(rcept.findall(resp["retrieved_context"] or "")):
            record = tools._record_of(no)
            if record is None or record.get("amount") is None:
                continue
            with_amount += 1
            if critic.verify_calculation(resp["answer"], record)["gated"]:
                gated += 1
    assert (with_amount, gated) == (149, 64), (
        f"게이트 실측이 움직였다: amount 보유 {with_amount} · 게이트 차단 {gated}")
