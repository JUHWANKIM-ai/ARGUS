"""A2 — CLOVA Studio 임베딩 단일 창구 + 벡터 저장소 + RRF 융합. 생성 호출 0회.

## 왜 CLOVA Studio 임베딩인가 (G1)

CONVENTION **G1** 은 "LLM 은 HyperCLOVA X 만" 이다. 임베딩은 **생성이 아니라 검색**이지만
회색지대이므로, 같은 플랫폼·같은 API 키로 제공되는 CLOVA Studio 임베딩을 쓴다.
외부 모델(HuggingFace 등)을 쓰면 `docs/clova_payment.md` §2.1 이 금지하는
"외부 모델로 만든 인덱스를 서빙" 에 정확히 걸린다 — **인덱스는 곧 제품**이다.

실측으로 고른 모델 (2026-08-09, 세 후보 전부 200 응답 확인):

| 모델 | 경로 | 차원 | 1,200자 입력 |
|---|---|---|---|
| `clir-emb-dolphin` | `/v1/api-tools/embedding/clir-emb-dolphin` | 1024 | **40003 Context length exceeded** |
| `clir-sts-dolphin` | `/v1/api-tools/embedding/clir-sts-dolphin` | 1024 | 상동 |
| **`bge-m3`** | **`/v1/api-tools/embedding/v2`** | **1024** | 200 (1,051 토큰) |

정기공시 색인 문자열은 `SECTION_INDEX_CHARS=1200` 자라 dolphin 두 모델은 **애초에 못 넣는다**
(512 토큰 상한). 그래서 `bge-m3` (Embedding v2) 하나만 후보로 남는다.

## 비용 회계

응답의 `result.inputTokens` 가 실측 usage 다. 단가는 NCP 콘솔 값을 이 저장소에서 직접
확인할 수 없어 **토큰 수를 1차 산출물로 남기고** 원화는 `PRICE_WON_PER_1K` 로 환산한다
(근거: NCP 포럼 답변 — 임베딩 토큰당 0.0001원 = 0.1원/1K). 단가가 바뀌면 이 상수만 바꾸면
모든 보고가 다시 계산된다. **추정 토큰이 아니라 실측 `inputTokens` 만 누적한다.**

## 예산 가드

`USAGE.limit_won` 을 넘는 순간 `EmbedBudgetExceeded` 를 던진다. 임베딩은 배치 입력을
지원하지 않아 (실측: `text` 에 리스트를 넣으면 40001) 호출이 문서 수만큼 나간다 —
가드가 없으면 루프 하나가 예산을 통째로 태운다.
"""
from __future__ import annotations

import hashlib
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from argus import clova
from argus.config import CLOVA_HOST, settings

#: Embedding v2. 모델은 bge-m3 하나이며 경로에 모델명이 들어가지 않는다.
EMBED_PATH = "/v1/api-tools/embedding/v2"
EMBED_MODEL = "bge-m3"
EMBED_DIM = 1024

#: 임베딩 단가(원/1K 토큰). 생성 단가 7.02원/1K 과 **다른 값**이다 — 70배 싸다.
PRICE_WON_PER_1K = 0.1

#: RRF 상수. TREC 관행값 60 을 그대로 쓴다 (Cormack et al. 2009).
#: 값이 클수록 두 순위의 상위권 차이가 뭉개진다 — 후보가 한 자릿수인 우리 풀에서는
#: 사실상 '두 리스트에 다 들면 이긴다' 규칙으로 동작한다.
RRF_K = 60

#: 융합 전 각 리스트에서 뽑아 둘 길이. 최종 top_k 보다 넉넉해야 dense 가 **새 문서를
#: 올릴 수 있다** — 두 리스트를 top_k 로 잘라 융합하면 순서만 바뀌고 집합은 안 바뀐다.
RRF_FETCH_MULT = 4
RRF_FETCH_MIN = 20

#: 한 검색 호출에서 dense 로 훑을 후보 상한. 메타 프리필터가 후보를 이미 좁히므로
#: (실측 dev 42문항 풀 중앙값 2 · 최대 554) 이 상한에 걸리는 호출은 없어야 정상이다.
#: 걸리면 dense 를 건너뛰고 트레이스에 남긴다 — 조용히 전 코퍼스를 임베딩하면 안 된다.
DENSE_MAX_POOL = 2000

