# R1-12 — 페어 결함 넷을 닫는다

지시 원문의 넷(paper_guideline.md Toolformer 과장 · technical-proposal.md A5 function
calling 서술 7자리 · ablation.py "계산 도구 14종" 주석 · `test_build_errors_tool_and
_routing_are_zero_in_grid`) 을 각각 조사하고 닫았다. 코드 동작은 한 줄도 바꾸지 않았다
(③은 docstring 주석, 나머지는 `.md` 문서).

---

## 정본 확인 — "15종 / 14종 / 7종" 중 무엇이 맞는가

**직접 코드를 세어 확인했다** (문서를 서로 대조하는 방식이 아니라 원천 리스트 리터럴을
읽었다). `src/argus/agent/tools.py`:

```
_RETRIEVAL_SPECS = [ resolve_company, query_events, list_filings, list_sections,
                     read_section, search_chunks, get_history, query_by_counterparty ]  = 8개
_CALC_SPECS      = [ calc_pct_change, calc_share, calc_total, calc_mean,
                     calc_cagr, calc_compare, calc_period ]                              = 7개
TOOL_SPECS = _RETRIEVAL_SPECS + _CALC_SPECS                                              = 15개
```

**정본: 검색 8종 + 계산 7종 = 총 15종.** "14종"은 `query_by_counterparty`(A8 발주처
2-hop)가 추가되기 전(v1~v2 초) 값이 화석처럼 남은 것이다 — `docs/pipeline/06-agent-
tool.md:406`의 자체 각주("종전 표기 14종은 `query_by_counterparty` 추가 전 값이었다")가
이 경위를 이미 기록해 두었다. `docs/pipeline/09-ablation.md:296`이 "계산 도구 **7종**
(TOOL_SPECS 는 전체 **15종**)"이라 적은 것도 이 실측과 일치한다 — **09-ablation.md:296
이 정본이었다는 지시 원문의 전제가 코드로 재확인됐다.**

또 하나 별도로 확인한 사실(②·③의 근거): `pipeline.answer_question` → `chat(...,
max_tokens=…)` 호출에 `tools=` 인자가 없다 (`src/argus/agent/pipeline.py` 를 열지
않고 `ablation.py:119-121`·`base.md §7`·`06-agent-tool.md` 세 문서가 이미 동일하게
문서화해 둔 사실을 재확인하는 선에서 그쳤다 — `pipeline.py`는 편집 금지 대상이라 열람도
grep 호출 없이 기존 문서 인용만 사용했다). 그래서 15종 중 어느 것도 LLM 이 직접
호출하지 않는다 — 등록만 돼 있다.

---

## ① `paper/paper_guideline.md:59` — Toolformer 과장

**주의**: `paper/` 전체가 `.gitignore` 대상이다. `paper/base/base.md` 만 예외로
`git add -f` 돼 있다(커밋 `51c2eed`). `paper_guideline.md` 는 지금까지 **한 번도 git에
들어간 적이 없었다** — 이번이 첫 커밋이다.

**전**:
```
| [Toolformer](base/toolformer-2023.pdf) | 파인튜닝 필요 → 원본 불가. function calling으로 대체 |
```

**후**:
```
| [Toolformer](base/toolformer-2023.pdf) | 파인튜닝 필요 → 원본 불가. function calling
**발상**만 있고 🔴 **미연결**(`tools=` 가 `chat()` 에 안 실린다 — `base.md §7` 정정 참고) |
```

base.md §7 의 정정 문장(`"채택(대체 구현)"` → `"미연결"`)과 같은 어휘(🔴 미연결)를 쓰고,
상세 근거는 새로 쓰지 않고 `base.md §7` 을 정본으로 가리키게 했다(§2 Global Constraints
"정본을 정하고 나머지가 가리키게 한다").

커밋: `65d441c`

---

## ② `docs/proposal/technical-proposal.md` — A5 "도구 15종 function calling" 7자리

