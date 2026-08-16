"""「추론 논리성」 검사 — 답변의 **논증 형태**를 본다. LLM 0회 · 검색 0회.

`critic` 과 가르는 선이 분명하다. 저쪽은 *"수가 맞는가"* 를 보고 여기는 *"말이 서로
맞는가"* 를 본다. 기준과 시험은 `docs/reasoning-metric-2026-08-16.md`.

🔴 **이 모듈은 처분을 하지 않는다 — 검출만 한다.** 자기부정이나 순서 오류를 코드가
고치려면 문장을 지우거나 다시 써야 하는데, 그것은 답변의 뜻을 바꾸는 일이라 결정적
규칙으로 감당할 범위를 넘는다. 재고, 세고, 적는다.

## 덮는 양식과 안 덮는 양식

    ① 수치 자기정합   `aggregate.fix_self_contradiction`   (다른 모듈 · 처분까지 한다)
    ② 식 불성립      `critic.verify_answer_arithmetic`
    ③ 근거 밖 수치    `critic.verify_number_provenance`
    ⑥ 순서 오류      **여기** `out_of_order`
    ⑦ 자기부정       **여기** `self_negating`
    ④ 전제 누락 · ⑤ 비약·무관                            🔴 **없다**

즉 일곱 중 다섯을 덮는다. 나머지 둘은 논증의 내용을 읽어야 하고, LLM 판정기는
closed judge 진단(갈린 7건 중 6이 오탐)이 그 도구의 신뢰도를 이미 보여 줬다.
"""
from __future__ import annotations

import re

from argus.agent import aggregate

#: 거절 문구. `pipeline` 의 고지 상수와 모델이 제 문장으로 쓰는 형태를 함께 본다.
_REFUSAL = ("확인되지 않습니다", "제공하지 않습니다", "제공해 드릴 수 없습니다",
            "판단을 생성하지 않습니다", "어느 기업을 말씀하시는지",
            "제공할 수 없습니다", "답변을 드릴 수 없습니다")

#: 실질 답변으로 볼 금액. 콤마 세 자리 묶음 둘 이상 — 연도·비율을 거른다.
_MONEY = re.compile(r"\d{1,3}(?:,\d{3}){2,}\s*원")

#: `2023-12-22 1,102,000,000,000원` 처럼 날짜와 금액이 한 자리에 있는 대목.
_DATED = re.compile(r"(20\d{2})[.\-/년]\s*(1[0-2]|0?[1-9])[.\-/월]?\s*"
                    r"(?:(3[01]|[12]\d|0?[1-9])\s*일?)?[^0-9]{0,40}?"
                    r"(\d{1,3}(?:,\d{3}){2,})\s*원")


def body_of(answer: str) -> str:
    """우리 부속물을 뗀 본문. 거기 실린 수는 **모델의 주장이 아니다**.

    🔴 규칙은 `aggregate.split_tail` 하나뿐이다 — 여기서 정규식을 다시 쓰지 않는다.
    """
    return aggregate.split_tail(answer or "")[0]


def self_negating(answer: str) -> dict:
    """⑦ **없다고 하고 이어서 답한다.**

    거절 문구와 실질 금액이 **한 본문에** 있으면 답변이 스스로를 부정한다. 규약
    「정보한계 대응」과 「추론 논리성」에 함께 걸린다 — 한계를 고지했는데 그 한계를
    넘는 값을 같은 답에서 내놓기 때문이다.

    🔴 **각주는 안 본다.** 우리가 붙인 `(근거: …)` 에는 금액이 실릴 수 있고, 그것은
    모델의 주장이 아니라 우리 표기다. 본문만 본다.

    🔴 **정상인 형태를 오탐하지 않는다**: *"A 는 확인되지 않습니다. B 는 1,000,000,000원
    입니다"* 처럼 **부분 거절**은 정당하다. 그래서 이 검사는 발화만 하고 처분하지
    않는다 — 사람이 읽고 가른다. 실측(A38 · 112문항): 발화 0건.
    """
    text = body_of(answer)
    refusal = [k for k in _REFUSAL if k in text]
    money = _MONEY.findall(text)
    hit = bool(refusal and money)
    return {"ok": not hit, "kind": "self_negating",
            "why": (f"거절({refusal[0]})과 금액({money[0]})이 한 본문에 있다"
                    if hit else "")}


def out_of_order(answer: str) -> dict:
    """⑥ **시계열이 뒤집혔다.**

    답변이 날짜와 금액을 짝지어 나열할 때, 그 날짜들이 **오름차순이 아니면** 발화한다.
    정정 이력·연도 추이를 묻는 문항이 그 형태다 (`Q-023`·`Q-050`).

    🔴 **오름차순만 본다.** 내림차순으로 적는 것도 정당한 서술이라 *"정렬돼 있지 않다"*
    를 결함으로 보면 오탐이 쏟아진다. 그래서 **뒤섞인 것**만 잡는다 — 오름도 내림도
    아닌 배열이다.

    🔴 **셋 미만이면 판정하지 않는다.** 둘은 어느 쪽으로도 정렬돼 있어 뜻이 없다.
    """
    pairs = []
    for match in _DATED.finditer(body_of(answer)):
        year, month, day, _amount = match.groups()
        pairs.append(int(f"{year}{int(month):02d}{int(day or 1):02d}"))
    if len(pairs) < 3:
        return {"ok": True, "kind": "out_of_order", "why": "",
                "skipped": "날짜·금액 짝이 셋 미만"}
    ascending = all(a <= b for a, b in zip(pairs, pairs[1:]))
    descending = all(a >= b for a, b in zip(pairs, pairs[1:]))
    hit = not (ascending or descending)
    return {"ok": not hit, "kind": "out_of_order",
            "why": (f"날짜가 오름도 내림도 아니다: {pairs}" if hit else "")}


def check_all(answer: str) -> list[dict]:
    """이 모듈이 보는 양식 전부. 발화한 것만 돌려준다."""
    return [r for r in (self_negating(answer), out_of_order(answer))
            if not r["ok"]]