EMBED_CACHE_DIRNAME = "embeddings"

_RETRY_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})
_RETRY_ATTEMPTS = 6
_RETRY_BASE_S = 1.5

# --- 처리율 (실측 rate limit) ---------------------------------------------------
# 🔴 **분당 60호출 고정창이다.** 60초 냉각 뒤 무간격 직렬 200연타를 재니 정확히 이렇게 나왔다:
#
#     1~60번  전부 200            (7초)
#     61번부터 200번  전부 429
#     응답 헤더  x-ratelimit-limit-requests: 60 · remaining: 0 · reset: 53s
#                x-ratelimit-limit-tokens:  40000 · remaining: 39,700
#
# 처음에 "직렬이면 465건/분" 으로 읽었던 것은 **쌓여 있던 창 하나를 태운 것**이었다.
# 병렬화는 그 창을 더 빨리 태울 뿐 총량을 못 늘린다 — 그래서 워커는 1, 간격은 1초다.
#
# 이 한 줄이 A2 의 범위를 정한다:
#
#     정형 3종  3,150건  ->  약 55분
#     정기공시 전량 157,397건  ->  **약 46시간**   ← 상주·비용과 별개로 이것만으로도 불가
#
# 토큰 창(40,000/분)도 있다. 정기공시 조각은 1건 약 600토큰이라 57호출/분 × 600 =
# 34,200 으로 아슬아슬하게 안쪽이다. 넘으면 429 가 오고 아래 회복 로직이 받는다.
_MIN_INTERVAL_S = 1.05          # 57건/분 — 고정창 60 의 95%
_MAX_INTERVAL_S = 4.0
_RECOVER = 0.999
#: 429 를 받으면 창이 리셋될 때까지 기다린다. 지수 백오프보다 이게 정확하다 —
#: 고정창이라 '조금 더 천천히' 가 아니라 '창이 바뀔 때까지' 가 답이다.
_WINDOW_S = 20.0
#: 기본 동시성. 고정창이라 병렬화가 총량을 못 늘린다.
WORKERS = 1

_pace_lock = threading.Lock()
_pace = {"interval": _MIN_INTERVAL_S, "next": 0.0}


def _wait_turn() -> None:
    with _pace_lock:
        now = time.monotonic()
        slot = max(now, _pace["next"])
        _pace["next"] = slot + _pace["interval"]
        _pace["interval"] = max(_MIN_INTERVAL_S, _pace["interval"] * _RECOVER)
    delay = slot - time.monotonic()
    if delay > 0:
        time.sleep(delay)


def _slow_down() -> None:
    with _pace_lock:
        _pace["interval"] = min(_MAX_INTERVAL_S, _pace["interval"] * 1.2)
        _pace["next"] = max(_pace["next"], time.monotonic() + _WINDOW_S)


class EmbedBudgetExceeded(RuntimeError):
    """임베딩 예산 상한. 우리 규칙이지 API 오류가 아니다."""


@dataclass
class EmbedUsage:
    """실측 usage 누적기. `clova.CALL_LOG` 과 같은 자리의 물건이다."""

    calls: int = 0
    tokens: int = 0
    cache_hits: int = 0
    limit_won: float | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def clear(self) -> None:
        with self._lock:
            self.calls = self.tokens = self.cache_hits = 0

    def add(self, tokens: int) -> None:
        with self._lock:
            self.calls += 1
            self.tokens += tokens
            if self.limit_won is not None and self.won > self.limit_won:
                raise EmbedBudgetExceeded(
                    f"임베딩 예산 {self.limit_won:,.0f}원 초과 "
                    f"(실측 {self.tokens:,} 토큰 = {self.won:,.1f}원)")

    def hit(self) -> None:
        with self._lock:
            self.cache_hits += 1

    @property
    def won(self) -> float:
        return self.tokens / 1000.0 * PRICE_WON_PER_1K

    def totals(self) -> dict:
        return {"calls": self.calls, "tokens": self.tokens,
                "cache_hits": self.cache_hits, "won": round(self.won, 2),
                "price_won_per_1k": PRICE_WON_PER_1K, "model": EMBED_MODEL}


USAGE = EmbedUsage()


