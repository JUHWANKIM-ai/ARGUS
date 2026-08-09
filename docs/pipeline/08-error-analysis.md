# Phase H — Error Analysis

**policy §9** · 크레딧 중간 · 선행 [02-golden-set](02-golden-set.md) · [06-agent-tool](06-agent-tool.md)

> 평가 점수보다 어디서 실패했는지가 중요하다. 병목을 찾아야 개선 방향이 명확해진다. — policy §9

**이 문서의 출력이 [09-ablation](09-ablation.md) 의 입력이다.** 병목을 모르면 어느 arm 을 추가할지 못 정한다.

---

## H-1. 단계별 실패 귀속

각 문항의 실패를 **가장 앞선 단계에 귀속**시킨다. 뒤 단계는 앞이 실패하면 자동으로 실패하므로,
첫 실패 지점만 세야 병목이 보인다.

| 실패 유형 | 판정 기준 | LLM 필요? |
|---|---|---|
| `RETRIEVAL` | 골든 `rcept_no` 가 후보 top-k 에 없음 | ❌ 무료 |
| `RANKING` | top-k 엔 있으나 상위 3위 밖 | ❌ 무료 |
| `TOOL` | 필요 도구 미호출 **또는** 계산값이 결정적 재계산과 불일치 | ❌ 무료 |
| `REASONING` | 근거는 맞는데 답이 틀림 | closed ❌ / open ✅ |
| `CITATION` | 답은 맞는데 접수번호 표기 없음 | ❌ 무료 |
| `REFUSAL` | 답할 수 있는데 거부 · 없는데 답함 | ❌ 무료 |

**6종 중 5종이 골든셋 대조만으로 판정된다.** open 문항의 `REASONING` 만 judge 가 필요하다.

---

## H-2. 축별 교차 집계 — 핵심

병목은 **전체 평균이 아니라 축별로 다르게** 나타난다.

```
① 난이도   × 실패단계    난이도가 올라갈수록 어디서 깨지는가
② 답변형식 × 실패단계    closed 와 open 의 실패 원인이 다른가
③ 과제유형 × 실패단계    복합추론이 검색에서 깨지는가 추론에서 깨지는가
```

| | RETRIEVAL | RANKING | TOOL | REASONING | CITATION | REFUSAL | 정답률 |
|---|---|---|---|---|---|---|---|
| 하 (18) | | | | | | | |
| 중 (18) | | | | | | | |
| 상 (18) | | | | | | | |
| closed (27) | | | | | | | |
| open (27) | | | | | | | |
| search_extract (18) | | | | | | | |
| multi_compare (18) | | | | | | | |
| complex_reason (18) | | | | | | | |

### 해석 규칙 → arm 연결

| 관측 | 원인 | 대응 arm |
|---|---|---|
| 난이도↑ 할수록 `RETRIEVAL` 증가 | 검색 리콜 부족 | **A2 임베딩** |
| 난이도 무관 `RANKING` 높음 | 순위 품질 | **A3 리랭커** |
| `상` 에서만 `REASONING` 급증 | 다단계 추론 한계 | **A4 Agent 루프** |
| `closed` 가 `TOOL` 에서 깨짐 | **policy §4 위반** — 계산이 LLM 으로 샘 | **A5 계산 Tool** |
| `open` 만 `CITATION` 나쁨 | 서술형 근거 표기 강제 부족 | 프롬프트 · **A6 Verifier** |
| `REFUSAL` 양방향 오류 | 정보한계 임계 오설정 | 라우터 임계 조정 |

---

## H-3. Closed / Open 채점 분리 — policy §5

| | 정답 판정 | 추가 지표 |
|---|---|---|
| **closed** | `answer_form` 별 정규화 EM | Self-Consistency 일치율 |
| **open** | LLM-as-a-Judge ([02](02-golden-set.md) B-5 규격) | Groundedness · Evidence Score |

**두 축을 합산한 단일 점수를 만들지 않는다.** 합치면 어느 쪽이 나쁜지 사라진다.

---

## H-4. 산출물

| 파일 | 내용 |
|---|---|
| `artifacts/error_analysis.json` | 문항별 `first_failure` + 3축 라벨 + 호출·토큰 |
| `artifacts/error_matrix.md` | 교차표 3종 |
| `evalset/questions.jsonl` | **새 실패 유형 재현 문항 추가** (`source: error_analysis`) |

---

## 게이트 H

- [ ] 60문항 전부 `first_failure` 귀속
- [ ] 교차표 3종 산출
- [ ] **병목 1위가 축별로 특정됨** → [09](09-ablation.md) arm 우선순위
- [ ] closed/open 이 서로 다른 지표로 채점됨
- [ ] 발견된 실패 유형이 골든셋에 환류됨
