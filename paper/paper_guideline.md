# paper — 논문 가이드라인

ARGUS(공시 Agent) 구현을 위해 읽은 논문 **18편**의 지도이자, **구현 순서 지침**이다.
(2026-08-12: `idea/QA.pdf` 추가 — 기법이 아니라 **진단** 논문이다.)

과제 규약 [../docs/CONVENTION.md](../docs/CONVENTION.md) ·
예산 방침 [../docs/clova_payment.md](../docs/clova_payment.md) ·
예산 계산 [../notebooks/clova_budget.ipynb](../notebooks/clova_budget.ipynb)

---

## 0. 구현 순서 — 반드시 이 순서로

```
   base  ─────────▶  idea  ─────────▶  multi_agent
 (5편)              (10편)             (3편)
 루프가 도는가?      점수가 나오는가?     더 짜낼 수 있는가?
 싸고 빠름           공수 대부분          여유 있을 때만
 ✅ 완료            ✅ 대부분 완료        🔴 미착수
```

🔴 **`idea/QA.pdf` 는 이 순서 밖에 선다** (2026-08-12 추가). 기법이 아니라 **진단**이므로
*"무엇을 만들까"* 가 아니라 *"만든 것이 실제로 쓰이는지 어떻게 재는가"* 를 준다.
**세 축 어디에든 붙는 검산 도구**로 읽는다 — 그래서 arm 을 늘리는 대신 **arm 의 전제를
검증하는 실험**(`ORACLE`)이 나왔다.

**한 줄 근거** — `base`는 **가장 싸게 가장 큰 불확실성을 없앤다**(HCX에서 CoT가 되는가).
`idea`는 **점수가 실제로 나오는 곳**이다(데이터·검색). `multi_agent`는 **정확도를 짜내는 마감층**이며
잘못 쓰면 비용·지연으로 감점된다.

순서를 바꾸면 생기는 일:

| 잘못된 순서 | 결과 |
|---|---|
| `idea` 먼저 | 인덱스를 다 만든 뒤에 HCX에서 CoT가 안 되는 걸 발견 → 뼈대 재설계, 크레딧은 이미 소진 |
| `multi_agent` 먼저 | 검증 안 된 파이프라인에 에이전트를 겹쳐 디버깅 불가 + 호출 수 폭발 |
| 병렬 진행 | D-30에 인력이 분산되어 어느 것도 완성되지 않음 |

---

## 1. 폴더 구성

| 폴더 | 편수 | 정체 | 정리 문서 |
|---|---|---|---|
| [base/](base/) | 5 | 학습 없이 프롬프트로 능력을 끌어내는 계보. **우리 제약의 유일한 선택지** | [base/base.md](base/base.md) |
| [idea/](idea/) | **10** | GraphRAG 계열 9 + **진단 1**(`QA.pdf`) | [idea/idea.md](idea/idea.md) |
| [multi_agent/](multi_agent/) | 3 | 멀티에이전트 협업 + 벤치마크 | [multi_agent/multi_agent.md](multi_agent/multi_agent.md) |

### base/ — 토대 (5편)

HCX 베이스 모델을 학습시킬 수 없으므로([CONVENTION §8](../docs/CONVENTION.md)) 이 계보가 전부다.

| 논문 | 남는 것 |
|---|---|
| [GPT-3](base/gpt-3-few-shot-learners-2020.pdf) | few-shot ICL이 성립한다는 전제. **exemplar 설계가 곧 성능** |
| [Chain-of-Thought](base/chain-of-thought-2022.pdf) | `think_trace`의 근거. **단, ~100B 창발 — HCX 실측 필요** |
| [Self-Consistency](base/self-consistency-2023.pdf) | k경로 다수결. **Closed 질의 한정** (논문 명시 한계) |
| [ReAct](base/react-2023.pdf) | 에이전트 루프 골격 + **backoff 휴리스틱** + hop 예산 근거 |
| [Toolformer](base/toolformer-2023.pdf) | 파인튜닝 필요 → 원본 불가. function calling **발상**만 있고 🔴 **미연결**(`tools=` 가 `chat()` 에 안 실린다 — `base.md §7` 정정 참고) |

### idea/ — 본체 (9편)