def embed_url() -> str:
    return CLOVA_HOST + EMBED_PATH


def embed_text(text: str) -> np.ndarray:
    """문자열 하나 -> L2 정규화된 float32 벡터. **API 호출 1회.**

    배치 입력은 지원되지 않는다 (실측: 리스트를 주면 40001 Invalid parameter).
    그래서 문서 수만큼 호출이 나가고, 병렬화는 `embed_many` 가 맡는다.
    """
    body = {"text": text or " "}
    headers = {"Authorization": f"Bearer {settings().clova_api_key}"}
    for attempt in range(_RETRY_ATTEMPTS):
        _wait_turn()
        status, payload, _ = clova._post(embed_url(), headers, body)
        if status == 429:
            _slow_down()
        if status == 200:
            result = payload.get("result") or {}
            vector = np.asarray(result.get("embedding") or (), dtype=np.float32)
            if vector.size != EMBED_DIM:
                raise RuntimeError(f"임베딩 차원 이상: {vector.size}")
            USAGE.add(int(result.get("inputTokens") or 0))
            return normalize(vector)
        if status not in _RETRY_STATUS or attempt == _RETRY_ATTEMPTS - 1:
            raise RuntimeError(f"임베딩 호출 실패 {status}: {payload}")
        # 429 는 `_slow_down` 이 이미 창 리셋만큼 밀어 뒀다. 여기서 또 지수로 자면
        # 두 번 기다리는 셈이라 빌드가 몇 배로 늘어난다.
        if status != 429:
            time.sleep(_RETRY_BASE_S * (2 ** attempt))
    raise RuntimeError("도달 불가")


def embed_many(texts: list[str], workers: int = WORKERS,
               on_progress=None) -> np.ndarray:
    """여러 문자열을 임베딩한다. 순서는 입력과 같다.

    기본 워커는 **1** 이다 (`WORKERS`) — 이 API 는 동시 호출에서 429 를 내므로 병렬화가
    처리율을 떨어뜨린다. 풀 구조는 남겨 둔다: 상한이 바뀌면 상수 하나로 되돌린다.
    예산 초과(`EmbedBudgetExceeded`)는 **삼키지 않는다** — 남은 작업을 취소하고 올린다.
    """
    if not texts:
        return np.zeros((0, EMBED_DIM), dtype=np.float32)
    out: list[np.ndarray | None] = [None] * len(texts)
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {pool.submit(embed_text, t): i for i, t in enumerate(texts)}
        done = 0
        for future, i in futures.items():
            out[i] = future.result()
            done += 1
            if on_progress and done % 200 == 0:
                on_progress(done, len(texts))
    return np.vstack([v for v in out if v is not None])


def normalize(mat: np.ndarray) -> np.ndarray:
    """L2 정규화. 코사인 유사도를 내적 한 번으로 끝내기 위한 전처리다."""
    arr = np.asarray(mat, dtype=np.float32)
    if arr.ndim == 1:
        norm = float(np.linalg.norm(arr)) or 1.0
        return arr / norm
    norms = np.linalg.norm(arr, axis=1, keepdims=True)
    return arr / np.where(norms == 0, 1.0, norms)


def rrf(*ranked: list[str], k: int = RRF_K, top_k: int | None = None) -> list[tuple[str, float]]:
    """Reciprocal Rank Fusion. 점수 스케일이 다른 순위표를 **순위만으로** 합친다.

        score(d) = Σ_l 1 / (k + rank_l(d))

    BM25 점수와 코사인 유사도는 단위가 달라 가중합을 하려면 정규화 상수를 하나 더
    도입해야 하고, 그 상수가 곧 숨은 하이퍼파라미터가 된다. RRF 는 그 상수를 없앤다 —
    `k` 하나뿐이고 관행값이 있다. 동점은 **첫 번째 리스트 우선**으로 안정 정렬한다.
    """
    scores: dict[str, float] = {}
    order: dict[str, int] = {}
    for lane, ids in enumerate(ranked):
        for rank, doc_id in enumerate(ids, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)
            if doc_id not in order:
                order[doc_id] = lane * 10_000 + rank
    fused = sorted(scores.items(), key=lambda kv: (-kv[1], order[kv[0]]))
    return fused[:top_k] if top_k else fused


