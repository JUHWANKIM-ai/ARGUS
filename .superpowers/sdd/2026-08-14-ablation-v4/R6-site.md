# `R6` — 결과 공개 사이트 (로컬 정적 빌드) · 실행 보고

**일시** 2026-08-15 · **크레딧 0원 · LLM 0회 · `.env` 미열람 · `heldout` 미열람 · `src/argus/` 미열람**
**산출** `scripts/build_site.py`(2,134행 · 신설) · `tests/test_site.py`(506행 · 신설 **59 테스트**) ·
`site/`(13파일 · 1.86MB · **생성물이라 `.gitignore`**) · `.gitignore` 3행

```
빌드      PYTHONIOENCODING=utf-8 python scripts/build_site.py
게이트    PYTHONIOENCODING=utf-8 python scripts/build_site.py --prove-gates
전량      1,055 passed / 0 failed   (착수 시점 기준선 990 + 신설 59 + 다른 에이전트 6)
```

> 🔴 **이 보고서에도 성적·지출 수치를 옮겨 적지 않는다.** 값은 `site/` 를 다시 빌드하면
> 나온다. 아래에 수가 나오는 자리는 **파서·게이트가 낸 결과의 형태**를 보이기 위한 것이고,
> 정본은 `docs/total_plan.md §1` 과 `artifacts/ablation/{gen}/**` 이다.

---

## 1. 만든 것 — 정적 HTML 5쪽

| 쪽 | 무엇이 실렸나 | 값의 출처 |
|---|---|---|
| `index.html` | 헤드라인(그리드·전체·회귀·정보한계·밸류체인 **실질 병기**) · 능력 카드 4 · 정본 링크표 · 🔴 **라이브 데모 자리** | ② 센다 + ① 파싱 |
| `ablation.html` | 사다리(**세대 라벨** · Δ · 막대) · LOO(**같은 세대 기준선**) · **진단·기각 arm**("성적이 아니다") · 밸류체인(채점/실질/부풀림 문항/골든 회수) · 축별 히트맵 · 지출 원장 12행 · arm 판정표 | ② + ① + ③ |
| `errors.html` | **분모 다섯을 라벨과 함께**(그리드·전체·회귀·정보한계·밸류체인) · 단계 합 ↔ 오답 수 대조 · 근본원인 · 문서군 · 잔여 오답 ID · 데이터 상한(§4) · 🔴 **정적 필터 1,632행** | ② + ① |
| `pipeline.html` | Phase 표(문서 링크 · 상태 · 게이트 · 크레딧) · 게이트 총계 · 테스트 수 · 크레딧 열 ↔ 총계 차액 | ① 파싱 |
| `data.html` | 코퍼스 규모 · 색인 산출물 대장 · Recall@5(**코호트 라벨 필수**) · 그래프 밀도·기재율 · API 계약(**IP 없음**) | ② + ③ |

부속: `style.css`(레퍼런스 토큰·시맨틱 클래스만) · `filter.js`(순수 JS · **네트워크 0**) ·
`site/README.md` · `site/data/site_{meta,ablation,errors,pipeline,data}.json`.

**정적 필터**는 사용자 확정대로 `(a)` 만 만들었다 — 세대 · 코호트 · arm · 분모(track) ·
`task_type` · 난이도 · 문서군 · 실패 단계 · **정답/오답** 아홉 축. 데이터는 빌드 타임에
쪽 안에 박힌다(`file://` 에서 `fetch` 가 막히므로). 🔴 **라이브 질의응답은 없다.**

---

## 2. 🔴 낡지 않게 만드는 것 — 방식 셋을 그대로 구현했다

### ① 정본을 파싱한다 — 그리고 **문서가 스스로와 맞는지 검산한다**

`parse_ssot()` 는 `docs/total_plan.md §1` 에서 Phase 표 · 게이트 총계 · 테스트 수 ·
지출 원장 12행을 읽고 **검산 넷**을 돌린다. 하나라도 깨지면 `SsotMismatch` 로 **빌드가 죽는다.**

