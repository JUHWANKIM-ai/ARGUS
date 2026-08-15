"""답변의 내적 일관성 검사 (`critic.verify_answer_arithmetic`) — LLM 0회.

`tests/test_critic.py` 는 **근거 레코드 대조**(`verify_calculation`)의 회귀 감시를
들고 있고 옛 수가 상수로 박혀 있다. 그 구조를 건드리지 않으려고 새 축은 여기에 쓴다
(계획서 `Phase 2` Files — `tests/test_calc_checks.py(신설)`).

여기서 고정하는 것은 두 가지다.
  ① 겨냥 — Q-044(등식)·Q-018(원값↔환산값)을 실제 저장 응답 원문으로 잡는다
  ② 회귀 — 저장 응답 전수 재채점에서 **정답 문항 거짓 경보가 몇 건인지**를 박는다

🔴 v4 `R1-1` 이 셋째 축을 붙였다 — **`A20`(재계산값 치환)**. 검사가 정답을 정확히
재계산해 놓고 파이프라인이 각주만 붙이던 것(탐지 100% · 교정 0%)을 arm 으로 만든다.
"""
import json
import pathlib
import types
from dataclasses import replace

import pytest

from argus import ablation
from argus.agent import critic, pipeline
from argus.agent.budget import Budget
from argus.agent.critic import verify_number_provenance
from argus.agent.prompts import build_answer_messages
from scripts import error_analysis as ea

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


def test_equation_reads_a_bold_wrapped_result():
    """🔴 실측 Q-107(밸류체인 **오답**). `= **4,967,110,535,840원**` 이라 종전 `\\s*=\\s*` 는
    등식을 **아예 매치하지 못했다** — 덧셈이 실제로 틀렸는데 `checked` 에 `equation` 이
    없었고, 사슬이 끊긴 것(정당한 미검사)과 구별되지 않았다.

    같은 파일의 `_RESTATE_OPEN` 이 이미 같은 이유로 `[\\s*_]*` 를 쓴다
    (`test_restatement_reads_bold_markup_between_the_pair` 참조).
    """
    out = critic.verify_answer_arithmetic(
        "- 1,250,850,298,678 + 2,081,254,239,072 + 1,715,754,987,100 = "
        "**4,967,110,535,840원**")
    assert out["ok"] is False and out["failed_kinds"] == ["equation"]
    assert out["mismatches"][0]["expected"] == 5_047_859_524_850
    assert out["mismatches"][0]["claimed_value"] == 4_967_110_535_840


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
    # 🔴 R2E 신설 — `L1`·`L2`·`L3` 이 현 세대에 처음 측정됐다. 전수 집계를 이 표에
    #    넣지 않으면 "현 세대 전 arm" 이라는 말이 세 arm 을 빠뜨린 채로 굳는다.
    #    `L1` 만 `Q-018` 이 빠진다 — 정정체인 hop 을 끄면 그 답변이 아예 달라진다.
    "v3/L1(88)": ("artifacts/ablation/v3/L1/responses",
                  "artifacts/ablation/v3/L1/report.json", {"Q-044"}),
    "v3/L2(88)": ("artifacts/ablation/v3/L2/responses",
                  "artifacts/ablation/v3/L2/report.json", {"Q-018", "Q-044"}),
    "v3/L3(88)": ("artifacts/ablation/v3/L3/responses",
                  "artifacts/ablation/v3/L3/report.json", {"Q-018", "Q-044"}),
    "v3/L4(88)": ("artifacts/ablation/v3/L4/responses",
                  "artifacts/ablation/v3/L4/report.json", {"Q-018", "Q-044"}),
    "v3/ORACLE(88)": ("artifacts/ablation/v3/ORACLE/responses",
                      "artifacts/ablation/v3/ORACLE/report.json", set()),
    # 🔴 R2G 신설 — `A8-lean`(규칙 8·9 를 뺀 arm). 발화 집합이 `A8` 과 **같다**
    #    (`Q-018`·`Q-044`) 는 사실이 이 arm 의 판정에서 값을 한다: 답변 16건이 전부
    #    바뀌었는데도 산술 결함의 자리가 안 옮겨갔다. 🔴 다만 `Q-018` 은 **결함의 성질이
    #    바뀌었다** — `restatement`(13자리 중 한 자리) → `equation`+`amount`(10배 자릿수
    #    셋 + 뺄셈). 발화 집합만 보면 그 변화가 안 보인다.
    "v3/A8-lean(88)": ("artifacts/ablation/v3/A8-lean/responses",
                       "artifacts/ablation/v3/A8-lean/report.json",
                       {"Q-018", "Q-044"}),
    # 🔴 밸류체인은 발화가 **줄었다** (3 → 1). 개선이 아니다 — `Q-110`·`Q-115` 의 새
    #    답변이 금액·계산식을 **아예 쓰지 않아** `checked` 가 빈 목록이 됐다. 오답
    #    `Q-115` 는 여전히 오답인데 이제 검사가 볼 것이 없다. 부풀림 1 → 0 도 같은
    #    까닭이고, 그것을 성적 개선으로 읽으면 안 된다 (`A8lean-report.md` §4).
    #    🔴 v4 R1-1 Step 0a — `Q-107` 이 추가됐다. 굵게 감싼 등식 결과를 보게 되면서
    #    발화한 것이고 **답변은 바뀌지 않았다** (이미 오답).
    "v3/vc/A8-lean(12)": ("artifacts/ablation/v3/vc/A8-lean/responses",
                          "artifacts/ablation/v3/vc/A8-lean/report.json",
                          {"Q-107", "Q-109"}),
    # 🔴 R2D 재측정으로 `Q-110` 이 추가됐다 — 새 답변이 `590,947,351,842원 (591억원)`
    #    이라는 10배 어긋난 재기술쌍을 만들었고 judge 는 통과시켰다
    #    (`JUDGED_CORRECT_BUT_ARITHMETIC_IS_WRONG` 참조).
    # 🔴 v4 R1-1 Step 0a — `Q-107` 이 추가됐다. `- 1,250,850,298,678 + 2,081,254,239,072
    #    + 1,715,754,987,100 = **4,967,110,535,840원**` 의 좌변합은 5,047,859,524,850 이라
    #    **덧셈이 실제로 틀렸다.** 종전 정규식이 굵게 감싼 결과를 못 봐서 놓쳤다.
    "v3/vc/A8(12)": ("artifacts/ablation/v3/vc/A8/responses",
                     "artifacts/ablation/v3/vc/A8/report.json",
                     {"Q-107", "Q-109", "Q-110", "Q-115"}),
    #    🔴 v4 R1-1 Step 0a — `Q-115` 가 추가됐다 (같은 기전 · 이미 오답).
    "v3/vc/OR(12)": ("artifacts/ablation/v3/vc/ORACLE/responses",
                     "artifacts/ablation/v3/vc/ORACLE/report.json",
                     {"Q-109", "Q-115"}),
}

#: 🔴 현 세대 · 전체 88 코호트의 스냅샷 — **위 표에서 파생한다.** 리터럴로 또 적으면
#: arm 을 하나 더 재는 라운드가 두 곳을 고쳐야 하고, 한 곳을 빼먹으면 "현 세대 전 arm"
#: 이라는 말이 조용히 좁아진다 (R2E 가 `L1`·`L2`·`L3` 를 넣을 때 실제로 두 곳이었다).
CURRENT_GENERATION_FULL = tuple(k for k in ARITHMETIC_SNAPSHOTS
                                if k.startswith("v3/") and k.endswith("(88)"))

