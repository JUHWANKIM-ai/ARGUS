"""Phase I — arm 스위치. 구성요소를 끄고 켠다. LLM 0회.

09-ablation §I-3 의 통제 조건이 이 모듈의 존재 이유다.

    고정 : 프롬프트 · 온도 0.0 · maxTokens · 골든셋 · 인덱스 스냅샷 · 모델
    변동 : arm 구성요소만

**스위치를 파이프라인 밖에 두면 통제가 깨진다.** arm 마다 코드를 갈아끼우거나 브랜치를
파면 프롬프트·상수까지 함께 흔들려 Δ 의 귀속이 불가능해진다. 그래서 분기점을 전부
이 한 곳의 플래그로 모으고, 파이프라인은 **같은 코드 그대로** 플래그만 읽는다.

기본값은 `A8` 다 — 완성 구성이 곧 현재 시스템이므로, 스위치를 안 건드리면 종전 동작이다.
**v1 의 `A6` 기본값과 플래그가 같다** (옛 `A6` 이 `counterparty_hop=True` 를 A4 로부터
물려받고 있었고, v2 가 그것을 A8 로 옮겨 이름만 바로잡았다). 운영 경로의 동작은 안 바뀐다.

## 플래그와 arm 의 대응

| 플래그 | 뜻 | A0 | A1 | A1b | A4 | A5 | A6 | A7 | A8 |
|---|---|---|---|---|---|---|---|---|---|
| `retrieval` | `meta` 프리필터 / `exchange` BM25 / `all` 인덱스 | meta | exchange | all | all | all | all | all | all |
| `schema_fields` | major·holding 추출 필드를 근거에 싣는가 | ✗ | ✗ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `dense` | **임베딩 하이브리드 (dense + RRF)** | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ |
| `hop_ladder` | 재검색 hop 1~3 · 이벤트 폴백 | ✗ | ✗ | ✗ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `chain_hop` | 정정·후속 공시 체인 hop | ✗ | ✗ | ✗ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `followup_hop` | 그 hop 안에서 **`※관련공시` 역방향 후속공시**까지 끌어오는가 | ✗ | ✗ | ✗ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `split_query` | 다지점 질의 분해 | ✗ | ✗ | ✗ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `calc_values` | 결정적 계산 결과를 근거에 싣는가 | ✗ | ✗ | ✗ | ✗ | ✓ | ✓ | ✓ | ✓ |
| `verifier` | `verify_calculation` + `verify_completeness` | ✗ | ✗ | ✗ | ✗ | ✗ | ✓ | ✓ | ✓ |
| `counterparty_hop` | 발주처 2-hop (밸류체인 fan-in/fan-out) | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | **✓** |

LOO 는 **A8**(= 완성 구성)에서 플래그 하나만 되돌린다 —
`L1 = A8 − chain_hop`, `L2 = A8 − schema_fields`, `L3 = A8 − counterparty_hop`,
`L4 = A8 − followup_hop`, `L6 = A8 − corp_name`, `L7 = A8 − rcept_dt`,
`L8 = A8 − group`.

## 🔴 메타 축 LOO (`L6`·`L7`·`L8`) — v3 의 *"구조적으로 측정 불가"* 가 틀렸다

v3 는 메타 프리필터(`corp_name`·접수일·인덱스 군)를 *"플래그로 표현할 자리가 없다"* 는
이유로 LOO 표에서 비워 뒀다. 그런데 표현 자리는 있었다 — **`search_chunks` 의 인자**다.

    pipeline  `args = {"query": …, "top_k": …, **cell}` + `corp_name` + `group`
    tools     `filters["corp_name"]` · `prefix["rcept_dt"]` · `_group_index(group)`
    LOO       **그 인자를 넘기지 않는다**

그래서 이 셋은 검색 단계를 끄는 것이 아니라 **필터 축 하나를 안 거는 것**이다. 축이
빠지면 BM25 는 그대로 돌고 후보 집합만 넓어진다 — `A1`(BM25 도입)과 방향이 반대다.

    `meta_corp`   `corp_name` 필터        — 기업 축
    `meta_date`   `ym`·`year`·`rcept_dt`  — 접수일 접두 축 (사업연도 `base_year` 는 **아니다**)
    `meta_group`  `group`                 — 인덱스 군 축 (빠지면 `exchange` 로 떨어진다)

🔴 **`base_year`·`doc_subtype` 은 건드리지 않는다.** 정기공시의 사업연도는 접수일이 아니고
(`query.plan_section_hops` 주석 1번), 함께 떼면 `L7` 이 단일 변수 조작이 아니게 된다.

🔴 **`META_UNREACHABLE_FLAGS` 에 넣을 수 없다.** 저 목록은 `True = 그 구성요소를 켠다`
전제로 '켜져 있으면 예외' 를 던지는데, 메타 축 플래그는 **기본값이 `True`(= 축을 넘긴다)**
라서 넣는 순간 `A0` 자신이 즉시 예외가 된다. 대신 `META_UNREACHABLE_AXES` 가
`ORACLE_UNREACHABLE_FLAGS` 와 같은 **'기본값에서 움직였는가'** 형태로 막는다.

## 🔴 `followup_hop` 을 `chain_hop` 에서 떼어낸 이유 — 두 엣지가 한 hop 에 섞여 있었다

`CHAIN_HOP` 은 `get_history` **한 번**으로 두 종류를 함께 돌려준다:

    chain      `공시 —정정한다→ 공시`   550엣지 (`record["related"]`)
    followups  `공시 —관련→ 공시`       1,267엣지 (`※관련공시` 역방향 `_followup_index`)

그래서 `L1`(= `chain_hop` 제거)의 **−9.7%p 는 두 엣지의 합**이고, 어느 쪽 몫인지 갈린 적이
없다. `tools.get_history(include_followups=…)` 가 이미 있으므로 **플래그 하나로 갈린다.**

🔴 **다만 판정력은 실측으로 이미 좁혀졌다 (2026-08-12, LLM 0회 · 0원).** A8 산출물의
`think_trace` 에서 `CHAIN_HOP` 씨앗을 뽑아 `get_history` 를 다시 부른 결과:

    grid+회귀 88문항   CHAIN_HOP 발화 16문항 · 고유 씨앗 15 · followups 를 반환한 씨앗 **1**
    밸류체인 12문항    발화 1문항(Q-113)      · 고유 씨앗  1 · followups **0**

**즉 `L4` 의 Δ 상한은 1문항(Q-035, ±1.4%p)이고 87/88 은 프롬프트가 바이트 동일해
캐시 전량 적중이다 — 예상 비용 13~15원.** 재는 값이 있는 이유는 그 15원으로
"`REFERS_TO` 는 이 코호트에서 기여 0" 을 **주장이 아니라 측정**으로 바꿀 수 있기 때문이다.

🔴 **위 두 예측이 다 틀렸다 — 실측으로 정정한다.**
① **비용**: 13~15원이 아니라 R1 에서 51.1원(43.5+7.6), R2D 재측정에서 72.7원이었다.
② **Δ 의 자리**: R1 에서는 그리드 −1.4%p 였는데, R2D 재측정 후 **그리드는 66/72 로 A8 과
같아졌고**(Δ 0.0%p) 차이가 **회귀 트랙으로 옮겨갔다**(A8 10/10 · L4 9/10). 옮겨간 문항은
`Q-035` 가 아니라 **`Q-066`** 이다 — R2 ④ 가 `terminates` 순방향 엣지를 연 뒤
`include_followups` 로 **함께** 가리므로, `L4` 가 끄는 것이 이제 그 엣지까지다.
따라서 *"`REFERS_TO` 계열의 기여는 회귀 트랙 1문항"* 이 현재의 측정값이고,
**그리드 기준으로 인용하면 0 이 된다.** 분모를 반드시 밝혀서 인용한다.

🔴 **기회가 왜 1건뿐인가 — 씨앗 조건이다.** `_chain_seeds` 는 `is_latest=False` 이고
`links` 가 있는 후보를 씨앗으로 삼는다 — **다만 예외가 하나 생겼다** (`6d6de5d`, R2 ④):
문서에 `terminates` 가 있고 **질문이 원계약을 물을 때는** `is_latest=True` 후보도 씨앗이
된다. `_followup_index` 는 키 478(원계약) · 값 1,233 이고 그중 대체본은 301 인데,
**해지 공시는 `is_correction=False` 라 `related` 가 비므로** (`corpus/corrections.py`)
원계약이 **정정까지 받은 경우**에만 종전 경로로 씨앗이 된다.
평가셋의 해지·철회 문항은 dev 7 · 회귀 2 · 밸류체인 2 = **11문항**인데 후속공시가 실릴
기회는 1건이다.

🔴 **`A9`(씨앗 조건 일반화)는 재고 후 기각됐다 — arm 으로 세우지 않는다.** R2A ④ 가
세 안을 100문항·골든 179 전수로 재 보니 **셋의 골든이 같았다**(160→161). `_followup_index`
키까지 씨앗으로 넓히는 일반형이 `terminates` 하나보다 더 회수하는 골든이 **0건**이고,
늘어나는 것은 `Q-035` 의 근거 18자뿐인데 근거를 바꾸는 문항 수는 가장 많다 — **얻는 것
없이 희석만 늘린다.** 그래서 채택한 것은 위의 좁은 예외(`terminates` + 질문 게이트)이고,
그것이 회귀 `Q-066` 을 실제로 되살렸다(R2D 재측정에서 정답으로 뒤집혔다).
**되살리는 조건**: 새 heldout 18문항에 해지·철회 문항이 늘어 `_followup_index` 키가
상위 후보로 올라오는 문항이 2건 이상 생기면. `L4` 와 섞지 않는다.

## 🔴 `A7` 은 플래그가 없다 — 이름만 있는 칸이다

`A7`(거절 강제 + 시점 지정)은 **무조건 실행되는 코드**다 (`router.missing_named_report` ·
`pipeline._as_of_ym`). 플래그로 끌 수 없으므로 `A7` 의 구성은 `A6` 과 **글자 하나 다르지
않다**. 따라서 사다리에서 `Δ(A6→A7)` 은 **측정값이 아니라 정의상 0** 이다 — 그렇게 읽어야
한다. A7 의 실효는 §I-5d 가 `A6`(옛 코드) vs `A7`(새 코드) 를 42문항 코호트로 이미 쟀다
(grid +2.8%p · 환각 1→0). 여기 칸을 남겨 두는 이유는 사다리의 번호를 §I-5d 와
맞추고, `L3 ≡ A7` 이라는 항등식을 눈에 보이게 하기 위해서다.

## 🔴 `counterparty_hop` 을 A8 에 세우는 이유 — A4 에 두면 Δ 가 사라진다

v1 은 이 플래그를 `A4` 에서 켰다. 그런데 `A0` 은 `uses_bm25=False` 조기 반환이 발주처
hop **앞에** 있어서 `A0 + counterparty_hop=True` 로 강제해도 발화가 0 이다(실측). 그래서
"A4 에서 켠 것" 은 사다리 어디에서도 자기 Δ 를 못 낸다 — A1b→A4 전이에 hop_ladder ·
chain_hop · split_query 와 함께 묻힌다.

`counterparty_hop` 은 `hop_ladder` 로부터 **독립**임이 검증됐다(`A1b + cp=True`, ladder
off 에서 발화 확인). 그래서 v2 는 이 플래그만 A8 로 옮겼다. `A8 = A7 + counterparty_hop`
이 BM25 이후 단계에 서므로 Δ 가 이 hop 하나에 귀속된다.

## `dense` 가 누적 사다리 밖에 있는 이유 — G1 격리

CONVENTION **G1**("LLM 은 HyperCLOVA X 만") 의 임베딩 적용 여부가 **디스코드 미확정**이다.
그래서 `dense` 는 **사다리 어느 칸에도 켜져 있지 않다.** A2 는 곁가지 두 개로만 존재한다.

    A2  = A1b + dense      §I-2 가 정의한 자리에서의 한계 기여도
    A2F = A6  + dense      **제출 구성에서의** 한계 기여도 (leave-one-in)

이렇게 두면 G1 이 불허로 확정돼도 `A0→A1→A1b→A4→A5→A6` 곡선이 **글자 하나 안 바뀐 채**
성립한다. 반대로 dense 를 A2 이상 전 arm 에 켜 두면 A4·A5·A6 이 전부 재측정 대상이 되고,
불허 시 v1 결과가 통째로 무효가 된다.

**A6 대비 Δ 를 재는 것은 `A2F` 다.** §I-4b 가 A6 을 새 기준선으로 삼았기 때문이다.
`A2` 는 §I-2 표의 칸을 채우기 위한 것이고, 둘 다 검색 단계는 LLM 0회로 잰다.

## `calc_values` 를 '제거' 가 아니라 '대체' 로 잰 이유

계산 도구 7종은 `TOOL_SPECS` 로 노출돼 있지만 **사다리의 생성 호출에는 `tools` 를 넘기지
않는다** (`pipeline.answer_question` → `chat(..., max_tokens=…)`). 따라서 사다리 위에서
"function calling 을 끈다" 는 조작은 실행 경로에서 아무것도 바꾸지 못한다 — Δ 가 정의상
0 이 되는 헛측정이다.

🔴 **v4 에서 그 '켜는' 방향이 `A24` 라는 곁가지로 따로 생겼다** (`function_calling` 플래그).
`A5` 의 축과 헷갈리면 안 된다: `A5` 는 *"근거에 박아 둔 계산 결과를 뗀다"* 이고 `A24` 는
*"HCX-005 자신이 도구를 부르게 한다"* 다. 두 조작은 같은 자리를 건드리지 않는다.

실제로 계산 결과가 생성에 도달하는 경로는 하나뿐이다. `corpus/markdown.to_markdown` 이
근거 블록에 **결정적으로 계산한 값**을 박아 넣는다.

    - 계약금액: 22,764,764,160,000원 (227,648억원)      ← 단위 환산
    - 기간: 2025-07-24 ~ 2033-12-31 (8년 5개월)          ← compute.period_parts

그래서 A5 의 조작은 이 두 괄호를 떼어 **LLM 이 직접 계산하게 만드는 것**이다. 부록 D-3 의
HippoRAG 방식(제거가 성립하지 않는 요소는 '더 단순한 것으로 대체')과 같다. 실측 근거도
정확히 이 지점이다 — HCX-005 는 `2025-07-24 ~ 2033-12-31` 을 Direct·CoT 모두 `9년 5개월`
로 답했다 (정답 `8년 5개월`).

**`ratio` 는 떼지 않는다.** `- 매출액 대비: 7.6%` 의 분모(최근매출액)는 근거에 실리지
않으므로, 이걸 떼면 '계산을 LLM 에게 넘긴' 것이 아니라 **정보를 지운** 것이 된다.
계산 ablation 이 정보 ablation 으로 둔갑하면 Δ 의 귀속이 틀어진다.

## 🔴 `ORACLE` — 검색을 고정하고 **생성만** 재는 곁가지 (v3 신설 · 사다리 밖)

`A0`~`A8` 은 전부 "검색 + 생성" 의 **합성** 성능이다. 그래서 잔여 오답이 검색 몫인지
생성 몫인지 사다리만으로는 못 가른다 — v2 A8 의 잔여 11건 중 **8건이 `GENERATION`
단계**로 귀속돼 있는데, 그 라벨은 "골든이 프롬프트에 실렸는데 답이 틀렸다" 라는
**소거법의 결과**이지 생성 실패를 직접 관측한 것이 아니다.

`ORACLE` 은 검색 전체를 **골든 근거 주입**으로 갈아치운다.

### 🔴 이 arm 이 **실제로** 재는 것 (R2E 재측정으로 계약이 갈렸다 · 2026-08-14)

R1 은 이 arm 의 계약을 아래 두 줄로 적었다. **둘 다 그대로 인용하면 안 된다.**

    ~~Δ(A8 → ORACLE)      검색이 아직 못 가져온 몫~~     ← 🔴 이 세대에서 깨졌다
    ~~ORACLE 의 잔여 오답  순수 생성 실패~~              ← 🔴 조건이 붙는다

`ORACLE` 은 **"A8 + 완벽한 검색" 이 아니다 — "A8 의 근거를 골든만으로 *교체*한 것"** 이다.
주입은 근거를 **더하지 않고 갈아치우므로** 근거가 오히려 **줄어든다** — 실측 전체 88 에서
292,127자 → 155,687자(**−47%**)다. R2E 는 `A8` 과 정오가 갈린 **7문항 전수**에서
① 골든 회수가 `A8` 과 **동일**하고 ② 오라클 근거가 **더 작다**(레코드 10→5 · 8→1 · 3→2)를
확인했다. 즉 그 Δ 는 검색 부족분이 아니라 **비-골든 보조 근거를 걷어낸 효과**이고,
두 항이 분리되지 않는다.

    지금 이 arm 이 재는 것    "근거를 골든만으로 한정했을 때 답이 서는가"
    Δ(A8 → ORACLE)          🔴 검색 부족분과 보조근거 제거 손실의 **합**. 한 항으로 못 읽는다
    잔여 오답 = 생성 실패     🔴 근거가 A8 과 **바이트 동일**한 문항에서만 정의상 성립한다
                            (그리드 72 에서 `Q-093`·`Q-097` 둘)

**여전히 성립하는 판정은 하나이고, 그것이 이 arm 을 세운 값이다** — *"골든을 전건
주입해도 정답이 안 늘면 남은 실패는 검색으로 살 수 없다."* 밸류체인이 가장 날카롭다:
골든 회수 **34 → 46/46(+12건)** · `RETRIEVAL` **3 → 0** 인데 정답률이 **7/12 불변**
(0.0%p)이고, 사라진 세 건은 그 자리에서 `GENERATION` 으로 내려앉았다.

🔴 **이 arm 의 진단 가치는 `A8` 이 잘해질수록 줄어든다.** `A8` 의 골든 회수가 전체 88 에서
126/133 까지 올라왔으므로 오라클이 살 수 있는 검색 개선분은 **7건**뿐이고, 그 7건은
정답을 바꾸는 문항이 아니었다. 정본 서술은 `docs/pipeline/09-ablation.md` §I-5f ④ ·
`.superpowers/sdd/2026-08-12-ablation-v3/R2E-report.md` §1.

🔴 **`ORACLE` 은 제출 구성이 될 수 없다.** 평가 시점에는 골든이 없다.
🔴 **그리고 이제 상한도 아니다** — 그리드 72 에서 `A8` 66/72 · `ORACLE` 64/72 다. 이 arm 의 정답률을
게이트·제안서에 '성적' 으로 인용하면 자기 성적표를 부풀리는 것이 된다 (그래서
`DIAGNOSTIC_ARMS` 에 넣어 두고, 러너의 요약이 그 사실을 표 옆에 찍는다).
같은 이유로 `LADDER` 에도 `LOO_REMOVED` 에도 넣지 않는다 — `A2`/`A2F` 와 같은 곁가지다.

**골든은 러너가 주입한다.** 파이프라인은 `question_id` 만 알고 evalset 을 모른다. 주입
없이 arm 만 켜면 근거가 조용히 비어 `UNANSWERABLE` 이 나가고, 그것은 "생성이 다 틀렸다"
로 읽힌다 — 이 저장소가 최악으로 보는 **조용한 Δ** 다. 그래서 `oracle_golden_for()` 는
빈 값을 돌려주지 않고 `OracleGoldenMissing` 을 던진다.

## 🔴 `A8-lean` — 생성 프롬프트에서 **규칙 8·9 를 뺀다** (v3 신설 · 사다리 밖 곁가지)

`A8` 과 **플래그 하나만** 다르다. 그 하나가 `prompts.MONEY_UNIT_RULE`(규칙 8·9)을
시스템 프롬프트에서 뗀다. **`LADDER` 에도 `LOO_REMOVED` 에도 넣지 않는다** — `A2`/`A2F`/
`ORACLE` 과 같은 곁가지이고, 같은 자리(`A8`)에서 프롬프트만 갈아 끼우는 변형이다.

근거는 KDD '26 `paper/idea/QA.pdf` Figure 2 의 주의 배분이다 —
`system 36.9% · output 34.5% · **근거 14.0%** · question 10.8%`. **시스템 프롬프트가
근거보다 2.6배** 무겁다. HCX 는 attention 을 못 보지만 **대리 실험은 된다** — 규칙을 줄이고
정답률을 재는 것이다. 실측 겨냥은 `Q-018` 이고 기전은 R2+ 가 현 세대에서 재현했다
(`.superpowers/sdd/2026-08-12-ablation-v3/R2plus-decision.md` §4):

    split_trillion_eok(Q-018 근거) -> 변환 2건  =>  규칙 8·9 가 붙는다 (system 735 -> 959자)
    답변 어휘  "최초 공시 금액" / "최종 정정 금액"      ->  `계약금액` 이 없다
    critic._says_amount("계약금액") = False   =>  amount 검사가 꺼진다 (gated 2레코드)
    근거에 정답값 1,842,200,000,000 이 **있고** 답변은 1,843,200,000,000 을 썼다

🔴 **뺀 것은 규칙 문안뿐이고 `split_trillion_eok` 는 그대로 둔다.** 괄호를 조·억으로
가르는 것은 근거 성형(`calc_values` 축)이고 규칙 8·9 는 그 위에 얹은 프롬프트 보강이다.
둘을 함께 떼면 단일 변수 조작이 아니게 되고, `Q-065`(콤마 한 자리 밀림 → 답이 10배)를
지키던 **1차 방어선까지** 사라진다.

🔴 **인질을 세어 두고 세운다.** 규칙 8·9 가 붙는 문항은 전체 88 중 **16**(밸류체인 12 중
**10**)이고, 그중 **현재 정답이 12건**(밸류체인 5건)이다. 겨냥은 1건(`Q-018`)이므로 이
arm 은 **잃을 것이 얻을 것보다 12배 많은** 조작이다 — 성적이 떨어지면 그것이 기각이고,
되돌릴 순서는 `tests/test_prompt_rules.py` 의 `MONEY_RULE_EXPOSED_WHILE_CORRECT` 가 든다.

## 근거 성형은 **생성 직전에만** 한다

`schema_fields` · `calc_values` 는 프롬프트에 실릴 근거 문자열에만 건다. 검색·충분성
판정(`_sufficiency`)은 원본 마크다운을 그대로 본다 — 여기까지 바꾸면 A5·L2 의 Δ 에
검색 변화가 섞여 단일 변수 조작이 아니게 된다.

`ORACLE` 의 근거에도 **같은 성형이 그대로 걸린다** (`answer_question` 이 근거 출처와
무관하게 `shape_evidence` 를 한 번 부른다). 그래서 `schema_fields`·`calc_values`·`verifier`
는 오라클 위에서도 살아 있고, `ORACLE_UNREACHABLE_FLAGS` 에 들어가지 않는다.
"""
from __future__ import annotations

