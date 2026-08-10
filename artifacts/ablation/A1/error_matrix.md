# Phase H — 3축 교차집계 (dev)

생성 2026-08-09 15:08 · 자동 생성물 (`scripts/error_analysis.py`)

셀 표기 = `정답/채점대상` + 실패단계. **채점 대상에서 DATA_LIMIT 은 뺐다.**


## 18셀

| task_type | mode | 하 | 중 | 상 |
|---|---|---|---|---|
| search_extract | closed | 1/2 ROUTING1 | 0/2 ROUTING1 TOOL1 | 0/2 RETRIEVAL2 |
| search_extract | open | 2/2 | 1/2 ROUTING1 | 0/2 ROUTING2 |
| multi_compare | closed | 2/2 | 0/2 RETRIEVAL1 ROUTING1 | 0/2 RETRIEVAL1 ROUTING1 |
| multi_compare | open | 0/2 ROUTING2 | 0/2 ROUTING2 | 0/2 RETRIEVAL2 |
| complex_reason | closed | 2/2 | 0/2 ROUTING2 | 0/2 ROUTING2 |
| complex_reason | open | 1/2 ROUTING1 | 2/2 | 0/2 RETRIEVAL1 ROUTING1 |

## 축별 주변합


### task_type

| 값 | 정답률 | ROUTING | RETRIEVAL | RANKING | TOOL | GENERATION | VERIFY | DATA_LIMIT |
|---|---|---|---|---|---|---|---|---|
| search_extract | 0.333 (4/12) | 5 | 2 | 0 | 1 | 0 | 0 | 0 |
| multi_compare | 0.167 (2/12) | 6 | 4 | 0 | 0 | 0 | 0 | 0 |
| complex_reason | 0.417 (5/12) | 6 | 1 | 0 | 0 | 0 | 0 | 0 |

### answer_mode

| 값 | 정답률 | ROUTING | RETRIEVAL | RANKING | TOOL | GENERATION | VERIFY | DATA_LIMIT |
|---|---|---|---|---|---|---|---|---|
| closed | 0.278 (5/18) | 8 | 4 | 0 | 1 | 0 | 0 | 0 |
| open | 0.333 (6/18) | 9 | 3 | 0 | 0 | 0 | 0 | 0 |

### difficulty

| 값 | 정답률 | ROUTING | RETRIEVAL | RANKING | TOOL | GENERATION | VERIFY | DATA_LIMIT |
|---|---|---|---|---|---|---|---|---|
| 하 | 0.667 (8/12) | 4 | 0 | 0 | 0 | 0 | 0 | 0 |
| 중 | 0.250 (3/12) | 7 | 1 | 0 | 1 | 0 | 0 | 0 |
| 상 | 0.000 (0/12) | 6 | 6 | 0 | 0 | 0 | 0 | 0 |

## 실패 단계 히스토그램

| 단계 | 건수 | 비고 |
|---|---|---|
| ROUTING | 17 |  |
| RETRIEVAL | 7 |  |
| TOOL | 1 |  |

## 근본 원인 — 6단계만으로는 arm 이 안 갈린다

| 단계 / 원인 | 건수 | 문항 | 겨냥할 arm |
|---|---|---|---|
| ROUTING/ROUTING | 17 | Q-010 Q-012 Q-016 Q-020 Q-022 Q-028 Q-030 Q-043 Q-044 Q-046 Q-048 Q-049 Q-050 Q-051 Q-052 Q-053 Q-054 | — |
| RETRIEVAL/CHAIN_NOT_TRAVERSED | 5 | Q-005 Q-006 Q-018 Q-023 Q-024 | **A4** — `get_history` 로 정정 체인을 밟는 hop |
| TOOL/TOOL | 1 | Q-003 | **A5** — 계산 Tool |
| RETRIEVAL/MULTI_POINT_QUERY | 1 | Q-015 | **A4** — 시점·기업별 질의 분해 |
| RETRIEVAL/CHAIN_PARTIAL | 1 | Q-035 | **A4** — 체인 + 후속공시 연결 규칙 |

## 정보한계 6문항 (answerable=false)

거절 정답 6/6 · 환각 0


## 비용 (실측 usage)

| 항목 | 값 |
|---|---|
| questions | 42 |
| llm_calls | 55 |
| prompt_tokens | 33243 |
| completion_tokens | 6212 |
| judge_tokens | 14937 |
| total_tokens | 54392 |
| cost_won | 381.8 |
| latency_s_mean | 1.99 |
| hops_total | 0 |
| steps_max | 2 |
