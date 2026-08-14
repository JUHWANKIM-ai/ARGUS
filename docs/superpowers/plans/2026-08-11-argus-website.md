# ARGUS 웹사이트 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `scratch/ARGUS_example` 의 구조·디자인·데이터 흐름을 그대로 물려받아, ARGUS 공시 QA Agent 의 근거·정정체인·발주처·기여도 사다리를 보여주는 Next.js 사이트를 `site/` 에 만든다.

**Architecture:** 레퍼런스의 핵심 원칙을 그대로 가져온다 — **사이트는 계산하지 않고 렌더링만 한다.** Python 빌드 스크립트(`scripts/build_site_data.py`)가 `artifacts/` 와 `evalset/` 을 읽어 `site/public/data/site_*.json` 을 생성하고, Next.js 서버 컴포넌트가 그 JSON 을 정적 import 한다. 상호작용이 필요한 곳만 클라이언트 컴포넌트다. 파싱·집계 로직은 전부 Python 에 있으므로 기존 pytest 하네스가 그대로 테스트한다.

**Tech Stack:** Next.js 15 (App Router) · React 19 · TypeScript · 순수 CSS(토큰 방식, Tailwind 없음) · Python 3 빌드 스크립트 · pytest

---

## §0. 🔴 현재 상태 재판정 (2026-08-14) — **범위를 MVP 로 좁혔다**

```
작성        2026-08-11        미완 108 / 완료 0        site/ 디렉터리 없음
재검토      2026-08-14        제출 09-06 23:59 KST (D-23) · 평가 09-07~09-20
편입        ablation v4 §R6   (계획 정본 = docs/superpowers/plans/2026-08-14-ablation-v4.md)
```

🔴 **이 계획은 세 세대 뒤의 수를 들고 있었다.** 작성 시점은 ablation **v2** 세대(2026-08-11)이고
그 뒤 **v3 R1·R2D·R2E·R2F·R2G** 와 **v4 R0** 가 성적·지출·arm 판정을 전부 움직였다.
본문에 복사돼 있던 표가 낡은 것이 아니라 **복사한 것 자체가 결함**이다 —
이 저장소가 **열한 번 겪은 결함 양식**(같은 수를 두 곳에 적고 한쪽만 고친다)이다.
그래서 값을 새 수로 갈아 덮지 않고 **정본을 가리키고 사본을 없앴다**(v3 `R3-b` 규율).

### 🔴 정본 대장 — 값을 이 문서에 박지 않는다

| 값 | 정본 | 사이트가 얻는 경로 |
|---|---|---|
| 성적(그리드·정보한계·회귀·전체·밸류체인·골든 근거) | `artifacts/ablation/v4/summary.json` → 없으면 `v3/summary.json` | **빌드가 읽는다** |
| 문항별 채점·단계 귀속·근본 원인 | `artifacts/ablation/v4/A8/report.json` → 없으면 `v3/A8/report.json` | **빌드가 읽는다** |
| 누적 지출 · Phase 별 게이트/크레딧 · 테스트 수 | [`docs/total_plan.md`](../../total_plan.md) §1 | **빌드가 마크다운을 파싱한다** (§Task 7·10) |
| arm 판정(채택·기각) 대장 | [`docs/pipeline/09-ablation.md`](../../pipeline/09-ablation.md) `§I-5f ⑧` | 빌드가 읽지 않는다 — **링크만 낸다** |
| 엔드포인트·공인 IP·배포 함정 | [`deploy/README.md`](../../../deploy/README.md) 첫 절 | 링크만 |
| 주최측 공지 전사·영향 | [`docs/organizer-qa.md`](../../organizer-qa.md) | 링크만 |
| 잔여 실패 문항 목록 | v4 계획서 `§12 현재 상태` + 위 report.json | 빌드가 report.json 에서 센다 |

**규칙 세 줄.**
1. 🔴 **숫자를 이 계획서·TSX·Python 상수에 적지 않는다.** 화면의 모든 수는 산출물 또는
   정본 마크다운 파싱에서 온다. 어길 수 있는 유일한 곳은 **분모 라벨과 문턱값**이다
   (예: "그리드 72", "게이트 D 문턱 0.85" — 이것들은 측정값이 아니라 정의다).
2. 🔴 **정본 마크다운을 파싱한 값은 검산까지 파싱한다.** 지출은 표의 행 합과 `누적` 행을
   둘 다 읽어 **일치하지 않으면 빌드를 깬다.** 사본이 아니라 **미러**여야 한다.
3. 🔴 **세대 라벨을 값에 붙인다.** `summary.json` 의 `meta.generated` 와 `argusCommit` 을
   모든 화면 하단에 렌더한다. 라벨 없는 수치가 낡아서 이 저장소가 이미 한 번 오판했다.

### 🔴 세 세대를 지나며 정정된 서술 — **문장을 그대로 쓴다**

| 종전 서술 (이 계획서에 있던 것) | 🔴 정정 |
|---|---|
| "엣지 밀도 12.4% → **GraphRAG 기각**" | **"기업—기업 엣지 정의 기각"**. GraphRAG 전제는 기각되지 않았다. 노드·엣지를 코퍼스에 맞게 재정의한 쪽은 **채택**됐고 성적의 대부분이 거기서 나온다 — `doc_subtype` 메타 축이 `A1→A1b` **+41.7%p** 로 **사다리 최대 전이**다 (`total_plan §7` · `03-graph-schema.md:68`) |
| "A5 이후 Δ 가 0" | 꺾이는 지점은 **A4** 다. A4=A5=A6=A7=A8 (`total_plan §0`) |
| "A3(리랭커)는 보류" | **최종 기각** — 겨냥 0 을 세 분모 전부에서 확인, 게이트 E 3/3 으로 닫혔다 |
| "잔여 실패 13 · 11 · 12" | 세대마다 바뀐다. **빌드가 report.json 에서 센다** (v4 R0 시점 그리드 6건) |
| "제출물 3종" | **4종** — 전처리 산출물이 추가됐다(주최측 #10). `Dockerfile` 은 **필수 아님**(#11) |
| "heldout 15/18 을 헤드라인에 싣는다" | 🔴 **싣지 않는다.** 낙폭 게이트는 **은퇴**했고 그 원장은 옛 세대와 짝지어진 **동결 값**이다. 새 heldout 은 v4 `R4-1` 이 만든다 |

### 🔴 실행 범위 — Task 1~10 은 **제출 후로 이월한다**

```
제출물 4종에 웹사이트는 없다          → 제출을 위험에 빠뜨릴 수 없다
미완 108 / 완료 0 · D-23             → 코드 동결 08-25 까지 11일
평가 서버 c2-g3a 2 vCPU / 4 GB       → 🔴 웹사이트를 그 서버에 올리지 않는다
```

- **`R6-MVP`(§R6)** — 이 문서 맨 아래 절. **정적 HTML 5쪽 · Node/npm 0 · 크레딧 0원 ·
  LLM 0회.** 여기까지가 v4 `R6` 의 실행 범위다.
- **Task 1~10 (Next.js 판)** — 🔴 **제출 후(09-21 이후) 또는 폐기.** 삭제하지 않는 이유는
  설계와 실측 확인이 들어 있어서다. **실행하려면 §0 의 정본 대장을 먼저 적용해야 한다.**
- 🔴 **호스팅은 `223.130.129.131` 이 아니다.** 그 서버는 평가 전용이고, 09-07~09-20 에
  같은 2 vCPU 를 웹서버와 나누면 **단건 지연이 평가 기준일 수 있다**(주최측 #1 sequential ·
  타임아웃 300초). MVP 산출물은 **정적 파일이므로 저장소·GitHub Pages·로컬로 낸다.**

---

## Global Constraints

프로젝트 전체에 걸리는 요구사항이다. **모든 Task 의 요구사항에 이 절이 암묵적으로 포함된다.**

- **레퍼런스 충실도.** `scratch/ARGUS_example` 의 구조를 최대한 따른다: `app/` 라우트 + `components/` + `lib/` + `public/data/site_*.json`, 순수 CSS 토큰(`:root` 커스텀 프로퍼티), 시맨틱 클래스명(`.card` `.badge` `.lead` `.section-heading` `.table` `.panel` `.grid` `.grid-4` `.eyebrow` `.helper` `.metric`), `Header` 의 드롭다운 내비게이션, 히어로 + 검색 랜딩.
- **차트 라이브러리 금지.** 레퍼런스와 동일하게 `div` + CSS 로 막대·히트맵을 그린다. `react-simple-maps` 같은 지도 의존성도 넣지 않는다 (ARGUS 에는 지도가 없다).
- **Tailwind 금지.** 모든 스타일은 `site/app/globals.css` 한 파일에 둔다.
- **다크모드 비대상.** 레퍼런스가 라이트 전용이므로 동일하게 라이트 전용으로 간다. `prefers-color-scheme` 블록을 쓰지 않는다.
- **UI 문구는 전부 한국어.** 지표 이름(`Recall@5`, `hit rate`, `rank IC`)과 arm 코드(`A0`~`A8`, `L1`~`L3`)는 영문·코드 그대로 둔다.
- **버전 고정.** `next@^15.5.0` · `react@^19.0.0` · `react-dom@^19.0.0` · `typescript@^5.6.0` · `@types/node@^22` · `@types/react@^19` · `@types/react-dom@^19`. 레퍼런스의 `"latest"` 는 재현이 안 되므로 따르지 않는다. 레퍼런스가 겪은 Turbopack panic 은 Next 15 의 기본 webpack dev 서버로 회피한다.
- 🔴 **숫자를 하드코딩하지 않는다 — TSX 도, Python 상수도.** 화면에 보이는 모든 수치는 `site/public/data/*.json` 에서 오고, 그 JSON 의 모든 수치는 **산출물(`artifacts/`) 또는 정본 마크다운 파싱**에서 온다. 이 규칙이 `docs/total_plan.md` §0 의 "값을 갈아 덮으면 이력이 사라진다" 와 짝이다.
  - 🔴 **빌드 스크립트의 `X = 12846.7` 같은 리터럴이 이 계획서의 원래 결함이었다.** 문서에서 옮겨 적은 값은 **다음 라운드에 낡는다.** 옮겨 적는 대신 **정본 파일을 읽어라** (§0 정본 대장).
  - 허용되는 리터럴은 **분모 라벨·문턱값·정의**뿐이다 (예: `"그리드 72"`, 게이트 D 문턱 `0.85`, 낙폭 임계 `−10%p`). 측정값은 하나도 허용되지 않는다.
  - 🔴 **테스트도 값을 못박지 않는다.** `assert sum(...) == 11` 형태는 금지다 — **자기일관성**으로 쓴다(`sum(실패) == n_grid − correct`). 값 대조는 산출물 대 산출물로만 한다.
- **모든 생성 JSON 은 `metadata` 블록을 갖는다.** 필수 키: `generated`(ISO8601) · `sourceFiles`(읽은 파일 경로 배열) · `argusCommit`(git rev-parse --short HEAD) · `note`(그 파일이 무엇인지 한 줄). 레퍼런스의 `site_*.json` 이 `metadata.sourceFiles` 로 출처를 들고 다니는 것과 같다.
## 🔴 보안 — 웹으로 나가면 되돌릴 수 없는 것 다섯

**발행은 비가역이다.** 아래 다섯은 Task 2 의 테스트가 생성 JSON 전량을 훑어 강제하고,
어느 하나라도 걸리면 **빌드가 깨진다**(경고가 아니라 `ValueError`).

- 🔴 **① `.env` · `CLOVA_API_KEY` 를 `site/` 아래에 절대 복사하지 않는다.**
  빌드 스크립트는 `settings()` 를 **import 하지 않고**, 환경변수를 **읽지 않는다** —
  키가 메모리에 들어오지 않으면 JSON 으로 새어 나갈 경로 자체가 없다.
  생성 JSON 을 `nv-`·`sk-`·`Bearer ` 접두와 40자 이상 base64/hex 토큰 패턴으로 훑는다.
  🔴 **`deploy/README.md` 의 `scp .env` 절차를 사이트 문서에 옮겨 적지 않는다** — 절차서가 정본이다.
- 🔴 **② 코퍼스 원문(5.55GB)은 재배포 대상이 아니다.** 주최측 제공물이고 우리 것이 아니다.
  근거 표시는 **산출물의 `retrieved_context` 에 이미 들어 있는 발췌만** 쓰고,
  발췌 길이 상한(문서당 1,200자)을 빌드에서 자른다. `data/` 아래를 읽는 코드를 두지 않는다.
- 🔴 **③ `evalset/heldout.jsonl` 과 `artifacts/ablation/*/heldout*/` 은 읽지 않는다.**
  18문항은 **최종 구성 1회용이고 2회 다 썼다.** 질문 전문·정답·`golden` 접수번호·`golden_rank`
  가 **한 글자도** 생성 JSON 에 들어가지 않는다. 근거: `docs/total_plan.md` §0·§5 ·
  `artifacts/ablation/v2/heldout/README-DO-NOT-TUNE.md`.
  🔴 **집계 수치(`15/18`)조차 싣지 않는다** — 낙폭 게이트는 **은퇴**했고 그 원장은 옛 세대와
  짝지어진 **동결 값**이다. 싣으면 새 heldout(v4 `R4-1`)이 나오는 순간 낡는다. **링크만 낸다.**
- 🔴 **④ `evalset/questions.jsonl`(+`regression`·`valuechain`)의 질문 전문과 정답을 싣지 않는다.**
  🔴 **이것이 2026-08-14 재검토에서 새로 막은 항목이다** — 종전 계획은 `/`(예시 문항 6개) ·
  `/search`(100문항 전문) · `/answers`(질문+답변)에서 **질문 전문을 그대로 발행**하게 되어 있었다.
  이유 셋: ① 정답은 **우리가 만든 라벨**이고 `total_plan §5` 가 **채점 결함 셋**을 명시한다
  (`Q-110` 10× 환산이 judge 를 통과했다 — 즉 우리 라벨에 부풀림이 있다), ② 문항·정답은
  **검색·랭킹 튜닝이 가능한 형태의 라벨**이고 heldout 폴더에 그 경고가 이미 박혀 있다,
  ③ 새 heldout(`R4-1`)을 만들 때 dev 문항이 공개돼 있으면 셀 설계가 오염된다.
  **싣는 것**: `question_id` · 축 메타(`difficulty`·`doc_group`·`task_type`·`answer_mode`·
  `track`·`answerable`) · 채점 결과(`correct`·`first_failure`) — **질문 문자열이 아닌 것들.**
- 🔴 **⑤ `golden`·`golden_rank`·`answer`·`think_trace` 를 문항 단위로 싣지 않는다.**
  `artifacts/ablation/*/A8/report.json` 의 `rows` 는 **44필드**이고 그 안에 `golden`(접수번호)·
  `golden_rank`·`answer`·`think_trace` 가 있다. 🔴 **v2 의 `error_analysis_v2.json` 에서
  v3/v4 의 `report.json` 으로 원천을 옮기면 노출면이 커진다** — 빌드는 **화이트리스트 방식**으로
  필드를 고른다(블랙리스트 금지. 다음 세대에 새 필드가 붙으면 블랙리스트는 조용히 샌다).
  `answer` 는 질문을 되풀어 쓰므로 ④ 의 우회로다 — **답변 전문도 싣지 않는다.**
- **`artifacts/` 는 gitignore 대상이다.** 따라서 빌드 스크립트의 출력물 `site/public/data/*.json` 은 **커밋한다** (레퍼런스도 그렇게 한다). 산출물이 없는 클론에서도 사이트가 빌드돼야 한다.
- **검증 방식.** 사이트 코드는 `npx tsc --noEmit` + `npm run build`(프리렌더가 타입·런타임 오류를 잡는다)로 검증한다. **Jest·Vitest 를 도입하지 않는다** — 레퍼런스에 테스트가 없고, 로직은 전부 Python 쪽에 있어 `pytest` 가 이미 덮는다.
- **커밋 메시지는 한국어 + conventional prefix.** 기존 이력과 같은 형식(`feat:` `fix:` `docs:` `measure:`).

---

## File Structure

### 새로 만드는 것 — 사이트

| 경로 | 책임 |
|---|---|
| `site/package.json` | 의존성 고정, `dev`/`build`/`start`/`typecheck` 스크립트 |
| `site/next.config.ts` | 최소 설정 |
| `site/tsconfig.json` | Next 기본 + `strict: true` |
| `site/.gitignore` | `node_modules` · `.next` |
| `site/app/globals.css` | **디자인 토큰 + 전체 스타일 단일 파일** (레퍼런스와 동일 구조) |
| `site/app/layout.tsx` | `<html lang="ko">` + `Header` + metadata |
| `site/app/page.tsx` | 홈 — 히어로 + 질의 검색 |
| `site/app/answers/page.tsx` | **질의응답 트레이스** (근거 → 추론 → 답변). 이 사이트의 주력 화면 |
| `site/app/search/page.tsx` | 상세 검색 — 평가셋 문항 **메타** 탐색 (🔴 문항 수를 여기 적지 않는다. 빌드가 센다. 🔴 질문 전문 금지 — 보안 ④) |
| `site/app/ablation/page.tsx` | 기여도 사다리 A0→A8 + LOO + 비용 원장 |
| `site/app/errors/page.tsx` | 실패 해부 — 6단계 귀속 × 3축 |
| `site/app/corrections/page.tsx` | 정정 체인 |
| `site/app/counterparty/page.tsx` | 발주처 밸류체인 |
| `site/app/pipeline/page.tsx` | 파이프라인 Phase A~I (레퍼런스 `/methodology` 대응) |
| `site/app/data/page.tsx` | 데이터·산출물 — 코퍼스 프로파일 · 출처 · 다운로드 |
| `site/components/Header.tsx` | 드롭다운 내비 (레퍼런스 그대로 이식) |
| `site/components/StatCard.tsx` | 지표 카드 (레퍼런스 `ScoreCard` 대응) |
| `site/components/LandingSearch.tsx` | 홈 검색 입력 → `/search?query=` |
| `site/components/TraceViewer.tsx` | 문항 선택 + 트레이스 단계 렌더 (클라이언트) |
| `site/components/QuestionExplorer.tsx` | 문항 필터·검색 (클라이언트) |
| `site/components/LadderBars.tsx` | arm 별 정답률 막대 (서버 컴포넌트, CSS 막대) |
| `site/components/Heatmap.tsx` | 축 × arm 히트맵 (서버 컴포넌트) |
| `site/lib/pipeline.ts` | Phase A~I 방법론 본문 (레퍼런스 `lib/methodology.ts` 대응) |
| `site/public/data/site_*.json` | **생성물, 커밋함** |

### 새로 만드는 것 — 빌드

| 경로 | 책임 |
|---|---|
| `scripts/build_site_data.py` | `artifacts/` + `evalset/` → `site/public/data/*.json`. LLM 호출 0회 |
| `tests/test_site_data.py` | 스키마 · **heldout 유출 게이트** · 트레이스 파서 단위 테스트 |

### 손대지 않는 것

`src/argus/**` · `evalset/**` · `artifacts/**` 는 **읽기만** 한다. 사이트 작업이 파이프라인 코드나 평가셋을 수정하는 일은 없다.

---

## 데이터 원천 — 어느 파일이 어느 화면을 먹이나

🔴 **원천이 세대를 탄다. 경로를 문서에 박지 않고 `resolve_generation()` 이 고른다.**
v2 → v3 → v4 로 세대가 올라갈 때마다 이 표를 손으로 고치면 그것이 열두 번째 하드코딩이다.

```python
# scripts/build_site_data.py — 세대 해석은 한 곳에서만 한다
GENERATIONS = ("v4", "v3", "v2")   # 🔴 앞에서부터 존재하는 첫 세대를 쓴다

def resolve_generation() -> str:
    for gen in GENERATIONS:
        if (REPO / "artifacts" / "ablation" / gen / "summary.json").exists():
            return gen
    raise FileNotFoundError("ablation 산출물이 없다 — 사이트 데이터를 만들 수 없다")
```

| 원천 | 무엇이 들어 있나 | 먹이는 화면 |
|---|---|---|
| `artifacts/ablation/{gen}/summary.json` | `meta`(`generated`·`n_cases`·`price_won_per_1k`·`index_snapshot`) + `summary.arms` · `summary.axes` · `summary.cells` | `/ablation` · 홈 |
| `artifacts/ablation/{gen}/A8/report.json` | `rows`(88문항 × **44필드**) + `failure_histogram_grid` + `root_cause_histogram_grid` + `by_doc_group_grid` + `totals` | `/errors` · `/search` |
| `artifacts/ablation/{gen}/vc/A8/report.json` | 밸류체인 12문항 | `/counterparty` |
| `docs/total_plan.md` **§1** | 🔴 **누적 지출 표 + `누적` 행 + Phase 별 게이트/크레딧 + 게이트 총계 + 테스트 수** — 사이트가 파싱한다 | `/ablation`(원장) · `/pipeline` · `/data` |
| `artifacts/eval/recall_v2.json` | 코호트별 Recall@5, 난이도·문서군 분해, 게이트 D 판정 | `/data` |
| `artifacts/eda/doc_profile.json` | `corpus_bytes`(문서군별 MB·비중) + `meta.sampling` | `/data` |
| `artifacts/eda/relation_profile.json` | `correction_chains`(문서군별 체인 길이 분포) + `corp_to_corp_edge_density` | `/corrections` |
| `artifacts/counterparty_graph.json` | `nodes` · `pairs` · `stats` | `/counterparty` |
| `evalset/{questions,regression,valuechain}.jsonl` | 🔴 **축 메타만** (`difficulty`·`task_type`·`answer_mode`·`answerable`). **`question` 필드는 읽고 버린다** — 보안 ④ | `/search` |

🔴 **`artifacts/eval/error_analysis_v2.json` 을 성적·단계 귀속의 원천으로 쓰지 않는다.**
`total_plan §5` 가 그 이유를 박아 뒀다 — 그 파일은 재생성되지 않았고 **옛 라벨(`VERIFY` 3)을
들고 있다.** 현 정본은 `artifacts/ablation/{gen}/A8/report.json` 이다. (다만 `recall_v2.json` 은
게이트 D 의 정본이라 그대로 쓴다 — 이름의 `v2` 는 ablation 세대와 무관하다.)

🔴 **읽지 않는 것 (하드 게이트 · 보안 ②③):**
`evalset/heldout.jsonl` · `artifacts/ablation/*/heldout*/` · `data/` 아래 코퍼스 원문 전체.
`artifacts/ablation/v2/heldout/` 는 커밋돼 있어서 읽기는 쉽지만 **경로 조각이 소스에 나타나면
테스트가 깨진다.**

🔴 **`report.json > rows` 는 화이트리스트로만 뽑는다.** 그 44필드에는 `golden` ·
`golden_rank` · `answer` · `think_trace` 가 들어 있다 (보안 ⑤).

```python
ROW_FIELDS_ALLOWED = (
    "question_id", "task_type", "answer_mode", "difficulty", "doc_group", "track",
    "answerable", "answer_form", "correct", "scored_by", "first_failure",
    "root_cause", "failure_why", "critic_supported", "critic_passed_wrong_answer",
    "has_citation", "route", "route_group", "n_hops", "n_steps", "n_llm_calls",
    "data_limit", "calc_checked", "calc_mismatch",
)
# 🔴 여기에 없는 필드는 존재조차 사이트로 나가지 않는다. 블랙리스트를 쓰지 않는 이유는
#    다음 세대에 새 필드가 붙으면 블랙리스트가 조용히 새기 때문이다.
#
# 🔴 `corp_name`·`sector` 는 **일부러 뺐다.** 질문 전문은 아니지만 문항 100건에 대해
#    `(문서군, 난이도, 기업명)` 을 함께 내면 **평가셋을 부분 복원할 수 있다**. Task 6
#    (이월분)이 이 둘을 쓰려면 그 판단을 다시 하고 여기 사유를 적어라 — 지금은 없는 쪽이 정본이다.
# 🔴 `failure_why` 는 사람이 쓴 자유 서술이라 골든 값이 인용돼 있을 수 있다.
#    싣기 전에 `숫자 6자리 이상` 패턴을 마스킹한다 (Task 8 이 그 함수를 만든다).
```

---

### Task 1: 사이트 스캐폴드 — 토큰 · 레이아웃 · 헤더

**Files:**
- Create: `site/package.json`
- Create: `site/next.config.ts`
- Create: `site/tsconfig.json`
- Create: `site/.gitignore`
- Create: `site/app/globals.css`
- Create: `site/app/layout.tsx`
- Create: `site/app/page.tsx`
- Create: `site/components/Header.tsx`
- Create: `site/components/StatCard.tsx`

**Interfaces:**
- Consumes: 없음 (첫 Task)
- Produces:
  - CSS 클래스 어휘: `.container` `.container.narrow` `.nav` `.nav-inner` `.logo` `.links` `.nav-dropdown` `.nav-dropdown-trigger` `.nav-dropdown-menu` `.hero` `.landing-hero` `.landing-hero-inner` `.section` `.section-heading` `.section-heading.compact` `.badge` `.lead` `.helper` `.eyebrow` `.metric` `.card` `.metric-card` `.panel` `.grid` `.grid-2` `.grid-3` `.grid-4` `.table` `.table-wrap` `.btn` `.btn-primary` `.chip` `.empty-note` `.spacious` `.subhead`
  - `StatCard` 컴포넌트: `{ label: string; value: string | number; helper?: string }`
  - `Header` 컴포넌트: props 없음

- [ ] **Step 1: `site/package.json` 작성**

```json
{
  "name": "argus-site",
  "version": "1.0.0",
  "private": true,
  "scripts": {
    "dev": "next dev",
    "build": "next build",
    "start": "next start",
    "typecheck": "tsc --noEmit"
  },
  "dependencies": {
    "next": "^15.5.0",
    "react": "^19.0.0",
    "react-dom": "^19.0.0"
  },
  "devDependencies": {
    "@types/node": "^22.0.0",
    "@types/react": "^19.0.0",
    "@types/react-dom": "^19.0.0",
    "typescript": "^5.6.0"
  }
}
```

- [ ] **Step 2: `site/next.config.ts` · `site/tsconfig.json` · `site/.gitignore` 작성**

`site/next.config.ts`:

```ts
import type { NextConfig } from "next";

const nextConfig: NextConfig = {};

export default nextConfig;
```

`site/tsconfig.json`:

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "lib": ["dom", "dom.iterable", "esnext"],
    "allowJs": false,
    "skipLibCheck": true,
    "strict": true,
    "noEmit": true,
    "esModuleInterop": true,
    "module": "esnext",
    "moduleResolution": "bundler",
    "resolveJsonModule": true,
    "isolatedModules": true,
    "jsx": "preserve",
    "incremental": true,
    "plugins": [{ "name": "next" }]
  },
  "include": ["next-env.d.ts", "**/*.ts", "**/*.tsx", ".next/types/**/*.ts"],
  "exclude": ["node_modules"]
}
```

`site/.gitignore`:

```text
node_modules/
.next/
next-env.d.ts
```

- [ ] **Step 3: `site/app/globals.css` 작성 — 디자인 토큰과 공용 프리미티브**

레퍼런스 `globals.css` 의 토큰 구조를 그대로 쓰되, 색을 공시·규제 문서 성격에 맞게 잡는다. `--amber` 는 정정, `--danger` 는 실패 단계에 쓴다.

```css
:root {
  --bg: #f6f7f9;
  --surface: #ffffff;
  --surface-strong: #eef1f8;
  --text: #141a26;
  --muted: #626d7f;
  --line: #dbe0e8;
  --primary: #2b4bb8;
  --primary-dark: #1a2f7d;
  --teal: #0f7f76;
  --amber: #a86a10;
  --danger: #b3382f;
  --ink-soft: #22304a;
}

* {
  box-sizing: border-box;
}

html {
  scroll-behavior: smooth;
}

body {
  margin: 0;
  background: var(--bg);
  color: var(--text);
  font-family: "Pretendard", "Malgun Gothic", Arial, Helvetica, sans-serif;
  line-height: 1.6;
}

a {
  color: inherit;
  text-decoration: none;
}

.container {
  max-width: 1180px;
  margin: 0 auto;
  padding: 0 24px;
}

.container.narrow {
  max-width: 940px;
}

/* ── 내비게이션 ─────────────────────────────────────── */
.nav {
  position: sticky;
  top: 0;
  z-index: 20;
  height: 68px;
  border-bottom: 1px solid var(--line);
  background: rgba(255, 255, 255, 0.93);
  backdrop-filter: blur(10px);
}

.nav-inner {
  height: 68px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 24px;
}

.logo {
  font-weight: 800;
  letter-spacing: 0.02em;
  color: var(--ink-soft);
}

.logo span {
  margin-left: 8px;
  font-size: 12px;
  font-weight: 700;
  color: var(--muted);
}

.links {
  display: flex;
  align-items: center;
  gap: 14px;
  color: var(--muted);
  font-size: 14px;
  font-weight: 700;
}

.links a:hover,
.nav-dropdown-trigger:hover {
  color: var(--primary-dark);
}

.links > a {
  min-height: 36px;
  display: inline-flex;
  align-items: center;
  border-radius: 999px;
  padding: 0 12px;
}

.links > a.is-active {
  border: 1px solid #c3d0f2;
  background: #eef2ff;
  color: var(--primary-dark);
}

.nav-dropdown {
  position: relative;
}

.nav-dropdown::after {
  content: "";
  position: absolute;
  left: 0;
  right: 0;
  top: 100%;
  height: 12px;
}

.nav-dropdown-trigger {
  min-height: 36px;
  border: 1px solid transparent;
  border-radius: 999px;
  background: transparent;
  color: var(--muted);
  cursor: pointer;
  font: inherit;
  font-weight: 800;
  padding: 0 14px;
}

.nav-dropdown-trigger.is-active {
  border-color: var(--primary);
  background: var(--primary);
  color: #ffffff;
}

.nav-dropdown-menu {
  position: absolute;
  right: 0;
  top: calc(100% + 8px);
  display: grid;
  min-width: 200px;
  padding: 8px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: #ffffff;
  box-shadow: 0 18px 40px rgba(15, 25, 55, 0.14);
  opacity: 0;
  pointer-events: none;
  transform: translateY(-4px);
  transition: opacity 0.15s ease, transform 0.15s ease;
}

.nav-dropdown:hover .nav-dropdown-menu,
.nav-dropdown:focus-within .nav-dropdown-menu {
  opacity: 1;
  pointer-events: auto;
  transform: translateY(0);
}

.nav-dropdown-menu a {
  padding: 10px 11px;
  border-radius: 6px;
  color: var(--ink-soft);
}

.nav-dropdown-menu a:hover {
  background: #eef2ff;
}

.nav-dropdown-menu a.is-active {
  background: var(--primary);
  color: #ffffff;
}

