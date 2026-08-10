"""Phase I — arm 스위치. 구성요소를 끄고 켠다. LLM 0회.

09-ablation §I-3 의 통제 조건이 이 모듈의 존재 이유다.

    고정 : 프롬프트 · 온도 0.0 · maxTokens · 골든셋 · 인덱스 스냅샷 · 모델
    변동 : arm 구성요소만

**스위치를 파이프라인 밖에 두면 통제가 깨진다.** arm 마다 코드를 갈아끼우거나 브랜치를
파면 프롬프트·상수까지 함께 흔들려 Δ 의 귀속이 불가능해진다. 그래서 분기점을 전부
이 한 곳의 플래그로 모으고, 파이프라인은 **같은 코드 그대로** 플래그만 읽는다.

기본값은 `A6` 다 — 완성 구성이 곧 현재 시스템이므로, 스위치를 안 건드리면 종전 동작이다
(`verifier` 만 새로 붙는다. §I-4b 가 A6 을 새 기준선으로 삼는다).

## 플래그와 arm 의 대응

| 플래그 | 뜻 | A0 | A1 | A1b | A4 | A5 | A6 |
|---|---|---|---|---|---|---|---|
| `retrieval` | `meta` 프리필터 / `exchange` BM25 / `all` 인덱스 | meta | exchange | all | all | all | all |
| `schema_fields` | major·holding 추출 필드를 근거에 싣는가 | ✗ | ✗ | ✓ | ✓ | ✓ | ✓ |
| `dense` | **임베딩 하이브리드 (dense + RRF)** | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ |
| `hop_ladder` | 재검색 hop 1~3 · 이벤트 폴백 | ✗ | ✗ | ✗ | ✓ | ✓ | ✓ |
| `chain_hop` | 정정·후속 공시 체인 hop | ✗ | ✗ | ✗ | ✓ | ✓ | ✓ |
| `split_query` | 다지점 질의 분해 | ✗ | ✗ | ✗ | ✓ | ✓ | ✓ |
| `calc_values` | 결정적 계산 결과를 근거에 싣는가 | ✗ | ✗ | ✗ | ✗ | ✓ | ✓ |
| `verifier` | `verify_calculation` + `verify_completeness` | ✗ | ✗ | ✗ | ✗ | ✗ | ✓ |

LOO 는 A6 에서 플래그 하나만 되돌린다 — `L1 = A6 − chain_hop`, `L2 = A6 − schema_fields`.

## `dense` 가 누적 사다리 밖에 있는 이유 — G1 격리

CONVENTION **G1**("LLM 은 HyperCLOVA X 만") 의 임베딩 적용 여부가 **디스코드 미확정**이다.
그래서 `dense` 는 **사다리 어느 칸에도 켜져 있지 않다.** A2 는 곁가지 두 개로만 존재한다.

    A2  = A1b + dense      §I-2 가 정의한 자리에서의 한계 기여도
    A2F = A6  + dense      **제출 구성에서의** 한계 기여도 (leave-one-in)

이렇게 두면 G1 이 불허로 확정돼도 `A0→A1→A1b→A4→A5→A6` 곡선이 **글자 하나 안 바뀐 채**
성립한다. 반대로 dense 를 A2 이상 전 arm 에 켜 두면 A4·A5·A6 이 전부 재측정 대상이 되고,
불허 시 v1 결과가 통째로 무효가 된다.

**A6 대비 Δ 를 재는 것은 `A2F` 다.** §I-4b 가 A6 을 새 기준선으로 삼았기 때문이다.
`A2` 는 §I-2 표의 칸을 채우기 위한 것이고, 둘 다 검색 단계는 LLM 0회로 잰다.

## `calc_values` 를 '제거' 가 아니라 '대체' 로 잰 이유

계산 도구 14종은 `TOOL_SPECS` 로 노출돼 있지만 **생성 호출에는 `tools` 를 넘기지 않는다**
(`pipeline.answer_question` → `chat(..., max_tokens=…)`). 따라서 "function calling 을 끈다"
는 조작은 실행 경로에서 아무것도 바꾸지 못한다 — Δ 가 정의상 0 이 되는 헛측정이다.

실제로 계산 결과가 생성에 도달하는 경로는 하나뿐이다. `corpus/markdown.to_markdown` 이
근거 블록에 **결정적으로 계산한 값**을 박아 넣는다.

    - 계약금액: 22,764,764,160,000원 (227,648억원)      ← 단위 환산
    - 기간: 2025-07-24 ~ 2033-12-31 (8년 5개월)          ← compute.period_parts

그래서 A5 의 조작은 이 두 괄호를 떼어 **LLM 이 직접 계산하게 만드는 것**이다. 부록 D-3 의
HippoRAG 방식(제거가 성립하지 않는 요소는 '더 단순한 것으로 대체')과 같다. 실측 근거도
정확히 이 지점이다 — HCX-005 는 `2025-07-24 ~ 2033-12-31` 을 Direct·CoT 모두 `9년 5개월`
로 답했다 (정답 `8년 5개월`).

**`ratio` 는 떼지 않는다.** `- 매출액 대비: 7.6%` 의 분모(최근매출액)는 근거에 실리지
않으므로, 이걸 떼면 '계산을 LLM 에게 넘긴' 것이 아니라 **정보를 지운** 것이 된다.
계산 ablation 이 정보 ablation 으로 둔갑하면 Δ 의 귀속이 틀어진다.

## 근거 성형은 **생성 직전에만** 한다

`schema_fields` · `calc_values` 는 프롬프트에 실릴 근거 문자열에만 건다. 검색·충분성
판정(`_sufficiency`)은 원본 마크다운을 그대로 본다 — 여기까지 바꾸면 A5·L2 의 Δ 에
검색 변화가 섞여 단일 변수 조작이 아니게 된다.
"""
from __future__ import annotations

