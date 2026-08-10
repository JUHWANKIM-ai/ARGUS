# Phase A~I 잔여 작업 + Ablation v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 확장된 106문항 기준으로 Phase A·C·D·H 를 재측정하고, `counterparty` 노드를 붙여 Ablation v2 를 돌린다.

**Architecture:** 현재 수치는 전부 **42문항 코호트** 기준이다(Recall@5 0.967, 그리드 33/36, Phase H 귀속). 평가셋이 106문항으로 늘고 A7 이 붙은 뒤 아무것도 다시 재지 않았다. 이 계획은 ① 낡은 측정을 새 코호트로 갱신하고 ② 밸류체인 문항이 요구하는 `counterparty` 2-hop 을 구현한 뒤 ③ v1 과 같은 프로토콜로 v2 를 돌린다. 배포는 이 계획에 포함하지 않는다(별도 계획).

**Tech Stack:** Python 3.12 · `rank-bm25` · FastAPI · HyperCLOVA X `HCX-005` · pytest

## Global Constraints

- **CLOVA 호출**: 명시된 측정 태스크(T7·T9·T10)에서만. 나머지는 **LLM 0회**.
- **`.env` 를 읽거나 출력하지 않는다.** API 키를 로그·아티팩트·문서 어디에도 남기지 않는다.
- **`tests/conftest.py` 의 `block_clova_network` fixture 를 우회하지 않는다.** 테스트는 항상 LLM 0회.
- **`evalset/heldout.jsonl` 은 최종 구성 1회용.** T10 에서만 연다. 그 전 태스크는 **열지도 않는다**.
- **Python 인터프리터**: `C:\Users\Juhwankim\miniconda3\python.exe` (`python` 은 Windows Store 스텁, exit 9009).
- **테스트 기준선**: 계획 착수 시점 **576 passed**. 각 태스크는 이를 유지하며 늘린다.
- **인덱스 스냅샷**: T9 시작 전 해시를 찍고, v2 실행 중 재빌드하지 않는다.
- **예산 상한**: T7 3,000원 · T9 20,000원 · T10 1,000원. 각 태스크에서 80% 도달 시 중단하고 부분 보고.
- **단가**: 혼합 **7.02원 / 1K 토큰** (실측). dev 42문항 전수 = 944.5원.
- **데이터 상한을 실패로 집계하지 않는다**: 공시유보 66 · 임상 166 · 2023-01-01 이전 참조 1,005 · 정기공시 본문 부재 2건(`periodic_20260619000667` · `periodic_20240514001522`).

---

## File Structure

| 파일 | 책임 | 태스크 |
|---|---|---|
| `scripts/eda_counterparty.py` (신규) | `counterparty` 표기 정규화 실태 측정 | T1 |
| `src/argus/corpus/counterparty.py` (신규) | 정규화 · 분류(`universe`/`public`/`overseas`/`vague`/`other`) | T2 |
| `scripts/build_counterparty_graph.py` (신규) | `artifacts/counterparty_graph.json` 생성 | T3 |
| `src/argus/agent/tools.py` | `query_by_counterparty` 도구 추가 (14 → 15종) | T4 |
| `src/argus/agent/pipeline.py` | 발주처 질의 라우팅 · 2-hop 근거 수집 | T5 |
| `scripts/eda_new_sectors.py` (신규) | 신규 25개사 문서 프로파일 (Phase A 부분 재실행) | T6 |
| `scripts/error_analysis.py` | 코호트 인자화 (42 → 106) | T7 |
| `docs/pipeline/04-retriever.md` | 게이트 D 를 새 코호트로 갱신 | T8 |
| `src/argus/ablation.py` · `scripts/ablation.py` | arm `A8` (counterparty 2-hop) 추가 | T9 |
| `docs/pipeline/09-ablation.md` · `artifacts/ablation/v2/` | v2 결과 | T9 · T10 |

---

## Task 1: `counterparty` 표기 실태 측정

**Files:**
- Create: `scripts/eda_counterparty.py`
- Create: `artifacts/eda/counterparty_profile.json`
- Test: `tests/test_counterparty.py`

**Interfaces:**
- Consumes: `artifacts/exchange_records.jsonl` (필드 `counterparty` · `corp_name`)
- Produces: `artifacts/eda/counterparty_profile.json` — 키 `total` · `named` · `unique_raw` · `variants`(list of `{canonical, surface_forms, count}`) · `hub_candidates`

**🔴 배경 — 이 태스크는 대부분 이미 끝나 있다.**

밸류체인 문항 트랙이 정규화를 **실행 가능한 형태로 만들어 뒀다**:

```
scripts/validate_evalset.py::normalize_counterparty   ← 정규화 규격
tests/test_evalset.py::COUNTERPARTY_VARIANTS          ← 변이 5유형 고정
```

**실측 (이 값이 정본이다):**

```
값 있음 1,125 / 1,469  ·  고유 원문 462종  →  정규화 후 378종 (비실명 제외)
2회 이상 노드 후보 157개  ·  2-hop 중개 노드 25개
기업 쌍  20(직접)  →  44(2-hop),  신규 36쌍
정규화가 실제로 합친 그룹 34개
```

> **초안 계획의 `417종 · 29허브 · 40쌍` 은 폐기한다.** 그것은 코디네이터가 짠 간이
> `norm()` 으로 잰 값이고, 위 값은 **변이 5유형을 실제로 확인한 정규화**로 잰 것이다.
> 정규화가 좋아지면 조각이 합쳐지므로 고유 수는 줄고 허브는 굵어진다.

**변이 5유형** (전부 실재 — 접지 않으면 방위사업청 91건이 5조각, 한수원 35건이 5조각으로 흩어져 **어느 조각도 2-hop 을 못 만든다**):

| 유형 | 실례 |
|---|---|
| ① 국호 접두 + 괄호 영문명 | `대한민국 방위사업청` / `방위사업청` / `방위사업청 (Defense Acquisition Program Administation)` ← **오타 포함** |
| ② 법인격 접미 4형 | `한국수력원자력` / `(주)` / `㈜` / `주식회사` / ` 주식회사` |
| ③ 법인격 접두 | `(주)포스코이앤씨` / `㈜포스코이앤씨` |
| ④ **공백만 다름** | `주식회사 유토개발2차` vs `주식회사유토개발2차` |
| ⑤ 괄호 약칭 | `한국토지주택공사(LH)` |

**따라서 T1 은 "새로 재는" 태스크가 아니라 "이미 잰 것을 산출물로 고정하는" 태스크다.**

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
# tests/test_counterparty.py
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_counterparty_profile_records_surface_form_variants():
    """같은 발주처의 표기 변이가 한 canonical 아래 묶여야 한다."""
    p = ROOT / "artifacts/eda/counterparty_profile.json"
    prof = json.loads(p.read_text(encoding="utf-8"))

    by_canon = {v["canonical"]: v for v in prof["variants"]}
    # 실측 확인된 변이 — 방위사업청은 최소 두 표기로 등장한다
    assert "방위사업청" in by_canon
    forms = by_canon["방위사업청"]["surface_forms"]
    assert len(forms) >= 2, f"표기 변이가 1개뿐이면 정규화가 안 된 것: {forms}"
    assert by_canon["방위사업청"]["count"] >= 40

    # 1회 등장이 절반을 넘는다는 사실이 기록돼야 한다
    assert prof["singleton_ratio"] > 0.5
