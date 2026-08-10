# Phase H — 3축 교차집계 (dev)

생성 2026-08-09 15:08 · 자동 생성물 (`scripts/error_analysis.py`)

셀 표기 = `정답/채점대상` + 실패단계. **채점 대상에서 DATA_LIMIT 은 뺐다.**


## 18셀

| task_type | mode | 하 | 중 | 상 |
|---|---|---|---|---|
| search_extract | closed | 1/2 RETRIEVAL1 | 0/2 RETRIEVAL1 TOOL1 | 0/2 RETRIEVAL2 |
| search_extract | open | 2/2 | 0/2 RETRIEVAL1 VERIFY1 | 0/2 RETRIEVAL2 |
| multi_compare | closed | 2/2 | 0/2 GENERATION1 RETRIEVAL1 | 0/2 RANKING1 RETRIEVAL1 |
| multi_compare | open | 0/2 RETRIEVAL1 ROUTING1 | 0/2 GENERATION1 RETRIEVAL1 | 0/2 RETRIEVAL2 |
| complex_reason | closed | 2/2 | 0/2 GENERATION1 RETRIEVAL1 | 0/2 RANKING1 RETRIEVAL1 |
| complex_reason | open | 1/2 RANKING1 | 2/2 | 0/2 RANKING1 RETRIEVAL1 |

## 축별 주변합


### task_type

| 값 | 정답률 | ROUTING | RETRIEVAL | RANKING | TOOL | GENERATION | VERIFY | DATA_LIMIT |
|---|---|---|---|---|---|---|---|---|
| search_extract | 0.250 (3/12) | 0 | 7 | 0 | 1 | 0 | 1 | 0 |
| multi_compare | 0.167 (2/12) | 1 | 6 | 1 | 0 | 2 | 0 | 0 |
| complex_reason | 0.417 (5/12) | 0 | 3 | 3 | 0 | 1 | 0 | 0 |

### answer_mode

| 값 | 정답률 | ROUTING | RETRIEVAL | RANKING | TOOL | GENERATION | VERIFY | DATA_LIMIT |
|---|---|---|---|---|---|---|---|---|
| closed | 0.278 (5/18) | 0 | 8 | 2 | 1 | 2 | 0 | 0 |
| open | 0.278 (5/18) | 1 | 8 | 2 | 0 | 1 | 1 | 0 |

### difficulty

| 값 | 정답률 | ROUTING | RETRIEVAL | RANKING | TOOL | GENERATION | VERIFY | DATA_LIMIT |
|---|---|---|---|---|---|---|---|---|
| 하 | 0.667 (8/12) | 1 | 2 | 1 | 0 | 0 | 0 | 0 |
| 중 | 0.167 (2/12) | 0 | 5 | 0 | 1 | 3 | 1 | 0 |
| 상 | 0.000 (0/12) | 0 | 9 | 3 | 0 | 0 | 0 | 0 |

## 실패 단계 히스토그램

| 단계 | 건수 | 비고 |
|---|---|---|
| ROUTING | 1 |  |
| RETRIEVAL | 16 |  |
| RANKING | 4 |  |
| TOOL | 1 |  |
| GENERATION | 3 |  |
| VERIFY | 1 |  |

## 근본 원인 — 6단계만으로는 arm 이 안 갈린다

| 단계 / 원인 | 건수 | 문항 | 겨냥할 arm |
|---|---|---|---|
| RETRIEVAL/MULTI_POINT_QUERY | 9 | Q-012 Q-015 Q-020 Q-043 Q-044 Q-045 Q-046 Q-050 Q-051 | **A4** — 시점·기업별 질의 분해 |
| RETRIEVAL/CHAIN_NOT_TRAVERSED | 6 | Q-005 Q-006 Q-018 Q-023 Q-024 Q-030 | **A4** — `get_history` 로 정정 체인을 밟는 hop |
| RANKING/WRONG_SECTION | 3 | Q-048 Q-052 Q-054 | **A3** — 목차 경로 리랭킹 |
| GENERATION/INDEX_FIELD_MISSING | 2 | Q-016 Q-028 | **A1b** — major·holding 필드 추출 |
| TOOL/TOOL | 1 | Q-003 | **A5** — 계산 Tool |
| VERIFY/VERIFY | 1 | Q-010 | **A6** — Verifier |
| GENERATION/REASONING | 1 | Q-022 | **A4** — 다단계 추론 |
| RETRIEVAL/CHAIN_PARTIAL | 1 | Q-035 | **A4** — 체인 + 후속공시 연결 규칙 |
| ROUTING/ROUTING | 1 | Q-049 | — |
| RANKING/LOW_RANK | 1 | Q-053 | **A3** — 리랭커 |

## 정보한계 6문항 (answerable=false)

거절 정답 6/6 · 환각 0


## 비용 (실측 usage)

| 항목 | 값 |
|---|---|
| questions | 42 |
| llm_calls | 53 |
| prompt_tokens | 88746 |
| completion_tokens | 6441 |
| judge_tokens | 30873 |
| total_tokens | 126060 |
| cost_won | 884.9 |
| latency_s_mean | 3.19 |
| hops_total | 0 |
| steps_max | 0 |