#: 검사가 옳은데 판정기가 통과시킨 문항. **묵인이 아니라 명시다** — 목록이 늘면
#: 그때는 검사가 아니라 채점을 봐야 한다.
#:
#: 🔴 **2026-08-14 (R2D 재측정) — 목록이 늘었다. 그 경고가 발화한 것이다.**
#: `Q-110`(밸류체인 · judge 가 정답 처리)의 답변이
#: `590,947,351,842원 (591억원)` 을 썼다. 590,947,351,842원은 **5,909억원**이므로
#: 괄호 환산이 **10배 틀렸다**. `restatement` 검사가 옳고 채점이 통과시킨 것이다.
#: `Q-034` 와 같은 종류이고 같은 방향이다 — 두 건 다 **개방형 judge** 가 놓쳤다.
#: 🔴 이제 이 목록은 둘이다. R2B 가 적어 둔 규칙대로 **다음은 검사가 아니라 채점을
#: 봐야 한다** — 개방형 판정기가 수치 일관성을 아예 안 보고 있을 가능성이 있다.
#:
#: 🔴 **2026-08-14 (R2E) — 현 세대 전 arm 전수로 셌다. 세대를 갈라 읽어야 한다.**
#: 분모: 전체 88 × 6 arm(`A8`·`L1`·`L2`·`L3`·`L4`·`ORACLE`) + 밸류체인 12 × 2 arm
#: (`vc/A8`·`vc/ORACLE`) = **552 답변**.
#:
#:     현 세대(v3) 전체 88     0건   🔴 그리드·회귀·정보한계 성적은 이 결함에 안 물렸다
#:     현 세대(v3) 밸류체인 12  1건   `Q-110` — `vc/A8` 에서만
#:     옛 세대(42문항)         1건   `Q-034` — `phase_h(42)`·`A6(42)`. v3 에는 없다
#:
#: 즉 이 상수의 두 원소는 **다른 세대의 것**이고, 현 세대에서 성적을 부풀리고 있는
#: 것은 `Q-110` 하나다. `vc/ORACLE` 의 `Q-110` 은 R2E 재측정에서 **오답으로 갈렸다**
#: (골든 6/6 을 전부 주입해도 틀렸다) — 그래서 오라클 쪽은 부풀지 않는다.
#:
#: 🔴 **두 건의 성질이 다르다. 섞어 읽으면 안 된다.**
#:  · `Q-034` — 합계 자체가 틀렸다(817,100 vs 817,900). 채점 축이 재는 것이 틀렸으므로
#:    **"오답인데 정답으로 채점됐다"** 가 맞다.
#:  · `Q-110` — 질문은 *"상장사는 어디이며 각각 어떤 역할"* 이고 `answer_key_points`
#:    여섯 개는 **전부 충족한다.** 틀린 것은 모델이 덤으로 붙인 한전기술 합계의 괄호
#:    환산(`590,947,351,842원 (591억원)` — 실제 5,909억원)이다. 즉 **채점 축 위에서는
#:    정답이고, 채점 축이 보지 않는 자리에서 10배가 틀렸다.** 채점기의 세 축
#:    (`covers_key_points`·`grounded`·`no_fabrication`)에 수치 자기일관성이 없다
#:    (`prompts.SYSTEM_OPEN_JUDGE` · `critic.judge_open` 을 읽어 확인했다).
#:    ⚠️ **단 `grounded` 는 원리적으로 잡을 수 있는 자리다** — `591억원` 은 근거에 없고
#:    합계 `590,947,351,842` 도 모델이 계산한 값인데 판정기가 `grounded=1` 을 줬다.
#:    판정기가 **파생 산술을 '근거에서 확인된 것' 으로 취급**하는 것으로 보이나
#:    `reason` 이 빈 문자열이라 **확인하지 못했다.** 다음 라운드가 볼 지점이 여기다.
#:    🔴 그래도 성적은 부풀려져 있다 — 공시 QA 로서 "정답" 이라고 부른 답변 안에 10배
#:    틀린 금액이 들어 있고, 그것을 읽는 사람은 잘못된 값을 얻는다.
#:    **밸류체인 12 의 `A8` 7/12 는 이 기준을 넣으면 6/12 다** (−8.3%p).
#:
#: 🔴 **이번 라운드에서 고치지 않았다.** 채점기를 바꾸면 전 세대 성적의 정의가 바뀌어
#: R2D·R2E 재측정과의 비교가 깨진다. 다음 라운드로 넘긴다 —
#: 처방 후보는 `critic.judge_open` 에 `verify_answer_arithmetic` 을 **하드 게이트가
#: 아니라 넷째 축**으로 붙이는 것이고, 붙이는 순간 전 세대 재채점이 필요하다.
#: 🔴 **R2F — 정본을 `scripts/error_analysis.py` 로 옮겼다. 여기는 재수출이다.**
#: 리터럴을 여기 두는 동안 게이트 렌더러(`scripts/ablation.py` §11)는 같은 사실을
#: **문자열로** 들고 있었고, 재생성된 v3 게이트가 §0·§4(계산 34/46 · 7/12)와 §11
#: (문자열 28/46 · 6/12)에서 서로를 반박했다. 이제 렌더러가 상수를 읽는다.
#: **값을 여기서 고치지 마라 — `ea.JUDGED_CORRECT_ARITHMETIC_WRONG` 을 고쳐라.**
JUDGED_CORRECT_BUT_ARITHMETIC_IS_WRONG = set(ea.JUDGED_CORRECT_ARITHMETIC_WRONG)


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
    """🔴 현 세대(v3 전체 88 × 6 arm = 528 답변)에는 예외조차 없다.

    옛 세대의 예외 하나(Q-034)가 면죄부로 굳지 않게, 현 세대는 따로 0 을 박는다.
    🔴 R2E 에서 `L1`·`L2`·`L3` 가 측정되면서 264 → 528 답변으로 늘었다. 밸류체인 12 는
    여기 없다 — 그쪽에는 `Q-110` 이라는 실재하는 예외가 있고, 그 예외는
    `JUDGED_CORRECT_BUT_ARITHMETIC_IS_WRONG` 이 명시로 들고 있다.
    """
    # 🔴 arm 목록을 두 번째로 손으로 적지 않는다 (R2F). 종전에는 여섯 이름이 여기
    #    리터럴로 또 있었고, R2E 가 `L1`·`L2`·`L3` 를 `ARITHMETIC_SNAPSHOTS` 에 넣을 때
    #    **두 곳을 다 고쳐야** 했다 — 이 저장소가 여섯 번 겪은 "한쪽만 갱신되는 짝" 이다.
    #    이제 위 표에서 파생하므로 arm 을 하나 더 재면 자동으로 여기에 들어온다.
    assert CURRENT_GENERATION_FULL, "현 세대 전체 88 스냅샷이 표에서 사라졌다"
    checked = 0
    for snapshot in CURRENT_GENERATION_FULL:
        loaded = _snapshot(snapshot)
        if loaded is None:
            continue                      # 🔴 루프 안에서 skip 하면 나머지 arm 이 안 돈다
        responses, rows = loaded
        for qid, resp in sorted(responses.items()):
            if rows.get(qid, {}).get("correct"):
                out = critic.verify_answer_arithmetic(resp.get("answer") or "")
                assert out["ok"], f"{snapshot} {qid} 정답 문항 거짓 경보: {out['reason']}"
                checked += 1
    if not checked:
        pytest.skip("현 세대 전체 88 산출물이 없다")


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
    # 🔴 **2026-08-14 (R2D) — 149/64 → 152/67.** 색인 재빌드로 근거가 바뀌면서
    #    amount 를 가진 근거 레코드가 3건 늘고 그 3건이 전부 게이트에 걸렸다.
    #    비율은 42.95% → 44.08% 로 사실상 그대로다 — 게이트의 성질이 바뀐 것이 아니라
    #    분모가 커진 것이다. R2B §7-⑥ 이 "이 수가 크게 뛰면 다시 재야 한다" 고 적었고,
    #    +3 은 그 문턱이 아니라고 판단했다.
    assert (with_amount, gated) == (152, 67), (
        f"게이트 실측이 움직였다: amount 보유 {with_amount} · 게이트 차단 {gated}")


