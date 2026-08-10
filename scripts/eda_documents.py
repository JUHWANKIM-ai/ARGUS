"""Phase A-1 — 문서 구조 프로파일 (docs/pipeline/01-eda.md).

LLM 호출 0회. 파싱은 src/argus/corpus/* 를 그대로 재사용한다.

측정 항목
  · 유형×서식별 문서 길이 분포        -> chunk 크기 결정 근거
  · 표 비율 (표 개수 · 표 문자/전체)   -> 표 분리 여부 (policy §8)
  · 숫자 비율 (숫자 토큰/전체 토큰)    -> BM25 vs 임베딩 적합성
  · 시간 정보 밀도·출현 위치           -> 기간 필터 설계 근거
  · 표 라벨 어휘 사전 (서식별)         -> 규칙 파싱 확장 여지 판정
  · periodic 목차 트리 (표본 5+)       -> 섹션 청킹 선행조건
  · schema_matched=False 라벨 갭 추정  -> 규칙으로 건질 분량

사용
    PYTHONIOENCODING=utf-8 PYTHONUTF8=1 python scripts/eda_documents.py
    옵션 --periodic-sample N (기본 40) --toc-sample N (기본 8)
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from argus.config import settings  # noqa: E402
from argus.corpus.html_doc import extract_rows, read_document, strip_markup  # noqa: E402
from argus.corpus.manifest import filter_docs, load_manifest  # noqa: E402

# ---------------------------------------------------------------- 정규식

# DART XML 은 <TU>(단위셀) <TE>(추출셀) 를 쓴다. html_doc._TD 는 td/th 만 잡으므로
# 지분·정기공시에서는 셀 대부분을 놓친다. 프로파일링용으로만 넓혀 쓴다.
_TR_ANY = re.compile(r"(?is)<tr[^>]*>(.*?)</tr>")
_CELL_ANY = re.compile(r"(?is)<(t[dhue])[^>]*>(.*?)</\1>")
_TABLE_BLOCK = re.compile(r"(?is)<table[^>]*>.*?</table>")
_TABLE_OPEN = re.compile(r"(?i)<table[^>]*>")
_TAG = re.compile(r"(?s)<[^>]*>")
_WS = re.compile(r"\s+")
_WS_ALL = re.compile(r"\s+")

# 숫자 토큰: 1,234 / 3.14 / (1,234) / 12% / △301,146 / 2023년
_NUM_TOKEN = re.compile(r"^[\(\[△▲▽▼+\-]?[\d][\d,\.]*\)?[%원건주명개년월일]?$")
_DATE_PAT = re.compile(
    r"\d{4}[-./]\d{1,2}[-./]\d{1,2}"
    r"|\d{4}\s*년\s*\d{1,2}\s*월\s*\d{1,2}\s*일"
    r"|\b(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\b"
)
_YM_PAT = re.compile(r"\d{4}\s*[-./년]\s*\d{1,2}\s*[월]?")

_SECTION_TAG = re.compile(r"(?is)<(/?)SECTION-(\d)[^>]*>")
_TITLE_TAG = re.compile(r"(?is)<(?:COVER-)?TITLE[^>]*>(.*?)</(?:COVER-)?TITLE>")

PCTS = (5, 10, 25, 50, 75, 90, 95, 99)


def _clean_cell(fragment: str) -> str:
    import html as html_lib

    return _WS.sub(" ", html_lib.unescape(_TAG.sub(" ", fragment))).strip()


def _rows_any(html: str) -> list[list[str]]:
    """td/th 뿐 아니라 DART XML 의 TU/TE 셀까지 포함한 행 추출."""
    rows: list[list[str]] = []
    for tr in _TR_ANY.findall(html):
        cells = [c for _, c in _CELL_ANY.findall(tr)]
        cells = [x for x in (_clean_cell(c) for c in cells) if x]
        if cells:
            rows.append(cells)
    return rows


def _pct(values: list[float]) -> dict:
    if not values:
        return {}
    ordered = sorted(values)
    out = {f"p{p}": ordered[min(len(ordered) - 1, int(len(ordered) * p / 100))] for p in PCTS}
    out["min"] = ordered[0]
    out["max"] = ordered[-1]
    out["mean"] = round(mean(ordered), 1)
    return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in out.items()}


# ---------------------------------------------------------------- 문서 1건 측정


def profile_document(html: str) -> dict:
    text = strip_markup(html)
    n_chars = len(text)
    if n_chars == 0:
        return {}

    table_blocks = _TABLE_BLOCK.findall(html)
    table_chars = sum(len(strip_markup(b)) for b in table_blocks)
    n_tables = len(_TABLE_OPEN.findall(html))

    tokens = text.split()
    n_num_tokens = sum(1 for t in tokens if _NUM_TOKEN.match(t))
    n_digits = sum(1 for ch in text if ch.isdigit())

    dates = list(_DATE_PAT.finditer(text))
    positions = [m.start() / n_chars for m in dates]
    decile = Counter(min(9, int(p * 10)) for p in positions)

    return {
        "n_chars": n_chars,
        "n_tables": n_tables,
        "table_chars": table_chars,
        "table_char_ratio": table_chars / n_chars,
        "n_tokens": len(tokens),
        "num_token_ratio": n_num_tokens / max(1, len(tokens)),
        "digit_char_ratio": n_digits / n_chars,
        "n_dates": len(dates),
        "date_per_1k_chars": len(dates) * 1000 / n_chars,
        "n_ym": len(_YM_PAT.findall(text)),
        "date_decile": {str(k): decile.get(k, 0) for k in range(10)},
    }


def _doc_files(doc: dict) -> list[Path]:
    folder = settings().corpus_root / doc["file_path"]
    return sorted(folder.glob("*.xml"))


def _form_key(doc: dict) -> str:
    """서식 키. major 는 manifest 의 doc_subtype 이 비어 있어 report_nm 을 쓴다."""
    sub = doc.get("doc_subtype")
    if sub:
        return str(sub)
    name = re.sub(r"^\[[^\]]*\]\s*", "", doc.get("report_nm", "") or "")
    return re.sub(r"\s*\(.*$", "", name).strip() or "(미상)"


# ---------------------------------------------------------------- 라벨 어휘


def _label_cells(rows: list[list[str]]) -> list[str]:
    """값 셀(마지막)을 뺀 라벨 후보. normalize._labels 와 같은 원리."""
    out = []
    for row in rows:
        if len(row) < 2:
            continue
        for cell in row[:-1]:
            label = _WS_ALL.sub(" ", cell).strip()
            # 순번 접두('1. ', '- ')를 떼서 서식 간 비교가 되게 한다
            label = re.sub(r"^[\-※]?\s*\d{0,2}[\.\)]?\s*", "", label).strip()
            if 1 <= len(label) <= 40 and not _NUM_TOKEN.match(label):
                out.append(label)
    return out


# ---------------------------------------------------------------- 목차 트리


def parse_toc(html: str) -> dict:
    """SECTION-n 중첩 + TITLE 로 정기공시 목차 트리를 만든다."""
    events: list[tuple[int, int, str]] = []  # (pos, depth_delta, tag)
    for m in _SECTION_TAG.finditer(html):
        closing, depth = m.group(1), int(m.group(2))
        events.append((m.start(), depth, "close" if closing else "open"))
    titles = [(m.start(), _clean_cell(m.group(1))) for m in _TITLE_TAG.finditer(html)]

    nodes: list[dict] = []
    stack: list[dict] = []
    ti = 0
    for pos, depth, kind in events:
        if kind == "open":
            # 섹션 시작 직후의 TITLE 을 제목으로 삼는다
            while ti < len(titles) and titles[ti][0] < pos:
                ti += 1
            title = ""
            if ti < len(titles) and titles[ti][0] - pos < 4000:
                title = titles[ti][1]
                ti += 1  # 한 제목이 두 노드에 중복 배정되지 않게 소비한다
            node = {"depth": depth, "title": title[:120], "start": pos, "children": 0}
            if stack:
                stack[-1]["children"] += 1
            nodes.append(node)
            stack.append(node)
        else:
            if stack:
                node = stack.pop()
                node["n_chars"] = len(strip_markup(html[node["start"]:pos]))
    for n in nodes:
        n.setdefault("n_chars", 0)
        n.pop("start", None)

    by_depth = Counter(n["depth"] for n in nodes)
    stubs = sum(1 for n in nodes if n["n_chars"] < 100)
    return {
        "n_nodes": len(nodes),
        "max_depth": max(by_depth) if by_depth else 0,
        "nodes_by_depth": {str(k): v for k, v in sorted(by_depth.items())},
        "n_stub_under_100_chars": stubs,
        "titled_ratio": round(sum(1 for n in nodes if n["title"]) / max(1, len(nodes)), 3),
        "sample_paths": [
            f"{'  ' * (n['depth'] - 1)}{n['title']} ({n['n_chars']}자)" for n in nodes[:25]
        ],
    }


# ---------------------------------------------------------------- 라벨 갭 (Q②)

# 투자판단·신규시설투자의 자유서술 안에서 금액을 건지는 후보 규칙
_AMOUNT_IN_TEXT = re.compile(
    r"(투자\s*금액|계약\s*금액|취득\s*금액|총\s*투자|투자\s*규모|계약\s*규모|"
    r"공급\s*금액|거래\s*금액|양수\s*금액|출자\s*금액|발행\s*금액|투자액)"
    r"\s*[:：]?\s*(?:약\s*)?"
    r"([\d,]+(?:\.\d+)?)\s*(조원|조|억원|억|백만원|천만원|만원|원|USD|달러|불)"
)
_PERIOD_IN_TEXT = re.compile(
    r"(투자\s*기간|계약\s*기간|사업\s*기간|공급\s*기간)\s*[:：]?\s*"
    r"(\d{4})\s*[년.\-/]\s*(\d{1,2})"
)
_CP_IN_TEXT = re.compile(
    r"(계약\s*상대|계약\s*당사자|거래\s*상대|상대방|발주처|매수인|매도인|양수인|양도인|"
    r"콜옵션\s*권리자|투자\s*대상|피투자|출자\s*대상)\s*[:：]?\s*([^\n·]{2,60})"
)
# 라벨 없이 금액 표현만 있는지 — '규칙이 놓친 것' 과 '애초에 금액이 없는 것' 을 가른다
_ANY_MONEY = re.compile(r"[\d,]+(?:\.\d+)?\s*(?:조원|조|억원|억|백만원|천만원|만원|원|USD|달러)")
# 투자판단 300건이 무엇에 관한 공시인지 — 제목 행 주제 분류
_TOPIC_RULES = (
    ("공급·수주계약", r"계약|수주|공급|납품|MOU|양해각서"),
    ("시설·설비투자", r"투자|증설|공장|건설|Fab|라인|착공|설비"),
    ("지분·출자·M&A", r"지분|출자|인수|합병|주식|콜옵션|매각|양수|양도|SPA"),
    ("임상·품목허가", r"임상|허가|승인|FDA|IND|NDA|品目|품목|시판"),
    ("기술이전·라이선스", r"기술이전|라이선스|License|기술수출|기술도입"),
    ("자금조달·차입·보증", r"차입|보증|사채|증자|자금조달|대여"),
    ("소송·제재·사고", r"소송|제재|과징금|화재|사고|중단|정지"),
    ("실적·전망", r"실적|매출|영업이익|잠정|전망|가이던스"),
)


def label_gap_report(docs: list[dict], records: dict[str, dict]) -> dict:
    """schema_matched=False 문서의 라벨 분포와 규칙 확장 회수 추정."""
    per_subtype: dict[str, dict] = {}
    for doc in docs:
        rec = records.get(doc["rcept_no"])
        if rec is None or rec.get("schema_matched"):
            continue
        sub = _form_key(doc)
        bucket = per_subtype.setdefault(
            sub,
            {
                "n": 0,
                "labels": Counter(),
                "label_rows_with_value": Counter(),
                "recoverable": Counter(),
                "examples": [],
                "topics": Counter(),
                "titles": [],
            },
        )
        bucket["n"] += 1

        files = _doc_files(doc)
        if not files:
            continue
        html = read_document(files[0])
        rows = extract_rows(html)
        text = strip_markup(html)

        for row in rows:
            if len(row) < 2:
                bucket["labels"][_norm_label(row[0])] += 1
                continue
            for cell in row[:-1]:
                bucket["labels"][_norm_label(cell)] += 1
            bucket["label_rows_with_value"][_norm_label(" ".join(row[:-1]))] += 1

        joined_labels = {_norm_label(c) for row in rows for c in row[:-1]}

        # (a) 라벨 기반 — 표 라벨이 이미 존재하는데 normalize 가 안 보는 경우
        if any("투자금액" in x for x in joined_labels):
            bucket["recoverable"]["amount_by_label_투자금액"] += 1
        if any("자기자본대비" in x for x in joined_labels):
            bucket["recoverable"]["ratio_by_label_자기자본대비"] += 1
        if any("투자대상" in x for x in joined_labels):
            bucket["recoverable"]["subject_by_label_투자대상"] += 1
        if any("투자구분" in x for x in joined_labels):
            bucket["recoverable"]["kind_by_label_투자구분"] += 1

        # (b) 자유서술 기반 — 값이 문장 안에 있는 경우
        am = _AMOUNT_IN_TEXT.search(text)
        if am:
            bucket["recoverable"]["amount_by_freetext"] += 1
            if len(bucket["examples"]) < 6:
                bucket["examples"].append(am.group(0)[:80])
        if _PERIOD_IN_TEXT.search(text):
            bucket["recoverable"]["period_by_freetext"] += 1
        if _CP_IN_TEXT.search(text):
            bucket["recoverable"]["counterparty_by_freetext"] += 1

        # 금액이 '규칙에 안 걸린 것' 인지 '애초에 없는 것' 인지 가른다
        if am is None and not any("투자금액" in x for x in joined_labels):
            if _ANY_MONEY.search(text):
                bucket["recoverable"]["amount_MISSED_money_present"] += 1
            else:
                bucket["recoverable"]["amount_NO_MONEY_IN_DOC"] += 1

        # 공시유보로 금액이 원천 비공개인 건 규칙으로도 LLM 으로도 못 건진다
        if rec.get("reserved_reason"):
            bucket["recoverable"]["amount_RESERVED_by_issuer"] += 1

        # 이 서식이 무엇에 관한 공시인지
        title_row = next((r for r in rows if "제목" in _norm_label(r[0]) and len(r) >= 2), None)
        subject = title_row[-1] if title_row else rec.get("title", "")
        for name, pattern in _TOPIC_RULES:
            if re.search(pattern, subject, re.I):
                bucket["topics"][name] += 1
                break
        else:
            bucket["topics"]["기타·미분류"] += 1
        if len(bucket["titles"]) < 25:
            bucket["titles"].append(subject[:70])

    return {
        sub: {
            "n": b["n"],
            "top_labels": b["labels"].most_common(30),
            "top_label_rows": b["label_rows_with_value"].most_common(20),
            "rule_extension_estimate": dict(b["recoverable"].most_common()),
            "freetext_amount_examples": b["examples"],
            "subject_topics": dict(b["topics"].most_common()),
            "sample_subjects": b["titles"],
        }
        for sub, b in per_subtype.items()
    }


def _norm_label(cell: str) -> str:
    label = re.sub(r"^[\-※]?\s*\d{0,2}[\.\)]?\s*", "", _WS_ALL.sub(" ", cell).strip())
    return label[:40]


# ---------------------------------------------------------------- main


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--periodic-sample", type=int, default=40)
    ap.add_argument("--toc-sample", type=int, default=8)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()
    rng = random.Random(args.seed)
    t0 = time.time()

    manifest = list(load_manifest())
    out_dir = settings().artifacts_root / "eda"
    out_dir.mkdir(parents=True, exist_ok=True)

    # ---- 코퍼스 물리 규모 (재검증) -------------------------------------
    corpus_bytes: dict[str, dict] = {}
    for group in ("exchange", "major", "holding", "periodic"):
        files = list((settings().corpus_root / "raw" / group).rglob("*.xml"))
        total = sum(f.stat().st_size for f in files)
        corpus_bytes[group] = {
            "n_xml_files": len(files),
            "bytes": total,
            "mb": round(total / 1e6, 1),
            "avg_kb": round(total / max(1, len(files)) / 1024, 1),
        }
    grand = sum(v["bytes"] for v in corpus_bytes.values())
    for v in corpus_bytes.values():
        v["share_pct"] = round(v["bytes"] * 100 / grand, 2)

    # ---- 표본 설계 -----------------------------------------------------
    by_group = defaultdict(list)
    for doc in manifest:
        by_group[doc["doc_group"]].append(doc)

    sampled: dict[str, list[dict]] = {
        "exchange": by_group["exchange"],          # 전량
        "major": by_group["major"],                # 전량
        "holding": by_group["holding"],            # 전량
    }
    # periodic 은 5.3GB 라 층화 표본만 본다 (전량 파싱은 다른 트랙 담당)
    periodic_by_sub = defaultdict(list)
    for doc in by_group["periodic"]:
        periodic_by_sub[doc["doc_subtype"]].append(doc)
    per_sub_n = max(1, args.periodic_sample // max(1, len(periodic_by_sub)))
    sampled["periodic"] = [
        d for sub, docs in periodic_by_sub.items()
        for d in rng.sample(docs, min(per_sub_n, len(docs)))
    ]

    # ---- 문서 프로파일 --------------------------------------------------
    per_form: dict[tuple[str, str], list[dict]] = defaultdict(list)
    label_vocab: dict[str, Counter] = defaultdict(Counter)
    cell_tag_stats: dict[str, Counter] = defaultdict(Counter)
    n_read = 0

    for group, docs in sampled.items():
        for doc in docs:
            files = _doc_files(doc)
            if not files:
                continue
            html = read_document(files[0])
            n_read += 1
            prof = profile_document(html)
            if not prof:
                continue
            prof["rcept_no"] = doc["rcept_no"]
            per_form[(group, _form_key(doc))].append(prof)

            rows_std = extract_rows(html)        # html_doc 기본 (td/th)
            rows_any = _rows_any(html)           # TU/TE 포함
            cell_tag_stats[group]["docs"] += 1
            cell_tag_stats[group]["cells_td_th"] += sum(len(r) for r in rows_std)
            cell_tag_stats[group]["cells_incl_tu_te"] += sum(len(r) for r in rows_any)
            for label in _label_cells(rows_any):
                label_vocab[f"{group}/{_form_key(doc)}"][label] += 1

    length_profile = {}
    for (group, form), items in sorted(per_form.items()):
        length_profile[f"{group}/{form}"] = {
            "n_docs_profiled": len(items),
            "n_chars": _pct([i["n_chars"] for i in items]),
            "n_tables": _pct([float(i["n_tables"]) for i in items]),
            "table_char_ratio": _pct([i["table_char_ratio"] for i in items]),
            "num_token_ratio": _pct([i["num_token_ratio"] for i in items]),
            "digit_char_ratio": _pct([i["digit_char_ratio"] for i in items]),
            "date_per_1k_chars": _pct([i["date_per_1k_chars"] for i in items]),
            "n_dates": _pct([float(i["n_dates"]) for i in items]),
            "date_position_decile": {
                str(k): sum(i["date_decile"][str(k)] for i in items) for k in range(10)
            },
        }

    # ---- periodic 목차 트리 (소표본) ------------------------------------
    toc_docs = rng.sample(by_group["periodic"], min(args.toc_sample, len(by_group["periodic"])))
    toc_results = []
    for doc in toc_docs:
        files = _doc_files(doc)
        if not files:
            continue
        tree = parse_toc(read_document(files[0]))
        tree.update(
            {
                "doc_id": doc["doc_id"],
                "corp_name": doc["corp_name"],
                "report_nm": doc["report_nm"],
                "n_xml_in_folder": len(files),
            }
        )
        toc_results.append(tree)

    # ---- 라벨 갭 (Q②) ---------------------------------------------------
    records_path = settings().artifacts_root / "exchange_records.jsonl"
    records: dict[str, dict] = {}
    if records_path.exists():
        with records_path.open(encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    rec = json.loads(line)
                    records[rec["rcept_no"]] = rec
    gap = label_gap_report(by_group["exchange"], records)

    # 서식별 필드 충전율 (현재 상태 재확인)
    fill = defaultdict(lambda: Counter())
    for rec in records.values():
        sub = rec["doc_subtype"]
        fill[sub]["n"] += 1
        for field in ("amount", "counterparty", "period_start", "period_end", "ratio",
                      "event_date", "reserved_reason"):
            if rec.get(field) not in (None, "", []):
                fill[sub][field] += 1
        if rec.get("schema_matched"):
            fill[sub]["schema_matched"] += 1

    payload = {
        "meta": {
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "elapsed_sec": round(time.time() - t0, 1),
            "n_docs_in_manifest": len(manifest),
            "n_docs_parsed": n_read,
            "sampling": {
                "exchange": "전량 1469",
                "major": "전량 598",
                "holding": "전량 1083",
                "periodic": f"층화표본 {len(sampled['periodic'])} / {len(by_group['periodic'])}"
                            " (5.3GB 라 전량 파싱은 다른 트랙 담당)",
            },
            "llm_calls": 0,
        },
        "corpus_bytes": corpus_bytes,
        "length_and_table_and_number_profile": length_profile,
        "cell_tag_coverage": {
            g: {
                **dict(c),
                "td_th_coverage_pct": round(
                    c["cells_td_th"] * 100 / max(1, c["cells_incl_tu_te"]), 1
                ),
            }
            for g, c in cell_tag_stats.items()
        },
        "label_vocabulary": {
            k: v.most_common(40) for k, v in sorted(label_vocab.items())
        },
        "periodic_toc_trees": toc_results,
        "field_fill_by_subtype": {k: dict(v) for k, v in fill.items()},
        "label_gap_schema_unmatched": gap,
    }

    out = out_dir / "doc_profile.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {out}  ({out.stat().st_size / 1024:.0f} KB, {time.time() - t0:.1f}s)")


if __name__ == "__main__":
    main()
