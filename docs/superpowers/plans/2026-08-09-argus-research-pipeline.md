# ARGUS 연구 파이프라인 — EDA부터 Ablation까지

**Goal:** [policy.md](../../policy.md)의 추천 개발 순서를 [CONVENTION.md](../../CONVENTION.md)의 제약 아래에서 실행 가능한 9단계 파이프라인으로 구체화한다.

**기준일:** 2026-08-09 · **제출 마감 09-06 (D-28)** · **평가 09-07~09-20**

**두 문서의 관계**
- `policy.md` — *무엇을 해야 하는가* (방법론)
- `CONVENTION.md` — *무엇을 할 수 없는가* (규정·예산)
- 이 문서 — *그래서 어떤 순서로 무엇을 측정하는가*

---

## 0. 현재 위치 — policy §12 대비

| policy 순서 | 상태 | 실제 산출물 |
|---|---|---|
| 1. Golden Test Set | 🟡 **6문항, 필드 미달** | `evalset/questions.jsonl` — `추론과정`·`필요 Tool`·`허용 답변 형태` 없음 |
| 2. 공시 데이터 분석 | 🔴 **미실시** | 부분 사실만 산발적으로 확인 |
| 3. Graph Schema | 🔴 미착수 | — |
| 4. Retriever | 🟢 BM25 + 메타필터 | `exchange` 1,469건, 골든 1위 |
| 5. Re-ranker | 🔴 미착수 | — |
| 6. Agent + Tool | 🟢 도구 7종 + 파이프라인 | 110 tests |
| 7. Verifier | 🟢 critic | `agent/critic.py` |
| 8. Error Analysis | 🔴 미착수 | 단계별 계측 없음 |
| 9. Ablation | 🔴 미착수 | — |

> **순서가 뒤집혀 있다.** policy는 1·2를 먼저 하라는데 우리는 4·6·7을 먼저 만들었다.
> 그 결과가 지금까지의 시행착오다 — `title`이 검색어가 안 되는 문제, `is_latest` 뒤집힘,
> 한글 복합명사, 연월이 랭킹을 좌우한다는 사실을 **전부 사후에** 발견했다.
> **EDA를 먼저 했으면 대부분 예방됐다.** 이 파이프라인은 그 빚을 갚는 것부터 시작한다.

---

## 1. 연구 대상 선정 — policy §0 × CONVENTION G1

policy는 4개 모듈 중 **1~2개만 연구 대상**으로 고르고 나머지는 검증된 오픈소스를 쓰라고 한다.
그런데 우리 제약에서는 **선택의 여지가 없는 칸이 이미 정해져 있다.**

| 모듈 | policy 권장 | CONVENTION 제약 | 결론 |
|---|---|---|---|
| Retrieval | BGE / E5 | 보조 모델 허용 (Q&A 12) | **연구 대상 ①** |
| Re-ranker | BGE | 보조 모델 허용 | 오픈소스 사용 |
| Inference | GPT / Qwen | 🔴 **HCX-005 고정** — 선택 불가 | **고정** |
| Agent | — | 우리가 설계 | **연구 대상 ②** |

**연구 대상 = Retrieval/GraphRAG + Agent(Planning·Tool).**
Inference는 바꿀 수 없으므로 애초에 연구 대상이 될 수 없다. policy §11의
"모델 자체를 새로 학습시키는 연구가 아니라면 Retrieval·Planning·Tool·Evaluation에 집중"과 정확히 일치한다.

> ⚠️ **G1 미해결 리스크.** 보조 모델(임베딩·리랭커) 허용 범위는 과제소개자료 p.4와
> 설명회 Q&A 12가 충돌한다. **디스코드 확정 전에는 임베딩·리랭커에 큰 공수를 넣지 않는다.**
> Phase D·E를 "붙였다 뗄 수 있는" 형태로 설계하는 이유다(§Ablation).

---

## Phase A. 데이터 EDA — policy §1

**LLM 호출 0회. 크레딧 확보 전에 지금 당장 할 수 있고, 가장 먼저 해야 한다.**

### A-1. 문서 구조 프로파일

| 측정 | 방법 | 왜 |
|---|---|---|
| 문서 길이 분포 | 유형×서식별 텍스트 길이 히스토그램 | chunk 크기 결정 |
| **표 비율** | `<table>` 수 / 텍스트 대비 표 문자 비중 | 표 분리 여부 (policy §8) |
| **숫자 비율** | 숫자 토큰 / 전체 토큰 | BM25 vs 임베딩 적합성 판단 |
| 시간 정보 | 날짜 패턴 출현 위치·밀도 | 기간 필터 설계 근거 |
| 섹션 목차 깊이 | `periodic` 목차 트리 depth·노드 수 | 섹션 청킹 설계 |

