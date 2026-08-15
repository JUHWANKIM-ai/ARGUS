# 도면 v3 — 파이프라인 전체 · 서비스 흐름 (2026-08-15)

산출물 둘.

    docs/figures/argus-algorithm-v3.drawio    파이프라인 전체 (기술 독자 · 심사위원)
    docs/figures/argus-service-flow.drawio    질문 → 답 (비기술 독자 · 7단계)

`docs/figures/argus-algorithm-v2.drawio` 는 **한 바이트도 안 고쳤다** — v2 는 기록이다.
코드는 읽기만 했다. 쓴 것은 도면 둘과 이 보고서뿐이다.

---

## 1. v2 도면이 낡은 지점

v2 는 *"ARGUS 공시 Agent — 알고리즘 구성과 출처"* 이고 부제가 *"논문 17편 매핑"* 이다.
즉 **실행 순서 도면이 아니라 논문 귀속 도면**이었다. 낡은 것이 다섯이다.

| # | v2 가 그린 것 | 코드의 현재 |
|---|---|---|
| ① | **색 = 비용** (HCX 호출 / LLM 0회 / 분기) | 축은 맞는데 라벨이 '비용' 이라 *"어디가 LLM 0회인가"* 가 부차적으로 읽힌다. v3 는 색 규약을 **LLM 호출 여부**로 바꾸고 파랑을 지배색으로 뒀다 |
| ② | 온라인 파이프라인이 `PPR 단일스텝 → KG×Text 반복 → PCST 압축 → distractor 제거 → 결정적 계산 → HCX 생성 → Self-Consistency → critic 판정` | 🔴 **이 사슬 중 실행 경로에 있는 것은 '결정적 계산'(근거 성형)과 'HCX 생성' 둘뿐이다.** PPR·ToG-2 반복·PCST·distractor 제거는 코드에 없다 (`MAX_EVIDENCE=10` 절단이 PCST 자리를 대신한다고 `pipeline.py` 가 스스로 적어 뒀다). Self-Consistency 는 `A10` 곁가지이고 **운영 기본값에서 `sc_k=1`** 이다 |
| ③ | 라우터 분기가 `단일 조회 / GoT 집계 / ReAct 루프 / 답변 불가` 넷이고 **전부 주황(LLM 호출)** | 🔴 **라우터는 LLM 을 한 번도 안 부른다** (`router.py` 첫 줄: *"규칙으로 갈리면 LLM 을 쓰지 않는다"*). 도면에서 라우터 계열이 주황이면 우리 설계의 핵심이 정반대로 읽힌다. GoT 집계는 코드에 없다 |
| ④ | `critic 판정` 뒤가 `통과 → 응답` / `미달 → 재검색` | 🔴 코드의 critic 은 **재검색으로 안 돌아간다.** `judge` 는 인용 누락 시에만 부르고 결과는 각주다. 실제 되먹임 고리는 v4 에서 생긴 **검증 → 재생성 1회**다 |
| ⑤ | 없는 것 | **입출력 안전 계층**(`sanitize_question`·`mask_pii`) · **두 갈래 한계 고지**(`UNANSWERABLE` vs `NO_SUCH_REPORT`) · **발주처 2-hop** · **열거 hop** · v4 신설 셋(수치 출처 검증 · 재생성 1회 · `fault_response`) · **안전성 축** |

v2 의 오프라인 인덱싱 블록(A)은 v3 에서 뺐다. v3 의 주제가 **온라인 파이프라인**이고,
인덱싱은 별 도면의 몫이다 — 한 장에 둘을 넣으면 "질문 하나가 지나는 길" 이 흐려진다.

---

## 2. 두 도면의 구조

### ① `argus-algorithm-v3.drawio` — 파이프라인 전체

`mxCell` 85 (vertex 53 · edge 30 · root 2). 페이지 1760 × 1600.

    A. 입력 · 라우팅            질의 수신 → 입력 안전 → 라우터 → [범위 판정]
                              ├ 범위 밖        UNANSWERABLE
                              └ 보고서 부재     NO_SUCH_REPORT
    B. 검색 · 도구              메타 프리필터 → BM25 사다리 / 섹션 인덱스 → [충분성]
                                 ↺ 부족 → 재검색 (hop ≤ 3)
                              → 열거 hop(4) → 정정체인(5) → 계약상대 그래프(6) → 근거 성형
    C. 생성 · 검증 · 처분        [근거 유무] → 생성① → [인용 유무] → critic② → 검증 3종
                              → [발화 여부] → 재생성 1회③ → 출력 안전 → 응답 5키
    D. 가드 · 예외 · 계기판      예산 가드 · fault_response · 안전성 축 · think_trace · arm 스위치

