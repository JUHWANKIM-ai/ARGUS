r"""마크다운 문서를 **주최측 양식 규격의 docx 로 짓고 PDF 로 굳힌다.**

    PYTHONIOENCODING=utf-8 python scripts/build_pdf.py docs/proposal/technical-proposal-v2.md
    PYTHONIOENCODING=utf-8 python scripts/build_pdf.py <입력.md> --out <출력이름>

## 왜 새로 만드나

이 기계에는 `pandoc` 도 `weasyprint` 도 `reportlab` 도 없다. 있는 것은 `python-docx` 와
**Word 본체**(COM)다. 그래서 경로를 `md → docx → pdf` 로 잡는다. 기존
`기술제안서_ARGUS.docx` 와 **같은 판형·같은 글꼴**로 낸다.

    용지  21.6 × 27.9 cm      여백  좌우 1.8 · 상 1.3 · 하 1.1 cm
    글꼴  맑은 고딕            본문 9.5pt

## 🔴 마크다운 전부를 지원하지 않는다

이 저장소의 제안서가 실제로 쓰는 것만 받는다 — 제목 3단계 · 문단 · 글머리 · 표 ·
그림 · 인용 · 굵게. 그 밖의 문법은 **글자 그대로 나간다.** 조용히 삼키면 문서에
`**` 가 박힌 채로 제출된다.

## 🔴 Word 를 띄운다

`--no-pdf` 를 주면 docx 까지만 만든다. Word 가 없는 기계에서는 PDF 단계에서 세워
알린다 — 조용히 docx 만 남기면 PDF 가 있는 줄 알고 제출한다.
"""
from __future__ import annotations

import argparse
import pathlib
import re
import sys

import docx
from docx.enum.section import WD_ORIENT                           # noqa: F401
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

sys.stdout.reconfigure(encoding="utf-8")

ROOT = pathlib.Path(__file__).resolve().parents[1]

FONT = "맑은 고딕"
BODY_PT = 9.5
H_PT = {1: 16, 2: 13, 3: 11}
INK = RGBColor(0x00, 0x00, 0x00)
GREY = RGBColor(0x55, 0x55, 0x55)
ACCENT = RGBColor(0x1F, 0x40, 0x6E)

PAGEBREAK = "<!-- pagebreak -->"


def _set_font(run, *, size=BODY_PT, bold=False, color=INK, italic=False):
    run.font.name = FONT
    run.font.size = Pt(size)
    run.bold = bold
    run.italic = italic
    run.font.color.rgb = color
    #: 🔴 한글은 `w:eastAsia` 를 따로 안 박으면 Word 가 기본 글꼴로 되돌린다.
    run._element.rPr.rFonts.set(qn("w:eastAsia"), FONT)


_BOLD = re.compile(r"\*\*(.+?)\*\*")
_CODE = re.compile(r"`([^`]+)`")


def _emit_inline(par, text, *, size=BODY_PT, color=INK, bold_all=False):
    """`**굵게**` 와 `` `코드` `` 만 푼다. 나머지는 글자 그대로 둔다."""
    #: 코드 표시는 굵기를 안 쓰고 회색으로만 구분한다 — 표 안에서 굵은 글씨가 너무 튄다.
    for chunk in re.split(r"(\*\*.+?\*\*|`[^`]+`)", text):
        if not chunk:
            continue
        if chunk.startswith("**") and chunk.endswith("**"):
            _set_font(par.add_run(chunk[2:-2]), size=size, bold=True, color=color)
        elif chunk.startswith("`") and chunk.endswith("`"):
            r = par.add_run(chunk[1:-1])
            _set_font(r, size=size, bold=bold_all, color=color)
            r.font.name = "Consolas"
            r._element.rPr.rFonts.set(qn("w:eastAsia"), FONT)
        else:
            _set_font(par.add_run(chunk), size=size, bold=bold_all, color=color)


def _para(doc, text, *, size=BODY_PT, color=INK, space_after=5, indent=0,
          bold_all=False):
    p = doc.add_paragraph()
    fmt = p.paragraph_format
    fmt.space_after = Pt(space_after)
    fmt.space_before = Pt(0)
    fmt.line_spacing = 1.35
    if indent:
        fmt.left_indent = Cm(indent)
    _emit_inline(p, text, size=size, color=color, bold_all=bold_all)
    return p


def _heading(doc, level, text):
    p = doc.add_paragraph()
    fmt = p.paragraph_format
    fmt.space_before = Pt(16 if level <= 2 else 11)
    fmt.space_after = Pt(6)
    fmt.keep_with_next = True
    _emit_inline(p, text, size=H_PT[level],
                 color=ACCENT if level <= 2 else INK, bold_all=True)
    return p


def _table(doc, rows):
    head, body = rows[0], rows[1:]
    t = doc.add_table(rows=len(rows), cols=len(head))
    t.style = "Table Grid"
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    for j, cell in enumerate(head):
        par = t.cell(0, j).paragraphs[0]
        par.paragraph_format.space_after = Pt(2)
        _emit_inline(par, cell, size=BODY_PT - 0.5, bold_all=True)
    for i, row in enumerate(body, start=1):
        for j in range(len(head)):
            par = t.cell(i, j).paragraphs[0]
            par.paragraph_format.space_after = Pt(2)
            _emit_inline(par, row[j] if j < len(row) else "", size=BODY_PT - 0.5)
    doc.add_paragraph().paragraph_format.space_after = Pt(4)
    return t


