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

정정이 아닌 관계 하나를 여기서 함께 낸다 — **해지 -> 원계약**(`terminates`).
해지 공시는 `is_correction=False` 라 위 루프에 아예 들어오지 않으므로 `related` 가
영원히 빈다(실측 해지 20건 중 0건). 사유와 설계는 `attach_related` 독스트링에 있다.
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


def _drop_cycles(parent: dict[str, str]) -> list[tuple[str, str]]:
    """`정정 -> 대상` 간선에서 **순환을 끊는다.** 끊은 간선을 돌려준다 (진단용).

    같은 날 낸 정정 두 건이 서로를 `※ 관련공시` 에 적는 일이 실재한다. 그러면
    `parent[A]=B` 와 `parent[B]=A` 가 동시에 서고, 이 파일이 만드는 자료구조 두 개가
    조용히 깨진다 (실측 exchange 2쌍 · 4건):

      ① `attach_related` 의 `superseded` 가 **두 사슬의 합집합**이 된다. 뿌리가 둘이라
         `[A,B]` 와 `[B,A]` 를 각각 매달고 각자 마지막 1건만 빼므로 A·B 가 **둘 다**
         `is_latest=False` 로 떨어진다. 체인에 최신본이 **한 건도 없다** — 그 두 건은
         `query_events(is_latest=True)` 와 BM25 `is_latest` 필터에서 **통째로 사라진다**
         (삼성중공업 20240612800459 · 20240612800468).
      ② `member_chain` 이 **덮인다.** 4본 사슬 `[1320, 1290, 20240712800050,
         20240712800053]` 을 매단 뒤 뿌리가 하나 더 돌아 `[1290, 1320]` 로 덮어써서
         1320 의 `related` 가 3건 -> 1건으로 줄었다 (현대로템 20240514801320).
         dict 순회 순서에 딸린 값이라 재빌드마다 달라질 수 있었다.

    🔴 **순환 구성원 중 하나를 뿌리로 골라 나머지를 매다는 방식은 채택하지 않았다.**
    그렇게 하면 사슬은 서고 `is_latest` 도 한 건이 되지만, **상호 참조를 근거로 한 건을
    `대체됨` 으로 단정**하게 된다. 실측 삼성중공업 두 건은 서로의 정정이 아니다 —
    `정정관련 공시서류제출일` 이 각각 2021-10-18 · 2020-11-23 로 **다른 계약**을 가리키고
    (둘 다 코퍼스 밖), 같은 날 형제를 `※ 관련공시` 에 적었을 뿐이다. 하나를 대체됨으로
    만들면 그 건이 다시 검색에서 사라진다. 이 파일의 기존 원칙과도 같다 —
    "진짜 대상이 코퍼스 밖이면 **연결하지 않는 것이 정답**이다"(`_form_key` 주석).

    그래서 **순환에 든 간선은 전부 버린다.** 단서가 대상을 특정하지 못한 것이므로
    연결하지 않는다. 사슬 밖 간선(현대로템 1320 -> 20240712800050·053)은 그대로 산다.

    ⚠️ 이 함수는 `artifacts/*_records.jsonl` 을 **다시 빌드할 때** 효력이 생긴다.
    재빌드하면 `is_latest` 가 3건 바뀌고(459·468·20240514801290 이 False -> True)
    BM25 메타가 달라진다 — v2 측정과의 대응이 깨지므로 재빌드 시점은 별도 판단이다.
    """
    dropped: list[tuple[str, str]] = []
    #: None = 미방문 · 0 = 이번 경로에서 진행 중 · 1 = 순환 없음 확정
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

    # 상호 참조로 생긴 순환을 먼저 끊는다. 이게 없으면 사슬 하나가 뿌리 둘로 갈려
    # `is_latest` 가 한 건도 없는 체인이 나온다 (`_drop_cycles` 참조).
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


#: 해지 공시의 `doc_subtype`. 실측 exchange 20건 전부 이 한 종류다 (major 의
#: `주요사항보고서(자기주식취득신탁계약해지결정)` 는 계약 해지가 아니라 신탁 해지라 뺀다).
TERMINATION_SUBTYPES = ("단일판매공급계약해지",)

#: `※ 관련공시` 라벨. `references._is_reference_label` 의 **레코드 쪽 쌍둥이**다.
#: 그쪽은 원문 XML 셀을 보고 이쪽은 정규화된 `raw_rows` 를 본다 — 레코드에는
#: `report_nm` 이 없어 서식 대조도 `doc_subtype` 으로 해야 하므로 함수를 나눠 둔다.
#: 부분일치로 '관련공시' 를 찾으면 정정공시의 `1. 정정관련 공시서류` 가 같이 걸린다.
def _is_reference_label(cell: str) -> bool:
    packed = _WS.sub("", cell or "")
    return "관련공시" in packed and "정정관련공시" not in packed


def _reference_cells(record: dict) -> list[str]:
    """레코드의 `raw_rows` 에서 `※ 관련공시` 값 칸들. 원문 재파싱 0회."""
    cells: list[str] = []
    for row in record.get("raw_rows") or ():
        if len(row) >= 2 and any(_is_reference_label(cell) for cell in row[:-1]):
            cells.append(row[-1])
    return cells


