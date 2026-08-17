"""Parsing of DART disclosure HTML and XML.

Two pitfalls govern this module.

Encoding: file bytes are UTF-8 while the meta tag declares euc-kr. Automatic
detection therefore corrupts the text, so UTF-8 is forced.

Cell tags come from two lineages. Exchange filings are KIND HTML and carry
values in `<td>`; major, holding and periodic filings are DART XML and carry
values in `<TE>` and `<TU>`. A parser restricted to `td|th` misses most value
cells in the holding group, leaving labels without figures. All four tags are
therefore treated as cells.
"""
from __future__ import annotations

import html as html_lib
import re
from pathlib import Path

_STYLE = re.compile(r"(?is)<style[^>]*>.*?</style>")
_SCRIPT = re.compile(r"(?is)<script[^>]*>.*?</script>")
_TAG = re.compile(r"(?s)<[^>]*>")
_TITLE = re.compile(r"(?is)<title>(.*?)</title>")
_WS = re.compile(r"\s+")

# Four cell kinds. TD and TH are HTML lineage, TU and TE are DART XML.
_TR_OPEN = re.compile(r"(?i)<TR\b[^>]*>")
_TR_CLOSE = re.compile(r"(?i)</TR>")
_CELL_OPEN = re.compile(r"(?i)<(T[DHUE])\b[^>]*>")
_CELL_CLOSE = re.compile(r"(?i)</(T[DHUE])>")
_COLSPAN = re.compile(r"(?i)COLSPAN\s*=\s*[\"']?(\d+)")


def read_document(path: Path) -> str:
    """Read the file as UTF-8, ignoring the declared charset."""
    return Path(path).read_text(encoding="utf-8", errors="replace")


def _clean(fragment: str) -> str:
    text = _TAG.sub(" ", fragment)
    return _WS.sub(" ", html_lib.unescape(text)).strip()


def strip_markup(html: str) -> str:
    body = _SCRIPT.sub(" ", _STYLE.sub(" ", html))
    return _clean(body)


def extract_title(html: str) -> str:
    match = _TITLE.search(html)
    return _clean(match.group(1)) if match else ""


def _top_level(
    text: str, open_re: re.Pattern, close_re: re.Pattern
) -> list[tuple[int, int, int, int]]:
    """Return top-level elements only, skipping nested ones.

    Yields (open start, content start, content end, close end).

    A non-greedy `<tr>(.*?)</tr>` is insufficient: when a table is nested
    inside a cell, the outer row terminates at the inner `</TR>` and labels
    become separated from their values.
    """
    events = [(m.start(), 1, m.end()) for m in open_re.finditer(text)]
    events += [(m.start(), -1, m.end()) for m in close_re.finditer(text)]
    events.sort()
    out: list[tuple[int, int, int, int]] = []
    depth, opened = 0, None
    for pos, kind, tag_end in events:
        if kind == 1:
            if depth == 0:
                opened = (pos, tag_end)
            depth += 1
        elif depth > 0:
            depth -= 1
            if depth == 0 and opened:
                out.append((opened[0], opened[1], pos, tag_end))
                opened = None
    return out


def _row_cells(row: str, expand_colspan: bool) -> list[str]:
    """Cell text for one row. With `expand_colspan`, merged cells are padded.

    Without expansion every value after a merged cell shifts left by the merge
    width, which breaks the label-to-value mapping for the whole row.
    """
    cells: list[str] = []
    for tag_start, inner_start, inner_end, _ in _top_level(row, _CELL_OPEN, _CELL_CLOSE):
        cells.append(_clean(row[inner_start:inner_end]))
        if expand_colspan:
            span = _COLSPAN.search(row[tag_start:inner_start])
            if span:
                cells.extend([""] * (max(1, int(span.group(1))) - 1))
    return cells


def _iter_rows(html: str, expand_colspan: bool) -> list[list[str]]:
    body = _SCRIPT.sub(" ", _STYLE.sub(" ", html))
    rows: list[list[str]] = []
    for _, inner_start, inner_end, _ in _top_level(body, _TR_OPEN, _TR_CLOSE):
        rows.append(_row_cells(body[inner_start:inner_end], expand_colspan))
    return rows


def extract_rows(html: str) -> list[list[str]]:
    """Table to label/value rows, dropping empty cells.

    Consumers read `row[:-1]` as labels and `row[-1]` as the value, so an
    interleaved empty cell would displace the value. Use `extract_grid` when
    column position matters.
    """
    return [cells for row in _iter_rows(html, expand_colspan=False)
            if (cells := [c for c in row if c])]


def extract_grid(html: str) -> list[list[str]]:
    """Table to grid, preserving empty cells and COLSPAN so columns align.

    Used for tables where column position carries meaning, such as financials.
    """
    return [row for row in _iter_rows(html, expand_colspan=True) if any(row)]
