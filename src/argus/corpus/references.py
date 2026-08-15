"""`※ 관련공시` -> REFERS_TO 엣지 (Phase C 게이트 항목).

정정 체인과 **다른 관계**다. 정정은 '이 문서가 저 문서를 고친다' 이고, 관련공시는
'같은 사안의 선행 공시가 이것들이다' 다. 한 사안이 조회공시 요구 -> 답변 ->
계약체결 -> 해지로 이어지는 흐름이 여기 남는다.

실측(exchange+major 2,067건 스캔): 참조 행 존재 1,463건(70.8%), 총 2,088개 참조.

해소되지 않는 참조를 **조용히 버리지 않는다.** 버리면 "파서가 못 읽은 것" 과
"코퍼스에 없는 것" 이 구분되지 않아 Phase H 오류분석이 왜곡된다. 그래서 이유를
세 가지로 나눠 기록한다.

    resolved       코퍼스 안의 문서로 붙었다
    before_corpus  2023-01-02 이전 공시다 — 코퍼스가 거기서 시작하므로 **구조적 상한**
    not_found      기간 안인데 해당 문서가 없다 (수집 범위 밖 서식이거나 미수집)

`targets` 를 리스트로 두는 이유는 같은 기업이 같은 날 같은 서식으로 여러 건을
내는 경우가 실제로 있기 때문이다(LG에너지솔루션 2024-10-15 계약체결 2건).
하나를 임의로 고르면 근거 인용이 틀릴 수 있어 후보를 모두 남긴다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from argus.config import settings
from argus.corpus.html_doc import extract_rows, read_document
from argus.corpus.manifest import load_manifest

# 코퍼스 시작일. 이보다 이른 참조는 코드로 넘을 수 없다.
CORPUS_START = "20230102"

def _is_reference_label(cell: str) -> bool:
    """`※ 관련공시` 라벨인가.

    부분일치로 '관련공시' 를 찾으면 정정공시의 `1. 정정관련 공시서류` 가 같이
    걸린다. 그러면 그 옆의 `2. 정정관련 공시서류제출일` 날짜가 **서식명 없는
    가짜 참조**로 새어 들어와, 같은 날 아무 문서에나 붙는다.
    """
    packed = _WS.sub("", cell)
    return "관련공시" in packed and "정정관련공시" not in packed
# '2022-09-06 단일판매ㆍ공급계약체결' — 날짜가 앞
_DATE_FIRST = re.compile(r"(\d{4})[-.](\d{2})[-.](\d{2})")
# '특수관계인으로부터의 주식의 취득(2024.07.11)' — 서식명이 앞, 날짜가 괄호 안
_DATE_IN_PAREN = re.compile(r"\(\s*(\d{4})[-.](\d{1,2})[-.](\d{1,2})\s*\)")
_EMPTY = {"-", "–", "—", ""}
_WS = re.compile(r"\s+")


@dataclass(frozen=True)
class Reference:
    """한 건의 `※ 관련공시` 항목."""

    source_rcept_no: str
    source_doc_id: str
    corp_name: str
    target_date: str                     # yyyymmdd (원문이 적어 둔 선행 공시 접수일)
    form_name: str                       # 원문이 적어 둔 서식명
    resolved: bool
    reason: str                          # resolved | before_corpus | not_found
    targets: tuple[str, ...] = field(default_factory=tuple)
    # 원문이 적어 둔 서식명이 붙은 문서의 서식과 맞는가. False 면 '같은 날 같은
    # 기업' 만으로 붙은 약한 엣지다 — 참조된 서식(조회공시 요구·풍문해명 등)이
    # 애초에 코퍼스에 없는 종류일 때 이렇게 된다. 정밀도를 따지는 소비자는
    # 이 값으로 거를 수 있어야 한다.
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
    """참조 칸 -> `[(yyyymmdd, 서식명), ...]`.

    한 칸에 여러 건이 **구분자 없이** 이어 붙으므로 날짜 위치를 경계로 자른다.
    """
    text = (cell or "").strip()
    if text in _EMPTY:
        return []

    # 날짜로 **시작하면** '날짜 서식명' 나열이다. 괄호 안 날짜
    # ('...취득(2024.07.11)')를 여기서 처리하면 서식명이 ')' 로 잘린다.
    if _DATE_FIRST.match(text):
        matches = list(_DATE_FIRST.finditer(text))
        out = []
        for i, m in enumerate(matches):
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            name = _WS.sub(" ", text[m.end():end]).strip(" ,;")
            out.append(("".join(m.groups()), name))
        return out

    # 서식명이 앞, 날짜가 괄호 안인 주요사항보고서 형태
    out = []
    for m in _DATE_IN_PAREN.finditer(text):
        year, month, day = m.groups()
        name = _WS.sub(" ", text[:m.start()]).strip(" :,;")
        out.append((f"{year}{int(month):02d}{int(day):02d}", name))
    return out


_FORM_NOISE = re.compile(r"[\s·ㆍ()\[\]]")
_FORM_PREFIX = re.compile(r"^\[[^\]]*\]")
# 서식명 표기는 원문과 manifest 가 조금씩 다르다('단일판매 공급계약 체결' vs
# '단일판매ㆍ공급계약체결'). 앞부분만 맞으면 같은 서식으로 본다.
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
        # 라벨 칸에 '관련공시' 가 있으면 값 칸이 참조 목록이다.
        if any(_is_reference_label(cell) for cell in row[:-1]):
            cells.append(row[-1])
        # 주요사항보고서는 서술문 안에 '※ 관련공시 : ...' 로 끼워 넣는다.
        elif (last := row[-1]) and _is_reference_label(last):
            cells.append(last.split("관련공시", 1)[-1].lstrip(" :："))
    return cells


def extract_references(doc: dict) -> list[Reference]:
    """공시 한 건 -> REFERS_TO 후보들. 해소 실패도 이유를 달아 남긴다."""
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
            # 같은 날 여러 건이면 원문이 적어 둔 서식명으로 좁힌다.
            same_form = [o for o in siblings if _forms_agree(form_name, o["report_nm"])]
            chosen = same_form or siblings
            candidates = tuple(o["rcept_no"] for o in chosen)

            if candidates:
                reason = "resolved"
            elif target_date < CORPUS_START:
                # 코퍼스가 2023-01-02 부터라 애초에 없는 문서다.
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
    """코퍼스 전체를 훑어 REFERS_TO 를 낸다. 🔴 **분석 전용 — 호출부가 0건이다.**

    운영 경로도 러너도 이 함수를 안 부른다 (`extract_references` 를 문서 단위로 부른다).
    지우지 않는 이유는 REFERS_TO 전수 통계가 `corrections.attach_related` 독스트링의
    판정 근거(*"강한 엣지 출처 453건 중 해지는 11건뿐"*)이고, 그 수를 다시 세는 유일한
    수단이 이 함수이기 때문이다. **새 코드에서 부르기 전에 비용을 재라 — 전수 훑기다.**
    """
    for doc in load_manifest():
        if doc["doc_group"] in doc_groups:
            yield from extract_references(doc)