# --- ⑤ A20 — 검사가 낸 유일한 재계산값으로 답변을 치환한다 ----------------------
# 🔴 **이 절이 재는 것은 '교정' 이다.** `verify_calculation` 은 v1 부터 정답을 정확히
# 재계산해 왔다 — Q-097 의 `per_unit` 재계산값 207,684,631,210 은 골든과 **바이트까지
# 같다.** 그런데 파이프라인은 그것을 각주로만 붙이고 버렸고, 채점기는 그 각주를 일부러
# 잘라내고 채점한다(`error_analysis.strip_verify_tail`). 즉 **탐지 100% · 교정 0%** 였다.
#
# 🔴 우리 착상이 아니라 **CoT 논문 부록 B 의 처방**이다 — *"모델이 스스로 쓴 등식마다
# 사후 계산기를 돌리고, 문자열 매칭으로 결과를 전파한다."*

#: 실측 Q-097 의 구조를 그대로 옮긴 최소 픽스처. 답변이 **같은 값을 두 표기로** 쓴다는
#: 것이 이 문항의 핵심이다 — 하나만 갈아 끼우면 나머지가 남아 답변이 스스로 모순된다.
Q097_RECORD = {"amount": 2_492_215_574_520, "amount_type": "투자금액"}
Q097_ANSWER = ("- 총 투자금액: 2,492,215,574,520원\n"
               "- 선박 척수: 12척\n"
               "- 1척 당 평균 투자금액 = 207,848,465,710원\n"
               "따라서 1척당 평균 투자금액은 약 2,078억 원입니다.")


def test_aggregate_reports_a_unique_recomputed_value():
    """🔴 재계산값이 reason 문자열에만 있으면 치환에 쓸 수 없다."""
    report = {"ok": False, "checked": ["per_unit"], "hard_kinds": [],
              "reason": "척당 금액 207,684,631,210 과 다르다",
              "recomputed": {"per_unit": 207684631210.0}}
    agg = critic.aggregate_calculation([report])
    assert agg["recomputed"] == {"per_unit": 207684631210.0}


def test_aggregate_withholds_recomputed_when_records_disagree():
    """🔴 정정 체인은 원공시와 정정본 금액이 다른 것이 정상이다 (227,500 vs 114,800).
    값이 갈리면 치환하지 않는다."""
    a = {"ok": False, "checked": ["amount"], "hard_kinds": [], "reason": "x",
         "recomputed": {"amount": 227500.0}}
    b = {"ok": False, "checked": ["amount"], "hard_kinds": [], "reason": "y",
         "recomputed": {"amount": 114800.0}}
    assert critic.aggregate_calculation([a, b])["recomputed"] is None


def test_verify_calculation_exports_the_value_it_already_recomputed():
    """실측 Q-097. `reason` 문장에 박혀 있던 값을 **구조화된 필드로** 함께 낸다."""
    out = critic.verify_calculation(Q097_ANSWER, Q097_RECORD)
    assert out["recomputed"] == {"per_unit": 207_684_631_210.0}
    # 🔴 `reason` 의 형식은 한 글자도 바뀌지 않는다 — `scripts/error_analysis.py` 와
    #    게이트 문서가 그것을 읽는다.
    assert out["reason"] == "per_unit: 답변 2,078억 vs 재계산 207,684,631,210"


def test_recomputed_omits_period_because_its_expected_value_is_text():
    """`period` 의 기대값은 `"8년 5개월"` 이다 — 수치가 아니라 갈아 끼울 수 없다."""
    out = critic.verify_calculation(
        "계약기간은 9년 5개월입니다.",
        {"period_start": "2025-07-24", "period_end": "2033-12-31"})
    assert out["failed_kinds"] == ["period"] and out["recomputed"] == {}


def test_recomputed_omits_restatement_because_the_wrong_side_is_unknown():
    """🔴 실측 Q-018 은 괄호가 옳고 원값이 틀렸는데 `Q-110` 은 그 반대다.

    어느 쪽이 틀린지 검사가 모르므로 치환 자리를 못 박는다. 계획서도 `Q-018` 을 `A15`
    로 돌렸다 — *"재계산이 중간값이라 A20 로는 안 됨"*.
    """
    out = critic.verify_answer_arithmetic(
        "- 최종 정정 금액: 1,843,200,000,000원 (1조 8,422억원)")
    assert out["failed_kinds"] == ["restatement"] and out["recomputed"] == {}


def test_recomputed_carries_the_equation_total():
    """실측 Q-044. 레코드가 0건인 문항이라 이 축이 유일한 재계산 경로다."""
    out = critic.verify_answer_arithmetic(
        r"$$1,699,923 + 665,945 = 1,365,868 \text{억원}$$")
    assert out["recomputed"] == {"equation": 2_365_868.0}


def test_substitution_rewrites_every_notation_of_the_same_value():
    """🔴 실측 Q-097 은 같은 값을 `207,848,465,710원` 과 `약 2,078억 원` 으로 **두 번** 쓴다.

    보고된 후보(`2,078억`) 하나만 갈아 끼우면 정확한 자리가 틀린 채로 남아 답변이
    스스로 모순된다. 그리고 채점은 원 단위 13자리를 본다 — 그 자리를 못 고치면 Δ 가 0 이다.
    """
    report = critic.verify_calculation(Q097_ANSWER, Q097_RECORD)
    fixed, done = critic.substitute_recomputed(
        Q097_ANSWER, [report], critic.aggregate_calculation([report])["recomputed"])
    assert "207,684,631,210원" in fixed          # 골든과 바이트까지 같다
    assert "207,848,465,710" not in fixed
    assert "약 2,077억 원" in fixed              # 표기의 배율을 유지한다
    assert len(done) == 2, done
    # 총액은 기대값의 12배다 — 치환 창(`SUBSTITUTE_LOG_TOLERANCE`) 밖이라 안 건드린다.
    assert "2,492,215,574,520원" in fixed


def test_substitution_is_withheld_when_the_value_is_not_unique():
    """유일성 판정이 `None` 이면 한 글자도 바꾸지 않는다."""
    report = critic.verify_calculation(Q097_ANSWER, Q097_RECORD)
    assert critic.substitute_recomputed(Q097_ANSWER, [report], None) == (Q097_ANSWER, [])


