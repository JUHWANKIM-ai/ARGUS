# ARGUS 연구 파이프라인 — 개요

**기준일 2026-08-09 (착수 시점 문서) · 제출 마감 09-06 · 평가 09-07~09-20**

> ⚠️ 이 문서는 **파이프라인 설계의 출발점**을 기록한 것이다. 실행 결과·현재 수치는
> [total_plan.md](../total_plan.md) 와 각 Phase 문서를 본다.

세 문서의 관계:

| 문서 | 역할 |
|---|---|
| [policy.md](../policy.md) | **무엇을 해야 하는가** — 방법론 |
| [CONVENTION.md](../CONVENTION.md) | **무엇을 할 수 없는가** — 규정·예산 |
| 이 파이프라인 | **그래서 어떤 순서로 무엇을 측정하는가** |

---

## 문서 구성

| # | 문서 | Phase | 크레딧 |
|---|---|---|---|
| 00 | 개요 (이 문서) | — | — |
| 01 | [데이터 EDA](01-eda.md) | A | **0** |
| 02 | [Golden Test Set](02-golden-set.md) | B | **0** |
| 03 | [Graph Schema](03-graph-schema.md) | C | **0** |
| 04 | [Retriever](04-retriever.md) | D | **0** |
| 05 | [Re-ranker](05-reranker.md) | E | 0 (G1 확정 후) |
| 06 | [Agent + Tool](06-agent-tool.md) | F | 소량 |
| 07 | [Verifier](07-verifier.md) | G | **0** |
| 08 | [Error Analysis](08-error-analysis.md) | H | 중간 |
| 09 | [Ablation](09-ablation.md) | I | **대부분** |

> **01~04·07은 크레딧 없이 전부 할 수 있다.** 과제 크레딧 20만원을 기다리는 동안
> policy §12의 1~5번을 끝낼 수 있다는 뜻이다.

---

## 현재 위치 — policy §12 대비

> 🔴 **아래 표는 2026-08-09 착수 시점의 기록이고 갱신하지 않는다.** 그때 무엇이 비어
> 있었는지가 이 문서의 값이다 — "순서가 뒤집혀 있었다" 는 아래 교훈이 이 표 없이는 읽히지
> 않는다. **현재 진행 상황의 정본은 [total_plan §1](../total_plan.md)** 이다
> (🔴 2026-08-14 기준 게이트 **71/71** · 테스트 **953 passed** · Phase A~I 완료 —
> 05 의 게이트 E 는 v3 R1 이 `A3` 를 최종 기각하며 닫았다. 종전 서술은
> "게이트 68/71 · 811 passed · 05 만 의도적 보류" 였다).
> 🔴 **다음 판은 `ablation 실행 v3`** — arm `L4` 신설 · CLOVA 1급 API arm · 생성 arm 후보
> ([v3 계획](../superpowers/plans/2026-08-12-ablation-v3.md)).

| policy 순서 | 상태 (**2026-08-09 착수 시점**) | 산출물 |
|---|---|---|
| 1. Golden Test Set | 🟡 6문항, 필드 미달 | `evalset/questions.jsonl` |
| 2. 공시 데이터 분석 | 🔴 **미실시** | — |
| 3. Graph Schema | 🔴 미착수 | — |
| 4. Retriever | 🟢 BM25 + 메타필터 | exchange 1,469건 |
| 5. Re-ranker | 🔴 미착수 | — |
| 6. Agent + Tool | 🟢 도구 7종 + 파이프라인 | 110 tests |
| 7. Verifier | 🟢 critic | `agent/critic.py` |
| 8. Error Analysis | 🔴 미착수 | — |
| 9. Ablation | 🔴 미착수 | — |

