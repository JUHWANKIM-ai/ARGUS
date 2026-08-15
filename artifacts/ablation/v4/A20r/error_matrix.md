# Phase H — 3축 교차집계 (dev)

생성 2026-08-15 22:38 · 자동 생성물 (`scripts/error_analysis.py`)

셀 표기 = `정답/채점대상` + 실패단계. **채점 대상에서 DATA_LIMIT 은 뺐다.**


## 18셀

| task_type | mode | 하 | 중 | 상 |
|---|---|---|---|---|
| search_extract | closed | 4/4 | 4/4 | 4/4 |
| search_extract | open | 4/4 | 4/4 | 4/4 |
| multi_compare | closed | 4/4 | 4/4 | 2/4 TOOL1 VERIFY1 |
| multi_compare | open | 4/4 | 4/4 | 2/4 GENERATION2 |
| complex_reason | closed | 4/4 | 4/4 | 4/4 |
| complex_reason | open | 4/4 | 3/4 GENERATION1 | 4/4 |

## 축별 주변합


### task_type

| 값 | 정답률 | ROUTING | RETRIEVAL | RANKING | TOOL | GENERATION | VERIFY | DATA_LIMIT |
|---|---|---|---|---|---|---|---|---|
| search_extract | 1.000 (24/24) | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| multi_compare | 0.833 (20/24) | 0 | 0 | 0 | 1 | 2 | 1 | 0 |
| complex_reason | 0.958 (23/24) | 0 | 0 | 0 | 0 | 1 | 0 | 0 |

### answer_mode

| 값 | 정답률 | ROUTING | RETRIEVAL | RANKING | TOOL | GENERATION | VERIFY | DATA_LIMIT |
|---|---|---|---|---|---|---|---|---|
| closed | 0.944 (34/36) | 0 | 0 | 0 | 1 | 0 | 1 | 0 |
| open | 0.917 (33/36) | 0 | 0 | 0 | 0 | 3 | 0 | 0 |

### difficulty

| 값 | 정답률 | ROUTING | RETRIEVAL | RANKING | TOOL | GENERATION | VERIFY | DATA_LIMIT |
|---|---|---|---|---|---|---|---|---|
| 하 | 1.000 (24/24) | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| 중 | 0.958 (23/24) | 0 | 0 | 0 | 0 | 1 | 0 | 0 |
| 상 | 0.833 (20/24) | 0 | 0 | 0 | 1 | 2 | 1 | 0 |

## 실패 단계 히스토그램

| 단계 | 건수 | 비고 |
|---|---|---|
| TOOL | 1 |  |
| GENERATION | 3 |  |
| VERIFY | 1 |  |

## 근본 원인 — 6단계만으로는 arm 이 안 갈린다 (**그리드만**)

| 단계 / 원인 | 건수 | 문항 | 겨냥할 arm |
|---|---|---|---|
| GENERATION/REASONING | 3 | Q-023 Q-034 Q-093 | **A4** — 다단계 추론 |
| TOOL/TOOL | 1 | Q-018 | **A5** — 계산 Tool |
| VERIFY/VERIFY | 1 | Q-087 | 미분류 — 원문 대조 필요 (VERIFY 는 A6 을 의미하지 않는다) |

## 🔴 arm 배분 — 자동 분류기 vs 수동 원문 대조 (**정본은 수동**)

자동 분류기는 A5 를 구조적으로 못 본다 — `verify_calculation` 이 답변 수치 중 최유리 값을 골라 비교하므로 10× 단위환산 오류가 `mismatch=False` 로 통과하고, TOOL 버킷이 그리드에서 0건이 된다. arm 확정은 원문 대조(`TARGETED_BY_MANUAL_REVIEW`)의 몫이다.

분모가 다르다: 자동 = 그리드만 5건 · 수동 = 전 트랙 7건. arm 별 건수를 나란히 읽을 때 그 자체로 어긋나므로 두 축을 다 낸다.

| arm | 자동(그리드) | 자동(전 트랙) | **수동 정본(전 트랙)** |
|---|---|---|---|
| A4 | 3 | 3 | **1** |
| A5 | 1 | 1 | **3** |
| A6 | 0 | 0 | **2** |
| — | 0 | 0 | **1** |
| 미분류 | 1 | 1 | **0** |

**불일치 4/5건** — 문항 단위로 적는다. 사람이 기억으로 잇던 자리이고, 그 기억이 두 번 틀렸다.

| 문항 | track | 자동 버킷 | 자동 arm | **수동 정본 arm** |
|---|---|---|---|---|
| Q-018 | grid | `TOOL/TOOL` | A5 | **A6** |
| Q-034 | grid | `GENERATION/REASONING` | A4 | **A5** |
| Q-087 | grid | `VERIFY/VERIFY` | 미분류 | **A6** |
| Q-093 | grid | `GENERATION/REASONING` | A4 | **—** |

critic 이 오답을 통과시킨 문항 (귀속 단계와 무관하게 센다): Q-018 Q-023 Q-034 Q-087


## 트랙별 (합치지 않는다)

| track | 정답/채점대상 | 정답률 | DATA_LIMIT | 비고 |
|---|---|---|---|---|
| grid | 67/72 | 0.931 | 0 | 3축 결론은 **이 행에서만** 낸다 |
| info_limit | 6/6 | 1.000 | 0 | 거절이 정답. 지어내면 HALLUCINATION |
| regression | 10/10 | 1.000 | 0 | 고친 실패의 감시 — 정답률에 섞지 않는다 |

## 정보한계 문항 (answerable=false)

거절 정답 7/7 · 환각 0


## 신규 6섹터 RETRIEVAL 실패 — 리랭커(Phase E) 재검토 조건

게임 · 로봇 · 소비재·유통 · 자동차·모빌리티 · 철강 · 통신

RETRIEVAL 실패 **0건** (문턱 3) → 미발동 — 보류 유지


## 비용 (실측 usage)

| 항목 | 값 |
|---|---|
| questions | 88 |
| llm_calls | 134 |
| prompt_tokens | 290601 |
| completion_tokens | 15929 |
| judge_tokens | 79447 |
| total_tokens | 385977 |
| cost_won | 2709.6 |
| latency_s_mean | 0.27 |
| hops_total | 32 |
| steps_max | 5 |
