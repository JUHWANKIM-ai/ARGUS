# 원자료 전처리로 닫는 것 — 2026-08-21

> **작업자에게:** 이 계획은 `superpowers:subagent-driven-development` 또는
> `superpowers:executing-plans` 로 과제 단위로 실행한다. 단계는 `- [ ]` 체크박스다.

작업 브랜치 없음. **`main` 에서 바로 작업한다** (사용자 지시, 2026-08-21).

**목표** 남은 결함 중 **원인이 데이터 표현 층에 있는 둘**을 닫는다 — `Q-116` 정정 역행과
`Q-054`·`Q-068`·`Q-082`·`Q-094` 원공시 미인용.

**접근** 원자료 재파싱이 아니다. **이미 산출물에 들어 있는데 근거 문면까지 못 온 사실**을
런타임에 덧붙인다(`nary` 이음매). periodic 그룹에만 없는 정정 링크는 **대장 조인 하나**로
새로 만든다. `to_markdown` 과 BM25 색인은 한 바이트도 안 바꾼다.

**기술 스택** Python 3.x · `argus.ablation` 플래그 · `argus.agent.tools` 런타임 병합 · pytest

**진단 정본** `docs/open-issues-v2.md` §4.4 · §5.3
**측정 기준** `scratch/ablation/v6/server_{full,valuechain,safety}_rN/A56` (서버 3회)
**파이썬** `C:/Users/Juhwankim/miniconda3/python.exe` — `PATH` 의 `python` 은 Store 스텁이라
exit 49 로 죽는다. 시험은 `C:/Users/Juhwankim/miniconda3/python.exe -m pytest`

---

## 전역 제약

1. **생성 프롬프트를 건드리지 않는다.** `prompts._BASE_RULES` · `_DIRECT` · `_COT` ·
   `CITATION_RE` · `SYSTEM_CRITIC` 는 이 계획 내내 불변이다.
2. **`artifacts/*_records.jsonl` 과 `*_bm25.json` 을 다시 만들지 않는다.** 고치면 BM25 색인
   문자열이 갈려 v1~v9 측정이 전량 무효가 된다 (`scripts/build_index.py:44` ·
   `src/argus/corpus/markdown.py:105-118`). 새 파일을 만들고 **런타임에 덧붙인다** —
   `nary` 가 쓴 이음매다 (`src/argus/agent/tools.py:68-75`).
3. **스위치로 세우고 잰다.** 처방마다 `ablation.Arm` 플래그 하나, 기본은 꺼 둔다.
4. **퇴행 0 이 채택 조건이다.** 112문항에서 한 건이라도 새로 깨뜨리면 채택하지 않는다.
5. **서버 배포는 사용자 승인 없이 하지 않는다.** 로컬 측정까지가 범위다.
6. **못 재면 적지 않는다.** 안 먹으면 "안 먹었다" 를 남긴다. 기각도 결과다.
7. **자기 성적을 부풀리는 줄을 쓰지 않는다.** 근거에 안 실린 공시의 접수번호를 블록 안에
   적으면 `error_analysis._blocks` 가 그것을 적재된 골든으로 읽는다
   (`src/argus/agent/pipeline.py:862-874`). §5 가 이 계획에서 그 선에 닿는 자리를 지목한다.

---

## 0. 무엇이 남았는가 — 캠페인 진행분을 먼저 뺀다

`open-issues-v2.md` §8 의 우선순위 여섯 중 넷은 이 계획을 세우는 사이에 이미 닫혔다.
`ablation.py` 의 `A62` 주석이 측정을 갖고 있다.

| §8 | 항목 | 처분 |
|---|---|---|
| 1 | `Q-115` 열거≠합계 | **닫혔다** — `A61`(`aggregate_items`+`sum_operand_fix`) 3/3 수리 |
| 3 | `Q-053` 표기 미부착 | **닫혔다** — `A57`(`refusal_needs_gap`) 표기 0/3 → 3/3 |
| 4 | `Q-113` 자기 산술 | 🔴 **안 닫혔다** — 아래 정정 참고 |
| 6 | `Q-110` 답변·표기 | **닫혔다** — `A60`(`roster_items`) 정답 0/3 → 3/3 · 표기 0/3 → 3/3 |

🔴 **정정 (검토, 2026-08-21).** 첫 판이 `Q-113` 을 닫힌 것으로 적었다. **틀렸다.**
`equation` 이 3회 → 0회가 된 것은 `Q-115` 다. `A61` 게이트가 잰 넷(`Q-034` `Q-107`
`Q-109` `Q-115`)에 `Q-113` 이 없고, 그 문항은 `A62` 합성의 **불변 90문항**에 속해 `A56`
동작을 그대로 물려받는다. 축 실측(`scratch/audit/axis_A62.json`):

```
추론 논리성  A56 1·3·2 → A62 0·2·1   합집합 Q-065 Q-113
```

**남은 것은 넷이고, 그중 둘의 원인이 데이터 표현 층에 있다.**

| | 남은 것 | 원인이 어디인가 | 이 계획 |
|---|---|---|---|
| **W-A** | §4.4 `Q-116` 정정 역행 (결함 3/3) | **레코드에 링크가 있는데 문면에 안 나온다** | **과제 1** |
| **W-B** | §5.3 원공시 미인용 4문항 | **periodic 그룹에 정정 링크가 아예 없다** | **과제 2·3** |
| — | §4.1 `Q-110` 근거 완전성 167/168 | **선택 문제다. 전처리 밖** | §6.1 |
| — | §5.2 생성 7건 | **생성. 전처리 밖** | §6.2 |
| — | §4.5 `Q-113` 자기 등식 (2/3) | **생성. 전처리 밖** | §6.4 |

---

## 1. W-A 진단 — 링크는 있고 문면에 없다

`related` 는 이미 레코드에 있고 양방향이다. `artifacts/exchange_records.jsonl` 실측:

```
20240722800037  amount 581,790,000,000  is_latest=False  related=["20250923800193"]
20250923800193  amount 583,874,002,800  is_latest=True   related=["20240722800037"]
```

그런데 `src/argus/corpus/markdown.py:52-100` 의 `to_markdown` 이 `related` 도 `is_latest` 도
**한 줄도 안 쓴다.** 두 레코드의 `markdown` 을 그대로 찍어 확인했다 — 대체본 블록에 폐기
신호가 **하나도 없다.**

런타임에는 줄이 있다 — `src/argus/agent/pipeline.py:426` 의 `_chain_note` 가
`- 정정체인: n/N (원공시 · 대체됨)` 을 박는다. **그런데 체인을 걸었을 때만 붙는다.** 씨앗
조건이 `CHAIN_SEED_WINDOW = 2` 이고 `Q-116` 의 대체본은 근거 5번째다 (`pipeline.py:522`).
그래서 그 줄이 안 붙는다.

**창을 넓히는 판(`latest_pin`)은 퇴행 2건으로 기각됐다** — 상한 있는 창에 더하는 것은
밀어내는 것이었다. 레코드에서 직접 각인하는 판은 **창을 안 건드리고 근거 슬롯을 하나도 더
안 쓴다** (블록 안의 줄 하나). 그리고 순위와 무관하게 **모든 대체본에** 붙는다 — §4.4 가
센 "17문항 18자리" 가 전부 여기 걸린다.

---

## 2. W-B 진단 — periodic 그룹에는 정정 체인이 없다

