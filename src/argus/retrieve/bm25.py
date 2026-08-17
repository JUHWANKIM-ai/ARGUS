"""BM25 retrieval indexes with an optional dense hybrid. No LLM calls.

Two indexes exist.

`Bm25Index` holds exchange structured records and carries the full markdown.
`SectionBm25Index` holds periodic report sections as a CSR inverted index and
does not carry body text.

They are kept separate because merging them degrades exchange recall. Dense
sidecars, when enabled, attach per document group in the same way.

Dense ranking is applied after the metadata prefilter, not before, so the
dense store only has to cover candidates that survive filtering.

The score returned to callers is always the raw BM25 score. Downstream
sufficiency checks are calibrated on the BM25 scale, so emitting fused scores
would change hop behaviour rather than ranking.
"""
from __future__ import annotations

import json
import re
from array import array
from pathlib import Path

import numpy as np
from rank_bm25 import BM25Okapi

from argus import ablation, embed

# Filing form names contain the middle dot U+318D, which lies outside the
# Hangul syllable range and would otherwise split a token.
_MIDDOT = re.compile(r"[ㆍ·・･]")
_TOKEN = re.compile(r"[0-9]+(?:[,.][0-9]+)*|[가-힣]+|[A-Za-z]+")

BIGRAM_MIN = 3   # only Korean tokens at least this long are split into bigrams


def _bigrams(word: str) -> list[str]:
    return [word[i:i + 2] for i in range(len(word) - 1)]


def tokenize_words(text: str) -> list[str]:
    """Word tokens only, without bigrams."""
    return _TOKEN.findall(_MIDDOT.sub("", text or ""))


def tokenize(text: str) -> list[str]:
    """Word tokens plus Hangul character bigrams.

    Korean compound nouns form a single token, so word-level matching alone
    misses partial queries. Character bigrams provide the overlap without the
    memory cost of a morphological analyser.
    """
    words = tokenize_words(text)
    tokens = list(words)
    for word in words:
        if len(word) >= BIGRAM_MIN and "가" <= word[0] <= "힣":
            tokens.extend(_bigrams(word))
    return tokens


#: Tokeniser used to build the index, reapplied at query time. Mixing the
#: two silently degrades recall.
TOKENIZERS = {"bigram": tokenize, "word": tokenize_words}


# --- Dense sidecar --------------------------------------------------------
# Vector store attached beside an index. Active only when the dense switch is
# on; otherwise no query embedding is requested even if the file exists.

#: Fusion record from the most recent `search()`, surfaced in `think_trace`
#: so that dense activity can be inspected without additional cost.
LAST_FUSION: dict | None = None

DENSE_DIRNAME = embed.EMBED_CACHE_DIRNAME


def dense_path(name: str):
    from argus.config import settings
    return settings().artifacts_root / DENSE_DIRNAME / f"{name}_dense"


class DenseSide:
    """Vector store with lazy embedding of candidates. One per index.

    `text_of` must return the same string BM25 indexed. Embedding a different
    string would conflate two variables in any comparison.
    """

    def __init__(self, name: str, text_of, store: embed.VectorStore | None = None,
                 lazy: bool = True):
        self.name = name
        self.text_of = text_of
        self.store = store if store is not None else embed.VectorStore.open(dense_path(name))
        self.lazy = lazy
        self.added = 0

    @staticmethod
    def enabled() -> bool:
        return bool(getattr(ablation.current(), "dense", False))

    def __len__(self) -> int:
        return len(self.store)

    def save(self) -> None:
        self.store.save(dense_path(self.name))

    def rank(self, query: str, candidates: list[str], top_k: int) -> list[str]:
        """Top-k dense matches within the candidate set, embedding on demand.

        Embedding every section chunk up front would exceed both the memory
        ceiling and the credit budget.
        """
        missing = [d for d in candidates if d not in self.store]
        if missing and self.lazy:
            self.added = self.store.ensure(missing, self.text_of,
                                           on_batch=lambda *_: self.save())
            if self.added:
                self.save()
        else:
            self.added = 0
        return [doc_id for doc_id, _ in
                self.store.rank(embed.query_vector(query), candidates, top_k)]


