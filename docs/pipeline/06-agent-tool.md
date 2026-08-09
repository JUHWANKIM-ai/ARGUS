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
| `list_sections`/`read_section` | 🟡 인덱스 없으면 `{"error": ...}` — 파이프라인은 죽지 않는다 (테스트로 고정) |
| ReAct 반복 루프 | 🟢 **F-3 완료 — hop 4단 (최초 1 + 재검색 3)** |

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

## F-3. ReAct 반복 루프 — 완료

단발 검색을 `_gather_evidence` 안의 반복 루프로 바꿨다. **LLM 호출은 여전히 0회다.**

```
검색 → 근거 충분? ─ 예 → 생성
          └─ 아니오 → 질의 재구성 → 재검색 (hop ≤ 3, 총 스텝 ≤ 8)
```

**hop 0 은 최초 검색이라 예산을 쓰지 않고, 재검색 3회가 `Budget.spend_hop()` 을 쓴다.**
상한을 넘겨도 예외를 밖으로 던지지 않는다 — 그 시점 근거로 생성으로 넘어간다.

**스텝 상한 근거**: ReAct 논문에서 정답 궤적 중 7스텝을 쓴 것은 **0.84%**.
주최 측 참조 아키텍처도 **최대 8스텝**. 도구 호출 1건 = 1스텝.

### 재구성 전략 4단 (`agent/query.py::plan_hops`)

**필터는 넓히고 질의는 좁힌다** — 기간·기업 필터가 걸러주던 잡음을 필터를 풀면 질의어가 뒤집어쓴다.

| hop | 전략 | 기간 필터 | 기업 필터 | 질의어 | `top_k` |
|---|---|---|---|---|---|
| 0 | `narrow` | 연월(또는 연도) | ✅ | 전체 어휘 | 5 |
| 1 | `relax_period` | 연월 → 연도 → 없음 | ✅ | 전체 어휘 | 8 |
| 2 | `corp_only` | 없음 | ✅ | **핵심어만** | 10 |
| 3 | `global` | 없음 | ❌ | 핵심어만 | 10 |

핵심어(`core_terms`) = 숫자 토큰과 **실측 DF ≥ 0.30** 어휘를 뺀 나머지.
1,469건 기준 `계약` 0.80 · `상대` 0.77 · `금액` 0.73 · `매출` 0.72 · `대비` 0.72 · `정정` 0.43.
서술어 어미는 떼어내 어간을 살린다 — `체결한`→`체결`(불용어, 제거) · `해지된`→`해지`(변별어, 유지).

> **불용어 목록 정정** — `해지` 를 `체결` 과 나란히 손으로 불용어에 넣은 것이 잘못이었다.
> 실측 DF 는 `해지` 0.014(21건) 대 `체결` 0.766(1,125건) 이다. 되살리니 두산퓨얼셀 해지 3건
> 질의의 상위 3위가 1건 → 3건이 됐다 (16.9/16.2/16.1 vs 9.7/9.4/9.3).

### 충분성 판정 — LLM 0회

판정에 LLM 을 쓰면 hop 마다 호출이 붙어 6회 상한을 태운다. 세 가지만 본다.

| 규칙 | 임계 | 근거 |
|---|---|---|
| 후보 수 | `closed_lookup` 1 · `multi_compare`·`complex_reason` 2, **기업 수 이상**, CoT 경로는 2 이상 | ReAct 단독은 CoT 보다 낮다 (27.4 vs 29.4) — 더 돌린다고 이기지 않는다. 최소치만 채운다 |
| BM25 최고 점수 | **`≥ 10.0`** | 실측 — 무관 질의('우주선 발사 로켓 엔진' 8.7 · '블록체인 가상자산 채굴' 6.6) 와 평가셋 최고점 최솟값 10.8 사이 |
| 골든 필드 존재 | 질문이 요구한 필드 라벨이 근거에 있는가 (`금액:` · `대비:` · `기간:` · `계약상대:`) | 금액을 물었는데 근거에 금액 필드가 없으면 답을 만들 수 없다 |
| 기업 커버리지 | 비교 질의는 **기업마다 1건 이상** | 한쪽만 찾으면 비교가 성립하지 않는다 |

### 넓히는 것이 늘 이득은 아니다 — 3가지 제동

1. **`global` hop 은 기업 특정 질의에 근거가 이미 있으면 건너뛴다.** 다른 회사 공시가 섞이면
   요약·비교가 통째로 오염된다.
