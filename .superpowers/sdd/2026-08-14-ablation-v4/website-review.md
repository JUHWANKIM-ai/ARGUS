# 웹사이트 계획서 검토

**대상** `docs/superpowers/plans/2026-08-11-argus-website.md` (검토 전 6,039행 · 미완 108 / 완료 0 · `site/` 없음)
**검토일** 2026-08-14 · **크레딧 0원 · LLM 0회 · `.env` 미열람 · `heldout` 미열람**
**결론** 낡은 자리 **22곳**을 찾아 **17곳을 정본 참조/파싱으로 구조 교체**, 5곳은 서술 정정.
범위는 **정적 HTML 5쪽 MVP(`R6`)** 로 좁혔고 Next.js 판 Task 1~10 은 **제출 후로 이월**.
검토 후 **7,132행**(§0 정본 대장 · 보안 5절 · §R6 5태스크 28스텝 추가).

> 🔴 **이 보고서에 적힌 수치는 2026-08-14 스냅샷이다.** 성적·지출·테스트 수의 정본은
> `docs/total_plan.md` §1 이고, arm 판정 정본은 `docs/pipeline/09-ablation.md §I-5f ⑧` 이다.
> **이 파일을 인용하지 말고 그 두 곳을 인용하라.** 아래 "종전 값" 열은 **결함의 기록**이므로
> 갱신하지 않는다.

---

## 낡은 것 전수 (찾은 것 / 고친 방식)

### A. 🔴 사용자가 지목한 결함 — 본문에 복사된 지출 표

| | 종전 (계획서) | 현재 정본 | 고친 방식 |
|---|---|---|---|
| A1 | `COST_LEDGER` **8행 상수 표** + 합계 **12,846.7원** (3자리: 코드 상수 · 주석 · 테스트 실패 안내문) | `total_plan §1` (누적 **12행**) | 🔴 **표를 삭제하고 `parse_ssot()` 로 마크다운을 파싱.** 행 합과 `누적` 행을 둘 다 읽어 **어긋나면 `ValueError` 로 빌드가 깨진다** — 사본이 아니라 **미러** |
| A2 | `build_meta.spentWon = 12846.7` | 같음 | 같은 파서 |
| A3 | `test_build_ablation_ledger_sums_to_total` (자기합만 검사 → 사본이 낡아도 통과) | — | `test_build_ablation_ledger_mirrors_the_ssot` — 파싱값과 **동일성** 검사 |
| A4 | — | — | 🆕 `test_build_site_data_has_no_hardcoded_currency` — **소스에 금액 리터럴 자체를 금지**(주석 제외) |

**실측 검증**: 파서를 실제로 돌려 원장 **12행 · 행 합 = `누적` 행(검산 통과) · 테스트 수 ·
게이트 총계 · Phase 9행**을 모두 뽑았고, `count_tool_specs()` 도 `tools.py` 를 **import 하지 않고**
도구 종수를 냈다. (값은 정본을 보라.)

### B. 성적·측정 수치 (v2 세대 → v3 R2D·R2E·R2G · v4 R0 로 세 번 이동)

