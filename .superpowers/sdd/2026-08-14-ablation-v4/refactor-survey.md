# 리팩토링 대상 조사 — v4 코드 동결(08-25) 전 정리

**날짜** 2026-08-15 · **LLM 호출** 0회 · **비용** 0원 · **성격** 🔴 **조사 전용 — 코드 한 줄도 안 고쳤다**

v4 에서 arm 을 열셋 세우고 열셋을 기각하는 동안 코드가 자랐다. 이 보고서는 **무엇을 어떤
순서로 정리할 수 있는가**를 잰다. 실행은 하지 않는다 — `pipeline.py`·`critic.py`·
`ablation.py` 를 다른 에이전트가 동시에 고치는 중이라 **줄 번호가 아니라 심볼 이름으로만**
인용한다.

```
측정 시점의 사실
  테스트          1,239 passed in 24.92s   (pytest -q · 실패 0)
  arm             28   (LADDER 8 · LOO 7 · 진단 1 · 곁가지 12)
  플래그          26   (`ablation.FLAG_NAMES`)
  운영 기본값     `ablation.current() == A20r`
```

🔴 **결론 먼저.** 이 저장소는 흔히 말하는 "죽은 코드가 쌓인" 상태가 **아니다.** 전수로
훑은 결과 참조 0인 모듈 수준 심볼은 **한 개도 없고**, 안 읽히는 플래그도 **없다**. 자란
것은 ① **한 파일이 맡은 일의 수**와 ② **저장소 전체에 흩어진 금액·숫자 파싱의 사본**이다.
그리고 조사 중에 **리팩토링이 아니라 결함**인 것을 하나 찾았다 (§3-1 — 운영 기본값 `A20r`
의 상류 장애가 러너의 캐시 가드를 통과한다).

---

## 1. 파일별 규모와 한 문장 책임

### 1-a. 규모 — 🔴 "줄 수" 로 읽으면 틀린다

|파일|전체|**코드**|주석·독스트링|함수|최대 함수|
|---|---:|---:|---:|---:|---|
|`scripts/ablation.py`|3,218|**2,190** (68%)|725 (22%)|70|🔴 `render_gate` **933줄**|
|`src/argus/agent/pipeline.py`|2,555|**1,078** (42%)|1,114 (43%)|61|`_gather_evidence` 266줄|
|`src/argus/agent/critic.py`|1,519|**750** (49%)|548 (36%)|51|`verify_calculation` 158줄|
|`src/argus/ablation.py`|1,078|**163** (15%)|778 (**72%**)|14|`__post_init__` 46줄|

🔴 **`src/argus/ablation.py` 는 큰 파일이 아니다.** 1,078줄 중 코드가 163줄이고 나머지는
판정의 물증(모듈 머리말 252줄 + 필드별 근거 주석)이다. **쪼개면 안 되는 파일**이다 —
그 주석이 곧 "왜 이 arm 을 이 자리에 세웠는가" 의 정본이다.

🔴 **가장 무거운 것은 `scripts/ablation.py` 다.** 코드가 2,190줄이고 그중 **933줄이
`render_gate` 함수 하나**다. 그 안에 중첩 헬퍼가 **0개**이고 `# --- N. …` 주석으로만
14개 절이 갈려 있다.

### 1-b. 한 문장 책임 — 🔴 못 적은 파일 셋

**`src/argus/ablation.py` — ✅ 적을 수 있다 (단 한 군데 예외)**
> "arm 의 구성을 정의하고, 지금 어느 arm 인지를 들고 있다."

예외는 `shape_evidence`·`_record_fields`·`_EOK_SUFFIX`·`_DURATION_SUFFIX`·`_BLOCK_SPLIT`
이다. **이것은 스위치가 아니라 근거 문자열 성형(정규식 텍스트 변환)** 이고, 이 모듈이
`argus.agent.tools` 를 **함수 안에서 늦게 import** 하는 유일한 이유이기도 하다.

**`src/argus/agent/critic.py` — ❌ 못 적는다. 다섯 가지를 한다.**
> ① 결정적 검사 4종(`verify_calculation`·`verify_answer_arithmetic`·
> `verify_number_provenance`·`verify_completeness`)
> ② 🔴 **금액·비율·배율 파싱 라이브러리**(`_money_spans`·`money_values`·`_scale_of`·
> `_to_float`·`_bare_claims`) — 저장소가 "단일 원천" 으로 선언한 자리
> ③ 🔴 **답변을 고쳐 쓰는 기계**(`substitute_recomputed`·`_rendered`·`unique_recomputed`·
> `tool_result_reports`) — 판정이 아니라 **변경**이다
> ④ LLM 판정자(`judge`·`judge_open`)
> ⑤ 🔴 **오프라인 채점·집계 헬퍼**(`aggregate_calculation`·`completeness_audit`) —
> `scripts/error_analysis.py` 가 쓴다
>
> 모듈 머리말은 아직 *"4단 검증"* 이라고 적혀 있다. **머리말이 낡았다.**

