# ARGUS 로컬 기반 구축 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 서버 없이 로컬에서 공시 Agent의 데이터 파이프라인·검색·API 골격을 완성해, 8월 말 배포 시 코드와 인덱스만 올리면 되는 상태를 만든다.

**Architecture:** 순수 파이썬 패키지 `src/argus/`. 코퍼스 계층(`corpus/`)은 DART HTML을 정규화 레코드 → 마크다운으로 변환하고, 검색 계층(`retrieve/`)은 BM25 인덱스를 만들며, API 계층(`api/`)은 주최 측이 고정한 `GET /answer` 스키마를 노출한다. LLM 호출은 `clova.py` 하나로 격리해 토큰·비용을 전수 기록한다.

**Tech Stack:** Python 3.12 · FastAPI · uvicorn · pytest · 표준 라이브러리 `urllib`(HCX 호출) · `rank_bm25`

## Global Constraints

- **인코딩**: DART XML은 **파일은 UTF-8인데 `<meta charset=euc-kr>`로 선언**되어 있다. 모든 파일 읽기는 `encoding='utf-8'`을 명시적으로 강제한다. `pandas.read_html`·BeautifulSoup의 자동 인코딩 탐지를 절대 신뢰하지 않는다.
- **API 스키마 고정**: 응답 JSON은 `question_id` · `question` · `retrieved_context` · `think_trace` · `answer` 5개 키. 이름·타입을 바꾸지 않는다. (`docs/CONVENTION.md` §4)
- **LLM**: 최종 답변 생성은 HyperCLOVA X 단독. 엔드포인트는 `https://clovastudio.stream.ntruss.com/testapp/v3/chat-completions/HCX-005`, 인증은 `Authorization: Bearer <key>`.
- **`tools`와 `maxTokens`는 동시 지정 불가** (HTTP 400).
- **비밀정보**: API 키는 `.env`에서만 읽는다. 코드·테스트·커밋에 절대 하드코딩하지 않는다.
- **날짜·기간 연산은 LLM에 맡기지 않는다.** 결정적 파이썬 함수로 처리한다. (실측에서 CoT로도 오답)
- **메모리 상한 2.8GiB**를 가정한다. 배포 서버 가용 메모리가 그만큼이다. 전량 메모리 적재 설계 금지.
- **코퍼스 원본(5.2GB)은 서버에 올리지 않는다.** 가공 산출물만 `artifacts/`에 생성한다.
- 커밋 메시지는 `feat:` / `test:` / `chore:` 접두사를 쓴다.

## File Structure

| 경로 | 책임 |
|---|---|
| `src/argus/config.py` | `.env` 로딩, 코퍼스·산출물 경로 해석, 상수 |
| `src/argus/clova.py` | HCX 호출 단일 창구. 토큰·지연·비용 기록 |
| `src/argus/corpus/manifest.py` | `manifest.jsonl` + `universe.csv` 로딩, 메타데이터 프리필터 |
| `src/argus/corpus/html_doc.py` | DART HTML → 제목·표 행 추출 (인코딩 강제) |
| `src/argus/corpus/normalize.py` | 거래소공시 표 → 정규화 레코드 (CONVENTION §7.3 스키마) |
| `src/argus/corpus/markdown.py` | 정규화 레코드 → LLM 입력용 마크다운 |
| `src/argus/corpus/corrections.py` | 정정공시 ↔ 원본 연결 체인 |
| `src/argus/corpus/dates.py` | 날짜·기간 결정적 연산 |
| `src/argus/retrieve/bm25.py` | BM25 인덱스 생성·검색 |
| `src/argus/api/server.py` | FastAPI `GET /answer` |
| `scripts/build_index.py` | 파이프라인 일괄 실행 CLI |
| `scripts/eval_e2e.py` | E2E 평가 하네스 |
| `deploy/setup.sh` · `deploy/Dockerfile` · `deploy/argus.service` | 배포 산출물 (제출 요건) |

---

## Task 1: 프로젝트 스캐폴딩과 설정

**Files:**
- Create: `pyproject.toml`, `src/argus/__init__.py`, `src/argus/config.py`
- Create: `tests/test_config.py`, `tests/conftest.py`

**Interfaces:**
- Consumes: 없음 (첫 태스크)
- Produces: `config.settings()` → `Settings` 데이터클래스. 필드: `repo_root: Path`, `corpus_root: Path`, `artifacts_root: Path`, `clova_api_key: str`, `clova_url: str`, `model: str`

- [x] **Step 1: 실패하는 테스트 작성**

`tests/test_config.py`:
```python
from pathlib import Path
from argus.config import settings

def test_settings_resolves_repo_root():
    s = settings()
    assert (s.repo_root / "pyproject.toml").exists()

def test_settings_points_at_corpus():
    s = settings()
    assert s.corpus_root.name == "dart-disclosures"

def test_clova_url_is_v3_hcx005():
    s = settings()
    assert s.clova_url.endswith("/testapp/v3/chat-completions/HCX-005")
```

- [x] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'argus'`

- [x] **Step 3: pyproject.toml 작성**

```toml
[project]
name = "argus"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = ["fastapi>=0.115", "uvicorn>=0.32", "rank-bm25>=0.2.2"]

[project.optional-dependencies]
dev = ["pytest>=8.0", "httpx>=0.27"]

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
pythonpath = ["src"]
testpaths = ["tests"]
```

- [x] **Step 4: config.py 구현**

`src/argus/config.py`:
```python
"""설정 단일 출처. 비밀정보는 .env 에서만 읽는다."""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

CLOVA_HOST = "https://clovastudio.stream.ntruss.com"
MODEL = "HCX-005"
CLOVA_PATH = f"/testapp/v3/chat-completions/{MODEL}"


def _find_repo_root(start: Path | None = None) -> Path:
    here = (start or Path(__file__)).resolve()
    for base in [here, *here.parents]:
        if (base / "pyproject.toml").exists():
            return base
    raise RuntimeError("pyproject.toml 을 찾지 못했습니다")


def _load_dotenv(root: Path) -> None:
    path = root / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


@dataclass(frozen=True)
class Settings:
    repo_root: Path
    corpus_root: Path
    artifacts_root: Path
    clova_api_key: str
    clova_url: str
    model: str


@lru_cache(maxsize=1)
def settings() -> Settings:
    root = _find_repo_root()
    _load_dotenv(root)
    artifacts = root / "artifacts"
    artifacts.mkdir(exist_ok=True)
    return Settings(
        repo_root=root,
        corpus_root=root / "data" / "dart-disclosures",
        artifacts_root=artifacts,
        clova_api_key=os.environ.get("CLOVA_API_KEY", ""),
        clova_url=CLOVA_HOST + CLOVA_PATH,
        model=MODEL,
    )
```

`src/argus/__init__.py`: 빈 파일.

`tests/conftest.py`:
```python
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
```

- [x] **Step 5: 테스트 통과 확인**

Run: `python -m pytest tests/test_config.py -v`
Expected: PASS (3 passed)

- [x] **Step 6: artifacts/ gitignore 추가**

`.gitignore` 끝에 추가:
```
artifacts/
```

- [x] **Step 7: 커밋**

```bash
git add pyproject.toml src/argus/__init__.py src/argus/config.py tests/test_config.py tests/conftest.py .gitignore
git commit -m "feat: 프로젝트 스캐폴딩과 설정 모듈"
```

---

## Task 2: HCX 클라이언트

노트북의 `chat()`을 앱 코드로 이식한다. **모든 LLM 호출이 이 함수 하나를 지나가야** 토큰·비용 회계가 성립한다.

**Files:**
- Create: `src/argus/clova.py`, `tests/test_clova.py`

**Interfaces:**
- Consumes: `config.settings()`
- Produces:
  - `ChatResult` 데이터클래스: `text: str`, `prompt_tokens: int`, `completion_tokens: int`, `latency_s: float`, `finish: str`, `tool_calls: list | None`
  - `chat(messages: list[dict], max_tokens: int = 512, temperature: float = 0.0, tools: list | None = None) -> ChatResult`
  - `CallLog.records: list[ChatResult]`, `CallLog.totals() -> dict`

- [x] **Step 1: 실패하는 테스트 작성**

네트워크 없이 도는 테스트다. `_post`를 가짜로 바꿔 넣는다.

`tests/test_clova.py`:
```python
from argus import clova


def _fake_post(url, headers, body, timeout=60):
    assert "maxTokens" not in body or "tools" not in body, "tools와 maxTokens 동시 전송 금지"
    return 200, {
        "status": {"code": "20000"},
        "result": {
            "message": {"role": "assistant", "content": "답변"},
            "finishReason": "stop",
            "usage": {"promptTokens": 11, "completionTokens": 7, "totalTokens": 18},
        },
    }, 0.5


