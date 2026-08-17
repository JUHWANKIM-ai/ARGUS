"""Link a revised filing to what it revises.

Two clues are available: the submission date of the document being revised,
which points at it directly, and the related-filings list at the end of the
document, which enumerates earlier filings on the same matter.

Candidates are earlier filings by the same company, revisions included, and
both clues are used together. A chain is keyed on its root document, which is
the original when the corpus holds it and otherwise the earliest revision.

One non-revision relation is produced here as well: from a termination to the
contract it terminated. A termination filing is not a revision, so it never
enters the revision loop and its link field would otherwise stay empty.
See docs/design-rationale.md#revision-linking
"""
from __future__ import annotations

import html as html_lib
import re
from collections import defaultdict
from pathlib import Path

from argus.config import settings
from argus.corpus.dates import parse_date
from argus.corpus.html_doc import extract_rows, read_document
from argus.corpus.manifest import filter_docs
from argus.corpus.references import parse_reference_cell

_PARAGRAPH = re.compile(r"(?is)<P\b[^>]*>(.*?)</P>")
_ANY_TAG = re.compile(r"(?s)<[^>]*>")
_WS = re.compile(r"\s+")


def _text(fragment: str) -> str:
    return _WS.sub(" ", html_lib.unescape(_ANY_TAG.sub(" ", fragment))).strip()

# A related-filings cell repeats a date followed by a form name.
_DATE = re.compile(r"\d{4}[-.]\d{2}[-.]\d{2}")

# The label naming what a revision targets differs per document lineage, and
# in one lineage the label and value sit together in a paragraph outside any
# table. Supporting extra cell types alone does not link those; the label
# vocabulary is the actual cause.
# See docs/design-rationale.md#revision-linking
_SUBMIT_LABEL = re.compile(r"(?:정정관련|정정대상).*제출일")
# In the paragraph form the label and value are joined by a colon.
_LABEL_VALUE_SPLIT = re.compile(r"[:：]")


def parse_submit_clue(label: str, value: str) -> str | None:
    """Submission date of the document being revised, or None.

    Both the split table form and the single-paragraph form are accepted. A
    date under a label that does not match is ignored: the revision's own date,
    or a date inside the revision reason, would link to the wrong document.
    """
    if not _SUBMIT_LABEL.search(label):
        return None
    # In the paragraph form the value is attached to the label string.
    tail = _LABEL_VALUE_SPLIT.split(label, 1)[1] if _LABEL_VALUE_SPLIT.search(label) else ""
    parsed = parse_date(tail) or parse_date(value)
    return parsed.strftime("%Y%m%d") if parsed else None


def _doc_path(doc: dict) -> Path | None:
    folder = settings().corpus_root / doc["file_path"]
    xml = sorted(folder.glob("*.xml"))
    return xml[0] if xml else None


def _clue_dates(doc: dict) -> list[str]:
    """Candidate receipt dates for the revision target, in priority order."""
    path = _doc_path(doc)
    if not path:
        return []

    body = read_document(path)
    submit: str | None = None
    related: list[str] = []
    for row in extract_rows(body):
        if submit is None:
            submit = parse_submit_clue(" ".join(row[:-1]), row[-1])
        if any("관련공시" in cell for cell in row):
            related = [m.replace("-", "").replace(".", "") for m in _DATE.findall(row[-1])]

    # Absent from the table, read the paragraphs outside it.
    if submit is None:
        for paragraph in _PARAGRAPH.findall(body):
            submit = parse_submit_clue(_text(paragraph), "")
            if submit:
                break

    clues = [submit] if submit else []
    # Related filings are read newest first, skipping the submission date
    # already used.
    clues += [d for d in sorted(set(related), reverse=True) if d != submit]
    return clues


# Stripping the revision tag from a report name leaves the target form name.
_CORRECTION_PREFIX = re.compile(r"^\s*(?:\[[^\]]*\]|\(정정\))\s*")
_FORM_NOISE = re.compile(r"[\s·ㆍ]")


def _form_key(doc: dict) -> str:
    """Form identifier, used to pick the target among several filings a company
    made on one day.

    For one lineage the parentheses carry the form type and have to be kept;
    for the other they carry free text such as a contract name and have to be
    dropped. The document subtype is therefore preferred where present.
    """
    name = _FORM_NOISE.sub("", _CORRECTION_PREFIX.sub("", doc.get("report_nm") or ""))
    if name.startswith("주요사항보고서"):
        return name
    return doc.get("doc_subtype") or name.split("(")[0]


