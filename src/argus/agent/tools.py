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
        base, kind = _group_records_nary(group), "nary"
    elif arm.correction_fields:
        base, kind = _group_records_fields(group), "fields"
    else:
        base, kind = _group_records_raw(group), "raw"
    # 🔴 `stale_note` 는 앞의 셋을 **대체하지 않고 겹친다.** 계획서 과제 1 단계 4 는 이
    # 분기를 맨 앞에 두고 바로 돌려주게 적었는데, `A56` 이 `correction_fields` 를 켜 두고
    # 있으므로 그러면 `A63` 이 그 분기를 조용히 잃는다 — 스위치와 무관한 근거 변화가
    # 델타에 섞인다. 어느 바탕 위에 각인했는지를 캐시 키에 넣어 팔별로 따로 담는다.
    if arm.stale_note:
        return _group_records_stale(group, kind)
    return base


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


def _stale_line(record: dict, latest: dict | None) -> str | None:
    """대체본 각인. 최신본이 같은 그룹에 있을 때 최신 금액까지 적는다.

    🔴 `정정체인:` 과 `대체됨` 은 하중을 받는 낱말이다. `critic._STALE_NOTE`
    (`정정체인:.*대체됨`)가 이 어구로 블록의 체인 위치를 읽어 `stale_value` 검사를 돈다.
    바꾸면 그 검사의 전제가 사라진다.

    🔴 최신본 접수번호는 적지 않는다. 근거에 안 실린 공시의 14자리를 블록에 쓰면
    `error_analysis._blocks` 가 그것을 **적재된 골든**으로 읽어 우리 재현율만 오른다 —
    `pipeline._roster_line` 이 `loaded` 로 제한한 것과 같은 근거다. 금액은 14자리가
    아니라 그 회계에 안 들어간다.
    See docs/superpowers/plans/2026-08-21-raw-preprocessing.md 과제 1
    """
    if record.get("is_latest", True):
        return None
    head = "- 정정체인: 이 공시는 정정되어 대체됨"
    if not latest:
        return head
    amount, mine = latest.get("amount"), record.get("amount")
    if not isinstance(amount, int) or amount == mine:
        return head
    label = latest.get("amount_type") or "금액"
    return f"{head} (최신 {label} {amount:,}원)"


@lru_cache(maxsize=len(RECORD_GROUPS) * 3)
def _group_records_stale(group: str, kind: str = "raw") -> dict[str, dict]:
    """각인한 사본. 원본 매핑은 절대 변형하지 않는다 — 같은 프로세스에서 다른 팔을
    돌리면 그 팔의 근거가 조용히 오염된다. `_group_records_nary` 와 같은 규율이다.

    `kind` 는 어느 바탕 위에 각인하는가다(`raw` · `fields` · `nary`). 캐시를 바탕별로
    나누지 않으면 첫 팔의 매핑이 뒤의 팔에 재생돼 없던 델타를 0 으로 보고한다.
    """
    base = {"nary": _group_records_nary, "fields": _group_records_fields}.get(
        kind, _group_records_raw)(group)
    by_rcept = {str(r.get("rcept_no")): r for r in base.values()}
    out: dict[str, dict] = {}
    changed = False
    for doc_id, record in base.items():
        links = [by_rcept.get(str(r)) for r in (record.get("related") or ())]
        latest = next((r for r in links if r and r.get("is_latest")), None)
        line = _stale_line(record, latest)
        if not line or line in (record.get("markdown") or ""):
            out[doc_id] = record
            continue
        changed = True
        head, sep, rest = (record.get("markdown") or "").partition("\n")
        out[doc_id] = {**record,
                       "markdown": f"{head}\n{line}\n{rest}" if sep else f"{head}\n{line}"}
    return out if changed else base


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