| | 종전 | 고친 방식 |
|---|---|---|
| B1 | 홈 지표 카드 기대값 `61/72 · 6/6 · 8/10 · 15/18` | 기대값 삭제. **`site_meta.json` 과 글자 단위 일치**만 확인. 🔴 heldout 카드 제거 |
| B2 | `test_build_meta_carries_gate_numbers` 가 `correct == 61` 단언 | `..._mirrors_the_artifact` — 산출물과 동일성만 |
| B3 | `FINDINGS` 문장에 박힌 **토큰 337,603 / 337,555 · judge 48 · 14.1원 · +56.9%p · +51.4%p · −8.3%p** (6개) | `{자리표시자}` + `_fill_findings(ladder_arms)` 주입. **실측 재계산 결과가 정본과 일치**(−8.3 · 367,864 / 367,793 · gap 71 · +61.1 · +52.8) |
| B4 | `/ablation` Expected 에 `A1b +38.9%p · A4 +18.1%p · L1 −8.3 · 원장 12,847원` | 값 삭제 → **구조와 부호**로 판정(A1 막대 빨강 · 최대 Δ 행이 `A1b` · A4 이후 0.0 · L2 최대 음수 · 두 세대 라벨 · 원장 = JSON) |
| B5 | `DENOMINATORS` 3값 `13 · 11 · 12` + 구성 문자열 `GENERATION 5 · VERIFY 3 · RETRIEVAL 2 · RANKING 1` | `report.json` 에서 **센다**(`DENOMINATOR_DEFS` 는 분모 라벨만). 🔴 **분모 라벨은 정의라서 남긴다** |
| B6 | `test_build_errors_grid_failure_count_is_eleven` (`== 11`) | `..._are_self_consistent` — `단계 합 == 그리드 분모 − 정답`. 어느 세대에서도 성립 |
| B7 | `test_build_errors_tool_and_routing_are_zero_in_grid` (`TOOL == 0`) | 🔴 **현 세대 실측 `TOOL 3 · GENERATION 2 · VERIFY 1`** — 단언이 거짓이 됐다. **분포가 원천과 같은지**만 검사 |
| B8 | `test_build_errors_lists_three_denominators` (`== [13,11,12]`) | 키 순서·라벨 존재·포함관계(`overall ≥ grid`)만 |
| B9 | `DATA_LIMITS` 규모 `66건 · 105건 · 49.2% · 45.5% · 2건 · 24건` (한 건은 자기 주석에 *"§4 의 166건은 출처 미확인"* 이라 적혀 이미 어긋나 있었다) | 항목·사유만 남기고 **규모는 §4 링크** |
| B10 | Phase 표 `게이트 5/5…0/3…12/12`(합 68/71) · 크레딧 6열 | 🔴 게이트 E 가 `A3` 최종 기각으로 **3/3** 이 되며 한 행이 낡고 총계가 틀어졌다 → `parse_ssot()["phases"]` 와 `order` ↔ `no` 조인. **TS 에서 필드 삭제** |
| B11 | `/pipeline` `테스트 728` · `기존 728건 + 신규 9건` (3자리) | 파싱값. 회귀 판정은 **`failed` 0건**으로. 🔴 건수 문장 삭제 |
| B12 | `Phase B outcome: 106문항 (그리드 90 + 정보한계 6 + 회귀 10)` | 🔴 **어느 세대와도 안 맞는 수**(현 구성은 그리드 72 + 정보한계 6 + 회귀 10 + 밸류체인 12). 문항 수 삭제 → 분모는 산출물이 센다 |
| B13 | `Phase F outcome: 상 A1b→A4 +33.3%p` · `Phase D: Recall@5 0.880 (117/133)` · `Phase H: RETRIEVAL 64.7% · 13·11·12` | 전부 산문화. 값은 히트맵·`site_data.json` 이 산출물에서 낸다 |
| B14 | 홈 능력 카드 `정정체인 hop 제거 시 −8.3%p (L1)` | 현 세대 −9.7%p. `{l1DeltaPp}` 자리표시자 |
| B15 | 밸류체인 판정 `정답률 5/12 대 4/12 · 골든 Q-110 5/6 …` | `vc_golden()` 이 **`vc/{arm}/report.json` 행에서 센다**. 🔴 실측: `vc/summary.json` 의 집계 필드는 **0** 이라 그대로 읽으면 화면에 `0/0` 이 뜬다 — 행에서 세면 정본과 일치(34/46 · 27/46 · 도달불가 3) |
| B16 | 원천 경로 `artifacts/ablation/v2/**` **11자리 고정** | `resolve_generation()` + `resolve_ladder_generation()`. 🔴 **사다리 A0~A8 은 v2 에만 있고 v3 는 A8·LOO·진단 arm 뿐**(실측) — 두 세대를 **라벨 붙여 따로** 렌더하고 **서로 빼지 않는다** |
| B17 | 성적·단계 귀속 원천 = `artifacts/eval/error_analysis_v2.json` | 🔴 `total_plan §5` 가 *"그 파일은 재생성되지 않았고 옛 라벨(`VERIFY` 3)을 들고 있다"* 고 명시 → `ablation/{gen}/A8/report.json` 으로 이동 (`recall_v2.json` 은 게이트 D 정본이라 유지) |