**`src/argus/agent/pipeline.py` — ❌ 못 적는다.** 높이를 올리면 한 문장이 되지만
("질문을 받아 검색·생성·검증을 거쳐 5키를 낸다") 그 문장은 **아무것도 배제하지 않는다.**
실제로는 검색 차선 **넷**(메타 전용 / 정기공시 섹션 / exchange BM25 사다리 / 발주처 2-hop)
+ 골든 주입 차선 + 생성 + 도구 왕복 + 자기일관성 + 검증·재생성·치환 + 트레이스 파서
(`parse_hops`·`upstream_failure`)를 한 모듈이 한다.

**`scripts/ablation.py` — ❌ 못 적는다. 아홉 가지를 한다.** (`# --- ` 최상위 절 그대로)
> ① 요청 캐시(HTTP 계층) ② 입력 적재(dev·heldout·오라클 골든 주입) ③ arm 실행 + heldout
> 원장 + 지출 정산 ④ judge 재현 편차 σ ⑤ 검색 단계만 재기 ⑥ 집계·요약 렌더 ⑦ 게이트 I
> 보고서 렌더 ⑧ 전 세대 재채점 ⑨ CLI

---

## 2. 중복 — 전수 조사

### 2-a. 🔴 이미 통일돼 있는 것 — **손대지 말 것**

조사 지시가 의심한 자리 둘은 **이미 한 곳으로 모여 있었다.** 확인만 하고 지나간다.

```
치환 로직   A20(substitute_verified) 과 A24b(substitute_tool_results) 가
            `critic.substitute_recomputed` · `unique_recomputed` · `_targets` ·
            `_recomputed` · `_rendered` 를 **공유**한다.
            `pipeline._substitute_tool_results` 는 얇은 래퍼일 뿐이다.
            ✅ 중복 아님. `tests/test_function_calling.py` 가 그 공유를 고정한다.

분모 계산   `scripts/ablation._cohort_rate` · `_grid_rate` 가 둘 다
            `error_analysis._rate` 로 위임한다(= `DATA_LIMIT` 를 분모에서 빼는
            유일한 자리). `_exact_rate` 가 반올림 없는 나눗셈의 단일 원천이고
            `scripts/ablation_report.py` 가 그것을 import 한다.
            ✅ 규칙 자체는 통일돼 있다. (서식 헬퍼만 사본 — 2-b ④)

R3b        `covers_key_amounts`·`key_point_amounts` 는 `scripts/error_analysis.py`
            에만 있고 `critic.money_values` 로 위임한다. ✅ 통일 확인.
```

### 2-b. 🔴 실제 중복 — 열 자리

**① 금액 정규식 · 배율표 — 🔴 최대 6벌 (가장 큰 자리)**

|자리|심볼|성격|
|---|---|---|
|`src/argus/agent/critic.py`|`_MONEY_RE`·`_SCALE`·`_SCALE_UNITS`·`_scale_of`·`_money_spans`|선언된 단일 원천|
|`scripts/error_analysis.py`|`_MONEY_RE`·`_SCALE`·`_numbers`·`_num_hit`|🔴 `critic._SCALE`·`_SCALE_ALT` 를 **import 한 뒤 자기 정규식을 다시 만든다** (표지가 선택적) + 추출 루프를 다시 쓴다|
|`src/argus/corpus/normalize.py`|`_MAGNITUDE`·`_FREE_AMOUNT`·`_compound_tail`|독립 5단위 표|
|`scripts/eda_documents.py`|`_AMOUNT_IN_TEXT`·`_ANY_MONEY`|독립 단위 목록 (`천만원` 포함 — critic 은 의도적으로 제외)|
|`scripts/build_site.py`|`_AMOUNT`·`_NUM`·`_won`|맨숫자|
|`scripts/build_nary.py`|`_NUM_RE`·`_DIGITS`|맨숫자·`\D` 제거|