`scripts/build_index.py:41` 이 `build_chains` 를 부르는데 그건 exchange·major·holding 만
탄다. periodic 은 `scripts/build_sections.py` 를 타고, `artifacts/periodic_sections.jsonl` 의
필드는 이것뿐이다:

```
chunk_id doc_id corp_code corp_name sector doc_subtype base_year base_month
rcept_dt is_correction path level n_chars n_tables is_stub is_container
text_offset text_bytes
```

`is_correction` 불리언은 있고 **무엇을 정정했는지가 없다.** periodic 레코드 파일 자체가
없으므로 `tools.chain_probe` 도 periodic 접수번호에 대해 `None` 을 돌려준다.

**이것이 `A58` 의 사정거리가 짧았던 이유다.** `_cite_chain_pair` docstring 이 적어 둔
*"`Q-054`·`Q-068`·`Q-082` 는 원공시가 근거에 아예 없다 · `Q-094` 는 정정본의 `links` 가
비어 있다"* (`src/argus/ablation.py:1976-1983`) — 넷 다 periodic 이고, `links` 가 빈 게
아니라 **그 그룹에 링크를 만드는 코드가 없다.**

### 링크는 대장 하나로 나온다 (실측)

`report_nm` 이 `[기재정정]사업보고서 (2024.12)` 형태다. (기업 · 서식 · 기간) 으로 조인해 쟀다:

```
periodic 정정 159건
  단일 원공시로 확정   157건 (98.7%)
  후보 2+ (모호)         0건
  원공시가 코퍼스에 없다  2건   ← 한화솔루션 20230522000371 · 20260325001401
report_nm 파싱 실패      0건 / periodic 1,054건 전부
```

원문 파싱 0 · LLM 0 · 모호 0 이다. 그리고 §5.3 표의 periodic 넷이 **글자 그대로 재현된다**:

```
Q-054  20240514001580 -> 20240514000756   HMM        분기보고서 (2024.03)
Q-068  20250814001076 -> 20250321000310   한전기술     사업보고서 (2024.12)
Q-082  20240826000294 -> 20240814003869   현대글로비스   반기보고서 (2024.06)
Q-094  20250328002285 -> 20250318001264   NC         사업보고서 (2024.12)
```

다섯째 `Q-093` 은 exchange 이고 `A58` 이 이미 닫았다.

### 사정거리를 미리 좁혀 둔다

§5.3 이 센 어긋남은 **접수번호 미기재**다 — 원공시 *본문*이 아니라 *접수번호*가 빠진 것이다.
과제 3 은 정정본 블록에 **그 문서 자신의 메타데이터**(무엇을 정정한 것인가)를 적는다.
원공시 본문을 근거에 끌어오는 것은 회수 변경이고 이 계획 밖이다. 그래서 이 과제는
`- 정정 대상 원공시: 접수번호 …` 를 적고, *무엇이 어떻게 바뀌었나*는 원공시 본문이 함께
실린 문항에서만 답해진다.

---

## 3. 파일 구조

| 파일 | 책임 | 과제 |
|---|---|---|
| `src/argus/ablation.py` | 플래그 `stale_note` · `section_chain_note`, 팔 `A63` · `A64` · `A65` | 1·3·4 |
| `src/argus/agent/tools.py` | `_stale_line` · `_group_records_stale` · `_periodic_chains` · `_section_markdown` | 1·3 |
| `src/argus/agent/pipeline.py` | `_annotate` 중복 방지 | 1 |
| `scripts/build_periodic_chains.py` | **신설.** 대장 조인 → `artifacts/periodic_chains.jsonl` | 2 |
| `scripts/build_preprocessing_manifest.py` | 제출 목록에 새 산출물 한 줄 | 2 |
| `tests/test_stale_note.py` | **신설** | 1 |
| `tests/test_periodic_chains.py` | **신설** | 2·3 |
| `tests/test_artifacts_layout.py` | 새 산출물 존재 확인 | 2 |

---

## 과제 1 — 대체본에 폐기 표시를 각인한다 (`stale_note`)

**파일**
- 수정: `src/argus/ablation.py` (플래그 선언 · 팔 `A63`)
- 수정: `src/argus/agent/tools.py` (`_group_records` 분기 · `_stale_line` · `_group_records_stale`)
- 수정: `src/argus/agent/pipeline.py` (`_annotate` 중복 방지)
- 시험: `tests/test_stale_note.py`

**인터페이스**
- 소비: `tools._group_records_raw(group) -> dict[str, dict]` (기존)
- 생산: `tools._stale_line(record, latest) -> str | None` ·
  `tools._group_records_stale(group) -> dict[str, dict]` · `ablation.Arm.stale_note: bool`

### 줄의 모양 — 최신본 접수번호는 안 적는다

```
- 정정체인: 이 공시는 정정되어 대체됨 (최신 계약금액 583,874,002,800원)
```

🔴 **최신본 접수번호를 적지 않는 것이 의도다.** 적으면 그 블록 안의 14자리가
`error_analysis._blocks` 에게 *적재된 골든*으로 읽혀 우리 근거 재현율만 올라간다
(`pipeline.py:862-874` 가 `roster_items` 에서 같은 이유로 `loaded` 로 제한한 자리다).
금액은 14자리 접수번호가 아니므로 그 회계에 안 들어간다.

🔴 **`정정체인:` 과 `대체됨` 두 낱말은 하중을 받는다.** `critic._STALE_NOTE`
(`정정체인:.*대체됨`, `src/argus/agent/critic.py:1200`)가 이 어구로 블록의 체인 위치를 읽어
`stale_value` 검사를 돈다. 바꾸면 그 검사의 전제가 사라진다.

- [ ] **단계 1: 실패하는 시험을 쓴다**

`tests/test_stale_note.py`:

```python
"""대체본 각인. 레코드의 related·is_latest 는 이미 있고 문면에 안 나온다."""
from argus import ablation
from argus.agent import tools


def test_stale_line_names_the_corrected_amount_but_no_receipt():
    record = {"rcept_no": "20240722800037", "is_latest": False,
              "amount": 581_790_000_000, "amount_type": "계약금액"}
    latest = {"rcept_no": "20250923800193", "is_latest": True,
              "amount": 583_874_002_800, "amount_type": "계약금액"}
    line = tools._stale_line(record, latest)
    assert line == ("- 정정체인: 이 공시는 정정되어 대체됨 "
                    "(최신 계약금액 583,874,002,800원)")
    assert "20250923800193" not in line          # 자기 성적 부풀리기 금지


def test_stale_line_is_none_for_a_latest_record():
    record = {"rcept_no": "20250923800193", "is_latest": True, "amount": 1}
    assert tools._stale_line(record, None) is None


def test_stale_line_without_an_amount_delta_still_marks_supersession():
    record = {"rcept_no": "A", "is_latest": False, "amount": 100}
    latest = {"rcept_no": "B", "is_latest": True, "amount": 100}
    assert tools._stale_line(record, latest) == "- 정정체인: 이 공시는 정정되어 대체됨"


def test_arm_off_returns_the_untouched_mapping():
    with ablation.using("A56"):
        tools._group_records_stale.cache_clear()
        assert tools._group_records("exchange") is tools._group_records_raw("exchange")


def test_arm_on_stamps_the_superseded_record_only():
    with ablation.using(ablation.A63):
        tools._group_records_stale.cache_clear()
        merged = tools._group_records_stale("exchange")
        stale = merged["exchange_20240722800037"]["markdown"]
        latest = merged["exchange_20250923800193"]["markdown"]
        assert "정정되어 대체됨" in stale
        assert "583,874,002,800" in stale
        assert "정정되어 대체됨" not in latest


def test_stamped_line_sits_directly_under_the_header():
    with ablation.using(ablation.A63):
        tools._group_records_stale.cache_clear()
        merged = tools._group_records_stale("exchange")
        lines = merged["exchange_20240722800037"]["markdown"].split("\n")
        assert lines[0].startswith("## ")
        assert lines[1].startswith("- 정정체인:")
```