### C. 서술 정정 (값이 아니라 문장이 틀린 것)

| | 종전 | 정정 | 자리 |
|---|---|---|---|
| C1 | **"GraphRAG 를 기각했다"** | 🔴 **"기업—기업 엣지 정의 기각"**. GraphRAG 전제는 기각되지 않았고 **재정의한 쪽은 채택**됐다 — `doc_subtype` 메타 축이 `A1→A1b` 로 **사다리 최대 전이**(실측 재확인: 8전이 중 최대) | 6자리: `COUNTERPARTY_VERDICT` 제목·본문 · 테스트 docstring · `/counterparty` 히어로 · `Phase A shortDescription`·`outcome` |
| C2 | "`A5` 부터 Δ 가 0" | 꺾이는 지점은 **`A4`** (A4=A5=A6=A7=A8) | `FINDINGS` 제목·본문 · Phase I detail |
| C3 | "`A3` 리랭커는 **의도적 보류** · 미완 3게이트" | **최종 기각** — 겨냥 0 을 세 분모 전부에서 확인, 게이트 E **3/3** | `Phase E` 전체 |
| C4 | "제출물 3종" 가정(제출물 목록 언급 없음) | **4종** — 전처리 산출물 추가(주최측 #10) · `Dockerfile` **필수 아님**(#11) | §0 정정표 · `ARTIFACTS_NOTE` |
| C5 | "측정이 계획을 **여섯 번** 뒤집었다" | 라운드마다 늘어난다(v4 `R0` 가 전제 5개를 더 뒤집었다) → **횟수 삭제 + §7 링크** | `/pipeline` h1 |

### D. 추가로 찾은 것 (사용자 지목 밖)

| | 무엇 |
|---|---|
| D1 | `toolCount = 15` 리터럴 → `ast` 로 `tools.py` 를 센다. 🔴 **import 하면 `argus.config.settings` 가 `.env` 를 읽는다** — 보안 ① 위반 경로였다 |
| D2 | `test_build_script_never_references_heldout_artifacts` 가 `"v2/heldout" not in src` 였다 → **세대가 v3·v4 로 올라가면 비켜 간다.** `read_json` **호출 인자**를 검사하도록 교체 |
| D3 | 세대 라벨이 어디에도 없었다 → 모든 페이지 푸터에 `generation.arm · generated · argusCommit` 필수 |
| D4 | `failure_why` 자유 서술에 **6자리 이상 숫자가 3행**(실측) → `_mask_amounts()` 로 마스킹 |
| D5 | `L4`(REFERS_TO LOO)가 `LOO_ORDER` 에 없었다(v3 신설) → 추가. `ORACLE`·`A8-lean` 은 `DIAGNOSTIC_OR_REJECTED` 로 **사다리에서 분리**하고 "성적이 아니다" 라벨 강제 |

---

## 사본 → 정본 참조로 바꾼 목록

**17자리.** 방식은 셋뿐이다 — ① **빌드가 정본을 파싱**, ② **빌드가 산출물에서 센다**, ③ **링크만**.

| # | 값 | 정본 | 방식 |
|---|---|---|---|
| 1 | 누적 지출 총액 | `docs/total_plan.md` §1 `누적` 행 | ① `parse_ssot()` + **검산 대조** |
| 2 | 지출 원장 12행 | 같은 표 | ① |
| 3 | 웰컴 크레딧 | `CONVENTION.md` (규정값) | 상수 유지 + 비율 계산 |
| 4 | 테스트 수 | `total_plan §1` | ① |
| 5 | 게이트 총계 | `total_plan §1` | ① |
| 6 | Phase 9행 게이트·크레딧·상태 | `total_plan §1` Phase 표 | ① `order` ↔ `no` 조인 |
| 7 | 도구 종수 | `src/argus/agent/tools.py` | ② `ast` (import 금지) |
| 8 | 그리드·정보한계·회귀·전체 성적 | `artifacts/ablation/{gen}/summary.json` | ② |
| 9 | 사다리 Δ 8행 | 사다리 세대 `summary.json` | ② + 세대 라벨 |
| 10 | LOO Δ | **현 세대** `summary.json` | ② + 같은 세대 기준선 강제 |
| 11 | 잔여 실패 세 분모 | `{gen}/A8/report.json` | ② 센다 |
| 12 | 단계·근본원인·문서군 분포 | 같은 파일 | ② |
| 13 | 밸류체인 골든 회수 | `{gen}/vc/{arm}/report.json` **행** | ② 센다 (summary 집계는 0) |
| 14 | 엣지 밀도·기재율 | `artifacts/eda/relation_profile.json` | ② |
| 15 | arm 판정(채택·기각) 목록 | `docs/pipeline/09-ablation.md §I-5f ⑧` | ③ 링크 (`verdictSource`) |
| 16 | 엔드포인트·공인 IP·유지 기한 | `deploy/README.md` 첫 절 | ③ 링크 (`endpointSource`). 🔴 **IP 는 발행하지 않는다** |
| 17 | 주최측 규격·공지 영향 | `docs/organizer-qa.md` · `CONVENTION.md §4` | ③ 링크 (`specSource`) |

🔴 **역참조도 넣었다** — `R6-5 Step 5` 가 `docs/total_plan.md` §8 참조표에
*"`site/` 가 이 문서를 파싱한다"* 한 줄을 박는다. **정본→사본 방향만 적어 두면
§1 을 고치는 사람이 사본의 존재를 모른다.** 이것이 열한 번 반복된 결함의 실제 기전이다.

🔴 **그리고 파서는 조용히 틀리지 않는다.** 원장 행 합 ≠ `누적` 이면 `ValueError` 로 빌드가
깨진다. `total_plan §1` 이 그 사고를 실제로 겪었으므로(R2E 가 행만 넣고 `누적`·검산식을
안 고쳐 하루를 낡은 채로 살았다), 이 파서는 **재발 감지기**를 겸한다.

---

## 보안 보강

종전 계획의 보안은 두 줄(*"heldout 유출 금지"* · *"비밀정보 금지"*)이었다.
**다섯 절로 확장**하고 각각에 **하드 게이트**를 붙였다. `Global Constraints` 안의 `## 🔴 보안` 절.

| # | 무엇 | 강제 장치 |
|---|---|---|
| ① | `.env` · `CLOVA_API_KEY` 를 `site/` 아래에 **절대 복사하지 않는다** | 🔴 **`argus` 패키지 import 금지**(`settings()` 가 `.env` 를 읽는다) — 소스 정규식 검사 + 생성물에서 `nv-`·`sk-`·`Bearer `·40자 이상 영숫자 덩어리 검사. `scp .env` 절차를 사이트 문서에 옮겨 적지 않는다 |
| ② | 코퍼스 원문(**5.55GB**)은 **재배포 대상이 아니다** | `FORBIDDEN_PATHS` 에 `"data/"` 추가 → `read_json("data/…")` 이 `ValueError`. 근거는 산출물의 `retrieved_context` **발췌만**, 문서당 1,200자 상한 |
| ③ | `evalset/heldout.jsonl` · heldout 산출물 | `FORBIDDEN_PATHS` `"heldout"` (🔴 `heldout_v2.jsonl`·`v3/`·`v4/` 도 함께 막힌다 — 테스트에 케이스 추가). 🔴 **집계 수치 `15/18` 조차 싣지 않는다** — 낙폭 게이트는 **은퇴**했고 그 원장은 옛 세대와 짝지어진 **동결 값**이다 |
| ④ | 🆕 **`evalset/questions.jsonl`(+`regression`·`valuechain`)의 질문 전문·정답** | 🔴 **이 검토가 새로 막았다.** 종전 계획은 홈 예시 6개 · `/search` **전 문항 전문** · `/answers` 질문+답변을 **그대로 발행**하게 되어 있었다. 사유 셋: ⓐ 정답은 **우리가 만든 라벨**이고 `total_plan §5` 가 **채점 결함 셋**을 명시한다(`Q-110` 10× 환산이 judge 통과 = 라벨에 부풀림), ⓑ 검색·랭킹 튜닝이 가능한 형태(heldout 폴더에 같은 경고가 이미 박혀 있다), ⓒ 새 heldout(`R4-1`) 셀 설계가 오염된다. **싣는 것은 `question_id` + 축 메타 + 채점 결과뿐.** 게이트가 전 문항 전문과 **앞 20자**를 대조 |
| ⑤ | 🆕 `golden` · `golden_rank` · `answer` · `think_trace` (문항 단위) | 🔴 원천을 `error_analysis_v2.json`(41필드) → `report.json`(**44필드**)로 옮기면 **노출면이 커진다**. `ROW_FIELDS_ALLOWED` **화이트리스트**로만 뽑는다(블랙리스트 금지 — 다음 세대에 새 필드가 붙으면 조용히 샌다). `corp_name`·`sector` 는 **일부러 뺐다**(문항 100건에 `(문서군, 난이도, 기업명)` 을 함께 내면 평가셋 부분 복원 가능). `answer` 는 질문을 되풀어 쓰므로 ④ 의 우회로 |

**추가**
- 🔴 **공인 IP 비발행.** `test_gate_no_public_ip` 는 IP 를 테스트에 적지 않고 `deploy/README.md`
  에서 뽑아 대조한다 — **정본에서 읽어 검사**한다.
- 🔴 **게이트는 HTML 까지 훑는다.** JSON 만 검사하면 템플릿에 박은 문자열을 놓친다.
- 🔴 **발화 확인을 태스크로 만들었다** (`R6-2 Step 2`). 일부러 위반 페이로드를 넣어 **FAIL 을 본 뒤**
  지운다. **발화를 확인하지 않은 게이트는 게이트가 아니다.**
- 🔴 **`/answers` 는 폐지가 아니라 조건부 동결.** 되살리는 조건 셋(자체 시연 질의 · `A19` 로 부풀림 0 확인 · 09-21 이후)을 Task 5 머리에 박았다.

---

## 범위 판정 — MVP 는 무엇인가

### 판정: 🔴 **Next.js 판은 3주 안에 들어가지 않는다**

```
미완 108 / 완료 0 · site/ 없음        코드 동결 08-25 까지 11일 · 제출 09-06
제출물 4종에 웹사이트는 없다           → 제출을 위험에 빠뜨릴 수 없다
v4 §14 가 이미 "제출 후 또는 폐기" 로 분류    (이 검토는 그 판단을 유지하되 최소본을 남긴다)
```

### MVP = **정적 HTML 5쪽 · Node/npm 0 · 크레딧 0원 · LLM 0회**

사용자가 준 세 조건을 그대로 대조했다.

| 조건 | Next.js 판 | 정적 HTML 판 |
|---|---|---|
| 제출을 위험에 빠뜨리지 않는다 | 🔴 npm 툴체인 신설 · TSX 12파일 · CSS 900행 | ✅ 스크립트 1개 + 테스트 1개 |
| 🔴 평가 서버(223.130.129.131:80 · c2-g3a 2 vCPU / 4 GB)와 자원을 다투지 않는다 | 🟡 `node` 런타임이 필요해 **같은 서버에 올릴 유혹**이 생긴다 | ✅ **파일이라 서버가 없다.** 저장소/GitHub Pages/로컬 |
| 빌드 타임에 `artifacts/` 를 읽어 정적 산출물 | ✅ | ✅ |

🔴 **호스팅 판정을 계획서에 명문화했다** — *"09-07~09-20 에 같은 2 vCPU 를 웹서버와 나누면
단건 지연이 평가 기준일 수 있다(주최측 #1 sequential · 타임아웃 300초). 그 서버에 올리지 않는다."*

**만드는 것 5쪽** — `index`(헤드라인·능력 4개·축 커버리지) · `ablation`(사다리·LOO·히트맵·원장·기각 링크) ·
`errors`(6단계·근본원인·문서군·**분모 셋**·데이터 상한) · `pipeline`(Phase A~I·게이트·크레딧) ·
`data`(코퍼스·인덱스·Recall@5·API 계약·출처).

**안 만드는 것** — `/answers`(보안 ④⑤ · 조건부 동결) · `/search`(클라이언트 필터는 정적 범위 밖) ·
`/corrections`·`/counterparty`(🟡 여유 시 `R6-6`, 각 40분) · Next.js·React·TS·npm ·
평가 서버 배포 · 새 측정.

🔴 **디자인은 버리지 않는다.** `scratch/ARGUS_example` 의 토큰·시맨틱 클래스명·카드/표/막대 구조를
CSS 한 파일로 옮긴다. 버리는 것은 **런타임**이다.
🔴 **데이터 층은 재사용된다** — `parse_ssot`·`resolve_generation`·유출 게이트·화이트리스트가
그대로 `build_site.py` 로 간다. 나중에 Next.js 판을 살리면 **렌더 층만 다시 쓴다.**

**예상 공수** `R6` 전체 **6~9시간** (골격 1h + 게이트 1h + 빌더 2h + 렌더 2~3h + 마감 1~2h) ·
**크레딧 0원 · LLM 0회** · 우리 토큰 ~25%.
**선행** `R5`(제안서 최종) 완료 후. 🔴 **밀리면 버린다 — 제출물 4종이 먼저다.**

---

## ★ v4 §R6 에 넣을 요약 (태스크 양식)

```markdown
# §R6. 결과 공개 사이트 — 🔴 MVP · 정적 HTML 5쪽 (제출물 아님)

**전문** [`2026-08-11-argus-website.md` §0·§R6](2026-08-11-argus-website.md).
🔴 그 문서의 Task 1~10(Next.js 판)은 **제출 후 이월분**이다. `R6` 은 `§R6` 만 실행한다.

**Goal:** `artifacts/` 와 `docs/total_plan.md` 를 빌드 시점에 읽어 **정적 HTML 5쪽**을 낸다.
**Node/npm 0 · 크레딧 0원 · LLM 0회 · `src/` 는 읽기만** (코드 동결을 깨지 않는다).

🔴 **선행 조건** — `R5` 완료 후. **밀리면 버린다. 제출물 4종이 먼저다.**
🔴 **호스팅은 `223.130.129.131` 이 아니다.** 09-07~09-20 에 평가 서버의 2 vCPU 를
웹서버와 나누면 단건 지연이 평가 기준일 수 있다(주최측 #1 sequential · 타임아웃 300초).

## 🔴 이 라운드의 실제 산출물은 "사본 없는 구조" 다

이 계획서가 **v2 세대 수치를 22자리에 복사**해 두고 세 세대를 낡은 채로 살았다
(누적 지출 · 테스트 수 · 게이트 68/71 · 성적 61/72 · 잔여 실패 13·11·12 · 문항 106 …).
🔴 **값을 갈아 덮지 않고 정본을 가리키게 고쳤다** (v3 `R3-b` 규율). 17자리가 파싱/셈/링크로 바뀌었다.

## Task R6-1: 빌드 골격 — 정본 파서 · 세대 해석 · 유출 게이트 (~1h)

- [ ] **Step 1: 실패하는 테스트를 먼저 쓴다 (4분)** — 스키마 5건.
     `pytest tests/test_build_site.py -q` → `ModuleNotFoundError: build_site`
- [ ] **Step 2: `scripts/build_site.py` 헬퍼 (5분)** — `FORBIDDEN_PATHS=("heldout","data/")` ·
     `resolve_generation` · `resolve_ladder_generation` · `parse_ssot` · `count_tool_specs` ·
     `ROW_FIELDS_ALLOWED` · `_mask_amounts`. 🔴 **`argus` import 금지**(`settings()` 가 `.env` 를 읽는다)
- [ ] **Step 3: 정본 파서를 실측한다 (2분)**
     `python -c "...import build_site as b; print(b.parse_ssot()['spentWon'], b.parse_ssot()['tests'], b.count_tool_specs())"`
     Expected: 누적 지출·테스트 수·도구 종수가 나온다. 🔴 **그 값을 어디에도 적지 않는다.**
     행 합 ≠ `누적` 이면 `ValueError` — **고칠 것은 스크립트가 아니라 `total_plan §1` 이다**
- [ ] **Step 4: 테스트 통과 (2분)** · **Step 5: 커밋 (2분)**

## Task R6-2: 🔴 유출 게이트를 화면보다 먼저 켠다 (~1h)

- [ ] **Step 1: 다섯 검사 (5분)** — ③heldout · ④evalset 질문 전문(+앞 20자) ·
     ⑤`golden`/`golden_rank`/`answer`/`think_trace` · ①API 키 형태 · 공인 IP.
     🔴 IP 는 `deploy/README.md` 에서 뽑아 대조한다 — 테스트에 적지 않는다
- [ ] **Step 2: 🔴 발화를 확인한다 (3분)** — 일부러 질문 전문을 실어 `-k gate` 가 **FAIL** 하는지 본다.
     **발화를 확인하지 않은 게이트는 게이트가 아니다**
- [ ] **Step 3: 프로브 삭제 후 재통과 (1분)** · **Step 4: 커밋 (2분)**

## Task R6-3: 데이터 빌더 다섯 (~2h)

- [ ] **Step 1~4: `meta`·`ablation`·`errors`·`pipeline`·`data` (각 4~5분)**
     🔴 `heldout` 키 없음 · **두 세대 라벨**(사다리는 v2 에만, LOO 는 현 세대) ·
     분모 셋을 **센다** · Phase 표는 파싱 · `API_CONTRACT` 에 IP 없음
- [ ] **Step 5: 생성 + 게이트 (3분)** — `site/data/site_*.json` 5개 · 전건 PASS
- [ ] **Step 6: 🔴 생성물을 사람이 읽는다 (4분)** — 각 `metadata.note` 와 `sourceFiles` 확인 ·
     `grep -c golden site/data/*.json` → 전부 0
- [ ] **Step 7: 커밋 (2분)** — `git add -f site/data`

## Task R6-4: 렌더러 — 정적 HTML 5쪽 (~2~3h)

- [ ] **Step 1: `site/style.css` (5분)** — 레퍼런스 토큰·시맨틱 클래스만. 막대는 `div` 폭 %
- [ ] **Step 2: HTML 헬퍼 (5분)** — `esc`(전 값 통과) · `page`(🔴 푸터에 세대 라벨 + `argusCommit`) ·
     `card` · `table` · `bar`. **템플릿에 수치를 넣지 않는다**
- [ ] **Step 3: 다섯 쪽 (5분 × 5)** — 🔴 `ablation` 에 **두 세대 라벨**과 "성적이 아니다" 라벨(`ORACLE`·`A8-lean`) ·
     `errors` 에 **분모 셋을 각자 라벨과 함께** · `data` 에 Recall@5 **코호트 라벨 필수**
- [ ] **Step 4: 렌더 + 육안 (4분)** — 죽은 링크 0 · 375px 가로 스크롤 0 · 푸터 세대 라벨
- [ ] **Step 5: 🔴 HTML 에도 게이트 (3분)** — `grep -c 223.130 site/*.html site/data/*.json` → 0
- [ ] **Step 6: 커밋 (2분)**

## Task R6-5: 마감 — 회귀 · 재현 · 정본 역참조 (~1~2h)

- [ ] **Step 1: 전량 테스트 (3분)** — `failed` 0건. 🔴 늘어난 수를 `total_plan §1` 에 반영하고
     **증가 이력 꼬리도 함께 잇는다**(헤드라인만 고치면 열 번째 결함의 재발이다)
- [ ] **Step 2: 🔴 재현성 (3분)** — 두 번 돌려 `git status --porcelain site/` 가
     `metadata.generated` 외에 깨끗한지. 불안정하면 `sorted()` 를 넣는다
- [ ] **Step 3: `site/README.md` (4분)** — 공개하지 않는 것 다섯 · 페이지 표 · 데이터 흐름
- [ ] **Step 4: 루트 `README.md` 두 행 (2분)** — 🔴 "평가 서버에 올리지 않는다" 포함
- [ ] **Step 5: 🔴 `docs/total_plan.md` §8 에 역참조 한 줄 (2분)** —
     *"`site/` 가 성적·지출을 이 문서에서 파싱한다 — 사본이 아니다."*
     **이 줄이 없으면 §1 을 고치는 사람이 사본의 존재를 모른다**
- [ ] **Step 6: 커밋 (2분)**

**예상**: `R6` 전체 **6~9시간** · 크레딧 **0원** · LLM **0회** · 우리 토큰 ~25%
**하지 않는 것**: `/answers`(보안) · `/search`(정적 범위 밖) · Next.js·npm · 평가 서버 배포 · 새 측정
```

---

## 확인 못 한 것

1. 🔴 **`scratch/ARGUS_example` 을 열지 않았다.** 레퍼런스 충실도(토큰 이름·클래스명 목록)는
   계획서 본문의 서술을 그대로 신뢰했다. `R6-4 Step 1` 에서 실제 파일을 봐야 한다.
2. 🔴 **`artifacts/ablation/v4/` 는 존재하지 않는다.** `resolve_generation()` 의 `v4` 분기는
   **아직 실행된 적이 없다** — `R1`~`R3` 이 `v4/summary.json` 을 만들면 그때 첫 검증이 된다.
   🔴 **v4 가 사다리를 재측정하지 않으면 사다리는 계속 v2 세대로 렌더된다** (그것이 옳지만,
   화면 라벨이 그 사실을 말해야 한다).
3. **`recall_v2.json` 의 코호트 키를 열어보지 않았다.** `build_data` 의 `_recall_cohort` 가
   값으로 최대 코호트를 고르게 되어 있어 키 이름 변경에 견디지만, 실측은 안 했다.
4. **`artifacts/eda/doc_profile.json`·`relation_profile.json`·`counterparty_graph.json` 의 필드명**은
   계획서가 *"실측 확인"* 이라 적은 것을 신뢰했다. 세 파일은 EDA 산출물이라 세대를 타지 않는다.
5. 🔴 **`docs/pipeline/09-ablation.md §I-5f ⑧`(arm 판정 대장)의 내용을 전수 확인하지 않았다.**
   사이트는 그 목록을 **복사하지 않고 링크**하므로 낡을 수 없지만, 보고서 본문의 기각 arm 목록
   (`A3`·`A11`·`A9`·`A8-lean`·`A16`·`T1-full`·`T1-md`·`A2`·`A1b-clova`·`A4-rr`)은 v4 계획서 §14 와
   총괄 문서에서 조합한 것이다 — **인용은 `§I-5f ⑧` 으로 한다.**
6. **`total_plan §5` 의 세 분모 표(11 · 10 · 9)가 §0 의 현 세대(잔여 6건)와 갈려 있다.**
   이것은 웹사이트의 결함이 아니라 **정본 문서 안의 낡은 절**이다. 🔴 사이트는 산출물에서
   세므로 영향받지 않지만, **`R5-7`(열한 번째 하드코딩 찾기)이 이 절을 봐야 한다.**
   (실측: 현 세대 `report.json` 은 전체 오답 6 · 그리드 오답 6 · 배분 6 · 단계 히스토그램 합 6.)
7. **HTML 렌더 층에는 자동 테스트가 없다.** `R6-4 Step 4`·`Step 5` 의 육안 확인과 게이트가
   유일한 방어다. Next.js 판도 같은 한계였다(`Jest·Vitest` 미도입).
8. 🔴 **`heldout` 관련 값은 계획서에서 전부 걷어냈지만, `R4-1` 이 새 heldout 을 만들면
   "새 낙폭을 사이트에 실을지" 는 열린 판단이다.** 현 계획은 **싣지 않는다** 쪽이다 —
   그 값이 제안서 `§5`·`§6`·`§11` 에 들어가고, 사이트는 정본을 링크하면 된다.
