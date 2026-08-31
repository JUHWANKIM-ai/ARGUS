r"""`A93` — 정정 추이 금액을 결정적 줄에 맞춘다 (2026-08-31).

## 왜 생겼나

미공개셋 `K-015` 는 계약 하나의 정정 이력을 금액과 함께 시간순으로 묻는다. 근거 **첫
줄**이 그 답을 이미 다 준다.

    - [결정적 집계] 접수일 순 금액 추이 (4건): 2024-04-30 1,457,049,721,180원
      → 2024-06-11 1,457,049,721,180원 → 2025-01-02 1,457,049,721,180원
      → 2025-10-13 1,481,561,966,798원

그런데 답변은 네 걸음 전부에 `1,558,826,903,000원` 을 붙였다. 그 금액은 근거에 있지만
**다른 계약**의 것이다 (대전 도안2-2지구 공동주택 신축공사, 접수번호 20240415800078,
계약상대 주식회사유토개발2차, 근거 맨 끝 블록).

## 검색도 근거 배치도 아니다

바른 금액은 근거에 **6회**, 오인된 금액은 **1회** 나온다. 그리고 정답을 낸 회차와
오답을 낸 회차의 근거가 **바이트까지 같다** (2,899자 · sha c71b5ad3e4). 즉 순수한
전사(transcription) 실패다.

같은 실패가 `A84` · `A92` · LoRA 튜닝 모델 **세 구성에서 재현**됐다. 그래서 프롬프트
규칙이 처방이 아니다 — 이 저장소는 「지시 줄은 늘 듣지 않는다」를 이미 실측했다.

## 무엇을 갈고 무엇을 안 가는가

근거에 **있는** 금액만 간다. 그것이 다른 블록에서 옮겨 왔다는 표지다. 답변이 스스로
유도한 수는 그대로 둔다 — `24,512,245,638원`(답변이 계산한 증가분)은 어느 블록에도
없으므로 손대지 않는다. 파생값은 `drop_unsourced_total` 과 `fix_broken_equation` 의
몫이다.

## 사정거리

결정적 추이 줄이 붙는 문항은 골든 2개(`Q-023`·`Q-081`)와 미공개 4개(`H-015`·`H-016`·
`K-015`·`K-016`)뿐이다. 저장된 응답에 걸어 본 실측: 골든 8응답 발화 **0**, 미공개
16응답 중 **`K-015` r4 하나**.

## 🔴 이 팔은 미공개셋 실패를 보고 만들었다

그래서 `A92` 를 최종 베이스라인으로 둔 채 `A93` 을 따로 둔다. `K-015` 는 이 뒤로
독립 측정이 아니며, 미공개셋 성적을 인용할 때 그 사실이 함께 적혀야 한다.
"""
import sys

import pytest

sys.path.insert(0, "src")

from argus import ablation
from argus.agent import aggregate

SERIES = ("- [결정적 집계] 접수일 순 금액 추이 (3건): "
          "2024-04-30 1,457,049,721,180원 → 2025-01-02 1,457,049,721,180원 "
          "→ 2025-10-13 1,481,561,966,798원")
OTHER = "- 계약금액: 1,558,826,903,000원 (15,588억원)"
EVIDENCE = SERIES + "\n\n## 다른 계약\n" + OTHER


def align(answer, evidence=EVIDENCE, arm="A93"):
    with ablation.use(arm):
        return aggregate.align_series_amounts(answer, evidence)


def test_it_realigns_every_step_to_the_decisive_line():
    """`K-015` 의 모양 — 세 걸음 전부 다른 계약 금액이 붙어 있다."""
    answer = ("1. 최초 공시 (2024-04-30)\n   - 계약금액: 1,558,826,903,000원\n"
              "2. 첫 정정 (2025-01-02)\n   - 계약금액: 1,558,826,903,000원\n"
              "3. 두 번째 정정 (2025-10-13)\n   - 계약금액: 1,558,826,903,000원\n")
    out, fixes = align(answer)
    assert len(fixes) == 3
    assert out.count("1,457,049,721,180") == 2
    assert out.count("1,481,561,966,798") == 1
    assert "1,558,826,903,000" not in out


