# 리팩토링 실행 — v4 코드 동결 전

**날짜** 2026-08-16 · **LLM 호출** 0회 · **비용** 0원 · **처방의 정본** `refactor-survey.md`

```
결과
  ① A2+A3  테스트 대기·재계산 제거    828fc71   ✅ 전량 25.27s -> 14.70s
  ② A1     상류 장애 가드 교체        c597af5   ✅ + 1663ef7 (사이트 게이트 후속)
  ③ B1     render_gate 933줄 분해     74544e0   ✅ gate_I.md 3세대 바이트 동일
  ④ 곁      죽은 흔적·주석 정정        090e981   ✅

  전량 테스트  1,243 passed (25.27s)  ->  1,253 passed (15.62s)
  성적         🔴 26 arm · 1,246 행 스냅샷이 리팩토링 전과 sha256 동일
```

🔴 **한 단계도 성적을 흔들지 않았다.** 증명 방식은 §5 에 적었다.

---

## ① `A2` + `A3` — 테스트만 (`828fc71`)

|무엇|어디|근거|
|---|---|---|
|`PACE_S=0` autouse 픽스처|`tests/conftest.py`|저장소에 `PACE_S` 를 패치하는 테스트가 **0건**이었다. 소비자 셋이 전부 모듈 속성으로 읽으므로(`ea.PACE_S`) 한 곳이면 전부 걸린다|
|`build_chains` 그룹별 1회 빌드|`tests/test_corrections.py`|같은 그룹을 **18번** 다시 지었다 (exchange 만 열둘). 깊은 사본을 돌려줘 순서 의존을 안 만든다|

```
전량            24.92s (조사 시점) · 25.27s (실행 시점 기준선)  ->  14.70s
가장 느린 넷    4.01s · 2.05s · 2.01s · 1.01s  ->  전부 사라졌다
남은 1위        test_scorer_r3.py::test_rescore_restores_the_global_cache_dirs  2.09s
```

🔴 **단언은 한 글자도 안 바꿨다.** 기준선 그대로 1,243 passed.
재시도 백오프(`RETRY_BASE_S`)는 안 건드렸다 — 429 를 만났을 때만 도는 길이라 테스트에서
밟히지 않는다.

---

## ② `A1` — 상류 장애 가드 (`c597af5` · `1663ef7`)

### 조사가 옳았다 — 그리고 경로가 **하나 더** 있었다

```
러너      scripts/error_analysis.run_case 가 사람이 읽는 `생성 실패:` 를 정규식으로 긁었다
샌 경로   critic 생략 / verify regen=skip_error(🔴 A20r 전용) / fc stop=(A24)
🔴 다섯   pipeline._self_consistent 의 `sc skip=upstream k=2/3 (…)`
          — `_upstream_mark` 를 **안 붙인다.** 조사 표에 없던 자리다
```

### 🔴 실측 정정 — 429 는 **28건이 아니라 4건**이다

브리핑은 *"429 가 산출물에 28건 있습니다"* 였다. `artifacts/**` 전수를 다시 셌다 (LLM 0회).

```
진짜 상류 429 (= CLOVA 가 실제로 거절한 호출)          4건
  artifacts/ablation/v4/L6/responses/Q-049.json    stage=critic  429 retryable=True
  artifacts/ablation/v4/L6/responses/Q-054.json    stage=critic  429 retryable=True
  artifacts/ablation/v4/L6/responses/Q-090.json    stage=critic  429 retryable=True
  artifacts/ablation/v4/A10/responses/Q-049.json   stage=sc      429 retryable=True

🔴 "28건" 의 정체 — `429` 라는 **문자열**의 매치다. 실제 내용은
     tokens=3429 · tokens=1429 · tokens=4293 같은 토큰 수     ← 18건
     질의문 안의 숫자 (`… 2025 월에 공시한 …`)                ← 43회
   `Q-030`·`Q-071` 이 거의 모든 arm 에 공통으로 잡힌 것이 그 증거다 — 그 둘은
   상류 장애를 밟은 적이 없고 **토큰 수에 429 가 들어 있었을 뿐**이다.
```

