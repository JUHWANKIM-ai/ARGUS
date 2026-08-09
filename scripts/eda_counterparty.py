"""counterparty 표기 실태 — 노드 승격 전에 무엇을 정규화해야 하는지 센다.

정규화 규칙은 `validate_evalset.normalize_counterparty` 가 정본이다.
비실명 판정은 별도 함수가 아니라 `validate_evalset.ANON_COUNTERPARTY` 정규식으로 있다
(브리프가 가정한 `is_vague_counterparty` 라는 이름의 함수는 존재하지 않는다 — 실제 이름을 쓴다).
T2 가 이 규칙을 `argus.corpus.counterparty` 로 옮기면 이 import 만 갈아끼운다.
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))  # `from scripts import ...` 를 스크립트 직접 실행에서도 되게 한다

from scripts.validate_evalset import (  # noqa: E402
    ANON_COUNTERPARTY,
    normalize_counterparty as canonical,
)


def is_vague(raw: str) -> bool:
    return bool(ANON_COUNTERPARTY.search(raw))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "artifacts/eda/counterparty_profile.json"))
    args = ap.parse_args()

    recs = [
        json.loads(l)
        for l in (ROOT / "artifacts/exchange_records.jsonl").read_text(encoding="utf-8").splitlines()
        if l.strip()
    ]
    raw = [(r["counterparty"], r.get("corp_name")) for r in recs if r.get("counterparty")]
    named = [(c, o) for c, o in raw if not is_vague(c)]

    surfaces = collections.defaultdict(collections.Counter)
    owners = collections.defaultdict(set)
    for c, owner in named:
        k = canonical(c)
        surfaces[k][c] += 1
        if owner:
            owners[k].add(owner)

    variants = [
        {
            "canonical": k,
            "surface_forms": sorted(v),
            "count": sum(v.values()),
            "universe_partners": sorted(owners[k]),
        }
        for k, v in sorted(surfaces.items(), key=lambda x: -sum(x[1].values()))
    ]
    singles = sum(1 for v in variants if v["count"] == 1)
    hubs = [v for v in variants if len(v["universe_partners"]) >= 2]

    prof = {
        "total": len(raw),
        "named": len(named),
        "unique_raw": len({c for c, _ in named}),
        "unique_canonical": len(variants),
        "singleton_ratio": singles / len(variants),
        "hub_candidates": len(hubs),
        "variants": variants,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(prof, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"canonical {len(variants)} · singleton {prof['singleton_ratio']:.1%} · hub {len(hubs)}")


if __name__ == "__main__":
    main()