```python
    if abs(row_sum - total_won) > 0.05:
        raise SsotMismatch(
            f"🔴 정본 검산 실패 — 지출 원장 행 합 {row_sum:,.1f}원 ≠ `누적` {total_won:,.1f}원 "
            f"({SSOT} §1). 🔴 **고칠 것은 이 스크립트가 아니라 정본이다.** "
            "행을 넣었으면 `누적` 행과 검산식을 함께 고쳐야 한다."
        )
```

| 검산 | 무엇을 대조하나 | 어떤 재발을 잡나 |
|---|---|---|
| ① | 원장 **행 합** == `누적` 행 | R2E 가 행만 넣고 `누적`·검산식을 안 고쳐 하루를 낡은 채로 산 사고 |
| ② | Phase 표 **게이트 분자·분모 합** == `게이트 총계 x/y` | 검토가 찾은 `68/71` 결함(게이트 E 가 `3/3` 이 되며 총계가 틀어졌다) |
| ③ | 테스트 **증가 이력 꼬리** == 헤드라인 `N passed` | 🔴 **열 번째 결함이 한 문장 안에서 난 그 자리** |
| ④ | Phase **크레딧 열 실측 합** == 문서가 적어 둔 합, `+ 배분 밖 차액` == `누적` | 크레딧 열을 총계로 오인하는 인용 |

**실측**(2026-08-15 빌드): 검산 넷 전부 통과. 원장 **12행**, 게이트 총계와 Phase 합 일치,
테스트 꼬리와 헤드라인 일치, 크레딧 열 + 배분 밖 = `누적` 일치. **값은 `site/` 를 보라.**

같은 방식으로 `parse_data_limits()`(§4 · 🔴 `⚠️ 출처 미확인` 표시를 지우지 않고 취소선 값은
버린다) · `parse_arm_verdicts()`(판정 **라벨**만) · `welcome_credit_won()`(규정값이라 표가 없어
**정본 문장이 살아 있는지 확인**하고 상수를 쓴다) · `inflated_question_ids()`
(`scripts/error_analysis.py` 의 상수를 **import 없이 `ast` 로** 읽는다).

### ② 산출물에서 센다

`resolve_generation()` · `resolve_ladder_generation()` · `load_rows()` · `inflation()` ·
`vc_golden()`. 성적 · 분모 · 단계 분포 · 잔여 오답 ID · 사다리 Δ · LOO Δ · 밸류체인
골든 회수 · Recall@5 · 코퍼스 규모 · 엣지 밀도는 **전부 셈의 결과**다.

🔴 **밸류체인 실질값을 셈으로 만들었다.** *"`Q-110` 이 10× 재진술 오류로 통과"* 라는 주의를
문장으로 베끼지 않고, `JUDGED_CORRECT_ARITHMETIC_WRONG` ∩ *(그 세대에서 정답으로 채점된 행)*
으로 **센다** — 옛 세대 문항(`Q-034`)은 현 세대 행에 없어 **세대가 자동으로 갈린다.**
채점 결함이 고쳐지면 상수가 비고, 그러면 화면의 "실질" 은 채점값과 같아진다. 사본이 아니다.

🔴 **Δ 는 반올림된 비율끼리 빼지 않는다** — `_delta()` 가 `correct/n` 에서 직접 계산한다
(반올림 뺄셈은 `L1` 을 `-9.8%p` 로 만든다. 정본은 `-9.7%p` 다).

### ③ 링크만 한다

`sources_for()` — arm 판정 대장(`09-ablation.md §I-5f ⑧`) · 엔드포인트(`deploy/README.md`) ·
주최측 규격(`organizer-qa.md`) · 도구 명세(`06-agent-tool.md`) · 게이트 I 렌더(**해석된 세대**로
경로를 채운다) · 성적·지출 정본(`total_plan.md §1`). 각 쪽 하단과 `index` 의 링크표에 실린다.

