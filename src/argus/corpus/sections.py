"""정기공시(사업·반기·분기보고서) 목차 섹션 청킹 — CONVENTION §7.3 / 04-retriever D-2.

DART 원문 XML(dart4.xsd)은 목차가 이미 마크업되어 있다.

    <SECTION-1><TITLE ATOC="Y" ATOCID="9">II. 사업의 내용</TITLE>
      <SECTION-2><TITLE ATOC="Y" ATOCID="11">2. 주요 제품 및 서비스</TITLE> ... </SECTION-2>

`ATOC="Y"` 가 목차 등재 표시다. 따라서 청킹은 추론이 아니라 **마크업을 그대로 따라가면**
된다. SECTION-n 중첩 깊이가 계층, TITLE 텍스트가 목차 항목이다.

설계 결정 세 가지 (근거는 docs/pipeline/04-retriever.md D-2):

1. 한 섹션의 `text` 는 **자기 소유 구간만** 담는다(하위 섹션 제외). 상위 목차는
   `path` 로만 남는다. 그래야 같은 문장이 여러 청크에 중복되지 않는다.
2. 본문은 `periodic_sections.txt` 블롭에 모으고 jsonl 에는 **바이트 오프셋만** 둔다.
   전량 인라인은 인덱스가 700MB 를 넘어 배포 서버(2.8GiB) 에서 상주시킬 수 없다.
3. `n_chars < 100` 스텁도 인덱스에 넣되 `is_stub` 로 표시한다. §7.7 `list_sections`
   명세가 이걸 배제 기준으로 쓰므로 값 자체는 보존해야 판정을 재현할 수 있다.
"""
from __future__ import annotations

import html as html_lib
import json
import re
import sys
from collections.abc import Iterator
from functools import lru_cache
from pathlib import Path

from argus.config import settings
from argus.corpus.html_doc import read_document

INDEX_NAME = "periodic_sections.jsonl"
BLOB_NAME = "periodic_sections.txt"

# 주최 측 list_sections 명세의 '기재 생략' 스텁 배제 기준
STUB_CHARS = 100

# --- 원문 태그 ---------------------------------------------------------------
_TOC = re.compile(r"(?is)<(TITLE|COVER-TITLE)\b([^>]*)>(.*?)</\1>")
_ATOC_Y = re.compile(r"(?i)ATOC\s*=\s*[\"']Y[\"']")
_SEC_OPEN = re.compile(r"(?i)<SECTION-(\d)\b[^>]*>")
_SEC_CLOSE = re.compile(r"(?i)</SECTION-(\d)>")
_TABLE_OPEN = re.compile(r"(?i)<TABLE\b(?!-)[^>]*>")
_TABLE_CLOSE = re.compile(r"(?i)</TABLE>")
_TR_OPEN = re.compile(r"(?i)<TR\b[^>]*>")
_TR_CLOSE = re.compile(r"(?i)</TR>")
_CELL_OPEN = re.compile(r"(?i)<(T[DHUE])\b[^>]*>")
_CELL_CLOSE = re.compile(r"(?i)</(T[DHUE])>")
_COLSPAN = re.compile(r"(?i)COLSPAN\s*=\s*[\"']?(\d+)")
_LAYOUT_TABLE = re.compile(r"(?i)BORDER\s*=\s*[\"']?0|CLASS\s*=\s*[\"']nb[\"']")
_DOC_NAME = re.compile(r"(?is)<DOCUMENT-NAME[^>]*>(.*?)</DOCUMENT-NAME>")

# 인라인 굵은 글씨(USERMARK 의 B 플래그)는 사실상 소제목이다. 줄바꿈 없이 본문과
# 붙어 있어서, 그냥 태그를 지우면 '가. 주요 제품 매출당사는 TV...' 로 엉겨 붙는다.
_BOLD_SPAN = re.compile(r"(?is)<SPAN\b[^>]*USERMARK=\"[^\"]*\bB\b[^\"]*\"[^>]*>(.*?)</SPAN>")
_BLOCK_TAG = re.compile(r"(?i)</?(P|BR|PGBRK|TITLE|LI|DIV|TR)\b[^>]*/?>")
_ANY_TAG = re.compile(r"(?s)<[^>]*>")
_COMMENT = re.compile(r"(?s)<!--.*?-->")
_STYLE = re.compile(r"(?is)<(style|script)[^>]*>.*?</\1>")
_INTERTAG_WS = re.compile(r">\s+<")
_SPACES = re.compile(r"[ \t 　]+")
_BLANKS = re.compile(r"\n{3,}")

