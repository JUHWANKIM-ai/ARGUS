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

import html as html_lib
import re
from collections import defaultdict
from pathlib import Path

from argus.config import settings
from argus.corpus.dates import parse_date
from argus.corpus.html_doc import extract_rows, read_document
from argus.corpus.manifest import filter_docs

_PARAGRAPH = re.compile(r"(?is)<P\b[^>]*>(.*?)</P>")
_ANY_TAG = re.compile(r"(?s)<[^>]*>")
_WS = re.compile(r"\s+")


def _text(fragment: str) -> str:
    return _WS.sub(" ", html_lib.unescape(_ANY_TAG.sub(" ", fragment))).strip()

# '※ 관련공시' 셀은 '2022-03-28 단일판매ㆍ공급계약체결 ...' 처럼 날짜+서식명이 반복된다.
_DATE = re.compile(r"\d{4}[-.]\d{2}[-.]\d{2}")

# 정정 대상을 가리키는 라벨은 **문서 계통마다 다르다.** 실측:
#
#   거래소(KIND HTML)      '2. 정정관련 공시서류제출일'        표 안, 라벨칸/값칸 분리
#   주요사항·지분(DART XML) '2. 정정대상 공시서류의 최초제출일'  major 116 / holding 21 은
#                                                        표 밖 <P> 단락에 라벨+값이 한 덩어리,
#                                                        major  57 / holding 20 은 표 안
#
# EDA 는 이 값이 <TU> 셀에 있다고 봤으나 실제로는 <P> 단락이거나 평범한 <TD> 였다.
# 그래서 TU/TE 지원만으로는 major·holding 이 붙지 않는다 — 라벨 어휘가 진짜 원인이다.
_SUBMIT_LABEL = re.compile(r"(?:정정관련|정정대상).*제출일")
# 단락형은 라벨과 값이 ' : ' 로 이어져 있다.
_LABEL_VALUE_SPLIT = re.compile(r"[:：]")


def parse_submit_clue(label: str, value: str) -> str | None:
    """'정정 대상 문서의 제출일' 단서를 yyyymmdd 로. 없으면 None.

    라벨과 값이 나뉜 표 형태와, 한 단락에 붙어 있는 형태를 모두 받는다.
    라벨이 맞지 않으면 날짜가 보여도 무시한다 — '정정일자'(정정본 자신의 날짜)나
    '3. 정정사유' 서술문의 날짜를 단서로 쓰면 엉뚱한 문서에 연결된다.
    """
    if not _SUBMIT_LABEL.search(label):
        return None
    # 단락형이면 라벨 문자열 자체에 값이 붙어 있다.
    tail = _LABEL_VALUE_SPLIT.split(label, 1)[1] if _LABEL_VALUE_SPLIT.search(label) else ""
    parsed = parse_date(tail) or parse_date(value)
    return parsed.strftime("%Y%m%d") if parsed else None


def _doc_path(doc: dict) -> Path | None:
    folder = settings().corpus_root / doc["file_path"]
    xml = sorted(folder.glob("*.xml"))
    return xml[0] if xml else None


def _clue_dates(doc: dict) -> list[str]:
    """정정 대상 후보 접수일을 우선순위대로 돌려준다 (yyyymmdd)."""
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

    # 표에 없으면 표 밖 <P> 단락을 본다. major 116건 / holding 21건이 이쪽이다.
    if submit is None:
        for paragraph in _PARAGRAPH.findall(body):
            submit = parse_submit_clue(_text(paragraph), "")
            if submit:
                break

    clues = [submit] if submit else []
    # 관련공시는 최근 것부터 본다. 제출일과 겹치면 건너뛴다.
    clues += [d for d in sorted(set(related), reverse=True) if d != submit]
    return clues


# 정정본의 report_nm 에서 '[기재정정]' 을 떼면 대상 서식명이 그대로 남는다.
_CORRECTION_PREFIX = re.compile(r"^\s*(?:\[[^\]]*\]|\(정정\))\s*")
_FORM_NOISE = re.compile(r"[\s·ㆍ]")


def _form_key(doc: dict) -> str:
    """서식 식별자. 같은 날 여러 건을 낸 기업에서 대상을 가려내는 데 쓴다.

    `주요사항보고서(...)` 는 **괄호 안이 서식 종류**라 통째로 봐야 한다
    (회사합병결정 != 타법인주식양수결정). 반면 거래소공시의 괄호는 계약명 같은
    자유 서술이라 빼야 한다 — 그래서 doc_subtype 을 우선 쓴다.
    """
    name = _FORM_NOISE.sub("", _CORRECTION_PREFIX.sub("", doc.get("report_nm") or ""))
    if name.startswith("주요사항보고서"):
        return name
    return doc.get("doc_subtype") or name.split("(")[0]


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
            form = _form_key(doc)
            for clue in _clue_dates(doc):
                if clue > doc["rcept_dt"]:
                    continue  # 미래 문서를 고칠 수는 없다
                candidates = [
                    other for other in by_date.get(clue, ())
                    if other["rcept_no"] != doc["rcept_no"]
                    # 보고자가 다르면 남의 공시다. 지분공시는 corp_name 이 발행회사라
                    # 같은 날 여러 보고자의 문서가 섞인다.
                    and other.get("flr_nm") == doc.get("flr_nm")
                    # 서식이 다르면 대상이 아니다. 같은 날 여러 건을 낸 기업에서
                    # 날짜만 맞춰 붙이면 엉뚱한 문서가 '대체됨' 으로 뒤집힌다.
                    # 진짜 대상이 코퍼스 밖이면 연결하지 않는 것이 정답이다.
                    and _form_key(other) == form
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
    """체인의 마지막 문서만 is_latest=True 로 둔다.

    접수번호는 시간순으로 증가하므로 [원본, *정정...] 의 마지막이 최신본이다.
    원본과 중간 정정본은 모두 '대체됨(superseded)' 이다 — 검색이 낡은 원본을
    최신으로 착각하면 정정된 금액 대신 옛 금액을 답하게 된다.
    """
    superseded: set[str] = set()
    member_chain: dict[str, list[str]] = {}

    for root, corrections in chains.items():
        sequence = [root, *corrections]
        superseded.update(sequence[:-1])
        for rcept_no in sequence:
            # 체인 어느 지점에서 조회해도 나머지 전부를 볼 수 있어야 한다
            member_chain[rcept_no] = [x for x in sequence if x != rcept_no]

    out = []
    for record in records:
        rcept_no = record["rcept_no"]
        enriched = dict(record)
        enriched["related"] = member_chain.get(rcept_no, [])
        enriched["is_latest"] = rcept_no not in superseded
        out.append(enriched)
    return out