🔴 **`pipeline` 쪽의 "Phase 별 서술" 은 일부러 안 실었다.** `total_plan §1` 의
*"Phase 별 한 줄 요약"* 표에 **낡은 값이 넷** 있다(아래 §7). 옮겨 적으면 그 자리가
사이트에서 다섯 번째로 낡는다 — **문서 열의 링크가 그 자리를 대신한다.**

### 하드코딩 0 을 **테스트로 강제한다**

`test_source_holds_no_currency_or_score_literals` 가 `build_site.py` 를 `ast` 로 열어
독스트링을 뺀 리터럴을 훑는다 — 1,000 이상의 수(예외 셋: 웰컴 크레딧 규정값 · 연도 경계 ·
KB 환산) · `…원` 금액(0원 제외) · `분자/분모` 꼴 · `+N.N%p` 꼴을 **전부 금지**한다.
`test_source_never_hardcodes_a_generation_path` 는 `v2/`·`v3/` 같은 세대 경로 리터럴을 막는다.

---

## 3. 🔴 보안 게이트 다섯 — 어떻게 강제되나 (코드)

게이트는 **파일을 쓰기 전에** 전량(HTML·JSON·CSS·JS·README)을 훑는다. 하나라도 걸리면
`SiteLeak` 이고 **아무 파일도 쓰이지 않는다.**

```python
def build_site(out: Path = SITE, *, write: bool = True) -> dict[str, str]:
    payloads = build_payloads()
    files = render_all(payloads)
    gate_all(files)  # 🔴 쓰기 전에 훑는다. 걸리면 아무 파일도 쓰이지 않는다.
```

### ① `.env` · API 키 — **경로 자체를 없앴다**

```python
FORBIDDEN_PATHS: tuple[str, ...] = ("heldout", "data/", ".env", "src/", "secrets", "attic")

def _guard_path(rel: str) -> Path:
    ...
    for token in FORBIDDEN_PATHS:
        if token in low:
            raise ForbiddenRead(f"🔴 보안: 이 경로는 사이트 빌드가 읽지 않는다 — …")
```

🔴 **검토안보다 한 칸 더 잠갔다.** 검토안은 *"`argus` 를 import 하지 않는다"* 였는데,
여기서는 **`src/` 를 읽는 것 자체를 금지**했다(`argus.config.settings()` 가 `.env` 를 아는
유일한 통로다). 그 대가로 `count_tool_specs()`(= `tools.py` 를 `ast` 로 세기)를 **버리고**
도구 종수는 **③ 링크**로 돌렸다 — 마침 `R1-12b`(`392af47`)가 그 종수의 정본을 세웠으므로
사이트는 그 문서를 가리키기만 하면 낡지 않는다.
산출물 쪽은 `SECRET_PATTERNS` 여섯(키 접두사 2 · `Bearer` · NCP 헤더 · 키 이름 ·
40자 이상 영숫자 덩어리)으로 훑는다. `tests/test_site.py` 가 `ast` 로 **`import argus` 부재**를
단언한다.

### ② 코퍼스 원문 5.55GB

`FORBIDDEN_PATHS` 의 `"data/"` 로 읽기를 막고, 산출물에서 `CORPUS_TOKENS`
(`dart-disclosures` · 정기공시 본문 덩어리 파일명 · `universe.csv`)를 훑는다.
🔴 **색인 대장에서도 뺐다** — `build_data()` 가 `index_snapshot` 중 `artifacts/` 로 시작하는
것만 싣고 정기공시 본문 덩어리는 이름조차 빼며, 근거 발췌(`retrieved_context`)는 **한 자도
싣지 않았다**(원래 계획의 1,200자 발췌도 만들지 않았다).

### ③ heldout — 집계도, **이름도** 싣지 않는다

