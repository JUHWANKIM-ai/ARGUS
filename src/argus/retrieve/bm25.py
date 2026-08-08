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

BIGRAM_MIN = 3   # 이 길이 이상인 한글 토큰만 bigram 으로 분해한다


def _bigrams(word: str) -> list[str]:
    return [word[i:i + 2] for i in range(len(word) - 1)]


def tokenize(text: str) -> list[str]:
    """어절 토큰 + 한글 문자 bigram.

    한국어 복합명사 때문에 어절 토큰만으로는 부분 일치가 안 된다.
    '배전변압기' 는 한 토큰이라 '변압기' 질의에 걸리지 않는다 — 실측으로 확인된 실패다.
    형태소 분석기는 서버 메모리 2.8GiB 제약에서 부담이라 문자 bigram 을 병용한다.
    '변압기' -> [변압, 압기] 가 '배전변압기' -> [배전, 전변, 변압, 압기] 와 겹친다.
    """
    words = _TOKEN.findall(_MIDDOT.sub("", text or ""))
    tokens = list(words)
    for word in words:
        if len(word) >= BIGRAM_MIN and "가" <= word[0] <= "힣":
            tokens.extend(_bigrams(word))
    return tokens


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