import re
from contextlib import contextmanager
from dataclasses import dataclass, replace

#: 검색 바닥. `meta` 는 BM25 없이 manifest·universe 필터만 쓴다 (§I-1).
RETRIEVAL_MODES = ("meta", "exchange", "all")

#: `retrieval="meta"` 에서 **코드가 도달하지 않는** 플래그. `pipeline._gather_evidence` 는
#: `uses_bm25` 가 False 면 `_gather_meta` 직후 `return` 이라 사다리·체인·분해·발주처 hop 이
#: 전부 그 아래에 있다. 켜도 아무 일이 안 일어나므로 **Δ 가 조용히 0** 이 된다 —
#: `enabled()` 가 오타 하나에 예외를 던지는 것과 같은 종류의 방어를 여기도 걸어야 한다.
#:
#: 🔴 이 함정은 이미 한 라운드를 썼다. 원장 C3/④ 가 `A0 + counterparty_hop=True` 로 강제해도
#: 발화가 0 임을 실측하고 나서야 계획을 고쳤다. 계획서 '확정된 사항 ②' 는 A8 한 arm 에
#: 대해서만 "BM25 이후에 세워라" 라고 지시했고, 같은 구조적 의존이 나머지 셋에도 있다는
#: 일반화는 하지 않았다. 이 상수가 그 일반화다.
META_UNREACHABLE_FLAGS = ("hop_ladder", "chain_hop", "split_query", "counterparty_hop",
                          "followup_hop")

#: 🔴 `retrieval="meta"` 에서 **기본값에서 움직이면 안 되는** 축. 뜻은
#: `META_UNREACHABLE_FLAGS` 와 같고 **검사 형태만 다르다** — 저 목록은 `True = 켠다` 를
#: 전제로 '켜져 있으면 예외' 인데, 메타 축 LOO 플래그는 기본값이 `True`(= 축을 넘긴다)라
#: 같은 목록에 넣으면 `A0` 자신이 즉시 예외가 된다. 그래서
#: `ORACLE_UNREACHABLE_FLAGS` 와 같은 '기본값에서 움직였는가' 형태로 검사한다.
#:
#: 근거는 코드다: `_gather_meta` 는 `search_chunks` 를 **한 번도 부르지 않고**
#: `filter_docs` 만 쓴다. 이 축들은 `search_chunks` 인자에서만 떨어지므로 `A0` 에서
#: 끄면 근거가 한 글자도 안 바뀐다 — `META_UNREACHABLE_FLAGS` 와 똑같은 **조용한 Δ 0**.
META_UNREACHABLE_AXES = ("meta_corp", "meta_date", "meta_group")

#: 🔴 **플래그 사이의 도달 의존.** 키가 꺼지면 값들도 반드시 함께 꺼져야 한다 —
#: 부모 hop 안에서만 읽히는 플래그라 부모 없이 켜면 `META_UNREACHABLE_FLAGS` 와 똑같이
#: **Δ 가 조용히 0** 이 된다. `__post_init__` 이 이 사전으로 막고, LOO 의 '단일 변수 조작'
#: 검사도 이 사전을 읽어 **부모와 함께 움직인 자식은 예외로 허용**한다.
#:
#: 산문으로만 적어 두면 검사가 그것을 못 읽는다 — 그래서 데이터로 둔다.
#: 🔴 `calc_values -> lean_rules` 는 **실측으로 확인한 의존**이다. `shape_evidence` 가
#: `calc_values=False` 에서 `- 계약금액: …원 (49,290억원)` 의 괄호를 떼므로
#: `prompts.split_trillion_eok` 이 변환할 자리가 사라지고, 그러면 규칙 8·9 가 **애초에
#: 붙지 않아** 그것을 빼는 조작이 아무것도 바꾸지 못한다. 저장 응답 전수로 셌다 —
#: `calc_values=False` 로 성형한 뒤 변환이 남는 문항은 전체 88 · 밸류체인 12 · ORACLE 88
#: **모두 0건**이다.
#: 🔴 `verifier -> substitute_verified` 는 **구조적 의존**이다. `pipeline._verify` 가
#: `if not ablation.current().verifier: return answer` 로 첫 줄에서 나가므로 검증 단계가
#: 꺼진 arm 에서 치환 플래그를 켜면 코드가 도달하지 않는다.
#: 🔴 `verifier -> provenance_gate` 도 같은 구조적 의존이다 — `A21` 의 게이트가
#: `_verify` 안에 있으므로 검증 단계가 꺼진 arm 에서는 코드가 도달하지 않는다.
#: 🔴 `verifier -> regenerate_on_verify` 도 같다 — `A20r` 의 재생성은 `_verify` 가
#: 결함을 찾은 **뒤에** 서므로 검증 단계가 꺼지면 재생성이 한 번도 안 돈다.
#: 🔴 `counterparty_hop -> multi_hub` 도 구조적 의존이다 — `A22` 의 추가 허브는
#: `_gather_evidence` 의 **발주처 차선 안**에서만 결속되므로, 그 hop 이 꺼진 arm
#: (`A0`~`A7`·`L3`)에서 켜면 코드가 도달하지 않는다.
#: 🔴 `function_calling -> calc_tools_only`·`calc_queries_only`·`substitute_tool_results`
#: 도 구조적 의존이다 — 셋 다 `_answer_with_tools`/`_offer_tools` **안에서만** 읽히므로
#: 도구를 안 내미는 arm 에서 켜면 코드가 도달하지 않는다 (`A24b` 의 하위 옵션들).
FLAG_DEPENDENCIES: dict[str, tuple[str, ...]] = {
    "chain_hop": ("followup_hop",),
    "calc_values": ("lean_rules",),
    "counterparty_hop": ("multi_hub",),
    "function_calling": ("calc_tools_only", "calc_queries_only",
                         "substitute_tool_results"),
    "verifier": ("substitute_verified", "provenance_gate", "regenerate_on_verify")}