# 대체 수집본(pdf+html) 의 목차 앵커
_HTML_ANCHOR = re.compile(r"(?is)<A\s+name=['\"]toc\d+['\"][^>]*>(.*?)</A>")
_ROMAN = re.compile(r"^(?:[IVXLC]+)\.\s")
_NUM2 = re.compile(r"^(\d+)\.\s")
_NUM3 = re.compile(r"^(\d+)-(\d+)\.\s")


# --- 텍스트화 ---------------------------------------------------------------
def plain_text(fragment: str) -> str:
    """태그를 걷어내되 SPAN 분절로 끊긴 단어를 도로 붙인다.

    원문은 색·굵기가 바뀔 때마다 SPAN 이 갈리고 그 사이에 개행이 들어간다.
    태그를 공백으로 바꾸면 '스마트폰 은 전년 대비' 처럼 조사가 떨어져 나온다.
    태그 사이 공백을 먼저 없앤 뒤 태그를 지우면 원래 문장으로 복원된다.
    """
    s = _COMMENT.sub("", _STYLE.sub(" ", fragment))
    s = _INTERTAG_WS.sub("><", s)
    s = _BOLD_SPAN.sub(lambda m: "\n" + m.group(1) + "\n", s)
    s = _BLOCK_TAG.sub("\n", s)
    s = _ANY_TAG.sub("", s)
    s = html_lib.unescape(s)
    s = _SPACES.sub(" ", s)
    s = "\n".join(line.strip() for line in s.split("\n"))
    return _BLANKS.sub("\n\n", s).strip()


def _top_level(text: str, open_re: re.Pattern,
               close_re: re.Pattern) -> list[tuple[int, int, int, int]]:
    """중첩을 건너뛰고 최상위 요소만 (여는태그시작, 내용시작, 내용끝, 닫는태그끝)."""
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


def _row_cells(row: str) -> list[str]:
    """COLSPAN 을 빈 칸으로 펼친다.

    펼치지 않으면 병합 셀만큼 값이 왼쪽으로 밀린다. 삼성전자 매출 표의 '총 계'
    행이 COLSPAN=2 라서, 안 펼치면 매출액 3,336,059 가 '주요 제품' 열에 들어간다.
    재무 수치가 통째로 어긋나므로 이 처리는 선택이 아니다.
    """
    cells: list[str] = []
    for tag_start, inner_start, inner_end, _ in _top_level(row, _CELL_OPEN, _CELL_CLOSE):
        span = _COLSPAN.search(row[tag_start:inner_start])
        cells.append(row[inner_start:inner_end])
        if span:
            cells.extend([""] * (max(1, int(span.group(1))) - 1))
    return cells


def _cell_text(raw: str) -> str:
    return plain_text(raw).replace("\n", " ").replace("|", r"\|").strip()


def render(fragment: str) -> str:
    """구간 -> 마크다운. 표는 파이프 테이블로 인라인한다 (CONVENTION §7.3)."""
    parts: list[str] = []
    cursor = 0
    for tag_start, inner_start, inner_end, close_end in _top_level(
            fragment, _TABLE_OPEN, _TABLE_CLOSE):
        head = plain_text(fragment[cursor:tag_start])
        if head:
            parts.append(head)
        block = _render_table(fragment[tag_start:inner_start], fragment[inner_start:inner_end])
        if block:
            parts.append(block)
        cursor = close_end
    tail = plain_text(fragment[cursor:])
    if tail:
        parts.append(tail)
    return "\n\n".join(parts)


def _render_table(open_tag: str, inner: str) -> str:
    rows = [_row_cells(inner[s:e]) for _, s, e, _ in _top_level(inner, _TR_OPEN, _TR_CLOSE)]
    rows = [r for r in rows if r]
    if not rows:
        return plain_text(inner)

    # BORDER=0 / class=nb 는 단위 표기·주석을 얹는 레이아웃 상자다. 표가 아니라
    # 문장으로 풀어야 '(단위 : 억원, %)' 같은 표기가 본문에 남는다 (§7.7 read_section).
    nested = any(_TABLE_OPEN.search(c) for row in rows for c in row)
    if nested or _LAYOUT_TABLE.search(open_tag) or max(len(r) for r in rows) < 2:
        blocks = [render(cell) for row in rows for cell in row]
        return "\n\n".join(b for b in blocks if b)

    width = max(len(r) for r in rows)
    grid = [[_cell_text(c) for c in row] + [""] * (width - len(row)) for row in rows]
    lines = ["| " + " | ".join(grid[0]) + " |", "|" + "---|" * width]
    lines += ["| " + " | ".join(row) + " |" for row in grid[1:]]
    return "\n".join(lines)