def test_a_figure_the_answer_derived_is_left_alone():
    """근거에 없는 수는 우리 몫이 아니다 — 파생값을 갈면 없는 수를 만든다."""
    answer = ("1. 최초 (2024-04-30)\n   - 계약금액: 1,558,826,903,000원\n"
              "2. 최종 (2025-10-13)\n   - 계약금액: 1,558,826,903,000원\n"
              "   - 증가분: 24,512,245,638원\n")
    out, fixes = align(answer)
    assert "24,512,245,638" in out
    assert all("24,512,245,638" not in f for f in fixes)


def test_a_correct_answer_is_returned_byte_identical():
    answer = ("1. 최초 (2024-04-30) 1,457,049,721,180원\n"
              "2. 최종 (2025-10-13) 1,481,561,966,798원\n")
    out, fixes = align(answer)
    assert (out, fixes) == (answer, [])


def test_it_stays_silent_without_the_decisive_line():
    answer = "1. 최초 (2024-04-30)\n   - 계약금액: 1,558,826,903,000원\n"
    out, fixes = align(answer, evidence="## 다른 계약\n" + OTHER)
    assert (out, fixes) == (answer, [])


def test_one_date_is_not_a_timeline():
    """앵커가 하나면 「추이」가 아니다. 우연히 날짜 하나가 스친 답변을 안 건드린다."""
    answer = "2024-04-30 공시 건의 금액은 1,558,826,903,000원입니다."
    out, fixes = align(answer)
    assert (out, fixes) == (answer, [])


def test_the_system_tail_is_never_rewritten():
    """`split_tail` 의 규율 — 꼬리는 관측이고 고치면 기록이 발명이 된다."""
    answer = ("1. 최초 (2024-04-30) 1,558,826,903,000원\n"
              "2. 최종 (2025-10-13) 1,558,826,903,000원\n"
              "\n\n(주의: 자동 검증에서 결함이 발견되었습니다 — "
              "답변 1,558,826,903,000원 vs 재계산)")
    out, fixes = align(answer)
    assert len(fixes) == 2
    assert out.count("1,558,826,903,000") == 1
    assert "(주의:" in out


def test_the_switch_is_off_on_the_deployed_baseline():
    """`A92` 가 최종 베이스라인이다. 이 팔에서는 한 글자도 바뀌지 않는다."""
    answer = ("1. 최초 (2024-04-30) 1,558,826,903,000원\n"
              "2. 최종 (2025-10-13) 1,558,826,903,000원\n")
    assert align(answer, arm="A92") == (answer, [])
    assert ablation.enabled("align_series_amounts", "A93")
    assert not ablation.enabled("align_series_amounts", "A92")


def test_unit_parens_repair_follows_the_alignment():
    """순서 주장 — 금액을 갈면 괄호 환산이 낡는데 다음 수리가 그것을 잡는다."""
    answer = ("1. 최초 (2024-04-30)\n"
              "   - 계약금액: 1,558,826,903,000원 (1조 5,588억원)\n"
              "2. 최종 (2025-10-13)\n"
              "   - 계약금액: 1,558,826,903,000원 (1조 5,588억원)\n")
    with ablation.use("A93"):
        step, fixes = aggregate.align_series_amounts(answer, EVIDENCE)
        out, parens = aggregate.fix_unit_parens(step)
    assert len(fixes) == 2 and len(parens) == 2
    assert "1조 4,570억원" in out and "1조 5,588억원" not in out


@pytest.mark.parametrize("arm", ["A92", "A93"])
def test_the_flag_name_is_known_to_every_arm(arm):
    """`enabled` 는 모르는 플래그에 예외를 던진다 — 오타가 무효과로 보고되지 않게."""
    assert ablation.enabled("align_series_amounts", arm) in (True, False)
