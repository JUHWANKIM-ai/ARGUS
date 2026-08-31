"""Single entry point for CLOVA Studio embeddings, the vector store and RRF
fusion. No generation calls.

The platform is fixed by the convention that only the sanctioned LLM may be
used: an embedding is retrieval rather than generation, but the index it builds
ships as part of the product, so it uses the same platform and the same API
key. Model choice, cost accounting and the budget guard are recorded
separately.
See docs/design-rationale.md#embedding-platform
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

#: Embedding v2. There is one model and the path does not name it.
EMBED_PATH = "/v1/api-tools/embedding/v2"
EMBED_MODEL = "bge-m3"
EMBED_DIM = 1024

#: Embedding price per 1K tokens. A different figure from the generation
#: price, roughly seventy times cheaper.
#: See docs/design-rationale.md#embedding-platform
PRICE_WON_PER_1K = 0.1

#: RRF constant, the conventional value from the literature. A larger value
#: flattens the difference between the tops of the two rankings, which on a
#: single-digit candidate pool amounts to rewarding presence in both lists.
#: See docs/design-rationale.md#rrf-fusion
RRF_K = 60

#: Depth taken from each list before fusing. It has to exceed the final top_k
#: for the dense side to promote a new document at all: fusing two lists
#: already cut to top_k changes the order but not the set.
RRF_FETCH_MULT = 4
RRF_FETCH_MIN = 20

#: Cap on candidates scanned densely in one retrieval call. The meta prefilter
#: already narrows the pool, so no call should reach this cap; one that does
#: skips the dense pass and records that in the trace, rather than silently
#: embedding the whole corpus.
DENSE_MAX_POOL = 2000

EMBED_CACHE_DIRNAME = "embeddings"

_RETRY_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})
_RETRY_ATTEMPTS = 6
_RETRY_BASE_S = 1.5

# --- Throughput, measured against the published rate limit ------------------
# The limit is a fixed window of sixty calls per minute. Parallelism only burns
# that window faster and cannot raise the total, so there is one worker and a
# one-second interval. A token window applies as well and sits just inside the
# limit at this pace.
# See docs/design-rationale.md#embedding-rate-limit
_MIN_INTERVAL_S = 1.05          # 57 per minute, 95% of the window
_MAX_INTERVAL_S = 4.0
_RECOVER = 0.999
#: On a rate-limit response, wait for the window to reset. That is more
#: accurate than exponential backoff: with a fixed window the answer is not to
#: go slightly slower but to wait for the window to turn over.
_WINDOW_S = 20.0
#: Default concurrency. With a fixed window, parallelism cannot raise the
#: total.
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
    """Embedding budget ceiling. A rule of this codebase rather than an API
    error."""


@dataclass
class EmbedUsage:
    """Accumulator for measured usage, the counterpart of the generation call
    log."""

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
    """One string to an L2-normalised float32 vector. One API call.

    Batch input is not supported, so one call goes out per document and the
    batching helper handles the loop.
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
        # A rate-limit response has already been pushed out by a full window,
        # so sleeping exponentially here would wait twice and multiply the
        # build time.
        if status != 429:
            time.sleep(_RETRY_BASE_S * (2 ** attempt))
    raise RuntimeError("도달 불가")


def embed_many(texts: list[str], workers: int = WORKERS,
               on_progress=None) -> np.ndarray:
    """Embed several strings, preserving input order.

    The default worker count is one, because concurrent calls hit the rate
    limit and lower throughput. The pool structure is kept so that a changed
    limit is a one-constant revert. A budget overrun is not swallowed: the
    remaining work is cancelled and the error propagates.
    See docs/design-rationale.md#embedding-rate-limit
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
    """L2 normalisation, so cosine similarity reduces to a single dot
    product."""
    arr = np.asarray(mat, dtype=np.float32)
    if arr.ndim == 1:
        norm = float(np.linalg.norm(arr)) or 1.0
        return arr / norm
    norms = np.linalg.norm(arr, axis=1, keepdims=True)
    return arr / np.where(norms == 0, 1.0, norms)


def rrf(*ranked: list[str], k: int = RRF_K, top_k: int | None = None) -> list[tuple[str, float]]:
    """Reciprocal rank fusion: combine rankings of incompatible score scales
    using the ranks alone.

        score(d) = Σ_l 1 / (k + rank_l(d))

    A weighted sum would require a normalisation constant, which becomes a
    hidden hyperparameter. This removes it: there is one constant and it has a
    conventional value. Ties are broken stably in favour of the first list.
    See docs/design-rationale.md#rrf-fusion
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


# --- Vector store -----------------------------------------------------------
# The resident size has to stay inside the memory gate, and it is exactly rows
# times dimensions times dtype width. Storing at half precision and admitting
# only prefiltered candidates keeps it there, and the second choice does not
# distort the measurement because fusion happens after the prefilter anyway.
# See docs/design-rationale.md#vector-store-size


class VectorStore:
    """Id to vector, resident at half precision in a single npz file. Missing
    ids are filled in by `ensure`."""

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
        """Append only new ids. A duplicate row would let one document count
        twice in the ranking."""
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
        """Embed and store only the ids that are missing. Ids already present
        cost no call.

        Work is committed in batches with a callback per batch: running
        thousands of documents as one unit and dying on a single rate-limit
        response discards tokens already paid for. Exceptions are not
        swallowed, and whatever was stored up to that point remains.

        The key is the document id alone, with no content hash, so a rebuilt
        body leaves a stale vector in place and signals nothing. That condition
        exists in the current store and is not addressed here, because no
        recorded measurement uses the dense path.
        See docs/design-rationale.md#embedding-staleness
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
        """Top-k by cosine within the candidate set. Candidates absent from the
        store drop out silently."""
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

    # --- Persistence ----------------------------------------------------------
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


# --- Query embedding cache --------------------------------------------------
# The same query string recurs across hops and per-company cells, with fewer
# than half of the retrieval calls carrying a distinct query. Without a cache
# the same vector is paid for repeatedly.

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
    #: Embeddings are a branch we measured and rejected, and the production
    #: path never opens them. Being no part of the submission, they live under
    #: `scratch_root` (see the two-roots comment in `config.settings`).
    path = settings().scratch_root / EMBED_CACHE_DIRNAME
    path.mkdir(parents=True, exist_ok=True)
    return path


_QUERY_STORE: VectorStore | None = None


def _query_store() -> VectorStore:
    global _QUERY_STORE
    if _QUERY_STORE is None:
        _QUERY_STORE = VectorStore.open(cache_dir() / _QUERY_STORE_NAME)
    return _QUERY_STORE


def reset_caches() -> None:
    """For tests and rebuilds. Does not touch the disk."""
    global _QUERY_STORE
    _QUERY_STORE = None
    _QUERY_CACHE.clear()