/* ── 히어로 · 섹션 ──────────────────────────────────── */
.hero {
  padding: 64px 0 48px;
  background: linear-gradient(180deg, #ffffff 0%, var(--bg) 100%);
}

.landing-hero {
  min-height: calc(100vh - 68px);
  display: grid;
  align-items: center;
  padding: 64px 0;
  background: linear-gradient(180deg, #ffffff 0%, #eef2ff 100%);
}

.landing-hero-inner {
  max-width: 860px;
}

.section {
  padding: 56px 0 72px;
}

.section-heading {
  max-width: 880px;
  margin-bottom: 28px;
}

.section-heading.compact {
  margin-bottom: 16px;
}

.section-heading h1 {
  margin: 12px 0 10px;
  font-size: 34px;
  line-height: 1.28;
}

.section-heading h2 {
  margin: 10px 0 8px;
  font-size: 22px;
}

.badge {
  display: inline-flex;
  align-items: center;
  border: 1px solid #c3d0f2;
  border-radius: 999px;
  background: #eef2ff;
  padding: 5px 12px;
  color: var(--primary-dark);
  font-size: 12px;
  font-weight: 800;
}

.lead {
  margin: 0;
  color: var(--muted);
  font-size: 16px;
}

.eyebrow {
  margin: 0 0 6px;
  color: var(--muted);
  font-size: 12px;
  font-weight: 800;
  letter-spacing: 0.04em;
}

.helper {
  margin: 8px 0 0;
  color: var(--muted);
  font-size: 13px;
}

.subhead {
  margin: 20px 0 10px;
  font-size: 15px;
}

.metric {
  font-size: 30px;
  font-weight: 800;
  color: var(--ink-soft);
  font-variant-numeric: tabular-nums;
}

/* ── 카드 · 그리드 ──────────────────────────────────── */
.card,
.panel {
  border: 1px solid var(--line);
  border-radius: 10px;
  background: var(--surface);
}

.card {
  padding: 18px;
}

.panel {
  padding: 22px;
}

.panel.spacious {
  margin-top: 28px;
}

.grid {
  display: grid;
  gap: 16px;
}

.grid-2 {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

.grid-3 {
  grid-template-columns: repeat(3, minmax(0, 1fr));
}

.grid-4 {
  grid-template-columns: repeat(4, minmax(0, 1fr));
}

/* ── 표 ─────────────────────────────────────────────── */
.table-wrap {
  overflow-x: auto;
}

.table {
  width: 100%;
  border-collapse: collapse;
  font-size: 14px;
}

.table th,
.table td {
  border-bottom: 1px solid var(--line);
  padding: 10px 12px;
  text-align: left;
  vertical-align: top;
}

.table th {
  background: var(--surface-strong);
  color: var(--ink-soft);
  font-weight: 800;
  white-space: nowrap;
}

.table td.num,
.table th.num {
  text-align: right;
  font-variant-numeric: tabular-nums;
}

.table td span {
  display: block;
  color: var(--muted);
  font-size: 12px;
}

/* ── 버튼 · 칩 ──────────────────────────────────────── */
.btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-height: 44px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: #ffffff;
  padding: 0 18px;
  color: var(--ink-soft);
  cursor: pointer;
  font: inherit;
  font-weight: 800;
}

.btn-primary {
  border-color: var(--primary);
  background: var(--primary);
  color: #ffffff;
}

.chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  border: 1px solid var(--line);
  border-radius: 999px;
  background: var(--surface-strong);
  padding: 4px 11px;
  color: var(--ink-soft);
  font-size: 12px;
  font-weight: 700;
}

.empty-note {
  margin: 0;
  padding: 24px;
  border: 1px dashed var(--line);
  border-radius: 8px;
  color: var(--muted);
  text-align: center;
}

/* ── 푸터 ───────────────────────────────────────────── */
.footer {
  border-top: 1px solid var(--line);
  padding: 28px 0;
  color: var(--muted);
  font-size: 13px;
}

/* ── 반응형 ─────────────────────────────────────────── */
@media (max-width: 900px) {
  .grid-4 {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .grid-3 {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 640px) {
  .grid-2,
  .grid-3,
  .grid-4 {
    grid-template-columns: minmax(0, 1fr);
  }
  .section-heading h1 {
    font-size: 26px;
  }
  .links {
    gap: 8px;
    font-size: 13px;
  }
}
```

- [ ] **Step 4: `site/components/Header.tsx` 작성**

레퍼런스 `Header.tsx` 를 구조 그대로 옮기고 메뉴만 ARGUS 것으로 바꾼다.

```tsx
"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const analysisItems = [
  { href: "/answers", label: "질의응답 트레이스" },
  { href: "/ablation", label: "기여도 사다리" },
  { href: "/errors", label: "실패 해부" },
  { href: "/corrections", label: "정정 체인" },
  { href: "/counterparty", label: "발주처 밸류체인" },
];

const navItems = [
  { href: "/search", label: "상세 검색" },
  { href: "/pipeline", label: "파이프라인" },
  { href: "/data", label: "데이터" },
];

function isActive(pathname: string, href: string) {
  return pathname === href || pathname.startsWith(`${href}/`);
}

export default function Header() {
  const pathname = usePathname();
  const analysisActive = analysisItems.some((item) => isActive(pathname, item.href));

  return (
    <header className="nav">
      <div className="container nav-inner">
        <Link href="/" className="logo">
          ARGUS<span>DART 공시 QA Agent</span>
        </Link>
        <nav className="links" aria-label="주요 메뉴">
          <div className="nav-dropdown">
            <button
              type="button"
              className={`nav-dropdown-trigger ${analysisActive ? "is-active" : ""}`}
              aria-haspopup="true"
            >
              분석
            </button>
            <div className="nav-dropdown-menu">
              {analysisItems.map((item) => (
                <Link href={item.href} key={item.href} className={isActive(pathname, item.href) ? "is-active" : ""}>
                  {item.label}
                </Link>
              ))}
            </div>
          </div>
          {navItems.map((item) => (
            <Link href={item.href} key={item.href} className={isActive(pathname, item.href) ? "is-active" : ""}>
              {item.label}
            </Link>
          ))}
        </nav>
      </div>
    </header>
  );
}
```

- [ ] **Step 5: `site/components/StatCard.tsx` 작성**

```tsx
type Props = {
  label: string;
  value: string | number;
  helper?: string;
};

export default function StatCard({ label, value, helper }: Props) {
  return (
    <article className="card metric-card">
      <p className="eyebrow">{label}</p>
      <div className="metric">{value}</div>
      {helper ? <p className="helper">{helper}</p> : null}
    </article>
  );
}
```

- [ ] **Step 6: `site/app/layout.tsx` 작성**

```tsx
import type { Metadata } from "next";
import "./globals.css";
import Header from "../components/Header";

export const metadata: Metadata = {
  title: "ARGUS — DART 공시 QA Agent",
  description: "거래소·주요사항·지분·정기공시를 정규화·색인해 근거와 함께 답하는 공시 질의응답 Agent",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ko">
      <body>
        <Header />
        {children}
        <footer className="footer">
          <div className="container">
            ARGUS · DART 공시 QA Agent · 모든 수치는 <code>artifacts/</code> 실측에서 생성됩니다
          </div>
        </footer>
      </body>
    </html>
  );
}
```

- [ ] **Step 7: `site/app/page.tsx` 임시 홈 작성 (Task 4 에서 데이터로 교체)**

```tsx
export default function Home() {
  return (
    <main>
      <section className="landing-hero">
        <div className="container landing-hero-inner">
          <span className="badge">ARGUS</span>
          <h1>공시 질문에 근거와 함께 답한다</h1>
          <p className="lead">스캐폴드 단계입니다. 홈 콘텐츠는 Task 4 에서 붙습니다.</p>
        </div>
      </section>
    </main>
  );
}
```

- [ ] **Step 8: 의존성 설치와 타입체크**

Run:
```bash
cd site && npm install && npm run typecheck
```
Expected: 설치 성공, `tsc --noEmit` 오류 0건.

- [ ] **Step 9: 빌드가 통과하는지 확인**

Run:
```bash
cd site && npm run build
```
Expected: `Compiled successfully`. 라우트 목록에 `/` 가 뜬다.

- [ ] **Step 10: 개발 서버로 헤더 드롭다운 육안 확인**

Run:
```bash
cd site && npm run dev
```
Expected: `http://localhost:3000` 에서 상단에 `ARGUS DART 공시 QA Agent` 로고, `분석 ▾` 호버 시 5개 항목 드롭다운, `상세 검색`·`파이프라인`·`데이터` 링크가 보인다. 확인 후 서버 종료.

- [ ] **Step 11: 커밋**

```bash
git add site/package.json site/package-lock.json site/next.config.ts site/tsconfig.json site/.gitignore site/app site/components
git commit -m "feat: ARGUS 사이트 스캐폴드 — 디자인 토큰·레이아웃·헤더"
```

---

### Task 2: 빌드 스크립트 기반 — provenance metadata 와 heldout 유출 게이트

이 Task 가 **안전 장치**다. 나머지 모든 Task 가 여기서 만든 헬퍼를 통해 JSON 을 쓴다.

**Files:**
- Create: `scripts/build_site_data.py`
- Create: `tests/test_site_data.py`

**Interfaces:**
- Consumes: Task 1 의 `site/public/data/` 디렉터리 위치
- Produces:
  - `scripts/build_site_data.py` 의 공개 함수:
    - `REPO: Path` — 저장소 루트
    - `OUT_DIR: Path` — `REPO / "site" / "public" / "data"`
    - `FORBIDDEN_PATHS: tuple[str, ...]` — 읽으면 안 되는 경로 조각 (`"heldout"` · `"data/"`)
    - `GENERATIONS: tuple[str, ...]` · `resolve_generation() -> str` · `resolve_ladder_generation() -> str`
    - `ROW_FIELDS_ALLOWED: tuple[str, ...]` — 🔴 `report.json > rows` 화이트리스트 (보안 ⑤)
    - `SSOT_MD: str` · `parse_ssot() -> dict` — 🔴 **정본 마크다운 파서.** 지출·게이트·테스트·Phase
    - `count_tool_specs() -> int` — `ast` 로 `tools.py` 를 센다 (**import 금지**)
    - `_mask_amounts(text: str) -> str` — 자유 서술의 6자리 이상 숫자 마스킹 (보안 ⑤)
    - `argus_commit() -> str`
    - `metadata(note: str, source_files: list[str]) -> dict` — `{"generated","argusCommit","sourceFiles","note"}`
    - `read_json(rel: str) -> dict` — `artifacts/`·`evalset/` 상대경로 읽기. `FORBIDDEN_PATHS` 에 걸리면 `ValueError`
    - `read_jsonl(rel: str) -> list[dict]`
    - `write_site_json(name: str, payload: dict) -> Path` — `payload["metadata"]` 필수 검사 후 `OUT_DIR / name` 에 저장
    - `build_meta() -> dict` — `site_meta.json` 페이로드
    - `main(argv: list[str] | None = None) -> int` — CLI

- [ ] **Step 1: 실패하는 테스트를 먼저 쓴다**

`tests/test_site_data.py`:

```python
"""사이트 데이터 빌드 — 스키마와 heldout 유출 게이트.

🔴 이 파일의 핵심은 test_no_heldout_leak 이다. heldout 18문항은 재측정이 불가능하고
(docs/total_plan.md §0) 산출물이 커밋돼 있어 실수로 사이트에 실릴 수 있다.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import build_site_data as bsd  # noqa: E402


def test_out_dir_is_inside_site():
    assert bsd.OUT_DIR == ROOT / "site" / "public" / "data"


def test_metadata_has_required_keys():
    meta = bsd.metadata("테스트", ["artifacts/x.json"])
    assert set(meta) == {"generated", "argusCommit", "sourceFiles", "note"}
    assert meta["sourceFiles"] == ["artifacts/x.json"]
    assert meta["note"] == "테스트"


def test_read_json_refuses_heldout_paths():
    for rel in [
        "artifacts/ablation/v2/heldout/heldout_run.json",
        "artifacts/ablation/v2/heldout/A8_heldout_run/report.json",
        "artifacts/ablation/v3/heldout/report.json",   # 🔴 세대가 올라도 막혀야 한다
        "artifacts/ablation/v4/heldout/report.json",
        "evalset/heldout.jsonl",
        "evalset/heldout_v2.jsonl",                    # 🔴 v4 R4-1 이 만들 새 heldout
    ]:
        with pytest.raises(ValueError, match="heldout"):
            bsd.read_json(rel)


def test_read_json_refuses_corpus_paths():
    """🔴 보안 ② — 코퍼스 원문(5.55GB)은 재배포 대상이 아니다."""
    for rel in ["data/exchange/x.xml", "data/periodic/y.html"]:
        with pytest.raises(ValueError, match="data/"):
            bsd.read_json(rel)


def test_write_site_json_requires_metadata(tmp_path, monkeypatch):
    monkeypatch.setattr(bsd, "OUT_DIR", tmp_path)
    with pytest.raises(ValueError, match="metadata"):
        bsd.write_site_json("site_x.json", {"a": 1})
    path = bsd.write_site_json("site_x.json", {"metadata": bsd.metadata("n", []), "a": 1})
    assert json.loads(path.read_text(encoding="utf-8"))["a"] == 1


def test_build_meta_mirrors_the_artifact():
    """🔴 값을 못박지 않는다 — 산출물과 같은지만 본다.

    종전 판은 `correct == 61` 을 단언했다. 그 수는 v3 R2D 에서 66 이 됐고, 그때
    이 테스트는 '사이트가 낡았다' 가 아니라 '테스트가 낡았다' 로 깨졌을 것이다.
    """
    payload = bsd.build_meta()
    assert "metadata" in payload
    head = payload["headline"]
    assert head["submissionArm"] == "A8"
    gen = bsd.resolve_generation()
    a8 = bsd.read_json(f"artifacts/ablation/{gen}/summary.json")["summary"]["arms"]["A8"]
    assert head["devGrid"]["correct"] == a8["correct"]
    assert head["devGrid"]["n"] == a8["n_scored"] == 72   # 분모 라벨은 정의라 박아도 된다
    # 🔴 heldout 은 집계 수치조차 없다 (보안 ③ · 낙폭 게이트 은퇴)
    assert "heldout" not in head
    # 🔴 지출·테스트 수·게이트 수는 정본 마크다운에서 파싱된 값이어야 한다
    assert head["spentWon"] == bsd.parse_ssot()["spent_won"]


def _generated_json_files() -> list[Path]:
    return sorted(bsd.OUT_DIR.glob("site_*.json"))


def test_no_heldout_leak():
    """생성된 모든 JSON 에 heldout 질문 전문·정답·골든 접수번호가 없어야 한다."""
    heldout_path = ROOT / "evalset" / "heldout.jsonl"
    if not heldout_path.exists():
        pytest.skip("heldout.jsonl 이 없는 환경")
    files = _generated_json_files()
    if not files:
        pytest.skip("아직 생성물이 없음 — build_site_data.py 실행 후 유효")

    forbidden: set[str] = set()
    for line in heldout_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        forbidden.add(row["question"][:40])
        if row.get("answer"):
            forbidden.add(str(row["answer"]))
        for ev in row.get("evidence") or []:
            if ev.get("rcept_no"):
                forbidden.add(ev["rcept_no"])

    for path in files:
        blob = path.read_text(encoding="utf-8")
        for needle in forbidden:
            assert needle not in blob, f"{path.name} 에 heldout 값이 실렸다: {needle[:24]}"


def test_no_golden_rank_keys_in_generated_json():
    files = _generated_json_files()
    if not files:
        pytest.skip("아직 생성물이 없음")
    for path in files:
        blob = path.read_text(encoding="utf-8")
        assert "golden_rank" not in blob, f"{path.name} 에 golden_rank 가 실렸다"


def test_build_script_never_reads_heldout_or_corpus():
    """🔴 소스에 읽기 호출이 아예 없어야 한다 — 세대에 무관하게.

    종전 판은 `"v2/heldout" not in src` 였다. 세대가 v3·v4 로 올라가면 그 문자열은
    비켜 가고 게이트가 조용히 무력해진다. 이제는 **읽기 함수의 인자**를 본다.
    """
    src = (ROOT / "scripts" / "build_site_data.py").read_text(encoding="utf-8")
    calls = re.findall(r"read_json(?:l)?\(\s*f?[\"']([^\"']+)", src)
    for arg in calls:
        assert "heldout" not in arg, f"빌드가 heldout 경로를 읽는다: {arg}"
        assert not arg.startswith("data/"), f"빌드가 코퍼스 원문을 읽는다: {arg}"
    # 🔴 그리고 argus 패키지를 import 하지 않는다 (보안 ① — settings() 가 .env 를 읽는다)
    assert not re.search(r"^\s*(from|import)\s+argus", src, re.M), "빌드가 argus 를 import 한다"


def test_no_api_key_in_generated_json():
    files = _generated_json_files()
    if not files:
        pytest.skip("아직 생성물이 없음")
    for path in files:
        blob = path.read_text(encoding="utf-8")
        assert "CLOVA_API_KEY" not in blob
        assert "nv-" not in blob, f"{path.name} 에 API 키 형태의 문자열이 있다"
        assert "Bearer " not in blob
        # 🔴 40자 이상 영숫자 덩어리 = 키 형태. 순수 숫자(접수번호 14자리 등)는 뺀다.
        #    오탐이 나면 **패턴을 좁히고 사유를 적어라** — 테스트를 지우지 마라.
        suspects = [s for s in re.findall(r"[A-Za-z0-9+/=]{40,}", blob) if not s.isdigit()]
        assert not suspects, f"{path.name} 에 키 형태 문자열이 있다: {suspects[0][:12]}…"


def test_no_evalset_question_text_in_generated_json():
    """🔴 보안 ④ — 어느 생성 JSON 에도 평가셋 질문 전문이 없어야 한다."""
    files = _generated_json_files()
    if not files:
        pytest.skip("아직 생성물이 없음")
    questions = [
        item["question"]
        for rel in ("evalset/questions.jsonl", "evalset/regression.jsonl", "evalset/valuechain.jsonl")
        for item in bsd.read_jsonl(rel)
    ]
    for path in files:
        blob = path.read_text(encoding="utf-8")
        for q in questions:
            assert q not in blob and q[:20] not in blob, f"{path.name} 에 질문 전문이 실렸다"


def test_no_public_ip_in_generated_json():
    """🔴 공인 IP 를 발행하지 않는다. IP 는 `deploy/README.md` 에서 읽어 대조한다."""
    files = _generated_json_files()
    if not files:
        pytest.skip("아직 생성물이 없음")
    deploy = (ROOT / "deploy" / "README.md").read_text(encoding="utf-8")
    ips = set(re.findall(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", deploy)) - {"0.0.0.0"}
    for path in files:
        blob = path.read_text(encoding="utf-8")
        for ip in ips:
            assert ip not in blob, f"{path.name} 에 평가 엔드포인트 IP 가 실렸다: {ip}"
```

- [ ] **Step 2: 테스트가 실패하는 것을 확인**

Run: `python -m pytest tests/test_site_data.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'build_site_data'`

- [ ] **Step 3: `scripts/build_site_data.py` 최소 구현**

```python
"""artifacts/ + evalset/ → site/public/data/site_*.json 생성기.

LLM 호출 0회. 사이트는 계산하지 않고 이 파일이 만든 JSON 만 렌더링한다.

    python scripts/build_site_data.py            # 전부 생성
    python scripts/build_site_data.py --only meta

🔴 읽지 않는 것 (read_json 이 ValueError 로 거부한다):
  · evalset/heldout.jsonl 과 heldout 산출물 폴더 전체 — 18문항은 최종 구성 1회용이고
    두 번 다 썼다. 질문 전문·golden 접수번호가 검색 튜닝 라벨로 쓰일 수 있다
  · data/ 아래 코퍼스 원문 — 주최측 제공물이고 재배포 대상이 아니다
🔴 argus 패키지를 import 하지 않는다 — argus.config.settings 가 .env 를 읽는다.
   src 를 봐야 할 때는 ast 로 소스만 읽는다 (count_tool_specs).
🔴 수치를 이 파일에 적지 않는다 — 성적은 artifacts/, 지출·게이트·테스트 수는
   docs/total_plan.md §1 파싱(parse_ssot)에서 온다. 옮겨 적은 값은 다음 라운드에 낡는다.
근거: docs/total_plan.md §0·§5 · 계획서 §0 정본 대장.
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
from datetime import datetime
from pathlib import Path

# 🔴 argus 패키지를 import 하지 않는다 — argus.config.settings 가 .env 를 읽는다 (보안 ①).
#    src 를 봐야 할 때는 ast 로 소스만 읽는다 (count_tool_specs).

REPO = Path(__file__).resolve().parents[1]
OUT_DIR = REPO / "site" / "public" / "data"

# 읽으면 ValueError. 경로 조각 단위 검사다.
# 🔴 "heldout" — evalset/heldout.jsonl · artifacts/ablation/*/heldout*/ 를 한 번에 막는다 (보안 ③)
# 🔴 "data/"   — 코퍼스 원문 5.55GB. 재배포 대상이 아니다 (보안 ②)
FORBIDDEN_PATHS = ("heldout", "data/")

GENERATIONS = ("v4", "v3", "v2")   # 🔴 앞에서부터 존재하는 첫 세대를 쓴다


def resolve_generation() -> str:
    """ablation 세대를 산출물의 존재로 결정한다 — 경로를 코드에 박지 않는다."""
    for gen in GENERATIONS:
        if (REPO / "artifacts" / "ablation" / gen / "summary.json").exists():
            return gen
    raise FileNotFoundError("ablation 산출물이 없다 — 사이트 데이터를 만들 수 없다")


def argus_commit() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=REPO,
            capture_output=True,
            text=True,
            check=True,
        )
        return out.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"


def metadata(note: str, source_files: list[str]) -> dict:
    return {
        "generated": datetime.now().isoformat(timespec="seconds"),
        "argusCommit": argus_commit(),
        "sourceFiles": list(source_files),
        "note": note,
    }


def _guard(rel: str) -> None:
    lowered = rel.replace("\\", "/").lower()
    for token in FORBIDDEN_PATHS:
        if token in lowered:
            raise ValueError(f"{token} 경로는 사이트 빌드에서 읽지 않는다: {rel}")


def read_json(rel: str) -> dict:
    _guard(rel)
    path = REPO / rel
    if not path.exists():
        raise FileNotFoundError(f"원천 파일이 없다: {rel}")
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(rel: str) -> list[dict]:
    _guard(rel)
    path = REPO / rel
    if not path.exists():
        raise FileNotFoundError(f"원천 파일이 없다: {rel}")
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def write_site_json(name: str, payload: dict) -> Path:
    if "metadata" not in payload:
        raise ValueError(f"{name} 에 metadata 블록이 없다")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / name
    path.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    return path


SSOT_MD = "docs/total_plan.md"   # 🔴 성적·지출·게이트·테스트 수의 정본


def count_tool_specs() -> int:
    """도구 종수를 `src/argus/agent/tools.py` 에서 센다.

    🔴 **import 하지 않는다.** `tools.py` 는 `argus.config.settings` 를 import 하고
    그것이 `.env` 를 읽는다 — 보안 ① 이 금지하는 경로다. 그래서 `ast` 로 소스만 읽는다
    (`TOOL_SPECS = _RETRIEVAL_SPECS + _CALC_SPECS` 이므로 두 리스트의 원소를 센다).
    """
    tree = ast.parse((REPO / "src/argus/agent/tools.py").read_text(encoding="utf-8"))
    sizes = {
        node.targets[0].id: len(node.value.elts)
        for node in tree.body
        if isinstance(node, ast.Assign)
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id in {"_RETRIEVAL_SPECS", "_CALC_SPECS"}
        and isinstance(node.value, ast.List)
    }
    if set(sizes) != {"_RETRIEVAL_SPECS", "_CALC_SPECS"}:
        raise ValueError("tools.py 의 스펙 리스트 이름이 바뀌었다 — 세는 방법을 고쳐라")
    return sum(sizes.values())


def parse_ssot() -> dict:
    """🔴 `docs/total_plan.md` §1 을 파싱한다 — 값을 옮겨 적지 않기 위해서다.

    이 함수가 이 파일에 리터럴 수치를 두지 않는 유일한 이유다. 종전 판은
    `spentWon = 12846.7` · `tests = 728` · `gates = 68/71` 을 상수로 들고 있었고,
    세 값 모두 다음 라운드에 낡았다 (19,730.5원 · 972 · 71/71).

    반환:
      spent_won          누적 지출 표의 `누적` 행
      spent_ledger       그 표의 행 목록 [{item, tokens, cost_won, source}]
      credit_won         100000 (규정. CONVENTION.md — 측정값이 아니다)
      tests              "테스트 972 passed" 의 수
      gates              {"closed": n, "total": m} — "게이트 총계 71 / 71"
      phases             Phase 표 [{no, doc, phase, status, gate, credit}]
    """
    text = (REPO / SSOT_MD).read_text(encoding="utf-8")

    # ── 누적 지출 표: `| # | 항목 | 토큰 | 비용 | 출처 |` 아래의 행들
    ledger, total_won = [], None
    for row in re.findall(r"^\|(.+)\|\s*$", text, re.M):
        cells = [c.strip() for c in row.split("|")]
        if len(cells) != 5:
            continue
        idx, item, tokens, cost, source = cells
        won = re.search(r"([\d,]+\.\d)원", cost)
        if not won:
            continue
        value = float(won.group(1).replace(",", ""))
        if "누적" in item or "누적" in idx:
            total_won = value
        elif idx.strip("* ").isdigit():
            ledger.append({
                "item": re.sub(r"[*`🔴🆕]", "", item).strip(),
                "tokens": int(tokens.replace(",", "")) if tokens.replace(",", "").isdigit() else None,
                "costWon": value,
                "source": re.sub(r"[*`]", "", source).strip(),
            })

    if total_won is None:
        raise ValueError(f"{SSOT_MD} §1 의 `누적` 행을 못 찾았다 — 표 서식이 바뀌었다")

    # 🔴 검산까지 파싱한다. 사본이 아니라 미러여야 한다.
    ledger_sum = round(sum(r["costWon"] for r in ledger), 1)
    if abs(ledger_sum - total_won) > 0.05:
        raise ValueError(
            f"{SSOT_MD} 원장 행 합 {ledger_sum} ≠ `누적` {total_won} — "
            "문서 안에서 두 수가 갈렸다. 사이트를 만들기 전에 문서를 고쳐라"
        )

    tests = re.search(r"테스트\s*\*{0,2}([\d,]+)\s*passed", text)
    gates = re.search(r"게이트\s*총계\s*\*{0,2}\s*(\d+)\s*/\s*(\d+)", text)
    if not (tests and gates):
        raise ValueError(f"{SSOT_MD} §1 의 테스트 수·게이트 총계를 못 찾았다")

    def plain(cell: str) -> str:
        return re.sub(r"[*`]", "", re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", cell)).strip()

    phases = []
    for row in re.findall(r"^\|\s*(\d{2})\s*\|(.+)\|\s*$", text, re.M):
        cells = [c.strip() for c in row[1].split("|")]
        if len(cells) < 5 or cells[1] == "—":     # 00 overview 행은 Phase 가 아니다
            continue
        phases.append({
            "no": row[0],
            "doc": plain(cells[0]),
            "phase": plain(cells[1]),
            "status": plain(cells[2]),
            "gate": plain(cells[3]),
            "credit": plain(cells[4]),
        })
    if len(phases) != 9:
        raise ValueError(f"{SSOT_MD} §1 Phase 표에서 9행이 아니라 {len(phases)}행을 읽었다")

    return {
        "spentWon": total_won,
        "spentLedger": ledger,
        "creditWon": 100000,          # CONVENTION.md — 규정값이고 측정값이 아니다
        "tests": int(tests.group(1).replace(",", "")),
        "gates": {"closed": int(gates.group(1)), "total": int(gates.group(2))},
        "phases": phases,
        "ssot": SSOT_MD,
    }


def build_meta() -> dict:
    """홈과 모든 페이지가 공유하는 헤드라인 수치.

    🔴 이 함수에 수치 리터럴이 하나도 없어야 한다. 성적은 산출물에서,
    지출·게이트·테스트 수는 docs/total_plan.md §1 파싱에서 온다.
    🔴 heldout 은 등장하지 않는다 (보안 ③ — 낙폭 게이트 은퇴 · 동결 원장).
    """
    gen = resolve_generation()
    summary = read_json(f"artifacts/ablation/{gen}/summary.json")
    a8 = summary["summary"]["arms"]["A8"]
    ssot = parse_ssot()
    recall = read_json("artifacts/eval/recall_v2.json")
    full = recall["cohorts"].get("full_88", {}).get("harness")

    return {
        "metadata": metadata(
            "홈·전 페이지 공유 헤드라인. 성적은 산출물, 지출·게이트·테스트는 total_plan §1 파싱.",
            [
                f"artifacts/ablation/{gen}/summary.json",
                "artifacts/eval/recall_v2.json",
                SSOT_MD,
            ],
        ),
        "generation": {
            "arm": gen,
            "generated": summary["meta"]["generated"],
            "indexSnapshot": summary["meta"].get("index_snapshot"),
        },
        "headline": {
            "submissionArm": "A8",
            "devGrid": {
                "correct": a8["correct"],
                "n": a8["n_scored"],
                "rate": round(a8["rate"], 4),
            },
            "overall": {"correct": a8["overall_correct"], "n": a8["overall_n_scored"]},
            "infoLimit": {"correct": a8["info_limit_correct"], "n": a8["info_limit_n"]},
            "regression": {
                "correct": a8["tracks"]["regression"]["correct"],
                "n": a8["tracks"]["regression"]["n"],
            },
            "hallucination": a8["hallucination"],
            "citationRate": a8["citation_rate"],
            "goldenEvidence": {
                "hit": a8["golden_evidence_hit"],
                "total": a8["golden_total"],
                "unreachable": a8.get("golden_unreachable"),
            },
            "recall5": full["recall5_rate"] if full else None,
            "spentWon": ssot["spentWon"],
            "creditWon": ssot["creditWon"],
        },
        "toolCount": count_tool_specs(),   # 🔴 src 에서 센다. "15종" 을 적지 않는다
        "tests": ssot["tests"],
        "gates": ssot["gates"],
        "ssotLinks": {
            "grades": f"artifacts/ablation/{gen}/summary.json",
            "spend": f"{SSOT_MD} §1",
            "armVerdicts": "docs/pipeline/09-ablation.md §I-5f ⑧",
            "endpoint": "deploy/README.md",
            "organizer": "docs/organizer-qa.md",
        },
    }


BUILDERS = {"meta": ("site_meta.json", build_meta)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="사이트 데이터 생성")
    parser.add_argument("--only", action="append", help="특정 빌더만 실행 (반복 지정 가능)")
    args = parser.parse_args(argv)

    names = args.only or list(BUILDERS)
    for name in names:
        if name not in BUILDERS:
            print(f"알 수 없는 빌더: {name}. 가능: {', '.join(BUILDERS)}")
            return 1
        filename, fn = BUILDERS[name]
        path = write_site_json(filename, fn())
        print(f"{name:14s} → {path.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

> ✅ **`parse_ssot()` 는 2026-08-14 에 실측으로 검증했다 (LLM 0회 · 크레딧 0원).**
> 현 `docs/total_plan.md` 에서 **누적 12행 · 행 합 = `누적` 행 (검산 통과) · 테스트 수 ·
> 게이트 총계 · Phase 9행**을 전부 뽑았고, `count_tool_specs()` 도 `tools.py` 를
> **import 하지 않고** 도구 종수를 냈다. 🔴 **여기에 그 값들을 적지 않는다** — 적으면 그것이
> 정확히 이 계획서가 처음 저지른 결함이다. 값을 보려면 스크립트를 돌려라.
>
> 🔴 **원장 행에 `heldout 최종 1회` 항목이 들어온다. 그 행을 지우지 마라.** 그것은
> **지출 기록**이고 보안 ③ 이 막는 것은 **문항·정답·골든 라벨**이다. 지우면 검산이 깨진다.
>
> 🔴 **검산이 깨지면 사이트를 고치지 말고 문서를 고쳐라.** `ValueError` 는 "파서가 틀렸다" 가
> 아니라 **"정본 안에서 두 수가 갈렸다"** 는 신호일 수 있다 — `total_plan §1` 이 그 사고를
> 실제로 겪었고(R2E 가 행만 넣고 `누적`·검산식을 안 고쳐 하루를 낡은 채로 살았다),
> 이 파서는 **그 사고를 다시 나면 즉시 알려 주는 장치**다.

- [ ] **Step 4: `recall_v2.json` 의 실제 코호트 키를 확인해 `build_meta` 를 맞춘다**

Run:
```bash
python -c "import json;d=json.load(open('artifacts/eval/recall_v2.json',encoding='utf-8'));print(list(d['cohorts']))"
```
Expected: 코호트 키 목록이 출력된다. `full_88` 이 아니라면 `build_meta` 의 키를 출력된 이름으로 고친다. `harness` 하위에 `recall5_rate` 가 있는지도 함께 확인한다:
```bash
python -c "import json;d=json.load(open('artifacts/eval/recall_v2.json',encoding='utf-8'));k=list(d['cohorts'])[-1];print(k, list(d['cohorts'][k]));print(list(d['cohorts'][k].get('harness',{})))"
```

- [ ] **Step 5: 테스트가 통과하는지 확인**

Run: `python -m pytest tests/test_site_data.py -v`
Expected: PASS. 생성물이 아직 없어 유출 게이트 4건은 `skip` 이다.

- [ ] **Step 6: 실제로 생성해서 유출 게이트를 켠다**

Run:
```bash
python scripts/build_site_data.py --only meta
python -m pytest tests/test_site_data.py -v
```
Expected: `site/public/data/site_meta.json` 생성. 이제 `test_no_heldout_leak`·`test_no_golden_rank_keys_in_generated_json`·`test_no_api_key_in_generated_json` 이 `skip` 이 아니라 PASS 다.

- [ ] **Step 7: 전체 테스트가 깨지지 않았는지 확인**

Run: `python -m pytest tests/ -q`
Expected: 기존 테스트 전건 + 신규 9건 모두 PASS. 🔴 **기존 건수를 여기 적지 않는다** —
그 수는 라운드마다 늘어나고 정본은 `docs/total_plan.md` §1 이다(v3 `R3-b` 규율).
회귀 판정은 **"실패 0건"** 으로 한다: `python -m pytest tests/ -q` 의 마지막 줄에 `failed` 가 없으면 통과.

- [ ] **Step 8: 커밋**

```bash
git add scripts/build_site_data.py tests/test_site_data.py site/public/data/site_meta.json
git commit -m "feat: 사이트 데이터 빌드 기반 + heldout 유출 게이트"
```

---

### Task 3: think_trace · retrieved_context 파서 (Python, pytest)

`/answers` 화면의 심장이다. **파싱은 Python 에서 하고 사이트는 결과만 렌더링한다** — 그래야 기존 pytest 하네스가 로직을 덮는다.

파싱 대상은 실측 형식이다:

```text
route=closed_lookup difficulty=low cot=False sc=False | hop=0 strat=narrow tool=search_chunks corp="HD현대일렉트릭" period=ym:202301 top_k=5 q="HD 현대일렉트릭 ..." n=1 top_score=73.669 ids=exchange_20230131800162 | hop=0 verdict=sufficient evidence=1 | generate tokens=584+82 | 근거 표기 누락 — critic 호출 | critic supported=True reason=... | verify calc_checked=amount complete_checked=- ok=True kinds=- | budget steps=1 hops=0 llm=2 tokens=1075
```

**Files:**
- Modify: `scripts/build_site_data.py` (파서 함수 추가)
- Modify: `tests/test_site_data.py` (파서 테스트 추가)

**Interfaces:**
- Consumes: Task 2 의 `read_json`
- Produces:
  - `parse_kv(text: str) -> dict[str, str]` — `k=v` 와 `k="v w"` 를 모두 처리
  - `classify_segment(seg: str) -> str` — `"route" | "search" | "verdict" | "generate" | "critic" | "verify" | "budget" | "note"`
  - `parse_think_trace(trace: str) -> list[dict]` — `[{"kind","raw","fields"}]`
  - `parse_evidence(context: str) -> list[dict]` — `[{"title","rceptNo","dartUrl","lines"}]`
  - `DART_URL: str` — `"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={}"`

- [ ] **Step 1: 실패하는 테스트를 쓴다 — `tests/test_site_data.py` 에 추가**

```python
# ── think_trace 파서 ─────────────────────────────────────────────────

TRACE = (
    'route=closed_lookup difficulty=low cot=False sc=False | '
    'hop=0 strat=narrow tool=search_chunks corp="HD현대일렉트릭" period=ym:202301 '
    'top_k=5 q="HD 현대일렉트릭 2023" n=1 top_score=73.669 ids=exchange_20230131800162 | '
    'hop=0 verdict=sufficient evidence=1 | '
    'generate tokens=584+82 | '
    '근거 표기 누락 — critic 호출 | '
    'critic supported=True reason=답변에 근거 표기가 존재합니다. | '
    'verify calc_checked=amount complete_checked=- ok=True kinds=- | '
    'budget steps=1 hops=0 llm=2 tokens=1075'
)


def test_parse_kv_handles_quoted_values():
    got = bsd.parse_kv('tool=search_chunks corp="HD현대일렉트릭" top_k=5')
    assert got["tool"] == "search_chunks"
    assert got["corp"] == "HD현대일렉트릭"
    assert got["top_k"] == "5"


def test_parse_kv_ignores_bare_words():
    got = bsd.parse_kv("generate tokens=584+82")
    assert got == {"tokens": "584+82"}


def test_classify_segment():
    assert bsd.classify_segment("route=closed_lookup difficulty=low") == "route"
    assert bsd.classify_segment("hop=0 strat=narrow tool=search_chunks") == "search"
    assert bsd.classify_segment("hop=0 verdict=sufficient evidence=1") == "verdict"
    assert bsd.classify_segment("generate tokens=584+82") == "generate"
    assert bsd.classify_segment("critic supported=True reason=ok") == "critic"
    assert bsd.classify_segment("verify calc_checked=amount ok=True") == "verify"
    assert bsd.classify_segment("budget steps=1 hops=0") == "budget"
    assert bsd.classify_segment("근거 표기 누락 — critic 호출") == "note"


def test_parse_think_trace_segments_in_order():
    steps = bsd.parse_think_trace(TRACE)
    assert [s["kind"] for s in steps] == [
        "route", "search", "verdict", "generate", "note", "critic", "verify", "budget",
    ]
    assert steps[0]["fields"]["route"] == "closed_lookup"
    assert steps[1]["fields"]["tool"] == "search_chunks"
    assert steps[1]["fields"]["ids"] == "exchange_20230131800162"
    assert steps[2]["fields"]["verdict"] == "sufficient"
    assert steps[7]["fields"]["tokens"] == "1075"
    assert steps[4]["raw"] == "근거 표기 누락 — critic 호출"


def test_parse_think_trace_empty():
    assert bsd.parse_think_trace("") == []


# ── retrieved_context 파서 ───────────────────────────────────────────

CONTEXT = (
    "## [공급계약 체결] HD현대일렉트릭 – 단일판매ㆍ공급계약체결 — 2500kVA 배전변압기 등 3,500대\n"
    "- 공시일: 2023-01-31 (접수번호 20230131800162)\n"
    "- 계약금액: 97,000,000,000원 (970억원)\n"
    "\n"
    "## [정정] 삼성SDI – 유상증자결정\n"
    "- 공시일: 2025-04-09 (접수번호 20250409001154)\n"
)


def test_parse_evidence_splits_blocks():
    blocks = bsd.parse_evidence(CONTEXT)
    assert len(blocks) == 2
    assert blocks[0]["title"].startswith("[공급계약 체결] HD현대일렉트릭")
    assert blocks[0]["rceptNo"] == "20230131800162"
    assert blocks[0]["dartUrl"].endswith("20230131800162")
    assert "- 계약금액: 97,000,000,000원 (970억원)" in blocks[0]["lines"]
    assert blocks[1]["rceptNo"] == "20250409001154"


def test_parse_evidence_without_rcept_no():
    blocks = bsd.parse_evidence("## 제목만 있는 블록\n- 내용")
    assert len(blocks) == 1
    assert blocks[0]["rceptNo"] is None
    assert blocks[0]["dartUrl"] is None


def test_parse_evidence_empty():
    assert bsd.parse_evidence("") == []
```

- [ ] **Step 2: 테스트가 실패하는 것을 확인**

Run: `python -m pytest tests/test_site_data.py -k "parse" -v`
Expected: FAIL — `AttributeError: module 'build_site_data' has no attribute 'parse_kv'`

- [ ] **Step 3: `scripts/build_site_data.py` 에 파서를 구현**

`import re` 를 상단 import 에 추가하고, `build_meta` 위에 아래를 넣는다.

```python
DART_URL = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo={}"

# k="여백 있는 값" 또는 k=여백없는값
_KV = re.compile(r'([A-Za-z_][A-Za-z0-9_]*)=("([^"]*)"|[^\s]*)')
_RCEPT = re.compile(r"접수번호\s*(\d{14})")


def parse_kv(text: str) -> dict[str, str]:
    """세그먼트에서 k=v 쌍만 뽑는다. 맨앞 키워드 같은 맨몸 단어는 버린다."""
    out: dict[str, str] = {}
    for match in _KV.finditer(text):
        key = match.group(1)
        out[key] = match.group(3) if match.group(3) is not None else match.group(2)
    return out


def classify_segment(seg: str) -> str:
    """세그먼트 종류. 순서가 중요하다 — search 와 verdict 는 둘 다 hop= 으로 시작한다."""
    stripped = seg.strip()
    if stripped.startswith("route="):
        return "route"
    if stripped.startswith("hop="):
        return "search" if "tool=" in stripped else "verdict"
    for head in ("generate", "critic", "verify", "budget"):
        if stripped.startswith(head):
            return head
    return "note"


def parse_think_trace(trace: str) -> list[dict]:
    """think_trace 를 ' | ' 로 갈라 단계 배열로 만든다."""
    if not trace or not trace.strip():
        return []
    steps = []
    for seg in trace.split(" | "):
        raw = seg.strip()
        if not raw:
            continue
        steps.append({"kind": classify_segment(raw), "raw": raw, "fields": parse_kv(raw)})
    return steps


def parse_evidence(context: str) -> list[dict]:
    """retrieved_context 를 '## ' 헤더 단위 근거 블록으로 가른다.

    접수번호 14자리를 찾으면 DART 뷰어 링크를 붙인다.
    """
    if not context or not context.strip():
        return []
    blocks = []
    for chunk in context.split("\n## "):
        body = chunk.lstrip()
        if body.startswith("## "):
            body = body[3:]
        if not body.strip():
            continue
        lines = [line for line in body.splitlines() if line.strip()]
        if not lines:
            continue
        title = lines[0].strip()
        rest = lines[1:]
        found = _RCEPT.search(body)
        rcept = found.group(1) if found else None
        blocks.append(
            {
                "title": title,
                "rceptNo": rcept,
                "dartUrl": DART_URL.format(rcept) if rcept else None,
                "lines": rest,
            }
        )
    return blocks
```

- [ ] **Step 4: 테스트가 통과하는지 확인**

Run: `python -m pytest tests/test_site_data.py -k "parse" -v`
Expected: 9건 PASS.

- [ ] **Step 5: 실제 산출물로 파서를 관통시켜 본다**

Run:
```bash
python -c "
import json,sys; sys.path.insert(0,'scripts')
import build_site_data as b
gen=b.resolve_generation()
d=json.load(open(f'artifacts/ablation/{gen}/A8/responses/Q-001.json',encoding='utf-8'))['response']
steps=b.parse_think_trace(d['think_trace'])
ev=b.parse_evidence(d['retrieved_context'])
print('steps:',[s['kind'] for s in steps])
print('evidence:',[(e['rceptNo'],e['title'][:30]) for e in ev])
assert all(s['kind']!='note' or '=' not in s['raw'] for s in steps)
"
```
Expected: 단계 종류가 `route`/`search`/... 로 나오고 `note` 로 잘못 떨어진 세그먼트가 없다. 근거 블록에 14자리 접수번호가 붙는다.

- [ ] **Step 6: 여러 문항에 대해 미분류 세그먼트가 없는지 훑는다**

Run:
```bash
python -c "
import json,glob,sys; sys.path.insert(0,'scripts')
import build_site_data as b
from collections import Counter
c=Counter(); notes=[]
for p in glob.glob(f'artifacts/ablation/{b.resolve_generation()}/A8/responses/Q-*.json'):
    d=json.load(open(p,encoding='utf-8'))['response']
    for s in b.parse_think_trace(d['think_trace']):
        c[s['kind']]+=1
        if s['kind']=='note' and '=' in s['raw']: notes.append(s['raw'][:70])
print(c)
print('의심 note:', len(notes)); [print(' ',n) for n in notes[:8]]
"
```
Expected: `route`·`search`·`verdict`·`generate`·`critic`·`verify`·`budget` 이 모두 등장한다. "의심 note" 가 0 이 아니면 `classify_segment` 에 해당 접두어를 추가하고 Step 1 에 테스트를 한 줄 더 넣는다.

- [ ] **Step 7: 커밋**

```bash
git add scripts/build_site_data.py tests/test_site_data.py
git commit -m "feat: think_trace·retrieved_context 파서 + 단위 테스트"
```

---

### Task 4: 홈 — 히어로 + 질의 검색 + 헤드라인 지표

**Files:**
- Modify: `scripts/build_site_data.py` (`build_home` 추가, `BUILDERS` 등록)
- Modify: `tests/test_site_data.py` (`build_home` 테스트 추가)
- Create: `site/components/LandingSearch.tsx`
- Modify: `site/app/page.tsx` (Task 1 의 임시 홈 교체)
- Modify: `site/app/globals.css`
- Create: `site/public/data/site_home.json` (생성물)

**Interfaces:**
- Consumes: `bsd.metadata` · `bsd.read_json` · `bsd.read_jsonl` · `bsd.write_site_json` · `bsd.build_meta` (Task 2) · `StatCard` (Task 1)
- Produces:
  - `read_jsonl_unsafe_heldout_ids() -> list[dict]` — 테스트 전용. heldout 의 `question_id` 만 반환
  - `build_home() -> dict` — `{"metadata", "headline", "generation", "capabilities", "coverage"}`
  - `site_home.json` 스키마:
    - `headline` — Task 2 `build_meta()["headline"]` 과 동일 구조
    - `generation` — `{arm, generated, indexSnapshot}`. 🔴 세대 라벨을 화면 하단에 렌더한다
    - `capabilities: [{ "title": str, "body": str, "evidence": str }]` — 4개
    - `coverage: [{ "docGroup": str, "byDifficulty": {난이도: n}, "n": int }]`
  - 🔴 **`examples` 키는 없다** (2026-08-14 재검토 · 보안 ④). 종전 스키마의
    `examples[].question` 이 `evalset/questions.jsonl` 의 **질문 전문**이었다.
    `LandingSearch` 는 예시 칩 없이 입력창만 둔다 — props 는 `{}` 다.

- [ ] **Step 1: 실패하는 테스트를 쓴다 — `tests/test_site_data.py` 맨 끝에 추가**

```python
# ── 홈 ───────────────────────────────────────────────────────────────

def test_build_home_shape():
    payload = bsd.build_home()
    assert set(payload) == {"metadata", "headline", "generation", "capabilities", "coverage"}
    assert payload["headline"]["submissionArm"] == "A8"
    assert len(payload["capabilities"]) == 4
    assert all({"title", "body", "evidence"} == set(c) for c in payload["capabilities"])
    assert payload["coverage"]                       # 문서군 축이 비어 있지 않다
    assert payload["generation"]["generated"]        # 🔴 세대 라벨이 있다


def test_build_home_carries_no_question_text():
    """🔴 보안 ④ — 질문 전문이 한 글자도 없다.

    종전 판은 예시 문항 6개의 `question` 을 그대로 실었다. 이 테스트가 그 회귀를 막는다.
    evalset 전 문항의 질문 문자열을 실제로 읽어 와 페이로드 안에 없는지 본다.
    """
    payload = bsd.build_home()
    blob = json.dumps(payload, ensure_ascii=False)
    for rel in ("evalset/questions.jsonl", "evalset/regression.jsonl", "evalset/valuechain.jsonl"):
        for item in bsd.read_jsonl(rel):
            question = item["question"]
            assert question not in blob, f"{item['question_id']} 질문 전문이 홈에 실렸다"
            # 앞 20자만 잘라 실어도 유출이다
            assert question[:20] not in blob


def test_build_home_has_no_heldout_ids():
    """heldout 문항 ID 조차 등장하지 않는다."""
    payload = bsd.build_home()
    blob = json.dumps(payload, ensure_ascii=False)
    for row in bsd.read_jsonl_unsafe_heldout_ids():
        assert row["question_id"] not in blob
```

`read_jsonl_unsafe_heldout_ids` 는 **테스트 전용 헬퍼**다. 유출 게이트를 우회하지 않고 ID 만 읽는다 — Step 3 에서 함께 구현한다.

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_site_data.py -k "home" -v`
Expected: FAIL — `AttributeError: module 'build_site_data' has no attribute 'build_home'`

- [ ] **Step 3: `scripts/build_site_data.py` 에 `build_home` 과 테스트 헬퍼 구현**

`build_meta` 아래, `BUILDERS` 위에 넣는다.

```python
def read_jsonl_unsafe_heldout_ids() -> list[dict]:
    """테스트 전용 — heldout 의 question_id 만 읽는다.

    유출 게이트의 목적은 질문 전문·정답·골든 접수번호를 사이트에 싣지 않는 것이다.
    ID 자체는 evalset/questions.jsonl 과 같은 공개 번호 체계이므로,
    '예시 문항에 heldout 이 섞였는지' 검사에는 ID 만 있으면 된다.
    """
    path = REPO / "evalset" / "heldout.jsonl"
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.append({"question_id": json.loads(line)["question_id"]})
    return out


# 홈에서 소개하는 공시 Agent 고유 능력 4개. body 는 문서 근거가 있는 문장만 쓴다.
CAPABILITIES = [
    {
        "title": "근거 없이 답하지 않는다",
        "body": "모든 답변은 접수번호 단위 근거와 함께 나온다. 응답 스키마는 question_id · "
        "question · retrieved_context · think_trace · answer 5개 키로 고정돼 있고, "
        "근거 표기가 빠지면 critic 이 따로 호출된다.",
        "evidence": "골든 근거 회수 {goldenHit}/{goldenTotal} · 근거 표기율 {citationRate}",
    },
    {
        "title": "정정 공시를 체인으로 따라간다",
        "body": "공시는 정정된다. 원공시와 정정본을 체인으로 잇고 체인 위치로만 최신 여부를 "
        "판정한다. 질문이 시점을 지정하면 그 시점 값을 답하고 정정 사실을 부기한다.",
        # 🔴 값을 적지 않는다 — L1 Δ 는 세대마다 움직인다 (v2 −8.3%p → v3 −9.7%p).
        "evidence": "정정체인 hop 제거 시 {l1DeltaPp}%p (L1) · 골든 근거도 동반 하락한다",
    },
    {
        "title": "계산은 모델이 아니라 도구가 한다",
        "body": "증감률·비중·합계·CAGR·기간 계산은 전용 도구가 수행하고, 검증기가 답변의 수치를 "
        "근거와 다시 맞춘다. 검증 경로에는 LLM 호출이 0회다. "
        # 🔴 이 문장을 '검증기가 계산 오류를 잡는다' 로 강화하면 안 된다 —
        #    critic.py:208 의 min(claims, key=…) 이 답변 수치 중 최유리 값을 골라 비교하므로
        #    10× 단위환산 오류가 통과한다 (Q-065 가 실제로 통과했다).
        #    docs/total_plan.md §1 의 한계 고지 · v2/gate_I.md §9c 를 그대로 따른다.
        "다만 검증기는 답변 수치 중 근거와 가장 가까운 것을 골라 비교하므로, "
        "10배 단위환산 오류처럼 '어느 한 수치가 설명되면' 통과하는 구간이 남아 있다.",
        "evidence": "도구 {toolCount}종 · 검증기 LLM 0회 (한계는 본문)",
    },
    {
        "title": "모르는 것은 거절한다",
        "body": "공시유보·서식상 부재처럼 원본에 값이 없는 질의는 지어내지 않고 거절한다. "
        "정보한계 문항은 분모를 따로 세어 거절이 정답이 되게 했다.",
        "evidence": "정보한계 {infoLimitCorrect}/{infoLimitN} · 환각 {hallucination}건",
    },
]


def _fill(template: str, head: dict, tool_count: int, l1_delta_pp: float) -> str:
    return template.format(
        goldenHit=head["goldenEvidence"]["hit"],
        goldenTotal=head["goldenEvidence"]["total"],
        citationRate=f"{head['citationRate']:.3f}",
        infoLimitCorrect=head["infoLimit"]["correct"],
        infoLimitN=head["infoLimit"]["n"],
        hallucination=head["hallucination"],
        toolCount=tool_count,
        l1DeltaPp=f"{l1_delta_pp:+.1f}",
    )


# 🔴 홈 예시 문항을 싣지 않는다 (2026-08-14 재검토 · 보안 ④).
#    종전 판은 EXAMPLE_IDS 6개의 `question` 전문을 site_home.json 에 넣었다.
#    evalset 문항·정답은 우리가 만든 라벨이고 total_plan §5 가 그 라벨의 부풀림을 명시한다.
#    대신 **축 커버리지 요약**(문서군 × 난이도 셀이 채워졌다는 사실)만 낸다 — 질문 문자열 0개.


def build_home() -> dict:
    meta_payload = build_meta()
    head = meta_payload["headline"]
    gen = meta_payload["generation"]["arm"]
    arms = read_json(f"artifacts/ablation/{gen}/summary.json")["summary"]["arms"]
    l1_delta_pp = (arms["L1"]["rate"] - arms["A8"]["rate"]) * 100

    rows = [
        {k: r[k] for k in ROW_FIELDS_ALLOWED if k in r}      # 🔴 화이트리스트 (보안 ⑤)
        for r in read_json(f"artifacts/ablation/{gen}/A8/report.json")["rows"]
    ]

    coverage: dict[str, dict[str, int]] = {}
    for r in rows:
        if r.get("track") != "grid":
            continue
        coverage.setdefault(r["doc_group"], {}).setdefault(r["difficulty"], 0)
        coverage[r["doc_group"]][r["difficulty"]] += 1

    return {
        "metadata": metadata(
            "홈 — 헤드라인 지표, 공시 Agent 고유 능력 4개, 축 커버리지. "
            "🔴 질문 전문·정답·답변 전문을 싣지 않는다 (보안 ③④⑤).",
            [
                f"artifacts/ablation/{gen}/summary.json",
                f"artifacts/ablation/{gen}/A8/report.json",
                SSOT_MD,
            ],
        ),
        "headline": head,
        "generation": meta_payload["generation"],
        "capabilities": [
            {
                "title": c["title"],
                "body": c["body"],
                "evidence": _fill(c["evidence"], head, meta_payload["toolCount"], l1_delta_pp),
            }
            for c in CAPABILITIES
        ],
        "coverage": [
            {"docGroup": group, "byDifficulty": by_diff, "n": sum(by_diff.values())}
            for group, by_diff in sorted(coverage.items())
        ],
    }
```

그리고 `BUILDERS` 를 아래로 교체한다.

```python
BUILDERS = {
    "meta": ("site_meta.json", build_meta),
    "home": ("site_home.json", build_home),
}
```

- [ ] **Step 4: 예시 문항 ID 가 실제로 존재하는지 확인**

Run:
```bash
python -m pytest tests/test_site_data.py -k "home" -v
```
Expected: 3건 PASS. 🔴 `test_build_home_carries_no_question_text` 가 실패하면 **테스트를
고치지 말고 페이로드에서 질문 문자열을 빼라** — 그것이 보안 ④ 다.

축 커버리지가 비었으면 원천의 `track`·`doc_group` 값을 확인한다 (질문 전문을 출력하지 않는다):

```bash
PYTHONIOENCODING=utf-8 python -c "
import json
gen=next(g for g in ('v4','v3','v2') if __import__('pathlib').Path(f'artifacts/ablation/{g}/summary.json').exists())
rows=json.load(open(f'artifacts/ablation/{gen}/A8/report.json',encoding='utf-8'))['rows']
grid=[r for r in rows if r['track']=='grid']
for g in ['exchange','major','holding','periodic']:
    print(g, sorted({r['difficulty'] for r in grid if r['doc_group']==g}), len([r for r in grid if r['doc_group']==g]))
"
```

- [ ] **Step 5: 생성 후 유출 게이트 재확인**

Run:
```bash
python scripts/build_site_data.py --only meta --only home
python -m pytest tests/test_site_data.py -v
```
Expected: 전건 PASS. `site/public/data/site_home.json` 생성.

- [ ] **Step 6: `site/components/LandingSearch.tsx` 작성**

레퍼런스 `LandingSearch.tsx` 와 같은 역할 — Enter 로 `/search?query=` 로 이동한다.

```tsx
"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

// 🔴 예시 칩을 두지 않는다 (보안 ④) — 칩 라벨이 곧 evalset 질문 전문이었다.
//    props 없이 입력창만 둔다. 종전 `Example = { questionId, question }` 타입은 삭제했다.

export default function LandingSearch() {
  const router = useRouter();
  const [value, setValue] = useState("");

  function go(query: string) {
    const trimmed = query.trim();
    router.push(trimmed ? `/search?query=${encodeURIComponent(trimmed)}` : "/search");
  }

  return (
    <div className="landing-search">
      <label htmlFor="landing-q">공시 질문을 입력하세요</label>
      <form
        className="landing-search-row"
        onSubmit={(event) => {
          event.preventDefault();
          go(value);
        }}
      >
        <input
          id="landing-q"
          value={value}
          onChange={(event) => setValue(event.target.value)}
          placeholder="예: Q-047 · 지분공시 · 난이도 상"
          autoComplete="off"
        />
        <button type="submit" className="btn btn-primary">
          검색
        </button>
      </form>
      <p className="landing-search-note">
        평가셋 문항을 번호·문서군·난이도·트랙으로 찾습니다. 🔴 질문 전문과 정답은 공개하지
        않습니다 — 우리가 만든 라벨이고, 검색 튜닝이 가능한 형태이기 때문입니다.
      </p>
    </div>
  );
}
```

- [ ] **Step 7: `site/app/globals.css` 에 랜딩 검색 스타일 추가**

파일 맨 끝의 `/* ── 반응형 ── */` 블록 **앞**에 넣는다. 이후 Task 도 모두 이 위치에 추가한다.

```css
/* ── 랜딩 검색 ──────────────────────────────────────── */
.landing-search {
  margin-top: 26px;
  padding: 18px;
  border: 1px solid var(--line);
  border-radius: 10px;
  background: #ffffff;
  box-shadow: 0 18px 40px rgba(15, 25, 55, 0.08);
}

.landing-search label {
  display: block;
  margin-bottom: 10px;
  color: var(--ink-soft);
  font-weight: 800;
}

.landing-search-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 10px;
}

.landing-search-row input {
  min-height: 52px;
  width: 100%;
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 0 16px;
  font: inherit;
  font-size: 16px;
}

.landing-search-note {
  margin: 12px 0 0;
  color: var(--muted);
  font-size: 13px;
}

.example-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 14px;
}

.example-chips .chip {
  cursor: pointer;
  border-color: #c3d0f2;
  background: #eef2ff;
  color: var(--primary-dark);
}

.capability-card h3 {
  margin: 6px 0 0;
  font-size: 17px;
}

.capability-card p {
  margin: 8px 0 0;
  color: var(--muted);
  font-size: 14px;
}

.capability-card .chip {
  margin-top: 12px;
}
```

- [ ] **Step 8: `site/app/page.tsx` 를 데이터 기반으로 교체**

```tsx
import data from "../public/data/site_home.json";
import LandingSearch from "../components/LandingSearch";
import StatCard from "../components/StatCard";

const head = data.headline;

export default function Home() {
  return (
    <main>
      <section className="landing-hero">
        <div className="container landing-hero-inner">
          <span className="badge">ARGUS · 제출 구성 {head.submissionArm}</span>
          <h1>공시 질문에 근거와 함께 답한다</h1>
          <p className="lead">
            거래소공시·주요사항보고서·지분공시·정기공시를 정규화·색인해 두고, 접수번호 단위 근거와
            추론 트레이스를 함께 돌려주는 DART 공시 질의응답 Agent 입니다.
          </p>
          <LandingSearch />
        </div>
      </section>

      <section className="section">
        <div className="container">
          <div className="section-heading">
            <span className="badge">현재 성능</span>
            <h2>제출 구성 {head.submissionArm} 실측</h2>
            <p className="lead">
              전부 <code>artifacts/</code> 실측값입니다. 트랙마다 분모와 목적이 다르므로 섞어 읽지
              않습니다.
            </p>
          </div>

          <section className="grid grid-4" aria-label="헤드라인 지표">
            <StatCard
              label="dev 그리드"
              value={`${head.devGrid.correct}/${head.devGrid.n}`}
              helper={`정답률 ${(head.devGrid.rate * 100).toFixed(1)}% — 판정에 쓰는 유일한 분모`}
            />
            <StatCard
              label="정보한계 트랙"
              value={`${head.infoLimit.correct}/${head.infoLimit.n}`}
              helper={`환각 ${head.hallucination}건 — 거절이 정답인 트랙`}
            />
            <StatCard
              label="회귀 트랙"
              value={`${head.regression.correct}/${head.regression.n}`}
              helper="고친 실패의 재발 감시"
            />
            <StatCard
              label="heldout"
              value={`${head.heldout.correct}/${head.heldout.n}`}
              helper={`${head.heldout.runs}회 사용 후 은퇴 · 재측정 불가`}
            />
          </section>

          <section className="grid grid-2 spacious" aria-label="공시 Agent 특성">
            {data.capabilities.map((capability) => (
              <article className="panel capability-card" key={capability.title}>
                <p className="eyebrow">공시 Agent 특성</p>
                <h3>{capability.title}</h3>
                <p>{capability.body}</p>
                <span className="chip">{capability.evidence}</span>
              </article>
            ))}
          </section>
        </div>
      </section>
    </main>
  );
}
```

- [ ] **Step 9: 타입체크와 빌드**

Run: `cd site && npm run typecheck && npm run build`
Expected: 오류 0건, `/` 프리렌더 성공.

- [ ] **Step 10: 육안 확인**

Run: `cd site && npm run dev`
Expected: 히어로에 `제출 구성 A8` 배지와 검색창. 🔴 **예시 칩은 없다**(보안 ④).
지표 카드 4장 — **그리드 · 정보한계 · 회귀 · 골든 근거**. 🔴 **네 값이 `site_meta.json` 과
글자 단위로 같은지만 본다. 기대값을 여기 적지 않는다** — 세대마다 움직인다.
🔴 **heldout 카드를 두지 않는다**(보안 ③ — 낙폭 게이트 은퇴 · 동결 원장).
하단에 세대 라벨(`generation.arm` · `generated`)이 보인다. 특성 카드 4장. 확인 후 종료.

- [ ] **Step 11: 커밋**

```bash
git add scripts/build_site_data.py tests/test_site_data.py site/app/page.tsx site/app/globals.css site/components/LandingSearch.tsx site/public/data/site_home.json
git commit -m "feat: 홈 — 히어로·질의 검색·헤드라인 지표"
```

---

### Task 5: 질의응답 트레이스 화면 — 이 사이트의 주력

> 🔴 **2026-08-14 재검토 — 이 Task 는 `R6-MVP` 에서 제외됐다. 보안 ④⑤ 와 정면으로 충돌한다.**
>
> 이 화면은 설계상 **질문 전문 + 답변 전문 + 근거 발췌**를 함께 낸다. 그 셋 중 앞의 둘이
> `evalset` 문항과 우리가 만든 정답에 직접 맞물려 있고(답변은 질문을 되풀어 쓴다),
> `total_plan §5` 는 **우리 라벨에 부풀림이 있다**고 명시한다(`Q-110` 10× 환산이 judge 통과).
> **틀렸을 수 있는 것을 "정답 트레이스" 로 발행하는 것**이 이 화면의 위험이다.
>
> **되살리는 조건 셋 — 하나라도 빠지면 만들지 않는다.**
> 1. 🔴 `evalset` 문항이 아닌 **자체 시연 질의**를 쓴다 (평가셋 문항을 재사용하지 않는다).
>    그 질의의 응답은 **캐시에 없으므로 LLM 호출이 필요하다** — 코드 동결(08-25) 전에는 금지다.
> 2. 🔴 `A19`(judge 수치 자기일관성 축 · v4 `R3-1`)가 닫혀서 **부풀림 0** 이 확인돼야 한다.
> 3. 🔴 제출·평가가 끝난 뒤(**09-21 이후**)여야 한다.
>
> 아래 원문은 **설계 기록**으로 남긴다. `parse_think_trace`·`parse_evidence`(Task 3)는
> 그 자체로는 유출이 아니고 `R6-MVP` 의 근거 통계에 쓰이므로 **Task 3 은 유지된다.**

문항 하나를 고르면 **근거(접수번호 + DART 원문 링크) → 추론 단계 → 답변** 이 순서대로 보인다. 공시 Agent 의 성격이 가장 직접적으로 드러나는 화면이다.

**Files:**
- Modify: `scripts/build_site_data.py` (`build_answers` 추가)
- Modify: `tests/test_site_data.py`
- Create: `site/components/TraceViewer.tsx`
- Create: `site/app/answers/page.tsx`
- Modify: `site/app/globals.css`
- Create: `site/public/data/site_answers.json` (생성물)

**Interfaces:**
- Consumes: `parse_think_trace` · `parse_evidence` · `DART_URL` (Task 3) · `read_json` · `metadata` (Task 2)
- Produces:
  - `STEP_LEGEND: list[dict]` — 8종 `{"kind","label","note"}`
  - `build_answers(limit: int = 24) -> dict`
  - `site_answers.json` 스키마:
    - `metadata`
    - `stepLegend: [{ kind, label, note }]`
    - `cases: [{ questionId, question, difficulty, docGroup, taskType, answerMode, track, corpName, correct, firstFailure, rootCause, hasCitation, nHops, nSteps, nLlmCalls, latencyS, answer, evidence, steps }]`
      - `evidence: [{ title: str, rceptNo: str|null, dartUrl: str|null, lines: str[] }]`
      - `steps: [{ kind: str, raw: str, fields: {str: str} }]`
  - `TraceViewer` 컴포넌트 + `export type Case`

- [ ] **Step 1: 실패하는 테스트를 쓴다 — `tests/test_site_data.py` 맨 끝에 추가**

```python
# ── 트레이스 ─────────────────────────────────────────────────────────

def test_build_answers_shape():
    payload = bsd.build_answers(limit=8)
    assert set(payload) == {"metadata", "stepLegend", "cases"}
    assert len(payload["cases"]) == 8
    assert len(payload["stepLegend"]) == 8
    case = payload["cases"][0]
    assert {
        "questionId", "question", "difficulty", "docGroup", "taskType", "answerMode",
        "track", "corpName", "correct", "firstFailure", "rootCause", "hasCitation",
        "nHops", "nSteps", "nLlmCalls", "latencyS", "answer", "evidence", "steps",
    } == set(case)


def test_build_answers_includes_failures_not_only_successes():
    """성공만 고르면 홍보물이 된다. 오답이 최소 2건 들어가야 한다."""
    payload = bsd.build_answers(limit=24)
    wrong = [c for c in payload["cases"] if not c["correct"]]
    assert len(wrong) >= 2
    assert all(c["firstFailure"] for c in wrong)


def test_build_answers_evidence_has_dart_links():
    payload = bsd.build_answers(limit=8)
    with_rcept = [
        block for case in payload["cases"] for block in case["evidence"] if block["rceptNo"]
    ]
    assert with_rcept, "접수번호가 붙은 근거 블록이 하나도 없다"
    assert all(b["dartUrl"].startswith("https://dart.fss.or.kr/") for b in with_rcept)


def test_build_answers_has_no_golden_fields():
    """error_analysis rows 의 golden·golden_rank 를 실어보내지 않는다."""
    blob = json.dumps(bsd.build_answers(limit=24), ensure_ascii=False)
    assert "golden_rank" not in blob
    assert "goldenRank" not in blob


def test_build_answers_covers_all_doc_groups():
    payload = bsd.build_answers(limit=24)
    groups = {c["docGroup"] for c in payload["cases"]}
    assert {"exchange", "major", "holding", "periodic"} <= groups


def test_build_answers_steps_are_parsed():
    payload = bsd.build_answers(limit=8)
    kinds = {step["kind"] for case in payload["cases"] for step in case["steps"]}
    assert "route" in kinds
    assert "search" in kinds
    assert "budget" in kinds
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_site_data.py -k "answers" -v`
Expected: FAIL — `AttributeError: module 'build_site_data' has no attribute 'build_answers'`

- [ ] **Step 3: `build_answers` 구현 — `build_home` 아래, `BUILDERS` 위**

```python
STEP_LEGEND = [
    {"kind": "route", "label": "라우팅", "note": "질의 유형·난이도·문서군을 정하고 CoT 사용 여부를 결정한다"},
    {"kind": "search", "label": "검색", "note": "도구·기업·기간·top_k 로 후보 공시를 회수한다"},
    {"kind": "verdict", "label": "충분성 판정", "note": "현재 근거로 답할 수 있는지 보고 부족하면 hop 을 늘린다"},
    {"kind": "generate", "label": "생성", "note": "근거만으로 답을 만든다. 프롬프트·완성 토큰이 남는다"},
    {"kind": "critic", "label": "critic", "note": "근거 표기가 빠졌을 때만 호출되는 지지 여부 검사"},
    {"kind": "verify", "label": "검증", "note": "계산·완결성 재확인. LLM 호출 0회"},
    {"kind": "budget", "label": "예산", "note": "스텝·hop·LLM 호출·토큰 누적"},
    {"kind": "note", "label": "메모", "note": "파이프라인이 남긴 자유 문구"},
]

_DOC_GROUP_ORDER = ("exchange", "major", "holding", "periodic")


def _response_of(question_id: str) -> dict | None:
    """A8 응답 하나를 읽는다. 없으면 None."""
    rel = f"artifacts/ablation/v2/A8/responses/{question_id}.json"
    try:
        return read_json(rel)["response"]
    except FileNotFoundError:
        return None


def _pick_cases(rows: list[dict], limit: int) -> list[dict]:
    """오답 선점 + 문서군 라운드로빈.

    오답을 먼저 넣는 이유는 성능 소개가 아니라 진단 화면이어야 하기 때문이다.
    남는 자리는 문서군 4종을 돌아가며 채워 exchange 편중을 막는다.
    """
    wrong = [r for r in rows if not r["correct"]]
    right = [r for r in rows if r["correct"]]
    picked: list[dict] = []
    seen: set[str] = set()

    for row in wrong[: max(2, limit // 8)]:
        picked.append(row)
        seen.add(row["question_id"])

    buckets = {g: [r for r in right if r["doc_group"] == g] for g in _DOC_GROUP_ORDER}
    index = 0
    guard = 0
    while len(picked) < limit and any(buckets.values()) and guard < limit * 8:
        guard += 1
        group = _DOC_GROUP_ORDER[index % len(_DOC_GROUP_ORDER)]
        index += 1
        bucket = buckets.get(group) or []
        while bucket:
            row = bucket.pop(0)
            if row["question_id"] not in seen:
                picked.append(row)
                seen.add(row["question_id"])
                break
    return picked[:limit]


def build_answers(limit: int = 24) -> dict:
    # 🔴 이월분. 아래 두 줄이 보안 ④⑤ 를 위반한다 — 되살릴 때 반드시 고칠 곳이다:
    #      "question": response["question"]   ← evalset 질문 전문
    #      "answer":   response["answer"]     ← 답변이 질문을 되풀어 쓴다 (④ 의 우회로)
    #    그리고 원천은 `ablation/{gen}/A8/report.json` 으로 옮겨야 한다
    #    (`error_analysis_v2.json` 은 옛 라벨을 들고 있다 — `total_plan §5`).
    #    `corp_name` 도 화이트리스트에서 일부러 뺀 필드다.
    rows = read_json("artifacts/eval/error_analysis_v2.json")["rows"]
    with_response = [r for r in rows if _response_of(r["question_id"])]
    picked = _pick_cases(with_response, limit)

    cases = []
    for row in picked:
        response = _response_of(row["question_id"])
        if not response:
            continue
        cases.append(
            {
                "questionId": row["question_id"],
                "question": response["question"],
                "difficulty": row["difficulty"],
                "docGroup": row["doc_group"],
                "taskType": row["task_type"],
                "answerMode": row["answer_mode"],
                "track": row["track"],
                "corpName": row["corp_name"] or [],
                "correct": bool(row["correct"]),
                "firstFailure": row["first_failure"],
                "rootCause": row["root_cause"] or None,
                "hasCitation": bool(row["has_citation"]),
                "nHops": row["n_hops"],
                "nSteps": row["n_steps"],
                "nLlmCalls": row["n_llm_calls"],
                "latencyS": round(float(row["latency_s"]), 3),
                "answer": response["answer"],
                "evidence": parse_evidence(response["retrieved_context"]),
                "steps": parse_think_trace(response["think_trace"]),
            }
        )

    return {
        "metadata": metadata(
            "질의응답 트레이스 — A8 응답의 근거·추론단계·답변. golden 라벨은 싣지 않는다.",
            [
                "artifacts/eval/error_analysis_v2.json",
                "artifacts/ablation/v2/A8/responses/Q-*.json",
            ],
        ),
        "stepLegend": STEP_LEGEND,
        "cases": cases,
    }
```

`BUILDERS` 에 한 줄 추가:

```python
    "answers": ("site_answers.json", build_answers),
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_site_data.py -k "answers" -v`
Expected: 6건 PASS.

`test_build_answers_covers_all_doc_groups` 가 실패하면 어느 문서군에 정답 응답이 없는지 확인한다:
```bash
python -c "
import json,os
rows=json.load(open('artifacts/eval/error_analysis_v2.json',encoding='utf-8'))['rows']
from collections import Counter
c=Counter()
for r in rows:
    if os.path.exists(f\"artifacts/ablation/v2/A8/responses/{r['question_id']}.json\"):
        c[(r['doc_group'], bool(r['correct']))]+=1
print(sorted(c.items()))
"
```

- [ ] **Step 5: 생성과 유출 게이트 재확인**

Run:
```bash
python scripts/build_site_data.py --only answers
python -m pytest tests/test_site_data.py -v
```
Expected: 전건 PASS. `site_answers.json` 생성.

- [ ] **Step 6: `site/components/TraceViewer.tsx` 작성**

```tsx
"use client";

import { useState } from "react";

type Step = { kind: string; raw: string; fields: Record<string, string> };
type Evidence = {
  title: string;
  rceptNo: string | null;
  dartUrl: string | null;
  lines: string[];
};

export type Case = {
  questionId: string;
  question: string;
  difficulty: string;
  docGroup: string;
  taskType: string;
  answerMode: string;
  track: string;
  corpName: string[];
  correct: boolean;
  firstFailure: string | null;
  rootCause: string | null;
  hasCitation: boolean;
  nHops: number;
  nSteps: number;
  nLlmCalls: number;
  latencyS: number;
  answer: string;
  evidence: Evidence[];
  steps: Step[];
};

export type Legend = { kind: string; label: string; note: string };

export default function TraceViewer({
  cases,
  stepLegend,
}: {
  cases: Case[];
  stepLegend: Legend[];
}) {
  const [activeId, setActiveId] = useState(cases[0]?.questionId ?? "");
  const active = cases.find((item) => item.questionId === activeId) ?? cases[0];

  if (!active) {
    return <p className="empty-note">표시할 문항이 없습니다.</p>;
  }

  const legendOf = (kind: string) => stepLegend.find((item) => item.kind === kind);

  return (
    <div className="trace-layout">
      <aside className="trace-list" aria-label="문항 목록">
        {cases.map((item) => (
          <button
            type="button"
            key={item.questionId}
            className={`trace-list-item ${item.questionId === active.questionId ? "is-active" : ""}`}
            onClick={() => setActiveId(item.questionId)}
          >
            <span className="trace-list-head">
              <strong>{item.questionId}</strong>
              <em className={item.correct ? "ok" : "bad"}>{item.correct ? "정답" : "오답"}</em>
            </span>
            <span className="trace-list-q">{item.question}</span>
            <span className="trace-list-tags">
              {item.docGroup} · {item.difficulty} · {item.track}
            </span>
          </button>
        ))}
      </aside>

      <div className="trace-detail">
        <div className="panel">
          <p className="eyebrow">질문 · {active.questionId}</p>
          <h2 className="trace-question">{active.question}</h2>
          <div className="trace-chips">
            <span className="chip">{active.docGroup}</span>
            <span className="chip">난이도 {active.difficulty}</span>
            <span className="chip">{active.taskType}</span>
            <span className="chip">{active.answerMode}</span>
            {active.corpName.map((name) => (
              <span className="chip" key={name}>
                {name}
              </span>
            ))}
            <span className={`chip ${active.correct ? "chip-ok" : "chip-bad"}`}>
              {active.correct ? "정답" : `오답 · ${active.firstFailure ?? "?"}`}
            </span>
          </div>
          {!active.correct && active.rootCause ? (
            <p className="helper">근본 원인: {active.rootCause}</p>
          ) : null}
        </div>

        <section className="panel spacious">
          <p className="eyebrow">1. 근거 — 접수번호 단위</p>
          <h3>회수된 공시 {active.evidence.length}건</h3>
          {active.evidence.length === 0 ? (
            <p className="empty-note">회수된 근거가 없습니다.</p>
          ) : (
            <div className="grid evidence-grid">
              {active.evidence.map((block, blockIndex) => (
                <article
                  className="card evidence-card"
                  key={`${block.rceptNo ?? "no-rcept"}-${blockIndex}`}
                >
                  <h4>{block.title}</h4>
                  {block.rceptNo && block.dartUrl ? (
                    <a
                      className="evidence-rcept"
                      href={block.dartUrl}
                      target="_blank"
                      rel="noreferrer"
                    >
                      접수번호 {block.rceptNo} · DART 원문 열기
                    </a>
                  ) : (
                    <p className="helper">접수번호 미확인 블록</p>
                  )}
                  <ul>
                    {block.lines.map((line, lineIndex) => (
                      <li key={lineIndex}>{line.replace(/^-\s*/, "")}</li>
                    ))}
                  </ul>
                </article>
              ))}
            </div>
          )}
        </section>

        <section className="panel spacious">
          <p className="eyebrow">2. 추론 트레이스</p>
          <h3>
            {active.steps.length}단계 · hop {active.nHops} · LLM {active.nLlmCalls}회 ·{" "}
            {active.latencyS}초
          </h3>
          <ol className="step-list">
            {active.steps.map((step, stepIndex) => {
              const legend = legendOf(step.kind);
              const fields = Object.entries(step.fields);
              return (
                <li className={`step step-${step.kind}`} key={stepIndex}>
                  <div className="step-head">
                    <span className="step-kind">{legend?.label ?? step.kind}</span>
                    {legend ? <span className="step-note">{legend.note}</span> : null}
                  </div>
                  {fields.length > 0 ? (
                    <div className="step-fields">
                      {fields.map(([key, value]) => (
                        <span className="step-field" key={key}>
                          <b>{key}</b>
                          {value === "" ? "-" : value}
                        </span>
                      ))}
                    </div>
                  ) : (
                    <p className="step-raw">{step.raw}</p>
                  )}
                </li>
              );
            })}
          </ol>
        </section>

        <section className="panel spacious">
          <p className="eyebrow">3. 답변</p>
          <h3>{active.hasCitation ? "근거 표기 있음" : "근거 표기 누락"}</h3>
          <pre className="answer-block">{active.answer}</pre>
        </section>
      </div>
    </div>
  );
}
```

- [ ] **Step 7: `site/app/globals.css` 에 트레이스 스타일 추가 (반응형 블록 앞)**

```css
/* ── 트레이스 화면 ──────────────────────────────────── */
.trace-layout {
  display: grid;
  grid-template-columns: minmax(0, 300px) minmax(0, 1fr);
  gap: 20px;
  align-items: start;
}

.trace-list {
  display: grid;
  gap: 8px;
  max-height: 78vh;
  overflow-y: auto;
  padding-right: 4px;
}

.trace-list-item {
  display: grid;
  gap: 4px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: #ffffff;
  padding: 10px 12px;
  cursor: pointer;
  font: inherit;
  text-align: left;
}

.trace-list-item.is-active {
  border-color: var(--primary);
  background: #eef2ff;
}

.trace-list-head {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  font-size: 12px;
  font-weight: 800;
}

.trace-list-head em {
  font-style: normal;
}

.trace-list-head em.ok {
  color: var(--teal);
}

.trace-list-head em.bad {
  color: var(--danger);
}

.trace-list-q {
  color: var(--text);
  font-size: 13px;
  line-height: 1.45;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.trace-list-tags {
  color: var(--muted);
  font-size: 11px;
  font-weight: 700;
}

.trace-question {
  margin: 8px 0 14px;
  font-size: 20px;
  line-height: 1.45;
}

.trace-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.chip-ok {
  border-color: #9fd4cb;
  background: #e6f6f3;
  color: var(--teal);
}

.chip-bad {
  border-color: #edb9b4;
  background: #fdecea;
  color: var(--danger);
}

.evidence-grid {
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  margin-top: 14px;
}

.evidence-card h4 {
  margin: 0 0 8px;
  font-size: 14px;
  line-height: 1.45;
}

.evidence-rcept {
  display: inline-block;
  margin-bottom: 8px;
  color: var(--primary);
  font-size: 12px;
  font-weight: 800;
  text-decoration: underline;
}

.evidence-card ul {
  margin: 0;
  padding-left: 16px;
  color: var(--muted);
  font-size: 13px;
}

.step-list {
  margin: 14px 0 0;
  padding-left: 0;
  list-style: none;
  display: grid;
  gap: 10px;
}

.step {
  border: 1px solid var(--line);
  border-left: 4px solid var(--line);
  border-radius: 8px;
  background: var(--surface);
  padding: 12px 14px;
}

.step-route { border-left-color: var(--primary); }
.step-search { border-left-color: var(--teal); }
.step-verdict { border-left-color: var(--ink-soft); }
.step-generate { border-left-color: var(--primary-dark); }
.step-critic { border-left-color: var(--amber); }
.step-verify { border-left-color: var(--teal); }
.step-budget { border-left-color: var(--line); }
.step-note { border-left-color: var(--amber); }

.step-head {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 10px;
}

.step-kind {
  font-size: 13px;
  font-weight: 800;
  color: var(--ink-soft);
}

.step-note {
  color: var(--muted);
  font-size: 12px;
}

.step-fields {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 8px;
}

.step-field {
  border: 1px solid var(--line);
  border-radius: 6px;
  background: var(--surface-strong);
  padding: 3px 8px;
  font-size: 12px;
  font-variant-numeric: tabular-nums;
  max-width: 100%;
  overflow-wrap: anywhere;
}

.step-field b {
  margin-right: 6px;
  color: var(--muted);
  font-weight: 700;
}

.step-raw {
  margin: 8px 0 0;
  color: var(--text);
  font-size: 13px;
}

.answer-block {
  margin: 12px 0 0;
  padding: 16px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--surface-strong);
  font: inherit;
  font-size: 14px;
  line-height: 1.7;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

@media (max-width: 900px) {
  .trace-layout {
    grid-template-columns: minmax(0, 1fr);
  }
  .trace-list {
    max-height: 320px;
  }
}
```

- [ ] **Step 8: `site/app/answers/page.tsx` 작성**

```tsx
import data from "../../public/data/site_answers.json";
import TraceViewer, { type Case, type Legend } from "../../components/TraceViewer";

const cases = data.cases as Case[];
const legend = data.stepLegend as Legend[];

export default function AnswersPage() {
  const correct = cases.filter((item) => item.correct).length;

  return (
    <main className="section">
      <div className="container">
        <div className="section-heading">
          <span className="badge">질의응답 트레이스 · 제출 구성 A8</span>
          <h1>근거에서 답변까지, 한 문항씩 그대로 펼쳐본다</h1>
          <p className="lead">
            실제 응답 {cases.length}건입니다. 정답 {correct}건과 오답 {cases.length - correct}건을 함께
            실었습니다 — 오답을 빼면 이 화면은 성능 소개가 되고, 무엇이 왜 틀렸는지는 보이지 않습니다.
            접수번호를 누르면 DART 원문으로 갑니다.
          </p>
        </div>

        <TraceViewer cases={cases} stepLegend={legend} />
      </div>
    </main>
  );
}
```

- [ ] **Step 9: 타입체크와 빌드**

Run: `cd site && npm run typecheck && npm run build`
Expected: 오류 0건, `/answers` 프리렌더 성공.

- [ ] **Step 10: 육안 확인**

Run: `cd site && npm run dev`
Expected: `/answers` 에서 좌측 문항 목록(정답/오답 라벨), 우측에 질문 → 근거 카드(접수번호 링크) → 단계 목록(라우팅·검색·충분성 판정·생성·critic·검증·예산이 좌측 색선으로 구분) → 답변 블록. 다른 문항을 눌러도 즉시 바뀐다. 접수번호 링크가 DART 뷰어로 열린다. 확인 후 종료.

- [ ] **Step 11: 커밋**

```bash
git add scripts/build_site_data.py tests/test_site_data.py site/app/answers site/app/globals.css site/components/TraceViewer.tsx site/public/data/site_answers.json
git commit -m "feat: 질의응답 트레이스 화면 — 근거·추론단계·답변"
```

---

### Task 6: 상세 검색 — 평가셋 문항 탐색

> 🔴 **2026-08-14 재검토 — `questions[].question` 을 스키마에서 뺐다 (보안 ④).**
> 종전 판은 `evalset/{questions,regression,valuechain}.jsonl` 의 **질문 전문 전량**을
> `site_search.json` 에 실었다. 축 메타·채점 결과만 남기고 **질문 문자열은 읽고 버린다.**
> 검색창은 `question_id`·기업명·문서군·난이도·트랙을 대상으로 한다.
> `corpName`·`sector` 는 산출물의 값이고 질문 전문이 아니므로 남긴다.
>
> 🔴 **그리고 이 Task 도 `R6-MVP` 에는 없다** — 클라이언트 필터 컴포넌트가 필요해서
> 정적 HTML 5쪽 범위를 넘는다. MVP 는 `/errors` 의 **문항 ID 표**로 같은 정보를 낸다.

**Files:**
- Modify: `scripts/build_site_data.py` (`build_search` 추가)
- Modify: `tests/test_site_data.py`
- Create: `site/components/QuestionExplorer.tsx`
- Create: `site/app/search/page.tsx`
- Modify: `site/app/globals.css`
- Create: `site/public/data/site_search.json` (생성물)

**Interfaces:**
- Consumes: `read_json` · `read_jsonl` · `metadata` (Task 2) · `read_jsonl_unsafe_heldout_ids` (Task 4, 테스트용)
- Produces:
  - `TRACK_LABELS: dict[str, str]` · `TRACK_NOTES: dict[str, str]`
  - `build_search() -> dict`
  - `site_search.json` 스키마:
    - `metadata`
    - `facets: { docGroup: str[], difficulty: str[], taskType: str[], answerMode: str[], track: str[] }`
    - `tracks: [{ id: str, label: str, n: int, note: str }]`
    - `questions: [{ questionId, difficulty, docGroup, taskType, answerMode, track, answerable, corpName, sector, correct, firstFailure }]` — `correct` 는 미측정 시 `null`. 🔴 **`question` 키는 없다 (보안 ④)**
  - `QuestionExplorer` 컴포넌트 + `export type Question` · `export type Facets`

- [ ] **Step 1: 실패하는 테스트를 쓴다 — `tests/test_site_data.py` 맨 끝에 추가**

```python
# ── 상세 검색 ────────────────────────────────────────────────────────

def test_build_search_shape():
    payload = bsd.build_search()
    assert set(payload) == {"metadata", "facets", "tracks", "questions"}
    assert set(payload["facets"]) == {"docGroup", "difficulty", "taskType", "answerMode", "track"}
    q = payload["questions"][0]
    assert {
        "questionId", "question", "difficulty", "docGroup", "taskType",
        "answerMode", "track", "answerable", "corpName", "sector",
        "correct", "firstFailure",
    } == set(q)


def test_build_search_track_counts_match_questions():
    payload = bsd.build_search()
    counted: dict[str, int] = {}
    for q in payload["questions"]:
        counted[q["track"]] = counted.get(q["track"], 0) + 1
    for track in payload["tracks"]:
        assert counted.get(track["id"], 0) == track["n"], track["id"]


def test_build_search_excludes_heldout_ids():
    payload = bsd.build_search()
    heldout_ids = {r["question_id"] for r in bsd.read_jsonl_unsafe_heldout_ids()}
    ids = {q["questionId"] for q in payload["questions"]}
    assert not (ids & heldout_ids)


def test_build_search_has_no_answer_values():
    """정답값·근거를 검색 화면에 싣지 않는다 — 문항 메타와 채점 결과만 보여준다."""
    payload = bsd.build_search()
    for q in payload["questions"]:
        assert "answer" not in q
        assert "evidence" not in q


def test_build_search_has_four_tracks():
    payload = bsd.build_search()
    assert [t["id"] for t in payload["tracks"]] == [
        "grid", "info_limit", "regression", "valuechain",
    ]
    assert all(t["n"] > 0 for t in payload["tracks"])


def test_build_search_question_ids_are_unique():
    payload = bsd.build_search()
    ids = [q["questionId"] for q in payload["questions"]]
    assert len(ids) == len(set(ids))
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_site_data.py -k "search" -v`
Expected: FAIL — `AttributeError: module 'build_site_data' has no attribute 'build_search'`

- [ ] **Step 3: `build_search` 구현 — `build_answers` 아래, `BUILDERS` 위**

```python
TRACK_LABELS = {
    "grid": "dev 그리드",
    "info_limit": "정보한계",
    "regression": "회귀",
    "valuechain": "밸류체인",
}

TRACK_NOTES = {
    "grid": "3축 그리드. 정답률 판정에 쓰는 유일한 분모다",
    "info_limit": "원본에 값이 없는 질의. 거절이 정답이다",
    "regression": "고친 실패의 재발 감시. 그리드 밖에 두어 난이도 축을 오염시키지 않는다",
    "valuechain": "발주처를 노드로 승격해야만 풀리는 질의. 별도 파일이다",
}

_TRACK_ORDER = ("grid", "info_limit", "regression", "valuechain")


def build_search() -> dict:
    gen = resolve_generation()
    rows = {
        r["question_id"]: {k: r[k] for k in ROW_FIELDS_ALLOWED if k in r}
        for r in read_json(f"artifacts/ablation/{gen}/A8/report.json")["rows"]
    }

    # (파일, 강제 트랙). questions.jsonl 은 grid 와 info_limit 이 섞여 있어
    # error_analysis 의 track 값을 그대로 쓴다.
    sources: list[tuple[str, str | None]] = [
        ("evalset/questions.jsonl", None),
        ("evalset/regression.jsonl", "regression"),
        ("evalset/valuechain.jsonl", "valuechain"),
    ]

    questions = []
    seen: set[str] = set()
    for rel, forced_track in sources:
        for item in read_jsonl(rel):
            qid = item["question_id"]
            if qid in seen:
                continue
            seen.add(qid)
            row = rows.get(qid)
            if forced_track:
                track = forced_track
            elif row:
                track = row["track"]
            else:
                track = "info_limit" if not item.get("answerable", True) else "grid"
            questions.append(
                {
                    # 🔴 `item["question"]` 을 읽지 않는다 (보안 ④). 축 메타만 쓴다.
                    "questionId": qid,
                    "difficulty": item.get("difficulty") or (row or {}).get("difficulty") or "-",
                    "docGroup": (row or {}).get("doc_group") or "-",
                    "taskType": item.get("task_type") or "-",
                    "answerMode": item.get("answer_mode") or "-",
                    "track": track,
                    "answerable": bool(item.get("answerable", True)),
                    "correct": None if row is None else bool(row["correct"]),
                    "firstFailure": (row or {}).get("first_failure"),
                }
            )

    def uniq(key: str) -> list[str]:
        return sorted({q[key] for q in questions if q[key] and q[key] != "-"})

    counts: dict[str, int] = {}
    for q in questions:
        counts[q["track"]] = counts.get(q["track"], 0) + 1

    tracks = [
        {
            "id": tid,
            "label": TRACK_LABELS[tid],
            "n": counts.get(tid, 0),
            "note": TRACK_NOTES[tid],
        }
        for tid in _TRACK_ORDER
    ]

    return {
        "metadata": metadata(
            "상세 검색 — 축 메타와 채점 결과만. 🔴 질문 전문·정답값·근거·기업명을 "
            "싣지 않는다 (보안 ④⑤). heldout 제외.",
            [
                "evalset/questions.jsonl",
                "evalset/regression.jsonl",
                "evalset/valuechain.jsonl",
                f"artifacts/ablation/{gen}/A8/report.json",
            ],
        ),
        "facets": {
            "docGroup": uniq("docGroup"),
            "difficulty": ["하", "중", "상"],
            "taskType": uniq("taskType"),
            "answerMode": uniq("answerMode"),
            "track": list(_TRACK_ORDER),
        },
        "tracks": tracks,
        "questions": questions,
    }
```

`BUILDERS` 에 한 줄 추가:

```python
    "search": ("site_search.json", build_search),
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_site_data.py -k "search" -v`
Expected: 6건 PASS. 문항 수는 `grid` 72 · `info_limit` 6 · `regression` 10 · `valuechain` 12 = 100건이다 (`questions.jsonl` 78행 = 그리드 dev 72 + 정보한계 6).

- [ ] **Step 5: 생성과 유출 게이트 재확인**

Run:
```bash
python scripts/build_site_data.py --only search
python -m pytest tests/test_site_data.py -v
```
Expected: 전건 PASS.

- [ ] **Step 6: `site/components/QuestionExplorer.tsx` 작성**

```tsx
"use client";

import Link from "next/link";
import { useMemo, useState } from "react";

export type Question = {
  questionId: string;
  question: string;
  difficulty: string;
  docGroup: string;
  taskType: string;
  answerMode: string;
  track: string;
  answerable: boolean;
  corpName: string[];
  sector: string;
  correct: boolean | null;
  firstFailure: string | null;
};

export type Facets = {
  docGroup: string[];
  difficulty: string[];
  taskType: string[];
  answerMode: string[];
  track: string[];
};

const ALL = "전체";

export default function QuestionExplorer({
  initialQuery,
  questions,
  facets,
}: {
  initialQuery: string;
  questions: Question[];
  facets: Facets;
}) {
  const [query, setQuery] = useState(initialQuery);
  const [track, setTrack] = useState(ALL);
  const [docGroup, setDocGroup] = useState(ALL);
  const [difficulty, setDifficulty] = useState(ALL);

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return questions.filter((item) => {
      if (track !== ALL && item.track !== track) return false;
      if (docGroup !== ALL && item.docGroup !== docGroup) return false;
      if (difficulty !== ALL && item.difficulty !== difficulty) return false;
      if (!needle) return true;
      const haystack = [
        item.questionId,
        item.question,
        item.sector,
        item.taskType,
        ...item.corpName,
      ]
        .join(" ")
        .toLowerCase();
      return haystack.includes(needle);
    });
  }, [questions, query, track, docGroup, difficulty]);

  return (
    <div>
      <div className="panel filter-panel">
        <label className="filter-search" htmlFor="explorer-q">
          검색어
          <input
            id="explorer-q"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="문항 번호 · 기업 · 섹터 · 질문 본문"
            autoComplete="off"
          />
        </label>
        <div className="filter-row">
          <label>
            트랙
            <select value={track} onChange={(event) => setTrack(event.target.value)}>
              {[ALL, ...facets.track].map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </select>
          </label>
          <label>
            문서군
            <select value={docGroup} onChange={(event) => setDocGroup(event.target.value)}>
              {[ALL, ...facets.docGroup].map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </select>
          </label>
          <label>
            난이도
            <select value={difficulty} onChange={(event) => setDifficulty(event.target.value)}>
              {[ALL, ...facets.difficulty].map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </select>
          </label>
        </div>
        <p className="helper">
          {filtered.length}건 / 전체 {questions.length}건
        </p>
      </div>

      {filtered.length === 0 ? (
        <p className="empty-note">조건에 맞는 문항이 없습니다.</p>
      ) : (
        <div className="panel spacious">
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>문항</th>
                  <th>질문</th>
                  <th>트랙</th>
                  <th>문서군</th>
                  <th>난이도</th>
                  <th>유형</th>
                  <th>채점</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((item) => (
                  <tr key={item.questionId}>
                    <td>
                      <strong>{item.questionId}</strong>
                      <span>{item.sector}</span>
                    </td>
                    <td>
                      {item.question}
                      {item.corpName.length > 0 ? <span>{item.corpName.join(" · ")}</span> : null}
                    </td>
                    <td>{item.track}</td>
                    <td>{item.docGroup}</td>
                    <td>{item.difficulty}</td>
                    <td>
                      {item.taskType}
                      <span>{item.answerable ? item.answerMode : "answerable=false"}</span>
                    </td>
                    <td>
                      {item.correct === null ? (
                        <span>미측정</span>
                      ) : item.correct ? (
                        <strong className="ok-text">정답</strong>
                      ) : (
                        <>
                          <strong className="bad-text">오답</strong>
                          <span>{item.firstFailure ?? "-"}</span>
                        </>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      <p className="helper">
        문항별 근거와 추론 트레이스는 <Link href="/answers">질의응답 트레이스</Link> 에서 볼 수 있습니다.
      </p>
    </div>
  );
}
```

- [ ] **Step 7: `site/app/globals.css` 에 필터 스타일 추가 (반응형 블록 앞)**

```css
/* ── 필터 ───────────────────────────────────────────── */
.filter-panel {
  display: grid;
  gap: 14px;
}

.filter-search {
  display: grid;
  gap: 8px;
  color: var(--ink-soft);
  font-weight: 800;
}

.filter-search input {
  min-height: 48px;
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 0 14px;
  font: inherit;
  font-size: 15px;
  font-weight: 400;
}

.filter-row {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
}

.filter-row label {
  display: grid;
  gap: 6px;
  color: var(--ink-soft);
  font-size: 13px;
  font-weight: 800;
}

.filter-row select {
  min-height: 42px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: #ffffff;
  padding: 0 10px;
  font: inherit;
  font-weight: 400;
}

.ok-text {
  color: var(--teal);
}

.bad-text {
  color: var(--danger);
}

@media (max-width: 640px) {
  .filter-row {
    grid-template-columns: minmax(0, 1fr);
  }
}
```

- [ ] **Step 8: `site/app/search/page.tsx` 작성**

레퍼런스 `app/search/page.tsx` 의 `searchParams` 처리 방식 그대로다 (Next 15 는 `searchParams` 가 Promise).

```tsx
import { Suspense } from "react";
import data from "../../public/data/site_search.json";
import QuestionExplorer, { type Facets, type Question } from "../../components/QuestionExplorer";

type SearchPageProps = {
  searchParams?: Promise<{ query?: string | string[] }>;
};

export default async function SearchPage({ searchParams }: SearchPageProps) {
  const params = await searchParams;
  const raw = params?.query;
  const initialQuery = Array.isArray(raw) ? raw[0] ?? "" : raw ?? "";

  return (
    <main className="section">
      <div className="container">
        <div className="section-heading">
          <span className="badge">상세 검색</span>
          <h1>평가셋 {data.questions.length}문항을 트랙·문서군·난이도로 탐색</h1>
          <p className="lead">
            트랙마다 분모와 목적이 다릅니다. 정답률 판정에 쓰는 것은 dev 그리드 하나뿐이고, 나머지는
            감시·완결성·거절을 각각 따로 봅니다. 그래서 네 트랙을 합산한 수를 두지 않았습니다.
          </p>
        </div>

        <section className="grid grid-4" aria-label="트랙 요약">
          {data.tracks.map((track) => (
            <article className="card" key={track.id}>
              <p className="eyebrow">{track.label}</p>
              <div className="metric">{track.n}</div>
              <p className="helper">{track.note}</p>
            </article>
          ))}
        </section>

        <div className="spacious">
          <Suspense fallback={null}>
            <QuestionExplorer
              initialQuery={initialQuery}
              questions={data.questions as Question[]}
              facets={data.facets as Facets}
            />
          </Suspense>
        </div>
      </div>
    </main>
  );
}
```

- [ ] **Step 9: 타입체크와 빌드**

Run: `cd site && npm run typecheck && npm run build`
Expected: 오류 0건. `/search` 가 라우트 목록에 뜬다.

- [ ] **Step 10: 홈 → 검색 연결 확인**

Run: `cd site && npm run dev`
Expected: 홈 예시 칩을 누르면 `/search?query=...` 로 이동하고 검색어가 입력창에 반영된 상태로 표가 걸러진다. 트랙 카드 4장이 `72 · 6 · 10 · 12`. 트랙/문서군/난이도 선택이 즉시 반영된다. 확인 후 종료.

- [ ] **Step 11: 커밋**

```bash
git add scripts/build_site_data.py tests/test_site_data.py site/app/search site/app/globals.css site/components/QuestionExplorer.tsx site/public/data/site_search.json
git commit -m "feat: 상세 검색 — 트랙·문서군·난이도 탐색"
```

---

### Task 7: 기여도 사다리 — A0→A8 · LOO · 비용 원장

이 프로젝트의 방법론이 가장 잘 드러나는 화면이다. **"측정이 계획을 뒤집었다"** 를 화면으로 보여준다 — `A0→A1` 이 마이너스라는 사실, `A5` 이후 Δ 가 0 인데도 넣은 이유(한계 비용), LOO 세 개.

**Files:**
- Modify: `scripts/build_site_data.py` (`build_ablation` 추가)
- Modify: `tests/test_site_data.py`
- Create: `site/components/LadderBars.tsx`
- Create: `site/components/Heatmap.tsx`
- Create: `site/app/ablation/page.tsx`
- Modify: `site/app/globals.css`
- Create: `site/public/data/site_ablation.json` (생성물)

**Interfaces:**
- Consumes: `read_json` · `metadata` (Task 2) · `StatCard` (Task 1)
- Produces:
  - `LADDER_ORDER: tuple[str, ...]` = `("A0","A1","A1b","A4","A5","A6","A7","A8")`
  - `LOO_ORDER: tuple[str, ...]` = `("L1","L2","L3","L4")`
  - `DIAGNOSTIC_OR_REJECTED: tuple[str, ...]` = `("ORACLE","A8-lean")`
  - `resolve_ladder_generation() -> str` · `_fill_findings(ladder_arms) -> list[dict]`
  - `build_ablation() -> dict`
  - `site_ablation.json` 스키마:
    - `metadata`
    - `generations: { ladder: {arm, generated}, current: {arm, generated}, warning: str }` — 🔴 필수
    - `cohort: { nCases: int, nGrid: int, priceWonPer1k: float }`
    - `ladder: [{ arm, label, note, correct, n, rate, deltaPp: float|null, tokens, costWon, llmCalls, goldenHit, goldenTotal, citationRate, hallucination }]`
    - `loo: [{ arm, label, note, correct, n, rate, deltaPp, generation }]` — `deltaPp` 는 **같은 세대** A8 대비
    - `axes: [{ axis: str, rows: [{ key: str, cells: [{ arm, correct, n, rate }] }] }]`
    - `ledger` + `ledgerTotalWon` + `creditWon` + `ledgerSource` — 🔴 **`parse_ssot()` 파싱값**
    - `findings: [{ title: str, body: str }]` · `notAdopted` · `verdictSource`
  - `LadderBars` 컴포넌트: `{ rows: LadderRow[] }`
  - `Heatmap` 컴포넌트: `{ arms: string[]; rows: HeatRow[] }`

- [ ] **Step 1: 실패하는 테스트를 쓴다 — `tests/test_site_data.py` 맨 끝에 추가**

```python
# ── Ablation ─────────────────────────────────────────────────────────

def test_build_ablation_shape():
    payload = bsd.build_ablation()
    assert set(payload) == {
        "metadata", "generations", "cohort", "ladder", "loo", "axes",
        "ledger", "ledgerTotalWon", "creditWon", "ledgerSource",
        "findings", "notAdopted", "verdictSource",
    }
    assert [row["arm"] for row in payload["ladder"]] == list(bsd.LADDER_ORDER)
    assert [row["arm"] for row in payload["loo"]] == [
        name for name in bsd.LOO_ORDER if name in
        bsd.read_json(f"artifacts/ablation/{bsd.resolve_generation()}/summary.json")["summary"]["arms"]
    ]


def test_build_ablation_labels_both_generations():
    """🔴 사다리와 LOO 의 세대가 다르다 — 라벨 없이 렌더하면 화면이 세대를 섞는다.

    실측 2026-08-14: 사다리 전 arm(A0~A8)은 v2 에만 있고 v3 는 A8·LOO·진단 arm 만 갖는다.
    """
    payload = bsd.build_ablation()
    gens = payload["generations"]
    assert gens["ladder"]["arm"] and gens["current"]["arm"]
    assert gens["ladder"]["generated"] and gens["current"]["generated"]
    assert "빼지 않는다" in gens["warning"]


def test_build_ablation_ladder_matches_its_own_generation():
    """사다리 수치는 **사다리 세대의** summary.json 을 그대로 옮긴 것이어야 한다."""
    gen = bsd.resolve_ladder_generation()
    summary = bsd.read_json(f"artifacts/ablation/{gen}/summary.json")["summary"]["arms"]
    payload = bsd.build_ablation()
    for row in payload["ladder"]:
        arm = summary[row["arm"]]
        assert row["correct"] == arm["correct"]
        assert row["n"] == arm["n_scored"]
        assert row["tokens"] == arm["tokens"]


def test_build_ablation_a0_to_a1_delta_is_negative():
    """핵심 발견 — A0→A1 이 마이너스다. 🔴 값이 아니라 **부호**만 단언한다."""
    payload = bsd.build_ablation()
    a1 = next(row for row in payload["ladder"] if row["arm"] == "A1")
    assert a1["deltaPp"] is not None
    assert a1["deltaPp"] < 0


def test_build_ablation_a1b_is_the_largest_single_delta():
    """`doc_subtype` 메타 축이 사다리 최대 전이다 — 이것이 '엣지 재정의는 채택됐다' 의 근거다."""
    payload = bsd.build_ablation()
    deltas = {row["arm"]: row["deltaPp"] for row in payload["ladder"] if row["deltaPp"] is not None}
    assert max(deltas, key=lambda k: deltas[k]) == "A1b"


def test_build_ablation_first_row_has_no_delta():
    payload = bsd.build_ablation()
    assert payload["ladder"][0]["arm"] == "A0"
    assert payload["ladder"][0]["deltaPp"] is None


def test_build_ablation_loo_deltas_are_same_generation():
    """🔴 LOO Δ 의 기준선은 **같은 세대의** A8 이어야 한다 (세대 혼합 뺄셈 금지)."""
    payload = bsd.build_ablation()
    gen = bsd.resolve_generation()
    a8 = bsd.read_json(f"artifacts/ablation/{gen}/summary.json")["summary"]["arms"]["A8"]
    base = a8["correct"] / a8["n_scored"] * 100
    for row in payload["loo"]:
        assert row["generation"] == gen
        assert row["deltaPp"] == round(row["rate"] * 100 - base, 1), row["arm"]


def test_build_ablation_axes_cover_three_axes():
    payload = bsd.build_ablation()
    assert {a["axis"] for a in payload["axes"]} == {"difficulty", "answer_mode", "task_type"}
    difficulty = next(a for a in payload["axes"] if a["axis"] == "difficulty")
    assert [row["key"] for row in difficulty["rows"]] == ["하", "중", "상"]


def test_build_ablation_ledger_mirrors_the_ssot():
    """🔴 원장은 `docs/total_plan.md` §1 의 **미러**여야 한다 — 사본이 아니다.

    종전 판은 8행을 코드에 상수로 들고 합계 12,846.7원을 단언했다. 그 값은 v2 세대이고
    지금 정본은 다르다. 이 테스트는 값을 모르고도 어긋남을 잡는다.
    """
    payload = bsd.build_ablation()
    ssot = bsd.parse_ssot()
    assert payload["ledger"] == ssot["spentLedger"]
    assert payload["ledgerTotalWon"] == ssot["spentWon"]
    assert round(sum(item["costWon"] for item in payload["ledger"]), 1) == payload["ledgerTotalWon"]


def test_build_site_data_has_no_hardcoded_currency():
    """🔴 빌드 스크립트 소스에 `12846.7` 류의 금액 리터럴이 없어야 한다.

    이 저장소가 열한 번 겪은 결함이 '같은 수를 두 곳에 적고 한쪽만 고치는 것' 이다.
    여기서는 **소스를 문자열로 훑어** 그 양식 자체를 금지한다.
    """
    src = (ROOT / "scripts" / "build_site_data.py").read_text(encoding="utf-8")
    code = "\n".join(
        line.split("#", 1)[0] for line in src.splitlines()
    )   # 주석은 검사 대상이 아니다 (이력 서술에 옛 값이 남아 있어도 된다)
    # 1,000 이상의 소수 리터럴 = 금액. 허용 예외는 credit 상수 100000 뿐이고 그것은 정수다.
    assert not re.findall(r"\b\d{3,}\.\d\b", code), "금액 리터럴이 소스에 있다 — 정본을 파싱하라"


def test_build_ablation_findings_are_present():
    payload = bsd.build_ablation()
    assert len(payload["findings"]) == 3
    assert all({"title", "body"} == set(f) for f in payload["findings"])
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_site_data.py -k "ablation" -v`
Expected: FAIL — `AttributeError: module 'build_site_data' has no attribute 'build_ablation'`

- [ ] **Step 3: `build_ablation` 구현 — `build_search` 아래, `BUILDERS` 위**

```python
LADDER_ORDER = ("A0", "A1", "A1b", "A4", "A5", "A6", "A7", "A8")
LOO_ORDER = ("L1", "L2", "L3", "L4")     # 🔴 L4(REFERS_TO 계열)는 v3 에서 신설됐다

# 🔴 진단 arm 은 사다리·LOO 어디에도 넣지 않는다. `summary.json` 의 `diagnostic` 플래그가
#    코드 쪽 제동이고, 화면 쪽 제동이 이 집합이다.
#    `ORACLE` — 평가 시점에 골든이 없어 제출 구성이 될 수 없다. **정답률을 성적으로 인용 금지.**
#    `A8-lean` — 🔴 **쟀고 기각됐다**(그리드 불변 · 오답 집합 문항 단위 동일).
DIAGNOSTIC_OR_REJECTED = ("ORACLE", "A8-lean")

ARM_LABELS = {
    "A0": "메타 프리필터",
    "A1": "+ BM25 (exchange 만)",
    "A1b": "+ 인덱스 커버리지 (doc_subtype 메타 축)",
    "A4": "+ Agent 루프",
    "A5": "+ 계산 Tool",
    "A6": "+ Verifier",
    "A7": "+ 거절 강제·시점 지정",
    "A8": "+ 발주처 2-hop",
    "L1": "A8 − 정정체인 hop",
    "L2": "A8 − major·holding 필드",
    "L3": "A8 − 발주처 2-hop",
    "L4": "A8 − REFERS_TO 계열",
}

ARM_NOTES = {
    "A0": "manifest 메타로만 후보를 좁힌다. 검색 없음",
    "A1": "BM25 를 켜지만 exchange 인덱스 1,469건만 본다",
    "A1b": "문서군 4종 전부에 인덱스를 붙인다. 성능의 대부분이 여기서 나온다",
    "A4": "ReAct 루프 · 정정체인 hop · 다중지점 분해. 난이도 '상' 이 여기서 튄다",
    "A5": "증감률·비중·합계 등 계산을 도구로 넘긴다",
    "A6": "계산·완결성 검증기. LLM 호출 0회이므로 한계 비용도 0이다",
    "A7": "정보한계 질의 거절 강제 + 시점 지정 질의 처리. 플래그가 없는 무조건 코드다",
    "A8": "발주처를 노드로 승격해 2-hop. 그리드에서는 발화 0건이고 밸류체인에서만 듣는다",
    "L1": "정정체인을 따라가지 않으면 무엇을 잃는가 — 최대 낙폭 축 둘 중 하나다",
    "L2": "주요사항·지분공시 스키마 필드를 빼면 무엇을 잃는가",
    "L3": "발주처 hop 을 빼면 무엇을 잃는가 — 그리드에서는 0, 밸류체인에서만 듣는다",
    "L4": "REFERS_TO 계열을 빼면 무엇을 잃는가 — 그리드 0, 회귀 트랙에서 1문항",
}

# 🔴 원장을 옮겨 적지 않는다. `parse_ssot()` 가 docs/total_plan.md §1 을 파싱한다 (Task 2).
#    종전 판은 이 자리에 8행을 상수로 들고 있었고 합계 12,846.7원을 적어 뒀다 —
#    그 값은 v2 세대이고 지금 정본은 다르며, 사본은 **다음 라운드에 또 낡는다.**
#    파서는 행 합과 `누적` 행을 둘 다 읽어 어긋나면 빌드를 깬다.
# 🔴 이 원장은 '신규 지출' 이다 — 산출물의 totals.cost_won(재생 비용)과 섞으면 이중 계상된다.

# 발견 서술. 🔴 **수치는 {중괄호} 자리표시자로만 두고 산출물에서 채운다.**
#    종전 판은 337,603 · 337,555 · 14.1원 · +56.9%p · +51.4%p · −8.3%p 를 문장에 박아 뒀고
#    전부 세대가 바뀌며 낡았다 (v3 R2D·R2E 재측정).
FINDINGS = [
    {
        "title": "A0 → A1 이 {deltaA0A1}%p — v1 에서 부호가 뒤집혔다",
        "body": "BM25 를 켰는데 정답률이 내려간다. 원인은 랭킹이 아니라 커버리지다. "
        "A1 은 정의상 exchange 인덱스만 보므로 주요사항·지분·정기공시 질의가 검색 단계에서 "
        "통째로 떨어진다. exchange 쪽은 오르고 나머지 문서군에서 그보다 많이 잃는다. "
        "커버리지가 랭킹보다 먼저라는 뜻이고, 그 몫이 곧 A1b 의 Δ 다.",
    },
    {
        "title": "Δ 가 0이 된 뒤의 arm 도 다 넣었다",
        "body": "판단 기준을 정답률 Δ 가 아니라 한계 비용으로 바꿨기 때문이다. A6 검증기는 "
        "LLM 0회라 비용이 0이고, A7 은 플래그가 없는 무조건 코드다. A8 은 그리드에서 발화가 "
        "0건이라 Δ 가 측정값이 아니라 정의상 0이다. 통제 증거는 A6·A7·A8·L3 의 토큰이 "
        "{controlTokens} 으로 전 항목 일치한다는 것 — 생성 프롬프트가 바이트 동일임을 "
        "LLM 0회로 확인했다. (A5 는 {a5Tokens} 이다. Verifier 경고문이 없어 open 채점기 "
        "프롬프트가 달라 judge 토큰이 {judgeTokenGap} 어긋난다. A5 를 통제 증거에 넣으면 "
        "산출물과 대조하는 사람에게 증거 전체가 의심받는다.) "
        "🔴 꺾이는 지점은 A5 가 아니라 A4 다 — A4=A5=A6=A7=A8 이다.",
    },
    {
        "title": "성능은 A1b·A4 두 arm 에서 나왔고, 둘 다 LLM 을 더 쓰는 것이 아니다",
        "body": "두 arm 의 Δ 합이 {twoArmSum}%p 인데 A0→A8 전체 순증은 {netGain}%p 다. "
        "두 arm 의 몫이 전체보다 큰 것은 사이에서 A1 이 깎았기 때문이고, 그래서 "
        "'성능의 몇 %' 같은 비율로 적을 수 없다. 두 arm 의 실체는 규칙 파싱과 도구 호출이다. "
        "A1b 는 `doc_subtype` 을 탐색 축이 아니라 **메타 축**으로 쓴 것이고, 그것이 "
        "사다리 최대 전이다 — 🔴 기각된 것은 GraphRAG 가 아니라 **기업—기업 엣지 정의** 하나다.",
    },
]


def _fill_findings(ladder_arms: dict) -> list[dict]:
    """FINDINGS 의 자리표시자를 산출물 값으로 채운다. 🔴 여기 리터럴 수치가 없다.

    🔴 **사다리 세대의 arms 만 받는다.** A0~A5 는 사다리 세대에만 있고 현 세대에는 없다
    (실측 2026-08-14: v3 의 `summary.arms` 는 A8·L1·L2·L3·L4·A8-lean·ORACLE 뿐이다).
    두 세대를 한 문장에 섞으면 그것이 `total_plan §0b` 가 금지한 세대 혼합 뺄셈이다.
    """
    def pp(name: str, base: str) -> str:
        return f"{(ladder_arms[name]['rate'] - ladder_arms[base]['rate']) * 100:+.1f}"

    a8_tokens = ladder_arms["A8"]["tokens"]
    a5_tokens = ladder_arms["A5"]["tokens"]
    values = {
        "deltaA0A1": pp("A1", "A0"),
        "controlTokens": f"{a8_tokens:,}",
        "a5Tokens": f"{a5_tokens:,}",
        "judgeTokenGap": f"{abs(a8_tokens - a5_tokens):,}",
        "twoArmSum": f"{(ladder_arms['A4']['rate'] - ladder_arms['A1']['rate']) * 100:+.1f}",
        "netGain": f"{(ladder_arms['A8']['rate'] - ladder_arms['A0']['rate']) * 100:+.1f}",
    }
    return [
        {"title": f["title"].format(**values), "body": f["body"].format(**values)}
        for f in FINDINGS
    ]


LADDER_ARMS_REQUIRED = ("A0", "A1", "A1b", "A4", "A5", "A6", "A7", "A8")


def resolve_ladder_generation() -> str:
    """🔴 사다리(A0→A8)를 가진 **가장 새로운** 세대를 고른다.

    사다리는 세대마다 다시 재는 것이 아니다 — v3·v4 는 `A8` 과 LOO 만 재측정한다.
    그래서 `/ablation` 화면은 **두 세대를 나란히 두고 라벨을 붙여야 한다.**
    """
    for gen in GENERATIONS:
        path = REPO / "artifacts" / "ablation" / gen / "summary.json"
        if not path.exists():
            continue
        arms = json.loads(path.read_text(encoding="utf-8"))["summary"]["arms"]
        if all(name in arms for name in LADDER_ARMS_REQUIRED):
            return gen
    raise FileNotFoundError("사다리 전 arm 을 가진 세대가 없다")


def _rate_pp(arm: dict) -> float:
    return arm["correct"] / arm["n_scored"] * 100


def build_ablation() -> dict:
    # 🔴 두 세대를 **따로** 읽고 라벨을 붙인다. 섞어 뺄셈하지 않는다.
    ladder_gen = resolve_ladder_generation()      # 사다리 A0→A8 을 가진 세대
    current_gen = resolve_generation()            # 현 세대 (A8 · LOO)
    raw = read_json(f"artifacts/ablation/{ladder_gen}/summary.json")
    meta = raw["meta"]
    summary = raw["summary"]
    arms = summary["arms"]
    current = read_json(f"artifacts/ablation/{current_gen}/summary.json")
    current_arms = current["summary"]["arms"]

    ladder = []
    previous: float | None = None
    for name in LADDER_ORDER:
        arm = arms[name]
        rate_pp = _rate_pp(arm)
        ladder.append(
            {
                "arm": name,
                "label": ARM_LABELS[name],
                "note": ARM_NOTES[name],
                "correct": arm["correct"],
                "n": arm["n_scored"],
                "rate": round(arm["rate"], 4),
                "deltaPp": None if previous is None else round(rate_pp - previous, 1),
                "tokens": arm["tokens"],
                "costWon": arm["arm_cost_won"],
                "llmCalls": arm["llm_calls"],
                "goldenHit": arm["golden_evidence_hit"],
                "goldenTotal": arm["golden_total"],
                "citationRate": arm["citation_rate"],
                "hallucination": arm["hallucination"],
            }
        )
        previous = rate_pp

    # 🔴 LOO 는 **현 세대**에서 뽑는다. 기준선도 같은 세대의 A8 이다.
    base_pp = _rate_pp(current_arms["A8"])
    loo = []
    for name in LOO_ORDER:
        arm = current_arms.get(name)
        if arm is None:          # 세대에 그 LOO 축이 없으면 조용히 빼지 않고 건너뛴 사실을 남긴다
            continue
        loo.append(
            {
                "arm": name,
                "label": ARM_LABELS[name],
                "note": ARM_NOTES[name],
                "correct": arm["correct"],
                "n": arm["n_scored"],
                "rate": round(arm["rate"], 4),
                "deltaPp": round(_rate_pp(arm) - base_pp, 1),
                "generation": current_gen,
            }
        )

    axis_order = {"difficulty": ["하", "중", "상"]}
    axes = []
    for axis_name, axis_data in summary["axes"].items():
        keys = axis_order.get(axis_name) or list(axis_data)
        rows = []
        for key in keys:
            per_arm = axis_data.get(key)
            if not per_arm:
                continue
            rows.append(
                {
                    "key": key,
                    "cells": [
                        {
                            "arm": name,
                            "correct": per_arm[name]["correct"],
                            "n": per_arm[name]["n"],
                            "rate": round(per_arm[name]["rate"], 4),
                        }
                        for name in LADDER_ORDER
                        if name in per_arm
                    ],
                }
            )
        axes.append({"axis": axis_name, "rows": rows})

    ssot = parse_ssot()
    return {
        "metadata": metadata(
            "Ablation — 사다리 8구성 + LOO + 축별 분해 + 비용 원장. "
            "🔴 사다리와 LOO 는 세대가 다르다. 라벨을 붙여 렌더하고 서로 빼지 않는다.",
            [
                f"artifacts/ablation/{ladder_gen}/summary.json",
                f"artifacts/ablation/{current_gen}/summary.json",
                SSOT_MD,
            ],
        ),
        # 🔴 두 세대 라벨. 화면에서 사다리 표와 LOO 표 머리에 각각 붙인다.
        "generations": {
            "ladder": {"arm": ladder_gen, "generated": meta["generated"]},
            "current": {"arm": current_gen, "generated": current["meta"]["generated"]},
            "warning": (
                "사다리와 LOO 의 세대가 다르면 두 표의 값을 서로 빼지 않는다. "
                "세대 간 입력 차이가 있어 그 뺄셈은 어느 시스템의 값도 아니다."
            ),
        },
        "cohort": {
            "nCases": meta["n_cases"],
            "nGrid": arms["A8"]["n_grid"],
            "priceWonPer1k": meta["price_won_per_1k"],
        },
        "ladder": ladder,
        "loo": loo,
        "axes": axes,
        # 🔴 원장은 정본 마크다운 파싱값이다. 여기에 상수 표를 두지 않는다.
        "ledger": ssot["spentLedger"],
        "ledgerTotalWon": ssot["spentWon"],
        "creditWon": ssot["creditWon"],
        "ledgerSource": f"{SSOT_MD} §1",
        "findings": _fill_findings(arms),
        # 🔴 기각·진단 arm 을 사다리에 섞지 않고 **따로** 낸다. 판정 정본은 링크로 가리킨다.
        "verdictSource": "docs/pipeline/09-ablation.md §I-5f ⑧",
        "notAdopted": [
            {"arm": name, "correct": current_arms[name]["correct"], "n": current_arms[name]["n_scored"]}
            for name in DIAGNOSTIC_OR_REJECTED
            if name in current_arms
        ],
    }
```

🔴 **`notAdopted` 의 정답률을 성적으로 렌더하지 마라.** `ORACLE` 은 진단 arm 이고(평가 시점에
골든이 없다) 현 세대에서 `A8` 보다 **낮다** — 상한도 아니다. `A8-lean` 은 **쟀고 기각**됐다.
화면에는 **"이것은 성적이 아니다" 라는 라벨과 함께** 낸다. 기각 목록의 정본은
`docs/pipeline/09-ablation.md §I-5f ⑧` 이고, 그 대장이 `A3`·`A11`·`A9`·`A8-lean`·`A16`·`T1-full`·
`T1-md`·`A2`·`A1b-clova`·`A4-rr` 을 들고 있다 — **사이트는 그 목록을 복사하지 않고 링크한다.**

`BUILDERS` 에 한 줄 추가:

```python
    "ablation": ("site_ablation.json", build_ablation),
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_site_data.py -k "ablation" -v`
Expected: 8건 PASS.

🔴 **`test_build_ablation_ledger_mirrors_the_ssot` 가 실패하는 두 경우를 가른다.**

| 증상 | 뜻 | 처방 |
|---|---|---|
| `parse_ssot()` 가 `ValueError`(행 합 ≠ `누적`) | **정본 안에서 두 수가 갈렸다** | 🔴 **사이트가 아니라 `docs/total_plan.md` 를 고친다.** §1 은 그 사고를 실제로 겪었다 — 표 · `누적` 행 · 검산식 · §0 머리말 **네 자리를 한 커밋에서** 고친다 |
| `payload["ledger"] != ssot["spentLedger"]` | 빌드가 파싱값을 가공했다 | 빌드에서 가공을 뺀다. **원장은 미러다** |
| `parse_ssot()` 가 `ValueError`(표를 못 찾음) | 문서 서식이 바뀌었다 | 파서를 고친다. 🔴 **값을 코드에 옮겨 적어 우회하지 않는다** |

🔴 **어느 경우에도 `COST_LEDGER` 같은 상수를 되살리지 않는다.** 종전 판이 그 상수를 두었고,
그것이 이 계획서가 세 세대를 낡은 채로 살아 있게 한 원인이다.

- [ ] **Step 5: 생성과 유출 게이트 재확인**

Run:
```bash
python scripts/build_site_data.py --only ablation
python -m pytest tests/test_site_data.py -v
```
Expected: 전건 PASS.

- [ ] **Step 6: `site/components/LadderBars.tsx` 작성 (서버 컴포넌트, CSS 막대)**

```tsx
export type LadderRow = {
  arm: string;
  label: string;
  note: string;
  correct: number;
  n: number;
  rate: number;
  deltaPp: number | null;
};

export default function LadderBars({ rows }: { rows: LadderRow[] }) {
  const max = Math.max(...rows.map((row) => row.rate), 0.0001);

  return (
    <div className="ladder">
      {rows.map((row) => {
        const width = `${(row.rate / max) * 100}%`;
        const delta = row.deltaPp;
        const tone = delta === null ? "flat" : delta > 0 ? "up" : delta < 0 ? "down" : "flat";
        return (
          <div className="ladder-row" key={row.arm}>
            <div className="ladder-label">
              <strong>{row.arm}</strong>
              <span>{row.label}</span>
            </div>
            <div className="ladder-track">
              <div className={`ladder-bar tone-${tone}`} style={{ width }} />
              <span className="ladder-value">
                {row.correct}/{row.n} · {(row.rate * 100).toFixed(1)}%
              </span>
            </div>
            <div className={`ladder-delta tone-${tone}`}>
              {delta === null ? "기준" : `${delta > 0 ? "+" : ""}${delta.toFixed(1)}%p`}
            </div>
            <p className="ladder-note">{row.note}</p>
          </div>
        );
      })}
    </div>
  );
}
```

- [ ] **Step 7: `site/components/Heatmap.tsx` 작성 (서버 컴포넌트)**

```tsx
export type HeatCell = { arm: string; correct: number; n: number; rate: number };
export type HeatRow = { key: string; cells: HeatCell[] };

function shade(rate: number) {
  // 0 → 흰색, 1 → 진한 인디고. 배경이 진해지면 글자를 흰색으로 뒤집는다.
  const alpha = 0.08 + rate * 0.82;
  return {
    background: `rgba(43, 75, 184, ${alpha.toFixed(3)})`,
    color: rate > 0.55 ? "#ffffff" : "var(--ink-soft)",
  };
}

export default function Heatmap({ arms, rows }: { arms: string[]; rows: HeatRow[] }) {
  return (
    <div className="table-wrap">
      <table className="table heatmap">
        <thead>
          <tr>
            <th>구분</th>
            {arms.map((arm) => (
              <th key={arm} className="num">
                {arm}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.key}>
              <th scope="row">{row.key}</th>
              {arms.map((arm) => {
                const cell = row.cells.find((item) => item.arm === arm);
                if (!cell) {
                  return (
                    <td key={arm} className="num">
                      -
                    </td>
                  );
                }
                return (
                  <td key={arm} className="num heat-cell" style={shade(cell.rate)}>
                    {cell.correct}/{cell.n}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
```

- [ ] **Step 8: `site/app/globals.css` 에 사다리·히트맵 스타일 추가 (반응형 블록 앞)**

```css
/* ── 기여도 사다리 ──────────────────────────────────── */
.ladder {
  display: grid;
  gap: 10px;
  margin-top: 16px;
}

.ladder-row {
  display: grid;
  grid-template-columns: minmax(0, 180px) minmax(0, 1fr) 78px;
  grid-template-areas: "label track delta" "note note note";
  gap: 8px 14px;
  align-items: center;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--surface);
  padding: 12px 14px;
}

.ladder-label {
  grid-area: label;
  display: grid;
}

.ladder-label strong {
  font-size: 14px;
}

.ladder-label span {
  color: var(--muted);
  font-size: 12px;
}

.ladder-track {
  grid-area: track;
  position: relative;
  height: 26px;
  border-radius: 6px;
  background: var(--surface-strong);
  overflow: hidden;
}

.ladder-bar {
  height: 100%;
  border-radius: 6px;
  background: var(--primary);
}

.ladder-bar.tone-down {
  background: var(--danger);
}

.ladder-bar.tone-flat {
  background: var(--ink-soft);
}

.ladder-value {
  position: absolute;
  top: 0;
  left: 10px;
  height: 26px;
  display: flex;
  align-items: center;
  color: #ffffff;
  font-size: 12px;
  font-weight: 800;
  font-variant-numeric: tabular-nums;
  text-shadow: 0 1px 2px rgba(0, 0, 0, 0.35);
}

.ladder-delta {
  grid-area: delta;
  text-align: right;
  font-size: 13px;
  font-weight: 800;
  font-variant-numeric: tabular-nums;
}

.ladder-delta.tone-up {
  color: var(--primary);
}

.ladder-delta.tone-down {
  color: var(--danger);
}

.ladder-delta.tone-flat {
  color: var(--muted);
}

.ladder-note {
  grid-area: note;
  margin: 0;
  color: var(--muted);
  font-size: 12px;
}

/* ── 히트맵 ─────────────────────────────────────────── */
.heatmap th[scope="row"] {
  background: var(--surface-strong);
  white-space: nowrap;
}

.heat-cell {
  font-weight: 800;
  font-size: 12px;
}

.finding-card h3 {
  margin: 6px 0 0;
  font-size: 17px;
  line-height: 1.4;
}

.finding-card p {
  margin: 10px 0 0;
  color: var(--muted);
  font-size: 14px;
}

@media (max-width: 720px) {
  .ladder-row {
    grid-template-columns: minmax(0, 1fr) 78px;
    grid-template-areas: "label delta" "track track" "note note";
  }
}
```

- [ ] **Step 9: `site/app/ablation/page.tsx` 작성**

```tsx
import data from "../../public/data/site_ablation.json";
import LadderBars, { type LadderRow } from "../../components/LadderBars";
import Heatmap, { type HeatRow } from "../../components/Heatmap";
import StatCard from "../../components/StatCard";

const AXIS_LABELS: Record<string, string> = {
  difficulty: "난이도",
  answer_mode: "답변 형태",
  task_type: "질의 유형",
};

const ladder = data.ladder as LadderRow[];
const arms = ladder.map((row) => row.arm);
const a8 = ladder[ladder.length - 1];
const a0 = ladder[0];

export default function AblationPage() {
  const netPp = ((a8.rate - a0.rate) * 100).toFixed(1);

  return (
    <main className="section">
      <div className="container">
        <div className="section-heading">
          {/* 🔴 세대를 문자열로 박지 않는다. 사다리 세대와 현 세대가 다를 수 있다 */}
          <span className="badge">
            사다리 {data.generations.ladder.arm} · 현 세대 {data.generations.current.arm} · 그리드{" "}
            {data.cohort.nGrid}문항
          </span>
          <h1>무엇이 성능을 벌었는지 하나씩 떼어보고 확인했다</h1>
          <p className="lead">
            구성요소를 하나씩 켜 올리는 사다리 8구성과, 완성 구성에서 하나씩 빼는 LOO 3구성입니다.
            판정에 쓰는 분모는 dev 그리드 {data.cohort.nGrid}문항 하나이고, 토큰 단가는 실측 역산
            {" "}{data.cohort.priceWonPer1k}원/1K 입니다.
          </p>
        </div>

        <section className="grid grid-4" aria-label="사다리 요약">
          <StatCard label="제출 구성" value={a8.arm} helper={a8.label} />
          <StatCard
            label="A0 → A8 순증"
            value={`+${netPp}%p`}
            helper={`${(a0.rate * 100).toFixed(1)}% → ${(a8.rate * 100).toFixed(1)}%`}
          />
          <StatCard
            label="골든 근거 회수"
            value={`${a8.goldenHit}/${a8.goldenTotal}`}
            helper={`근거 표기율 ${a8.citationRate}`}
          />
          <StatCard
            label="누적 지출"
            value={`${data.ledgerTotalWon.toLocaleString("ko-KR")}원`}
            helper={`웰컴 크레딧 ${data.creditWon.toLocaleString("ko-KR")}원 기준 · ${(
              (data.ledgerTotalWon / data.creditWon) *
              100
            ).toFixed(1)}%`}
          />
        </section>

        <section className="panel spacious">
          <div className="section-heading compact">
            <span className="badge">누적 곡선</span>
            <h2>사다리 — 켜 올릴 때마다 무엇이 붙었나</h2>
            <p className="lead">
              막대는 정답률, 우측 수치는 직전 구성 대비 Δ 입니다. 빨간 막대는 정답률이 내려간
              구성입니다.
            </p>
          </div>
          <LadderBars rows={ladder} />
        </section>

        <section className="grid grid-3 spacious" aria-label="핵심 발견">
          {data.findings.map((finding) => (
            <article className="panel finding-card" key={finding.title}>
              <p className="eyebrow">핵심 발견</p>
              <h3>{finding.title}</h3>
              <p>{finding.body}</p>
            </article>
          ))}
        </section>

        <section className="panel spacious">
          <div className="section-heading compact">
            <span className="badge">Leave-One-Out</span>
            <h2>완성 구성에서 하나씩 빼면 무엇을 잃나</h2>
            <p className="lead">Δ 는 제출 구성 {a8.arm} 대비입니다.</p>
          </div>
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>구성</th>
                  <th>무엇을 뺐나</th>
                  <th className="num">정답</th>
                  <th className="num">정답률</th>
                  <th className="num">Δ vs {a8.arm}</th>
                </tr>
              </thead>
              <tbody>
                {data.loo.map((row) => (
                  <tr key={row.arm}>
                    <td>
                      <strong>{row.arm}</strong>
                    </td>
                    <td>
                      {row.label}
                      <span>{row.note}</span>
                    </td>
                    <td className="num">
                      {row.correct}/{row.n}
                    </td>
                    <td className="num">{(row.rate * 100).toFixed(1)}%</td>
                    <td className="num">
                      <strong className={row.deltaPp < 0 ? "bad-text" : "ok-text"}>
                        {row.deltaPp > 0 ? "+" : ""}
                        {row.deltaPp.toFixed(1)}%p
                      </strong>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="helper">
            L3 은 그리드에서 0%p 입니다. 발주처 hop 은 그리드에서 발화가 0건이고, 발주처를 지목한
            밸류체인 12문항에서만 듣습니다. 🔴 그 12문항의 정답률 차(5/12 대 4/12)는 <strong>문항
            하나에 매달려 있으므로</strong> 근거로 쓰지 않습니다 — A8 의 축은 <strong>골든 근거
            완결성</strong>입니다 (Q-110 5/6 대 2/6 · Q-116 3/3 대 2/3).
          </p>
        </section>

        {data.axes.map((axis) => (
          <section className="panel spacious" key={axis.axis}>
            <div className="section-heading compact">
              <span className="badge">축별 분해</span>
              <h2>{AXIS_LABELS[axis.axis] ?? axis.axis}</h2>
              <p className="lead">
                전체 평균만 보면 놓치는 것을 봅니다. 셀이 진할수록 정답률이 높습니다.
              </p>
            </div>
            <Heatmap arms={arms} rows={axis.rows as HeatRow[]} />
          </section>
        ))}

        <section className="panel spacious">
          <div className="section-heading compact">
            <span className="badge">비용 원장</span>
            <h2>전 파이프라인 누적 지출</h2>
            <p className="lead">
              전부 API 응답의 <code>usage</code> 필드 실측입니다. 추정치는 없습니다.
              이 표는 <code>{data.ledgerSource}</code> 를 빌드 시점에 파싱한 것이며, 행 합과
              누적 행이 어긋나면 빌드가 실패합니다 — 🔴 <strong>사이트가 정본을 복사하지 않습니다.</strong>
              기각된 구성의 지출도 원장에 싣습니다. 빼면 다음 사람이 같은 것을 다시 삽니다.
            </p>
          </div>
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>항목</th>
                  <th className="num">토큰</th>
                  <th className="num">비용</th>
                  <th>출처</th>
                </tr>
              </thead>
              <tbody>
                {data.ledger.map((item) => (
                  <tr key={item.item}>
                    <td>{item.item}</td>
                    <td className="num">{item.tokens ? item.tokens.toLocaleString("ko-KR") : "-"}</td>
                    <td className="num">{item.costWon.toLocaleString("ko-KR")}원</td>
                    <td>
                      <span>{item.source}</span>
                    </td>
                  </tr>
                ))}
                <tr>
                  <th>누적</th>
                  <td className="num">-</td>
                  <td className="num">
                    <strong>{data.ledgerTotalWon.toLocaleString("ko-KR")}원</strong>
                  </td>
                  <td>
                    <span>docs/total_plan.md §1</span>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </section>
      </div>
    </main>
  );
}
```

- [ ] **Step 10: 타입체크와 빌드**

Run: `cd site && npm run typecheck && npm run build`
Expected: 오류 0건, `/ablation` 프리렌더 성공.

- [ ] **Step 11: 육안 확인 — 부호가 화면에서 맞는지**

Run: `cd site && npm run dev`
Expected: `/ablation` 에서 사다리 8행 + LOO 표. 🔴 **기대 Δ 값을 여기 적지 않는다** — 세대마다
움직이고, 적으면 그것이 열두 번째 하드코딩이다. 대신 **구조와 부호**를 본다:

```
사다리 8행이 A0…A8 순서로 있다
A1 행의 막대가 빨간색이다              (Δ < 0 — 이 곡선의 핵심 발견)
최대 단일 Δ 행이 A1b 다                (doc_subtype 메타 축 = 사다리 최대 전이)
A4 이후 행의 Δ 가 전부 0.0%p 다         (꺾이는 지점은 A5 가 아니라 A4)
LOO 표에서 L2 가 가장 큰 음수다         (최대 병목 = n-ary 이벤트 노드 속성)
L3·L4 는 그리드에서 0.0%p 다            (밸류체인·회귀에서만 듣는다 — 그 라벨이 붙어 있다)
🔴 사다리 표와 LOO 표 머리에 **서로 다른 세대 라벨**이 붙어 있다
🔴 원장 마지막 행이 site_ablation.json 의 ledgerTotalWon 과 글자 단위로 같다
🔴 heldout 카드·ORACLE 정답률이 성적 자리에 없다
```

확인 후 종료.

- [ ] **Step 12: 커밋**

```bash
git add scripts/build_site_data.py tests/test_site_data.py site/app/ablation site/app/globals.css site/components/LadderBars.tsx site/components/Heatmap.tsx site/public/data/site_ablation.json
git commit -m "feat: 기여도 사다리 — A0→A8 · LOO · 축별 분해 · 비용 원장"
```

---

### Task 8: 실패 해부 — 6단계 귀속 · 근본 원인 · 문서군 · 분모 세 개

**Files:**
- Modify: `scripts/build_site_data.py` (`build_errors` 추가)
- Modify: `tests/test_site_data.py`
- Create: `site/app/errors/page.tsx`
- Modify: `site/app/globals.css`
- Create: `site/public/data/site_errors.json` (생성물)

**Interfaces:**
- Consumes: `read_json` · `metadata` (Task 2) · `StatCard` (Task 1) · `Heatmap` (Task 7 — 여기서는 쓰지 않지만 클래스 `.heat-cell` 은 이미 있다)
- Produces:
  - `STAGE_ORDER: tuple[str, ...]` = `("ROUTING","RETRIEVAL","RANKING","GENERATION","TOOL","VERIFY")`
  - `STAGE_NOTES: dict[str, str]`
  - `build_errors() -> dict`
  - `site_errors.json` 스키마:
    - `metadata`
    - `totals: { questions, llmCalls, totalTokens, costWon, costWonScope, hopsTotal, stepsMax }`
      🔴 **`latencySMean` 을 싣지 않는다.** `report.json > totals.latency_s_mean`
      은 응답 파일에 **기록된** 벽시계 시간의 평균이고, 그 파일을 만들 때 요청 캐시가
      적중했다면 파이프라인이 **~1ms** 에 끝난 시간이 저장돼 있다(A0 3.73초 vs A5·A8
      0.02~0.03초). 편향이 **언제나 빠르게 보이는 한 방향**이라 UI 에 "평균 응답 시간" 으로
      그리면 두 자릿수 배로 틀린다 (`v2/gate_I.md §13` 경고 · F1 이 `summary.json` 에서는
      필드명을 `latency_s_mean_recorded` 로 갈았다). 진짜 응답 시간은 **캐시 없이 다시 재야**
      나오므로 제출 후 측정할 값이고, 그때까지 이 카드는 만들지 않는다.
      `costWonScope` 는 `totals.cost_won_scope` 를 그대로 옮긴다 — 그 값은 **누적 재생
      비용**이고 신규 지출이 아니다.
    - `stages: [{ stage, label, note, gridCount, allCount }]`
    - `rootCauses: [{ key, stage, cause, gridCount }]`
    - `byDocGroup: [{ docGroup, correct, n, rate, stages: {str: int} }]`
    - `denominators: [{ value: int, denominator: str, what: str, composition: str }]` — 3개
    - `failures: [{ questionId, difficulty, docGroup, track, taskType, firstFailure, rootCause, failureWhy, hasCitation }]`
    - `dataLimits: [{ item: str, scale: str, why: str }]`

- [ ] **Step 1: 실패하는 테스트를 쓴다 — `tests/test_site_data.py` 맨 끝에 추가**

```python
# ── 실패 해부 ────────────────────────────────────────────────────────

def test_build_errors_shape():
    payload = bsd.build_errors()
    assert set(payload) == {
        "metadata", "totals", "stages", "rootCauses", "byDocGroup",
        "denominators", "failures", "dataLimits",
    }
    assert [s["stage"] for s in payload["stages"]] == list(bsd.STAGE_ORDER)


def test_build_errors_grid_failures_are_self_consistent():
    """🔴 값을 못박지 않는다 — **자기일관성**으로 잡는다.

    종전 판은 `== 11` 을 단언했다. 그 수는 v3 R2D 에서 6 이 됐고, 그때 이 테스트는
    '사이트가 낡았다' 가 아니라 '테스트가 낡았다' 로 깨졌을 것이다.
    이제는 단계 합 == (그리드 분모 − 정답) 인지만 본다. 어느 세대에서도 성립해야 한다.
    """
    payload = bsd.build_errors()
    gen = bsd.resolve_generation()
    rows = bsd.read_json(f"artifacts/ablation/{gen}/A8/report.json")["rows"]
    grid = [r for r in rows if r["track"] == "grid"]
    wrong = [r for r in grid if not r["correct"]]
    assert sum(s["gridCount"] for s in payload["stages"]) == len(wrong)


def test_build_errors_lists_three_denominators():
    """세 분모가 **각자 라벨과 함께** 있는지만 본다. 값은 세대마다 움직인다."""
    payload = bsd.build_errors()
    keys = [d["key"] for d in payload["denominators"]]
    assert keys == ["overall", "grid", "attributed"]
    assert all({"key", "value", "denominator", "what", "composition"} == set(d)
               for d in payload["denominators"])
    # 🔴 분모 라벨에 반드시 수가 붙어 있어야 한다 — 라벨 없는 수치가 이 저장소를 한 번 오판시켰다
    assert all(d["denominator"] for d in payload["denominators"])
    by_key = {d["key"]: d["value"] for d in payload["denominators"]}
    assert by_key["overall"] >= by_key["grid"]        # 그리드는 전체의 부분집합이다
    assert by_key["overall"] >= by_key["attributed"]  # 배분은 오답의 부분집합이다


def test_build_errors_masks_amounts_in_free_text():
    """🔴 보안 ⑤ — `failure_why` 의 6자리 이상 숫자가 마스킹돼야 한다."""
    payload = bsd.build_errors()
    for f in payload["failures"]:
        assert not re.search(r"\d[\d,]{5,}", f["failureWhy"]), f["questionId"]


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


def test_build_errors_failures_are_wrong_answers_only():
    payload = bsd.build_errors()
    assert payload["failures"]
    assert all(f["firstFailure"] for f in payload["failures"])


def test_build_errors_doc_group_rates_match_source():
    gen = bsd.resolve_generation()
    source = bsd.read_json(f"artifacts/ablation/{gen}/A8/report.json")["by_doc_group_grid"]
    payload = bsd.build_errors()
    for row in payload["byDocGroup"]:
        assert row["correct"] == source[row["docGroup"]]["correct"]
        assert row["n"] == source[row["docGroup"]]["n_scored"]


def test_build_errors_has_no_golden_fields():
    blob = json.dumps(bsd.build_errors(), ensure_ascii=False)
    assert "golden_rank" not in blob
    assert "goldenRank" not in blob


def test_build_errors_data_limits_present():
    payload = bsd.build_errors()
    assert len(payload["dataLimits"]) == 6
    assert all({"item", "scale", "why"} == set(d) for d in payload["dataLimits"])
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_site_data.py -k "errors" -v`
Expected: FAIL — `AttributeError: module 'build_site_data' has no attribute 'build_errors'`

- [ ] **Step 3: `build_errors` 구현 — `build_ablation` 아래, `BUILDERS` 위**

```python
STAGE_ORDER = ("ROUTING", "RETRIEVAL", "RANKING", "GENERATION", "TOOL", "VERIFY")

STAGE_LABELS = {
    "ROUTING": "라우팅",
    "RETRIEVAL": "검색",
    "RANKING": "랭킹",
    "GENERATION": "생성",
    "TOOL": "도구",
    "VERIFY": "검증",
}

STAGE_NOTES = {
    "ROUTING": "질의를 엉뚱한 문서군·기업으로 보냈다",
    "RETRIEVAL": "근거가 후보에 아예 들어오지 않았다",
    "RANKING": "근거가 후보에는 있었지만 순위가 낮아 잘렸다",
    "GENERATION": "근거는 실렸는데 답이 틀렸다",
    "TOOL": "도구 호출이나 계산이 틀렸다",
    "VERIFY": "검증 단계가 잡아내지 못했거나 잘못 잡았다",
}

# 🔴 분모는 **정의**라서 여기 두고, **값은 산출물에서 센다.**
#    종전 판은 세 값(13 · 11 · 12)과 구성 문자열까지 상수로 들고 있었다.
#    그 셋은 v3 R2D 재측정에서 전부 바뀌었고(그리드 잔여 10건 → v4 R0 시점 6건)
#    구성 문자열(`GENERATION 5 · VERIFY 3 …`)도 F3 의 귀속 순서 수정으로 낡았다.
#    🔴 `total_plan §5` 는 그 파일(`error_analysis_v2.json`)이 **옛 라벨을 들고 있다**고
#    명시한다 — 그래서 원천을 `ablation/{gen}/A8/report.json` 으로 옮겼다.
DENOMINATOR_DEFS = [
    {
        "key": "overall",
        "denominator": "전체 88 (그리드 72 + 정보한계 6 + 회귀 10)",
        "what": "A8 오답 전량",
    },
    {
        "key": "grid",
        "denominator": "그리드 72",
        "what": "정답률 판정에 쓰는 잔여 실패 — 🔴 성적의 축은 이 분모 하나다",
    },
    {
        "key": "attributed",
        "denominator": "전체 88 오답 − 미배분",
        "what": "arm 겨냥 배분. 🔴 미배분은 그 라운드 부작용이라 배분표에 자리가 없다",
    },
]

DENOMINATOR_SOURCE = "docs/total_plan.md §5 '잔여 실패의 세 가지 수'"

# 🔴 데이터 상한 표는 `docs/total_plan.md` §4 가 정본이고, 그 규모 수치는 **코퍼스 프로파일**에서
#    나온 것이라 재측정 대상이 아니다. 그래도 여기 옮겨 적지 않는다 — 옮겨 적은 판에서
#    이미 한 건이 어긋났다("임상·품목허가 105건 vs total_plan 166건, 출처 미확인").
#    사이트는 **항목명과 사유만** 들고 규모는 §4 를 링크한다. 🔴 링크가 값보다 늙지 않는다.
DATA_LIMITS = [
    {"item": "공시유보 계약", "why": "경영상 비밀유지로 원본 값이 '-' 다"},
    {"item": "임상·품목허가 공시", "why": "애초에 금액 항목이 없는 서식이다"},
    {"item": "※ 관련공시 미해소", "why": "코퍼스 수집 시작일 이전 문서를 참조한다"},
    {"item": "정정 원본 부재", "why": "원본이 코퍼스 밖이다"},
    {"item": "정기공시 본문 부재", "why": "수집본이 뷰어 셸뿐이고 본문은 PDF 만 있다"},
    {"item": "합병·분할·교환이전 금액", "why": "서식에 단일 '금액' 칸이 없다"},
]

DATA_LIMITS_SOURCE = "docs/total_plan.md §4 '데이터 상한 — 노력으로 넘을 수 없는 것'"


def _mask_amounts(text: str) -> str:
    """🔴 자유 서술에서 6자리 이상 숫자를 마스킹한다 (보안 ⑤).

    `failure_why` 는 사람이 쓴 문장이고 골든 값이 인용돼 있을 수 있다 —
    실측 2026-08-14: 현 세대 88행 중 **3행**에 6자리 이상 숫자가 있다.
    """
    return re.sub(r"\d[\d,]{5,}", "(수치 생략)", text or "")


def build_errors() -> dict:
    gen = resolve_generation()
    data = read_json(f"artifacts/ablation/{gen}/A8/report.json")
    totals = data["totals"]
    grid_hist = data["failure_histogram_grid"]
    all_hist = data["failure_histogram"]
    root_grid = data["root_cause_histogram_grid"]
    by_group = data["by_doc_group_grid"]

    stages = [
        {
            "stage": stage,
            "label": STAGE_LABELS[stage],
            "note": STAGE_NOTES[stage],
            "gridCount": grid_hist.get(stage, 0),
            "allCount": all_hist.get(stage, 0),
        }
        for stage in STAGE_ORDER
    ]

    root_causes = []
    for key, count in sorted(root_grid.items(), key=lambda kv: (-kv[1], kv[0])):
        stage, _, cause = key.partition("/")
        root_causes.append({"key": key, "stage": stage, "cause": cause or key, "gridCount": count})

    doc_groups = []
    for group in ("exchange", "major", "holding", "periodic"):
        row = by_group.get(group)
        if not row:
            continue
        doc_groups.append(
            {
                "docGroup": group,
                "correct": row["correct"],
                "n": row["n_scored"],
                "rate": round(row["rate"], 4),
                "stages": row.get("stages") or {},
            }
        )

    failures = [
        {
            "questionId": r["question_id"],
            "difficulty": r["difficulty"],
            "docGroup": r["doc_group"],
            "track": r["track"],
            "taskType": r["task_type"],
            "firstFailure": r["first_failure"],
            "rootCause": r["root_cause"] or "-",
            "failureWhy": _mask_amounts(r["failure_why"]) or "-",   # 🔴 보안 ⑤
            "hasCitation": bool(r["has_citation"]),
        }
        for r in data["rows"]
        if not r["correct"] and r["first_failure"]
    ]
    failures.sort(key=lambda f: (STAGE_ORDER.index(f["firstFailure"]) if f["firstFailure"] in STAGE_ORDER else 99, f["questionId"]))

    # 🔴 세 분모의 **값을 센다.** 상수로 두지 않는다.
    grid_rows = [r for r in data["rows"] if r["track"] == "grid"]
    overall_wrong = [r for r in data["rows"] if not r["correct"]]
    grid_wrong = [r for r in grid_rows if not r["correct"]]
    attributed = [r for r in overall_wrong if r["first_failure"]]
    counted = {
        "overall": len(overall_wrong),
        "grid": len(grid_wrong),
        "attributed": len(attributed),
    }
    denominators = [
        dict(
            d,
            value=counted[d["key"]],
            composition=", ".join(
                f"{STAGE_LABELS.get(stage, stage)} {n}"
                for stage, n in sorted(grid_hist.items(), key=lambda kv: -kv[1])
                if n
            ) if d["key"] == "grid" else "",
        )
        for d in DENOMINATOR_DEFS
    ]

    return {
        "metadata": metadata(
            "실패 해부 — 6단계 귀속, 근본 원인, 문서군별, 분모 세 개. "
            "🔴 golden 라벨·질문 전문·답변 전문을 싣지 않는다 (보안 ④⑤).",
            [f"artifacts/ablation/{gen}/A8/report.json", SSOT_MD],
        ),
        "generation": {"arm": gen},
        "denominatorSource": DENOMINATOR_SOURCE,
        "dataLimitsSource": DATA_LIMITS_SOURCE,
        "totals": {
            "questions": totals["questions"],
            "llmCalls": totals["llm_calls"],
            "totalTokens": totals["total_tokens"],
            "costWon": totals["cost_won"],
            # 🔴 latency_s_mean 은 싣지 않는다 (재생 시간이라 한 방향으로 편향된다 — 스키마 주석)
            "costWonScope": totals.get("cost_won_scope", ""),
            "hopsTotal": totals["hops_total"],
            "stepsMax": totals["steps_max"],
        },
        "stages": stages,
        "rootCauses": root_causes,
        "byDocGroup": doc_groups,
        "denominators": denominators,
        "failures": failures,
        "dataLimits": DATA_LIMITS,
    }
```

`BUILDERS` 에 한 줄 추가:

```python
    "errors": ("site_errors.json", build_errors),
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_site_data.py -k "errors" -v`
Expected: 전건 PASS. 🔴 **건수를 여기 적지 않는다.**

`test_build_errors_grid_failures_are_self_consistent` 가 실패하면 **단계 히스토그램과
문항별 채점이 어긋난 것**이다 — 사이트 버그가 아니라 **산출물 안의 불일치**일 수 있다.
아래로 세 수를 각자의 분모와 함께 확인한다:

```bash
PYTHONIOENCODING=utf-8 python -c "
import json,pathlib
gen=next(g for g in ('v4','v3','v2') if pathlib.Path(f'artifacts/ablation/{g}/summary.json').exists())
d=json.load(open(f'artifacts/ablation/{gen}/A8/report.json',encoding='utf-8'))
rows=d['rows']; grid=[r for r in rows if r['track']=='grid']
print('세대', gen)
print('그리드 분모', len(grid), '오답', len([r for r in grid if not r['correct']]))
print('전체 분모', len(rows), '오답', len([r for r in rows if not r['correct']]))
print('grid hist:', d['failure_histogram_grid'], sum(d['failure_histogram_grid'].values()))
print('all  hist:', d['failure_histogram'], sum(d['failure_histogram'].values()))
"
```

🔴 **어긋나면 `total_plan §5` 와 `09-ablation §I-5f ⑧` 을 함께 본다.** 그 절이
"자동 arm 배분표는 원문 대조와 **원리적으로** 일치시킬 수 없다" 고 적어 뒀다 —
사이트는 **자동 배분값을 렌더하고 그 한계를 화면에 적는다.** 값을 손으로 맞추지 않는다.

- [ ] **Step 5: 생성과 유출 게이트 재확인**

Run:
```bash
python scripts/build_site_data.py --only errors
python -m pytest tests/test_site_data.py -v
```
Expected: 전건 PASS.

- [ ] **Step 6: `site/app/globals.css` 에 실패 화면 스타일 추가 (반응형 블록 앞)**

```css
/* ── 실패 해부 ──────────────────────────────────────── */
.stage-grid {
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  margin-top: 16px;
}

.stage-card {
  border-left: 4px solid var(--line);
}

.stage-card.has-failures {
  border-left-color: var(--danger);
}

.stage-card.is-clean {
  border-left-color: var(--teal);
}

.stage-card .metric {
  font-size: 26px;
}

.denominator-card {
  border-left: 4px solid var(--amber);
}

.denominator-card .metric {
  font-size: 34px;
}

.denominator-card dl {
  margin: 12px 0 0;
  display: grid;
  gap: 8px;
  font-size: 13px;
}

.denominator-card dt {
  color: var(--muted);
  font-weight: 800;
}

.denominator-card dd {
  margin: 2px 0 0;
}

.rate-cell {
  display: flex;
  align-items: center;
  gap: 8px;
  justify-content: flex-end;
}

.rate-bar {
  width: 90px;
  height: 8px;
  border-radius: 999px;
  background: var(--surface-strong);
  overflow: hidden;
  flex: 0 0 auto;
}

.rate-bar span {
  display: block;
  height: 100%;
  background: var(--primary);
}
```

- [ ] **Step 7: `site/app/errors/page.tsx` 작성**

```tsx
import data from "../../public/data/site_errors.json";
import StatCard from "../../components/StatCard";

export default function ErrorsPage() {
  const gridFailures = data.stages.reduce((sum, stage) => sum + stage.gridCount, 0);

  return (
    <main className="section">
      <div className="container">
        <div className="section-heading">
          <span className="badge">실패 해부 · 전체 {data.totals.questions}문항</span>
          <h1>어느 단계에서 처음 틀렸는지로 귀속한다</h1>
          <p className="lead">
            오답을 원문까지 대조해 6단계 중 <strong>처음 틀어진 곳</strong>에 배분했습니다. 같은 오답을
            여러 단계에 중복 계상하지 않기 때문에, 어디를 고쳐야 하는지가 한 곳으로 모입니다.
          </p>
        </div>

        <section className="grid grid-4" aria-label="측정 요약">
          <StatCard label="채점 문항" value={data.totals.questions} helper="그리드 72 + 정보한계 6 + 회귀 10" />
          <StatCard label="그리드 잔여 실패" value={gridFailures} helper="정답률 판정에 쓰는 분모 기준" />
          {/* 🔴 '문항당 지연' 카드는 만들지 않는다 — latency_s_mean 은 재생 시간이라
              캐시 적중 문항이 ~1ms 로 저장돼 있고 편향이 한 방향이다 (스키마 주석 참조).
              제출 후 캐시 없이 다시 재면 그때 카드를 넣는다. */}
          <StatCard
            label="LLM 호출"
            value={data.totals.llmCalls.toLocaleString("ko-KR")}
            helper={`최대 ${data.totals.stepsMax}스텝 · hop ${data.totals.hopsTotal}`}
          />
          <StatCard
            label="재생 비용"
            value={`${Math.round(data.totals.costWon).toLocaleString("ko-KR")}원`}
            helper={`${data.totals.totalTokens.toLocaleString("ko-KR")} 토큰 · 캐시 재생 포함(신규 지출 아님)`}
          />
        </section>

        <section className="panel spacious">
          <div className="section-heading compact">
            <span className="badge">6단계 귀속</span>
            <h2>처음 틀어진 단계</h2>
            <p className="lead">
              사전 예상은 생성·도구에 몰릴 것이었습니다. 실측은 달랐고, 도구와 라우팅은 그리드에서 0건입니다.
            </p>
          </div>
          <div className="grid stage-grid">
            {data.stages.map((stage) => (
              <article
                className={`card stage-card ${stage.gridCount > 0 ? "has-failures" : "is-clean"}`}
                key={stage.stage}
              >
                <p className="eyebrow">{stage.stage}</p>
                <div className="metric">{stage.gridCount}</div>
                <p className="helper">
                  {stage.label} — {stage.note}
                </p>
                {stage.allCount !== stage.gridCount ? (
                  <p className="helper">전체 88 기준 {stage.allCount}건</p>
                ) : null}
              </article>
            ))}
          </div>
        </section>

        <section className="panel spacious">
          <div className="section-heading compact">
            <span className="badge">🔴 분모를 반드시 붙인다</span>
            <h2>잔여 실패의 세 가지 수</h2>
            {/* 🔴 세 값을 문장에 적지 않는다 — 재측정마다 바뀐다. 카드가 산출물 값을 낸다 */}
            <p className="lead">
              아래 세 수는 <strong>서로 다른 것을 셉니다.</strong> 분모를 떼면 합이 맞지 않아 보이고,
              그러면 다음 사람이 엉뚱한 것을 고칩니다. 이 프로젝트는 같은 유형으로 이미 두 번 틀렸습니다.
              🔴 그리고 자동 배분표는 원문 대조와 <strong>원리적으로</strong> 일치시킬 수 없습니다 —
              아래 값은 자동 배분이고, 원문 대조 결과는{" "}
              <code>{data.denominatorSource}</code> 에 있습니다.
            </p>
          </div>
          <div className="grid grid-3">
            {data.denominators.map((item) => (
              <article className="card denominator-card" key={item.key}>
                <p className="eyebrow">오답 수</p>
                <div className="metric">{item.value}</div>
                <dl>
                  <div>
                    <dt>분모</dt>
                    <dd>{item.denominator}</dd>
                  </div>
                  <div>
                    <dt>무엇을 세나</dt>
                    <dd>{item.what}</dd>
                  </div>
                  <div>
                    <dt>구성</dt>
                    <dd>{item.composition}</dd>
                  </div>
                </dl>
              </article>
            ))}
          </div>
        </section>

        <section className="panel spacious">
          <div className="section-heading compact">
            <span className="badge">근본 원인</span>
            <h2>단계 아래 한 칸 더</h2>
            <p className="lead">단계만으로는 처방이 안 나오므로 원인까지 내려 적습니다. 그리드 기준입니다.</p>
          </div>
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>단계</th>
                  <th>근본 원인</th>
                  <th className="num">건수</th>
                </tr>
              </thead>
              <tbody>
                {data.rootCauses.map((cause) => (
                  <tr key={cause.key}>
                    <td>{cause.stage}</td>
                    <td>{cause.cause}</td>
                    <td className="num">{cause.gridCount}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        <section className="panel spacious">
          <div className="section-heading compact">
            <span className="badge">문서군별</span>
            <h2>정기공시가 가장 어렵다</h2>
            <p className="lead">
              코퍼스의 95.4% 가 정기공시이고, 목차 축으로 내려가야 근거가 나옵니다. 실패도 여기 몰립니다.
            </p>
          </div>
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>문서군</th>
                  <th className="num">정답</th>
                  <th className="num">정답률</th>
                  <th>실패 단계</th>
                </tr>
              </thead>
              <tbody>
                {data.byDocGroup.map((row) => (
                  <tr key={row.docGroup}>
                    <td>
                      <strong>{row.docGroup}</strong>
                    </td>
                    <td className="num">
                      {row.correct}/{row.n}
                    </td>
                    <td className="num">
                      <span className="rate-cell">
                        <span className="rate-bar">
                          <span style={{ width: `${row.rate * 100}%` }} />
                        </span>
                        {(row.rate * 100).toFixed(1)}%
                      </span>
                    </td>
                    <td>
                      {Object.keys(row.stages).length === 0
                        ? "-"
                        : Object.entries(row.stages)
                            .map(([stage, count]) => `${stage} ${count}`)
                            .join(" · ")}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        <section className="panel spacious">
          <div className="section-heading compact">
            <span className="badge">문항별</span>
            <h2>오답 {data.failures.length}건 전수</h2>
            <p className="lead">감추지 않고 전부 적습니다. 트랙을 함께 적어 분모를 알 수 있게 했습니다.</p>
          </div>
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>문항</th>
                  <th>트랙</th>
                  <th>문서군</th>
                  <th>난이도</th>
                  <th>단계</th>
                  <th>근본 원인</th>
                  <th>왜 틀렸나</th>
                </tr>
              </thead>
              <tbody>
                {data.failures.map((row) => (
                  <tr key={row.questionId}>
                    <td>
                      <strong>{row.questionId}</strong>
                      <span>{row.hasCitation ? "근거 표기 있음" : "근거 표기 누락"}</span>
                    </td>
                    <td>{row.track}</td>
                    <td>{row.docGroup}</td>
                    <td>{row.difficulty}</td>
                    <td>
                      <strong className="bad-text">{row.firstFailure}</strong>
                    </td>
                    <td>{row.rootCause}</td>
                    <td>{row.failureWhy}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        <section className="panel spacious">
          <div className="section-heading compact">
            <span className="badge">데이터 상한</span>
            <h2>노력으로 넘을 수 없는 것</h2>
            <p className="lead">
              원본에 값이 없는 항목입니다. 이것을 우리 실패로 집계하면 병목 판정이 오염되므로, 정보한계
              트랙으로 따로 세고 지어내면 환각으로 잡습니다.
            </p>
          </div>
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>항목</th>
                  <th className="num">규모</th>
                  <th>왜</th>
                </tr>
              </thead>
              <tbody>
                {data.dataLimits.map((limit) => (
                  <tr key={limit.item}>
                    <td>
                      <strong>{limit.item}</strong>
                    </td>
                    <td className="num">{limit.scale}</td>
                    <td>{limit.why}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      </div>
    </main>
  );
}
```

- [ ] **Step 8: 타입체크와 빌드**

Run: `cd site && npm run typecheck && npm run build`
Expected: 오류 0건, `/errors` 프리렌더 성공.

- [ ] **Step 9: 육안 확인**

Run: `cd site && npm run dev`
Expected: `/errors` 에서 6단계 카드 — `TOOL`·`ROUTING` 은 0건이고 초록 좌측선, `GENERATION` 5 · `VERIFY` 3 · `RETRIEVAL` 2 · `RANKING` 1 은 빨간 좌측선. 분모 카드 3장(13·11·12)에 각자 분모가 적혀 있다. 문서군 표에서 `periodic` 정답률 막대가 가장 짧다. 오답 전수 표와 데이터 상한 6행. 확인 후 종료.

- [ ] **Step 10: 커밋**

```bash
git add scripts/build_site_data.py tests/test_site_data.py site/app/errors site/app/globals.css site/public/data/site_errors.json
git commit -m "feat: 실패 해부 — 6단계 귀속·근본 원인·분모 세 개"
```

---

### Task 9: 정정 체인 · 발주처 밸류체인 — 공시 고유 구조 두 화면

두 화면을 한 Task 로 묶는다. 둘 다 "공시라는 데이터의 구조" 를 보여주는 관계 화면이고, 같은 CSS 프리미티브(`.chain-*`)를 공유하며, 리뷰어가 하나만 통과시키기 어렵다.

**Files:**
- Modify: `scripts/build_site_data.py` (`build_corrections` · `build_counterparty` 추가)
- Modify: `tests/test_site_data.py`
- Create: `site/app/corrections/page.tsx`
- Create: `site/app/counterparty/page.tsx`
- Modify: `site/app/globals.css`
- Create: `site/public/data/site_corrections.json` · `site/public/data/site_counterparty.json` (생성물)

**Interfaces:**
- Consumes: `read_json` · `metadata` · `DART_URL` (Task 2·3) · `StatCard` (Task 1)
- Produces:
  - `DOC_GROUP_LABELS: dict[str, str]`
  - `build_corrections() -> dict`
    - `metadata`
    - `groups: [{ docGroup, label, nDocs, nCorrections, nChains, linkRate, orphanRate, maxChainLength, meanChainLength, lengthDistribution: {str: int} }]`
    - `longestChain: { docGroup, root, members: [{ rceptNo, dartUrl, position }] }`
    - `policy: [{ title, body }]` — 3개
  - `build_counterparty() -> dict`
    - `metadata`
    - `stats: { nodes, pairs, minCount, candidatesBeforeMinCount, hubs, boilerplateExcludedUnique, vagueExcludedOccurrences }`
    - `edgeDensity: { nContractRecords, nWithCounterparty, fillRate, strictDensity, classBreakdown: [{ cls, label, count, pct }] }`
    - `hubs: [{ canonical, display, kind, count, partners: str[] }]` — 상위 12
    - `pairs: [{ a, b, via: str[] }]`
    - `verdict: [{ title, body }]` — 3개

- [ ] **Step 1: 실패하는 테스트를 쓴다 — `tests/test_site_data.py` 맨 끝에 추가**

```python
# ── 정정 체인 ────────────────────────────────────────────────────────

def test_build_corrections_shape():
    payload = bsd.build_corrections()
    assert set(payload) == {"metadata", "groups", "longestChain", "policy"}
    group = payload["groups"][0]
    assert {
        "docGroup", "label", "nDocs", "nCorrections", "nChains", "linkRate",
        "orphanRate", "maxChainLength", "meanChainLength", "lengthDistribution",
    } == set(group)


def test_build_corrections_longest_chain_is_exchange_19():
    """거래소공시 최장 체인 19본. 정정이 예외가 아니라 일상이라는 증거다."""
    payload = bsd.build_corrections()
    chain = payload["longestChain"]
    assert chain["docGroup"] == "exchange"
    assert len(chain["members"]) == 19
    assert chain["members"][0]["position"] == 1
    assert all(m["dartUrl"].endswith(m["rceptNo"]) for m in chain["members"])


def test_build_corrections_link_rates_match_source():
    source = bsd.read_json("artifacts/eda/relation_profile.json")["correction_chains"]
    payload = bsd.build_corrections()
    for group in payload["groups"]:
        assert group["linkRate"] == source[group["docGroup"]]["link_rate"]


def test_build_corrections_policy_present():
    payload = bsd.build_corrections()
    assert len(payload["policy"]) == 3
    assert all({"title", "body"} == set(p) for p in payload["policy"])


# ── 발주처 밸류체인 ──────────────────────────────────────────────────

def test_build_counterparty_shape():
    payload = bsd.build_counterparty()
    assert set(payload) == {"metadata", "stats", "edgeDensity", "hubs", "pairs", "verdict"}
    assert payload["stats"]["nodes"] > 0
    assert payload["stats"]["pairs"] > 0


def test_build_counterparty_hubs_are_sorted_and_capped():
    payload = bsd.build_counterparty()
    hubs = payload["hubs"]
    assert len(hubs) <= 12
    counts = [h["count"] for h in hubs]
    assert counts == sorted(counts, reverse=True)
    assert all(len(h["partners"]) >= 2 for h in hubs)


def test_build_counterparty_edge_density_supports_edge_definition_rejection():
    """🔴 기각된 것은 **기업—기업 엣지 정의** 하나다 — GraphRAG 가 아니다.

    `total_plan §7` · `03-graph-schema.md:68` 이 그 구분을 명시적으로 요구한다.
    여기서는 값이 아니라 **부등호**만 본다 (기재율은 높고 밀도는 낮다).
    """
    payload = bsd.build_counterparty()
    assert payload["edgeDensity"]["strictDensity"] < 0.2
    assert payload["edgeDensity"]["fillRate"] > 0.9


def test_build_counterparty_verdict_does_not_say_graphrag_rejected():
    """🔴 서술 회귀 방지 — 'GraphRAG 를 기각' 이라는 문구가 화면 데이터에 없어야 한다."""
    blob = json.dumps(bsd.build_counterparty()["verdict"], ensure_ascii=False)
    assert "GraphRAG 를 기각" not in blob
    assert "GraphRAG 기각" not in blob
    assert "엣지 정의" in blob      # 정정된 서술이 실제로 실려 있다


def test_build_counterparty_class_breakdown_sums_to_hundred():
    payload = bsd.build_counterparty()
    total = sum(item["pct"] for item in payload["edgeDensity"]["classBreakdown"])
    assert 99.0 <= total <= 101.0, total


def test_build_counterparty_verdict_present():
    payload = bsd.build_counterparty()
    assert len(payload["verdict"]) == len(bsd.COUNTERPARTY_VERDICT)
    assert all({"title", "body"} == set(v) for v in payload["verdict"])
    # 🔴 자리표시자가 남아 있으면 화면에 중괄호가 그대로 뜬다
    assert not any("{" in v["body"] or "{" in v["title"] for v in payload["verdict"])
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_site_data.py -k "corrections or counterparty" -v`
Expected: FAIL — `AttributeError: module 'build_site_data' has no attribute 'build_corrections'`

- [ ] **Step 3: `build_corrections` 구현 — `build_errors` 아래**

```python
DOC_GROUP_LABELS = {
    "exchange": "거래소공시",
    "major": "주요사항보고서",
    "holding": "지분공시",
    "periodic": "정기공시",
}

CORRECTION_POLICY = [
    {
        "title": "체인 위치로만 최신 여부를 판정한다",
        "body": "문서 안의 날짜나 금액 크기로 최신본을 고르지 않는다. 정정 금액은 늘어나기도 하고 "
        "줄어들기도 하므로 크기 기준은 틀린다. 체인을 복원한 뒤 마지막 위치를 최신으로 본다.",
    },
    {
        "title": "질문이 시점을 지정하면 그 시점 값을 답한다",
        "body": "'2025년 3월에 결의한' 처럼 시점이 박힌 질의는 최신값이 아니라 그 시점 값을 답하고 "
        "정정 사실을 부기한다. 시점 지정이 없고 정정이 있으면 최신값을 답한다. 두 읽기를 각각 "
        "다른 문항이 시험하므로 한쪽으로 통일할 수 없다.",
    },
    {
        "title": "정기공시는 원공시도 색인한다",
        "body": "정기공시는 원공시와 정정본이 같은 경로를 쓴다. 최신본만 색인하면 원공시 본문이 "
        "사라지므로 둘 다 넣고, 같은 접수번호는 같은 문서로 취급해 원본·대체본으로 가르지 않는다.",
    },
]


def build_corrections() -> dict:
    profile = read_json("artifacts/eda/relation_profile.json")
    chains = profile["correction_chains"]

    groups = []
    for group in ("exchange", "major", "holding", "periodic"):
        row = chains.get(group)
        if not row:
            continue
        groups.append(
            {
                "docGroup": group,
                "label": DOC_GROUP_LABELS[group],
                "nDocs": row["n_docs"],
                "nCorrections": row["n_corrections"],
                "nChains": row["n_chains"],
                "linkRate": row["link_rate"],
                "orphanRate": row["orphan_rate_out_of_corpus"],
                "maxChainLength": row["max_chain_length"],
                "meanChainLength": row["mean_chain_length"],
                "lengthDistribution": row["chain_length_distribution"],
            }
        )

    # 최장 체인을 가진 문서군을 고른다. 실측상 exchange 가 19본으로 가장 길다.
    best_group = max(groups, key=lambda g: g["maxChainLength"])
    source = chains[best_group["docGroup"]]
    members = [
        {"rceptNo": rcept, "dartUrl": DART_URL.format(rcept), "position": index + 1}
        for index, rcept in enumerate(source["longest_chain_members"])
    ]

    return {
        "metadata": metadata(
            "정정 체인 — 문서군별 체인 통계와 최장 체인",
            ["artifacts/eda/relation_profile.json"],
        ),
        "groups": groups,
        "longestChain": {
            "docGroup": best_group["docGroup"],
            "root": source["longest_chain_root"],
            "members": members,
        },
        "policy": CORRECTION_POLICY,
    }
```

- [ ] **Step 4: `build_counterparty` 구현 — `build_corrections` 아래, `BUILDERS` 위**

```python
COUNTERPARTY_CLASS_LABELS = {
    "foreign_entity": "해외 법인",
    "public_sector": "공공 부문",
    "anonymized": "비공개 처리",
    "domestic_other": "국내 기타",
    "universe_exact": "유니버스 완전일치",
    "own_subsidiary": "자사 종속회사",
    "universe_affiliate": "유니버스 계열",
}

# 🔴 서술 정정 (2026-08-14). 종전 판은 "GraphRAG 를 기각했다" 로 적었다 —
#    `total_plan §7` · `03-graph-schema.md:68` 이 그것을 명시적으로 금지한다:
#    *"전제가 기각된 것이 아니라 논문이 쓴 엣지 정의가 기각된 것이다."*
#    🔴 그리고 수치는 자리표시자로 두고 산출물에서 채운다 (밀도·기재율·발화 건수·골든 회수).
COUNTERPARTY_VERDICT = [
    {
        "title": "기업–기업 **엣지 정의**를 기각했다 — GraphRAG 를 기각한 것이 아니다",
        "body": "계약 상대가 적힌 레코드는 {fillRate} 인데, 그중 우리 유니버스 안의 다른 "
        "상장사로 정확히 연결되는 것은 {density} 뿐이다(완결도는 더 낮다). 나머지는 해외 "
        "법인·공공 부문·비공개 처리다. 🔴 기각한 것은 `기업 —계약했다→ 기업` **직접 엣지 "
        "하나**와 그것을 전제로 한 커뮤니티 탐지다. **노드·엣지를 코퍼스에 맞게 재정의한 "
        "쪽은 채택됐고, 우리 성적의 대부분이 거기서 나온다.**",
    },
    {
        "title": "재정의한 쪽이 성적을 벌었다 — 사다리 최대 전이가 그 증거다",
        "body": "① `공시 —정정한다→ 공시` 체인 순회, ② n-ary 이벤트 노드 속성(주요사항·지분 "
        "필드), ③ **2부 그래프를 탐색이 아니라 메타 축으로**(`doc_subtype` 문서군별 색인). "
        "셋 다 실측으로 기여가 확인됐고, 특히 ③ 은 `A1 → A1b` 전이로 **사다리에서 가장 큰 "
        "단일 Δ** 다. 즉 '공시는 관계형 데이터' 라는 전제는 살아 있고, 그 관계를 **무엇으로 "
        "정의하는가**만 우리 코퍼스에 맞게 바뀌었다.",
    },
    {
        "title": "그래서 기업이 아니라 발주처를 노드로 승격했다",
        "body": "여러 상장사와 계약하는 발주 주체를 노드로 올리면, 기업–발주처–기업 2-hop 으로 "
        "같은 사업의 계약을 모을 수 있다. 기업 간 직접 엣지가 희소한 것과 발주처 경유 경로가 "
        "있는 것은 다른 이야기다. 🔴 이 엣지는 **아직 미확정**이다 — 그리드 Δ 가 0 이고 근거로 "
        "쓰는 축은 골든 근거 완결성이다.",
    },
    {
        "title": "그리드에서는 발화 0건, 밸류체인에서만 듣는다",
        "body": "발주처 hop 은 3축 그리드에서 한 번도 발화하지 않고, 발주처를 지목한 밸류체인 "
        "문항에서만 발화한다. 그래서 그리드 Δ 는 측정값이 아니라 **정의상 0** 이다. "
        "🔴 밸류체인 정답률 Δ 는 **부호만 읽고 크기는 읽지 않는다** — n 이 작아 1문항이 크게 "
        "움직이고, 실제로 두 라운드 연속 같은 한 문항이 뒤집혔다. 정본 축은 골든 근거 회수 "
        "({vcGoldenA8} 대 {vcGoldenL3})다. ⚠️ 그리고 그 정답률에는 **부풀림 1건**이 들어 "
        "있다(개방형 judge 가 수치 자기일관성을 안 본다 — `total_plan §5` 채점 결함 ①). "
        "그리드·전체 분모에는 이 결함이 0건이므로 제출 성적은 여기 물려 있지 않다.",
    },
]


def build_counterparty() -> dict:
    graph = read_json("artifacts/counterparty_graph.json")
    stats = graph["stats"]
    density = read_json("artifacts/eda/relation_profile.json")["corp_to_corp_edge_density"]

    hubs = sorted(
        (node for node in graph["nodes"] if len(node.get("partners") or []) >= 2),
        key=lambda node: (-node["count"], node["canonical"]),
    )[:12]

    breakdown = [
        {
            "cls": cls,
            "label": COUNTERPARTY_CLASS_LABELS.get(cls, cls),
            "count": count,
            "pct": density["class_breakdown_pct"][cls],
        }
        for cls, count in sorted(density["class_breakdown"].items(), key=lambda kv: -kv[1])
    ]

    return {
        "metadata": metadata(
            "발주처 밸류체인 — 그래프 통계, 허브, 기업쌍, 엣지 밀도 판정",
            ["artifacts/counterparty_graph.json", "artifacts/eda/relation_profile.json"],
        ),
        "stats": {
            "nodes": stats["nodes"],
            "pairs": stats["pairs"],
            "minCount": stats["min_count"],
            "candidatesBeforeMinCount": stats["candidates_before_min_count"],
            "hubs": stats["hubs_2plus_partners"],
            "boilerplateExcludedUnique": stats["boilerplate_excluded_unique"],
            "vagueExcludedOccurrences": stats["vague_excluded_occurrences"],
        },
        "edgeDensity": {
            "nContractRecords": density["n_contract_records"],
            "nWithCounterparty": density["n_with_counterparty"],
            "fillRate": density["counterparty_fill_rate"],
            "strictDensity": density["edge_density_strict_over_all_contracts"],
            "classBreakdown": breakdown,
        },
        "hubs": [
            {
                "canonical": node["canonical"],
                "display": node.get("display") or node["canonical"],
                "kind": node.get("kind") or "-",
                "count": node["count"],
                "partners": node.get("partners") or [],
            }
            for node in hubs
        ],
        "pairs": [
            {"a": pair["a"], "b": pair["b"], "via": pair.get("via") or []}
            for pair in graph["pairs"]
        ],
        # 🔴 판정 서술의 자리표시자를 산출물 값으로 채운다. 문장에 수를 박지 않는다.
        "verdict": [
            {
                "title": v["title"].format(**verdict_values),
                "body": v["body"].format(**verdict_values),
            }
            for v in COUNTERPARTY_VERDICT
        ],
        "verdictSource": "docs/total_plan.md §7 · docs/pipeline/03-graph-schema.md C-0",
    }
```

`verdict_values` 는 `return` 문 **앞**에서 만든다 — 밸류체인 골든 회수는 밸류체인 산출물에서 온다:

```python
    vc_gen = resolve_generation()

    def vc_golden(arm: str) -> str:
        """밸류체인 골든 회수를 `vc/{arm}/report.json` 의 행에서 **센다**.

        🔴 `vc/summary.json` 의 `golden_evidence_hit` 은 **0 이다** (실측 2026-08-14) —
        그 집계는 그리드 트랙 기준이라 밸류체인에서 채워지지 않는다. summary 를 그대로
        읽으면 화면에 `0/0` 이 뜨고 그것이 거짓말이다. 행에서 세면 정본과 일치한다.

        🔴 `golden`(접수번호 목록)을 **세기만 하고 내보내지 않는다** (보안 ⑤).
        """
        rel = f"artifacts/ablation/{vc_gen}/vc/{arm}/report.json"
        if not (REPO / rel).exists():
            return "미측정"
        rows = read_json(rel)["rows"]
        hit = sum(len(r.get("golden_in_evidence") or []) for r in rows)
        total = sum(len(r.get("golden") or []) for r in rows)
        unreachable = sum(len(r.get("golden_unreachable") or []) for r in rows)
        # 🔴 도달불가를 뺀 조정 분모를 함께 낸다. 하나만 적으면 다음 사람이 두 수를 헷갈린다
        return f"{hit}/{total} (도달불가 {unreachable} 조정 {hit}/{total - unreachable})"

    verdict_values = {
        "fillRate": f"{density['counterparty_fill_rate'] * 100:.1f}%",
        "density": f"{density['edge_density_strict_over_all_contracts'] * 100:.1f}%",
        # 🔴 밸류체인 값은 **밸류체인 산출물**의 것이다. 그리드 값과 섞지 않는다.
        "vcGoldenA8": vc_golden("A8"),
        "vcGoldenL3": vc_golden("L3"),
    }
```

🔴 **밸류체인 arm 이 현 세대에 없으면 `"미측정"` 이 렌더된다.** 값을 채워 넣지 말고
그 문자열을 그대로 낸다 — **"없는 것" 과 "0" 은 다르다.**

> ✅ **이 셈법은 2026-08-14 에 실측으로 확인했다** — 현 세대 `vc/A8`·`vc/L3` 의 행에서 센
> 값이 `docs/total_plan.md` §0 의 골든 근거 수와 **일치**했고, `vc/summary.json` 의
> 집계 필드는 **0** 이었다. 🔴 **그 두 값을 여기 적지 않는다** (정본은 산출물과 §0 이다).

`BUILDERS` 에 두 줄 추가:

```python
    "corrections": ("site_corrections.json", build_corrections),
    "counterparty": ("site_counterparty.json", build_counterparty),
```

- [ ] **Step 5: 테스트 통과 확인**

Run: `python -m pytest tests/test_site_data.py -k "corrections or counterparty" -v`
Expected: 10건 PASS.

`test_build_counterparty_hubs_are_sorted_and_capped` 가 `len(partners) >= 2` 로 실패하면 `hubs_2plus_partners` 가 12보다 작은 것이다. 아래로 확인 후 상한을 실제 허브 수로 낮춘다:
```bash
python -c "
import json
g=json.load(open('artifacts/counterparty_graph.json',encoding='utf-8'))
h=[n for n in g['nodes'] if len(n.get('partners') or [])>=2]
print('허브 수:', len(h)); print([ (n['canonical'], n['count']) for n in sorted(h,key=lambda x:-x['count'])[:14]])
"
```

- [ ] **Step 6: 생성과 유출 게이트 재확인**

Run:
```bash
python scripts/build_site_data.py --only corrections --only counterparty
python -m pytest tests/test_site_data.py -v
```
Expected: 전건 PASS.

- [ ] **Step 7: `site/app/globals.css` 에 체인·그래프 스타일 추가 (반응형 블록 앞)**

```css
/* ── 정정 체인 ──────────────────────────────────────── */
.chain-flow {
  display: flex;
  flex-wrap: wrap;
  align-items: stretch;
  gap: 8px;
  margin-top: 16px;
}

.chain-node {
  display: grid;
  gap: 2px;
  min-width: 132px;
  border: 1px solid var(--line);
  border-top: 3px solid var(--amber);
  border-radius: 8px;
  background: var(--surface);
  padding: 8px 10px;
}

.chain-node:first-child {
  border-top-color: var(--primary);
}

.chain-node:last-child {
  border-top-color: var(--teal);
}

.chain-node .chain-pos {
  color: var(--muted);
  font-size: 11px;
  font-weight: 800;
}

.chain-node a {
  color: var(--primary);
  font-size: 12px;
  font-weight: 800;
  font-variant-numeric: tabular-nums;
  text-decoration: underline;
  overflow-wrap: anywhere;
}

.chain-node .chain-role {
  color: var(--muted);
  font-size: 11px;
}

.dist-bars {
  display: grid;
  gap: 6px;
  margin-top: 12px;
}

.dist-row {
  display: grid;
  grid-template-columns: 72px minmax(0, 1fr) 52px;
  align-items: center;
  gap: 10px;
  font-size: 12px;
}

.dist-row .dist-track {
  height: 14px;
  border-radius: 999px;
  background: var(--surface-strong);
  overflow: hidden;
}

.dist-row .dist-track span {
  display: block;
  height: 100%;
  background: var(--amber);
}

.dist-row .num {
  text-align: right;
  font-variant-numeric: tabular-nums;
  font-weight: 800;
}

/* ── 발주처 그래프 ──────────────────────────────────── */
.hub-card {
  display: grid;
  gap: 8px;
}

.hub-card-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 10px;
}

.hub-card-head strong {
  font-size: 15px;
}

.hub-card-head em {
  font-style: normal;
  color: var(--muted);
  font-size: 12px;
  font-weight: 800;
  font-variant-numeric: tabular-nums;
}

.hub-partners {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.pair-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  font-size: 13px;
}

.pair-row .via {
  border: 1px solid #c3d0f2;
  border-radius: 999px;
  background: #eef2ff;
  padding: 2px 10px;
  color: var(--primary-dark);
  font-size: 12px;
  font-weight: 800;
}

@media (max-width: 640px) {
  .dist-row {
    grid-template-columns: 60px minmax(0, 1fr) 44px;
  }
}
```

- [ ] **Step 8: `site/app/corrections/page.tsx` 작성**

```tsx
import data from "../../public/data/site_corrections.json";
import StatCard from "../../components/StatCard";

const exchange = data.groups.find((group) => group.docGroup === "exchange");
const chain = data.longestChain;

function roleOf(position: number, total: number) {
  if (position === 1) return "원공시";
  if (position === total) return "최신 정정본";
  return "정정본";
}

export default function CorrectionsPage() {
  const distribution = Object.entries(exchange?.lengthDistribution ?? {}).sort(
    (a, b) => Number(a[0]) - Number(b[0]),
  );
  const maxCount = Math.max(...distribution.map(([, count]) => count), 1);

  return (
    <main className="section">
      <div className="container">
        <div className="section-heading">
          <span className="badge">정정 체인</span>
          <h1>공시는 정정된다 — 그래서 체인을 복원한다</h1>
          <p className="lead">
            같은 사건이 원공시 한 건으로 끝나지 않습니다. 금액이 늘기도 줄기도 하므로 크기로 최신본을
            고르면 틀립니다. 원공시와 정정본을 체인으로 잇고 <strong>체인 위치로만</strong> 최신 여부를
            판정합니다.
          </p>
        </div>

        <section className="grid grid-4" aria-label="정정 체인 요약">
          <StatCard
            label="거래소공시 정정"
            value={exchange ? `${exchange.nCorrections}건` : "-"}
            helper={exchange ? `전체 ${exchange.nDocs}건 중` : undefined}
          />
          <StatCard
            label="복원된 체인"
            value={exchange ? exchange.nChains : "-"}
            helper={exchange ? `연결률 ${(exchange.linkRate * 100).toFixed(1)}%` : undefined}
          />
          <StatCard
            label="최장 체인"
            value={`${chain.members.length}본`}
            helper={`평균 ${exchange?.meanChainLength ?? "-"}본`}
          />
          <StatCard
            label="원본 코퍼스 밖"
            value={exchange ? `${(exchange.orphanRate * 100).toFixed(1)}%` : "-"}
            helper="2023-01-01 이전 문서를 참조해 연결 불가"
          />
        </section>

        <section className="panel spacious">
          <div className="section-heading compact">
            <span className="badge">최장 체인 · {chain.docGroup}</span>
            <h2>한 사건이 {chain.members.length}번 정정된 사례</h2>
            <p className="lead">
              루트 {chain.root} 에서 시작합니다. 접수번호를 누르면 DART 원문으로 갑니다. 정정이 예외가
              아니라 일상이라는 것이 이 화면의 요점입니다.
            </p>
          </div>
          <div className="chain-flow">
            {chain.members.map((member) => (
              <div className="chain-node" key={member.rceptNo}>
                <span className="chain-pos">#{member.position}</span>
                <a href={member.dartUrl} target="_blank" rel="noreferrer">
                  {member.rceptNo}
                </a>
                <span className="chain-role">{roleOf(member.position, chain.members.length)}</span>
              </div>
            ))}
          </div>
        </section>

        <section className="panel spacious">
          <div className="section-heading compact">
            <span className="badge">문서군별</span>
            <h2>연결률이 문서군마다 크게 다르다</h2>
            <p className="lead">
              주요사항보고서·지분공시는 정정본의 원본이 대부분 코퍼스 밖입니다. 이것은 우리 파싱의
              실패가 아니라 수집 범위의 한계이므로, 데이터 상한으로 따로 셉니다.
            </p>
          </div>
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>문서군</th>
                  <th className="num">문서</th>
                  <th className="num">정정</th>
                  <th className="num">체인</th>
                  <th className="num">연결률</th>
                  <th className="num">최장</th>
                  <th className="num">평균</th>
                </tr>
              </thead>
              <tbody>
                {data.groups.map((group) => (
                  <tr key={group.docGroup}>
                    <td>
                      <strong>{group.label}</strong>
                      <span>{group.docGroup}</span>
                    </td>
                    <td className="num">{group.nDocs.toLocaleString("ko-KR")}</td>
                    <td className="num">{group.nCorrections.toLocaleString("ko-KR")}</td>
                    <td className="num">{group.nChains.toLocaleString("ko-KR")}</td>
                    <td className="num">{(group.linkRate * 100).toFixed(1)}%</td>
                    <td className="num">{group.maxChainLength}</td>
                    <td className="num">{group.meanChainLength}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        {exchange ? (
          <section className="panel spacious">
            <div className="section-heading compact">
              <span className="badge">체인 길이 분포 · 거래소공시</span>
              <h2>대부분은 2본이지만 꼬리가 길다</h2>
              <p className="lead">
                꼬리가 있다는 것이 중요합니다. 체인 상한을 짧게 잡으면 긴 체인의 근거를 놓칩니다.
              </p>
            </div>
            <div className="dist-bars">
              {distribution.map(([length, count]) => (
                <div className="dist-row" key={length}>
                  <span>{length}본</span>
                  <span className="dist-track">
                    <span style={{ width: `${(count / maxCount) * 100}%` }} />
                  </span>
                  <span className="num">{count}</span>
                </div>
              ))}
            </div>
          </section>
        ) : null}

        <section className="grid grid-3 spacious" aria-label="정정 처리 원칙">
          {data.policy.map((item) => (
            <article className="panel finding-card" key={item.title}>
              <p className="eyebrow">처리 원칙</p>
              <h3>{item.title}</h3>
              <p>{item.body}</p>
            </article>
          ))}
        </section>
      </div>
    </main>
  );
}
```

- [ ] **Step 9: `site/app/counterparty/page.tsx` 작성**

```tsx
import data from "../../public/data/site_counterparty.json";
import StatCard from "../../components/StatCard";

const density = data.edgeDensity;

export default function CounterpartyPage() {
  const maxPct = Math.max(...density.classBreakdown.map((item) => item.pct), 1);

  return (
    <main className="section">
      <div className="container">
        <div className="section-heading">
          <span className="badge">발주처 밸류체인</span>
          <h1>기업 간 직접 엣지가 안 서서, 발주처를 노드로 올렸다</h1>
          <p className="lead">
            "공시는 관계형 데이터" 라는 전제를 우리 코퍼스로 먼저 재봤습니다. 🔴 기각된 것은{" "}
            <strong>그 전제가 아니라 논문이 쓴 엣지 정의</strong>입니다 — 기업—기업 직접 엣지
            하나와 그것을 전제로 한 커뮤니티 탐지입니다. 노드·엣지를 코퍼스에 맞게 재정의한
            쪽은 채택됐고, 성적의 대부분이 거기서 나왔습니다.
          </p>
        </div>

        <section className="grid grid-4" aria-label="그래프 요약">
          <StatCard
            label="계약 상대 기재율"
            value={`${(density.fillRate * 100).toFixed(1)}%`}
            helper={`계약 ${density.nContractRecords.toLocaleString("ko-KR")}건 중 ${density.nWithCounterparty.toLocaleString("ko-KR")}건`}
          />
          <StatCard
            label="기업–기업 엣지 밀도"
            value={`${(density.strictDensity * 100).toFixed(1)}%`}
            helper="유니버스 안 상장사로 정확히 연결되는 비율"
          />
          <StatCard label="발주처 노드" value={data.stats.nodes} helper={`허브 ${data.stats.hubs}개`} />
          <StatCard
            label="발주처 경유 기업쌍"
            value={data.stats.pairs}
            helper={`최소 등장 ${data.stats.minCount}회 필터 적용`}
          />
        </section>

        <section className="panel spacious">
          <div className="section-heading compact">
            <span className="badge">왜 기업 그래프가 안 서나</span>
            <h2>계약 상대의 정체</h2>
            <p className="lead">
              상대가 적혀 있어도 대부분 우리 유니버스 밖입니다. 해외 법인, 공공 부문, 비공개 처리가
              합쳐서 압도적입니다.
            </p>
          </div>
          <div className="dist-bars">
            {density.classBreakdown.map((item) => (
              <div className="dist-row" key={item.cls}>
                <span>{item.label}</span>
                <span className="dist-track">
                  <span style={{ width: `${(item.pct / maxPct) * 100}%` }} />
                </span>
                <span className="num">{item.pct.toFixed(1)}%</span>
              </div>
            ))}
          </div>
          <p className="helper">
            비공개 처리 {density.classBreakdown.find((item) => item.cls === "anonymized")?.pct.toFixed(1)}% 는
            노력으로 넘을 수 없는 상한입니다 — 원본이 '-' 입니다.
          </p>
        </section>

        <section className="panel spacious">
          <div className="section-heading compact">
            <span className="badge">허브</span>
            <h2>여러 상장사와 계약하는 발주처</h2>
            <p className="lead">
              이 주체를 노드로 올리면 같은 사업의 계약을 기업–발주처–기업 2-hop 으로 모을 수 있습니다.
            </p>
          </div>
          <div className="grid grid-3">
            {data.hubs.map((hub) => (
              <article className="card hub-card" key={hub.canonical}>
                <div className="hub-card-head">
                  <strong>{hub.display}</strong>
                  <em>{hub.count}건</em>
                </div>
                <span className="chip">{hub.kind}</span>
                <div className="hub-partners">
                  {hub.partners.map((partner) => (
                    <span className="chip" key={partner}>
                      {partner}
                    </span>
                  ))}
                </div>
              </article>
            ))}
          </div>
        </section>

        <section className="panel spacious">
          <div className="section-heading compact">
            <span className="badge">기업쌍 {data.pairs.length}개</span>
            <h2>발주처를 경유해 이어지는 상장사</h2>
            <p className="lead">직접 엣지가 아니라 2-hop 경로입니다. 가운데 칩이 경유 발주처입니다.</p>
          </div>
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>기업 A</th>
                  <th>경유 발주처</th>
                  <th>기업 B</th>
                </tr>
              </thead>
              <tbody>
                {data.pairs.map((pair) => (
                  <tr key={`${pair.a}-${pair.b}`}>
                    <td>
                      <strong>{pair.a}</strong>
                    </td>
                    <td>
                      <span className="pair-row">
                        {pair.via.map((via) => (
                          <span className="via" key={via}>
                            {via}
                          </span>
                        ))}
                      </span>
                    </td>
                    <td>
                      <strong>{pair.b}</strong>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        <section className="grid grid-3 spacious" aria-label="판정">
          {data.verdict.map((item) => (
            <article className="panel finding-card" key={item.title}>
              <p className="eyebrow">판정</p>
              <h3>{item.title}</h3>
              <p>{item.body}</p>
            </article>
          ))}
        </section>
      </div>
    </main>
  );
}
```

- [ ] **Step 10: 타입체크와 빌드**

Run: `cd site && npm run typecheck && npm run build`
Expected: 오류 0건, `/corrections` · `/counterparty` 프리렌더 성공.

- [ ] **Step 11: 육안 확인**

Run: `cd site && npm run dev`
Expected:
- `/corrections` — 최장 체인 19개 노드가 흐름으로 늘어서고 첫 노드는 파란 상단선(원공시), 마지막은 초록(최신 정정본). 접수번호가 DART 로 열린다. 문서군 표에서 `major` 연결률이 0.6% 로 극히 낮다. 길이 분포 막대에 2본이 가장 길고 12·14·15·19 꼬리가 보인다.
- `/counterparty` — 엣지 밀도 카드가 `12.4%`, 기재율 `97.5%`. 계층 막대에서 해외 법인이 최상단. 허브 카드에 방위사업청·한국전력공사 등이 파트너 칩과 함께 뜬다. 기업쌍 표에 경유 발주처 칩이 보인다.

확인 후 종료.

- [ ] **Step 12: 커밋**

```bash
git add scripts/build_site_data.py tests/test_site_data.py site/app/corrections site/app/counterparty site/app/globals.css site/public/data/site_corrections.json site/public/data/site_counterparty.json
git commit -m "feat: 정정 체인·발주처 밸류체인 화면"
```

---

### Task 10: 파이프라인 · 데이터 화면 + README + 마감 게이트

레퍼런스의 `lib/methodology.ts` 와 `/data` 에 대응한다. 마지막 Task 이므로 전 페이지 회귀 확인과 README 도 여기서 닫는다.

**Files:**
- Modify: `scripts/build_site_data.py` (`build_data` 추가)
- Modify: `tests/test_site_data.py`
- Create: `site/lib/pipeline.ts`
- Create: `site/app/pipeline/page.tsx`
- Create: `site/app/data/page.tsx`
- Modify: `site/app/globals.css`
- Create: `site/README.md`
- Modify: `README.md` (저장소 루트 — `site/` 항목 추가)
- Create: `site/public/data/site_data.json` (생성물)

**Interfaces:**
- Consumes: 전 Task 의 산출물 · `StatCard` (Task 1)
- Produces:
  - `build_data() -> dict`
    - `metadata`
    - `corpus: { totalMb, nDocsManifest, nDocsParsed, groups: [{ docGroup, label, nFiles, mb, avgKb, sharePct, sampling }] }`
    - `indexes: [{ name, label, note }]`
    - `recall: { cohort, golden, recall5, recall5Rate, evidenceHitRate, byDifficulty: [{ key, golden, recall5, rate }], gateVerdict }`
    - `apiContract: { endpoint, keys: str[], note }`
    - `artifactsNote: str`
  - `build_pipeline() -> dict` — `{metadata, phases, gates, tests, source}`. 🔴 `parse_ssot()` 파싱값
  - `site/lib/pipeline.ts` — `export type PipelinePhase` · `export const pipelinePhases: PipelinePhase[]` (9개).
    🔴 **산문만.** `gate`·`creditWon`·`status` 는 `site_pipeline.json` 에서 `order` ↔ `no` 로 조인한다

- [ ] **Step 1: 실패하는 테스트를 쓴다 — `tests/test_site_data.py` 맨 끝에 추가**

```python
# ── 데이터 ───────────────────────────────────────────────────────────

def test_build_data_shape():
    payload = bsd.build_data()
    assert set(payload) == {
        "metadata", "corpus", "indexes", "recall", "apiContract", "artifactsNote",
    }
    assert [g["docGroup"] for g in payload["corpus"]["groups"]] == [
        "exchange", "major", "holding", "periodic",
    ]


def test_build_data_periodic_dominates_corpus():
    """정기공시가 코퍼스의 95% 이상이다 — 이 사실이 인덱스 설계를 결정했다."""
    payload = bsd.build_data()
    periodic = next(g for g in payload["corpus"]["groups"] if g["docGroup"] == "periodic")
    assert periodic["sharePct"] > 90


def test_build_data_corpus_shares_sum_to_hundred():
    payload = bsd.build_data()
    total = sum(g["sharePct"] for g in payload["corpus"]["groups"])
    assert 99.0 <= total <= 101.0, total


def test_build_data_recall_meets_gate():
    """게이트 D 문턱은 0.85 다."""
    payload = bsd.build_data()
    assert payload["recall"]["recall5Rate"] >= 0.85


def test_build_data_api_contract_has_five_keys():
    payload = bsd.build_data()
    assert payload["apiContract"]["keys"] == [
        "question_id", "question", "retrieved_context", "think_trace", "answer",
    ]


def test_all_builders_registered():
    """빌더 9개가 전부 등록돼 있어야 한다 — 하나 빠지면 화면이 낡은 JSON 을 읽는다."""
    assert set(bsd.BUILDERS) == {
        "meta", "home", "answers", "search", "ablation", "errors", "corrections",
        "counterparty", "data",
    }


def test_main_generates_every_file(tmp_path, monkeypatch):
    monkeypatch.setattr(bsd, "OUT_DIR", tmp_path)
    assert bsd.main([]) == 0
    produced = {p.name for p in tmp_path.glob("site_*.json")}
    assert produced == {filename for filename, _ in bsd.BUILDERS.values()}
```

등록돼야 하는 빌더는 9개다 — `meta`·`home`·`answers`·`search`·`ablation`·`errors`·`corrections`·`counterparty`·`data`. Task 2·4·5·6·7·8·9·10 이 각자 하나 이상을 추가하므로, 이 테스트가 실패하면 앞선 Task 의 `BUILDERS` 등록이 빠진 것이다.

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_site_data.py -k "data or builders or main_generates" -v`
Expected: FAIL — `AttributeError: module 'build_site_data' has no attribute 'build_data'`

- [ ] **Step 3: `build_data` 구현 — `build_counterparty` 아래, `BUILDERS` 위**

```python
INDEX_CATALOG = [
    {"name": "exchange_bm25.json", "label": "거래소공시 BM25", "note": "단일판매·공급계약, 유상증자 등 1,469건"},
    {"name": "major_bm25.json", "label": "주요사항보고서 BM25", "note": "합병·분할·사채발행 등 598건"},
    {"name": "holding_bm25.json", "label": "지분공시 BM25", "note": "대량보유 상황보고 등 1,083건"},
    {"name": "section_index.json", "label": "정기공시 섹션 인덱스", "note": "목차 축으로 157,397 조각(비스텁 133,601). 상주 427MB"},
    {"name": "counterparty_graph.json", "label": "발주처 그래프", "note": "노드 145 · 기업쌍 44"},
]

API_CONTRACT = {
    # 🔴 경로·키·프로토콜은 **규정**이라 상수로 둔다 (측정값이 아니다). 정본은 아래 링크.
    "endpoint": "GET /answer?question_id=&question=",
    "keys": ["question_id", "question", "retrieved_context", "think_trace", "answer"],
    "protocol": "HTTP 기본 · HTTPS 선택(self-signed 무방) · 표준 포트 · 도메인 불필요 · 인증 헤더 없음",
    "calling": "순차 1건씩 · 타임아웃 300초 · 타임아웃/5xx 시 최대 2회 재시도",
    # 🔴 공인 IP 를 사이트에 싣지 않는다. 평가용 엔드포인트이고 정본은 deploy/README.md 다.
    #    사이트가 그 IP 를 발행하면 평가 서버가 외부 트래픽을 받을 수 있다 —
    #    c2-g3a 2 vCPU / 4 GB 에서 그것은 단건 지연을 흔든다.
    "endpointSource": "deploy/README.md 첫 절 (공인 IP·유지 기한의 정본)",
    "specSource": "docs/organizer-qa.md · docs/CONVENTION.md §4",
    "note": "주최 측 고정 계약이다. 키를 늘리거나 이름을 바꾸지 않는다. 모든 필드가 string 이고 "
    "빈 문자열도 허용된다. retrieved_context 가 근거, think_trace 가 추론 경로다. "
    "🔴 답할 수 없을 때도 5xx 를 던지지 않고 200 + 정보한계 응답을 낸다 — 재시도가 "
    "비결정적 실패에서 크레딧을 3배로 만들기 때문이다.",
}

ARTIFACTS_NOTE = (
    "artifacts/ 는 gitignore 대상이다. 이 사이트가 읽는 JSON 은 scripts/build_site_data.py 가 "
    "생성해 site/public/data/ 에 커밋한 것이므로, 산출물이 없는 클론에서도 사이트는 빌드된다. "
    "🔴 빌드가 읽지 않는 것: evalset/heldout.jsonl · heldout 산출물 폴더 전체 · 코퍼스 원문. "
    "heldout 18문항은 최종 구성 1회용이고 두 번 다 썼으므로 집계 수치조차 싣지 않는다. "
    "🔴 평가셋의 질문 전문과 정답도 싣지 않는다 — 우리가 만든 라벨이고, 그 라벨의 부풀림이 "
    "문서에 기록돼 있다. 이 사이트에 보이는 문항 정보는 번호와 축 메타뿐이다. "
    "🔴 전처리 산출물은 제출물 4종의 하나이고, 대용량은 클라우드 스토리지 링크로 제출한다 — "
    "이 사이트가 그 배포 경로가 아니다."
)


def _recall_cohort(recall: dict) -> tuple[str, dict]:
    """가장 큰 코호트를 고른다. 키 이름은 재측정 때 바뀔 수 있어 값으로 판단한다."""
    best_name = ""
    best: dict = {}
    for name, payload in recall["cohorts"].items():
        block = payload.get("harness") or payload.get("cached") or {}
        if block.get("golden", 0) > best.get("golden", 0):
            best_name, best = name, block
    return best_name, best


def build_data() -> dict:
    profile = read_json("artifacts/eda/doc_profile.json")
    corpus_bytes = profile["corpus_bytes"]
    sampling = profile["meta"].get("sampling") or {}
    recall = read_json("artifacts/eval/recall_v2.json")
    cohort_name, cohort = _recall_cohort(recall)

    groups = []
    for group in ("exchange", "major", "holding", "periodic"):
        row = corpus_bytes.get(group)
        if not row:
            continue
        groups.append(
            {
                "docGroup": group,
                "label": DOC_GROUP_LABELS[group],
                "nFiles": row["n_xml_files"],
                "mb": row["mb"],
                "avgKb": row["avg_kb"],
                "sharePct": row["share_pct"],
                "sampling": sampling.get(group, "-"),
            }
        )

    by_difficulty = []
    for key in ("하", "중", "상"):
        row = (cohort.get("by_difficulty") or {}).get(key)
        if not row:
            continue
        by_difficulty.append(
            {
                "key": key,
                "golden": row["golden"],
                "recall5": row["recall5"],
                "rate": round(row["rate"], 4),
            }
        )

    return {
        "metadata": metadata(
            "데이터·산출물 — 코퍼스 프로파일, 인덱스 목록, 게이트 D 회수 지표, API 계약",
            [
                "artifacts/eda/doc_profile.json",
                "artifacts/eval/recall_v2.json",
                "docs/CONVENTION.md",
            ],
        ),
        "corpus": {
            "totalMb": round(sum(g["mb"] for g in groups), 1),
            "nDocsManifest": profile["meta"]["n_docs_in_manifest"],
            "nDocsParsed": profile["meta"]["n_docs_parsed"],
            "groups": groups,
        },
        "indexes": INDEX_CATALOG,
        "recall": {
            "cohort": cohort_name,
            "golden": cohort.get("golden"),
            "recall5": cohort.get("recall5"),
            "recall5Rate": cohort.get("recall5_rate"),
            "evidenceHitRate": cohort.get("evidence_hit_rate"),
            "byDifficulty": by_difficulty,
            "gateVerdict": recall.get("gate_d_verdict"),
        },
        "apiContract": API_CONTRACT,
        "artifactsNote": ARTIFACTS_NOTE,
    }
```

`BUILDERS` 에 한 줄 추가:

```python
    "data": ("site_data.json", build_data),
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_site_data.py -k "data or builders or main_generates" -v`
Expected: PASS.

`test_build_data_recall_meets_gate` 가 `None >= 0.85` 로 실패하면 `recall_v2.json` 의 코호트 블록에 `recall5_rate` 가 없는 것이다. 실제 키를 확인해 `_recall_cohort` 를 맞춘다:
```bash
python -c "
import json
d=json.load(open('artifacts/eval/recall_v2.json',encoding='utf-8'))
for name,payload in d['cohorts'].items():
    print(name, list(payload))
    for sub in ('harness','cached'):
        if sub in payload: print('  ',sub, list(payload[sub]))
"
```

- [ ] **Step 5: `site/lib/pipeline.ts` 작성**

레퍼런스 `lib/methodology.ts` 의 형태를 그대로 쓴다 — 타입 + 배열 상수. 9개 Phase 는 `docs/pipeline/` 과 `docs/total_plan.md` §1 의 한 줄 요약에서 온다.

> 🔴 **2026-08-14 재검토 — 이 파일에서 `gate`·`creditWon`·수치 서술을 걷어냈다.**
> 종전 판은 Phase 별 게이트(`0/3`·`68/71` 합)와 크레딧을 TS 상수로 들고 있었고, 게이트 E 가
> `A3` 최종 기각으로 **3/3** 이 되면서 **9행 중 한 행이 낡고 총계가 틀어졌다.** 지금은
> `parse_ssot()["phases"]` 가 `site_pipeline.json` 으로 나가고, TS 는 **산문만** 갖는다.
> 매칭 키는 `order`(`"01"`~`"09"`)다.
>
> ```python
> # scripts/build_site_data.py — BUILDERS 에 "pipeline" 추가
> def build_pipeline() -> dict:
>     ssot = parse_ssot()
>     return {
>         "metadata": metadata(
>             "Phase A~I 게이트·크레딧 — docs/total_plan.md §1 Phase 표 파싱값. "
>             "산문(제목·상세·결론)은 site/lib/pipeline.ts 가 갖는다.",
>             [SSOT_MD],
>         ),
>         "phases": ssot["phases"],          # [{no, doc, phase, status, gate, credit}]
>         "gates": ssot["gates"],            # 게이트 총계
>         "tests": ssot["tests"],
>         "source": f"{SSOT_MD} §1",
>     }
> ```
>
> 🔴 **TS 쪽에 `gate`·`creditWon` 필드를 되살리지 마라.** 되살리면 다음 라운드에
> `05` 행이 또 낡는다. 화면은 `order` 로 두 원천을 조인한다.

```ts
export type PipelinePhase = {
  slug: string;
  order: string;      // 🔴 site_pipeline.json 의 `no` 와 조인하는 키
  phase: string;
  title: string;
  shortDescription: string;
  detail: string[];
  outcome: string;
  // 🔴 gate · creditWon · status 는 여기 두지 않는다 — total_plan §1 파싱값이 정본이다
};

export const pipelinePhases: PipelinePhase[] = [
  {
    slug: "eda",
    order: "01",
    phase: "A",
    title: "데이터 EDA — 전제부터 재본다",
    shortDescription: "코퍼스를 먼저 프로파일링해 논문이 전제한 그래프 정의를 검증했다.",
    detail: [
      "문서군 4종의 크기·필드 채움률·표 구조·목차 트리를 전수 또는 층화표본으로 훑었다.",
      "기업–기업 엣지 밀도를 직접 재서 '공시는 관계형 데이터' 라는 전제를 검증했다.",
      "정정 체인 복원 가능성과 관련공시 참조 해소율을 측정해 데이터 상한을 먼저 확정했다.",
    ],
    // 🔴 "GraphRAG 기각" 이 아니다. 기각된 것은 기업—기업 엣지 정의 하나다 (total_plan §7)
    outcome:
      "기업–기업 엣지 정의를 기각하고 노드·엣지를 코퍼스에 맞게 재정의했다. 재정의한 쪽은 채택됐다.",
  },
  {
    slug: "golden-set",
    order: "02",
    phase: "B",
    title: "Golden Test Set — 측정 도구를 먼저 만든다",
    shortDescription: "질의 유형 × 답변 형태 × 난이도 3축 그리드로 문항을 설계했다.",
    detail: [
      "18개 셀에 dev 문항과 heldout 문항을 채워 셀당 표본을 확보했다.",
      "감시가 목적인 회귀 문항과 발주처가 필요한 밸류체인 문항은 그리드 밖 별도 파일에 뒀다 — 그리드에 넣으면 난이도 축이 측정 도구이기를 그만둔다.",
      "정보한계 문항은 answerable=false 로 두어 거절이 정답이 되게 했다.",
      "🔴 heldout 은 최종 구성 1회용이고 두 번 다 썼다. 그래서 이 사이트는 문항도 정답도 공개하지 않는다.",
    ],
    // 🔴 문항 수를 적지 않는다 — 종전 판의 "106문항 (그리드 90 + …)" 은 어느 세대와도 안 맞았다.
    //    분모는 site_meta.json 과 site_errors.json 이 산출물에서 세어 낸다.
    outcome: "트랙을 합치지 않는다 — 그리드·정보한계·회귀·밸류체인이 각자 분모를 갖는다.",
  },
  {
    slug: "graph-schema",
    order: "03",
    phase: "C",
    title: "Graph Schema — 기업이 아니라 발주처를 노드로",
    shortDescription: "기업–공시–이벤트 2부 그래프에 발주처 노드를 더했다.",
    detail: [
      "기업 간 직접 엣지가 희소하다는 EDA 결과를 받아, 여러 상장사와 계약하는 발주처를 노드로 승격했다.",
      "관련공시 참조와 지분 보유 관계를 엣지로 잇고, 상용구·모호 표현은 정규화 단계에서 제외했다.",
      "🔴 2부 그래프를 탐색 축이 아니라 메타 축(doc_subtype 문서군별 색인)으로 쓴 것이 사다리 최대 전이를 냈다.",
    ],
    // 🔴 노드·엣지 건수는 site_counterparty.json 의 stats 에서 렌더한다 (여기 적지 않는다)
    outcome: "발주처 경유 2-hop 은 아직 미확정 엣지다 — 그리드 Δ 0, 근거 완결성 축에서만 신호가 있다.",
  },
  {
    slug: "retriever",
    order: "04",
    phase: "D",
    title: "Retriever — 문서군별 별도 인덱스 + 라우터",
    shortDescription: "하나의 인덱스가 아니라 문서군마다 인덱스를 따로 두고 라우터가 고른다.",
    detail: [
      "정기공시는 목차 축으로 섹션을 잘라 색인했다 — 100자 미만 스텁을 뺀 분모를 따로 적는다.",
      "메타 프리필터(기업·기간·공시유형)를 BM25 앞에 두어 후보를 먼저 좁힌다.",
      "게이트 수치에 모집단과 시점 라벨을 붙이도록 고쳤다 — 라벨 없는 수치가 낡아서 한 번 오판했다.",
    ],
    // 🔴 Recall@5 값은 site_data.json(recall_v2.json 파싱)에서 렌더한다. 코호트 라벨을 반드시 붙인다
    outcome: "Recall@5 는 코호트별로 따로 읽는다 — 하나의 수로 합치면 게이트 판정이 거짓이 된다.",
  },
  {
    slug: "reranker",
    order: "05",
    phase: "E",
    title: "Re-ranker — 겨냥할 실패가 사라져 최종 기각",
    shortDescription: "붙일 수는 있지만 겨냥할 실패가 세 분모 전부에서 0 이 됐다.",
    detail: [
      "리랭커가 고칠 수 있는 실패를 세어 보니 재검토 문턱에 닿지 않았다.",
      "McNemar 로 보면 불일치쌍이 너무 적어 판정 자체가 불가능한 구간이었다.",
      "🔴 종전에는 '보류' 였고, 겨냥 0 을 그리드·전체·밸류체인 세 분모 전부에서 확인해 **최종 기각**했다.",
      "🔴 닫은 사유는 '성능이 안 나왔다' 가 아니라 '잴 대상이 사라졌다' 이고, 다시 여는 조건을 기계적으로 명시해 뒀다.",
    ],
    outcome: "기각도 결과다 — 다시 여는 조건이 문서에 박혀 있다.",
  },
  {
    slug: "agent-tool",
    order: "06",
    phase: "F",
    title: "Agent + Tool — 한 번 더 물어보는 루프",
    shortDescription: "ReAct 루프에 정정체인 hop·다중지점 분해·발주처 2-hop 을 붙였다.",
    detail: [
      "근거가 부족하다고 판정되면 hop 을 늘려 다시 회수한다. 난이도 '상' 의 정답률이 여기서 크게 튄다.",
      "계산은 모델이 아니라 도구가 수행한다 — 도구 종수는 소스에서 센 값을 렌더한다.",
      "정기공시 섹션 하강은 붙여서 재본 뒤 넣지 않기로 판정했다 — 회수는 동일한데 스텝이 2배가 된다.",
      "🔴 타임아웃 300초가 확정돼 지연은 더 이상 제약이 아니다. 묶는 이유는 크레딧과 과잉탐색뿐이다.",
    ],
    // 🔴 "+33.3%p" 를 적지 않는다 — 재측정으로 값이 바뀌었다. 축별 Δ 는 히트맵이 산출물에서 낸다
    outcome: "커버리지 먼저, 그다음 루프 — 난이도 '상' 에서 두 칸이 거의 같은 크기로 튄다.",
  },
  {
    slug: "verifier",
    order: "07",
    phase: "G",
    title: "Verifier — LLM 을 부르지 않는 검증",
    shortDescription: "계산 검증과 완결성 검증을 규칙으로 구현했다.",
    detail: [
      "답변의 수치를 근거의 수치와 다시 맞추고, 열거형 질의는 빠진 항목이 있는지 본다.",
      "둘 다 LLM 호출이 0회이므로 한계 비용이 0이다. 그래서 정답률 Δ 가 0이어도 넣었다.",
      "거짓 경보는 전부 발화를 줄이는 방향으로만 고쳤다 — 새 발화 조건을 추가하지 않았다.",
      "전제가 선 횟수와 발화 횟수를 분리해 기록한다. 전체 문항으로 나누면 규칙이 저절로 좋아진 것처럼 보인다.",
      "🔴 한계: 검증기는 답변 수치 중 근거와 가장 가까운 것을 고르므로 10배 단위환산 오류가 통과하는 구간이 남아 있었다. 그 구간을 좁힌 이력과 남은 구멍을 함께 적는다.",
    ],
    outcome: "정답 문항의 거짓 경보를 0 으로 유지한다 — 정밀도를 재현율보다 우선했다.",
  },
  {
    slug: "error-analysis",
    order: "08",
    phase: "H",
    title: "Error Analysis — 처음 틀어진 곳에만 배분한다",
    shortDescription: "오답을 6단계 중 처음 틀어진 단계에 귀속하고 3축으로 교차했다.",
    detail: [
      "오답 전건을 원문까지 대조했다. 상관을 원인으로 적으면 다음 사람이 엉뚱한 것을 고친다.",
      "사전 예상은 생성·도구 병목이었는데 실측은 검색 쪽이었다 — 예상이 틀린 이력을 지우지 않는다.",
      "실패 유형을 재현하는 회귀 문항을 만들어 그리드 밖에 뒀다.",
      "🔴 자동 arm 배분표는 원문 대조와 원리적으로 일치시킬 수 없다 — 그 한계를 화면에 함께 적는다.",
    ],
    // 🔴 "13 · 11 · 12" 를 적지 않는다 — 세 수 전부 재측정으로 바뀌었다. site_errors.json 이 센다
    outcome: "잔여 실패는 **세 분모로 각각** 읽는다 — 하나의 수로 적으면 어느 것도 맞지 않는다.",
  },
  {
    slug: "ablation",
    order: "09",
    phase: "I",
    title: "Ablation — 사다리와 LOO",
    shortDescription: "구성요소를 켜 올리는 사다리와 빼는 LOO 를 세대마다 측정했다.",
    detail: [
      "앞 세대 산출물을 한 바이트도 덮지 않고 새 세대를 따로 냈다 — 측정이 계획을 뒤집은 이력이 산출물이다.",
      "A0→A1 의 부호가 뒤집혔다. 커버리지가 랭킹보다 먼저라는 뜻이다.",
      "Δ 가 0인 구성도 넣었다. 판단 기준을 Δ 가 아니라 한계 비용으로 바꿨다.",
      "heldout 낙폭 게이트는 분자가 영구 고정돼 1문항이 임계를 넘기므로 은퇴시켰다.",
      "🔴 세대가 다르면 Δ 를 비교하지 않는다. 사다리와 LOO 의 세대 라벨을 화면에 붙인다.",
      "🔴 기각된 구성의 지출도 원장에 싣는다 — 빼면 다음 사람이 같은 것을 다시 산다.",
    ],
    outcome: "제출 구성 A8. 성능은 A1b·A4 두 arm 에서 나왔고 둘 다 LLM 을 더 쓰는 것이 아니다.",
  },
];
```

- [ ] **Step 6: `site/app/globals.css` 에 파이프라인 스타일 추가 (반응형 블록 앞)**

```css
/* ── 파이프라인 ─────────────────────────────────────── */
.phase-card {
  display: grid;
  gap: 10px;
  border-left: 4px solid var(--primary);
}

.phase-card.is-paused {
  border-left-color: var(--amber);
}

.phase-head {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 10px;
}

.phase-order {
  color: var(--muted);
  font-size: 13px;
  font-weight: 800;
  font-variant-numeric: tabular-nums;
}

.phase-card h2 {
  margin: 0;
  font-size: 19px;
}

.phase-card ul {
  margin: 0;
  padding-left: 18px;
  color: var(--muted);
  font-size: 14px;
}

.phase-card ul li + li {
  margin-top: 6px;
}

.phase-outcome {
  margin: 0;
  padding: 10px 12px;
  border-radius: 8px;
  background: var(--surface-strong);
  color: var(--ink-soft);
  font-size: 13px;
  font-weight: 700;
}

.contract-keys {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 12px;
}

.contract-keys code {
  border: 1px solid var(--line);
  border-radius: 6px;
  background: var(--surface-strong);
  padding: 4px 10px;
  font-size: 13px;
}

.note-panel {
  border-left: 4px solid var(--amber);
}
```

- [ ] **Step 7: `site/app/pipeline/page.tsx` 작성**

```tsx
import { pipelinePhases } from "../../lib/pipeline";
import meta from "../../public/data/site_meta.json";
import pipelineData from "../../public/data/site_pipeline.json";

// 🔴 산문(TS)과 게이트·크레딧(정본 파싱값)을 `order` ↔ `no` 로 조인한다.
//    게이트 수를 TS 에 두면 다음 라운드에 낡는다 — 실제로 05 행이 그렇게 낡았다.
const byOrder = new Map(pipelineData.phases.map((row) => [row.no, row]));

export default function PipelinePage() {
  const done = pipelineData.phases.filter((row) => row.status.includes("완료")).length;

  return (
    <main className="section">
      <div className="container narrow">
        <div className="section-heading">
          <span className="badge">파이프라인 · Phase A~I</span>
          {/* 🔴 뒤집힌 횟수를 적지 않는다 — 라운드마다 늘어난다(정본 total_plan §7 · v4 §7). */}
          <h1>측정이 계획을 여러 번 뒤집었다</h1>
          <p className="lead">
            데이터를 먼저 보고, 측정 도구를 먼저 만들고, 그 다음에 모델을 붙이는 순서로 갔습니다. 그
            결과 계획이 여러 번 뒤집혔고, 뒤집힌 이력을 지우지 않고 남겨 뒀습니다 — 기각도 결과입니다.
            뒤집힌 항목의 정본 목록은 <code>docs/total_plan.md</code> §7 입니다.
          </p>
        </div>

        <section className="grid grid-3" aria-label="파이프라인 요약">
          <article className="card">
            <p className="eyebrow">완료 Phase</p>
            <div className="metric">
              {done}/{pipelineData.phases.length}
            </div>
            <p className="helper">docs/total_plan.md §1 의 Phase 표를 그대로 읽습니다</p>
          </article>
          <article className="card">
            <p className="eyebrow">게이트</p>
            <div className="metric">
              {meta.gates.closed}/{meta.gates.total}
            </div>
            <p className="helper">문서 체크박스를 실제로 센 값입니다</p>
          </article>
          <article className="card">
            <p className="eyebrow">테스트</p>
            <div className="metric">{meta.tests}</div>
            <p className="helper">
              라운드마다 늘어납니다 — 정본은 <code>{pipelineData.source}</code> 입니다
            </p>
          </article>
        </section>

        <div className="grid spacious">
          {pipelinePhases.map((phase) => {
            const row = byOrder.get(phase.order);
            return (
            <article
              className={`panel phase-card ${row?.status.includes("완료") ? "" : "is-paused"}`}
              key={phase.slug}
            >
              <div className="phase-head">
                <span className="phase-order">{phase.order}</span>
                <span className="badge">Phase {phase.phase}</span>
                {/* 🔴 세 칩은 전부 정본 파싱값이다. TS 상수로 되돌리지 않는다 */}
                <span className="chip">{row?.status ?? "—"}</span>
                <span className="chip">게이트 {row?.gate ?? "—"}</span>
                <span className="chip">{row?.credit ?? "—"}</span>
              </div>
              <h2>{phase.title}</h2>
              <p className="lead">{phase.shortDescription}</p>
              <ul>
                {phase.detail.map((line) => (
                  <li key={line}>{line}</li>
                ))}
              </ul>
              <p className="phase-outcome">결과 — {phase.outcome}</p>
            </article>
            );
          })}
        </div>
      </div>
    </main>
  );
}
```

- [ ] **Step 8: `site/app/data/page.tsx` 작성**

```tsx
import Link from "next/link";
import data from "../../public/data/site_data.json";
import StatCard from "../../components/StatCard";

const corpus = data.corpus;
const recall = data.recall;

export default function DataPage() {
  const maxMb = Math.max(...corpus.groups.map((group) => group.mb), 1);

  return (
    <main className="section">
      <div className="container">
        <div className="section-heading">
          <span className="badge">데이터 · 산출물</span>
          <h1>무엇을 읽고, 어떻게 색인하고, 어디까지 회수하나</h1>
          <p className="lead">
            코퍼스 프로파일과 인덱스 구성, 근거 회수 지표, 그리고 평가용 API 계약입니다. 화면의 모든
            수치는 <code>artifacts/</code> 실측에서 생성됩니다.
          </p>
        </div>

        <section className="grid grid-4" aria-label="코퍼스 요약">
          <StatCard
            label="코퍼스 크기"
            value={`${(corpus.totalMb / 1024).toFixed(2)}GB`}
            helper={`${corpus.totalMb.toLocaleString("ko-KR")}MB`}
          />
          <StatCard
            label="manifest 문서"
            value={corpus.nDocsManifest.toLocaleString("ko-KR")}
            helper={`파싱 ${corpus.nDocsParsed.toLocaleString("ko-KR")}건`}
          />
          <StatCard
            label="근거 회수 Recall@5"
            value={recall.recall5Rate?.toFixed(3) ?? "-"}
            helper={`골든 ${recall.recall5}/${recall.golden} · 코호트 ${recall.cohort}`}
          />
          <StatCard
            label="근거 칸 적중"
            value={recall.evidenceHitRate?.toFixed(3) ?? "-"}
            helper="게이트 D 문턱 0.85"
          />
        </section>

        <section className="panel spacious">
          <div className="section-heading compact">
            <span className="badge">코퍼스 구성</span>
            <h2>정기공시가 95% 를 차지한다</h2>
            <p className="lead">
              이 편중이 인덱스 설계를 결정했습니다. 하나의 인덱스로 뭉치면 정기공시가 나머지를 덮어버리므로
              문서군마다 인덱스를 따로 뒀습니다.
            </p>
          </div>
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>문서군</th>
                  <th className="num">파일</th>
                  <th className="num">크기</th>
                  <th className="num">비중</th>
                  <th className="num">평균</th>
                  <th>파싱 범위</th>
                </tr>
              </thead>
              <tbody>
                {corpus.groups.map((group) => (
                  <tr key={group.docGroup}>
                    <td>
                      <strong>{group.label}</strong>
                      <span>{group.docGroup}</span>
                    </td>
                    <td className="num">{group.nFiles.toLocaleString("ko-KR")}</td>
                    <td className="num">
                      <span className="rate-cell">
                        <span className="rate-bar">
                          <span style={{ width: `${(group.mb / maxMb) * 100}%` }} />
                        </span>
                        {group.mb.toLocaleString("ko-KR")}MB
                      </span>
                    </td>
                    <td className="num">{group.sharePct}%</td>
                    <td className="num">{group.avgKb}KB</td>
                    <td>
                      <span>{group.sampling}</span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        <section className="panel spacious">
          <div className="section-heading compact">
            <span className="badge">인덱스</span>
            <h2>문서군별 별도 인덱스 + 라우터가 고른다</h2>
            <p className="lead">LLM 호출 0회로 빌드됩니다.</p>
          </div>
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>산출물</th>
                  <th>무엇</th>
                </tr>
              </thead>
              <tbody>
                {data.indexes.map((index) => (
                  <tr key={index.name}>
                    <td>
                      <strong>{index.label}</strong>
                      <span>
                        <code>artifacts/{index.name}</code>
                      </span>
                    </td>
                    <td>{index.note}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        {recall.byDifficulty.length > 0 ? (
          <section className="panel spacious">
            <div className="section-heading compact">
              <span className="badge">난이도별 회수</span>
              <h2>상 난이도가 낮은 이유는 회수 난도보다 골든 다중성이 먼저다</h2>
              <p className="lead">문항당 골든 근거 수가 하·중의 두 배입니다.</p>
            </div>
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th>난이도</th>
                    <th className="num">골든</th>
                    <th className="num">Recall@5</th>
                    <th className="num">비율</th>
                  </tr>
                </thead>
                <tbody>
                  {recall.byDifficulty.map((row) => (
                    <tr key={row.key}>
                      <td>
                        <strong>{row.key}</strong>
                      </td>
                      <td className="num">{row.golden}</td>
                      <td className="num">{row.recall5}</td>
                      <td className="num">{row.rate.toFixed(3)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {recall.gateVerdict ? <p className="helper">게이트 판정: {recall.gateVerdict}</p> : null}
          </section>
        ) : null}

        <section className="panel spacious">
          <div className="section-heading compact">
            <span className="badge">평가용 API</span>
            <h2>응답 스키마는 5개 키 고정이다</h2>
            <p className="lead">{data.apiContract.note}</p>
          </div>
          <p className="phase-outcome">
            <code>{data.apiContract.endpoint}</code>
          </p>
          <div className="contract-keys">
            {data.apiContract.keys.map((key) => (
              <code key={key}>{key}</code>
            ))}
          </div>
          <p className="helper">
            {/* 🔴 /answers 는 MVP 에 없다 (보안 ④⑤). 살아 있는 링크만 건다 */}
            <code>retrieved_context</code> 가 근거, <code>think_trace</code> 가 추론 경로입니다.
            문항 단위 트레이스는 공개하지 않습니다 — 질문 전문과 답변이 곧 평가셋 라벨이기 때문입니다.
          </p>
        </section>

        <section className="panel spacious note-panel">
          <p className="eyebrow">산출물과 재현</p>
          <h2>이 사이트의 데이터는 어디서 오나</h2>
          <p className="lead">{data.artifactsNote}</p>
        </section>
      </div>
    </main>
  );
}
```

- [ ] **Step 9: `site/README.md` 작성**

```markdown
# ARGUS 사이트

ARGUS 공시 QA Agent 의 근거·정정체인·발주처·기여도 사다리를 보여주는 Next.js 사이트다.

## 실행

```bash
npm install
npm run dev      # http://localhost:3000
npm run build
npm run typecheck
```

## 데이터 흐름

**사이트는 계산하지 않는다.** 파싱·집계는 전부 Python 이 하고, 사이트는 결과 JSON 을 렌더링만 한다.

```
artifacts/  +  evalset/
      │
      ▼  scripts/build_site_data.py   (LLM 호출 0회)
site/public/data/site_*.json          (커밋함)
      │
      ▼  정적 import
site/app/**/page.tsx                  (서버 컴포넌트)
```

데이터를 다시 만들려면 저장소 루트에서:

```bash
python scripts/build_site_data.py              # 전부
python scripts/build_site_data.py --only ablation
```

`artifacts/` 는 gitignore 대상이지만 생성된 `site/public/data/*.json` 은 커밋하므로,
산출물이 없는 클론에서도 사이트는 빌드된다.

## 🔴 공개하지 않는 것 다섯

| # | 무엇 | 왜 |
|---|---|---|
| ① | `.env` · `CLOVA_API_KEY` | 빌드는 `argus` 패키지를 import 하지 않는다 — 키가 메모리에 들어오지 않는다 |
| ② | 코퍼스 원문 | 주최측 제공물이다. 근거는 산출물의 `retrieved_context` 발췌만 쓴다 |
| ③ | `evalset/heldout.jsonl` · heldout 산출물 | 최종 구성 1회용이고 두 번 다 썼다. **집계 수치조차 싣지 않는다** — 낙폭 게이트는 은퇴했다 |
| ④ | `evalset/*.jsonl` 의 **질문 전문·정답** | 우리가 만든 라벨이고 채점 결함이 문서화돼 있다. 검색 튜닝이 가능한 형태다 |
| ⑤ | `golden` · `golden_rank` · `answer` · `think_trace` (문항 단위) | 골든 라벨이고, 답변은 질문을 되풀어 쓴다 |

`FORBIDDEN_PATHS` 가 경로 조각으로 ③②를 막고, `ROW_FIELDS_ALLOWED` 화이트리스트가 ⑤를 막는다.
`tests/test_site_data.py` 가 생성된 JSON 전체를 훑어 heldout 문자열·질문 전문·`golden_rank`·
API 키 형태의 문자열이 없는지 검사한다.

## 페이지

🔴 **`R6-MVP` 는 아래 다섯 쪽만 만든다.** 나머지는 제출·평가 후(09-21 이후) 또는 폐기다.

| 경로 | MVP | 내용 |
|---|---|---|
| `/` | ✅ | 헤드라인 지표 · 공시 Agent 특성 4개 · 세대 라벨 |
| `/ablation` | ✅ | 사다리 A0→A8 · LOO · 축별 분해 · 비용 원장(정본 파싱) · 기각 목록 링크 |
| `/errors` | ✅ | 실패 해부 — 6단계 귀속 · 근본 원인 · **분모 세 개** · 데이터 상한 |
| `/pipeline` | ✅ | Phase A~I 방법론 + 게이트·크레딧(정본 파싱) |
| `/data` | ✅ | 코퍼스 프로파일 · 인덱스 · 회수 지표 · API 계약 · 출처 |
| `/corrections` | ⏸ | 정정 체인 — 문서군별 연결률 · 처리 원칙 |
| `/counterparty` | ⏸ | 발주처 밸류체인 — 엣지 정의 기각 · 허브 · 기업쌍 |
| `/search` | ⏸ | 평가셋 문항 **메타** 탐색 (질문 전문 없음) |
| `/answers` | ❌ | 질의응답 트레이스 — 🔴 보안 ④⑤ 와 충돌. 되살리는 조건 셋이 Task 5 에 있다 |

## 스타일

순수 CSS 다. Tailwind·차트 라이브러리·지도 라이브러리를 쓰지 않는다. 토큰은
`app/globals.css` 의 `:root` 에 있고, 막대·히트맵은 `div` + CSS 로 그린다.
라이트 전용이다.
```

- [ ] **Step 10: 저장소 루트 `README.md` 에 `site/` 항목 추가**

`## 구조` 표의 `deploy/` 행 **다음**에 두 행을 넣는다.

```markdown
| `site/` | 결과 공개용 Next.js 사이트 (`site/README.md`) |
| `scripts/build_site_data.py` | `artifacts/` → `site/public/data/*.json` 생성. LLM 0회 |
```

- [ ] **Step 11: 전체 데이터 재생성 + 전체 테스트**

Run:
```bash
python scripts/build_site_data.py
python -m pytest tests/ -q
```
Expected: `site_*.json` 이 `BUILDERS` 의 키 수만큼 생성. 🔴 **기존 테스트 건수를 적지 않는다** —
정본은 `docs/total_plan.md` §1 이고 라운드마다 늘어난다. 판정은 **`failed` 0건**으로 한다.
유출 게이트가 생성된 파일 **전부**를 훑는다(`OUT_DIR.glob("site_*.json")` — 파일 수를 못박지 않는다).

- [ ] **Step 12: 사이트 전체 빌드 + 라우트 확인**

Run:
```bash
cd site && npm run typecheck && npm run build
```
Expected: 오류 0건. 라우트 목록에 **만든 페이지가 전부** 뜬다. 🔴 `/answers` 는 없다(보안 ④⑤).

- [ ] **Step 13: 전 페이지 육안 회귀 + 좁은 화면 확인**

Run: `cd site && npm run dev`
Expected:
- 헤더 드롭다운의 항목이 모두 살아 있는 페이지로 가고, 현재 페이지가 활성 표시된다. **죽은 링크 0건.**
- 만든 페이지 모두 콘솔 오류 0건.
- 브라우저 폭을 375px 로 좁혔을 때 **가로 스크롤이 생기지 않는다.** 표는 각자의 `.table-wrap` 안에서만 가로 스크롤된다.
- 🔴 **모든 페이지 하단에 세대 라벨(`generation.arm` · `generated` · `argusCommit`)이 있다.**
- 🔴 **화면 어디에도 질문 전문·정답·공인 IP·API 키 형태 문자열이 없다.** 브라우저에서
  `Ctrl+F` 로 `223.130` 을 찾아 0건인지 확인한다.

확인 후 종료.

- [ ] **Step 14: 커밋**

```bash
git add scripts/build_site_data.py tests/test_site_data.py README.md site/README.md site/lib site/app site/public/data
git commit -m "feat: 파이프라인·데이터 화면 + 사이트 README"
```

---

## Self-Review 결과

계획을 다 쓰고 나서 요구사항과 대조한 기록이다.

**1. 커버리지**

| 요구 | 어느 Task |
|---|---|
| 레퍼런스 구조를 참조 (App Router · `components/` · `lib/` · `public/data/site_*.json` · 순수 CSS 토큰 · 드롭다운 헤더 · 히어로+검색) | Task 1 · 4 · 6 · 10 |
| 레퍼런스 `ScoreCard` → `StatCard` | Task 1 |
| 레퍼런스 `lib/methodology.ts` → `lib/pipeline.ts` | Task 10 |
| 레퍼런스 `/data`(출처·provenance) → `/data` | Task 10 |
| 레퍼런스 `LandingSearch` → `/search` 연결 | Task 4 · 6 |
| 공시 agent 특성 — 근거·접수번호·DART 링크 | Task 3 · 5 |
| 공시 agent 특성 — 정정 체인 | Task 9 |
| 공시 agent 특성 — 발주처 2-hop | Task 9 |
| 공시 agent 특성 — 도구 계산 · 검증기 LLM 0회 | Task 4 (특성 카드) · 10 (Phase G) |
| 공시 agent 특성 — 거절·환각 0 | Task 4 · 6 · 8 |
| 측정 문화 (사다리 · LOO · 분모 · 비용 원장) | Task 7 · 8 |
| heldout 유출 방지 | Task 2 (게이트) · 전 Task (재확인 스텝) |

**2. 플레이스홀더 스캔** — `TBD`·`적절히 처리`·`Task N 과 유사` 없음. 모든 코드 스텝에 실제 코드가 있고, 필드명은 실측 확인한 것이다 (`summary.json` `arms/axes/cells` · `A8/report.json` 44필드 · `relation_profile.correction_chains` · `counterparty_graph.stats`).

**3. 타입 일관성**

- `bsd.metadata` / `read_json` / `read_jsonl` / `write_site_json` — Task 2 정의, 이후 전부 같은 이름.
- `parse_evidence` 반환 `{title, rceptNo, dartUrl, lines}` — Task 3 정의, Task 5 `Evidence` 타입과 일치.
- `parse_think_trace` 반환 `{kind, raw, fields}` — Task 3 정의, Task 5 `Step` 타입과 일치.
- `DOC_GROUP_LABELS` — Task 9 에서 정의하고 Task 10 `build_data` 가 쓴다. **Task 9 가 Task 10 보다 먼저 실행돼야 한다** (순서 의존).
- `StatCard` props `{label, value, helper?}` — Task 1 정의, 전 페이지 동일.
- `LadderRow` / `HeatRow` — Task 7 에서 export, Task 7 페이지에서만 사용.

**4. 실행 순서 제약**

Task 1 → 2 → 3 → 4 → (5 · 6 · 7 · 8) → 9 → 10.
5~8 은 서로 독립이므로 순서를 바꿀 수 있다. **9 는 10 보다 앞**이어야 한다 (`DOC_GROUP_LABELS`).

**5. 계획을 쓰면서 확인한 위험 두 개**

- 🔴 `artifacts/` 가 gitignore 라서, **산출물이 없는 머신에서는 `scripts/build_site_data.py` 가 `FileNotFoundError` 로 죽는다.** 사이트 자체는 커밋된 JSON 으로 빌드되므로 문제없지만, 데이터 재생성은 산출물이 있는 머신에서만 된다. Task 10 의 `site/README.md` 에 적었다.
- ~~🔴 `test_build_ablation_ledger_sums_to_total` 과 `test_build_errors_grid_failure_count_is_eleven` 은 **문서의 수치를 코드에 못박는 테스트**다. 다음 재측정에서 값이 바뀌면 이 테스트가 먼저 깨진다 — 그것이 의도다.~~
  🔴 **2026-08-14 — 그 설계가 틀렸다.** 값을 못박은 테스트는 "사이트가 낡았다" 를 잡지 못한다.
  실제로 잡는 것은 **"테스트가 낡았다"** 이고, 값을 고치는 사람은 문서가 아니라 테스트를 고친다.
  두 테스트는 **자기일관성·미러 검사**로 교체했다(`..._mirrors_the_ssot` ·
  `..._are_self_consistent`). 값을 모르고도 어긋남을 잡는다.

---

## 🔴 재검토 기록 (2026-08-14 · v4 `R6` 편입 검토)

**세 세대 뒤의 계획서를 현 상태 기준으로 검토한 결과다.** 무엇을 어떻게 고쳤는지 남긴다 —
같은 결함이 다시 나면 이 절이 먼저 읽혀야 한다.

**① 낡은 사본을 정본 참조로 바꾼 자리 (값 삭제 → 파싱/링크)**

| 자리 | 종전 | 지금 |
|---|---|---|
| `build_meta.spentWon` | `12846.7` 리터럴 | `parse_ssot()` 가 `total_plan §1` 표를 파싱 · 검산까지 대조 |
| `build_meta.tests` · `gates` | `728` · `68/71` | 같은 파서 |
| `COST_LEDGER` 8행 | Python 상수 표 | 삭제. 파서의 `spentLedger` |
| `build_meta.heldout` | `15/18` 상수 | **삭제** (보안 ③ · 게이트 은퇴) |
| `toolCount` | `15` 리터럴 | `count_tool_specs()` — `ast` 로 `tools.py` 를 세고 **import 하지 않는다** |
| `FINDINGS` 세 문장 | 토큰·Δ·원 5개 리터럴 | `{자리표시자}` + 산출물 주입 |
| `DENOMINATORS` 3값·구성 | `13`·`11`·`12` 상수 | `report.json` 에서 센다 |
| `DATA_LIMITS` 규모 | `66건`·`105건`… | 항목·사유만. 규모는 §4 링크 |
| `pipelinePhases` 9행 | `gate`·`creditWon` TS 상수 | `parse_ssot()["phases"]` 와 `order` 로 조인 |
| `/answers`·`/search`·홈 예시 | 질문 전문 | **삭제** (보안 ④) |
| 원천 경로 `v2` 고정 | `artifacts/ablation/v2/…` | `resolve_generation()` · `resolve_ladder_generation()` |
| 게이트/성적 원천 | `error_analysis_v2.json` | `ablation/{gen}/A8/report.json` (`v2` 파일은 옛 라벨) |

**② 서술 정정** — `GraphRAG 기각` → **기업—기업 엣지 정의 기각**(재정의한 쪽은 채택, `A1→A1b`
사다리 최대 전이) · `A5 부터 Δ 0` → **A4 부터** · `A3 보류` → **최종 기각** · `제출물 3종` →
**4종**(전처리 산출물 · `Dockerfile` 필수 아님) · `106문항` → 분모는 산출물에서 센다.

**③ 이 라운드가 새로 막은 것** — 평가셋 **질문 전문·정답**의 발행(보안 ④),
`report.json > rows` 44필드의 **화이트리스트 강제**(보안 ⑤), `failure_why` 자유 서술의
**금액 마스킹**, `.env` 를 **import 경로에서 차단**, 공인 IP **비발행**,
호스팅을 평가 서버 밖으로.

**④ 남은 위험 셋**

1. 🔴 **`parse_ssot()` 는 마크다운 서식에 의존한다.** `total_plan §1` 의 표 열 수가 바뀌면
   `ValueError` 로 빌드가 깨진다. **그것이 설계다**(조용히 틀린 값을 내는 것보다 낫다).
2. 🔴 **사다리 세대와 현 세대가 다르다.** 화면이 라벨을 잃으면 그 자체가 세대 혼합이다.
   `test_build_ablation_labels_both_generations` 가 지키지만 **TSX 렌더는 테스트가 없다** —
   Step 13 육안 확인이 유일한 게이트다.
3. 🔴 **`R6-MVP` 는 Next.js 를 쓰지 않는다**(아래 §R6). Task 1~10 과 **툴체인이 다르다** —
   나중에 Next.js 판을 살릴 때 `build_site_data.py` 는 재사용되지만 렌더 층은 다시 쓴다.

---

# §R6. 🔴 MVP — **정적 HTML 5쪽 · Node 0 · 크레딧 0원 · LLM 0회**

> **For agentic workers:** 이 절만 실행한다. 위의 Task 1~10 은 **제출 후 이월분**이다.
> v4 계획서 `§11`·`§12` 와 같은 태스크 양식이고, `R6` 은 **`R5` 뒤**에 온다
> (제안서가 먼저다 — 웹사이트는 제출물이 아니다).

## 왜 Next.js 를 버리는가 — **판정**

| 조건 (MVP 기준) | Next.js 판 | 정적 HTML 판 |
|---|---|---|
| 제출을 위험에 빠뜨리지 않는다 | 🔴 미완 108 · npm 툴체인 신설 · 코드 동결 08-25 까지 11일 | ✅ 스크립트 1개 + 테스트 1개 |
| 평가 서버와 자원을 다투지 않는다 | 🟡 `node` 런타임이 필요해 같은 서버 유혹이 생긴다 | ✅ **파일이라 서버가 없다** |
| 빌드 타임에 `artifacts/` 를 읽어 정적 산출물 | ✅ (`output: export`) | ✅ |
| 우리 토큰 | 🔴 TSX 12파일 · CSS 900행 · 리뷰 | ✅ 그 1/5 |

🔴 **디자인은 버리지 않는다.** `scratch/ARGUS_example` 의 **토큰·시맨틱 클래스명·카드/표/막대
구조**를 CSS 한 파일로 옮긴다. 버리는 것은 **React·TypeScript·npm** 이다.
🔴 **그리고 `Task 2` 의 `build_site_data.py` 규격을 그대로 쓴다** — 정본 파서(`parse_ssot`)·
세대 해석(`resolve_generation`)·유출 게이트(`FORBIDDEN_PATHS`·`ROW_FIELDS_ALLOWED`)가
전부 재사용된다. 나중에 Next.js 판을 살리면 **데이터 층은 손대지 않는다.**

**산출물**

```
scripts/build_site.py          artifacts/ + docs/total_plan.md → site/*.html + site/data/*.json
tests/test_build_site.py       스키마 · 정본 미러 · 🔴 유출 게이트 5종
site/index.html                홈 — 헤드라인 지표 · 능력 4개 · 세대 라벨
site/ablation.html             사다리 · LOO · 축별 히트맵 · 지출 원장 · 기각 목록 링크
site/errors.html               6단계 귀속 · 근본 원인 · 문서군 · 🔴 분모 셋 · 데이터 상한
site/pipeline.html             Phase A~I · 게이트·크레딧(정본 파싱) · 뒤집힌 이력
site/data.html                 코퍼스 프로파일 · 인덱스 · 회수 지표 · API 계약 · 출처
site/style.css                 디자인 토큰 + 전체 스타일 (단일 파일)
site/data/site_*.json          provenance 용 생성 JSON (커밋함)
site/README.md                 실행 · 데이터 흐름 · 공개하지 않는 것 다섯
```

🔴 **호스팅**: 저장소의 정적 파일 또는 GitHub Pages. **`223.130.129.131` 에 올리지 않는다.**

---

## Task R6-1: 빌드 골격 — 정본 파서 · 세대 해석 · 유출 게이트

**Files:**
- Create: `scripts/build_site.py` · `tests/test_build_site.py`
- Read: `docs/total_plan.md` · `artifacts/ablation/*/summary.json`

- [ ] **Step 1: 실패하는 테스트를 먼저 쓴다 (4분)**

`tests/test_build_site.py` 에 §Task 2 의 테스트를 옮긴다 —
`test_out_dir_is_inside_site` · `test_metadata_has_required_keys` ·
`test_read_json_refuses_heldout_paths` · `test_write_site_json_requires_metadata` ·
`test_build_site_data_has_no_hardcoded_currency`.

```bash
PYTHONIOENCODING=utf-8 C:/Users/Juhwankim/miniconda3/python.exe -m pytest tests/test_build_site.py -q
```
Expected: `ModuleNotFoundError: build_site` 또는 `AttributeError` — **아직 스크립트가 없다.**

- [ ] **Step 2: `scripts/build_site.py` 의 헬퍼를 쓴다 (5분)**

§Task 2 의 블록을 그대로 옮긴다: `REPO` · `OUT_DIR = REPO/"site"/"data"` ·
`FORBIDDEN_PATHS = ("heldout", "data/")` · `GENERATIONS` · `resolve_generation` ·
`resolve_ladder_generation` · `argus_commit` · `metadata` · `_guard` · `read_json` ·
`read_jsonl` · `write_site_json` · `count_tool_specs` · `parse_ssot` ·
`ROW_FIELDS_ALLOWED` · `_mask_amounts`.

🔴 **`argus` 패키지를 import 하지 않는다** (보안 ① — `argus.config.settings` 가 `.env` 를 읽는다).

- [ ] **Step 3: 정본 파서를 실측한다 (2분)**

```bash
PYTHONIOENCODING=utf-8 C:/Users/Juhwankim/miniconda3/python.exe -c "import sys;sys.path.insert(0,'scripts');import build_site as b;d=b.parse_ssot();print(d['spentWon'],d['tests'],d['gates'],len(d['spentLedger']),len(d['phases']),b.count_tool_specs(),b.resolve_generation(),b.resolve_ladder_generation())"
```
Expected: 누적 지출 · 테스트 수 · 게이트 · 원장 12행 · Phase 9행 · 도구 종수 · 두 세대 이름이
한 줄로 나온다. 🔴 **그 값을 계획서·커밋 메시지에 적지 않는다.** 정본은 `docs/total_plan.md` §1 이다.
검산이 어긋나면 `ValueError` 가 나고, **그때 고칠 것은 스크립트가 아니라 문서다.**

- [ ] **Step 4: 테스트 통과 (2분)**

```bash
PYTHONIOENCODING=utf-8 C:/Users/Juhwankim/miniconda3/python.exe -m pytest tests/test_build_site.py -q
```
Expected: 전건 PASS. 생성물이 없어 유출 게이트는 `skip`.

- [ ] **Step 5: 커밋 (2분)**

```bash
git commit --only scripts/build_site.py tests/test_build_site.py -m "feat(site): 빌드 골격 — 정본 파서·세대 해석·유출 게이트"
```

## Task R6-2: 🔴 유출 게이트를 먼저 켠다 — **다섯 검사**

🔴 **화면보다 게이트가 먼저다.** 한 번 발행하면 되돌릴 수 없다.

- [ ] **Step 1: 다섯 검사를 쓴다 (5분)**

```python
def _generated() -> list[Path]:
    return sorted(bsd.OUT_DIR.glob("site_*.json")) + sorted((ROOT / "site").glob("*.html"))

def test_gate_no_heldout_text():          # ③ heldout 질문 전문·문항 ID
def test_gate_no_evalset_question_text(): # ④ questions/regression/valuechain 전문 + 앞 20자
def test_gate_no_golden_fields():         # ⑤ golden · golden_rank · answer · think_trace 키
def test_gate_no_api_key_shape():         # ① nv- · sk- · Bearer · 40자 이상 hex/base64
def test_gate_no_public_ip():             # 공인 IP (deploy/README.md 에서 읽어 대조)
```

🔴 `test_gate_no_public_ip` 는 IP 를 테스트에 적지 않는다 — `deploy/README.md` 에서
`\d+\.\d+\.\d+\.\d+` 를 뽑아 생성물에 없는지 본다. **정본에서 읽어 대조한다.**

- [ ] **Step 2: 게이트가 실제로 발화하는지 본다 (3분)**

일부러 위반 페이로드를 써 넣고 테스트가 **실패**하는지 확인한다.

```bash
PYTHONIOENCODING=utf-8 C:/Users/Juhwankim/miniconda3/python.exe -c "import sys;sys.path.insert(0,'scripts');import build_site as b;q=b.read_jsonl('evalset/questions.jsonl')[0]['question'];b.write_site_json('site_leak_probe.json',{'metadata':b.metadata('probe',[]),'q':q})"
PYTHONIOENCODING=utf-8 C:/Users/Juhwankim/miniconda3/python.exe -m pytest tests/test_build_site.py -q -k gate
```
Expected: 🔴 **FAIL** — `질문 전문이 실렸다`. 게이트가 살아 있다는 증거다.
🔴 **발화를 확인하지 않은 게이트는 게이트가 아니다** — 이 저장소가 거짓 경보/무발화를 둘 다 겪었다.

- [ ] **Step 3: 프로브를 지우고 다시 통과시킨다 (1분)**

```bash
rm site/data/site_leak_probe.json
PYTHONIOENCODING=utf-8 C:/Users/Juhwankim/miniconda3/python.exe -m pytest tests/test_build_site.py -q -k gate
```
Expected: PASS 또는 `skip`(생성물 없음).

- [ ] **Step 4: 커밋 (2분)**

```bash
git commit --only tests/test_build_site.py -m "test(site): 유출 게이트 다섯 — 발화 확인까지"
```

## Task R6-3: 데이터 빌더 — `meta` · `ablation` · `errors` · `pipeline` · `data`

- [ ] **Step 1: `build_meta` 를 옮긴다 (4분)** — §Task 2 의 최종본. 🔴 `heldout` 키 없음 · `toolCount` 는 `ast` 로 센 값.
- [ ] **Step 2: `build_ablation` 을 옮긴다 (5분)** — 🔴 두 세대 라벨 · `parse_ssot` 원장 · `notAdopted` · `_fill_findings`.
- [ ] **Step 3: `build_errors` 를 옮긴다 (5분)** — 🔴 분모 셋을 **센다** · `_mask_amounts` · 화이트리스트.
- [ ] **Step 4: `build_pipeline` · `build_data` 를 옮긴다 (5분)** — Phase 표 파싱 · `recall_v2` 코호트 라벨 · `API_CONTRACT`(🔴 IP 없음).
- [ ] **Step 5: 전부 생성하고 게이트를 켠다 (3분)**

```bash
PYTHONIOENCODING=utf-8 C:/Users/Juhwankim/miniconda3/python.exe scripts/build_site.py
PYTHONIOENCODING=utf-8 C:/Users/Juhwankim/miniconda3/python.exe -m pytest tests/test_build_site.py -q
```
Expected: `site/data/site_{meta,ablation,errors,pipeline,data}.json` 5개 생성 · 전건 PASS
(유출 게이트가 이제 `skip` 이 아니다).

- [ ] **Step 6: 🔴 생성물을 사람이 한 번 읽는다 (4분)**

```bash
PYTHONIOENCODING=utf-8 C:/Users/Juhwankim/miniconda3/python.exe -c "import json,glob;[print(p,json.load(open(p,encoding='utf-8'))['metadata']['note']) for p in sorted(glob.glob('site/data/site_*.json'))]"
```
Expected: 5줄. 각 `note` 가 "무엇인지" 를 말하고 `sourceFiles` 에 `docs/total_plan.md` 가 들어 있다.

```bash
grep -c golden site/data/*.json
```
Expected: 전부 0.

- [ ] **Step 7: 커밋 (2분)**

```bash
git commit --only scripts/build_site.py tests/test_build_site.py -m "feat(site): 데이터 빌더 다섯 — 성적은 산출물, 지출·게이트는 정본 파싱"
git add -f site/data
git commit --only site/data -m "feat(site): 생성 JSON 커밋 — 산출물 없는 클론에서도 빌드된다"
```

## Task R6-4: 렌더러 — 정적 HTML 다섯 쪽

**Files:** Modify: `scripts/build_site.py` · Create: `site/style.css`

- [ ] **Step 1: `site/style.css` 를 쓴다 (5분)**

§Task 1 의 `globals.css` 에서 **토큰과 시맨틱 클래스만** 옮긴다:
`:root` 커스텀 프로퍼티 · `.container` `.section` `.card` `.badge` `.chip` `.lead`
`.section-heading` `.table` `.table-wrap` `.panel` `.grid` `.grid-3` `.grid-4`
`.eyebrow` `.helper` `.metric` `.rate-bar` `.heat`.
🔴 라이트 전용 · Tailwind 없음 · 차트 라이브러리 없음(막대는 `div` 폭 %).

- [ ] **Step 2: HTML 헬퍼를 쓴다 (5분)**

```python
def esc(v) -> str: ...                      # html.escape. 🔴 모든 값이 이걸 통과한다
def page(title, body, gen, commit) -> str:  # <html lang="ko"> + style.css + 내비 5쪽
                                            # 🔴 푸터에 세대 라벨 + argusCommit
def card(label, value, helper) -> str: ...
def table(headers, rows) -> str: ...        # .table-wrap 로 감싼다
def bar(pct, negative) -> str: ...          # CSS 막대
```

🔴 **템플릿에 수치를 넣지 않는다.** 모든 값은 `site/data/site_*.json` 인자로 들어온다.

- [ ] **Step 3: 다섯 쪽을 쓴다 (5분 × 5)**

각 쪽은 `render_<name>(payload) -> str` 하나다. 담는 것:

| 쪽 | 반드시 있어야 하는 것 |
|---|---|
| `index` | 지표 카드 4장(그리드·정보한계·회귀·골든 근거) · 능력 4개 · 축 커버리지 · 🔴 heldout 카드 없음 |
| `ablation` | 사다리 막대(A1 빨강) · LOO 표 · 히트맵 3개 · 원장 표 + `ledgerSource` · `notAdopted`("성적이 아니다" 라벨) · 🔴 **두 세대 라벨** |
| `errors` | 6단계 히스토그램 · 근본 원인 · 문서군 · 🔴 **분모 셋을 나란히, 각자 라벨과 함께** · 데이터 상한(§4 링크) · 자동 배분의 한계 문장 |
| `pipeline` | Phase 9장 카드(게이트·크레딧은 파싱값) · 게이트 총계 · 테스트 수 + "정본은 §1" 문장 |
| `data` | 코퍼스 표 · 인덱스 · Recall@5(🔴 코호트 라벨 필수) · API 계약 · `artifactsNote` |

- [ ] **Step 4: 렌더하고 눈으로 본다 (4분)**

```bash
PYTHONIOENCODING=utf-8 C:/Users/Juhwankim/miniconda3/python.exe scripts/build_site.py --html
PYTHONIOENCODING=utf-8 C:/Users/Juhwankim/miniconda3/python.exe -c "import pathlib;[print(p.name,p.stat().st_size) for p in sorted(pathlib.Path('site').glob('*.html'))]"
```
Expected: 5개 HTML. 브라우저로 `site/index.html` 을 열어 ① 내비 5개가 전부 살아 있는지
(죽은 링크 0건) ② 375px 폭에서 가로 스크롤이 없는지 ③ 푸터에 세대 라벨이 있는지 본다.

- [ ] **Step 5: 🔴 HTML 에도 유출 게이트를 돌린다 (3분)**

게이트 다섯이 `site/*.html` 까지 훑도록 확장한다 — JSON 만 검사하면 **템플릿에 박은 문자열을 놓친다.**

```bash
PYTHONIOENCODING=utf-8 C:/Users/Juhwankim/miniconda3/python.exe -m pytest tests/test_build_site.py -q -k gate
grep -c 223.130 site/*.html site/data/*.json
```
Expected: 전건 PASS · IP 카운트 전부 0.

- [ ] **Step 6: 커밋 (2분)**

```bash
git commit --only scripts/build_site.py site/style.css -m "feat(site): 정적 HTML 렌더러 5쪽"
git add -f site/index.html site/ablation.html site/errors.html site/pipeline.html site/data.html
git commit --only site -m "feat(site): 발행본 5쪽"
```

## Task R6-5: 마감 게이트 — 회귀 · 재현 · 정본 역참조

- [ ] **Step 1: 전량 테스트 (3분)**

```bash
PYTHONIOENCODING=utf-8 C:/Users/Juhwankim/miniconda3/python.exe -m pytest tests/ -q
```
Expected: **`failed` 0건.** 🔴 건수를 여기 적지 않는다 — 정본은 `docs/total_plan.md` §1 이고
그 수는 이 태스크가 늘린다. **늘어난 수를 §1 에 반영하고 증가 이력 꼬리도 함께 잇는다**
(헤드라인만 고치고 꼬리를 두면 그것이 열 번째 결함의 재발이다).

- [ ] **Step 2: 🔴 재현성 — 두 번 돌려 같은 바이트가 나오는지 (3분)**

```bash
PYTHONIOENCODING=utf-8 C:/Users/Juhwankim/miniconda3/python.exe scripts/build_site.py --html
git status --porcelain site/
```
Expected: `metadata.generated` 를 뺀 나머지가 동일하다. 🔴 **정렬이 불안정한 곳이 있으면
`sorted()` 를 넣는다** — 매 빌드마다 diff 가 나면 사본이 있는 것과 같은 병이 된다.

- [ ] **Step 3: `site/README.md` 를 쓴다 (4분)** — §Task 10 Step 9 의 최종본(공개하지 않는 것 다섯 · 페이지 표 · 데이터 흐름). 🔴 `npm` 절을 빼고 Python 명령으로 바꾼다.

- [ ] **Step 4: 저장소 루트 `README.md` 에 두 행 추가 (2분)**

```markdown
| `site/` | 결과 공개용 정적 사이트 (`site/README.md`) — 🔴 평가 서버에 올리지 않는다 |
| `scripts/build_site.py` | `artifacts/` + `docs/total_plan.md` → `site/*.html`. LLM 0회 |
```

- [ ] **Step 5: 🔴 `docs/total_plan.md` §8 참조표에 한 줄 (2분)**

```markdown
| `site/` | 결과 공개용 정적 사이트. 🔴 성적·지출을 **이 문서에서 파싱**한다 — 사본이 아니다 |
```
🔴 **이 줄이 있어야 §1 을 고치는 사람이 "사이트가 나를 읽는다" 는 것을 안다.**
정본→사본 방향만 적어 두면 다음 사람이 사본의 존재를 모른 채 문서를 고친다.

- [ ] **Step 6: 커밋 (2분)**

```bash
git commit --only site/README.md README.md docs/total_plan.md -m "docs(site): 사이트 문서와 정본 역참조"
```

## 🔴 이 라운드에서 하지 않는 것

| 항목 | 이유 |
|---|---|
| `/answers` 트레이스 | 보안 ④⑤. 되살리는 조건 셋이 Task 5 머리에 있다 |
| `/search` 클라이언트 필터 | 정적 HTML 범위 밖. `errors` 의 문항 ID 표가 같은 정보를 낸다 |
| `/corrections`·`/counterparty` | 🟡 만들 수는 있다. **여유가 있으면 `R6-6` 으로 붙인다**(각 40분) |
| Next.js · React · TypeScript · npm | 위 판정표 |
| 평가 서버 배포 | 🔴 09-07~09-20 에 2 vCPU 를 나누지 않는다 |
| 새 측정 | 🔴 크레딧 0원 · LLM 0회. 사이트는 **이미 있는 산출물만** 읽는다 |
| 새 heldout 수치 | `R4-1` 이 만들면 **빌드가 저절로 읽는다** — 사이트에 값을 넣을 일이 없다 |

**예상**: `R6` 전체 **6~9시간** (골격 1h + 게이트 1h + 빌더 2h + 렌더 2~3h + 마감 1~2h) ·
**크레딧 0원** · **LLM 0회** · 우리 토큰 ~25%.

**선행 조건**: `R5`(제안서 최종) 완료 · `R4-0` 코드 동결 이후 —
🔴 **사이트는 `src/` 를 읽기만 하므로 동결을 깨지 않는다**(`ast` 로 소스를 세는 것도 읽기다).
🔴 **`R6` 이 밀리면 버린다.** 제출물 4종이 먼저다.