| 논문 | 남는 것 |
|---|---|
| [HyperGraphRAG](idea/hypergraphrag-2025.pdf) | **n-ary 하이퍼엣지** — 공시 팩트가 본질적으로 n-ary. 도메인 적합성 1위 |
| [Think-on-Graph 2.0](idea/think-on-graph-2-2025.pdf) | **training-free** KG×Text tight coupling. 재무제표 벤치마크에서 GraphRAG 5배 상회 |
| [HippoRAG](idea/hipporag-2024.pdf) | PPR 단일 스텝 멀티홉. 반복검색比 10~30배 저렴 → 저지연 1차 |
| [GraphReader](idea/graphreader-2024.pdf) | atomic fact + notebook 탐색. **정기공시 독해의 정답에 가장 가까움** |
| [Relink](idea/relink-2026.pdf) | PMI 공동출현 pool(LLM 불필요) + **distractor 랭커** |
| [G-Retriever](idea/g-retriever-2024.pdf) | **PCST** 서브그래프 압축 (튜닝 부분은 불가) |
| [GraphRAG](idea/graphrag-local-to-global-2024.pdf) | 커뮤니티 요약 → 전역 sensemaking. **전량 인덱싱은 예산 초과** |
| [Graph of Thoughts](idea/graph-of-thoughts-2024.pdf) | aggregation — 다중 문서 비교·연산 |
| [GraphRAG-R1](idea/graphrag-r1-2026.pdf) | RL은 불가. **얕은 검색/과잉사고** 문제 정의만 차용 |
| 🔴 [**Diagnosing Evidence Utilization**](idea/QA.pdf) | **골든 근거만 주고 재는 통제 실험** · system 이 근거보다 2.6배 주의 · **judge 3회 σ**. 기법이 아니라 **진단**이다 |

### multi_agent/ — 마감층 (3편)

| 논문 | 남는 것 |
|---|---|
| [SWE-Lancer](multi_agent/swe-lancer-2025.pdf) | **판단(45%) > 생성(26%)** → critic에 자원 배분하는 근거 |
| [CAMEL](multi_agent/camel-role-playing-2023.pdf) | **실패 모드 4종**(role flip·반복·빈응답·무한루프) + task specifier |
| [AutoGen](multi_agent/autogen-2023.pdf) | 통일 인터페이스 + **이중 종료 장치**(자연어 + 코드 상한) |

> `swe-lancer-2025.pdf`는 멀티에이전트 프레임워크가 아니라 **벤치마크 논문**이다.
> 분류가 다르지만 위 결과가 역할 설계에 직결되어 이 폴더에서 다룬다.

---

## 2. Phase 1 — base (권장 3~4일)

**목적: 가장 싸게 가장 큰 불확실성을 제거한다.**

### 하는 일

1. `GET /answer` 스켈레톤 (스키마 고정 — [CONVENTION §4](../docs/CONVENTION.md))
2. HCX-005 호출 래퍼 + **토큰/비용 로깅** ← 이게 없으면 예산 관리가 불가능하다
3. 고정 개발셋 20~30문항 (참고 질의 6종을 유형별로 확장)
4. E2E 평가 하네스 — 질의 → 답변 → 근거 표기까지 관통
5. ReAct 루프 최소 구현 (검색은 BM25 스텁으로 충분)

### 반드시 측정할 것

| 측정 | 왜 |
|---|---|
| **HCX-005에서 CoT가 작동하는가** | 창발 능력이다. 안 되면 "유창하지만 틀린" 추론이 나와 「근거 기반」 감점. **뼈대가 바뀐다** |
| **토큰 단가 4개** | [clova_budget.ipynb](../notebooks/clova_budget.ipynb) §1·§5. 민감도 1위가 입력 단가다 |
| Self-Consistency k별 이득/지연 | k=3/5/7. 이득 꺾이는 지점 고정 |
| hop 상한 | ReAct는 5~7스텝 이상 이득 없음. 공시에서도 그런지 |

### 통과 조건 (gate)

- [ ] 엔드포인트가 규정 스키마로 응답한다
- [ ] 질문 1건당 호출 수·토큰·비용이 로그에 남는다
- [ ] **CoT on/off 결론이 났다** (되는지 안 되는지)
- [ ] 예산표가 실측 단가로 채워졌다

> 이 gate를 통과하지 못하면 Phase 2로 가지 않는다. 인덱스를 만든 뒤 뼈대를 바꾸는 것이
> 이 과제에서 가장 비싼 실수다.

---

## 3. Phase 2 — idea (권장 3주, 공수의 대부분)

**목적: 점수가 실제로 나오게 한다.** 여기가 본체다.

### 2-A. LLM-free 작업 먼저 (호출 0회 — 크레딧 소모 없음)

전체 공수의 대부분이며 크레딧을 한 푼도 쓰지 않는다.

- XML 파싱 → **마크다운** 정규화, **목차 기반 섹션 청킹**
- `manifest.jsonl`(4,204건) · `universe.csv` **메타데이터 프리필터**
- **정정공시 체인** 역추적 (`is_correction` + `rcept_no`)
- 동의어 사전 — `universe.csv`에서 무료 (현대차/현대자동차, KT/케이티 등)
- 커뮤니티 계층 — 업종 8 > 섹터 20 > 기업 70을 Leiden 대신 사용 (무료)
- BM25 / 임베딩 / **PPR** / **PCST** / reranker
- **PMI 공동출현 그래프** (Relink)

### 2-B. LLM 인덱싱 — 티어 차별화 (단가 실측 후)

노트북 §4가 보여주듯 **`periodic`을 켜는 순간 예산이 무의미해진다** (전체 토큰의 95%).

