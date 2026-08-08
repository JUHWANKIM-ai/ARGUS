"""DART 공시 HTML 파싱.

핵심 함정: 파일 바이트는 UTF-8 인데 <meta charset=euc-kr> 로 선언되어 있다.
자동 인코딩 탐지에 맡기면 전부 깨지므로 UTF-8 을 강제한다.
"""
from __future__ import annotations

import html as html_lib
import re
from pathlib import Path

_STYLE = re.compile(r"(?is)<style[^>]*>.*?</style>")
_SCRIPT = re.compile(r"(?is)<script[^>]*>.*?</script>")
_TAG = re.compile(r"(?s)<[^>]*>")
_TITLE = re.compile(r"(?is)<title>(.*?)</title>")
_TR = re.compile(r"(?is)<tr[^>]*>(.*?)</tr>")
_TD = re.compile(r"(?is)<t[dh][^>]*>(.*?)</t[dh]>")
_WS = re.compile(r"\s+")


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


def extract_rows(html: str) -> list[list[str]]:
    body = _SCRIPT.sub(" ", _STYLE.sub(" ", html))
    rows: list[list[str]] = []
    for tr in _TR.findall(body):
        cells = [_clean(td) for td in _TD.findall(tr)]
        cells = [c for c in cells if c]
        if cells:
            rows.append(cells)
    return rows
