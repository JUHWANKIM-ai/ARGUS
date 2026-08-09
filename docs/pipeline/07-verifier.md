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
| **계산 오류** | — | 🔴 **없음** |

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
3. 허용 오차(반올림 1자리) 밖이면 `TOOL` 실패로 귀속

**실측 근거**: HCX 는 계약기간 산정(`2025-07-24 ~ 2033-12-31 → 8년 5개월`)을
Direct·CoT **둘 다 오답**(9년 5개월)이었다. 검증 없이는 잡히지 않는다.

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

- [ ] `verify_calculation()` 추가, LLM 호출 0회
- [ ] 계약기간·비율·증감률 3종에서 오차 검출 동작
- [ ] critic 호출률(= judge 실제 호출 / 전체 질문) 측정 — 비용 예측 입력
