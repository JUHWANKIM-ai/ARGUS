# Phase H — 3축 교차집계 (dev)

생성 2026-08-11 12:12 · 자동 생성물 (`scripts/error_analysis.py`)

셀 표기 = `정답/채점대상` + 실패단계. **채점 대상에서 DATA_LIMIT 은 뺐다.**


## 18셀

| task_type | mode | 하 | 중 | 상 |
|---|---|---|---|---|
| search_extract | closed | 3/4 RETRIEVAL1 | 1/4 RANKING1 RETRIEVAL1 TOOL1 | 0/4 RANKING1 RETRIEVAL3 |
| search_extract | open | 3/4 GENERATION1 | 1/4 GENERATION1 RANKING1 RETRIEVAL1 | 0/4 RANKING1 RETRIEVAL3 |
| multi_compare | closed | 3/4 RETRIEVAL1 | 0/4 GENERATION1 RANKING1 RETRIEVAL2 | 1/4 RANKING1 RETRIEVAL2 |
| multi_compare | open | 3/4 RETRIEVAL1 | 1/4 GENERATION1 RETRIEVAL2 | 0/4 RANKING1 RETRIEVAL3 |
| complex_reason | closed | 3/4 GENERATION1 | 0/4 GENERATION1 RANKING1 RETRIEVAL1 TOOL1 | 1/4 RANKING1 RETRIEVAL2 |
| complex_reason | open | 2/4 GENERATION1 RANKING1 | 2/4 GENERATION1 RETRIEVAL1 | 0/4 GENERATION1 RANKING1 RETRIEVAL2 |

## 축별 주변합


### task_type

| 값 | 정답률 | ROUTING | RETRIEVAL | RANKING | TOOL | GENERATION | VERIFY | DATA_LIMIT |
|---|---|---|---|---|---|---|---|---|
| search_extract | 0.333 (8/24) | 0 | 9 | 4 | 1 | 2 | 0 | 0 |
| multi_compare | 0.333 (8/24) | 0 | 11 | 3 | 0 | 2 | 0 | 0 |
| complex_reason | 0.333 (8/24) | 0 | 6 | 4 | 1 | 5 | 0 | 0 |

### answer_mode

| 값 | 정답률 | ROUTING | RETRIEVAL | RANKING | TOOL | GENERATION | VERIFY | DATA_LIMIT |
|---|---|---|---|---|---|---|---|---|
| closed | 0.333 (12/36) | 0 | 13 | 6 | 2 | 3 | 0 | 0 |
| open | 0.333 (12/36) | 0 | 13 | 5 | 0 | 6 | 0 | 0 |

### difficulty

| 값 | 정답률 | ROUTING | RETRIEVAL | RANKING | TOOL | GENERATION | VERIFY | DATA_LIMIT |
|---|---|---|---|---|---|---|---|---|
| 하 | 0.708 (17/24) | 0 | 3 | 1 | 0 | 3 | 0 | 0 |
| 중 | 0.208 (5/24) | 0 | 8 | 4 | 2 | 5 | 0 | 0 |
| 상 | 0.083 (2/24) | 0 | 15 | 6 | 0 | 1 | 0 | 0 |

## 실패 단계 히스토그램

| 단계 | 건수 | 비고 |
|---|---|---|
| RETRIEVAL | 30 |  |
| RANKING | 12 |  |
| TOOL | 2 |  |
| GENERATION | 12 |  |

## 근본 원인 — 6단계만으로는 arm 이 안 갈린다 (**그리드만**)

| 단계 / 원인 | 건수 | 문항 | 겨냥할 arm |
|---|---|---|---|
| RETRIEVAL/MULTI_POINT_QUERY | 14 | Q-012 Q-015 Q-020 Q-043 Q-044 Q-045 Q-046 Q-050 Q-051 Q-084 Q-085 Q-092 Q-104 Q-105 | **A4** — 시점·기업별 질의 분해 |
| RETRIEVAL/CHAIN_NOT_TRAVERSED | 11 | Q-005 Q-006 Q-018 Q-023 Q-024 Q-030 Q-075 Q-081 Q-087 Q-093 Q-099 | **A4** — `get_history` 로 정정 체인을 밟는 hop |
| GENERATION/INDEX_FIELD_MISSING | 6 | Q-016 Q-028 Q-078 Q-096 Q-101 Q-106 | **A1b** — 필드 추출 (⚠ 계산 답이면 오라벨) |
| RANKING/LOW_RANK | 6 | Q-053 Q-074 Q-080 Q-082 Q-086 Q-098 | **A3** — 리랭커 |
| RANKING/WRONG_SECTION | 5 | Q-048 Q-052 Q-054 Q-076 Q-094 | **A3** — 목차 경로 리랭킹 |
| GENERATION/REASONING | 3 | Q-010 Q-022 Q-103 | **A4** — 다단계 추론 |
| TOOL/TOOL | 2 | Q-003 Q-097 | **A5** — 계산 Tool |
| RETRIEVAL/CHAIN_PARTIAL | 1 | Q-035 | **A4** — 체인 + 후속공시 연결 규칙 |

