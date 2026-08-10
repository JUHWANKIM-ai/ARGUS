"""`※ 관련공시` REFERS_TO 엣지 추출 -> artifacts/references.jsonl. LLM 호출 없음."""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from argus.config import settings                                  # noqa: E402
from argus.corpus.manifest import load_manifest                    # noqa: E402
from argus.corpus.references import extract_references             # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--groups", default="exchange,major",
                        help="쉼표 구분 doc_group")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--out-dir", default=None,
                        help="산출물 경로. 테스트는 tmp 를 지정해 실제 인덱스를 덮지 않는다")
    args = parser.parse_args()

    groups = {g.strip() for g in args.groups.split(",") if g.strip()}
    docs = [d for d in load_manifest() if d["doc_group"] in groups]
    if args.limit:
        docs = docs[: args.limit]
    print(f"{'/'.join(sorted(groups))}: {len(docs)}건 스캔")

    references = []
    docs_with_refs = 0
    for i, doc in enumerate(docs, 1):
        found = extract_references(doc)
        if found:
            docs_with_refs += 1
        references.extend(found)
        if i % 500 == 0:
            print(f"  {i}/{len(docs)}")

    out_dir = Path(args.out_dir) if args.out_dir else settings().artifacts_root
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "references.jsonl"
    with path.open("w", encoding="utf-8") as fh:
        for ref in references:
            fh.write(json.dumps(ref.as_dict(), ensure_ascii=False) + "\n")

    reasons = Counter(r.reason for r in references)
    total = len(references)
    edges = sum(len(r.targets) for r in references)
    resolved = [r for r in references if r.resolved]
    strong = sum(1 for r in resolved if r.form_matched)
    print(f"\n참조 항목 {total}개 (참조 있는 문서 {docs_with_refs}건)")
    for reason in ("resolved", "before_corpus", "not_found"):
        n = reasons[reason]
        print(f"  {reason:14s} {n:5d}  ({100 * n / total if total else 0:.1f}%)")
    print(f"  해소된 엣지 수 {edges}")
    if resolved:
        print(f"  그중 서식명까지 일치 {strong}/{len(resolved)} "
              f"({100 * strong / len(resolved):.1f}%) — 나머지는 '같은 날·같은 기업' 만으로 "
              f"붙은 약한 엣지 (form_matched=false)")
    print(f"완료: {path}")


if __name__ == "__main__":
    main()
