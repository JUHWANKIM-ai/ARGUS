"""Phase A-2, 관계 구조 프로파일 (docs/pipeline/01-eda.md).

LLM 호출 0회. policy §2 의 전제("공시는 관계형 데이터라 GraphRAG 와 맞는다")가
우리 코퍼스에서 실제로 성립하는지 숫자로 검증한다.

측정 항목
  · 정정 체인 길이 분포 · 최장 체인 · 코퍼스 밖 참조 비율
  · '※ 관련공시' 행 파싱, 문서 간 참조 엣지 추출 가능성
  · counterparty 가 universe 70개사에 속하는 비율  <- GraphRAG 채택 기준
  · 기업×분기 공시 밀도 · 정기공시 제출 주기
  · 섹터 응집도

사용
    PYTHONIOENCODING=utf-8 PYTHONUTF8=1 python scripts/eda_relations.py
"""
from __future__ import annotations

import json
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from argus.config import settings  # noqa: E402
from argus.corpus.corrections import build_chains  # noqa: E402
from argus.corpus.dates import parse_date  # noqa: E402
from argus.corpus.html_doc import extract_rows, read_document  # noqa: E402
from argus.corpus.manifest import filter_docs, load_manifest, load_universe  # noqa: E402

# ---------------------------------------------------------------- 기업명 정규화

# 법인격 표기. 한국·영문 양쪽 모두 지운다.
_LEGAL = re.compile(
    r"주식회사|㈜|\(주\)|\(유\)|유한회사|합자회사|재단법인|사단법인|"
    r"\bco\.?\s*,?\s*ltd\.?|\bcorporation\b|\bcorp\.?|\bincorporated\b|\binc\.?|"
    r"\bllc\b|\bplc\b|\bs\.?a\.?\b|\bgmbh\b|\bag\b|\bbv\b|\bnv\b|\bpte\b|\bsdn\b|\bbhd\b|"
    r"\blimited\b|\bltd\.?|\bcompany\b|\bholdings?\b|\bgroup\b",
    re.I,
)
_NONWORD = re.compile(r"[\s\.,·ㆍ\-_'\"()\[\]/&]+")

# 비실명 계약상대, 거래소 서식이 허용하는 익명 표기
_ANON = re.compile(
    r"(지역|소재|권역)?\s*(선주|선사|제약사|바이오|기업|업체|고객|발주처|항공사|"
    r"완성차|병원|통신사)|글로벌|국내\s*(대형|중견)|해외\s*(대형|중견)|익명|비공개|"
    r"영업\s*비밀|비밀\s*유지|미공개|공시\s*유보|영문\s*미표기"
)
# 정부·공공기관
_PUBLIC = re.compile(
    r"청$|청\b|부$|처$|공사$|공단$|연구원$|연구소$|자치|시청|도청|군청|"
    r"방위사업청|조달청|한국수력원자력|한국전력|한국토지주택|국방과학연구소|"
    r"agency|authority|ministry|administration|government|corporation of|"
    r"국방|군|해군|육군|공군|경찰|철도|수자원|송전|전력청|보건|교통부"
)
_LATIN = re.compile(r"[A-Za-z]")
# 계약상대 셀에 여러 회사가 들어가는 경우 분리자
_SPLIT = re.compile(r"\s*(?:,|、|/|외\s|및\s|와\s|과\s)\s*")


def norm_name(name: str) -> str:
    """법인격·공백·구두점을 지운 비교 키."""
    return _NONWORD.sub("", _LEGAL.sub(" ", name or "")).lower()


