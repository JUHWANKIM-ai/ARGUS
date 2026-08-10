# Phase H — 3축 교차집계 (dev)

생성 2026-08-09 13:04 · 자동 생성물 (`scripts/error_analysis.py`)

셀 표기 = `정답/채점대상` + 실패단계. **채점 대상에서 DATA_LIMIT 은 뺐다.**


## 18셀

| task_type | mode | 하 | 중 | 상 |
|---|---|---|---|---|
| search_extract | closed | 2/2 | 2/2 | 0/2 RETRIEVAL2 |
| search_extract | open | 2/2 | 1/2 GENERATION1 | 0/2 RETRIEVAL2 |
| multi_compare | closed | 1/2 RETRIEVAL1 | 1/2 RETRIEVAL1 | 1/2 RETRIEVAL1 |
| multi_compare | open | 0/2 GENERATION1 RANKING1 | 1/2 GENERATION1 | 0/2 RETRIEVAL2 |
| complex_reason | closed | 2/2 | 1/2 GENERATION1 | 0/2 RANKING1 RETRIEVAL1 |
| complex_reason | open | 2/2 | 2/2 | 1/2 RETRIEVAL1 |

## 축별 주변합


### task_type

| 값 | 정답률 | ROUTING | RETRIEVAL | RANKING | TOOL | GENERATION | VERIFY | DATA_LIMIT |
|---|---|---|---|---|---|---|---|---|
| search_extract | 0.583 (7/12) | 0 | 4 | 0 | 0 | 1 | 0 | 0 |
| multi_compare | 0.333 (4/12) | 0 | 5 | 1 | 0 | 2 | 0 | 0 |
| complex_reason | 0.667 (8/12) | 0 | 2 | 1 | 0 | 1 | 0 | 0 |

### answer_mode

| 값 | 정답률 | ROUTING | RETRIEVAL | RANKING | TOOL | GENERATION | VERIFY | DATA_LIMIT |
|---|---|---|---|---|---|---|---|---|
| closed | 0.556 (10/18) | 0 | 6 | 1 | 0 | 1 | 0 | 0 |
| open | 0.500 (9/18) | 0 | 5 | 1 | 0 | 3 | 0 | 0 |

### difficulty

| 값 | 정답률 | ROUTING | RETRIEVAL | RANKING | TOOL | GENERATION | VERIFY | DATA_LIMIT |
|---|---|---|---|---|---|---|---|---|
| 하 | 0.750 (9/12) | 0 | 1 | 1 | 0 | 1 | 0 | 0 |
| 중 | 0.667 (8/12) | 0 | 1 | 0 | 0 | 3 | 0 | 0 |
| 상 | 0.167 (2/12) | 0 | 9 | 1 | 0 | 0 | 0 | 0 |

## 실패 단계 히스토그램

| 단계 | 건수 | 비고 |
|---|---|---|
| RETRIEVAL | 11 |  |
| RANKING | 2 |  |
| GENERATION | 4 |  |

## 근본 원인 — 6단계만으로는 arm 이 안 갈린다

| 단계 / 원인 | 건수 | 문항 | 겨냥할 arm |
|---|---|---|---|
| RETRIEVAL/CHAIN_NOT_TRAVERSED | 6 | Q-005 Q-006 Q-018 Q-023 Q-024 Q-030 | **A4** — `get_history` 로 정정 체인을 밟는 hop |
| GENERATION/INDEX_FIELD_MISSING | 4 | Q-010 Q-020 Q-022 Q-028 | **A1b** — major·holding 필드 추출 |
| RETRIEVAL/MULTI_POINT_QUERY | 4 | Q-012 Q-014 Q-015 Q-046 | **A4** — 시점·기업별 질의 분해 |
| RANKING/WRONG_SECTION | 2 | Q-049 Q-052 | **A3** — 목차 경로 리랭킹 |
| RETRIEVAL/CHAIN_PARTIAL | 1 | Q-035 | **A4** — 체인 + 후속공시 연결 규칙 |

## 정보한계 6문항 (answerable=false)

거절 정답 6/6 · 환각 0


## 비용 (실측 usage)

| 항목 | 값 |
|---|---|
| questions | 42 |
| llm_calls | 56 |
| prompt_tokens | 95756 |
| completion_tokens | 7113 |
| judge_tokens | 31669 |
| total_tokens | 134538 |
| cost_won | 944.5 |
| latency_s_mean | 4.59 |
| hops_total | 12 |
| steps_max | 3 |