- [ ] **단계 2: 실패를 확인한다**

```
C:/Users/Juhwankim/miniconda3/python.exe -m pytest tests/test_stale_note.py -v
```

기대: `AttributeError: module 'argus.agent.tools' has no attribute '_stale_line'`

- [ ] **단계 3: 플래그를 세운다**

`src/argus/ablation.py`, `citation_note_fix` 선언 바로 아래:

```python
    #: 대체본 블록에 폐기 표시를 각인한다. 레코드의 `related` · `is_latest` 는 이미 있고
    #: `to_markdown` 이 안 쓴다. 런타임 창(`CHAIN_SEED_WINDOW = 2`)과 무관하게 붙으므로
    #: `latest_pin`(퇴행 2건으로 기각)과 달리 근거 슬롯을 하나도 더 안 쓴다.
    #: 최신본 접수번호는 적지 않는다 — 근거에 안 실린 공시의 14자리를 블록에 쓰면
    #: `error_analysis._blocks` 가 적재된 골든으로 읽어 우리 재현율만 오른다.
    stale_note: bool = False
```

- [ ] **단계 4: 병합을 구현한다**

`src/argus/agent/tools.py`, `_group_records` 분기에 한 줄 (`arm.nary` 분기 앞):

```python
    if arm.stale_note:
        return _group_records_stale(group)
```

같은 파일, `_group_records_nary` 아래:

```python
def _stale_line(record: dict, latest: dict | None) -> str | None:
    """대체본 각인. 최신본이 같은 그룹에 있을 때 최신 금액까지 적는다.

    🔴 `정정체인:` 과 `대체됨` 은 하중을 받는 낱말이다. `critic._STALE_NOTE` 가 이 어구로
    체인 위치를 읽어 `stale_value` 검사를 돈다.

    🔴 최신본 접수번호는 적지 않는다. 근거에 안 실린 공시의 14자리를 블록에 쓰면
    `error_analysis._blocks` 가 적재된 골든으로 읽어 우리 재현율만 오른다 —
    `pipeline._roster_line` 이 `loaded` 로 제한한 것과 같은 근거다.
    """
    if record.get("is_latest", True):
        return None
    head = "- 정정체인: 이 공시는 정정되어 대체됨"
    if not latest:
        return head
    amount, mine = latest.get("amount"), record.get("amount")
    if not isinstance(amount, int) or amount == mine:
        return head
    label = latest.get("amount_type") or "금액"
    return f"{head} (최신 {label} {amount:,}원)"


@lru_cache(maxsize=len(RECORD_GROUPS))
def _group_records_stale(group: str) -> dict[str, dict]:
    """각인한 사본. 원본 매핑은 절대 변형하지 않는다 — 같은 프로세스에서 다른 팔을
    돌리면 그 팔의 근거가 조용히 오염된다. `_group_records_nary` 와 같은 규율이다."""
    raw = _group_records_raw(group)
    by_rcept = {str(r.get("rcept_no")): r for r in raw.values()}
    out: dict[str, dict] = {}
    changed = False
    for doc_id, record in raw.items():
        links = [by_rcept.get(str(r)) for r in (record.get("related") or ())]
        latest = next((r for r in links if r and r.get("is_latest")), None)
        line = _stale_line(record, latest)
        if not line or line in (record.get("markdown") or ""):
            out[doc_id] = record
            continue
        changed = True
        head, sep, rest = (record.get("markdown") or "").partition("\n")
        out[doc_id] = {**record,
                       "markdown": f"{head}\n{line}\n{rest}" if sep else f"{head}\n{line}"}
    return out if changed else raw
```

- [ ] **단계 5: `_annotate` 가 줄을 두 번 넣지 않게 한다**

각인된 줄이 이미 있는데 런타임이 체인을 걸면 `- 정정체인:` 이 두 줄 생긴다. 런타임 줄이
더 자세하므로(`n/N`) 그것으로 **교체**한다. `src/argus/agent/pipeline.py`:

```python
def _annotate(markdown: str, note: str) -> str:
    """머리글 바로 밑에 줄 하나를 넣는다. 블록 경계는 건드리지 않는다.

    같은 접두사(`- 정정체인:`)의 줄이 이미 있으면 넣지 않고 **교체**한다. 각인
    (`ablation.stale_note`)과 런타임 체인 주석이 같은 블록에서 만나면 두 줄이 되고,
    둘이 같은 것을 다르게 말하면 모델이 어느 쪽을 믿을지가 미정이 된다. 런타임 줄이
    체인 위치(`n/N`)까지 아는 쪽이라 그것이 남는다.
    """
    if not markdown:
        return markdown
    prefix = note.split(":", 1)[0] + ":"
    lines = markdown.split("\n")
    for i, line in enumerate(lines[1:], 1):
        if line.startswith(prefix):
            lines[i] = note
            return "\n".join(lines)
    head, sep, rest = markdown.partition("\n")
    if not sep:
        return f"{markdown}\n{note}"
    return f"{head}\n{note}\n{rest}"
```

- [ ] **단계 6: 팔을 세운다**

`src/argus/ablation.py`, `A62` 정의 아래:

```python
# --- W-A : 대체본에 폐기 표시를 각인한다 --------------------------------------
#: `A56` 위에 `stale_note` 하나. 계획서
#: `docs/superpowers/plans/2026-08-21-raw-preprocessing.md` 의 과제 1.
#: `A57`~`A61` 위가 아니다 — 델타를 섞지 않는다.
#:
#: `Q-116` 실측(서버 3회 전부): 답변이 폐기된 581,790,000,000 을 말하고 최신값
#: 583,874,002,800 을 한 번도 안 쓴다. 대체본 블록에 폐기 신호가 문면에 하나도 없기
#: 때문이다 — `related` 는 레코드에 있고 `to_markdown` 이 안 쓴다.
#:
#: 🔴 `latest_pin`(같은 진단, 창을 넓히는 처방)은 퇴행 2건으로 기각됐다. 이 팔은 창을
#: 안 건드리고 근거 슬롯을 하나도 더 안 쓴다 — 블록 안의 줄 하나다.
A63 = replace(A56, name="A63", stale_note=True)
```

- [ ] **단계 7: 통과를 확인한다**

```
C:/Users/Juhwankim/miniconda3/python.exe -m pytest tests/test_stale_note.py tests/test_pipeline.py tests/test_ablation.py tests/test_critic.py tests/test_latest_pin.py -v
```

기대: 전부 PASS. `_annotate` 를 고쳤으므로 `test_pipeline.py` · `test_critic.py` ·
`test_latest_pin.py` 를 함께 돈다.

- [ ] **단계 8: 커밋**