def holding_chain(reporter: str, corp_name: str) -> list[dict]:
    """Every holding record filed by `reporter` about `corp_name`, across the
    whole corpus, in filing order.

    `aggregate.holding_series` used to read a reporter's ratio chain out of
    the evidence records alone. The evidence carries only the receipts
    retrieval chose for one question, and a chain missing a filing renders a
    shorter history that computes correctly over what it was handed and is
    still false — the collapse a question asks about can sit in exactly the
    filing evidence dropped. The reporter and issuer are identified from the
    evidence's top-ranked record, same as before, but the chain itself is
    fetched from here. This is the same move `corp_number_line` makes for a
    registration-number row: read the source directly instead of trusting the
    evidence window to carry what is needed. No evidence slot is spent and no
    additional retrieval step runs, because every holding record is already
    parsed into memory by `_group_records`.

    The population is every holding record rather than the receipt-keyed
    mapping `_by_rcept()` builds, because the target here is the set of
    records sharing one (reporter, issuer) pair, not a single record reached
    by its own receipt number.
    See docs/design-rationale.md#holding-series
    """
    rows = [r for r in _group_records("holding").values()
            if r.get("reporter") == reporter and r.get("corp_name") == corp_name]
    rows.sort(key=lambda r: (str(r.get("rcept_dt", "")), r.get("rcept_no", "")))
    return rows


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
    """Table-of-contents paths common to every company: the population the
    path hints choose from.

    Cut to two levels deep and paths appearing in at least 200 documents, which
    keeps company-specific note headings out.
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
    """Read a section body from the blob and cut out one chunk. The split is
    deterministic."""
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


#: 분기보고서의 분기 이름. `base_month` 는 코퍼스 전체에서 3 또는 9 두 값뿐이라
#: (실측 2026-08-21: quarter/3 30,125건 · quarter/9 21,503건) 사상이 유일하다.
_QUARTER_NAME = {"3": "1분기", "9": "3분기"}


@lru_cache(maxsize=1)
def _base_month_by_rcept() -> dict[str, str]:
    """접수번호 -> 기준월. 대장에서 읽는다.

    🔴 **섹션 색인의 chunk 레코드에는 `base_month` 가 없다.** `record_at` 이 내는 필드는
    `base_year` · `doc_subtype` · `rcept_dt` 까지이고 기준월은 색인에 담기지 않았다. 첫
    구현이 `record.get("base_month")` 를 읽어 **112문항 전수에서 표지가 하나도 바뀌지
    않았다** — 조용한 무동작이었다.

    접수일로 유도하는 길은 막혀 있다. 실측(2026-08-21): 분기보고서의 접수월은 05월이
    `base_month` 3 이 28,983건이면서 9 도 66건, 02월도 양쪽에 걸린다(정정본이 늦게 접수된
    다). 유일하게 결정되지 않으므로 README §4.3 의 규칙대로 유도하지 않는다.

    대장에는 `base_month` 가 있고 `load_manifest` 가 이미 `lru_cache` 로 한 번만 읽는다.
    색인을 다시 만드는 것(제출 산출물 변경)보다 이쪽이 싸고 안전하다.
    """
    from argus.corpus.manifest import load_manifest

    out: dict[str, str] = {}
    for row in load_manifest():
        rcept, month = row.get("rcept_no"), row.get("base_month")
        if rcept and month not in (None, ""):
            out[str(rcept)] = str(month)
    return out


def _period_label(record: dict) -> str:
    """Report kind and period, as it appears inside the evidence header brackets.

    🔴 Returns the year too, so the quarter can follow it. The first version
    returned only the kind and let `_section_markdown` append the year, which
    put the tokens in the wrong order -- `[분기보고서 1분기 2025]`. The natural
    reading is `[분기보고서 2025 1분기]`, and the evidence header is text the model
    reads, so the order is not cosmetic.

    With the flag off the string is byte-identical to what the old
    `f"{kind} {base_year}"` produced, for every report kind.

    Two quarterly filings of one year rendered an identical header --
    `[분기보고서 2025] 케이티 – IV. 이사의 경영진단 및 분석의견` for both the first and
    the third quarter. With both in the evidence nothing distinguished them, and
    `Q-105` answered a question naming the third quarter by citing the first.

    Only quarterlies gain a token. Annual and half-year filings are already
    unique within a year, so adding one there would change evidence text for
    every periodic question to no purpose.

    The quarter comes from the ledger rather than the record: the section index
    does not carry `base_month`. See `_base_month_by_rcept`.
    """
    from argus import ablation       # 함수 안에서 — 이 모듈의 다른 두 곳과 같은 이유(순환)
    kind = {"annual": "사업보고서", "half": "반기보고서",
            "quarter": "분기보고서"}.get(record.get("doc_subtype", ""), "정기보고서")
    stem = "%s %s" % (kind, record.get("base_year", ""))
    if not ablation.enabled("period_label") or record.get("doc_subtype") != "quarter":
        return stem
    month = record.get("base_month")
    if month in (None, ""):
        rcept = str(record.get("doc_id", "")).split("_")[-1]
        month = _base_month_by_rcept().get(rcept)
    quarter = _QUARTER_NAME.get(str(month))
    return "%s %s" % (stem, quarter) if quarter else stem


def _section_markdown(record: dict) -> str:
    """Evidence block. The citation format is the one the convention requires:
    report and period, receipt number, section path."""
    rcept_no = str(record["doc_id"]).split("_")[-1]
    label = _period_label(record)
    part = ""
    if record.get("n_parts", 1) > 1:
        part = f" [{record['part'] + 1}/{record['n_parts']} 조각]"
    body = _section_body(record)
    return (f"## [{label}] "
            f"{record.get('corp_name', '')} – {record.get('path', '')}{part}\n"
            f"- 출처: 접수번호 {rcept_no} (공시일 {record.get('rcept_dt', '')})\n\n{body}")


def resolve_company_tool(name: str) -> dict:
    row = resolve_company(name)
    if not row:
        return {"error": f"universe 70개사에 '{name}' 이 없습니다"}
    return {"corp_code": row["corp_code"], "corp_name": row["corp_name"],
            "sector": row["sector"], "market": row["market"]}


#: Events returned by the event query. The cap reflects a list meant to be
#: read by the model.
EVENTS_PAGE = 20


def query_events(corp_name: str | None = None, doc_subtype: str | None = None,
                 year: int | None = None, min_amount: float | None = None,
                 is_latest: bool = True, limit: int = EVENTS_PAGE) -> dict:
    """Structured event lookup. The count is everything passing the filters
    while the events are the first `limit` of them.

    `limit` is deliberately not exposed in the tool specifications. It is not
    an axis the retrieval plan uses; it exists so the enumeration hop can take
    the whole population for grouping, since group sizes and per-group latest
    records cannot be determined from a truncated list. At its default nothing
    changes.
    See docs/design-rationale.md#enumeration-hop
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
    """Chunk records of one document, reduced back to section granularity."""
    table = index.tables["doc_id"]
    code = table.index(doc_id) if doc_id in table else -1
    if code < 0:
        return []
    import numpy as np
    rows = np.flatnonzero(index.codes["doc_id"] == code)
    return [index.record_at(int(i)) for i in rows]