def _original_submit_date(doc: dict) -> str | None:
    """Read the submission-date clue alone, for diagnostics and
    compatibility."""
    clues = _clue_dates(doc)
    return clues[0] if clues else None


def _drop_cycles(parent: dict[str, str]) -> list[tuple[str, str]]:
    """Break cycles in the revision-to-target edges. Returns the edges broken,
    for diagnostics.

    Two revisions filed on the same day can each name the other, which stands
    up a parent relation in both directions and quietly breaks two structures:
    a chain splits across two roots so that neither member is marked latest and
    both vanish from every latest-only filter, and the member list of one chain
    is overwritten by a shorter one, in an order that depends on dictionary
    iteration and could change between rebuilds.

    Choosing one member of a cycle as the root is deliberately not done: it
    would declare the other superseded on the strength of a mutual reference,
    when their actual targets are different contracts outside the corpus. Every
    edge in a cycle is dropped instead, since the clue failed to identify a
    target. Edges outside the cycle survive.

    This takes effect only when the records are rebuilt, and a rebuild changes
    the latest flags and the index metadata, so its timing is a separate
    decision.
    See docs/design-rationale.md#revision-cycles
    """
    dropped: list[tuple[str, str]] = []
    #: None unvisited, 0 in progress on this path, 1 confirmed acyclic.
    state: dict[str, int] = {}
    for start in list(parent):
        path: list[str] = []
        node = start
        while node in parent and node not in state:
            state[node] = 0
            path.append(node)
            node = parent[node]
        if state.get(node) == 0:                    # 진행 중인 노드로 되돌아왔다
            for member in path[path.index(node):]:
                dropped.append((member, parent.pop(member)))
        for member in path:
            state[member] = 1
    return dropped


def build_chains(doc_group: str = "exchange") -> dict[str, list[str]]:
    docs = filter_docs(doc_group=doc_group)
    by_corp: dict[str, list[dict]] = defaultdict(list)
    for doc in docs:
        by_corp[doc["corp_name"]].append(doc)

    # Revision to the document it targets.
    parent: dict[str, str] = {}
    for corp_docs in by_corp.values():
        by_date: dict[str, list[dict]] = defaultdict(list)
        for doc in corp_docs:
            by_date[doc["rcept_dt"]].append(doc)

        for doc in corp_docs:
            if not doc["is_correction"]:
                continue
            form = _form_key(doc)
            for clue in _clue_dates(doc):
                if clue > doc["rcept_dt"]:
                    continue  # 미래 문서를 고칠 수는 없다
                candidates = [
                    other for other in by_date.get(clue, ())
                    if other["rcept_no"] != doc["rcept_no"]
                    # A different reporter means a different party's filing.
                    # In holding filings the company name is the issuer, so one
                    # day mixes documents from several reporters.
                    and other.get("flr_nm") == doc.get("flr_nm")
                    # A different form is not the target. Matching on date
                    # alone would mark an unrelated document as superseded.
                    # When the real target is outside the corpus, not linking
                    # is the correct outcome.
                    and _form_key(other) == form
                ]
                if not candidates:
                    continue
                # Prefer an original over a revision.
                candidates.sort(key=lambda o: (o["is_correction"], o["rcept_no"]))
                parent[doc["rcept_no"]] = candidates[0]["rcept_no"]
                break

    # Break cycles created by mutual references first. Without this, one chain
    # splits across two roots and ends up with no latest record at all.
    # See docs/design-rationale.md#revision-cycles
    _drop_cycles(parent)

    def root_of(rcept_no: str) -> str:
        seen = {rcept_no}
        current = rcept_no
        while current in parent:
            nxt = parent[current]
            if nxt in seen:      # `_drop_cycles` 뒤라 도달 불가. 방어만 남긴다
                break
            seen.add(nxt)
            current = nxt
        return current

    chains: dict[str, list[str]] = defaultdict(list)
    for correction in parent:
        root = root_of(correction)
        if root != correction:
            chains[root].append(correction)

    for key in chains:
        chains[key].sort()
    return dict(chains)


def latest_of(rcept_no: str, chains: dict[str, list[str]]) -> str:
    corrections = chains.get(rcept_no)
    return corrections[-1] if corrections else rcept_no


#: Document subtype of a termination filing. Every measured exchange
#: termination uses this one form; a similarly named form in another lineage
#: terminates a trust rather than a contract and is excluded.
TERMINATION_SUBTYPES = ("단일판매공급계약해지",)