색 규약 — **일관되게 네 가지**.

    파랑 #DAE8FC/#3A6FA8    LLM 0회 (크레딧 0원)
    주황 #FFE6CC/#C87A2F    LLM 호출
    경고 #F8CECC/#B3453F    검증 · 안전
    마름모 #FFF2CC/#C9A227  분기 · 판정
    굵은 테두리 (strokeWidth=4)  v4 신설

범례는 도면 안 우상단에 있고, *"파랑이 파이프라인의 대부분이다"* 를 한 줄로 적었다.
LLM 호출 세 자리에 ①②③ 번호를 박아 **호출이 일어나는 곳이 정확히 셋**임을 세게 했다 —
① 생성(무조건) · ② critic(인용 누락 시에만) · ③ 재생성(검사 발화 시에만) · 상한 6회.

### ② `argus-service-flow.drawio` — 서비스 관점

`mxCell` 45 (vertex 33 · edge 10 · root 2). 페이지 1400 × 1340. 세로 7단계.

    1 질문이 들어온다            2 무엇을 묻는지 읽는다 ─[답할 수 있나]─ 아니오 → 여기서 멈춘다
    3 공시를 찾는다              4 문서 사이의 관계를 따라간다
    5 근거를 읽고 답을 쓴다       6 답을 검사하고, 어긋나면 한 번 다시 쓴다
    7 답을 내보낸다
    하단 :  우리가 지키는 세 가지

🔴 **arm 이름 · 플래그 · 함수명을 한 글자도 쓰지 않았다.** 확인 방법 — 원문 grep 은
색상 코드에 오탐이 난다(`#DAE8FC` 안에 `A8`). 그래서 **`value=` 의 표시 문자열만** 뽑아
잰다:

    python -c "..."   # mxCell 의 value 에서 HTML 태그를 걷어내고
                      # A[0-9]+r? · L[0-9] · ORACLE · ablation · arm · flag · hop · BM25 ·
                      # HCX · CoT · verify_* · _* · route · sanitize · mask_pii · rcept ·
                      # corp_name · search_chunks · get_history · top_k · LLM 를 찾는다
    jargon hits: []   → 0건

같은 사실을 일상어로 옮겼다 — `verify_number_provenance` → *"답에 쓴 숫자가 근거 문서에
실제로 있는지"*, `_expand_chain` → *"나중에 정정된 것 · 뒤에 해지된 것"*,
`ablation._current = A20R` → *"어긋나면 그 이유를 알려 주고 딱 한 번 다시 쓰게 한다"*.
색 규약은 ① 과 같되 라벨만 바꿨다 — *"규칙과 색인이 처리하는 단계"* / *"AI 모델이 글을
쓰는 단계"*. 그래야 두 도면을 나란히 놓아도 같은 색이 같은 뜻이다.

---

## 3. 🔴 코드에서 확인한 실제 흐름과 문서 서술이 어긋난 곳

도면을 그리려고 코드를 정본으로 읽는 과정에서 나온 것들이다. **코드는 고치지 않았다** —
어긋남을 기록만 한다.

### ① 과제 지시의 골격에 없는 단계 둘 — 입출력 안전 계층

지시 골격은 `질의 → 라우터` 로 시작한다. 코드는 **라우터 앞에 `sanitize_question`** 이
있고(`pipeline.py:2050`, PII 마스킹 + 프롬프트 인젝션 탐지), **응답 직전에 `mask_pii`**
가 있다(`:2119`). 둘 다 LLM 0회다. 안전성 축이 v4 의 평가 항목인데 도면에서 빠지면
"안전은 채점기에만 있다" 로 읽힌다. **도면 ①·② 에 둘 다 넣었다.**

### ② 한계 고지가 하나가 아니라 둘이다

지시 골격은 `범위 밖 → 한계 고지` 한 갈래다. 코드는 셋이다.

    r.kind == "unanswerable" && reason=no_such_report   NO_SUCH_REPORT   (:2057)
    r.kind == "unanswerable"                            UNANSWERABLE     (:2060)
    검색 결과 근거 0건                                    UNANSWERABLE     (:2074)

`pipeline.py:29-36` 이 그 이유를 적어 뒀다 — *"기업도 연도도 범위 안인데 그 보고서만
없는 경우 … '범위 밖' 이라고 말하면 사용자가 다시 물을 방법을 못 찾는다."* 도면 ① 은
두 갈래를 갈라 그렸고 옆에 그 사유를 붙였다.