def list_sections(doc_id: str, include_stubs: bool = False) -> dict:
    """List section paths with their character counts. Stubs below 100
    characters are excluded by default."""
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
    """Read a section verbatim, with tables as markdown.

    Called with a parent path, the child sections are concatenated in order.
    Path comparison ignores punctuation, because typos in the table of contents
    mean an exact match fails to open some documents.
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
    """Section retrieval for periodic filings, against a different index from
    the exchange one.

    Three axes differ: the period is the fiscal year rather than the receipt
    date, the report subtype replaces the form, and the table-of-contents path
    acts as a prefix filter.

    `doc_id` is an exact-match filter pinning a single document. The retrieval
    plan does not use it; it exists so the oracle path can choose chunks within
    a golden document, and at its default the candidate set is unchanged.
    See docs/design-rationale.md#oracle-arm
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
    """Hybrid retrieval entry point, choosing between the two indexes by its
    arguments.

    The period condition dominates ranking: a company filter alone leaves the
    golden record outside the top results, while adding a receipt-date prefix
    puts it first. It is therefore exposed in the tool specification.

    Any periodic axis, or an explicit periodic group, routes to the section
    index. The two indexes are kept separate by measurement: merging them
    lowers exchange golden recovery.

    `doc_id` exists on the section axis only and is not exposed in the tool
    specification, for the same reason as above.
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
    """Trace a revision chain and its follow-up filings. The one tool that
    ignores the latest flag.

    The chain returns the revision history of one matter in ascending filing
    order, which is what defines the latest position. It must not be defined by
    amount: chains whose amount decreases exist, and a largest-value rule would
    get every one of them wrong.

    Follow-ups are later matters rather than revisions, such as terminations.
    They are not reachable through the revision field and appear only in the
    reverse direction of the related-filings cell. Merged with revisions, a
    terminated contract would be indistinguishable from a revised live one, so
    they carry a separate kind.

    The terminated entries run the opposite way: the original contract this
    filing terminated, together with its revision chain. They fill the
    direction the reverse index cannot reach. They are never merged with
    follow-ups, because the time direction is reversed and merging makes the
    model answer with the termination amount where the original amount belongs.

    Both are gated by the same flag, since both come from the same source cell
    and the definition of the corresponding leave-one-out must not split.

    The markdown is returned alongside. Looking up the evidence block again
    after finding the chain would double the steps.
    See docs/design-rationale.md#chain-note-labels
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

    # Forward direction: the original contract this filing terminated, with
    # its revision chain. Kept under a separate kind from the follow-ups,
    # because the time direction is reversed and merging them makes the
    # evidence label an original contract as a later filing.
    #
    # Gated by the same flag as the follow-ups: both come from the same source
    # cell, so leaving one open would change what the corresponding
    # leave-one-out measures.
    # See docs/design-rationale.md#followup-edge-split
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
    """The counterparty graph, or None. The tool then returns an error and the
    pipeline stays up, the same pattern as the section index.
    """
    path = settings().artifacts_root / COUNTERPARTY_GRAPH_NAME
    if not path.exists():
        return None
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def query_by_counterparty(name: str, year: str | None = None) -> dict:
    """Collect the records of every universe company that contracted with a
    given counterparty.

    Complete fan-in and fan-out enumeration is structurally impossible through
    the event query, which has no counterparty filter, and returning the whole
    partner list is why this tool exists. The year argument filters on the
    receipt-date prefix; since the population here is exchange filings rather
    than periodic ones, receipt year and fiscal year do not diverge.

    A non-specific counterparty and a boilerplate confidentiality notice are
    both non-nodes for different reasons: the first cannot be identified, the
    second is not a company name at all. The reason for the second is already
    recorded in the graph and is reused rather than reimplemented here, so the
    pattern has a single source.
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


# --- Calculation tools ------------------------------------------------------
# The return shape is uniform. The rounded value is what the answer should
# quote verbatim, and the expression is what the model should use when
# explaining the calculation. The model does not recompute either.

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
    """Whole years and months between two dates, such as a contract duration.

    An area the model gets wrong on both the direct and the reasoning path.
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

