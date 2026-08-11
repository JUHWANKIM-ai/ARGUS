# Phase H — 3축 교차집계 (dev)

생성 2026-08-11 13:00 · 자동 생성물 (`scripts/error_analysis.py`)

셀 표기 = `정답/채점대상` + 실패단계. **채점 대상에서 DATA_LIMIT 은 뺐다.**


## 18셀

| task_type | mode | 하 | 중 | 상 |
|---|---|---|---|---|
| search_extract | closed | 2/4 ROUTING2 | 1/4 ROUTING2 TOOL1 | 0/4 RETRIEVAL3 ROUTING1 |
| search_extract | open | 3/4 ROUTING1 | 1/4 ROUTING3 | 1/4 ROUTING3 |
| multi_compare | closed | 2/4 ROUTING2 | 0/4 RETRIEVAL2 ROUTING2 | 0/4 RETRIEVAL2 ROUTING2 |
| multi_compare | open | 1/4 ROUTING3 | 1/4 ROUTING3 | 0/4 RETRIEVAL3 ROUTING1 |
| complex_reason | closed | 3/4 ROUTING1 | 0/4 ROUTING3 TOOL1 | 0/4 RETRIEVAL1 ROUTING3 |
| complex_reason | open | 1/4 GENERATION1 ROUTING2 | 2/4 ROUTING2 | 0/4 RETRIEVAL1 ROUTING3 |

## 축별 주변합


### task_type

| 값 | 정답률 | ROUTING | RETRIEVAL | RANKING | TOOL | GENERATION | VERIFY | DATA_LIMIT |
|---|---|---|---|---|---|---|---|---|
| search_extract | 0.333 (8/24) | 12 | 3 | 0 | 1 | 0 | 0 | 0 |
| multi_compare | 0.167 (4/24) | 13 | 7 | 0 | 0 | 0 | 0 | 0 |
| complex_reason | 0.250 (6/24) | 14 | 2 | 0 | 1 | 1 | 0 | 0 |

### answer_mode

| 값 | 정답률 | ROUTING | RETRIEVAL | RANKING | TOOL | GENERATION | VERIFY | DATA_LIMIT |
|---|---|---|---|---|---|---|---|---|
| closed | 0.222 (8/36) | 18 | 8 | 0 | 2 | 0 | 0 | 0 |
| open | 0.278 (10/36) | 21 | 4 | 0 | 0 | 1 | 0 | 0 |

### difficulty

| 값 | 정답률 | ROUTING | RETRIEVAL | RANKING | TOOL | GENERATION | VERIFY | DATA_LIMIT |
|---|---|---|---|---|---|---|---|---|
| 하 | 0.500 (12/24) | 11 | 0 | 0 | 0 | 1 | 0 | 0 |
| 중 | 0.208 (5/24) | 15 | 2 | 0 | 2 | 0 | 0 | 0 |
| 상 | 0.042 (1/24) | 13 | 10 | 0 | 0 | 0 | 0 | 0 |

## 실패 단계 히스토그램

| 단계 | 건수 | 비고 |
|---|---|---|
| ROUTING | 45 |  |
| RETRIEVAL | 14 |  |
| TOOL | 2 |  |
| GENERATION | 1 |  |

## 근본 원인 — 6단계만으로는 arm 이 안 갈린다 (**그리드만**)

| 단계 / 원인 | 건수 | 문항 | 겨냥할 arm |
|---|---|---|---|
| ROUTING/ROUTING | 39 | Q-010 Q-012 Q-014 Q-016 Q-020 Q-022 Q-028 Q-030 Q-043 Q-044 Q-045 Q-046 Q-048 Q-049 Q-050 Q-051 Q-052 Q-053 Q-054 Q-072 Q-074 Q-076 Q-078 Q-080 Q-082 Q-084 Q-086 Q-088 Q-090 Q-092 Q-094 Q-096 Q-098 Q-100 Q-102 Q-103 Q-104 Q-105 Q-106 | — |
| RETRIEVAL/CHAIN_NOT_TRAVERSED | 9 | Q-005 Q-006 Q-018 Q-023 Q-024 Q-075 Q-087 Q-093 Q-099 | **A4** — `get_history` 로 정정 체인을 밟는 hop |
| TOOL/TOOL | 2 | Q-003 Q-097 | **A5** — 계산 Tool |
| RETRIEVAL/MULTI_POINT_QUERY | 2 | Q-015 Q-085 | **A4** — 시점·기업별 질의 분해 |
| RETRIEVAL/CHAIN_PARTIAL | 1 | Q-035 | **A4** — 체인 + 후속공시 연결 규칙 |
| GENERATION/INDEX_FIELD_MISSING | 1 | Q-101 | **A1b** — 필드 추출 (⚠ 계산 답이면 오라벨) |