#: 🔴 `golden_oracle=True` 에서 **읽히지 않는** 축. 검색 차선을 통째로 골든 주입으로
#: 갈아치우므로 이 플래그들을 건드려도 근거가 한 글자도 안 바뀐다 —
#: `META_UNREACHABLE_FLAGS` 와 같은 종류의 **조용한 Δ 0** 이다.
#:
#: 다만 여기서는 '켜면 안 된다' 가 아니라 **'기본값에서 움직이면 안 된다'** 로 막는다.
#: `ORACLE = A8 + golden_oracle` 이라 이 축들이 전부 켜져 있는 것이 정상이고, 문제는
#: `L1 + golden_oracle` 처럼 **오라클 위에서 검색 LOO 를 흉내내는 것**이다. 그 Δ 는
#: 정의상 0 이고, 0 을 "그 구성요소는 기여가 없다" 로 읽는 사고가 이 모듈의 존재 이유다.
#: 기준값은 `Arm` 의 **필드 기본값**이다 (= A8, 머리말 참조 — 전방참조가 필요 없다).
#: 🔴 메타 축 셋도 여기 있다. `golden_oracle` 은 exchange 계열에서 `search_chunks` 를
#: 아예 안 부르고, 정기공시에서는 `doc_id` 로 골든 문서를 못박아 부르므로 `corp_name` ·
#: 접수일 접두 · `group` 어느 것도 후보 집합을 안 바꾼다.
ORACLE_UNREACHABLE_FLAGS = ("retrieval", "dense", "hop_ladder", "chain_hop",
                            "split_query", "counterparty_hop", "followup_hop",
                            "meta_corp", "meta_date", "meta_group", "multi_hub")


