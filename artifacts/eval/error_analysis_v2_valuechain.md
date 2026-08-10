# Phase H — 3축 교차집계 (dev)

생성 2026-08-10 14:09 · 자동 생성물 (`scripts/error_analysis.py`)

셀 표기 = `정답/채점대상` + 실패단계. **채점 대상에서 DATA_LIMIT 은 뺐다.**


## 18셀

| task_type | mode | 하 | 중 | 상 |
|---|---|---|---|---|
| search_extract | closed | — | — | — |
| search_extract | open | — | — | — |
| multi_compare | closed | — | — | — |
| multi_compare | open | — | — | — |
| complex_reason | closed | — | — | — |
| complex_reason | open | — | — | — |

## 축별 주변합


### task_type

| 값 | 정답률 | ROUTING | RETRIEVAL | RANKING | TOOL | GENERATION | VERIFY | DATA_LIMIT |
|---|---|---|---|---|---|---|---|---|
| search_extract | — (0/0) | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| multi_compare | — (0/0) | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| complex_reason | — (0/0) | 0 | 0 | 0 | 0 | 0 | 0 | 0 |

### answer_mode

| 값 | 정답률 | ROUTING | RETRIEVAL | RANKING | TOOL | GENERATION | VERIFY | DATA_LIMIT |
|---|---|---|---|---|---|---|---|---|
| closed | — (0/0) | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| open | — (0/0) | 0 | 0 | 0 | 0 | 0 | 0 | 0 |

### difficulty

| 값 | 정답률 | ROUTING | RETRIEVAL | RANKING | TOOL | GENERATION | VERIFY | DATA_LIMIT |
|---|---|---|---|---|---|---|---|---|
| 하 | — (0/0) | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| 중 | — (0/0) | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| 상 | — (0/0) | 0 | 0 | 0 | 0 | 0 | 0 | 0 |

## 실패 단계 히스토그램

| 단계 | 건수 | 비고 |
|---|---|---|
| ROUTING | 1 |  |
| RETRIEVAL | 3 |  |
| TOOL | 1 |  |
| GENERATION | 1 |  |

## 근본 원인 — 6단계만으로는 arm 이 안 갈린다 (**그리드만**)

| 단계 / 원인 | 건수 | 문항 | 겨냥할 arm |
|---|---|---|---|
| — | 0 | | |

### 그리드 밖 트랙 (정답률·겨냥 건수에 합치지 않는다)

| 단계 / 원인 | 건수 | 문항 | track |
|---|---|---|---|
| RETRIEVAL/MULTI_POINT_QUERY | 1 | Q-107 | valuechain |
| GENERATION/INDEX_FIELD_MISSING | 1 | Q-109 | valuechain |
| ROUTING/ROUTING | 1 | Q-111 | valuechain |
| RETRIEVAL/CHAIN_NOT_TRAVERSED | 1 | Q-113 | valuechain |
| TOOL/TOOL | 1 | Q-115 | valuechain |
| RETRIEVAL/MULTI_POINT_QUERY | 1 | Q-117 | valuechain |

## 트랙별 (합치지 않는다)

| track | 정답/채점대상 | 정답률 | DATA_LIMIT | 비고 |
|---|---|---|---|---|
| valuechain | 6/12 | 0.500 | 0 | 현재 시스템이 원리상 못 푸는 문항 포함 (Q-117) |

## 정보한계 문항 (answerable=false)

거절 정답 0/0 · 환각 0


## 신규 6섹터 RETRIEVAL 실패 — 리랭커(Phase E) 재검토 조건

게임 · 로봇 · 소비재·유통 · 자동차·모빌리티 · 철강 · 통신

RETRIEVAL 실패 **0건** (문턱 3) → 미발동 — 보류 유지


## 비용 (실측 usage)

| 항목 | 값 |
|---|---|
| questions | 12 |
| llm_calls | 21 |
| prompt_tokens | 37654 |
| completion_tokens | 3418 |
| judge_tokens | 9079 |
| total_tokens | 50151 |
| cost_won | 352.1 |
| latency_s_mean | 7.31 |
| hops_total | 4 |
| steps_max | 6 |