🔴 **`critic` ↔ `error_analysis` 통합은 동작을 바꾼다.** `error_analysis._numbers` 는
배율 표지를 **선택적**으로 두고 **환산값과 원값을 둘 다 후보로** 낸다. `_money_spans` 로
갈아끼우면 후보 집합이 줄어 **채점이 뒤집힐 수 있다.** → 규율 ①에 걸린다. **통합 금지,
주석으로 갈래를 명시**하는 것까지만.

**② 숫자 정규화 — `critic._digits` 와 `build_nary._DIGITS` 가 논리 동일**
(`re.sub(r"\D","",…)`). `normalize._to_number` ↔ `build_site._won` 도 근사 동일.
🔴 `critic._to_float`(엄격 fullmatch)만은 통합하면 안 된다 — `_bare_claims`·`_rendered`
가 부분 토큰을 안 집는 이유가 그 엄격함이다.

**③ 🔴 검사 반환 규격 — 다섯 곳이 같은 사전을 손으로 짓는다 (공장 없음)**

주석은 *"같은 반환 규격"* 이라고 적어 뒀다. **실제로 재 보니 셋만 같다.**

```
verify_answer_arithmetic   ok llm_calls checked skipped mismatches failed_kinds
verify_number_provenance   hard_kinds failure_stage reason recomputed          (10키)
_tool_result_report        ← 셋이 **완전히 같은 키 집합**

verify_calculation         위 10키 + `gated`                                    (11키)
verify_completeness        ok llm_calls checked skipped **gaps** failed_kinds
                           failure_stage reason                                 (8키 · 다른 규격)
```

🔴 **`verify_completeness` 는 같은 규격이 아니다** — `mismatches` 대신 `gaps`,
`recomputed`·`hard_kinds` 없음. 주석의 "셋" 이라는 표현이 어느 셋인지 모호하다.
공통 생성자(`_report(...)`)가 없어 **다섯 곳에 키를 손으로 적고 있고**, 새 검사를 붙일 때
키를 하나 빠뜨리면 `_verdict` 가 `KeyError` 대신 조용히 다른 판정을 낸다.

**④ 서식 헬퍼가 바이트 동일로 복사돼 있다**
`scripts/ablation_report.py::pct` ≡ `scripts/ablation.py::_pct`,
`scripts/ablation_report.py::delta` ≡ `scripts/ablation.py::_pp`.
🔴 같은 파일이 `_exact_rate` 는 이미 import 하고 있다 — **정밀도 규칙만 모으고 서식은
복사한** 상태다. 통합은 동작 중립(본문 동일).

**⑤ 🔴 상류 장애 판정이 두 벌이다 — 한 벌은 아무도 안 쓴다** → §3-1 에서 따로 다룬다.

**⑥ `_top_level` — 🔴 함수 본문 바이트 동일**
`src/argus/corpus/html_doc.py::_top_level` ≡ `src/argus/corpus/sections.py::_top_level`
(16줄). 받침 상수 `_TR_OPEN`·`_TR_CLOSE`·`_CELL_OPEN`·`_CELL_CLOSE`·`_COLSPAN` 도 양쪽에
동일. 통합 **동작 중립**. (`_row_cells` 는 인자가 달라 그대로 둔다.)

**⑦ HTML → 평문 — 네 벌**
`html_doc._clean` · `corrections._text` · `eda_documents._clean_cell` ·
`eda_relations.rows_with_units` 내부. 전부
`_WS.sub(" ", unescape(_TAG.sub(" ", x))).strip()`. 통합 동작 중립.

**⑧ BM25 토크나이저 — `retrieve/bm25.py::_TOKEN` ≡ `agent/query.py::_TOKEN`** (바이트 동일).
상수 공유는 동작 중립이나, **소비 방식이 달라**(bm25 는 bigram, query 는 조사 제거)
파이프라인은 갈라 둬야 한다.

**⑨ 테스트 픽스처 `ensure_counterparty_graph` — 세 벌 바이트 동일**
`tests/test_ablation.py` · `tests/test_pipeline.py` · `tests/test_router.py` (독스트링까지
동일). `tests/test_tools.py` 는 **`cache_clear` 가 빠진 축약본**이고 그 누락이 곧 순서
의존 취약점이다. `conftest.py` 로 올리면 동작 중립 + 취약점 하나 제거.

**⑩ 테스트 스냅샷 로더 `_snapshot` — 다섯 벌**
`tests/test_calc_checks.py` · `tests/test_critic.py`(**한 파일에 두 벌**: `_snapshot` ·
`_a8_snapshot`) · `tests/test_prompt_rules.py`(두 곳 인라인). 핵심 8줄이 바이트 동일.

