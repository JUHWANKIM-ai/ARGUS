# Phase I — Ablation Study

**policy §10** · 크레딧 **대부분** · 선행 [08-error-analysis](08-error-analysis.md)

> 모듈을 하나씩 추가하며 각 요소가 성능 향상에 얼마나 기여하는지 검증한다. — policy §10

> 🔄 **이 문서는 살아있다.** Phase A~H 를 돌리며 새 병목·새 구성요소가 나오면 arm 이 추가된다.
> 갱신 규칙은 §I-7. **현재 버전 v1 (2026-08-09)**.

---

## I-1. policy 안을 그대로 쓰지 않는 이유

policy §10 은 `Vector RAG → GraphRAG → +Re-ranker → +Agent → +Tool` 을 제시한다.
**우리 코퍼스에서는 바닥이 틀렸다.**

실측: 메타 프리필터(`corp_name` + `rcept_dt` prefix)만으로 후보가 **1,469 → 1건**이 되고
골든이 1위가 된다. Vector RAG 를 바닥으로 두면 **가장 큰 기여 요소가 측정에서 빠진다.**

→ **A0 을 메타 프리필터로 놓는다.**

---

## I-2. Arm 정의

| # | Arm | 누적 구성요소 | 논문 근거 | 크레딧 |
|---|---|---|---|---|
| **A0** | 메타 프리필터 | `manifest` + `universe` 필터, 생성만 | 실측 · 주최 측 툴 명세 | 생성 1 |
| **A1** | + BM25 | Sparse 검색 | [ToG-2](#appendix-b) · [Relink](#appendix-b) | 생성 1 |
| **A2** | + 임베딩 하이브리드 | Dense + RRF | [G-Retriever](#appendix-b) · [HippoRAG](#appendix-b) | 생성 1 |
| **A3** | + Re-ranker | 재순위 | [ToG-2](#appendix-b) · [Relink](#appendix-b) | 생성 1 |
| **A4** | + Agent 루프 | ReAct 반복 · hop ≤ 3 | [ReAct](#appendix-a) · [ToG-2](#appendix-b) · [GraphReader](#appendix-b) | hop당 1~2 |
| **A5** | + 계산 Tool | function calling | [Toolformer](#appendix-a) · policy §4 | +1 |
| **A6** | + Verifier | critic 판정 | [SWE-Lancer](#appendix-c) · [CAMEL](#appendix-c) | +1 |

**A2·A3 은 G1 미확정 시 건너뛴다.** 그 경우에도 `A0→A1→A4→A5→A6` 로 곡선은 성립한다
([05-reranker](05-reranker.md) 참조).

---

## I-3. 측정 프로토콜

### 통제 변수

**arm 외 모든 것을 고정한다.** 하나라도 같이 바뀌면 Δ 의 귀속이 불가능하다.

```
고정 : 프롬프트 · 온도(0.0) · maxTokens · 골든셋 · 인덱스 스냅샷 · 모델(HCX-005)
변동 : arm 구성요소만
```

인덱스는 **arm 시작 시점의 스냅샷을 해시로 기록**한다. 중간에 재빌드하면 비교가 깨진다.

### 실행 규칙

| 항목 | 값 |
|---|---|
| 대상 | dev 54문항 (+정보한계 6) |
| 반복 | arm 당 **1회** (온도 0.0 이므로 결정적) |
| heldout | **최종 구성 1회만** |
| 순서 | A0 → A6 순차. 실패해도 다음 arm 진행 |

### 기록 항목 — 성능만 재면 안 된다

```
성능 : closed EM · open judge(3항목) · 정답률
검색 : Recall@1/@5 · MRR
비용 : LLM 호출 수 · 입력/출력 토큰 · 지연(p50/p95)
실패 : first_failure 분포 (08 의 6종)
```

**성능/비용 곡선**을 그린다. policy §10 은 성능만 말하지만, 우리는 크레딧 상한이 있어
**Δ성능 / Δ비용** 이 실제 채택 기준이다.

### 축별 분해 — [02](02-golden-set.md) 3축 그대로

각 arm 의 Δ 를 **난이도 × 답변형식 × 과제유형** 으로 분해한다.
전체 평균만 보면 "왜 올랐는지"를 모른다.

| | A0 | A1 | A2 | A3 | A4 | A5 | A6 |
|---|---|---|---|---|---|---|---|
| 전체 정답률 | | | | | | | |
| 난이도 하/중/상 | | | | | | | |
| closed / open | | | | | | | |
| search / compare / reason | | | | | | | |
| 호출 수 (평균) | | | | | | | |
| 토큰 (평균) | | | | | | | |

---

## I-4. 누적 추가 vs Leave-one-out

**논문들은 대부분 제거(w/o X) 방식이고, 우리 A0~A6 은 누적 추가다.** 둘은 다른 것을 본다.

| 방식 | 답하는 질문 | 비용 |
|---|---|---|
| 누적 (A0→A6) | 순서상 **한계 기여도** | 7회 |
| Leave-one-out | 완성 구성에서의 **필수성** | 구성요소 수만큼 |

**설계**: 누적을 기본으로 하고, **[08](08-error-analysis.md) 병목 상위 2개만 leave-one-out** 을 추가한다.
전체 LOO 는 비용이 2배가 되므로 하지 않는다.

```
L1 = A6 − (병목 1위 요소)
L2 = A6 − (병목 2위 요소)
```

논문들의 LOO 결과(부록 D)와 대조해 우리 Δ 가 타당한 크기인지 판정한다.

---

## I-5. 예상 비용

```
누적  A0~A6 (7) × 60문항 × 평균 2호출 × 1,800토큰  ≈ 1.5M 토큰
LOO   2구성    × 60문항 × 평균 2호출              ≈ 0.4M 토큰
heldout 최종 1회 × 20문항                          ≈ 0.07M 토큰
─────────────────────────────────────────────────────────────
합계                                              ≈ 2.0M 토큰
```

> 🔴 **단가 미확정이라 금액을 낼 수 없다.** `PRICE_IN/OUT_PER_1K` 확정이 **착수 선행조건**이다.
> 비쌀 경우 축소 순서: ① LOO 생략 → ② 문항 30개로 축소 → ③ A2·A3 생략.

---

## I-6. 게이트 I

- [ ] arm 별 Δ 가 수치로 나옴
- [ ] **Δ 가 축별(난이도·답변형식·과제유형)로 분해**됨
- [ ] 성능/비용 곡선에서 **꺾이는 지점** 특정 → 제출 구성 결정
- [ ] 병목 상위 2개 LOO 완료
- [ ] 우리 Δ 를 부록 D 의 논문 효과 크기와 대조
- [ ] heldout 낙폭 측정 (일반화 갭)

---

## I-7. 갱신 규칙 — 이 문서는 계속 바뀐다

| 계기 | 조치 | 예 |
|---|---|---|
| [08](08-error-analysis.md) 에서 새 병목 발견 | **arm 추가** | `CITATION` 병목 → A7 근거표기 강제 |
| 새 논문·기법 도입 | arm 추가 + 부록 근거 | PCST 압축 → A8 |
| arm 간 Δ 가 0에 가까움 | 골든셋에 변별 문항 추가 ([02](02-golden-set.md) B-7) | |
| G1 확정 | A2·A3 활성/비활성 확정 | |
| 크레딧 부족 | §I-5 축소 순서 적용 | |

**버전 기록**

| ver | 일자 | 변경 |
|---|---|---|
| v1 | 2026-08-09 | 최초 — A0~A6 정의, 부록 A~D |

---
---

# Appendix — 논문 근거

논문 17편은 [paper/](../../paper/) 에 있다. 폴더별 정리는
[base.md](../../paper/base/base.md) · [idea.md](../../paper/idea/idea.md) · [multi_agent.md](../../paper/multi_agent/multi_agent.md).

---

## Appendix A — `paper/base/` : 프롬프팅·추론 근거

| 논문 | 이 ablation 에 주는 것 | 해당 arm |
|---|---|---|
| [GPT-3](../../paper/base/gpt-3-few-shot-learners-2020.pdf) | 학습 없이 few-shot ICL 로 태스크를 푼다 — **전 arm 의 전제** | 전체 |
| [Chain-of-Thought](../../paper/base/chain-of-thought-2022.pdf) | 중간 추론 단계. **난이도 상에서만 이득, 하에서는 없음** | A4 · 난이도 축 |
| [Self-Consistency](../../paper/base/self-consistency-2023.pdf) | k경로 다수결. **고정 답변 집합에서만** → closed 전용 | A6 · closed 축 |
| [ReAct](../../paper/base/react-2023.pdf) | thought-action-observation 교차. **backoff 휴리스틱** · 스텝 상한 | **A4** |
| [Toolformer](../../paper/base/toolformer-2023.pdf) | 모델이 스스로 도구 호출 시점 결정 | **A5** |

**A4 설계에 직접 쓰인 ReAct 근거**
- `ReAct → CoT-SC` : 스텝 내 답을 못 내면 폴백
- `CoT-SC → ReAct` : 다수 답이 n/2 미만이면 검색으로 전환
- 정답 궤적 중 7스텝을 쓴 것은 **0.84%** → hop 상한의 근거
- **ReAct 단독은 CoT 보다 낮다** (HotpotQA 27.4 vs 29.4). 조합이 이긴다 → A4 단독 기대치를 낮게 잡는다

**A5 설계에 쓰인 Toolformer 근거**
- 원본은 파인튜닝 전제라 적용 불가 → **function calling 으로 대체**
- 필터링 발상("도구 호출이 예측을 개선했는가")을 **A5 의 Δ 측정 자체**로 옮김

---

## Appendix B — `paper/idea/` : 검색·그래프 근거

| 논문 | 이 ablation 에 주는 것 | 해당 arm |
|---|---|---|
| [HyperGraphRAG](../../paper/idea/hypergraphrag-2025.pdf) | n-ary 하이퍼엣지 — 계약 팩트가 이진 트리플로 쪼개지면 안 됨 | 인덱스 설계 ([03](03-graph-schema.md)) |
| [Think-on-Graph 2.0](../../paper/idea/think-on-graph-2-2025.pdf) | KG×Text tight coupling 반복. **training-free** | **A4** |
| [HippoRAG](../../paper/idea/hipporag-2024.pdf) | PPR 단일스텝. 동의어 엣지(리트리버 인코더) | **A2** |
| [G-Retriever](../../paper/idea/g-retriever-2024.pdf) | SentenceBert 인덱싱 · PCST 서브그래프 압축 | **A2** · 후보 A8 |
| [Relink](../../paper/idea/relink-2026.pdf) | query-driven ranker · PMI 공동출현 pool | **A1 · A3** |
| [GraphRAG](../../paper/idea/graphrag-local-to-global-2024.pdf) | 커뮤니티 요약 map-reduce | 후보 (섹터 계층으로 축소) |
| [Graph of Thoughts](../../paper/idea/graph-of-thoughts-2024.pdf) | thought aggregation | A4 (multi_compare 경로) |
| [GraphRAG-R1](../../paper/idea/graphrag-r1-2026.pdf) | 얕은 검색·과잉사고 억제 (PRA·CAF) | 예산 가드 (RL 은 적용 불가) |

**A1 · A3 에 직접 쓰인 근거**
- **ToG-2**: 엔티티 프루닝 도구 비교에서 **BGE-Reranker 1위**, 그러나 **BM25 도 근접**하며 자원 효율 우수
  → A1(BM25) 을 진지한 baseline 으로, A3 의 Δ 가 작을 수 있음을 미리 예상
- **ToG-2 ToG-FinQA**(2023년 중국 기업 재무제표): ToG-2 **34.0%** vs GraphRAG 6.2% vs ToG 14.0% vs Vanilla RAG **0%**
  → 우리 도메인과 가장 가까운 조건. A4 의 기대 효과가 큼
- **Relink**: query-driven ranker 를 일반 임베딩 코사인으로 교체 시 **−19.4% EM**
  → A3 의 상한 기대치

**A2 에 쓰인 근거**
- **HippoRAG**: 동의어 엣지가 표기 변이를 흡수. 우리는 `universe.csv` 의
  `corp_name`/`listed_name`/`corp_eng_name` 이 **이미 동의어 사전**이라 무료로 얻음
- **G-Retriever**: 토큰 **83~99% 절감**, 환각 인용률 31%→77%

---

## Appendix C — `paper/multi_agent/` : Agent·Verifier 근거

| 논문 | 이 ablation 에 주는 것 | 해당 arm |
|---|---|---|
| [SWE-Lancer](../../paper/multi_agent/swe-lancer-2025.pdf) | **판단 44.9% > 생성 26.2%** — critic 이 생성기보다 쉬움 | **A6** |
| [CAMEL](../../paper/multi_agent/camel-role-playing-2023.pdf) | critic-in-the-loop · **실패 모드 4종** | **A6** · 가드 |
| [AutoGen](../../paper/multi_agent/autogen-2023.pdf) | 이중 종료 장치 (자연어 + 코드 상한) | 가드 |

**A6 이 정당화되는 이유 (SWE-Lancer)**
같은 모델이 "만들기"보다 "고르기"를 훨씬 잘한다(45% vs 26%).
→ **critic 에 호출 1회를 쓰는 것이 생성기를 키우는 것보다 효율적**이라는 독립적 증거.
Self-Consistency 의 다수결과 CAMEL 의 critic 이 왜 잘 듣는지도 같은 원리다.

**A4 에 반영된 CAMEL 실패 모드**

| 실패 모드 | 우리 대응 |
|---|---|
| role flipping | 역할 분리 파이프라인 (자유 대화 없음) |
| assistant repeating instructions | hop 상한 |
| flake replies | 근거 0건이면 즉시 종료 |
| **infinite loop** | 이중 종료 (AutoGen) + `Budget` |

**미채택 명시** — 대화형 멀티에이전트는 호출 20~50회 · 지연 누적 · 무한루프 위험으로
이 ablation 의 arm 에 넣지 않는다. **왜 안 썼는지가 기술제안서에서 더 중요할 수 있다.**

---

## Appendix D — 논문별 효과 크기 대조표

**우리가 측정한 Δ 가 타당한 크기인지 판정하는 기준표다.**
A3 에서 +1%p 가 나왔다면 Relink 의 −19.4% 와 비교해 "왜 이렇게 작은가"를 물을 수 있다.

### D-1. 제거(ablation) 효과 — 논문 보고값

| 논문 | 제거 대상 | 효과 | 우리 대응 arm |
|---|---|---|---|
| Relink | **query-driven ranker** | **−19.4% EM** (HotpotQA) | A3 |
| Relink | 명시 그래프 (Gb) | −12.9% EM | A2 |
| Relink | 대조 정렬 손실 | −7.2% EM | — |
| Relink | latent relation pool (Rc) | −5.7% EM | A2 |
| G-Retriever | **graph encoder** | **−22.5%** (WebQSP hit@1) | A2 |
| G-Retriever | textualized graph | −19.2% | 인덱스 설계 |
| G-Retriever | retrieval | −9.4% | A1 |
| G-Retriever | projection layer | −1.1% | — |

### D-2. 추가(누적) 효과 — 논문 보고값

| 논문 | 추가 요소 | 효과 | 우리 대응 arm |
|---|---|---|---|
| Self-Consistency | greedy → k경로 다수결 | **+17.9%** (GSM8K) | A6 |
| ToG-2 | ToG → ToG-2 | **+16.6%** (AdvHotpotQA) | A4 |
| ToG-2 | vs Vanilla RAG (ToG-FinQA) | **0% → 34.0%** | A1→A4 |
| HippoRAG | vs ColBERTv2 (2Wiki R@2) | 59.2 → **70.7** | A2 |
| HippoRAG | + IRCoT 결합 | R@5 +18% (2Wiki) | A4 |
| GraphRAG | vs vector RAG (comprehensiveness) | 승률 **72~83%** | — |
| G-Retriever | vs prompt tuning | +40.6% 평균 | A2 |
| Graph of Thoughts | vs ToT (정렬 품질) | **+62%**, 비용 −31% | A4 |
| **우리 실측** | **CoT on/off (HCX-005)** | **+33.3%p** | A4 |
| **우리 실측** | **메타 프리필터 (Q-001)** | **14/1,332 → 1/1** | A0 |

### D-3. 논문의 ablation 방법론 — 우리 설계와 대조

| 논문 | 방식 | 특징 |
|---|---|---|
| Relink | **제거** (w/o X) | 컴포넌트 하나씩 빼고 EM/F1 |
| G-Retriever | **제거** | 4개 컴포넌트 각각 |
| HippoRAG | **대체** | OpenIE 대안(REBEL·Llama) · PPR 대안(노드만/이웃까지) |
| GraphRAG | **조건 비교** | 6개 조건 head-to-head 승률 |
| SWE-Lancer | **조건 비교** | test-time compute · pass@k |
| **우리** | **누적 + 부분 LOO** | A0→A6 + 병목 상위 2개 제거 |

**HippoRAG 의 '대체' 방식이 우리에게 유용하다.** 제거하면 파이프라인이 안 돌아가는 구성요소
(예: 검색 자체)는 제거 대신 **더 단순한 것으로 대체**해 측정한다.

### D-4. 판정 기준

| 우리 Δ | 해석 |
|---|---|
| 논문 보고값의 **50% 이상** | 정상. 도메인 차이로 설명 가능 |
| 논문 보고값의 **10~50%** | 구현 확인 필요. 해당 arm 이 제대로 켜졌는가 |
| **10% 미만 또는 음수** | **버그 의심**. [08](08-error-analysis.md) 로 되돌아가 병목 재확인 |

> 예: A3(리랭커)에서 Relink 가 −19.4% 를 보고했는데 우리가 +1%p 만 얻었다면,
> 이미 메타 프리필터로 후보가 1~26건까지 좁혀져 **리랭커가 개선할 여지 자체가 없는 것**일 수 있다.
> 이 경우는 버그가 아니라 **우리 파이프라인의 구조적 특성**이며, 기술제안서에 그렇게 쓴다.
