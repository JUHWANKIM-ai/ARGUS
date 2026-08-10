# Phase H — 3축 교차집계 (dev)

생성 2026-08-10 14:09 · 자동 생성물 (`scripts/error_analysis.py`)

셀 표기 = `정답/채점대상` + 실패단계. **채점 대상에서 DATA_LIMIT 은 뺐다.**


## 18셀

| task_type | mode | 하 | 중 | 상 |
|---|---|---|---|---|
| search_extract | closed | 2/2 | 1/2 GENERATION1 | 2/2 |
| search_extract | open | 2/2 | 2/2 | 2/2 |
| multi_compare | closed | 1/2 GENERATION1 | 2/2 | 2/2 |
| multi_compare | open | 2/2 | 2/2 | 1/2 GENERATION1 |
| complex_reason | closed | 2/2 | 2/2 | 2/2 |
| complex_reason | open | 2/2 | 2/2 | 2/2 |

## 축별 주변합


### task_type

| 값 | 정답률 | ROUTING | RETRIEVAL | RANKING | TOOL | GENERATION | VERIFY | DATA_LIMIT |
|---|---|---|---|---|---|---|---|---|
| search_extract | 0.917 (11/12) | 0 | 0 | 0 | 0 | 1 | 0 | 0 |
| multi_compare | 0.833 (10/12) | 0 | 0 | 0 | 0 | 2 | 0 | 0 |
| complex_reason | 1.000 (12/12) | 0 | 0 | 0 | 0 | 0 | 0 | 0 |

### answer_mode

| 값 | 정답률 | ROUTING | RETRIEVAL | RANKING | TOOL | GENERATION | VERIFY | DATA_LIMIT |
|---|---|---|---|---|---|---|---|---|
| closed | 0.889 (16/18) | 0 | 0 | 0 | 0 | 2 | 0 | 0 |
| open | 0.944 (17/18) | 0 | 0 | 0 | 0 | 1 | 0 | 0 |

### difficulty

| 값 | 정답률 | ROUTING | RETRIEVAL | RANKING | TOOL | GENERATION | VERIFY | DATA_LIMIT |
|---|---|---|---|---|---|---|---|---|
| 하 | 0.917 (11/12) | 0 | 0 | 0 | 0 | 1 | 0 | 0 |
| 중 | 0.917 (11/12) | 0 | 0 | 0 | 0 | 1 | 0 | 0 |
| 상 | 0.917 (11/12) | 0 | 0 | 0 | 0 | 1 | 0 | 0 |

## 실패 단계 히스토그램

| 단계 | 건수 | 비고 |
|---|---|---|
| GENERATION | 3 |  |

## 근본 원인 — 6단계만으로는 arm 이 안 갈린다 (**그리드만**)

| 단계 / 원인 | 건수 | 문항 | 겨냥할 arm |
|---|---|---|---|
| GENERATION/REASONING | 1 | Q-023 | **A4** — 다단계 추론 |
| GENERATION/INDEX_FIELD_MISSING | 1 | Q-044 | **A1b** — 필드 추출 (⚠ 계산 답이면 오라벨) |
| GENERATION/UNVERIFIED | 1 | Q-047 | — 대조할 수치가 없다 |

## 트랙별 (합치지 않는다)

| track | 정답/채점대상 | 정답률 | DATA_LIMIT | 비고 |
|---|---|---|---|---|
| grid | 33/36 | 0.917 | 0 | 3축 결론은 **이 행에서만** 낸다 |
| info_limit | 6/6 | 1.000 | 0 | 거절이 정답. 지어내면 HALLUCINATION |

## 정보한계 문항 (answerable=false)

거절 정답 6/6 · 환각 0


## 신규 6섹터 RETRIEVAL 실패 — 리랭커(Phase E) 재검토 조건

게임 · 로봇 · 소비재·유통 · 자동차·모빌리티 · 철강 · 통신

RETRIEVAL 실패 **0건** (문턱 3) → 미발동 — 보류 유지


## 비용 (실측 usage)

| 항목 | 값 |
|---|---|
| questions | 42 |
| llm_calls | 58 |
| prompt_tokens | 123224 |
| completion_tokens | 7533 |
| judge_tokens | 37157 |
| total_tokens | 167914 |
| cost_won | 1178.8 |
| latency_s_mean | 0.04 |
| hops_total | 9 |
| steps_max | 5 |
