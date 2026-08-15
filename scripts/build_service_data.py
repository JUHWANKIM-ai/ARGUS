"""ARGUS 서비스 사이트 v2 — 공시 데이터를 화면용 JSON 으로 빌드한다.

    PYTHONIOENCODING=utf-8 python scripts/build_service_data.py

**LLM 0회 · 크레딧 0원 · 네트워크 0회.** `artifacts/` 의 공시 원본과 70개사 명부를
읽어 `site/public/data/` 아래에 JSON 을 낸다. 페이지는 그것만 읽는다.

    artifacts/*.jsonl  ──▶  이 스크립트  ──▶  site/public/data/*.json  ──▶  Next.js

🔴 **이 스크립트의 규율 넷.** 주석이 아니라 코드다.

    ① 표준 라이브러리만 쓴다        `src/argus` 를 import 하지 않는다 — `config` 가
                                   `.env` 경로를 안다. 통로 자체를 만들지 않는다.
    ② 읽는 파일이 **명단**이다      `ALLOWED_READS` 에 없는 경로는 `read_text()` 가
                                   거부한다. 🔴 **금지 목록이 아니라 허용 목록이다** —
                                   금지 목록은 다음 세대가 파일을 늘리면 조용히 샌다.
    ③ 모르는 필드가 붙으면 죽는다   원본이 필드를 늘리면 `gate()` 가 빌드를 멈춘다.
                                   **실을지는 사람이 판단한다.**
    ④ 추정하지 않는다              값이 없으면 `null` 이다. 0 으로도 어림수로도 쓰지 않는다.

🔴 **읽기 게이트를 허용 목록으로 짠 이유.** v1(`build_site.py`)은 금지 토큰 목록이었다.
그런데 이번 판은 **금지 대상의 이름을 소스에 적는 것 자체가 금지**다(계획서의 테스트가
소스를 훑는다). 허용 목록은 그 제약을 풀면서 동시에 더 강하다 — **여섯 파일 밖으로는
어떤 경로도 열리지 않는다.** 스펙 §5b (a) 의 판단을 이렇게 구현했다.

🔴 **v1 의 14자리 식별자 차단은 상속하지 않는다.** v1 은 접수번호가 평가 정답의 근거를
지목해서 막았다. 여기서 접수번호는 **DART 원문으로 가는 열쇠**이고 출처가 공시 원본이다.
근거가 달라서 판단이 다르다 — 스펙 §5b (d) 에 기록했다.
"""
from __future__ import annotations

import csv
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "site" / "public" / "data"


class ForbiddenRead(RuntimeError):
    """🔴 서비스 빌드가 열어서는 안 되는 경로다. 명단에 없으면 전부 여기로 온다."""


# ─────────────────────────────────────────────────────────────────────────────
# 1. 읽기 게이트 — 🔴 허용 목록 (규율 ②)
# ─────────────────────────────────────────────────────────────────────────────

CORPUS = "artifacts"
ROSTER = "data/dart-disclosures/universe.csv"

#: 🔴 **이 여섯 개가 전부다.** 서비스 데이터는 공시 원본과 70개사 명부에서만 온다.
#: 새 원천이 필요하면 여기에 적어라 — 적기 전에는 열리지 않는다.
ALLOWED_READS: frozenset[str] = frozenset({
    f"{CORPUS}/exchange_records.jsonl",   # 거래소공시 (계약·금액·상대·정정 표시)
    f"{CORPUS}/major_records.jsonl",      # 주요사항보고 (건수만 센다)
    f"{CORPUS}/holding_records.jsonl",    # 지분공시     (건수만 센다)
    f"{CORPUS}/references.jsonl",         # 정정 체인 — 추적 실패 사유가 여기 있다
    f"{CORPUS}/counterparty_graph.json",  # 거래관계 그래프
    ROSTER,                               # 70개사 명부 (UTF-8 BOM)
})


def _guard(rel: str) -> Path:
    posix = str(rel).replace("\\", "/")
    if posix not in ALLOWED_READS:
        raise ForbiddenRead(
            f"🔴 서비스 빌드는 이 파일을 읽지 않는다 — {posix!r}. "
            f"허용된 것은 {sorted(ALLOWED_READS)} 뿐이다."
        )
    return ROOT / posix