#: 원문 표기와 `doc_subtype` 은 조금씩 다르다('단일판매ㆍ공급계약체결' vs
#: '단일판매공급계약체결'). 잡음을 지우고 앞부분만 맞으면 같은 서식으로 본다.
_FORM_HEAD = 8


def _form_agrees(stated: str, doc_subtype: str) -> bool:
    left = _FORM_NOISE.sub("", _CORRECTION_PREFIX.sub("", stated or ""))
    right = _FORM_NOISE.sub("", doc_subtype or "")
    if not left or not right:
        return False
    return left[:_FORM_HEAD] in right or right[:_FORM_HEAD] in left


def _termination_links(records: list[dict],
                       member_chain: dict[str, list[str]]) -> dict[str, list[str]]:
    """해지 공시 -> 원계약 (+ 그 원계약의 정정 체인). 순방향만. LLM 0회 · 원문 재파싱 0회.

    실측(exchange 20건): `related` 로 이어진 해지는 **0건**이고 `※ 관련공시` 에 원계약을
    적어 둔 것이 11건이다. 나머지 9건은 코드로 넘을 수 없다 — 8건이 `before_corpus`
    (2015~2022년 원계약)이고 1건이 그것과 `not_found` 의 혼합이다.

    원계약의 **정정 체인까지 함께** 붙인다. 해지가 가리키는 것은 원계약 접수번호인데
    그 문서가 이미 대체본인 경우가 실재하고(두산퓨얼셀 20250331802527 -> 20240930800764,
    `is_latest=False`), 원계약 한 건만 주면 정정 전 금액을 답하게 된다.
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
                    # 서식이 안 맞으면 '같은 날 같은 기업' 만으로 붙은 약한 엣지다.
                    # `references.form_matched=False` 와 같은 문턱이다 — 근거로 끌어오면
                    # 조회공시 요구·풍문해명이 원계약으로 둔갑한다.
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
    """체인의 마지막 문서만 is_latest=True 로 둔다. `terminates` 를 함께 채운다.

    접수번호는 시간순으로 증가하므로 [원본, *정정...] 의 마지막이 최신본이다.
    원본과 중간 정정본은 모두 '대체됨(superseded)' 이다 — 검색이 낡은 원본을
    최신으로 착각하면 정정된 금액 대신 옛 금액을 답하게 된다.

    🔴 **해지 -> 원계약을 `related` 에 넣지 않았다.** 넣는 것이 한 줄이라 먼저 그렇게
    했다가 되돌렸다. `related` 는 소비자 쪽에서 **정정 체인**으로 읽힌다:
    `tools.get_history` 가 정정이 아닌 멤버에 `kind="related"` 를 달고,
    `pipeline._chain_note` 는 `followup` 만 예외 처리하므로 그 멤버를 접수일 순서대로
    `"정정체인 1/2 (원공시 · 대체됨)"` 으로 찍는다. 즉 **원계약 금액을 '대체된 값' 이라고
    근거에 명시**하고 해지 공시를 `"2/2 (최신본)"` 으로 올린다. Q-066 의 v2 A8 오답이
    정확히 그 값이다 — 원계약 351,882,914,000 대신 **해지금액 362,307,038,000** 을 답했다.
    되레 오답을 굳히는 처방이므로 필드를 나눈다.

    🔴 **`※ 관련공시` 순방향 전체를 붙이는 안도 기각했다.** 강한 엣지(`resolved` +
    `form_matched`)의 출처 문서가 **453건**이고 그중 해지는 11건뿐이다 — 나머지 442건
    (계약체결 317 · 투자판단 121 · 신규시설투자 4)은 정정도 해지도 아닌 '같은 사안의
    선행 공시' 라서, 붙이는 순간 `_sufficiency` 의 완결성 축과 체인 hop 이 442문서에서
    새로 발화한다. `pipeline` 이 못박아 둔 제동 셋의 전제("과잉 탐색이 진짜 위험이다")를
    무료로 재측정할 방법이 없다. 겨냥한 관계만 붙인다.

    ⚠️ `terminates` 는 아직 **소비자가 없다.** 인덱스에 값을 실어 두는 것까지가 이 파일의
    몫이고, 런타임에서 쓰려면 `tools.chain_probe`/`get_history` 가 이 필드를 읽고
    `pipeline._chain_seeds` 의 제동 ②(`씨앗은 is_latest=False 만`)가 '순방향 링크가 있는
    최신본' 을 받아 줘야 한다. 해지 공시는 전건 `is_latest=True` 라 지금 제동에 걸린다.
    """
    superseded: set[str] = set()
    member_chain: dict[str, list[str]] = {}

    for root, corrections in chains.items():
        sequence = [root, *corrections]
        superseded.update(sequence[:-1])
        for rcept_no in sequence:
            # 체인 어느 지점에서 조회해도 나머지 전부를 볼 수 있어야 한다
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