**⑪ 🔴 잠복 결합 — `- 라벨: 금액원 (억원)` 서식**
생산자 셋(`corpus/markdown._amount_line` · `agent/tools._nary_amount_line` ·
`agent/compute.fmt_won`)과 소비 정규식 둘(`prompts._EOK_ANNOTATION` ·
`ablation._EOK_SUFFIX`)이 **코드가 아니라 주석으로만 묶여 있다.** 생산자 한 곳의 반올림을
바꾸면 `shape_evidence`(= `calc_values` 축 = A5·L2 의 Δ)가 조용히 깨진다.
🔴 **이 저장소에서 가장 위험한 미확정 결합이다.** 통합보다 먼저 **계약 테스트**가 답이다.

---

## 3. 죽은 코드 — 후보와 🔴 물증의 구분

### 3-0. 🔴 먼저: 없는 것

```
참조 0 인 모듈 수준 심볼      pipeline · critic · router · tools · ablation → **0개**
안 읽히는 플래그              26개 전수 확인 → **0개**
                              (`retrieval`·`schema_fields` 는 `uses_bm25` 프로퍼티와
                               `shape_evidence` 를 통해 읽힌다 — 간접 소비다)
안 쓰는 import                 `router.py` 의 `resolve_company` **한 개뿐**
```

**기각 arm 의 코드는 죽은 코드가 아니다.** `_answer_with_tools`·`_tool_round_trip`·
`_offer_tools`·`_numeric_tool_results`·`_substitute_tool_results`(A24/A24b),
`_gather_golden`·`_oracle_section_blocks`(ORACLE), `_self_consistent`·`_sc_key`(A10) —
🔴 **전부 판정의 물증이고, 지우자고 하지 않는다.** 이것들은 §4 의 문제(자리)이지 §3 의
문제(존재)가 아니다.

### 3-1. 🔴 진짜로 안 쓰이는 것 하나 — 그리고 그것은 결함이다

```
pipeline.upstream_failure()      소비자: 테스트 5건.  운영·러너 **0건**
pipeline._UPSTREAM_RE            "                   "
pipeline.RETRYABLE_STATUS        "                   "
```

독스트링은 *"호출부(평가 러너·API)가 오답으로 캐시하지 않기 위해 쓰는 값"* 이라고 적혀
있다. **그 호출부가 없다.** 러너는 대신 `scripts/error_analysis.run_case` 안에서
**사람이 읽는 문구**를 손으로 긁는다:

```python
failure = re.search(r"생성 실패: (.+?)(?: \| |$)", response.get("think_trace", ""))
```

🔴 **그 문구를 안 쓰는 상류 장애 경로가 셋이다.** `_upstream_mark` 는 기계 표식을 남기지만
뒤따르는 사람 문구가 다르다:

|경로|트레이스|프로즈 가드에|운영(A20r)에서|
|---|---|---|---|
|`answer_question` 생성 실패|`… 생성 실패: …`|✅ 잡힌다|산다|
|`answer_question` critic 실패|`… critic 생략: …`|❌ **안 잡힌다**|🔴 산다|
|`_regenerate_once` 실패|`… verify regen=skip_error kept=original: …`|❌ **안 잡힌다**|🔴 **A20r 전용 경로다**|
|`_tool_round_trip` 실패|`fc stop=RuntimeError …`|❌ 안 잡힌다|A24 만|

즉 **운영 기본값 `A20r` 의 재생성 호출이 상류 장애로 죽으면, 그 응답이 정상 측정으로
디스크에 굳는다.** 이 코드가 막으려고 쓰였던 사고(Q-051·Q-053)와 같은 종류다.

#### 🔴 이미 발화했다 — 산출물 전수로 셌다 (LLM 0회)

`artifacts/**` 를 `upstream_error` 로 전수 grep 했다.

```
발화 3건 · 전부 같은 arm · 전부 같은 단계
  artifacts/ablation/v4/L6/responses/Q-049.json   stage=critic status=429 retryable=True
  artifacts/ablation/v4/L6/responses/Q-054.json   stage=critic status=429 retryable=True
  artifacts/ablation/v4/L6/responses/Q-090.json   stage=critic status=429 retryable=True

regen=skip_error   0건   ← `A20r` 경로는 **아직** 안 밟았다 (잠복)
fc stop=           0건   ← `A24` 경로도 안 밟았다
```

