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
    parser.add_argument("--out-dir", default=None,
                        help="산출물 경로. 테스트는 tmp 를 지정해 실제 인덱스를 덮지 않는다")
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

    out_dir = Path(args.out_dir) if args.out_dir else settings().artifacts_root
    out_dir.mkdir(parents=True, exist_ok=True)
    jsonl = out_dir / f"{args.group}_records.jsonl"
    with jsonl.open("w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    index = Bm25Index.build(
        [
            {
                "doc_id": r["doc_id"],
                "text": r["markdown"],
                "meta": {
                    "corp_name": r["corp_name"],
                    "doc_subtype": r["doc_subtype"],
                    "rcept_dt": r["rcept_dt"],
                    "year": str(r["rcept_dt"])[:4],
                    "ym": str(r["rcept_dt"])[:6],
                    "is_latest": r.get("is_latest", True),
                },
            }
            for r in records
        ]
    )
    index.save(out_dir / f"{args.group}_bm25.json")
    print(f"완료: {jsonl.name}, {args.group}_bm25.json ({len(records)}건)")


if __name__ == "__main__":
    main()