**판정에 미치는 영향은 브리핑의 결론과 같다.** L6 고유 3건은 정상 답변이 있고 A8 과
정오가 같다. 굳은 것은 *"이 세 문항만 critic 을 건너뛰었다"* 는 사실이고,
**그것이 어느 산출물에도 안 찍혔다.**

### 고친 것

```
러너 가드    `생성 실패:` 정규식  ->  pipeline.upstream_failure()
             예외 문구에 `status=` 를 남긴다 -> `ea._is_rate_limit` 이 429 를 알아보고
             `with_retry` 가 지수 백오프로 재시도한다.
             🔴 종전에는 파이프라인이 5키 계약을 지키려고 예외를 삼켜서
                러너가 그 429 를 **끝내 보지 못했다.**
retryable    False 도 되던진다. 400 대도 우리 답이 아니고, 종전 프로즈 가드도 생성
             단계에서는 재시도 여부를 안 보고 무조건 던졌다. `with_retry` 가 429 만
             재시도하고 나머지는 그대로 올리므로 **동작이 종전과 같다.**
산출물       report.json > rows[].upstream_failure   (문항 단위)
             report.json > upstream_failures         (최상위 · 이름을 댄다)
             error_matrix.md 머리의 경고표           (arm 폴더가 홀로 여행해도 딸려간다)
```

🔴 **표식 없는 저하 경로는 제동에 안 넣었다.** `sc skip=upstream` 까지 러너가 거부하면
저장된 arm 의 재실행이 달라진다. **표시는 하되 제동은 안 건다** — `ea.upstream_note` 가
`marked=True/False` 로 그 갈래를 낸다.

### 🔴 429 문항이 이제 산출물에 찍히는가 — **찍힌다**

```
artifacts/ablation/v4/L6/report.json   > upstream_failures
  {"Q-049": {"stage":"critic","status":"429","retryable":true,"marked":true},
   "Q-054": {…}, "Q-090": {…}}
artifacts/ablation/v4/A10/report.json  > upstream_failures
  {"Q-049": {"stage":"sc","status":"429","retryable":true,"marked":false}}

artifacts/ablation/v4/L6/error_matrix.md  (정답률 표보다 먼저 온다)
  > 🔴 **상류 장애 3건 — 이 arm 의 코호트는 균질하지 않다.**
  | 문항 | 단계 | 상태 | 재시도 가능 | 기계 표식 |
  | Q-049 | critic | 429 | True | True |  …
```

### 🔴 브리핑의 전제가 하나 더 틀렸다 — `scripts/build_site.py` 는 안 지워졌다

지시는 *"`scripts/build_site.py` 는 이미 지워졌으니 화이트리스트 걱정은 없습니다"*
였다. **파일은 그대로 있고, 화이트리스트가 설계대로 발화했다.**

```
tests/test_site.py   1 failed + 18 errors
원인                 project_row 가 모르는 필드를 만나면 SiteLeak 을 던진다
                     — A1 이 rows 에 필드를 하나 늘렸다
```

`1663ef7` 로 닫았다. 🔴 **유출이 아니라 모양 때문에 막았다** —
`{stage,status,retryable,marked}` 는 골든·답변·문서 식별자를 한 글자도 안 들고 있지만,
그 행은 정적 필터가 그리는 **스칼라 표**(`FILTER_ROW_FIELDS`)라 중첩 객체를 그릴 자리가
없다. 사이트에도 싣기로 판단하면 **스칼라로 펴서** 화이트리스트로 옮기면 된다.

---

## ③ `B1` — `render_gate` 933줄 (`74544e0`)

### 사람이 옮겨 적지 않았다

절 본문은 `o.append(` → `out.append(` 말고 **한 글자도 안 바뀌었다.** 옮기는 일 자체를
스크립트에 시켰다(스크래치). 사람이 933줄을 손으로 나누면 오타가 난다.

### 절 사이의 상태 흐름을 AST 로 전수 조사했다 — 추측 안 했다

