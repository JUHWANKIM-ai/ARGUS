# Phase F — Agent + Tool

**policy §4** · 크레딧 소량 · 선행 [04-retriever](04-retriever.md)

> LLM에게 계산을 맡기지 않는다. LLM = Reasoning, Tool = Calculation. — policy §4

---

## F-1. 현재 상태

| 항목 | 상태 |
|---|---|
| 검색 도구 7종 (`TOOL_SPECS`) | 🟢 주최 측 참조 명세대로 |
| 계산 도구 7종 (`TOOL_SPECS`) | 🟢 **F-2 에서 노출 완료 — 총 14종** |
| 라우터 (3축 분기) | 🟢 |
| 예산 가드 (8스텝·hop 3·호출 6) | 🟢 |
| 이중 종료 (TERMINATE + 코드 상한) | 🟢 |
| `compute.py` (증감률·비중·합산·기간·CAGR·평균) | 🟢 |
| `list_sections`/`read_section` | 🔴 항상 error (인덱스 부재) |
| ReAct 반복 루프 | 🔴 현재 단발 검색 |

---

## F-2. 계산 도구 노출 — 완료

`compute.py` 를 만들어놓고 **`TOOL_SPECS` 에 넣지 않아** LLM 이 계산을 스스로 하던 상태였다.
7종을 function calling 스펙으로 노출하고 `_DISPATCH` 에 등록했다.

| 도구 | 입력 | 반환 | policy §4 항목 |
|---|---|---|---|
| `calc_pct_change` | before, after | `value`·`rounded`·`unit`·`expr` | 증가율 |
| `calc_share` | part, whole | 〃 | 비율 |
| `calc_total` | values[] | 〃 | 합계 |
| `calc_mean` | values[] | 〃 | 평균 |
| `calc_cagr` | begin, end, years | 〃 | CAGR |
| `calc_compare` | a, b | `larger`·`diff`·`ratio` | 순위 |
| `calc_period` | start, end | `value`·`years`·`months` | (LLM 오답 영역) |

설계 규칙

- **인자는 전부 `required`** — 계산 도구는 인자가 빠지면 계산 자체가 성립하지 않는다.
- `rounded` 는 답변에 그대로 쓰라고 주고, `expr` 은 계산 과정 **설명**용으로 준다.
  LLM 은 `expr` 을 읽고 서술할 뿐 재계산하지 않는다.
- `expr` 은 지수표기를 쓰지 않는다 (`compute.fmt_num`). `2.27648e+13` 이 답변에 새면 안 된다.
- 계산 불가 입력(0 나눗셈·빈 목록·해석 불가 날짜)은 예외가 아니라 `{"error": ...}` 로 돌려준다.
  `execute()` 가 `ValueError` 를 잡으므로 파이프라인이 죽지 않는다.

**실측 근거**: HCX 는 계약기간 산정을 Direct·CoT **둘 다 오답**(`8년 5개월` → `9년 5개월`).
`calc_period("2025-07-24", "2033-12-31") → "8년 5개월"` 이 회귀 테스트로 고정돼 있다.

---

## F-3. ReAct 반복 루프

현재는 검색 1회 후 바로 생성한다. ReAct 의 backoff 를 적용한다.

```
검색 → 근거 충분? ─ 예 → 생성
          └─ 아니오 → 질의 재구성 → 재검색 (hop ≤ 3, 총 스텝 ≤ 8)
```

**backoff 휴리스틱** (ReAct 실측 규칙)
- `ReAct → CoT-SC` : 스텝 내 답을 못 내면 폴백
- `CoT-SC → ReAct` : 다수 답이 n/2 미만이면 검색으로 전환

**스텝 상한 근거**: ReAct 논문에서 정답 궤적 중 7스텝을 쓴 것은 **0.84%**.
주최 측 참조 아키텍처도 **최대 8스텝**.

---

## F-4. 비용 설계

| 경로 | 호출 수 |
|---|---|
| `unanswerable` | **0** |
| `closed_lookup` (난이도 하) | 1 |
| `closed_lookup` + 근거표기 누락 | 2 (+critic) |
| `multi_compare` | 1~3 (GoT 집계) |
| `complex_reason` | 2~5 (hop 반복) |

**질문당 상한을 하드코딩한다** — `Budget(max_llm_calls=6)`.

---

## 게이트 F

- [x] 계산 함수 7종이 `TOOL_SPECS` 에 노출되고 function calling 으로 호출됨
      — `TOOL_SPECS` 14종, `CALC_TOOLS` / `RETRIEVAL_TOOLS` 로 구분, `execute()` 왕복 테스트 통과
- [x] CAGR · 평균 구현 (policy §4 명시 항목) — `compute.cagr` · `compute.mean`
- [ ] periodic 섹션 하강 동작
- [ ] ReAct backoff 동작, hop 상한 준수
- [ ] 질문당 호출 수가 라우트별로 기록됨