🔴 **피해의 성격을 정확히 적는다.** 이 셋은 답변이 `UNANSWERABLE` 로 굳은 것이 **아니다** —
세 문항 다 정상 답변이 있다. 굳은 것은 **"이 세 문항만 critic 검증을 건너뛰었다"** 는
사실이고, 그것이 `L6` 88문항 중 3건에 **다른 처리 경로**를 만든 채 캐시됐다.
단일 변수 조작을 재는 arm 에서 **코호트가 균질하지 않게 된 것**이고, 아무 데도 안 찍힌다.

그리고 `retryable=True` 다 — **`ea.with_retry` 가 잡았으면 재시도했을 429**인데,
파이프라인이 5키 계약을 지키려고 예외를 삼키므로 러너가 그 429 를 **끝내 보지 못한다.**

🔴 **이것은 리팩토링이 아니라 결함이다.** 다만 고치면 **러너의 캐시 판단이 바뀐다**
(답변 자체는 안 바뀐다). §6 에서 별도 항목으로 세운다.

### 3-2. 계산만 되고 소비자가 없는 값 — 🔴 v3 `Route.use_sc` 가 그대로 있다

```
router.Route.use_sc      설정: router.route() 가 계산한다
                         소비: `pipeline.answer_question` 의 **트레이스 문자열뿐**
                         분기: **없다**
```

🔴 그리고 그 사실에는 측정상의 뜻이 있다. **`A10`(자기일관성)은 `arm.sc_k` 만 보고
`r.use_sc` 를 안 본다** — 즉 라우터가 *"이 문항은 SC 부적합"* 이라고 판정한 서술형 문항
(`tests/test_router.py` 가 그 판정을 고정한다)에서도 k=3 표본을 샀다.
**`A10` 의 Δ 는 그 조건에서 측정된 값이다.**

🔴 **배선하지 말 것.** `use_sc` 를 `_self_consistent` 에 물리면 A10 의 측정이 무효가 된다.
할 일은 **주석으로 그 사실을 못 박는 것**뿐이다.

### 3-3. 테스트에만 소비자가 있는 공개 함수

```
critic.completeness_audit     소비자: tests/test_critic.py 3건.  src·scripts 0건
```
🔴 지우지 않는다 — 완전성 검사의 '전제 성립' 과 '발화' 를 가르는 분석기이고 Phase H
귀속의 근거다. **`__all__` 이나 주석으로 "분석 전용" 을 표시**하는 선에서 끝낸다.

### 3-4. 안 쓰는 import

```
src/argus/agent/router.py   `from argus.corpus.manifest import … resolve_company`
                            → router.py 안에서 한 번도 안 쓴다. 테스트도 안 패치한다.
```

---

## 4. 운영 경로 대 실험 경로

운영 경로는 `api/server.py::answer` → `pipeline.answer_question` 이고, arm 을 **지정하지
않는다** — 따라서 `ablation._current = A20R` 이 곧 제출 구성이다.

### 4-a. 🔴 운영에서 절대 안 도는 코드가 `pipeline.py` 한복판에 310줄 있다

`A20r` 기준으로 **한 번도 실행되지 않는** 함수:

```
_gather_golden              67줄   golden_oracle=False
_oracle_section_blocks      51줄   "
_tool_round_trip            58줄   function_calling=False
_self_consistent            39줄   sc_k=1
_sc_key                     19줄   "
_numeric_tool_results       19줄   substitute_tool_results=False
_substitute_tool_results    17줄   "
_offer_tools                17줄   (첫 줄에서 False 반환)
_answer_with_tools          12줄   function_calling=False
_tool_arguments             11줄   "
                          ─────
                           310줄   = pipeline.py 전체의 12%
```

주석 블록까지 포함하면 실질 400줄 이상이 **운영 경로를 읽는 사람이 건너뛰어야 하는
분량**이다. 🔴 **지우자는 것이 아니다 — 자리를 옮기자는 것이다.**

### 4-b. 생성 경로의 분기 밀도

`answer_question` 본문에서 arm 을 읽는 자리가 **넷**(`golden_oracle` · `lean_rules` ·
`_offer_tools` · `sc_k`)이고, 운영 기본값에서 **넷 다 안 타는 쪽**이다.
`_verdict`·`_verify` 에서 셋(`substitute_verified`/`regenerate_on_verify` ·
`provenance_gate`)이고 여기서는 **두 개가 켜진다**(A20r).

### 4-c. 🔴 좋은 선례가 이미 있다 — `prompts.py`