def read_text(rel: str) -> str:
    #: 명부만 BOM 이 붙어 있다(엑셀 산출물). 나머지는 순수 UTF-8.
    encoding = "utf-8-sig" if rel == ROSTER else "utf-8"
    return _guard(rel).read_text(encoding=encoding)


def read_jsonl(rel: str) -> list[dict]:
    return [json.loads(line) for line in read_text(rel).splitlines() if line.strip()]


def read_json(rel: str) -> dict:
    return json.loads(read_text(rel))


def read_csv(rel: str) -> list[dict]:
    return list(csv.DictReader(read_text(rel).splitlines()))


# ─────────────────────────────────────────────────────────────────────────────
# 2. 필드 게이트 — 🔴 모르는 필드가 붙으면 죽는다 (규율 ③)
# ─────────────────────────────────────────────────────────────────────────────


def gate(row: dict, allowed: frozenset[str], where: str) -> dict:
    """🔴 모르는 필드가 붙으면 빌드가 죽는다. 블랙리스트는 다음 세대에 조용히 샌다."""
    unknown = set(row) - allowed
    if unknown:
        raise SystemExit(f"🔴 {where} 에 모르는 필드: {sorted(unknown)} — 실을지 판단하라")
    return row


#: 원본이 들고 있는 필드 전량(2026-08-16 실측). 🔴 **원본이 늘어나면 여기가 먼저 깨진다.**
#: 늘어난 필드를 화면에 실을지는 사람이 판단한다 — 조용히 통과시키지 않는다.
SOURCE_EXCHANGE = frozenset({
    "doc_id", "rcept_no", "rcept_dt", "corp_code", "corp_name", "sector", "doc_subtype",
    "is_correction", "amount", "amount_type", "ratio", "ratio_base", "counterparty",
    "period_start", "period_end", "event_date", "reserved_reason", "note", "related",
    "form", "fields", "corrections", "schema_matched", "withheld", "raw_rows", "title",
    "is_latest", "terminates", "markdown",
})
SOURCE_REFERENCE = frozenset({
    "source_rcept_no", "source_doc_id", "corp_name", "target_date", "form_name",
    "resolved", "reason", "targets", "form_matched",
})
SOURCE_NODE = frozenset({"canonical", "display", "kind", "count", "partners"})
SOURCE_PAIR = frozenset({"a", "b", "via"})

#: 🔴 **실어도 되는 것.** 여기 없는 것은 화면에 가지 않는다. 원문(`raw_rows`·`markdown`)과
#: 내부 표시(`schema_matched`·`is_latest`)는 의도적으로 뺐다 — 코퍼스 재배포가 아니다.
CONTRACT_FIELDS = frozenset({
    "rcept_no", "rcept_dt", "amount", "amount_type", "counterparty",
    "period_start", "period_end", "is_correction", "doc_subtype", "title",
})
CORRECTION_FIELDS = frozenset({
    "rcept_no", "corp_name", "slug", "date", "form_name", "resolved", "reason",
    #: `origin` 은 `amount_before` 를 **어느 공시에서 읽었는지**다. 🔴 금액만 싣고 출처를
    #: 안 실으면 화면이 그 수를 검증할 방법이 없다.
    "targets", "origin", "amount_before", "amount_after", "title",
})
COMPANY_FIELDS = frozenset({
    "slug", "name", "sector", "n_disclosures", "n_corrections", "top_partners",
})
HUB_FIELDS = frozenset({"name", "canonical", "kind", "count", "partners"})
PAIR_FIELDS = frozenset({"a", "b", "via"})


# ─────────────────────────────────────────────────────────────────────────────
# 3. 이름 정규화 — 계약상대 표기가 공시마다 흔들린다
# ─────────────────────────────────────────────────────────────────────────────

#: 🔴 **이것은 추정이 아니라 표기 정리다.** 법인격 표기와 공백만 지운다. 뜻을 바꾸지 않는다.
#: 이걸로도 못 붙는 상대는 **세지 않고 `null` 로 남긴다**(규율 ④) — 어림수를 쓰지 않는다.
_TRIM = re.compile(r"\(주\)|㈜|주식회사|\(유\)|유한회사|\(재\)|재단법인|대한민국")
_SQUEEZE = re.compile(r"[\s,.·]+")


def normalize(name: str | None) -> str:
    return _SQUEEZE.sub("", _TRIM.sub("", name or "")).strip().lower()


