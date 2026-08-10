# Phase H — 3축 교차집계 (dev)

생성 2026-08-09 15:08 · 자동 생성물 (`scripts/error_analysis.py`)

셀 표기 = `정답/채점대상` + 실패단계. **채점 대상에서 DATA_LIMIT 은 뺐다.**


## 18셀

| task_type | mode | 하 | 중 | 상 |
|---|---|---|---|---|
| search_extract | closed | 2/2 | 1/2 GENERATION1 | 0/2 RETRIEVAL2 |
| search_extract | open | 2/2 | 2/2 | 2/2 |
| multi_compare | closed | 1/2 VERIFY1 | 2/2 | 1/2 RETRIEVAL1 |
| multi_compare | open | 2/2 | 2/2 | 0/2 RETRIEVAL2 |
| complex_reason | closed | 2/2 | 2/2 | 1/2 RETRIEVAL1 |
| complex_reason | open | 2/2 | 2/2 | 1/2 RETRIEVAL1 |

## 축별 주변합


### task_type

| 값 | 정답률 | ROUTING | RETRIEVAL | RANKING | TOOL | GENERATION | VERIFY | DATA_LIMIT |
|---|---|---|---|---|---|---|---|---|
| search_extract | 0.750 (9/12) | 0 | 2 | 0 | 0 | 1 | 0 | 0 |
| multi_compare | 0.667 (8/12) | 0 | 3 | 0 | 0 | 0 | 1 | 0 |
| complex_reason | 0.833 (10/12) | 0 | 2 | 0 | 0 | 0 | 0 | 0 |

### answer_mode

| 값 | 정답률 | ROUTING | RETRIEVAL | RANKING | TOOL | GENERATION | VERIFY | DATA_LIMIT |
|---|---|---|---|---|---|---|---|---|
| closed | 0.667 (12/18) | 0 | 4 | 0 | 0 | 1 | 1 | 0 |
| open | 0.833 (15/18) | 0 | 3 | 0 | 0 | 0 | 0 | 0 |

### difficulty

| 값 | 정답률 | ROUTING | RETRIEVAL | RANKING | TOOL | GENERATION | VERIFY | DATA_LIMIT |
|---|---|---|---|---|---|---|---|---|
| 하 | 0.917 (11/12) | 0 | 0 | 0 | 0 | 0 | 1 | 0 |
| 중 | 0.917 (11/12) | 0 | 0 | 0 | 0 | 1 | 0 | 0 |
| 상 | 0.417 (5/12) | 0 | 7 | 0 | 0 | 0 | 0 | 0 |

## 실패 단계 히스토그램

| 단계 | 건수 | 비고 |
|---|---|---|
| RETRIEVAL | 7 |  |
| GENERATION | 1 |  |
| VERIFY | 1 |  |
| HALLUCINATION | 1 | **진짜 실패** — 거절해야 할 질문에 답을 지어냄 |

## 근본 원인 — 6단계만으로는 arm 이 안 갈린다

| 단계 / 원인 | 건수 | 문항 | 겨냥할 arm |
|---|---|---|---|
| RETRIEVAL/CHAIN_NOT_TRAVERSED | 6 | Q-005 Q-006 Q-018 Q-023 Q-024 Q-030 | **A4** — `get_history` 로 정정 체인을 밟는 hop |
| RETRIEVAL/CHAIN_PARTIAL | 1 | Q-035 | **A4** — 체인 + 후속공시 연결 규칙 |
| HALLUCINATION/HALLUCINATION | 1 | Q-040 | — |
| GENERATION/INDEX_FIELD_MISSING | 1 | Q-044 | **A1b** — major·holding 필드 추출 |
| VERIFY/VERIFY | 1 | Q-047 | **A6** — Verifier |

## 정보한계 6문항 (answerable=false)

거절 정답 5/6 · 환각 1


## 비용 (실측 usage)

| 항목 | 값 |
|---|---|
| questions | 42 |
| llm_calls | 53 |
| prompt_tokens | 114293 |
| completion_tokens | 7270 |
| judge_tokens | 35380 |
| total_tokens | 156943 |
| cost_won | 1101.7 |
| latency_s_mean | 1.44 |
| hops_total | 11 |
| steps_max | 4 |