```
열일곱 절이 읽는 것    머리말이 만든 값뿐 (`_GateContext` · frozen)
예외                   🔴 딱 하나 — `9b` 가 `9` 의 `targeted`·`idx` 를 읽는다
                       그것만 인자로 넘긴다
본문                   `o[...]`·`o.pop`·`o.insert`·`return`·중첩 `def` **전부 0건**
                       → 순수 추출이 가능한 모양이었다
```

|절|함수|줄|절|함수|줄|
|---|---|---:|---|---|---:|
|0|`_gate_arm_flags`|26|8|`_gate_flipped`|13|
|1|`_gate_cost`|121|9|`_gate_targeting`|69|
|2|`_gate_cumulative`|22|9b|`_gate_targeting_vs_loo`|20|
|3|`_gate_by_axis`|73|10|`_gate_paper_comparison`|23|
|4|`_gate_by_track`|17|11|`_gate_knee`|133|
|5|`_gate_loo`|29|12|`_gate_measured42`|101|
|5b|`_gate_diagnostic_arms`|34|13|`_gate_reading_notes`|39|
|6|`_gate_valuechain`|57|14|`_gate_heldout`|77|
|7|`_gate_failure_stages`|20|—|`render_gate` (조립)|**62**|

### 🔴 합격 조건은 미학이 아니라 바이트 — 세 세대 전부 동일

```
                    쪼개기 전 sha256          쪼갠 뒤       바이트
gate_v2.md  35,455B  ba5dab2a589e16cd…        같음          ✅ 동일
gate_v3.md  27,451B  bd5041f078132386…        같음          ✅ 동일
gate_v4.md  26,831B  07f2ee2dffa8b90c…        같음          ✅ 동일
```

🔴 **얼린 v2·v3 를 한 바이트도 안 건드렸다.** `render_gate` 는 디스크를 안 쓴다
(`write_gate_report` 가 쓴다) — 읽기 전용으로 렌더해 스크래치에서만 대조했다.

고정 테스트 2건을 세웠다 (`tests/test_ablation.py`):
`test_the_gate_report_is_assembled_from_section_functions` (절 목록·순서·순수성·frozen·
`render_gate` 120줄 상한) · `test_every_gate_section_is_reachable_from_the_real_v2_artifacts`
(실물 v2 관통 + **두 번 렌더해도 같은 문서** = 절이 공용 상태를 안 고친다).

---

## ④ 곁가지 — 죽은 흔적과 물증 (`090e981`)

판단 기준은 **"물증이면 남기고, 흔적이면 지운다"**.

```
지웠다   router.py 의 미사용 import `resolve_company`
         tools.py 의 stale `# noqa: F401` — 다섯 이름이 전부 그 파일에서 직접 쓰인다
         (`tools.Bm25Index` 처럼 이 모듈을 통해 끌어 쓰는 소비자 저장소 0건)

남겼다   🔴 Route.use_sc      배선하면 A10 측정이 무효 — 필드에 직접 못 박았다
         🔴 Route.by         대입조차 안 되지만 필드를 빼면 Route 의 모양이 바뀐다
         🔴 Route.difficulty 트레이스 문자열 전용임을 명시
         references.iter_all_references  호출 0건 · 분석 전용으로 표시

🔴 판정을 뒤집었다 — `query.SECTION_STRATEGIES`
   브리핑은 "참조 1건(자기 할당) → 안전하게 지울 수 있다" 였다. **지우면 안 된다.**
   그 줄은 `SECTION_TOP_K = (8, 10, 8, 10)` 의 **자리 이름**이고, 네 값이
   `section_plans` 의 hop 0~3(`toc_narrow`·`toc_corp`·`doc_narrow`·`corp_only`)에
   자리 순서로 대응한다. 지우면 설명 없는 4-튜플이 남는다. 사유를 코드에 적었다.