`agent/prompts.py` 는 **`ablation` 을 import 하지 않는다.** `lean_rules` 를 **인자로**
받는다(`build_answer_messages(..., lean_rules=…)`). 호출부가 `ablation.enabled` 로 읽어
넘긴다. 🔴 **이 규약이 저장소의 목표 형태**이고, `pipeline` 은 반대로 `ablation.current()`
를 함수 곳곳에서 직접 읽는다(`_gather_evidence`·`_verify`·`_search_args`·
`answer_question` 등).

### 4-d. 레이어 역전 — 채점기가 `scripts/` 에 있다

`score_closed`·`covers_key_amounts`·`strip_verify_tail` 이 `scripts/error_analysis.py` 에
있고, `scripts/ablation.py`·`scripts/ablation_report.py`·`scripts/build_nary.py` 와
**테스트 6파일**이 `from scripts import error_analysis as ea` 로 끌어 쓴다.
🔴 **동결 전에 옮기는 것은 권하지 않는다** — import 경로가 바뀌면 테스트 6파일이 함께
움직이고, 얻는 것은 미학뿐이다. **v5 이후 항목으로 남긴다.**

---

## 5. 테스트 소견

### 5-a. 1,239건이 지키는 것

```
계약        API 5키 스키마 · 검사 반환 규격 · TOOL_SPECS 목록 (test_server · test_tools)
회귀        실데이터로 골든에 닿는가 (test_pipeline 의 "실데이터 회귀" 절 · test_corrections)
게이트      비용 상한 · heldout 1회성 · v1 자리 덮어쓰기 거부 · 지출 이중계상 금지
            (test_ablation 의 절반이 여기다)
판정 물증   🔴 기각 arm 이 실제로 무엇을 했는지 (test_function_calling 44건 = A24/A24b,
            test_ablation 의 A10·A22·A8-lean 절)
불변        "끈 것이 실제로 꺼졌는가" (dense 가 임베딩을 한 번도 안 부른다 등)
```

### 5-b. 중복 테스트

🔴 **이름이 겹치는 테스트는 저장소 전체에 한 쌍뿐**이고 그것도 층이 다르다
(`test_counterparty_query_routes_to_the_graph` — `test_pipeline.py` / `test_router.py`).
**중복 테스트 문제는 없다.** 중복은 §2-b ⑨⑩ 의 **헬퍼 사본**이지 단언이 아니다.

### 5-c. 🔴 느린 테스트 — 전량 24.9초 중 **13.7초가 두 파일**

|파일|합계|원인|
|---|---:|---|
|`tests/test_ablation.py`|**10.76s**|🔴 `time.sleep(ea.PACE_S)` (=**1.0초**) 를 그대로 잔다|
|`tests/test_corrections.py`|3.98s|🔴 `build_chains("exchange")` 를 **16번** 다시 만든다 (캐시·픽스처 없음)|
|`tests/test_scorer_r3.py`|2.11s|`rescore_all({})` 가 artifacts 전체를 훑는다 — 단언은 전역 복원 2개뿐|
|`tests/test_site.py`|1.95s|실제 사이트 빌드|

가장 느린 넷이 전부 **잰 것과 무관한 대기·재계산**이다:

```
4.01s  test_judge_repeat_three_keeps_every_round_and_never_touches_the_canonical_file
2.05s  test_rescore_restores_the_global_cache_dirs
2.01s  test_a_finished_round_is_not_bought_twice
1.01s  test_judge_repeat_one_changes_nothing_about_the_old_path
```

🔴 **`PACE_S` 를 패치하는 테스트가 저장소에 0건이다** — 즉 위 9초는 순수 API 예의용 sleep
이고 단언과 아무 관계가 없다.

---

## 6. 🔴 처방 — 우선순위 · 위험도 · 공수

규율: **동작을 바꾸는 리팩토링을 제안하지 않는다.** 아래에서 "답변 변화 없음" 은
*"v1~v4 산출물의 answer 바이트가 안 바뀐다"* 는 뜻이다.

### 티어 A — 동결 전에 한다 (합계 ≈ 반나절)