**산출물:** `artifacts/eda/doc_profile.json` + `notebooks/eda_documents.ipynb`

**이미 아는 것 (재확인만)**: periodic 680MB(95%) · exchange 5MB · UTF-8인데 meta는 euc-kr ·
계약 서식은 exchange의 77%

### A-2. 관계 구조 프로파일 — policy §1의 "문서 간 참조 / 기업 간 관계 / 연속 공시"

| 측정 | 방법 |
|---|---|
| **정정 체인** | 길이 분포, 최장 체인, 코퍼스 밖 참조 비율 |
| 문서 간 참조 | `※ 관련공시` 행, 본문 내 접수번호 언급 |
| **기업 간 관계** | 계약상대(`counterparty`)가 universe 70개사에 있는 비율 → 기업-기업 엣지 후보 |
| 연속 공시 구조 | 기업×분기별 공시 밀도, 정기공시 제출 주기 |
| 섹터 응집도 | 같은 섹터 기업이 계약상대로 등장하는 빈도 |

**산출물:** `artifacts/eda/relation_profile.json`

**이 단계의 판정이 Phase C를 결정한다.** 기업-기업 엣지가 희소하면 GraphRAG의 이점이 작고,
policy §2의 전제("공시는 관계형 데이터")가 우리 코퍼스에서 실제로 성립하는지를 여기서 검증한다.

### A-3. 질의 가능성 매핑

평가 질의 6종(CONVENTION §6)이 **어느 유형·어느 필드에서 답이 나오는지** 역매핑한다.
답이 나올 수 없는 유형이 있으면 그게 곧 인덱싱 우선순위다.

**게이트 A**
- [ ] 표·숫자 비율이 유형별로 정량화됐다
- [ ] 기업-기업 엣지 밀도가 나왔다 → **GraphRAG 채택/기각 근거**
- [ ] 정기공시 목차 트리가 파싱된다 (Phase C·D의 선행조건)

---

## Phase B. Golden Test Set — policy §3 · §7

**현재 6문항은 policy 기준 미달이다.** 필드 4개가 없다.

### B-1. 스키마 확장

```yaml
question_id: Q-012
question: "HD현대일렉트릭이 2023년 1월에 체결한 변압기 공급계약의 계약금액은?"
type: closed_lookup          # 6종 (CONVENTION §6)
difficulty: low              # low | high
answer: "97,000,000,000"
answer_form: number_won      # number_won | number_pct | duration | list | narrative | refusal
evidence:                    # 근거 문서 (복수 가능)
  - rcept_no: "20230131800162"
    section: null            # periodic 이면 목차 경로
reasoning_steps:             # 필요한 추론 과정
  - "기업 해소: HD현대일렉트릭"
  - "기간 필터: 202301"
  - "계약금액 필드 조회"
required_tools: [resolve_company, search_chunks]
split: dev                   # dev | heldout
```

### B-2. 규모와 분포

| 축 | 목표 |
|---|---|
| 총 문항 | **60문항** (유형 6종 × 난이도 2 × 5문항) |
| 유형 | closed_lookup · closed_calc · closed_exists · multi_compare · open_summary · unanswerable |
| 공시 유형 | exchange 24 · periodic 24 · major 6 · holding 6 |
| 답변 불가 | 6문항 (범위 밖 기업 · 기간 밖 · 공시에 없는 정보) |

### B-3. Train/Held-out 분리 — policy §7

> Validation을 Train과 같은 분포로 만들지 않는다.

**섹터 단위로 홀드아웃한다.** 기업 단위로 나누면 같은 섹터의 서식·용어를 이미 본 셈이라
일반화 측정이 되지 않는다.

```
dev     : 16개 섹터 (약 56개사)   ← 프롬프트·규칙 튜닝은 여기서만
heldout :  4개 섹터 (약 14개사)   ← 최종 측정에만 1회 사용
```

홀드아웃 섹터는 **서식 다양성이 큰 쪽**으로 고른다 — 바이오·제약(임상), 금융·보험(NIM/CSM),
조선·건설·방산(수주), 엔터테인먼트.

> **홀드아웃을 보고 튜닝하면 그 순간 홀드아웃이 아니다.** 최종 측정 전까지 열지 않는다.

