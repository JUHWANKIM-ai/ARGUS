# R1-2 · R1-4 — `A20r` 사유 주입 재생성 · `A17` 판정

측정일 2026-08-15 · **LLM 0회 · 네트워크 0회 · 크레딧 0원.**
`--dry` 만 썼고 `tests/conftest.py` 의 `block_clova_network` 를 우회하지 않았다.
`.env` 미접촉 · `evalset/` 미접촉 · `artifacts/ablation/v1`·`v2`·`v3` 와 `artifacts/attic/`
읽기만 · `docs/` 미접촉 · `site/`·`scripts/build_site.py`·`tests/test_site.py` 미접촉.

| # | 커밋 | 내용 |
|---|---|---|
| ① | `278d06f` | `A20r` — 치환 대신 **사유를 실어 재생성 1회**. `A21` 의 판정을 여기서 소비한다 |
| ② | `b0eeaca` | `R1-4` `A17` **기각** — 절단이 아니라 렌더 게이트다. **동작 코드 무변경** |

**전량 테스트 1,102 passed** (기준선 1,085 + `A20r` 12 + `A17` 5 · 실패 0).

---

# ① `R1-2` — `A20r` (`278d06f`)

## 🔴 `A20r` 은 `A21` 의 `reason` 을 **실제로 싣는다** — 코드와 실물

사유 목록을 만드는 자리는 한 곳이고(`pipeline._verdict`), **각주와 재생성 프롬프트가
그 한 목록을 같이 읽는다.** 두 곳에서 만들면 "각주로 본 것" 과 "모델에게 준 것" 이 갈린다.

```python
# pipeline.py  _verdict()
reasons = agg["reasons"][:1] if calc_failed else []          # ① aggregate_calculation
if not reasons and arith_reports and arith_reports[0]["reason"]:
    reasons = [arith_reports[0]["reason"]]                   #   레코드 0건 문항 (Q-044)
if complete["reason"]:
    reasons.append(complete["reason"])
if provenance and provenance["reason"]:
    reasons.append(provenance["reason"])                     # ② 🔴 verify_number_provenance = A21
```

```python
# pipeline.py  _regenerate_once()
result = chat(build_regeneration_messages(
                  question, evidence, verdict["reasons"], answer, ...),
              max_tokens=800 if use_cot else 300,
              temperature=0.0, tag="regenerate")             # 🔴 온도 0.0 유지
budget.spend_llm(result.prompt_tokens, result.completion_tokens)
```

`provenance` 가 계산되려면 `arm.provenance_gate` 가 켜져 있어야 하므로 **`A20r` 은 그
플래그를 켠 채로 정의한다** — 아래 §플래그 참조.

## 재생성 프롬프트 전/후 — 실물 1건 (저장 응답 `v3/A8` `Q-018` · LLM 0회)

메시지는 4개다. **앞의 둘은 `build_answer_messages` 와 바이트 동일**이고 뒤에 앞선
답변(assistant)과 사유(user)가 붙는다.

```
roles = ['system', 'user', 'assistant', 'user']
kwargs = {'max_tokens': 300, 'temperature': 0.0, 'tag': 'regenerate'}
```

**전** — 4번째 메시지가 아예 없다 (프롬프트가 최초 생성과 같다 → 온도 0.0 이면 같은 답).
**후** — 4번째 메시지:

```
앞선 답변에 대해 자동 검증이 아래 결함을 찾았습니다.

- restatement: 답변 1,843,200,000,000원 (1조 8,422억원) vs 재계산 1,842,200,000,000
- stale_value: 근거에 최신본이 있는데 답이 대체본 값 1,566,300,000,000 을 썼다
- provenance: 답변 276,900,000,000원 이 근거에 없다; provenance: 답변 1,843,200,000,000원 이
  근거에 없다; provenance: 답변 276,900,000,000원 이 근거에 없다

같은 [공시 근거]만 보고 다시 답하십시오.
- 지적된 수치는 근거에서 다시 확인해 옮겨 적으십시오.
- 근거에 없는 수치를 쓰지 마십시오.
- 계산이 필요하면 근거의 원값으로 다시 계산해 식과 결과를 함께 적으십시오.
- 검증이 지적하지 않은 부분은 그대로 두십시오.
```

🔴 **세 번째 줄이 `A21` 의 `reason` 그대로다.** `R1-9` 가 *"`A21` 의 `reason` 은 재생성에
그대로 실을 수 있는 형태다"* 라고 적은 것을 실물로 확인했다. `Q-097` 은 계산 쪽만 선다
(`- per_unit: 답변 2,078억 vs 재계산 207,684,631,210`) — **두 원천이 각각 다른 문항을
겨냥하고 한 문항(`Q-018`)에서는 함께 선다.**

