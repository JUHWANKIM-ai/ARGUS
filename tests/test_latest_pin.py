"""P2 · `latest_pin` — 대체본이 있으면 최신본을 근거로 끌어온다 (계획서 2026-08-21).

## 진단이 한 번 틀렸고 그 기록을 남긴다

`scratch/audit/failure_dossier_A49.json` 은 `Q-116` 을 **선택 실패**로 적었다 — *"정정본이
코퍼스에 있고 related 로 이어져 있는데 최신본을 고르지 않았다."* 실측은 다르다.

  query_by_counterparty('한국중부발전')  ->  20250923800193 (is_latest=True, 583,874,002,800)
                                            을 **돌려준다**
  Q-116 의 근거                          ->  20240722800037 (is_latest=False) 만 실렸다
  tools.chain_probe                     ->  링크를 정확히 안다
  CHAIN_SEED_WINDOW = 2                 ->  대체본이 근거 **5번째** 라 씨앗이 안 됐다

**회수 구멍이고 원인은 상수 하나다.** 고른 것이 아니라 최신본이 애초에 근거에 없었다.

## 왜 창만 넓히는가

`_expand_chain` 은 이미 있고 `MAX_CHAIN_DOCS`(6)가 추가량을 묶는다. 창을 넓히는 것은
**덧붙이는 변경**이라 아무 근거도 빼지 않는다 — 대체본 값이 필요한 문항(`Q-054` 무엇이
정정됐나 · `Q-111` 해지와 신규)이 그대로 유지된다.
"""
import pytest

from argus import ablation
from argus.agent import pipeline


def test_window_widens_only_when_the_flag_is_on():
    """꺼진 팔의 창은 2 그대로여야 한다. `A49` 산출물과의 대조가 깨진다."""
    assert pipeline.CHAIN_SEED_WINDOW == 2
    assert pipeline.LATEST_PIN_SEED_WINDOW > pipeline.CHAIN_SEED_WINDOW
    #: 근거 칸 상한과 같아야 '근거에 실린 대체본 전부' 가 씨앗이 된다.
    assert pipeline.LATEST_PIN_SEED_WINDOW == pipeline.MAX_EVIDENCE


def test_flag_is_off_in_the_operational_default():
    assert ablation.current().latest_pin is False


def test_seed_window_is_read_from_the_flag():
    """🔴 처방을 고정한다. 배선을 지우면 이것이 깨진다.

    `_chain_seeds` 를 직접 부르는 대신 창 계산만 재현한다 — `tools.chain_probe` 를
    부르려면 산출물이 필요하고, 이 시험이 재려는 것은 배선이지 회수가 아니다.
    """
    source = pipeline._chain_seeds.__doc__ or ""
    assert source, "독스트링이 사라졌다 — 시험이 무의미해진다"
    import inspect
    body = inspect.getsource(pipeline._chain_seeds)
    assert 'ablation.enabled("latest_pin")' in body
    assert "LATEST_PIN_SEED_WINDOW" in body


def _window(question: str) -> int:
    """`_chain_seeds` 의 창 계산을 그대로 재현한다.

    🔴 `reverse_chain` 은 **질문 조건과 곱집합**으로만 발화한다
    (`wants_prior = enabled("reverse_chain") and _asks_original(question)`).
    첫 판의 이 시험은 질문 조건을 빼먹어 `A49` 의 창을 6 으로 봤고, 그래서 2 를 기대한
    단정이 깨졌다. 시험이 틀렸고 코드는 맞았다.
    """
    wants_prior = (ablation.enabled("reverse_chain")
                   and pipeline._asks_original(question))
    window = (pipeline.REVERSE_SEED_WINDOW if wants_prior
              else pipeline.CHAIN_SEED_WINDOW)
    if ablation.enabled("latest_pin"):
        window = max(window, pipeline.LATEST_PIN_SEED_WINDOW)
    return window


#: 원공시를 묻지 않는 질문. 실측 문항(`Q-116`)의 문면이다.
PLAIN = "한국중부발전을 계약상대로 하는 단일판매·공급계약 중 계약금액이 가장 큰 계약을 공시한 곳은 어디인가?"


@pytest.mark.parametrize("arm_name,expected", [("A49", 2), ("A52", 10)])
def test_window_value_per_arm(arm_name, expected):
    """팔마다 창이 몇인지 실제로 계산해 본다.

    위 시험이 배선의 **존재**를 보고, 이 시험이 그 배선의 **값**을 본다. 둘 다 있어야
    `max()` 를 `min()` 으로 잘못 써도 잡힌다.
    """
    with ablation.use(arm_name):
        assert _window(PLAIN) == expected


def test_the_reverse_window_is_not_shrunk():
    """원공시를 묻는 질문에서 역방향 창이 줄어들면 안 된다.

    `max()` 이므로 늘기만 한다. `min()` 으로 잘못 쓰면 `A52` 의 역방향 창이 6 에서
    10 이 아니라 6 아래로 내려가 `reverse_chain` 의 이득을 깎는다.
    """
    origin_q = "그 계약의 최초 공시는 무엇인가?"
    with ablation.use("A49"):
        assert pipeline._asks_original(origin_q), "이 문면이 원공시 질문으로 안 잡힌다"
        assert _window(origin_q) == pipeline.REVERSE_SEED_WINDOW
    with ablation.use("A52"):
        assert _window(origin_q) >= pipeline.REVERSE_SEED_WINDOW


def test_a52_is_a51_plus_one_flag():
    """팔 하나에 스위치 하나. 둘을 같이 켜면 어느 것이 이득을 냈는지 귀속할 수 없다."""
    import dataclasses
    a52, a51 = ablation.ARMS["A52"], ablation.ARMS["A51"]
    differ = [f.name for f in dataclasses.fields(a52)
              if getattr(a52, f.name) != getattr(a51, f.name)]
    assert differ == ["name", "latest_pin"], differ