@dataclass(frozen=True)
class Arm:
    """한 arm 의 구성. 얼려 둔다 — 실행 중에 바뀌면 Δ 의 귀속이 깨진다."""

    name: str
    retrieval: str = "all"
    schema_fields: bool = True
    #: A2 전용. 기본값이 False 인 것이 G1 격리의 전부다 — 스위치를 안 건드린 운영
    #: 경로에서는 임베딩 API 가 **한 번도 불리지 않는다** (질의 임베딩 포함).
    dense: bool = False
    hop_ladder: bool = True
    chain_hop: bool = True
    #: 발주처(counterparty) 2-hop. **`hop_ladder` 와 따로 둔다** — 묶어 두면 A0·A1b 에서
    #: 함께 꺼져 이 hop 만의 Δ 를 분리해서 잴 수 없다. `chain_hop` 을 따로 둔 것과 같은
    #: 이유이고, L3(= 발주처 hop 만 뺀 LOO)이 성립하려면 독립 플래그여야 한다.
    #: **사다리에서는 A8 에서만 켜진다** (위 머리말 참조 — A4 에 두면 Δ 가 묻힌다).
    counterparty_hop: bool = True
    split_query: bool = True
    calc_values: bool = True
    verifier: bool = True
    followup_hop: bool = True
    #: 🔴 **메타 축 LOO 셋 (`L6`·`L7`·`L8`) · v4 신설.** 다른 플래그와 달리 검색 단계를
    #: 끄는 것이 아니라 **`search_chunks` 에 그 인자를 안 넘긴다.** BM25 는 그대로 돌고
    #: 후보 집합만 넓어진다 (머리말 §메타 축 LOO 참조).
    #:
    #: 🔴 기본값 `True` 가 종전 동작이다 — 스위치를 안 건드린 운영 경로는 인자가 한 개도
    #: 안 빠진다. `META_UNREACHABLE_AXES`·`ORACLE_UNREACHABLE_FLAGS` 가 도달 불가 자리를
    #: 막는다 (`retrieval="meta"` 는 `filter_docs` 만 쓰고, `golden_oracle` 은 골든 주입).
    #:
    #: `meta_corp`  — `corp_name` 필터 (`tools.search_chunks` → `filters["corp_name"]`)
    meta_corp: bool = True
    #: `meta_date`  — 접수일 접두 (`ym`·`year`·`rcept_dt` → `prefix["rcept_dt"]`).
    #: 🔴 정기공시의 `base_year`(사업연도)는 **다른 축**이라 함께 떼지 않는다.
    meta_date: bool = True
    #: `meta_group` — 인덱스 군 (`group`). 빠지면 `tools.search_chunks` 의
    #: `target = group if group in RECORD_GROUPS else "exchange"` 가 exchange 로 떨어진다.
    meta_group: bool = True
    #: 🔴 **ORACLE 전용 · 진단용이고 제출 구성이 될 수 없다** (평가 시점에 골든이 없다).
    #: 참이면 `pipeline.answer_question` 이 검색 대신 골든 근거를 싣는다. 기본값 False 가
    #: G1 격리(`dense`)와 같은 역할을 한다 — 스위치를 안 건드린 운영 경로는 골든을
    #: **한 번도 열지 않는다** (러너가 주입하지 않으면 매핑 자체가 없다).
    golden_oracle: bool = False
    #: 🔴 **`A8-lean` 전용 · 이 모듈에서 유일하게 극성이 뒤집힌 플래그다.** 다른 플래그는
    #: `True = 그 구성요소를 켠다` 인데 이것은 `True = 규칙을 뺀다` 다. 이름의 `lean` 이
    #: 그 사실이다 — `money_unit_rule` 처럼 구성요소로 이름 붙이면 사다리 A0~A4 가
    #: `calc_values=False` 라서 기본값을 함께 뒤집어야 하고, 그러면 이미 공표된
    #: `Δ(A4→A5)` 의 플래그 표가 두 칸 움직여 v1·v2 산출물의 설명이 낡는다.
    #: 참이면 `prompts.build_answer_messages` 가 `MONEY_UNIT_RULE`(규칙 8·9)을 붙이지
    #: 않는다. 기본값 False 가 **규칙 전량 = 종전 동작**이고, 스위치를 안 건드린 운영
    #: 경로의 프롬프트는 한 바이트도 다르지 않다.
    #: 🔴 `calc_values=False` 와는 함께 켤 수 없다 (`FLAG_DEPENDENCIES` — Δ 가 조용히 0).
    lean_rules: bool = False
    #: 🔴 **`A20` 전용.** 참이면 `pipeline._verify` 가 ① `critic.verify_answer_arithmetic`
    #: 를 라이브 경로에 배선하고 ② 검사가 낸 **유일한** 재계산값으로 답변의 그 수치를
    #: 치환한다. 기본값 `False` 가 종전 동작이다 — 결함을 찾아도 각주만 붙인다.
    #:
    #: 🔴 **기본값을 바꾸지 않는 것이 중요하다.** 현 동작은 버그가 아니라 명시적 설계고
    #: 근거가 있다 (`pipeline.py` A6 머리말 · SWE-Lancer 부록 C: 판단 44.9% > 생성 26.2%).
    #: 그러니 갈아엎지 말고 **arm 으로 만들어 켰다 껐다 잰다.**
    #:
    #: 🔴 배선을 이 플래그 뒤에 둔 것은 **실측 때문이다.** 무조건 배선하면 플래그를 끈
    #: 상태에서도 v3 세대 답변에 각주가 새로 붙는다(전수 대조 실측) — 그 자체가 답변
    #: 변화이고 `A20` 의 Δ 와 섞인다. 계획서 `R1-1` Step 0b 가 그 경우 "배선도 플래그
    #: 뒤에 두라" 고 지시했다.
    #:
    #: 🔴 `verifier=False` 와는 함께 켤 수 없다 (`FLAG_DEPENDENCIES` — 검증 단계가 아예
    #: 서지 않으므로 Δ 가 조용히 0 이 된다). `META_UNREACHABLE_FLAGS` ·
    #: `ORACLE_UNREACHABLE_FLAGS` 에는 **넣지 않는다** — `_verify` 는 `_gather_evidence`
    #: 뒤에 있고 골든 주입 위에서도 그대로 돈다.
    substitute_verified: bool = False
    #: 🔴 **`A21` 전용.** 참이면 `pipeline._verify` 가 `critic.verify_number_provenance`
    #: 를 라이브 경로에 배선한다 — 답변의 12자리 이상 금액이 근거에 완전일치로 있는지
    #: 본다. 기본값 `False` 가 종전 동작이다 (게이트 자체가 v4 신설이다).
    #:
    #: 🔴 **`substitute_verified`(A20)와 묻는 것이 다르다.** 저쪽은 *"계산이 맞는가"* 고
    #: 이쪽은 *"이 숫자가 근거에 있는가"* 다. 그래서 `A15`(`_says_amount` 게이트)의
    #: 대체 후보이지 `A20` 의 변형이 아니다. 두 플래그는 서로 독립이다.
    #:
    #: 🔴 `verifier=False` 와는 함께 켤 수 없다 (`FLAG_DEPENDENCIES` — `_verify` 가
    #: 첫 줄에서 나가므로 Δ 가 조용히 0 이 된다). `ORACLE_UNREACHABLE_FLAGS` 에는
    #: **넣지 않는다** — 골든 주입 위에서도 `_verify` 는 그대로 돌고, 실제로 현 세대
    #: `ORACLE` 에서 이 게이트가 한 건 발화한다(`Q-023`).
    provenance_gate: bool = False
    #: 🔴 **`A20r` 전용.** 참이면 `pipeline._verify` 가 결함을 찾았을 때 각주를 붙이는
    #: 대신 **실패 사유를 프롬프트에 실어 답변을 한 번만 다시 생성한다.**
    #:
    #: 🔴 **사유 주입이 이 플래그의 전부다.** 온도 0.0 에서 같은 프롬프트로 다시 부르면
    #: 결정적이라 **같은 답이 나온다 — no-op 이다.** 독립 검토 둘이 초안의 그 결함을
    #: 같이 찾았고(`base` `05df07b` · `multi_agent` `33c3d99`), 사유 되먹임은 AutoGen 의
    #: board agent 처방이며(G34·G36 · CAMEL C34) **SWE-Lancer 가 *"사유를 안 넣으면 같은
    #: 답이 반복된다"* 를 실측했다**(S21·S22).
    #:
    #: 🔴 **온도는 0.0 그대로다.** 다양성을 사는 조작은 `A10`(SC k=3)의 것이고 이 arm 이
    #: 재는 것은 **진단 주입**이다. 둘은 같은 축의 변형이 아니라 서로 다른 기전이라
    #: 나란히 재야 하고, 한쪽이 통제 변수를 깨면 그 비교가 성립하지 않는다.
    #:
    #: 🔴 **`substitute_verified`(A20)와 배타는 아니지만 `A20r` 은 그것을 켜지 않는다** —
    #: 계획서가 *"치환 대신 재생성"* 으로 두 처분을 **나란히** 재라고 지시했다. 재생성이
    #: 켜지면 `verify_answer_arithmetic` 배선은 `A20` 과 같이 선다 (사유의 원천이 필요하고,
    #: 실측 `Q-044` 는 정형 레코드가 0건이라 그 검사 없이는 발화조차 없다).
    #:
    #: 🔴 재생성은 **1회**다. 두 번째를 열면 상한이 없어지고 문항당 비용이 흔들린다.
    #: 🔴 재생성 결과에 검사가 **여전히 발화하면 원본을 쓴다.**
    #: 🔴 `verifier=False` 와는 함께 켤 수 없다 (`FLAG_DEPENDENCIES`).
    regenerate_on_verify: bool = False
    #: 🔴 **`A18`(= `T1-targeted`) 전용.** 참이면 `agent/tools._group_records` 가
    #: `artifacts/nary_records.jsonl` 의 LLM 추출값을 근거 마크다운에 **덧붙인다.**
    #: 기본값 `False` 가 종전 동작이고, 파일이 없으면 켜도 아무 일이 안 일어난다.
    #:
    #: 🔴 **덮어쓰기가 아니라 합집합이다.** 규칙이 이미 뽑은 라벨(`금액`·`계약상대`·
    #: `기간`·`대상`)이 그 레코드의 마크다운에 있으면 그 항목은 **건너뛴다.** 규칙의
    #: 산출물을 LLM 산출물로 갈아치우면 이 arm 이 재는 것이 '추가' 가 아니라 '교체' 가
    #: 되고, `L2`(노드 속성 LOO)의 −15.3%p 와 겨냥이 겹쳐 Δ 의 귀속이 깨진다.
    #:
    #: 🔴 **코퍼스 파일은 한 바이트도 안 바뀐다.** 덧붙임은 런타임에 사본 위에서만
    #: 일어난다 — `artifacts/*_records.jsonl` 을 고치면 BM25 색인 문자열이 갈려
    #: v1~v4 의 측정이 전량 무효가 된다. 그래서 **BM25 점수도 안 바뀐다**: 색인은
    #: 미리 빌드된 파일이고 이 플래그는 검색 **뒤에** 실리는 근거 문자열만 건드린다.
    #:
    #: 🔴 `META_UNREACHABLE_FLAGS`·`ORACLE_UNREACHABLE_FLAGS` 에 **넣지 않는다** —
    #: `_gather_meta`(pipeline.py:1465)도 `_oracle_evidence`(1820)도 `tools._record_of`
    #: 로 같은 레코드를 읽으므로 두 경로 모두에서 코드가 도달한다.
    #:
    #: 🔴 **겨냥 0 인데도 세운 유일한 arm 이다** (계획서 §13 ③ · 사용자 판단).
    #: `R0-1` 이 238건 중 새로 회수 가능 13건 · 그중 평가셋 골든 **0건**을 실측했고
    #: **Δ = 0 을 예측으로 박아 뒀다.** 그 예외임을 제안서 §7 이 명시해야 한다.
    nary: bool = False
    #: 🔴 **`A22` 전용 — 다중 허브 결속 `W = 1 → 3`.** 참이면 `_gather_evidence` 가
    #: 발주처 차선에서 허브를 **하나가 아니라 최대 셋** 결속한다. 추가 허브는 질문이
    #: 아니라 **이미 담은 근거의 `- 계약상대:` 필드**에서 나온다.
    #:
    #: 🔴 **`Route.counterparty` 는 단수 그대로 둔다.** 계획서가 그것을 복수로 바꾸는
    #: 안을 냈지만 `R1-9` 실측이 독자 `pipeline.py` 6곳 + 테스트 단언 약 25건을 셌고,
    #: 무엇보다 라우터의 결속은 **질문에서 역할을 읽는** 축이라 `R1-11 ①` 이 오탐 3건으로
    #: 이미 기각한 자리다(`test_disclosing_company_is_never_bound_as_counterparty`).
    #: 근거의 `계약상대:` 는 **그 공시가 스스로 적은 상대**라 역할이 이미 확정돼 있고,
    #: 그래서 이 arm 은 그 불변을 깨지 않고 축을 넓힌다.
    #:
    #: 🔴 **추가 허브는 자기 칸을 따로 쓴다** (`MAX_EXTRA_HUB_EVIDENCE`). 6칸을 나눠
    #: 쓰면 첫 허브가 파트너당 1칸씩 담는 fan-in 문항(`Q-110` 실측 2/6 → 5/6)이 깨진다.
    #:
    #: 🔴 `counterparty_hop=False` 와는 함께 켤 수 없다 (`FLAG_DEPENDENCIES`).
    #: 🔴 `ORACLE_UNREACHABLE_FLAGS` 에 **넣는다** — `_gather_golden` 은 발주처 차선을
    #: 아예 안 탄다.
    multi_hub: bool = False
    #: 🔴 **`A24` 전용 — `chat()` 에 `TOOL_SPECS` 를 실제로 넘긴다.** 참이면
    #: `pipeline.answer_question` 이 답변 생성을 `_answer_with_tools` 로 돌려
    #: **HCX-005 자신이 도구를 부르고**, 우리가 `tools.execute()` 로 실행한 결과를 다시
    #: 넣어 한 번 더 부른다. 기본값 `False` 가 종전 동작이다 — 스위치를 안 건드린 운영
    #: 경로의 요청 본문은 한 바이트도 다르지 않다.
    #:
    #: 🔴 **`calc_values`(A5)와 축이 다르다.** `A5` 는 *"근거에 박아 둔 계산 결과를
    #: 뗀다"* 이고 이쪽은 *"모델이 스스로 도구를 부른다"* 다. 그래서
    #: `FLAG_DEPENDENCIES` 에 `calc_values -> function_calling` 을 걸지 **않는다** —
    #: 근거의 괄호를 떼도 도구는 그대로 불릴 수 있다(오히려 더 불릴 것이다).
    #:
    #: 🔴 **`META_UNREACHABLE_FLAGS`·`ORACLE_UNREACHABLE_FLAGS` 에도 넣지 않는다.**
    #: 이 플래그가 사는 곳은 검색 차선이 아니라 `answer_question` 의 **생성 호출**이고,
    #: `retrieval="meta"`(`_gather_meta` 조기 반환) 위에서도 `golden_oracle`(골든 주입)
    #: 위에서도 그 호출은 똑같이 선다. 도달 불가 자리가 없다.
    #:
    #: 🔴 **G1 판단**: 이것은 보조 모델을 답변 경로에 끼우는 것이 아니라 **HCX-005 가
    #: 자기 도구를 부르는 것**이다. `agent/tools.py` 는 LLM·임베딩 모듈을 하나도
    #: import 하지 않는다(BM25·manifest·`compute` 뿐) — 도구가 외부 모델을 부르지
    #: 않음을 코드로 확인했다. 따라서 G1 위반이 아니다.
    #:
    #: 🔴 **대가가 하나 있다 — `tools` 와 `maxTokens` 는 동시 지정 불가다**(40001 ·
    #: `docs/CONVENTION.md` §API 실측). 즉 이 플래그를 켜면 답변 호출의 길이 상한
    #: (`max_tokens=300`, CoT 800)이 **사라진다.** 단일 변수 조작이 아니라는 뜻이 아니라,
    #: **API 가 그 둘을 묶어 놨다**는 뜻이다. Δ 를 읽을 때 이 사실을 반드시 함께 읽는다.
    function_calling: bool = False
    #: 🔴 **`A24b` — 계산 도구 7종만 내민다.** `function_calling` 의 하위 옵션이라
    #: `FLAG_DEPENDENCIES` 로 묶어 뒀다 (부모가 꺼지면 코드가 도달하지 않는다).
    #:
    #: 근거는 `A24` 실측이다 — 모델이 부른 47건 중 **26건이 검색**이었고
    #: (`read_section` 16 · `get_history` 10) 이미 프롬프트에 실린 근거를 다시 읽어
    #: **희석시켰다.** 회귀 8건 중 7건이 도구 호출 문항이다. 그 차선을 아예 닫는다.
    calc_tools_only: bool = False
    #: 🔴 **`A24b` — 계산 질의에만 도구를 연다** (`router.needs_calculation`).
    #: `tools` 와 `maxTokens` 가 배타(40001)라 도구를 내미는 문항은 길이 상한을 잃는데,
    #: `A24` 는 그 대가를 **88문항 전부에** 물렸고 도구를 한 번도 안 부른 `Q-106` 까지
    #: 회귀했다. 겨냥이 계산 문항이므로 전 문항에 열 이유가 없다 — 실측으로 전체 88 중
    #: 38문항 · 밸류체인 12 중 6문항만 열린다.
    calc_queries_only: bool = False
    #: 🔴 **`A24b` 의 알맹이 — 도구가 돌려준 값으로 답변의 수치를 치환한다.**
    #: `A24` 기각 사유 ①이 *"도구가 정답을 줘도 모델이 옮겨 적지 못한다"* 였다
    #: (`calc_total` → `2,365,868` / 답변 `2,366,968`). 치환에는 **옮겨 적는 단계가
    #: 아예 없다** — `A20`(`substitute_verified`)이 이미 실측으로 이겨 둔 기전이고,
    #: 유일성 조건과 치환 코드를 `critic` 에서 **그대로 재사용**한다.
    #:
    #: 🔴 `substitute_verified`(A20)와 **배타가 아니다.** 원천이 다르다(도구 결과 대
    #: 재계산값) — `A24b+A20` 이 그 결합을 재는 arm 이다.
    substitute_tool_results: bool = False
    #: 🔴 **`A26` 의 알맹이 — 가리키는 대상이 없는 지시어를 '범위 밖' 과 가른다. LLM 0회.**
    #:
    #: 규약 감사 `F-3` 이 실측했다. `AUD-15` *"그 회사의 작년 실적 좀 알려줘"* 가
    #: `out_of_universe` 로 갈려 *"70개사 범위 안의 기업으로 질문해 주시면"* 이라 답했다 —
    #: **기업이 지목되지 않은 것인데 범위 밖이라고 말한다.** 사용자는 자기가 물은 회사가
    #: 70개사에 없다고 오해하고, `CONVENTION §3 R4` 의 *"역질문"* 은 발화하지 않는다.
    #:
    #: 🔴 `NO_SUCH_REPORT` 와 **같은 종류의 구분**이다. v3 에서 *"기업도 연도도 범위 안인데
    #: 그 보고서만 없는 경우"* 를 따로 가른 것과 축이 같고, 그때 적은 사유 — *"한계의
    #: 종류가 다르면 고지도 달라야 한다"* — 가 여기에도 그대로 적용된다. 갈래가 하나
    #: 빠져 있었을 뿐이다.
    #:
    #: 🔴 **겨냥은 작다.** 평가셋 88 에 이 형태가 몇 건인지는 재 봐야 안다. 그래도 세우는
    #: 이유는 크레딧 0원(LLM 0회)이고 `§5` 「정보한계 대응」이 독립 지표라서다.
    ambiguous_gate: bool = False
    #: 🔴 **`A25` 의 알맹이 — 근거 표기를 우리가 붙인다. LLM 0회.**
    #:
    #: 규약 감사(2026-08-16 · `docs/compliance-audit-2026-08-16.md` F-2)가 잰 값:
    #: 실질 답변 8건 중 규약 형식(`CONVENTION §7.5`)을 지킨 것이 **3건**이었고,
    #: 우리 채점기의 `citation_rate` 도 `A20r` 에서 **0.765**(그리드 0.750)다.
    #:
    #: 🔴 **기전은 `A20` 과 같다.** v4 의 단일 발견이 *"모델이 숫자를 손에 쥐는 순간
    #: 틀린다"* 였고 `A20` 이 이긴 이유는 재계산값을 **근거에서 우리가 직접 읽어** 모델의
    #: 타이핑을 안 거쳤기 때문이다. **접수번호는 14자리 숫자다** — 감사에서 정확히 같은
    #: 병이 나왔다. `AUD-08` 은 옮겨 적었으나 라벨을 빠뜨렸고 `AUD-11` 은 아예 안 적고
    #: *"(근거: 각 보고서별 상세내역 참조)"* 로 뭉갰다. **그래서 접수번호도 타이핑시키지
    #: 않는다.** 처분의 네 번째 갈래다 — 각주(v3) · 치환(`A20`) · 재생성(`A20r`) · **부착**.
    #:
    #: 🔴 **왜 `critic` 이 못 막았나.** `quick_check` 는 누락을 정확히 발화하는데(감사에서
    #: 8건 중 7건 발화) 처분으로 부르는 `judge` 는 *"근거가 답을 뒷받침하나"* 만 보고
    #: **표기 형식을 고치지 않아** `supported=True` 로 통과시킨다. 탐지 100% · 교정 0% 가
    #: 인용 축에서 되풀이된 것이다.
    #:
    #: 🔴 **부착은 LLM 호출을 하나도 안 쓴다.** 그래서 생성 프롬프트가 바이트 동일이고
    #: `RequestCache` 가 전부 적중한다 — 이 arm 의 재측정은 **생성 크레딧 0원**이다.
    attach_citation: bool = False
    #: 🔴 **`A10` 전용 — 자기일관성(Self-Consistency) 표본 수 `k`.** 1이 종전 동작이고,
    #: 스위치를 안 건드린 운영 경로는 답변 생성 호출이 **정확히 한 번**이다.
    sc_k: int = 1
    #: 🔴 **온도. 이 모듈의 머리말이 "고정" 이라고 적어 둔 통제 변수다** (§I-3).
    #: v3 까지 온도 0.0 은 `pipeline.answer_question` 에 **직접 하드코딩**돼 있었고
    #: `Arm` 에 표현할 자리가 없었다 (`R0-5` 실측: 저장소 전체 `.py` 에 `A10` 문자열이
    #: 없었다). 여기로 옮기되 **기본값이 0.0 이어야** v1~v4 측정이 전부 유효하게 남는다.
    #:
    #: 🔴 **`A10` 이 이 통제 변수를 깨는 유일한 arm 이다.** 그러니 단독으로 재고 다른
    #: arm 과 섞지 않는다. `A20r`(사유 주입 재생성)은 **온도 0.0 그대로**이고, 그래서
    #: 둘은 같은 축의 변형이 아니라 서로 다른 기전이다.
    #:
    #: 🔴 **요청 캐시가 온도를 키에 넣는다** (`RequestCache._key` = url + body 해시).
    #: 그래서 `A10` 은 재실행마다 전액 재결제이고, 나아가 **k 개 표본이 같은 키를 쓰므로**
    #: 2회차부터는 `clova.no_cache()` 로 캐시를 우회해야 다양성이 생긴다.
    temperature: float = 0.0

    def __post_init__(self) -> None:
        if self.retrieval not in RETRIEVAL_MODES:
            raise ValueError(f"retrieval 은 {RETRIEVAL_MODES} 중 하나여야 합니다: {self.retrieval}")
        if self.retrieval == "meta":
            on = [f for f in META_UNREACHABLE_FLAGS if getattr(self, f)]
            if on:
                raise ValueError(
                    f"retrieval='meta' 는 `_gather_meta` 뒤에서 즉시 반환하므로 {on} 은 "
                    f"켜도 코드가 도달하지 않습니다 — Δ 가 조용히 0 이 됩니다 (arm {self.name!r}). "
                    f"BM25 이후 arm 에 붙이십시오.")
            fields = type(self).__dataclass_fields__
            moved = [f for f in META_UNREACHABLE_AXES
                     if getattr(self, f) != fields[f].default]
            if moved:
                raise ValueError(
                    f"retrieval='meta' 는 `filter_docs` 만 쓰고 `search_chunks` 를 한 번도 "
                    f"부르지 않으므로 {moved} 를 기본값에서 움직여도 근거가 안 바뀝니다 — "
                    f"Δ 가 조용히 0 이 됩니다 (arm {self.name!r}). 메타 축 LOO 는 BM25 arm "
                    f"위에서 재십시오.")
        for parent, children in FLAG_DEPENDENCIES.items():
            if getattr(self, parent):
                continue
            on = [c for c in children if getattr(self, c)]
            if on:
                raise ValueError(
                    f"{parent}=False 이면 {on} 은 코드가 도달하지 않습니다 — Δ 가 조용히 "
                    f"0 이 됩니다 (arm {self.name!r}). 함께 끄십시오.")
        if self.golden_oracle:
            fields = type(self).__dataclass_fields__
            moved = [f for f in ORACLE_UNREACHABLE_FLAGS
                     if getattr(self, f) != fields[f].default]
            if moved:
                raise ValueError(
                    f"golden_oracle=True 는 검색 차선을 골든 주입으로 갈아치우므로 {moved} "
                    f"를 기본값에서 움직여도 근거가 안 바뀝니다 — Δ 가 조용히 0 이 됩니다 "
                    f"(arm {self.name!r}). 검색 LOO 는 오라클 없이 재십시오.")
        if self.sc_k < 1:
            raise ValueError(f"sc_k 는 1 이상이어야 합니다: {self.sc_k} (arm {self.name!r})")
        # 🔴 온도 0.0 에서 같은 프롬프트를 k 번 부르면 **표본이 전부 같다.** 다수결이
        #    항상 만장일치가 되어 자기일관성이 no-op 이 되고, 우리는 그 0 을 "SC 는
        #    효과가 없다" 로 읽는다. 값을 치른 만장일치와 공짜 만장일치는 다르다.
        if self.sc_k > 1 and self.temperature <= 0.0:
            raise ValueError(
                f"sc_k={self.sc_k} 인데 temperature={self.temperature} 입니다 — 온도 0 에서는 "
                f"표본이 전부 같아 다수결이 정의상 만장일치가 되고 Δ 가 조용히 0 이 됩니다 "
                f"(arm {self.name!r}).")

    @property
    def uses_bm25(self) -> bool:
        return self.retrieval != "meta"

    @property
    def uses_group_index(self) -> bool:
        """major·holding·periodic 인덱스를 보는가. A1 은 exchange 1,469건만 본다."""
        return self.retrieval == "all"


