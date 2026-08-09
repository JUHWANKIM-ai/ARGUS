# ARGUS 총괄 계획 — TOTAL PLAN

**기준일 2026-08-09 · 제출 마감 09-06 (D-28) · 평가 09-07 ~ 09-20**

이 문서가 **단일 진실 공급원**이다. 9개 파이프라인 문서의 진행상황·게이트·차단요인을 여기서 총괄한다.

> 🔄 **살아있는 문서.** Phase 를 돌리며 새 병목·새 구성요소가 나오면
> [09-ablation](pipeline/09-ablation.md) 의 arm 과 [02-golden-set](pipeline/02-golden-set.md) 의 문항이
> **계속 추가된다.** 그때마다 이 문서의 게이트와 진행률도 갱신한다.

---

## 1. 진행 현황

### 완료 (구현)

| 항목 | 결과 |
|---|---|
| CLOVA 연결 검증 | `/testapp/v3/chat-completions/HCX-005` + Bearer · function calling 확인 |
| **CoT 게이트** | Direct 50% vs **CoT 83.3%** (+33.3%p) — 통과 |
| 문자/토큰 실측 | **1.722** → 코퍼스 385M 토큰 |
| NCP 서버 | 생성·ACG·SSH·인바운드·아웃바운드·서버에서 HCX 호출 검증 후 **정지** |
| 요금제 | 손익분기 720h → **시간 요금제** 확정, 공인 IP 반납 |
| 기반 구현 (Task 1~13) | 서식 94.7% · 정정체인 61.2% · 인덱스 1,469건 |
| 알고리즘 구현 (Task 0~9) | 골든순위 **14/1,332 → 1/1** · **110 tests** |
| 테스트 API 차단 | `conftest` 전역 fixture — 뚫어보기까지 검증 |

### 파이프라인 진행률

| # | 문서 | Phase | 상태 | 크레딧 | 게이트 |
|---|---|---|---|---|---|
| 00 | [overview](pipeline/00-overview.md) | — | ✅ | — | — |
| 01 | [eda](pipeline/01-eda.md) | A 데이터 EDA | 🔴 **미착수** | **0** | 0/4 |
| 02 | [golden-set](pipeline/02-golden-set.md) | B Golden Set | 🟡 6/60문항 | **0** | 0/6 |
| 03 | [graph-schema](pipeline/03-graph-schema.md) | C Graph Schema | 🔴 미착수 | **0** | 0/4 |
| 04 | [retriever](pipeline/04-retriever.md) | D Retriever | 🟡 exchange만 | **0** | 1/4 |
| 05 | [reranker](pipeline/05-reranker.md) | E Re-ranker | ⏸ G1 대기 | 0 | 0/3 |
| 06 | [agent-tool](pipeline/06-agent-tool.md) | F Agent+Tool | 🟡 계산Tool 미노출 | 소량 | 1/5 |
| 07 | [verifier](pipeline/07-verifier.md) | G Verifier | 🟡 계산검증 없음 | **0** | 1/3 |
| 08 | [error-analysis](pipeline/08-error-analysis.md) | H Error Analysis | 🔴 미착수 | 중간 | 0/5 |
| 09 | [ablation](pipeline/09-ablation.md) | I Ablation | 🔴 미착수 | **대부분** | 0/6 |

**게이트 총계 3 / 40**

---

## 2. 지금 당장 — 크레딧 없이 가능

**01~04 · 07 은 크레딧 0이다.** 과제 크레딧을 기다리는 동안 policy §12 의 1~5번을 끝낼 수 있다.

| 우선순위 | 작업 | 문서 | 왜 지금 |
|---|---|---|---|
| **1** | **데이터 EDA** | [01](pipeline/01-eda.md) | 모든 판단의 입력. **GraphRAG 채택 여부**가 여기서 결정 |
| **2** | Golden Set 60문항 | [02](pipeline/02-golden-set.md) | EDA A-3 매핑이 재료를 준다 |
| **3** | periodic 섹션 인덱스 | [04](pipeline/04-retriever.md) | **정기공시 질의 근거 0건** — 최대 공백 |
| **4** | 계산 Tool 노출 | [06](pipeline/06-agent-tool.md) | **policy §4 위반 상태** |
| **5** | 계산 검증 추가 | [07](pipeline/07-verifier.md) | LLM 0회 |
| 6 | Graph Schema | [03](pipeline/03-graph-schema.md) | EDA A-2 판정 후 |

