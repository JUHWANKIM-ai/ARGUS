"""A 2-hop graph that promotes counterparties to nodes.

Direct company-to-company edges were rejected at 20 pairs (under 1.2%
completeness), but placing the ordering party as an intermediate node yields 44
pairs and exposes the nuclear and defence value chains. To cite one as evidence,
which ordering party the link ran through (`via`) has to survive.

## B1, where a boilerplate sentence rather than a company name lands in the
## counterparty field

Confirmed by checking all 462 unique source values in `exchange_records.jsonl`
(Task 3, 2026-08-10). Notices of the form "계약상대는 경영상 비밀유지 사유로 2024년
6월 30일 이전 공개될 예정이며,해당 일자는 추후 변동 가능함" occur as 27 spellings
across 34 records. Near-identical sentences differing only in the date fold
together under `canonical()` (two spellings differing only in whitespace collapse
onto the same canonical key), reach `count >= 2`, and get promoted to nodes.
Building without this filter promotes exactly 11 such notices (each count=2, with
0 to 1 partners). Company names do not use that vocabulary. `BOILERPLATE_NOTE`
was checked against all 462 values with zero false positives
(`tests/test_counterparty.py::test_boilerplate_filter_does_not_cut_real_company_names`).

What is filtered is not dropped silently: the source text, count and reason go
into `excluded_boilerplate`.

## B2, country-plus-generic-noun labels ("사우디 소재 EPC" and the like) are left
## alone rather than fixed in `classify()`

Task 2's decision not to add `EPC`, `기업` or `국방부` as vague triggers stands: the
risk of catching real company names is larger. This script only checks whether
such values reach node status and exposes `partners` as they are. When several
universe companies attach to the same vague label -- a sign that distinct
entities have merged into one node --
`tests/test_counterparty.py::test_country_plus_generic_noun_hubs_are_reported_not_silently_promoted`
catches it.
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from argus.corpus.counterparty import canonical, classify  # noqa: E402
RECORDS_PATH = ROOT / "artifacts/exchange_records.jsonl"
DEFAULT_OUT = ROOT / "artifacts/counterparty_graph.json"
MIN_COUNT = 2  # 1회 등장(실측 356개 노드 후보 중 211개, 59.3%)은 노드 가치가 없다

#: B1, a whole confidentiality or deferred-disclosure notice, or a "(삭제)"
#: marker, landing in the counterparty field. Company names do not use this
#: vocabulary (zero false positives across all 462 unique values). "비공개사항" is
#: already caught by `ANON_COUNTERPARTY` ("비공개") but is kept defensively, to
#: show this regex filters the notices completely on its own.
BOILERPLATE_NOTE = re.compile(r"비밀\s*유지|공시유보|비공개사항|^\(?삭제\)?$")

#: B/I-11, vocabulary alone (`BOILERPLATE_NOTE`) cannot prevent **a sentence
#: becoming a node key**. Rechecking all 462 values found 14 sentences that
#: neither `BOILERPLATE_NOTE` nor `classify()` filters and that reach promotion
#: (notices, enumerations, field-label contamination). All currently sit at
#: `count == 1`, so the only thing stopping them is `MIN_COUNT=2` -- **a defence
#: one duplicate away from failing**. Two of them are twins differing only in a
#: month count (`"… 10개월 이내에 …"` / `"… 8개월 이내에 …"`), so the moment one
#: issuer reuses that template the sentence becomes a node.
#:
#: Blocked by **structure** rather than vocabulary. A company name (1) does not
#: begin with an enumeration marker and (2) does not use sentence endings or
#: deictic words (`상기`, `해당 일자`).
#:
#: A length-only condition was **not adopted**. The reviewer's prescription was
#: "over 40 characters, or a leading marker, or a sentence ending -- **any one**",
#: on the claim that "of the 402 promoted values, only those sentences exceed 40
#: characters". Rechecking every value, **43** exceed 40 characters and 32 of them
#: are real names. `방위사업청 (Defense
#: Acquisition Program Administration)`(count 91, **최대 허브**) ·
#: `한국항공우주산업㈜ (KOREA AEROSPACE INDUSTRIES, LTD.)`(13) ·
#: `MPA (Maritime and Port Authority of Singapore)`(7) 등. 그대로 넣으면 그래프가 무너진다.
#: Length is used only as a **floor** guarding against false positives (it keeps
#: a short real company name from matching patterns like `함$`).
SENTENCE_LIKE_MIN_LEN = 20
#: Enumeration and clause markers: `- `, `※ `, `1) `, `3. `, `바. `
_SENTENCE_LEAD = re.compile(r"^\s*[-–—※]\s|^\s*\d+\s*[).]\s|^\s*[가-힣]\.\s")
#: Sentence endings and deictic words. Company names do not use this vocabulary
#: (checked across all 402 values).
_SENTENCE_BODY = re.compile(r"습니다|입니다|바랍니다|(?:^|\s)상기|해당\s*일자|[함임]\.?$")


def sentence_like(value: str) -> str:
    """A reason string when the value is a sentence or an enumeration, otherwise an
    empty string. Such values are not promoted to nodes."""
    if len(value) < SENTENCE_LIKE_MIN_LEN:
        return ""
    if _SENTENCE_LEAD.search(value):
        return "열거·조항 머리표지로 시작한다"
    if _SENTENCE_BODY.search(value):
        return "문장 종결어미 또는 지시어(상기·해당 일자)를 쓴다"
    return ""


def _load_records() -> list[dict]:
    return [
        json.loads(line)
        for line in RECORDS_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def build_graph(out: Path = DEFAULT_OUT, min_count: int = MIN_COUNT) -> dict:
    """Recompute the graph from `exchange_records.jsonl`, write it to `out` and
    return it.

    A pure function that does not go through `main()`'s argparse, so a test can
    call it directly with no arguments (to regenerate the artifact cheaply when a
    fixture finds it missing, for instance). The same pattern as
    `eda_counterparty.build_profile`.
    """
    recs = _load_records()

    counts: collections.Counter[str] = collections.Counter()
    display: dict[str, str] = {}
    partners: dict[str, set[str]] = collections.defaultdict(set)
    kinds: dict[str, str] = {}
    excluded_boilerplate: collections.Counter[str] = collections.Counter()
    excluded_sentence: collections.Counter[str] = collections.Counter()
    sentence_reason: dict[str, str] = {}
    excluded_vague_raw: collections.Counter[str] = collections.Counter()

    for r in recs:
        cp = r.get("counterparty")
        owner = r.get("corp_name")
        if not cp:
            continue
        if BOILERPLATE_NOTE.search(cp):
            excluded_boilerplate[cp] += 1
            continue
        why = sentence_like(cp)
        if why:
            excluded_sentence[cp] += 1
            sentence_reason[cp] = why
            continue
        kind = classify(cp)
        if kind == "vague":
            excluded_vague_raw[cp] += 1
            continue
        k = canonical(cp)
        counts[k] += 1
        kinds[k] = kind
        display.setdefault(k, cp)
        if owner:
            partners[k].add(owner)

    nodes = [
        {
            "canonical": k, "display": display[k], "kind": kinds[k],
            "count": c, "partners": sorted(partners[k]),
        }
        for k, c in counts.items()
        if c >= min_count
    ]
    nodes.sort(key=lambda n: -n["count"])

    via: dict[tuple[str, str], list[str]] = collections.defaultdict(list)
    for n in nodes:
        ps = n["partners"]
        for i in range(len(ps)):
            for j in range(i + 1, len(ps)):
                via[(ps[i], ps[j])].append(n["canonical"])
    pairs = [{"a": a, "b": b, "via": sorted(v)} for (a, b), v in sorted(via.items())]

    out_data = {
        "nodes": nodes,
        "pairs": pairs,
        "stats": {
            "nodes": len(nodes),
            "pairs": len(pairs),
            "min_count": min_count,
            "candidates_before_min_count": len(counts),
            "hubs_2plus_partners": sum(1 for n in nodes if len(n["partners"]) >= 2),
            "boilerplate_excluded_unique": len(excluded_boilerplate),
            "boilerplate_excluded_occurrences": sum(excluded_boilerplate.values()),
            "sentence_like_excluded_unique": len(excluded_sentence),
            "sentence_like_excluded_occurrences": sum(excluded_sentence.values()),
            "vague_excluded_unique": len(excluded_vague_raw),
            "vague_excluded_occurrences": sum(excluded_vague_raw.values()),
        },
        "excluded_boilerplate": [
            {
                "raw": raw,
                "count": c,
                "reason": (
                    "B1: 회사명이 아니라 비밀유지/공시유보 안내문 전체 또는 삭제 마커가 "
                    "counterparty 값으로 들어간 사례 — 실명 회사가 아니라서 노드로 올리지 않았다"
                ),
            }
            for raw, c in sorted(excluded_boilerplate.items(), key=lambda x: -x[1])
        ],
        # B/I-11, the sentences the vocabulary filter misses, which were leaning
        # on `MIN_COUNT` alone. They are not dropped silently: the source text,
        # count and reason are kept, because without a record of what was filtered
        # the next reader cannot tell "not caught" from "not there".
        "excluded_sentence_like": [
            {"raw": raw, "count": c,
             "reason": f"I-11: 회사명이 아니라 문장·열거문이 counterparty 값이다 — "
                       f"{sentence_reason[raw]}"}
            for raw, c in sorted(excluded_sentence.items(), key=lambda x: -x[1])
        ],
        # Previously only the count survived, with no source text, so whether a
        # filter had misfired could not be checked afterwards.
        "excluded_vague": [
            {"raw": raw, "count": c,
             "reason": "비실명 계약상대(ANON_COUNTERPARTY) — 어느 기업인지 원문에 없어 "
                       "노드로 올리면 서로 다른 실체가 한 노드로 뭉친다"}
            for raw, c in sorted(excluded_vague_raw.items(), key=lambda x: -x[1])
        ],
    }

    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(out_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return out_data


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--min-count", type=int, default=MIN_COUNT)
    args = ap.parse_args()

    data = build_graph(Path(args.out), args.min_count)
    s = data["stats"]
    print(
        f"nodes {s['nodes']} · pairs {s['pairs']} · hubs(2+partners) {s['hubs_2plus_partners']} · "
        f"boilerplate excluded {s['boilerplate_excluded_unique']} unique / "
        f"{s['boilerplate_excluded_occurrences']} occurrences · "
        f"sentence-like excluded {s['sentence_like_excluded_unique']} unique / "
        f"{s['sentence_like_excluded_occurrences']} occurrences"
    )


if __name__ == "__main__":
    main()
