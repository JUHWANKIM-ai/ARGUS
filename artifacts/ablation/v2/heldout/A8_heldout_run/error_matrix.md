# Phase H — 3축 교차집계 (dev)

생성 2026-08-10 22:43 · 자동 생성물 (`scripts/error_analysis.py`)

셀 표기 = `정답/채점대상` + 실패단계. **채점 대상에서 DATA_LIMIT 은 뺐다.**


## 18셀

| task_type | mode | 하 | 중 | 상 |
|---|---|---|---|---|
| search_extract | closed | 1/1 | 1/1 | 1/1 |
| search_extract | open | 1/1 | 1/1 | 1/1 |
| multi_compare | closed | 1/1 | 0/1 VERIFY1 | 1/1 |
| multi_compare | open | 1/1 | 0/1 VERIFY1 | 0/1 RETRIEVAL1 |
| complex_reason | closed | 1/1 | 1/1 | 1/1 |
| complex_reason | open | 1/1 | 1/1 | 1/1 |

## 축별 주변합


### task_type

| 값 | 정답률 | ROUTING | RETRIEVAL | RANKING | TOOL | GENERATION | VERIFY | DATA_LIMIT |
|---|---|---|---|---|---|---|---|---|
| search_extract | 1.000 (6/6) | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| multi_compare | 0.500 (3/6) | 0 | 1 | 0 | 0 | 0 | 2 | 0 |
| complex_reason | 1.000 (6/6) | 0 | 0 | 0 | 0 | 0 | 0 | 0 |

### answer_mode

| 값 | 정답률 | ROUTING | RETRIEVAL | RANKING | TOOL | GENERATION | VERIFY | DATA_LIMIT |
|---|---|---|---|---|---|---|---|---|
| closed | 0.889 (8/9) | 0 | 0 | 0 | 0 | 0 | 1 | 0 |
| open | 0.778 (7/9) | 0 | 1 | 0 | 0 | 0 | 1 | 0 |

### difficulty

| 값 | 정답률 | ROUTING | RETRIEVAL | RANKING | TOOL | GENERATION | VERIFY | DATA_LIMIT |
|---|---|---|---|---|---|---|---|---|
| 하 | 1.000 (6/6) | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| 중 | 0.667 (4/6) | 0 | 0 | 0 | 0 | 0 | 2 | 0 |
| 상 | 0.833 (5/6) | 0 | 1 | 0 | 0 | 0 | 0 | 0 |

## 실패 단계 히스토그램

| 단계 | 건수 | 비고 |
|---|---|---|
| RETRIEVAL | 1 |  |
| VERIFY | 2 |  |

## 근본 원인 — 6단계만으로는 arm 이 안 갈린다 (**그리드만**)

| 단계 / 원인 | 건수 | 문항 | 겨냥할 arm |
|---|---|---|---|
| VERIFY/VERIFY | 2 | Q-021 Q-057 | **A6** — Verifier (⚠ 계산 오류가 섞인다) |
| RETRIEVAL/MULTI_POINT_QUERY | 1 | Q-058 | **A4** — 시점·기업별 질의 분해 |

## 트랙별 (합치지 않는다)

| track | 정답/채점대상 | 정답률 | DATA_LIMIT | 비고 |
|---|---|---|---|---|
| grid | 15/18 | 0.833 | 0 | 3축 결론은 **이 행에서만** 낸다 |

## 정보한계 문항 (answerable=false)

거절 정답 0/0 · 환각 0


## 신규 6섹터 RETRIEVAL 실패 — 리랭커(Phase E) 재검토 조건

게임 · 로봇 · 소비재·유통 · 자동차·모빌리티 · 철강 · 통신

RETRIEVAL 실패 **0건** (문턱 3) → 미발동 — 보류 유지


## 비용 (실측 usage)

| 항목 | 값 |
|---|---|
| questions | 18 |
| llm_calls | 28 |
| prompt_tokens | 72419 |
| completion_tokens | 3892 |
| judge_tokens | 19673 |
| total_tokens | 95984 |
| cost_won | 673.8 |
| latency_s_mean | 5.88 |
| hops_total | 6 |
| steps_max | 3 |
