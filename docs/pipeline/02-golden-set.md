# Phase B — Golden Test Set

**policy §3 · §5 · §7** · 크레딧 **0** · 선행 [01-eda](01-eda.md) A-3

> 모델보다 Evaluation이 중요하다. 좋은 Evaluation이 좋은 모델을 만든다. — policy §3

**이 문서는 살아있다.** 문항은 Phase D~I 를 돌리며 계속 추가·교정된다 (§B-7).

---

## B-1. 현재 상태 — 미달

`evalset/questions.jsonl` 6문항. policy §3 이 요구하는 필드 중 **4개가 없다.**

| policy 요구 | 현재 | |
|---|---|---|
| 질문 | `question` | ✅ |
| 정답 | `gold` | ✅ |
| 근거 문서 | `must_cite` | 🟡 단수만 |
| **필요한 추론 과정** | — | 🔴 |
| **필요한 Tool** | — | 🔴 |
| **허용 가능한 답변 형태** | — | 🔴 |
| **난이도** | — | 🔴 |

---

## B-2. 스키마 — 축 3개를 독립 필드로

`closed_lookup` 처럼 유형과 답변형식이 뭉친 라벨을 쓰면
[08-error-analysis](08-error-analysis.md) 에서 축별 집계가 불가능하다.

```yaml
question_id: Q-012
question: "HD현대일렉트릭이 2023년 1월에 체결한 변압기 공급계약의 계약금액은?"

# ── 독립 축 3개 (CONVENTION §5·§6) ──────────────────
task_type:   search_extract      # search_extract | multi_compare | complex_reason
answer_mode: closed              # closed | open
difficulty:  하                   # 상 | 중 | 하
# ───────────────────────────────────────────────────

answerable: true                 # false 면 정보한계 대응 문항
answer: "97,000,000,000"
answer_form: number_won          # 판정기를 결정한다 (B-5)
tolerance: 0                     # 수치 허용 오차(%)

evidence:
  - rcept_no: "20230131800162"
    section: null                # periodic 이면 목차 경로
    why: "계약금액 필드"

reasoning_steps:
  - "기업 해소: HD현대일렉트릭"
  - "기간 필터: 202301"
  - "계약금액 필드 조회"

required_tools: [resolve_company, search_chunks]
doc_group: exchange              # exchange | periodic | major | holding
split: dev                       # dev | heldout
source: manual                   # manual | derived | error_analysis
notes: ""
```

**`answer_form` — 판정기를 결정한다**

| 값 | 판정 | 예 |
|---|---|---|
| `number_won` | 정규화 EM (콤마·단위 제거) | `97,000,000,000` |
| `number_pct` | 정규화 EM (소수 1자리) | `5.37` |
| `duration` | 정규화 EM | `1년 9개월` |
| `boolean` | 예/아니오 매핑 | `있다` |
| `list` | 항목 집합 F1 | `유상증자, CB, BW` |
| `narrative` | LLM-as-a-Judge | 서술형 |
| `refusal` | 거부 문구 포함 여부 | `확인되지 않` |

---

## B-3. 난이도 조작적 정의

감으로 붙이면 축이 무의미해진다. **문서 수 · 연산 수 · 이력 추적 여부**로 정의한다.

| 난이도 | 정의 | 실제 예 (우리 코퍼스) |
|---|---|---|
| **하** | 단일 문서 · 단일 필드 · 직접 조회 | "HD현대일렉트릭 2023-01 변압기 계약의 계약금액은?" → `97,000,000,000` |
| **중** | 단일 문서 복수 필드 **또는** 복수 문서 단일 필드 · 1회 연산 | "그 계약의 계약기간은 몇 년 몇 개월인가?" → `1년 9개월` |
| **상** | 복수 문서 종합 · 다단계 연산 · **정정 이력 추적** · 시점 비교 | "삼성전자 반도체 위탁생산 계약이 정정된 뒤 금액이 얼마나 바뀌었나?" → 원본 `20250728800035` vs 정정 `20250731800028` |

> **'상' 문항에 정정 이력을 반드시 포함한다.** 정정이 전체의 24%(1,004건), 거래소공시만 43%(631건)인데
> 현재 6문항에는 정정 문항이 **하나도 없다.**

---

## B-4. 규모와 분포 — 3×2×3 그리드

| task_type | answer_mode | 하 | 중 | 상 | 소계 |
|---|---|---|---|---|---|
| search_extract | closed | 3 | 3 | 3 | 9 |
| search_extract | open | 3 | 3 | 3 | 9 |
| multi_compare | closed | 3 | 3 | 3 | 9 |
| multi_compare | open | 3 | 3 | 3 | 9 |
| complex_reason | closed | 3 | 3 | 3 | 9 |
| complex_reason | open | 3 | 3 | 3 | 9 |
| | | | | **소계** | **54** |

