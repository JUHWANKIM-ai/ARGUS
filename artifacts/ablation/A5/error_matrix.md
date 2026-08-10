# Phase H — 3축 교차집계 (dev)

생성 2026-08-09 15:08 · 자동 생성물 (`scripts/error_analysis.py`)

셀 표기 = `정답/채점대상` + 실패단계. **채점 대상에서 DATA_LIMIT 은 뺐다.**


## 18셀

| task_type | mode | 하 | 중 | 상 |
|---|---|---|---|---|
| search_extract | closed | 2/2 | 1/2 GENERATION1 | 2/2 |
| search_extract | open | 2/2 | 2/2 | 2/2 |
| multi_compare | closed | 1/2 VERIFY1 | 2/2 | 1/2 GENERATION1 |
| multi_compare | open | 2/2 | 1/2 TOOL1 | 1/2 GENERATION1 |
| complex_reason | closed | 2/2 | 2/2 | 2/2 |
| complex_reason | open | 2/2 | 2/2 | 2/2 |

## 축별 주변합


### task_type

| 값 | 정답률 | ROUTING | RETRIEVAL | RANKING | TOOL | GENERATION | VERIFY | DATA_LIMIT |
|---|---|---|---|---|---|---|---|---|
| search_extract | 0.917 (11/12) | 0 | 0 | 0 | 0 | 1 | 0 | 0 |
| multi_compare | 0.667 (8/12) | 0 | 0 | 0 | 1 | 2 | 1 | 0 |
| complex_reason | 1.000 (12/12) | 0 | 0 | 0 | 0 | 0 | 0 | 0 |

### answer_mode

| 값 | 정답률 | ROUTING | RETRIEVAL | RANKING | TOOL | GENERATION | VERIFY | DATA_LIMIT |
|---|---|---|---|---|---|---|---|---|
| closed | 0.833 (15/18) | 0 | 0 | 0 | 0 | 2 | 1 | 0 |
| open | 0.889 (16/18) | 0 | 0 | 0 | 1 | 1 | 0 | 0 |

### difficulty

| 값 | 정답률 | ROUTING | RETRIEVAL | RANKING | TOOL | GENERATION | VERIFY | DATA_LIMIT |
|---|---|---|---|---|---|---|---|---|
| 하 | 0.917 (11/12) | 0 | 0 | 0 | 0 | 0 | 1 | 0 |
| 중 | 0.833 (10/12) | 0 | 0 | 0 | 1 | 1 | 0 | 0 |
| 상 | 0.833 (10/12) | 0 | 0 | 0 | 0 | 2 | 0 | 0 |

## 실패 단계 히스토그램

| 단계 | 건수 | 비고 |
|---|---|---|
| TOOL | 1 |  |
| GENERATION | 3 |  |
| VERIFY | 1 |  |
| HALLUCINATION | 1 | **진짜 실패** — 거절해야 할 질문에 답을 지어냄 |

## 근본 원인 — 6단계만으로는 arm 이 안 갈린다

| 단계 / 원인 | 건수 | 문항 | 겨냥할 arm |
|---|---|---|---|
| GENERATION/INDEX_FIELD_MISSING | 2 | Q-018 Q-044 | **A1b** — major·holding 필드 추출 |
| TOOL/TOOL | 1 | Q-022 | **A5** — 계산 Tool |
| GENERATION/REASONING | 1 | Q-023 | **A4** — 다단계 추론 |
| HALLUCINATION/HALLUCINATION | 1 | Q-040 | — |
| VERIFY/VERIFY | 1 | Q-047 | **A6** — Verifier |

## 정보한계 6문항 (answerable=false)

거절 정답 5/6 · 환각 1


## 비용 (실측 usage)

| 항목 | 값 |
|---|---|
| questions | 42 |
| llm_calls | 54 |
| prompt_tokens | 122193 |
| completion_tokens | 7634 |
| judge_tokens | 37368 |
| total_tokens | 167195 |
| cost_won | 1173.7 |
| latency_s_mean | 0.04 |
| hops_total | 10 |
| steps_max | 5 |