def test_chat_parses_usage(monkeypatch):
    monkeypatch.setattr(clova, "_post", _fake_post)
    clova.CALL_LOG.clear()
    r = clova.chat([{"role": "user", "content": "안녕"}])
    assert r.text == "답변"
    assert r.prompt_tokens == 11
    assert r.completion_tokens == 7


def test_tools_and_max_tokens_are_exclusive(monkeypatch):
    monkeypatch.setattr(clova, "_post", _fake_post)
    clova.CALL_LOG.clear()
    clova.chat([{"role": "user", "content": "안녕"}], tools=[{"type": "function"}])


def test_call_log_accumulates_totals(monkeypatch):
    monkeypatch.setattr(clova, "_post", _fake_post)
    clova.CALL_LOG.clear()
    clova.chat([{"role": "user", "content": "1"}])
    clova.chat([{"role": "user", "content": "2"}])
    totals = clova.CALL_LOG.totals()
    assert totals["calls"] == 2
    assert totals["prompt_tokens"] == 22
    assert totals["completion_tokens"] == 14
```

- [x] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_clova.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'argus.clova'`

- [x] **Step 3: clova.py 구현**

```python
"""HyperCLOVA X 호출 단일 창구.

주의:
  - HCX-005는 v3 API 필수 (v1은 40084 Unsupported API for model)
  - nv- 신규 키는 Bearer 인증만 허용 (레거시 헤더는 40104)
  - tools 와 maxTokens 는 동시 지정 불가 (40001)
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from argus.config import settings


@dataclass
class ChatResult:
    text: str
    prompt_tokens: int
    completion_tokens: int
    latency_s: float
    finish: str
    tool_calls: list | None = None
    tag: str = ""


@dataclass
class CallLog:
    records: list[ChatResult] = field(default_factory=list)

    def clear(self) -> None:
        self.records.clear()

    def add(self, r: ChatResult) -> None:
        self.records.append(r)

    def totals(self) -> dict:
        return {
            "calls": len(self.records),
            "prompt_tokens": sum(r.prompt_tokens for r in self.records),
            "completion_tokens": sum(r.completion_tokens for r in self.records),
            "latency_s": sum(r.latency_s for r in self.records),
        }


CALL_LOG = CallLog()


def _post(url: str, headers: dict, body: dict, timeout: int = 60):
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json", **headers},
    )
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8")), time.time() - t0
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            return exc.code, json.loads(raw), time.time() - t0
        except json.JSONDecodeError:
            return exc.code, {"raw": raw}, time.time() - t0


def chat(
    messages: list[dict],
    max_tokens: int = 512,
    temperature: float = 0.0,
    top_p: float = 0.8,
    repeat_penalty: float = 1.1,
    tools: list | None = None,
    tag: str = "",
) -> ChatResult:
    cfg = settings()
    body: dict = {
        "messages": messages,
        "temperature": temperature,
        "topP": top_p,
        "repeatPenalty": repeat_penalty,
    }
    if tools:
        body["tools"] = tools
        body["toolChoice"] = "auto"
    else:
        body["maxTokens"] = max_tokens

    status, payload, latency = _post(
        cfg.clova_url, {"Authorization": f"Bearer {cfg.clova_api_key}"}, body
    )
    if status != 200:
        raise RuntimeError(f"CLOVA 호출 실패 {status}: {payload}")

    result = payload.get("result") or {}
    message = result.get("message") or {}
    usage = result.get("usage") or {}
    record = ChatResult(
        text=message.get("content", ""),
        prompt_tokens=usage.get("promptTokens", 0),
        completion_tokens=usage.get("completionTokens", 0),
        latency_s=latency,
        finish=result.get("finishReason", ""),
        tool_calls=message.get("toolCalls"),
        tag=tag,
    )
    CALL_LOG.add(record)
    return record
```

- [x] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_clova.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: 실제 호출 1회로 연동 확인 (수동)** — SKIPPED (네트워크 호출·크레딧 절약을 위해 의도적으로 미실행)

Run:
```bash
python -c "from argus.clova import chat; r=chat([{'role':'user','content':'안녕'}], max_tokens=10); print(r.text, r.prompt_tokens, r.completion_tokens)"
```
Expected: 한국어 응답과 토큰 수 출력

- [x] **Step 6: 커밋**

```bash
git add src/argus/clova.py tests/test_clova.py
git commit -m "feat: HCX 클라이언트와 호출 로깅"
```

---

## Task 3: 메타데이터 로더와 프리필터

LLM 호출 0회로 후보를 좁히는 계층. **가장 싸고 효과가 큰 최적화**다.

**Files:**
- Create: `src/argus/corpus/__init__.py`, `src/argus/corpus/manifest.py`, `tests/test_manifest.py`

**Interfaces:**
- Consumes: `config.settings()`
- Produces:
  - `load_manifest() -> list[dict]` (4,204건)
  - `load_universe() -> dict[str, dict]` — `corp_name` → 기업 레코드
  - `resolve_company(name: str) -> dict | None` — 통용명·영문명·법인명 모두 해소
  - `filter_docs(corp_name=None, doc_group=None, doc_subtype=None, year=None, include_corrections=True) -> list[dict]`

- [x] **Step 1: 실패하는 테스트 작성**

`tests/test_manifest.py`:
```python
from argus.corpus.manifest import filter_docs, load_manifest, load_universe, resolve_company


def test_manifest_has_4204_records():
    assert len(load_manifest()) == 4204


def test_universe_has_70_companies():
    assert len(load_universe()) == 70


def test_resolve_company_handles_common_name():
    # universe 의 corp_name 은 현대자동차, 통용명은 현대차
    rec = resolve_company("현대차")
    assert rec is not None
    assert rec["corp_name"] == "현대자동차"
    assert rec["corp_code"] == "00164742"


def test_resolve_company_handles_official_name():
    assert resolve_company("삼성전자")["corp_code"] == "00126380"


def test_filter_by_group_and_subtype():
    docs = filter_docs(doc_group="exchange", doc_subtype="단일판매공급계약체결")
    assert len(docs) == 1106


def test_filter_can_exclude_corrections():
    all_ex = filter_docs(doc_group="exchange")
    no_corr = filter_docs(doc_group="exchange", include_corrections=False)
    assert len(all_ex) == 1469
    assert len(all_ex) - len(no_corr) == 631
```

- [x] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_manifest.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'argus.corpus'`

- [x] **Step 3: manifest.py 구현**

`src/argus/corpus/__init__.py`: 빈 파일.

`src/argus/corpus/manifest.py`:
```python
"""manifest.jsonl / universe.csv 로딩과 LLM 없는 프리필터."""
from __future__ import annotations

import csv
import json
from functools import lru_cache

from argus.config import settings


@lru_cache(maxsize=1)
def load_manifest() -> tuple[dict, ...]:
    path = settings().corpus_root / "manifest.jsonl"
    with path.open(encoding="utf-8") as fh:
        return tuple(json.loads(line) for line in fh if line.strip())


@lru_cache(maxsize=1)
def load_universe() -> dict[str, dict]:
    path = settings().corpus_root / "universe.csv"
    # universe.csv 는 UTF-8 BOM 이다
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return {row["corp_name"]: row for row in csv.DictReader(fh)}


@lru_cache(maxsize=1)
def _alias_index() -> dict[str, str]:
    """통용명·영문명·법인명을 모두 corp_name 으로 되돌리는 색인."""
    index: dict[str, str] = {}
    for corp_name, row in load_universe().items():
        for alias in (corp_name, row.get("listed_name"), row.get("corp_eng_name")):
            if alias:
                index[alias.strip().lower()] = corp_name
    return index


def resolve_company(name: str) -> dict | None:
    corp_name = _alias_index().get(name.strip().lower())
    return load_universe().get(corp_name) if corp_name else None


def filter_docs(
    corp_name: str | None = None,
    doc_group: str | None = None,
    doc_subtype: str | None = None,
    year: int | None = None,
    include_corrections: bool = True,
) -> list[dict]:
    rows = load_manifest()
    if corp_name:
        resolved = resolve_company(corp_name)
        target = resolved["corp_name"] if resolved else corp_name
        rows = [r for r in rows if r["corp_name"] == target]
    if doc_group:
        rows = [r for r in rows if r["doc_group"] == doc_group]
    if doc_subtype:
        rows = [r for r in rows if r.get("doc_subtype") == doc_subtype]
    if year:
        rows = [r for r in rows if str(r.get("rcept_dt", ""))[:4] == str(year)]
    if not include_corrections:
        rows = [r for r in rows if not r.get("is_correction")]
    return list(rows)