**게이트 B**
- [ ] 60문항, 전 필드 채움
- [ ] 근거 `rcept_no`가 코퍼스에 실재함을 자동 검증
- [ ] dev/heldout 섹터 분리, heldout 미개봉

**크레딧:** 0 (수작업 + 규칙 검증)

---

## Phase C. Graph Schema — policy §2

**Phase A-2의 판정에 따라 범위가 달라진다.**

### C-1. 우리 데이터에서 실제로 뽑히는 것만 정의한다

policy가 든 예시(임원·자회사·지분)는 이상적이지만, **우리 코퍼스에 실제로 있는지**부터 본다.

| Node | 근거 | 추출 가능성 |
|---|---|---|
| 기업 | `universe.csv` 70개사 | ✅ 확정 |
| 공시 | `manifest.jsonl` 4,204건 | ✅ 확정 |
| 계약 | exchange 정규화 레코드 | ✅ 1,126건 |
| 투자 | 신규시설투자 43건 | ✅ 소량 |
| 지분 | holding 1,083건 | 🟡 규칙 파싱 필요 |
| 재무항목 | periodic 섹션 | 🔴 섹션 인덱스 선행 |
| 임원 | periodic 섹션 | 🔴 추출 비용 큼 |

| Edge | 근거 |
|---|---|
| 기업 —계약했다→ 계약 —상대→ 기업/외부 | `counterparty` |
| 기업 —공시했다→ 공시 | `corp_code` |
| 공시 —정정한다→ 공시 | 정정 체인 |
| 기업 —속한다→ 섹터/업종 | universe |

### C-2. n-ary 표현 결정

계약은 (기업, 상대, 금액, 기간, 비율)이 한 덩어리다. 이진 트리플로 쪼개면 조건이 분리된다.
**하이퍼엣지 또는 이벤트 노드**로 표현한다 — 이미 정규화 레코드가 그 형태다.

> 🔴 **LLM 기반 n-ary 정밀 추출은 이 파이프라인에서 하지 않는다.**
> 규칙 기반 레코드(1,469건)로 그래프를 구성하고, LLM 추출은 크레딧·단가 확정 후 별도 계획.

**게이트 C**
- [ ] Node/Edge 스키마 확정, 각 항목의 추출 근거가 명시됨
- [ ] 규칙만으로 만든 그래프의 노드·엣지 수 집계
- [ ] **기업-기업 엣지가 유의미한 밀도인가** → 아니면 GraphRAG 대신 하이브리드 검색에 집중

---

## Phase D. Retriever — policy §8

### D-1. 현재 상태
BM25 + 메타 프리필터(`corp_name` 완전일치 · `rcept_dt` prefix). exchange 1,469건.

### D-2. 실험 축

| 축 | 후보 | 비고 |
|---|---|---|
| Chunk 크기 | 레코드 단위 / 섹션 단위 / 고정 길이 | periodic은 섹션 단위 필수 |
| Parent-Child | 섹션(child) → 문서(parent) | `read_section` 이 이미 이 구조 |
| 표 분리 | 표를 별도 청크로 / 본문에 인라인 | A-1의 표 비율이 결정 |
| 토크나이저 | 어절 / 어절+bigram | bigram은 리콜↑ 랭킹↓ (실측) |
| 임베딩 | 없음 / 한국어 임베딩 | **G1 확정 후** |
| 하이브리드 | BM25 단독 / BM25+Vector RRF | 동일 |

### D-3. 지표

```
Recall@1, Recall@5, Recall@20     골든 근거가 후보에 들어왔는가
MRR                                순위 품질
후보 축소율                        프리필터 전후 후보 수
```

**게이트 D**
- [ ] dev 60문항에서 **Recall@5 ≥ 0.85**
- [ ] periodic 질의도 근거를 만들 수 있다 (섹션 인덱스 완성)

**크레딧:** 0 (BM25·임베딩 모두 로컬)

---

## Phase E. Re-ranker

**G1이 확정되기 전에는 착수하지 않는다.** 확정되면 오픈소스 리랭커를 얹어 D 대비 증분만 측정한다.
확정이 안 나면 **Ablation에서 이 칸을 비운 채로 제출**한다 — 규정 리스크를 지지 않는다.

**지표:** Recall@5는 유지하고 **MRR·Precision@1 개선폭**만 본다.

---

## Phase F. Agent + Tool — policy §4

### F-1. 현재 상태
도구 7종 + 라우터 + 예산 가드 + 결정적 계산. 파이프라인 조립 완료.