```

- [ ] **Step 2: 실패를 확인한다**

Run: `C:\Users\Juhwankim\miniconda3\python.exe -m pytest tests/test_counterparty.py -v`
Expected: FAIL — `FileNotFoundError: artifacts/eda/counterparty_profile.json`

- [ ] **Step 3: 측정 스크립트를 쓴다**

🔴 **정규화 규칙을 여기서 다시 쓰지 않는다.** `scripts/validate_evalset.py::normalize_counterparty`
가 이미 변이 5유형을 확인해 만든 정본이고, `tests/test_evalset.py::COUNTERPARTY_VARIANTS` 가
고정하고 있다. **여기서 복제하면 T2 가 그 복제본을 지우게 되어 순수 낭비이고, 그 사이
문항 검증(C12)과 값이 갈릴 수 있다.** import 해서 쓴다.

```python
# scripts/eda_counterparty.py
"""counterparty 표기 실태 — 노드 승격 전에 무엇을 정규화해야 하는지 센다.

정규화 규칙은 `validate_evalset.normalize_counterparty` 가 정본이다.
T2 가 그것을 `argus.corpus.counterparty` 로 옮기면 이 import 만 갈아끼운다.
"""
import argparse
import collections
import json
from pathlib import Path

from scripts.validate_evalset import (
    normalize_counterparty as canonical,
    is_vague_counterparty,
)

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "artifacts/eda/counterparty_profile.json"))
    args = ap.parse_args()

    recs = [
        json.loads(l)
        for l in (ROOT / "artifacts/exchange_records.jsonl").read_text(encoding="utf-8").splitlines()
        if l.strip()
    ]
    raw = [(r["counterparty"], r.get("corp_name")) for r in recs if r.get("counterparty")]
    named = [(c, o) for c, o in raw if not is_vague_counterparty(c)]

    surfaces = collections.defaultdict(collections.Counter)
    owners = collections.defaultdict(set)
    for c, owner in named:
        k = canonical(c)
        surfaces[k][c] += 1
        if owner:
            owners[k].add(owner)

    variants = [
        {
            "canonical": k,
            "surface_forms": sorted(v),
            "count": sum(v.values()),
            "universe_partners": sorted(owners[k]),
        }
        for k, v in sorted(surfaces.items(), key=lambda x: -sum(x[1].values()))
    ]
    singles = sum(1 for v in variants if v["count"] == 1)
    hubs = [v for v in variants if len(v["universe_partners"]) >= 2]

    prof = {
        "total": len(raw),
        "named": len(named),
        "unique_raw": len({c for c, _ in named}),
        "unique_canonical": len(variants),
        "singleton_ratio": singles / len(variants),
        "hub_candidates": len(hubs),
        "variants": variants,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(prof, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"canonical {len(variants)} · singleton {prof['singleton_ratio']:.1%} · hub {len(hubs)}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: 실행하고 테스트를 통과시킨다**

```bash
C:\Users\Juhwankim\miniconda3\python.exe scripts/eda_counterparty.py
C:\Users\Juhwankim\miniconda3\python.exe -m pytest tests/test_counterparty.py -v
```
Expected: PASS. 콘솔에 `canonical 417 · singleton 56.8% · hub 29` 근처 값.

- [ ] **Step 5: 커밋**

```bash
git add scripts/eda_counterparty.py tests/test_counterparty.py artifacts/eda/counterparty_profile.json
git commit -m "eda: counterparty 표기 실태 측정 — 노드 승격 판정 입력"
```

---

## Task 2: `counterparty` 정규화·분류 모듈

**Files:**
- Create: `src/argus/corpus/counterparty.py`
- Test: `tests/test_counterparty.py` (Task 1 파일에 추가)

**Interfaces:**
- Consumes: Task 1 의 `canonical()` 규칙 (여기로 옮긴다)
- Produces:
  - `canonical(s: str) -> str`
  - `classify(s: str) -> str` — `"universe" | "public" | "overseas" | "vague" | "other"`
  - `PUBLIC_HINTS: frozenset[str]`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
# tests/test_counterparty.py 에 추가
from argus.corpus import counterparty as cp


def test_canonical_merges_known_surface_variants():
    assert cp.canonical("대한민국 방위사업청") == cp.canonical("방위사업청")
    assert cp.canonical("(주)한국수력원자력") == cp.canonical("한국수력원자력")
    assert cp.canonical("Torp Co., Ltd.") == cp.canonical("TORP")


def test_classify_separates_public_from_vague():
    assert cp.classify("방위사업청") == "public"
    assert cp.classify("한국수력원자력") == "public"
    assert cp.classify("삼성전자") == "universe"
    assert cp.classify("글로벌 대형기업") == "vague"
    assert cp.classify("오세아니아지역 선주") == "vague"


def test_vague_strings_never_become_nodes():
    """비실명은 노드가 되면 안 된다 — 서로 다른 실체가 한 노드로 뭉친다."""
    for s in ["글로벌 대형기업", "국내 대형사", "해외 법인", "아시아지역 선주"]:
        assert cp.classify(s) == "vague"
```

- [ ] **Step 2: 실패를 확인한다**

Run: `C:\Users\Juhwankim\miniconda3\python.exe -m pytest tests/test_counterparty.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'argus.corpus.counterparty'`

- [ ] **Step 3: 모듈을 구현한다 — 새로 쓰지 말고 옮긴다**

🔴 **`scripts/validate_evalset.py::normalize_counterparty` 를 먼저 읽고 그 규칙을 그대로 옮긴다.**
그 함수는 변이 5유형을 실제 데이터로 확인해 만든 것이고 `tests/test_evalset.py::COUNTERPARTY_VARIANTS`
가 고정하고 있다. **다시 발명하면 두 정규화가 갈려서 문항 검증(C12)과 런타임이 어긋난다.**

옮긴 뒤 `validate_evalset.py` 가 `argus.corpus.counterparty.canonical` 을 import 하도록 바꾸고,
**`COUNTERPARTY_VARIANTS` 테스트가 그대로 통과하는지 확인**한다. 아래는 뼈대이며
실제 정규식은 위 파일에서 가져온다.

```python
# src/argus/corpus/counterparty.py
"""계약 상대(counterparty) 문자열의 정규화와 분류.

노드로 승격할 수 있는 것과 없는 것을 가른다. 비실명(`"글로벌 대형기업"`)을
노드로 만들면 서로 다른 실체가 한 노드로 뭉쳐 그래프가 오염된다.
"""
from __future__ import annotations

import re

from argus.corpus.manifest import load_universe

_VAGUE = re.compile(
    r"글로벌|국내\s*[대상]|비공개|영업비밀|유보|미공개"
    r"|해외\s*(?:법인|기업|업체)|(?:지역|소재)\s*(?:선주|선사|고객)"
)
_SUFFIX = re.compile(
    r"\(주\)|주식회사|㈜|Co\.?,?|Ltd\.?|Inc\.?|Corp(oration)?\.?|LLC|S\.?A\.?", re.I
)
_OVERSEAS = re.compile(r"[A-Za-z]{3,}")

PUBLIC_HINTS = frozenset(
    {"방위사업청", "한국수력원자력", "한국중부발전", "한국남부발전", "한국동서발전",
     "한국서부발전", "한국남동발전", "한국전력공사", "한국가스공사", "조달청",
     "국방부", "산업통상자원부", "한국도로공사", "한국철도공사"}
)


def canonical(s: str) -> str:
    """표기 변이를 하나로 모은다. 비교 전용이며 표시용이 아니다."""
    s = re.sub(r"\s+", "", s)
    s = _SUFFIX.sub("", s)
    s = re.sub(r"^대한민국", "", s)
    return s.strip(" .,-").lower()


def classify(s: str) -> str:
    """`universe` | `public` | `overseas` | `vague` | `other`."""
    if _VAGUE.search(s):
        return "vague"
    k = canonical(s)
    if any(canonical(h) == k for h in PUBLIC_HINTS):
        return "public"
    if any(canonical(r["corp_name"]) == k for r in load_universe()):
        return "universe"
    if _OVERSEAS.search(s):
        return "overseas"
    return "other"
```

- [ ] **Step 4: 테스트를 통과시킨다**

Run: `C:\Users\Juhwankim\miniconda3\python.exe -m pytest tests/test_counterparty.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: import 를 단일 원천으로 모은다**

두 곳의 import 를 새 모듈로 갈아끼운다:

- `scripts/eda_counterparty.py` — `from scripts.validate_evalset import ...` →
  `from argus.corpus.counterparty import canonical, classify`
- `scripts/validate_evalset.py` — `normalize_counterparty` 의 **본체를 지우고**
  `from argus.corpus.counterparty import canonical` 를 감싼 얇은 별칭으로 남긴다
  (`tests/test_evalset.py::COUNTERPARTY_VARIANTS` 가 그 이름을 쓰므로 이름은 유지)

**재실행해 값이 안 바뀌는지 확인한다** — `unique_canonical` 378 · 노드 후보 157 · 중개 25.
바뀌면 정규화가 갈린 것이므로 되돌린다.

```bash
C:\Users\Juhwankim\miniconda3\python.exe scripts/eda_counterparty.py
C:\Users\Juhwankim\miniconda3\python.exe -m pytest tests/ -q
```
Expected: 576 + 6 = **582 passed**

- [ ] **Step 6: 커밋**

```bash
git add src/argus/corpus/counterparty.py scripts/eda_counterparty.py tests/test_counterparty.py
git commit -m "feat: counterparty 정규화·분류 — 비실명은 노드로 승격하지 않는다"
```

---

## Task 3: `counterparty` 2-hop 그래프 빌드

**Files:**
- Create: `scripts/build_counterparty_graph.py`
- Create: `artifacts/counterparty_graph.json`
- Test: `tests/test_counterparty.py` (추가)

**Interfaces:**
- Consumes: `counterparty.canonical()` · `counterparty.classify()` (Task 2)
- Produces: `artifacts/counterparty_graph.json` — 스키마:
  ```json
  {"nodes": [{"canonical": "방위사업청", "display": "방위사업청", "kind": "public",
              "count": 78, "partners": ["한화오션", "..."]}],
   "pairs": [{"a": "한화오션", "b": "HD현대중공업", "via": ["방위사업청"]}],
   "stats": {"nodes": 180, "pairs": 40, "min_count": 2}}
  ```

**🔴 겨냥점이 실측으로 특정됐다.** 밸류체인 문항 12건을 **현재 시스템에 LLM 0회로 던진 결과**:

```
기업을 이미 알고 검색 (상한선)   Recall@5 74.4%
허브명으로만 검색 (실제 조건)    Recall@5 50.0%  ·  Recall@20 80.0%
```

**개별 근거는 꽤 닿는다. 못 하는 것은 완결성이다.**

| 판정 | 문항 | 왜 |
|---|---|---|
| 🟡 풀릴 수 있음 | 8건 (Q-107·108·109·111·112·113·115·118) | 근거 전부 도달 |
| 🟡 확인 불가 | Q-116 | 근거는 닿지만 **"이 셋이 전부인가"** 를 확인할 수단 없음 |
| 🔴 **구조적 불가** | Q-110(3/6) · Q-114(2/5) · Q-117(3/5) | **fan-in/fan-out 완전 열거** |

> **실패 원인은 검색 품질이 아니라 "허브에 몇 개사가 걸렸는가"를 물을 수단이 없다는 것이다.**
> 그래서 이 그래프는 순위를 올리는 물건이 아니라 **집합의 경계를 긋는 물건**이다.
> `T4` 의 `query_by_counterparty` 가 `partners` 를 통째로 돌려주는 이유가 이것이다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
# tests/test_counterparty.py 에 추가
def test_graph_excludes_singletons_and_vague():
    """1회 등장(56.8%)과 비실명은 노드가 되지 않는다."""
    g = json.loads((ROOT / "artifacts/counterparty_graph.json").read_text(encoding="utf-8"))
    assert g["stats"]["min_count"] == 2
    for n in g["nodes"]:
        assert n["count"] >= 2
        assert n["kind"] != "vague"


def test_graph_reproduces_measured_value_chains():
    """실측으로 확인된 밸류체인이 그대로 나와야 한다."""
    g = json.loads((ROOT / "artifacts/counterparty_graph.json").read_text(encoding="utf-8"))
    by_canon = {n["canonical"]: set(n["partners"]) for n in g["nodes"]}

    from argus.corpus.counterparty import canonical
    khnp = by_canon[canonical("한국수력원자력")]
    assert {"두산에너빌리티", "한전기술", "현대건설"} <= khnp

    dapa = by_canon[canonical("방위사업청")]
    assert {"한화오션", "HD현대중공업", "한국항공우주"} <= dapa
    # 변이 5조각이 합쳐져야 91건이 된다 — 안 합치면 어느 조각도 허브가 못 된다
    assert next(n for n in g["nodes"] if n["canonical"] == canonical("방위사업청"))["count"] >= 80


def test_pairs_record_the_intermediary():
    """어느 발주처를 거쳐 이어졌는지 남아야 근거로 인용할 수 있다."""
    g = json.loads((ROOT / "artifacts/counterparty_graph.json").read_text(encoding="utf-8"))
    assert g["stats"]["pairs"] == 44        # 직접 20쌍 → 2-hop 44쌍 (실측)
    assert g["stats"]["nodes"] == 145       # 아래 정정 참조
    for p in g["pairs"]:
        assert p["via"], f"중개 노드가 비어 있다: {p}"


def test_graph_matches_the_evalset_normalizer():
    """문항 검증(C12)과 런타임이 같은 정규화를 써야 한다.

    갈리면 `validate_evalset` 이 통과시킨 문항을 파이프라인이 못 찾는다.
    """
    import scripts.validate_evalset as V
    from argus.corpus.counterparty import canonical
    for s in ("대한민국 방위사업청", "㈜한국수력원자력", "주식회사 유토개발2차",
              "한국토지주택공사(LH)", "(주)포스코이앤씨"):
        assert canonical(s) == V.normalize_counterparty(s)
```

- [ ] **Step 2: 실패를 확인한다**

Run: `C:\Users\Juhwankim\miniconda3\python.exe -m pytest tests/test_counterparty.py -v`
Expected: FAIL — `FileNotFoundError: artifacts/counterparty_graph.json`

- [ ] **Step 3: 빌더를 구현한다**

```python
# scripts/build_counterparty_graph.py
"""counterparty 를 노드로 승격한 2-hop 그래프.

직접 기업-기업 엣지는 29쌍(완결도 1.2%)으로 기각됐지만, 발주처를 중개 노드로
두면 40쌍이 되고 원전·방산 밸류체인이 드러난다. 근거로 인용하려면 '어느
발주처를 거쳤는가'(`via`)가 남아야 한다.
"""
import argparse
import collections
import json
from pathlib import Path

from argus.corpus.counterparty import canonical, classify

ROOT = Path(__file__).resolve().parents[1]
MIN_COUNT = 2  # 1회 등장 237개(56.8%)는 노드 가치가 없다


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "artifacts/counterparty_graph.json"))
    ap.add_argument("--min-count", type=int, default=MIN_COUNT)
    args = ap.parse_args()

    recs = [
        json.loads(l)
        for l in (ROOT / "artifacts/exchange_records.jsonl").read_text(encoding="utf-8").splitlines()
        if l.strip()
    ]

    counts = collections.Counter()
    display = {}
    partners = collections.defaultdict(set)
    kinds = {}
    for r in recs:
        cp = r.get("counterparty")
        owner = r.get("corp_name")
        if not cp:
            continue
        kind = classify(cp)
        if kind == "vague":
            continue
        k = canonical(cp)
        counts[k] += 1
        kinds[k] = kind
        display.setdefault(k, cp)
        if owner:
            partners[k].add(owner)

    nodes = [
        {"canonical": k, "display": display[k], "kind": kinds[k],
         "count": c, "partners": sorted(partners[k])}
        for k, c in counts.items()
        if c >= args.min_count
    ]
    nodes.sort(key=lambda n: -n["count"])

    via = collections.defaultdict(list)
    for n in nodes:
        ps = n["partners"]
        for i in range(len(ps)):
            for j in range(i + 1, len(ps)):
                via[(ps[i], ps[j])].append(n["canonical"])
    pairs = [{"a": a, "b": b, "via": sorted(v)} for (a, b), v in sorted(via.items())]

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {"nodes": nodes, "pairs": pairs,
             "stats": {"nodes": len(nodes), "pairs": len(pairs), "min_count": args.min_count}},
            ensure_ascii=False, indent=2),
        encoding="utf-8")
    print(f"nodes {len(nodes)} · pairs {len(pairs)}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: 실행하고 테스트를 통과시킨다**

```bash
C:\Users\Juhwankim\miniconda3\python.exe scripts/build_counterparty_graph.py
C:\Users\Juhwankim\miniconda3\python.exe -m pytest tests/test_counterparty.py -v
```
Expected: PASS (9 tests). 콘솔 `nodes 180 · pairs 40` 근처.

- [ ] **Step 5: `03-graph-schema.md` 를 갱신한다**

C-0 의 기각 판정 아래에 정정 블록을 넣는다 — **"공공 21.9%는 못 쓴다"가 틀렸고,
노드로 승격하면 상위 중개 노드 6개 중 4개가 공공기관**이라는 사실, 직접 29쌍 → 2-hop 40쌍,
그리고 **여전히 완결도 1.66%라 커뮤니티 탐지는 기각 유지**라는 것.

- [ ] **Step 6: 커밋**

```bash
git add scripts/build_counterparty_graph.py artifacts/counterparty_graph.json \
        tests/test_counterparty.py docs/pipeline/03-graph-schema.md
git commit -m "feat: counterparty 2-hop 그래프 — 직접 29쌍 → 40쌍, 밸류체인 복원"
```

---

## Task 4: `query_by_counterparty` 도구

**Files:**
- Modify: `src/argus/agent/tools.py`
- Test: `tests/test_tools.py`

**Interfaces:**
- Consumes: `artifacts/counterparty_graph.json` (Task 3)
- Produces: `TOOL_SPECS` 에 `query_by_counterparty` 추가 (14 → **15종**),
  `RETRIEVAL_TOOLS` 에 등재. 시그니처:
  `query_by_counterparty(name: str, year: str | None = None) -> dict`
  반환 `{"counterparty": str, "kind": str, "partners": [str], "records": [dict]}`

**🔴 `partners` 를 통째로 돌려주는 것이 이 도구의 존재 이유다.** 밸류체인 문항 12건 중
**3건이 fan-in/fan-out 완전 열거**를 요구하는데(Q-110 3/6 · Q-114 2/5 · Q-117 3/5),
현재 BM25 자유텍스트로는 개별 근거는 닿아도 **"이 허브에 몇 개사가 걸렸는가"를 물을 수단이
없다.** 순위를 올리는 도구가 아니라 **집합의 경계를 긋는 도구**다.

`query_events` 는 계약상대 필터가 없다(`corp_name`·`doc_subtype`·`year`·`min_amount`·`is_latest`).
그래서 이 도구가 없으면 그 3건은 **구조적으로 불가**다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
# tests/test_tools.py 에 추가
from argus.agent.tools import TOOL_SPECS, RETRIEVAL_TOOLS, execute


def test_counterparty_tool_is_registered():
    names = {s.get("function", s).get("name", s.get("name")) for s in TOOL_SPECS}
    assert "query_by_counterparty" in names
    assert "query_by_counterparty" in RETRIEVAL_TOOLS
    assert len(TOOL_SPECS) == 15


def test_query_by_counterparty_returns_the_value_chain():
    out = execute("query_by_counterparty", {"name": "한국수력원자력"})
    assert out["kind"] == "public"
    assert {"두산에너빌리티", "한전기술", "현대건설"} <= set(out["partners"])
    assert out["records"], "레코드가 비면 근거로 못 쓴다"
    for r in out["records"]:
        assert r.get("rcept_no"), "근거 인용 앵커가 없다"


def test_query_by_counterparty_rejects_vague_names():
    """비실명으로 조회하면 빈 결과가 아니라 사유를 돌려준다."""
    out = execute("query_by_counterparty", {"name": "글로벌 대형기업"})
    assert out.get("error")
    assert "비실명" in out["error"]
```

- [ ] **Step 2: 실패를 확인한다**

Run: `C:\Users\Juhwankim\miniconda3\python.exe -m pytest tests/test_tools.py -k counterparty -v`
Expected: FAIL — `assert "query_by_counterparty" in names`

- [ ] **Step 3: 도구를 구현한다**

`tools.py` 에 함수와 스펙을 추가한다. 기존 도구들이 쓰는 로딩·오류 반환 패턴을 그대로 따른다
(`execute()` 가 `KeyError`/`OSError`/`TypeError`/`ValueError` 를 잡아 `{"error": ...}` 로 만든다).

```python
def query_by_counterparty(name: str, year: str | None = None) -> dict:
    """발주처 이름으로 그 발주처와 계약한 universe 기업들의 레코드를 모은다."""
    from argus.corpus.counterparty import canonical, classify

    kind = classify(name)
    if kind == "vague":
        return {"error": f"비실명 상대('{name}')는 노드가 아니라 조회할 수 없습니다."}

    graph = _load_counterparty_graph()
    k = canonical(name)
    node = next((n for n in graph["nodes"] if n["canonical"] == k), None)
    if node is None:
        return {"error": f"'{name}' 은(는) 2회 이상 등장하는 발주처가 아닙니다."}

    recs = [
        r for r in _load_exchange_records()
        if r.get("counterparty") and canonical(r["counterparty"]) == k
        and (year is None or str(r.get("rcept_dt", "")).startswith(year))
    ]
    return {"counterparty": node["display"], "kind": node["kind"],
            "partners": node["partners"], "records": recs}
```

스펙(JSON Schema)은 기존 검색 도구 7종과 같은 형식으로 쓰고 `RETRIEVAL_TOOLS` 에 이름을 넣는다.

- [ ] **Step 4: 테스트를 통과시킨다**

```bash
C:\Users\Juhwankim\miniconda3\python.exe -m pytest tests/test_tools.py -v
C:\Users\Juhwankim\miniconda3\python.exe -m pytest tests/ -q
```
Expected: 전체 **585 passed** (582 + 3)

- [ ] **Step 5: 커밋**

```bash
git add src/argus/agent/tools.py tests/test_tools.py
git commit -m "feat: query_by_counterparty 도구 — 발주처 단위 조회 (Tool 14 → 15종)"
```

---

## Task 5: 발주처 질의 라우팅과 2-hop 근거 수집

**Files:**
- Modify: `src/argus/agent/pipeline.py`
- Modify: `src/argus/agent/router.py`
- Test: `tests/test_pipeline.py` · `tests/test_router.py`

**Interfaces:**
- Consumes: `query_by_counterparty` (Task 4) · `counterparty_graph.json` (Task 3)
- Produces: `think_trace` 에 `strat=counterparty via=<canonical> partners=<n>` 표식.
  `pipeline.parse_hops()` 가 기존과 같은 키로 되읽는다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
# tests/test_pipeline.py 에 추가
from argus.agent.pipeline import _gather_evidence, parse_hops
from argus.agent.router import route
from argus.agent.safety import sanitize_question


def test_counterparty_query_routes_to_the_graph():
    q, _ = sanitize_question("한국수력원자력이 발주한 계약을 수주한 기업들의 계약금액을 비교해줘")
    r = route(q)
    assert r.counterparty == "한국수력원자력"


def test_counterparty_hop_pulls_multiple_partners_into_evidence():
    q, _ = sanitize_question("한국수력원자력이 발주한 계약을 수주한 기업들의 계약금액을 비교해줘")
    ev, trace = _gather_evidence(route(q), q)
    hops = parse_hops(trace)
    assert any(h["strategy"] == "counterparty" for h in hops)
    # 한 기업만 오면 비교가 성립하지 않는다
    assert sum(c in ev for c in ("두산에너빌리티", "한전기술", "현대건설")) >= 2


def test_ordinary_queries_do_not_trigger_the_counterparty_hop():
    """발주처 질의가 아닌데 2-hop 이 돌면 근거가 오염된다."""
    q, _ = sanitize_question("삼성SDI가 2025년 3월에 결의한 유상증자의 총 조달 규모는?")
    _, trace = _gather_evidence(route(q), q)
    assert all(h["strategy"] != "counterparty" for h in parse_hops(trace))
```

- [ ] **Step 2: 실패를 확인한다**

Run: `C:\Users\Juhwankim\miniconda3\python.exe -m pytest tests/test_pipeline.py -k counterparty -v`
Expected: FAIL — `AttributeError: 'Route' object has no attribute 'counterparty'`

- [ ] **Step 3: 라우터와 파이프라인을 고친다**

`Route` 에 `counterparty: str | None = None` 을 더한다. 라우터는 **그래프 노드 이름과의 일치**로만
판정한다(정규식 추측 금지) — `counterparty_graph.json` 의 `nodes[].display` · `canonical` 에
질문 문자열이 포함되는지 본다. `_gather_evidence` 는 `route.counterparty` 가 있을 때만
`query_by_counterparty` 를 hop 0 에서 부르고, 파트너별 근거 할당량을 둔다.

**할당량은 기존 제동과 같은 방식으로 정한다** — 근거 칸이 한정돼 있으므로 파트너 수로 나눈다.
기존 제동 8종(`global` 생략 · 잡음 바닥 · 코퍼스 밖 연도 · 체인 씨앗 2 · 체인 문서 6 ·
분해는 시점 축 2개 이상 · 근거 도착 순서 유지 · 예산 초과 블록 건너뛰기)을 **없애지 않는다**.

- [ ] **Step 4: 테스트를 통과시키고 회귀를 확인한다**

```bash
C:\Users\Juhwankim\miniconda3\python.exe -m pytest tests/ -q
```
Expected: **588 passed**. 그리고 LLM 0회 회귀:

```bash
C:\Users\Juhwankim\miniconda3\python.exe scripts/error_analysis.py --retrieval-only --cohort measured
```
Expected: 골든 근거 적중 **59/60** · Recall@5 **58/60** · 스텝 최대 **≤ 8** — 하나라도 떨어지면 되돌린다.

- [ ] **Step 5: 커밋**

```bash
git add src/argus/agent/pipeline.py src/argus/agent/router.py \
        tests/test_pipeline.py tests/test_router.py
git commit -m "feat: 발주처 2-hop 근거 수집 — 밸류체인 질의 경로"
```

---

## Task 6: Phase A 부분 재실행 — 신규 25개사 프로파일

**Files:**
- Create: `scripts/eda_new_sectors.py`
- Create: `artifacts/eda/new_sector_profile.json`
- Modify: `docs/pipeline/01-eda.md`
- Test: `tests/test_eda_new_sectors.py`

**Interfaces:**
- Consumes: `data/dart-disclosures/universe.csv` · `manifest.jsonl` · `artifacts/*_records.jsonl`
- Produces: `artifacts/eda/new_sector_profile.json` — 섹터별 `schema_matched` 비율 · 표 문자비 ·
  숫자 토큰비 · 정정체인 연결률

**배경**: 평가셋 확장으로 **6섹터 25개사**(게임·로봇·소비재유통·자동차모빌리티·철강·통신)가
새로 들어왔는데 **그 문서들의 프로파일을 본 적이 없다**. 확장 트랙이 그 과정에서 이미
정규화 결함 4건(NAVER 체인 `amount` 정지 · 크래프톤 `ratio` 1.0 · 이마트 `amount` · 아모레 매출액)을 찾았다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
# tests/test_eda_new_sectors.py
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NEW_SECTORS = {"게임", "로봇", "소비재·유통", "자동차·모빌리티", "철강", "통신"}


def test_all_new_sectors_are_profiled():
    prof = json.loads(
        (ROOT / "artifacts/eda/new_sector_profile.json").read_text(encoding="utf-8"))
    assert NEW_SECTORS <= set(prof["by_sector"])


def test_profile_records_schema_matched_per_sector():
    """섹터별 파싱 커버리지가 기존 14섹터와 다르면 그게 다음 처방이다."""
    prof = json.loads(
        (ROOT / "artifacts/eda/new_sector_profile.json").read_text(encoding="utf-8"))
    for s, v in prof["by_sector"].items():
        assert "schema_matched_ratio" in v
        assert "n_docs" in v
        assert 0.0 <= v["schema_matched_ratio"] <= 1.0


def test_known_defects_are_listed():
    """확장 트랙이 찾은 결함 4건이 기록돼 있어야 다음 트랙이 안 놓친다."""
    prof = json.loads(
        (ROOT / "artifacts/eda/new_sector_profile.json").read_text(encoding="utf-8"))
    corps = {d["corp_name"] for d in prof["known_defects"]}
    assert {"NAVER", "크래프톤", "이마트", "아모레퍼시픽"} <= corps
```

- [ ] **Step 2: 실패를 확인한다**

Run: `C:\Users\Juhwankim\miniconda3\python.exe -m pytest tests/test_eda_new_sectors.py -v`
Expected: FAIL — `FileNotFoundError: artifacts/eda/new_sector_profile.json`

- [ ] **Step 3: 프로파일러를 구현한다**

`scripts/eda_documents.py` 의 측정 함수를 재사용한다(중복 구현 금지). 섹터 필터만 씌워
`schema_matched` 비율 · 표 문자비 · 숫자 토큰비 · 정정체인 연결률을 낸다.
`known_defects` 에는 아래 4건을 값과 함께 박는다:

```python
KNOWN_DEFECTS = [
    {"corp_name": "NAVER", "kind": "chain_amount_frozen",
     "detail": "왈라팝 3본 체인의 amount 가 원공시 903,559,577,175 에 멈춤 (본문 최신 902,863,854,765)"},
    {"corp_name": "크래프톤", "kind": "ratio_wrong",
     "detail": "정정본 ratio 가 1.0 (본문 2.93%)"},
    {"corp_name": "이마트", "kind": "amount_truncated",
     "detail": "정정본 amount 1조 (본문 '1조 1,500억원')"},
    {"corp_name": "아모레퍼시픽", "kind": "internal_inconsistency",
     "detail": "최근매출액이 같은 문서 안에서 100만원 어긋남"},
]
```

- [ ] **Step 4: 실행하고 테스트를 통과시킨다**

```bash
C:\Users\Juhwankim\miniconda3\python.exe scripts/eda_new_sectors.py
C:\Users\Juhwankim\miniconda3\python.exe -m pytest tests/ -q
```
Expected: **591 passed**

- [ ] **Step 5: `01-eda.md` 에 결과와 판정을 쓴다**

신규 6섹터의 `schema_matched` 가 기존 대비 **5%p 이상 낮으면** 그 섹터를 다음 파싱 트랙의
겨냥점으로 명시한다. 차이가 없으면 **없다고 쓴다** — 그것도 결과다.

- [ ] **Step 6: 커밋**

```bash
git add scripts/eda_new_sectors.py artifacts/eda/new_sector_profile.json \
        tests/test_eda_new_sectors.py docs/pipeline/01-eda.md
git commit -m "eda: 신규 6섹터 25개사 프로파일 — Phase A 부분 재실행"
```

---

## Task 7: 코호트 인자화 + Phase H 재실행 (106문항)

**Files:**
- Modify: `scripts/error_analysis.py`
- Modify: `docs/pipeline/08-error-analysis.md`
- Test: `tests/test_error_analysis.py`

**Interfaces:**
- Consumes: `validate_evalset.measured_dev_cohort()` (기존)
- Produces: `--cohort {measured,full}` 인자. `artifacts/eval/error_analysis_v2.json`
  (기존 `error_analysis.json` 을 덮어쓰지 않는다 — v1 비교 기준선이다)

**🔴 크레딧**: 상한 **3,000원**. dev 78문항 + 회귀 10 = 88문항 ≈ **1,980원** 예상.
**5문항 스모크로 단가를 먼저 확인**하고, 추정치가 상한을 넘으면 진행하지 않고 보고한다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
# tests/test_error_analysis.py 에 추가
def test_cohort_flag_selects_the_question_set():
    """--cohort measured 는 42문항, full 은 88문항(dev 78 + 회귀 10)."""
    measured = ea.load_cohort("measured")
    full = ea.load_cohort("full")
    assert len(measured) == 42
    assert len(full) == 88
    assert {q["question_id"] for q in measured} <= {q["question_id"] for q in full}


def test_v1_artifact_is_not_overwritten():
    """error_analysis.json 은 v1 비교 기준선이라 덮으면 안 된다."""
    import inspect
    src = inspect.getsource(ea)
    assert "error_analysis_v2.json" in src
```

- [ ] **Step 2: 실패를 확인한다**

Run: `C:\Users\Juhwankim\miniconda3\python.exe -m pytest tests/test_error_analysis.py -k cohort -v`
Expected: FAIL — `AttributeError: module has no attribute 'load_cohort'`

- [ ] **Step 3: 코호트 인자를 구현한다**

`load_cohort(name)` 을 만들고 `--cohort` · `--out` 인자를 붙인다. 기본값은 `measured` 로 두어
**기존 호출부가 안 깨지게** 한다.

- [ ] **Step 4: 스모크 5문항으로 비용을 확인한다**

```bash
C:\Users\Juhwankim\miniconda3\python.exe scripts/error_analysis.py --cohort full --limit 5 \
  --out artifacts/eval/_smoke_v2.json
```
5문항 실측 × 17.6 = 전체 추정. **3,000원을 넘으면 여기서 멈추고 보고한다.**

- [ ] **Step 5: 전수 실행하고 3축 교차집계를 낸다**

```bash
C:\Users\Juhwankim\miniconda3\python.exe scripts/error_analysis.py --cohort full \
  --out artifacts/eval/error_analysis_v2.json
```

`DATA_LIMIT` 을 분모에서 분리하고 `HALLUCINATION` 을 별도 집계한다(기존 규칙 유지).
**회귀 트랙 10문항은 정답률에 섞지 않는다** — 별도 행으로 낸다.

- [ ] **Step 6: `08-error-analysis.md` 에 v2 결과를 쓴다**

**v1(42문항)과 v2(88문항)를 나란히** 놓고, 실패 단계 분포가 어떻게 달라졌는지 쓴다.
신규 6섹터에서 `RETRIEVAL` 실패가 **3건 이상**이면 **리랭커 재검토 조건이 발동**한다고 명시한다.

- [ ] **Step 7: 커밋**

```bash
git add scripts/error_analysis.py tests/test_error_analysis.py \
        artifacts/eval/error_analysis_v2.json docs/pipeline/08-error-analysis.md
git commit -m "measure: Phase H 재실행 (88문항) — v1 42문항 기준선과 병기"
```

---

## Task 8: 게이트 D 재측정 (LLM 0회)

**Files:**
- Modify: `docs/pipeline/04-retriever.md`
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: Task 7 의 `load_cohort("full")`
- Produces: 게이트 D 줄이 **어느 코호트·어느 시점** 기준인지 명시된 형태

**배경**: 게이트 D 의 `70/93 (0.753)` 이 **모집단(heldout 혼입)과 시점 둘 다 틀렸던** 전례가 있다.
같은 실수를 반복하지 않으려면 수치에 코호트 라벨이 붙어야 한다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
def test_gate_d_line_carries_a_cohort_label():
    """모집단·시점이 안 적힌 게이트 수치는 낡는다 — 70/93 사건의 재발 방지."""
    text = (ROOT / "docs/pipeline/04-retriever.md").read_text(encoding="utf-8")
    line = next(l for l in text.splitlines() if "Recall@5" in l and l.startswith("- ["))
    assert "코호트" in line or "cohort" in line
    assert "2026-" in line, "측정 시점이 없으면 다음에 또 낡는다"
```

- [ ] **Step 2: 실패를 확인한다**

Run: `C:\Users\Juhwankim\miniconda3\python.exe -m pytest tests/test_pipeline.py -k gate_d -v`
Expected: FAIL — `assert "코호트" in line`

- [ ] **Step 3: LLM 0회로 재측정한다**

```bash
C:\Users\Juhwankim\miniconda3\python.exe scripts/error_analysis.py \
  --cohort full --retrieval-only --out artifacts/eval/recall_v2.json
```

- [ ] **Step 4: 게이트 줄을 갱신한다**

형식: `- [x] Recall@5 ≥ 0.85 — **full 코호트 88문항 / 2026-08-xx** · 골든 근거 xx/xxx (0.xxx)`
난이도별 3행도 같은 코호트로 갱신한다. **42문항 코호트 값은 지우지 말고 이력으로 남긴다.**

- [ ] **Step 5: 테스트를 통과시키고 커밋한다**

```bash
C:\Users\Juhwankim\miniconda3\python.exe -m pytest tests/ -q
git add docs/pipeline/04-retriever.md tests/test_pipeline.py artifacts/eval/recall_v2.json
git commit -m "measure: 게이트 D 재측정 (88문항) — 수치에 코호트·시점 라벨 부착"
```

---

## Task 9: Ablation v2

**Files:**
- Modify: `src/argus/ablation.py` (arm `A8` 추가)
- Modify: `scripts/ablation.py` (코호트 인자)
- Create: `artifacts/ablation/v2/`
- Modify: `docs/pipeline/09-ablation.md`
- Test: `tests/test_ablation.py`

**Interfaces:**
- Consumes: Task 5 의 `counterparty` hop · Task 7 의 `load_cohort`
- Produces: `artifacts/ablation/v2/gate_I.md` · `summary.json`

## 🔴 T1~T8 실행 후 확정된 사항 — v2 착수 전 반드시 반영

### ① arm 겨냥 배분이 바뀌었다 — "A6 가 새 1위" 는 **폐기**

Task 7 이 오답 13건을 **근거 원문까지 대조**해 재배분했다. 코디네이터가 두 번 "A6 새 1위" 로
보고했고 **두 번 다 틀렸다.**

| arm | 겨냥 | 문항 |
|---|---:|---|
| **A4 루프·추론** | **5** | Q-023 · Q-076 · Q-098 · Q-105 (+회귀 Q-066) |
| **A5 계산·단위 정규화** | **4** | Q-044 · Q-047 · Q-097 (+회귀 Q-065) |
| A1b 인덱스 필드 | 2 | Q-099 · Q-101 |
| A6 Verifier | **1** | Q-087 |
| A3 리랭커 | 1 | Q-094 |

경위: 코디네이터 "VERIFY 4건 → A6" → 리뷰어가 4건을 열어 `A5 2 · A1b 1 · A6 1` 로 갈랐고
→ 구현자가 같은 검사를 나머지에 돌려 **2건을 더 정정**(Q-098 이 A4, Q-099 는 기전이 다름).

**A5 의 4건 중 2건은 10배 단위환산 오류**다 — Q-065(`49,290억` → `492,909억`) ·
Q-047(`333조 6,059억원` → `33,636,059억원`). *"억원 반올림"* 이라는 옛 진단은 틀렸다.
반올림을 손봐도 안 없어진다.

**Q-099 는 골든이 맞고 우리 추출이 틀렸다** — T6 의 `known_defects` `NAVER chain_amount_frozen`
과 같은 건이다(정정 체인이 `amount` 를 최신본으로 갱신 못 함). Q-101 의 "필드 부재" 와 기전이 다르다.

### ② 🔴 A8 을 **BM25 이후 단계**에 세워라

`A0` 는 `uses_bm25=False` 조기 반환이 **counterparty hop 앞에** 있다. 그래서
`A0 + counterparty_hop=True` 로 강제해도 **발화가 0** 이다(실측).

**A8 을 meta 단에 세우면 조용히 Δ=0 이 나오고, T1~T5 작업 전체가 "효과 없음" 으로 보고된다.**

`Arm.counterparty_hop` 은 `hop_ladder` 로부터 **독립**임이 검증됐다
(`A1b + cp=True`, ladder off 에서 발화 확인). 따라서 `A8 = A7 + counterparty_hop`,
`L3 = A8 − counterparty_hop` 이 성립한다.

### ③ `measured 42` 는 **재생(replay)** 이지 독립 재측정이 아니다

Task 7 의 `measured 42` 는 요청 캐시로 서빙됐다 — **답변 42/42 가 A7 과 바이트 동일**,
토큰 합계 167,914 전 항목 일치. 온도 0.0 하에 타당하나 **새로 얻은 정보는
"현재 코드가 그 42문항에 A7 과 동일한 요청을 만든다"** 는 사실뿐이다.

v1 비교축으로만 쓰고, **"현재 시스템의 독립 측정" 으로 서술하지 마라.**

### ④ 입력이 버전관리 밖에 있다

`artifacts/` 가 `.gitignore` 대상이라 **v2 의 입력(`error_analysis_v2*.json` · `recall_v2.json`)이
로컬 워킹트리에만 존재**한다. 실행 전에 그 파일들이 있는지 확인하고, 없으면
`scripts/error_analysis.py --cohort full` 부터 다시 돌려야 한다(크레딧 재지출).

---

**🔴 A8 의 성공 기준은 정답률이 아니라 완결성이다.**

밸류체인 12문항 중 **8건은 A8 없이도 근거가 닿습니다**(BM25 자유텍스트, Recall@5 50%·@20 80%).
따라서 A8 의 Δ 를 전체 정답률로만 재면 **작게 나오고 오판하기 쉽다.**

**세 갈래로 나눠 보고한다:**

```
① fan-in/fan-out 완전 열거 3건   Q-110(3/6) · Q-114(2/5) · Q-117(3/5)   ← A8 의 진짜 겨냥점
② 완결성 확인 불가 1건            Q-116
③ 이미 닿는 8건                   회귀만 확인 (깨지면 순손실)
```

**①에서 안 오르면 A8 은 실패**다. ③에서 떨어지면 Δ 가 양수여도 넣지 않는다.

**arm 구성** (v1 에서 A2·A3 는 실측으로 제외 — 근거는 §I-4b):

```
A0 → A1 → A1b → A4 → A5 → A6 → A7 → A8
                                      └ counterparty 2-hop  (BM25 이후 단계에 — 위 ② 참조)
LOO   L1 = A8 − 정정체인 hop
      L2 = A8 − major·holding 필드
      L3 = A8 − counterparty_hop          (독립 플래그 검증됨)
```

**🔴 크레딧**: 상한 **20,000원**. **80%(16,000원) 도달 시 중단.**

Task 7 실측으로 단가가 갱신됐다:

```
88문항 1회 = 997.2원  (요청 캐시 74/147 미적중)
캐시 없는 arm 1개 ≈ 2,000원

9구성   캐시 없으면  ≈ 18,000원
        캐시 감안     ≈ 8,000 ~ 12,000원
```

v1 에서 요청 캐시가 4,873원을 막았고, **생성 프롬프트가 같은 arm(A5·A6 등)은 사실상 공짜**로
측정된다. `scripts/ablation.py` 의 `RequestCache` 를 **그대로 쓰라** — Task 7 이 복제하지 않고
붙여서 58% 를 아꼈다.

**arm 마다 결과를 즉시 디스크에 저장**하고, `A8 → A0` 역순으로 돌려 중간에 끊겨도
최신 성능을 확보한다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
# tests/test_ablation.py 에 추가
from argus import ablation


def test_a8_arm_exists_and_toggles_the_counterparty_hop():
    assert "A8" in ablation.ARMS
    with ablation.use("A8"):
        assert ablation.enabled("counterparty_hop")
    with ablation.use("A7"):
        assert not ablation.enabled("counterparty_hop")


def test_l3_removes_only_the_counterparty_hop():
    """LOO 는 한 요소만 빼야 Δ 귀속이 가능하다."""
    a8 = ablation.flags("A8")
    l3 = ablation.flags("L3")
    diff = {k for k in a8 if a8[k] != l3[k]}
    assert diff == {"counterparty_hop"}
```

- [ ] **Step 2: 실패를 확인한다**

Run: `C:\Users\Juhwankim\miniconda3\python.exe -m pytest tests/test_ablation.py -k a8 -v`
Expected: FAIL — `KeyError: 'A8'`

- [ ] **Step 3: arm 을 배선한다**

`ablation.py` 의 기존 7플래그 옆에 `counterparty_hop` 을 더하고 `A8` · `L3` 를 정의한다.
`tests/test_ablation.py` 의 기존 28개가 검증하는 방식대로 — **끈 것이 실제로 꺼졌는지**를
`think_trace` 로 확인하는 테스트를 A8 에도 붙인다(`strat=counterparty` 흔적이 없어야 한다).

- [ ] **Step 4: 인덱스 스냅샷을 찍고 v2 를 돌린다**

```bash
C:\Users\Juhwankim\miniconda3\python.exe scripts/ablation.py --cohort full \
  --arms A8,L1,L2,L3,A7,A6,A5,A4,A1b,A1,A0 --out artifacts/ablation/v2 --budget 20000
```

**A8 → A0 역순**으로 돈다. 가장 비싼 구성을 먼저 확보해야 중간에 끊겨도 최신 성능을 안다.
**arm 마다 결과를 즉시 디스크에 저장**한다.

- [ ] **Step 5: 축별 분해와 논문 대조를 낸다**

난이도(하/중/상) · 답변형식(closed/open) · 과제유형(se/mc/cr) 3표.
`multi_compare × closed` 는 v1 에서 변별력 경고가 있었으므로 **셀 n 이 늘어난 지금 다시 확인**한다.
부록 D 의 논문 효과 크기와 대조한다.

- [ ] **Step 6: 게이트 I 를 갱신하고 제출 구성을 결정한다**

성능/비용 곡선에서 **꺾이는 지점**을 특정한다. `A8` 의 Δ 가 **0 이면 0 이라고 쓰고 제출 구성에서 뺀다** —
A2 를 뺀 것과 같은 기준이다.

- [ ] **Step 7: 커밋**

```bash
git add src/argus/ablation.py scripts/ablation.py tests/test_ablation.py \
        artifacts/ablation/v2 docs/pipeline/09-ablation.md
git commit -m "measure: Ablation v2 (88문항, A8 counterparty 추가 + LOO 3구성)"
```

---

## Task 10: heldout 최종 측정 + total_plan 갱신

**Files:**
- Modify: `docs/total_plan.md`
- Create: `artifacts/ablation/v2/heldout/`

**Interfaces:**
- Consumes: Task 9 의 최종 구성
- Produces: 일반화 낙폭

**🔴 이 태스크에서만 `evalset/heldout.jsonl` 을 연다.** 상한 **1,000원**.
**최종 구성 1회만** — arm 별로 돌리지 않는다. v1 에서 A6 로 1회 썼으므로 **이번이 2회째**이고,
그 사실을 문서에 명시한다.

- [ ] **Step 1: 최종 구성으로 heldout 을 1회 돌린다**

```bash
C:\Users\Juhwankim\miniconda3\python.exe scripts/ablation.py --split heldout \
  --arms <Task 9 에서 결정된 최종 구성> --out artifacts/ablation/v2/heldout --budget 1000
```

- [ ] **Step 2: 낙폭을 기록한다**

v1 낙폭은 **−2.8%p**(dev 86.1% → heldout 83.3%)였다. v2 낙폭이 **−10%p 이상 벌어지면**
dev 과적합 신호이므로 `09-ablation.md` §I-7 에 원인 분석 항목을 추가한다.

- [ ] **Step 3: `total_plan.md` §0 을 갱신한다**

성능 · 게이트 총계 · 누적 지출 · 테스트 수를 v2 값으로. **v1 값은 지우지 말고 병기**한다 —
"측정이 계획을 뒤집은" 이력이 이 프로젝트의 산출물이다.

- [ ] **Step 4: 커밋**

```bash
git add docs/total_plan.md artifacts/ablation/v2/heldout
git commit -m "measure: heldout 최종 1회 (v2) + total_plan 갱신"
```

---

## 이 계획에 없는 것

| 항목 | 왜 |
|---|---|
| **배포·서빙** | 별도 계획. 인덱스 1.2GB 전송 · swap 0B · HTTPS 외부 의존이라 성격이 다르다 |
| **기술제안서** | 병행 트랙에서 작성 중 |
| A2 임베딩 | 실측 Δ 0 (dense 가 31건 올렸는데 골든 0건). `DENSE_ARMS` 두 줄로 격리돼 있다 |
| A3 리랭커 | 겨냥점 1건, Δ 상한 2.8%p. **단 Task 7 에서 `RETRIEVAL` 실패 3건 이상이면 재검토 발동** |
| T1 n-ary LLM 추출 | 약 6만원, 별도 계획. 현재 금지 |
| 정규화 결함 4건 수정 | Task 6 이 **기록**만 한다. 수정은 다음 파싱 트랙 |

---

## Self-Review

**1. 스펙 커버리지**

| 요구 | 태스크 |
|---|---|
| 밸류체인 문항이 요구하는 능력 | T1~T5 |
| Phase A 재실행 필요성 | T6 |
| Phase H 재측정 (106문항) | T7 |
| 게이트 D 재측정 | T8 |
| Ablation v2 | T9 |
| heldout · 총괄 갱신 | T10 |
| 리랭커 재검토 조건 | T7 Step 6 에 발동 조건 명시 |

**2. 플레이스홀더 스캔** — `TBD`·`적절히`·`유사하게` 없음. T5 Step 3 과 T9 Step 3 은 코드 대신
설계 제약을 명시했다(기존 제동 8종 유지 · 끈 것이 꺼졌는지 검증). 이는 기존 파일의 패턴을
따라야 하는 부분이라 코드를 박으면 오히려 틀린다.

**3. 타입 일관성** — `canonical()`·`classify()`(T2) → `build_counterparty_graph.py`(T3) →
`query_by_counterparty()`(T4) → `Route.counterparty`(T5) → `ablation.enabled("counterparty_hop")`(T9)
로 이름이 일관된다. `load_cohort()`(T7)는 T8·T9 가 같은 이름으로 소비한다.