```

- [x] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_manifest.py -v`
Expected: PASS (6 passed)

- [x] **Step 5: 커밋**

```bash
git add src/argus/corpus/__init__.py src/argus/corpus/manifest.py tests/test_manifest.py
git commit -m "feat: manifest/universe 로더와 메타데이터 프리필터"
```

---

## Task 4: DART HTML 파서

거래소공시는 **커스텀 XML이 아니라 HTML 표**다. 인코딩 함정을 여기서 한 번에 처리한다.

**Files:**
- Create: `src/argus/corpus/html_doc.py`, `tests/test_html_doc.py`

**Interfaces:**
- Consumes: 없음 (순수 함수)
- Produces:
  - `read_document(path: Path) -> str` — UTF-8 강제 읽기
  - `strip_markup(html: str) -> str` — style/script 제거 후 태그 제거
  - `extract_title(html: str) -> str`
  - `extract_rows(html: str) -> list[list[str]]` — 표의 행별 셀 목록

- [x] **Step 1: 실패하는 테스트 작성**

`tests/test_html_doc.py`:
```python
from argus.config import settings
from argus.corpus.html_doc import extract_rows, extract_title, read_document, strip_markup

SAMPLE = (
    settings().corpus_root
    / "raw/exchange/HD현대일렉트릭/20230131800162/20230131800162.xml"
)


def test_file_is_utf8_despite_euckr_meta():
    raw = SAMPLE.read_bytes()
    assert b"charset=euc-kr" in raw          # 선언은 euc-kr
    raw.decode("utf-8")                       # 그러나 실제 바이트는 UTF-8


def test_read_document_returns_korean():
    text = read_document(SAMPLE)
    assert "단일판매" in text


def test_strip_markup_removes_css():
    text = strip_markup(read_document(SAMPLE))
    assert "font-family" not in text
    assert "border-collapse" not in text


def test_extract_title():
    assert "공급계약" in extract_title(read_document(SAMPLE))


def test_extract_rows_finds_contract_amount():
    rows = extract_rows(read_document(SAMPLE))
    flat = [cell for row in rows for cell in row]
    assert any("97,000,000,000" in c for c in flat)
    assert any("계약금액" in c for c in flat)
```

- [x] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_html_doc.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'argus.corpus.html_doc'`

- [x] **Step 3: html_doc.py 구현**

```python
"""DART 공시 HTML 파싱.

핵심 함정: 파일 바이트는 UTF-8 인데 <meta charset=euc-kr> 로 선언되어 있다.
자동 인코딩 탐지에 맡기면 전부 깨지므로 UTF-8 을 강제한다.
"""
from __future__ import annotations

import html as html_lib
import re
from pathlib import Path

_STYLE = re.compile(r"(?is)<style[^>]*>.*?</style>")
_SCRIPT = re.compile(r"(?is)<script[^>]*>.*?</script>")
_TAG = re.compile(r"(?s)<[^>]*>")
_TITLE = re.compile(r"(?is)<title>(.*?)</title>")
_TR = re.compile(r"(?is)<tr[^>]*>(.*?)</tr>")
_TD = re.compile(r"(?is)<t[dh][^>]*>(.*?)</t[dh]>")
_WS = re.compile(r"\s+")


def read_document(path: Path) -> str:
    """meta 선언을 무시하고 UTF-8 로 읽는다."""
    return Path(path).read_text(encoding="utf-8", errors="replace")


def _clean(fragment: str) -> str:
    text = _TAG.sub(" ", fragment)
    return _WS.sub(" ", html_lib.unescape(text)).strip()


def strip_markup(html: str) -> str:
    body = _SCRIPT.sub(" ", _STYLE.sub(" ", html))
    return _clean(body)


def extract_title(html: str) -> str:
    match = _TITLE.search(html)
    return _clean(match.group(1)) if match else ""


def extract_rows(html: str) -> list[list[str]]:
    body = _SCRIPT.sub(" ", _STYLE.sub(" ", html))
    rows: list[list[str]] = []
    for tr in _TR.findall(body):
        cells = [_clean(td) for td in _TD.findall(tr)]
        cells = [c for c in cells if c]
        if cells:
            rows.append(cells)
    return rows
```

- [x] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_html_doc.py -v`
Expected: PASS (5 passed)

- [x] **Step 5: 커밋**

```bash
git add src/argus/corpus/html_doc.py tests/test_html_doc.py
git commit -m "feat: DART HTML 파서 (UTF-8 강제, 표 행 추출)"
```

---

## Task 5: 날짜·기간 결정적 연산

실측에서 **CoT로도 못 고친 유일한 실패 유형**이다. LLM에서 분리한다.

**Files:**
- Create: `src/argus/corpus/dates.py`, `tests/test_dates.py`

**Interfaces:**
- Consumes: 없음
- Produces:
  - `parse_date(text: str) -> date | None` — `2025-07-24`, `2025.07.24`, `20250724` 수용
  - `duration_ym(start: date, end: date) -> tuple[int, int]` — (년, 개월)
  - `format_duration(start: date, end: date) -> str` — `"8년 5개월"`

- [x] **Step 1: 실패하는 테스트 작성**

`tests/test_dates.py`:
```python
from datetime import date

import pytest

from argus.corpus.dates import duration_ym, format_duration, parse_date


@pytest.mark.parametrize(
    "text,expected",
    [
        ("2025-07-24", date(2025, 7, 24)),
        ("2025.07.24", date(2025, 7, 24)),
        ("20250724", date(2025, 7, 24)),
        ("2025년 7월 24일", date(2025, 7, 24)),
        ("해당없음", None),
    ],
)
def test_parse_date(text, expected):
    assert parse_date(text) == expected


def test_duration_matches_gold_answer():
    # HCX 가 Direct/CoT 모두 틀린 문항 — 정답은 8년 5개월
    assert duration_ym(date(2025, 7, 24), date(2033, 12, 31)) == (8, 5)


def test_format_duration():
    assert format_duration(date(2025, 7, 24), date(2033, 12, 31)) == "8년 5개월"


def test_duration_same_month_is_zero():
    assert duration_ym(date(2025, 7, 1), date(2025, 7, 20)) == (0, 0)
```

- [x] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_dates.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'argus.corpus.dates'`

- [x] **Step 3: dates.py 구현**

```python
"""날짜·기간 연산. LLM 이 신뢰할 수 없는 영역이라 결정적으로 처리한다."""
from __future__ import annotations

import re
from datetime import date

_PATTERNS = (
    re.compile(r"(\d{4})[-./](\d{1,2})[-./](\d{1,2})"),
    re.compile(r"(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일"),
    re.compile(r"\b(\d{4})(\d{2})(\d{2})\b"),
)


def parse_date(text: str) -> date | None:
    if not text:
        return None
    for pattern in _PATTERNS:
        match = pattern.search(text)
        if match:
            year, month, day = (int(g) for g in match.groups())
            try:
                return date(year, month, day)
            except ValueError:
                return None
    return None


def duration_ym(start: date, end: date) -> tuple[int, int]:
    """시작일~종료일의 만(滿) 년·개월. 일 단위 잔여는 버린다."""
    months = (end.year - start.year) * 12 + (end.month - start.month)
    if end.day < start.day:
        months -= 1
    months = max(months, 0)
    return months // 12, months % 12


def format_duration(start: date, end: date) -> str:
    years, months = duration_ym(start, end)
    return f"{years}년 {months}개월"
```

- [x] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_dates.py -v`
Expected: PASS (8 passed)

- [x] **Step 5: 커밋**

```bash
git add src/argus/corpus/dates.py tests/test_dates.py
git commit -m "feat: 날짜·기간 결정적 연산 (LLM 오답 영역 분리)"
```

---

## Task 6: 거래소공시 정규화

CONVENTION §7.3의 스키마를 **필드명 그대로** 산출한다. 주최 측이 제시한 형식이다.

**Files:**
- Create: `src/argus/corpus/normalize.py`, `tests/test_normalize.py`

**Interfaces:**
- Consumes: `html_doc.extract_rows`, `manifest.load_manifest`, `dates.parse_date`
- Produces:
  - `normalize_exchange(doc: dict) -> dict` — manifest 레코드 하나를 정규화 레코드로.
    키: `doc_id` `rcept_no` `rcept_dt` `corp_code` `corp_name` `sector` `doc_subtype`
    `is_correction` `title` `amount` `amount_type` `ratio` `ratio_base` `counterparty`
    `period_start` `period_end` `event_date` `reserved_reason` `note` `related`

