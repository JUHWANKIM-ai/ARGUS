"""periodic 섹션 BM25 인덱스 빌드 (04-retriever D-5). LLM 호출 0회.

    python scripts/build_section_index.py                        # 전량
    python scripts/build_section_index.py --limit 2000           # 표본
    python scripts/build_section_index.py --out-dir /tmp/idx     # 산출물 격리

`artifacts/periodic_sections.jsonl` + `periodic_sections.txt` 블롭을 입력으로 받아
`artifacts/section_index.npz` + `section_index.json` 두 파일을 만든다.
**`scripts/build_index.py`(exchange) 와는 산출물이 겹치지 않는다**. 두 인덱스는 따로다.

--out-dir 을 주면 그 아래에만 쓴다. 테스트는 반드시 tmp 를 지정할 것.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from argus.config import settings                                     # noqa: E402
from argus.corpus.sections import BLOB_NAME, INDEX_NAME               # noqa: E402
from argus.retrieve.bm25 import (SECTION_CHUNK_CHARS,                 # noqa: E402
                                 SECTION_INDEX_CHARS, SectionBm25Index,
                                 iter_section_parts)

OUT_STEM = "section_index"


def iter_sections(index_path: Path, blob_path: Path, limit: int | None = None,
                  include_stubs: bool = False):
    """(record, text) 스트림. 블롭은 한 번만 열고 seek 한다 (레코드가 오프셋 순서다)."""
    blob = blob_path.open("rb") if blob_path.exists() else None
    try:
        with index_path.open(encoding="utf-8") as fh:
            taken = 0
            for line in fh:
                if not line.strip():
                    continue
                record = json.loads(line)
                if record.get("is_stub") and not include_stubs:
                    continue
                text = record.pop("text", None)
                if text is None and blob is not None:
                    offset, length = record.get("text_offset"), record.get("text_bytes")
                    if offset is None or length is None:
                        text = ""
                    else:
                        blob.seek(offset)
                        text = blob.read(length).decode("utf-8", errors="replace")
                yield record, text or ""
                taken += 1
                if limit and taken >= limit:
                    return
    finally:
        if blob is not None:
            blob.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None,
                        help="색인할 섹션 수 상한 (표본 빌드용)")
    parser.add_argument("--out-dir", default=None,
                        help="산출물 경로. 테스트는 tmp 를 지정해 실제 인덱스를 덮지 않는다")
    parser.add_argument("--source-dir", default=None,
                        help="periodic_sections.* 가 있는 경로 (기본 artifacts/)")
    parser.add_argument("--cap", type=int, default=SECTION_INDEX_CHARS,
                        help="조각당 색인 본문 길이 상한 (0 = 무제한)")
    parser.add_argument("--chunk-chars", type=int, default=SECTION_CHUNK_CHARS,
                        help="2차 분할 상한. 표 경계는 깨지 않는다 (0 = 분할 안 함)")
    parser.add_argument("--tokenizer", default="bigram", choices=("bigram", "word"))
    # 스텁은 **색인에 넣는다**. 배제는 `list_sections` 가 한다 (§7.7 ④), 스텁 자체가
    # 답인 문항이 있다. Q-053 '내부통제에 관한 사항' 은 79자, Q-059 '이사의 경영진단'
    # 은 63자로 둘 다 '기재 생략' 이고, 그 사실을 짚는 것이 정답이다. 색인에서 빼면
    # 도구가 스텁을 열 수조차 없다.
    parser.add_argument("--exclude-stubs", action="store_true",
                        help="100자 미만 스텁을 색인에서 뺀다 (기본은 포함)")
    args = parser.parse_args()

    source = Path(args.source_dir) if args.source_dir else settings().artifacts_root
    out_dir = Path(args.out_dir) if args.out_dir else settings().artifacts_root
    out_dir.mkdir(parents=True, exist_ok=True)
    index_path, blob_path = source / INDEX_NAME, source / BLOB_NAME
    if not index_path.exists():
        raise SystemExit(f"섹션 인덱스가 없습니다: {index_path} "
                         f"(scripts/build_sections.py 를 먼저 실행하세요)")

    started = time.time()
    records, texts = [], []
    sections = 0
    for record, text in iter_sections(index_path, blob_path, args.limit,
                                      not args.exclude_stubs):
        sections += 1
        if args.chunk_chars:
            for part, body in iter_section_parts(record, text, args.chunk_chars):
                records.append(part)
                texts.append(body)
        else:
            record["part"], record["n_parts"] = 0, 1
            records.append(record)
            texts.append(text)
    read_secs = time.time() - started
    print(f"섹션 {sections:,}건 -> 조각 {len(records):,}건 로드 {read_secs:.1f}s "
          f"(본문 {sum(len(t) for t in texts)/1e6:.1f}M자, "
          f"chunk={args.chunk_chars or '분할없음'}, cap={args.cap or '무제한'})")

    built = time.time()
    index = SectionBm25Index.build(records, texts, cap=args.cap,
                                   tokenizer=args.tokenizer)
    build_secs = time.time() - built
    del texts

    index.save(out_dir / OUT_STEM)
    npz = (out_dir / OUT_STEM).with_suffix(".npz")
    meta = (out_dir / OUT_STEM).with_suffix(".json")
    print(f"빌드 {build_secs:.1f}s · 어휘 {len(index.vocab):,} · "
          f"포스팅 {index.post_docs.size:,} · 평균 길이 {index.avgdl:.0f} 토큰")
    print(f"완료: {npz.name} {npz.stat().st_size/1e6:.1f}MB · "
          f"{meta.name} {meta.stat().st_size/1e6:.1f}MB · 총 {time.time()-started:.1f}s")


if __name__ == "__main__":
    main()