def test_substitution_never_matches_a_longer_number_that_contains_the_target():
    """🔴 `1,365,868` 이 `21,365,868` 의 부분문자열로 걸리면 답변이 망가진다."""
    text = "합계는 21,365,868 이고 소계는 1,699,923 + 665,945 = 1,365,868 이다."
    report = critic.verify_answer_arithmetic(text)
    fixed, done = critic.substitute_recomputed(text, [report], report["recomputed"])
    assert "21,365,868" in fixed and "= 2,365,868 " in fixed and len(done) == 1


def test_substitution_leaves_a_compound_notation_alone():
    """`4조 9,671억원` 은 어느 자리를 바꿀지 정해지지 않는다 — 건드리지 않는다."""
    assert critic._rendered("4조 9,671억원", 5_047_859_524_850.0) is None
    assert critic._rendered("2,078억", 207_684_631_210.0) == "2,077억"


# --- ⑥ arm 플래그와 파이프라인 배선 --------------------------------------------

A20_ARM = ablation.ARMS["A20"]


def test_a20_is_registered_as_a_side_branch_arm():
    """🔴 등록이 없으면 `--arm A20` 이 아예 안 돈다 (`R1-1` 이 플래그만 넣고 갔다).

    `A8-lean`·`ORACLE` 과 같은 곁가지다 — 사다리의 칸도 LOO 도 아니고, 같은 자리에서
    검증 단계의 처분만 갈아 끼운 변형이다. `LADDER`/`LOO_REMOVED` 에 넣으면 누적
    곡선의 Δ(직전) 이 두 arm 의 차이가 아닌 것을 가리킨다.
    """
    assert "A20" in ablation.ARMS and "A20" in ablation.RUN_ORDER
    assert "A20" not in ablation.LADDER
    assert "A20" not in ablation.LOO_REMOVED
    assert "A20" not in ablation.DIAGNOSTIC_ARMS
    assert "A20" in ablation.SIDE_ARMS      # 없으면 산출물 문서에 한 글자도 안 남는다
    # 단일 변수 조작 — 하나라도 더 바뀌면 Δ 가 '치환' 의 것이 아니다.
    a8, a20 = ablation.flags("A8"), ablation.flags("A20")
    assert {k for k in a20 if a20[k] != a8[k]} == {"substitute_verified"}
    assert a20["substitute_verified"] is True
    # 채택 후보라 dense·진단 arm 보다 먼저 확보한다.
    order = list(ablation.RUN_ORDER)
    assert order.index("A20") < order.index("A2F") < order.index("ORACLE")


def test_the_substitution_flag_is_off_by_default_and_depends_on_the_verifier():
    """🔴 기본값을 바꾸지 않는 것이 이 arm 의 전부다 — 현 동작은 명시적 설계다."""
    assert "substitute_verified" in ablation.FLAG_NAMES
    assert ablation.A8.substitute_verified is False
    # 🔴 `R1-9` 가 `provenance_gate` 를 같은 부모 밑에 넣었다 — 목록 동치가 아니라
    #    **포함**으로 본다. 동치로 박아 두면 `verifier` 밑에 자식이 하나 늘 때마다
    #    이 단언이 이유 없이 깨지고, 깨진 김에 통째로 지우는 사고가 난다.
    assert "substitute_verified" in ablation.FLAG_DEPENDENCIES["verifier"]
    with pytest.raises(ValueError, match="substitute_verified"):
        replace(ablation.A0, name="bad", substitute_verified=True)


def _verify_stored(snapshot, qid, arm):
    """저장 응답을 `pipeline._verify` 에 그대로 태운다. LLM 0회 · 네트워크 0회."""
    loaded = _snapshot(snapshot)
    if loaded is None:
        pytest.skip(f"{snapshot} 산출물이 없다")
    responses, rows = loaded
    resp = responses[qid]
    body = ea.strip_verify_tail(resp["answer"])
    r = types.SimpleNamespace(companies=rows.get(qid, {}).get("route_corps") or [])
    trace: list[str] = []
    with ablation.use(arm):
        return body, pipeline._verify(resp["question"], body,
                                      resp["retrieved_context"], r, trace), trace


def test_the_arithmetic_check_is_not_wired_in_until_the_flag_is_on(monkeypatch):
    """🔴 배선만으로 각주가 늘어나면 그 자체가 답변 변화이고 `A20` 의 Δ 와 섞인다.

    실측(v3 세대 664 답변 전수)에서 배선만으로 **19건**의 각주 문자열이 달라졌다 —
    그래서 계획서 Step 0b 의 지시대로 배선도 플래그 뒤에 두었다. 여기서는 그것을
    호출 여부로 못 박는다.
    """
    calls = []
    monkeypatch.setattr(pipeline, "verify_answer_arithmetic",
                        lambda answer: calls.append(answer) or {
                            "ok": True, "checked": [], "hard_kinds": [], "reason": "",
                            "mismatches": [], "recomputed": {}})
    _verify_stored("v3/A8(88)", "Q-097", ablation.A8)
    assert calls == [], "플래그가 꺼져 있는데 산술 검사가 불렸다"
    _verify_stored("v3/A8(88)", "Q-097", A20_ARM)
    assert len(calls) == 1


def test_the_flag_off_path_only_footnotes_the_q097_defect():
    """종전 동작 — 정답을 정확히 재계산해 놓고 **각주만** 붙인다 (교정 0%)."""
    body, out, trace = _verify_stored("v3/A8(88)", "Q-097", ablation.A8)
    assert ea.strip_verify_tail(out) == body, "플래그가 꺼져 있는데 본문이 바뀌었다"
    assert "재계산 207,684,631,210" in out
    assert not any("substituted" in t for t in trace)


def test_the_flag_on_path_corrects_the_q097_answer_body():
    """🔴 `A20` 의 겨냥 하나. 치환 후 **본문**에 골든이 바이트까지 들어온다."""
    body, out, trace = _verify_stored("v3/A8(88)", "Q-097", A20_ARM)
    fixed = ea.strip_verify_tail(out)
    assert fixed != body
    assert "207,684,631,210원" in fixed and "207,848,465,710" not in fixed
    assert any("substituted" in t for t in trace)
    # 채점기가 각주를 떼고 채점해도 정답이 된다 — 종전에는 각주가 유일한 통로였다.
    assert "정정했습니다" in out


def test_the_flag_on_path_corrects_the_q044_sum():
    """🔴 `A20` 의 겨냥 둘. 레코드가 0건이라 **배선 없이는 발화조차 없다.**"""
    body, out, trace = _verify_stored("v3/A8(88)", "Q-044", A20_ARM)
    fixed = ea.strip_verify_tail(out)
    assert "2,365,868" in fixed and "1,365,868" not in fixed
    assert any("substituted=equation" in t for t in trace)
    # 플래그를 끄면 각주조차 안 붙는다 (v3 의 실제 동작).
    off_body, off_out, _ = _verify_stored("v3/A8(88)", "Q-044", ablation.A8)
    assert off_out == off_body