- [x] **Step 1: 실패하는 테스트 작성**

`tests/test_normalize.py`:
```python
from argus.corpus.manifest import filter_docs
from argus.corpus.normalize import normalize_exchange


def _doc(rcept_no: str) -> dict:
    return next(d for d in filter_docs(doc_group="exchange") if d["rcept_no"] == rcept_no)


def test_normalize_extracts_amount_and_ratio():
    rec = normalize_exchange(_doc("20230131800162"))
    assert rec["amount"] == 97_000_000_000
    assert rec["ratio"] == 5.37
    assert rec["ratio_base"] == "매출액"


def test_normalize_extracts_counterparty_and_period():
    rec = normalize_exchange(_doc("20230131800162"))
    assert "Hyundai Electric America" in rec["counterparty"]
    assert rec["period_start"] == "2023-01-30"
    assert rec["period_end"] == "2024-10-31"


def test_normalize_carries_manifest_identity():
    rec = normalize_exchange(_doc("20230131800162"))
    assert rec["doc_id"] == "exchange_20230131800162"
    assert rec["corp_name"] == "HD현대일렉트릭"
    assert rec["is_correction"] is False
    assert rec["related"] == []
```

- [x] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_normalize.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'argus.corpus.normalize'`

- [x] **Step 3: normalize.py 구현**

```python
"""거래소공시 표 -> 정규화 레코드 (CONVENTION §7.3 스키마)."""
from __future__ import annotations

import re
from pathlib import Path

from argus.config import settings
from argus.corpus.dates import parse_date
from argus.corpus.html_doc import extract_rows, extract_title, read_document

_NUM = re.compile(r"-?[\d,]+(?:\.\d+)?")
_TITLE_DATE = re.compile(r"^\(\d{4}[.\-]\d{2}[.\-]\d{2}\)\s*")

# 계약 서식 전용 라벨에 의존하는 doc_subtype. 나머지 363건(25%)은 서식이 다르다.
CONTRACT_SUBTYPES = {"단일판매공급계약체결", "단일판매공급계약해지"}


def _to_number(text: str) -> float | None:
    match = _NUM.search(text or "")
    if not match:
        return None
    try:
        return float(match.group(0).replace(",", ""))
    except ValueError:
        return None


def _clean(value: str | None) -> str | None:
    """DART 표는 빈 값을 '-' 로 채운다. None 으로 정규화한다."""
    if value is None:
        return None
    stripped = value.strip()
    return None if stripped in {"-", "–", "—", ""} else stripped


def _find(rows: list[list[str]], *keywords: str) -> str | None:
    """라벨 셀에 키워드가 모두 들어간 행의 마지막 셀을 돌려준다."""
    for row in rows:
        joined = " ".join(row)
        if all(k in joined for k in keywords):
            return _clean(row[-1])
    return None


def _doc_path(doc: dict) -> Path:
    folder = settings().corpus_root / doc["file_path"]
    xml = sorted(folder.glob("*.xml"))
    if not xml:
        raise FileNotFoundError(f"XML 없음: {folder}")
    return xml[0]


def normalize_exchange(doc: dict) -> dict:
    rows = extract_rows(read_document(_doc_path(doc)))
    title = extract_title(read_document(_doc_path(doc)))

    amount = _to_number(_find(rows, "계약금액") or "")
    ratio = _to_number(_find(rows, "매출액대비") or "")
    start = parse_date(_find(rows, "시작일") or "")
    end = parse_date(_find(rows, "종료일") or "")
    # 라벨은 '확정일자' 가 아니라 '7. 계약(수주)일자' 다. 없으면 접수일로 폴백한다.
    event = (parse_date(_find(rows, "계약", "일자") or "")
             or parse_date(_find(rows, "수주", "일자") or "")
             or parse_date(doc["rcept_dt"]))

    # 제목은 '법인명/서식명/(2023.01.31)서식명' 형태다. 날짜 접두어를 떼어낸다.
    raw_title = title.split("/")[-1].strip() if title else doc["report_nm"]
    clean_title = _TITLE_DATE.sub("", raw_title).strip() or doc["report_nm"]

    return {
        "doc_id": doc["doc_id"],
        "rcept_no": doc["rcept_no"],
        "rcept_dt": doc["rcept_dt"],
        "corp_code": doc["corp_code"],
        "corp_name": doc["corp_name"],
        "sector": doc["sector"],
        "doc_subtype": doc["doc_subtype"],
        "is_correction": bool(doc["is_correction"]),
        "title": clean_title,
        "amount": int(amount) if amount is not None else None,
        "amount_type": "계약금액",
        "ratio": ratio,
        "ratio_base": "매출액",
        "counterparty": _find(rows, "계약상대"),
        "period_start": start.isoformat() if start else None,
        "period_end": end.isoformat() if end else None,
        "event_date": event.isoformat() if event else None,
        "reserved_reason": _find(rows, "유보사유"),
        "note": None,
        "related": [],
        # 계약 서식이 아닌 363건(투자판단·신규시설투자·해지 일부)은 필드가 비게 된다.
        # 조용히 통과시키지 않고 명시적으로 표시해 Task 10에서 집계한다.
        "schema_matched": doc["doc_subtype"] in CONTRACT_SUBTYPES and amount is not None,
        "raw_rows": rows[:40],
    }
```

- [x] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_normalize.py -v`
Expected: PASS (3 passed)

> 실패하면 `extract_rows` 출력을 눈으로 확인하고 `_find` 키워드를 실제 라벨에 맞춘다.
> 라벨은 공시 서식 버전에 따라 다를 수 있다.

- [x] **Step 5: 커밋**

```bash
git add src/argus/corpus/normalize.py tests/test_normalize.py
git commit -m "feat: 거래소공시 정규화 (주최 측 스키마 준수)"
```

---

## Task 7: 마크다운 텍스트화

LLM이 마크다운을 잘 이해한다는 설명회 권고와 CONVENTION §7.3의 출력 예시를 따른다.

**Files:**
- Create: `src/argus/corpus/markdown.py`, `tests/test_markdown.py`

**Interfaces:**
- Consumes: Task 6의 정규화 레코드
- Produces: `to_markdown(record: dict) -> str`

- [x] **Step 1: 실패하는 테스트 작성**

`tests/test_markdown.py`:
```python
from argus.corpus.markdown import to_markdown

RECORD = {
    "doc_id": "exchange_20250728800035",
    "rcept_no": "20250728800035",
    "rcept_dt": "20250728",
    "corp_name": "삼성전자",
    "sector": "반도체·전자부품",
    "doc_subtype": "단일판매공급계약체결",
    "is_correction": False,
    "title": "반도체 위탁생산 공급계약",
    "amount": 22764764160000,
    "amount_type": "계약금액",
    "ratio": 7.6,
    "ratio_base": "매출액",
    "counterparty": "글로벌 대형기업",
    "period_start": "2025-07-24",
    "period_end": "2033-12-31",
    "event_date": "2025-07-26",
    "reserved_reason": "경영상 비밀유지",
    "note": None,
    "related": [],
}


def test_markdown_header_has_company_and_title():
    md = to_markdown(RECORD)
    assert md.startswith("## ")
    assert "삼성전자" in md
    assert "반도체 위탁생산 공급계약" in md


def test_markdown_formats_amount_with_commas_and_eok():
    md = to_markdown(RECORD)
    assert "22,764,764,160,000원" in md
    assert "227,648억원" in md


def test_markdown_includes_receipt_number_for_citation():
    assert "20250728800035" in to_markdown(RECORD)


def test_markdown_includes_computed_duration():
    # 날짜 연산은 결정적 함수가 담당한다
    assert "8년 5개월" in to_markdown(RECORD)
```

- [x] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_markdown.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'argus.corpus.markdown'`

- [x] **Step 3: markdown.py 구현**

```python
"""정규화 레코드 -> LLM 입력용 마크다운 (CONVENTION §7.3 형식)."""
from __future__ import annotations

from argus.corpus.dates import format_duration, parse_date

_SUBTYPE_LABEL = {
    "단일판매공급계약체결": "공급계약 체결",
    "단일판매공급계약해지": "공급계약 해지",
    "신규시설투자등": "신규 시설투자",
    "투자판단관련주요경영사항": "투자판단 관련 주요경영사항",
}


def _amount_line(record: dict) -> str | None:
    amount = record.get("amount")
    if amount is None:
        return None
    eok = amount / 100_000_000
    return f"- {record.get('amount_type', '금액')}: {amount:,}원 ({eok:,.0f}억원)"