```bash
git add src/argus/ablation.py src/argus/agent/tools.py src/argus/agent/pipeline.py tests/test_stale_note.py
git commit -m "feat(v10): 대체본 블록에 폐기 표시를 각인한다 — 팔 A63"
```

---

## 과제 2 — periodic 정정 체인을 만든다

**파일**
- 신설: `scripts/build_periodic_chains.py`
- 신설: `tests/test_periodic_chains.py`
- 수정: `scripts/build_preprocessing_manifest.py` (`FILES` 에 한 줄)
- 수정: `tests/test_artifacts_layout.py`
- 산출: `artifacts/periodic_chains.jsonl`

**인터페이스**
- 소비: `data/dart-disclosures/manifest.jsonl` (`corp_name` · `report_nm` · `rcept_no` ·
  `rcept_dt` · `is_correction` · `doc_group`)
- 생산: `build_chains(manifest: list[dict]) -> list[dict]` · 모듈 상수 `REPORT_KEY` ·
  `OUT_NAME`. 줄마다
  `{"rcept_no": str, "root": str | None, "prev": str | None, "corp_name": str,
    "report": str, "period": "YYYY.MM", "reason": str}` —
  `root` 는 원공시, `prev` 는 직전 제출, `reason` 은 못 이었을 때만 채운다.

🔴 **못 이은 것을 버리지 않고 적는다.** `corpus/references.py` 가 세운 규율이다 — 파서
실패와 진짜 부재가 구별돼야 한다. 실측 2건은 원공시가 코퍼스에 없는 경우이고 그건 *안
잇는 것이 옳은 결과*다.

- [ ] **단계 1: 실패하는 시험을 쓴다**

`tests/test_periodic_chains.py`:

```python
"""periodic 정정 체인. 대장 조인 하나로 나온다 — 원문 파싱도 LLM 도 없다."""
import json
import sys
from pathlib import Path

sys.path.insert(0, "scripts")

from build_periodic_chains import REPORT_KEY, build_chains   # noqa: E402

FIXTURE = [
    {"rcept_no": "20250318001264", "corp_name": "NC", "doc_group": "periodic",
     "report_nm": "사업보고서 (2024.12)", "rcept_dt": "20250318", "is_correction": False},
    {"rcept_no": "20250328002285", "corp_name": "NC", "doc_group": "periodic",
     "report_nm": "[기재정정]사업보고서 (2024.12)", "rcept_dt": "20250328",
     "is_correction": True},
    {"rcept_no": "20230522000371", "corp_name": "한화솔루션", "doc_group": "periodic",
     "report_nm": "[기재정정]분기보고서 (2023.03)", "rcept_dt": "20230522",
     "is_correction": True},
    {"rcept_no": "20240722800037", "corp_name": "두산에너빌리티", "doc_group": "exchange",
     "report_nm": "단일판매ㆍ공급계약체결", "rcept_dt": "20240722", "is_correction": False},
]


def test_report_key_reads_form_and_period_through_the_revision_tag():
    assert REPORT_KEY.match("[기재정정]사업보고서 (2024.12)").groups() == (
        "사업보고서", "2024", "12")
    assert REPORT_KEY.match("사업보고서 (2024.12)").groups() == ("사업보고서", "2024", "12")
    assert REPORT_KEY.match("단일판매ㆍ공급계약체결") is None


def test_correction_links_to_the_single_original():
    rows = {r["rcept_no"]: r for r in build_chains(FIXTURE)}
    assert rows["20250328002285"]["root"] == "20250318001264"
    assert rows["20250328002285"]["prev"] == "20250318001264"
    assert rows["20250328002285"]["period"] == "2024.12"
    assert rows["20250328002285"]["reason"] == ""


def test_missing_original_is_recorded_not_dropped():
    rows = {r["rcept_no"]: r for r in build_chains(FIXTURE)}
    assert rows["20230522000371"]["root"] is None
    assert rows["20230522000371"]["reason"] == "원공시가 코퍼스에 없다"


def test_only_periodic_corrections_are_emitted():
    ids = {r["rcept_no"] for r in build_chains(FIXTURE)}
    assert ids == {"20250328002285", "20230522000371"}


def test_real_corpus_links_157_of_159():
    """실측 고정(2026-08-21). 이 수가 움직이면 대장이 바뀐 것이다."""
    text = Path("data/dart-disclosures/manifest.jsonl").read_text(encoding="utf-8")
    manifest = [json.loads(line) for line in text.splitlines() if line.strip()]
    rows = build_chains(manifest)
    assert len(rows) == 159
    assert sum(1 for r in rows if r["root"]) == 157
    assert sum(1 for r in rows if r["reason"].startswith("원공시 후보가")) == 0
    linked = {r["rcept_no"]: r["root"] for r in rows}
    assert linked["20240514001580"] == "20240514000756"      # Q-054 HMM
    assert linked["20250814001076"] == "20250321000310"      # Q-068 한전기술
    assert linked["20240826000294"] == "20240814003869"      # Q-082 현대글로비스
    assert linked["20250328002285"] == "20250318001264"      # Q-094 NC
```

- [ ] **단계 2: 실패를 확인한다**

```
C:/Users/Juhwankim/miniconda3/python.exe -m pytest tests/test_periodic_chains.py -v
```

기대: `ModuleNotFoundError: No module named 'build_periodic_chains'`

- [ ] **단계 3: 스크립트를 구현한다**

`scripts/build_periodic_chains.py`:

```python
"""periodic 정정 체인. LLM 호출 없음. 원문도 안 읽는다 — 대장 하나로 나온다.

    python scripts/build_periodic_chains.py
    python scripts/build_periodic_chains.py --check     # 재생성 없이 대조만

exchange·major·holding 은 `corpus/corrections.build_chains` 가 원문의 `정정관련 제출일` 과
`※관련공시` 를 읽어 잇는다. periodic 은 그 경로를 안 탄다 — `scripts/build_index.py` 가
그 세 그룹만 돌고, `periodic_sections.jsonl` 은 `is_correction` 불리언만 갖는다. 그래서
정정본이 무엇을 정정한 것인지 런타임이 알 방법이 없었다.

periodic 은 원문을 열 필요가 없다. `report_nm` 이 서식과 기간을 글자로 갖고 있어
(기업 · 서식 · 기간) 이 키가 된다. 실측(2026-08-21): 정정 159건 중 157건이 **유일한**
원공시로 확정되고, 모호 0건, 못 이은 2건은 원공시가 코퍼스에 없는 경우다.

🔴 못 이은 것을 버리지 않고 `reason` 에 적는다. `corpus/references.py` 의 규율과 같다 —
파서 실패와 진짜 부재가 구별돼야 한다.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from argus.config import settings                      # noqa: E402

OUT_NAME = "periodic_chains.jsonl"

#: 정정 태그를 지나 서식과 기간을 읽는다. 태그는 `[기재정정]` 외 형태도 있어 대괄호
#: 내용을 특정하지 않는다.
REPORT_KEY = re.compile(
    r"^(?:\[[^\]]*\])?\s*(사업보고서|반기보고서|분기보고서)\s*\((\d{4})\.(\d{2})\)")


def _key(row: dict) -> tuple | None:
    match = REPORT_KEY.match(row.get("report_nm") or "")
    return (row["corp_name"], *match.groups()) if match else None


def build_chains(manifest: list[dict]) -> list[dict]:
    """정정본마다 한 줄. periodic 이 아닌 것과 정정이 아닌 것은 안 낸다."""
    periodic = [r for r in manifest if r.get("doc_group") == "periodic"]
    buckets: dict[tuple, list[dict]] = defaultdict(list)
    for row in periodic:
        key = _key(row)
        if key:
            buckets[key].append(row)

    out: list[dict] = []
    for row in periodic:
        if not row.get("is_correction"):
            continue
        key = _key(row)
        if key is None:
            out.append({"rcept_no": row["rcept_no"], "root": None, "prev": None,
                        "corp_name": row["corp_name"], "report": "", "period": "",
                        "reason": "report_nm 에서 서식·기간을 못 읽었다"})
            continue
        bucket = sorted(buckets[key], key=lambda r: r["rcept_dt"])
        originals = [r for r in bucket if not r.get("is_correction")]
        earlier = [r for r in bucket
                   if r["rcept_no"] != row["rcept_no"] and r["rcept_dt"] <= row["rcept_dt"]]
        reason = ""
        if len(originals) > 1:
            # 실측 0건. 일어나면 잇지 않는다 — 여럿 중 하나를 고르면 무관한 보고서를
            # 대체된 것으로 표시하게 된다. `corpus/corrections` 의 같은 판단이다.
            reason = f"원공시 후보가 {len(originals)}건이라 확정 못 한다"
        elif not originals:
            reason = "원공시가 코퍼스에 없다"
        out.append({
            "rcept_no": row["rcept_no"],
            "root": originals[0]["rcept_no"] if len(originals) == 1 else None,
            "prev": earlier[-1]["rcept_no"] if earlier else None,
            "corp_name": row["corp_name"],
            "report": key[1],
            "period": f"{key[2]}.{key[3]}",
            "reason": reason})
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true",
                        help="재생성 없이 기존 파일과 대조만 한다")
    args = parser.parse_args()

    path = settings().corpus_root / "manifest.jsonl"
    with path.open(encoding="utf-8") as fh:
        manifest = [json.loads(line) for line in fh if line.strip()]
    rows = build_chains(manifest)
    linked = sum(1 for r in rows if r["root"])
    print(f"periodic 정정 {len(rows)}건 · 원공시 확정 {linked}건 "
          f"({linked / max(1, len(rows)):.1%}) · 못 이음 {len(rows) - linked}건")
    for row in rows:
        if row["reason"]:
            print(f"  [못 이음] {row['rcept_no']} {row['corp_name']} · {row['reason']}")

    out = settings().artifacts_root / OUT_NAME
    body = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
    if args.check:
        same = out.exists() and out.read_text(encoding="utf-8") == body
        print("일치" if same else "🔴 불일치 — 재생성이 필요하다")
        raise SystemExit(0 if same else 1)
    out.write_text(body, encoding="utf-8")
    print(f"완료: {out}")


if __name__ == "__main__":
    main()
```

- [ ] **단계 4: 통과를 확인한다**

```
C:/Users/Juhwankim/miniconda3/python.exe -m pytest tests/test_periodic_chains.py -v
```

기대: 5개 전부 PASS.

- [ ] **단계 5: 산출물을 만들고 눈으로 본다**

```
C:/Users/Juhwankim/miniconda3/python.exe scripts/build_periodic_chains.py
```

기대 출력:

```
periodic 정정 159건 · 원공시 확정 157건 (98.7%) · 못 이음 2건
  [못 이음] 20230522000371 한화솔루션 · 원공시가 코퍼스에 없다
  [못 이음] 20260325001401 한화솔루션 · 원공시가 코퍼스에 없다
```

- [ ] **단계 6: 제출 목록에 넣는다**

`scripts/build_preprocessing_manifest.py` 의 `FILES` 에, `artifacts/nary_records.meta.json`
다음 줄에:

```python
    "artifacts/periodic_chains.jsonl",
```

🔴 그 목록의 주석이 *"이 목록이 제출물의 정의다 — 여기서 빠지면 심사자가 받는 묶음에서도
빠진다"* 라고 적는다. 런타임이 여는 파일이므로 반드시 들어간다.

`tests/test_artifacts_layout.py` 의 기존 산출물 목록에 같은 모양으로 한 줄 더한다.

- [ ] **단계 7: 매니페스트를 다시 서명하고 시험을 돈다**

```
C:/Users/Juhwankim/miniconda3/python.exe scripts/build_preprocessing_manifest.py
C:/Users/Juhwankim/miniconda3/python.exe -m pytest tests/test_periodic_chains.py tests/test_artifacts_layout.py -v
```

- [ ] **단계 8: 커밋**

```bash
git add scripts/build_periodic_chains.py scripts/build_preprocessing_manifest.py deploy/preprocessing_manifest.json artifacts/periodic_chains.jsonl tests/test_periodic_chains.py tests/test_artifacts_layout.py
git commit -m "feat(v10): periodic 정정 체인을 대장 조인으로 만든다 — 157/159"
```

---

## 과제 3 — periodic 근거 블록이 원공시를 밝힌다 (`section_chain_note`)

**파일**
- 수정: `src/argus/ablation.py` (플래그 · 팔 `A64`)
- 수정: `src/argus/agent/tools.py` (`_periodic_chains` · `_section_markdown`)
- 시험: `tests/test_periodic_chains.py` (덧붙임)

**인터페이스**
- 소비: `artifacts/periodic_chains.jsonl` (과제 2) — `rcept_no` · `root`
- 생산: `tools._periodic_chains() -> dict[str, dict]` ·
  `tools.PERIODIC_CHAINS_NAME: str` · `ablation.Arm.section_chain_note: bool`

### 줄의 모양

```
## [사업보고서 2024] NC – II. 사업의 내용 > 4. 매출 및 수주상황
- 출처: 접수번호 20250328002285 (공시일 20250328)
- 정정 대상 원공시: 접수번호 20250318001264
```

🔴 **여기서는 접수번호를 적는다. 과제 1 과 다르다.** 이것은 *다른 문서를 가리키는 포인터*가
아니라 **이 문서 자신이 무엇인가**라는 메타데이터다 — 정정본의 정체가 "무엇을 정정한
것인가" 로만 정의되기 때문이다. exchange 가 `- 정정체인: n/N` 으로 이미 하는 것과 같은
성질이다.

🔴 **다만 근거 재현율 회계에 영향이 있는지 재고 넘어간다.** `error_analysis._blocks` 가
블록 안의 14자리를 적재된 골든으로 읽으므로, 원공시가 골든이면 **본문 없이 적재로 세어진다.**
과제 4 의 게이트 조건 3 이 이것을 잰다. 부풀림이 확인되면 이 팔은 기각하고 §5 의 결정으로
올린다.

- [ ] **단계 1: 실패하는 시험을 쓴다**

`tests/test_periodic_chains.py` 끝에 덧붙인다:

```python
from argus import ablation                             # noqa: E402
from argus.agent import tools                          # noqa: E402

PERIODIC_RECORD = {"doc_id": "periodic_20250328002285", "corp_name": "NC",
                   "doc_subtype": "annual", "base_year": 2024, "base_month": 12,
                   "rcept_dt": "20250328", "path": "II. 사업의 내용",
                   "is_correction": True, "n_chars": 10, "n_tables": 0,
                   "is_stub": False, "text_offset": 0, "text_bytes": 0}


def test_arm_off_adds_no_line(monkeypatch):
    monkeypatch.setattr(tools, "_section_body", lambda r: "본문")
    with ablation.using("A56"):
        out = tools._section_markdown(PERIODIC_RECORD)
    assert "원공시" not in out


def test_arm_on_names_the_original_receipt(monkeypatch):
    monkeypatch.setattr(tools, "_section_body", lambda r: "본문")
    with ablation.using(ablation.A64):
        out = tools._section_markdown(PERIODIC_RECORD)
    assert "- 정정 대상 원공시: 접수번호 20250318001264" in out
    assert out.index("- 출처:") < out.index("- 정정 대상 원공시:")


def test_unlinked_correction_gets_no_line(monkeypatch):
    """root 가 None 이면 아무것도 안 적는다 — 없는 출처를 지목하지 않는다."""
    monkeypatch.setattr(tools, "_section_body", lambda r: "본문")
    record = {**PERIODIC_RECORD, "doc_id": "periodic_20230522000371",
              "corp_name": "한화솔루션"}
    with ablation.using(ablation.A64):
        out = tools._section_markdown(record)
    assert "원공시" not in out


def test_non_correction_section_gets_no_line(monkeypatch):
    monkeypatch.setattr(tools, "_section_body", lambda r: "본문")
    record = {**PERIODIC_RECORD, "doc_id": "periodic_20250318001264",
              "is_correction": False}
    with ablation.using(ablation.A64):
        out = tools._section_markdown(record)
    assert "원공시" not in out
```

- [ ] **단계 2: 실패를 확인한다**

```
C:/Users/Juhwankim/miniconda3/python.exe -m pytest tests/test_periodic_chains.py -v -k arm
```

기대: `AttributeError: module 'argus.ablation' has no attribute 'A64'`

- [ ] **단계 3: 플래그와 팔을 세운다**

`src/argus/ablation.py`, `stale_note` 선언 아래:

```python
    #: periodic 정정본의 근거 블록에 원공시 접수번호를 적는다. exchange 는 `- 정정체인:`
    #: 으로 이미 하고 periodic 은 그 링크를 만드는 코드가 없었다 —
    #: `scripts/build_periodic_chains.py` 가 대장 조인으로 만든다(157/159).
    #: `A58`(`cite_chain_pair`)의 사정거리가 짧았던 이유가 이것이다.
    section_chain_note: bool = False
```

`A63` 정의 아래:

```python
# --- W-B : periodic 근거가 원공시를 밝힌다 -----------------------------------
#: `A56` 위에 `section_chain_note` 하나. 계획서의 과제 3. `A63` 위가 아니다.
#:
#: §5.3 이 센 어긋남 다섯 중 넷(`Q-054`·`Q-068`·`Q-082`·`Q-094`)이 periodic 이고,
#: `A58` 이 못 닿은 이유가 그 그룹에 정정 링크가 아예 없었기 때문이다 —
#: `_cite_chain_pair` docstring 이 *"원공시가 근거에 아예 없다"* 로 적어 둔 자리다.
#:
#: 🔴 이 팔은 접수번호를 블록에 적는다. `error_analysis._blocks` 가 그것을 적재된 골든으로
#: 읽으므로 근거 재현율이 본문 없이 오를 수 있다. 게이트 조건 3 이 그것을 잰다.
A64 = replace(A56, name="A64", section_chain_note=True)
```

- [ ] **단계 4: 병합을 구현한다**

`src/argus/agent/tools.py`, `_section_markdown` 위에:

```python
PERIODIC_CHAINS_NAME = "periodic_chains.jsonl"


@lru_cache(maxsize=1)
def _periodic_chains() -> dict[str, dict]:
    """접수번호 -> 체인 행. 파일이 없으면 빈 매핑이고 팔은 조용한 무동작이 된다.
    `_nary_extract` 와 같은 규율이다."""
    path = settings().artifacts_root / PERIODIC_CHAINS_NAME
    if not path.exists():
        return {}
    out: dict[str, dict] = {}
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                row = json.loads(line)
                out[str(row["rcept_no"])] = row
    return out
```

같은 파일, `_section_markdown` 을 갈아 끼운다:

```python
def _section_markdown(record: dict) -> str:
    """Evidence block. The citation format is the one the convention requires:
    report and period, receipt number, section path.

    정정본에는 원공시 접수번호를 한 줄 더 적는다(`section_chain_note`). 이것은 다른
    문서를 가리키는 포인터가 아니라 이 문서 자신의 메타데이터다 — 정정본의 정체가
    "무엇을 정정한 것인가" 로만 정의된다. exchange 의 `- 정정체인: n/N` 과 같은 성질이고,
    periodic 에는 그 링크를 만드는 코드가 없어서 빠져 있었다.
    """
    from argus import ablation       # 함수 안에서 — 이 모듈의 다른 곳과 같은 이유(순환)
    rcept_no = str(record["doc_id"]).split("_")[-1]
    label = _period_label(record)
    part = ""
    if record.get("n_parts", 1) > 1:
        part = f" [{record['part'] + 1}/{record['n_parts']} 조각]"
    body = _section_body(record)
    meta = f"- 출처: 접수번호 {rcept_no} (공시일 {record.get('rcept_dt', '')})"
    if ablation.enabled("section_chain_note") and record.get("is_correction"):
        root = (_periodic_chains().get(rcept_no) or {}).get("root")
        if root:
            meta += f"\n- 정정 대상 원공시: 접수번호 {root}"
    return (f"## [{label}] "
            f"{record.get('corp_name', '')} – {record.get('path', '')}{part}\n"
            f"{meta}\n\n{body}")
```

- [ ] **단계 5: 통과를 확인한다**

```
C:/Users/Juhwankim/miniconda3/python.exe -m pytest tests/test_periodic_chains.py tests/test_pipeline.py tests/test_cite_chain_pair.py tests/test_period_label.py -v
```

- [ ] **단계 6: 커밋**

```bash
git add src/argus/ablation.py src/argus/agent/tools.py tests/test_periodic_chains.py
git commit -m "feat(v10): periodic 근거 블록이 원공시를 밝힌다 — 팔 A64"
```

---

## 과제 4 — 잰다. 채택은 측정이 정한다

**파일**
- 수정: `src/argus/ablation.py` (팔 주석에 측정값 · 조건부 `A65`)
- 수정: `docs/open-issues-v2.md` (§4.4 · §5.3 · §7 기준선)
- 신설: `scratch/audit/gate_A63.json` · `scratch/audit/gate_A64.json`

### 게이트 조건 — 넷을 다 넘어야 채택한다

| | 조건 | `A63` (W-A) | `A64` (W-B) |
|---|---|---|---|
| 1 | 겨냥한 결함이 닫힌다 | `Q-116` 답변이 581,790,000,000 을 3회 모두 안 쓴다 | `Q-054`·`Q-068`·`Q-082`·`Q-094` 의 원공시 접수번호가 답변에 나온다 |
| 2 | **퇴행 0** | 112문항에서 새로 깨지는 것 0 | 같음 |
| 3 | **자기 성적을 부풀리지 않는다** | 근거 완전성 167/168 불변 | 근거 완전성 불변 — 오르면 🔴 부풀림이고 기각 |
| 4 | 어긋남이 준다 | — | `keypoint_literal_report` 어긋남 22 → 18 이하 |

- [ ] **단계 1: 영향 집합을 먼저 계산한다 (LLM 0회)**