> **순서가 뒤집혀 있다.** policy는 1·2를 먼저 하라는데 우리는 4·6·7을 먼저 만들었다.
> 그 결과가 지금까지의 시행착오다 — `title`이 검색어가 안 되는 문제, `is_latest` 뒤집힘,
> 한글 복합명사, 연월이 랭킹을 좌우한다는 사실을 **전부 사후에** 발견했다.
> **EDA를 먼저 했으면 대부분 예방됐다.**

---

## 연구 대상 선정 — policy §0 × CONVENTION G1

policy는 4개 모듈 중 **1~2개만 연구 대상**으로 고르라고 한다.
그런데 우리 제약에서는 **선택 불가능한 칸이 이미 정해져 있다.**

| 모듈 | policy 권장 | CONVENTION 제약 | 결론 |
|---|---|---|---|
| Retrieval | BGE / E5 | 보조 모델 허용 (Q&A 12) | **연구 대상 ①** |
| Re-ranker | BGE | 보조 모델 허용 | 오픈소스 사용 |
| Inference | GPT / Qwen | 🔴 **HCX-005 고정 — 선택 불가** | **고정** |
| Agent | — | 우리가 설계 | **연구 대상 ②** |

**연구 대상 = Retrieval/GraphRAG + Agent(Planning·Tool).**
Inference는 바꿀 수 없으므로 애초에 연구 대상이 될 수 없다.
policy §11("모델 자체를 학습시키는 연구가 아니라면 Retrieval·Planning·Tool·Evaluation에 집중")과 일치한다.

> ⚠️ **G1 미해결.** 보조 모델 허용 범위는 과제소개자료 p.4와 설명회 Q&A 12가 충돌한다.
> **확정 전에는 임베딩·리랭커에 큰 공수를 넣지 않는다.** Phase D·E를 "붙였다 뗄 수 있는"
> 형태로 설계하는 이유다.

---

## 일정 (D-28)

| 기간 | Phase | 산출물 |
|---|---|---|
| 08-09 ~ 08-12 | **A EDA** + B 착수 | 데이터 프로파일, GraphRAG 채택 판정 |
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
| 🔴 과제 크레딧 미확보 | Phase H·I 불가 | 최우선. 미확보 시 dev 30문항으로 축소 |
| 🔴 G1 미확정 | Phase E 착수 불가 | A2·A3 뺀 ablation으로도 곡선은 나옴 |
| 🔴 periodic 섹션 인덱스 부재 | 정기공시 질의 근거 0건 | A에서 목차 파싱, D에서 완성 |
| 🟡 기업-기업 엣지 희소 | GraphRAG 전제 붕괴 | A-2에서 조기 판정 → 하이브리드로 선회 |
| 🟡 단가 미확정 | Ablation 비용 산정 불가 | 사용 내역 역산 |
| 🟡 heldout 오염 | 일반화 측정 무효 | 최종 1회까지 미개봉 |

---

## policy 원칙 → 문서 매핑

| policy | 대응 |
|---|---|
| §1 데이터 분석이 먼저 | [01-eda](01-eda.md) |
| §2 GraphRAG 적합성 | [01-eda](01-eda.md) A-2가 **전제 자체를 검증** · [03-graph-schema](03-graph-schema.md) |
| §3 Golden Set 먼저 | [02-golden-set](02-golden-set.md) |
| §4 계산은 Tool | [06-agent-tool](06-agent-tool.md) |
| §5 Closed/Open 분리 | [02-golden-set](02-golden-set.md) · [08-error-analysis](08-error-analysis.md) |
| §6 Verifier 분리 | [07-verifier](07-verifier.md) |
| §7 Validation 일반화 | [02-golden-set](02-golden-set.md) 섹터 홀드아웃 |
| §8 Vector Store 설계 | [04-retriever](04-retriever.md) |
| §9 Error Analysis | [08-error-analysis](08-error-analysis.md) |
| §10 Ablation | [09-ablation](09-ablation.md) |
| §11 최신 구조는 참고만 | 연구 대상 한정 (위) |
| §12 개발 순서 | 문서 01~09이 그 순서 |