import re
from contextlib import contextmanager
from dataclasses import dataclass, replace

#: 검색 바닥. `meta` 는 BM25 없이 manifest·universe 필터만 쓴다 (§I-1).
RETRIEVAL_MODES = ("meta", "exchange", "all")


@dataclass(frozen=True)
class Arm:
    """한 arm 의 구성. 얼려 둔다 — 실행 중에 바뀌면 Δ 의 귀속이 깨진다."""

    name: str
    retrieval: str = "all"
    schema_fields: bool = True
    #: A2 전용. 기본값이 False 인 것이 G1 격리의 전부다 — 스위치를 안 건드린 운영
    #: 경로에서는 임베딩 API 가 **한 번도 불리지 않는다** (질의 임베딩 포함).
    dense: bool = False
    hop_ladder: bool = True
    chain_hop: bool = True
    #: 발주처(counterparty) 2-hop. **`hop_ladder` 와 따로 둔다** — 묶어 두면 A0·A1b 에서
    #: 함께 꺼져 이 hop 만의 Δ 를 분리해서 잴 수 없다. `chain_hop` 을 따로 둔 것과 같은
    #: 이유이고, L3(= 발주처 hop 만 뺀 LOO)이 성립하려면 독립 플래그여야 한다.
    counterparty_hop: bool = True
    split_query: bool = True
    calc_values: bool = True
    verifier: bool = True

    def __post_init__(self) -> None:
        if self.retrieval not in RETRIEVAL_MODES:
            raise ValueError(f"retrieval 은 {RETRIEVAL_MODES} 중 하나여야 합니다: {self.retrieval}")

    @property
    def uses_bm25(self) -> bool:
        return self.retrieval != "meta"

    @property
    def uses_group_index(self) -> bool:
        """major·holding·periodic 인덱스를 보는가. A1 은 exchange 1,469건만 본다."""
        return self.retrieval == "all"


# --- 누적 사다리 (§I-4b) -------------------------------------------------------
A0 = Arm("A0", retrieval="meta", schema_fields=False, hop_ladder=False,
         chain_hop=False, counterparty_hop=False, split_query=False,
         calc_values=False, verifier=False)
A1 = replace(A0, name="A1", retrieval="exchange")
A1B = replace(A1, name="A1b", retrieval="all", schema_fields=True)
A4 = replace(A1B, name="A4", hop_ladder=True, chain_hop=True,
             counterparty_hop=True, split_query=True)
A5 = replace(A4, name="A5", calc_values=True)
A6 = replace(A5, name="A6", verifier=True)

# --- Leave-one-out — 병목 상위 2개 ---------------------------------------------
L1 = replace(A6, name="L1", chain_hop=False)        # 실패의 41%
L2 = replace(A6, name="L2", schema_fields=False)    # 실패의 24%

# --- A2 — 임베딩 하이브리드 (사다리 밖 곁가지) ----------------------------------
A2 = replace(A1B, name="A2", dense=True)            # §I-2 자리: A1b + dense
A2F = replace(A6, name="A2F", dense=True)           # 제출 구성 위: A6 + dense

