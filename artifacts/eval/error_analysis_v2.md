# Phase H — 3축 교차집계 (dev)

생성 2026-08-10 14:09 · 자동 생성물 (`scripts/error_analysis.py`)

셀 표기 = `정답/채점대상` + 실패단계. **채점 대상에서 DATA_LIMIT 은 뺐다.**


## 18셀

| task_type | mode | 하 | 중 | 상 |
|---|---|---|---|---|
| search_extract | closed | 4/4 | 3/4 GENERATION1 | 3/4 RETRIEVAL1 |
| search_extract | open | 4/4 | 4/4 | 4/4 |
| multi_compare | closed | 3/4 GENERATION1 | 4/4 | 3/4 VERIFY1 |
| multi_compare | open | 4/4 | 4/4 | 2/4 GENERATION1 RANKING1 |
| complex_reason | closed | 4/4 | 2/4 GENERATION1 VERIFY1 | 3/4 GENERATION1 |
| complex_reason | open | 3/4 VERIFY1 | 4/4 | 3/4 RETRIEVAL1 |

## 축별 주변합


### task_type

| 값 | 정답률 | ROUTING | RETRIEVAL | RANKING | TOOL | GENERATION | VERIFY | DATA_LIMIT |
|---|---|---|---|---|---|---|---|---|
| search_extract | 0.917 (22/24) | 0 | 1 | 0 | 0 | 1 | 0 | 0 |
| multi_compare | 0.833 (20/24) | 0 | 0 | 1 | 0 | 2 | 1 | 0 |
| complex_reason | 0.792 (19/24) | 0 | 1 | 0 | 0 | 2 | 2 | 0 |

### answer_mode

| 값 | 정답률 | ROUTING | RETRIEVAL | RANKING | TOOL | GENERATION | VERIFY | DATA_LIMIT |
|---|---|---|---|---|---|---|---|---|
| closed | 0.806 (29/36) | 0 | 1 | 0 | 0 | 4 | 2 | 0 |
| open | 0.889 (32/36) | 0 | 1 | 1 | 0 | 1 | 1 | 0 |

### difficulty

| 값 | 정답률 | ROUTING | RETRIEVAL | RANKING | TOOL | GENERATION | VERIFY | DATA_LIMIT |
|---|---|---|---|---|---|---|---|---|
| 하 | 0.917 (22/24) | 0 | 0 | 0 | 0 | 1 | 1 | 0 |
| 중 | 0.875 (21/24) | 0 | 0 | 0 | 0 | 2 | 1 | 0 |
| 상 | 0.750 (18/24) | 0 | 2 | 1 | 0 | 2 | 1 | 0 |

## 실패 단계 히스토그램

| 단계 | 건수 | 비고 |
|---|---|---|
| RETRIEVAL | 3 |  |
| RANKING | 1 |  |
| GENERATION | 5 |  |
| VERIFY | 4 |  |

## 근본 원인 — 6단계만으로는 arm 이 안 갈린다 (**그리드만**)

| 단계 / 원인 | 건수 | 문항 | 겨냥할 arm |
|---|---|---|---|
| GENERATION/INDEX_FIELD_MISSING | 3 | Q-044 Q-098 Q-099 | **A1b** — 필드 추출 (⚠ 계산 답이면 오라벨) |
| VERIFY/VERIFY | 3 | Q-087 Q-097 Q-101 | **A6** — Verifier (⚠ 계산 오류가 섞인다) |
| RETRIEVAL/MULTI_POINT_QUERY | 2 | Q-076 Q-105 | **A4** — 시점·기업별 질의 분해 |
| GENERATION/REASONING | 1 | Q-023 | **A4** — 다단계 추론 |
| GENERATION/UNVERIFIED | 1 | Q-047 | — 대조할 수치가 없다 |
| RANKING/LOW_RANK | 1 | Q-094 | **A3** — 리랭커 |

### 그리드 밖 트랙 (정답률·겨냥 건수에 합치지 않는다)

| 단계 / 원인 | 건수 | 문항 | track |
|---|---|---|---|
| VERIFY/VERIFY | 1 | Q-065 | regression |
| RETRIEVAL/MULTI_POINT_QUERY | 1 | Q-066 | regression |

## 트랙별 (합치지 않는다)

| track | 정답/채점대상 | 정답률 | DATA_LIMIT | 비고 |
|---|---|---|---|---|
| grid | 61/72 | 0.847 | 0 | 3축 결론은 **이 행에서만** 낸다 |
| info_limit | 6/6 | 1.000 | 0 | 거절이 정답. 지어내면 HALLUCINATION |
| regression | 8/10 | 0.800 | 0 | 고친 실패의 감시 — 정답률에 섞지 않는다 |

## 정보한계 문항 (answerable=false)

거절 정답 7/7 · 환각 0


## 신규 6섹터 RETRIEVAL 실패 — 리랭커(Phase E) 재검토 조건

게임 · 로봇 · 소비재·유통 · 자동차·모빌리티 · 철강 · 통신

RETRIEVAL 실패 **2건** (문턱 3) → 미발동 — 보류 유지

| 섹터 | 문항 |
|---|---|
| 자동차·모빌리티 | Q-076 |
| 통신 | Q-105 |

## 비용 (실측 usage)

| 항목 | 값 |
|---|---|
| questions | 88 |
| llm_calls | 124 |
| prompt_tokens | 247371 |
| completion_tokens | 15567 |
| judge_tokens | 74665 |
| total_tokens | 337603 |
| cost_won | 2370.0 |
| latency_s_mean | 2.63 |
| hops_total | 33 |
| steps_max | 5 |