7곳 전부 확인 후 수정했다. **§4.6 의 계산 근거 단락(HCX-005 계약기간 오답 실측 등)은
이미 A5 의 진짜 기전(계산값을 `to_markdown` 이 근거 텍스트에 내장)을 맞게 적어 뒀었다**
— 그 문단을 정본으로 삼아 나머지를 맞췄다.

| 위치 | 전 | 후 |
|---|---|---|
| §1.5 (L159) | `도구 15종 (검색 8 + 계산 7 · tools.TOOL_SPECS 실측)` | 동일 + `· 🔴 생성 호출엔 미배선` 추가 |
| §2.3 문제4 (L196) | `→ A5 도구 15종 (검색 8 + 계산 7)` | `→ A5 계산값 근거 내장 (도구 15종은 등록만·미배선)` |
| §4.1 다이어그램 ⑦ (L431) | `HCX-005 · 도구 15종 function calling · 근거 인용 강제` | `HCX-005 · 계산값 근거 내장(도구 15종 미배선) · 근거 인용 강제` |
| §4.2 흐름도 (L483) | `HCX-005 (…) + 도구 15종` | `HCX-005 (…) + 계산값 근거 내장 (도구 15종 미배선)` |
| §4.6 (L753~) | 종전 14→15종 정정만 있고 "function calling 미배선" 캐벗 없음 | 새 🔴 단락 삽입: "이 15종은 지금 function calling 으로 배선돼 있지 않다" + 실제 경로(`to_markdown`) 명시 |
| 부록 B-1 (L2639) | `Toolformer(2023) \| 모델이 스스로 도구 호출 시점을 결정 \| **A5**` | `... \| 🔴 미연결 — 대체는 A5(계산값 근거 내장)` |
| 부록 B-1 (L2645-46) | `→ function calling 으로 대체하고, 필터링 발상을 A5 의 Δ 측정으로 옮겼다` | `→ function calling 으로 대체를 시도했으나 🔴 생성 호출에 tools 가 배선되지 않아 미연결로 남았다. 대신 …A5 의 Δ 측정으로 옮겼다` |

**15종이라는 수 자체는 틀리지 않았다** — 위 "정본 확인" 절대로 실측과 일치한다. 틀린
것은 "A5 = 15종을 **function calling** 으로 쓴다"는 **기전 서술**이었다. 15종은 등록만
돼 있고, A5 가 실제로 재는 것은 계산 함수 7종을 **LLM 호출 없이** 직접 실행해 결과를
근거 텍스트에 미리 박아 넣는 경로다.

커밋: `3135af5`

---

## ③ `src/argus/ablation.py:119` — "계산 도구 14종"

**전**: `계산 도구 14종은 \`TOOL_SPECS\` 로 노출돼 있지만 **생성 호출에는 \`tools\` 를 넘기지 않는다**`
**후**: `계산 도구 7종은 \`TOOL_SPECS\` 로 노출돼 있지만 **생성 호출에는 \`tools\` 를 넘기지 않는다**`

이 줄은 파일 최상단 모듈 docstring(1~223행) 안의 마크다운 문장이다 — Python 실행에
전혀 관여하지 않으므로 이 한 글자 수정은 **동작 변화가 0**이다.

**파일이 다른 작업으로 지저분했다.** `git status` 로 확인한 결과 같은 파일에
`substitute_verified`/`FLAG_DEPENDENCIES["verifier"]` 관련 대규모 편집이 이미 진행
중이었다(A20 관련, 다른 에이전트 작업으로 추정). `git commit --only` 로는 파일 전체가
같이 커밋되므로, `git diff` 로 첫 번째 hunk(14→7 한 줄)만 골라 `.patch` 로 저장 →
`git apply --cached` 로 그 hunk **만** 인덱스에 스테이징 → 커밋. 커밋 후
`git diff`로 확인한 결과 다른 작업의 변경분(`substitute_verified` 필드·
`FLAG_DEPENDENCIES` 항목·주석 20여 줄)은 작업 트리에 **그대로 손대지 않은 채** 남아
있음을 확인했다.