#: 🔴 slug 는 `corp_code` 다. 한글 회사명을 경로에 그대로 쓰면 정적 export 가 만드는
#: 디렉터리 이름이 호스트마다 다르게 인코딩된다. `corp_code` 는 DART 가 발급한 8자리
#: 식별자라 **ASCII 이고 유일하고 회사명이 바뀌어도 안 움직인다.** 회사명은 JSON 안에 있다.
def slug_of(corp_code: str) -> str:
    return corp_code.strip()


# ─────────────────────────────────────────────────────────────────────────────
# 4. 원천을 읽는다
# ─────────────────────────────────────────────────────────────────────────────


def load_sources() -> dict:
    roster = read_csv(ROSTER)
    exchange = [gate(r, SOURCE_EXCHANGE, "거래소공시") for r in
                read_jsonl(f"{CORPUS}/exchange_records.jsonl")]
    references = [gate(r, SOURCE_REFERENCE, "정정 체인") for r in
                  read_jsonl(f"{CORPUS}/references.jsonl")]
    graph = read_json(f"{CORPUS}/counterparty_graph.json")
    nodes = [gate(n, SOURCE_NODE, "관계망 노드") for n in graph["nodes"]]
    pairs = [gate(p, SOURCE_PAIR, "관계망 쌍") for p in graph["pairs"]]

    #: 주요사항보고·지분공시는 **건수만** 센다 — 화면에 필드를 싣지 않으므로 게이트하지 않는다.
    other_dates: list[str] = []
    other_counts: Counter[str] = Counter()
    for name in ("major_records.jsonl", "holding_records.jsonl"):
        for row in read_jsonl(f"{CORPUS}/{name}"):
            other_counts[row["corp_code"]] += 1
            other_dates.append(row["rcept_dt"])

    return {"roster": roster, "exchange": exchange, "references": references,
            "nodes": nodes, "pairs": pairs, "other_counts": other_counts,
            "dates": [r["rcept_dt"] for r in exchange] + other_dates}


# ─────────────────────────────────────────────────────────────────────────────
# 5. 정정 체인 — 🔴 원공시를 못 찾으면 그렇게 적는다
# ─────────────────────────────────────────────────────────────────────────────

#: 추적이 실패한 사유. 🔴 **감추면 목록이 실제보다 완전해 보인다**(스펙 §4.3).
NO_REFERENCE = "no_reference"
#: 원공시 **후보가 여럿인데** 어느 것인지 못 가린 경우. 체인은 보여 주되 금액은 비우다.
AMBIGUOUS = "ambiguous_targets"

_TITLE_NOISE = re.compile(r"[\s\-—·,.()\[\]]+")


def title_key(title: str | None) -> str:
    """계약명을 비교용으로 다듬는다. 🔴 **뜻을 바꾸지 않는다** — 구두점과 공백만 지운다."""
    return _TITLE_NOISE.sub("", title or "").lower()