## 지켜야 할 것 — 전부 테스트로 박았다

| 요구 | 어디서 | 테스트 |
|---|---|---|
| 🔴 온도 0.0 유지 | `_regenerate_once` | `test_regeneration_feeds_both_failure_reasons_back` |
| 🔴 재생성 1회 | `_regenerate_once` 가 자기를 다시 안 부른다 | `test_a_regeneration_that_still_fires_is_discarded` |
| 🔴 더 나쁘면 원본 | 재생성본 판정에 `kinds` 가 남으면 원본·원본 판정을 돌려준다 | 같은 테스트 |
| 🔴 프롬프트가 실제로 다르다 | `build_regeneration_messages` | `…feeds_both_failure_reasons_back` |
| 🔴 배선이 플래그 뒤 | `if arm.regenerate_on_verify` | `test_regeneration_is_not_wired_in_until_the_flag_is_on` |
| 발화 안 하면 호출 0 | `if not v["kinds"]: return` | `test_a_clean_answer_is_never_regenerated` |

## 🔴 `Budget.llm_calls` 계정 — 센다 (end-to-end 확인)

`answer_question` 이 `_verify(..., budget)` 로 넘기고, `_regenerate_once` 가
`budget.spend_llm(prompt_tokens, completion_tokens)` 을 부른다. `_respond` 가 그 수를
`budget llm=` 으로 찍으므로 **게이트 F 가 읽는 수에 재생성이 들어간다.**

같은 근거·같은 결함 답변으로 arm 만 갈아 끼운 실측(네트워크 0회 · 스텁):

```
[A8]   tags=['answer']                budget steps=0 hops=0 llm=1 tokens=120
[A20r] tags=['answer','regenerate']   budget steps=0 hops=0 llm=2 tokens=240
       verify regen=1 reasons=equation: … ; provenance: 답변 1,843,200,000,000원 이 근거에 없다 …
       answer: 정정 후 계약금액은 1,842,200,000,000원입니다. (근거: 접수번호 …)
```

🔴 **상한은 호출 *전에* 본다** (`budget.remaining()["llm_calls"] <= 0` → `regen=skip_budget`).
부르고 나서 세면 크레딧은 이미 나갔다. 토큰 상한은 `spend_llm` 이 잡고, 그때도 원본을 쓴다.
🔴 **예산 없이 부르면 `ValueError`** 다 — 조용히 건너뛰면 조용한 Δ 0 이고, 안 세고 부르면
게이트 F 가 거짓을 말한다. 둘 다 막았다(`test_regeneration_refuses_to_run_without_a_budget`).

## 플래그와 등록 — 🔴 **두 칸이 움직인다**

```python
A20R = replace(A8, name="A20r", regenerate_on_verify=True, provenance_gate=True)
FLAG_DEPENDENCIES["verifier"] = ("substitute_verified", "provenance_gate", "regenerate_on_verify")
RUN_ORDER = (..., "A8-lean", "A20", "A20r", "A21", "A2F", "A2", "ORACLE")
```

- `ARMS`·`RUN_ORDER` 에 **등록했다** (`R1-1` 이 `A20` 에서 빠뜨린 것). `SIDE_ARMS` 가
  `RUN_ORDER` 에서 파생되므로 러너 요약·게이트 표에 잡힌다. `LADDER`·`LOO_REMOVED`
  에는 **넣지 않았다** — `A8-lean`·`A20`·`A21` 과 같은 **곁가지**다.
- 자리는 `A20` **바로 뒤**다. 나란히 재는 짝이라 사이에 다른 arm 이 들어가면 정지선이
  하나만 확보하고 끊길 때 그 비교가 통째로 사라진다.
- 🔴 **`provenance_gate` 를 함께 켠 이유.** 그것은 조작이 아니라 **입력**이다 — `R1-9` 가
  그 게이트의 단독 Δ 가 **구조적으로 0** 임을 확인했으므로(`_verify` 의 유일한 출력이
  각주이고 채점은 양쪽에서 `strip_verify_tail` 로 그 꼬리를 뗀다) `Δ(A8→A20r)` 에 자기
  몫을 섞지 않는다. 대신 **그 판정을 소비할 자리**를 하나 만든다.
- 🔴 `substitute_verified` 는 **끈 채로** 뒀다 — 계획서가 *"치환 대신 재생성"* 으로 두
  처분을 나란히 재라고 했다. 켜면 `A20` 의 몫과 섞인다.
