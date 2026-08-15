# Phase H — 3축 교차집계 (dev)

생성 2026-08-16 01:09 · 자동 생성물 (`scripts/error_analysis.py`)

셀 표기 = `정답/채점대상` + 실패단계. **채점 대상에서 DATA_LIMIT 은 뺐다.**


> 🔴 **상류 장애 1건 — 이 arm 의 코호트는 균질하지 않다.** 아래 문항은 CLOVA 가 죽은 채로 응답이 굳었다. 답변이 있어도 **그 문항만 다른 처리 경로를 탔으므로** 단일 변수 조작의 Δ 를 이 표와 함께 읽어야 한다.


| 문항 | 단계 | 상태 | 재시도 가능 | 기계 표식 |
|---|---|---|---|---|
| Q-049 | sc | 429 | True | False |

## 18셀

| task_type | mode | 하 | 중 | 상 |
|---|---|---|---|---|
| search_extract | closed | 2/2 | 1/2 TOOL1 | 2/2 |
| search_extract | open | 2/2 | 2/2 | 2/2 |
| multi_compare | closed | 1/2 GENERATION1 | 2/2 | 1/2 GENERATION1 |
| multi_compare | open | 2/2 | 0/1 GENERATION1 | 1/2 GENERATION1 |
| complex_reason | closed | 2/2 | 1/1 | 1/1 |
| complex_reason | open | 1/1 | 1/2 GENERATION1 | 1/1 |

## 축별 주변합


### task_type

| 값 | 정답률 | ROUTING | RETRIEVAL | RANKING | TOOL | GENERATION | VERIFY | DATA_LIMIT |
|---|---|---|---|---|---|---|---|---|
| search_extract | 0.917 (11/12) | 0 | 0 | 0 | 1 | 0 | 0 | 0 |
| multi_compare | 0.636 (7/11) | 0 | 0 | 0 | 0 | 4 | 0 | 0 |
| complex_reason | 0.875 (7/8) | 0 | 0 | 0 | 0 | 1 | 0 | 0 |

### answer_mode

| 값 | 정답률 | ROUTING | RETRIEVAL | RANKING | TOOL | GENERATION | VERIFY | DATA_LIMIT |
|---|---|---|---|---|---|---|---|---|
| closed | 0.812 (13/16) | 0 | 0 | 0 | 1 | 2 | 0 | 0 |
| open | 0.800 (12/15) | 0 | 0 | 0 | 0 | 3 | 0 | 0 |

### difficulty

| 값 | 정답률 | ROUTING | RETRIEVAL | RANKING | TOOL | GENERATION | VERIFY | DATA_LIMIT |
|---|---|---|---|---|---|---|---|---|
| 하 | 0.909 (10/11) | 0 | 0 | 0 | 0 | 1 | 0 | 0 |
| 중 | 0.700 (7/10) | 0 | 0 | 0 | 1 | 2 | 0 | 0 |
| 상 | 0.800 (8/10) | 0 | 0 | 0 | 0 | 2 | 0 | 0 |

## 실패 단계 히스토그램

| 단계 | 건수 | 비고 |
|---|---|---|
| TOOL | 1 |  |
| GENERATION | 5 |  |

## 근본 원인 — 6단계만으로는 arm 이 안 갈린다 (**그리드만**)

| 단계 / 원인 | 건수 | 문항 | 겨냥할 arm |
|---|---|---|---|
| GENERATION/REASONING | 3 | Q-022 Q-023 Q-034 | **A4** — 다단계 추론 |
| GENERATION/INDEX_FIELD_MISSING | 1 | Q-018 | **A1b** — 필드 추출 (⚠ 계산 답이면 오라벨) |
| TOOL/TOOL | 1 | Q-044 | **A5** — 계산 Tool |
| GENERATION/UNVERIFIED | 1 | Q-047 | — 대조할 수치가 없다 |

## 🔴 arm 배분 — 자동 분류기 vs 수동 원문 대조 (**정본은 수동**)

자동 분류기는 A5 를 구조적으로 못 본다 — `verify_calculation` 이 답변 수치 중 최유리 값을 골라 비교하므로 10× 단위환산 오류가 `mismatch=False` 로 통과하고, TOOL 버킷이 그리드에서 0건이 된다. arm 확정은 원문 대조(`TARGETED_BY_MANUAL_REVIEW`)의 몫이다.

분모가 다르다: 자동 = 그리드만 6건 · 수동 = 전 트랙 7건. arm 별 건수를 나란히 읽을 때 그 자체로 어긋나므로 두 축을 다 낸다.

| arm | 자동(그리드) | 자동(전 트랙) | **수동 정본(전 트랙)** |
|---|---|---|---|
| A1b | 1 | 1 | **0** |
| A4 | 3 | 3 | **1** |
| A5 | 1 | 1 | **3** |
| A6 | 0 | 0 | **2** |
| — | 0 | 0 | **1** |
| 미분류 | 1 | 1 | **0** |

**불일치 4/6건** — 문항 단위로 적는다. 사람이 기억으로 잇던 자리이고, 그 기억이 두 번 틀렸다.

| 문항 | track | 자동 버킷 | 자동 arm | **수동 정본 arm** |
|---|---|---|---|---|
| Q-018 | grid | `GENERATION/INDEX_FIELD_MISSING` | A1b | **A6** |
| Q-022 | grid | `GENERATION/REASONING` | A4 | **—** |
| Q-034 | grid | `GENERATION/REASONING` | A4 | **A5** |
| Q-047 | grid | `GENERATION/UNVERIFIED` | 미분류 | **—** |

critic 이 오답을 통과시킨 문항 (귀속 단계와 무관하게 센다): Q-018 Q-023 Q-034


## 트랙별 (합치지 않는다)

| track | 정답/채점대상 | 정답률 | DATA_LIMIT | 비고 |
|---|---|---|---|---|
| grid | 25/31 | 0.806 | 0 | 3축 결론은 **이 행에서만** 낸다 |
| info_limit | 6/6 | 1.000 | 0 | 거절이 정답. 지어내면 HALLUCINATION |

## 정보한계 문항 (answerable=false)

거절 정답 6/6 · 환각 0


## 신규 6섹터 RETRIEVAL 실패 — 리랭커(Phase E) 재검토 조건

게임 · 로봇 · 소비재·유통 · 자동차·모빌리티 · 철강 · 통신

RETRIEVAL 실패 **0건** (문턱 3) → 미발동 — 보류 유지


## 비용 (실측 usage)

| 항목 | 값 |
|---|---|
| questions | 37 |
| llm_calls | 113 |
| prompt_tokens | 230206 |
| completion_tokens | 17406 |
| judge_tokens | 26993 |
| total_tokens | 274605 |
| cost_won | 1927.7 |
| latency_s_mean | 12.5 |
| hops_total | 6 |
| steps_max | 5 |
