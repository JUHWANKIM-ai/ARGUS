"""BM25 검색 인덱스 (+ A2 dense 하이브리드). LLM 호출 없음.

두 가지 인덱스가 있다.

* `Bm25Index` — exchange 정형 레코드 1,469건. 전문(markdown)을 그대로 안고 있다.
* `SectionBm25Index` — periodic 목차 섹션 88,654건. 본문을 안지 않는 CSR 역색인.

**왜 하나로 안 합쳤는가는 실측 결과다** (docs/pipeline/04-retriever.md D-5).
합치면 exchange 골든 적중이 무너진다. 따로 가는 근거와 수치는 그 문서에 있다.
문서군별 인덱스 구조는 A2 에서도 그대로다 — dense 도 **문서군마다 따로** 붙는다.

## A2 — dense 를 어디에 끼우는가

    메타 프리필터(corp_name · rcept_dt · path)  ->  후보 집합
                                                     ├─ BM25 순위
                                                     └─ dense 순위      ->  RRF  ->  top_k

**dense 는 프리필터 앞이 아니라 뒤다.** §I-1 의 실측(1,469 -> 1건)이 그 이유이고,
그래서 정기공시 dense 저장소를 '전량' 이 아니라 '프리필터 통과 후보' 로 좁혀도
**측정이 훼손되지 않는다** — 프리필터가 거른 문서는 dense 인덱스에 있어도 후보가 아니다.

점수는 융합해도 **`score` 로 돌려주는 값은 BM25 원점수 그대로**다. `pipeline.MIN_TOP_SCORE`
(잡음 바닥 10.0)와 `_sufficiency` 의 `low_score` 판정이 BM25 스케일 위에 서 있어서,
RRF 점수(≈0.03)를 그대로 흘리면 **모든 문항이 low_score 가 되어 hop 사다리가 통째로
바뀐다.** 그러면 재는 것이 dense 가 아니라 hop 예산이 된다. A2 가 바꾸는 것은
**후보의 순서와 구성** 하나뿐이다.
"""
from __future__ import annotations

import json
import re
from array import array
from pathlib import Path

import numpy as np
from rank_bm25 import BM25Okapi

from argus import ablation, embed

# 공시 서식명에는 가운뎃점 'ㆍ'(U+318D) 가 섞여 있다 — '단일판매ㆍ공급계약체결'.
# [가-힣] 범위 밖이라 그냥 두면 토큰이 쪼개진다. 정규화해서 흡수한다.
_MIDDOT = re.compile(r"[ㆍ·・･]")
_TOKEN = re.compile(r"[0-9]+(?:[,.][0-9]+)*|[가-힣]+|[A-Za-z]+")

BIGRAM_MIN = 3   # 이 길이 이상인 한글 토큰만 bigram 으로 분해한다


def _bigrams(word: str) -> list[str]:
    return [word[i:i + 2] for i in range(len(word) - 1)]


def tokenize_words(text: str) -> list[str]:
    """어절 토큰만. bigram 없이."""
    return _TOKEN.findall(_MIDDOT.sub("", text or ""))


def tokenize(text: str) -> list[str]:
    """어절 토큰 + 한글 문자 bigram.

    한국어 복합명사 때문에 어절 토큰만으로는 부분 일치가 안 된다.
    '배전변압기' 는 한 토큰이라 '변압기' 질의에 걸리지 않는다 — 실측으로 확인된 실패다.
    형태소 분석기는 서버 메모리 2.8GiB 제약에서 부담이라 문자 bigram 을 병용한다.
    '변압기' -> [변압, 압기] 가 '배전변압기' -> [배전, 전변, 변압, 압기] 와 겹친다.
    """
    words = tokenize_words(text)
    tokens = list(words)
    for word in words:
        if len(word) >= BIGRAM_MIN and "가" <= word[0] <= "힣":
            tokens.extend(_bigrams(word))
    return tokens