- 🔴 `verify_answer_arithmetic` 배선은 `substitute_verified or regenerate_on_verify` 로
  넓혔다. **사유의 원천이 필요해서다** — 실측 `Q-044` 는 정형 레코드가 0건이라 이 검사
  없이는 발화조차 없고, 그러면 `A20r` 의 겨냥이 그 문항에서 조용히 사라진다.
  플래그를 둘 다 끈 arm 에서는 종전과 한 줄도 다르지 않다(`test_the_arithmetic_check_is_not_wired_in…`).

## 구조 변경 하나 — `_verify` 를 `_verdict` + 처분으로 갈랐다

`_verdict(question, answer, evidence, records, r, arm)` 는 **부작용 없는 판정 한 벌**이고
`A20r` 이 그것을 두 번 부른다(원본 · 재생성본). 규칙이 한 곳에 있어야 *"여전히 발화하면
원본을 쓴다"* 가 성립한다 — `critic.unique_recomputed` 를 한 곳에 둔 것과 같은 이유다.
트레이스 문자열·각주 문자열은 **바꾸지 않았다**(플래그를 끈 arm 은 바이트 동일).

## `--dry`

```
--arm A20r --cohort full --out artifacts/ablation/v4 --dry   exit 0 · 0.0원 · A20r 36/72
--arm A20  … --dry                                          exit 0 · 0.0원   (리팩터 회귀 확인)
--arm A21  … --dry                                          exit 0 · 0.0원
  셋 다 `이번 프로세스 0.0원 · 캐시 적중 0 / 미적중 0` · 산출물은 artifacts/ablation_dry/
```

🔴 **`--dry` 는 재생성 경로를 타지 않는다.** 리허설 스텁 답변에 수치가 없어 검사가 안
서기 때문이다. 그 경로는 위 end-to-end 스텁 실측과 12건의 테스트가 덮는다.

---

# ② `R1-4` — `A17` 🔴 **기각한다. 동작 코드는 한 글자도 안 바꿨다** (`b0eeaca`)

## Step 1 판정 — 계획서의 진단이 **자리가 틀렸다**

계획서는 `Q-087`(정답 `올림푸스제일차`)을 *"정정본 블록이 절단돼 `주요내용` 필드 부재"*
로 보고 `pipeline.MAX_EVIDENCE` / `MAX_SECTION_CHARS` 를 겨눴다. **둘 다 인과 경로에 없다.**

```
저장 응답 v3/A8 Q-087 근거   2블록 · 952자
MAX_EVIDENCE = 10           2 < 10                      절단이 안 걸린다
MAX_SECTION_CHARS = 12,000  섹션 근거 전용(_join_section_evidence) · 이 문항은 exchange 레코드다
정정본 20241114800538       🔴 근거에 **들어와 있다** (hop=5 chain get_history 가 실어 왔다)
정답 문자열                  raw_rows 에 **있고** markdown 에 **없다**
```

즉 계획서의 갈래 (a)/(b) 어느 쪽도 아니다. **레코드는 후보에 들어왔고, 정답을 담은
텍스트는 애초에 렌더되지 않았다.** 잘린 것이 아니라 안 실린 것이다. `R0-3` 이 *"절단을
∞ 로 풀어도 6문항 불변"* 을 실측한 이유가 이것으로 설명된다 — 상한이 인과에 없다.

## 진짜 기전 — 두 겹이고 둘 다 절단 상한이 아니다

```
① markdown._free_text_lines 게이트
   20241114800538 은 free_text_amount 가 서술문에서 매매금액 1조 1,500억원 을 찾아
   schema_matched=True 가 됐고, 그 순간 정답이 든 `2. 주요내용` 이 통째로 안 실린다.
   🔴 원공시 20240604800108 은 금액이 없어 False → 같은 칸이 실린다.
      저장 근거에서 그 비대칭이 그대로 보인다 (원공시엔 `- 주요내용:` 이 있고 정정본엔 없다).
   그 게이트는 v3 R2-C 가 2,912건을 지키려고 **일부러** 세운 것이다.

② normalize.FREETEXT_MAX_CHARS = 300
   게이트를 열어도 그 칸(623자)의 440번째 자리에 있는 정답이 잘린다.
   실측: 300 ✗ · 400 ✗ · 500 ○  →  **1.5배 이상** 올려야 들어온다.
```

🔴 **한쪽만 고치면 Δ 가 0 이고, 그 0 을 "진단이 틀렸다" 로 읽게 된다.**
둘을 다 열면 정답이 실제로 들어온다(시뮬레이션 확인 · 948자).

## 🔴 판정 근거 — 대가가 코퍼스의 절반이다

| 조작 | 마크다운이 바뀌는 레코드 |
|---|---:|
| 게이트를 연다 | `schema_matched=True` **2,912건 중 1,417건 (48.7%)** |
| 상한 300 → 500 | `schema_matched=False` 238건 중 **171건** |
| **합** | **3,150건 중 1,588건 = 50.4%** |