# --- 누적 사다리 (§I-4b) -------------------------------------------------------
A0 = Arm("A0", retrieval="meta", schema_fields=False, hop_ladder=False,
         chain_hop=False, counterparty_hop=False, split_query=False,
         calc_values=False, verifier=False, followup_hop=False)
A1 = replace(A0, name="A1", retrieval="exchange")
A1B = replace(A1, name="A1b", retrieval="all", schema_fields=True)
#: `followup_hop` 을 여기서 **명시적으로** 켠다. `chain_hop` 과 짝이므로 함께 적어야
#: 한쪽만 옮겨지는 사고를 막는다 (`__post_init__` 이 짝을 검사한다).
A4 = replace(A1B, name="A4", hop_ladder=True, chain_hop=True, split_query=True,
             followup_hop=True)
A5 = replace(A4, name="A5", calc_values=True)
A6 = replace(A5, name="A6", verifier=True)
#: 플래그가 A6 과 같다 — A7 은 무조건 코드다 (머리말 참조). 사다리 번호를 §I-5d 와
#: 맞추기 위한 칸이고, `Δ(A6→A7)` 은 정의상 0 이다.
A7 = replace(A6, name="A7")
A8 = replace(A7, name="A8", counterparty_hop=True)

# --- Leave-one-out — **A8 에서** 한 요소만 되돌린다 ------------------------------
#: 🔴 `followup_hop` 도 함께 끈다 — `chain_hop` 없이는 도달하지 않는 플래그다.
#: 그래서 `L1` 은 **엄밀히는 단일 변수 조작이 아니다**: 정정 550엣지와 관련공시 1,267엣지를
#: 한꺼번에 뺀다. 그 사실을 `L4` 가 갈라 준다 (머리말 §followup_hop).
L1 = replace(A8, name="L1", chain_hop=False, followup_hop=False)   # 실패의 41%
L2 = replace(A8, name="L2", schema_fields=False)    # 실패의 24%
#: L3 은 플래그로 보면 **A7 과 같다** (A8 − counterparty_hop = A7). 이름을 따로 두는
#: 이유는 읽는 방향이 반대라서다 — 사다리는 '더하면 얼마 오르나', LOO 는 '빼면 얼마
#: 잃나' 를 묻는다. 두 수는 부호만 다른 같은 값이어야 하고, 아니면 어딘가 틀렸다.
L3 = replace(A8, name="L3", counterparty_hop=False)
#: 🔴 `L1` 안에 섞여 있던 `※관련공시` 후속공시만 떼어낸다. **v3 신설** — Δ 상한이
#: 1문항(Q-035)임을 사전 실측으로 확인했다(머리말). 그 15원으로 "REFERS_TO 는 이 코호트에서
#: 기여 0" 을 주장이 아니라 측정으로 바꾼다.
L4 = replace(A8, name="L4", followup_hop=False)
#: 🔴 **메타 축 LOO 셋 (v4 신설).** `L5` 는 v3 가 기각한 이름이라 **비워 두고 건너뛴다** —
#: 재사용하면 v3 산출물의 `L5` 와 같은 칸으로 읽힌다.
#:
#: 이 셋이 `LOO_REMOVED` 에 들어가는 것이 핵심이다. 곁가지(`A20`·`A21`…)와 달리
#: **LOO 표의 빈칸을 메우는 것**이고, 제안서 §6 의 기여도 곡선이 그 공백을 안고 있다.
#: 🔴 Δ 는 크게 음수일 것이 거의 확실하다 — 메타 프리필터가 `A0` 의 본체이기 때문이고,
#: 그래서 이 셋은 '뺄 후보' 가 아니라 **'A0 의 기여를 A8 위에서 다시 재는 것'** 이다.
L6 = replace(A8, name="L6", meta_corp=False)
L7 = replace(A8, name="L7", meta_date=False)
L8 = replace(A8, name="L8", meta_group=False)

# --- A2 — 임베딩 하이브리드 (사다리 밖 곁가지) ----------------------------------
A2 = replace(A1B, name="A2", dense=True)            # §I-2 자리: A1b + dense
A2F = replace(A8, name="A2F", dense=True)           # 제출 구성 위: A8 + dense

# --- ORACLE — 검색 고정 · 생성만 잰다 (사다리 밖 곁가지 · 🔴 진단 전용) -----------
#: `A8` 과 **플래그 하나만** 다르다. 그 하나가 검색 차선 전체를 골든 주입으로 갈아치운다 —
#: 🔴 **더 주는 것이 아니라 골든만으로 교체하는 것**이라 근거가 줄어들고(실측 −47%),
#: 그래서 `Δ(A8→ORACLE)` 을 *"검색이 못 가져온 몫"* 으로 읽으면 **안 된다**(R2E · 머리말 참조).
#: 이 arm 이 재는 것은 **"근거를 골든만으로 한정했을 때 답이 서는가"** 다.
#: 🔴 제출 구성이 아니고 상한도 아니다 — 그리드 72 에서 A8 66 · ORACLE 64 다.
ORACLE = replace(A8, name="ORACLE", golden_oracle=True)

# --- A8-lean — 프롬프트 규칙 다이어트 (사다리 밖 곁가지 · v3 신설) ----------------
#: `A8` 과 **플래그 하나만** 다르다 — 그 하나가 규칙 8·9(`prompts.MONEY_UNIT_RULE`)를
#: 시스템 프롬프트에서 뗀다. 🔴 **`LADDER`·`LOO_REMOVED` 에 넣지 않는다** (머리말 참조):
#: 사다리의 칸이 아니라 같은 자리에서 프롬프트만 갈아 끼운 변형이다. 겨냥 1건(`Q-018`) ·
#: 인질 12건(전체 88 · 밸류체인 5/12)이고, 그 비대칭이 이 arm 의 판정 조건이다.
A8_LEAN = replace(A8, name="A8-lean", lean_rules=True)

# --- A20 — 검사가 낸 재계산값으로 답변을 치환한다 (사다리 밖 곁가지 · v4 신설) -----
#: `A8` 과 **플래그 하나만** 다르다 — 그 하나가 `pipeline._verify` 를 '각주만 붙이는
#: 판정자' 에서 '재계산값으로 정정하는 교정자' 로 바꾼다. 🔴 **`LADDER`·`LOO_REMOVED`
#: 에 넣지 않는다** (`A8-lean`·`ORACLE` 과 같은 곁가지다): 사다리의 칸이 아니라 같은
#: 자리에서 검증 단계의 처분만 갈아 끼운 변형이다.
#: 🔴 `verifier` 에 구조적으로 의존한다 (`FLAG_DEPENDENCIES`) — 검증 단계가 안 서면
#: `_verify` 가 첫 줄에서 나가므로 Δ 가 조용히 0 이 된다.
A20 = replace(A8, name="A20", substitute_verified=True)

# --- A21 — 수치 출처 게이트 (사다리 밖 곁가지 · v4 신설) --------------------------
#: `A8` 과 **플래그 하나만** 다르다 — 그 하나가 답변의 12자리 이상 금액이 근거에
#: 완전일치로 있는지 묻는다. 🔴 **`A15` 의 대체 후보**이지 `A20` 의 변형이 아니다:
#: `A15`·`A20` 은 *"계산이 맞는가"* 를 묻고 이 arm 은 *"이 숫자가 근거에 있는가"* 를
#: 묻는다. 겨냥 3건(`Q-018` 그리드 · `Q-109`·`Q-113` 밸류체인)을 v3 세대 664 답변
#: 전수 재채점으로 확인했다 (`R1-9` 보고서).
#: 🔴 `LADDER`·`LOO_REMOVED` 에 넣지 않는다 — `A8-lean`·`A20` 과 같은 곁가지다.
A21 = replace(A8, name="A21", provenance_gate=True)

# --- A20r — 치환 대신 **사유를 실어 재생성 1회** (사다리 밖 곁가지 · v4 신설) ------
#: 🔴 **이 arm 은 `A8` 과 플래그가 둘 다르다.** 다른 곁가지와 갈리는 자리라 이유를 적는다.
#: `provenance_gate` 는 **조작이 아니라 입력**이다 — `R1-9` 가 그 게이트의 단독 Δ 가
#: **구조적으로 0** 임을 확인했다(`_verify` 의 유일한 출력이 각주이고 채점은
#: `score_closed`·`judge_open` 양쪽에서 `strip_verify_tail` 로 그 꼬리를 뗀다.
#: 탐지 100% · 교정 0%). 즉 그 플래그는 이 arm 의 Δ 에 자기 몫을 섞지 않고, **판정을
#: 소비할 자리**를 여기 하나 만들 뿐이다. `A21` 의 `reason`
#: (`"provenance: 답변 1,843,200,000,000원 이 근거에 없다"`)은 `A20` 의 재계산값과 달리
#: 재생성 프롬프트에 **그대로 실을 수 있는 형태**라 겨냥이 겹치지 않고 합이 크다.
#:
#: 🔴 `substitute_verified` 는 **끈 채로 둔다** — 계획서가 *"치환 대신 재생성"* 으로
#: 두 처분을 나란히 재라고 했다. 켜면 `Δ(A8→A20r)` 이 치환의 몫과 섞인다.
#: 🔴 `LADDER`·`LOO_REMOVED` 에 넣지 않는다 — `A8-lean`·`A20`·`A21` 과 같은 곁가지다.
A20R = replace(A8, name="A20r", regenerate_on_verify=True, provenance_gate=True)

# --- A18 — `T1-targeted` n-ary 추출 (사다리 밖 곁가지 · v4 신설) ------------------
#: `A8` 과 **플래그 하나만** 다르다 — 그 하나가 `schema_matched=False` 238건의 근거에
#: `artifacts/nary_records.jsonl` 의 LLM 추출값을 **덧붙인다**(합집합 · 덮어쓰기 아님).
#: 🔴 **`LADDER`·`LOO_REMOVED` 에 넣지 않는다** — `A8-lean`·`A20`·`A21` 과 같은 곁가지다.
#:
#: 🔴 **이 저장소의 "겨냥 0 이면 세우지 않는다" 규칙을 의도적으로 예외 처리한 유일한 arm**
#: 이다 (계획서 §13 ③ · 사용자 판단 2026-08-14). 사유는 *"`T1` 은 논문 아이디어 10편 중
#: 하나라 제안서에서 안 재고 기각보다 재고 기각이 강하다"* 이고, **그 예외임을 제안서
#: §7 에 명시**해야 한다 — 기준을 조용히 바꾼 것으로 읽히면 다른 기각의 신뢰도가 함께
#: 떨어진다.
#:
#: 🔴 **Δ = 0 이 예측으로 먼저 박혔다** (`R0-1` `2c0558b` · 계획서 §9pre). 근거 넷:
#: ① 현 실패 6문항 중 `schema_matched=False` 근거를 가진 것은 `Q-087` 하나이고 그 정답은
#: `schema_matched=True` 레코드에 있다 ② `T1` 이 새로 회수 가능한 13 레코드 중 평가셋
#: 골든이 **0건** ③ 밸류체인은 근거·골든 양쪽에 false 레코드가 0건 ④ `Q-101` 은 이미 정답.
#: **틀리면 238건 판정 전체를 재검토하라** (계획서 §9pre 의 어긋남 표).
A18 = replace(A8, name="A18", nary=True)