### 그리드 밖 트랙 (정답률·겨냥 건수에 합치지 않는다)

| 단계 / 원인 | 건수 | 문항 | track |
|---|---|---|---|
| RETRIEVAL/CHAIN_NOT_TRAVERSED | 1 | Q-061 | regression |
| GENERATION/INDEX_FIELD_MISSING | 1 | Q-062 | regression |
| GENERATION/REASONING | 1 | Q-063 | regression |
| GENERATION/INDEX_FIELD_MISSING | 1 | Q-064 | regression |
| RETRIEVAL/MULTI_POINT_QUERY | 1 | Q-065 | regression |
| RETRIEVAL/MULTI_POINT_QUERY | 1 | Q-066 | regression |
| RANKING/WRONG_SECTION | 1 | Q-067 | regression |
| RETRIEVAL/MULTI_POINT_QUERY | 1 | Q-068 | regression |

## 🔴 arm 배분 — 자동 분류기 vs 수동 원문 대조 (**정본은 수동**)

자동 분류기는 A5 를 구조적으로 못 본다 — `verify_calculation` 이 답변 수치 중 최유리 값을 골라 비교하므로 10× 단위환산 오류가 `mismatch=False` 로 통과하고, TOOL 버킷이 그리드에서 0건이 된다. arm 확정은 원문 대조(`TARGETED_BY_MANUAL_REVIEW`)의 몫이다.

분모가 다르다: 자동 = 그리드만 48건 · 수동 = 전 트랙 13건. arm 별 건수를 나란히 읽을 때 그 자체로 어긋나므로 두 축을 다 낸다.

| arm | 자동(그리드) | 자동(전 트랙) | **수동 정본(전 트랙)** |
|---|---|---|---|
| A1b | 6 | 8 | **2** |
| A3 | 11 | 12 | **1** |
| A4 | 29 | 34 | **5** |
| A5 | 2 | 2 | **4** |
| A6 | 0 | 0 | **1** |

**불일치 50/56건** — 문항 단위로 적는다. 사람이 기억으로 잇던 자리이고, 그 기억이 두 번 틀렸다.