마크다운은 곧 **BM25 색인 문자열**이다(`_free_text_lines` 독스트링: *"한 글자라도 바뀌면
측정된 arm 이 전부 무효"*). 겨냥은 `Q-087` **1문항**이고 대가는 v1·v2·v3 측정 전량 무효 +
저장 응답에 걸린 회귀 테스트군의 세대 교체다. **그래서 취소한다.**

되살리려면 **`R4` 코드 동결 뒤 새 세대에서** 독립 arm 으로 세워야 한다. 이름을 `A17` 로
두면 안 된다 — 그것은 '절단 규칙' 이고 이 처방은 '렌더 게이트' 다.

## 남긴 것 — `R1-11` 과 같은 방식

- `markdown._free_text_lines` 독스트링과 `normalize.FREETEXT_MAX_CHARS` 주석에 기각
  근거와 수를 적었다. **동작은 안 바꿨다** (주석·독스트링만).
- `tests/test_evidence_truncation.py` **5건 신설**로 고정했다:
  정답이 레코드엔 있고 마크다운엔 없다 · 절단 상한 둘 다 인과에 없다 · 게이트가 첫 겹이다 ·
  게이트만 열어도 회수 실패(두 겹) · **대가가 코퍼스 절반**(비율 부등식).
- 🔴 절대 수 대신 **비율의 부등식**으로 썼다 — 색인을 다시 빌드하면 절대 수는 흔들리지만
  *"절반쯤 바뀐다"* 는 결론은 그것으로 안 흔들린다.

## 겨냥을 어디로 옮기는가

`Q-087` 은 **검색 실패가 아니다**(정정본이 근거에 이미 있다). 그러므로 계획서가 말한
*"겨냥을 검색 쪽으로 옮긴다"* 도 맞지 않는다. 남는 자리는 **근거 렌더링** 하나이고,
그것은 위 이유로 v4 코드 동결 전에는 열 수 없다. `Q-087` 은 **v4 에서 처방 없음**이다
(`Q-093` 과 같은 칸).

---

# 전량 테스트

```
pytest -q   1,102 passed   (기준선 1,085 · 신설 17 · 실패 0)
  ① 뒤 1,097   (A20r 12건)
  ② 뒤 1,102   (A17 기각 5건)
tests/test_site.py 는 통과했다 (다른 에이전트 것이라 손대지 않았다).
```

---

# 🔴 확인하지 못한 것

1. **`A20r` 의 실제 Δ 는 안 쟀다.** `R2` 의 몫이다. 여기서 확인한 것은 *"사유가 프롬프트에
   실린다"* 와 *"재생성본이 깨끗하면 본문이 바뀐다"* 뿐이고, **모델이 그 사유로 실제로
   고치는지는 미측정**이다. `--dry` 스텁은 그것을 못 잰다.
2. 🔴 **`A20r` 의 비용은 발화 문항 수에 비례한다.** 현 세대 발화(계산군 + 출처 게이트)는
   그리드 88 에서 문항 몇 건 수준이지만, `verify_completeness` 의 `kinds` 도 재생성을
   깨우므로 **`R2` 전에 발화 문항 수를 세어 정지선을 잡아야 한다.** 여기서는 안 셌다.
3. **`provenance` 의 `reason` 이 같은 수치를 반복한다** — 답변이 그 수를 두 번 쓰면
   mismatch 가 두 건이라 `"…276,900,000,000원 이 근거에 없다"` 가 프롬프트에 두 번 실린다.
   무해하지만 프롬프트가 길어진다. 고치면 `A21` 의 각주 문자열이 바뀌어 그 arm 의 저장
   대조가 흔들리므로 **여기서 안 건드렸다.**
4. **재생성본의 판정에 `verify_completeness` 가 함께 걸린다.** 재생성이 계산 결함을
   고쳐도 완전성 사유가 남아 있으면 `kept=original` 이 된다 — 보수적이지만, `R2` 에서
   *"고쳤는데 안 채택된"* 건이 나오면 이 규칙을 다시 봐야 한다. 현 세대에서 그런 건이
   몇 건인지 **안 셌다.**
5. **`heldout` 미확인.** `evalset/` 을 열지 않았다. 모든 판정은 그리드 88 + 밸류체인 12 +
   저장 응답에 대해서만 유효하다.
6. **`A17` 기각의 수는 현 색인 기준이다.** 코퍼스를 다시 빌드하면 1,417·171 은 움직인다.
   그래서 테스트를 비율 부등식으로 썼지만, **비율 자체가 문턱 밑으로 내려가면 기각을
   다시 재야 한다** — 그 조건을 단언 메시지에 적어 뒀다.