def fetch_depth(top_k: int) -> int:
    return max(RRF_FETCH_MIN, RRF_FETCH_MULT * max(top_k, 1))


# --- 벡터 저장소 ---------------------------------------------------------------
# 게이트 500MB 안에 들어야 한다. 상주량은 **행 수 × 차원 × dtype** 이 전부다.
#
#   float32 · 1024차원 · 157,397 조각 = 615MB   ← 이것만으로 게이트 초과
#   float16 · 1024차원 ·   3,150 레코드 = 6.4MB
#
# 그래서 ① 저장·상주 dtype 을 **float16** 으로 두고 (내적 직전에만 float32 로 올린다),
# ② 정기공시는 전량이 아니라 **메타 프리필터를 통과한 후보만** 담는다. ②가 측정을
# 훼손하지 않는 이유는 융합이 어차피 프리필터 뒤에서 일어나기 때문이다 — 프리필터가
# 거른 문서는 dense 인덱스에 있어도 후보가 아니다.


class VectorStore:
    """id -> 벡터. float16 상주 · npz 1파일. 없는 id 는 `ensure` 가 채운다."""

    def __init__(self, ids=(), vectors: np.ndarray | None = None) -> None:
        self.ids: list[str] = list(ids)
        if vectors is None or len(self.ids) == 0:
            self.vectors = np.zeros((0, EMBED_DIM), dtype=np.float16)
        else:
            self.vectors = np.asarray(vectors, dtype=np.float16)
        self._pos = {doc_id: i for i, doc_id in enumerate(self.ids)}
        self._lock = threading.Lock()

    def __len__(self) -> int:
        return len(self.ids)

    def __contains__(self, doc_id: str) -> bool:
        return doc_id in self._pos

    @property
    def nbytes(self) -> int:
        return int(self.vectors.nbytes)

    def add(self, ids: list[str], vectors: np.ndarray) -> None:
        """새 id 만 덧붙인다. 이미 있는 id 는 버린다 — 중복 행은 순위를 두 번 세게 한다."""
        block = np.atleast_2d(np.asarray(vectors, dtype=np.float16))
        fresh = [(i, d) for i, d in enumerate(ids) if d not in self._pos]
        if not fresh:
            return
        with self._lock:
            self.vectors = np.vstack([self.vectors, block[[i for i, _ in fresh]]])
            for _, doc_id in fresh:
                self._pos[doc_id] = len(self.ids)
                self.ids.append(doc_id)

    def ensure(self, ids: list[str], text_of, workers: int = WORKERS,
               batch: int = 250, on_batch=None) -> int:
        """없는 id 만 임베딩해 채운다. 이미 있으면 **호출하지 않는다**.

        배치로 끊어 담고 배치마다 `on_batch` 를 부른다. 3,150건을 한 덩어리로 돌리다
        429 하나로 죽으면 **이미 지불한 토큰이 통째로 날아간다** — 실측으로 그 사고를 냈다.
        예외는 삼키지 않는다. 그때까지 담긴 것은 저장소에 남는다.

        🔴 **키가 `doc_id` 뿐이고 내용 해시가 없다 — 텍스트가 바뀌면 벡터가 조용히 낡는다.**
        `missing` 은 "저장소에 그 id 가 없는가" 만 보므로, 같은 id 의 본문이 재빌드로
        바뀌어도 **옛 텍스트의 벡터가 그대로 남고 아무 신호도 안 난다.**

        R2D(2026-08-14)의 색인 재빌드가 그 조건을 실제로 만들었다. `9c4fbcf` 가
        `schema_matched=false` 서식에 자유 텍스트를 실으면서 exchange **233건**의
        `markdown` 이 바뀌었는데, 그 233건은 **233/233 전부** 이미
        `artifacts/embeddings/exchange_dense.npz` 에 벡터가 있다. 즉 지금 이 저장소의
        exchange dense 벡터 233건은 **출하된 색인 문자열과 다른 텍스트의 벡터**다.

        지금은 안 밟는다 — **측정되어 저장된** 산출물 중 `dense=true` 인 것이 한 건도
        없다(`artifacts/ablation/**/report.json` 29개 전수 · `arm_config.dense` 전부 거짓).
        정의만 남아 있다(`ablation.ARMS` 의 `A2` · `A2F`). **그 둘을 다시 세우면 밟는다.**
        되살릴 때 할 일: 그 233건 id 를 `npz` 에서 지우고 다시 임베딩한다(크레딧이 든다).
        근본 처방은 키에 본문 해시를 더해 `missing` 판정을 내용 기반으로 바꾸는 것이고,
        그러면 재빌드가 재임베딩 대상을 **스스로 신고**한다.
        """
        missing = [d for d in dict.fromkeys(ids) if d not in self._pos]
        if not missing:
            USAGE.hit()
            return 0
        done = 0
        for start in range(0, len(missing), max(1, batch)):
            chunk = missing[start:start + max(1, batch)]
            vectors = embed_many([text_of(d) for d in chunk], workers=workers)
            self.add(chunk, vectors)
            done += len(chunk)
            if on_batch:
                on_batch(done, len(missing))
        return done

    def rank(self, query: np.ndarray, candidates: list[str],
             top_k: int) -> list[tuple[str, float]]:
        """후보 안에서 코사인 상위 top_k. 저장소에 없는 후보는 조용히 빠진다."""
        rows, kept = [], []
        for doc_id in candidates:
            i = self._pos.get(doc_id)
            if i is not None:
                rows.append(i)
                kept.append(doc_id)
        if not rows:
            return []
        mat = self.vectors[np.asarray(rows, dtype=np.int64)].astype(np.float32)
        scores = mat @ np.asarray(query, dtype=np.float32)
        order = np.argsort(-scores, kind="stable")[:top_k]
        return [(kept[int(i)], float(scores[int(i)])) for i in order]

    # --- 저장 -----------------------------------------------------------------
    def save(self, path) -> None:
        target = Path(path).with_suffix(".npz")
        target.parent.mkdir(parents=True, exist_ok=True)
        np.savez(target, ids=np.asarray(self.ids, dtype=object),
                 vectors=self.vectors, model=np.asarray(EMBED_MODEL))

    @classmethod
    def load(cls, path) -> "VectorStore":
        target = Path(path).with_suffix(".npz")
        with np.load(target, allow_pickle=True) as npz:
            return cls(list(npz["ids"]), npz["vectors"])

    @staticmethod
    def exists(path) -> bool:
        return Path(path).with_suffix(".npz").exists()

    @classmethod
    def open(cls, path) -> "VectorStore":
        return cls.load(path) if cls.exists(path) else cls()


