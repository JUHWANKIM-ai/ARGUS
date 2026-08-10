"""DART 공시 HTML/XML 파싱.

핵심 함정 두 가지.

1. **인코딩** — 파일 바이트는 UTF-8 인데 `<meta charset=euc-kr>` 로 선언되어 있다.
   자동 인코딩 탐지에 맡기면 전부 깨지므로 UTF-8 을 강제한다.

2. **셀 태그가 두 계통이다** — 코퍼스에는 출처가 다른 두 종류의 문서가 섞여 있다.

       거래소공시(exchange)  = KIND HTML  -> 값이 <td>
       주요사항·지분·정기공시  = DART XML   -> 값이 <TE>(추출 셀)·<TU>(코드값 셀)

   `td|th` 만 찾던 파서로 잰 **값 셀 커버리지 실측**은 이렇다.

       exchange  100.0%      major  55.2%      periodic  62.0%      holding  17.2%

   지분공시는 값 칸의 82.8% 가 통째로 안 읽혔다. 라벨(`<TD>`)만 나오고
   금액·주식수(`<TE>`)가 빠지므로, 표는 있는데 값이 없는 상태가 된다.
   그래서 `T[DHUE]` 네 태그를 모두 셀로 인식한다.
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

# 셀은 네 종류다. TD/TH 는 HTML 계통, TU/TE 는 DART XML 계통.
_TR_OPEN = re.compile(r"(?i)<TR\b[^>]*>")
_TR_CLOSE = re.compile(r"(?i)</TR>")
_CELL_OPEN = re.compile(r"(?i)<(T[DHUE])\b[^>]*>")
_CELL_CLOSE = re.compile(r"(?i)</(T[DHUE])>")
_COLSPAN = re.compile(r"(?i)COLSPAN\s*=\s*[\"']?(\d+)")


def read_document(path: Path) -> str:
    """meta 선언을 무시하고 UTF-8 로 읽는다."""
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
    """중첩을 건너뛰고 최상위 요소만 (여는태그시작, 내용시작, 내용끝, 닫는태그끝).

    비탐욕 정규식 `<tr>(.*?)</tr>` 로는 안 된다. 셀 안에 표가 중첩되면
    (지분공시는 표가 문서당 p50 50개다) 바깥 행이 **안쪽 `</TR>` 에서 잘려**
    라벨과 값이 서로 다른 행으로 흩어진다.
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
    """한 행의 셀 텍스트. `expand_colspan` 이면 병합 폭만큼 빈 칸을 채운다.

    COLSPAN 을 안 펼치면 병합된 셀 수만큼 이후 값이 왼쪽으로 밀린다. 삼성전자
    매출 표의 `총 계` 행이 `COLSPAN=2` 라서, 안 펼치면 매출액 `3,336,059` 가
    '주요 제품' 열에 들어간다. 열 정렬이 깨지면 라벨-값 매핑이 통째로 틀린다.
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
    """표 -> 라벨/값 행들. **빈 칸은 버린다.**

    소비자(`normalize`·`corrections`)가 `row[:-1]` 을 라벨, `row[-1]` 을 값으로
    읽는 계약이라 빈 칸이 끼면 값 자리가 `''` 로 밀린다. 열 정렬이 필요한
    쪽은 `extract_grid` 를 쓴다.
    """
    return [cells for row in _iter_rows(html, expand_colspan=False)
            if (cells := [c for c in row if c])]


def extract_grid(html: str) -> list[list[str]]:
    """표 -> 격자. **빈 칸과 COLSPAN 을 보존**해 열 위치를 유지한다.

    재무 표처럼 '몇 번째 열인가' 가 의미를 갖는 표를 읽을 때 쓴다.
    """
    return [row for row in _iter_rows(html, expand_colspan=True) if any(row)]