|#|처방|위험도|공수|무엇이 좋아지는가|
|---|---|---|---|---|
|**A1**|🔴 **러너의 프로즈 가드가 상류 장애 세 경로를 못 본다 — 이미 3건 발화** (§3-1). `error_analysis.run_case` 의 `생성 실패:` 정규식을 `pipeline.upstream_failure()` 호출로 교체|🟠 **중 — 러너 동작이 바뀐다** (답변은 안 바뀐다. 지금까지 조용히 캐시되던 429 가 예외로 떠 `with_retry` 가 잡는다). 🔴 `retryable=False` 는 그대로 두는가를 먼저 정할 것|2~3h|**arm 코호트의 균질성을 지킨다.** 지금 `v4/L6` 은 88문항 중 3건이 critic 을 건너뛴 채 굳어 있고 **어느 산출물에도 안 찍힌다.** `A20r` 재생성 경로는 아직 안 밟았을 뿐 같은 구멍이다|
|**A2**|테스트 pacing 제거 — `conftest.py` 에 `PACE_S=0` autouse 픽스처|🟢 **없음 — 테스트 전용**|30m|**전량 24.9s → 약 16s.** 동결 전 반복 실행이 잦은 시기에 직접 이득|
|**A3**|`test_corrections.py` 의 `build_chains` 를 세션 스코프 픽스처로|🟢 **없음 — 테스트 전용**|30m|추가 **−3.7s**. 합쳐서 전량 **약 12초**|
|**A4**|`ensure_counterparty_graph` 세 사본 → `conftest.py` (§2-b ⑨). `test_tools.py` 축약본의 `cache_clear` 누락도 함께 사라진다|🟢 **없음 — 테스트 전용**|1h|사본 3벌 제거 + **순서 의존 취약점 1건 제거**|
|**A5**|`router.py` 의 미사용 import `resolve_company` 제거|🟢 **없음**|2m|— (자명)|
|**A6**|🔴 **주석 정정 셋** ① `critic.py` 머리말의 "4단 검증" ② `verify_completeness` 가 다른 규격임을 명시 ③ `Route.use_sc` 에 "소비자 없음 · A10 은 `sc_k` 만 본다" 를 박는다|🟢 **없음 — 주석만**|1h|**다음 세대가 `use_sc` 를 '고쳐야 할 버그' 로 보고 배선하는 것을 막는다** (배선하면 A10 측정이 무효)|

### 티어 B — 동결 전에 가능하면 (합계 ≈ 하루)

|#|처방|위험도|공수|무엇이 좋아지는가|
|---|---|---|---|---|
|**B1**|🔴 **`scripts/ablation.py::render_gate`(933줄)를 절별 함수로 쪼갠다.** 14개 `# --- N.` 절이 이미 경계다. 각 절 → `_gate_cost(...)`·`_gate_ladder(...)` … 로 **순수 추출**|🟡 **저 — 운영 경로 아님(러너 산출물)**. 🔴 합격 조건이 이미 있다: `test_the_regenerated_v2_gate_reads_the_way_the_documents_quote_it` 이 실물 `v2` 로 렌더해 문장을 단언한다. **v2·v3·v4 세 root 를 쪼개기 전후로 렌더해 바이트 대조**하면 된다 (LLM 0회)|4~6h|현재 이 함수는 **읽을 수도 테스트할 수도 없다.** 절 단위 테스트가 가능해지고, 게이트 항목 추가가 함수 추가가 된다|
|**B2**|🔴 **`pipeline.py` 의 실험 전용 310줄을 `agent/arms.py`(가칭)로 이동** — `_gather_golden`·`_oracle_section_blocks`·도구 왕복 5종·`_self_consistent`·`_sc_key`. **순수 이동 + import**|🟡 **저~중 — 운영 경로 파일을 건드린다.** 🔴 심볼은 `pipeline` 에 재노출해야 한다 (테스트가 `pipeline._offer_tools` 등으로 부른다)|3~4h|**운영 경로가 12% 짧아진다.** 물증은 한 글자도 안 지운다 — 자리만 옮긴다|
|**B3**|검사 반환 규격 공장 `critic._report(...)` 도입 — 손으로 짓는 5곳을 한 생성자로 (§2-b ③)|🟡 **저 — 키 집합이 바뀌면 안 된다.** 🔴 `verify_completeness` 는 **다른 규격이므로 합치지 않는다**|2~3h|새 검사를 붙일 때 키 누락이 **불가능**해진다. `_verdict` 의 조용한 오판정 경로가 닫힌다|
|**B4**|`scripts/ablation_report.py::pct`·`delta` 를 `scripts/ablation` 것으로 대체 (§2-b ④)|🟢 **없음 — 본문 바이트 동일**|30m|서식 규칙이 한 곳|
|**B5**|🔴 **`- 라벨: 금액원 (억원)` 서식 계약 테스트** (§2-b ⑪). `markdown._amount_line` 산출이 `prompts._EOK_ANNOTATION` 과 `ablation._EOK_SUFFIX` 에 **둘 다 걸리는가**를 단언|🟢 **없음 — 테스트 추가만**|1~2h|주석으로만 묶인 결합이 **코드로** 묶인다. 이걸 깨면 A5·L2 의 Δ 가 조용히 변한다|

