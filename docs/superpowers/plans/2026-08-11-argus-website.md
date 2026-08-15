# ARGUS 서비스 사이트 **v2** — 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development.
> 단계는 체크박스(`- [ ]`)로 추적한다.

**Goal:** DART 공시를 **관계로 읽어 주는 서비스**를 만든다 — 기업 브리핑 · 정정 추적 ·
거래관계 탐색. **LLM 0회 · 크레딧 0원.**

**Architecture:** Next.js(정적 export). `artifacts/*.jsonl` 을 빌드 타임에 읽어
`site/public/data/*.json` 을 만들고, 페이지가 그것만 읽는다. 서버·DB·런타임 API 없음.

**Tech Stack:** Next.js 16 · TypeScript · SVG(그래프 라이브러리 없음) · Python 빌드 스크립트

**Spec:** [`docs/superpowers/specs/2026-08-15-argus-service-design.md`](../specs/2026-08-15-argus-service-design.md)
— **반드시 먼저 읽는다.** 특히 `§0`(첫 판이 왜 쓰레기였나)과 `§9`(성공 기준).

---

# §0. 🔴 v1 은 죽었다 — 무엇을 지우고 무엇을 살리나

**v1**(2026-08-15 · 커밋 `508bd12`)은 정적 5쪽으로 **ablation arm 표 · 오류 분석 476KB ·
기여도 곡선 · 테스트 수**를 발행했다. 사용자 평가:

> **"진짜 아주 쓰레기 같은 사이트를 만들었네. 누가 너가 실험한 게 궁금하대?"**

```
🔴 지운다   site/ 전체 · scripts/build_site.py · tests/test_site.py (59건)
🟢 살린다   보안 게이트 여섯의 설계 · 게이트 검증 테스트 방식 · 정본 참조 규율
🔴 남긴다   docs/ 의 연구 문서 — 제안서·정본의 근거다. 웹사이트와 무관하다
```

**`total_plan §8` 의 역참조 경고도 고쳐야 한다** — `parse_ssot()` 이 사라지므로.

---

## Global Constraints

**모든 Task 의 요구사항에 이 절이 암묵적으로 포함된다.**

- 🔴 **`evalset/` 을 읽지 마세요.** 골든셋·heldout 은 서비스와 무관합니다.
  **서비스 데이터는 공시 원본(`artifacts/*_records.jsonl` 등)에서만 옵니다.**
- 🔴 **`artifacts/ablation/` 을 읽지 마세요.** 실험 산출물입니다.
- 🔴 **`src/argus` 를 import 하지 마세요.** `config.py` 가 `.env` 경로를 압니다.
  빌드 스크립트는 `json`·`csv` 표준 라이브러리로만 파일을 읽습니다.
- 🔴 **`.env` 를 읽거나 출력하지 마세요.** `settings()` 를 통째로 print 하지 마세요.
- 🔴 **공인 IP(`223.130.129.131`)를 사이트에 싣지 마세요.**
- 🔴 **코퍼스 원문을 재배포하지 마세요.** 정규화된 공시 필드만 씁니다.
- 🔴 **화면 어디에도 쓰지 않는 말**: `arm` · `A20` · `A8` · `L6` · `ablation` · 정답률 ·
  기여도 곡선 · 테스트 수 · `judge` · 근거 회수율 · 게이트 · 크레딧 · 코호트.
- 🔴 **`site/node_modules` 를 절대 커밋하지 마세요.** 제출물 1번(소스코드)에 섞이면 안 됩니다.
- 🔴 **`git add -A` / `git add .` 금지.** `git commit --only <경로>` 만.
- python 실행기는 `C:/Users/Juhwankim/miniconda3/python.exe`. 한글 출력 시 `PYTHONIOENCODING=utf-8`.
- 🔴 **매 화면을 만들기 전에 *"이걸 보러 온 사람이 무슨 일을 하려는가"* 를 한 줄로 적으세요.**
  적을 수 없으면 그 화면은 필요 없습니다.

---

# File Structure

