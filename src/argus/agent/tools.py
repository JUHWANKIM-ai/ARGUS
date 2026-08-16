"""도구 15종 = 검색 8종 + 계산 7종.

검색 7종은 CONVENTION §7.7 의 참조 명세를 따른다. `query_by_counterparty` 는 8번째
검색 도구로, 발주처(계약상대) 2-hop 그래프(Task 3 산출물)를 노출해 밸류체인 문항의
fan-in/fan-out 완전 열거를 가능하게 한다 — 순위를 올리는 도구가 아니라 집합의
경계를 긋는 도구다.
계산 7종은 policy §4 ("LLM = Reasoning, Tool = Calculation") 을 강제하기 위해
`compute.py` 의 결정적 함수를 function calling 스펙으로 노출한 것이다.
노출되지 않은 계산 함수는 LLM 이 대신 계산하므로 §4 위반이다.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache

from argus.agent import compute
from argus.config import settings
from argus.corpus.counterparty import canonical, classify
from argus.corpus.manifest import filter_docs, resolve_company
from argus.corpus.sections import BLOB_NAME
#: 🔴 `# noqa: F401` 이 붙어 있었는데 **다섯 이름이 전부 이 파일 안에서 직접 쓰인다.**
#: 재수출이라고 오해시키는 주석이라 뗀다 (전수 확인: `tools.Bm25Index` 처럼 이 모듈을
#: 통해 끌어 쓰는 소비자가 저장소에 0건이다).
from argus.retrieve.bm25 import (SECTION_CHUNK_CHARS, Bm25Index,
                                 SectionBm25Index, norm_path, split_section)

SECTION_INDEX_STEM = "section_index"
COUNTERPARTY_GRAPH_NAME = "counterparty_graph.json"


#: 정형 레코드 문서군. **인덱스는 문서군마다 따로다** — 하나로 합치면 exchange 골든
#: 근거 회수가 29 -> 28 로 떨어진다 (04-retriever D-5 실측). 얻는 것이 있어도 기존
#: 성능을 깎으면 손해라, 라우터가 문서군을 골라 해당 인덱스만 본다.
RECORD_GROUPS = ("exchange", "major", "holding")

#: `※ 관련공시` REFERS_TO 엣지 (`scripts/build_references.py` 산출물).
REFERENCES_NAME = "references.jsonl"


@lru_cache(maxsize=len(RECORD_GROUPS))
def _group_records_raw(group: str) -> dict[str, dict]:
    path = settings().artifacts_root / f"{group}_records.jsonl"
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as fh:
        rows = [json.loads(line) for line in fh if line.strip()]
    return {r["doc_id"]: r for r in rows}


def _group_records(group: str) -> dict[str, dict]:
    """정형 레코드. arm `A18`(`nary`)이면 n-ary 추출값이 덧붙은 **사본**을 돌려준다.

    🔴 **두 사전을 따로 캐시한다.** `lru_cache` 를 arm 과 무관하게 하나만 두면
    먼저 부른 arm 의 사전이 나중 arm 에 그대로 재생돼 **Δ 가 조용히 0** 이 된다 —
    이 모듈이 아니라 `ablation.py` 가 통째로 막으려는 종류의 사고다.
    """
    from argus import ablation

    arm = ablation.current()
    if arm.nary:
        return _group_records_nary(group)
    if arm.correction_fields:
        return _group_records_fields(group)
    return _group_records_raw(group)


# --- A18 (`nary`) — `T1-targeted` 추출값을 근거에 덧붙인다 ------------------------
#
# 🔴 **코퍼스 파일은 안 고친다.** `scripts/build_nary.py` 가 새 파일 하나
# (`artifacts/nary_records.jsonl`)만 쓰고, 합치는 것은 런타임에 여기서 사본 위에만
# 일어난다. `*_records.jsonl` 을 고치면 BM25 색인 문자열이 갈려 v1~v4 측정이 전량
# 무효가 되고, 되돌릴 수단이 없다 (`artifacts/` 는 gitignore 다).
#
# 🔴 **BM25 점수는 안 바뀐다.** 인덱스는 미리 빌드된 `*_bm25.json` 이고 여기서 바꾸는
# 것은 검색 **뒤에** 프롬프트로 실리는 근거 문자열뿐이다. 그래서 `A18` 의 Δ 는
# '검색이 달라졌다' 가 아니라 순수하게 '근거에 필드가 늘었다' 에 귀속된다.

NARY_NAME = "nary_records.jsonl"

#: 덧붙일 항목과 **그 항목이 이미 있는지 보는 정규식**. 합집합의 정의가 이 표다 —
#: 규칙이 뽑아 `corpus/markdown.to_markdown` 이 이미 찍은 라벨이면 그 항목을 건너뛴다.
#: 🔴 금액 라벨은 `record["amount_type"]` 이 정하므로 고정 문자열로 못 본다
#: (`계약금액`·`투자금액`·`취득금액`… `_amount_line` 참조). 그래서 `…금액:` 으로 본다.
_NARY_PRESENT = {
    "amount": re.compile(r"^-\s*\S*금액\s*:", re.M),
    "counterparty": re.compile(r"^-\s*계약상대\s*:", re.M),
    "period": re.compile(r"^-\s*(기간|계약\s*시작일|계약\s*종료일)\s*:", re.M),
    "subject": re.compile(r"^-\s*대상\s*:", re.M)}
#: 항목당 줄 상한. 한 레코드에서 관계가 여러 건 나오면 값이 여러 개인데, 근거가
#: 길어지면 `MAX_EVIDENCE` 아래에서 **다른 문서를 밀어낸다**. 앞의 몇 개만 싣는다.
MAX_NARY_VALUES = 3


#: 🔴 **모델이 라벨 자리에 값을 넣는다.** 실측 추출에서 `amount_label` 로
#: `"USD 663,688,303"`·`"US$ 34,500,000"`·`"$100"` 이 나왔고, 그대로 쓰면 근거에
#: `- USD 663,688,303: 663,688,303원 (7억원)` 같은 **필드명이 아닌 줄**이 실린다.
#: 숫자가 섞였거나 지나치게 긴 라벨은 필드명이 아니므로 기본 라벨로 접는다.
_LABEL_HAS_DIGIT = re.compile(r"\d")
MAX_NARY_LABEL_CHARS = 12


def _nary_amount_line(relation: dict) -> str | None:
    amount = relation.get("amount")
    if not isinstance(amount, int) or amount <= 0:
        return None
    label = " ".join(str(relation.get("amount_label") or "").split())
    if (not label or _LABEL_HAS_DIGIT.search(label)
            or len(label) > MAX_NARY_LABEL_CHARS):
        label = "계약금액"
    #: `_amount_line` 과 **같은 서식**이다 — 규칙이 찍은 줄과 모양이 다르면
    #: `prompts.split_trillion_eok` 와 `critic._says_amount` 가 이 줄만 못 본다.
    return f"- {label}: {amount:,}원 ({amount / 100_000_000:,.0f}억원)"


def _nary_lines(relations: list[dict]) -> dict[str, list[str]]:
    """관계 목록 -> 항목별 근거 줄. 중복은 **순서를 지키며** 접는다."""
    out: dict[str, list[str]] = {}

    def push(key: str, line: str | None) -> None:
        if not line:
            return
        bucket = out.setdefault(key, [])
        if line not in bucket and len(bucket) < MAX_NARY_VALUES:
            bucket.append(line)

    for relation in relations:
        if not isinstance(relation, dict):
            continue
        push("amount", _nary_amount_line(relation))
        if relation.get("counterparty"):
            push("counterparty", f"- 계약상대: {relation['counterparty']}")
        start, end = relation.get("period_start"), relation.get("period_end")
        if start and end:
            push("period", f"- 기간: {start} ~ {end}")
        elif start:
            push("period", f"- 계약 시작일: {start}")
        elif end:
            push("period", f"- 계약 종료일: {end}")
        if relation.get("subject"):
            push("subject", f"- 대상: {relation['subject']}")
    return out


@lru_cache(maxsize=1)
def _nary_extract() -> dict[str, dict[str, list[str]]]:
    """`{접수번호: {항목: [근거 줄]}}`. 파일이 없으면 빈 사전 — arm 이 조용히 no-op 이다."""
    path = settings().artifacts_root / NARY_NAME
    if not path.exists():
        return {}
    out: dict[str, dict[str, list[str]]] = {}
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            row = json.loads(line)
            lines = _nary_lines(row.get("relations") or [])
            if lines:
                out[str(row["rcept_no"])] = lines
    return out


def merge_nary(markdown: str, lines: dict[str, list[str]]) -> str:
    """🔴 **합집합이다 — 덮어쓰지 않는다.** 규칙이 이미 찍은 항목은 건너뛴다.

    규칙의 산출물을 LLM 산출물로 갈아치우면 `A18` 이 재는 것이 '추가' 가 아니라
    '교체' 가 되고, `L2`(노드 속성 LOO)의 −15.3%p 와 겨냥이 겹쳐 Δ 의 귀속이 깨진다.
    """
    add = [line for key, values in lines.items()
           if not _NARY_PRESENT[key].search(markdown or "")
           for line in values]
    if not add:
        return markdown
    return (markdown.rstrip("\n") + "\n" + "\n".join(add)) if markdown else "\n".join(add)


@lru_cache(maxsize=len(RECORD_GROUPS))
def _group_records_nary(group: str) -> dict[str, dict]:
    """`A18` 전용 사본. 🔴 **원본 사전을 변형하지 않는다** — 같은 프로세스에서 다른
    arm 을 이어 돌리면 그 arm 의 근거까지 오염되고, 그것은 조용히 일어난다."""
    extract = _nary_extract()
    if not extract:
        return _group_records_raw(group)
    out: dict[str, dict] = {}
    for doc_id, record in _group_records_raw(group).items():
        lines = extract.get(str(record.get("rcept_no")))
        if not lines:
            out[doc_id] = record
            continue
        merged = merge_nary(record.get("markdown", ""), lines)
        out[doc_id] = record if merged == record.get("markdown", "") else {
            **record, "markdown": merged}
    return out


# --- A30 (`correction_fields`) — 정정사유·주요내용을 근거에 덧붙인다 -----------------
#
# 🔴 **코퍼스 파일은 안 고친다.** `A18`(`nary`)이 연 이음매에 그대로 얹는다 —
# `artifacts/*_records.jsonl` 을 고치면 BM25 색인 문자열이 갈려 v1~v4 측정이 전량
# 무효가 되고, 되돌릴 수단이 없다 (`artifacts/` 는 gitignore).
#
# 🔴 **`A17` 이 아니다.** `A17` 은 `corpus/markdown._free_text_lines` 의 게이트를 열어
# 코퍼스 3,150건 중 **1,588건(50.4%)** 의 마크다운을 다시 쓰는 조작이었고 그래서
# 기각됐다. 여기서 바뀌는 것은 검색 **뒤에** 프롬프트로 실리는 문자열뿐이라
# **BM25 점수가 한 바이트도 안 바뀐다.**
#
# 🔴 **R0(2026-08-16)이 겨눈 결함**: 정형 매칭이 정보를 지운다. `schema_matched=True`
# 면 넉 줄 요약을 내고 `raw_rows` 를 통째로 버리고, 매칭이 **안 되면** 원문을 실어
# 752자가 나온다. 그래서 답이 레코드에 있는데 근거에 없다:
#
#     20241114800538 (이마트 정정본)   "올림푸스제일차"           레코드 O · 마크다운 X (156자)
#     20260306801011 (크래프톤 정정본)  "출자일자 확정에 따른 정정"  레코드 O · 마크다운 X (150자)

#: 덧붙일 라벨. 🔴 **둘뿐이다.** `raw_rows` 전량 덤프는 `A17` 이 기각된 자리다.
#: R0 실측 번짐: `3. 정정사유` 630레코드 · `2. 주요내용` 120레코드 (전체 1,469).
CORRECTION_FIELD_LABELS = ("3. 정정사유", "2. 주요내용")

#: 🔴 **덧붙임을 이 서식에만 건다.** 첫 판은 서식을 안 가렸고 그 대가로 `Q-110`(지금
#: 정답)을 잃었다 — 밸류체인 7/12 → 6/12. judge 세 축은 다 통과였는데 답변에서 금액
#: 표기가 사라져 커버리지 게이트에 걸렸다. 근거가 길어지자 서술이 역할 나열로 기울었다.
#:
#: 겨냥 둘(`Q-087`·`Q-093`)은 **모두 이 서식**이고 `Q-110` 은 `단일판매공급계약체결`
#: 이다. 그리고 성형 결손이 가장 심한 것도 이 서식이다 — 정형 템플릿이 `금액` 과
#: `확정일` 넉 줄만 내고 본문을 통째로 버린다(실측 150~156자). `단일판매공급계약체결`
#: 은 템플릿이 금액·상대·기간·대상을 이미 실어 버려지는 것이 대부분 상용구다.
#:
#: 번짐: 707레코드 → 300레코드 (`투자판단관련주요경영사항` 전건).
CORRECTION_FIELD_FORMS = ("투자판단관련주요경영사항",)

#: 값 길이 상한. 🔴 `normalize.FREETEXT_MAX_CHARS`(300)로는 부족하다 — `Q-087` 의 정답
#: `올림푸스제일차` 가 `2. 주요내용`(623자)의 **408·440번째 자리**에 있어 300 에서
#: 잘린다 (v4 `R1-4` 실측). 500 이상이어야 들어온다.
CORRECTION_FIELD_CHARS = 560


def _correction_lines(record: dict) -> list[str]:
    """이 레코드에 덧붙일 줄. 이미 마크다운에 있는 값은 건너뛴다 (합집합).

    🔴 **마지막 열을 읽는다.** 정정 표의 행은 `[라벨, 정정전, 정정후]` 세 열이고,
    답은 **정정후**에 있다 (`Q-087` 실측: `2. 주요내용` 행의 3열짜리에서 `올림푸스
    제일차` 가 열 2 에만 있다). 열 1 만 읽으면 정정 전 문안을 싣게 되고, 그것은
    `stale_value` 가 잡던 바로 그 오류를 우리가 근거에 박아 주는 꼴이다.

    🔴 **라벨당 가장 긴 값을 고른다.** 같은 라벨이 여러 행에 나온다 (정정 표 안 ·
    본문 두 자리). 첫 판은 앞 24자가 같으면 건너뛰었는데 그것이 **더 긴 쪽을 버렸다**
    — 484자본에는 답이 408번째에 있고 623자본에는 440번째에 있는데, 짧은 쪽을 남기면
    상한 안에 들어와도 뒤쪽 문맥이 잘린다.
    """
    if (record.get("form") or "") not in CORRECTION_FIELD_FORMS:
        return []
    md = record.get("markdown") or ""
    best: dict[str, str] = {}
    for row in (record.get("raw_rows") or ()):
        if not (isinstance(row, (list, tuple)) and len(row) >= 2):
            continue
        label = str(row[0]).strip()
        if label not in CORRECTION_FIELD_LABELS:
            continue
        cells = [" ".join(str(c).split()) for c in row[1:]]
        value = next((c for c in reversed(cells) if len(c) >= 2), "")
        if len(value) < 2 or value[:24] in md:
            continue
        if len(value) > len(best.get(label, "")):
            best[label] = value
    return [f"- {label.split('. ', 1)[-1]}: {best[label][:CORRECTION_FIELD_CHARS]}"
            for label in CORRECTION_FIELD_LABELS if label in best]


@lru_cache(maxsize=len(RECORD_GROUPS))
def _group_records_fields(group: str) -> dict[str, dict]:
    """`A30` 전용 사본. 🔴 **원본 사전을 변형하지 않는다** — `_group_records_nary` 와
    같은 이유다. 같은 프로세스에서 다른 arm 을 이어 돌리면 그 arm 의 근거까지
    오염되고, 그것은 조용히 일어난다."""
    out: dict[str, dict] = {}
    for doc_id, record in _group_records_raw(group).items():
        lines = _correction_lines(record)
        out[doc_id] = ({**record,
                        "markdown": record.get("markdown", "") + "\n" + "\n".join(lines)}
                       if lines else record)
    return out


@lru_cache(maxsize=len(RECORD_GROUPS))
def _group_index(group: str) -> Bm25Index | None:
    path = settings().artifacts_root / f"{group}_bm25.json"
    return Bm25Index.load(path) if path.exists() else None


def _records() -> list[dict]:
    """`query_events`·`get_history` 의 모집단. §7.7 ② 정형 이벤트 = 거래소공시다.

    주요사항·지분 레코드를 여기 섞으면 `query_events` 의 의미가 바뀌고 exchange 질의의
    폴백이 오염된다. 검색 인덱스만 문서군별로 늘리고 이 모집단은 그대로 둔다.
    """
    return list(_group_records("exchange").values())


def _index() -> Bm25Index | None:
    return _group_index("exchange")


@lru_cache(maxsize=3)
def _by_rcept_cached(nary: bool, fields: bool = False) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for group in RECORD_GROUPS:
        if nary:
            source = _group_records_nary(group)
        elif fields:
            source = _group_records_fields(group)
        else:
            source = _group_records_raw(group)
        for record in source.values():
            out.setdefault(record["rcept_no"], record)
    return out


def _by_rcept() -> dict[str, dict]:
    """접수번호 -> 레코드. **문서군 셋을 전부 덮는다.**

    `_records()` 와 다르다. `query_events` 모집단은 거래소공시로 묶어 둬야 하지만
    (§7.7 ②), 정정 체인은 문서군을 가리지 않는다 — 실측 Q-030 의 고려아연 유상증자
    철회는 `major` 체인이라 거래소 모집단만 보면 영원히 `레코드 없음` 이다.

    🔴 `A18` 은 여기도 갈라야 한다 — `_gather_meta`·오라클 주입·정정 체인이 전부
    이 사전을 지나므로, 여기만 안 갈면 arm 의 근거가 경로마다 달라진다.

    🔴 **`A30` 도 같다. 그 함정에 실제로 빠졌다** (2026-08-16). `correction_fields` 를
    `_group_records` 에만 걸었더니 `Q-087` 의 근거가 **한 글자도 안 바뀌었다** —
    그 문항의 정정본은 검색이 아니라 **체인 hop**(`get_history` → `_chain_entry`)으로
    오고, 그 경로는 `_by_rcept()` 를 지난다. 위 문단이 경고하던 바로 그 자리다.
    """
    from argus import ablation

    arm = ablation.current()
    return _by_rcept_cached(arm.nary, arm.correction_fields)


@lru_cache(maxsize=1)
def _followup_index() -> dict[str, tuple[str, ...]]:
    """`※ 관련공시` **역방향** 색인 — 이 문서를 뒤에서 가리킨 공시들.

    정정 체인(`related`)이 못 잇는 관계가 하나 있다. **후속 해지**다. 실측:
    해지 공시 20건 중 `related` 로 이어진 것은 0건인데, 원계약을 `※ 관련공시` 에
    적어 둔 것은 11건이다 (LG에너지솔루션 20251217800800 -> 20241015800258).
    엣지 방향이 '뒤 -> 앞' 이므로 앞 문서에서 뒤를 보려면 뒤집어야 한다.

    `resolved` + `form_matched` 인 엣지만 쓴다. 서식명이 안 맞는 엣지는 '같은 날
    같은 기업' 만으로 붙은 약한 연결이라(1,154/2,041) 근거로 끌어오면 잡음이다.
    """
    path = settings().artifacts_root / REFERENCES_NAME
    if not path.exists():
        return {}
    rev: dict[str, set[str]] = {}
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            edge = json.loads(line)
            if not (edge.get("resolved") and edge.get("form_matched")):
                continue
            for target in edge.get("targets") or ():
                rev.setdefault(target, set()).add(edge["source_rcept_no"])
    return {key: tuple(sorted(v - {key})) for key, v in rev.items()}


def _record_of(doc_id: str) -> dict | None:
    """`exchange_20230602800079` 든 `20230602800079` 든 레코드를 찾는다."""
    if not doc_id:
        return None
    key = str(doc_id).split("_")[-1].split("#")[0]
    return _by_rcept().get(key)


def chain_probe(doc_id: str) -> dict:
    """체인을 밟기 전 예비 조회. **도구 호출이 아니라 스텝을 쓰지 않는다.**

    `section_paths()` 와 같은 취급이다 — 이미 메모리에 있는 레코드 한 건을 보는 것뿐이라
    검색도 I/O 도 일어나지 않는다. 이게 없으면 링크가 아예 없는 문서에도 `get_history`
    를 걸어 스텝을 태우게 된다.

    `is_latest` 는 **체인 위치**로 정해진 값이다 (`corpus/corrections.attach_related`).
    금액 크기로 정하면 안 된다 — 값이 줄어드는 체인이 실재한다
    (한화오션 517,856,145,079 -> 275,063,868,984).

    🔴 **엣지 세 종류를 갈라서 함께 돌려준다** (v3 R2 ④). `links` 는 종전대로 셋의
    합집합이고, `followups`·`terminates` 는 씨앗 조건이 **어느 엣지 때문에** 열렸는지
    호출부가 알아야 해서 따로 싣는다 (`pipeline._chain_seeds` 제동 ②).

        related     정정 체인 550엣지          양방향 (`member_chain`)
        followups   ※관련공시 **역방향**       이 문서를 **뒤에서** 가리킨 공시들
        terminates  해지 -> 원계약 **순방향**   `corpus/corrections._termination_links`

    방향이 왜 둘 다 필요한가 — `_followup_index` 는 역방향 전용이라 **해지 공시에서
    출발하면 빈다.** 실측: `chain_probe(해지)` links=[] · `chain_probe(원계약)`
    links=[해지]. 회귀 `Q-066` 은 해지에서 출발하는 문항이라 역방향만으로는 런타임
    대체 경로가 **없다** (W4 진단). `terminates` 가 그 방향을 채운다.
    """
    record = _record_of(doc_id)
    if record is None:
        return {}
    rcept_no = record["rcept_no"]
    links = [r for r in (record.get("related") or []) if r != rcept_no]
    followups = tuple(_followup_index().get(rcept_no, ()))
    terminates = tuple(r for r in (record.get("terminates") or ()) if r != rcept_no)
    for other in followups + terminates:
        if other not in links:
            links.append(other)
    return {"rcept_no": rcept_no, "corp_name": record.get("corp_name", ""),
            "is_latest": bool(record.get("is_latest", True)),
            "links": tuple(links),
            "followups": followups, "terminates": terminates}


@lru_cache(maxsize=1)
def _section_index() -> SectionBm25Index | None:
    """정기공시 섹션 인덱스. 없으면 None — 도구는 error 를 돌려주고 파이프라인은 산다."""
    stem = settings().artifacts_root / SECTION_INDEX_STEM
    return SectionBm25Index.load(stem) if SectionBm25Index.exists(stem) else None


@lru_cache(maxsize=1)
def section_paths() -> tuple[str, ...]:
    """전 기업 공통 목차 경로. `path_hints` 가 후보를 고르는 모집단이다.

    깊이 2단 이하 + 200개 문서 이상 등장으로 자른다. 실측 64개이고, 여기에
    '12. 차입금 (연결)' 같은 기업별 주석 제목은 들어오지 않는다.
    """
    index = _section_index()
    if index is None:
        return ()
    docs: dict[str, set[int]] = {}
    path_codes, doc_codes = index.codes["path"], index.codes["doc_id"]
    table = index.tables["path"]
    for i in range(len(index.chunk_ids)):
        docs.setdefault(table[int(path_codes[i])], set()).add(int(doc_codes[i]))
    return tuple(p for p, seen in docs.items()
                 if len(seen) >= 200 and p.count(" > ") <= 1)


def _section_body(record: dict) -> str:
    """블롭에서 섹션 본문을 읽고 조각 하나를 잘라 낸다. `split_section` 이 결정적이다."""
    offset, length = record.get("text_offset", -1), record.get("text_bytes", 0)
    if offset < 0 or length <= 0:
        return ""
    with (settings().artifacts_root / BLOB_NAME).open("rb") as fh:
        fh.seek(offset)
        text = fh.read(length).decode("utf-8", errors="replace")
    if record.get("n_parts", 1) <= 1:
        return text
    parts = split_section(text, SECTION_CHUNK_CHARS)
    index = record.get("part", 0)
    return parts[index] if index < len(parts) else text


def _section_markdown(record: dict) -> str:
    """근거 블록. 근거 표기 형식은 §7.5 — 보고서(기준연월) · 접수번호 · 섹션 경로."""
    rcept_no = str(record["doc_id"]).split("_")[-1]
    label = {"annual": "사업보고서", "half": "반기보고서",
             "quarter": "분기보고서"}.get(record.get("doc_subtype", ""), "정기보고서")
    part = ""
    if record.get("n_parts", 1) > 1:
        part = f" [{record['part'] + 1}/{record['n_parts']} 조각]"
    body = _section_body(record)
    return (f"## [{label} {record.get('base_year', '')}] "
            f"{record.get('corp_name', '')} – {record.get('path', '')}{part}\n"
            f"- 출처: 접수번호 {rcept_no} (공시일 {record.get('rcept_dt', '')})\n\n{body}")


def resolve_company_tool(name: str) -> dict:
    row = resolve_company(name)
    if not row:
        return {"error": f"universe 70개사에 '{name}' 이 없습니다"}
    return {"corp_code": row["corp_code"], "corp_name": row["corp_name"],
            "sector": row["sector"], "market": row["market"]}


#: `query_events` 가 돌려주는 이벤트 수. LLM 이 읽을 목록이라 20 이 상한이었다.
EVENTS_PAGE = 20


def query_events(corp_name: str | None = None, doc_subtype: str | None = None,
                 year: int | None = None, min_amount: float | None = None,
                 is_latest: bool = True, limit: int = EVENTS_PAGE) -> dict:
    """정형 이벤트 조회. `count` 는 **필터를 통과한 전량**이고 `events` 는 그중 `limit` 건이다.

    `limit` 은 **`TOOL_SPECS` 에 노출하지 않는다** — 검색 계획이 쓰는 축이 아니라
    `pipeline._enumerate_events` 가 **그룹핑을 위해** 모집단 전체를 받아야 해서 있는
    것이다(계약상대별 그룹 크기와 각 그룹의 최신본은 20건만 봐서는 못 정한다 — 실측
    `Q-117` 은 한전기술 최신본 27건이고 골든 3건이 21번째 이후에 있다). `search_chunks`
    의 `doc_id` 축과 같은 성격이고, 기본값이면 종전과 글자 하나 안 바뀐다.
    """
    rows = _records()
    if corp_name:
        resolved = resolve_company(corp_name)
        target = resolved["corp_name"] if resolved else corp_name
        rows = [r for r in rows if r["corp_name"] == target]
    if doc_subtype:
        rows = [r for r in rows if r.get("doc_subtype") == doc_subtype]
    if year:
        rows = [r for r in rows if str(r.get("rcept_dt", ""))[:4] == str(year)]
    if min_amount is not None:
        rows = [r for r in rows if (r.get("amount") or 0) >= min_amount]
    if is_latest:
        rows = [r for r in rows if r.get("is_latest", True)]
    return {"count": len(rows), "events": rows[:max(int(limit), 0)]}


def list_filings(corp_name: str, doc_group: str = "periodic",
                 base_year: int | None = None) -> dict:
    docs = filter_docs(corp_name=corp_name, doc_group=doc_group, include_corrections=True)
    if base_year:
        docs = [d for d in docs if d.get("base_year") == base_year]
    return {"count": len(docs),
            "filings": [{"doc_id": d["doc_id"], "report_nm": d["report_nm"],
                         "rcept_no": d["rcept_no"], "rcept_dt": d["rcept_dt"],
                         "base_year": d.get("base_year")} for d in docs]}


def _sections_of(index: SectionBm25Index, doc_id: str) -> list[dict]:
    """한 문서의 조각 레코드. `part == 0` 만 남겨 섹션 단위로 되돌린다."""
    table = index.tables["doc_id"]
    code = table.index(doc_id) if doc_id in table else -1
    if code < 0:
        return []
    import numpy as np
    rows = np.flatnonzero(index.codes["doc_id"] == code)
    return [index.record_at(int(i)) for i in rows]


def list_sections(doc_id: str, include_stubs: bool = False) -> dict:
    """§7.7 ④ — 목차 `path` + 글자수. 기본은 100자 미만 스텁을 뺀다."""
    index = _section_index()
    if index is None:
        return {"error": "섹션 인덱스가 없습니다. "
                         "scripts/build_section_index.py 를 먼저 실행하세요"}
    out = []
    for record in _sections_of(index, doc_id):
        if record["part"] != 0:
            continue
        if record["is_stub"] and not include_stubs:
            continue
        out.append({"path": record["path"], "n_chars": record["section_chars"],
                    "n_tables": record["n_tables"], "n_parts": record["n_parts"],
                    "is_stub": bool(record["is_stub"])})
    return {"count": len(out), "sections": out}


def read_section(doc_id: str, path: str, part: int | None = None) -> dict:
    """§7.7 ⑤ — 섹션 원문(표=마크다운).

    상위 목차로 부르면 하위 섹션을 순서대로 이어 붙인다. 목차 오타(`12, 관계기업투자`)
    때문에 경로 비교는 구두점을 무시한다 — 완전일치로는 HMM 20240514001580 을 못 연다.
    """
    index = _section_index()
    if index is None:
        return {"error": "섹션 인덱스가 없습니다"}
    wanted = norm_path(path)
    rows = [r for r in _sections_of(index, doc_id)
            if norm_path(r["path"]) == wanted
            or norm_path(r["path"]).startswith(wanted)]
    if not rows:
        return {"error": f"섹션 없음: {doc_id} / {path}"}
    rows.sort(key=lambda r: (r["chunk_id"].split("/")[0], r["part"]))
    if part is not None:
        rows = [r for r in rows if r["part"] == part] or rows[:1]
    text = "\n\n".join(t for t in (_section_body(r) for r in rows) if t)
    head = rows[0]
    return {"doc_id": doc_id, "path": path, "chunk_id": head["chunk_id"],
            "corp_name": head["corp_name"], "rcept_dt": head["rcept_dt"],
            "doc_subtype": head["doc_subtype"], "base_year": head["base_year"],
            "n_sections": len(rows), "n_chars": len(text),
            "n_tables": sum(r["n_tables"] for r in rows), "text": text}


def _search_sections(query: str, corp_name: str | None = None, top_k: int = 5,
                     base_year: int | str | None = None,
                     doc_subtype: str | None = None,
                     path: str | None = None, rcept_dt: str | None = None,
                     include_stubs: bool = True,
                     doc_id: str | None = None) -> dict:
    """정기공시 섹션 검색. exchange 와 **인덱스가 다르다**.

    축이 셋 다르다 — 기간은 접수일이 아니라 `base_year`(사업연도), 보고서 종류가
    `doc_subtype`, 그리고 목차 `path` 접두. 실측 근거는 04-retriever D-5.

    `doc_id` 는 **문서 하나로 못박는 완전일치 필터**다 (`SECTION_FIELDS` 축). 검색 계획은
    이 인자를 쓰지 않는다 — `ablation.ORACLE` 이 골든 문서 안에서만 조각을 고르기 위한
    것이고, 기본값 `None` 이면 종전 후보 집합이 글자 하나 안 바뀐다.
    """
    index = _section_index()
    if index is None:
        return {"error": "섹션 인덱스가 없습니다"}
    filters: dict = {}
    if doc_id:
        filters["doc_id"] = doc_id
    if corp_name:
        resolved = resolve_company(corp_name)
        filters["corp_name"] = resolved["corp_name"] if resolved else corp_name
    if base_year:
        filters["base_year"] = str(base_year)
    if doc_subtype:
        filters["doc_subtype"] = doc_subtype
    prefix = {}
    if path:
        prefix["path"] = path
    if rcept_dt:
        prefix["rcept_dt"] = str(rcept_dt).replace("-", "")
    prefix = prefix or None
    hits = index.search(query, top_k=top_k * (2 if not include_stubs else 1),
                        prefix=prefix, **filters)
    chunks = []
    for chunk_id, score in hits:
        record = index.record(chunk_id)
        if record is None or (record["is_stub"] and not include_stubs):
            continue
        chunks.append({"doc_id": record["doc_id"], "chunk_id": chunk_id,
                       "path": record["path"], "score": round(score, 3),
                       "markdown": _section_markdown(record)})
        if len(chunks) >= top_k:
            break
    return {"count": len(chunks), "chunks": chunks}


def search_chunks(query: str, corp_name: str | None = None, top_k: int = 5,
                  year: int | None = None, ym: str | None = None,
                  group: str | None = None, base_year: int | str | None = None,
                  doc_subtype: str | None = None, path: str | None = None,
                  rcept_dt: str | None = None, include_stubs: bool = True,
                  doc_id: str | None = None) -> dict:
    """§7.7 ⑥ 하이브리드 검색 폴백. **인덱스 두 개를 인자로 가른다.**

    기간 조건이 랭킹을 좌우한다 — 실측에서 corp_name 만으로는 골든이 15/26 이지만
    rcept_dt prefix 를 함께 걸면 1위가 된다. 도구 스펙에도 노출해야 한다.

    정기공시 축(`base_year`·`doc_subtype`·`path`·`doc_id`)이 하나라도 오거나
    `group="periodic"` 이면 섹션 인덱스로 간다. 두 인덱스를 합치지 않은 이유는 실측이다 —
    합치면 exchange 골든 회수가 27 -> 26, top3 25 -> 24 로 떨어진다 (04-retriever D-5).

    `doc_id` 는 **섹션 인덱스 축에만 있다** (`periodic_<접수번호>`). `TOOL_SPECS` 에는
    노출하지 않는다 — 검색 계획이 쓰는 축이 아니라 `ablation.ORACLE` 이 골든 문서를
    못박기 위한 축이다.
    """
    if group == "periodic" or base_year or doc_subtype or path or doc_id:
        return _search_sections(query, corp_name=corp_name, top_k=top_k,
                                base_year=base_year, doc_subtype=doc_subtype,
                                path=path, rcept_dt=rcept_dt or ym,
                                include_stubs=include_stubs, doc_id=doc_id)
    target = group if group in RECORD_GROUPS else "exchange"
    index = _group_index(target)
    if index is None:
        return {"error": f"BM25 인덱스가 없습니다 ({target})"}
    filters = {}
    if corp_name:
        resolved = resolve_company(corp_name)
        filters["corp_name"] = resolved["corp_name"] if resolved else corp_name
    prefix = {}
    if ym:
        prefix["rcept_dt"] = str(ym).replace("-", "")[:6]
    elif year:
        prefix["rcept_dt"] = str(year)[:4]
    hits = index.search(query, top_k=top_k, prefix=prefix or None, **filters)
    by_id = _group_records(target)
    return {"count": len(hits),
            "chunks": [{"doc_id": d, "score": round(s, 3),
                        "markdown": by_id.get(d, {}).get("markdown", "")} for d, s in hits]}


def _chain_entry(record: dict, kind: str, is_latest: bool) -> dict:
    return {"rcept_no": record["rcept_no"], "doc_id": record["doc_id"],
            "rcept_dt": record.get("rcept_dt", ""),
            "is_correction": record.get("is_correction", False),
            "is_latest": is_latest, "amount": record.get("amount"),
            "kind": kind, "markdown": record.get("markdown", "")}


def get_history(rcept_no: str, include_followups: bool = True) -> dict:
    """정정 체인 + 후속 공시 추적. 유일하게 is_latest 를 무시한다.

    `chain` 은 같은 사안의 정정 이력을 **접수일 오름차순**으로 돌려준다. 그래서
    `is_latest` 는 **체인의 마지막 자리인가**로만 정해진다 — 금액 크기로 정하면 안 된다.
    실측으로 값이 줄어드는 체인이 있다 (한화오션 잠수함 1,102,000,000,000 ->
    443,500,000,000 -> ... -> 899,400,000,000). '가장 큰 값 = 최신' 규칙을 썼다면
    전부 틀렸을 것이다.

    `followups` 는 정정이 아니라 **뒤에 온 다른 사안**이다 (해지·철회). `related` 로는
    안 잡히고 `※ 관련공시` 역방향으로만 잡힌다 — 실측 해지 20건 중 related 0건,
    관련공시 11건. 정정본과 섞으면 '정정된 유효 계약' 과 '해지된 계약' 이 구분되지
    않으므로 `kind` 로 갈라 둔다.

    `terminated` 는 그 **반대 방향**이다 — 이 공시가 해지한 **원계약**(+그 정정 체인).
    `followups` 가 역방향뿐이라 해지에서 출발하면 빈다는 것이 회귀 `Q-066` 의 기전이고,
    이 칸이 그 방향을 채운다. **`followups` 와 절대 합치지 않는다**: 시간 방향이
    반대라 합치면 원계약이 '후속 공시' 로 찍히고, 그러면 모델이 원계약 금액 자리에
    해지 금액을 답한다 (Q-066 의 v2 A8 오답이 정확히 그 값이다).
    두 칸 다 `include_followups`(= `followup_hop` arm) 로 함께 가린다 — 출처가 같은
    `※ 관련공시` 셀이라 `L4` 의 정의가 갈라지면 안 된다.

    `markdown` 을 함께 실어 돌려준다. 체인을 찾아 놓고 근거 블록을 다시 조회해야 하면
    스텝이 두 배가 된다.
    """
    origin = _by_rcept().get(rcept_no)
    if not origin:
        return {"error": f"레코드 없음: {rcept_no}"}

    by_rcept = _by_rcept()
    members = [origin]
    for related in origin.get("related") or []:
        record = by_rcept.get(related)
        if record is not None and record["rcept_no"] != rcept_no:
            members.append(record)
    members.sort(key=lambda r: (str(r.get("rcept_dt", "")), r["rcept_no"]))
    latest = members[-1]["rcept_no"]

    chain = [_chain_entry(r, "origin" if r["rcept_no"] == rcept_no else
                          ("correction" if r.get("is_correction") else "related"),
                          r["rcept_no"] == latest)
             for r in members]

    followups = []
    if include_followups:
        known = {r["rcept_no"] for r in members}
        for source in _followup_index().get(rcept_no, ()):
            record = by_rcept.get(source)
            if record is None or record["rcept_no"] in known:
                continue
            followups.append(_chain_entry(record, "followup",
                                          bool(record.get("is_latest", True))))
        followups.sort(key=lambda e: (e["rcept_dt"], e["rcept_no"]))

    # 🔴 순방향 — 이 공시가 **해지한** 원계약(+그 원계약의 정정 체인). v3 R2 ④.
    # `followups` 와 **kind 를 갈라 둔다**: 저쪽은 '뒤에 온 다른 사안' 이고 이쪽은
    # '앞에 있던 원계약' 이라 시간 방향이 반대다. 섞으면 `_chain_note` 가 원계약을
    # '후속 공시' 로 찍어 모델이 해지금액을 원계약 금액으로 답한다 — Q-066 의 v2 오답이
    # 정확히 그 값이다(원계약 351,882,914,000 대신 해지금액 362,307,038,000).
    #
    # 🔴 **`include_followups` 로 함께 가린다.** `terminates` 도 출처가 `※ 관련공시`
    # 셀이므로(`corrections._termination_links` 가 `_reference_cells` 를 읽는다) 같은
    # `REFERS_TO` 엣지 가족이다. 따로 열어 두면 `L4`(= `followup_hop=False`)가
    # "관련공시 기여를 다 뺀 구성" 이 아니게 되어 이미 낸 −1.4%p 의 정의가 바뀐다.
    terminated = []
    if include_followups:
        known = {e["rcept_no"] for e in chain} | {e["rcept_no"] for e in followups}
        for target in (origin.get("terminates") or ()):
            record = by_rcept.get(target)
            if record is None or record["rcept_no"] in known:
                continue
            terminated.append(_chain_entry(record, "terminated",
                                           bool(record.get("is_latest", True))))
            known.add(record["rcept_no"])
        terminated.sort(key=lambda e: (e["rcept_dt"], e["rcept_no"]))

    return {"rcept_no": rcept_no, "chain_length": len(chain), "latest": latest,
            "chain": chain, "followups": followups, "terminated": terminated}


@lru_cache(maxsize=1)
def _counterparty_graph() -> dict | None:
    """발주처 2-hop 그래프 (`scripts/build_counterparty_graph.py` 산출물).

    없으면 None — 도구는 error 를 돌려주고 파이프라인은 산다 (`_section_index()` 와
    같은 패턴).
    """
    path = settings().artifacts_root / COUNTERPARTY_GRAPH_NAME
    if not path.exists():
        return None
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def query_by_counterparty(name: str, year: str | None = None) -> dict:
    """발주처 이름으로 그 발주처와 계약한 universe 기업들의 레코드를 모은다.

    밸류체인 문항의 fan-in/fan-out 완전 열거(Q-110·Q-114·Q-117)는 `query_events` 에
    계약상대 필터가 없어 구조적으로 불가하다 — `partners` 를 통째로 돌려주는 것이
    이 도구의 존재 이유다. `year` 는 거래소공시 `rcept_dt`(접수일) 접두 필터다 — 이
    도구가 보는 모집단이 정기공시가 아니라 거래소공시라 접수연도와 사업연도가 갈리는
    문제가 없다.

    비실명("글로벌 대형기업")과 보일러플레이트 안내문("...비밀유지...")은 둘 다
    노드가 아니지만 사유가 다르다 — 전자는 실체를 특정할 수 없고, 후자는 회사명이
    아니라 안내문 문장이다. 후자는 그래프의 `excluded_boilerplate` 에 이미 사유가
    기록돼 있어 재사용한다(정규식을 여기 새로 복제하지 않는다 — `scripts/
    build_counterparty_graph.py::BOILERPLATE_NOTE` 가 그 유일한 원천이다).
    """
    kind = classify(name)
    if kind == "vague":
        return {"error": f"비실명 상대('{name}')는 노드가 아니라 조회할 수 없습니다."}

    graph = _counterparty_graph()
    if graph is None:
        return {"error": "counterparty_graph.json 이 없습니다. "
                         "scripts/build_counterparty_graph.py 를 먼저 실행하세요"}

    key = canonical(name)
    node = next((n for n in graph["nodes"] if n["canonical"] == key), None)
    if node is None:
        boiler = next((b for b in graph.get("excluded_boilerplate", ())
                       if canonical(b["raw"]) == key), None)
        if boiler is not None:
            return {"error": f"'{name}' 은(는) 회사명이 아니라 안내문입니다 "
                             f"({boiler['reason']})"}
        return {"error": f"'{name}' 은(는) 2회 이상 등장하는 발주처가 아닙니다."}

    records = [
        r for r in _records()
        if r.get("counterparty") and canonical(r["counterparty"]) == key
        and (year is None or str(r.get("rcept_dt", "")).startswith(str(year)))
    ]
    return {"counterparty": node["display"], "kind": node["kind"],
            "partners": node["partners"], "records": records}


# --- 계산 도구 (policy §4) ---------------------------------------------------
# 반환 형태를 통일한다. `rounded` 는 답변에 그대로 쓰라고 주는 값이고
# `expr` 은 LLM 이 계산 과정을 설명할 때 쓰라고 주는 식이다 (LLM 은 재계산하지 않는다).

_fmt = compute.fmt_num


def _num_result(value: float, unit: str, expr: str, digits: int = 1) -> dict:
    return {"value": value, "rounded": round(value, digits), "unit": unit, "expr": expr}


def calc_pct_change(before: float, after: float) -> dict:
    return _num_result(compute.pct_change(before, after), "%",
                       f"({_fmt(after)} - {_fmt(before)}) / {_fmt(before)} × 100")


def calc_share(part: float, whole: float) -> dict:
    return _num_result(compute.share(part, whole), "%",
                       f"{_fmt(part)} / {_fmt(whole)} × 100")


def calc_total(values: list[float]) -> dict:
    return _num_result(compute.total(values), "",
                       " + ".join(_fmt(v) for v in values) or "0")


def calc_mean(values: list[float]) -> dict:
    return _num_result(compute.mean(values), "",
                       f"({' + '.join(_fmt(v) for v in values)}) / {len(values)}")


def calc_cagr(begin: float, end: float, years: float) -> dict:
    return _num_result(compute.cagr(begin, end, years), "%",
                       f"(({_fmt(end)} / {_fmt(begin)})^(1/{_fmt(years)}) - 1) × 100")


def calc_compare(a: float, b: float) -> dict:
    out = compute.compare(a, b)
    return {"value": out["larger"], "unit": "", "expr": f"{_fmt(a)} vs {_fmt(b)}", **out}


def calc_period(start: str, end: str) -> dict:
    """계약기간 등 두 날짜 사이의 만 년·개월.

    실측에서 HCX 가 Direct·CoT 모두 틀린 영역이다
    (2025-07-24 ~ 2033-12-31 → 8년 5개월인데 9년 5개월로 답했다).
    """
    parts = compute.period_parts(start, end)
    if parts is None:
        return {"error": f"날짜를 해석할 수 없습니다: start={start!r} end={end!r}"}
    years, months = parts
    return {"value": f"{years}년 {months}개월", "years": years, "months": months,
            "unit": "기간", "start": start, "end": end,
            "expr": f"{start} ~ {end}"}


def _spec(name: str, desc: str, props: dict, required: list[str]) -> dict:
    return {"type": "function",
            "function": {"name": name, "description": desc,
                         "parameters": {"type": "object", "properties": props,
                                        "required": required}}}


_STR = {"type": "string"}
_INT = {"type": "integer"}
_NUM = {"type": "number"}
_BOOL = {"type": "boolean"}
_NUMS = {"type": "array", "items": {"type": "number"}}

_RETRIEVAL_SPECS = [
    _spec("resolve_company", "기업 통용명을 DART corp_code·섹터로 변환한다",
          {"name": {**_STR, "description": "기업 통용명 (예: 현대차)"}}, ["name"]),
    _spec("query_events", "계약·투자·해지 등 정형 이벤트를 조회한다 (기본 최신 정정본)",
          {"corp_name": _STR, "doc_subtype": _STR, "year": _INT,
           "min_amount": _NUM, "is_latest": _BOOL}, []),
    _spec("list_filings", "정기공시 문서 목록을 조회한다",
          {"corp_name": _STR, "doc_group": _STR, "base_year": _INT}, ["corp_name"]),
    _spec("list_sections", "보고서의 목차 경로와 글자수를 조회한다 (기재생략 스텁 제외)",
          {"doc_id": _STR, "include_stubs": _BOOL}, ["doc_id"]),
    _spec("read_section", "특정 섹션 원문을 읽는다 (표는 마크다운). "
                          "긴 섹션은 조각으로 나뉘어 있고 part 로 하나만 읽을 수 있다",
          {"doc_id": _STR, "path": _STR, "part": _INT}, ["doc_id", "path"]),
    _spec("search_chunks", "목차로 찾기 어려운 주제를 하이브리드 검색한다. "
                           "거래소·주요사항·지분 공시는 기간(year/ym)을 주면 랭킹이 크게 좋아진다. "
                           "정기공시 섹션을 찾으려면 group='periodic' 과 함께 "
                           "base_year(사업연도)·doc_subtype·path 를 준다 — "
                           "'2023년 사업보고서' 는 2024년에 접수되므로 year 로는 못 찾는다",
          {"query": _STR, "corp_name": _STR, "top_k": _INT,
           "year": {**_INT, "description": "접수 연도 (예: 2023). 정기공시에는 쓰지 않는다"},
           "ym": {**_STR, "description": "접수 연월 (예: 202301). year 보다 우선"},
           "group": {**_STR, "description": "periodic 이면 정기공시 섹션 인덱스를 본다"},
           "base_year": {**_INT, "description": "정기공시 사업연도 (예: 2025)"},
           "doc_subtype": {**_STR, "description": "정기공시 종류 — annual · half · quarter"},
           "path": {**_STR, "description": "목차 경로 접두 (예: 'III. 재무에 관한 사항')"},
           "include_stubs": _BOOL},
          ["query"]),
    _spec("get_history", "공시 정정 이력 체인과 후속 공시(해지·철회)를 추적한다. "
                         "체인은 접수일 오름차순이고 마지막 건이 최신본이다 — "
                         "금액이 큰 쪽이 최신본이 아니다 (줄어드는 체인이 있다)",
          {"rcept_no": _STR,
           "include_followups": {**_BOOL,
                                 "description": "※관련공시로 이어진 후속 공시도 함께 "
                                                "돌려준다 (기본 true)"}},
          ["rcept_no"]),
    _spec("query_by_counterparty", "발주처(계약상대) 이름으로 그 발주처와 계약한 "
                                   "기업 전체(partners)와 관련 레코드를 조회한다. "
                                   "'A 사와 같은 발주처와 계약한 기업이 몇 개사인가' "
                                   "같은 fan-in/fan-out 완전 열거는 이 도구가 아니면 답할 "
                                   "수 없다 — query_events 에는 계약상대 필터가 없다",
          {"name": {**_STR, "description": "발주처(계약상대) 이름 (예: 한국수력원자력)"},
           "year": {**_STR, "description": "접수연도 4자리 또는 접수연월 6자리 접두 "
                                           "(예: '2024', '202407'). 정기공시 사업연도가 "
                                           "아니라 거래소공시 접수일 기준이다"}},
          ["name"]),
]

# policy §4 — 수치 연산은 전부 여기를 통과해야 한다. LLM 이 직접 계산하면 안 된다.
_CALC_SPECS = [
    _spec("calc_pct_change", "증감률(%)을 계산한다. 전기 대비 증가율·감소율은 반드시 이 도구를 쓴다",
          {"before": {**_NUM, "description": "기준 시점 값 (0 불가)"},
           "after": {**_NUM, "description": "비교 시점 값"}}, ["before", "after"]),
    _spec("calc_share", "비중·구성비(%)를 계산한다. 매출액 대비 비율 등에 쓴다",
          {"part": {**_NUM, "description": "부분 값"},
           "whole": {**_NUM, "description": "전체 값 (0 불가)"}}, ["part", "whole"]),
    _spec("calc_total", "여러 수치를 합산한다. 단위는 호출자가 맞춰서 넣는다",
          {"values": {**_NUMS, "description": "합산할 수치 목록"}}, ["values"]),
    _spec("calc_mean", "여러 수치의 산술평균을 계산한다",
          {"values": {**_NUMS, "description": "평균낼 수치 목록 (비어 있으면 불가)"}}, ["values"]),
    _spec("calc_cagr", "연평균 성장률 CAGR(%)을 계산한다. 여러 해에 걸친 성장률은 이 도구를 쓴다",
          {"begin": {**_NUM, "description": "기초 값 (0 초과)"},
           "end": {**_NUM, "description": "기말 값"},
           "years": {**_NUM, "description": "경과 연수 (0 초과)"}}, ["begin", "end", "years"]),
    _spec("calc_compare", "두 수치의 대소·차이·배수를 비교한다. 순위 판단에 쓴다",
          {"a": {**_NUM, "description": "첫 번째 값"},
           "b": {**_NUM, "description": "두 번째 값"}}, ["a", "b"]),
    _spec("calc_period", "두 날짜 사이의 기간을 '몇 년 몇 개월'로 계산한다. "
                         "계약기간 산정은 반드시 이 도구를 쓴다 (직접 계산하면 틀린다)",
          {"start": {**_STR, "description": "시작일 (예: 2025-07-24)"},
           "end": {**_STR, "description": "종료일 (예: 2033-12-31)"}}, ["start", "end"]),
]

TOOL_SPECS = _RETRIEVAL_SPECS + _CALC_SPECS

#: 🔴 **`A24b` 가 내미는 목록.** `TOOL_SPECS` 15종을 전부 내민 `A24` 는 모델이 부른 47건
#: 중 **26건이 검색**이었고(`read_section` 16 · `get_history` 10), 이미 프롬프트에 실려
#: 있는 근거를 다시 읽어 **희석시켰다** — 회귀 8건 중 7건이 도구 호출 문항이다
#: (`.superpowers/sdd/2026-08-14-ablation-v4/A24-function-calling.md` §4b ①).
#: 계산 7종만 내밀면 그 차선이 아예 닫힌다.
CALC_SPECS = list(_CALC_SPECS)

RETRIEVAL_TOOLS = frozenset(s["function"]["name"] for s in _RETRIEVAL_SPECS)
CALC_TOOLS = frozenset(s["function"]["name"] for s in _CALC_SPECS)

_DISPATCH = {
    "resolve_company": resolve_company_tool,
    "query_events": query_events,
    "list_filings": list_filings,
    "list_sections": list_sections,
    "read_section": read_section,
    "search_chunks": search_chunks,
    "get_history": get_history,
    "query_by_counterparty": query_by_counterparty,
    "calc_pct_change": calc_pct_change,
    "calc_share": calc_share,
    "calc_total": calc_total,
    "calc_mean": calc_mean,
    "calc_cagr": calc_cagr,
    "calc_compare": calc_compare,
    "calc_period": calc_period,
}


def execute(name: str, args: dict) -> dict:
    fn = _DISPATCH.get(name)
    if not fn:
        return {"error": f"알 수 없는 도구: {name}"}
    try:
        return fn(**args)
    except TypeError as exc:
        return {"error": f"인자 오류: {exc}"}
    except ValueError as exc:
        # 0 나눗셈·빈 목록 등 계산이 성립하지 않는 입력. 파이프라인을 죽이지 않는다.
        return {"error": f"계산 오류: {exc}"}
    except (KeyError, OSError) as exc:
        # 인덱스 컬럼 누락·블롭 부재 등 산출물 불일치. 도구 하나가 죽어도 다음 수단이 있다.
        return {"error": f"인덱스 오류: {type(exc).__name__} {exc}"}
