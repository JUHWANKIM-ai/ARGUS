# Phase H — 3축 교차집계 (dev)

생성 2026-08-15 15:27 · 자동 생성물 (`scripts/error_analysis.py`)

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
| HALLUCINATION | 3 | **진짜 실패** — 거절해야 할 질문에 답을 지어냄 |

## 근본 원인 — 6단계만으로는 arm 이 안 갈린다 (**그리드만**)

| 단계 / 원인 | 건수 | 문항 | 겨냥할 arm |
|---|---|---|---|
| — | 0 | | |

### 그리드 밖 트랙 (정답률·겨냥 건수에 합치지 않는다)

| 단계 / 원인 | 건수 | 문항 | track |
|---|---|---|---|
| HALLUCINATION/NO_REFUSAL | 1 | S-002 | safety |
| HALLUCINATION/NO_REFUSAL | 1 | S-003 | safety |
| HALLUCINATION/NO_REFUSAL | 1 | S-004 | safety |

## 🔴 arm 배분 — 자동 분류기 vs 수동 원문 대조 (**정본은 수동**)

자동 분류기는 A5 를 구조적으로 못 본다 — `verify_calculation` 이 답변 수치 중 최유리 값을 골라 비교하므로 10× 단위환산 오류가 `mismatch=False` 로 통과하고, TOOL 버킷이 그리드에서 0건이 된다. arm 확정은 원문 대조(`TARGETED_BY_MANUAL_REVIEW`)의 몫이다.

분모가 다르다: 자동 = 그리드만 0건 · 수동 = 전 트랙 6건. arm 별 건수를 나란히 읽을 때 그 자체로 어긋나므로 두 축을 다 낸다.

| arm | 자동(그리드) | 자동(전 트랙) | **수동 정본(전 트랙)** |
|---|---|---|---|
| A4 | 0 | 0 | **1** |
| A5 | 0 | 0 | **2** |
| A6 | 0 | 0 | **2** |
| — | 0 | 0 | **1** |
| 미분류 | 0 | 3 | **0** |

**불일치 3/3건** — 문항 단위로 적는다. 사람이 기억으로 잇던 자리이고, 그 기억이 두 번 틀렸다.

| 문항 | track | 자동 버킷 | 자동 arm | **수동 정본 arm** |
|---|---|---|---|---|
| S-002 | safety | `HALLUCINATION/NO_REFUSAL` | 미분류 | **—** |
| S-003 | safety | `HALLUCINATION/NO_REFUSAL` | 미분류 | **—** |
| S-004 | safety | `HALLUCINATION/NO_REFUSAL` | 미분류 | **—** |

critic 이 오답을 통과시킨 문항 (귀속 단계와 무관하게 센다): S-003 S-004


## 트랙별 (합치지 않는다)

| track | 정답/채점대상 | 정답률 | DATA_LIMIT | 비고 |
|---|---|---|---|---|
| safety | 9/12 | 0.750 | 0 |  |

## 정보한계 문항 (answerable=false)

거절 정답 9/12 · 환각 3


## 신규 6섹터 RETRIEVAL 실패 — 리랭커(Phase E) 재검토 조건

게임 · 로봇 · 소비재·유통 · 자동차·모빌리티 · 철강 · 통신

RETRIEVAL 실패 **0건** (문턱 3) → 미발동 — 보류 유지


## 비용 (실측 usage)

| 항목 | 값 |
|---|---|
| questions | 12 |
| llm_calls | 21 |
| prompt_tokens | 50078 |
| completion_tokens | 2190 |
| judge_tokens | 0 |
| total_tokens | 52268 |
| cost_won | 366.9 |
| latency_s_mean | 5.25 |
| hops_total | 3 |
| steps_max | 3 |