def resolve_chain(record: dict, by_rcept: dict, ref_edges: list[dict]) -> dict:
    """정정 하나가 가리키는 **원공시**를 찾는다. 못 찾으면 그렇게 적는다.

    🔴 **`related` 를 쓰지 않는다.** 그 필드는 정정 체인이 아니라 *관련공시 묶음*이다 —
    같은 회사의 서로 다른 사업(`P5 Project` 와 `[P4 Ph2(하동)] 그린동 변전소`)이 서로를
    가리킨다. 그것으로 금액을 비교하면 **없는 변화를 만들어 낸다**(2026-08-16 확인:
    삼성E&A 에서 883,888,500,000 → 1,770,780,000,000 이라는 가짜 diff 가 나왔다).

    쓰는 것은 `references.jsonl` 뿐이다. 다만 그 아티팩트는 *(날짜, 서식명)* 으로 풀기
    때문에 **같은 날 같은 서식의 공시가 여럿이면 후보가 여럿**이다. 그래서 계약명으로
    한 번 더 가린다. 🔴 **그래도 못 가리면 금액을 비우고 후보만 보여 준다** — 규율 ④.
    """
    here, mine = record["rcept_dt"], record["rcept_no"]
    targets = sorted({
        t for edge in ref_edges if edge["resolved"]
        for t in edge["targets"]
        if t in by_rcept and t != mine and by_rcept[t]["rcept_dt"] <= here
    })

    if not targets:
        #: 🔴 추적 실패. 사유는 원본이 적어 둔 것을 쓴다 — 없으면 참조 자체가 없었다.
        reasons = [e["reason"] for e in ref_edges if not e["resolved"]]
        return {"targets": [], "origin": None, "resolved": False, "amount_before": None,
                "reason": Counter(reasons).most_common(1)[0][0] if reasons else NO_REFERENCE}

    #: 🔴 **계약명이 같은 후보만 원공시로 인정한다.** 여러 번 정정됐으면 가장 가까운 앞선 것.
    #: 후보가 하나뿐이어도 계약명이 다르면 **쓰지 않는다.** 2026-08-16 에 그 분기를 켜 봤더니
    #: `VLAC 2척` 을 `157,000 DWT 원유운반선 2척` 의 원공시로, `신고리5,6호기` 를
    #: `새울3,4호기` 의 원공시로 잡아 **가짜 금액 변화 넷**을 만들었다. 참을 하나 놓치는 것이
    #: 거짓을 하나 싣는 것보다 낫다 — 체인(`targets`)은 그대로 보여 주므로 사람이 볼 수 있다.
    same_title = [t for t in targets
                  if title_key(by_rcept[t]["title"]) == title_key(record["title"])]
    if not same_title:
        return {"targets": targets, "origin": None, "resolved": True,
                "reason": AMBIGUOUS, "amount_before": None}

    origin = max(same_title, key=lambda t: (by_rcept[t]["rcept_dt"], t))
    return {"targets": targets, "origin": origin, "resolved": True, "reason": None,
            "amount_before": by_rcept[origin].get("amount")}


# ─────────────────────────────────────────────────────────────────────────────
# 6. 빌드
# ─────────────────────────────────────────────────────────────────────────────