def test_substitution_is_withheld_on_the_valuechain_sum_questions():
    """🔴 **유일성 조건이 실제로 막는 자리를 붙든다.** 밸류체인 합산 문항은 답변이 여러
    레코드의 금액을 더하는데, 레코드별 `amount` 재계산값이 서로 다르다. 무조건 치환하면
    정당한 합계를 **한 레코드의 금액으로** 갈아엎는다 (실측 Q-107 은 3건 합산이 맞고
    덧셈만 틀렸다).
    """
    for qid in ("Q-107", "Q-109", "Q-110", "Q-115"):
        body, out, trace = _verify_stored("v3/vc/A8(12)", qid, A20_ARM)
        assert ea.strip_verify_tail(out) == body, f"{qid} 본문이 바뀌었다"
        assert not any("substituted" in t for t in trace), qid


def test_no_correct_answer_is_rewritten_on_the_current_generation():
    """🔴 **정답 문항의 본문은 한 글자도 바뀌면 안 된다.** 오답을 놓치는 것보다 나쁘다.

    현 세대 전체 88 × 6 arm 전수. 실측 결과는 0건이고, 여기서 그 0 을 박는다.
    """
    assert CURRENT_GENERATION_FULL, "현 세대 전체 88 스냅샷이 표에서 사라졌다"
    checked = 0
    for snapshot in CURRENT_GENERATION_FULL:
        loaded = _snapshot(snapshot)
        if loaded is None:
            continue                      # 🔴 루프 안에서 skip 하면 나머지 arm 이 안 돈다
        responses, rows = loaded
        for qid, resp in sorted(responses.items()):
            if not rows.get(qid, {}).get("correct"):
                continue
            body = ea.strip_verify_tail(resp["answer"])
            r = types.SimpleNamespace(
                companies=rows.get(qid, {}).get("route_corps") or [])
            with ablation.use(A20_ARM):
                out = pipeline._verify(resp["question"], body,
                                       resp["retrieved_context"], r, [])
            assert ea.strip_verify_tail(out) == body, f"{snapshot} {qid} 본문이 바뀌었다"
            checked += 1
    if not checked:
        pytest.skip("현 세대 전체 88 산출물이 없다")


# --- ⑦ A21 — 수치 출처 게이트 (`R1-9`) ------------------------------------------
# 🔴 `A15` 는 *"계산이 맞는가"* 를 묻고 `A21` 은 *"이 숫자가 근거에 있는가"* 를 묻는다.
# `Q-018` 은 근거에 없는 13자리 수를 답에 썼으므로 **후자가 직접 겨냥한다.**
#
# 🔴 `skipped` 판별이 이 검사의 절반이다. 우리가 계산해서 낸 수(`A20` 의 재계산값
# 포함)는 근거에 없는 것이 **정상**이고, 그것을 위반으로 세면 `A20` 과 정면으로 싸운다.

def test_long_numbers_must_appear_in_the_evidence_verbatim():
    """🔴 12자리 이상 숫자는 근거에 완전일치로 존재해야 한다."""
    evidence = "접수번호 20240312000736\n계약금액(원): 1,842,200,000,000"
    ok = "계약금액은 1,842,200,000,000원입니다."
    bad = "계약금액은 1,843,200,000,000원입니다."          # 근거에 없는 13자리
    assert verify_number_provenance(ok, evidence)["ok"] is True
    assert verify_number_provenance(bad, evidence)["ok"] is False


def test_short_numbers_are_not_gated():
    """🔴 연도·건수·비율은 근거에 없어도 정상이다. 12자리 미만은 검사하지 않는다."""
    assert verify_number_provenance("2024년 3건, 약 7.6% 입니다.", "접수번호 1")["ok"] is True


def test_computed_numbers_are_skipped_not_failed():
    """🔴 우리가 계산해서 낸 수는 근거에 없다 — 그건 위반이 아니라 skipped 다.
    verify_calculation 의 skipped 설계와 같은 이유다 (귀속 오염 방지)."""
    evidence = "접수번호 1\n총액: 2,492,215,574,520\n척수: 12"
    out = verify_number_provenance("척당 207,684,631,210원입니다.", evidence)
    assert out["ok"] is True
    assert "provenance" in out["skipped"]


def test_provenance_matches_the_other_verifiers_contract():
    """Phase I 는 네 판정을 같은 모양으로 집계한다."""
    out = verify_number_provenance("계약금액은 1,842,200,000,000원입니다.",
                                   "계약금액(원): 1,842,200,000,000")
    for key in ("ok", "llm_calls", "checked", "skipped", "mismatches", "failed_kinds",
                "hard_kinds", "failure_stage", "reason", "recomputed"):
        assert key in out, key
    assert out["llm_calls"] == 0


def test_provenance_never_feeds_the_a20_substitution():
    """🔴 이 검사는 **옳은 수가 무엇인지 모른다** — 근거의 어느 수를 말하려던 것인지
    정할 수 없다. `recomputed` 가 비어 있어야 `A20` 이 이 판정으로 답변을 갈아엎지
    않는다. 두 arm 은 판정만 OR 로 합쳐야 한다."""
    out = verify_number_provenance("계약금액은 1,843,200,000,000원입니다.",
                                   "계약금액(원): 1,842,200,000,000")
    assert out["ok"] is False
    assert out["recomputed"] == {}
    assert out["mismatches"][0]["targets"] == []
    assert critic.unique_recomputed([out]) is None
    assert out["failure_stage"] == "GENERATION"      # TOOL 이 아니다 — 옮겨 적기다


def test_a_correct_difference_the_answer_derived_is_skipped_not_failed():
    """🔴 실측 Q-065(**정답**). `4,928,968,079,043 - 2,338,117,950,000` 의 차는 근거에
    없다 — 우리가 뺀 값이다. 등식으로 적지 않아도 재계산으로 설명된다."""
    evidence = "계약금액(원): 4,928,968,079,043\n계약금액(원): 2,338,117,950,000"
    answer = ("Dukovany 계약금액은 4,928,968,079,043원이고 신한울은 2,338,117,950,000원"
              "이므로, 차이는 **2,590,850,129,043원**입니다.")
    assert verify_number_provenance(answer, evidence)["ok"] is True


def test_a_derivation_that_leans_on_an_ungrounded_term_is_not_an_excuse():
    """🔴 **겨냥 Q-018 이 여기에 달려 있다.**

    `1,566,300,000,000 + 276,900,000,000 = 1,843,200,000,000` 은 산술로는 정확히
    맞지만 `276,900,000,000` 이 이미 근거 밖이다. 재계산의 재료를 근거 있는 값으로
    제한하지 않으면 답변이 **자기 오류로 자기 오류를 설명하고** 게이트가 조용히 열린다.
    """
    evidence = "계약금액(원): 1,566,300,000,000\n계약금액(원): 1,842,200,000,000"
    answer = ("- 최초 공시 금액: 1,566,300,000,000원\n"
              "- 최종 정정 금액: 1,843,200,000,000원\n"
              "- 1,843,200,000,000 - 1,566,300,000,000 = 276,900,000,000원")
    out = verify_number_provenance(answer, evidence)
    assert out["ok"] is False
    assert "1,843,200,000,000원" in out["reason"]


def test_a_number_inside_a_longer_evidence_run_does_not_count_as_present():
    """접수번호 14자리 안에서 12자리가 우연히 발견되면 게이트가 조용히 열린다 —
    수 하나를 통째로 비교한다."""
    assert verify_number_provenance("계약금액은 202412230000원입니다.",
                                    "접수번호 20241223000052")["ok"] is False