**+ 정보한계 6문항** (범위 밖 기업 2 · 기간 밖 2 · 공시에 없는 정보 2) = **총 60문항**

| 축 | 표본 |
|---|---|
| 난이도 | 상 18 · 중 18 · 하 18 |
| 답변형식 | closed 27 · open 27 |
| 과제유형 | 각 18 |
| 2원 교차 | 9 |
| **3원 교차** | **3 ← 셀 단위 결론 금지** |

**공시 유형** exchange 24 · periodic 24 · major 6 · holding 6

> ⚠️ **periodic 24문항은 `periodic_sections.jsonl` 이 있어야 답이 나온다.**
> 섹션 인덱스가 [04-retriever](04-retriever.md) 에서 완성될 때까지 **작성만 하고 측정에서 제외**한다.

---

## B-5. Closed / Open 판정기 분리 — policy §5

| | 판정 | 추가 지표 | 비용 |
|---|---|---|---|
| **closed** | `answer_form` 별 정규화 EM | Self-Consistency 일치율 | **0** |
| **open** | LLM-as-a-Judge | Groundedness · Evidence Score | judge 1회 |

**judge 프롬프트 규격** — 판정이 흔들리면 측정이 무의미하다.

```
[근거] {evidence}
[질문] {question}
[모범답안 요소] {answer_key_points}
[제출답변] {answer}

각각 0/1 로 판정하고 JSON 한 줄로만 답하라.
  covers_key_points : 모범답안 요소를 빠짐없이 포함했는가
  grounded          : 모든 서술이 근거에서 확인되는가
  no_fabrication    : 근거에 없는 내용을 사실처럼 쓰지 않았는가
{"covers_key_points":0/1,"grounded":0/1,"no_fabrication":0/1,"reason":"한 문장"}
```

open 문항은 `answer` 대신 **`answer_key_points: [str]`** 를 채운다.

---

## B-6. Train / Held-out 분리 — policy §7

**기업이 아니라 섹터 단위로 홀드아웃한다.** 기업만 나누면 같은 섹터의 서식·용어를 이미 본 셈이라
일반화 측정이 되지 않는다.

```
dev     : 16개 섹터 (약 56개사)  ← 프롬프트·규칙 튜닝은 여기서만
heldout :  4개 섹터 (약 14개사)  ← 최종 측정 1회만
```

| 홀드아웃 섹터 | 왜 |
|---|---|
| 바이오·제약 (6) | 임상 단계·기술이전 — 서식이 가장 이질적 |
| 금융·보험 (8) | NIM/CSM·충당금 — 지표 체계가 다름 |
| 조선·건설·방산 (4) | 수주잔고 — 누적 개념 |
| 엔터테인먼트 (4) | 부문별 매출·아티스트 계약 |

> 🔴 **홀드아웃을 보고 튜닝하면 그 순간 홀드아웃이 아니다.**
> `evalset/heldout.jsonl` 로 분리하고 평가 스크립트에서 기본 제외한다.

---

## B-7. 작성 절차와 살아있는 갱신

### 절차

1. **재료 수집** — [01-eda](01-eda.md) A-3 매핑에서 각 셀을 채울 실제 레코드를 뽑는다
2. **문항 초안** — 레코드에서 역으로 질문을 만든다 (답을 먼저 알고 질문을 쓴다)
3. **자동 검증** — 아래 항목
4. **교차 검토** — 난이도 라벨이 B-3 정의와 맞는지

### 자동 검증

```
□ evidence[].rcept_no 가 manifest 에 실재
□ answer 가 answer_form 형식과 일치
□ answerable=false 면 answer_form == refusal
□ required_tools 가 TOOL_SPECS 에 존재
□ 3×2×3 셀 카운트가 그리드와 일치
□ heldout 문항의 corp_name 이 홀드아웃 섹터에 속함
□ dev/heldout 간 기업 중복 없음
```

### 갱신 규칙 — 문항은 늘어난다

| 계기 | 추가 문항 | `source` |
|---|---|---|
| [08](08-error-analysis.md) 새 실패 유형 발견 | 그 유형을 재현하는 문항 | `error_analysis` |
| [09](09-ablation.md) arm 간 차이가 안 보임 | 변별력 있는 문항 | `derived` |
| 주최 측 추가 공지 | 해당 유형 | `manual` |

**그리드 균형을 깨지 않는다.** 셀당 3 → 4 로 올릴 때는 18셀 전부 올린다.
한 셀만 늘리면 축별 비교가 오염된다.

---

## 게이트 B

- [ ] 60문항, 전 필드 채움
- [ ] `evidence[].rcept_no` 자동 검증 통과
- [ ] 3×2×3 셀 카운트 일치
- [ ] **'상' 문항에 정정 이력 추적 포함**
- [ ] open 문항에 `answer_key_points` 존재
- [ ] dev/heldout 섹터 분리, **heldout 미개봉**