#: 실행 순서는 **A6 부터 역순**이다 (§I-4b) — 가장 비싼 구성을 먼저 확보해야
#: 중간에 예산이 끊겨도 최신 성능을 안다. A2 계열은 사다리를 다 확보한 뒤 맨 끝이다 —
#: G1 불허로 A2 를 버려도 앞의 여덟 칸이 그대로 남아야 한다.
RUN_ORDER = ("A6", "L1", "L2", "A5", "A4", "A1b", "A1", "A0", "A2F", "A2")

#: 사다리(=v1 곡선). A2 계열은 여기 들어가지 않는다.
LADDER = ("A0", "A1", "A1b", "A4", "A5", "A6")

#: dense 를 켜는 arm. G1 이 불허로 확정되면 **이 둘만 빼면 된다.**
DENSE_ARMS = ("A2", "A2F")

ARMS: dict[str, Arm] = {a.name: a for a in (A0, A1, A1B, A4, A5, A6, L1, L2, A2, A2F)}

#: 스위치를 안 건드리면 완성 구성이다. 운영 경로의 기본값이 곧 A6 이어야
#: '기본값으로 돌린 것' 과 'A6 arm' 이 같은 것임이 보장된다.
_current: Arm = A6


def current() -> Arm:
    return _current


def set_arm(arm: Arm | str) -> Arm:
    """전역 arm 을 바꾸고 이전 값을 돌려준다. 평가 러너 전용."""
    global _current
    previous = _current
    _current = ARMS[arm] if isinstance(arm, str) else arm
    return previous


@contextmanager
def use(arm: Arm | str):
    """`with ablation.use("A1"): ...` — 블록을 벗어나면 반드시 되돌린다."""
    previous = set_arm(arm)
    try:
        yield current()
    finally:
        set_arm(previous)


# --- 근거 성형 (생성 직전) -----------------------------------------------------

#: `- 계약금액: 22,764,764,160,000원 (227,648억원)` 의 괄호. 원값은 남긴다.
_EOK_SUFFIX = re.compile(r"^(-\s*[^:\n]*:\s*-?[\d,]+원)\s*\(-?[\d,]+억원\)\s*$", re.M)
#: `- 기간: 2025-07-24 ~ 2033-12-31 (8년 5개월)` 의 괄호. 시작·종료일은 남긴다.
_DURATION_SUFFIX = re.compile(r"^(-\s*기간:\s*\S+\s*~\s*\S+)\s*\([^()\n]*\)\s*$", re.M)

_RCEPT_RE = re.compile(r"\d{14}")
_BLOCK_SPLIT = re.compile(r"(?m)^(?=##\s)")


def _record_fields(block: str) -> list[str]:
    """이 근거 블록이 `record["fields"]` 로부터 만든 줄들. 없으면 빈 목록.

    `to_markdown` 이 찍은 것과 **같은 문자열**을 다시 만들어 지운다. 줄 모양을
    정규식으로 추측하면 계약상대·공시유보처럼 서식이 같은 다른 줄까지 지운다.
    """
    from argus.agent import tools

    found = _RCEPT_RE.search(block)
    if not found:
        return []
    record = tools._record_of(found.group(0))
    fields = (record or {}).get("fields") or {}
    return [f"- {label}: {value}" for label, value in fields.items()]


def shape_evidence(evidence: str, arm: Arm | None = None) -> str:
    """arm 에 맞춰 근거 문자열을 성형한다. LLM 0회 · 검색 결과는 건드리지 않는다.

    * `schema_fields=False` — major·holding 추출 필드 줄을 뺀다 (Phase H 이전 상태).
    * `calc_values=False`   — 결정적으로 계산해 박아 둔 괄호(억원 환산 · 계약기간)를 뗀다.
    """
    arm = arm or current()
    if arm.schema_fields and arm.calc_values:
        return evidence
    text = evidence or ""

    if not arm.schema_fields and text:
        blocks = []
        for block in _BLOCK_SPLIT.split(text):
            drop = set(_record_fields(block))
            if drop:
                block = "\n".join(line for line in block.split("\n")
                                  if line.strip() not in drop)
            blocks.append(block)
        text = "".join(blocks)

    if not arm.calc_values:
        text = _EOK_SUFFIX.sub(r"\1", text)
        text = _DURATION_SUFFIX.sub(r"\1", text)
    return text