경로(`heldout` 부분 문자열 → `heldout_v2`·`v3/`·`v4/` 도 함께)와 산출물 문자열
(`heldout`·`held-out`·`held_out`)을 둘 다 막는다. 🔴 **문제가 하나 있었다** — 지출 원장 8번
행의 **항목명**이 그 낱말을 쓴다. 행을 빼면 원장 합이 `누적` 과 어긋나 검산 ①이 깨진다.
그래서 **금액은 남기고 이름만 지운다**:

```python
RESERVED_TRACK_LABEL = "[비공개 평가 트랙]"
_RESERVED_WORD = re.compile(r"held[-_ ]?out", re.IGNORECASE)

def _clean(cell: str) -> str:
    ...
    return redact_reserved(" ".join(text.split()).strip())
```

정본에서 읽은 **모든 문장**이 `_clean()` 을 지나므로 한 자리에서 막힌다.

### ④ 평가셋 질문 전문·정답 — 게이트가 **원본을 읽어 대조한다**

```python
EVALSET_FILES = ("evalset/questions.jsonl", "evalset/regression.jsonl",
                 "evalset/valuechain.jsonl")
PROBE_PREFIX = 20
...
                if len(value) >= PROBE_PREFIX:
                    probes.append((f"{qid}.{field}[:{PROBE_PREFIX}]", value[:PROBE_PREFIX]))
                    probes.append((f"{qid}.{field} 전문", value))
```

질문 **전문**과 **앞 20자**, 그리고 20자 이상인 **정답 문장**까지 프로브로 만들어 전 산출물과
대조한다(현 빌드 기준 프로브 수백 개). 🔴 **`heldout` 은 여기서도 열지 않는다** — 목록에
없고, 넣더라도 `read_text()` 가 경로째 거부한다(테스트로 고정).

### ⑤ 행 단위 민감 필드 — 화이트리스트 + **모르는 필드면 죽는다**

```python
    unknown = sorted(set(row) - ROW_FIELDS_KNOWN)
    if unknown:
        raise SiteLeak(
            "🔴 보안 ⑤: report.json 에 모르는 필드가 붙었다 — "
            f"{unknown}. `ROW_FIELDS_ALLOWED` 또는 `ROW_FIELDS_BLOCKED` 에 "
            "명시하기 전에는 실리지 않는다."
        )
    out = {k: row[k] for k in sorted(ROW_FIELDS_ALLOWED) if k in row}
```

`report.json > rows` 의 실측 필드는 **45**(v2·v3 전 arm 합집합)다. 그중 **29를 허용**하고
**16을 차단**했다 — 요구된 넷(`golden`·`golden_rank`·`answer`·`think_trace`)과
`corp_name`·`sector` 에 더해 **`route_corps`**(라우터가 고른 기업명 = `corp_name` 의 우회로) ·
`golden_in_candidates`·`golden_in_evidence`·`golden_unreachable`(골든 **문서번호**를 들고 있다) ·
`calls`·`judge`(판정 사유 원문) · `data_limit`(문서번호 목록) · `calc_*`(자유 서술)까지 막았다.
🔴 **44필드 화이트리스트보다 좁다.**

덧붙인 방어 셋:

- `_mask_amounts()` — `failure_why` 자유 서술의 4자리 이상 수를 지운다(**연도 1900~2100은 남긴다**).
  🔴 **장식이 아니다**: 마스킹을 끄고 빌드하면 문서번호 14자리가 실려 게이트가 죽는다(실측 §4).
- `DOC_ID` 게이트 — 산출물에 **14자리 접수번호**가 있으면 죽는다. 어떤 경로로도 골든 근거
  식별자가 새지 않는다.
- `sanitize_aggregate()` — 산출물의 **집계** 블록에는 차단 필드와 같은 이름의 스칼라가 있다
  (`by_doc_group.data_limit = 0` · Recall 코호트의 `golden = 60`). 스칼라면 이름을 바꿔
  (`dataLimitCount`·`goldenDenominator`) 통과시키고, **내용이면 `SiteLeak`** 이다.

### + 공인 IP — 테스트에도 IP 를 적지 않는다