def build_universe_index() -> tuple[dict[str, str], dict[str, dict]]:
    """정규화 키 -> corp_name. 통용명·영문명도 같은 기업으로 되돌린다."""
    universe = load_universe()
    index: dict[str, str] = {}
    for corp_name, row in universe.items():
        for alias in (corp_name, row.get("listed_name"), row.get("corp_eng_name")):
            key = norm_name(alias or "")
            if len(key) >= 2:
                index.setdefault(key, corp_name)
    # 자주 등장하는 표기 변형, 코퍼스 실측으로 확인된 것만 넣는다
    extra = {
        "케이티": "케이티", "kt": "케이티",
        "엘지유플러스": "LG유플러스", "lg유플러스": "LG유플러스",
        "엔씨소프트": "NC", "ncsoft": "NC",
        "현대차": "현대자동차",
        "엘에스일렉트릭": "엘에스일렉트릭", "ls일렉트릭": "엘에스일렉트릭",
        "lselectric": "엘에스일렉트릭",
        "hd현대일렉트릭": "HD현대일렉트릭", "현대일렉트릭": "HD현대일렉트릭",
        "hd현대중공업": "HD현대중공업", "현대중공업": "HD현대중공업",
        "포스코홀딩스": "POSCO홀딩스", "posco": "POSCO홀딩스",
        "lig넥스원": "LIG디펜스앤에어로스페이스",
        "한국항공우주산업": "한국항공우주",
        "한국전력기술": "한전기술",
        "제이와이피엔터테인먼트": "JYP Ent", "jypentertainment": "JYP Ent",
        "에스엠엔터테인먼트": "에스엠",
    }
    for key, corp in extra.items():
        if corp in universe:
            index.setdefault(key, corp)
    return index, universe


def candidate_names(raw: str) -> list[str]:
    """계약상대 셀 하나에서 비교할 이름 후보들을 뽑는다.

    '(주)한국전력기술, 금호건설(주)' 처럼 복수 기업이 한 칸에 들어가고,
    'SK하이닉스(SK Hynix Inc.)' 처럼 괄호 안에 영문명이 병기되기도 한다.
    """
    out: list[str] = [raw]
    # 괄호 안팎을 각각 후보로
    out += re.findall(r"\(([^)]{2,60})\)", raw)
    out.append(re.sub(r"\([^)]*\)", " ", raw))
    parts: list[str] = []
    for chunk in out:
        parts += [p for p in _SPLIT.split(chunk) if p and p.strip()]
    seen, uniq = set(), []
    for p in parts + out:
        key = p.strip()
        if key and key not in seen:
            seen.add(key)
            uniq.append(key)
    return uniq


# universe 이름을 부분문자열로 품고 있지만 다른 조직인 경우 (한국항공우주 vs 한국항공우주연구원)
_NOT_AFFILIATE = re.compile(r"연구원|연구소|재단|협회|조합|대학|병원|공단")


def classify_counterparty(raw: str, index: dict[str, str]) -> tuple[str, list[str]]:
    """(분류, 매칭된 universe corp_name 목록).

    분류: universe_exact      정규화 후 이름이 그대로 일치
          universe_affiliate  universe 이름을 품은 자회사·계열사 (LS ELECTRIC AMERICA 등)
          anonymized          '오세아니아 지역 선주' 같은 비실명 표기
          public_sector       정부·공공기관
          foreign_entity      해외 법인
          domestic_other      국내 비-universe 법인

    한 셀에 여러 기업이 들어가는 경우가 있어 목록으로 돌려준다.
    """
    exact: list[str] = []
    for cand in candidate_names(raw):
        key = norm_name(cand)
        if key in index and index[key] not in exact:
            exact.append(index[key])
    if exact:
        return "universe_exact", exact

    # 자회사·계열사, universe 키가 이름 안에 부분문자열로 들어 있는 경우.
    # 한글 키는 3자 이상, 라틴 키는 6자 이상이어야 우연 일치를 피한다.
    key_full = norm_name(raw)
    affiliates: list[str] = []
    if not _NOT_AFFILIATE.search(raw):
        for key, corp in index.items():
            floor = 6 if _LATIN.search(key) else 3
            if len(key) >= floor and key in key_full and key != key_full:
                if corp not in affiliates:
                    affiliates.append(corp)
    if affiliates:
        return "universe_affiliate", affiliates

    if _ANON.search(raw):
        return "anonymized", []
    if _PUBLIC.search(raw):
        return "public_sector", []
    if _LATIN.search(raw):
        return "foreign_entity", []
    return "domestic_other", []