#: Related-filings label, the record-side twin of the source-side check. That
#: one reads the raw cells while this reads normalised rows, and a record has
#: no report name so the form comparison differs too, which is why the two are
#: separate functions. A substring match would also catch the revision label.
def _is_reference_label(cell: str) -> bool:
    packed = _WS.sub("", cell or "")
    return "관련공시" in packed and "정정관련공시" not in packed


def _reference_cells(record: dict) -> list[str]:
    """Related-filings value cells from a record's rows, with no re-parsing of
    the source."""
    cells: list[str] = []
    for row in record.get("raw_rows") or ():
        if len(row) >= 2 and any(_is_reference_label(cell) for cell in row[:-1]):
            cells.append(row[-1])
    return cells


#: The source wording and the document subtype differ slightly in punctuation.
#: Noise is stripped and a prefix match counts as the same form.
_FORM_HEAD = 8


def _form_agrees(stated: str, doc_subtype: str) -> bool:
    left = _FORM_NOISE.sub("", _CORRECTION_PREFIX.sub("", stated or ""))
    right = _FORM_NOISE.sub("", doc_subtype or "")
    if not left or not right:
        return False
    return left[:_FORM_HEAD] in right or right[:_FORM_HEAD] in left


def _termination_links(records: list[dict],
                       member_chain: dict[str, list[str]]) -> dict[str, list[str]]:
    """Termination to the contract it terminated, plus that contract's revision
    chain. Forward direction only. No LLM call and no re-parsing of the source.

    No termination is reachable through the revision field; about half name the
    contract in the related-filings cell, and the rest point outside the
    corpus.

    The revision chain is included because the contract a termination names can
    itself already be superseded, and supplying that document alone would make
    the answer state the pre-revision amount.
    See docs/design-rationale.md#termination-links
    """
    by_corp_date: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for record in records:
        by_corp_date[(record.get("corp_name", ""), str(record.get("rcept_dt", "")))
                     ].append(record)

    links: dict[str, list[str]] = {}
    for record in records:
        if record.get("doc_subtype") not in TERMINATION_SUBTYPES:
            continue
        rcept_no = record["rcept_no"]
        found: list[str] = []
        for cell in _reference_cells(record):
            for target_date, form_name in parse_reference_cell(cell):
                siblings = [
                    other for other in by_corp_date.get(
                        (record.get("corp_name", ""), target_date), ())
                    if other["rcept_no"] != rcept_no
                    # A form mismatch leaves an edge held together by company
                    # and date alone. Pulling those into the evidence turns
                    # unrelated announcements into the original contract.
                    and _form_agrees(form_name, other.get("doc_subtype", ""))
                ]
                for other in siblings:
                    for candidate in (other["rcept_no"],
                                      *member_chain.get(other["rcept_no"], ())):
                        if candidate != rcept_no and candidate not in found:
                            found.append(candidate)
        if found:
            links[rcept_no] = sorted(found)
    return links


def attach_related(records: list[dict], chains: dict[str, list[str]]) -> list[dict]:
    """Mark only the last document of a chain as the latest, and fill in the
    termination links.

    Receipt numbers increase with time, so the last member of a chain is the
    latest. The original and every intermediate revision are superseded: if
    retrieval mistakes a stale original for the current document, the answer
    states the pre-revision amount.

    The termination relation is deliberately kept out of the revision link
    field. Consumers read that field as a revision chain and would label the
    original contract as a superseded value while promoting the termination to
    the latest, which hardens exactly the wrong answer.

    Attaching the whole forward direction of the related-filings cell was also
    rejected: most of those edges are neither revisions nor terminations, and
    attaching them would fire the completeness axis and the chain hop across
    hundreds of documents with no free way to re-measure the result.
    See docs/design-rationale.md#termination-links
    """
    superseded: set[str] = set()
    member_chain: dict[str, list[str]] = {}

    for root, corrections in chains.items():
        sequence = [root, *corrections]
        superseded.update(sequence[:-1])
        for rcept_no in sequence:
            # A lookup from any point of the chain has to see all of it.
            member_chain[rcept_no] = [x for x in sequence if x != rcept_no]

    terminates = _termination_links(records, member_chain)

    out = []
    for record in records:
        rcept_no = record["rcept_no"]
        enriched = dict(record)
        enriched["related"] = member_chain.get(rcept_no, [])
        enriched["is_latest"] = rcept_no not in superseded
        enriched["terminates"] = terminates.get(rcept_no, [])
        out.append(enriched)
    return out
