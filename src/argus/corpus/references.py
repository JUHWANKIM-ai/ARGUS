"""Extraction of REFERS_TO edges from the related-disclosure section.

Distinct from correction chains. A correction says one document amends
another; a related disclosure says these are the preceding filings on the
same matter, such as an inquiry, its answer, a contract and its termination.

Unresolved references are recorded rather than discarded, so that a parser
failure remains distinguishable from a genuine absence:

    resolved       bound to a document inside the corpus
    before_corpus  filed before the corpus start date, a structural bound
    not_found      within range but absent, either uncollected or out of form

`targets` is a list because one company can file several documents of the same
form on the same day. Selecting one arbitrarily would produce a wrong citation.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from argus.config import settings
from argus.corpus.html_doc import extract_rows, read_document
from argus.corpus.manifest import load_manifest

# Corpus start date. References earlier than this cannot be resolved.
CORPUS_START = "20230102"

def _is_reference_label(cell: str) -> bool:
    """Whether a cell is the related-filings label.

    A substring match would also catch the revision label, and the date beside
    that one would then leak in as a reference with no form name, binding to
    any document filed the same day.
    """
    packed = _WS.sub("", cell)
    return "관련공시" in packed and "정정관련공시" not in packed
# Date first, then the form name.
_DATE_FIRST = re.compile(r"(\d{4})[-.](\d{2})[-.](\d{2})")
# Form name first, with the date in parentheses.
_DATE_IN_PAREN = re.compile(r"\(\s*(\d{4})[-.](\d{1,2})[-.](\d{1,2})\s*\)")
_EMPTY = {"-", "–", "—", ""}
_WS = re.compile(r"\s+")


@dataclass(frozen=True)
class Reference:
    """One related-filings entry."""

    source_rcept_no: str
    source_doc_id: str
    corp_name: str
    target_date: str                     # receipt date as the source wrote it
    form_name: str                       # form name as the source wrote it
    resolved: bool
    reason: str                          # resolved | before_corpus | not_found
    targets: tuple[str, ...] = field(default_factory=tuple)
    # Whether the form named by the source matches the form of the document
    # bound to. False leaves an edge held together by company and date alone,
    # which happens when the referenced form is of a kind the corpus does not
    # carry at all. A consumer that cares about precision filters on this.
    form_matched: bool = False

    def as_dict(self) -> dict:
        return {
            "source_rcept_no": self.source_rcept_no,
            "source_doc_id": self.source_doc_id,
            "corp_name": self.corp_name,
            "target_date": self.target_date,
            "form_name": self.form_name,
            "resolved": self.resolved,
            "reason": self.reason,
            "targets": list(self.targets),
            "form_matched": self.form_matched,
        }


def parse_reference_cell(cell: str) -> list[tuple[str, str]]:
    """A reference cell to (date, form name) pairs.

    Several entries run together in one cell with no separator, so the dates
    are used as the boundaries.
    """
    text = (cell or "").strip()
    if text in _EMPTY:
        return []

    # Starting with a date means a list of date and form name. Handling the
    # parenthesised form here instead would cut the form name at the bracket.
    if _DATE_FIRST.match(text):
        matches = list(_DATE_FIRST.finditer(text))
        out = []
        for i, m in enumerate(matches):
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            name = _WS.sub(" ", text[m.end():end]).strip(" ,;")
            out.append(("".join(m.groups()), name))
        return out

    # The other shape: form name first, date in parentheses.
    out = []
    for m in _DATE_IN_PAREN.finditer(text):
        year, month, day = m.groups()
        name = _WS.sub(" ", text[:m.start()]).strip(" :,;")
        out.append((f"{year}{int(month):02d}{int(day):02d}", name))
    return out


_FORM_NOISE = re.compile(r"[\s·ㆍ()\[\]]")
_FORM_PREFIX = re.compile(r"^\[[^\]]*\]")
# The source and the manifest space and punctuate a form name differently, so
# a prefix match counts as the same form.
_FORM_HEAD = 8


def _forms_agree(stated: str, report_nm: str) -> bool:
    left = _FORM_NOISE.sub("", _FORM_PREFIX.sub("", stated or ""))
    right = _FORM_NOISE.sub("", _FORM_PREFIX.sub("", report_nm or ""))
    if not left or not right:
        return False
    return left[:_FORM_HEAD] in right or right[:_FORM_HEAD] in left


@lru_cache(maxsize=1)
def _by_corp_date() -> dict[tuple[str, str], tuple[dict, ...]]:
    index: dict[tuple[str, str], list[dict]] = {}
    for doc in load_manifest():
        index.setdefault((doc["corp_name"], doc["rcept_dt"]), []).append(doc)
    return {k: tuple(v) for k, v in index.items()}


def _doc_path(doc: dict) -> Path | None:
    folder = settings().corpus_root / doc["file_path"]
    xml = sorted(folder.glob("*.xml"))
    return xml[0] if xml else None


def _reference_cells(doc: dict) -> list[str]:
    path = _doc_path(doc)
    if not path:
        return []
    cells: list[str] = []
    for row in extract_rows(read_document(path)):
        # With the label in a label cell, the value cell is the reference list.
        if any(_is_reference_label(cell) for cell in row[:-1]):
            cells.append(row[-1])
        # One lineage embeds the label inside prose instead.
        elif (last := row[-1]) and _is_reference_label(last):
            cells.append(last.split("관련공시", 1)[-1].lstrip(" :："))
    return cells


def extract_references(doc: dict) -> list[Reference]:
    """One filing to its reference candidates. A reference that does not
    resolve is kept with the reason it did not."""
    index = _by_corp_date()
    out: list[Reference] = []
    seen: set[tuple[str, str]] = set()

    for cell in _reference_cells(doc):
        for target_date, form_name in parse_reference_cell(cell):
            if (target_date, form_name) in seen:
                continue
            seen.add((target_date, form_name))

            siblings = [
                other for other in index.get((doc["corp_name"], target_date), ())
                if other["rcept_no"] != doc["rcept_no"]
            ]
            # Several filings on one day are narrowed by the form the source
            # named.
            same_form = [o for o in siblings if _forms_agree(form_name, o["report_nm"])]
            chosen = same_form or siblings
            candidates = tuple(o["rcept_no"] for o in chosen)

            if candidates:
                reason = "resolved"
            elif target_date < CORPUS_START:
                # Earlier than the corpus start, so the document is absent by
                # construction.
                reason = "before_corpus"
            else:
                reason = "not_found"

            out.append(Reference(
                source_rcept_no=doc["rcept_no"],
                source_doc_id=doc["doc_id"],
                corp_name=doc["corp_name"],
                target_date=target_date,
                form_name=form_name,
                resolved=bool(candidates),
                reason=reason,
                targets=candidates,
                form_matched=bool(same_form),
            ))
    return out


def iter_all_references(doc_groups: tuple[str, ...] = ("exchange", "major")):
    """Sweep the whole corpus for reference edges. Analysis only: nothing calls
    this.

    Neither the operational path nor the runner uses it; they extract
    references one document at a time. It is kept because the corpus-wide
    counts behind the termination-link decision can only be recomputed here.
    Measure the cost before calling it from new code: it is a full sweep.
    See docs/design-rationale.md#termination-links
    """
    for doc in load_manifest():
        if doc["doc_group"] in doc_groups:
            yield from extract_references(doc)