@pytest.mark.parametrize("answer", ["", None])
def test_provenance_survives_an_empty_answer(answer):
    out = verify_number_provenance(answer, "접수번호 1")
    assert out["ok"] is True and out["mismatches"] == []


# --- ⑧ A21 Step 6 — v3 세대 664 답변 전수 재채점 -------------------------------
# 🔴 **이것이 `R1-9` 의 판정이다.** 겨냥이 잡히는지와 정답 문항에서 서는지를 한 단언
# 안에서 고정한다. 한쪽만 보면 규칙이 낡는다 (`test_arithmetic_firing_…` 과 같은 구조).

#: 🔴 `A21` 이 정답 판정 문항에서 서는 자리 — **거짓 경보가 아니라 채점 결함이다.**
#: 둘 다 답변의 산술이 실제로 틀렸고 판정기가 통과시켰다. 손으로 확인한 산수:
#:   Q-034  72,200,000,000 + 398,800,000,000 + 346,900,000,000 = 817,900,000,000
#:          인데 답변은 757,900,000,000 이라 적었다 (`ea.JUDGED_CORRECT_ARITHMETIC_WRONG`
#:          가 이미 들고 있는 문항이다)
#:   Q-023  1,102,000,000,000 - 899,400,000,000 = 202,600,000,000 인데 답변은
#:          212,600,000,000 이라 적었다. 🔴 **v4 R1-9 가 새로 찾았다** — 근거의
#:          9자리+ 수 10개 중 차가 212,600,000,000 인 쌍은 없다(전수 확인).
#:
#: 🔴 **관용으로 지울 수 없다.** 겨냥 `Q-109` 의 덧셈 오차는 998,000원(상대 3.6e-7)이고
#: 위 둘(6e10 · 1e10)보다 **더 작다.** 겨냥을 지키면서 이 둘만 빼는 창은 존재하지 않는다.
PROVENANCE_JUDGED_CORRECT_BUT_WRONG = {"Q-034", "Q-023"}

#: 스냅샷별 발화 문항. v3 세대 전량(그리드 88 × 7 arm + 밸류체인 12 × 4 arm = 664 답변).
#: 🔴 겨냥 3건은 `Q-018`(그리드) · `Q-109`·`Q-113`(밸류체인)이고 셋 다 여기 들어 있다.
PROVENANCE_FIRING = {
    "v3/A8(88)": {"Q-018", "Q-034"},
    "v3/L1(88)": {"Q-034"},                       # L1 은 Q-018 답변이 아예 다르다
    "v3/L2(88)": {"Q-018", "Q-034", "Q-062"},
    "v3/L3(88)": {"Q-018", "Q-034"},
    "v3/L4(88)": {"Q-018", "Q-034"},
    "v3/A8-lean(88)": {"Q-018", "Q-034"},
    "v3/ORACLE(88)": {"Q-023"},
    "v3/vc/A8(12)": {"Q-107", "Q-109", "Q-113"},
    "v3/vc/A8-lean(12)": {"Q-107", "Q-109", "Q-113"},
    "v3/vc/OR(12)": {"Q-109", "Q-113", "Q-115"},
}


@pytest.mark.parametrize("snapshot", sorted(PROVENANCE_FIRING))
def test_provenance_firing_on_the_stored_responses(snapshot):
    """🔴 겨냥과 거짓 경보를 **한 단언 안에서** 고정한다. LLM 0회 · 지출 0원."""
    loaded = _snapshot(snapshot)
    if loaded is None:
        pytest.skip(f"{snapshot} 산출물이 없다")
    responses, rows = loaded

    fired, false_alarms = set(), []
    for qid, resp in sorted(responses.items()):
        body = ea.strip_verify_tail(resp.get("answer") or "")
        out = verify_number_provenance(body, resp.get("retrieved_context") or "")
        if out["ok"]:
            continue
        fired.add(qid)
        if rows.get(qid, {}).get("correct") \
                and qid not in PROVENANCE_JUDGED_CORRECT_BUT_WRONG:
            false_alarms.append((qid, out["reason"]))

    assert false_alarms == [], f"{snapshot} 정답 문항 거짓 경보: {false_alarms}"
    assert fired == PROVENANCE_FIRING[snapshot], \
        f"{snapshot} 발화가 달라졌다: {sorted(fired)}"


def test_provenance_catches_all_three_targets():
    """🔴 `R1-9` 의 겨냥 3건. 하나라도 빠지면 이 arm 은 기각이고 `A15` 를 되살려야 한다."""
    hit = set()
    for snapshot in PROVENANCE_FIRING:
        loaded = _snapshot(snapshot)
        if loaded is None:
            continue
        responses, _ = loaded
        for qid, resp in responses.items():
            body = ea.strip_verify_tail(resp.get("answer") or "")
            if not verify_number_provenance(body, resp.get("retrieved_context") or "")["ok"]:
                hit.add(qid)
    if not hit:
        pytest.skip("v3 세대 산출물이 없다")
    assert {"Q-018", "Q-109", "Q-113"} <= hit, f"겨냥을 놓쳤다: {sorted(hit)}"


def test_the_provenance_gate_is_not_wired_in_until_the_flag_is_on(monkeypatch):
    """🔴 배선만으로 각주가 늘어나면 그 자체가 답변 변화이고 `A21` 의 Δ 와 섞인다.

    v3 세대 664 답변 중 23건이 발화하므로 무조건 배선하면 그만큼 답변이 달라진다.
    """
    calls = []
    monkeypatch.setattr(pipeline, "verify_number_provenance",
                        lambda answer, evidence: calls.append(answer) or {
                            "ok": True, "checked": [], "skipped": [], "failed_kinds": [],
                            "hard_kinds": [], "reason": "", "mismatches": [],
                            "recomputed": {}, "failure_stage": None, "llm_calls": 0})
    _verify_stored("v3/A8(88)", "Q-018", ablation.A8)
    assert calls == [], "플래그가 꺼져 있는데 출처 게이트가 불렸다"
    _verify_stored("v3/A8(88)", "Q-018", ablation.ARMS["A21"])
    assert len(calls) == 1


def test_the_gate_flag_is_off_by_default_and_depends_on_the_verifier():
    """🔴 기본값이 곧 운영 경로다. 참이면 제출 구성이 게이트를 켠 채로 답한다."""
    assert "provenance_gate" in ablation.FLAG_NAMES
    assert ablation.A8.provenance_gate is False
    assert ablation.current().provenance_gate is False
    assert "provenance_gate" in ablation.FLAG_DEPENDENCIES["verifier"]
    with pytest.raises(ValueError, match="provenance_gate"):
        replace(ablation.A0, name="bad", provenance_gate=True)


def test_a21_is_registered_as_a_side_branch_arm():
    """`A8-lean`·`A20` 과 같은 곁가지다 — 사다리의 칸도 LOO 도 아니다."""
    assert "A21" in ablation.ARMS and "A21" in ablation.RUN_ORDER
    assert "A21" not in ablation.LADDER and "A21" not in ablation.LOO_REMOVED
    assert "A21" not in ablation.DIAGNOSTIC_ARMS
    assert "A21" in ablation.SIDE_ARMS
    a8, a21 = ablation.flags("A8"), ablation.flags("A21")
    assert {k for k in a21 if a21[k] != a8[k]} == {"provenance_gate"}
    # 🔴 `A20` 과 **다른 축**이다. 두 플래그가 겹치면 Δ 가 섞인다.
    assert a21["substitute_verified"] is False
    assert ablation.flags("A20")["provenance_gate"] is False