# --- A25 — 근거 표기 결정적 부착 (사다리 밖 곁가지 · 규약 감사 F-2) ----------------
#: `A20r`(운영 기본값)과 **플래그 하나만** 다르다. 사유·기전·비용은 `Arm.attach_citation`
#: 독스트링에 적었다.
#:
#: 🔴 **겨냥이 실측으로 박혀 있다** — `A20r` 의 `citation_rate` 0.765(그리드 0.750)이고,
#: 감사 16문항에서는 규약 형식이 실질 답변 8건 중 3건이었다. 즉 겨냥은 최소 **17문항**
#: (88 × 0.235)이다. 이 저장소의 "겨냥 0 이면 세우지 않는다" 규칙을 넉넉히 넘긴다.
#:
#: 🔴 **성적이 오를 것이라 주장하지 않는다.** 부착은 `answer` 의 **꼬리에 글자를 더할
#: 뿐** 본문 수치를 건드리지 않으므로 정확성 축은 불변이어야 한다. 그것이 예측이고,
#: **그리드 72·전체 88 이 움직이면 부착이 채점기의 다른 축을 건드린 것이니 그때 왜인지
#: 찾아야 한다** (가장 그럴듯한 경로: 인용문의 숫자가 `covers_key_points` 나
#: 금액 대조에 섞여 든다).
A25 = replace(A20R, name="A25", attach_citation=True)

# --- A26 — 지시대상 불명 갈래 (사다리 밖 곁가지 · 규약 감사 F-3) -------------------
#: `A20r` 과 **플래그 하나만** 다르다. 사유는 `Arm.ambiguous_gate` 독스트링.
#: 🔴 `A25` 와 **합치지 않는다** — 축이 다르다(인용 표기 대 라우팅 갈래). 둘을 한 arm 에
#: 넣으면 Δ 를 어느 쪽에 귀속할지 모른다. 둘 다 채택되면 그때 합친 arm 을 따로 잰다.
A26 = replace(A20R, name="A26", ambiguous_gate=True)

# --- A25+A26 — 둘을 함께 켠다 (채택 후보) ----------------------------------------
#: 🔴 **단독 Δ 를 먼저 확보하고 나서 결합을 잰다** (`A24b`/`A24b+A20` 과 같은 규칙).
#: 축이 다르므로(인용 표기 · 라우팅 갈래) 상호작용이 없을 것이라 보지만, **없을 것이라
#: 보는 것과 재 보는 것은 다르다.**
A25_A26 = replace(A20R, name="A25+A26", attach_citation=True, ambiguous_gate=True)

# --- A22 — 다중 허브 결속 `W=1→3` (사다리 밖 곁가지 · v4 신설) --------------------
#: `A8` 과 **플래그 하나만** 다르다 — 그 하나가 발주처 차선의 허브를 하나에서 최대 셋으로
#: 넓힌다. 🔴 **`LADDER`·`LOO_REMOVED` 에 넣지 않는다** — `A20`·`A21`·`A18` 과 같은 곁가지다.
#:
#: 🔴 **이 arm 은 정답률을 올리지 않는다 — 근거 회수를 올린다.** 계획서 §4·§9pre 가
#: 예측을 박아 뒀다: **뒤집기 0 · 근거 회수 +2**(`Q-108`·`Q-117`). 보고할 때 두 분모를
#: 반드시 갈라야 한다(그리드 72 / 밸류체인 골든 46). 성적 표에 섞으면 "유료 arm 이
#: 성적을 못 올렸다" 와 "근거를 더 가져왔다" 가 한 칸에서 상쇄된다.
#:
#: 🔴 겨냥 셋의 성격이 다르다:
#:   `Q-107`  hop 은 이미 발화한다(`via=한국수력원자력` · partners=6). 단수라 **진짜
#:            계약상대**(두산에너빌리티)를 허브로 못 잡는 것이 병목이다 (`R1-11 ②` 이관)
#:   `Q-108`  `Route.counterparty` 가 **아예 None** 이라 hop 이 안 선다. 라우터 결속은
#:            `R1-11 ①` 이 기각했고(두 회사가 다 공시한 쪽), 근거의 `계약상대:` 는 그
#:            기각과 충돌하지 않는다
#:   `Q-117`  열거 hop(`strat=enumerate`)이 서는데 갈래가 계약상대별이라 허브가 더 필요하다
A22 = replace(A8, name="A22", multi_hub=True)

# --- A10 — 자기일관성 `k=3` (사다리 밖 곁가지 · v4 신설) --------------------------
#: 🔴 **이 저장소에서 통제 변수(온도 0.0)를 깨는 유일한 arm 이다** (§I-3 · 머리말).
#: 그래서 **단독으로 재고 다른 arm 과 섞지 않는다.** `A20r`(사유 주입 재생성)이 가장
#: 가까운 이웃인데 그쪽은 온도 0.0 이라 **기전이 다르다** — 저쪽은 진단을 되먹이고
#: 이쪽은 다양성을 사서 다수결한다. 나란히 놓아야 v3 의 미검증 주장이 닫힌다.
#:
#: `k=3` · `temperature=0.7` 은 Self-Consistency 논문(2023)의 기본 설정이다. 🔴 우리
#: 코퍼스로 재본 적이 없으므로 **그 값 자체가 측정 대상**이고, 채택 여부와 무관하게
#: "논문의 전제를 우리 데이터에서 재고 판정했다" 가 이 arm 의 산출물이다.
#:
#: 🔴 **겨냥 5건 중 둘(`Q-044`·`Q-097`)은 `A20` 이 이미 고쳤다 — 순증만 센다.**
#: 남는 순증 겨냥은 `Q-018`(그리드) · `Q-109`·`Q-115`(밸류체인) 셋이다.
#:
#: 🔴 `LADDER`·`LOO_REMOVED` 에 넣지 않는다 — `A20`·`A21`·`A22` 와 같은 곁가지다.
A10 = replace(A8, name="A10", sc_k=3, temperature=0.7)

# --- A24 — `chat(tools=…)` 을 실제로 켠다 (사다리 밖 곁가지 · v4 신설) -------------
#: `A8` 과 **플래그 하나만** 다르다 — 그 하나가 답변 생성 호출에 `TOOL_SPECS`(15종 =
#: 검색 8 + 계산 7)를 넘겨 **HCX-005 자신이 도구를 부르게** 한다.
#: 🔴 **`LADDER`·`LOO_REMOVED` 에 넣지 않는다** — `A20`·`A21`·`A18`·`A22` 와 같은 곁가지다.
#:
#: 🔴 **이름 충돌 주의.** 계획서 §4b `idea` 표의 ★4 가 `A24` 를 *"modal(의무/재량/예정)
#: 보존 지시"* 로 적어 뒀는데 **그쪽은 코드로 존재한 적이 없다**(저장소 전 `.py` 에
#: `A24` 문자열 0건이었다). 사용자 지시가 이 이름을 function calling 에 배정했으므로
#: 여기가 정본이고, modal arm 은 세울 때 다른 번호를 써야 한다.
#:
#: 🔴 **겨냥은 순증 0 이다** (`R1-8` `2aaf46f`). `tools=` 가 새로 겨냥하는 것은
#: `Q-018`·`Q-044`·`Q-097` 셋인데 셋 다 더 싼 0원 arm 이 이미 가져갔다
#: (`Q-044`·`Q-097` → `A20` 치환 · `Q-018` → `A21` 게이트). 그래도 세운 이유는
#: **게이트 F 의 유일한 미완을 측정으로 닫기 위해서**다 — `A18` 과 같은 성격의 예외이고,
#: *"안 재고 기각"* 보다 *"재고 기각"* 이 제안서 §7 에서 비교할 수 없이 강하다.
#:
#: 🔴 **`A8` 대비 도구가 실제로 닿는 상한은 +1 이다.** `Q-044` 는 `calc_total` 이 닿지만
#: `Q-097` 의 `per_unit`(나눗셈)은 **도구 7종에 대응이 없고**(`calc_share` 는 ×100 이 붙는다)
#: `Q-018` 은 계산이 아니라 **전사** 오류다.
#:
#: ❌ **판정: 기각 (2026-08-15 · 실측 3,759.2원).** 게이트 F 의 `[~]` 를 이것으로 닫았다.
#:
#:     그리드 72     A8 65  ->  A24 59      Δ = −6 (−8.3%p)
#:     전체 88       A8 81  ->  A24 74      Δ = −7
#:     밸류체인 12   A8  6  ->  A24  6      Δ =  0  (다만 +Q-110 / −Q-111 로 갈렸다)
#:     회귀          9건    (그리드·전체 8 + 밸류체인 1)
#:     호출          129 -> 165회 (+27.9%) · 프롬프트 토큰 +44.0% · 비용 +33.3%
#:
#: 🔴 **기각 사유는 "모델이 도구를 안 불러서" 가 아니다 — 88문항 중 37문항(42.0%)이
#: 실제로 불렀다.** 사유는 셋이고, 셋 다 실측이다:
#:
#:   ① 🔴 **도구가 정답을 줘도 모델이 옮겨 적지 못한다.** `Q-044` 에서 `calc_total` 이
#:      골든과 **바이트까지 같은** `2,365,868` 을 돌려줬는데 답변은 `2,366,968` 을 썼다.
#:      독립 스모크(같은 인자·다른 회차)에서는 `2,366,868` 이 나왔다 — **두 번 다 틀리고
#:      틀린 자리가 다르다.** 이 저장소의 병목은 계산이 아니라 **전사**이고
#:      (`Q-018` · `A18` 의 환각률 62.1% 와 같은 기전), function calling 은 그 축을
#:      건드리지 않는다. 반대로 `A20`(치환)이 이기는 이유가 여기서 설명된다 —
#:      **치환에는 옮겨 적는 단계가 아예 없다.**
#:   ② 🔴 **모델이 부른 도구의 과반이 계산이 아니라 검색이다** (47건 중 검색 26 · 계산 21 ·
#:      `read_section` 16 · `get_history` 10). 근거는 이미 프롬프트에 실려 있는데 모델이
#:      다시 읽으러 가고, 그 결과가 근거를 희석한다. 회귀 8건 중 7건이 도구를 불렀다.
#:   ③ 🔴 **`tools` 와 `maxTokens` 가 배타라 길이 상한이 사라진다**(40001). 도구를 한 번도
#:      안 부른 `Q-106` 도 회귀했다 — 즉 순수 디코딩 조건 변화만으로도 답이 흔들린다.
#:
#: 🔴 **`_CALC_SPECS` 만 내밀면 ②는 줄지만 ①·③은 그대로다.** 그리고 ①이 유일한 겨냥
#: (`Q-044`)을 이미 죽였으므로 그 변형의 상한은 0 이다 — 그래서 재시도를 세우지 않는다.
#: 🔴 **그 추론은 ①을 고칠 수 없다고 가정한 것이었고, 고칠 수 있다** — `A20` 과 같은
#: 기전(값을 코드가 문자열에 박는다)을 도구 결과에 걸면 된다. `A24b` 가 그것이다.
A24 = replace(A8, name="A24", function_calling=True)

# --- A24b — 🔴 도구 결과를 모델이 옮겨 적게 하지 않는다 (v4 신설) ------------------
#: 🔴 **`A24` 를 지우지 않는다** — 그 arm 은 기각 판정의 물증이다. `A24b` 는 그 위에
#: 기각 사유 셋을 **하나씩 겨냥한** 재시도이고, 셋 다 실측에 대응한다:
#:
#:     ① 전사 오류      `substitute_tool_results` — 도구 결과로 답변의 수를 갈아 끼운다
#:     ② 근거 희석      `calc_tools_only`         — 계산 7종만 내민다 (검색 8종을 뺀다)
#:     ③ 길이 상한 소실  `calc_queries_only`       — 계산 질의에만 도구를 연다
#:
#: 🔴 **`A24` 의 §9 ③이 *"그 변형의 상한은 0"* 이라고 적어 뒀고, 그 추론은 ①을
#: 고칠 수 있다는 것을 몰랐다.** ②만 고치면 상한이 0 인 것이 맞다 — ①이 유일한 겨냥
#: (`Q-044`)을 죽이기 때문이다. `A24b` 가 재는 것은 **①을 고쳤을 때도 그런가** 이다.
#:
#: 🔴 **순증 겨냥이 얇다.** 겨냥 셋 중 `Q-044`·`Q-097` 은 `A20`(0원 치환)이 이미
#: 고쳤고, `Q-018` 은 계산이 아니라 **근거에서 옮겨 적기**라 계산 도구가 낼 값이
#: 없다. 그래서 `A24b` 단독의 상한은 사실상 `A8` 회복(Δ 0)이고, 볼 만한 것은 아래
#: 결합 arm 이다.
A24B = replace(A8, name="A24b", function_calling=True, calc_tools_only=True,
               calc_queries_only=True, substitute_tool_results=True)

