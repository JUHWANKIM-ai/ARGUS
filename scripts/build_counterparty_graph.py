"""counterparty 를 노드로 승격한 2-hop 그래프.

직접 기업-기업 엣지는 20쌍(완결도 1.2% 미만)으로 기각됐지만, 발주처를 중개 노드로
두면 44쌍이 되고 원전·방산 밸류체인이 드러난다. 근거로 인용하려면 '어느 발주처를
거쳤는가'(`via`)가 남아야 한다.

## B1, "회사명"이 아니라 "안내문 문장"이 counterparty 값으로 들어간 사례

`exchange_records.jsonl` 462개 유일 원문 전량 대조(Task 3, 2026-08-10)로 확인했다.
"계약상대는 경영상 비밀유지 사유로 2024년 6월 30일 이전 공개될 예정이며,해당 일자는
추후 변동 가능함" 류 안내문이 27종 표기·34건 있다. 날짜만 다른 거의 동일한 문장이
`canonical()`로 접히면(공백만 다른 두 표기가 같은 canonical 키로 합쳐지는 식으로)
`count >= 2`가 되어 노드로 승격된다. 실제로 이 필터 없이 빌드하면 정확히 11개의
"안내문 문장"이 노드가 된다(각각 count=2, partners 0~1개). 회사명은 저 어휘를 쓰지
않는다. `BOILERPLATE_NOTE` 는 462건 전량 대조로 오탐 0건을 확인했다
(`tests/test_counterparty.py::test_boilerplate_filter_does_not_cut_real_company_names`).

거른 항목은 조용히 버리지 않고 `excluded_boilerplate`에 원문·건수·사유를 기록한다.

## B2, 국가+일반명사 조합("사우디 소재 EPC" 등)은 `classify()`를 고치지 않고 그대로 둔다

Task 2 가 `EPC`·`기업`·`국방부` 를 vague 트리거로 넣지 않기로 한 결정을 유지한다.
실명 회사 오탐 위험이 더 크다. 이 스크립트는 그 값들이 노드로 올라오는지만 확인하고
`partners` 를 그대로 노출한다. 여러 universe 기업이 같은 모호한 라벨에 걸리면(=서로
다른 실체가 한 노드로 뭉쳤다는 신호) `tests/test_counterparty.py::
test_country_plus_generic_noun_hubs_are_reported_not_silently_promoted` 가 잡는다.
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

#: B1, 비밀유지/공시유보 안내문 전체 또는 "(삭제)" 마커가 counterparty 값으로 들어간
#: 사례. 회사명은 이 어휘를 쓰지 않는다(462개 유일 원문 전량 대조로 오탐 0건 확인).
#: "비공개사항" 은 이미 `ANON_COUNTERPARTY`("비공개")가 잡지만 방어적으로 남겨 둔다.
#: 이 정규식이 단독으로도 안내문을 완결하게 걸러낸다는 걸 보여준다.
BOILERPLATE_NOTE = re.compile(r"비밀\s*유지|공시유보|비공개사항|^\(?삭제\)?$")

#: B/I-11, 어휘(`BOILERPLATE_NOTE`)만으로는 **문장이 노드 키가 되는 사고**를 못 막는다.
#: 462종 전량 재대조로 `BOILERPLATE_NOTE` 도 `classify()` 도 안 걸러 승격 후보로
#: 올라오는 '문장' 14종을 찾았다(안내문·열거문·필드라벨 오염). 전부 현재 `count == 1`
#: 이라 지금 막고 있는 것은 `MIN_COUNT=2` 뿐이고, **그 방어막은 중복 한 건 거리**다.
#: 걸린 것 중 두 종은 `"… 10개월 이내에 …"` / `"… 8개월 이내에 …"` 로 **개월 수만 다른
#: 쌍둥이**이므로, 같은 발행사가 같은 템플릿을 한 번 더 쓰면 그 순간 문장이 노드가 된다.
#:
#: 어휘가 아니라 **구조**로 막는다. 회사명은 (1) 열거 머리표지로 시작하지 않고
#: (2) 문장 종결어미·지시어(`상기`·`해당 일자`)를 쓰지 않는다.
#:
#: 길이 단독 조건은 **채택하지 않았다.** 리뷰어 처방은 "40자 초과 · 머리표지 · 문장어미
#: 중 **하나라도**" 였고 "승격 402종 중 40자 초과는 그 문장들뿐" 이라고 했으나, 전량
#: 재대조 결과 40자 초과는 **43종**이고 그중 32종이 실명이다. `방위사업청 (Defense
#: Acquisition Program Administration)`(count 91, **최대 허브**) ·
#: `한국항공우주산업㈜ (KOREA AEROSPACE INDUSTRIES, LTD.)`(13) ·
#: `MPA (Maritime and Port Authority of Singapore)`(7) 등. 그대로 넣으면 그래프가 무너진다.
#: 길이는 오탐 방어용 **하한**으로만 쓴다(짧은 실명 회사명이 `함$` 류에 걸리는 것을 막는다).
SENTENCE_LIKE_MIN_LEN = 20
#: 열거·조항 머리표지: `- ` · `※ ` · `1) ` · `3. ` · `바. `
_SENTENCE_LEAD = re.compile(r"^\s*[-–—※]\s|^\s*\d+\s*[).]\s|^\s*[가-힣]\.\s")
#: 문장 종결어미·지시어. 회사명은 이 어휘를 쓰지 않는다(402종 전량 대조).
_SENTENCE_BODY = re.compile(r"습니다|입니다|바랍니다|(?:^|\s)상기|해당\s*일자|[함임]\.?$")


def sentence_like(value: str) -> str:
    """문장·열거문이면 사유 문자열, 아니면 빈 문자열. 노드로 승격하지 않는다."""
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
    """`exchange_records.jsonl` 에서 그래프를 다시 계산해 `out` 에 쓰고 반환한다.

    `main()` 의 argparse 를 거치지 않는 순수 함수라 테스트가 인자 없이 직접 부를 수
    있다(예: fixture 에서 산출물이 없을 때 값싸게 재생성), `eda_counterparty.build_profile`
    과 같은 패턴이다.
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
        # B/I-11, 어휘 필터가 못 걸러 `MIN_COUNT` 하나에만 기대고 있던 문장들.
        # 조용히 버리지 않고 원문·건수·사유를 남긴다 (걸러진 기록이 없으면 다음 사람이
        # "안 걸렸다" 와 "없었다" 를 구별할 수 없다).
        "excluded_sentence_like": [
            {"raw": raw, "count": c,
             "reason": f"I-11: 회사명이 아니라 문장·열거문이 counterparty 값이다 — "
                       f"{sentence_reason[raw]}"}
            for raw, c in sorted(excluded_sentence.items(), key=lambda x: -x[1])
        ],
        # 종전에는 건수만 남고 원문이 없어 오탐 여부를 사후에 확인할 수 없었다.
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