def test_the_gate_footnotes_q018_without_touching_the_body():
    """🔴 `A21` 은 답변을 고치지 않는다 — 판정만 한다 (`A20` 과 갈리는 자리)."""
    body, out, trace = _verify_stored("v3/A8(88)", "Q-018", ablation.ARMS["A21"])
    assert ea.strip_verify_tail(out) == body, "출처 게이트가 본문을 건드렸다"
    assert "provenance" in trace[0]
    assert "1,843,200,000,000원 이 근거에 없다" in out
    assert not any("substituted" in t for t in trace)


def test_no_correct_answer_gets_a_new_footnote_from_the_gate():
    """🔴 현 세대 전체 88 × 7 arm 전수. 정답 문항에서 게이트가 서는 것은 위 명시된
    채점 결함 둘뿐이고, 그 둘은 답변의 산술이 실제로 틀렸다."""
    assert CURRENT_GENERATION_FULL, "현 세대 전체 88 스냅샷이 표에서 사라졌다"
    checked, fired = 0, []
    for snapshot in CURRENT_GENERATION_FULL:
        loaded = _snapshot(snapshot)
        if loaded is None:
            continue
        responses, rows = loaded
        for qid, resp in sorted(responses.items()):
            if not rows.get(qid, {}).get("correct"):
                continue
            body = ea.strip_verify_tail(resp["answer"])
            out = verify_number_provenance(body, resp["retrieved_context"])
            checked += 1
            if not out["ok"] and qid not in PROVENANCE_JUDGED_CORRECT_BUT_WRONG:
                fired.append((snapshot, qid, out["reason"]))
    if not checked:
        pytest.skip("현 세대 전체 88 산출물이 없다")
    assert fired == [], f"정답 문항 거짓 경보: {fired}"


# --- ⑨ A20r — 치환 대신 **사유를 실어 재생성 1회** (`R1-2`) ----------------------
# 🔴 **`R1-9` 가 이 arm 의 설계를 바꿨다.** `A21`(출처 게이트)의 Δ 는 구조적으로 0 이다 —
# `_verify` 의 유일한 출력은 각주이고 채점은 `score_closed`·`judge_open` 양쪽에서
# `strip_verify_tail` 로 그 꼬리를 뗀다. 탐지 100% · 교정 0%. **그 판정을 소비하는
# 자리가 이 arm 이다.** 그래서 재생성 프롬프트는 사유 **둘**을 함께 싣는다:
#
#   ① `aggregate_calculation` 의 reasons       계산 불일치 — `A20` 과 같은 원천
#   ② `verify_number_provenance` 의 reason     근거에 없는 수 — `A21` 이 잡은 것
#
# 🔴 **온도 0.0 을 유지한다.** 사유를 안 실으면 결정적 재시도라 **같은 답이 나온다**
# (SWE-Lancer S21·S22 실측 · AutoGen board agent 처방 G34·G36 · CAMEL C34). 온도를 올려
# 다양성을 사는 것은 `A10`(SC k=3)의 조작이고, 이 arm 이 재는 것은 **진단 주입**이다.
# 둘은 같은 축의 변형이 아니라 서로 다른 기전이라 통제 변수를 깨면 Δ 의 귀속이 무너진다.

A20R_ARM = ablation.ARMS["A20r"]

#: 합성 사례 하나로 **두 사유가 함께 서는 자리**를 만든다. 접수번호를 안 넣어 정형
#: 레코드 조회(`_evidence_records`)를 0건으로 만들었다 — 코퍼스 인덱스에 기대지 않는다.
REGEN_QUESTION = "계약금액이 얼마로 정정되었는가?"
REGEN_EVIDENCE = "계약금액(원): 1,566,300,000,000\n계약금액(원): 1,842,200,000,000"
#: ① 뺄셈이 틀렸고(`equation`) ② `1,843,200,000,000` 이 근거에 없다(`provenance`).
REGEN_ANSWER = ("- 최초 공시 금액: 1,566,300,000,000원\n"
                "- 최종 정정 금액: 1,843,200,000,000원\n"
                "- 1,843,200,000,000 - 1,566,300,000,000 = 266,900,000,000원")
REGEN_FIXED = "정정 후 계약금액은 1,842,200,000,000원입니다. (근거: 공시 본문)"
REGEN_WORSE = "계약금액은 9,999,999,999,999원입니다."


def _fake_chat(monkeypatch, replies):
    """`pipeline.chat` 을 가로채 (messages, kwargs) 를 기록한다. 네트워크 0회 · 0원."""
    seen = []

    def _chat(messages, **kwargs):
        seen.append((messages, kwargs))
        return types.SimpleNamespace(text=replies[min(len(seen) - 1, len(replies) - 1)],
                                     prompt_tokens=10, completion_tokens=5)

    monkeypatch.setattr(pipeline, "chat", _chat)
    return seen


def _run_verify(arm, answer=REGEN_ANSWER, budget=None):
    r = types.SimpleNamespace(companies=[], use_cot=False)
    trace: list[str] = []
    with ablation.use(arm):
        out = pipeline._verify(REGEN_QUESTION, answer, REGEN_EVIDENCE, r, trace,
                               Budget() if budget is None else budget)
    return out, trace


def _prompt_of(call) -> str:
    return "\n".join(m["content"] for m in call[0])


def test_regeneration_feeds_both_failure_reasons_back(monkeypatch):
    """🔴 **이 태스크의 전부다.** 온도 0.0 · 같은 프롬프트로 다시 부르면 결정적이라 같은
    답이 나온다 — 사유를 실어야 no-op 이 아니다.

    🔴 그리고 **두 원천을 다 실어야 한다.** `A21` 의 판정을 소비하는 자리가 여기다.
    """
    seen = _fake_chat(monkeypatch, [REGEN_FIXED])
    _run_verify(A20R_ARM)

    assert len(seen) == 1, "🔴 재생성은 1회다"
    _messages, kwargs = seen[0]
    assert kwargs["temperature"] == 0.0, "🔴 통제 변수 — 온도를 올리는 것은 A10 의 조작이다"
    prompt = _prompt_of(seen[0])
    base = "\n".join(m["content"] for m in
                     build_answer_messages(REGEN_QUESTION, REGEN_EVIDENCE))
    assert prompt != base, "🔴 프롬프트가 실제로 달라야 한다 — 같으면 결정적 no-op 이다"
    # ① 계산 불일치 — `A20` 과 같은 원천
    assert "266,900,000,000" in prompt and "276,900,000,000" in prompt
    # ② 🔴 `A21` 이 잡은 것 — 이 줄이 `R1-9` 의 판정을 소비한다는 증거다
    assert "1,843,200,000,000원 이 근거에 없다" in prompt
    # 무엇을 고치라는 것인지 — 앞선 답변을 함께 준다 (AutoGen board agent 처방)
    assert REGEN_ANSWER in prompt