def _fuse(query: str, dense: "DenseSide | None", bm25_ranked: list[tuple[str, float]],
          n_candidates: int, candidates_of, top_k: int) -> list[tuple[str, float]]:
    """Fuse BM25 and dense rankings with RRF, returning raw BM25 scores.

    A document surfaced only by dense retrieval carries a BM25 score of 0.0,
    which marks a lexical miss with a semantic hit. Candidate enumeration is
    deferred, since unfiltered section search skips dense entirely.
    """
    global LAST_FUSION
    LAST_FUSION = None
    if dense is None or not DenseSide.enabled():
        return bm25_ranked[:top_k]
    if n_candidates > embed.DENSE_MAX_POOL:
        LAST_FUSION = {"mode": "skip_pool", "pool": n_candidates,
                       "dense_n": 0, "added": 0, "gain": 0}
        return bm25_ranked[:top_k]
    candidates = candidates_of()
    depth = embed.fetch_depth(top_k)
    dense_ids = dense.rank(query, candidates, depth)
    bm25_ids = [doc_id for doc_id, _ in bm25_ranked[:depth]]
    fused = embed.rrf(bm25_ids, dense_ids, top_k=top_k)
    score_of = dict(bm25_ranked)
    out = [(doc_id, score_of.get(doc_id, 0.0)) for doc_id, _ in fused]
    LAST_FUSION = {"mode": "rrf", "pool": n_candidates, "dense_n": len(dense_ids),
                   "added": dense.added,
                   "gain": len({d for d, _ in out} - set(bm25_ids[:top_k]))}
    return out


class Bm25Index:
    def __init__(self, doc_ids: list[str], texts: list[str], metas: list[dict]):
        self.doc_ids = doc_ids
        self.texts = texts
        self.metas = metas
        self.dense: DenseSide | None = None
        self._bm25 = BM25Okapi([tokenize(t) for t in texts])

    def attach_dense(self, name: str, lazy: bool = True) -> "DenseSide":
        """The index name determines the sidecar filename."""
        text_of = {d: t for d, t in zip(self.doc_ids, self.texts)}.get
        self.dense = DenseSide(name, lambda d: text_of(d) or "", lazy=lazy)
        return self.dense

    @classmethod
    def build(cls, docs: list[dict]) -> "Bm25Index":
        return cls(
            [d["doc_id"] for d in docs],
            [d["text"] for d in docs],
            [d.get("meta", {}) for d in docs],
        )

    def candidates(self, prefix: dict | None = None, **meta_filters) -> list[int]:
        """Indices of documents passing the metadata prefilter."""
        prefix = prefix or {}
        out = []
        for i, meta in enumerate(self.metas):
            if any(meta.get(k) != v for k, v in meta_filters.items()):
                continue
            if any(not str(meta.get(k, "")).startswith(v) for k, v in prefix.items()):
                continue
            out.append(i)
        return out

    def search(self, query: str, top_k: int = 10,
               prefix: dict | None = None, **meta_filters) -> list[tuple[str, float]]:
        """Exact matching for `meta_filters`, prefix matching for `prefix`.

        Period conditions cannot be expressed as equality, since a month maps
        to a receipt-date prefix.
        """
        scores = self._bm25.get_scores(tokenize(query))
        keep = self.candidates(prefix, **meta_filters)
        # BM25Okapi assigns negative IDF to terms appearing in more than half
        # the corpus. A negative score means a common term matched, not a miss.
        # Only an exact zero indicates no overlap.
        ranked = sorted(((self.doc_ids[i], float(scores[i])) for i in keep
                         if scores[i] != 0), key=lambda x: -x[1])
        return _fuse(query, self.dense, ranked, len(keep),
                     lambda: [self.doc_ids[i] for i in keep], top_k)

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
        index = cls(raw["doc_ids"], raw["texts"], raw["metas"])
        # The sidecar is attached whether or not its file exists. Without the
        # file it is an empty store, and lazy fill runs only when dense is on.
        index.attach_dense(Path(path).stem.replace("_bm25", ""))
        return index