2. **`global` hop 은 `MIN_TOP_SCORE` 미만 후보를 버린다.** 필터가 하나도 없는 검색은 점수만이
   관련성의 보증이다. (실측: 카카오 전환사채 질의가 무관 공시 10건을 근거로 끌고 왔다 → 0건으로)
3. **코퍼스 기간(2023.01~2026.03) 밖 연도는 재검색하지 않는다.** 기간 필터를 푸는 순간 2021년
   질문에 2025년 공시가 근거로 둔갑한다. 범위 밖은 못 찾은 게 아니라 없는 것이다.

두 hop 연속 새 후보가 없으면(`stop=stalled`) 남은 hop 을 태우지 않는다.
단, **근거가 0건일 때는 수렴이 아니라 못 찾은 것**이므로 끝까지 넓힌다.

### `think_trace` hop 로그 — Phase H 입력

hop 별로 질의어·필터·후보 수·후보 `doc_id` 를 `key=value` 로 남기고 `pipeline.parse_hops()` 로 되읽는다.

```
hop=0 strat=narrow tool=search_chunks corp="HD현대일렉트릭" period=ym:202301 top_k=5
      q="HD 현대일렉트릭 2023 변압기" n=1 top_score=32.605 ids=exchange_20230131800162
hop=0 verdict=sufficient evidence=1
hop=1 verdict=insufficient evidence=1 reason=few:1<2,low_score:8.8<10.0,missing_corp:고려아연
hop=3 strat=global skip=corp_scoped (기업 특정 질의에 근거 2건 확보 — 전역 검색은 오염)
```

```python
ids = [d for h in parse_hops(resp["think_trace"]) for d in h["ids"]]
RETRIEVAL  — 골든 rcept_no 가 ids 어디에도 없음
RANKING    — ids 에는 있으나 상위 3위 밖
```

`reason` 코드: `no_candidate` · `few:n<need` · `low_score:s<임계` · `missing_field:필드` · `missing_corp:기업`.
`notes` 에는 `skip=tool_error` · `stop=hop_budget` · `stop=step_budget` · `stop=stalled` · `stop=out_of_range` 가 실린다.

### 평가셋 30문항 실측 (LLM 0회)

| 지표 | 값 |
|---|---|
| hop 합계 | **14** (문항당 평균 0.47) |
| 스텝 합계 | **47** (문항당 최대 5 — 상한 8 이내) |
| hop 0 에서 종료 | 22 / 30 |
| 재검색으로 근거 확보 | Q-007·Q-014·Q-024 (`relax_period`) · Q-030 (`corp_only`) |
| hop 상한 도달 | Q-010 (major 문서 — 근거 0건으로 한계 고지) |
| **`exchange` 골든 적중** | **16 / 16 — 14건 1위, 2건 2위 (전부 top-3)** |

골든 미적중 8건은 전부 `major`(4) · `holding`(4) 문서군이다. BM25 인덱스가 `exchange` 1,469건만
덮고 있어서지 hop 로직 때문이 아니다 — **Phase H 의 `RETRIEVAL` 실패는 이 문서군에 몰릴 것이다.**

### 아직 안 한 것

- `backoff 휴리스틱` 2종은 **검색 쪽만** 구현했다. 생성 쪽 `ReAct → CoT-SC` /
  `CoT-SC → ReAct` 전환은 SC 샘플링이 붙는 시점(F-5)의 일이다.
- 골든 필드 판정은 `exchange` 마크다운 스키마(`- 계약금액: ...`)에 묶여 있다.
  `periodic`·`major`·`holding` 이 인덱싱되면 그 스키마의 라벨을 `_FIELD_HINTS` 에 추가해야 한다.

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
      — `list_sections`/`read_section` 이 `{"error": ...}` 여도 파이프라인이 5키 스키마를
      지키는 것까지는 테스트로 고정됐다 (`test_section_tool_errors_do_not_break_pipeline`).
      실제 하강은 인덱스가 붙은 뒤.
- [x] ReAct backoff 동작, hop 상한 준수
      — hop 4단(최초 1 + 재검색 3), `Budget.spend_hop()`·`spend_step()` 연동,
      상한 초과 시 현재 근거로 진행. 평가셋 30문항 hop 합계 14 · 최대 스텝 5 · LLM 0회
- [x] 질문당 호출 수가 라우트별로 기록됨
      — `think_trace` 끝에 `budget steps=N hops=M llm=K tokens=T`, hop 별 로그는
      `pipeline.parse_hops()` 로 파싱된다