### ③ critic(`judge`)은 LLM 호출인데 골격에 없다

지시 골격의 LLM 호출은 `생성` 과 `재생성` 둘로만 보인다. 코드에는 하나 더 있다 —
`quick_check(answer)["has_citation"]` 가 거짓이면 `judge(evidence, answer, budget)` 를
부른다(`:2104-2110`). `09-ablation.md:1611` 이 실측을 적어 뒀다: **47/88 발화**.
즉 **절반 이상의 문항에서 이 호출이 실제로 일어난다.** 도면 ① 은 이것을 ② 번 호출로
그리고 `인용이 없을 때만` 이라는 조건을 간선 라벨에 박았다.

### ④ 🔴 `06-agent-tool.md` F-1 표의 "검색 도구 7종" 이 틀렸다 — 같은 문서 안에서 모순

`tools.py` 의 `_RETRIEVAL_SPECS` 는 **8종**이다 (`resolve_company` · `query_events` ·
`list_filings` · `list_sections` · `read_section` · `search_chunks` · `get_history` ·
`query_by_counterparty`). 같은 문서의 게이트 F 항목은 이미 **"15종(검색 8 + 계산 7)"**
이라고 적고 *"⚠️ 종전 표기 14종은 `query_by_counterparty` 추가 전 값이었다"* 까지
달아 뒀는데, **F-1 표 첫 줄만 7종으로 남았다.** 표와 게이트가 서로 다른 수를 말한다.

### ⑤ 🔴 계산 도구 7종은 **호출되지 않는다** — 도면에 그대로 그리면 거짓이 된다

지시 골격은 `도구 … 계산 7종` 이라 적었다. 코드에서 `chat()` 은 `tools=` 를 **한 번도
넘기지 않는다** (`pipeline.py:2088` · `:2265`). `ablation.py:144-152` 가 그 사실과
결과를 적어 뒀다 — 결정적 계산값이 생성에 도달하는 경로는 하나뿐이고, 그것은
`corpus/markdown.to_markdown` 이 **근거 블록에 미리 박아 넣는 괄호**다.

도면 ① 은 이 칸을 *"등록돼 있으나 생성 호출에 `tools=` 를 넘기지 않는다 — 결정적
계산값은 근거 블록에 미리 박혀 프롬프트에 닿는다"* 로 적고, 실행 사슬이 아니라
**근거 성형으로 들어가는 점선**으로 그렸다. 실선으로 그렸으면 도면이 없는 호출을
있다고 말하게 된다.

### ⑥ 🔴 `09-ablation.md` 에 **§I-5g 가 없다** — v4 측정이 정본 문서에 안 실렸다

과제가 `§I-5f`·`§I-5g` 를 읽으라고 했는데 **`§I-5g` 는 존재하지 않는다.** 마지막
측정 절은 `§I-5f. v3 R1 실행`(2026-08-14)이다. 그래서 다음이 어긋난다.

| `09-ablation.md` 서술 | 코드 · 산출물의 현재 |
|---|---|
| `:1619` — `A20` 은 **"설계 확정 · 측정 전 — 선행조건 미충족"** 이고 *"`verify_answer_arithmetic` 이 `pipeline.py` 어디에서도 import/호출되지 않는다 = **배선 안 됨**"* | 🔴 **배선돼 있다.** `pipeline.py:13` import · `:2199` 호출 (`arm.substitute_verified or arm.regenerate_on_verify` 뒤) |
| `A20r` 은 표에서 *"재생성 축은 `A20r` 로 이관"* 수준의 계획 항목 | 🔴 **운영 기본값이다.** `ablation.py:779` `_current = A20R`, 실측 그리드 67/72 · 전체 83/88 · 밸류체인 7/12 · 안전성 9/12 (정본 `artifacts/ablation/v4/rescore_r3b/rescore.md`) |
| `verify_number_provenance`(`A21`) 언급 없음 | `critic.py:957` 구현 · `pipeline.py:2211` 배선 · `A20r` 이 그 판정을 **재생성 사유의 원천**으로 쓴다 |

즉 **`09-ablation.md` 는 v3 세대에서 멈춰 있고 v4 실행이 §I-5g 로 안 적혔다.**
도면은 코드를 정본으로 그렸으므로 도면 ① 과 `09-ablation.md` 를 나란히 읽으면 문서 쪽이
낡은 것으로 보인다. **그 방향이 맞다** — 다만 §I-5g 를 채우기 전까지는 제안서가
`A20`·`A20r` 을 인용할 때 정본을 `ablation.py` 와 `rescore_r3b/rescore.md` 로 걸어야 한다.