| 문항 | track | 자동 버킷 | 자동 arm | **수동 정본 arm** |
|---|---|---|---|---|
| Q-003 | grid | `TOOL/TOOL` | A5 | **—** |
| Q-005 | grid | `RETRIEVAL/CHAIN_NOT_TRAVERSED` | A4 | **—** |
| Q-006 | grid | `RETRIEVAL/CHAIN_NOT_TRAVERSED` | A4 | **—** |
| Q-010 | grid | `GENERATION/REASONING` | A4 | **—** |
| Q-012 | grid | `RETRIEVAL/MULTI_POINT_QUERY` | A4 | **—** |
| Q-015 | grid | `RETRIEVAL/MULTI_POINT_QUERY` | A4 | **—** |
| Q-016 | grid | `GENERATION/INDEX_FIELD_MISSING` | A1b | **—** |
| Q-018 | grid | `RETRIEVAL/CHAIN_NOT_TRAVERSED` | A4 | **—** |
| Q-020 | grid | `RETRIEVAL/MULTI_POINT_QUERY` | A4 | **—** |
| Q-022 | grid | `GENERATION/REASONING` | A4 | **—** |
| Q-024 | grid | `RETRIEVAL/CHAIN_NOT_TRAVERSED` | A4 | **—** |
| Q-028 | grid | `GENERATION/INDEX_FIELD_MISSING` | A1b | **—** |
| Q-030 | grid | `RETRIEVAL/CHAIN_NOT_TRAVERSED` | A4 | **—** |
| Q-035 | grid | `RETRIEVAL/CHAIN_PARTIAL` | A4 | **—** |
| Q-043 | grid | `RETRIEVAL/MULTI_POINT_QUERY` | A4 | **—** |
| Q-044 | grid | `RETRIEVAL/MULTI_POINT_QUERY` | A4 | **A5** |
| Q-045 | grid | `RETRIEVAL/MULTI_POINT_QUERY` | A4 | **—** |
| Q-046 | grid | `RETRIEVAL/MULTI_POINT_QUERY` | A4 | **—** |
| Q-048 | grid | `RANKING/WRONG_SECTION` | A3 | **—** |
| Q-050 | grid | `RETRIEVAL/MULTI_POINT_QUERY` | A4 | **—** |
| Q-051 | grid | `RETRIEVAL/MULTI_POINT_QUERY` | A4 | **—** |
| Q-052 | grid | `RANKING/WRONG_SECTION` | A3 | **—** |
| Q-053 | grid | `RANKING/LOW_RANK` | A3 | **—** |
| Q-054 | grid | `RANKING/WRONG_SECTION` | A3 | **—** |
| Q-061 | regression | `RETRIEVAL/CHAIN_NOT_TRAVERSED` | A4 | **—** |
| Q-062 | regression | `GENERATION/INDEX_FIELD_MISSING` | A1b | **—** |
| Q-063 | regression | `GENERATION/REASONING` | A4 | **—** |
| Q-064 | regression | `GENERATION/INDEX_FIELD_MISSING` | A1b | **—** |
| Q-065 | regression | `RETRIEVAL/MULTI_POINT_QUERY` | A4 | **A5** |
| Q-067 | regression | `RANKING/WRONG_SECTION` | A3 | **—** |
| Q-068 | regression | `RETRIEVAL/MULTI_POINT_QUERY` | A4 | **—** |
| Q-074 | grid | `RANKING/LOW_RANK` | A3 | **—** |
| Q-075 | grid | `RETRIEVAL/CHAIN_NOT_TRAVERSED` | A4 | **—** |
| Q-076 | grid | `RANKING/WRONG_SECTION` | A3 | **A4** |
| Q-078 | grid | `GENERATION/INDEX_FIELD_MISSING` | A1b | **—** |
| Q-080 | grid | `RANKING/LOW_RANK` | A3 | **—** |
| Q-081 | grid | `RETRIEVAL/CHAIN_NOT_TRAVERSED` | A4 | **—** |
| Q-082 | grid | `RANKING/LOW_RANK` | A3 | **—** |
| Q-084 | grid | `RETRIEVAL/MULTI_POINT_QUERY` | A4 | **—** |
| Q-085 | grid | `RETRIEVAL/MULTI_POINT_QUERY` | A4 | **—** |
| Q-086 | grid | `RANKING/LOW_RANK` | A3 | **—** |
| Q-087 | grid | `RETRIEVAL/CHAIN_NOT_TRAVERSED` | A4 | **A6** |
| Q-092 | grid | `RETRIEVAL/MULTI_POINT_QUERY` | A4 | **—** |
| Q-093 | grid | `RETRIEVAL/CHAIN_NOT_TRAVERSED` | A4 | **—** |
| Q-096 | grid | `GENERATION/INDEX_FIELD_MISSING` | A1b | **—** |
| Q-098 | grid | `RANKING/LOW_RANK` | A3 | **A4** |
| Q-099 | grid | `RETRIEVAL/CHAIN_NOT_TRAVERSED` | A4 | **A1b** |
| Q-103 | grid | `GENERATION/REASONING` | A4 | **—** |
| Q-104 | grid | `RETRIEVAL/MULTI_POINT_QUERY` | A4 | **—** |
| Q-106 | grid | `GENERATION/INDEX_FIELD_MISSING` | A1b | **—** |

critic 이 오답을 통과시킨 문항 (귀속 단계와 무관하게 센다): Q-003 Q-006 Q-010 Q-024 Q-035 Q-044 Q-061 Q-066 Q-082 Q-084 Q-085 Q-087 Q-093 Q-096 Q-101 Q-103


## 트랙별 (합치지 않는다)

| track | 정답/채점대상 | 정답률 | DATA_LIMIT | 비고 |
|---|---|---|---|---|
| grid | 24/72 | 0.333 | 0 | 3축 결론은 **이 행에서만** 낸다 |
| info_limit | 6/6 | 1.000 | 0 | 거절이 정답. 지어내면 HALLUCINATION |
| regression | 2/10 | 0.200 | 0 | 고친 실패의 감시 — 정답률에 섞지 않는다 |

## 정보한계 문항 (answerable=false)

거절 정답 7/7 · 환각 0


## 신규 6섹터 RETRIEVAL 실패 — 리랭커(Phase E) 재검토 조건

게임 · 로봇 · 소비재·유통 · 자동차·모빌리티 · 철강 · 통신

RETRIEVAL 실패 **6건** (문턱 3) → 🔴 **발동** — Phase E 보류 근거를 다시 본다

| 섹터 | 문항 |
|---|---|
| 게임 | Q-093 |
| 로봇 | Q-075 |
| 소비재·유통 | Q-087 Q-092 |
| 철강 | Q-084 |
| 통신 | Q-105 |

## 비용 (실측 usage)

| 항목 | 값 |
|---|---|
| questions | 88 |
| llm_calls | 129 |
| prompt_tokens | 215646 |
| completion_tokens | 15097 |
| judge_tokens | 67639 |
| total_tokens | 298382 |
| cost_won | 2094.6 |
| latency_s_mean | 3.73 |
| hops_total | 0 |
| steps_max | 0 |