def to_markdown(record: dict) -> str:
    label = _SUBTYPE_LABEL.get(record.get("doc_subtype", ""), record.get("doc_subtype", "공시"))
    prefix = "[기재정정] " if record.get("is_correction") else ""
    lines = [
        f"## {prefix}[{label}] {record['corp_name']} – {record.get('title', '')}".rstrip(" –"),
        f"- 공시일: {_fmt_date(record.get('rcept_dt'))} (접수번호 {record['rcept_no']})",
    ]
    if record.get("sector"):
        lines.append(f"- 섹터: {record['sector']}")

    amount_line = _amount_line(record)
    if amount_line:
        lines.append(amount_line)
    if record.get("ratio") is not None:
        lines.append(f"- {record.get('ratio_base', '매출액')} 대비: {record['ratio']}%")
    if record.get("counterparty"):
        lines.append(f"- 계약상대: {record['counterparty']}")

    start, end = record.get("period_start"), record.get("period_end")
    if start and end:
        line = f"- 기간: {start} ~ {end}"
        sd, ed = parse_date(start), parse_date(end)
        if sd and ed:
            line += f" ({format_duration(sd, ed)})"
        lines.append(line)

    if record.get("event_date"):
        lines.append(f"- 확정일: {record['event_date']}")
    if record.get("reserved_reason"):
        lines.append(f"- 공시유보: {record['reserved_reason']}")
    return "\n".join(lines)


def _fmt_date(raw: str | None) -> str:
    if raw and len(raw) == 8 and raw.isdigit():
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:]}"
    return raw or ""
```

- [x] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_markdown.py -v`
Expected: PASS (4 passed)

- [x] **Step 5: 커밋**

```bash
git add src/argus/corpus/markdown.py tests/test_markdown.py
git commit -m "feat: 정규화 레코드 마크다운 텍스트화"
```

---

## Task 8: 정정공시 체인

거래소공시 1,469건 중 **631건(43%)이 정정**이다. 원본과 연결하지 않으면 중복·모순 근거가 나온다.

**Files:**
- Create: `src/argus/corpus/corrections.py`, `tests/test_corrections.py`

**Interfaces:**
- Consumes: `manifest.load_manifest`, `html_doc`
- Produces:
  - `build_chains(doc_group: str = "exchange") -> dict[str, list[str]]` — 원본 `rcept_no` → 정정 `rcept_no` 목록 (접수일 오름차순)
  - `latest_of(rcept_no: str, chains: dict) -> str` — 체인의 최신 접수번호
  - `attach_related(records: list[dict], chains: dict) -> list[dict]` — `related` 채우고 `is_latest` 부여

- [x] **Step 1: 실패하는 테스트 작성**

`tests/test_corrections.py`:
```python
from argus.corpus.corrections import attach_related, build_chains, latest_of


def test_chains_cover_all_corrections():
    chains = build_chains("exchange")
    linked = sum(len(v) for v in chains.values())
    assert linked > 0
    assert linked <= 631      # 정정 총건수를 넘을 수 없다


def test_latest_of_returns_self_when_no_correction():
    chains = build_chains("exchange")
    orphan = "20230131800162"
    assert latest_of(orphan, chains) == orphan


def test_attach_related_sets_is_latest():
    chains = build_chains("exchange")
    records = [
        {"rcept_no": "20230131800162", "is_correction": False, "related": []},
    ]
    out = attach_related(records, chains)
    assert out[0]["is_latest"] is True
    assert isinstance(out[0]["related"], list)
```

- [x] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_corrections.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'argus.corpus.corrections'`

- [x] **Step 3: corrections.py 구현**

정정 공시 본문에는 `정정관련 공시서류제출일` 행이 있다. 그 날짜와 같은 기업·같은 서식의
직전 공시를 원본으로 본다.

```python
"""정정공시 <-> 원본 연결.

정정 공시 본문의 '정정관련 공시서류제출일' 을 단서로, 같은 기업의 같은 유형
공시 중 그 날짜에 접수된 것을 원본으로 잇는다.
"""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from argus.config import settings
from argus.corpus.dates import parse_date
from argus.corpus.html_doc import extract_rows, read_document
from argus.corpus.manifest import filter_docs


def _doc_path(doc: dict) -> Path | None:
    folder = settings().corpus_root / doc["file_path"]
    xml = sorted(folder.glob("*.xml"))
    return xml[0] if xml else None


def _original_submit_date(doc: dict) -> str | None:
    path = _doc_path(doc)
    if not path:
        return None
    for row in extract_rows(read_document(path)):
        joined = " ".join(row)
        if "정정관련" in joined and "제출일" in joined:
            parsed = parse_date(row[-1])
            return parsed.strftime("%Y%m%d") if parsed else None
    return None


def build_chains(doc_group: str = "exchange") -> dict[str, list[str]]:
    docs = filter_docs(doc_group=doc_group)
    by_corp: dict[str, list[dict]] = defaultdict(list)
    for doc in docs:
        by_corp[doc["corp_name"]].append(doc)

    chains: dict[str, list[str]] = defaultdict(list)
    for corp_docs in by_corp.values():
        originals = {d["rcept_dt"]: d for d in corp_docs if not d["is_correction"]}
        for doc in corp_docs:
            if not doc["is_correction"]:
                continue
            submit_dt = _original_submit_date(doc)
            origin = originals.get(submit_dt) if submit_dt else None
            if origin:
                chains[origin["rcept_no"]].append(doc["rcept_no"])

    for key in chains:
        chains[key].sort()
    return dict(chains)


def latest_of(rcept_no: str, chains: dict[str, list[str]]) -> str:
    corrections = chains.get(rcept_no)
    return corrections[-1] if corrections else rcept_no


def attach_related(records: list[dict], chains: dict[str, list[str]]) -> list[dict]:
    corrected = {c for v in chains.values() for c in v}
    out = []
    for record in records:
        rcept_no = record["rcept_no"]
        enriched = dict(record)
        enriched["related"] = list(chains.get(rcept_no, []))
        enriched["is_latest"] = rcept_no not in corrected or not enriched["related"]
        if rcept_no in corrected:
            enriched["is_latest"] = False
        out.append(enriched)
    return out
```

- [x] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_corrections.py -v`
Expected: PASS (3 passed)

- [x] **Step 5: 연결률 확인 (수동)**

Run:
```bash
python -c "
from argus.corpus.corrections import build_chains
c = build_chains('exchange')
print('원본', len(c), '건에 정정', sum(len(v) for v in c.values()), '건 연결 / 전체 정정 631건')
"
```
연결률이 50% 미만이면 `_original_submit_date` 의 라벨 매칭을 실제 서식에 맞춰 넓힌다.

- [x] **Step 6: 커밋**

```bash
git add src/argus/corpus/corrections.py tests/test_corrections.py
git commit -m "feat: 정정공시 체인 연결"
```

---

## Task 9: BM25 인덱스

LLM 0회. ToG-2가 프루닝에 BM25도 충분하다고 보고했으므로 1차 검색은 여기서 시작한다.

**Files:**
- Create: `src/argus/retrieve/__init__.py`, `src/argus/retrieve/bm25.py`, `tests/test_bm25.py`

**Interfaces:**
- Consumes: Task 7의 마크다운 문자열
- Produces:
  - `tokenize(text: str) -> list[str]`
  - `Bm25Index.build(docs: list[dict]) -> Bm25Index` — `docs` 는 `{"doc_id", "text", "meta"}`
  - `Bm25Index.search(query: str, top_k: int = 10, **meta_filters) -> list[tuple[str, float]]`
  - `Bm25Index.save(path) / Bm25Index.load(path)`

- [x] **Step 1: 실패하는 테스트 작성**

`tests/test_bm25.py`:
```python
from argus.retrieve.bm25 import Bm25Index, tokenize

DOCS = [
    {"doc_id": "a", "text": "삼성전자 반도체 위탁생산 공급계약 22조원", "meta": {"corp_name": "삼성전자"}},
    {"doc_id": "b", "text": "HD현대일렉트릭 변압기 공급계약 970억원", "meta": {"corp_name": "HD현대일렉트릭"}},
    {"doc_id": "c", "text": "삼성전자 신규 시설투자 결정", "meta": {"corp_name": "삼성전자"}},
]


def test_tokenize_splits_korean_and_numbers():
    tokens = tokenize("계약금액 22,764,764,160,000원")
    assert "계약금액" in tokens


def test_search_ranks_relevant_doc_first():
    index = Bm25Index.build(DOCS)
    hits = index.search("변압기 공급계약", top_k=2)
    assert hits[0][0] == "b"


def test_meta_filter_restricts_candidates():
    index = Bm25Index.build(DOCS)
    hits = index.search("공급계약", top_k=5, corp_name="삼성전자")
    assert {h[0] for h in hits} <= {"a", "c"}


def test_roundtrip_save_load(tmp_path):
    index = Bm25Index.build(DOCS)
    path = tmp_path / "idx.json"
    index.save(path)
    assert Bm25Index.load(path).search("변압기", top_k=1)[0][0] == "b"
```

