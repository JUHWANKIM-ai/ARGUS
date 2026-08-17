"""Fifteen tools: eight for retrieval and seven for calculation.

Seven of the retrieval tools follow the reference specification in
`CONVENTION §7.7`. The counterparty query is the eighth, exposing the
counterparty graph so that fan-in and fan-out questions can be enumerated
completely. It draws the boundary of a set rather than improving a ranking.

The calculation tools expose the deterministic functions of `compute.py` as
function-calling specifications, which is how the rule that calculation belongs
to a tool is enforced. A calculation function left unexposed is one the model
performs itself, which breaks that rule.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache

from argus.agent import compute
from argus.config import settings
from argus.corpus.counterparty import canonical, classify
from argus.corpus.manifest import filter_docs, resolve_company
from argus.corpus.sections import BLOB_NAME
from argus.retrieve.bm25 import (SECTION_CHUNK_CHARS, Bm25Index,
                                 SectionBm25Index, norm_path, split_section)

SECTION_INDEX_STEM = "section_index"
COUNTERPARTY_GRAPH_NAME = "counterparty_graph.json"


#: Structured record groups. Each group has its own index: merging them into
#: one lowers exchange golden recovery, so the router selects a group and only
#: that index is consulted.
RECORD_GROUPS = ("exchange", "major", "holding")

#: Reference edges built from the related-filings cell.
REFERENCES_NAME = "references.jsonl"


@lru_cache(maxsize=len(RECORD_GROUPS))
def _group_records_raw(group: str) -> dict[str, dict]:
    path = settings().artifacts_root / f"{group}_records.jsonl"
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as fh:
        rows = [json.loads(line) for line in fh if line.strip()]
    return {r["doc_id"]: r for r in rows}


def _group_records(group: str) -> dict[str, dict]:
    """Structured records. On the appending arms, returns a copy with the extra
    values attached.

    Each variant is cached separately. A single arm-independent cache would
    replay the first arm's mapping to every later arm and report a delta of
    zero for a manipulation that never took effect.
    """
    from argus import ablation

    arm = ablation.current()
    if arm.nary:
        return _group_records_nary(group)
    if arm.correction_fields:
        return _group_records_fields(group)
    return _group_records_raw(group)


# --- Appending extracted n-ary values to the evidence -----------------------
# No corpus file is modified. The build script writes one new file, and the
# merge happens at runtime on a copy. Editing the record files would change the
# BM25 index strings and invalidate every recorded measurement irreversibly.
#
# BM25 scores are therefore unchanged: the index is prebuilt and what changes
# here is only the evidence string carried into the prompt after retrieval.
# See docs/design-rationale.md#arm-catalog

NARY_NAME = "nary_records.jsonl"

#: Items to append, each with the pattern that detects whether it is already
#: present. This table is the definition of the union: an item the rules
#: already emitted is skipped. The amount label is chosen per record rather
#: than fixed, so it is matched by suffix.
_NARY_PRESENT = {
    "amount": re.compile(r"^-\s*\S*금액\s*:", re.M),
    "counterparty": re.compile(r"^-\s*계약상대\s*:", re.M),
    "period": re.compile(r"^-\s*(기간|계약\s*시작일|계약\s*종료일)\s*:", re.M),
    "subject": re.compile(r"^-\s*대상\s*:", re.M)}
#: Line cap per item. One record can yield several relations, and longer
#: evidence displaces other documents under the evidence cap.
MAX_NARY_VALUES = 3


#: The model puts values where a label belongs. Extraction produced amount
#: labels that were themselves amounts, which would emit an evidence line whose
#: field name is a figure. A label containing digits or running too long is not
#: a field name and is folded back to the default.
_LABEL_HAS_DIGIT = re.compile(r"\d")
MAX_NARY_LABEL_CHARS = 12


def _nary_amount_line(relation: dict) -> str | None:
    amount = relation.get("amount")
    if not isinstance(amount, int) or amount <= 0:
        return None
    label = " ".join(str(relation.get("amount_label") or "").split())
    if (not label or _LABEL_HAS_DIGIT.search(label)
            or len(label) > MAX_NARY_LABEL_CHARS):
        label = "계약금액"
    # Same format as the rule-emitted amount line. A different shape would
    # make the prompt formatting and the label gate miss this line alone.
    return f"- {label}: {amount:,}원 ({amount / 100_000_000:,.0f}억원)"


def _nary_lines(relations: list[dict]) -> dict[str, list[str]]:
    """Relations to evidence lines per item, deduplicated in order."""
    out: dict[str, list[str]] = {}

    def push(key: str, line: str | None) -> None:
        if not line:
            return
        bucket = out.setdefault(key, [])
        if line not in bucket and len(bucket) < MAX_NARY_VALUES:
            bucket.append(line)

    for relation in relations:
        if not isinstance(relation, dict):
            continue
        push("amount", _nary_amount_line(relation))
        if relation.get("counterparty"):
            push("counterparty", f"- 계약상대: {relation['counterparty']}")
        start, end = relation.get("period_start"), relation.get("period_end")
        if start and end:
            push("period", f"- 기간: {start} ~ {end}")
        elif start:
            push("period", f"- 계약 시작일: {start}")
        elif end:
            push("period", f"- 계약 종료일: {end}")
        if relation.get("subject"):
            push("subject", f"- 대상: {relation['subject']}")
    return out


@lru_cache(maxsize=1)
def _nary_extract() -> dict[str, dict[str, list[str]]]:
    """Mapping of receipt number to item to evidence lines. An absent file
    yields an empty mapping, making the arm a silent no-op."""
    path = settings().artifacts_root / NARY_NAME
    if not path.exists():
        return {}
    out: dict[str, dict[str, list[str]]] = {}
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            row = json.loads(line)
            lines = _nary_lines(row.get("relations") or [])
            if lines:
                out[str(row["rcept_no"])] = lines
    return out


def merge_nary(markdown: str, lines: dict[str, list[str]]) -> str:
    """A union rather than an overwrite: items the rules already emitted are
    skipped.

    Replacing rule output with model output would make this arm measure
    substitution rather than addition, and its target would overlap the node
    attribute leave-one-out, leaving the delta unattributable.
    See docs/design-rationale.md#arm-catalog
    """
    add = [line for key, values in lines.items()
           if not _NARY_PRESENT[key].search(markdown or "")
           for line in values]
    if not add:
        return markdown
    return (markdown.rstrip("\n") + "\n" + "\n".join(add)) if markdown else "\n".join(add)


@lru_cache(maxsize=len(RECORD_GROUPS))
def _group_records_nary(group: str) -> dict[str, dict]:
    """Copy used by the appending arm. The source mapping is never mutated:
    running another arm in the same process would otherwise contaminate its
    evidence, silently."""
    extract = _nary_extract()
    if not extract:
        return _group_records_raw(group)
    out: dict[str, dict] = {}
    for doc_id, record in _group_records_raw(group).items():
        lines = extract.get(str(record.get("rcept_no")))
        if not lines:
            out[doc_id] = record
            continue
        merged = merge_nary(record.get("markdown", ""), lines)
        out[doc_id] = record if merged == record.get("markdown", "") else {
            **record, "markdown": merged}
    return out


# --- Appending the revision reason and summary to the evidence --------------
# Built on the same runtime seam as the n-ary append, and likewise modifying no
# corpus file, so BM25 scores are unchanged.
#
# The defect it targets is that schema matching erases information: a matched
# record emits a four-line summary and discards the source rows, while an
# unmatched one carries the raw text. That leaves answers present in the record
# and absent from the evidence.
# See docs/design-rationale.md#arm-catalog

#: Labels to append. Only two: dumping the source rows wholesale is the
#: manipulation that was rejected.
CORRECTION_FIELD_LABELS = ("3. 정정사유", "2. 주요내용")

#: The append is restricted to one form. Applied to every form it cost a
#: question that was correct: the judge passed on all three criteria, but the
#: longer evidence pushed the answer toward listing roles and the amount
#: dropped out.
#:
#: Both target questions use this form, and it is also where the shaping loss
#: is worst: its template emits four lines and discards the body entirely,
#: while the contract form already carries amount, counterparty, period and
#: subject, so what it discards is mostly boilerplate.
CORRECTION_FIELD_FORMS = ("투자판단관련주요경영사항",)

#: Value length cap. The free-text cap used elsewhere is too short here: a
#: target answer sits past the 400th character of its field and would be cut.
CORRECTION_FIELD_CHARS = 560


def _correction_lines(record: dict) -> list[str]:
    """Lines to append to this record, skipping values already in the markdown.

    The last column is read. A revision table row is (label, before, after) and
    the answer is in the after column; reading the first would stamp the
    pre-revision text into the evidence, which is exactly the error the
    stale-value check exists to catch.

    The longest value per label wins. The same label appears in several rows,
    and skipping on a shared prefix discarded the longer one, truncating the
    context that carries the answer.
    """
    if (record.get("form") or "") not in CORRECTION_FIELD_FORMS:
        return []
    md = record.get("markdown") or ""
    best: dict[str, str] = {}
    for row in (record.get("raw_rows") or ()):
        if not (isinstance(row, (list, tuple)) and len(row) >= 2):
            continue
        label = str(row[0]).strip()
        if label not in CORRECTION_FIELD_LABELS:
            continue
        cells = [" ".join(str(c).split()) for c in row[1:]]
        value = next((c for c in reversed(cells) if len(c) >= 2), "")
        if len(value) < 2 or value[:24] in md:
            continue
        if len(value) > len(best.get(label, "")):
            best[label] = value
    return [f"- {label.split('. ', 1)[-1]}: {best[label][:CORRECTION_FIELD_CHARS]}"
            for label in CORRECTION_FIELD_LABELS if label in best]


@lru_cache(maxsize=len(RECORD_GROUPS))
def _group_records_fields(group: str) -> dict[str, dict]:
    """Copy used by this arm. The source mapping is never mutated, for the same
    reason as the n-ary variant: another arm run in the same process would
    inherit the contamination silently."""
    out: dict[str, dict] = {}
    for doc_id, record in _group_records_raw(group).items():
        lines = _correction_lines(record)
        out[doc_id] = ({**record,
                        "markdown": record.get("markdown", "") + "\n" + "\n".join(lines)}
                       if lines else record)
    return out


@lru_cache(maxsize=len(RECORD_GROUPS))
def _group_index(group: str) -> Bm25Index | None:
    path = settings().artifacts_root / f"{group}_bm25.json"
    return Bm25Index.load(path) if path.exists() else None


def _records() -> list[dict]:
    """Population for the event tools: structured events are exchange filings.

    Mixing the other groups in would change what an event query means and
    contaminate the fallback for exchange questions. The retrieval indexes grow
    per group; this population stays as it is.
    """
    return list(_group_records("exchange").values())


def _index() -> Bm25Index | None:
    return _group_index("exchange")


@lru_cache(maxsize=3)
def _by_rcept_cached(nary: bool, fields: bool = False) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for group in RECORD_GROUPS:
        if nary:
            source = _group_records_nary(group)
        elif fields:
            source = _group_records_fields(group)
        else:
            source = _group_records_raw(group)
        for record in source.values():
            out.setdefault(record["rcept_no"], record)
    return out


def _by_rcept() -> dict[str, dict]:
    """Receipt number to record, across all three document groups.

    Different from the event population. Event queries stay scoped to exchange
    filings, but a revision chain does not respect group boundaries: a chain
    living in another group would otherwise never resolve.

    The appending arms have to branch here too. Meta gathering, oracle
    injection and the chain hop all pass through this mapping, so branching in
    only one place makes an arm's evidence differ by path. That trap was hit in
    practice: an arm applied only to the group-level accessor left the evidence
    of its target question completely unchanged, because that question's
    revision arrives through the chain hop rather than retrieval.
    """
    from argus import ablation

    arm = ablation.current()
    return _by_rcept_cached(arm.nary, arm.correction_fields)


@lru_cache(maxsize=1)
def _followup_index() -> dict[str, tuple[str, ...]]:
    """Reverse index of the related-filings cell: filings that point back at
    this document.

    The revision chain cannot express one relation: a later termination. Of the
    recorded terminations none are linked through the revision field while
    eleven name the original contract in the related-filings cell. That edge
    runs from the later document to the earlier one, so reaching forward
    requires reversing it.

    Only resolved edges whose form name matches are used. An edge matched on
    company and date alone is a weak link and brings noise into the evidence.
    """
    path = settings().artifacts_root / REFERENCES_NAME
    if not path.exists():
        return {}
    rev: dict[str, set[str]] = {}
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            edge = json.loads(line)
            if not (edge.get("resolved") and edge.get("form_matched")):
                continue
            for target in edge.get("targets") or ():
                rev.setdefault(target, set()).add(edge["source_rcept_no"])
    return {key: tuple(sorted(v - {key})) for key, v in rev.items()}


def _record_of(doc_id: str) -> dict | None:
    """Find a record whether given a prefixed document id or a bare receipt
    number."""
    if not doc_id:
        return None
    key = str(doc_id).split("_")[-1].split("#")[0]
    return _by_rcept().get(key)


def chain_probe(doc_id: str) -> dict:
    """Preliminary lookup before walking a chain. Not a tool call, so it costs
    no step: it reads one record already in memory, with no retrieval and no
    I/O. Without it, `get_history` would be issued against documents that carry
    no link at all.

    The latest flag is determined by chain position rather than by amount.
    Deciding it by size would be wrong, because chains whose amount decreases
    exist.

    Three edge kinds are returned separately alongside their union, so the
    caller can tell which edge opened the seed condition:

        related     the revision chain, bidirectional
        followups   the reverse direction of the related-filings cell
        terminates  the forward direction from a termination to its contract

    Both directions are needed because the reverse index is empty when starting
    from a termination filing, and questions that start there exist.
    """
    record = _record_of(doc_id)
    if record is None:
        return {}
    rcept_no = record["rcept_no"]
    links = [r for r in (record.get("related") or []) if r != rcept_no]
    followups = tuple(_followup_index().get(rcept_no, ()))
    terminates = tuple(r for r in (record.get("terminates") or ()) if r != rcept_no)
    for other in followups + terminates:
        if other not in links:
            links.append(other)
    return {"rcept_no": rcept_no, "corp_name": record.get("corp_name", ""),
            "is_latest": bool(record.get("is_latest", True)),
            "links": tuple(links),
            "followups": followups, "terminates": terminates}


@lru_cache(maxsize=1)
def _section_index() -> SectionBm25Index | None:
    """Periodic section index, or None. The tool then returns an error and the
    pipeline stays up."""
    stem = settings().artifacts_root / SECTION_INDEX_STEM
    return SectionBm25Index.load(stem) if SectionBm25Index.exists(stem) else None


@lru_cache(maxsize=1)
def section_paths() -> tuple[str, ...]:
    """전 기업 공통 목차 경로. `path_hints` 가 후보를 고르는 모집단이다.

    깊이 2단 이하 + 200개 문서 이상 등장으로 자른다. 실측 64개이고, 여기에
    '12. 차입금 (연결)' 같은 기업별 주석 제목은 들어오지 않는다.
    """
    index = _section_index()
    if index is None:
        return ()
    docs: dict[str, set[int]] = {}
    path_codes, doc_codes = index.codes["path"], index.codes["doc_id"]
    table = index.tables["path"]
    for i in range(len(index.chunk_ids)):
        docs.setdefault(table[int(path_codes[i])], set()).add(int(doc_codes[i]))
    return tuple(p for p, seen in docs.items()
                 if len(seen) >= 200 and p.count(" > ") <= 1)


def _section_body(record: dict) -> str:
    """블롭에서 섹션 본문을 읽고 조각 하나를 잘라 낸다. `split_section` 이 결정적이다."""
    offset, length = record.get("text_offset", -1), record.get("text_bytes", 0)
    if offset < 0 or length <= 0:
        return ""
    with (settings().artifacts_root / BLOB_NAME).open("rb") as fh:
        fh.seek(offset)
        text = fh.read(length).decode("utf-8", errors="replace")
    if record.get("n_parts", 1) <= 1:
        return text
    parts = split_section(text, SECTION_CHUNK_CHARS)
    index = record.get("part", 0)
    return parts[index] if index < len(parts) else text


def _section_markdown(record: dict) -> str:
    """근거 블록. 근거 표기 형식은 §7.5 — 보고서(기준연월) · 접수번호 · 섹션 경로."""
    rcept_no = str(record["doc_id"]).split("_")[-1]
    label = {"annual": "사업보고서", "half": "반기보고서",
             "quarter": "분기보고서"}.get(record.get("doc_subtype", ""), "정기보고서")
    part = ""
    if record.get("n_parts", 1) > 1:
        part = f" [{record['part'] + 1}/{record['n_parts']} 조각]"
    body = _section_body(record)
    return (f"## [{label} {record.get('base_year', '')}] "
            f"{record.get('corp_name', '')} – {record.get('path', '')}{part}\n"
            f"- 출처: 접수번호 {rcept_no} (공시일 {record.get('rcept_dt', '')})\n\n{body}")


def resolve_company_tool(name: str) -> dict:
    row = resolve_company(name)
    if not row:
        return {"error": f"universe 70개사에 '{name}' 이 없습니다"}
    return {"corp_code": row["corp_code"], "corp_name": row["corp_name"],
            "sector": row["sector"], "market": row["market"]}


#: `query_events` 가 돌려주는 이벤트 수. LLM 이 읽을 목록이라 20 이 상한이었다.
EVENTS_PAGE = 20


def query_events(corp_name: str | None = None, doc_subtype: str | None = None,
                 year: int | None = None, min_amount: float | None = None,
                 is_latest: bool = True, limit: int = EVENTS_PAGE) -> dict:
    """정형 이벤트 조회. `count` 는 **필터를 통과한 전량**이고 `events` 는 그중 `limit` 건이다.

    `limit` 은 **`TOOL_SPECS` 에 노출하지 않는다** — 검색 계획이 쓰는 축이 아니라
    `pipeline._enumerate_events` 가 **그룹핑을 위해** 모집단 전체를 받아야 해서 있는
    것이다(계약상대별 그룹 크기와 각 그룹의 최신본은 20건만 봐서는 못 정한다 — 실측
    `Q-117` 은 한전기술 최신본 27건이고 골든 3건이 21번째 이후에 있다). `search_chunks`
    의 `doc_id` 축과 같은 성격이고, 기본값이면 종전과 글자 하나 안 바뀐다.
    """
    rows = _records()
    if corp_name:
        resolved = resolve_company(corp_name)
        target = resolved["corp_name"] if resolved else corp_name
        rows = [r for r in rows if r["corp_name"] == target]
    if doc_subtype:
        rows = [r for r in rows if r.get("doc_subtype") == doc_subtype]
    if year:
        rows = [r for r in rows if str(r.get("rcept_dt", ""))[:4] == str(year)]
    if min_amount is not None:
        rows = [r for r in rows if (r.get("amount") or 0) >= min_amount]
    if is_latest:
        rows = [r for r in rows if r.get("is_latest", True)]
    return {"count": len(rows), "events": rows[:max(int(limit), 0)]}


def list_filings(corp_name: str, doc_group: str = "periodic",
                 base_year: int | None = None) -> dict:
    docs = filter_docs(corp_name=corp_name, doc_group=doc_group, include_corrections=True)
    if base_year:
        docs = [d for d in docs if d.get("base_year") == base_year]
    return {"count": len(docs),
            "filings": [{"doc_id": d["doc_id"], "report_nm": d["report_nm"],
                         "rcept_no": d["rcept_no"], "rcept_dt": d["rcept_dt"],
                         "base_year": d.get("base_year")} for d in docs]}


def _sections_of(index: SectionBm25Index, doc_id: str) -> list[dict]:
    """한 문서의 조각 레코드. `part == 0` 만 남겨 섹션 단위로 되돌린다."""
    table = index.tables["doc_id"]
    code = table.index(doc_id) if doc_id in table else -1
    if code < 0:
        return []
    import numpy as np
    rows = np.flatnonzero(index.codes["doc_id"] == code)
    return [index.record_at(int(i)) for i in rows]


def list_sections(doc_id: str, include_stubs: bool = False) -> dict:
    """§7.7 ④ — 목차 `path` + 글자수. 기본은 100자 미만 스텁을 뺀다."""
    index = _section_index()
    if index is None:
        return {"error": "섹션 인덱스가 없습니다. "
                         "scripts/build_section_index.py 를 먼저 실행하세요"}
    out = []
    for record in _sections_of(index, doc_id):
        if record["part"] != 0:
            continue
        if record["is_stub"] and not include_stubs:
            continue
        out.append({"path": record["path"], "n_chars": record["section_chars"],
                    "n_tables": record["n_tables"], "n_parts": record["n_parts"],
                    "is_stub": bool(record["is_stub"])})
    return {"count": len(out), "sections": out}


def read_section(doc_id: str, path: str, part: int | None = None) -> dict:
    """§7.7 ⑤ — 섹션 원문(표=마크다운).

    상위 목차로 부르면 하위 섹션을 순서대로 이어 붙인다. 목차 오타(`12, 관계기업투자`)
    때문에 경로 비교는 구두점을 무시한다 — 완전일치로는 HMM 20240514001580 을 못 연다.
    """
    index = _section_index()
    if index is None:
        return {"error": "섹션 인덱스가 없습니다"}
    wanted = norm_path(path)
    rows = [r for r in _sections_of(index, doc_id)
            if norm_path(r["path"]) == wanted
            or norm_path(r["path"]).startswith(wanted)]
    if not rows:
        return {"error": f"섹션 없음: {doc_id} / {path}"}
    rows.sort(key=lambda r: (r["chunk_id"].split("/")[0], r["part"]))
    if part is not None:
        rows = [r for r in rows if r["part"] == part] or rows[:1]
    text = "\n\n".join(t for t in (_section_body(r) for r in rows) if t)
    head = rows[0]
    return {"doc_id": doc_id, "path": path, "chunk_id": head["chunk_id"],
            "corp_name": head["corp_name"], "rcept_dt": head["rcept_dt"],
            "doc_subtype": head["doc_subtype"], "base_year": head["base_year"],
            "n_sections": len(rows), "n_chars": len(text),
            "n_tables": sum(r["n_tables"] for r in rows), "text": text}


def _search_sections(query: str, corp_name: str | None = None, top_k: int = 5,
                     base_year: int | str | None = None,
                     doc_subtype: str | None = None,
                     path: str | None = None, rcept_dt: str | None = None,
                     include_stubs: bool = True,
                     doc_id: str | None = None) -> dict:
    """정기공시 섹션 검색. exchange 와 **인덱스가 다르다**.

    축이 셋 다르다 — 기간은 접수일이 아니라 `base_year`(사업연도), 보고서 종류가
    `doc_subtype`, 그리고 목차 `path` 접두. 실측 근거는 04-retriever D-5.

    `doc_id` 는 **문서 하나로 못박는 완전일치 필터**다 (`SECTION_FIELDS` 축). 검색 계획은
    이 인자를 쓰지 않는다 — `ablation.ORACLE` 이 골든 문서 안에서만 조각을 고르기 위한
    것이고, 기본값 `None` 이면 종전 후보 집합이 글자 하나 안 바뀐다.
    """
    index = _section_index()
    if index is None:
        return {"error": "섹션 인덱스가 없습니다"}
    filters: dict = {}
    if doc_id:
        filters["doc_id"] = doc_id
    if corp_name:
        resolved = resolve_company(corp_name)
        filters["corp_name"] = resolved["corp_name"] if resolved else corp_name
    if base_year:
        filters["base_year"] = str(base_year)
    if doc_subtype:
        filters["doc_subtype"] = doc_subtype
    prefix = {}
    if path:
        prefix["path"] = path
    if rcept_dt:
        prefix["rcept_dt"] = str(rcept_dt).replace("-", "")
    prefix = prefix or None
    hits = index.search(query, top_k=top_k * (2 if not include_stubs else 1),
                        prefix=prefix, **filters)
    chunks = []
    for chunk_id, score in hits:
        record = index.record(chunk_id)
        if record is None or (record["is_stub"] and not include_stubs):
            continue
        chunks.append({"doc_id": record["doc_id"], "chunk_id": chunk_id,
                       "path": record["path"], "score": round(score, 3),
                       "markdown": _section_markdown(record)})
        if len(chunks) >= top_k:
            break
    return {"count": len(chunks), "chunks": chunks}


def search_chunks(query: str, corp_name: str | None = None, top_k: int = 5,
                  year: int | None = None, ym: str | None = None,
                  group: str | None = None, base_year: int | str | None = None,
                  doc_subtype: str | None = None, path: str | None = None,
                  rcept_dt: str | None = None, include_stubs: bool = True,
                  doc_id: str | None = None) -> dict:
    """§7.7 ⑥ 하이브리드 검색 폴백. **인덱스 두 개를 인자로 가른다.**

    기간 조건이 랭킹을 좌우한다 — 실측에서 corp_name 만으로는 골든이 15/26 이지만
    rcept_dt prefix 를 함께 걸면 1위가 된다. 도구 스펙에도 노출해야 한다.

    정기공시 축(`base_year`·`doc_subtype`·`path`·`doc_id`)이 하나라도 오거나
    `group="periodic"` 이면 섹션 인덱스로 간다. 두 인덱스를 합치지 않은 이유는 실측이다 —
    합치면 exchange 골든 회수가 27 -> 26, top3 25 -> 24 로 떨어진다 (04-retriever D-5).

    `doc_id` 는 **섹션 인덱스 축에만 있다** (`periodic_<접수번호>`). `TOOL_SPECS` 에는
    노출하지 않는다 — 검색 계획이 쓰는 축이 아니라 `ablation.ORACLE` 이 골든 문서를
    못박기 위한 축이다.
    """
    if group == "periodic" or base_year or doc_subtype or path or doc_id:
        return _search_sections(query, corp_name=corp_name, top_k=top_k,
                                base_year=base_year, doc_subtype=doc_subtype,
                                path=path, rcept_dt=rcept_dt or ym,
                                include_stubs=include_stubs, doc_id=doc_id)
    target = group if group in RECORD_GROUPS else "exchange"
    index = _group_index(target)
    if index is None:
        return {"error": f"BM25 인덱스가 없습니다 ({target})"}
    filters = {}
    if corp_name:
        resolved = resolve_company(corp_name)
        filters["corp_name"] = resolved["corp_name"] if resolved else corp_name
    prefix = {}
    if ym:
        prefix["rcept_dt"] = str(ym).replace("-", "")[:6]
    elif year:
        prefix["rcept_dt"] = str(year)[:4]
    hits = index.search(query, top_k=top_k, prefix=prefix or None, **filters)
    by_id = _group_records(target)
    return {"count": len(hits),
            "chunks": [{"doc_id": d, "score": round(s, 3),
                        "markdown": by_id.get(d, {}).get("markdown", "")} for d, s in hits]}


def _chain_entry(record: dict, kind: str, is_latest: bool) -> dict:
    return {"rcept_no": record["rcept_no"], "doc_id": record["doc_id"],
            "rcept_dt": record.get("rcept_dt", ""),
            "is_correction": record.get("is_correction", False),
            "is_latest": is_latest, "amount": record.get("amount"),
            "kind": kind, "markdown": record.get("markdown", "")}


def get_history(rcept_no: str, include_followups: bool = True) -> dict:
    """정정 체인 + 후속 공시 추적. 유일하게 is_latest 를 무시한다.

    `chain` 은 같은 사안의 정정 이력을 **접수일 오름차순**으로 돌려준다. 그래서
    `is_latest` 는 **체인의 마지막 자리인가**로만 정해진다 — 금액 크기로 정하면 안 된다.
    실측으로 값이 줄어드는 체인이 있다 (한화오션 잠수함 1,102,000,000,000 ->
    443,500,000,000 -> ... -> 899,400,000,000). '가장 큰 값 = 최신' 규칙을 썼다면
    전부 틀렸을 것이다.

    `followups` 는 정정이 아니라 **뒤에 온 다른 사안**이다 (해지·철회). `related` 로는
    안 잡히고 `※ 관련공시` 역방향으로만 잡힌다 — 실측 해지 20건 중 related 0건,
    관련공시 11건. 정정본과 섞으면 '정정된 유효 계약' 과 '해지된 계약' 이 구분되지
    않으므로 `kind` 로 갈라 둔다.

    `terminated` 는 그 **반대 방향**이다 — 이 공시가 해지한 **원계약**(+그 정정 체인).
    `followups` 가 역방향뿐이라 해지에서 출발하면 빈다는 것이 회귀 `Q-066` 의 기전이고,
    이 칸이 그 방향을 채운다. **`followups` 와 절대 합치지 않는다**: 시간 방향이
    반대라 합치면 원계약이 '후속 공시' 로 찍히고, 그러면 모델이 원계약 금액 자리에
    해지 금액을 답한다 (Q-066 의 v2 A8 오답이 정확히 그 값이다).
    두 칸 다 `include_followups`(= `followup_hop` arm) 로 함께 가린다 — 출처가 같은
    `※ 관련공시` 셀이라 `L4` 의 정의가 갈라지면 안 된다.

    `markdown` 을 함께 실어 돌려준다. 체인을 찾아 놓고 근거 블록을 다시 조회해야 하면
    스텝이 두 배가 된다.
    """
    origin = _by_rcept().get(rcept_no)
    if not origin:
        return {"error": f"레코드 없음: {rcept_no}"}

    by_rcept = _by_rcept()
    members = [origin]
    for related in origin.get("related") or []:
        record = by_rcept.get(related)
        if record is not None and record["rcept_no"] != rcept_no:
            members.append(record)
    members.sort(key=lambda r: (str(r.get("rcept_dt", "")), r["rcept_no"]))
    latest = members[-1]["rcept_no"]

    chain = [_chain_entry(r, "origin" if r["rcept_no"] == rcept_no else
                          ("correction" if r.get("is_correction") else "related"),
                          r["rcept_no"] == latest)
             for r in members]

    followups = []
    if include_followups:
        known = {r["rcept_no"] for r in members}
        for source in _followup_index().get(rcept_no, ()):
            record = by_rcept.get(source)
            if record is None or record["rcept_no"] in known:
                continue
            followups.append(_chain_entry(record, "followup",
                                          bool(record.get("is_latest", True))))
        followups.sort(key=lambda e: (e["rcept_dt"], e["rcept_no"]))

    # 🔴 순방향 — 이 공시가 **해지한** 원계약(+그 원계약의 정정 체인). v3 R2 ④.
    # `followups` 와 **kind 를 갈라 둔다**: 저쪽은 '뒤에 온 다른 사안' 이고 이쪽은
    # '앞에 있던 원계약' 이라 시간 방향이 반대다. 섞으면 `_chain_note` 가 원계약을
    # '후속 공시' 로 찍어 모델이 해지금액을 원계약 금액으로 답한다 — Q-066 의 v2 오답이
    # 정확히 그 값이다(원계약 351,882,914,000 대신 해지금액 362,307,038,000).
    #
    # 🔴 **`include_followups` 로 함께 가린다.** `terminates` 도 출처가 `※ 관련공시`
    # 셀이므로(`corrections._termination_links` 가 `_reference_cells` 를 읽는다) 같은
    # `REFERS_TO` 엣지 가족이다. 따로 열어 두면 `L4`(= `followup_hop=False`)가
    # "관련공시 기여를 다 뺀 구성" 이 아니게 되어 이미 낸 −1.4%p 의 정의가 바뀐다.
    terminated = []
    if include_followups:
        known = {e["rcept_no"] for e in chain} | {e["rcept_no"] for e in followups}
        for target in (origin.get("terminates") or ()):
            record = by_rcept.get(target)
            if record is None or record["rcept_no"] in known:
                continue
            terminated.append(_chain_entry(record, "terminated",
                                           bool(record.get("is_latest", True))))
            known.add(record["rcept_no"])
        terminated.sort(key=lambda e: (e["rcept_dt"], e["rcept_no"]))

    return {"rcept_no": rcept_no, "chain_length": len(chain), "latest": latest,
            "chain": chain, "followups": followups, "terminated": terminated}


@lru_cache(maxsize=1)
def _counterparty_graph() -> dict | None:
    """발주처 2-hop 그래프 (`scripts/build_counterparty_graph.py` 산출물).

    없으면 None — 도구는 error 를 돌려주고 파이프라인은 산다 (`_section_index()` 와
    같은 패턴).
    """
    path = settings().artifacts_root / COUNTERPARTY_GRAPH_NAME
    if not path.exists():
        return None
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def query_by_counterparty(name: str, year: str | None = None) -> dict:
    """발주처 이름으로 그 발주처와 계약한 universe 기업들의 레코드를 모은다.

    밸류체인 문항의 fan-in/fan-out 완전 열거(Q-110·Q-114·Q-117)는 `query_events` 에
    계약상대 필터가 없어 구조적으로 불가하다 — `partners` 를 통째로 돌려주는 것이
    이 도구의 존재 이유다. `year` 는 거래소공시 `rcept_dt`(접수일) 접두 필터다 — 이
    도구가 보는 모집단이 정기공시가 아니라 거래소공시라 접수연도와 사업연도가 갈리는
    문제가 없다.

    비실명("글로벌 대형기업")과 보일러플레이트 안내문("...비밀유지...")은 둘 다
    노드가 아니지만 사유가 다르다 — 전자는 실체를 특정할 수 없고, 후자는 회사명이
    아니라 안내문 문장이다. 후자는 그래프의 `excluded_boilerplate` 에 이미 사유가
    기록돼 있어 재사용한다(정규식을 여기 새로 복제하지 않는다 — `scripts/
    build_counterparty_graph.py::BOILERPLATE_NOTE` 가 그 유일한 원천이다).
    """
    kind = classify(name)
    if kind == "vague":
        return {"error": f"비실명 상대('{name}')는 노드가 아니라 조회할 수 없습니다."}

    graph = _counterparty_graph()
    if graph is None:
        return {"error": "counterparty_graph.json 이 없습니다. "
                         "scripts/build_counterparty_graph.py 를 먼저 실행하세요"}

    key = canonical(name)
    node = next((n for n in graph["nodes"] if n["canonical"] == key), None)
    if node is None:
        boiler = next((b for b in graph.get("excluded_boilerplate", ())
                       if canonical(b["raw"]) == key), None)
        if boiler is not None:
            return {"error": f"'{name}' 은(는) 회사명이 아니라 안내문입니다 "
                             f"({boiler['reason']})"}
        return {"error": f"'{name}' 은(는) 2회 이상 등장하는 발주처가 아닙니다."}

    records = [
        r for r in _records()
        if r.get("counterparty") and canonical(r["counterparty"]) == key
        and (year is None or str(r.get("rcept_dt", "")).startswith(str(year)))
    ]
    return {"counterparty": node["display"], "kind": node["kind"],
            "partners": node["partners"], "records": records}


# --- 계산 도구 (policy §4) ---------------------------------------------------
# 반환 형태를 통일한다. `rounded` 는 답변에 그대로 쓰라고 주는 값이고
# `expr` 은 LLM 이 계산 과정을 설명할 때 쓰라고 주는 식이다 (LLM 은 재계산하지 않는다).

_fmt = compute.fmt_num


def _num_result(value: float, unit: str, expr: str, digits: int = 1) -> dict:
    return {"value": value, "rounded": round(value, digits), "unit": unit, "expr": expr}


def calc_pct_change(before: float, after: float) -> dict:
    return _num_result(compute.pct_change(before, after), "%",
                       f"({_fmt(after)} - {_fmt(before)}) / {_fmt(before)} × 100")


def calc_share(part: float, whole: float) -> dict:
    return _num_result(compute.share(part, whole), "%",
                       f"{_fmt(part)} / {_fmt(whole)} × 100")


def calc_total(values: list[float]) -> dict:
    return _num_result(compute.total(values), "",
                       " + ".join(_fmt(v) for v in values) or "0")


def calc_mean(values: list[float]) -> dict:
    return _num_result(compute.mean(values), "",
                       f"({' + '.join(_fmt(v) for v in values)}) / {len(values)}")


def calc_cagr(begin: float, end: float, years: float) -> dict:
    return _num_result(compute.cagr(begin, end, years), "%",
                       f"(({_fmt(end)} / {_fmt(begin)})^(1/{_fmt(years)}) - 1) × 100")


def calc_compare(a: float, b: float) -> dict:
    out = compute.compare(a, b)
    return {"value": out["larger"], "unit": "", "expr": f"{_fmt(a)} vs {_fmt(b)}", **out}


def calc_period(start: str, end: str) -> dict:
    """계약기간 등 두 날짜 사이의 만 년·개월.

    실측에서 HCX 가 Direct·CoT 모두 틀린 영역이다
    (2025-07-24 ~ 2033-12-31 → 8년 5개월인데 9년 5개월로 답했다).
    """
    parts = compute.period_parts(start, end)
    if parts is None:
        return {"error": f"날짜를 해석할 수 없습니다: start={start!r} end={end!r}"}
    years, months = parts
    return {"value": f"{years}년 {months}개월", "years": years, "months": months,
            "unit": "기간", "start": start, "end": end,
            "expr": f"{start} ~ {end}"}


def _spec(name: str, desc: str, props: dict, required: list[str]) -> dict:
    return {"type": "function",
            "function": {"name": name, "description": desc,
                         "parameters": {"type": "object", "properties": props,
                                        "required": required}}}


_STR = {"type": "string"}
_INT = {"type": "integer"}
_NUM = {"type": "number"}
_BOOL = {"type": "boolean"}
_NUMS = {"type": "array", "items": {"type": "number"}}

_RETRIEVAL_SPECS = [
    _spec("resolve_company", "기업 통용명을 DART corp_code·섹터로 변환한다",
          {"name": {**_STR, "description": "기업 통용명 (예: 현대차)"}}, ["name"]),
    _spec("query_events", "계약·투자·해지 등 정형 이벤트를 조회한다 (기본 최신 정정본)",
          {"corp_name": _STR, "doc_subtype": _STR, "year": _INT,
           "min_amount": _NUM, "is_latest": _BOOL}, []),
    _spec("list_filings", "정기공시 문서 목록을 조회한다",
          {"corp_name": _STR, "doc_group": _STR, "base_year": _INT}, ["corp_name"]),
    _spec("list_sections", "보고서의 목차 경로와 글자수를 조회한다 (기재생략 스텁 제외)",
          {"doc_id": _STR, "include_stubs": _BOOL}, ["doc_id"]),
    _spec("read_section", "특정 섹션 원문을 읽는다 (표는 마크다운). "
                          "긴 섹션은 조각으로 나뉘어 있고 part 로 하나만 읽을 수 있다",
          {"doc_id": _STR, "path": _STR, "part": _INT}, ["doc_id", "path"]),
    _spec("search_chunks", "목차로 찾기 어려운 주제를 하이브리드 검색한다. "
                           "거래소·주요사항·지분 공시는 기간(year/ym)을 주면 랭킹이 크게 좋아진다. "
                           "정기공시 섹션을 찾으려면 group='periodic' 과 함께 "
                           "base_year(사업연도)·doc_subtype·path 를 준다 — "
                           "'2023년 사업보고서' 는 2024년에 접수되므로 year 로는 못 찾는다",
          {"query": _STR, "corp_name": _STR, "top_k": _INT,
           "year": {**_INT, "description": "접수 연도 (예: 2023). 정기공시에는 쓰지 않는다"},
           "ym": {**_STR, "description": "접수 연월 (예: 202301). year 보다 우선"},
           "group": {**_STR, "description": "periodic 이면 정기공시 섹션 인덱스를 본다"},
           "base_year": {**_INT, "description": "정기공시 사업연도 (예: 2025)"},
           "doc_subtype": {**_STR, "description": "정기공시 종류 — annual · half · quarter"},
           "path": {**_STR, "description": "목차 경로 접두 (예: 'III. 재무에 관한 사항')"},
           "include_stubs": _BOOL},
          ["query"]),
    _spec("get_history", "공시 정정 이력 체인과 후속 공시(해지·철회)를 추적한다. "
                         "체인은 접수일 오름차순이고 마지막 건이 최신본이다 — "
                         "금액이 큰 쪽이 최신본이 아니다 (줄어드는 체인이 있다)",
          {"rcept_no": _STR,
           "include_followups": {**_BOOL,
                                 "description": "※관련공시로 이어진 후속 공시도 함께 "
                                                "돌려준다 (기본 true)"}},
          ["rcept_no"]),
    _spec("query_by_counterparty", "발주처(계약상대) 이름으로 그 발주처와 계약한 "
                                   "기업 전체(partners)와 관련 레코드를 조회한다. "
                                   "'A 사와 같은 발주처와 계약한 기업이 몇 개사인가' "
                                   "같은 fan-in/fan-out 완전 열거는 이 도구가 아니면 답할 "
                                   "수 없다 — query_events 에는 계약상대 필터가 없다",
          {"name": {**_STR, "description": "발주처(계약상대) 이름 (예: 한국수력원자력)"},
           "year": {**_STR, "description": "접수연도 4자리 또는 접수연월 6자리 접두 "
                                           "(예: '2024', '202407'). 정기공시 사업연도가 "
                                           "아니라 거래소공시 접수일 기준이다"}},
          ["name"]),
]

# policy §4 — 수치 연산은 전부 여기를 통과해야 한다. LLM 이 직접 계산하면 안 된다.
_CALC_SPECS = [
    _spec("calc_pct_change", "증감률(%)을 계산한다. 전기 대비 증가율·감소율은 반드시 이 도구를 쓴다",
          {"before": {**_NUM, "description": "기준 시점 값 (0 불가)"},
           "after": {**_NUM, "description": "비교 시점 값"}}, ["before", "after"]),
    _spec("calc_share", "비중·구성비(%)를 계산한다. 매출액 대비 비율 등에 쓴다",
          {"part": {**_NUM, "description": "부분 값"},
           "whole": {**_NUM, "description": "전체 값 (0 불가)"}}, ["part", "whole"]),
    _spec("calc_total", "여러 수치를 합산한다. 단위는 호출자가 맞춰서 넣는다",
          {"values": {**_NUMS, "description": "합산할 수치 목록"}}, ["values"]),
    _spec("calc_mean", "여러 수치의 산술평균을 계산한다",
          {"values": {**_NUMS, "description": "평균낼 수치 목록 (비어 있으면 불가)"}}, ["values"]),
    _spec("calc_cagr", "연평균 성장률 CAGR(%)을 계산한다. 여러 해에 걸친 성장률은 이 도구를 쓴다",
          {"begin": {**_NUM, "description": "기초 값 (0 초과)"},
           "end": {**_NUM, "description": "기말 값"},
           "years": {**_NUM, "description": "경과 연수 (0 초과)"}}, ["begin", "end", "years"]),
    _spec("calc_compare", "두 수치의 대소·차이·배수를 비교한다. 순위 판단에 쓴다",
          {"a": {**_NUM, "description": "첫 번째 값"},
           "b": {**_NUM, "description": "두 번째 값"}}, ["a", "b"]),
    _spec("calc_period", "두 날짜 사이의 기간을 '몇 년 몇 개월'로 계산한다. "
                         "계약기간 산정은 반드시 이 도구를 쓴다 (직접 계산하면 틀린다)",
          {"start": {**_STR, "description": "시작일 (예: 2025-07-24)"},
           "end": {**_STR, "description": "종료일 (예: 2033-12-31)"}}, ["start", "end"]),
]

TOOL_SPECS = _RETRIEVAL_SPECS + _CALC_SPECS

#: 🔴 **`A24b` 가 내미는 목록.** `TOOL_SPECS` 15종을 전부 내민 `A24` 는 모델이 부른 47건
#: 중 **26건이 검색**이었고(`read_section` 16 · `get_history` 10), 이미 프롬프트에 실려
#: 있는 근거를 다시 읽어 **희석시켰다** — 회귀 8건 중 7건이 도구 호출 문항이다
#: (정본 `docs/pipeline/09-ablation.md` §I-5g · `A24` 기각 사유).
#: 계산 7종만 내밀면 그 차선이 아예 닫힌다.
CALC_SPECS = list(_CALC_SPECS)

RETRIEVAL_TOOLS = frozenset(s["function"]["name"] for s in _RETRIEVAL_SPECS)
CALC_TOOLS = frozenset(s["function"]["name"] for s in _CALC_SPECS)

_DISPATCH = {
    "resolve_company": resolve_company_tool,
    "query_events": query_events,
    "list_filings": list_filings,
    "list_sections": list_sections,
    "read_section": read_section,
    "search_chunks": search_chunks,
    "get_history": get_history,
    "query_by_counterparty": query_by_counterparty,
    "calc_pct_change": calc_pct_change,
    "calc_share": calc_share,
    "calc_total": calc_total,
    "calc_mean": calc_mean,
    "calc_cagr": calc_cagr,
    "calc_compare": calc_compare,
    "calc_period": calc_period,
}


def execute(name: str, args: dict) -> dict:
    fn = _DISPATCH.get(name)
    if not fn:
        return {"error": f"알 수 없는 도구: {name}"}
    try:
        return fn(**args)
    except TypeError as exc:
        return {"error": f"인자 오류: {exc}"}
    except ValueError as exc:
        # 0 나눗셈·빈 목록 등 계산이 성립하지 않는 입력. 파이프라인을 죽이지 않는다.
        return {"error": f"계산 오류: {exc}"}
    except (KeyError, OSError) as exc:
        # 인덱스 컬럼 누락·블롭 부재 등 산출물 불일치. 도구 하나가 죽어도 다음 수단이 있다.
        return {"error": f"인덱스 오류: {type(exc).__name__} {exc}"}


# --- 법인등록번호 행 조회 (LLM 0회 · 2026-08-17) ------------------------------
#
# 🔴 **조각이 문제였지 근거 칸이 문제가 아니었다.** `AFFILIATE_PATH` 로 목차를 좁히면
# 검색은 **맞는 보고서**를 고른다. 그런데 그 섹션이 34,122자라 6조각으로 쪼개지고,
# BM25 는 회사명이 빽빽한 4번 조각을 고른다 — 정작 찾는 행은 703자 지점(1번 조각)이다.
#
#     [4/6 조각]  | CHONGQING HAPPYNARAE Co., Ltd. | - |  |  |   ← 골라 온 것
#     [1/6 조각]  | SK하이닉스㈜ | 134411-0001387 |            ← 답이 있는 곳
#
# 칸을 더 주는 것(`MAX_SECTION_CHARS` 확대)은 v4 가 세 번 기각한 방향이고, 조각을 다
# 실으면 34K 자가 근거를 통째로 먹는다. 그래서 **섹션 전문에서 행 하나만 뽑아 온다** —
# `aggregate.line` 이 집계를 한 줄로 싣는 것과 같은 자리다.
#
# 🔴 **`aggregate` 에 두지 않는 이유**: 그 모듈은 네트워크·파일 접근이 없다는 것이
# 규약 감사 `G2` 의 근거다. 코퍼스를 읽는 일은 코퍼스를 소유한 여기의 몫이다.

#: 법인등록번호가 실린 목차. `query.AFFILIATE_PATH` 와 **같은 값이어야 한다** —
#: 한쪽만 고치면 라우팅은 그 섹션을 부르는데 조회는 딴 데를 뒤진다.
CORP_NUMBER_PATH = "XII. 상세표 > 2. 계열회사 현황(상세)"

#: 표 행: `| SK하이닉스㈜ | 134411-0001387 |`. 회사명은 호출부가 끼운다.
_CORP_NUMBER_CELL = r"[|｜]\s*{name}\s*(?:㈜|\(주\)|주식회사)?\s*[|｜]\s*(\d{{6}}\s*-\s*\d{{7}})\s*[|｜]"


def _section_full_text(record: dict) -> str:
    """섹션 **전문**. `_section_body` 와 달리 조각으로 자르지 않는다."""
    offset, length = record.get("text_offset", -1), record.get("text_bytes", 0)
    if offset < 0 or length <= 0:
        return ""
    with (settings().artifacts_root / BLOB_NAME).open("rb") as fh:
        fh.seek(offset)
        return fh.read(length).decode("utf-8", errors="replace")


def corp_number_line(corp_name: str, doc_ids) -> str:
    """`corp_name` 의 법인등록번호를 계열회사 표에서 찾아 한 줄로 돌려준다.

    돌려주는 것은 근거에 그대로 실을 문장이고, 못 찾으면 빈 문자열이다.
    **찾은 문서의 접수번호를 함께 싣는다** — 근거 표기 규칙(§7.5)을 지켜야 하고,
    그러지 않으면 `critic` 이 출처 없는 수로 읽는다.

    🔴 `doc_ids` 는 **검색이 이미 고른 문서**로 한정한다. 코퍼스 전체를 뒤지면
    남의 보고서에 실린 행을 끌어와 *"이 회사가 저 회사의 계열사"* 라는 사실을
    출처 없이 주장하게 된다. 실측(70개사): 자기 보고서에 자기 번호를 싣는 기업이
    **16개**, 남의 보고서에만 실린 기업이 1개(기아)다. 뒤엣것은 안 집는다.
    """
    index = _section_index()
    if index is None or not corp_name:
        return ""
    pattern = re.compile(_CORP_NUMBER_CELL.format(name=re.escape(corp_name)))
    for doc_id in list(dict.fromkeys(doc_ids or ()))[:MAX_CORP_NUMBER_DOCS]:
        for record in _sections_of(index, doc_id):
            if record.get("path") != CORP_NUMBER_PATH or record.get("part", 0):
                continue
            hit = pattern.search(_section_full_text(record))
            if hit:
                return (f"[결정적 조회] {corp_name} 법인등록번호 {hit.group(1)} "
                        f"(출처: 접수번호 {record.get('rcept_no') or doc_id.split('_')[-1]}, "
                        f"{CORP_NUMBER_PATH})")
    return ""


#: 조회할 문서 수 상한. 검색 상위 몇 건이면 충분하고, 넘기면 블롭을 반복해 읽는다.
MAX_CORP_NUMBER_DOCS = 4