### F-2. 남은 것

| 항목 | 상태 |
|---|---|
| `list_sections` / `read_section` | 🔴 `periodic_sections.jsonl` 부재로 항상 error |
| 계산 Tool 노출 | 🟡 `compute.py`는 있으나 **도구 스펙에 없음** — LLM이 부를 수 없다 |
| CAGR · 순위 · 평균 | 🔴 policy §4가 요구하는데 미구현 |
| ReAct 반복 루프 | 🔴 현재는 단발 검색 |

> **policy §4의 핵심이 아직 안 지켜졌다.** `compute.py`를 만들어놓고 `TOOL_SPECS`에 안 넣어서,
> LLM은 여전히 계산을 스스로 한다. 도구로 노출해야 "LLM=Reasoning, Tool=Calculation"이 성립한다.

**게이트 F**
- [ ] 계산 함수가 `TOOL_SPECS`에 노출되고 function calling으로 호출된다
- [ ] periodic 섹션 하강이 동작한다

---

## Phase G. Verifier — policy §6

현재 `critic.py`는 **근거 존재·인용·환각** 3항목을 본다. policy는 **계산 오류**를 추가로 요구한다.

계산 오류는 LLM에게 물을 게 아니라 **결정적 재계산으로 대조**한다 — 답변에서 수치를 파싱해
`compute.py`로 다시 계산하고 불일치를 잡는다. 무료다.

**게이트 G**
- [ ] `verify_calculation(answer, evidence) -> bool` 추가, LLM 호출 0회

---

## Phase H. Error Analysis — policy §9

**이게 없으면 Ablation 숫자를 해석할 수 없다.** 점수가 왜 올랐는지 모르면 개선이 아니라 우연이다.

### H-1. 단계별 실패 귀속

각 문항의 실패를 **가장 앞선 단계에 귀속**시킨다.

| 실패 유형 | 판정 |
|---|---|
| `RETRIEVAL` | 골든 근거가 top-k에 없음 |
| `RANKING` | top-k엔 있으나 top-1이 아님 |
| `REASONING` | 근거는 맞는데 답이 틀림 |
| `TOOL` | 계산값 불일치 |
| `CITATION` | 답은 맞는데 근거 표기 없음 |
| `REFUSAL` | 답할 수 있는데 거부 / 없는데 답함 |

### H-2. 산출물

`artifacts/error_analysis.json` — 문항별 실패 단계 + 병목 히스토그램

**게이트 H**
- [ ] 60문항 전부 실패 단계가 귀속됨
- [ ] **병목 1위 단계가 특정됨** → 다음 스프린트 우선순위

---

## Phase I. Ablation Study — policy §10

### I-1. 구성 — 우리 제약에 맞게 조정

policy의 5단계를 그대로 쓰지 않는다. **Vector RAG를 바닥으로 두면 우리 최선 구성이 안 나온다**
(메타 프리필터가 가장 큰 효과인데 그게 빠짐).

| # | 구성 | 누적 요소 | 크레딧 |
|---|---|---|---|
| **A0** | 메타 프리필터만 | 규칙 | 생성 1회 |
| **A1** | + BM25 | Sparse | 생성 1회 |
| **A2** | + 임베딩 하이브리드 | Dense | 생성 1회 |
| **A3** | + Re-ranker | 재순위 | 생성 1회 (G1 확정 시) |
| **A4** | + Agent 루프 | 반복 검색 | hop당 1~2회 |
| **A5** | + 계산 Tool | function calling | +1회 |
| **A6** | + Verifier | critic | +1회 |

**A2·A3는 G1 미확정 시 건너뛴다.** 그 경우에도 A0→A1→A4→A5→A6로 기여도 곡선은 나온다.

### I-2. 측정 프로토콜

- **dev 60문항으로 A0~A6 각 1회.** 프롬프트는 고정 — 구성만 바꾼다
- **heldout은 최종 구성 1회만.** 여기서 dev 대비 낙폭이 일반화 갭이다
- Closed는 Exact Match, Open은 LLM-as-a-Judge + Groundedness (policy §5)
- 각 구성마다 **호출 수·토큰·지연**을 함께 기록 → 성능/비용 곡선

### I-3. 예상 비용

```
A0~A6 (7구성) × 60문항 × 평균 2호출 × 1,800토큰 ≈ 1.5M 토큰
heldout 최종 1회        × 20문항 × 2호출         ≈  70k 토큰
```