```
scripts/build_service_data.py      🔴 artifacts/ → site/public/data/   (Python · LLM 0회)
tests/test_service_data.py         빌드 산출물의 계약과 보안 게이트

site/
  package.json · tsconfig.json · next.config.ts
  app/
    layout.tsx          공통 껍데기 · 헤더 · 푸터
    globals.css         디자인 토큰
    page.tsx            랜딩 — 관계망 + 검색 + 정정 훅
    company/[slug]/page.tsx   기업 브리핑 (generateStaticParams 70개사)
    corrections/page.tsx
    network/page.tsx
    about/page.tsx
  components/
    Header.tsx          네비게이션
    HubGraph.tsx        🔴 SVG 허브 방사형 관계망 (라이브러리 없음)
    CompanySearch.tsx   클라이언트 검색
    ContractTable.tsx   계약 표 + 정정 표시
    CorrectionChain.tsx 원공시 → 정정본 체인
    Limits.tsx          🔴 한계 고지 — 여러 화면이 재사용
  public/data/*.json    빌드 산출물 (gitignore)
```

**책임 경계**: 빌드 스크립트는 **데이터만** 만든다(표현 없음). 컴포넌트는 **표현만** 한다
(계산 없음). 페이지는 **조립만** 한다.

---

# Task 1: v1 을 지우고 자리를 비운다

**Files:**
- Delete: `site/` (전체) · `scripts/build_site.py` · `tests/test_site.py`
- Modify: `docs/total_plan.md` (`§8` 역참조 경고)
- Modify: `.gitignore` (필요하면)

**Interfaces:**
- Produces: 빈 `site/` 자리와 `docs/total_plan.md` 의 정합성

- [ ] **Step 1: 지우기 전에 살릴 것을 옮겨 적는다**

`scripts/build_site.py` 의 **보안 게이트 여섯**과 **화이트리스트 방식**을 읽어
`docs/superpowers/specs/2026-08-15-argus-service-design.md §5` 와 대조한다.
🔴 **스펙에 없는 게이트가 코드에 있으면 스펙에 추가하라.** 지운 뒤에는 못 본다.

- [ ] **Step 2: 파일을 지운다**

```bash
git rm -r --cached site 2>/dev/null; rm -rf site
git rm scripts/build_site.py tests/test_site.py
```

- [ ] **Step 3: `total_plan §8` 의 역참조를 고친다**

`§8` 에 *"`scripts/build_site.py` 의 `parse_ssot()` 가 `§1` 을 파싱한다"* 는 경고가 있다.
🔴 **그 파일이 사라지므로 경고도 사라져야 한다.** 다만 **`§1` 이 기계 판독 대상이라는
사실 자체는 남긴다** — 새 빌드 스크립트가 읽을 수도 있고, 형식을 함부로 깨면 안 되기 때문이다.

- [ ] **Step 4: 전량 테스트**

Run: `PYTHONIOENCODING=utf-8 C:/Users/Juhwankim/miniconda3/python.exe -m pytest -q`
Expected: 🔴 **59건이 줄어든다**(1,184 → 약 1,125). 그것이 정상이다. **다른 테스트가
깨지면 멈추고 보고하라** — `test_site.py` 가 다른 것을 지키고 있었다는 뜻이다.

- [ ] **Step 5: 커밋**

```bash
git commit --only site scripts/build_site.py tests/test_site.py docs/total_plan.md \
  -m "chore(site): v1 을 지운다 — 연구 로그를 서비스와 섞지 않는다"
```

---

# Task 2: 데이터 빌드 스크립트 — 🔴 나머지가 다 여기 의존한다

**Files:**
- Create: `scripts/build_service_data.py`
- Create: `tests/test_service_data.py`
- Read: `artifacts/{exchange,major,holding}_records.jsonl` · `references.jsonl` ·
  `counterparty_graph.json` · `data/dart-disclosures/universe.csv`

**Interfaces:**
- Produces: `site/public/data/` 아래 다섯 파일
  ```
  meta.json          {generated_at, ranges: {companies:70, from:"2023-01", to:"2026-03"},
                      counts: {disclosures, corrections, contracts, partners}}
  companies.json     [{slug, name, sector, n_disclosures, n_corrections, top_partners:[...]}]
  company/<slug>.json {name, sector, contracts:[...], corrections:[...],
                       partners:[...], financials:{...}|null}
  corrections.json   [{rcept_no, corp_name, date, form_name, resolved, reason,
                       targets:[...], amount_before, amount_after}]
  network.json       {hubs:[{name, kind, count, partners:[...]}], pairs:[{a,b,via}]}
  ```

## 🔴 이 스크립트의 규율

