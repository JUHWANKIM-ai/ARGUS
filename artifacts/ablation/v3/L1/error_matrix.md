# Phase H — 3축 교차집계 (dev)

생성 2026-08-14 20:56 · 자동 생성물 (`scripts/error_analysis.py`)

셀 표기 = `정답/채점대상` + 실패단계. **채점 대상에서 DATA_LIMIT 은 뺐다.**


## 18셀

| task_type | mode | 하 | 중 | 상 |
|---|---|---|---|---|
| search_extract | closed | 4/4 | 3/4 TOOL1 | 1/4 RETRIEVAL3 |
| search_extract | open | 4/4 | 4/4 | 4/4 |
| multi_compare | closed | 4/4 | 4/4 | 2/4 RETRIEVAL2 |
| multi_compare | open | 4/4 | 4/4 | 1/4 GENERATION1 RETRIEVAL2 |
| complex_reason | closed | 4/4 | 3/4 TOOL1 | 2/4 RETRIEVAL2 |
| complex_reason | open | 4/4 | 4/4 | 3/4 RETRIEVAL1 |

## 축별 주변합


### task_type

| 값 | 정답률 | ROUTING | RETRIEVAL | RANKING | TOOL | GENERATION | VERIFY | DATA_LIMIT |
|---|---|---|---|---|---|---|---|---|
| search_extract | 0.833 (20/24) | 0 | 3 | 0 | 1 | 0 | 0 | 0 |
| multi_compare | 0.792 (19/24) | 0 | 4 | 0 | 0 | 1 | 0 | 0 |
| complex_reason | 0.833 (20/24) | 0 | 3 | 0 | 1 | 0 | 0 | 0 |

### answer_mode

| 값 | 정답률 | ROUTING | RETRIEVAL | RANKING | TOOL | GENERATION | VERIFY | DATA_LIMIT |
|---|---|---|---|---|---|---|---|---|
| closed | 0.750 (27/36) | 0 | 7 | 0 | 2 | 0 | 0 | 0 |
| open | 0.889 (32/36) | 0 | 3 | 0 | 0 | 1 | 0 | 0 |

### difficulty

| 값 | 정답률 | ROUTING | RETRIEVAL | RANKING | TOOL | GENERATION | VERIFY | DATA_LIMIT |
|---|---|---|---|---|---|---|---|---|
| 하 | 1.000 (24/24) | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| 중 | 0.917 (22/24) | 0 | 0 | 0 | 2 | 0 | 0 | 0 |
| 상 | 0.542 (13/24) | 0 | 10 | 0 | 0 | 1 | 0 | 0 |

## 실패 단계 히스토그램

| 단계 | 건수 | 비고 |
|---|---|---|
| RETRIEVAL | 12 |  |
| TOOL | 2 |  |
| GENERATION | 1 |  |

## 근본 원인 — 6단계만으로는 arm 이 안 갈린다 (**그리드만**)

| 단계 / 원인 | 건수 | 문항 | 겨냥할 arm |
|---|---|---|---|
| RETRIEVAL/CHAIN_NOT_TRAVERSED | 9 | Q-005 Q-006 Q-018 Q-023 Q-024 Q-030 Q-075 Q-087 Q-099 | **A4** — `get_history` 로 정정 체인을 밟는 hop |
| TOOL/TOOL | 2 | Q-044 Q-097 | **A5** — 계산 Tool |
| RETRIEVAL/CHAIN_PARTIAL | 1 | Q-035 | **A4** — 체인 + 후속공시 연결 규칙 |
| GENERATION/REASONING | 1 | Q-093 | **A4** — 다단계 추론 |

### 그리드 밖 트랙 (정답률·겨냥 건수에 합치지 않는다)

| 단계 / 원인 | 건수 | 문항 | track |
|---|---|---|---|
| RETRIEVAL/CHAIN_NOT_TRAVERSED | 1 | Q-061 | regression |
| RETRIEVAL/MULTI_POINT_QUERY | 1 | Q-066 | regression |

## 🔴 arm 배분 — 자동 분류기 vs 수동 원문 대조 (**정본은 수동**)