# ---------------------------------------------------------------- 관련공시 파싱

_REL_ENTRY = re.compile(r"(\d{4}[-.]\d{2}[-.]\d{2})\s*([^\d]{0,40})")

# DART XML 은 값 셀에 <TU>/<TE> 를 쓴다. html_doc.extract_rows 는 td/th 만 보므로
# 주요사항보고서에서는 값 칸의 45% 를 놓친다. 여기서만 넓혀 쓴다(원본 모듈은 손대지 않는다).
_TR_ANY = re.compile(r"(?is)<tr[^>]*>(.*?)</tr>")
_CELL_ANY = re.compile(r"(?is)<(t[dhue])[^>]*>(.*?)</\1>")
_TAG = re.compile(r"(?s)<[^>]*>")
_WS = re.compile(r"\s+")


def rows_with_units(html: str) -> list[list[str]]:
    """td/th 에 더해 DART XML 의 TU/TE 셀까지 포함한 행 추출."""
    import html as html_lib

    rows: list[list[str]] = []
    for tr in _TR_ANY.findall(html):
        cells = [
            _WS.sub(" ", html_lib.unescape(_TAG.sub(" ", c))).strip()
            for _, c in _CELL_ANY.findall(tr)
        ]
        cells = [c for c in cells if c]
        if cells:
            rows.append(cells)
    return rows


def parse_related_disclosures(rows: list[list[str]]) -> list[dict]:
    """'※ 관련공시' 행에서 (날짜, 서식명) 목록을 뽑는다."""
    out: list[dict] = []
    for row in rows:
        if "관련공시" not in " ".join(row):
            continue
        cell = row[-1]
        if cell.strip() in {"-", "", "–"}:
            return []
        for m in _REL_ENTRY.finditer(cell):
            form = m.group(2).strip(" ,·ㆍ-")
            out.append({"date": m.group(1).replace(".", "-"), "form": form[:40]})
    return out


# ---------------------------------------------------------------- main


