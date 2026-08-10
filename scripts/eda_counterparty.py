"""counterparty 표기 실태 — 노드 승격 전에 무엇을 정규화해야 하는지 센다.

정규화·비실명 판정 규칙은 `argus.corpus.counterparty` 가 정본이다(Task 2 로 이관됨).
`scripts/validate_evalset.py::normalize_counterparty` · `ANON_COUNTERPARTY` 는 이제
그 모듈을 감싼 얇은 별칭이다.
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from argus.corpus.counterparty import ANON_COUNTERPARTY, canonical  # noqa: E402


DEFAULT_OUT = ROOT / "artifacts/eda/counterparty_profile.json"
RECORDS_PATH = ROOT / "artifacts/exchange_records.jsonl"


def is_vague(raw: str) -> bool:
    return bool(ANON_COUNTERPARTY.search(raw))


def build_profile(out: Path = DEFAULT_OUT) -> dict:
    """`exchange_records.jsonl` 에서 프로파일을 다시 계산해 `out` 에 쓰고 반환한다.

    `main()` 의 argparse 를 거치지 않는 순수 함수라 테스트가 인자 없이 직접 부를 수 있다
    (예: fixture 에서 산출물이 없을 때 값싸게 재생성).
    """
    recs = [
        json.loads(l)
        for l in RECORDS_PATH.read_text(encoding="utf-8").splitlines()
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
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(prof, ensure_ascii=False, indent=2), encoding="utf-8")
    return prof


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    args = ap.parse_args()
    prof = build_profile(Path(args.out))
    n, s, h = prof["unique_canonical"], prof["singleton_ratio"], prof["hub_candidates"]
    print(f"canonical {n} · singleton {s:.1%} · hub {h}")


if __name__ == "__main__":
    main()