자동 분류기는 A5 를 구조적으로 못 본다 — `verify_calculation` 이 답변 수치 중 최유리 값을 골라 비교하므로 10× 단위환산 오류가 `mismatch=False` 로 통과하고, TOOL 버킷이 그리드에서 0건이 된다. arm 확정은 원문 대조(`TARGETED_BY_MANUAL_REVIEW`)의 몫이다.

분모가 다르다: 자동 = 그리드만 13건 · 수동 = 전 트랙 6건. arm 별 건수를 나란히 읽을 때 그 자체로 어긋나므로 두 축을 다 낸다.

| arm | 자동(그리드) | 자동(전 트랙) | **수동 정본(전 트랙)** |
|---|---|---|---|
| A4 | 11 | 13 | **1** |
| A5 | 2 | 2 | **2** |
| A6 | 0 | 0 | **2** |
| — | 0 | 0 | **1** |

**불일치 12/15건** — 문항 단위로 적는다. 사람이 기억으로 잇던 자리이고, 그 기억이 두 번 틀렸다.

| 문항 | track | 자동 버킷 | 자동 arm | **수동 정본 arm** |
|---|---|---|---|---|
| Q-005 | grid | `RETRIEVAL/CHAIN_NOT_TRAVERSED` | A4 | **—** |
| Q-006 | grid | `RETRIEVAL/CHAIN_NOT_TRAVERSED` | A4 | **—** |
| Q-018 | grid | `RETRIEVAL/CHAIN_NOT_TRAVERSED` | A4 | **A6** |
| Q-024 | grid | `RETRIEVAL/CHAIN_NOT_TRAVERSED` | A4 | **—** |
| Q-030 | grid | `RETRIEVAL/CHAIN_NOT_TRAVERSED` | A4 | **—** |
| Q-035 | grid | `RETRIEVAL/CHAIN_PARTIAL` | A4 | **—** |
| Q-061 | regression | `RETRIEVAL/CHAIN_NOT_TRAVERSED` | A4 | **—** |
| Q-066 | regression | `RETRIEVAL/MULTI_POINT_QUERY` | A4 | **—** |
| Q-075 | grid | `RETRIEVAL/CHAIN_NOT_TRAVERSED` | A4 | **—** |
| Q-087 | grid | `RETRIEVAL/CHAIN_NOT_TRAVERSED` | A4 | **A6** |
| Q-093 | grid | `GENERATION/REASONING` | A4 | **—** |
| Q-099 | grid | `RETRIEVAL/CHAIN_NOT_TRAVERSED` | A4 | **—** |

critic 이 오답을 통과시킨 문항 (귀속 단계와 무관하게 센다): Q-005 Q-006 Q-018 Q-023 Q-024 Q-035 Q-066 Q-097 Q-099


## 트랙별 (합치지 않는다)

| track | 정답/채점대상 | 정답률 | DATA_LIMIT | 비고 |
|---|---|---|---|---|
| grid | 59/72 | 0.819 | 0 | 3축 결론은 **이 행에서만** 낸다 |
| info_limit | 6/6 | 1.000 | 0 | 거절이 정답. 지어내면 HALLUCINATION |
| regression | 8/10 | 0.800 | 0 | 고친 실패의 감시 — 정답률에 섞지 않는다 |

## 정보한계 문항 (answerable=false)

거절 정답 7/7 · 환각 0


## 신규 6섹터 RETRIEVAL 실패 — 리랭커(Phase E) 재검토 조건

게임 · 로봇 · 소비재·유통 · 자동차·모빌리티 · 철강 · 통신

RETRIEVAL 실패 **2건** (문턱 3) → 미발동 — 보류 유지

| 섹터 | 문항 |
|---|---|
| 로봇 | Q-075 |
| 소비재·유통 | Q-087 |

## 비용 (실측 usage)

| 항목 | 값 |
|---|---|
| questions | 88 |
| llm_calls | 130 |
| prompt_tokens | 267656 |
| completion_tokens | 14735 |
| judge_tokens | 77291 |
| total_tokens | 359682 |
| cost_won | 2525.0 |
| latency_s_mean | 0.2 |
| hops_total | 33 |
| steps_max | 4 |