#: 인덱스가 어떤 토크나이저로 지어졌는지 저장해 두고 질의에도 같은 것을 쓴다.
#: 섞이면 bigram 으로 지은 색인을 어절 질의로 때리게 되어 조용히 리콜이 죽는다.
TOKENIZERS = {"bigram": tokenize, "word": tokenize_words}


# --- A2 dense 사이드카 ---------------------------------------------------------
# 인덱스 옆에 붙는 벡터 저장소. **arm 이 켜졌을 때만** 동작하고, 꺼져 있으면 파일이
# 있어도 질의 임베딩조차 부르지 않는다 (`tests/test_ablation.py` 가 그것을 고정한다).

#: 직전 `search()` 의 융합 기록. `pipeline` 이 `think_trace` 에 찍는다 —
#: **"dense 가 실제로 돌았는가" 를 크레딧 0원으로 되읽는 유일한 창구**다.
LAST_FUSION: dict | None = None

DENSE_DIRNAME = embed.EMBED_CACHE_DIRNAME


def dense_path(name: str):
    from argus.config import settings
    return settings().artifacts_root / DENSE_DIRNAME / f"{name}_dense"


class DenseSide:
    """벡터 저장소 + 후보 임베딩 지연 채움. 한 인덱스에 하나.

    `text_of` 는 **BM25 가 색인한 것과 같은 문자열**을 돌려줘야 한다. 다른 문자열을
    임베딩하면 A2 의 Δ 에 '색인 텍스트 변경' 이 섞여 단일 변수 조작이 아니게 된다.
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
        """후보 안에서 dense 상위 top_k. 저장소에 없는 후보는 그 자리에서 채운다.

        지연 채움이 **정기공시 dense 를 성립시키는 유일한 방법**이다. 157,397 조각 전량은
        상주 615MB(float32)·비용 7천원대라 게이트와 예산을 동시에 넘긴다.
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
    """BM25 순위와 dense 순위를 RRF 로 합친다. 돌려주는 점수는 **BM25 원점수**다.

    dense 만 올린 문서는 BM25 점수가 0.0 이다 — 그것이 "어휘로는 안 걸렸는데 의미로는
    걸렸다" 는 뜻이고, 이 arm 이 재려는 바로 그 사건이다.

    후보 목록은 **끝까지 미룬다** (`candidates_of`). 필터 없는 섹션 검색은 후보가
    157,397개라 목록을 만드는 것만으로 손해인데, 그 경우는 어차피 dense 를 건너뛴다.
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
        """이름이 곧 사이드카 파일명이다 (`artifacts/embeddings/<name>_dense.npz`)."""
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
        """메타 프리필터를 통과한 문서 인덱스. **A0 의 후보 집합이 곧 이것이다.**"""
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
        """meta_filters 는 완전일치, prefix 는 접두 일치.

        기간 조건은 완전일치로 표현할 수 없다 — '2023년 1월' 은 rcept_dt 접두 '202301' 이다.
        """
        scores = self._bm25.get_scores(tokenize(query))
        keep = self.candidates(prefix, **meta_filters)
        # BM25Okapi 는 코퍼스 절반 넘게 등장하는 어휘에 음수 IDF 를 준다.
        # 음수 점수는 '매칭됐지만 흔한 어휘' 라는 뜻이지 미매칭이 아니다.
        # 토큰이 하나도 겹치지 않을 때만 점수가 정확히 0 이 된다 — 그때만 거른다.
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
        # 사이드카는 **파일이 있든 없든** 붙인다. 없으면 빈 저장소이고, A2 가 켜졌을 때만
        # 지연 채움이 돈다 — 그래서 A2 를 안 켜면 파일 유무가 동작에 영향을 주지 않는다.
        index.attach_dense(Path(path).stem.replace("_bm25", ""))
        return index


# --- 섹션 2차 분할 -------------------------------------------------------------
# 목차 마크업(ATOC="Y")은 제출인마다 붙이는 정도가 다르다. 실측 최장 섹션이
# **410,023자**(`periodic_20260313001191` 연결감사보고서 주석)이고, 카카오 2023
# 사업보고서 원공시의 `연결재무제표 주석` 은 255,691자가 한 덩어리다 — 같은 문서의
# 정정본에서는 주석 1~43 으로 쪼개져 있다. **목차만 믿으면 20만자 근거가 나온다.**
#
# 따라서 길이 상한 분할은 선택이 아니다. 다만 **표 중간에서 자르면 안 된다** —
# 정기공시 표 문자비가 0.82 라 값-라벨 매핑이 통째로 깨진다.

#: 조각 하나의 상한. 정기공시 섹션 중앙값 2,306자의 3.5배로, 중앙값 섹션은 그대로 한 조각이다.
#: 8,000자 ≈ HCX 4~5천 토큰이라 근거 2~3건까지 물려도 20,000 토큰 예산 안에 든다.
SECTION_CHUNK_CHARS = 8000
EVIDENCE_CHUNK_CHARS = SECTION_CHUNK_CHARS

#: 표 머리(헤더 + 구분선). 표를 쪼갤 때 조각마다 다시 얹는다.
_TABLE_HEAD = 2


def _split_table(block: str, max_chars: int) -> list[str]:
    """거대한 표를 행 경계에서만 자르고 조각마다 헤더를 다시 얹는다.

    헤더를 안 얹으면 두 번째 조각부터 열 의미가 사라진다 — 숫자만 남은 표는 근거가 아니다.
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
    """섹션 본문을 표 경계를 지키며 조각낸다.

    `render()` 가 블록을 빈 줄로 이어 붙이므로 빈 줄이 곧 안전한 경계다 — 표 하나는
    줄바꿈 하나로만 이어져 있어 빈 줄로 자르면 절대 표 중간이 갈리지 않는다.
    빈 줄 없이 한 블록이 상한을 넘는 경우(거대 표)만 행 단위로 다시 자른다.
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
    """섹션 레코드 하나 -> (조각 레코드, 조각 본문) 스트림.

    조각은 `chunk_id` 에 `/p{n}` 을 붙여 구분한다. 조각 본문은 블롭에 따로 저장하지
    않는다 — `split_section` 이 결정적이라 원문을 다시 읽어 같은 자리에서 자르면 된다.
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