def count_tables(fragment: str) -> int:
    """§7.3 의 n_tables. 슬라이드 예시(II-2 = 3)와 맞추려면 TABLE 요소 전수다."""
    return len(_TABLE_OPEN.findall(fragment))


# --- 목차 파싱 ---------------------------------------------------------------
def parse_toc(xml: str) -> list[dict]:
    """`ATOC="Y"` 목차 항목 -> [{level, title, path, start, end, has_children}].

    level 은 SECTION-n 중첩 깊이다. 한 SECTION 안에 TITLE 이 여러 개면(연결재무제표
    주석의 주석 1~34 가 그렇다) 두 번째부터는 한 단계 아래 소제목으로 본다.
    """
    events: list[tuple[int, int, re.Match]] = []
    events += [(m.start(), 0, m) for m in _SEC_OPEN.finditer(xml)]
    events += [(m.start(), 1, m) for m in _SEC_CLOSE.finditer(xml)]
    events += [(m.start(), 2, m) for m in _TOC.finditer(xml)]
    events.sort(key=lambda ev: (ev[0], ev[1]))

    raw: list[dict] = []
    depth = 0
    titles_here = [0]
    for _, kind, match in events:
        if kind == 0:
            depth += 1
            titles_here.append(0)
        elif kind == 1:
            depth = max(0, depth - 1)
            if len(titles_here) > 1:
                titles_here.pop()
        else:
            if not _ATOC_Y.search(match.group(2)):
                continue
            title = _SPACES.sub(" ", plain_text(match.group(3)).replace("\n", " ")).strip()
            if not title:
                continue
            level = max(1, depth) + (1 if titles_here[-1] else 0)
            titles_here[-1] += 1
            raw.append({"level": level, "title": title,
                        "start": match.end(), "head": match.start()})
    return _finish(raw, len(xml))


def _finish(raw: list[dict], doc_end: int) -> list[dict]:
    """구간 끝을 채우고 path 를 세운다. 구간은 '다음 목차 항목 직전' 까지다."""
    for i, node in enumerate(raw):
        node["end"] = raw[i + 1]["head"] if i + 1 < len(raw) else doc_end
        node["has_children"] = bool(i + 1 < len(raw) and raw[i + 1]["level"] > node["level"])
    stack: list[tuple[int, str]] = []
    for node in raw:
        while stack and stack[-1][0] >= node["level"]:
            stack.pop()
        node["path"] = " > ".join([t for _, t in stack] + [node["title"]])
        stack.append((node["level"], node["title"]))
        node.pop("head", None)
    return raw


def parse_toc_html(html: str) -> list[dict]:
    """대체 수집본(pdf+html) 의 공시뷰어 HTML.

    SECTION 마크업이 없고 `<A name='tocN'>` 앵커만 있다. 레벨은 제목 표기로
    되짚는다 — 'II.' 는 1단, '2.' 는 2단, '2-1.' 은 3단. 번호가 되감기면
    (…'3. 연결재무제표 주석' 다음에 '1. 일반사항') 하위 목록으로 내려간 것이다.
    """
    raw: list[dict] = []
    last_num = 0
    for match in _HTML_ANCHOR.finditer(html):
        title = _SPACES.sub(" ", plain_text(match.group(1)).replace("\n", " ")).strip()
        if not title:
            continue
        if _ROMAN.match(title) or title.startswith("【") or " 보 고 서" in title:
            level, last_num = 1, 0
        elif _NUM3.match(title):
            level = 3
        elif (num := _NUM2.match(title)) is not None:
            value = int(num.group(1))
            level = 3 if value <= last_num else 2
            if level == 2:
                last_num = value
        else:
            level = 2
        raw.append({"level": level, "title": title,
                    "start": match.end(), "head": match.start()})
    return _finish(raw, len(html))


# --- 문서 -> 레코드 -----------------------------------------------------------
def document_files(doc: dict) -> list[tuple[Path, str]]:
    """(경로, 경로 접두어). 본문이 먼저, 감사보고서 첨부가 뒤.

    첨부 두 개(감사보고서/연결감사보고서)는 목차 제목이 '독립된 감사인의
    감사보고서' 로 동일해서 그냥 합치면 path 가 충돌한다. DOCUMENT-NAME 을
    접두어로 붙여 구분한다.
    """
    folder = settings().corpus_root / doc["file_path"]
    main = folder / f"{doc['rcept_no']}.xml"
    out: list[tuple[Path, str]] = []
    if main.exists():
        out.append((main, ""))
    else:
        viewer = folder / f"{doc['rcept_no']}_viewer.html"
        if viewer.exists():
            out.append((viewer, ""))
    for extra in sorted(folder.glob(f"{doc['rcept_no']}_*.xml")):
        head = extra.open(encoding="utf-8", errors="replace").read(4000)
        name = _DOC_NAME.search(head)
        out.append((extra, f"[{name.group(1).strip() if name else extra.stem}] "))
    return out