**단가 미확정이라 금액을 못 낸다.** `PRICE_IN/OUT_PER_1K` 확정이 Ablation 착수의 선행조건이다.
비쌀 경우 **문항을 30개로 줄이거나 A2·A3를 생략**한다.

**게이트 I**
- [ ] 각 구성의 기여도(Δ)가 수치로 나옴
- [ ] 성능/비용 곡선에서 **꺾이는 지점**이 특정됨 → 제출 구성 결정
- [ ] heldout 낙폭 측정

---

## 크레딧 배분

| Phase | 크레딧 | 지금 가능? |
|---|---|---|
| A EDA | **0** | ✅ **즉시** |
| B Golden Set | **0** | ✅ 즉시 |
| C Graph Schema | **0** (규칙 기반) | ✅ 즉시 |
| D Retriever | **0** | ✅ 즉시 |
| E Re-ranker | 0 | G1 확정 후 |
| F Agent+Tool | 소량 (수동 확인) | 소액 가능 |
| G Verifier | 0 | ✅ 즉시 |
| H Error Analysis | 중간 (평가셋 실행) | 크레딧 후 |
| **I Ablation** | **대부분** | 🔴 크레딧 + 단가 확정 후 |

> **A~D·G는 크레딧 없이 전부 할 수 있다.** 과제 크레딧 20만원을 기다리는 동안
> policy §12의 1~5번을 다 끝낼 수 있다는 뜻이다.

---

## 일정 (D-28)

| 기간 | Phase | 산출물 |
|---|---|---|
| 08-09 ~ 08-12 | **A EDA** + B Golden Set 착수 | 데이터 프로파일, GraphRAG 채택 판정 |
| 08-13 ~ 08-16 | B 완료 + **C Graph Schema** | 60문항, Node/Edge 확정 |
| 08-17 ~ 08-22 | **D Retriever** + F 보강 | Recall@5 ≥ 0.85, 계산 Tool 노출, periodic 섹션 |
| 08-23 ~ 08-26 | G Verifier + **H Error Analysis** | 병목 특정 |
| 08-27 ~ 08-31 | **I Ablation** + 배포 리허설 | 기여도 곡선, 제출 구성 결정 |
| 09-01 ~ 09-05 | 기술제안서 + 최종 점검 | heldout 1회 측정 |
| **09-06** | **제출 마감** | |

---

## 리스크

| 리스크 | 영향 | 대응 |
|---|---|---|
| 🔴 **과제 크레딧 미확보** | Phase H·I 불가 | 최우선 해결. 미확보 시 dev 30문항으로 축소 |
| 🔴 **G1 미확정** | Phase E 착수 불가 | A2·A3를 뺀 Ablation으로도 곡선은 나옴 |
| 🔴 **periodic 섹션 인덱스 부재** | 정기공시 질의 근거 0건 | Phase A-1에서 목차 파싱 선행, D에서 완성 |
| 🟡 기업-기업 엣지 희소 | GraphRAG 전제 붕괴 | A-2에서 조기 판정, 하이브리드 검색으로 선회 |
| 🟡 단가 미확정 | Ablation 비용 산정 불가 | 사용 내역 역산 |
| 🟡 heldout 오염 | 일반화 측정 무효 | 최종 1회까지 미개봉 원칙 |

---

## 이 파이프라인이 policy에 답하는 방식

| policy 원칙 | 이 파이프라인에서 |
|---|---|
| §1 데이터 분석이 먼저 | Phase A — 크레딧 0, 즉시 착수 |
| §2 GraphRAG 적합성 | Phase A-2가 **전제 자체를 검증**한다 (엣지 밀도) |
| §3 Golden Set 먼저 | Phase B — 6→60문항, 필드 4개 추가 |
| §4 계산은 Tool | Phase F — `compute.py`를 `TOOL_SPECS`에 노출 (현재 누락) |
| §5 Closed/Open 평가 분리 | Phase I 측정 프로토콜 |
| §6 Verifier 분리 | Phase G — 계산 검증 추가 |
| §7 Validation 일반화 | Phase B-3 — **섹터 단위** 홀드아웃 |
| §8 Vector Store 설계 | Phase D 실험 축 6개 |
| §9 Error Analysis | Phase H — 6종 실패 귀속 |
| §10 Ablation | Phase I — A0~A6, 비용 곡선 포함 |
| §11 최신 구조는 참고만 | 연구 대상을 Retrieval+Agent로 한정 (§1) |
| §12 개발 순서 | Phase A~I가 그 순서다 |
