# ARGUS 서비스 사이트

> 공시 하나를 읽는 게 아니라, **공시들 사이의 관계를 읽는다.**

DART 공시를 관계로 이어 보여 주는 정적 사이트다. **서버도 DB도 런타임 API 도 없다** —
페이지가 읽는 것은 `public/data/*.json` 뿐이고, 그 JSON 은 파이썬이 빌드 타임에 만든다.

```
artifacts/*.jsonl  ──▶  scripts/build_service_data.py  ──▶  site/public/data/*.json  ──▶  Next.js
```

**LLM 0회 · 비용 0원 · 네트워크 0회.** 이 사이트를 만드는 데 모델을 부르지 않는다.

---

## 🔴 먼저 데이터를 만들어야 한다

```bash
cd site
npm install
npm run data     # 🔴 이것을 먼저. 안 하면 npm run build 가 죽는다
npm run build
```

**`npm run data` 를 건너뛰면 빌드가 실패한다.** `app/layout.tsx` 가
`public/data/meta.json` 을 import 하는데 그 폴더는 `.gitignore` 되어 있어서
새로 받은 저장소에는 없다. 산출물을 커밋하지 않는 이유는 `artifacts/` 가 움직일 때마다
사본이 하나 더 낡기 때문이다.

`npm run data` 는 `python ../scripts/build_service_data.py` 를 부른다. `python` 이
경로에 없으면 직접 부르면 된다:

```bash
PYTHONIOENCODING=utf-8 C:/Users/<사용자>/miniconda3/python.exe scripts/build_service_data.py
```

## 명령

| | |
|---|---|
| `npm run data` | 🔴 `artifacts/` → `public/data/*.json` (74 파일). **나머지 전부의 선행조건** |
| `npm run dev` | 개발 서버. 🔴 **`--webpack` 이다** — Next 16 개발 서버가 Turbopack 에서 죽는다 |
| `npm run build` | 정적 export → `out/` |
| `npm test` | 좌표·표기 계산의 단위 테스트 (Node 24 가 `.ts` 를 그대로 돈다) |

## 🔴 `out/` 은 로컬 서버로 연다

```bash
npx serve out          # 또는
python -m http.server 8765 --directory out
```

**`file://` 로 열면 안 된다.** 정적 export 는 `/_next/...` 절대 경로를 쓴다.

---

## 데이터는 어디서 오나

빌드 스크립트가 읽는 파일은 **명단(`ALLOWED_READS`)에 적힌 여덟 개뿐**이다. 그 밖의
어떤 경로도 `ForbiddenRead` 로 막힌다 — 금지 목록이 아니라 **허용 목록**이라서
다음 세대가 원천을 늘려도 조용히 새지 않는다.

```
artifacts/exchange_records.jsonl    거래소공시 — 계약 · 금액 · 상대 · 정정 표시
artifacts/major_records.jsonl       주요사항보고 (건수만)
artifacts/holding_records.jsonl     지분공시     (건수만)
artifacts/references.jsonl          정정 체인 — 🔴 추적 실패 사유가 여기 있다
artifacts/counterparty_graph.json   거래관계 그래프
artifacts/periodic_sections.jsonl   정기공시 목차 — 요약재무정보가 어디 있는지
artifacts/periodic_sections.txt     🔴 926MB. 통째로 안 읽는다 — offset seek 만
data/dart-disclosures/universe.csv  70개사 명부
```

**갱신하려면** `artifacts/` 를 새로 만든 뒤 `npm run data` 를 다시 돌린다. 코퍼스는
고정이므로 사이트가 스스로 새 공시를 받아 오지 않는다 — 그 사실을 `/about` 에 적어 뒀다.

### 산출물

```
meta.json            범위 · 생성 시각 · 건수
companies.json       70개사 요약
company/<slug>.json  회사별 상세 (slug = corp_code, DART 8자리)
corrections.json     정정 631건 — 🔴 추적 실패 309건도 사유와 함께 들어 있다
network.json         발주처 145곳 · 쌍 44
```

`slug` 가 회사명이 아니라 `corp_code` 인 이유: 한글 이름을 경로에 쓰면 정적 export 가
만드는 디렉터리 이름이 호스트마다 다르게 인코딩된다.

---

## 화면

| 경로 | 이걸 보러 온 사람이 하려는 일 |
|---|---|
| `/` | 이게 뭘 해 주는 서비스인지 3초 안에 알고, 내가 궁금한 회사로 들어간다 |
| `/company` | 내가 아는 회사가 여기 있는지 확인하고 그 회사 화면으로 들어간다 |
| `/company/[slug]` | 🔴 이 회사가 최근 뭘 했는지, 그중 나중에 바뀐 게 있는지 한 화면에서 안다 |
| `/corrections` | 내가 아는 그 공시가 나중에 바뀌었는지 확인한다 |
| `/network` | 이 발주처가 누구와 일하는지, 이 회사가 누구에게서 일을 받는지 본다 |
| `/about` | 이 화면들을 어디까지 믿어도 되는지 판단한다 |

🔴 **화면을 새로 만들기 전에 이 한 줄을 먼저 쓴다.** 쓸 수 없으면 그 화면은 필요 없다.

## 🔴 정직성이 기능이다

값이 없으면 **비운다.** 0 으로도 어림수로도 채우지 않는다.

- 정정 추적이 원공시를 못 찾은 **309건**을 목록에서 빼지 않고 사유와 함께 보여 준다.
  감추면 목록이 실제보다 완전해 보이고, 사람은 "여기 없으니 정정이 없구나" 로 읽는다.
- 재무 요약은 **70개사 중 50곳**만 있다. 기간·단위·연결 여부가 문서에서 모두 읽힐 때만
  싣는다 — 기간을 모르는 매출액은 뜻이 없다.
- 거래상대 건수를 못 세면 관계는 남기고 **건수만** 비운다.
- `<Limits/>` 가 화면마다 그 화면의 한계를 적는다.

## 저장소 규율

```
추적한다   app/ · components/ · lib/ · package.json · tsconfig.json · next.config.ts · README.md
안 한다    node_modules/ · .next/ · out/ · public/data/ · tsconfig.tsbuildinfo
```

🔴 **`node_modules` 를 커밋하지 않는다.**
