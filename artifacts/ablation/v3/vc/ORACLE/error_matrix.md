# Phase H — 3축 교차집계 (dev)

생성 2026-08-14 20:54 · 자동 생성물 (`scripts/error_analysis.py`)

셀 표기 = `정답/채점대상` + 실패단계. **채점 대상에서 DATA_LIMIT 은 뺐다.**


> 🔴 **`ORACLE` 는 진단 전용이다 — 이 문서의 정답률을 성적으로 인용하면 자기 성적표를 부풀리는 것이 된다.** 평가 시점에 없는 골든 근거를 주입한 값이라 제출 구성이 될 수 없고, **상한도 아니다**(주입은 근거를 더하는 것이 아니라 골든만으로 교체하므로 근거가 줄고, 실측에서 `A8` 보다 낮다). 아래 `GENERATION` 칸이 큰 것도 생성이 더 나빠진 것이 아니라 **다른 단계의 실패가 이동한 것**이다. 읽는 법은 `src/argus/ablation.py` 머리말 §ORACLE 이 정본이다.


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
| TOOL | 1 |  |
| GENERATION | 4 |  |

## 근본 원인 — 6단계만으로는 arm 이 안 갈린다 (**그리드만**)

| 단계 / 원인 | 건수 | 문항 | 겨냥할 arm |
|---|---|---|---|
| — | 0 | | |

### 그리드 밖 트랙 (정답률·겨냥 건수에 합치지 않는다)

| 단계 / 원인 | 건수 | 문항 | track |
|---|---|---|---|
| GENERATION/INDEX_FIELD_MISSING | 1 | Q-107 | valuechain |
| TOOL/TOOL | 1 | Q-109 | valuechain |
| GENERATION/REASONING | 1 | Q-110 | valuechain |
| GENERATION/INDEX_FIELD_MISSING | 1 | Q-113 | valuechain |
| GENERATION/INDEX_FIELD_MISSING | 1 | Q-115 | valuechain |

## 🔴 arm 배분 — 자동 분류기 vs 수동 원문 대조 (**정본은 수동**)

자동 분류기는 A5 를 구조적으로 못 본다 — `verify_calculation` 이 답변 수치 중 최유리 값을 골라 비교하므로 10× 단위환산 오류가 `mismatch=False` 로 통과하고, TOOL 버킷이 그리드에서 0건이 된다. arm 확정은 원문 대조(`TARGETED_BY_MANUAL_REVIEW`)의 몫이다.

분모가 다르다: 자동 = 그리드만 0건 · 수동 = 전 트랙 6건. arm 별 건수를 나란히 읽을 때 그 자체로 어긋나므로 두 축을 다 낸다.

| arm | 자동(그리드) | 자동(전 트랙) | **수동 정본(전 트랙)** |
|---|---|---|---|
| A1b | 0 | 3 | **0** |
| A4 | 0 | 1 | **1** |
| A5 | 0 | 1 | **2** |
| A6 | 0 | 0 | **2** |
| — | 0 | 0 | **1** |

**불일치 5/5건** — 문항 단위로 적는다. 사람이 기억으로 잇던 자리이고, 그 기억이 두 번 틀렸다.

| 문항 | track | 자동 버킷 | 자동 arm | **수동 정본 arm** |
|---|---|---|---|---|
| Q-107 | valuechain | `GENERATION/INDEX_FIELD_MISSING` | A1b | **—** |
| Q-109 | valuechain | `TOOL/TOOL` | A5 | **—** |
| Q-110 | valuechain | `GENERATION/REASONING` | A4 | **—** |
| Q-113 | valuechain | `GENERATION/INDEX_FIELD_MISSING` | A1b | **—** |
| Q-115 | valuechain | `GENERATION/INDEX_FIELD_MISSING` | A1b | **—** |

critic 이 오답을 통과시킨 문항 (귀속 단계와 무관하게 센다): Q-107 Q-109 Q-110 Q-115


## 트랙별 (합치지 않는다)

| track | 정답/채점대상 | 정답률 | DATA_LIMIT | 비고 |
|---|---|---|---|---|
| valuechain | 7/12 | 0.583 | 0 | 현재 시스템이 원리상 못 푸는 문항 포함 (Q-117) |

## 정보한계 문항 (answerable=false)

거절 정답 0/0 · 환각 0


## 신규 6섹터 RETRIEVAL 실패 — 리랭커(Phase E) 재검토 조건

게임 · 로봇 · 소비재·유통 · 자동차·모빌리티 · 철강 · 통신

RETRIEVAL 실패 **0건** (문턱 3) → 미발동 — 보류 유지


## 비용 (실측 usage)

| 항목 | 값 |
|---|---|
| questions | 12 |
| llm_calls | 23 |
| prompt_tokens | 22699 |
| completion_tokens | 3108 |
| judge_tokens | 6591 |
| total_tokens | 32398 |
| cost_won | 227.4 |
| latency_s_mean | 0.38 |
| hops_total | 0 |
| steps_max | 0 |