# Every numeric operation passes through this registry. The model must not
# perform one itself.
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

#: The subset offered when tools are restricted to calculation. Offering every
#: tool sent most calls to retrieval, which re-read evidence already in the
#: prompt and diluted it; restricting the list closes that lane entirely.
#: See docs/design-rationale.md#function-calling-arm
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
        # Input on which the calculation does not hold, such as a zero
        # divisor or an empty list. This must not take the pipeline down.
        return {"error": f"계산 오류: {exc}"}
    except (KeyError, OSError) as exc:
        # Artifact mismatch, such as a missing index column or absent blob.
        # One failing tool still leaves the next means available.
        return {"error": f"인덱스 오류: {type(exc).__name__} {exc}"}


# --- Corporate registration number row lookup. No LLM call ------------------
# The problem was chunking rather than evidence slots: narrowing the table of
# contents makes retrieval choose the right report, but the section is large
# enough to split and the chunk that wins on BM25 is not the chunk holding the
# row.
#
# Widening the window was rejected repeatedly, and carrying every chunk would
# let one section consume the whole evidence, so a single row is extracted from
# the full section instead.
#
# It lives here rather than in the aggregation module, because that module
# having no file or network access is what a compliance check depends on.
# Reading the corpus belongs to the module that owns it.
# See docs/design-rationale.md#section-chunking

#: Table-of-contents path carrying the registration numbers. It has to equal
#: the value the query planner uses: changing one alone makes routing request
#: that section while the lookup searches elsewhere.
CORP_NUMBER_PATH = "XII. 상세표 > 2. 계열회사 현황(상세)"

#: A table row pairing a company name with its number. The name is
#: interpolated by the caller.
_CORP_NUMBER_CELL = r"[|｜]\s*{name}\s*(?:㈜|\(주\)|주식회사)?\s*[|｜]\s*(\d{{6}}\s*-\s*\d{{7}})\s*[|｜]"


def _section_full_text(record: dict) -> str:
    """The full section body, uncut, unlike the chunked accessor."""
    offset, length = record.get("text_offset", -1), record.get("text_bytes", 0)
    if offset < 0 or length <= 0:
        return ""
    with (settings().artifacts_root / BLOB_NAME).open("rb") as fh:
        fh.seek(offset)
        return fh.read(length).decode("utf-8", errors="replace")


def corp_number_line(corp_name: str, doc_ids) -> str:
    """Find a company's registration number in the affiliate table and return
    it as one line.

    The return value is a sentence to place in the evidence, or an empty string
    when nothing is found. The receipt number of the document it came from is
    included, because the citation rule requires it and without it the verifier
    reads the figure as having no source.

    The search is limited to documents retrieval already chose. Scanning the
    whole corpus would pull a row out of another company's report and assert an
    affiliation without a source.
    See docs/design-rationale.md#section-chunking
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


#: Cap on documents inspected. The top few retrieval results suffice, and
#: going further re-reads the blob repeatedly.
MAX_CORP_NUMBER_DOCS = 4