### 그리드 밖 트랙 (정답률·겨냥 건수에 합치지 않는다)

| 단계 / 원인 | 건수 | 문항 | track |
|---|---|---|---|
| ROUTING/ROUTING | 1 | Q-061 | regression |
| ROUTING/ROUTING | 1 | Q-062 | regression |
| ROUTING/ROUTING | 1 | Q-063 | regression |
| ROUTING/ROUTING | 1 | Q-064 | regression |
| RETRIEVAL/MULTI_POINT_QUERY | 1 | Q-065 | regression |
| RETRIEVAL/MULTI_POINT_QUERY | 1 | Q-066 | regression |
| ROUTING/ROUTING | 1 | Q-067 | regression |
| ROUTING/ROUTING | 1 | Q-068 | regression |

## 🔴 arm 배분 — 자동 분류기 vs 수동 원문 대조 (**정본은 수동**)

자동 분류기는 A5 를 구조적으로 못 본다 — `verify_calculation` 이 답변 수치 중 최유리 값을 골라 비교하므로 10× 단위환산 오류가 `mismatch=False` 로 통과하고, TOOL 버킷이 그리드에서 0건이 된다. arm 확정은 원문 대조(`TARGETED_BY_MANUAL_REVIEW`)의 몫이다.

분모가 다르다: 자동 = 그리드만 54건 · 수동 = 전 트랙 13건. arm 별 건수를 나란히 읽을 때 그 자체로 어긋나므로 두 축을 다 낸다.

| arm | 자동(그리드) | 자동(전 트랙) | **수동 정본(전 트랙)** |
|---|---|---|---|
| A1b | 1 | 1 | **2** |
| A3 | 0 | 0 | **1** |
| A4 | 12 | 14 | **5** |
| A5 | 2 | 2 | **4** |
| A6 | 0 | 0 | **1** |
| 미분류 | 39 | 45 | **0** |

**불일치 58/62건** — 문항 단위로 적는다. 사람이 기억으로 잇던 자리이고, 그 기억이 두 번 틀렸다.