# --- 섹션 인덱스 ---------------------------------------------------------------
# rank_bm25 는 문서마다 `dict[token] -> tf` 를 하나씩 들고 있다. 88,654 섹션 전문이면
# 실측 2,308MB — 게이트 500MB 의 4.6배다 (04-retriever D-5 표). 같은 BM25Okapi 수식을
# CSR(term -> 정렬된 doc/tf 배열) 로 다시 구현해 절반 이하로 떨군다.

BM25_K1 = 1.5
BM25_B = 0.75
BM25_EPSILON = 0.25          # rank_bm25 와 동일 — 음수 IDF 를 eps*평균IDF 로 바닥친다

#: 색인 대상 본문 길이 상한. 전문 색인은 CSR 로도 1,168MB 라 상한이 필수다.
#: 값은 실측으로 골랐다 (04-retriever D-5 회수/상주량 표).
SECTION_INDEX_CHARS = 1200

#: 사전 인코딩할 메타 축. `path` 는 고유 8,593개뿐이라 코드화가 특히 잘 먹는다.
SECTION_FIELDS = ("doc_id", "corp_name", "doc_subtype", "rcept_dt", "base_year", "path")

#: 조각 단위 부가 컬럼. `part` 로 블롭에서 다시 잘라 낼 위치를 되찾는다.
#: `section_chars`/`is_stub` 은 §7.7 ④ `list_sections` 를 이 인덱스만으로 돌리기 위한 것이다
#: — 없으면 55MB jsonl 을 또 상주시켜야 하고 그것만으로 174MB 다.
SECTION_COLUMNS = ("text_offset", "text_bytes", "n_chars", "n_tables",
                   "part", "n_parts", "section_chars", "is_stub", "level")

#: 구두점·공백을 지우고 비교하는 축. 목차 오타가 실재한다 — HMM 20240514001580 의
#: `"12, 관계기업투자 (연결)"` 는 마침표 자리에 쉼표가 찍혀 있어 완전일치로는 못 찾는다.
_PUNCT_INSENSITIVE = frozenset({"path"})
_PUNCT = re.compile(r"[^0-9A-Za-z가-힣]+")