def main() -> None:
    t0 = time.time()
    out_dir = settings().artifacts_root / "eda"
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = list(load_manifest())
    index, universe = build_universe_index()

    records = [
        json.loads(line)
        for line in (settings().artifacts_root / "exchange_records.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
        if line.strip()
    ]
    by_rcept = {r["rcept_no"]: r for r in records}

    # ================================================================
    # ① 기업-기업 엣지 밀도, GraphRAG 채택 판정
    # ================================================================
    contract_subtypes = {"단일판매공급계약체결", "단일판매공급계약해지"}
    contracts = [r for r in records if r["doc_subtype"] in contract_subtypes]
    with_cp = [r for r in contracts if r.get("counterparty")]

    edge_class = Counter()
    edges: list[dict] = []
    unmatched_samples: Counter = Counter()
    n_records_with_universe_edge = 0
    for rec in with_cp:
        kind, targets = classify_counterparty(rec["counterparty"], index)
        # 자사 해외법인('LS ELECTRIC AMERICA Inc.')은 기업-기업 엣지가 아니다
        outward = [t for t in targets if t != rec["corp_name"]]
        if targets and not outward:
            kind = "own_subsidiary"
        edge_class[kind] += 1
        if outward:
            n_records_with_universe_edge += 1
        for target in outward:
            edges.append(
                {
                    "src": rec["corp_name"],
                    "dst": target,
                    "kind": kind,
                    "rcept_no": rec["rcept_no"],
                    "amount": rec.get("amount"),
                    "src_sector": rec.get("sector"),
                    "dst_sector": universe[target]["sector"],
                }
            )
        if not outward:
            unmatched_samples[f"{kind}::{rec['counterparty'][:50]}"] += 1

    self_edges = [e for e in edges if e["src"] == e["dst"]]
    real_edges = [e for e in edges if e["src"] != e["dst"]]
    exact_edges = [e for e in real_edges if e["kind"] == "universe_exact"]
    n_records_exact = len(
        {e["rcept_no"] for e in exact_edges}
    )

    n_contracts = len(contracts)
    density = {
        "n_contract_records": n_contracts,
        "n_with_counterparty": len(with_cp),
        "counterparty_fill_rate": round(len(with_cp) / max(1, n_contracts), 4),
        "class_breakdown": dict(edge_class.most_common()),
        "class_breakdown_pct": {
            k: round(v * 100 / max(1, len(with_cp)), 2) for k, v in edge_class.most_common()
        },
        # 핵심 지표: 01-eda.md 정의 = universe 계약상대 건수 / 전체 계약 건수.
        # 분자는 '엣지 수' 가 아니라 '레코드 수' 다 (한 셀에 복수 기업이 들어갈 수 있음).
        "edge_density_strict_over_all_contracts": round(
            n_records_exact / max(1, n_contracts), 4
        ),
        "edge_density_strict_over_filled": round(n_records_exact / max(1, len(with_cp)), 4),
        "edge_density_incl_affiliate_over_all_contracts": round(
            n_records_with_universe_edge / max(1, n_contracts), 4
        ),
        "n_records_with_universe_counterparty_exact": n_records_exact,
        "n_records_with_universe_counterparty_incl_affiliate": n_records_with_universe_edge,
        "n_edges_exact": len(exact_edges),
        "n_edges_incl_affiliate": len(real_edges),
        "n_self_reference_dropped": len(self_edges),
        "n_distinct_ordered_pairs": len({(e["src"], e["dst"]) for e in real_edges}),
        "n_distinct_undirected_pairs": len(
            {tuple(sorted((e["src"], e["dst"]))) for e in real_edges}
        ),
        "n_corps_touched_by_any_edge": len(
            {e["src"] for e in real_edges} | {e["dst"] for e in real_edges}
        ),
        "universe_size": len(universe),
        "graph_completeness_pct": round(
            len({tuple(sorted((e["src"], e["dst"]))) for e in real_edges})
            * 100
            / (len(universe) * (len(universe) - 1) / 2),
            3,
        ),
        "top_pairs": Counter(
            f"{e['src']} -> {e['dst']}" for e in real_edges
        ).most_common(25),
        "top_unmatched_counterparties": unmatched_samples.most_common(40),
        "n_distinct_counterparty_strings": len({r["counterparty"] for r in with_cp}),
    }

    # 섹터 응집도
    sector_pairs = Counter(f"{e['src_sector']} -> {e['dst_sector']}" for e in real_edges)
    density["sector_cohesion"] = {
        "same_sector_edges": sum(1 for e in real_edges if e["src_sector"] == e["dst_sector"]),
        "cross_sector_edges": sum(1 for e in real_edges if e["src_sector"] != e["dst_sector"]),
        "top_sector_pairs": sector_pairs.most_common(15),
    }

    # ================================================================
    # ①-b 계약 외 엣지원, 지분공시 보고자 · 주요사항보고 거래상대방
    # ================================================================
    # (1) 지분공시: manifest 의 flr_nm 이 곧 '5% 이상 보유 보고자' 다. 파싱 비용 0.
    holding = filter_docs(doc_group="holding")
    hold_class = Counter()
    hold_edges = Counter()
    for doc in holding:
        kind, targets = classify_counterparty(doc["flr_nm"], index)
        outward = [t for t in targets if t != doc["corp_name"]]
        hold_class[kind if outward or not targets else "own_name"] += 1
        for t in outward:
            hold_edges[(t, doc["corp_name"])] += 1  # 보유자 -> 피보유 기업

    # (2) 주요사항보고: 합병·분할·주식교환·타법인주식·영업양수 서식의 상대방
    _MA_FORM = re.compile(r"타법인주식|합병|분할|영업양수|영업양도|주식교환")
    _MA_LABEL = re.compile(r"거래상대방|합병상대회사|교환ㆍ이전 대상법인|분할설립회사|발행회사")
    ma_edges = Counter()
    ma_docs = [d for d in filter_docs(doc_group="major") if _MA_FORM.search(d["report_nm"])]
    ma_samples: list[dict] = []
    for doc in ma_docs:
        files = sorted((settings().corpus_root / doc["file_path"]).glob("*.xml"))
        if not files:
            continue
        for row in rows_with_units(read_document(files[0])):
            if len(row) < 2 or not _MA_LABEL.search(row[0]):
                continue
            kind, targets = classify_counterparty(row[-1], index)
            for t in targets:
                if t != doc["corp_name"]:
                    ma_edges[(doc["corp_name"], t)] += 1
            if len(ma_samples) < 25:
                ma_samples.append(
                    {"corp": doc["corp_name"], "label": row[0][:30],
                     "value": row[-1][:60], "matched": targets}
                )

    other_sources = {
        "holding_shareholder_edges": {
            "note": "manifest.flr_nm(5% 대량보유 보고자) 기준 — 원문 파싱 없이 얻어진다",
            "n_docs": len(holding),
            "filer_class_breakdown": dict(hold_class.most_common()),
            "n_records_with_universe_edge": sum(hold_edges.values()),
            "n_distinct_pairs": len(hold_edges),
            "pairs": [f"{a} -(지분보유)-> {b} x{n}" for (a, b), n in hold_edges.most_common()],
        },
        "major_transaction_edges": {
            "note": "합병·분할·주식교환·타법인주식·영업양수 서식의 상대방 라벨",
            "n_docs_scanned": len(ma_docs),
            "n_distinct_pairs": len(ma_edges),
            "pairs": [f"{a} -> {b} x{n}" for (a, b), n in ma_edges.most_common()],
            "samples": ma_samples,
        },
    }

    # ================================================================
    # ② 정정 체인
    # ================================================================
    correction_profile: dict[str, dict] = {}
    for group in ("exchange", "major", "holding"):
        docs = filter_docs(doc_group=group)
        chains = build_chains(group)
        n_corr = sum(1 for d in docs if d["is_correction"])
        linked = sum(len(v) for v in chains.values())
        lengths = Counter(len(v) + 1 for v in chains.values())
        longest = max(chains.items(), key=lambda kv: len(kv[1]), default=(None, []))
        # 코퍼스 밖 참조: 정정인데 어느 체인에도 못 붙은 건
        in_chain = {c for v in chains.values() for c in v}
        orphan = [d for d in docs if d["is_correction"] and d["rcept_no"] not in in_chain]
        correction_profile[group] = {
            "n_docs": len(docs),
            "n_corrections": n_corr,
            "n_chains": len(chains),
            "n_corrections_linked": linked,
            "link_rate": round(linked / max(1, n_corr), 4),
            "orphan_rate_out_of_corpus": round(len(orphan) / max(1, n_corr), 4),
            "chain_length_distribution": {str(k): v for k, v in sorted(lengths.items())},
            "max_chain_length": (max(lengths) if lengths else 0),
            "longest_chain_root": longest[0],
            "longest_chain_members": [longest[0], *longest[1]] if longest[0] else [],
            "mean_chain_length": round(
                sum(k * v for k, v in lengths.items()) / max(1, sum(lengths.values())), 3
            ),
        }

    # corrections.build_chains 가 major·holding 에서 왜 실패하는지 계량한다.
    # 거래소 서식은 '2. 정정관련 공시서류제출일' 이지만 주요사항·지분 서식은
    # '2. 정정대상 공시서류의 최초제출일 :' 이고 값이 <TU> 셀에 들어 있다.
    for group in ("major", "holding"):
        docs = [d for d in filter_docs(doc_group=group) if d["is_correction"]]
        outcome = Counter()
        labels = Counter()
        for doc in docs:
            files = sorted((settings().corpus_root / doc["file_path"]).glob("*.xml"))
            if not files:
                continue
            clue = None
            for row in rows_with_units(read_document(files[0])):
                joined = " ".join(row)
                if "제출일" in joined and ("정정" in joined or "최초" in joined):
                    labels[row[0][:40]] += 1
                    if clue is None and len(row) >= 2:
                        parsed = parse_date(row[-1])
                        if parsed:
                            clue = parsed.strftime("%Y%m%d")
            if clue is None:
                outcome["no_submit_date_row"] += 1
            elif clue < "20230101":
                outcome["target_before_corpus_start"] += 1
            elif clue > doc["rcept_dt"]:
                outcome["target_in_future"] += 1
            else:
                outcome["RECOVERABLE_target_in_corpus_range"] += 1
        correction_profile[group]["recovery_estimate"] = {
            "note": "'정정대상 공시서류의 최초제출일' 라벨 + TU 셀까지 읽었을 때",
            "n_corrections": len(docs),
            "outcome": dict(outcome.most_common()),
            "recoverable_link_rate": round(
                outcome["RECOVERABLE_target_in_corpus_range"] / max(1, len(docs)), 4
            ),
            "observed_submit_date_labels": labels.most_common(6),
        }

    # periodic 은 5.3GB 라 원문을 안 읽고 manifest 키로만 잇는다
    periodic = filter_docs(doc_group="periodic")
    pkey: dict[tuple, list[dict]] = defaultdict(list)
    for d in periodic:
        pkey[(d["corp_name"], d["doc_subtype"], d["base_year"], d["base_month"])].append(d)
    plen = Counter(len(v) for v in pkey.values())
    correction_profile["periodic"] = {
        "note": "원문 미파싱 — (corp, subtype, base_year, base_month) 키로 묶은 결과",
        "n_docs": len(periodic),
        "n_corrections": sum(1 for d in periodic if d["is_correction"]),
        "n_report_slots": len(pkey),
        "chain_length_distribution": {str(k): v for k, v in sorted(plen.items())},
        "max_chain_length": max(plen) if plen else 0,
        "n_slots_with_correction": sum(1 for v in pkey.values() if len(v) > 1),
    }

    # ================================================================
    # ③ '※ 관련공시' 문서 간 참조 엣지
    # ================================================================
    ref_stats = {"scanned": 0, "docs_with_row": 0, "docs_with_entries": 0, "entries": 0}
    ref_forms = Counter()
    resolved = Counter()
    ref_edges_sample: list[dict] = []
    by_corp_date: dict[tuple, list[dict]] = defaultdict(list)
    for d in manifest:
        by_corp_date[(d["corp_name"], d["rcept_dt"])].append(d)

    for group in ("exchange", "major"):
        for doc in filter_docs(doc_group=group):
            folder = settings().corpus_root / doc["file_path"]
            files = sorted(folder.glob("*.xml"))
            if not files:
                continue
            ref_stats["scanned"] += 1
            rows = extract_rows(read_document(files[0]))
            if any("관련공시" in " ".join(r) for r in rows):
                ref_stats["docs_with_row"] += 1
            entries = parse_related_disclosures(rows)
            if entries:
                ref_stats["docs_with_entries"] += 1
            for e in entries:
                ref_stats["entries"] += 1
                ref_forms[e["form"]] += 1
                target_dt = e["date"].replace("-", "")
                hits = [
                    t for t in by_corp_date.get((doc["corp_name"], target_dt), [])
                    if t["rcept_no"] != doc["rcept_no"]
                ]
                if hits:
                    resolved["in_corpus"] += 1
                    if len(ref_edges_sample) < 40:
                        ref_edges_sample.append(
                            {
                                "src": doc["doc_id"],
                                "dst": [h["doc_id"] for h in hits][:3],
                                "form": e["form"],
                                "date": e["date"],
                            }
                        )
                elif target_dt < "20230101":
                    resolved["before_corpus_start"] += 1
                else:
                    resolved["in_range_but_missing"] += 1

    ref_profile = {
        **ref_stats,
        "docs_with_row_pct": round(
            ref_stats["docs_with_row"] * 100 / max(1, ref_stats["scanned"]), 2
        ),
        "entries_per_doc_with_entries": round(
            ref_stats["entries"] / max(1, ref_stats["docs_with_entries"]), 2
        ),
        "resolution": dict(resolved),
        "resolution_pct": {
            k: round(v * 100 / max(1, ref_stats["entries"]), 2) for k, v in resolved.items()
        },
        "top_referenced_forms": ref_forms.most_common(25),
        "sample_edges": ref_edges_sample,
        "note": "periodic·holding 은 이 서식 행이 없어 제외",
    }

    # ================================================================
    # ④ 기업×분기 공시 밀도
    # ================================================================
    def quarter(dt: str) -> str:
        return f"{dt[:4]}Q{(int(dt[4:6]) - 1) // 3 + 1}"

    cell = Counter()
    per_group_cell: dict[str, Counter] = defaultdict(Counter)
    for d in manifest:
        cell[(d["corp_name"], quarter(d["rcept_dt"]))] += 1
        per_group_cell[d["doc_group"]][(d["corp_name"], quarter(d["rcept_dt"]))] += 1

    quarters = sorted({q for _, q in cell})
    n_cells_possible = len(universe) * len(quarters)
    counts = sorted(cell.values())
    zero_cells = n_cells_possible - len(cell)
    per_corp = Counter()
    for (corp, _), v in cell.items():
        per_corp[corp] += v

    periodic_gap = Counter()
    by_corp_periodic = defaultdict(list)
    for d in periodic:
        by_corp_periodic[d["corp_name"]].append(d["rcept_dt"])
    for corp, dates in by_corp_periodic.items():
        ds = sorted(dates)
        for a, b in zip(ds, ds[1:]):
            from datetime import date

            gap = (date(int(b[:4]), int(b[4:6]), int(b[6:]))
                   - date(int(a[:4]), int(a[4:6]), int(a[6:]))).days
            periodic_gap[min(400, (gap // 30) * 30)] += 1

    density_profile = {
        "n_quarters": len(quarters),
        "quarters": quarters,
        "n_corp_quarter_cells_possible": n_cells_possible,
        "n_corp_quarter_cells_nonempty": len(cell),
        "empty_cell_pct": round(zero_cells * 100 / max(1, n_cells_possible), 2),
        "docs_per_corp_quarter": {
            "mean": round(sum(counts) / max(1, len(counts)), 2),
            "p50": counts[len(counts) // 2] if counts else 0,
            "p90": counts[int(len(counts) * 0.9)] if counts else 0,
            "max": counts[-1] if counts else 0,
        },
        "docs_per_corp": {
            "mean": round(sum(per_corp.values()) / max(1, len(per_corp)), 1),
            "max": per_corp.most_common(1),
            "min": per_corp.most_common()[-5:],
        },
        "nonempty_cells_by_group": {
            g: len(c) for g, c in sorted(per_group_cell.items())
        },
        "periodic_filing_gap_days_histogram": {
            str(k): v for k, v in sorted(periodic_gap.items())
        },
        "top_corps_by_doc_count": per_corp.most_common(10),
        "bottom_corps_by_doc_count": per_corp.most_common()[-10:],
    }

    payload = {
        "meta": {
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "elapsed_sec": round(time.time() - t0, 1),
            "llm_calls": 0,
            "universe_size": len(universe),
            "n_docs_in_manifest": len(manifest),
        },
        "corp_to_corp_edge_density": density,
        "other_edge_sources": other_sources,
        "correction_chains": correction_profile,
        "related_disclosure_references": ref_profile,
        "filing_density": density_profile,
    }
    out = out_dir / "relation_profile.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {out}  ({out.stat().st_size / 1024:.0f} KB, {time.time() - t0:.1f}s)")
    print(
        "edge density (strict) =",
        density["edge_density_strict_over_all_contracts"],
        "| incl subsidiary =",
        density["edge_density_incl_affiliate_over_all_contracts"],
    )


if __name__ == "__main__":
    main()