def build(src: dict) -> dict[str, object]:
    by_rcept = {r["rcept_no"]: r for r in src["exchange"]}
    edges_by_source: dict[str, list[dict]] = defaultdict(list)
    for edge in src["references"]:
        edges_by_source[edge["source_rcept_no"]].append(edge)

    slug_by_name = {r["corp_name"]: slug_of(r["corp_code"]) for r in src["roster"]}

    # ── 회사별 계약 · 상대 집계 ───────────────────────────────────────────────
    contracts_by_code: dict[str, list[dict]] = defaultdict(list)
    #: (회사명, 상대 정규화) → 건수. 🔴 **센 것만 싣는다.**
    counted: Counter[tuple[str, str]] = Counter()
    for row in sorted(src["exchange"], key=lambda r: (r["rcept_dt"], r["rcept_no"]),
                      reverse=True):
        contracts_by_code[row["corp_code"]].append(gate(
            {k: row.get(k) for k in sorted(CONTRACT_FIELDS)}, CONTRACT_FIELDS, "계약 행"))
        if row.get("counterparty"):
            counted[(row["corp_name"], normalize(row["counterparty"]))] += 1

    # ── 정정 ─────────────────────────────────────────────────────────────────
    corrections: list[dict] = []
    for row in src["exchange"]:
        if not row["is_correction"]:
            continue
        chain = resolve_chain(row, by_rcept, edges_by_source.get(row["rcept_no"], []))
        corrections.append(gate({
            "rcept_no": row["rcept_no"],
            "corp_name": row["corp_name"],
            "slug": slug_by_name[row["corp_name"]],
            "date": row["rcept_dt"],
            "form_name": row.get("form") or row["doc_subtype"],
            #: 계약명. 🔴 정정 목록에서 *"무엇에 대한 정정인가"* 가 이것 없이는 안 보인다.
            "title": row.get("title"),
            "resolved": chain["resolved"],
            "reason": chain["reason"],
            "targets": chain["targets"],
            "origin": chain["origin"],
            "amount_before": chain["amount_before"],
            #: 🔴 정정본이 적은 금액. `amount_before` 와 같을 수도 있다(금액이 안 바뀐 정정).
            "amount_after": row.get("amount"),
        }, CORRECTION_FIELDS, "정정 행"))
    corrections.sort(key=lambda c: (c["date"], c["rcept_no"]), reverse=True)
    corrections_by_code: dict[str, list[dict]] = defaultdict(list)
    for c in corrections:
        corrections_by_code[c["slug"]].append(c)

    # ── 관계망 ───────────────────────────────────────────────────────────────
    hubs = [gate({"name": n["display"], "canonical": n["canonical"], "kind": n["kind"],
                  "count": n["count"], "partners": n["partners"]},
                 HUB_FIELDS, "관계망 허브") for n in src["nodes"]]
    pairs = [gate({k: p[k] for k in sorted(PAIR_FIELDS)}, PAIR_FIELDS, "관계망 쌍")
             for p in src["pairs"]]

    #: 회사 → 그 회사가 등장하는 허브들. 🔴 건수는 **셀 수 있을 때만** 싣는다.
    partners_by_company: dict[str, list[dict]] = defaultdict(list)
    for node in src["nodes"]:
        keys = {normalize(node["canonical"]), normalize(node["display"])}
        for company in node["partners"]:
            n = sum(counted.get((company, k), 0) for k in keys)
            partners_by_company[company].append({
                "name": node["display"], "kind": node["kind"],
                #: 🔴 이 회사의 계약 중 이 상대를 적은 것. 표기가 안 붙으면 `null` 이다 —
                #: 관계 자체는 원본이 확인했으므로 **관계를 지우지 않고 건수만 비운다.**
                "contracts": n or None,
            })
    for entries in partners_by_company.values():
        entries.sort(key=lambda p: (-(p["contracts"] or 0), p["name"]))

    # ── 회사 ─────────────────────────────────────────────────────────────────
    companies: list[dict] = []
    details: dict[str, dict] = {}
    for row in src["roster"]:
        slug = slug_of(row["corp_code"])
        name = row["corp_name"]
        contracts = contracts_by_code.get(row["corp_code"], [])
        mine = corrections_by_code.get(slug, [])
        partners = partners_by_company.get(name, [])
        n_disclosures = len(contracts) + src["other_counts"].get(row["corp_code"], 0)
        companies.append(gate({
            "slug": slug, "name": name, "sector": row["sector"],
            "n_disclosures": n_disclosures, "n_corrections": len(mine),
            "top_partners": [p["name"] for p in partners[:3]],
        }, COMPANY_FIELDS, "회사 행"))
        details[slug] = {
            "slug": slug, "name": name, "sector": row["sector"],
            "n_disclosures": n_disclosures, "n_corrections": len(mine),
            "contracts": contracts,
            #: 🔴 `resolved=false` 도 담는다. 감추면 이 회사가 실제보다 깨끗해 보인다.
            "corrections": mine,
            "partners": partners,
            #: 🔴 재무 요약은 다음 파도에서 정기공시에서 뽑는다. **지금 지어내지 않는다.**
            "financials": None,
        }

    # ── 메타 ─────────────────────────────────────────────────────────────────
    dates = sorted(d for d in src["dates"] if d)

    def month(yyyymmdd: str) -> str:
        return f"{yyyymmdd[:4]}-{yyyymmdd[4:6]}"

    meta = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "ranges": {"companies": len(companies), "from": month(dates[0]),
                   "to": month(dates[-1])},
        "counts": {
            "disclosures": sum(c["n_disclosures"] for c in companies),
            "corrections": len(corrections),
            "contracts": len(src["exchange"]),
            "partners": len(hubs),
        },
    }

    files: dict[str, object] = {
        "meta.json": meta,
        "companies.json": companies,
        "corrections.json": corrections,
        "network.json": {"hubs": hubs, "pairs": pairs},
    }
    for slug, detail in details.items():
        files[f"company/{slug}.json"] = detail
    return files


def write(files: dict[str, object], out: Path) -> None:
    for name, payload in sorted(files.items()):
        target = out / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
            encoding="utf-8", newline="\n")


def main() -> int:
    files = build(load_sources())
    write(files, OUT)
    meta = files["meta.json"]
    print(f"site/public/data: {len(files)} 파일 → {OUT}")
    print(f"  회사 {meta['ranges']['companies']}개사 · "
          f"공시 {meta['counts']['disclosures']}건 · "
          f"계약 {meta['counts']['contracts']}건 · "
          f"정정 {meta['counts']['corrections']}건 · "
          f"거래상대 {meta['counts']['partners']}곳")
    print(f"  기간 {meta['ranges']['from']} ~ {meta['ranges']['to']} · LLM 0회 · 0원")
    return 0


if __name__ == "__main__":
    sys.exit(main())
