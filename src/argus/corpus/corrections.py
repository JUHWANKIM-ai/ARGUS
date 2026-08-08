"""정정공시 <-> 원본 연결.

단서는 두 가지다.
  1순위 '2. 정정관련 공시서류제출일' — 이 정정이 무엇을 고치는지 직접 가리킨다.
  2순위 문서 말미의 '※ 관련공시' — 같은 사안의 선행 공시 목록(날짜 + 서식명).

계획서 원안은 1순위 단서를 '원본(is_correction=False)' 에만 맞췄고 연결률이 24.6%
에 그쳤다. 실측으로 드러난 두 가지 이유:

  - **정정의 정정이 흔하다.** 정정 공시가 원본이 아니라 직전 정정을 가리키는
    경우가 많아, 원본만 후보로 두면 사슬이 끊긴다. 정정 문서도 후보에 넣으면
    사정권 내 미연결이 189건 -> 13건으로 줄었다.
  - **원본이 코퍼스 밖이다.** 코퍼스는 2023-01-02 부터인데 정정 631건 중 287건
    (45.5%)이 2022년 이전 공시를 가리킨다. 이건 코드로 못 넘는 데이터 상한이라
    실질 최대 연결률은 54.5% 다.

그래서 후보를 '같은 기업의 선행 공시(정정 포함)' 로 넓히고 두 단서를 함께 쓴다.
연결률 비교는 다음과 같다.

    제출일 -> 원본만 (계획서 원안)      24.6%
    관련공시 -> 원본만                  27.1%
    제출일 -> 선행 공시(정정 포함)      52.5%
    제출일 + 관련공시 -> 선행 공시      61.2%   <- 채택

체인은 뿌리 문서(코퍼스 안에 원본이 있으면 그 원본, 없으면 가장 이른 정정)를
키로 삼아 같은 사안의 정정을 모두 매단다.
"""
from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

from argus.config import settings
from argus.corpus.dates import parse_date
from argus.corpus.html_doc import extract_rows, read_document
from argus.corpus.manifest import filter_docs

# '※ 관련공시' 셀은 '2022-03-28 단일판매ㆍ공급계약체결 ...' 처럼 날짜+서식명이 반복된다.
_DATE = re.compile(r"\d{4}[-.]\d{2}[-.]\d{2}")


def _doc_path(doc: dict) -> Path | None:
    folder = settings().corpus_root / doc["file_path"]
    xml = sorted(folder.glob("*.xml"))
    return xml[0] if xml else None


def _clue_dates(doc: dict) -> list[str]:
    """정정 대상 후보 접수일을 우선순위대로 돌려준다 (yyyymmdd)."""
    path = _doc_path(doc)
    if not path:
        return []

    submit: str | None = None
    related: list[str] = []
    for row in extract_rows(read_document(path)):
        joined = " ".join(row)
        if submit is None and "정정관련" in joined and "제출일" in joined:
            parsed = parse_date(row[-1])
            if parsed:
                submit = parsed.strftime("%Y%m%d")
        if "관련공시" in joined:
            related = [m.replace("-", "").replace(".", "") for m in _DATE.findall(row[-1])]

    clues = [submit] if submit else []
    # 관련공시는 최근 것부터 본다. 제출일과 겹치면 건너뛴다.
    clues += [d for d in sorted(set(related), reverse=True) if d != submit]
    return clues


def _original_submit_date(doc: dict) -> str | None:
    """'정정관련 공시서류제출일' 단독 조회 (진단·호환용)."""
    clues = _clue_dates(doc)
    return clues[0] if clues else None


def build_chains(doc_group: str = "exchange") -> dict[str, list[str]]:
    docs = filter_docs(doc_group=doc_group)
    by_corp: dict[str, list[dict]] = defaultdict(list)
    for doc in docs:
        by_corp[doc["corp_name"]].append(doc)

    # 정정 -> 직전 대상 문서
    parent: dict[str, str] = {}
    for corp_docs in by_corp.values():
        by_date: dict[str, list[dict]] = defaultdict(list)
        for doc in corp_docs:
            by_date[doc["rcept_dt"]].append(doc)

        for doc in corp_docs:
            if not doc["is_correction"]:
                continue
            for clue in _clue_dates(doc):
                if clue > doc["rcept_dt"]:
                    continue  # 미래 문서를 고칠 수는 없다
                candidates = [
                    other for other in by_date.get(clue, ())
                    if other["rcept_no"] != doc["rcept_no"]
                ]
                if not candidates:
                    continue
                # 원본을 정정보다 먼저 고른다
                candidates.sort(key=lambda o: (o["is_correction"], o["rcept_no"]))
                parent[doc["rcept_no"]] = candidates[0]["rcept_no"]
                break

    def root_of(rcept_no: str) -> str:
        seen = {rcept_no}
        current = rcept_no
        while current in parent:
            nxt = parent[current]
            if nxt in seen:      # 같은 날 상호 참조 등 순환 방지
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


def attach_related(records: list[dict], chains: dict[str, list[str]]) -> list[dict]:
    corrected = {c for v in chains.values() for c in v}
    out = []
    for record in records:
        rcept_no = record["rcept_no"]
        enriched = dict(record)
        enriched["related"] = list(chains.get(rcept_no, []))
        enriched["is_latest"] = rcept_no not in corrected
        out.append(enriched)
    return out