🔴 **첫 판은 `scripts/ablation.py --arm A63 --repeat 3` 이라고 적었다. 그 플래그는 없다.**
그리고 112문항 전수를 다시 사는 것은 `A62` 캠페인에서 이미 대체한 판이다 —
`scratch/a62_affected.py` 가 112문항 중 **90문항의 답변이 글자 그대로 같음을 증명**했고
22문항만 돌렸다. 다시 사면 같은 답을 다시 사면서 뽑기(근거가 같은 문항 104개 중 56개가
문면이 다르다)를 얹는다.

```
C:/Users/Juhwankim/miniconda3/python.exe scratch/a63_affected.py
```

- [ ] **단계 2: 영향받는 문항만 3회 돌린다**

```
GATE_ARM=A63 GATE_QS=<단계 1 이 출력한 목록>   C:/Users/Juhwankim/miniconda3/python.exe scratch/gate_a59.py --rounds 3
C:/Users/Juhwankim/miniconda3/python.exe scratch/compose_a62.py    # 112문항 합성
```

🔴 근거 문면이 바뀌므로 **요청 캐시가 깨진다** — 영향받는 문항에 대해서만 유료다.

- [ ] **단계 3: 게이트 조건 넷을 하나씩 확인해 적는다**

🔴 **첫 판의 두 명령이 다 인자 오류로 죽는다.** `build_failures.py` 는 `--run` 이
`required=True` 인데 빠져 있었고, `keypoint_literal_report.py` 는 `--arm` 이 아니라
`--source {local,server}` 를 받는다(경로가 `v7/%s_%s_r1` 로 박혀 있다).

```
C:/Users/Juhwankim/miniconda3/python.exe scripts/build_failures.py   --arm A63 --run gate_A63_r1 --out scratch/audit/gate_A63.json
C:/Users/Juhwankim/miniconda3/python.exe scratch/axis_a62.py          # 8축 · 부풀림 확인
```

어긋남(조건 4)은 `keypoint_literal_report.py` 가 읽는 경로 규약(`v7/<팔>_<벌>_r1`)으로
산출물을 낸 뒤에 `--source local` 로 돌린다. 규약을 안 맞추면 그 스크립트가 `A63` 을
못 읽는다.

조건 3 을 특히 본다 — 근거 완전성이 167/168 에서 **올랐으면 부풀림이다.** 각인 줄에
14자리가 없으므로 오를 이유가 없고, 오르면 구현이 틀린 것이다.

- [ ] **단계 3: 결과를 팔 주석에 적는다. 기각도 적는다**

`A63` 주석 끝에 `A57`~`A61` 과 같은 모양으로 (`NNN` 은 실측으로 채운다):

```python
#: 측정 — `A63` 로컬 3회 **NNN · NNN · NNN**. Q-116 폐기값 3/3 -> N/3 · 퇴행 N ·
#: 근거 완전성 167/168 불변
```

- [ ] **단계 4: `A64` 에 같은 것을 한다**

```
C:/Users/Juhwankim/miniconda3/python.exe scripts/ablation.py --arm A64 --repeat 3
C:/Users/Juhwankim/miniconda3/python.exe scripts/build_failures.py --arm A64 --out scratch/audit/gate_A64.json
C:/Users/Juhwankim/miniconda3/python.exe scratch/keypoint_literal_report.py --arm A64
```

- [ ] **단계 5: 둘 다 넘으면 합친 팔을 세운다**

```python
#: `A62` 위에 `stale_note` · `section_chain_note` 를 얹은 팔. 계획서의 W-A + W-B.
A65 = replace(A62, name="A65", stale_note=True, section_chain_note=True)
```

🔴 **하나라도 게이트를 못 넘으면 `A65` 를 세우지 않는다.** 못 넘은 것은 기각으로 남긴다 —
`latest_pin` 을 남겨 둔 것과 같은 이유다.

- [ ] **단계 6: 오픈 이슈를 갱신한다**

`docs/open-issues-v2.md` §4.4 와 §5.3 에 결과를 쓴다. 닫혔으면 닫혔다고, 안 닫혔으면
무엇이 남았는지 쓴다. §7 의 기준선 22 를 새 값으로 갈지 여부도 적는다.

- [ ] **단계 7: 커밋**

```bash
git add src/argus/ablation.py docs/open-issues-v2.md scratch/audit/gate_A63.json scratch/audit/gate_A64.json
git commit -m "docs: A63 A64 를 3회 측정한다 — 게이트 결과"
```

---

## 5. 🔴 결정이 필요한 자리

### 5.1 최신본 접수번호를 적을 것인가

과제 1 은 최신본 접수번호를 **안 적는** 판으로 세워져 있다. 그러면 `Q-116` 이 얻는 것은
"폐기값을 말하지 않는다" 이고, 골든이 요구하는 `20250923800193` 인용은 얻지 못한다.

| | 적지 않는다 (계획의 기본값) | 적는다 |
|---|---|---|
| `Q-116` 답변 | 폐기값 581,790,000,000 을 안 쓴다 | 그 위에 최신본을 인용한다 |
| 근거 표시 축 | 지금 2/3 그대로 | 3/3 이 될 수 있다 |
| 🔴 우리 재현율 | 불변 | **본문 없이 오른다** — §7 이 하지 말라고 한 것과 같은 종류 |
| 전례 | `roster_items` 가 `loaded` 로 제한한 근거와 같다 | 없다 |

**기본값(적지 않는다)으로 먼저 재고, 게이트 조건 1 이 부족할 때 다시 묻는다.**

### 5.2 각인한 최신 금액이 거짓 귀속을 만들 수 있다

각인 줄에 **최신 금액**을 적으면 `cite_used` 의 유일성 규칙이 그 수를 *대체본 블록에만
유일한 수*로 읽어 **대체본을 그 금액의 출처로 인용할 수 있다.** 대체본에는 그 수가 없으므로
거짓 귀속이다.

과제 1 을 실행할 때 `ablation._cite_missing_used` 의 숫자 추출이 `- 정정체인:` 으로
시작하는 줄을 건너뛰게 해야 한다. `src/argus/agent/aggregate.py:1204` 가 `- 출처:` 메타
줄을 본문이 아니라고 배제한 전례가 있다. **과제 1 단계 4 를 구현할 때 이 배제를 함께
넣고, 시험을 하나 더 쓴다** — "각인 줄의 금액으로는 대체본이 인용되지 않는다".

---

## 6. 이 계획이 안 하는 것 — 근거를 붙여 적어 둔다

### 6.1 `Q-110` 근거 완전성 167/168 — 선택 문제다, 전처리 밖

레코드는 멀쩡하다. `20240329800342` 는 `counterparty: 한국수력원자력㈜` ·
`amount: 73,800,000,000` · `schema_matched: true` 다. 한수원 계약 35건을 파트너별로 금액
내림차순으로 세워 보면 골든의 순위가 이렇다:

```
대우건설       1건 중 1위    ← 들어온다
현대건설       1건 중 1위    ← 들어온다
두산에너빌리티   5건 중 2위
두산퓨얼셀      6건 중 2위    (1위 74,100,000,000 과 3억 차이다)
우리기술       7건 중 7위
한전기술      15건 중 15위
```