def test_a_clean_regeneration_replaces_the_answer_body(monkeypatch):
    """결함이 사라졌으면 **본문이 바뀌고 각주는 안 붙는다** — 고쳐 놓고 경고를 남기면
    각주가 본문과 어긋난다 (`A20` 의 치환 각주와 같은 원칙)."""
    _fake_chat(monkeypatch, [REGEN_FIXED])
    out, trace = _run_verify(A20R_ARM)
    assert out == REGEN_FIXED
    assert "(주의:" not in out
    assert any("kept=regenerated" in t for t in trace)


def test_a_regeneration_that_still_fires_is_discarded(monkeypatch):
    """🔴 **재생성이 더 나쁘면 원본을 쓴다.** 검사가 여전히 발화하면 원본 유지다.
    그리고 🔴 **재생성은 1회로 못 박는다** — 무한 루프 방지."""
    seen = _fake_chat(monkeypatch, [REGEN_WORSE])
    out, trace = _run_verify(A20R_ARM)
    assert len(seen) == 1, "🔴 재생성이 두 번 돌았다 — 상한이 안 걸렸다"
    assert ea.strip_verify_tail(out) == REGEN_ANSWER, "더 나쁜 답을 채택했다"
    assert "(주의:" in out, "원본을 유지했으면 종전대로 각주가 붙어야 한다"
    assert any("kept=original" in t for t in trace)


def test_a_clean_answer_is_never_regenerated(monkeypatch):
    """검사가 안 서면 호출을 쓰지 않는다 — 발화한 문항만 재생성한다."""
    seen = _fake_chat(monkeypatch, [REGEN_FIXED])
    out, _ = _run_verify(A20R_ARM, answer=REGEN_FIXED)
    assert seen == [] and out == REGEN_FIXED


def test_regeneration_counts_on_the_budget(monkeypatch):
    """🔴 게이트 F 는 `Budget.llm_calls` 를 읽는다 — 재생성을 안 세면 그 게이트가
    거짓을 말한다 (`_respond` 가 그 수를 `budget llm=` 으로 찍는다)."""
    _fake_chat(monkeypatch, [REGEN_FIXED])
    budget = Budget()
    _run_verify(A20R_ARM, budget=budget)
    assert budget.llm_calls == 1
    assert budget.tokens == 15


def test_the_budget_stops_the_regeneration_before_it_spends(monkeypatch):
    """상한이 다 찼으면 **부르기 전에** 멈춘다 — 부르고 나서 세면 크레딧이 이미 나갔다."""
    seen = _fake_chat(monkeypatch, [REGEN_FIXED])
    budget = Budget(max_llm_calls=1)
    budget.spend_llm(1, 1)
    out, trace = _run_verify(A20R_ARM, budget=budget)
    assert seen == [], "🔴 상한을 넘겨 놓고 호출했다 — 크레딧이 나간다"
    assert ea.strip_verify_tail(out) == REGEN_ANSWER
    assert any("regen=skip_budget" in t for t in trace)


def test_regeneration_refuses_to_run_without_a_budget(monkeypatch):
    """🔴 예산 없이 돌면 호출을 아무도 안 세고 게이트 F 가 거짓을 말한다.
    조용히 건너뛰면 그것은 **조용한 Δ 0** 이다 — 둘 다 안 된다."""
    _fake_chat(monkeypatch, [REGEN_FIXED])
    r = types.SimpleNamespace(companies=[], use_cot=False)
    with ablation.use(A20R_ARM), pytest.raises(ValueError, match="예산"):
        pipeline._verify(REGEN_QUESTION, REGEN_ANSWER, REGEN_EVIDENCE, r, [])


def test_regeneration_is_not_wired_in_until_the_flag_is_on(monkeypatch):
    """배선만으로 호출이 늘면 그 돈은 `A20r` 의 것이 아닌데 `A20`·`A21` 에 얹힌다."""
    seen = _fake_chat(monkeypatch, [REGEN_FIXED])
    for arm in (ablation.A8, A20_ARM, ablation.ARMS["A21"]):
        _run_verify(arm)
        assert seen == [], f"{arm.name} 이 재생성을 불렀다"


def test_a20r_is_registered_as_a_side_branch_arm():
    """🔴 `R1-1` 이 `A20` 을 등록하지 않아 `--arm A20` 이 안 돌았다. 같은 실수를 막는다."""
    assert "A20r" in ablation.ARMS and "A20r" in ablation.RUN_ORDER
    assert "A20r" not in ablation.LADDER and "A20r" not in ablation.LOO_REMOVED
    assert "A20r" not in ablation.DIAGNOSTIC_ARMS
    assert "A20r" in ablation.SIDE_ARMS      # 없으면 산출물 문서에 한 글자도 안 남는다
    a8, a20r = ablation.flags("A8"), ablation.flags("A20r")
    # 🔴 **두 칸이 움직인다.** `provenance_gate` 는 조작이 아니라 **입력**이다 — `R1-9` 가
    #    그 게이트의 단독 Δ 가 구조적으로 0 임을 확인했으므로(각주만 내고 채점기가 그
    #    꼬리를 뗀다) 이 arm 의 Δ 에 섞이지 않는다. 대신 이 arm 이 그 판정을 소비한다.
    assert {k for k in a20r if a20r[k] != a8[k]} == {"regenerate_on_verify",
                                                     "provenance_gate"}
    # 🔴 `A20` 과 갈리는 자리 — **치환하지 않는다.** "치환 대신 재생성" 이 이 arm 의 정의다.
    assert a20r["substitute_verified"] is False
    order = list(ablation.RUN_ORDER)
    assert order.index("A20") < order.index("A20r") < order.index("A2F")


def test_the_regeneration_flag_is_off_by_default_and_depends_on_the_verifier():
    """🔴 기본값이 곧 운영 경로다. 참이면 제출 구성이 문항마다 호출을 하나 더 쓴다."""
    assert "regenerate_on_verify" in ablation.FLAG_NAMES
    assert ablation.A8.regenerate_on_verify is False
    assert ablation.current().regenerate_on_verify is False
    assert "regenerate_on_verify" in ablation.FLAG_DEPENDENCIES["verifier"]
    with pytest.raises(ValueError, match="regenerate_on_verify"):
        replace(ablation.A0, name="bad", regenerate_on_verify=True)


@pytest.mark.parametrize("qid,needle", [
    ("Q-097", "207,684,631,210"),          # ① 계산 불일치 — `A20` 과 같은 원천
    ("Q-018", "이 근거에 없다"),            # ② 🔴 `A21` 이 잡은 것
])
def test_the_real_targets_carry_their_reason_into_the_prompt(monkeypatch, qid, needle):
    """🔴 합성 사례가 아니라 **저장 응답 원문**으로 확인한다. LLM 0회 · 지출 0원."""
    loaded = _snapshot("v3/A8(88)")
    if loaded is None:
        pytest.skip("v3/A8(88) 산출물이 없다")
    responses, rows = loaded
    resp = responses[qid]
    body = ea.strip_verify_tail(resp["answer"])
    seen = _fake_chat(monkeypatch, ["재생성 답변입니다. (근거: 공시 본문)"])
    r = types.SimpleNamespace(companies=rows.get(qid, {}).get("route_corps") or [],
                              use_cot=False)
    with ablation.use(A20R_ARM):
        pipeline._verify(resp["question"], body, resp["retrieved_context"], r, [], Budget())
    assert len(seen) == 1, f"{qid} 에서 재생성이 안 돌았다"
    assert needle in _prompt_of(seen[0])
