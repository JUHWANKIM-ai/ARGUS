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
`L4 = A8 − followup_hop`.

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

계산 도구 14종은 `TOOL_SPECS` 로 노출돼 있지만 **생성 호출에는 `tools` 를 넘기지 않는다**
(`pipeline.answer_question` → `chat(..., max_tokens=…)`). 따라서 "function calling 을 끈다"
는 조작은 실행 경로에서 아무것도 바꾸지 못한다 — Δ 가 정의상 0 이 되는 헛측정이다.

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

#: 🔴 **플래그 사이의 도달 의존.** 키가 꺼지면 값들도 반드시 함께 꺼져야 한다 —
#: 부모 hop 안에서만 읽히는 플래그라 부모 없이 켜면 `META_UNREACHABLE_FLAGS` 와 똑같이
#: **Δ 가 조용히 0** 이 된다. `__post_init__` 이 이 사전으로 막고, LOO 의 '단일 변수 조작'
#: 검사도 이 사전을 읽어 **부모와 함께 움직인 자식은 예외로 허용**한다.
#:
#: 산문으로만 적어 두면 검사가 그것을 못 읽는다 — 그래서 데이터로 둔다.
FLAG_DEPENDENCIES: dict[str, tuple[str, ...]] = {"chain_hop": ("followup_hop",)}

#: 🔴 `golden_oracle=True` 에서 **읽히지 않는** 축. 검색 차선을 통째로 골든 주입으로
#: 갈아치우므로 이 플래그들을 건드려도 근거가 한 글자도 안 바뀐다 —
#: `META_UNREACHABLE_FLAGS` 와 같은 종류의 **조용한 Δ 0** 이다.
#:
#: 다만 여기서는 '켜면 안 된다' 가 아니라 **'기본값에서 움직이면 안 된다'** 로 막는다.
#: `ORACLE = A8 + golden_oracle` 이라 이 축들이 전부 켜져 있는 것이 정상이고, 문제는
#: `L1 + golden_oracle` 처럼 **오라클 위에서 검색 LOO 를 흉내내는 것**이다. 그 Δ 는
#: 정의상 0 이고, 0 을 "그 구성요소는 기여가 없다" 로 읽는 사고가 이 모듈의 존재 이유다.
#: 기준값은 `Arm` 의 **필드 기본값**이다 (= A8, 머리말 참조 — 전방참조가 필요 없다).
ORACLE_UNREACHABLE_FLAGS = ("retrieval", "dense", "hop_ladder", "chain_hop",
                            "split_query", "counterparty_hop", "followup_hop")


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
    #: 🔴 **ORACLE 전용 · 진단용이고 제출 구성이 될 수 없다** (평가 시점에 골든이 없다).
    #: 참이면 `pipeline.answer_question` 이 검색 대신 골든 근거를 싣는다. 기본값 False 가
    #: G1 격리(`dense`)와 같은 역할을 한다 — 스위치를 안 건드린 운영 경로는 골든을
    #: **한 번도 열지 않는다** (러너가 주입하지 않으면 매핑 자체가 없다).
    golden_oracle: bool = False

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

#: 실행 순서는 **A8 부터 역순**이다 (§I-4b) — 가장 비싼 구성을 먼저 확보해야
#: 중간에 예산이 끊겨도 최신 성능을 안다. A2 계열은 사다리를 다 확보한 뒤 맨 끝이다 —
#: G1 불허로 A2 를 버려도 앞의 여덟 칸이 그대로 남아야 한다.
#: 🔴 `ORACLE` 은 맨 끝이다 — 진단용이라 사다리·LOO 를 다 확보한 뒤에 붙는다.
RUN_ORDER = ("A8", "L1", "L2", "L3", "L4", "A7", "A6", "A5", "A4", "A1b", "A1", "A0",
             "A2F", "A2", "ORACLE")

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
                     "(순방향) 둘 다 (정정체인은 유지)"}

#: dense 를 켜는 arm. G1 이 불허로 확정되면 **이 둘만 빼면 된다.**
DENSE_ARMS = ("A2", "A2F")

#: 🔴 **성적으로 인용하면 안 되는 arm.** 평가 시점에 재현할 수 없는 정보(골든)를 쓰므로
#: 제출 구성이 될 수 없다. 러너의 요약이 이 목록을 읽어 표 옆에 경고를 찍는다 —
#: 산출물만 보는 사람이 숫자를 성적으로 읽는 것을 막는 유일한 수단이다.
DIAGNOSTIC_ARMS = ("ORACLE",)

ARMS: dict[str, Arm] = {a.name: a for a in
                        (A0, A1, A1B, A4, A5, A6, A7, A8, L1, L2, L3, L4, A2, A2F,
                         ORACLE)}

#: 모든 스위치 이름. 새 플래그를 넣으면 여기 자동으로 잡힌다 — 목록을 손으로 적어 두면
#: 빠뜨린 플래그가 LOO 의 '단일 변수 조작' 검사를 조용히 통과한다.
FLAG_NAMES: tuple[str, ...] = tuple(
    f for f in A8.__dataclass_fields__ if f != "name")

#: 스위치를 안 건드리면 완성 구성이다. 운영 경로의 기본값이 곧 A8 이어야
#: '기본값으로 돌린 것' 과 'A8 arm' 이 같은 것임이 보장된다.
#: 🔴 v1 의 기본값(`A6`)과 **플래그가 동일**하다 — 옛 A6 이 `counterparty_hop` 을 A4 로부터
#: 물려받고 있었다. 이름만 바로잡은 것이므로 운영 동작은 안 바뀐다.
_current: Arm = A8


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