`MAX_COUNTERPARTY_EVIDENCE = 6` 에 파트너가 6곳이라 **몫이 1건씩**이다. 금액 내림차순으로
1위만 담으므로 여섯 중 넷이 골든이 아니다. 35건이 전부 산출물에 정확히 있으므로 **어떤
전처리도 이것을 안 바꾼다.** 몫·정렬·상한을 건드리는 회수 과제이고 프리즈 전에 댈 것이
아니다 — `A60` 이 답변과 표기는 이미 닫았고 남은 것은 근거 완전성 1건이다.

🔴 그리고 **35건을 열거하는 로스터 블록을 근거에 넣으면 안 된다.**
`src/argus/agent/pipeline.py:862-874` 가 `roster_items` 를 `loaded` 로 제한한 이유가 정확히
이것이고, 그 주석이 `Q-110` 의 두산퓨얼셀을 실례로 든다.

### 6.2 §5.2 "질문이 물었는데 답변이 안 적었다" 7건 — 생성이다

`Q-012` 를 열어 확인했다. 요점1·3 이 요구하는 네 수가 `artifacts/holding_records.jsonl` 의
`markdown` 에 **글자 그대로 다 있다**:

```
- 보유주식등의 수: 직전 196,452,737주 → 이번 196,488,145주
- 주요계약체결 주식등의 수: 직전 84,634,380주 → 이번 84,282,066주
```

데이터가 아니라 생성이다. 전처리로 안 닫힌다.

### 6.3 ROWSPAN 미처리 — 근거는 크고 인과는 미측정. 프리즈 후

`src/argus/corpus/sections.py:113` 의 `_row_cells` 는 COLSPAN 만 펼치고 **ROWSPAN 은 안
다룬다.** 그 함수 docstring 이 COLSPAN 을 펼치는 이유로 적은 *"매출 수치가 품목 칸 아래로
밀린다. 재무 수치가 전면적으로 틀리므로 선택 사항이 아니다"* — 그 논거가 ROWSPAN 에는
적용돼 있지 않다.

쟀다 (`scratch/rowspan_only.py` · 문서 16건, 레이아웃·중첩표 제외):

```
표 3,556개
  ROWSPAN 있는 표                      1,415 (39.8%)
  ROWSPAN 만 펼쳐도 렌더가 달라지는 표    1,415 (39.8%)   ← 있으면 전부 틀리게 나온다
    그중 5자리 이상 수가 든 표           1,128 (31.7%)

전량(`artifacts/periodic_sections.txt` 926MB)
  표 583,219 중 밀림 흔적 186,546 (32.0%)
  데이터 행 5,973,215 중 1,386,896 (23.2%)
```

실례 — NC 2023 1분기 매출실적(`ROWSPAN=6` 이 첫 열에, `ROWSPAN=2` 가 헤더에):

```
지금        | 리니지2M | 73,059 | 15% |  |     ← 73,059 가 '상품명' 칸에 앉는다
헤더 부행   | 매출액 | 비율 |  |  |            ← 두 칸 밀렸다
```

🔴 **그런데 이것이 열려 있는 어느 오픈 이슈를 닫는지는 확인 못 했다.** `Q-080` 을 열어
보니 데이터 행은 안 밀리고 헤더만 밀리며 요구된 `80,318` 은 문면에 멀쩡히 있다 — 답변이
안 적은 것이다. **넓은 위생 결함인 것은 측정됐고 인과는 미측정이다.**

**프리즈 전에 안 댄다.** 세 가지 이유다:

1. `periodic_sections.txt`(926MB)와 `periodic_sections.jsonl` 전량 재빌드다. 오프셋이 전부
   갈리고 `section_index.npz` 임베딩을 다시 계산해야 하며 `preprocessing_manifest` 를 다시
   서명해야 한다.
2. `nary` 이음매에 안 얹힌다 — 런타임 덧붙이기가 아니라 블롭 자체가 바뀐다. 과거 측정과의
   비교 가능성이 끊긴다.
3. 인과가 미측정이라 게이트 조건 1 을 세울 수가 없다.

**프리즈 후 과제로 넘긴다.** 그때 할 것: `_row_cells` 에 ROWSPAN 이어받기를 넣고, 별도
`--out-dir` 로 블롭을 만들어 **같은 문항 코호트에서 옛 블롭과 새 블롭을 A/B** 로 잰다.
`scratch/rowspan_probe.py` 와 `scratch/rowspan_only.py` 가 그 측정의 출발점이다 —
전자는 ROWSPAN+COLSPAN 을 모두 펼친 판, 후자는 ROWSPAN 만 펼쳐 델타를 가른 판이다.

### 6.4 `Q-113` 자기 등식 (2/3) — 생성이다, 전처리 밖

`A62` 축 실측에서 추론 논리성에 남는 둘 중 하나다(다른 하나는 `Q-065` 생성 흔들림).

`Q-113` 은 답변이 스스로 쓴 등식이 성립하지 않는 결함이고, `A61`(`sum_operand_fix`)이
`Q-115`·`Q-107` 을 고친 그 기계가 **이 문항엔 안 닿는다.** 발화 조건이 「근거에 결정적
집계 명세가 있다」인데 `Q-113` 의 결정적 줄은 `합` 연산이 아니라 명세를 안 받는다.

전처리로도 안 닫힌다 — 레코드에 없는 수를 답변이 만든 것이므로 데이터 표현 층의 문제가
아니다. 명세를 받는 연산을 넓히는 것은 `aggregate._op` 의 라우팅을 바꾸는 일이고, 그것은
결정적 줄이 실리는 문항 집합을 바꾸므로 프리즈 나흘 앞두고 댈 것이 아니다.

**프리즈 후 과제로 남긴다.**

---

## 7. 자기 검토

**진단 대응** — `open-issues-v2.md` §8 의 여섯 항목: **1·3·6** 은 §0 이 `A57`~`A61` 로
닫힌 것을 확인했고, 5(`Q-116`)는 과제 1, 2(§5.3)는 과제 2·3 이다. §5.2 나머지 ·
`Q-110` 근거 완전성 · **4(`Q-113`)** 는 §6 이 근거를 대고 범위 밖으로 뺐다. **빈 항목 없다.**

🔴 **첫 판이 4(`Q-113`)를 닫힌 것으로 셌다.** 축 실측이 그것을 반박한다 — §0 의 정정과
§6.4 를 참고. 닫힌 것은 셋이고 범위 밖이 셋이다.

**미완 표현 점검** — `NNN` 이 남은 자리는 과제 4 단계 3 하나이고, 그건 아직 측정하지 않은
수를 적는 자리이므로 의도적이다. 그 밖에 TBD·TODO·"적절히 처리한다" 없다.

**이름 일관성** — `_stale_line` · `_group_records_stale` · `_periodic_chains` ·
`PERIODIC_CHAINS_NAME` · `stale_note` · `section_chain_note` · `A63` · `A64` · `A65` ·
`OUT_NAME` · `REPORT_KEY` · `build_chains(manifest)` 가 시험과 구현에서 같은 이름으로
쓰였다. `A63`~`A65` 는 미사용 번호임을 확인했다 (현재 최대 `A62`).

**실행 순서** — 과제 2 는 과제 3 의 선행이다(산출물이 있어야 한다). 과제 1 은 독립이므로
과제 2 와 병행할 수 있다. 과제 4 는 1·3 이 끝난 뒤다. §5.2 의 배제 구현은 과제 1 안에서
같이 한다.