# --- Secondary section splitting ------------------------------------------
# Table-of-contents markup varies by filer, so a single declared section can
# reach several hundred thousand characters. A length ceiling is therefore
# mandatory. Splitting inside a table must be avoided, because periodic
# filings are table-dense and a mid-table cut destroys label-value mapping.

#: Maximum characters per chunk. Chosen so that a median section remains a
#: single chunk and two or three chunks fit within the token budget.
SECTION_CHUNK_CHARS = 8000
EVIDENCE_CHUNK_CHARS = SECTION_CHUNK_CHARS

#: Table header and separator, reapplied to every chunk of a split table.
_TABLE_HEAD = 2


def _split_table(block: str, max_chars: int) -> list[str]:
    """Split a large table on row boundaries, repeating the header per chunk.

    Without the header, columns lose meaning from the second chunk onward.
    """
    lines = block.split("\n")
    if len(lines) <= _TABLE_HEAD:
        return [block]
    head, rows = lines[:_TABLE_HEAD], lines[_TABLE_HEAD:]
    head_len = sum(len(x) + 1 for x in head)
    out: list[str] = []
    buf: list[str] = []
    size = head_len
    for row in rows:
        if buf and size + len(row) + 1 > max_chars:
            out.append("\n".join(head + buf))
            buf, size = [], head_len
        buf.append(row)
        size += len(row) + 1
    if buf:
        out.append("\n".join(head + buf))
    return out


def split_section(text: str, max_chars: int = EVIDENCE_CHUNK_CHARS) -> list[str]:
    """Split section text while preserving table boundaries.

    Blocks are joined by blank lines, and a table contains none, so blank lines
    are safe cut points. A single block exceeding the ceiling is split by row.
    """
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]
    blocks: list[str] = []
    for block in text.split("\n\n"):
        if len(block) <= max_chars:
            blocks.append(block)
        elif block.lstrip().startswith("|"):
            blocks.extend(_split_table(block, max_chars))
        else:
            blocks.extend(block[i:i + max_chars] for i in range(0, len(block), max_chars))
    out: list[str] = []
    buf: list[str] = []
    size = 0
    for block in blocks:
        if buf and size + len(block) + 2 > max_chars:
            out.append("\n\n".join(buf))
            buf, size = [], 0
        buf.append(block)
        size += len(block) + 2
    if buf:
        out.append("\n\n".join(buf))
    return out


def iter_section_parts(record: dict, text: str,
                       chunk_chars: int = SECTION_CHUNK_CHARS):
    """Stream (chunk record, chunk text) pairs for one section record.

    Chunk bodies are not stored separately. `split_section` is deterministic,
    so re-reading the source reproduces the same boundaries.
    """
    parts = split_section(text, chunk_chars) or [""]
    for i, part in enumerate(parts):
        out = dict(record)
        out["chunk_id"] = record["chunk_id"] if len(parts) == 1 else f"{record['chunk_id']}/p{i}"
        out["part"] = i
        out["n_parts"] = len(parts)
        out["section_chars"] = record.get("n_chars", len(text))
        out["is_stub"] = int(bool(record.get("is_stub")))
        out["n_chars"] = len(part)
        yield out, part


# --- Section index ---------------------------------------------------------
# The reference BM25 implementation holds a term-frequency dict per document,
# which exceeds the memory ceiling at this corpus size. The same BM25Okapi
# formula is reimplemented over a CSR inverted index instead.

BM25_K1 = 1.5
BM25_B = 0.75
BM25_EPSILON = 0.25          # matches the library default: floors a negative IDF

#: Indexed body length ceiling. Full-text indexing exceeds the memory budget
#: even under CSR.
SECTION_INDEX_CHARS = 1200

#: Metadata axes encoded as integer codes. `path` has low cardinality and
#: benefits most.
SECTION_FIELDS = ("doc_id", "corp_name", "doc_subtype", "rcept_dt", "base_year", "path")

#: Per-chunk columns. `part` recovers the slice position within the blob.
#: `section_chars` and `is_stub` let `list_sections` run from this index
#: alone, avoiding a second resident copy of the section records.
SECTION_COLUMNS = ("text_offset", "text_bytes", "n_chars", "n_tables",
                   "part", "n_parts", "section_chars", "is_stub", "level")

