# Phase H — 3축 교차집계 (dev)

생성 2026-08-09 15:08 · 자동 생성물 (`scripts/error_analysis.py`)

셀 표기 = `정답/채점대상` + 실패단계. **채점 대상에서 DATA_LIMIT 은 뺐다.**


## 18셀

| task_type | mode | 하 | 중 | 상 |
|---|---|---|---|---|
| search_extract | closed | 2/2 | 1/2 TOOL1 | 0/2 RETRIEVAL2 |
| search_extract | open | 2/2 | 2/2 | 1/2 RETRIEVAL1 |
| multi_compare | closed | 1/2 VERIFY1 | 1/2 RETRIEVAL1 | 1/2 RETRIEVAL1 |
| multi_compare | open | 1/2 RETRIEVAL1 | 2/2 | 0/2 RETRIEVAL2 |
| complex_reason | closed | 2/2 | 2/2 | 1/2 RETRIEVAL1 |
| complex_reason | open | 2/2 | 2/2 | 1/2 RETRIEVAL1 |

## 축별 주변합


### task_type

| 값 | 정답률 | ROUTING | RETRIEVAL | RANKING | TOOL | GENERATION | VERIFY | DATA_LIMIT |
|---|---|---|---|---|---|---|---|---|
| search_extract | 0.667 (8/12) | 0 | 3 | 0 | 1 | 0 | 0 | 0 |
| multi_compare | 0.500 (6/12) | 0 | 5 | 0 | 0 | 0 | 1 | 0 |
| complex_reason | 0.833 (10/12) | 0 | 2 | 0 | 0 | 0 | 0 | 0 |

### answer_mode

| 값 | 정답률 | ROUTING | RETRIEVAL | RANKING | TOOL | GENERATION | VERIFY | DATA_LIMIT |
|---|---|---|---|---|---|---|---|---|
| closed | 0.611 (11/18) | 0 | 5 | 0 | 1 | 0 | 1 | 0 |
| open | 0.722 (13/18) | 0 | 5 | 0 | 0 | 0 | 0 | 0 |

### difficulty

| 값 | 정답률 | ROUTING | RETRIEVAL | RANKING | TOOL | GENERATION | VERIFY | DATA_LIMIT |
|---|---|---|---|---|---|---|---|---|
| 하 | 0.833 (10/12) | 0 | 1 | 0 | 0 | 0 | 1 | 0 |
| 중 | 0.833 (10/12) | 0 | 1 | 0 | 1 | 0 | 0 | 0 |
| 상 | 0.333 (4/12) | 0 | 8 | 0 | 0 | 0 | 0 | 0 |

## 실패 단계 히스토그램

| 단계 | 건수 | 비고 |
|---|---|---|
| RETRIEVAL | 10 |  |
| TOOL | 1 |  |
| VERIFY | 1 |  |

## 근본 원인 — 6단계만으로는 arm 이 안 갈린다

| 단계 / 원인 | 건수 | 문항 | 겨냥할 arm |
|---|---|---|---|
| RETRIEVAL/CHAIN_NOT_TRAVERSED | 6 | Q-005 Q-006 Q-018 Q-023 Q-024 Q-030 | **A4** — `get_history` 로 정정 체인을 밟는 hop |
| RETRIEVAL/MULTI_POINT_QUERY | 3 | Q-012 Q-015 Q-020 | **A4** — 시점·기업별 질의 분해 |
| TOOL/TOOL | 1 | Q-003 | **A5** — 계산 Tool |
| RETRIEVAL/CHAIN_PARTIAL | 1 | Q-035 | **A4** — 체인 + 후속공시 연결 규칙 |
| VERIFY/VERIFY | 1 | Q-047 | **A6** — Verifier |

## 정보한계 6문항 (answerable=false)

거절 정답 6/6 · 환각 0


## 비용 (실측 usage)

| 항목 | 값 |
|---|---|
| questions | 42 |
| llm_calls | 57 |
| prompt_tokens | 100373 |
| completion_tokens | 6895 |
| judge_tokens | 32769 |
| total_tokens | 140037 |
| cost_won | 983.1 |
| latency_s_mean | 2.33 |
| hops_total | 0 |
| steps_max | 4 |