### 티어 C — 🔴 동결 전에 하지 말 것

|#|안 하는 것|이유|
|---|---|---|
|**C1**|`src/argus/ablation.py` 분할|코드가 163줄뿐이다. 크기는 **물증 주석**이고 그것이 이 모듈의 값이다. (`shape_evidence` 분리조차 v5 로 미룬다 — 얻는 것이 없다)|
|**C2**|`critic._MONEY_RE` ↔ `error_analysis._MONEY_RE` 통합|🔴 **후보 집합이 달라 채점이 뒤집힐 수 있다** (§2-b ①). 규율 ① 위반|
|**C3**|`Route.use_sc` 배선|🔴 **`A10` 측정이 무효가 된다** (§3-2)|
|**C4**|채점기(`error_analysis`)를 `src/` 로 이동|import 경로가 바뀌면 테스트 6파일이 함께 움직인다. 얻는 것은 미학뿐|
|**C5**|기각 arm 코드 삭제(`_answer_with_tools` 등)|🔴 **판정의 물증이다**|
|**C6**|`_gather_evidence`(266줄) 차선 분리|가장 크지만 **운영 경로의 심장**이다. 검색 순서가 한 칸만 밀려도 체인 씨앗이 바뀌고 v1~v4 가 통째로 흔들린다. 열흘 안에 안전하게 못 한다|
|**C7**|`html_doc`/`sections` 의 `_top_level` 통합, HTML→평문 4벌 통합|동작 중립이고 옳지만 **코퍼스 빌드 경로**다. 재빌드하면 BM25 색인 문자열이 갈릴 위험을 안는다 — v5 로|

### 실행 순서 (08-25 까지 열흘)

```
1일차   A5 · A6 · A2 · A3 · A4        (반나절 · 위험 0)  → 테스트가 12초로 내려온다
2~3일차 A1                            (러너 결함 · 🔴 저장 응답 전수 스캔 먼저)
4~5일차 B4 · B3 · B5
6~8일차 B1  (render_gate — 산출물 바이트 동일이 합격 조건)
9일차   B2  (pipeline 실험 코드 이관 — 🔴 전량 테스트 + A20r 스모크가 합격 조건)
10일차  예비
```

---

## 7. 확인 못 한 것

```
① 🔴 다른 에이전트가 `pipeline.py`·`critic.py`·`ablation.py` 를 고치는 중이었다.
   조사 중 HEAD 가 dac2bf0 → e0c0fc9 로 움직였다. 위 줄 수·함수 크기는 **그 시점의
   스냅샷**이고, 심볼 이름은 유효하되 수치는 다시 세야 할 수 있다.

② `evalset/` 을 열지 않았다 — 테스트가 무엇을 지키는지는 **테스트 코드에서만** 읽었고
   골든 자체와 대조하지 않았다.

③ A1 의 발화 건수는 셌지만(3건 · §3-1) **그 3건이 채점에 실제로 영향을 줬는지는 안 봤다.**
   critic 은 `quick_check` 가 근거 표기를 못 찾았을 때만 도는 보조 단계라, 셋 다 애초에
   판정을 안 바꿨을 수 있다. `L6` 의 `report.json` 에서 그 셋의 정오를 A8 과 대조하면
   닫힌다 — LLM 0회다.

④ B1·B2 의 공수는 **읽어서 추정한 값**이고 실제로 쪼개 보지 않았다. 특히 B2 는
   테스트가 어느 심볼을 `pipeline.` 으로 부르는지 전수를 세지 않았다 (표본만 봤다).

⑤ `scripts/error_analysis.py`(2,273줄)와 `scripts/build_site.py`(2,151줄)는 규모만
   재고 **책임 분해를 하지 않았다.** 지시받은 넷 밖이다.

⑥ 🔴 `_gather_evidence` 266줄의 **차선별 실제 분기 조건**을 전수로 표로 만들지 않았다.
   C6 로 미룬 판단은 "위험하다" 는 정성 판단이고, 정량 근거(차선 간 상태 공유 변수의 수)
   까지는 세지 않았다.
```
