# 공시 Agent 개발 전략 및 연구 방향

> 원본: `scratch/policy.txt` (2026-08-09).
> 텍스트 깨짐이 있던 구간(§0 예시 · §5 제목 · §7 말미 · §8 제목 · §10 목록 · §11 · 철학)은
> 문맥에 맞게 복원했다. 내용은 바꾸지 않았다.

## 핵심 원칙

> **공시 Agent의 성능은 더 좋은 LLM보다 더 좋은 데이터 구조, 평가셋, 검색 전략, Tool 설계에서 결정된다.**

모델은 마지막 20%를 개선하지만, 데이터와 평가 설계는 처음 80%를 결정한다.

---

## 0. 전체 Framework

공시 Agent는 크게 4개의 모듈로 구성된다.

```text
Query
   ↓
Retrieval
   ↓
Re-ranker
   ↓
Inference (Reasoning)
   ↓
Agent (Planning / Tool Calling)
```

공모전 기간은 제한되어 있으므로 **모든 모듈을 동시에 개선하려 하지 않는다.**
Retrieval · Re-ranker · Inference · Agent 중 **1~2개만 연구 대상으로 선정**하고,
나머지는 검증된 오픈소스를 쓰는 것이 현실적이다.

예시

- Retrieval : BGE / E5
- Re-ranker : BGE
- Inference : GPT / Qwen
- **연구 대상 : Agent 또는 GraphRAG**

---

## 1. 데이터 분석이 가장 먼저다

모델보다 먼저 해야 하는 일은 데이터 분석이다.

분석 항목

- 문서 길이
- 표(Table) 비율
- 숫자 데이터 비율
- 시간 정보 포함 여부
- 문서 간 참조 관계
- 기업 간 관계
- 동일 기업의 연속 공시 구조

데이터 특성을 먼저 이해해야 **Vector RAG / Hybrid Search / GraphRAG** 중
어떤 방식이 적합한지 판단할 수 있다.

---

## 2. DART 공시는 GraphRAG와 잘 맞는다

공시는 단순한 텍스트가 아니라 **관계형 데이터**다.

```text
기업
 ├─ 사업보고서
 ├─ 투자
 ├─ 계약
 ├─ 자회사
 ├─ 임원
 ├─ 지분
 ├─ 재무
 └─ 공시
```

GraphRAG의 핵심은 모델이 아니라 **Node 정의 · Edge 정의 · Graph Schema 설계**다.

| 구분 | 예시 |
|---|---|
| Node | 기업 · 계약 · 임원 · 공시 · 재무항목 · 투자 |
| Edge | 투자했다 · 소유한다 · 대표이다 · 계약했다 · 포함한다 |

**여기에 가장 많은 시간을 투자해야 한다.**

---

## 3. Golden Test Set을 가장 먼저 만든다

최근 LLM 개발에서는 **모델보다 Evaluation이 중요하다.**

Golden Test Set에는 다음까지 정의한다.

- 질문
- 정답
- 근거 문서
- 필요한 추론 과정
- 필요한 Tool
- 허용 가능한 답변 형태

```yaml
Question: 삼성전자의 최근 시설투자 규모는?
Retriever: 사업보고서
Tool:      금액 계산
Answer:    숫자
Citation:  사업보고서 페이지
```

**좋은 Evaluation이 좋은 모델을 만든다.**

---

## 4. 계산은 Tool Calling으로 처리한다

LLM에게 계산을 맡기지 않는다. CAGR · 증가율 · 평균 · 합계 · 비율 · 순위 등은 Tool에서 계산한다.

LLM은 **Tool 선택**과 **계산 결과 설명**만 담당한다.

```
LLM  = Reasoning
Tool = Calculation
```

---

## 5. 질문 유형을 먼저 분류한다

### Closed Question — 정답이 명확한 질문

> 2024년 매출은?

평가: Accuracy · Exact Match · Self-Consistency · Voting

### Open-ended Question — 정답이 하나가 아닌 질문

> 왜 최근 투자 규모가 증가했는가?

평가: F1 · LLM-as-a-Judge · Evidence Score · Groundedness

**Closed와 Open은 평가 방식을 분리해야 한다.**

---

## 6. Verifier Agent를 둔다

Answer 생성 Agent와 검증 Agent를 분리한다.

```text
Planner → Retriever → Reasoner → Verifier → Final Answer
```

Verifier는 **근거 존재 여부 · Hallucination · 계산 오류 · 출처 누락**을 검사한다.

---

## 7. Validation은 실제 서비스처럼 만든다

Validation을 Train과 동일한 분포로 만들지 않는다.

| Train | Validation |
|---|---|
| 기업 A · 기업 A · 기업 A | 기업 B · 기업 C · 새로운 질문 |

실제 서비스는 항상 새로운 질문이 들어오므로, **Validation도 일반화 성능을 측정하도록** 설계해야 한다.

---

## 8. Vector Store 설계도 모델만큼 중요하다

고려할 요소

- Chunk Size · Chunk Overlap
- Parent-Child Chunk
- Table 분리 여부
- Metadata
- Hybrid Search (BM25 + Vector)
- Embedding Model

같은 모델이라도 **Chunk 전략에 따라 성능이 크게 달라진다.**

---

## 9. Error Analysis가 가장 중요하다

평가 점수보다 **어디서 실패했는지**가 중요하다.

```text
Retriever 실패 → Re-ranker 실패 → Reasoning 실패 → Tool 실패
```

**병목을 찾아야 개선 방향이 명확해진다.**

---

## 10. Ablation Study를 설계한다

```text
Vector RAG
   ↓
GraphRAG
   ↓
GraphRAG + Re-ranker
   ↓
GraphRAG + Agent
   ↓
GraphRAG + Agent + Tool
```

모듈을 하나씩 추가하며 **각 요소가 성능 향상에 얼마나 기여하는지** 검증한다.

---

## 11. 최신 LLM 구조는 참고만 한다

Gemma PLE, DeepSeek MLA 등은 **모델 자체를 새로 학습시키는 연구가 아니라면 우선순위가 높지 않다.**
현 단계에서는 **Retrieval · Planning · Tool Calling · Evaluation** 에 집중하는 것이 효과적이다.

---

## 12. 추천 개발 순서

1. Golden Test Set 구축
2. 공시 데이터 분석
3. Graph Schema (Node / Edge) 설계
4. Retriever 구축
5. Re-ranker 구축
6. Agent + Tool Calling 구축
7. Verifier Agent 구축
8. Error Analysis
9. Ablation Study

---

## 향후 연구 아이디어

| 모듈 | 아이디어 |
|---|---|
| Retrieval | Hybrid Search · Adaptive Retrieval · Query Rewriting |
| Re-ranker | Domain-specific Re-ranking · Multi-hop Ranking |
| Inference | Self-Consistency · Tree of Thoughts · Graph of Thoughts |
| Agent | Planner · Tool Calling · Verifier · Reflection · Multi-Agent |

---

## 프로젝트 철학

> **LLM은 답을 외우는 모델이 아니라, 적절한 정보를 찾고(Search), 필요한 도구를 호출하며(Tool Calling),
> 근거를 기반으로 추론(Reasoning)하는 시스템으로 설계해야 한다.**

```
좋은 LLM Agent  ≠  큰 모델

                =  좋은 데이터 구조
                +  좋은 Retrieval
                +  좋은 Evaluation
                +  좋은 Tool 설계
                +  좋은 Agent Planning
```