```
① src/argus 를 import 하지 않는다 — json · csv · pathlib 만
② evalset/ · artifacts/ablation/ 을 열지 않는다
③ 출력 필드 화이트리스트 — 🔴 모르는 필드가 붙으면 빌드가 죽는다
④ 값이 없으면 null 을 쓰고 추정하지 않는다
```

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
# tests/test_service_data.py
import json, subprocess, sys, pathlib
ROOT = pathlib.Path(__file__).resolve().parents[1]
DATA = ROOT / "site" / "public" / "data"


def test_build_produces_the_five_files():
    subprocess.run([sys.executable, str(ROOT / "scripts" / "build_service_data.py")],
                   check=True, cwd=ROOT)
    for name in ("meta.json", "companies.json", "corrections.json", "network.json"):
        assert (DATA / name).exists(), f"{name} 이 없다"
    assert (DATA / "company").is_dir()


def test_builder_never_reads_the_evalset_or_ablation():
    """🔴 서비스 데이터는 공시 원본에서만 온다."""
    src = (ROOT / "scripts" / "build_service_data.py").read_text(encoding="utf-8")
    for forbidden in ("evalset", "ablation", "heldout", "from argus", "import argus"):
        assert forbidden not in src, f"빌드 스크립트가 {forbidden!r} 를 건드린다"


def test_output_carries_no_research_vocabulary():
    """🔴 화면에 실험 어휘가 새면 안 된다."""
    banned = ("정답률", "ablation", "기여도", "judge", "코호트", "골든")
    for path in DATA.rglob("*.json"):
        text = path.read_text(encoding="utf-8")
        for word in banned:
            assert word not in text, f"{path.name} 에 {word!r} 가 있다"
```

- [ ] **Step 2: 실패를 확인한다**

Run: `PYTHONIOENCODING=utf-8 C:/Users/Juhwankim/miniconda3/python.exe -m pytest tests/test_service_data.py -q`
Expected: FAIL — 스크립트가 없다

- [ ] **Step 3: `meta.json` · `companies.json` 을 내는 최소 구현**

`universe.csv`(UTF-8 BOM — `encoding="utf-8-sig"`)로 70개사를 읽고,
`exchange_records.jsonl` 에서 회사별 공시 수와 `is_correction` 수를 센다.

🔴 **`slug` 는 URL 안전해야 한다.** 한글 회사명을 그대로 쓰면 경로가 깨질 수 있으므로
`corp_code`(있으면) 또는 회사명의 URL 인코딩을 쓴다. **선택을 주석에 남겨라.**

- [ ] **Step 4: `company/<slug>.json` 을 낸다**

```
contracts   exchange_records 에서 그 회사 것 — amount · counterparty · period_start
            · rcept_dt · is_correction · rcept_no
corrections references.jsonl 에서 그 회사 것 — 🔴 resolved=false 도 담는다
partners    counterparty_graph 의 노드에서 그 회사가 partners 에 들어간 허브들
financials  🔴 이번 판에서는 null 로 둔다 — Task 9 에서 채운다
```

- [ ] **Step 5: `corrections.json` 을 낸다**

🔴 **금액 diff 가 이 화면의 핵심이다.** `targets` 로 원공시를 찾아 `amount` 를 비교한다.
**못 찾으면 `amount_before`·`amount_after` 를 `null` 로 두고 `resolved:false` 를 남긴다.**
추정하지 않는다.

- [ ] **Step 6: `network.json` 을 낸다**

`counterparty_graph.json` 의 `nodes`·`pairs` 를 그대로 옮기되 **화이트리스트로 거른다**:
`canonical` · `display` · `kind` · `count` · `partners`. 그 밖의 필드는 버린다.

- [ ] **Step 7: 🔴 화이트리스트 게이트를 넣는다**

```python
ALLOWED_CONTRACT_FIELDS = frozenset({
    "rcept_no", "rcept_dt", "amount", "amount_type", "counterparty",
    "period_start", "is_correction", "doc_subtype",
})
#: 🔴 모르는 필드가 붙으면 빌드가 죽는다. 블랙리스트는 다음 세대에 조용히 샌다.
def gate(row: dict, allowed: frozenset[str], where: str) -> dict:
    unknown = set(row) - allowed
    if unknown:
        raise SystemExit(f"🔴 {where} 에 모르는 필드: {sorted(unknown)} — 실을지 판단하라")
    return row