```python
def public_ips() -> list[str]:
    """🔴 IP 를 코드에 적지 않는다. 정본(`deploy/README.md`)에서 뽑아 쓴다."""
```

`deploy/README.md` 에서 IPv4 를 뽑아 산출물과 대조한다. 테스트도 같은 함수를 쓰고,
**`public_ips()` 가 비면 테스트가 실패한다**(빈 검사 방지).

---

## 4. 🔴 게이트를 일부러 어겼다 — 실측

`--prove-gates` 는 여섯 게이트에 위반 페이로드를 넣어 **발화를 확인**하고 끝낸다
(같은 프로브가 `tests/test_site.py` 에 파라미터라이즈로 박혀 있다).

```
🔴 게이트 발화 확인 — 일부러 위반 페이로드를 넣는다
  PASS  ① API 키 — 발화했다: 보안 ① [x.json] API 키 형태 nv- — nv-012345678…
  PASS  ② 코퍼스 원문 — 발화했다: 보안 ② [x.json] 코퍼스 원문 참조 — 'dart-disclosures'
  PASS  ③ heldout — 발화했다: 보안 ③ [x.json] heldout 언급 — 'heldout'
  PASS  ④ 평가셋 문항 전문 — 발화했다: 보안 ④ [x.html] 평가셋 문항 전문/정답 — Q-001.question[:20]
  PASS  ⑤ 차단 필드 — 발화했다: 보안 ⑤ [x.json] 차단 필드가 실렸다 — 'golden'
  PASS  + 공인 IP — 발화했다: 보안 + [x.html] 공인 IP 발행 — deploy/README.md 의 IP
결과: 6/6 게이트가 발화했다
```

그리고 **실제 빌드 경로**로 여덟 가지를 어겨 봤다(저장소 파일은 고치지 않고 런타임에서).

| 어긴 것 | 결과 |
|---|---|
| 화이트리스트에 `golden`·`corp_name` 을 넣는다 | `SiteLeak: 🔴 보안 ⑤: 차단 필드가 통과했다 — ['corp_name', 'golden']` |
| 금액 마스킹을 끈다 | `SiteLeak: 보안 ⑤ [errors.html] 문서 식별자 14자리 — 20260203800709` |
| 비공개 트랙 이름 지우기를 끈다 | `SiteLeak: 보안 ③ [ablation.html] heldout 언급` |
| 공인 IP 를 쪽에 싣는다 | `SiteLeak: 보안 + [index.html] 공인 IP 발행` |
| 원장에 행을 넣고 `누적` 을 안 고친다 | `SsotMismatch: 🔴 정본 검산 실패 — 지출 원장 행 합 ≠ 누적` |
| 은닉 트랙 산출물을 읽는다 | `ForbiddenRead: 금지 토큰 'heldout'` |
| 코퍼스 원문을 읽는다 | `ForbiddenRead: 금지 토큰 'data/'` |
| `src/argus/config.py` 를 읽는다 | `ForbiddenRead: 금지 토큰 'src/'` |
| (대조) 정상 빌드 | **PASS** |

🔴 **두 번째 줄이 이 라운드의 실측 발견이다** — `failure_why` 자유 서술이 골든 문서번호를
되풀어 쓴다. 마스킹이 없으면 그대로 발행됐을 것이고, `DOC_ID` 게이트가 그것을 잡았다.

---

## 5. 세대 라벨을 어떻게 붙였나

```
성적 정본 세대   resolve_generation("full")   — v* 폴더 중 summary.json 이 있는 최신
사다리 세대      resolve_ladder_generation()  — 🔴 `A0` 과 `A8` 이 **둘 다** 있는 최신 세대
밸류체인 세대    resolve_generation("vc")
```

- 실측 해석: 성적·밸류체인은 **현 세대**, 사다리는 **한 세대 아래**(`A0` 이 그 세대에만 있다).
- 두 세대가 갈리면 `mixedGenerationWarning` 이 생기고 `index` 상단에 **경고 카드**가 뜬다.
- 표마다 `gen_badge()` 가 **행 단위로** 세대 pill 을 찍는다. 사다리·LOO·진단·밸류체인·
  히트맵·색인 대장 전부.