### ⑦ `06-agent-tool.md` 는 v4 를 통째로 모른다

`verify_number_provenance` · 재생성 1회 · `fault_response` · 안전성 축이 **한 번도
안 나온다**(grep 0건). F-4 비용표(`closed_lookup` 1회 · `complex_reason` 2~5회)도
재생성 호출을 안 세므로 운영 기본값에서 한 자리 낮다. 도면은 코드를 따랐다.

### ⑧ 체인 hop 번호는 문서가 맞다

`06-agent-tool.md` 가 `hop=5 strat=chain` 이라 적은 것은 코드와 일치한다
(`EVENTS_HOP = len(STRATEGIES) = 4` → `CHAIN_HOP = 5` → `COUNTERPARTY_HOP = 6`).
도면 ① 도 4 · 5 · 6 으로 적었다.

---

## 4. XML 유효성

```
PYTHONIOENCODING=utf-8 C:/Users/Juhwankim/miniconda3/python.exe -c "..."
 OK docs/figures/argus-algorithm-v3.drawio  mxCell 85 | vertex 53 | edge 30 | dup id 0
 OK docs/figures/argus-service-flow.drawio  mxCell 45 | vertex 33 | edge 10 | dup id 0
 OK docs/figures/argus-algorithm-v2.drawio  mxCell 60 | vertex 40 | edge 18 | dup id 0
```

* `xml.etree.ElementTree.parse` 통과 — 셋 다.
* **압축 안 씀.** 셋 다 `<diagram>` 에 `compressed` 속성이 없고 자식 텍스트(base64 본문)도
  없다 — 기존 `argus-algorithm-v2.drawio` 도 평문이었으므로 형식이 같다. diff 가 읽힌다.
* **id 중복 0 · 끊어진 간선 0** (모든 `source`/`target` 이 실재하는 id 를 가리킨다).
* 접두를 갈라 뒀다 — v3 는 `v3*`, 서비스 흐름은 `sf*`. 두 파일을 한 draw.io 문서로 합쳐도
  id 가 안 부딪는다.

전량 테스트:

```
PYTHONIOENCODING=utf-8 C:/Users/Juhwankim/miniconda3/python.exe -m pytest -q
1203 passed, 1 skipped, 1 warning in 26.15s
```

🔴 지시받은 기준선은 **1,184 passed · 1 skipped** 였고 실측은 **1,203 passed · 1 skipped**
다. 실패는 0 이고, 늘어난 19건은 이 작업이 만든 것이 아니다 — 도면 둘은 `docs/figures/`
아래 데이터 파일이라 수집 대상이 아니다(테스트 파일도, import 대상도 아니다). 같은 세대에
다른 에이전트가 테스트를 추가하는 중으로 본다. **기준선 표기를 갱신할 사람이 필요하다.**

---

## 5. 확인 못 한 것

1. **렌더 결과를 눈으로 못 봤다.** XML 유효성 · 좌표 겹침 없음 · 간선 끝점 실재까지는
   확인했지만, draw.io 로 열어 **글자 넘침 · 간선 우회 경로**를 본 적은 없다. v2 에는
   `.png` 가 함께 있는데 v3 는 PNG 를 만들지 않았다 (draw.io CLI 가 이 환경에 없다).
   제안서에 싣기 전에 사람이 한 번 열어 보고 PNG 를 뽑아야 한다.
2. **`_gather_evidence` 를 전량 읽지 않았다.** 2,359줄 중 검색 사다리 본문(1548~1815)은
   구조와 hop 번호 · 차선 분리 · 절단 규칙까지만 확인했고, 셀 분해(`_drain_report_cells`)
   내부와 `_gather_sections` 세부는 도면 해상도(한 칸)를 넘어서 안 팠다. 도면이 그 칸을
   `섹션 인덱스 hop` 한 상자로 접은 것이 과한 단순화인지는 미확인이다.
3. **`evalset/` 을 열지 않았다** (금지). 도면에 적은 실측 수치(코퍼스 4,204건 · 발화 2건 ·
   47/88 등)는 전부 코드 주석과 `docs/` 서술에서 옮긴 것이고 직접 세지 않았다.
4. **§I-5g 를 쓰지 않았다.** 이 보고서 §3⑥ 은 어긋남의 **기록**이고, 정본 문서를 고치는
   것은 이 태스크의 범위 밖이다 (`docs/pipeline/` 수정은 다른 에이전트와 충돌한다).
5. **v1 도면(`argus-algorithm-v1.drawio`)은 안 읽었다.** v2 가 그 후속이므로 v3 의 기준은
   v2 하나로 잡았다.