def norm_path(value: str) -> str:
    """목차 경로 비교용 정규형. 구두점·공백·대소문자를 지운다."""
    return _PUNCT.sub("", str(value or "")).lower()


def section_index_text(record: dict, text: str, cap: int = SECTION_INDEX_CHARS,
                       with_meta: bool = True) -> str:
    """섹션 하나의 색인 문자열 = 기업명 + 목차 경로 + 본문 머리.

    목차 경로를 넣는 이유는 상위 목차 어휘('III. 재무에 관한 사항')가 본문에는 없기
    때문이다. 본문은 머리만 쓴다 — 뒤쪽은 대부분 숫자 표라 질의어가 되지 못한다.
    `with_meta=False` 면 본문만 색인한다 (exchange markdown 처럼 메타가 이미 본문에
    적혀 있는 레코드를 섞어 볼 때 쓴다 — 통합 인덱스 실험).
    """
    head = (text or "")[:cap] if cap else (text or "")
    if not with_meta:
        return head
    return f"{record.get('corp_name', '')} {record.get('path', '')}\n{head}"


class SectionBm25Index:
    """대규모 섹션 코퍼스용 BM25. 본문을 상주시키지 않는다.

    `Bm25Index` 와 검색 계약은 같다 — `search(query, top_k, prefix=..., **완전일치)`.
    다른 점 둘:

    1. 반환 식별자가 `chunk_id` 다. 본문은 `record()` 가 주는 오프셋으로 블롭에서 읽는다.
    2. `prefix` 축에 **`path`** 가 들어간다. 고유 8,593개 목차 경로가 전 기업 공통이라
       `prefix={"path": "III. 재무에 관한 사항"}` 하나로 후보가 65% -> 해당 대목만 남는다.
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
        """이 조각이 **BM25 에 색인된 것과 같은 문자열**. dense 는 이걸 임베딩한다.

        본문은 인덱스가 안 들고 있으므로 블롭에서 다시 읽는다. `tools._section_body`
        를 지연 import 로 재사용한다 — 같은 오프셋 규칙을 두 번 적으면 언젠가 갈라진다.
        """
        from argus.agent import tools

        # `record()` 는 `chunk_ids.index()` 라 조각 하나에 157,397 비교다. 인덱싱에서는
        # 수천 번 부르므로 위치표를 만든다 — **dense 를 켠 경로에서만** 만들어 A6 상주량을
        # 건드리지 않는다 (게이트 500MB).
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

    # --- 빌드 -----------------------------------------------------------------
    @classmethod
    def build(cls, records, texts, cap: int = SECTION_INDEX_CHARS,
              tokenizer: str = "bigram", with_meta: bool = True) -> "SectionBm25Index":
        """`records` 는 섹션 메타, `texts` 는 같은 순서의 본문."""
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
        """rank_bm25 BM25Okapi 와 동일. 음수 IDF 는 eps × 평균IDF 로 바닥친다."""
        size = len(self.chunk_ids)
        if not size or self.ptr.size < 2:
            return np.zeros(0, dtype=np.float64)
        df = np.diff(self.ptr).astype(np.float64)
        idf = np.log(size - df + 0.5) - np.log(df + 0.5)
        floor = BM25_EPSILON * (idf.sum() / len(idf))
        return np.where(idf < 0, floor, idf)

    # --- 검색 -----------------------------------------------------------------
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
        # dense 를 켜면 융합 전에 두 리스트를 top_k 보다 깊게 뽑아야 한다 —
        # 둘 다 top_k 로 자르면 순서만 바뀌고 후보 집합은 절대 안 바뀐다.
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
        """검색 결과 하나의 메타 + 블롭 오프셋. 본문은 담지 않는다."""
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

    # --- 저장 -----------------------------------------------------------------
    def save(self, path) -> None:
        """`<stem>.npz`(배열) + `<stem>.json`(어휘·값표) 두 파일."""
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
