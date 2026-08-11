# Phase H — 3축 교차집계 (dev)

생성 2026-08-11 12:12 · 자동 생성물 (`scripts/error_analysis.py`)

셀 표기 = `정답/채점대상` + 실패단계. **채점 대상에서 DATA_LIMIT 은 뺐다.**


## 18셀

| task_type | mode | 하 | 중 | 상 |
|---|---|---|---|---|
| search_extract | closed | 4/4 | 3/4 GENERATION1 | 0/4 RETRIEVAL4 |
| search_extract | open | 4/4 | 4/4 | 4/4 |
| multi_compare | closed | 3/4 GENERATION1 | 4/4 | 2/4 RETRIEVAL2 |
| multi_compare | open | 4/4 | 4/4 | 1/4 RANKING1 RETRIEVAL2 |
| complex_reason | closed | 4/4 | 2/4 GENERATION1 TOOL1 | 3/4 RETRIEVAL1 |
| complex_reason | open | 3/4 GENERATION1 | 4/4 | 2/4 RETRIEVAL2 |

## 축별 주변합


### task_type

| 값 | 정답률 | ROUTING | RETRIEVAL | RANKING | TOOL | GENERATION | VERIFY | DATA_LIMIT |
|---|---|---|---|---|---|---|---|---|
| search_extract | 0.792 (19/24) | 0 | 4 | 0 | 0 | 1 | 0 | 0 |
| multi_compare | 0.750 (18/24) | 0 | 4 | 1 | 0 | 1 | 0 | 0 |
| complex_reason | 0.750 (18/24) | 0 | 3 | 0 | 1 | 2 | 0 | 0 |

### answer_mode

| 값 | 정답률 | ROUTING | RETRIEVAL | RANKING | TOOL | GENERATION | VERIFY | DATA_LIMIT |
|---|---|---|---|---|---|---|---|---|
| closed | 0.694 (25/36) | 0 | 7 | 0 | 1 | 3 | 0 | 0 |
| open | 0.833 (30/36) | 0 | 4 | 1 | 0 | 1 | 0 | 0 |

### difficulty

| 값 | 정답률 | ROUTING | RETRIEVAL | RANKING | TOOL | GENERATION | VERIFY | DATA_LIMIT |
|---|---|---|---|---|---|---|---|---|
| 하 | 0.917 (22/24) | 0 | 0 | 0 | 0 | 2 | 0 | 0 |
| 중 | 0.875 (21/24) | 0 | 0 | 0 | 1 | 2 | 0 | 0 |
| 상 | 0.500 (12/24) | 0 | 11 | 1 | 0 | 0 | 0 | 0 |

## 실패 단계 히스토그램

| 단계 | 건수 | 비고 |
|---|---|---|
| RETRIEVAL | 13 |  |
| RANKING | 1 |  |
| TOOL | 2 |  |
| GENERATION | 4 |  |

## 근본 원인 — 6단계만으로는 arm 이 안 갈린다 (**그리드만**)

| 단계 / 원인 | 건수 | 문항 | 겨냥할 arm |
|---|---|---|---|
| RETRIEVAL/CHAIN_NOT_TRAVERSED | 8 | Q-005 Q-006 Q-018 Q-023 Q-024 Q-075 Q-087 Q-099 | **A4** — `get_history` 로 정정 체인을 밟는 hop |
| GENERATION/INDEX_FIELD_MISSING | 3 | Q-044 Q-098 Q-101 | **A1b** — 필드 추출 (⚠ 계산 답이면 오라벨) |
| RETRIEVAL/MULTI_POINT_QUERY | 2 | Q-076 Q-105 | **A4** — 시점·기업별 질의 분해 |
| RETRIEVAL/CHAIN_PARTIAL | 1 | Q-035 | **A4** — 체인 + 후속공시 연결 규칙 |
| GENERATION/UNVERIFIED | 1 | Q-047 | — 대조할 수치가 없다 |
| RANKING/LOW_RANK | 1 | Q-094 | **A3** — 리랭커 |
| TOOL/TOOL | 1 | Q-097 | **A5** — 계산 Tool |

### 그리드 밖 트랙 (정답률·겨냥 건수에 합치지 않는다)

| 단계 / 원인 | 건수 | 문항 | track |
|---|---|---|---|
| RETRIEVAL/CHAIN_NOT_TRAVERSED | 1 | Q-061 | regression |
| TOOL/TOOL | 1 | Q-065 | regression |
| RETRIEVAL/MULTI_POINT_QUERY | 1 | Q-066 | regression |

