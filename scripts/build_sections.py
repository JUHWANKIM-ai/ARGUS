"""periodic 섹션 인덱스 빌드 (04-retriever D-2). LLM 호출 없음.

    python scripts/build_sections.py                 # 전량
    python scripts/build_sections.py --limit 20      # 표본
    python scripts/build_sections.py --inline-text   # text 를 jsonl 에 인라인 (크기 비교용)

기본은 오프셋 방식이다. jsonl 에는 메타데이터만 넣고 본문은 periodic_sections.txt
블롭에 모은 뒤 (offset, bytes) 로 가리킨다. 근거는 docs/pipeline/04-retriever.md D-2.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from argus.config import settings                                   # noqa: E402
from argus.corpus.manifest import filter_docs                       # noqa: E402
from argus.corpus.sections import (BLOB_NAME, INDEX_NAME,           # noqa: E402
                                   iter_doc_sections)

SEPARATOR = b"\n\x0c\n"  # 블롭을 사람이 열어볼 때만 쓰는 구분자. 오프셋에는 안 들어간다


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--out-dir", default=None,
                        help="산출물 경로. 테스트는 tmp 를 지정해 실제 인덱스를 덮지 않는다")
    parser.add_argument("--inline-text", action="store_true",
                        help="본문을 jsonl 에 직접 넣는다 (블롭 없음)")
    args = parser.parse_args()

    docs = filter_docs(doc_group="periodic")
    if args.limit:
        docs = docs[: args.limit]
    out_dir = Path(args.out_dir) if args.out_dir else settings().artifacts_root
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"periodic {len(docs)}건 → {out_dir}  (inline_text={args.inline_text})")

    started = time.time()
    stats = {"docs": 0, "empty": 0, "sections": 0, "stubs": 0, "containers": 0,
             "chars": 0, "tables": 0, "failed": []}
    blob_path = out_dir / BLOB_NAME
    index_path = out_dir / INDEX_NAME
    offset = 0

    blob = None if args.inline_text else blob_path.open("wb")
    try:
        with index_path.open("w", encoding="utf-8") as index:
            for i, doc in enumerate(docs, 1):
                try:
                    records = list(iter_doc_sections(doc))
                except Exception as exc:                            # noqa: BLE001
                    stats["failed"].append((doc["doc_id"], f"{type(exc).__name__}: {exc}"))
                    continue
                if not records:
                    stats["empty"] += 1
                    stats["failed"].append((doc["doc_id"], "섹션 0건"))
                stats["docs"] += 1
                for record in records:
                    text = record.pop("text")
                    if blob is not None:
                        payload = text.encode("utf-8")
                        blob.write(payload + SEPARATOR)
                        record["text_offset"] = offset
                        record["text_bytes"] = len(payload)
                        offset += len(payload) + len(SEPARATOR)
                    else:
                        record["text"] = text
                    index.write(json.dumps(record, ensure_ascii=False) + "\n")
                    stats["sections"] += 1
                    stats["stubs"] += record["is_stub"]
                    stats["containers"] += record["is_container"]
                    stats["chars"] += record["n_chars"]
                    stats["tables"] += record["n_tables"]
                if i % 50 == 0 or i == len(docs):
                    print(f"  {i}/{len(docs)}  섹션 {stats['sections']:,}  "
                          f"{time.time() - started:.0f}s", flush=True)
    finally:
        if blob is not None:
            blob.close()

    elapsed = time.time() - started
    print(f"\n문서 {stats['docs']}건 (섹션 0건 {stats['empty']}건) "
          f"섹션 {stats['sections']:,}개  본문 {stats['chars']:,}자  표 {stats['tables']:,}개")
    print(f"스텁 {stats['stubs']:,} ({stats['stubs'] / max(1, stats['sections']):.1%})  "
          f"상위목차 {stats['containers']:,}")
    print(f"{index_path.name} {index_path.stat().st_size / 1e6:.1f}MB", end="")
    if not args.inline_text:
        print(f" + {blob_path.name} {blob_path.stat().st_size / 1e6:.1f}MB", end="")
    print(f"   소요 {elapsed:.0f}s")
    for doc_id, reason in stats["failed"][:20]:
        print(f"  [이상] {doc_id}: {reason}")
    if len(stats["failed"]) > 20:
        print(f"  ... 외 {len(stats['failed']) - 20}건")


if __name__ == "__main__":
    main()