# --- 질의 임베딩 캐시 -----------------------------------------------------------
# 같은 질의 문자열이 hop·기업 셀마다 되풀이된다 (실측 dev 42문항에서 search_chunks 142회에
# 고유 질의는 그 절반 이하다). 캐시가 없으면 같은 벡터를 여러 번 산다.

_QUERY_CACHE: dict[str, np.ndarray] = {}
_QUERY_STORE_NAME = "query_vectors"


def query_vector(text: str) -> np.ndarray:
    key = hashlib.sha256((text or "").encode("utf-8")).hexdigest()
    hit = _QUERY_CACHE.get(key)
    if hit is not None:
        USAGE.hit()
        return hit
    store = _query_store()
    i = store._pos.get(key)
    if i is not None:
        vector = store.vectors[i].astype(np.float32)
        _QUERY_CACHE[key] = vector
        USAGE.hit()
        return vector
    vector = embed_text(text)
    store.add([key], vector.reshape(1, -1))
    store.save(cache_dir() / _QUERY_STORE_NAME)
    _QUERY_CACHE[key] = vector
    return vector


def cache_dir() -> Path:
    path = settings().artifacts_root / EMBED_CACHE_DIRNAME
    path.mkdir(parents=True, exist_ok=True)
    return path


_QUERY_STORE: VectorStore | None = None


def _query_store() -> VectorStore:
    global _QUERY_STORE
    if _QUERY_STORE is None:
        _QUERY_STORE = VectorStore.open(cache_dir() / _QUERY_STORE_NAME)
    return _QUERY_STORE


def reset_caches() -> None:
    """테스트·재빌드용. 디스크는 건드리지 않는다."""
    global _QUERY_STORE
    _QUERY_STORE = None
    _QUERY_CACHE.clear()