```

- [ ] **Step 8: 통과를 확인하고 산출물을 눈으로 본다**

Run: 빌드 → `companies.json` 의 회사 수가 **70** 인가 · `corrections.json` 건수가
**631 근처**인가 · `network.json` 허브가 **145 노드** 인가.
🔴 **어긋나면 멈추고 왜인지 적어라.** 스펙 `§2` 의 실측이 기준이다.

- [ ] **Step 9: 커밋**

```bash
git add scripts/build_service_data.py tests/test_service_data.py
git commit --only scripts/build_service_data.py tests/test_service_data.py \
  -m "feat(site): 공시 데이터를 서비스용 JSON 으로 빌드한다 — LLM 0회"
```

---

# Task 3: Next.js 골격과 디자인 토큰

**Files:**
- Create: `site/package.json` · `site/tsconfig.json` · `site/next.config.ts`
- Create: `site/app/layout.tsx` · `site/app/globals.css` · `site/components/Header.tsx`
- Modify: `.gitignore`

**Interfaces:**
- Consumes: `site/public/data/meta.json`
- Produces: `<Header/>` · CSS 토큰 · 정적 export 설정

- [ ] **Step 1: `scratch/ARGUS_example` 의 설정을 읽는다**

`package.json`·`next.config.ts`·`tsconfig.json`·`app/globals.css` 를 읽어 **같은 구조를
따른다.** 🔴 **그 프로젝트가 Turbopack panic 때문에 webpack 모드를 쓴다** — `README.md` 에
적혀 있다. **같은 함정을 밟지 마라.**

- [ ] **Step 2: 정적 export 를 켠다**

```ts
// site/next.config.ts
import type { NextConfig } from "next";
const config: NextConfig = {
  output: "export",              // 🔴 서버 없이 어디든 올린다
  images: { unoptimized: true }, // export 에는 이미지 최적화 서버가 없다
};
export default config;
```

- [ ] **Step 3: `.gitignore` 에 산출물을 넣는다**

```
site/node_modules/
site/.next/
site/out/
site/public/data/
```
🔴 **`site/app`·`site/components`·`package.json` 은 커밋한다** — 그것이 소스다.

- [ ] **Step 4: 디자인 토큰을 정한다**

`globals.css` 에 색·간격·타이포를 CSS 변수로. 🔴 **예시의 토큰 이름을 그대로 쓰지 말고
우리 것으로 정하되, 구조(변수 → 컴포넌트)는 따른다.**

**공시 도메인에 맞는 색 하나를 정하라**: 🔴 **정정 표시**가 눈에 띄어야 한다 —
경고색을 토큰으로 둔다(`--tone-corrected`).

- [ ] **Step 5: `Header.tsx` 와 `layout.tsx`**

네비게이션: `기업` · `정정 추적` · `관계망` · `소개`.
🔴 **`/about` 을 "소개" 로 쓴다** — "방법론"·"기술"이 아니다.

- [ ] **Step 6: 빌드가 도는지 확인한다**

```bash
cd site && npm install && npm run build
```
Expected: `out/` 이 생긴다

- [ ] **Step 7: 커밋** (🔴 `node_modules` 가 안 들어갔는지 `git status` 로 확인)

---

# Task 4: 🔴 `HubGraph` — 랜딩의 주인공

**Files:**
- Create: `site/components/HubGraph.tsx`
- Test: 🔴 시각 요소라 단위 테스트가 아니라 **눈으로 본다**. 다만 좌표 계산은 순수 함수로 빼서 테스트한다

**Interfaces:**
- Consumes: `network.json` 의 `hubs:[{name, kind, count, partners:[]}]`
- Produces: `<HubGraph hubs={...} minCount={5} />` — SVG 한 덩이

## 🔴 왜 라이브러리를 안 쓰나

노드 145 · 쌍 44 다. **힘 기반 시뮬레이션은 이 규모에서 오히려 산만해 보인다.**
그리고 실제 구조가 **쌍이 아니라 발주처 → 수주사**다. **허브 방사형이 데이터의 모양이다.**

- [ ] **Step 1: 좌표 계산을 순수 함수로 쓰고 테스트한다**

```tsx
// 🔴 허브를 세로로 쌓고 파트너를 오른쪽 반원에 배치한다.
export function layoutHub(index: number, total: number, partners: number) {
  const cy = 90 + index * 170;
  const spread = Math.min(140, 30 * partners);
  return {
    hub: { x: 180, y: cy },
    partners: Array.from({ length: partners }, (_, i) => ({
      x: 470,
      y: cy - spread / 2 + (partners === 1 ? spread / 2 : (spread * i) / (partners - 1)),
    })),
  };
}
```

- [ ] **Step 2: 원 크기를 계약 건수에 잇는다**

```tsx
// 🔴 반지름은 sqrt 로 — 넓이가 건수에 비례해야 눈이 속지 않는다
const r = 14 + Math.sqrt(count) * 2.2;
```

- [ ] **Step 3: 렌더한다** — 허브 원 + 이름 + 건수 · 파트너 원 + 이름 · 잇는 선

🔴 **파트너가 우리 70개사면 `/company/<slug>` 로 링크**한다. 발주처(공공·해외)는 링크 없음.

- [ ] **Step 4: `minCount` 로 자른다** — 랜딩은 `count >= 5` 인 허브만(약 10개).
      **나머지는 `/network` 에서 본다** 는 링크를 아래 둔다

- [ ] **Step 5: 🔴 눈으로 본다** — `npm run dev` 로 띄워 **겹침·잘림·읽기 어려움**을 확인.
      겹치면 `spread`·`cy` 간격을 키운다

- [ ] **Step 6: 반응형** — 폭이 좁으면 SVG `viewBox` 로 축소되게. 🔴 **모바일에서 글자가
      읽히는지 확인하라**

- [ ] **Step 7: 커밋**

---

# Task 5: 랜딩 `/`

**Files:**
- Create: `site/app/page.tsx` · `site/components/CompanySearch.tsx`

**Interfaces:**
- Consumes: `companies.json` · `network.json` · `meta.json`

**이걸 보러 온 사람이 하려는 일**: *"이게 뭘 해 주는 서비스인지 3초 안에 알고, 내가 궁금한
회사로 들어간다."*

- [ ] **Step 1: 히어로 — 한 줄과 검색창**

```
누가 누구에게서 일을 받는가
[ 기업 검색 ▸ ]
```
🔴 **부제는 한 줄만.** *"공시 4,204건을 관계로 이었습니다"* 정도. **기술 설명 금지.**

- [ ] **Step 2: `CompanySearch` — 클라이언트 필터**

`"use client"` · `companies.json` 을 받아 이름 부분일치. 🔴 **엔터로 첫 결과로 이동.**
결과 없으면 *"70개사 안에서만 찾습니다"* 를 보여 준다 — **한계를 그 자리에서 말한다.**

- [ ] **Step 3: `<HubGraph/>` 를 놓는다**

- [ ] **Step 4: 🔴 정정 훅**

```
공시 10건 중 4건이 나중에 정정됩니다        [정정된 공시 보기 →]
```
🔴 **수는 `meta.json` 에서 읽는다. 하드코딩 금지.**

- [ ] **Step 5: 🔴 실험 어휘가 없는지 눈으로 확인한다** (스펙 `§9 ④`)

- [ ] **Step 6: 커밋**

---

# Task 6: `/company/[slug]` — 🔴 핵심 화면

**Files:**
- Create: `site/app/company/[slug]/page.tsx`
- Create: `site/components/ContractTable.tsx` · `site/components/Limits.tsx`

**Interfaces:**
- Consumes: `companies.json`(목록·`generateStaticParams`) · `company/<slug>.json`
- Produces: `<ContractTable contracts={...}/>` · `<Limits kinds={[...]}/>`

**이걸 보러 온 사람이 하려는 일**: *"이 회사가 최근 뭘 했는지, 그중 나중에 바뀐 게 있는지
한 화면에서 안다."*

- [ ] **Step 1: `generateStaticParams` 로 70개사를 미리 생성**

```tsx
export async function generateStaticParams() {
  const companies = (await import("../../../public/data/companies.json")).default;
  return companies.map((c: {slug: string}) => ({ slug: c.slug }));
}
```

- [ ] **Step 2: 헤더 — 회사명 · 섹터 · 공시 건수**

- [ ] **Step 3: 🔴 정정 경보를 맨 위에**

```
🔴 이 회사의 공시 N건이 나중에 정정됐습니다        [보기 →]
```
**N=0 이면 이 블록을 아예 안 그린다.** 🔴 *"정정 없음"* 도 쓰지 마라 — 잡음이다.

- [ ] **Step 4: `ContractTable`** — 금액 · 상대 · 기간 · 공시일.
      🔴 **정정된 행은 시각적으로 구분**하고 **`amount_before → amount_after` 를 같은 줄에** 쓴다

- [ ] **Step 5: 거래상대 목록** — 건수순. 🔴 **우리 70개사면 링크**

- [ ] **Step 6: 재무 요약** — `financials` 가 `null` 이면 **그 블록을 안 그린다**(Task 9 에서 채운다)

- [ ] **Step 7: `<Limits/>` 를 아래에**

```tsx
// 🔴 한계를 화면에 둔다. 숨기면 신뢰를 잃는다.
// kinds 로 그 화면에 해당하는 것만 고른다.
const LIMIT_TEXT = {
  universe: "70개사만 다룹니다.",
  counterparty: "계약 상대 표기가 공시마다 달라 같은 회사가 여러 이름으로 잡힐 수 있습니다.",
  correction: "정정 공시가 원공시를 가리키지 못하는 경우가 있습니다.",
  network: "공시에 상대가 명시된 계약만 담습니다 — 전체 거래관계가 아닙니다.",
} as const;
```

- [ ] **Step 8: 🔴 세 회사로 눈 확인** — 공시 많은 곳(`대우건설` 133건) · 적은 곳 ·
      정정 0건인 곳. **레이아웃이 다 버티는지 본다**

- [ ] **Step 9: 커밋**

---

# Task 7: `/corrections` — 🔴 우리만의 것

**Files:**
- Create: `site/app/corrections/page.tsx` · `site/components/CorrectionChain.tsx`

**이걸 보러 온 사람이 하려는 일**: *"내가 아는 그 공시가 나중에 바뀌었는지 확인한다."*

- [ ] **Step 1: 목록 — 회사 · 공시일 · 서식명 · 🔴 금액 변화**
      **금액이 바뀐 것을 앞에 정렬**한다 — 그것이 사람이 놀라는 지점이다

- [ ] **Step 2: `CorrectionChain`** — 원공시 → 정정본을 시각적으로 잇는다

- [ ] **Step 3: 🔴 `resolved:false` 를 정직하게 표시**

```
추적 실패 — 원공시를 찾지 못했습니다 (사유: …)
```
🔴 **감추면 목록이 실제보다 완전해 보인다.**

- [ ] **Step 4: 회사 필터** — 클라이언트 필터로 충분하다

- [ ] **Step 5: 🔴 631건이 한 페이지에 들어가는지 확인** — 무거우면 상위 N + 더보기

- [ ] **Step 6: 커밋**

---

# Task 8: `/network` 와 `/about`

**Files:**
- Create: `site/app/network/page.tsx` · `site/app/about/page.tsx`

## `/network`

**이걸 보러 온 사람이 하려는 일**: *"이 발주처가 누구와 일하는지, 이 회사가 누구에게서
일을 받는지 본다."*

- [ ] **Step 1: 허브 전량**(145) 을 `kind` 필터와 함께 — 공공 · 국내기업 · 해외 · 기타
- [ ] **Step 2: 🔴 역방향** — 우리 70개사를 고르면 **그 회사가 등장하는 허브들**을 보여 준다
- [ ] **Step 3: `pairs` 44** — 같은 발주처를 공유하는 기업 쌍. `via` 를 함께 표시
- [ ] **Step 4: `<Limits kinds={["network","counterparty"]}/>`**

## `/about` — 🔴 기술 얘기는 여기 한 쪽만

- [ ] **Step 5: 무엇을 하는가** — 세 문장 이내
- [ ] **Step 6: 데이터 범위** — 70개사 · 2023.01~2026.03 · 문서군 넷 · 건수는 `meta.json` 에서
- [ ] **Step 7: 🔴 한계 전량** — `LIMIT_TEXT` 넷을 다 보여 준다
- [ ] **Step 8: 🔴 금지 어휘 확인** — `arm`·정답률·테스트 수가 없는지 파일을 grep

```bash
grep -nE "ablation|정답률|기여도|arm |테스트 [0-9]|judge" site/app/about/page.tsx
# 아무것도 안 나와야 한다
```

- [ ] **Step 9: 커밋**

---

# Task 9: 재무 요약 채우기

**Files:**
- Modify: `scripts/build_service_data.py` · `site/app/company/[slug]/page.tsx`

🔴 **Task 6 에서 `null` 로 비워 둔 칸이다.** 정기공시에서 매출·영업이익을 뽑는다.

- [ ] **Step 1: `periodic_sections.jsonl` 에서 요약재무 섹션을 찾는다**
      경로가 `III. 재무에 관한 사항 > 1. 요약재무정보` 류다
- [ ] **Step 2: 🔴 `periodic_sections.txt` 에서 offset seek 로 본문을 읽는다**
      926MB 라 통째로 로드하면 안 된다
- [ ] **Step 3: 표에서 매출액·영업이익을 뽑는다** — 🔴 **뽑히지 않으면 `null`. 추정 금지**
- [ ] **Step 4: 🔴 뽑힌 값이 맞는지 세 회사로 손 검증** — 원문과 대조
- [ ] **Step 5: 페이지에 블록을 켠다** · 커밋

---

# Task 10: 마감 — 다듬기와 검증

**Files:**
- Modify: 전반
- Create: `site/README.md`

- [ ] **Step 1: 🔴 성공 기준 다섯을 하나씩 확인한다** (스펙 `§9`)

```
① 랜딩 한 화면에서 "무엇을 하는 서비스인지" 가 설명 없이 보이는가
② 회사 하나를 고르면 공시 수십 건을 안 열고도 최근 움직임을 아는가
③ 정정된 공시를 찾는 데 DART 보다 빠른가
④ 🔴 화면 어디에도 arm 이름 · 정답률 · 테스트 수가 없는가
⑤ 한계가 숨겨져 있지 않은가
```

- [ ] **Step 2: 🔴 전 페이지 금지 어휘 스캔**

```bash
grep -rnE "ablation|정답률|기여도 곡선|arm |A20|A8|L6|judge|코호트|골든|크레딧" site/app site/components
# 아무것도 안 나와야 한다
```

- [ ] **Step 3: 🔴 보안 확인 넷**

```bash
grep -rn "223.130.129.131" site/          # 공인 IP — 0건이어야 한다
grep -rn "evalset\|heldout" site/ scripts/build_service_data.py   # 0건
grep -rn "CLOVA_API_KEY\|\.env" site/     # 0건
git status --short | grep node_modules    # 0건
```

- [ ] **Step 4: 정적 export 로 빌드하고 `out/` 을 브라우저로 연다**

```bash
cd site && npm run build && npx serve out
```
🔴 **`file://` 이 아니라 로컬 서버로 확인한다** — Next.js export 는 상대 경로에 민감하다