| 문항 | track | 자동 버킷 | 자동 arm | **수동 정본 arm** |
|---|---|---|---|---|
| Q-003 | grid | `TOOL/TOOL` | A5 | **—** |
| Q-005 | grid | `RETRIEVAL/CHAIN_NOT_TRAVERSED` | A4 | **—** |
| Q-006 | grid | `RETRIEVAL/CHAIN_NOT_TRAVERSED` | A4 | **—** |
| Q-010 | grid | `ROUTING/ROUTING` | 미분류 | **—** |
| Q-012 | grid | `ROUTING/ROUTING` | 미분류 | **—** |
| Q-014 | grid | `ROUTING/ROUTING` | 미분류 | **—** |
| Q-015 | grid | `RETRIEVAL/MULTI_POINT_QUERY` | A4 | **—** |
| Q-016 | grid | `ROUTING/ROUTING` | 미분류 | **—** |
| Q-018 | grid | `RETRIEVAL/CHAIN_NOT_TRAVERSED` | A4 | **—** |
| Q-020 | grid | `ROUTING/ROUTING` | 미분류 | **—** |
| Q-022 | grid | `ROUTING/ROUTING` | 미분류 | **—** |
| Q-024 | grid | `RETRIEVAL/CHAIN_NOT_TRAVERSED` | A4 | **—** |
| Q-028 | grid | `ROUTING/ROUTING` | 미분류 | **—** |
| Q-030 | grid | `ROUTING/ROUTING` | 미분류 | **—** |
| Q-035 | grid | `RETRIEVAL/CHAIN_PARTIAL` | A4 | **—** |
| Q-043 | grid | `ROUTING/ROUTING` | 미분류 | **—** |
| Q-044 | grid | `ROUTING/ROUTING` | 미분류 | **A5** |
| Q-045 | grid | `ROUTING/ROUTING` | 미분류 | **—** |
| Q-046 | grid | `ROUTING/ROUTING` | 미분류 | **—** |
| Q-048 | grid | `ROUTING/ROUTING` | 미분류 | **—** |
| Q-049 | grid | `ROUTING/ROUTING` | 미분류 | **—** |
| Q-050 | grid | `ROUTING/ROUTING` | 미분류 | **—** |
| Q-051 | grid | `ROUTING/ROUTING` | 미분류 | **—** |
| Q-052 | grid | `ROUTING/ROUTING` | 미분류 | **—** |
| Q-053 | grid | `ROUTING/ROUTING` | 미분류 | **—** |
| Q-054 | grid | `ROUTING/ROUTING` | 미분류 | **—** |
| Q-061 | regression | `ROUTING/ROUTING` | 미분류 | **—** |
| Q-062 | regression | `ROUTING/ROUTING` | 미분류 | **—** |
| Q-063 | regression | `ROUTING/ROUTING` | 미분류 | **—** |
| Q-064 | regression | `ROUTING/ROUTING` | 미분류 | **—** |
| Q-065 | regression | `RETRIEVAL/MULTI_POINT_QUERY` | A4 | **A5** |
| Q-067 | regression | `ROUTING/ROUTING` | 미분류 | **—** |
| Q-068 | regression | `ROUTING/ROUTING` | 미분류 | **—** |
| Q-072 | grid | `ROUTING/ROUTING` | 미분류 | **—** |
| Q-074 | grid | `ROUTING/ROUTING` | 미분류 | **—** |
| Q-075 | grid | `RETRIEVAL/CHAIN_NOT_TRAVERSED` | A4 | **—** |
| Q-076 | grid | `ROUTING/ROUTING` | 미분류 | **A4** |
| Q-078 | grid | `ROUTING/ROUTING` | 미분류 | **—** |
| Q-080 | grid | `ROUTING/ROUTING` | 미분류 | **—** |
| Q-082 | grid | `ROUTING/ROUTING` | 미분류 | **—** |
| Q-084 | grid | `ROUTING/ROUTING` | 미분류 | **—** |
| Q-085 | grid | `RETRIEVAL/MULTI_POINT_QUERY` | A4 | **—** |
| Q-086 | grid | `ROUTING/ROUTING` | 미분류 | **—** |
| Q-087 | grid | `RETRIEVAL/CHAIN_NOT_TRAVERSED` | A4 | **A6** |
| Q-088 | grid | `ROUTING/ROUTING` | 미분류 | **—** |
| Q-090 | grid | `ROUTING/ROUTING` | 미분류 | **—** |
| Q-092 | grid | `ROUTING/ROUTING` | 미분류 | **—** |
| Q-093 | grid | `RETRIEVAL/CHAIN_NOT_TRAVERSED` | A4 | **—** |
| Q-094 | grid | `ROUTING/ROUTING` | 미분류 | **A3** |
| Q-096 | grid | `ROUTING/ROUTING` | 미분류 | **—** |
| Q-098 | grid | `ROUTING/ROUTING` | 미분류 | **A4** |
| Q-099 | grid | `RETRIEVAL/CHAIN_NOT_TRAVERSED` | A4 | **A1b** |
| Q-100 | grid | `ROUTING/ROUTING` | 미분류 | **—** |
| Q-102 | grid | `ROUTING/ROUTING` | 미분류 | **—** |
| Q-103 | grid | `ROUTING/ROUTING` | 미분류 | **—** |
| Q-104 | grid | `ROUTING/ROUTING` | 미분류 | **—** |
| Q-105 | grid | `ROUTING/ROUTING` | 미분류 | **A4** |
| Q-106 | grid | `ROUTING/ROUTING` | 미분류 | **—** |

critic 이 오답을 통과시킨 문항 (귀속 단계와 무관하게 센다): Q-003 Q-006 Q-023 Q-024 Q-035 Q-043 Q-045 Q-050 Q-051 Q-065 Q-085 Q-087 Q-093 Q-101


## 트랙별 (합치지 않는다)

| track | 정답/채점대상 | 정답률 | DATA_LIMIT | 비고 |
|---|---|---|---|---|
| grid | 18/72 | 0.250 | 0 | 3축 결론은 **이 행에서만** 낸다 |
| info_limit | 6/6 | 1.000 | 0 | 거절이 정답. 지어내면 HALLUCINATION |
| regression | 2/10 | 0.200 | 0 | 고친 실패의 감시 — 정답률에 섞지 않는다 |

## 정보한계 문항 (answerable=false)

거절 정답 7/7 · 환각 0


## 신규 6섹터 RETRIEVAL 실패 — 리랭커(Phase E) 재검토 조건

게임 · 로봇 · 소비재·유통 · 자동차·모빌리티 · 철강 · 통신

RETRIEVAL 실패 **3건** (문턱 3) → 🔴 **발동** — Phase E 보류 근거를 다시 본다

| 섹터 | 문항 |
|---|---|
| 게임 | Q-093 |
| 로봇 | Q-075 |
| 소비재·유통 | Q-087 |

## 비용 (실측 usage)

| 항목 | 값 |
|---|---|
| questions | 88 |
| llm_calls | 104 |
| prompt_tokens | 67962 |
| completion_tokens | 10960 |
| judge_tokens | 25711 |
| total_tokens | 104633 |
| cost_won | 734.5 |
| latency_s_mean | 0.73 |
| hops_total | 0 |
| steps_max | 2 |