```

주석 정정(조사 `A6`) 둘도 함께 닫았다 — `critic.py` 머리말의 *"4단 검증"*(실제로는
다섯 가지를 한다 · **arm 을 모른다**는 설계 명시), `verify_completeness` 가
*"같은 반환 규격"* 이 아니라는 사실(8키 · `mismatches` 대신 `gaps`).

---

## 5. 🔴 성적 불변 증명 — 두 겹으로 걸었다

### 겹 ①. 전 arm 채점 스냅샷 (LLM 0회 · 디스크 쓰기 0)

`artifacts/ablation/v4` 의 **26 arm 전부**(+`vc`·`safety` 하위 코호트)에 `ea.analyse` 를
돌려 `correct`·`scored_by`·`first_failure`·`root_cause`·`failure_why`·`critic_supported`·
`numeric_ok`·`covers_key_amounts_ok`·`gold_value_in_evidence`·`has_citation`·`n_llm_calls`·
`prompt_tokens`·`completion_tokens`·**`answer` 원문**까지 1,246 행을 떠서 대조했다.

```
리팩토링 전   sha256 e8f4798a2b6a35e514ed49bef675595686e38918d2bbcc67382a1417b0976e84
A1 직후       sha256 e8f4798a2b6a35e5…   ✅ 동일
전 단계 종료  sha256 e8f4798a2b6a35e5…   ✅ 동일
```

### 겹 ②. 지시받은 `--offline` 재집계

```
PYTHONIOENCODING=utf-8 …/python.exe scripts/ablation.py \
  --arm A20r --cohort full --out artifacts/ablation/v4 --offline

  실지출 9,561.0원 · 이번 프로세스 0.0원 · 🔴 캐시 적중 0 / 미적중 0
  A20r 67/72 rate=0.931   (재집계 전과 같다)
  정오 맵(88문항) 바이트 동일  sha256 b553f3e6e6dab517…
  report.json 의 변화 = `+upstream_failure` 88줄 · `+upstream_failures` · 타임스탬프뿐
```

`L6`·`A10` 도 같은 방식으로 재집계했다 (429 를 산출물에 실으려면 그 두 폴더가 필요하다).
**정오 불일치 0건** — L6 85행 · A10 37행.

---

## 6. 못 한 것과 사유

```
A4   `ensure_counterparty_graph` 세 사본 -> conftest.py
     ▸ 지시받은 ①②③ 밖이다. 위험 0 · 30분~1시간. 다음 세대가 가져가면 된다.
B2   pipeline 의 실험 전용 310줄 이관
B3   검사 반환 규격 공장 `critic._report(...)`
B4   `ablation_report.pct`·`delta` 통합
B5   `- 라벨: 금액원 (억원)` 계약 테스트
     ▸ 넷 다 지시 밖이다. 🔴 그중 **B5 가 가장 값싸고 가장 위험을 줄인다** —
       생산자 셋과 소비 정규식 둘이 주석으로만 묶여 있고, 깨지면 A5·L2 의 Δ 가
       조용히 변한다.

`_self_consistent` 에 `_upstream_mark` 붙이기
     ▸ **일부러 안 했다.** 붙이면 `run_case` 가 그 경로도 거부하게 되어
       `sc_k>1` arm 의 재실행 동작이 바뀐다. 조사 표의 넷 밖이라 범위를 안 넓혔다.
       대신 `ea.upstream_note` 가 그 경로를 **산출물에는** 싣는다 (`marked=False`).

artifacts/ablation/v4/gate_I.md
     ▸ `--report` 로 재생성은 했지만 **커밋하지 않았다.** 지금 그 파일은 다른
       에이전트의 `A24b`·`A24b+A20` 행을 함께 들고 있어 그쪽 커밋에 속한다.
```

## 7. 🔴 다음 사람이 알아야 할 것

```
① `scripts/build_site.py` 는 살아 있다. `report.json > rows` 에 필드를 늘리면
   `SiteLeak` 이 뜬다 — **설계다.** `ROW_FIELDS_ALLOWED` 또는 `_BLOCKED` 에
   갈래를 적기 전에는 안 실린다.
② 429 는 4건이었다. 브리핑의 "28건" 은 문자열 매치(토큰 수·질의문 숫자)다.
③ `L6` 3건과 `A10` 1건은 **답변이 정상이고 정오도 A8 과 같다.** 굳은 것은 처리
   경로의 비균질성이고, 이제 `report.json`·`error_matrix.md` 가 그것을 이름으로 댄다.
④ `render_gate` 는 다시 자라면 테스트가 먼저 깨진다 (조립부 120줄 상한).
```