# --- A24b+A20 — 두 치환의 결합 (v4 신설) -----------------------------------------
#: 🔴 **원천이 다른 두 치환을 함께 켠다.** `A24b` 는 *도구가 돌려준 값*으로,
#: `A20` 은 *검사가 재계산한 값*으로 답변의 수를 갈아 끼운다. 배타가 아니고, 순서는
#: **도구 치환이 먼저**다 (`pipeline._answer_with_tools` → `_verify`) — 도구 값이
#: 더 이른 원천이고, 치환된 뒤의 답변을 `_verify` 가 다시 본다.
#:
#: 🔴 **`A24b` 와 플래그가 하나만 다르다**(`substitute_verified`). 그래야
#: `Δ(A24b → A24b+A20)` 이 `A20` 의 몫으로 읽힌다.
A24BS = replace(A24B, name="A24b+A20", substitute_verified=True)

#: 실행 순서는 **A8 부터 역순**이다 (§I-4b) — 가장 비싼 구성을 먼저 확보해야
#: 중간에 예산이 끊겨도 최신 성능을 안다. A2 계열은 사다리를 다 확보한 뒤 맨 끝이다 —
#: G1 불허로 A2 를 버려도 앞의 여덟 칸이 그대로 남아야 한다.
#: 🔴 `ORACLE` 은 맨 끝이다 — 진단용이라 사다리·LOO 를 다 확보한 뒤에 붙는다.
#: 🔴 `A8-lean` 은 **사다리·LOO 뒤, `A2` 계열 앞**이다. 앞의 열두 칸은 이미 측정돼 요청
#: 캐시로 0원에 재생되고, `A8-lean` 은 **채택 후보**라 G1 미확정인 dense arm(`A2`·`A2F`)과
#: 진단 arm(`ORACLE`)보다 먼저 확보해야 한다. `--run` 이 정지선에 걸리면 이 순서가 곧
#: 무엇을 잃는지의 순서다.
#: 🔴 `A20` 은 `A8-lean` 바로 뒤다 — 같은 이유로 **채택 후보**이고, G1 미확정인 dense
#: arm 과 진단 arm 보다 먼저 확보해야 한다.
#: 🔴 `A20r` 은 `A20` 바로 뒤다 — **나란히 재는 짝**이라 둘 사이를 다른 arm 이 갈라
#: 놓으면 정지선이 하나만 확보하고 끊길 때 그 비교가 통째로 사라진다.
#: 🔴 `L6`·`L7`·`L8` 은 `L4` 바로 뒤다 — LOO 는 한 덩어리로 확보해야 표가 완성된다.
#: 🔴 `A18` 은 `A21` 뒤 · dense 앞이다 — **채택 후보가 아니라 기각 예정 arm**이라
#: (`R0-1` 겨냥 0 · Δ=0 예측) 채택 후보 넷(`A8-lean`·`A20`·`A20r`·`A21`)을 먼저
#: 확보한 뒤에 선다. 그래도 G1 미확정인 dense·진단 arm 보다는 앞이다 — 이쪽은
#: **제안서 §7 의 논거**로 반드시 필요하고 저쪽은 아니다.
#: 🔴 `A22` 는 `A21` 뒤 · `A18` 앞이다 — **밸류체인 근거 회수**를 올리는 arm 이라
#: 겨냥 0 인 `A18` 보다 앞에 서야 정지선에 걸릴 때 잃는 것이 적다.
#: 🔴 `A24` 는 `A18` 뒤 · dense 앞이다 — `A18` 과 같은 **기각 예정 arm**(순증 겨냥 0)이라
#: 채택 후보들 뒤에 서지만, **게이트 F 의 유일한 미완을 닫는 arm** 이라 G1 미확정인
#: dense·진단 arm 보다는 앞이다.
#: 🔴 `A24b`·`A24b+A20` 은 `A24` 바로 뒤다 — **나란히 재는 짝**이라(`A20`/`A20r` 과
#: 같은 이유) 사이를 다른 arm 이 갈라 놓으면 정지선이 하나만 확보하고 끊길 때 그 비교가
#: 통째로 사라진다. 결합 arm 이 뒤인 것은 단독 Δ 를 먼저 확보해야 결합의 몫을 귀속할 수
#: 있어서다.
#: 🔴 `A25` 는 `A20r` 바로 뒤다 — **운영 기본값에 플래그 하나만 더한 짝**이라 사이가
#: 벌어지면 Δ 를 귀속할 상대를 잃는다. `A20`/`A20r` 과 같은 이유다.
RUN_ORDER = ("A8", "L1", "L2", "L3", "L4", "L6", "L7", "L8",
             "A7", "A6", "A5", "A4", "A1b", "A1", "A0",
             "A8-lean", "A20", "A20r", "A25", "A26", "A25+A26", "A21", "A22", "A10", "A18", "A24",
             "A24b", "A24b+A20", "A2F", "A2", "ORACLE")

#: 사다리(=v2 곡선). A2 계열·LOO 는 여기 들어가지 않는다.
LADDER = ("A0", "A1", "A1b", "A4", "A5", "A6", "A7", "A8")

#: LOO 구성과 각자가 뺀 것. 보고서가 이 사전을 그대로 읽는다.
#: 🔴 `L1` 의 라벨을 "정정체인 hop" 이라고만 적으면 안 된다 — `followup_hop` 까지 함께
#: 끄므로 `※관련공시` 1,267엣지가 그 안에 섞여 있다. `L4` 가 그것을 갈라 준다.
LOO_REMOVED = {"L1": "CHAIN_HOP 전체 (정정체인 550엣지 + 관련공시 후속공시)",
               "L2": "major·holding 필드", "L3": "발주처 2-hop",
               # 🔴 라벨이 절반만 적고 있었다 (R2A `O-2`). `6d6de5d` 이후 `L4` 는
               #    역방향 `followups` 와 **순방향 `terminates` 를 함께** 뗀다 —
               #    둘 다 출처가 `※ 관련공시` 셀이라 `include_followups` 가 같이 가린다.
               #    값은 그대로 옳지만 라벨이 낡으면 Δ 의 뜻이 잘못 인용된다.
               "L4": "관련공시 REFERS_TO 계열 — 후속공시(역방향) + 해지→원계약"
                     "(순방향) 둘 다 (정정체인은 유지)",
               # 🔴 메타 축 셋 (v4). '검색을 끈다' 가 아니라 '필터 인자를 안 넘긴다' 이므로
               #    라벨에 그 사실을 적는다 — "기업 축" 이라고만 적으면 BM25 까지 끈 것으로
               #    읽힌다.
               "L6": "search_chunks 의 corp_name 필터 (기업 축 · BM25 는 그대로)",
               "L7": "search_chunks 의 접수일 접두 ym·year·rcept_dt "
                     "(사업연도 base_year 는 유지)",
               "L8": "search_chunks 의 group 인자 (인덱스 군 축 — exchange 로 떨어진다)"}

#: dense 를 켜는 arm. G1 이 불허로 확정되면 **이 둘만 빼면 된다.**
DENSE_ARMS = ("A2", "A2F")

#: 🔴 **성적으로 인용하면 안 되는 arm.** 평가 시점에 재현할 수 없는 정보(골든)를 쓰므로
#: 제출 구성이 될 수 없다. 러너의 요약이 이 목록을 읽어 표 옆에 경고를 찍는다 —
#: 산출물만 보는 사람이 숫자를 성적으로 읽는 것을 막는 유일한 수단이다.
DIAGNOSTIC_ARMS = ("ORACLE",)

ARMS: dict[str, Arm] = {a.name: a for a in
                        (A0, A1, A1B, A4, A5, A6, A7, A8, L1, L2, L3, L4, L6, L7, L8,
                         A2, A2F, ORACLE, A8_LEAN, A20, A20R, A21, A18, A22, A10, A24,
                         A24B, A24BS, A25, A26, A25_A26)}

#: 🔴 **사다리도 LOO 도 진단도 아닌 곁가지.** 목록을 손으로 적지 않는다 — 러너의 요약과
#: 게이트가 `LADDER + LOO_REMOVED + DIAGNOSTIC_ARMS` 만 훑으므로, 여기 안 잡히는 arm 은
#: **돈을 쓰고도 산출물 문서에 한 글자도 안 나온다.** 그 사고는 이미 한 번 실물로 났다
#: (`ORACLE` 1,747원을 쓰고 `gate_I.md` 어디에도 없었다 · `render_gate` 주석). 더 나쁜
#: 것은 비용 검산이다 — 실지출은 `run.json` 재귀 합이라 그 돈이 들어가는데 arm 비용
#: 합계에는 안 들어가서 "캐시가 막은 금액" 이 그만큼 작게 찍힌다.
SIDE_ARMS: tuple[str, ...] = tuple(
    a for a in RUN_ORDER
    if a not in LADDER and a not in LOO_REMOVED and a not in DIAGNOSTIC_ARMS)

#: 모든 스위치 이름. 새 플래그를 넣으면 여기 자동으로 잡힌다 — 목록을 손으로 적어 두면
#: 빠뜨린 플래그가 LOO 의 '단일 변수 조작' 검사를 조용히 통과한다.
FLAG_NAMES: tuple[str, ...] = tuple(
    f for f in A8.__dataclass_fields__ if f != "name")

#: 스위치를 안 건드리면 완성 구성이다. 운영 경로의 기본값이 곧 A8 이어야
#: '기본값으로 돌린 것' 과 'A8 arm' 이 같은 것임이 보장된다.
#: 🔴 **운영 기본값 = 제출 구성이다.** 평가 서버(`api/server.py`)가 arm 을 지정하지 않으므로
#: 이 값이 곧 주최측이 받는 답변의 구성이다. **여기를 바꾸면 배포도 다시 해야 한다.**
#:
#: v4 (2026-08-15) 에서 `A8` → `A20r` 로 바꿨다. 새 채점기 기준 실측:
#:
#:     A8    그리드 65/72 · 전체 81/88 · 밸류체인 6/12 · 안전성 9/12
#:     A20   그리드 67/72 · 전체 83/88 · 밸류체인 6/12
#:     A20r  그리드 67/72 · 전체 83/88 · 밸류체인 7/12 · 안전성 9/12   ← 최고
#:
#: `A20r` 만 `Q-110` 의 10× 재진술 오류를 고친다 — 치환(`A20`)은 유일한 재계산값이
#: 없으면 못 고치고, 재생성이 사유를 받아 다시 쓴다. 문항당 약 2원.
#: 정본: `artifacts/ablation/v4/rescore_r3b/rescore.md`
#:
#: 🔴 종전 주석(v1 의 `A6` 과 플래그가 동일하다는 기록)은 `A8` 시절의 것이라 지웠다.
_current: Arm = A20R


def current() -> Arm:
    return _current


def flags(arm: Arm | str | None = None) -> dict:
    """arm 의 플래그만 담은 사전 (`name` 제외). LOO 의 단일 변수 조작을 검사하는 값이다."""
    target = current() if arm is None else (ARMS[arm] if isinstance(arm, str) else arm)
    return {f: getattr(target, f) for f in FLAG_NAMES}


def enabled(flag: str, arm: Arm | str | None = None) -> bool:
    """플래그가 켜져 있는가. 없는 플래그 이름은 **조용히 False 가 아니라 예외**다 —
    오타 하나가 "그 구성요소는 효과가 없었다" 로 보고되는 것을 막는다."""
    if flag not in FLAG_NAMES:
        raise KeyError(f"그런 플래그가 없습니다: {flag!r} (있는 것: {FLAG_NAMES})")
    target = current() if arm is None else (ARMS[arm] if isinstance(arm, str) else arm)
    return bool(getattr(target, flag))


def set_arm(arm: Arm | str) -> Arm:
    """전역 arm 을 바꾸고 이전 값을 돌려준다. 평가 러너 전용."""
    global _current
    previous = _current
    _current = ARMS[arm] if isinstance(arm, str) else arm
    return previous


@contextmanager
def use(arm: Arm | str):
    """`with ablation.use("A1"): ...` — 블록을 벗어나면 반드시 되돌린다."""
    previous = set_arm(arm)
    try:
        yield current()
    finally:
        set_arm(previous)


# --- ORACLE 골든 주입 (러너 전용) ----------------------------------------------
# 파이프라인은 `question_id` 만 알고 evalset 을 모른다. 그래서 골든은 **러너가 넣는다**.
# 모듈 전역인 이유는 `set_arm` 과 같다 — arm 을 갈아끼우는 축과 같은 자리에 있어야
# "무엇으로 쟀는가" 가 한 곳에서 읽힌다.


class OracleGoldenMissing(RuntimeError):
    """🔴 `golden_oracle` arm 인데 골든이 주입되지 않았다. **조용히 넘어가지 않는다.**

    빈 근거로 진행하면 파이프라인이 `UNANSWERABLE` 을 내보내고, 채점기는 그것을 오답으로
    세며, 우리는 그 결과를 "오라클을 줘도 생성이 다 틀렸다" 로 읽는다. 측정이 아니라
    **주입 누락**인데 그것이 판정으로 굳는다 — 이 모듈이 막으려는 바로 그 사고다.
    """


#: `{question_id: [{"rcept_no": ..., "section": ...}, ...]}`. `None` 은 '주입 안 됨' 이고
#: 빈 목록은 '이 문항의 골든이 실제로 없다'(= answerable=false) 라서 **구분해야 한다.**
_oracle_golden: dict[str, list[dict]] | None = None


def set_oracle_golden(mapping: dict[str, list[dict]] | None) -> dict | None:
    """코호트의 `{question_id: evidence}` 를 주입하고 이전 값을 돌려준다. 러너 전용.

    🔴 **heldout 에는 절대 주입하지 않는다** (`scripts/ablation.py` 의 heldout 경로는
    이 함수를 부르지 않고, 그 앞에서 반환한다).
    """
    global _oracle_golden
    previous = _oracle_golden
    _oracle_golden = None if mapping is None else {
        str(qid): list(items or []) for qid, items in mapping.items()}
    return previous