---

## 3. 차단 요인

| 심각도 | 항목 | 막히는 것 | 담당 |
|---|---|---|---|
| 🔴 | **과제 크레딧 20만원 미확보** — 웰컴 10만원뿐 | Phase H·I 전체 | 사용자 |
| 🔴 | **G1 미확정** — 보조모델 허용 범위 | Phase E · arm A2·A3 | 사용자 (디스코드) |
| 🔴 | **HTTPS 대안 미공지** | 8월 말 배포 리허설 | 주최 측 대기 |
| 🟡 | 토큰 단가 미확정 | Ablation 비용 산정 | 사용자 (사용 내역 역산) |
| 🟡 | API 키 채팅 노출 | 보안 | 사용자 (재발급) |
| 🟡 | `periodic_sections.jsonl` 부재 | 정기공시 질의 전부 | Phase D |
| 🟡 | 정정본 정책 미정 | 골든이 `is_latest=False` 일 수 있음 | Phase C |

---

## 4. 일정 (D-28)

| 기간 | Phase | 산출물 | 게이트 |
|---|---|---|---|
| 08-09 ~ 08-12 | **A EDA** + B 착수 | 데이터 프로파일 · **GraphRAG 판정** | A 4개 |
| 08-13 ~ 08-16 | B 완료 + **C** | 60문항 · Node/Edge 확정 | B 6 · C 4 |
| 08-17 ~ 08-22 | **D** + F 보강 | Recall@5 ≥ 0.85 · 계산 Tool · periodic 섹션 | D 4 · F 5 |
| 08-23 ~ 08-26 | G + **H** | 병목 특정 | G 3 · H 5 |
| 08-27 ~ 08-31 | **I Ablation** + 리허설 | 기여도 곡선 · 제출 구성 | I 6 |
| 09-01 ~ 09-05 | 기술제안서 + 최종 | heldout 1회 | — |
| **09-06** | **제출** | 소스·제안서·엔드포인트 | — |

---

## 5. 살아있는 문서 원칙

**Ablation 과 Golden Set 은 완성되지 않는다.** 작업하며 계속 자란다.

```
Phase 실행 → 새 실패 유형 발견 → 골든셋에 재현 문항 추가 (source: error_analysis)
                             → ablation 에 대응 arm 추가
                             → total_plan 게이트·진행률 갱신
```

| 계기 | 갱신 대상 |
|---|---|
| 새 병목 발견 | [09](pipeline/09-ablation.md) arm 추가 + 부록 근거 |
| arm 간 Δ 가 0 | [02](pipeline/02-golden-set.md) 변별 문항 추가 |
| 새 논문·기법 | [09](pipeline/09-ablation.md) 부록 A~D |
| G1·크레딧 확정 | 이 문서 §3 + [09](pipeline/09-ablation.md) §I-5 |

**갱신 시 그리드 균형을 깨지 않는다** — 골든셋은 18셀 전부 같이 올린다.

---

## 6. 참조

| 문서 | 역할 |
|---|---|
| [policy.md](policy.md) | 방법론 — 무엇을 해야 하는가 |
| [CONVENTION.md](CONVENTION.md) | 규정·예산 — 무엇을 할 수 없는가 |
| [clova_payment.md](clova_payment.md) | 크레딧 운용 방침 |
| [pipeline/](pipeline/) | Phase A~I 실행 문서 9종 |
| [paper/paper_guideline.md](../paper/paper_guideline.md) | 논문 17편 지도 |
| [figures/argus-algorithm-v2.drawio](figures/argus-algorithm-v2.drawio) | 아키텍처 도면 |
| 구현 계획 | [기반](superpowers/plans/2026-08-08-argus-local-foundation.md) · [알고리즘](superpowers/plans/2026-08-08-argus-algorithm.md) |