커밋: `21d37bf`

---

## ④ `test_build_errors_tool_and_routing_are_zero_in_grid` — 판정: 이미 해결됨 (코드 无존재)

### 조사 결과

**이 테스트는 실행 가능한 코드로 존재한 적이 없다.**

```
grep -r "tool_and_routing_are_zero_in_grid" tests/   → 0건
grep -r "build_errors"                     tests/   → 0건 (site 관련 스크립트/테스트 미구현)
git log --all -- tests/test_site_data.py scripts/build_site_data.py   → 커밋 0건
```

이 이름은 **아직 만들어지지 않은 웹사이트(R6, "제출물 아님") 테스트 스위트의 계획서
초안**에서만 존재했다 — `docs/superpowers/plans/2026-08-11-argus-website.md` 의
**옛 버전**에 명세된 픽스처였다(website-review.md 의 "종전(계획서)" 열이 그 옛 버전을
인용한 것). 실제 파이썬 `tests/` 디렉터리에는 한 번도 들어간 적이 없다.

### 이미 닫혀 있었다

`docs/superpowers/plans/2026-08-11-argus-website.md` (현재 라이브 버전, 커밋
`eb924ca` · `git status` 클린)을 열어 보니, **문제의 그 이름/단언은 이미 없다.**
대신 아래로 교체돼 있다:

```python
def test_build_errors_stage_histogram_is_not_asserted_by_value():
    """🔴 단계별 0/비0 을 못박지 않는다.

    종전 판은 `TOOL == 0` · `ROUTING == 0` 을 단언했다. `Q-097` 은 실제로
    `root_cause=TOOL` 로 찍혀 있었고(v4 `R0-2`), 그 단언은 세대에 따라 깨진다.
    여기서는 **분포가 원천과 같은지**만 본다.
    """
    payload = bsd.build_errors()
    gen = bsd.resolve_generation()
    source = bsd.read_json(f"artifacts/ablation/{gen}/A8/report.json")["failure_histogram_grid"]
    for s in payload["stages"]:
        assert s["gridCount"] == source.get(s["stage"], 0), s["stage"]
```

그리고 `.superpowers/sdd/2026-08-14-ablation-v4/website-review.md` (커밋 `f37df47`)
가 이 교체를 **B7 행**으로 이미 기록해 두었다:

```
B7 | test_build_errors_tool_and_routing_are_zero_in_grid (TOOL == 0)
   | 🔴 현 세대 실측 TOOL 3 · GENERATION 2 · VERIFY 1 — 단언이 거짓이 됐다.
     **분포가 원천과 같은지**만 검사
```

### 판정

세 갈래(🟢 값 갱신 / 🟡 이름 정정 / ❌ 삭제) 중 어느 것도 정확히 들어맞지 않는다 —
**코드가 없어서 "지금 통과하는지" 자체를 확인할 수 없었다.** 실질적으로는 **🟡에
해당하는 조치가 이미 선행 작업(website 계획 개정 `eb924ca` → 검토 `f37df47`)에서
끝나 있었다**: 고정값 단언을 없애고 자기일관성 검사(`stage 합 == source` 대조)로
이름·의도를 함께 바꿨다. **내가 새로 커밋할 코드/문서 diff가 없다** — 이 작업에 대한
별도 커밋은 만들지 않았다.

**남은 흠 하나**: `docs/superpowers/plans/2026-08-14-ablation-v4.md` §12b 의 R5-7
대기 목록("열넷")이 이 사실을 아직 반영하지 못하고 옛 이름을 "닫아야 할 결함"으로
들고 있다 — 이 파일은 사용자가 직접 관리하는 문서라 손대지 않았다. 아래 "추가로 찾은
결함" 목록에 참고용으로만 적는다(수정 아님).

