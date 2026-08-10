"""Phase A 부분 재실행 — 신규 6섹터 25개사 프로파일 (docs/pipeline/01-eda.md).

**배경**: 평가셋 확장으로 게임·로봇·소비재유통·자동차모빌리티·철강·통신 6섹터가
`universe.csv` 에 새로 들어왔는데, 기존 Phase A(`eda_documents.py`/`eda_relations.py`)는
그 문서들을 프로파일한 적이 없다. 여기서는 **새 측정 함수를 만들지 않는다** —
`eda_documents.profile_document`(표 문자비 · 숫자 토큰비 등)와
`argus.corpus.corrections.build_chains`(정정체인 연결률)를 그대로 재사용하고,
섹터 필터만 씌운다. 새로 쓰면 기존 프로파일과 비교가 안 된다.

**실측 vs 브리프 가정**: 브리프는 "6섹터 25개사"를 가정했지만 `universe.csv` 실측은
**19개사**다(게임 3 · 로봇 2 · 소비재·유통 4 · 자동차·모빌리티 4 · 철강 3 · 통신 3).
아래 코드는 실측(19개사)을 그대로 쓴다 — 계획 문서 숫자가 아니라 데이터가 정본이다.

LLM 호출 0회.

사용
    PYTHONIOENCODING=utf-8 PYTHONUTF8=1 python scripts/eda_new_sectors.py
"""
from __future__ import annotations

import json
import random
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # eda_documents 재사용
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from eda_documents import _doc_files, _pct, profile_document  # noqa: E402

from argus.config import settings  # noqa: E402
from argus.corpus.corrections import build_chains  # noqa: E402
from argus.corpus.html_doc import read_document  # noqa: E402
from argus.corpus.manifest import filter_docs, load_manifest, load_universe  # noqa: E402

NEW_SECTORS = {"게임", "로봇", "소비재·유통", "자동차·모빌리티", "철강", "통신"}

RECORD_FILES = {
    "exchange": "exchange_records.jsonl",
    "major": "major_records.jsonl",
    "holding": "holding_records.jsonl",
}

# 기존 실측 (docs/pipeline/01-eda.md, 2026-08-09) — 신규 섹터와 비교할 기준선.
# 이 스크립트가 새로 재는 것이 아니라 문서에 이미 적힌 값을 그대로 옮긴 것이다.
BASELINE = {
    "schema_matched_ratio": {"exchange": 0.842, "major": 0.992, "holding": 1.0},
    "table_char_ratio_range": [0.71, 0.93],
    "num_token_ratio_range": [0.075, 0.20],
    "correction_chain_link_rate": {"exchange": 0.610, "major": 0.757, "holding": 0.927},
}

# 평가셋 확장 트랙(T1~T5)이 이미 찾은 정규화 결함 4건. 수정 대상이 아니라 기록 대상 —
# 값까지 그대로 남겨서 다음 파싱 트랙이 놓치지 않게 한다.
KNOWN_DEFECTS = [
    {
        "corp_name": "NAVER",
        "kind": "chain_amount_frozen",
        "detail": "왈라팝 3본 체인의 amount 가 원공시 903,559,577,175 에 멈춤 "
                  "(본문 최신 902,863,854,765)",
    },
    {
        "corp_name": "크래프톤",
        "kind": "ratio_wrong",
        "detail": "정정본 ratio 가 1.0 (본문 2.93%)",
    },
    {
        "corp_name": "이마트",
        "kind": "amount_truncated",
        "detail": "정정본 amount 1조 (본문 '1조 1,500억원')",
    },
    {
        "corp_name": "아모레퍼시픽",
        "kind": "internal_inconsistency",
        "detail": "최근매출액이 같은 문서 안에서 100만원 어긋남",
    },
]