def oracle_golden() -> dict[str, list[dict]] | None:
    """주입된 매핑 그대로. 주입 전이면 `None`."""
    return _oracle_golden


def oracle_golden_for(question_id: str) -> list[dict]:
    """한 문항의 골든 근거. **없으면 예외** — 조용한 빈 근거를 만들지 않는다."""
    if _oracle_golden is None:
        raise OracleGoldenMissing(
            "ORACLE arm 인데 골든이 주입되지 않았습니다 — "
            "`ablation.set_oracle_golden({question_id: evidence})` 를 먼저 부르십시오 "
            f"(문항 {question_id!r}). 빈 근거로 진행하면 '생성 실패' 로 오독됩니다.")
    if question_id not in _oracle_golden:
        raise OracleGoldenMissing(
            f"ORACLE 골든 매핑에 {question_id!r} 이 없습니다 (주입된 문항 "
            f"{len(_oracle_golden)}건). 코호트와 매핑이 어긋났습니다.")
    return _oracle_golden[question_id]


@contextmanager
def use_oracle_golden(mapping: dict[str, list[dict]] | None):
    """`with ablation.use_oracle_golden(m): ...` — 블록을 벗어나면 반드시 되돌린다."""
    previous = set_oracle_golden(mapping)
    try:
        yield oracle_golden()
    finally:
        set_oracle_golden(previous)


# --- 근거 성형 (생성 직전) -----------------------------------------------------

#: `- 계약금액: 22,764,764,160,000원 (227,648억원)` 의 괄호. 원값은 남긴다.
_EOK_SUFFIX = re.compile(r"^(-\s*[^:\n]*:\s*-?[\d,]+원)\s*\(-?[\d,]+억원\)\s*$", re.M)
#: `- 기간: 2025-07-24 ~ 2033-12-31 (8년 5개월)` 의 괄호. 시작·종료일은 남긴다.
_DURATION_SUFFIX = re.compile(r"^(-\s*기간:\s*\S+\s*~\s*\S+)\s*\([^()\n]*\)\s*$", re.M)

_RCEPT_RE = re.compile(r"\d{14}")
_BLOCK_SPLIT = re.compile(r"(?m)^(?=##\s)")


def _record_fields(block: str) -> list[str]:
    """이 근거 블록이 `record["fields"]` 로부터 만든 줄들. 없으면 빈 목록.

    `to_markdown` 이 찍은 것과 **같은 문자열**을 다시 만들어 지운다. 줄 모양을
    정규식으로 추측하면 계약상대·공시유보처럼 서식이 같은 다른 줄까지 지운다.
    """
    from argus.agent import tools

    found = _RCEPT_RE.search(block)
    if not found:
        return []
    record = tools._record_of(found.group(0))
    fields = (record or {}).get("fields") or {}
    return [f"- {label}: {value}" for label, value in fields.items()]


def shape_evidence(evidence: str, arm: Arm | None = None) -> str:
    """arm 에 맞춰 근거 문자열을 성형한다. LLM 0회 · 검색 결과는 건드리지 않는다.

    * `schema_fields=False` — major·holding 추출 필드 줄을 뺀다 (Phase H 이전 상태).
    * `calc_values=False`   — 결정적으로 계산해 박아 둔 괄호(억원 환산 · 계약기간)를 뗀다.
    """
    arm = arm or current()
    if arm.schema_fields and arm.calc_values:
        return evidence
    text = evidence or ""

    if not arm.schema_fields and text:
        blocks = []
        for block in _BLOCK_SPLIT.split(text):
            drop = set(_record_fields(block))
            if drop:
                block = "\n".join(line for line in block.split("\n")
                                  if line.strip() not in drop)
            blocks.append(block)
        text = "".join(blocks)

    if not arm.calc_values:
        text = _EOK_SUFFIX.sub(r"\1", text)
        text = _DURATION_SUFFIX.sub(r"\1", text)
    return text


# --- A25 — 근거 표기 결정적 부착 (LLM 0회) --------------------------------------
#
# 근거 마크다운은 두 형태다 (실측 — 감사 응답의 `retrieved_context`):
#
#   정형  ## [공급계약 체결] 삼성전자 – 단일판매ㆍ공급계약체결 — 반도체 위탁생산 공급계약
#         - 공시일: 2025-07-28 (접수번호 20250728800035)
#   정기  ## [사업보고서 2025] 삼성전자 – IV. 이사의 경영진단 및 분석의견
#         - 출처: 접수번호 20260310002820 (공시일 20260310)
#
# 🔴 **`pipeline._evidence_records` 를 쓰지 않는다.** 그것은 `tools._record_of` 로 정형
# 레코드만 되찾고 **정기공시에는 `None` 을 돌려준다** (실측: `20260310002820` → `None`).
# 사업보고서 답변에 인용을 못 붙이면 이 arm 의 절반이 죽는다. 그래서 근거 마크다운
# 자체를 읽는다 — 어차피 모델이 베껴 적어야 했던 그 글자다.

#: `–` 는 EN DASH(U+2013)다. 하이픈으로 적으면 헤더가 하나도 안 걸린다.
_CITE_HEAD = re.compile(r"^(?:\[기재정정\]\s*)?\[([^\]]+)\]\s*(.*?)\s*–\s*(.*)$")
_CITE_RCEPT = re.compile(r"접수번호\s*(\d{14})")
_CITE_DATE = re.compile(r"공시일[:\s]*(\d{4})-?(\d{2})-?(\d{2})")
#: 답변과 근거를 잇는 지문. 금액·수량은 7자리 이상만 본다 — 연도·비율까지 세면
#: 아무 블록이나 다 걸려 선별이 무의미해진다.
_CITE_NUM = re.compile(r"\d[\d,]{6,}")
#: 붙이지 않는 답변. 🔴 **거절에 근거를 달면 거짓 귀속이다** — 감사 `F-6` 이 실측한
#: `AUD-04` 가 개인정보 거절에 사업보고서 접수번호를 근거로 달았다.
_CITE_REFUSAL = ("확인되지 않습니다", "확인할 수 없", "드릴 수 없습니다",
                 "제공할 수 없", "답변을 드릴 수 없")
#: 한 답변에 붙이는 인용 상한. 근거 칸은 최대 10건인데 그것을 다 적으면 답변보다
#: 인용이 길어진다.
MAX_CITATIONS = 3


def _cite_numbers(text: str) -> set[str]:
    return {m.replace(",", "") for m in _CITE_NUM.findall(text or "")}


#: 답변과 근거를 잇는 **말**. 숫자만으로는 못 잇는 답이 있다 — 실측 `Q-007` 은
#: *"투자 대상은 '청주 M15X 건설'"* 이라 금액을 한 번도 안 쓴다. 3자 이상만 보는 것은
#: 조사·접미사가 아무 블록에나 걸리는 것을 막기 위해서다.
_CITE_TERM = re.compile(r"[0-9A-Za-z가-힣]{3,}")
#: 어느 공시에나 나오는 말. 이것으로 이으면 전부 걸린다.
_CITE_STOP = frozenset((
    "공시일", "접수번호", "계약금액", "투자금액", "계약상대", "사업보고서", "분기보고서",
    "반기보고서", "주식회사", "기재정정", "신규시설투자등", "단일판매공급계약체결",
))


def _cite_terms(text: str) -> set[str]:
    return {t for t in _CITE_TERM.findall(text or "") if t not in _CITE_STOP}


def _citation_units(evidence: str, provenance: dict[str, dict] | None = None) -> list[dict]:
    """근거 블록 하나 = 인용 하나. 접수번호가 없는 블록은 인용할 수 없으므로 버린다.

    🔴 **일을 절반씩 나눠 갖는다.**

    * **어느 근거가 살아남았나 → 근거 문자열에서.** 절단(`MAX_EVIDENCE` ·
      `_join_section_evidence`)이 여기서 일어나므로 여기서 읽어야 맞다. 읽는 것은
      `접수번호 (\\d{14})` 정규식 **하나**뿐이라 서식이 흔들려도 안 깨진다.
    * **인용에 쓸 값 → `provenance` 에서.** `pipeline._citation_source` 가 레코드에서
      그대로 꺼내 둔 것이다. 우리가 방금 렌더한 글자를 도로 파싱하지 않는다.

    `provenance` 가 비면(구버전 산출물 · `ORACLE` · 테스트) 머리글 되읽기로 되돌아간다.
    """
    provenance = provenance or {}
    units: list[dict] = []
    for block in re.split(r"(?m)^##\s+", evidence or ""):
        if not block.strip():
            continue
        rcept = _CITE_RCEPT.search(block)
        if not rcept:
            continue
        head = block.split("\n", 1)[0].strip()
        known = provenance.get(rcept.group(1))
        if known:
            label, date = known["label"], known["date"]
            # 🔴 **근거에 실제로 살아남은 섹션만 적는다.** 기억은 절단 전에 채워지므로
            #    `MAX_SECTION_CHARS` 에서 잘려 나간 섹션까지 들어 있다. 그것을 그대로
            #    쓰면 프롬프트에 닿지도 않은 근거를 인용하게 된다 — 거짓 귀속이다.
            alive = [t for t in (known.get("tails") or []) if t and t in block]
            tail = " / ".join(alive) or known.get("tail", "")
        else:
            matched = _CITE_HEAD.match(head)
            label, tail = (matched.group(1), matched.group(3)) if matched else (head, "")
            found = _CITE_DATE.search(block)
            date = "-".join(found.groups()) if found else ""
        # 🔴 **접수번호 하나로 합친다.** 근거 블록은 섹션 단위라 정기공시 한 보고서가
        #    블록 여럿으로 쪼개지는데, 그것을 인용 여럿으로 내면 같은 접수번호가 반복된다.
        #    `CONVENTION §7.5` 의 예시는 접수번호 하나 아래 섹션을 ` / ` 로 나열한다.
        key = rcept.group(1)
        unit = next((u for u in units if u["rcept_no"] == key), None)
        if unit is None:
            unit = {"rcept_no": key, "label": label.strip(), "date": date,
                    "tails": [], "numbers": set(), "terms": set()}
            units.append(unit)
        for part in (t.strip() for t in tail.split(" / ")):
            if part and part not in unit["tails"]:
                unit["tails"].append(part)
        unit["numbers"] |= _cite_numbers(block)
        # 🔴 짚기는 **머리글과 기억한 제목** 둘 다 본다. 블록 본문 전체를 쓰면
        #    정기공시 섹션(수천 자)이 아무 답변에나 걸려 선별이 무의미해진다.
        unit["terms"] |= _cite_terms(head) | _cite_terms(tail)
    return units


def _render_citation(unit: dict) -> str:
    """`CONVENTION §7.5` 형식 — `(근거: 보고서명 기준연월, 접수번호 14자리, 섹션경로)`."""
    head = f'{unit["label"]} ({unit["date"]})' if unit["date"] else unit["label"]
    parts = [head, f'접수번호 {unit["rcept_no"]}']
    tail = " / ".join(unit.get("tails") or [])
    if tail:
        parts.append(tail)
    return "(근거: " + ", ".join(parts) + ")"


def attach_citation(answer: str, evidence: str, arm: Arm | None = None,
                    provenance: dict[str, dict] | None = None) -> tuple[str, list[str]]:
    """근거 표기가 없으면 우리가 붙인다. `(바뀐 답변, 붙인 접수번호들)`.

    🔴 **부르는 자리가 중요하다.** 모든 LLM 호출이 끝난 **뒤**에 불러야 생성·critic·재생성
    프롬프트가 바이트 동일로 남고 `RequestCache` 가 적중한다. 앞에서 부르면 재측정이
    캐시를 통째로 빗나가 크레딧을 다시 쓴다.

    🔴 **각주 꼬리 앞에 끼운다.** 채점기(`error_analysis.strip_verify_tail`)가 `(주의:`
    부터를 잘라내므로 그 뒤에 붙이면 우리가 붙인 인용이 채점 전에 사라진다.
    """
    arm = arm or current()
    if not arm.attach_citation:
        return answer, []
    from argus.agent.critic import quick_check       # 순환 import 회피 — 호출 시점에 든다
    if not answer or quick_check(answer)["has_citation"]:
        return answer, []

    units = _citation_units(evidence, provenance)
    if not units:
        return answer, []

    answer_numbers = _cite_numbers(answer)
    named = [u for u in units if u["rcept_no"] in answer]
    used = [u for u in units if u["numbers"] & answer_numbers]
    titled = [u for u in units if u["terms"] & _cite_terms(answer)]
    # 🔴 거절인데 근거를 하나도 안 쓴 답변에는 붙이지 않는다 (`F-6`). 무언가 썼다면 사실을
    #    말한 것이므로 일부에 "확인되지 않습니다" 가 섞여 있어도 인용은 붙인다.
    if not (named or used or titled) and any(k in answer for k in _CITE_REFUSAL):
        return answer, []

    # 🔴 **못 고르겠으면 하나만 붙인다.** 처음에는 `units` 전부를 붙였는데 실측에서
    #    `Q-007` 이 `no_fabrication` 을 잃었다 — 답이 '청주 M15X' 하나를 말하는데 인용이
    #    무관한 둘(용인 Fab · 투자판단)을 끌고 오자 채점기가 **없는 내용을 지어냈다**고
    #    본 것이다. 근거를 더 대는 것이 늘 이득이 아니다. 짚어낸 것이 있으면 그것만,
    #    없으면 검색 1순위 하나만 적는다.
    picked = (named or used or titled or units[:1])[:MAX_CITATIONS]
    citation = "\n\n" + " ".join(_render_citation(u) for u in picked)

    head, sep, tail = answer.partition("\n\n(주의:")
    return head + citation + sep + tail, [u["rcept_no"] for u in picked]