def _split_row(line):
    return [c.strip() for c in line.strip().strip("|").split("|")]


def render(md: str, doc) -> None:
    lines = md.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].rstrip()
        stripped = line.strip()

        if stripped == PAGEBREAK:
            doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
            i += 1
            continue

        if not stripped or stripped == "---":
            i += 1
            continue

        m = re.match(r"^(#{1,3})\s+(.*)$", stripped)
        if m:
            _heading(doc, len(m.group(1)), m.group(2))
            i += 1
            continue

        m = re.match(r"^!\[(.*?)\]\((.+?)\)$", stripped)
        if m:
            path = ROOT / m.group(2)
            if not path.exists():
                raise SystemExit("🔴 그림이 없습니다: %s" % path)
            doc.add_picture(str(path), width=Cm(17.9))
            doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
            if m.group(1):
                cap = _para(doc, m.group(1), size=BODY_PT - 1, color=GREY,
                            space_after=10)
                cap.alignment = WD_ALIGN_PARAGRAPH.CENTER
            i += 1
            continue

        #: 표 — 다음 줄이 구분행이어야 표로 읽는다.
        if (stripped.startswith("|") and i + 1 < len(lines)
                and re.match(r"^\|[\s:|-]+\|$", lines[i + 1].strip())):
            rows = [_split_row(stripped)]
            i += 2
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append(_split_row(lines[i].strip()))
                i += 1
            _table(doc, rows)
            continue

        if stripped.startswith("> "):
            _para(doc, stripped[2:], color=GREY, indent=0.5, space_after=5)
            i += 1
            continue

        m = re.match(r"^([-·•])\s+(.*)$", stripped)
        if m:
            _para(doc, "· " + m.group(2), indent=0.45, space_after=3)
            i += 1
            continue

        #: 이어지는 본문 줄은 한 문단으로 잇는다. 마크다운의 줄바꿈 규칙과 같다.
        buf = [stripped]
        i += 1
        while i < len(lines):
            nxt = lines[i].strip()
            if (not nxt or nxt == "---" or nxt.startswith(("#", "|", ">", "- ", "· ", "!["))
                    or nxt == PAGEBREAK):
                break
            buf.append(nxt)
            i += 1
        _para(doc, " ".join(buf))


def build_docx(md_path: pathlib.Path, docx_path: pathlib.Path) -> None:
    doc = docx.Document()
    st = doc.styles["Normal"]
    st.font.name = FONT
    st.font.size = Pt(BODY_PT)
    st.element.rPr.rFonts.set(qn("w:eastAsia"), FONT)

    #: 기존 `기술제안서_ARGUS.docx` 와 같은 판형.
    s = doc.sections[0]
    s.page_width, s.page_height = Cm(21.6), Cm(27.9)
    s.left_margin = s.right_margin = Cm(1.8)
    s.top_margin, s.bottom_margin = Cm(1.3), Cm(1.1)

    render(md_path.read_text(encoding="utf-8"), doc)
    doc.save(str(docx_path))


def to_pdf(docx_path: pathlib.Path, pdf_path: pathlib.Path) -> None:
    """Word 로 굳힌다. 없으면 세워 알린다 — 조용히 넘기면 PDF 가 있는 줄 안다."""
    try:
        import win32com.client
    except ImportError:
        raise SystemExit("🔴 pywin32 가 없어 PDF 로 못 굳힙니다. --no-pdf 로 docx 만 내세요")
    word = None
    try:
        word = win32com.client.DispatchEx("Word.Application")
        word.Visible = False
        d = word.Documents.Open(str(docx_path), ReadOnly=False)
        d.SaveAs(str(pdf_path), FileFormat=17)          # 17 = wdFormatPDF
        d.Close(SaveChanges=0)
    except Exception as exc:                             # noqa: BLE001
        raise SystemExit("🔴 Word 변환 실패: %s" % exc)
    finally:
        if word is not None:
            word.Quit()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("markdown")
    ap.add_argument("--out", help="확장자 없는 출력 이름 (기본: 입력 파일 이름)")
    ap.add_argument("--outdir", default="docs/proposal")
    ap.add_argument("--no-pdf", action="store_true")
    args = ap.parse_args()

    md = ROOT / args.markdown
    if not md.exists():
        raise SystemExit("🔴 입력이 없습니다: %s" % md)
    outdir = ROOT / args.outdir
    outdir.mkdir(parents=True, exist_ok=True)
    stem = args.out or md.stem
    docx_path, pdf_path = outdir / (stem + ".docx"), outdir / (stem + ".pdf")

    build_docx(md, docx_path)
    print("docx  %s (%.0f KB)" % (docx_path, docx_path.stat().st_size / 1024))
    if not args.no_pdf:
        to_pdf(docx_path, pdf_path)
        print("pdf   %s (%.0f KB)" % (pdf_path, pdf_path.stat().st_size / 1024))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