- [ ] **Step 5: 모바일 폭에서 확인** — 🔴 관계망 SVG 가 읽히는가

- [ ] **Step 6: `site/README.md`** — 빌드·실행 방법 · 🔴 **데이터 출처와 갱신 방법**

- [ ] **Step 7: 전량 테스트 · 커밋**

---

# §11. 🔴 하지 않는 것

| 항목 | 이유 |
|---|---|
| **라이브 질의응답 데모** | 09-21 이후. 🔴 평가기간에 자원을 나누면 응답 속도가 깎인다 |
| **평가 서버(`223.130.129.131`)에 올리기** | 🔴 **절대.** 로컬 빌드만. 09-21 이후 별도 호스팅 |
| 로그인 · 저장 · 알림 | 서비스 범위 밖 |
| 그래프 라이브러리 | 노드 145 · 쌍 44 — SVG 로 충분하고 더 낫다 |
| 실시간 공시 수집 | 코퍼스가 고정이다. **그 사실을 `/about` 에 쓴다** |
| 연구 산출물 페이지 | 🔴 **v1 이 그것으로 죽었다** |

---

# §12. 새 세션 진입

| 순서 | 파일 | 왜 |
|---|---|---|
| 1 | **스펙 `§0`·`§9`** | 🔴 v1 이 왜 쓰레기였나 · 성공 기준 |
| 2 | 이 문서 `Global Constraints` | 금지 어휘가 거기 있다 |
| 3 | 스펙 `§2` | 데이터 실측 — 631/1,469 · 허브 25 |
| 4 | `scratch/ARGUS_example` | 참조 구조. 🔴 Turbopack 함정도 |

**읽지 않아도 되는 것**: `docs/pipeline/` · `docs/superpowers/plans/2026-08-14-ablation-v4.md` —
**연구 문서다. 이 사이트와 무관하다.**
