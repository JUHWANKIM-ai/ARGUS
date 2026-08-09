# Phase G — Verifier

**policy §6** · 크레딧 **0** (계산 검증) · 선행 [06-agent-tool](06-agent-tool.md)

> Answer 생성 Agent와 검증 Agent를 분리한다.
> Verifier는 근거 존재 여부 · Hallucination · **계산 오류** · 출처 누락을 검사한다. — policy §6

---

## G-1. 현재 상태 vs policy 요구

| policy 요구 | 현재 `critic.py` | 상태 |
|---|---|---|
| 근거 존재 여부 | `supported` | ✅ |
| Hallucination | `hallucination` | ✅ |
| 출처 누락 | `has_citation` + `quick_check` (무료 선검사) | ✅ |
| **계산 오류** | `verify_calculation` (무료 재계산) | ✅ |

---

## G-2. 계산 검증은 LLM 에게 묻지 않는다

policy §4 의 논리를 그대로 적용한다 — 계산이 Tool 의 일이면 **계산 검증도 Tool 의 일**이다.

```python
def verify_calculation(answer: str, evidence: dict) -> dict:
    """답변에서 수치를 파싱해 compute.py 로 재계산하고 대조한다. LLM 0회."""
```

절차:
1. 답변 텍스트에서 수치·단위 추출 (`22,764,764,160,000원`, `7.6%`, `8년 5개월`)
2. 근거 레코드의 원값으로 `compute.py` 재계산
3. 허용 오차(반올림 1자리, `ROUND_TOLERANCE = 0.05`) 밖이면 `TOOL` 실패로 귀속

**실측 근거**: HCX 는 계약기간 산정(`2025-07-24 ~ 2033-12-31 → 8년 5개월`)을
Direct·CoT **둘 다 오답**(9년 5개월)이었다. 검증 없이는 잡히지 않는다.

### 검사 6종

| kind | 근거 키 | 답변에서 찾는 것 | 재계산 |
|---|---|---|---|
| `period` | `period_start` · `period_end` | `N년 M개월` | `compute.period_parts` |
| `amount` | `amount` | `…원` · `…억원` · `…조원` | 단위 환산 후 대조 |
| `share` | `amount` + 최근매출액(`raw_rows`) · 없으면 `ratio` | `N%` | `compute.share` |
| `pct_change` | `before` · `after` | `N%` | `compute.pct_change` |
| `cagr` | `begin` · `end` · `years` | `N%` | `compute.cagr` |
| `total` · `mean` | `values[]` | 합계/평균 키워드가 있을 때만 | `compute.total` · `mean` |

`evidence` 는 `exchange_records.jsonl` 레코드를 그대로 받고,
`query_events` 반환형(`{"count":…, "events":[…]}`)도 그대로 받는다.

### 허용 오차 — 답변이 쓴 자릿수만큼만 봐준다

`max(0.05, 표기 자릿수의 절반)`. `227,648억원` 은 ±5천만원까지 같은 값으로 본다
(실제 원값 `22,764,764,160,000` 과 3,584만원 차이 → 통과).

### 안 말한 것과 틀린 것을 섞지 않는다

답변이 해당 수치를 아예 말하지 않으면 불일치가 아니라 **`skipped`** 다.
섞으면 Phase H 의 병목 판정이 오염된다.

### 반환 (Phase H Error Analysis 입력)

```python
{"ok": False, "llm_calls": 0,
 "checked": ["period", "share"], "skipped": ["amount"],
 "failed_kinds": ["period", "share"],
 "failure_stage": "TOOL",          # ok 면 None
 "mismatches": [{"kind": "period", "claimed": "9년 5개월", "claimed_value": 113,
                 "expected": "8년 5개월", "expected_value": 101,
                 "tolerance": 0, "detail": "2025-07-24 ~ 2033-12-31"}],
 "reason": "period: 답변 9년 5개월 vs 재계산 8년 5개월"}
```

`failure_stage` 는 policy §9 의 `Retriever → Re-ranker → Reasoning → Tool` 중
**`TOOL`** 로 바로 귀속된다 — Phase H 는 이 값을 그대로 집계하면 된다.

---

## G-3. 호출 비용 설계

| 단계 | 비용 |
|---|---|
| `quick_check` — 근거 표기 정규식 | **0** |
| `verify_calculation` — 결정적 재계산 | **0** |
| `judge` — 근거 지지 판정 | LLM 1회 |

**무료 두 단계에서 걸리면 `judge` 를 호출하지 않는다.** 실측상 표기가 있으면 critic 을 건너뛴다.

---

## 게이트 G

- [x] `verify_calculation()` 추가, LLM 호출 0회
      — `chat` 을 예외로 바꿔놓고 호출해도 통과하는 테스트로 고정
- [x] 계약기간·비율·증감률 3종에서 오차 검출 동작
      — 여기에 금액·CAGR·합계·평균까지 6종. HCX-005 기간 오답이 회귀 테스트다
- [ ] critic 호출률(= judge 실제 호출 / 전체 질문) 측정 — 비용 예측 입력