- **LOO 는 같은 세대의 `A8` 만 기준선으로 쓴다**(`test_loo_baseline_is_the_same_generation`).
- 다섯 쪽 **푸터 전부**에 `성적 세대 · 사다리 세대 · 밸류체인 세대 · 기준선 arm · 빌드 시각 ·
  `argusCommit` · LLM 0회 · 크레딧 0원` 이 박힌다.
- 🔴 **`v4` 분기는 graceful 하다.** `generations()` 는 `artifacts/ablation/v\d+` 를 실제로
  훑어 번호 역순으로 돌려주고, `summary.json` 이 없는 세대는 조용히 빠진다.
  `v4/summary.json` 이 생기는 순간 성적 세대가 v4 가 되고, **v4 가 사다리를 재측정하지 않으면
  사다리는 그대로 아래 세대로 렌더된다**(그것이 옳고, 라벨이 그 사실을 말한다).
  없는 세대를 가정한 테스트(`…_graceful_when_a_newer_folder_has_no_summary`)로 고정했다.
- 진단·기각 arm 은 **성적 표에서 분리**하고 매 행에 `🔴 성적이 아니다` 라벨을 강제한다
  (`ORACLE` 은 산출물의 `diagnostic` 플래그, 기각은 판정 대장의 **기호**로 판별한다 —
  키워드로 읽으면 *"⏸ 보류 (세우지 않는다)"* 가 기각으로 잘못 분류된다. 테스트로 고정).

---

## 6. 산출물 · 테스트

```
site/  13파일 · 1,859 KB
  index.html 7.3K · ablation.html 20.0K · errors.html 406.7K · pipeline.html 5.1K · data.html 6.3K
  style.css 4.9K · filter.js 2.0K · README.md 2.8K
  data/site_meta.json 6.6K · site_ablation.json 37.8K · site_errors.json 1,343.6K
  data/site_pipeline.json 7.6K · site_data.json 8.6K
```

`errors.html` 이 큰 이유는 **정적 필터가 쓸 1,632행**(2세대 × 14 arm)을 쪽 안에 박기 때문이다
(`file://` 에서 `fetch` 가 막힌다). 쪽에는 **화면이 쓰는 12열만** 넣고 전량은 JSON 에 둔다.

```
tests/test_site.py   59 passed      전량 1,055 passed / 0 failed
```

🔴 **착수 시점 기준선은 973 이 아니라 990 이었다**(다른 에이전트들이 그사이 늘렸다).
59를 더한 뒤 다시 잰 값이 1,055 이므로 그사이 6이 더 늘었다 — **감소는 없다.**

테스트가 고정하는 것: 정본 검산 넷 · **검산이 깨지면 죽는지**(원장·게이트 두 경우를 인위로
깨뜨려 확인) · 리터럴 금지 · 세대 경로 리터럴 금지 · `import argus` 부재 · 금지 경로 8종 ·
화이트리스트와 차단 목록이 **산출물 전 필드를 덮는지**(새 필드가 붙으면 실패한다) ·
게이트 6종의 발화 · 마스킹이 없으면 게이트가 죽는지 · 세대 라벨(푸터·표·LOO 기준선) ·
분모 자기일관성(`단계 합 == 분모 − 정답`) · 부풀림 병기 · 잔여 오답이 셈의 결과인지 ·
**재현성**(두 번 빌드해 `metadata.generated` 외 동일) · 죽은 링크 0 · 데모 자리 예약 ·
필터 JS 의 네트워크 호출 0.

---

## 7. 🔴 09-21 이후 라이브 데모를 붙일 자리

`site/index.html` 맨 아래 **`<div class="card pad" id="live-demo-slot" data-status="reserved">`**.
지금은 비어 있고 세 줄을 들고 있다.