## 🔴 arm 배분 — 자동 분류기 vs 수동 원문 대조 (**정본은 수동**)

자동 분류기는 A5 를 구조적으로 못 본다 — `verify_calculation` 이 답변 수치 중 최유리 값을 골라 비교하므로 10× 단위환산 오류가 `mismatch=False` 로 통과하고, TOOL 버킷이 그리드에서 0건이 된다. arm 확정은 원문 대조(`TARGETED_BY_MANUAL_REVIEW`)의 몫이다.

분모가 다르다: 자동 = 그리드만 17건 · 수동 = 전 트랙 13건. arm 별 건수를 나란히 읽을 때 그 자체로 어긋나므로 두 축을 다 낸다.

| arm | 자동(그리드) | 자동(전 트랙) | **수동 정본(전 트랙)** |
|---|---|---|---|
| A1b | 3 | 3 | **2** |
| A3 | 1 | 1 | **1** |
| A4 | 11 | 13 | **5** |
| A5 | 1 | 2 | **4** |
| A6 | 0 | 0 | **1** |
| 미분류 | 1 | 1 | **0** |

**불일치 12/20건** — 문항 단위로 적는다. 사람이 기억으로 잇던 자리이고, 그 기억이 두 번 틀렸다.

| 문항 | track | 자동 버킷 | 자동 arm | **수동 정본 arm** |
|---|---|---|---|---|
| Q-005 | grid | `RETRIEVAL/CHAIN_NOT_TRAVERSED` | A4 | **—** |
| Q-006 | grid | `RETRIEVAL/CHAIN_NOT_TRAVERSED` | A4 | **—** |
| Q-018 | grid | `RETRIEVAL/CHAIN_NOT_TRAVERSED` | A4 | **—** |
| Q-024 | grid | `RETRIEVAL/CHAIN_NOT_TRAVERSED` | A4 | **—** |
| Q-035 | grid | `RETRIEVAL/CHAIN_PARTIAL` | A4 | **—** |
| Q-044 | grid | `GENERATION/INDEX_FIELD_MISSING` | A1b | **A5** |
| Q-047 | grid | `GENERATION/UNVERIFIED` | 미분류 | **A5** |
| Q-061 | regression | `RETRIEVAL/CHAIN_NOT_TRAVERSED` | A4 | **—** |
| Q-075 | grid | `RETRIEVAL/CHAIN_NOT_TRAVERSED` | A4 | **—** |
| Q-087 | grid | `RETRIEVAL/CHAIN_NOT_TRAVERSED` | A4 | **A6** |
| Q-098 | grid | `GENERATION/INDEX_FIELD_MISSING` | A1b | **A4** |
| Q-099 | grid | `RETRIEVAL/CHAIN_NOT_TRAVERSED` | A4 | **A1b** |

critic 이 오답을 통과시킨 문항 (귀속 단계와 무관하게 센다): Q-005 Q-006 Q-023 Q-024 Q-035 Q-061 Q-065 Q-066 Q-075 Q-087 Q-097 Q-099 Q-101


## 트랙별 (합치지 않는다)

| track | 정답/채점대상 | 정답률 | DATA_LIMIT | 비고 |
|---|---|---|---|---|
| grid | 55/72 | 0.764 | 0 | 3축 결론은 **이 행에서만** 낸다 |
| info_limit | 6/6 | 1.000 | 0 | 거절이 정답. 지어내면 HALLUCINATION |
| regression | 7/10 | 0.700 | 0 | 고친 실패의 감시 — 정답률에 섞지 않는다 |

## 정보한계 문항 (answerable=false)

거절 정답 7/7 · 환각 0


## 신규 6섹터 RETRIEVAL 실패 — 리랭커(Phase E) 재검토 조건

게임 · 로봇 · 소비재·유통 · 자동차·모빌리티 · 철강 · 통신

RETRIEVAL 실패 **4건** (문턱 3) → 🔴 **발동** — Phase E 보류 근거를 다시 본다

| 섹터 | 문항 |
|---|---|
| 로봇 | Q-075 |
| 소비재·유통 | Q-087 |
| 자동차·모빌리티 | Q-076 |
| 통신 | Q-105 |

## 비용 (실측 usage)

| 항목 | 값 |
|---|---|
| questions | 88 |
| llm_calls | 128 |
| prompt_tokens | 240838 |
| completion_tokens | 15298 |
| judge_tokens | 72599 |
| total_tokens | 328735 |
| cost_won | 2307.7 |
| latency_s_mean | 0.99 |
| hops_total | 34 |
| steps_max | 4 |