---

## 전량 테스트

```
PYTHONIOENCODING=utf-8 C:/Users/Juhwankim/miniconda3/python.exe -m pytest -q
990 passed, 1 warning in ~20s   (두 차례 재확인 — 커밋 전/후 동일)
```

기준선 973 보다 많다(다른 동시 작업의 신규 테스트 포함으로 추정) — **감소 없음.**
③ 이 docstring 한 줄만 바꿨으므로 동작 회귀가 없다는 것도 이 결과로 확인된다.

---

## 추가로 찾은 같은 양식의 결함 (고치지 않음 — 보고만)

지시대로 **손대지 않았다.** 사용자가 판단할 자료로만 남긴다.

1. 🔴 **`docs/pipeline/06-agent-tool.md:14`** — F-1 "현재 상태" 표:
   `| 계산 도구 7종 (TOOL_SPECS) | 🟢 F-2 에서 노출 완료 — 총 14종 |`
   **같은 파일** §게이트F(`06-agent-tool.md:405-407`)는 이미 "TOOL_SPECS **15종**
   (검색 8 + 계산 7 · ⚠️ 종전 표기 14종은 query_by_counterparty 추가 전 값이었다)"로
   정정돼 있다. **한 파일 안에서 한쪽만 고쳐진 전형** — ③과 똑같은 결함이 이 문서
   14행에 그대로 남아 있다.

2. 🔴 **`docs/pipeline/06-agent-tool.md:405`** — 게이트 체크리스트:
   `[x] 계산 함수 7종이 TOOL_SPECS 에 노출되고 **function calling 으로 호출됨**`
   이건 과장이 아니라 **완료 체크(`[x]`)로 표시된 거짓 진술**이다 — `ablation.py:119`
   자신이 "생성 호출에는 tools 를 넘기지 않는다"고 명시한 사실과 정면으로 어긋난다.
   ①②에서 고친 "과장"류보다 한 단계 더 나쁘다(미완료를 완료로 체크해 둔 것).

3. 🔴 **`docs/pipeline/09-ablation.md:183`** — §I-2 arm 사다리 요약표:
   `| A5 | + 계산 Tool (14종 function calling) | 실행 |`
   **바로 이 문서** `09-ablation.md:295-299`(§I-5b 근처)는 이미 "A5 의 Δ 는 계산
   도구의 function calling 이 아니라 근거에 박아 둔 계산 결과다" · "계산 도구 7종
   (TOOL_SPECS 전체 15종)"이라고 실측으로 정정해 두었다. **같은 문서 안의 자기모순**
   이라 §2 Global Constraints 가 "가장 나쁘다"고 지목한 유형("정본이 스스로 어긋나
   있으면 어느 사본이 맞는지 알 수 없다")에 정확히 해당한다. 부수적으로 §I-1 사다리
   설계표(`09-ablation.md:41`, `A5 | + 계산 Tool | function calling | [Toolformer]…`)
   도 같은 어휘를 쓰지만 이쪽은 "설계 시점 매핑" 성격이 강해 우선순위는 낮다.

이 셋은 전부 **오늘 고친 ①②③과 정확히 같은 모양**(총량 15/7 vs 낡은 14, 그리고
"function calling"이 실제로 안 걸려 있다는 사실 누락)이라, 다음에 이 영역을 다시
볼 때 **한 번에 같이 잡는 것**을 권한다.

---

## 커밋

```
65d441c fix(docs): paper_guideline.md 의 Toolformer 과장을 base.md 정정과 맞춘다     [①]
3135af5 fix(docs): A5 를 '도구 15종 function calling' 으로 서술한 7자리를 정정한다   [②]
21d37bf fix(docs): ablation.py 주석의 "계산 도구 14종"을 실측 7종으로 고친다        [③]
                                                                                    [④ 코드 diff 없음]
```