```
① 지금은 정적 필터만 제공한다 (오류 분석 쪽)
② 붙일 때 함께 붙일 것 둘 — 소비 상한 · API 키 분리 또는 상한
   (단가는 산출물의 `price_won_per_1k` 에서 읽어 화면에 띄운다 — 리터럴이 아니다)
③ 🔴 데모의 질문 입력은 **사용자가 쓴 문장**이어야 한다 — 평가셋 문항을 채워 두면 보안 ④ 가 깨진다
```

`site/README.md` 의 마지막 절도 같은 자리를 가리킨다. 렌더러는 `render_index()` 한 곳만
고치면 되고, **데이터 층(파서·게이트·화이트리스트)은 그대로 재사용된다.**

---

## 8. 남긴 것 · 넘기는 것

**하지 않은 것 (지시된 쓰기 범위 밖이라 손대지 않았다 — `site/` · `scripts/build_site.py` ·
`tests/test_site.py` · 이 보고서 · `.gitignore` 만 썼다)**

| 원 태스크 | 왜 안 했나 | 넘김 |
|---|---|---|
| `R6-5 Step 5` — `docs/total_plan.md §8` 에 **역참조 한 줄** | 계획서·정본 문서는 사용자가 관리한다 | 🔴 **여전히 필요하다.** *"`site/` 가 성적·지출을 이 문서에서 파싱한다 — 사본이 아니다"* 한 줄이 없으면 §1 을 고치는 사람이 파서의 존재를 모른다 |
| `R6-5 Step 4` — 루트 `README.md` 두 행 | 같음 | *"결과 사이트는 로컬 정적 빌드다 · 🔴 평가 서버에 올리지 않는다"* |

**`.gitignore` 판단** — `site/` 를 **넣었다**. `artifacts/` 와 같은 규율이다(생성물을 커밋하면
정본이 움직일 때마다 사본이 하나 더 낡는다). 빌더와 테스트만 추적한다.

**측정 중에 눈에 띈 것 (전부 이 사이트의 결함이 아니라 정본 문서 쪽이다)**

1. 🔴 **`total_plan §1` 의 테스트 수가 낡았다** — 헤드라인·꼬리는 서로 맞아 검산 ③은 통과하지만
   (그래서 사이트는 그 값을 그대로 미러한다), **실측 전량은 그보다 크다.** 라운드가 테스트를
   계속 늘리고 있다. 🔴 **사이트는 "정본 문서 기준" 이라고 라벨을 붙여 렌더한다.**
2. `total_plan §1` 의 *"Phase 별 한 줄 요약"* 표에 낡은 값이 넷 있다 — `B` 의 문항 수 ·
   `E` *"보류"*(게이트가 이미 `3/3` 이고 `A3` 는 최종 기각) · `H` 의 오답 건수 · `F` 의 도구 종수.
   **그래서 사이트는 이 표를 렌더하지 않고 문서를 링크한다.** `R5-7` 이 볼 자리다.
3. 도구 종수(14 vs 15)는 `R1-12b`(`392af47`)가 이미 닫았다. 사이트는 애초에 링크만 한다.
4. `total_plan §5` 의 세 분모 표가 §0 의 현 세대와 갈려 있다(검토가 이미 지적) — 사이트는
   **산출물에서 세므로** 영향받지 않는다.

**한계**

- HTML 렌더 층에는 스냅샷 테스트가 없다(구조·게이트·링크만 검사). 육안 확인은 했다 —
  5쪽 · 표 · 필터 · 375px 폭에서 표만 가로 스크롤(`.table-wrap`).
- 사다리 Δ 는 **그 세대에 실제로 있는 arm 사이**의 전이다(빠진 arm 은 건너뛴다). 각 행이
  `Δ 기준` 열로 **무엇에서 뺐는지**를 밝힌다.
- `--prove-gates` 는 게이트가 **발화하는지**를 보이지, 게이트가 **모든 유출을 잡는지**를
  증명하지 않는다. 화이트리스트가 그 자리를 맡는다.