- [x] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_bm25.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'argus.retrieve'`

- [x] **Step 3: bm25.py 구현**

`src/argus/retrieve/__init__.py`: 빈 파일.

```python
"""BM25 검색 인덱스. LLM 호출 없음."""
from __future__ import annotations

import json
import re
from pathlib import Path

from rank_bm25 import BM25Okapi

# 공시 서식명에는 가운뎃점 'ㆍ'(U+318D) 가 섞여 있다 — '단일판매ㆍ공급계약체결'.
# [가-힣] 범위 밖이라 그냥 두면 토큰이 쪼개진다. 정규화해서 흡수한다.
_MIDDOT = re.compile(r"[ㆍ·・･]")
_TOKEN = re.compile(r"[0-9]+(?:[,.][0-9]+)*|[가-힣]+|[A-Za-z]+")


def tokenize(text: str) -> list[str]:
    return _TOKEN.findall(_MIDDOT.sub("", text or ""))


class Bm25Index:
    def __init__(self, doc_ids: list[str], texts: list[str], metas: list[dict]):
        self.doc_ids = doc_ids
        self.texts = texts
        self.metas = metas
        self._bm25 = BM25Okapi([tokenize(t) for t in texts])

    @classmethod
    def build(cls, docs: list[dict]) -> "Bm25Index":
        return cls(
            [d["doc_id"] for d in docs],
            [d["text"] for d in docs],
            [d.get("meta", {}) for d in docs],
        )

    def search(self, query: str, top_k: int = 10, **meta_filters) -> list[tuple[str, float]]:
        scores = self._bm25.get_scores(tokenize(query))
        ranked = sorted(zip(self.doc_ids, scores, self.metas), key=lambda x: -x[1])
        out = []
        for doc_id, score, meta in ranked:
            if any(meta.get(k) != v for k, v in meta_filters.items()):
                continue
            if score <= 0:
                continue
            out.append((doc_id, float(score)))
            if len(out) >= top_k:
                break
        return out

    def save(self, path) -> None:
        Path(path).write_text(
            json.dumps(
                {"doc_ids": self.doc_ids, "texts": self.texts, "metas": self.metas},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    @classmethod
    def load(cls, path) -> "Bm25Index":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(raw["doc_ids"], raw["texts"], raw["metas"])
```

- [x] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_bm25.py -v`
Expected: PASS (4 passed)

- [x] **Step 5: 커밋**

```bash
git add src/argus/retrieve/__init__.py src/argus/retrieve/bm25.py tests/test_bm25.py
git commit -m "feat: BM25 검색 인덱스"
```

---

## Task 10: T1 인덱스 빌드 CLI

거래소공시 1,469건 전체를 정규화 → 마크다운 → BM25 인덱스까지 일괄 생성한다. **LLM 호출 0회**로 여기까지 간다.

**Files:**
- Create: `scripts/build_index.py`, `tests/test_build_index.py`

**Interfaces:**
- Consumes: Task 3·6·7·8·9 전부
- Produces: `artifacts/exchange_records.jsonl`, `artifacts/exchange_bm25.json`
  - CLI: `python scripts/build_index.py --group exchange [--limit N]`

- [x] **Step 1: 실패하는 테스트 작성**

`tests/test_build_index.py`:
```python
import subprocess
import sys
from pathlib import Path

from argus.config import settings


def test_build_index_writes_artifacts():
    root = settings().repo_root
    subprocess.run(
        [sys.executable, "scripts/build_index.py", "--group", "exchange", "--limit", "20"],
        cwd=root, check=True,
    )
    assert (settings().artifacts_root / "exchange_records.jsonl").exists()
    assert (settings().artifacts_root / "exchange_bm25.json").exists()


def test_built_records_have_schema_keys():
    import json
    path = settings().artifacts_root / "exchange_records.jsonl"
    first = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    for key in ("doc_id", "amount", "counterparty", "period_start", "related", "markdown"):
        assert key in first
```

- [x] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_build_index.py -v`
Expected: FAIL — `scripts/build_index.py` 없음

- [x] **Step 3: build_index.py 구현**

```python
"""T1 인덱스 빌드. LLM 호출 없음."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from argus.config import settings                      # noqa: E402
from argus.corpus.corrections import attach_related, build_chains  # noqa: E402
from argus.corpus.manifest import filter_docs          # noqa: E402
from argus.corpus.markdown import to_markdown          # noqa: E402
from argus.corpus.normalize import normalize_exchange  # noqa: E402
from argus.retrieve.bm25 import Bm25Index              # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--group", default="exchange")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    docs = filter_docs(doc_group=args.group)
    if args.limit:
        docs = docs[: args.limit]
    print(f"{args.group}: {len(docs)}건 처리")

    records = []
    for i, doc in enumerate(docs, 1):
        try:
            records.append(normalize_exchange(doc))
        except Exception as exc:                       # noqa: BLE001
            print(f"  [skip] {doc['rcept_no']}: {type(exc).__name__} {exc}")
        if i % 200 == 0:
            print(f"  {i}/{len(docs)}")

    chains = build_chains(args.group)
    records = attach_related(records, chains)
    for record in records:
        record["markdown"] = to_markdown(record)

    out_dir = settings().artifacts_root
    jsonl = out_dir / f"{args.group}_records.jsonl"
    with jsonl.open("w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    index = Bm25Index.build(
        [
            {
                "doc_id": r["doc_id"],
                "text": r["markdown"],
                "meta": {"corp_name": r["corp_name"], "doc_subtype": r["doc_subtype"]},
            }
            for r in records
        ]
    )
    index.save(out_dir / f"{args.group}_bm25.json")
    print(f"완료: {jsonl.name}, {args.group}_bm25.json ({len(records)}건)")


if __name__ == "__main__":
    main()
```

- [x] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_build_index.py -v`
Expected: PASS (2 passed)

- [x] **Step 5: 전량 빌드 실행 + 서식 커버리지 확인 (수동)**

Run: `python scripts/build_index.py --group exchange`

**예외로 죽는 건수(skip)보다 조용히 비는 건수가 더 위험하다.** exchange 1,469건 중
계약 서식은 1,126건(체결 1,106 + 해지 20)뿐이고, 나머지 343건(투자판단관련 300 ·
신규시설투자 43)은 라벨 체계가 다르다. 그래서 `schema_matched` 플래그로 집계한다.

```bash
python -c "
import json, collections
from argus.config import settings
rows=[json.loads(l) for l in open(settings().artifacts_root/'exchange_records.jsonl', encoding='utf-8')]
c=collections.Counter((r['doc_subtype'], r['schema_matched']) for r in rows)
for (st, ok), n in sorted(c.items()):
    print(f'{st:<28} matched={ok}  {n}')
print('전체', len(rows), '· matched', sum(r['schema_matched'] for r in rows))
"
```

**합격 기준**: `단일판매공급계약체결`·`단일판매공급계약해지` 의 `matched=True` 비율이 **90% 이상**.
미달이면 Task 6의 `_find` 라벨을 실제 행 라벨에 맞춰 보강한다.
`투자판단관련주요경영사항`·`신규시설투자등` 은 이번 계획 범위 밖이므로 `matched=False` 여도 정상이며,
각 서식 전용 정규화는 후속 계획에서 다룬다.

- [x] **Step 6: 커밋**

```bash
git add scripts/build_index.py tests/test_build_index.py
git commit -m "feat: T1 거래소공시 인덱스 빌드 CLI"
```

---

## Task 11: GET /answer 스켈레톤

주최 측이 고정한 스키마를 노출한다. 내부는 나중에 교체하되 **계약은 지금 확정**한다.

**Files:**
- Create: `src/argus/api/__init__.py`, `src/argus/api/server.py`, `tests/test_server.py`

**Interfaces:**
- Consumes: `retrieve.bm25`, `clova.chat`
- Produces: `app` (FastAPI). `GET /answer?question_id=&question=` → 5키 JSON. `GET /health` → `{"status": "ok"}`

- [x] **Step 1: 실패하는 테스트 작성**

`tests/test_server.py`:
```python
from fastapi.testclient import TestClient

from argus.api.server import app

client = TestClient(app)


def test_health():
    assert client.get("/health").json() == {"status": "ok"}


def test_answer_returns_exact_schema_keys():
    resp = client.get("/answer", params={"question_id": "Q-001", "question": "테스트"})
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"question_id", "question", "retrieved_context", "think_trace", "answer"}


def test_answer_echoes_identifiers():
    body = client.get("/answer", params={"question_id": "Q-777", "question": "삼성전자"}).json()
    assert body["question_id"] == "Q-777"
    assert body["question"] == "삼성전자"


def test_answer_requires_params():
    assert client.get("/answer").status_code == 422
```

- [x] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_server.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'argus.api'`

- [x] **Step 3: server.py 구현**

`src/argus/api/__init__.py`: 빈 파일.

```python
"""평가용 API. 응답 스키마는 주최 측 고정 계약이다 (CONVENTION §4)."""
from __future__ import annotations

from fastapi import FastAPI, Query
from pydantic import BaseModel

app = FastAPI(title="ARGUS 공시 Agent")


class AnswerResponse(BaseModel):
    question_id: str
    question: str
    retrieved_context: str
    think_trace: str
    answer: str


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/answer", response_model=AnswerResponse)
def answer(
    question_id: str = Query(...),
    question: str = Query(...),
) -> AnswerResponse:
    # TODO(Task 12+): 라우팅 -> 검색 -> critic -> HCX 생성으로 교체
    return AnswerResponse(
        question_id=question_id,
        question=question,
        retrieved_context="",
        think_trace="skeleton",
        answer="아직 구현되지 않았습니다.",
    )
```

- [x] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_server.py -v`
Expected: PASS (4 passed)

- [x] **Step 5: 로컬 기동 확인 (수동)**

Run: `uvicorn argus.api.server:app --host 0.0.0.0 --port 8000`
그리고 다른 터미널에서:
```bash
curl -s "http://127.0.0.1:8000/answer?question_id=Q-001&question=테스트"
```
Expected: 5키 JSON

실측: `PYTHONPATH=src python -m uvicorn argus.api.server:app --host 127.0.0.1 --port 8123` 를
백그라운드로 띄우고 curl 로 확인 → `/health` 는 `{"status":"ok"}`, `/answer` 는 5키 JSON.
질의를 퍼센트 인코딩(`%ED%85%8C%EC%8A%A4%ED%8A%B8`)해 보내면 `question` 이
`테스트`(테스트) 로 그대로 되돌아온다. Windows 콘솔에서 한글이 깨져 보이는 건
Git Bash 의 CP949 인자 전달 문제이지 서버 인코딩 문제가 아니다.

- [x] **Step 6: 커밋**

```bash
git add src/argus/api/__init__.py src/argus/api/server.py tests/test_server.py
git commit -m "feat: GET /answer 스켈레톤 (스키마 고정)"
```

---

## Task 12: E2E 평가 하네스

SWE-Lancer의 교훈 — **부분 지표(검색 recall)만 보면 착각한다.** 질의부터 근거 표기까지 관통해 본다.

**Files:**
- Create: `evalset/questions.jsonl`, `scripts/eval_e2e.py`, `tests/test_eval_e2e.py`

**Interfaces:**
- Consumes: `api.server.app`
- Produces:
  - `evalset/questions.jsonl` — 각 줄 `{"question_id", "question", "type", "gold", "must_cite"}`
  - `scripts/eval_e2e.py` → `artifacts/eval_report.json`
  - `score(response: dict, case: dict) -> dict` — 키 `has_schema` `answer_match` `has_citation`

- [x] **Step 1: 평가셋 작성**

`evalset/questions.jsonl` (CONVENTION §6 참고 질의 6종을 코퍼스에 맞게 구체화):
```json
{"question_id":"Q-001","question":"HD현대일렉트릭이 2023년 1월에 체결한 변압기 공급계약의 계약금액은 얼마인가?","type":"closed_lookup","gold":"97,000,000,000","must_cite":"20230131800162"}
{"question_id":"Q-002","question":"삼성전자가 2025년 7월에 체결한 반도체 위탁생산 공급계약의 매출액 대비 비율은?","type":"closed_lookup","gold":"7.6","must_cite":"20250728800035"}
{"question_id":"Q-003","question":"HD현대일렉트릭의 2023년 1월 변압기 공급계약 기간은 몇 년 몇 개월인가?","type":"closed_calc","gold":"1년 9개월","must_cite":"20230131800162"}
{"question_id":"Q-004","question":"삼성전자가 2025년에 공시한 단일판매·공급계약을 정리해줘","type":"open_summary","gold":"","must_cite":""}
{"question_id":"Q-005","question":"2023년에 계약이 해지된 사례가 존재하는가?","type":"closed_exists","gold":"","must_cite":""}
{"question_id":"Q-006","question":"존재하지 않는 기업 ZZZ의 2025년 매출액은?","type":"unanswerable","gold":"확인","must_cite":""}
```

> 정답 검증: `artifacts/exchange_records.jsonl` 대조 완료. `20230131800162` = 97,000,000,000원 ·
> 기간 2023-01-30~2024-10-31(= 1년 9개월) · `20250728800035` = 매출액 대비 7.6% ·
> 2023년 해지 공시 3건 존재. 여섯 문항 모두 코퍼스에 근거가 있다.

- [x] **Step 2: 실패하는 테스트 작성**

`tests/test_eval_e2e.py`:
```python
from scripts.eval_e2e import load_cases, score


def test_evalset_loads():
    cases = load_cases()
    assert len(cases) >= 6
    assert {"question_id", "question", "type"} <= set(cases[0])


def test_score_detects_schema_violation():
    result = score({"answer": "x"}, {"gold": "", "must_cite": ""})
    assert result["has_schema"] is False


def test_score_detects_answer_and_citation():
    resp = {
        "question_id": "Q-001", "question": "q",
        "retrieved_context": "ctx", "think_trace": "t",
        "answer": "계약금액은 97,000,000,000원입니다 (접수번호 20230131800162)",
    }
    result = score(resp, {"gold": "97,000,000,000", "must_cite": "20230131800162"})
    assert result["has_schema"] and result["answer_match"] and result["has_citation"]


def test_unanswerable_case_requires_limitation_phrase():
    resp = {
        "question_id": "Q-006", "question": "q",
        "retrieved_context": "", "think_trace": "t",
        "answer": "공시에서 확인되지 않습니다.",
    }
    assert score(resp, {"gold": "확인", "must_cite": ""})["answer_match"]
```

- [x] **Step 3: 테스트 실패 확인**

Run: `python -m pytest tests/test_eval_e2e.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts.eval_e2e'`

- [x] **Step 4: eval_e2e.py 구현**

```python
"""E2E 평가 하네스. 질의 -> 답변 -> 근거 표기까지 관통해 본다."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

REQUIRED_KEYS = {"question_id", "question", "retrieved_context", "think_trace", "answer"}


def load_cases() -> list[dict]:
    path = ROOT / "evalset" / "questions.jsonl"
    with path.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def _norm(text: str) -> str:
    return (text or "").replace(",", "").replace(" ", "")


def score(response: dict, case: dict) -> dict:
    has_schema = REQUIRED_KEYS <= set(response)
    answer = response.get("answer", "")
    gold = case.get("gold", "")
    must_cite = case.get("must_cite", "")
    return {
        "has_schema": has_schema,
        "answer_match": bool(gold) and _norm(gold) in _norm(answer),
        "has_citation": bool(must_cite) and must_cite in answer,
    }


def main() -> None:
    from fastapi.testclient import TestClient

    from argus.api.server import app

    client = TestClient(app)
    cases = load_cases()
    rows = []
    for case in cases:
        resp = client.get(
            "/answer",
            params={"question_id": case["question_id"], "question": case["question"]},
        )
        body = resp.json() if resp.status_code == 200 else {}
        rows.append({"case": case["question_id"], "type": case["type"], **score(body, case)})

    out = ROOT / "artifacts" / "eval_report.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")

    total = len(rows)
    print(f"{'case':<8}{'type':<16}{'schema':>8}{'answer':>8}{'cite':>7}")
    for row in rows:
        print(f"{row['case']:<8}{row['type']:<16}{str(row['has_schema']):>8}"
              f"{str(row['answer_match']):>8}{str(row['has_citation']):>7}")
    print(f"\n스키마 준수 {sum(r['has_schema'] for r in rows)}/{total} · "
          f"정답 일치 {sum(r['answer_match'] for r in rows)}/{total} · "
          f"근거 표기 {sum(r['has_citation'] for r in rows)}/{total}")


if __name__ == "__main__":
    main()
```

`scripts/__init__.py`: 빈 파일 (테스트 import 용).

> `pyproject.toml` 의 `pythonpath` 를 `["src"]` → `["src", "."]` 로 넓혔다.
> `python -m pytest` 는 cwd 를 `sys.path` 에 넣어 주지만 `pytest` 로 직접 부르면 넣지 않아
> `from scripts.eval_e2e import ...` 가 `ModuleNotFoundError` 로 깨진다. 호출 방식에
> 의존하지 않도록 ini 에 못 박았다.

- [x] **Step 5: 테스트 통과 확인**

Run: `python -m pytest tests/test_eval_e2e.py -v`
Expected: PASS (4 passed)

- [x] **Step 6: 하네스 실행 (수동)**

Run: `python scripts/eval_e2e.py`
Expected: 스켈레톤이라 정답 일치 0/6. **스키마 준수 6/6이면 성공** — 이 시점의 목적은 계측선 확보다.

실측 결과 (스켈레톤 기준선):

```
case    type              schema  answer   cite
Q-001   closed_lookup       True   False  False
Q-002   closed_lookup       True   False  False
Q-003   closed_calc         True   False  False
Q-004   open_summary        True   False  False
Q-005   closed_exists       True   False  False
Q-006   unanswerable        True   False  False

스키마 준수 6/6 · 정답 일치 0/6 · 근거 표기 0/6
```

- [x] **Step 7: 커밋**

```bash
git add evalset/questions.jsonl scripts/eval_e2e.py scripts/__init__.py tests/test_eval_e2e.py
git commit -m "feat: E2E 평가 하네스와 초기 평가셋"
```

---

## Task 13: 배포 산출물

CONVENTION §9 제출 요건이다. **지금 만드는 게 그대로 제출물**이 된다.

**Files:**
- Create: `deploy/setup.sh`, `deploy/Dockerfile`, `deploy/argus.service`, `deploy/README.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: Task 11의 `argus.api.server:app`
- Produces: 실행 가능한 배포 스크립트 3종

- [x] **Step 1: setup.sh 작성**

```bash
#!/usr/bin/env bash
# ARGUS 평가용 서버 초기화 (Ubuntu 24.04, 2 vCPU / 4 GB)
set -euo pipefail

apt-get update
apt-get install -y python3-pip python3-venv git

# 타임존 — 로그 타임스탬프를 KST 로
timedatectl set-timezone Asia/Seoul

# swap 2GB — 기본 이미지에 swap 이 0B 이라 인덱스 로딩 시 OOM 이 난다
if [ ! -f /swapfile ]; then
  fallocate -l 2G /swapfile
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

# 애플리케이션
APP_DIR=/opt/argus
mkdir -p "$APP_DIR"
python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install --upgrade pip
"$APP_DIR/.venv/bin/pip" install -e "$APP_DIR"

echo "setup 완료. .env 와 artifacts/ 를 $APP_DIR 에 배치한 뒤 argus.service 를 등록하세요."
```

- [x] **Step 2: argus.service 작성**

```ini
[Unit]
Description=ARGUS 공시 Agent API
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/argus
EnvironmentFile=/opt/argus/.env
ExecStart=/opt/argus/.venv/bin/uvicorn argus.api.server:app --host 0.0.0.0 --port 80
Restart=always
RestartSec=3
StandardOutput=append:/var/log/argus.log
StandardError=append:/var/log/argus.log

[Install]
WantedBy=multi-user.target
```

- [x] **Step 3: Dockerfile 작성**

```dockerfile
FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml ./
COPY src/ ./src/
RUN pip install --no-cache-dir -e .

COPY artifacts/ ./artifacts/

EXPOSE 80
CMD ["uvicorn", "argus.api.server:app", "--host", "0.0.0.0", "--port", "80"]
```

- [x] **Step 4: deploy/README.md 작성**

```markdown
# 배포 절차

## 서버 준비 (NCP, Ubuntu 24.04, c2-g3a 2vCPU/4GB)
1. VPC `argus` / Subnet `argus-subnet`(Public, 192.168.1.0/24) / **시간 요금제** / 공인 IP 신청
2. ACG Inbound: 22(관리자 IP) · 80 · 443(0.0.0.0/0) — 3389 불필요
   ACG Outbound: TCP 0.0.0.0/0 → 1-65535 (**CLOVA API 호출에 필수**)

## 설치
```bash
scp -r . root@<IP>:/opt/argus
ssh root@<IP> 'bash /opt/argus/deploy/setup.sh'
scp .env root@<IP>:/opt/argus/.env
scp -r artifacts root@<IP>:/opt/argus/
ssh root@<IP> 'cp /opt/argus/deploy/argus.service /etc/systemd/system/ && \
  systemctl daemon-reload && systemctl enable --now argus'
```

## 확인
```bash
curl -s "http://<IP>/answer?question_id=Q-001&question=테스트"
```

## Docker 대안
```bash
docker build -f deploy/Dockerfile -t argus .
docker run -d --env-file .env -p 80:80 argus
```
```

- [ ] **Step 5: Dockerfile 빌드 검증 (수동)** — SKIPPED (개발 머신에 Docker 미설치, 8월 말 리허설로 이월)

Run: `docker build -f deploy/Dockerfile -t argus-test .`
Expected: 빌드 성공. Docker 미설치면 이 단계는 8월 말 리허설로 미룬다.

Docker 없이 가능한 만큼은 대신 검증했다:

- `bash -n deploy/setup.sh` — 문법 통과
- `deploy/argus.service` — `configparser` 로 `[Unit]/[Service]/[Install]` 3섹션과 `ExecStart` 파싱 확인
- Dockerfile 의 `RUN pip install --no-cache-dir -e .` 에 해당하는 패키징 단계를
  `pyproject.toml` + `src/` 만 복사한 임시 컨텍스트에서 재현 → editable 설치 성공,
  `argus` · `argus.api.server` · `argus.corpus.*` · `argus.retrieve.*` 전부 해소됨
  (`--no-deps` 로 돌려 서드파티만 미설치). 즉 패키지 탐색(`where = ["src"]`)에 구멍은 없다.
- 세 파일 모두 LF 개행 확인. `.gitattributes` 에 `*.sh`/`*.service`/`Dockerfile` 을
  `eol=lf` 로 고정했다 — `core.autocrlf=true` 인 Windows 작업 사본에서 그대로 `scp` 하면
  `/bin/bash^M: bad interpreter` 로 죽기 때문이다.

**남은 미검증 항목**: 이미지 실제 빌드·기동, `COPY artifacts/`(빌드 컨텍스트에 `artifacts/`
가 있어야 성공), systemd 등록.

- [x] **Step 6: 커밋**

```bash
git add deploy/ README.md
git commit -m "chore: 배포 스크립트와 Dockerfile (제출 요건)"
```

---

## Self-Review

**스펙 커버리지** — 8개 항목 매핑

| 원래 항목 | 담당 태스크 |
|---|---|
| 1 `GET /answer` 스켈레톤 | Task 11 |
| 2 HCX 호출 래퍼 | Task 2 |
| 3 예산표 확정 | 계획 범위 밖 (NCP 요금 확인 대기) |
| 4 CoT A/B | 완료 (`clova_verify.ipynb`) |
| 5 LLM-free 파이프라인 | Task 3·4·5·6·7·8·9·10 |
| 6 T1 n-ary 인덱싱 | Task 10 (LLM 없는 규칙 추출까지). LLM 보강은 후속 |
| 7 E2E 평가 하네스 | Task 12 |
| 8 setup.sh + Dockerfile | Task 13 |

**미커버 항목**: 예산표(단가 대기), 라우터·ToG-2 루프·critic — 이번 계획은 **기반 구축**까지이고, 검색·추론 계층은 이 위에 올리는 후속 계획으로 분리한다.

**타입 일관성 확인**: `normalize_exchange` 가 내는 키를 `to_markdown` · `attach_related` · `build_index` 가 동일하게 쓴다. `Bm25Index.build` 의 입력 `{"doc_id","text","meta"}` 는 Task 10에서 그대로 구성한다. `score()` 가 검사하는 5키는 `AnswerResponse` 필드와 일치한다.

**리스크**: Task 6의 `_find` 라벨 매칭이 공시 서식 버전에 따라 빗나갈 수 있다. Task 10 Step 5에서 skip 비율로 조기 검출하도록 배치했다.
