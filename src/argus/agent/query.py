"""질의어 추출. 공시 보일러플레이트를 걷어내 변별력 있는 어휘만 남긴다."""
from __future__ import annotations

import re

# 전 문서에 공통으로 등장해 변별력이 없는 어휘 (실측 기반)
STOPWORDS = {
    "공급계약", "단일판매", "판매공급계약", "계약금액", "체결", "해지",
    "매출액대비", "매출액", "최근매출액", "계약상대", "계약기간",
    "주요경영사항", "투자판단", "신규시설투자", "공시", "보고서",
    "얼마", "무엇", "어디", "언제", "누구", "인가", "인지", "이며",
    "얼마인가", "정리해줘", "설명해줘", "알려줘", "존재하는가",
}
_TOKEN = re.compile(r"[0-9]+(?:[,.][0-9]+)*|[가-힣]+|[A-Za-z]+")
_JOSA = re.compile(r"(이|가|은|는|을|를|의|에|에서|으로|로|와|과|도|만|이라|라)$")


def _strip_josa(word: str) -> str:
    if len(word) <= 2:
        return word
    return _JOSA.sub("", word)


def extract_terms(question: str) -> list[str]:
    terms: list[str] = []
    for raw in _TOKEN.findall(question or ""):
        word = _strip_josa(raw)
        if len(word) < 2:
            continue
        if word in STOPWORDS:
            continue
        # 불용어가 접미로 붙은 복합어도 제거 ('의공급계약' 같은 잔재)
        if any(word.endswith(s) and word != s for s in STOPWORDS if len(s) >= 4):
            continue
        if word not in terms:
            terms.append(word)
    return terms