#: Comparison axis with punctuation and whitespace removed. Table-of-contents
#: entries contain typographic errors that defeat exact matching.
_PUNCT_INSENSITIVE = frozenset({"path"})
_PUNCT = re.compile(r"[^0-9A-Za-z가-힣]+")


def norm_path(value: str) -> str:
    """Normal form for comparing TOC paths, ignoring punctuation and case."""
    return _PUNCT.sub("", str(value or "")).lower()


def section_index_text(record: dict, text: str, cap: int = SECTION_INDEX_CHARS,
                       with_meta: bool = True) -> str:
    """Indexed string for one section: company, TOC path and body head.

    The TOC path is included because upper-level heading vocabulary does not
    appear in the body. Only the head of the body is used, since the remainder
    is mostly numeric tables. With `with_meta=False` only the body is indexed.
    """
    head = (text or "")[:cap] if cap else (text or "")
    if not with_meta:
        return head
    return f"{record.get('corp_name', '')} {record.get('path', '')}\n{head}"


class SectionBm25Index:
    """BM25 over a large section corpus without keeping bodies resident.

    Shares the search contract of `Bm25Index`, with two differences:

    1. Results are identified by `chunk_id`; bodies are read from the blob
       using the offsets returned by `record()`.
    2. `prefix` accepts a `path` axis. TOC paths are shared across companies,
       so one prefix narrows the candidate set sharply.
    """

    def __init__(self, chunk_ids, fields, codes, tables, cols,
                 vocab, ptr, post_docs, post_tfs, doc_len, tokenizer: str = "bigram"):
        self.chunk_ids = list(chunk_ids)
        self.fields = tuple(fields)
        self.codes = {k: np.asarray(v, dtype=np.int32).copy() for k, v in codes.items()}
        self.tables = {k: list(v) for k, v in tables.items()}
        self.cols = {k: np.asarray(v).copy() for k, v in cols.items()}
        self.vocab = vocab
        self.tokenizer = tokenizer
        self._tokenize = TOKENIZERS[tokenizer]
        self.ptr = np.asarray(ptr, dtype=np.int64).copy()
        self.post_docs = np.asarray(post_docs, dtype=np.int32).copy()
        self.post_tfs = np.asarray(post_tfs, dtype=np.uint16).copy()
        self.doc_len = np.asarray(doc_len, dtype=np.int32).copy()
        self.dense: DenseSide | None = None
        self.index_chars = SECTION_INDEX_CHARS
        n = len(self.chunk_ids)
        self.avgdl = float(self.doc_len.sum()) / n if n else 0.0
        self.idf = self._calc_idf()

    def index_text(self, chunk_id: str) -> str:
        """The exact string BM25 indexed for this chunk, as embedded by dense.

        Bodies are re-read from the blob, reusing the offset logic in `tools`
        rather than duplicating it.
        """
        from argus.agent import tools

        # `record()` performs a linear scan, which is costly when called per
        # chunk. A position table is built only on the dense path so that
        # resident memory is unchanged when dense is off.
        if getattr(self, "_chunk_pos", None) is None:
            self._chunk_pos = {c: i for i, c in enumerate(self.chunk_ids)}
        i = self._chunk_pos.get(chunk_id)
        if i is None:
            return ""
        record = self.record_at(i)
        return section_index_text(record, tools._section_body(record), self.index_chars)

    def attach_dense(self, name: str = "section", lazy: bool = True) -> "DenseSide":
        self.dense = DenseSide(name, self.index_text, lazy=lazy)
        return self.dense

    def __len__(self) -> int:
        return len(self.chunk_ids)

    # --- Build ----------------------------------------------------------------
    @classmethod
    def build(cls, records, texts, cap: int = SECTION_INDEX_CHARS,
              tokenizer: str = "bigram", with_meta: bool = True) -> "SectionBm25Index":
        """Build from section metadata and bodies given in the same order."""
        tokenize_fn = TOKENIZERS[tokenizer]
        chunk_ids = [r["chunk_id"] for r in records]
        tables = {f: [] for f in SECTION_FIELDS}
        lookup = {f: {} for f in SECTION_FIELDS}
        codes = {f: array("i") for f in SECTION_FIELDS}
        for record in records:
            for field in SECTION_FIELDS:
                value = str(record.get(field, ""))
                code = lookup[field].get(value)
                if code is None:
                    code = len(tables[field])
                    lookup[field][value] = code
                    tables[field].append(value)
                codes[field].append(code)

        cols = {name: np.asarray([r.get(name, 0) for r in records],
                                 np.int64 if name == "text_offset" else np.int32)
                for name in SECTION_COLUMNS}

        vocab: dict[str, int] = {}
        term_of = array("i")
        doc_of = array("i")
        tf_of = array("I")
        doc_len = array("i")
        for i, (record, text) in enumerate(zip(records, texts)):
            tokens = tokenize_fn(section_index_text(record, text, cap, with_meta))
            doc_len.append(len(tokens))
            freq: dict[int, int] = {}
            for token in tokens:
                tid = vocab.get(token)
                if tid is None:
                    tid = len(vocab)
                    vocab[token] = tid
                freq[tid] = freq.get(tid, 0) + 1
            for tid, count in freq.items():
                term_of.append(tid)
                doc_of.append(i)
                tf_of.append(min(count, 65535))

        term = np.frombuffer(term_of, dtype=np.int32)
        order = np.argsort(term, kind="stable")
        term = term[order]
        post_docs = np.frombuffer(doc_of, dtype=np.int32)[order]
        post_tfs = np.frombuffer(tf_of, dtype=np.uint32)[order].astype(np.uint16)
        ptr = np.searchsorted(term, np.arange(len(vocab) + 1, dtype=np.int32), side="left")
        return cls(chunk_ids, SECTION_FIELDS, {k: np.frombuffer(v, dtype=np.int32)
                                               for k, v in codes.items()},
                   tables, cols, vocab, ptr.astype(np.int64), post_docs, post_tfs,
                   np.frombuffer(doc_len, dtype=np.int32), tokenizer)

    def _calc_idf(self) -> np.ndarray:
        """BM25Okapi scoring. Negative IDF is floored at eps times mean IDF."""
        size = len(self.chunk_ids)
        if not size or self.ptr.size < 2:
            return np.zeros(0, dtype=np.float64)
        df = np.diff(self.ptr).astype(np.float64)
        idf = np.log(size - df + 0.5) - np.log(df + 0.5)
        floor = BM25_EPSILON * (idf.sum() / len(idf))
        return np.where(idf < 0, floor, idf)

    # --- Search ---------------------------------------------------------------
    def _codes_where(self, field: str, test) -> np.ndarray:
        table = self.tables.get(field)
        if table is None:
            raise KeyError(f"알 수 없는 메타 축: {field}")
        wanted = np.asarray([i for i, v in enumerate(table) if test(v)], dtype=np.int32)
        if not wanted.size:
            return np.zeros(len(self.chunk_ids), dtype=bool)
        return np.isin(self.codes[field], wanted)

    def _mask(self, prefix: dict, filters: dict) -> np.ndarray | None:
        mask = None
        for field, value in filters.items():
            if field in _PUNCT_INSENSITIVE:
                target = norm_path(value)
                hit = self._codes_where(field, lambda v: norm_path(v) == target)
            else:
                hit = self._codes_where(field, lambda v, w=str(value): v == w)
            mask = hit if mask is None else (mask & hit)
        for field, value in (prefix or {}).items():
            if field in _PUNCT_INSENSITIVE:
                target = norm_path(value)
                hit = self._codes_where(field, lambda v: norm_path(v).startswith(target))
            else:
                hit = self._codes_where(field, lambda v, w=str(value): v.startswith(w))
            mask = hit if mask is None else (mask & hit)
        return mask

    def scores(self, query: str) -> np.ndarray:
        out = np.zeros(len(self.chunk_ids), dtype=np.float64)
        for token in self._tokenize(query):
            tid = self.vocab.get(token)
            if tid is None:
                continue
            lo, hi = int(self.ptr[tid]), int(self.ptr[tid + 1])
            if lo == hi:
                continue
            docs = self.post_docs[lo:hi]
            freq = self.post_tfs[lo:hi].astype(np.float64)
            norm = 1.0 - BM25_B + BM25_B * self.doc_len[docs] / (self.avgdl or 1.0)
            out[docs] += self.idf[tid] * freq * (BM25_K1 + 1.0) / (freq + BM25_K1 * norm)
        return out

    def search(self, query: str, top_k: int = 10,
               prefix: dict | None = None, **meta_filters) -> list[tuple[str, float]]:
        if not len(self.chunk_ids):
            return []
        scores = self.scores(query)
        mask = self._mask(prefix or {}, meta_filters)
        if mask is not None:
            scores = np.where(mask, scores, 0.0)
        # With dense enabled both lists must be drawn deeper than top_k before
        # fusion; truncating each at top_k would permute order without
        # changing the candidate set.
        fusing = self.dense is not None and DenseSide.enabled()
        depth = embed.fetch_depth(top_k) if fusing else top_k
        keep = np.flatnonzero(scores > 0)
        if keep.size > depth:
            keep = keep[np.argpartition(-scores[keep], depth - 1)[:depth]]
        keep = keep[np.argsort(-scores[keep], kind="stable")]
        ranked = [(self.chunk_ids[int(i)], float(scores[int(i)])) for i in keep]
        n_cand = int(mask.sum()) if mask is not None else len(self.chunk_ids)
        return _fuse(query, self.dense, ranked, n_cand,
                     lambda: [self.chunk_ids[int(i)] for i in
                              (np.flatnonzero(mask) if mask is not None
                               else np.arange(len(self.chunk_ids)))], top_k)

    def record(self, chunk_id: str) -> dict | None:
        """Metadata and blob offsets for one hit. Body text is not included."""
        try:
            i = self.chunk_ids.index(chunk_id)
        except ValueError:
            return None
        return self.record_at(i)

    def record_at(self, i: int) -> dict:
        out = {"chunk_id": self.chunk_ids[i]}
        for field in self.fields:
            out[field] = self.tables[field][int(self.codes[field][i])]
        for key, column in self.cols.items():
            out[key] = int(column[i])
        return out

    # --- Persistence ------------------------------------------------------------
    def save(self, path) -> None:
        """Persist as `<stem>.npz` for arrays and `<stem>.json` for tables."""
        base = Path(path)
        arrays = {"ptr": self.ptr, "post_docs": self.post_docs,
                  "post_tfs": self.post_tfs, "doc_len": self.doc_len}
        arrays |= {f"code_{k}": v for k, v in self.codes.items()}
        arrays |= {f"col_{k}": v for k, v in self.cols.items()}
        np.savez(base.with_suffix(".npz"), **arrays)
        vocab = [""] * len(self.vocab)
        for token, tid in self.vocab.items():
            vocab[tid] = token
        base.with_suffix(".json").write_text(
            json.dumps({"chunk_ids": self.chunk_ids, "fields": list(self.fields),
                        "tables": self.tables, "vocab": vocab,
                        "tokenizer": self.tokenizer}, ensure_ascii=False),
            encoding="utf-8")

    @classmethod
    def load(cls, path) -> "SectionBm25Index":
        base = Path(path)
        meta = json.loads(base.with_suffix(".json").read_text(encoding="utf-8"))
        with np.load(base.with_suffix(".npz")) as npz:
            arrays = {k: npz[k] for k in npz.files}
        fields = meta["fields"]
        index = cls(meta["chunk_ids"], fields,
                    {f: arrays[f"code_{f}"] for f in fields},
                    meta["tables"],
                    {k[4:]: v for k, v in arrays.items() if k.startswith("col_")},
                    {token: i for i, token in enumerate(meta["vocab"])},
                    arrays["ptr"], arrays["post_docs"], arrays["post_tfs"],
                    arrays["doc_len"], meta.get("tokenizer", "bigram"))
        index.attach_dense(base.stem.replace("_index", ""))
        return index

    @staticmethod
    def exists(path) -> bool:
        base = Path(path)
        return base.with_suffix(".npz").exists() and base.with_suffix(".json").exists()