def _load_records(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def _doc_text_profile(docs: list[dict]) -> dict:
    """`eda_documents.profile_document` 재사용 — 표 문자비 · 숫자 토큰비."""
    items = []
    for doc in docs:
        files = _doc_files(doc)
        if not files:
            continue
        html = read_document(files[0])
        prof = profile_document(html)
        if prof:
            items.append(prof)
    if not items:
        return {"n_docs_profiled": 0, "table_char_ratio": None, "num_token_ratio": None}
    return {
        "n_docs_profiled": len(items),
        "table_char_ratio": _pct([i["table_char_ratio"] for i in items]),
        "num_token_ratio": _pct([i["num_token_ratio"] for i in items]),
    }


def build_profile(out_path: Path, periodic_sample: int = 40, seed: int = 42) -> dict:
    t0 = time.time()
    artifacts_root = settings().artifacts_root
    universe = load_universe()
    manifest = list(load_manifest())
    rng = random.Random(seed)

    corp_sector = {c: row.get("sector") for c, row in universe.items()}
    sector_corps: dict[str, set[str]] = defaultdict(set)
    for corp, sector in corp_sector.items():
        if sector in NEW_SECTORS:
            sector_corps[sector].add(corp)
    all_new_corps: set[str] = set().union(*sector_corps.values()) if sector_corps else set()

    # ---- schema_matched: exchange/major/holding_records.jsonl 재사용 (LLM 재추출 없음) --
    records_by_group = {g: _load_records(artifacts_root / f) for g, f in RECORD_FILES.items()}

    # ---- manifest 문서: exchange/major/holding 은 전량, periodic 은 층화표본 -----------
    # periodic 은 5.3GB 라 신규 6섹터분만도 ~1GB 다. eda_documents.py 와 같은 표본 방식.
    manifest_by_group: dict[str, list[dict]] = defaultdict(list)
    for doc in manifest:
        if doc["corp_name"] in all_new_corps:
            manifest_by_group[doc["doc_group"]].append(doc)

    periodic_by_sub: dict[str, list[dict]] = defaultdict(list)
    for doc in manifest_by_group["periodic"]:
        periodic_by_sub[doc["doc_subtype"]].append(doc)
    per_sub_n = max(1, periodic_sample // max(1, len(periodic_by_sub))) if periodic_by_sub else 0
    periodic_sample_docs = [
        d for sub, docs in periodic_by_sub.items()
        for d in rng.sample(docs, min(per_sub_n, len(docs)))
    ]

    # ---- 정정체인: exchange/major/holding 전체에 대해 한 번만 계산(build_chains 재사용) --
    chains_by_group = {g: build_chains(g) for g in ("exchange", "major", "holding")}
    in_chain_by_group = {
        g: {c for members in chains.values() for c in members}
        for g, chains in chains_by_group.items()
    }
    # 정정체인 판단용 원본 문서(코퍼스 전체, 섹터 무관) — build_chains 와 짝을 맞춘다.
    all_docs_by_group = {g: filter_docs(doc_group=g) for g in ("exchange", "major", "holding")}

    by_sector: dict[str, dict] = {}
    for sector in sorted(NEW_SECTORS):
        corps = sector_corps.get(sector, set())

        schema_group: dict[str, dict] = {}
        n_total = n_matched = 0
        for group in ("exchange", "major", "holding"):
            recs = [r for r in records_by_group[group] if r.get("corp_name") in corps]
            matched = sum(1 for r in recs if r.get("schema_matched"))
            schema_group[group] = {"n": len(recs), "matched": matched}
            n_total += len(recs)
            n_matched += matched

        corr_group: dict[str, dict] = {}
        for group in ("exchange", "major", "holding"):
            docs = [d for d in all_docs_by_group[group] if d["corp_name"] in corps]
            corrections = [d for d in docs if d["is_correction"]]
            linked = sum(1 for d in corrections if d["rcept_no"] in in_chain_by_group[group])
            corr_group[group] = {
                "n_corrections": len(corrections),
                "n_linked": linked,
                "link_rate": round(linked / len(corrections), 4) if corrections else None,
            }

        text_docs = [
            d for group in ("exchange", "major", "holding") for d in manifest_by_group[group]
            if d["corp_name"] in corps
        ] + [d for d in periodic_sample_docs if d["corp_name"] in corps]
        text_profile = _doc_text_profile(text_docs)

        by_sector[sector] = {
            "n_companies": len(corps),
            "companies": sorted(corps),
            "n_docs": n_total,
            "schema_matched_ratio": round(n_matched / n_total, 4) if n_total else 0.0,
            "schema_matched_by_group": schema_group,
            "correction_chain_by_group": corr_group,
            "table_char_ratio": text_profile["table_char_ratio"],
            "num_token_ratio": text_profile["num_token_ratio"],
            "n_docs_text_profiled": text_profile["n_docs_profiled"],
            "n_docs_periodic_total": len(
                [d for d in manifest_by_group["periodic"] if d["corp_name"] in corps]
            ),
            "n_docs_periodic_sampled": len(
                [d for d in periodic_sample_docs if d["corp_name"] in corps]
            ),
        }

    payload = {
        "meta": {
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "elapsed_sec": round(time.time() - t0, 1),
            "llm_calls": 0,
            "new_sectors": sorted(NEW_SECTORS),
            "n_companies_total": len(all_new_corps),
            "note_on_company_count": (
                "브리프는 '6섹터 25개사'를 가정했으나 universe.csv 실측은 19개사다 "
                "(게임 3 · 로봇 2 · 소비재·유통 4 · 자동차·모빌리티 4 · 철강 3 · 통신 3). "
                "계획 문서 숫자가 아니라 실측을 썼다."
            ),
            "sampling": {
                "exchange": "전량",
                "major": "전량",
                "holding": "전량",
                "periodic": (
                    f"층화표본 {len(periodic_sample_docs)} / "
                    f"{len(manifest_by_group['periodic'])} "
                    "(신규섹터 periodic 만도 ~1GB — 전량 파싱은 다른 트랙 담당)"
                ),
            },
            "baseline_for_comparison": BASELINE,
        },
        "by_sector": by_sector,
        "known_defects": KNOWN_DEFECTS,
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def main() -> None:
    out = Path(__file__).resolve().parents[1] / "artifacts" / "eda" / "new_sector_profile.json"
    payload = build_profile(out)
    print(f"wrote {out} ({out.stat().st_size / 1024:.1f} KB, "
          f"{payload['meta']['elapsed_sec']}s)")


if __name__ == "__main__":
    main()