| 티어 | 대상 | 규모 | 전략 |
|---|---|---|---|
| T1 | exchange + major | ~8M 토큰 | **LLM n-ary 추출 전량** (하이퍼엣지) |
| T2 | holding | ~16M 토큰 | 정형 필드 규칙 파싱 + n-ary 팩트화 |
| T3 | periodic | ~450M 토큰 | **섹션 인덱스만.** 본문은 질의 시 lazy 로딩 |

> **T1이 투자 대비 효과 최고 구간이다** — 전체 토큰의 1.7%뿐인데
> 평가 예시 질의 6개 중 4개를 커버한다(공급계약·자금조달·계약해지·설비투자).

### 2-C. 온라인 파이프라인

```
[0] 메타데이터 프리필터        LLM 0회
[1] 질의 라우터                단순/비교연산/복합추론 분기
[2] HippoRAG PPR 단일 스텝     저지연 1차
[3] 부족하면 ToG-2 반복        depth ≤ 3
[4] 정기공시는 GraphReader     섹션 하강
[5] PCST 근거 압축             HCX 컨텍스트 절약
[6] Relink 랭커로 distractor 제거   계약체결↔해지, 원본↔정정
[7] HCX-005 생성               근거 provenance 강제
```

### 통과 조건 (gate)

- [ ] T1 하이퍼엣지 인덱스 완성, 평가 예시 4종에 답이 나온다
- [ ] 정정공시를 구분해 근거에 포함한다
- [ ] 서버에 배포되어 외부에서 호출된다
- [ ] 질문당 비용이 예산표 상한 안에 있다

---

## 4. Phase 3 — multi_agent (여유가 있을 때만, 3~5일)

**목적: 정확도를 짜낸다. 단, 비용·지연을 늘리지 않는 형태로만.**

### 대화형 멀티에이전트는 쓰지 않는다

| 제약 | 타격 |
|---|---|
| 크레딧 30만원, 초과 자체 부담 | 자유 대화는 질문 1개에 호출 20~50회 |
| **응답 속도가 평가 기준일 수 있음** | 순차 대화는 지연이 턴 수에 비례 |
| 서버 2 vCPU / 4 GB | 에이전트 병렬 실행 여력 없음 |
| CAMEL이 보고한 무한 루프 | 크레딧과 시간을 동시에 태움 |

### 대신 — 역할 분리된 결정적 파이프라인

```
[1] 질의 분해기   task specifier (CAMEL)        1회
[2] 검색          ReAct 루프, hop 상한          hop당 1~2회
[3] 근거 판정     critic (SWE-Lancer 근거)      1회  ★ 우선순위 1위
[4] 답변 생성     HCX-005 단독                  1회  (G1 준수)
[5] 자기검증      Closed 질의만 self-consistency  k회
```

단계마다 **고정 횟수**여서 질문당 비용 상한이 계산되고, 자유 대화가 없어
role flipping·무한 루프가 **구조적으로 불가능**하다.

**[3] critic이 이 Phase의 전부라고 봐도 된다.** 호출 1회로
「근거 기반」·「근거 완전성」·「안전성」 세 지표를 건드리고,
SWE-Lancer의 45% vs 26%가 그 효율의 근거다.

### 함께 만들 것 — 논문에 없는 두 지표

평가 8지표 중 **「안전성·신뢰성」과 「정보한계 대응」은 17편 어디에도 설계가 없다.**
여기가 차별화 지점이다. 단, ReAct의 backoff 휴리스틱이 후자의 절반을 준다:

- `ReAct → CoT-SC`: 스텝 내 답을 못 내면 폴백
- `CoT-SC → ReAct`: 다수 답이 n/2 미만이면(내부 지식 불확실) 검색으로 전환

---

## 5. 일정 배분 (제출 마감 2026-09-06)

| Phase | 기간 | 산출물 | 크레딧 |
|---|---|---|---|
| **1 base** | 3~4일 | 엔드포인트 + 비용 로깅 + CoT 결론 + 예산표 | 소액 (개발셋 소규모) |
| **2 idea** | ~3주 | T1 인덱스 + 온라인 파이프라인 + 배포 | 대부분 (T1/T2 인덱싱) |
| **3 multi_agent** | 3~5일 | critic + 안전성/정보한계 모듈 | 소액 |
| 예비 | 3~4일 | 기술제안서, 리허설, 예비율 15% | — |

**서버는 8월에 켜두지 않는다.** 로컬 개발 → 8월 말 배포 리허설 → 09-07~09-20 필수 상시 가동
→ 09-20 이후 삭제. 자세히는 [clova_payment.md §3](../docs/clova_payment.md).

---

## 6. 요약 — 세 문장

1. **base 먼저.** HCX에서 CoT가 되는지 모르는 채로 인덱스를 만들면 안 된다. 가장 싼 검증이다.
2. **idea가 본체.** LLM-free 작업(공수 대부분, 비용 0)을 앞으로 당기고,
   LLM 인덱싱은 T1에 집중한다. `periodic`은 켜지 않는다.
3. **multi_agent는 critic 하나로 족하다.** 대화형은 이 과제에서 감점 요인이다.