def iter_doc_sections(doc: dict) -> Iterator[dict]:
    """manifest 한 행 -> §7.3 청크 레코드들 (`text` 포함)."""
    index = 0
    for path, prefix in document_files(doc):
        body = read_document(path)
        nodes = parse_toc_html(body) if path.suffix == ".html" else parse_toc(body)
        for node in nodes:
            fragment = body[node["start"]:node["end"]]
            rendered = render(fragment)
            text = f"{node['title']}\n\n{rendered}" if rendered else node["title"]
            yield {
                "chunk_id": f"{doc['doc_id']}#{index:03d}",
                "doc_id": doc["doc_id"],
                "corp_code": doc["corp_code"],
                "corp_name": doc["corp_name"],
                "sector": doc["sector"],
                "doc_subtype": doc["doc_subtype"],
                "base_year": doc["base_year"],
                "base_month": doc["base_month"],
                "rcept_dt": doc["rcept_dt"],
                "is_correction": bool(doc["is_correction"]),
                "path": prefix + node["path"],
                "level": node["level"],
                "n_chars": len(text),
                "n_tables": count_tables(fragment),
                "is_stub": len(text) < STUB_CHARS,
                "is_container": node["has_children"],
                "text": text,
            }
            index += 1


# --- 인덱스 조회 ---------------------------------------------------------------
# 반복되는 문자열은 인턴한다. path 는 11.2만 건 중 고유값이 8,636개뿐이라
# (목차 서식이 전 기업 공통) 인턴만으로 상주 225MB -> 174MB 로 줄어든다.
_REPEATED = ("doc_id", "corp_code", "corp_name", "sector", "doc_subtype", "rcept_dt", "path")


@lru_cache(maxsize=1)
def load_index() -> tuple[dict, ...]:
    path = settings().artifacts_root / INDEX_NAME
    if not path.exists():
        return ()
    records = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            record = json.loads(line)
            for key in _REPEATED:
                record[key] = sys.intern(record[key])
            records.append(record)
    return tuple(records)


@lru_cache(maxsize=1)
def _by_doc() -> dict[str, tuple[dict, ...]]:
    grouped: dict[str, list[dict]] = {}
    for record in load_index():
        grouped.setdefault(record["doc_id"], []).append(record)
    return {k: tuple(v) for k, v in grouped.items()}


def section_text(record: dict) -> str:
    """본문을 지연 로딩한다. 인라인 `text` 가 있으면 그대로 쓴다."""
    if "text" in record:
        return record["text"]
    offset, length = record.get("text_offset"), record.get("text_bytes")
    if offset is None or length is None:
        return ""
    with (settings().artifacts_root / BLOB_NAME).open("rb") as fh:
        fh.seek(offset)
        return fh.read(length).decode("utf-8")


def list_sections(doc_id: str, include_stubs: bool = False) -> list[dict]:
    """§7.7 ④ — 목차 `path` + 글자수. 기본은 100자 미만 스텁을 뺀다."""
    records = _by_doc().get(doc_id, ())
    return [
        {k: r[k] for k in ("chunk_id", "path", "level", "n_chars", "n_tables", "is_stub")}
        for r in records
        if include_stubs or not r["is_stub"]
    ]


def read_section(doc_id: str, path: str) -> dict | None:
    """§7.7 ⑤ — 섹션 원문(표=마크다운).

    상위 목차(예: 'II. 사업의 내용')로 부르면 하위 섹션을 순서대로 이어 붙인다.
    목차 계층이 그대로 parent-child 구조라서 별도 인덱스가 필요 없다.
    """
    records = _by_doc().get(doc_id, ())
    wanted = path.strip()
    exact = [r for r in records if r["path"] == wanted]
    children = [r for r in records if r["path"].startswith(wanted + " > ")]
    hit = exact + children
    if not hit:
        return None
    body = "\n\n".join(t for t in (section_text(r) for r in hit) if t)
    head = exact[0] if exact else children[0]
    return {
        "doc_id": doc_id,
        "chunk_id": head["chunk_id"],
        "path": wanted,
        "corp_name": head["corp_name"],
        "rcept_dt": head["rcept_dt"],
        "doc_subtype": head["doc_subtype"],
        "base_year": head["base_year"],
        "base_month": head["base_month"],
        "n_sections": len(hit),
        "n_chars": len(body),
        "n_tables": sum(r["n_tables"] for r in hit),
        "text": body,
    }
