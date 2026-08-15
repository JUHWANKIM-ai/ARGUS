"""공시 표 -> 정규화 레코드 (CONVENTION §7.3 스키마).

문서군마다 표의 **생김새가 다르다**. 하나의 스키마로 뭉개면 값이 통째로 빈다.

    exchange  거래소 KIND 서식.  `라벨 | 값` 2단. row[-1] 이 값이다.
    major     주요사항보고서 28종. 서식마다 필드가 다르다(권면총액·전환가액·자금용도…).
    holding   대량보유상황보고서 1종. 요약정보가 **머리행 + 직전/이번 2행**의 격자다.

Phase H 실측: major 0/4 · holding 1/4. 원인은 생성이 아니라 추출 누락이었다 —
근거 마크다운에 권면총액·전환가액·보유비율이 한 줄도 없었다. 원문 XML 에는 있다.
"""
from __future__ import annotations

import re
from pathlib import Path

from argus.config import settings
from argus.corpus.dates import parse_date
from argus.corpus.html_doc import extract_rows, extract_title, read_document

_NUM = re.compile(r"-?[\d,]+(?:\.\d+)?")
_TITLE_DATE = re.compile(r"^\(\d{4}[.\-]\d{2}[.\-]\d{2}\)\s*")
_WS_ALL = re.compile(r"\s+")

# 계약 서식 전용 라벨에 의존하는 doc_subtype.
CONTRACT_SUBTYPES = {"단일판매공급계약체결", "단일판매공급계약해지"}
# 라벨 확장으로 금액을 회수하는 서식. 신규시설투자는 `투자금액(원)` 이 전건에 있고,
# 투자판단은 금액이 자유서술문 안에 있다.
INVESTMENT_SUBTYPES = {"신규시설투자등", "투자판단관련주요경영사항"}

# --- 자유서술 금액 (투자판단 서식) -------------------------------------------
# 이 서식에는 금액 칸이 없고 `"2. 투자 금액: 5.9조원"` 처럼 문장 안에 들어 있다.
_MAGNITUDE = {"조": 10**12, "억": 10**8, "천만": 10**7, "백만": 10**6, "만": 10**4}
_FREE_AMOUNT = re.compile(
    r"(계약|공급|수주|투자|취득|처분|양수|양도|거래|매매|발행|조달|출자|인수)?\s*"
    r"(?:금\s*액|규모|대금|가액)"
    r"[^0-9<>\n]{0,10}?"
    r"(\d[\d,]*(?:\.\d+)?)\s*"
    r"(조|억|천만|백만|만)?\s*"
    r"(원|달러|USD|\$)?"
)
# `$58억` 처럼 통화기호가 숫자 **앞**에 붙는 표기도 외화다. 뒤만 보면 놓친다.
_FOREIGN_BEFORE = re.compile(r"(?i)(?:\$|USD|US\s*\$|달러|유로|EUR|엔|위안)\s*$")
_FOREIGN_AFTER = {"달러", "USD", "$"}
# 100만원 미만은 금액 표기가 아니라 수량·건수일 확률이 높다.
_MIN_AMOUNT = 10**6

# `1조 1,500억원` 처럼 큰 단위와 작은 단위를 **이어 쓴** 표기. 앞 단위에서 끊으면
# 1조가 되고 1,500억이 통째로 사라진다 — 이마트 20241114800538 의 매매대금이
# 1조 1,500억원인데 정규화 필드가 1,000,000,000,000 이었다.
_AMOUNT_TAIL = re.compile(r"\s*(\d[\d,]*(?:\.\d+)?)\s*(억|천만|백만|만)")
_WON_TAIL = re.compile(r"\s*원")


def _compound_tail(cell: str, pos: int, magnitude: str) -> float:
    """`1조 1,500억원` 의 꼬리(`1,500억`)를 원 단위로 환산해 돌려준다. 없으면 0.

    두 조건을 **둘 다** 만족할 때만 붙인다.

        ① 단위가 내림차순이다 (조 -> 억 -> 천만 -> 백만 -> 만)
        ② 마지막 조각이 `원` 으로 끝난다

    ②가 없으면 `5,000억 100만주` 의 `100만` 을 금액으로 더한다 — 그건 주식수이고,
    값이 비는 것보다 나쁘다.
    """
    extra, scale, cursor = 0.0, _MAGNITUDE[magnitude], pos
    while (part := _AMOUNT_TAIL.match(cell, cursor)) is not None:
        step = _MAGNITUDE[part.group(2)]
        if step >= scale:
            return 0.0
        try:
            extra += float(part.group(1).replace(",", "")) * step
        except ValueError:
            return 0.0
        scale, cursor = step, part.end()
        if _WON_TAIL.match(cell, cursor):
            return extra
    return 0.0


def free_text_amount(cells: list[str]) -> tuple[int, str] | None:
    """자유서술문에서 **원화** 금액을 뽑는다. `(금액, 금액종류)` 또는 None.

    셀을 **뒤에서부터** 본다. 정정 표가 `[항목, 정정전, 정정후]` 순서라
    앞에서부터 읽으면 고쳐지기 전의 옛 금액을 집는다.

    외화는 버린다. `$58억` 을 58억원으로 읽으면 금액이 통째로 틀리는데,
    이건 값이 비는 것보다 나쁘다.
    """
    for cell in reversed(cells):
        for match in _FREE_AMOUNT.finditer(cell):
            kind, number, magnitude, currency = match.groups()
            currency = (currency or "").strip()
            if currency in _FOREIGN_AFTER:
                continue
            if _FOREIGN_BEFORE.search(cell[:match.start(2)][-12:]):
                continue
            if not magnitude and currency != "원":
                continue          # 단위가 없으면 금액이라 단정할 수 없다
            try:
                value = float(number.replace(",", ""))
            except ValueError:
                continue
            if magnitude:
                value = value * _MAGNITUDE[magnitude] + _compound_tail(
                    cell, match.end(3), magnitude)
            if value < _MIN_AMOUNT:
                continue
            return int(value), f"{kind or ''}금액"
    return None


def _to_number(text: str) -> float | None:
    match = _NUM.search(text or "")
    if not match:
        return None
    try:
        return float(match.group(0).replace(",", ""))
    except ValueError:
        return None


def _clean(value: str | None) -> str | None:
    """DART 표는 빈 값을 '-' 로 채운다. None 으로 정규화한다."""
    if value is None:
        return None
    stripped = value.strip()
    return None if stripped in {"-", "–", "—", ""} else stripped


def _labels(row: list[str]) -> str:
    """값 셀(마지막)을 뺀 라벨 부분. 공백은 지운다.

    계획서 원안은 행 전체를 이어 붙여 키워드를 찾았는데, 그러면 서술형 값이
    라벨로 오인된다. 대표 사례가 정정공시의 '3. 정정사유 | 변경계약으로 인한
    계약금액 및 계약기간 정정' 행이다. 여기에 '계약금액' 이 들어 있어서 진짜
    금액 행보다 먼저 걸리고, 숫자 대신 문장이 잡혀 222건이 통째로 비었다.
    라벨 칸만 보면 이 오염이 사라진다.

    공백을 지우는 이유는 서식마다 '매출액대비(%)' 와 '매출액 대비(%)' 가
    섞이기 때문이다.
    """
    return _WS_ALL.sub("", " ".join(row[:-1]))


def _rows_matching(rows: list[list[str]], *keywords: str):
    """라벨에 키워드가 모두 들어간 행들을 순서대로 준다."""
    for row in rows:
        if len(row) < 2:
            continue  # 값 칸이 없는 서술 행
        if all(_WS_ALL.sub("", k) in _labels(row) for k in keywords):
            yield row


def _find(rows: list[list[str]], *keywords: str) -> str | None:
    """라벨이 맞는 행의 값 셀을 돌려준다. 값이 비었으면 다음 후보를 계속 본다."""
    for row in _rows_matching(rows, *keywords):
        value = _clean(row[-1])
        if value is not None:
            return value
    return None


def _find_number(rows: list[list[str]], *keywords: str) -> float | None:
    """값이 숫자로 읽히는 첫 행을 찾는다. 숫자가 아니면 건너뛴다."""
    for row in _rows_matching(rows, *keywords):
        number = _to_number(_clean(row[-1]) or "")
        if number is not None:
            return number
    return None


def _find_date(rows: list[list[str]], *keywords: str):
    """값이 날짜로 읽히는 첫 행을 찾는다."""
    for row in _rows_matching(rows, *keywords):
        parsed = parse_date(_clean(row[-1]) or "")
        if parsed is not None:
            return parsed
    return None


_PURE_NUMBER = re.compile(r"^-?\d[\d,]*(?:\.\d+)?$")


def _find_loose(rows: list[list[str]], *keywords: str) -> str | None:
    """값 칸이 **마지막이 아닌** 행을 위한 폴백. 행 전체로 매칭하고 순수 숫자 칸을 준다.

    실측 사례 — 유상증자 서식의 발행가액은 하위머리(rowspan)가 붙으면 이렇게 된다.

        ['6. 신주 발행가액', '예정발행가', '보통주식 (원)', '169,200', '확정예정일', '2025년 05월 22일']

    `row[-1]` 은 확정예정일이다. 발행가액 자리에서 날짜를 집으면 값이 통째로 틀린다.
    라벨 칸(row[:-1])만 보는 `_find` 로도 안 잡히므로 행 전체를 본다.
    """
    normalized = [_WS_ALL.sub("", k) for k in keywords]
    for row in rows:
        joined = _WS_ALL.sub("", " ".join(row))
        if not all(k in joined for k in normalized):
            continue
        for cell in row:
            text = _clean(cell)
            if text and _PURE_NUMBER.match(text):
                return text
    return None


_LABEL_TAIL = re.compile(r"(?:[(（][^)）]*[)）]|[:：])+$")


def _find_beside(rows: list[list[str]], keyword: str) -> str | None:
    """`라벨 | 값 | 라벨 | 값` 4칸 행에서 라벨 **바로 옆** 칸을 읽는다.

    지분공시 요약정보의 첫 행이 그렇다.

        ['발행회사명', '두산에너빌리티(주)', '발행회사와의 관계', '최대주주']

    `row[-1]` 을 값으로 쓰면 발행회사명이 '최대주주' 가 된다 — 실측으로 1,069건이
    그렇게 나갔다. 라벨 꼬리의 단위 표기(`(주)`)와 콜론은 떼고 정확히 맞춘다.
    """
    key = _LABEL_TAIL.sub("", _WS_ALL.sub("", keyword))
    for row in rows:
        for index, cell in enumerate(row[:-1]):
            if _LABEL_TAIL.sub("", _WS_ALL.sub("", cell)) == key:
                value = _clean(row[index + 1])
                if value is not None:
                    return value
    return None


def _find_after(rows: list[list[str]], anchor: tuple[str, ...],
                keywords: tuple[str, ...], span: int = 3) -> str | None:
    """anchor 행 **바로 다음** 몇 행에서 값을 찾는다 (rowspan 으로 갈라진 형제 행).

    `['3. 처분예정금액(원)', '보통주식', '287,545,592,500']` 다음 행은
    `['기타주식', '-']` 이다. 형제 행에는 머리 라벨이 없으므로 `_find` 로는 못 찾고,
    라벨 없이 '기타주식' 만 찾으면 문서 어디서든 걸려 엉뚱한 값을 집는다.
    """
    normalized_anchor = [_WS_ALL.sub("", k) for k in anchor]
    normalized = [_WS_ALL.sub("", k) for k in keywords]
    for index, row in enumerate(rows):
        if len(row) < 2 or not all(k in _labels(row) for k in normalized_anchor):
            continue
        for sibling in rows[index + 1: index + 1 + span]:
            if len(sibling) >= 2 and all(k in _labels(sibling) for k in normalized):
                return _clean(sibling[-1])
    return None


def _doc_path(doc: dict) -> Path:
    folder = settings().corpus_root / doc["file_path"]
    xml = sorted(folder.glob("*.xml"))
    if not xml:
        raise FileNotFoundError(f"XML 없음: {folder}")
    return xml[0]


#: 필드 값 한 개의 상한. 서술형 칸(전환가액 조정방법 등)이 통째로 실리면 근거가 폭주한다.
FIELD_MAX_CHARS = 90
#: 한 레코드가 마크다운으로 내보내는 필드 수 상한.
FIELD_MAX_COUNT = 12


def _shorten(value: str | None) -> str | None:
    if value is None:
        return None
    return value if len(value) <= FIELD_MAX_CHARS else value[:FIELD_MAX_CHARS].rstrip() + "…"


def _collect(rows: list[list[str]], spec, limit: int = FIELD_MAX_COUNT) -> dict[str, str]:
    """`(표시라벨, 키워드들)` 목록대로 값을 모은다. 없는 필드는 **넣지 않는다**.

    빈 필드를 `-` 로 채우면 마크다운만 길어지고 LLM 은 값이 있다고 착각한다.
    """
    fields: dict[str, str] = {}
    for label, keywords in spec:
        if len(fields) >= limit:
            break
        value = _find(rows, *keywords)
        if value is not None:
            fields[label] = _shorten(value)
    return fields


def _base_record(doc: dict, rows: list[list[str]]) -> dict:
    """문서군과 무관하게 같은 키를 갖는 레코드 뼈대. 소비자(API·평가)가 키를 가정한다."""
    return {
        "doc_id": doc["doc_id"],
        "rcept_no": doc["rcept_no"],
        "rcept_dt": doc["rcept_dt"],
        "corp_code": doc["corp_code"],
        "corp_name": doc["corp_name"],
        "sector": doc["sector"],
        "doc_subtype": doc["doc_subtype"],
        "is_correction": bool(doc["is_correction"]),
        "amount": None,
        "amount_type": None,
        "ratio": None,
        "ratio_base": None,
        "counterparty": None,
        "period_start": None,
        "period_end": None,
        "event_date": None,
        "reserved_reason": None,
        "note": None,
        "related": [],
        "form": None,
        "fields": {},
        "corrections": [],
        "schema_matched": False,
        "withheld": False,
        "raw_rows": rows[:40],
    }


#: KIND 서식 본문의 첫 항목(`1. 제목` · `1. 판매ㆍ공급계약 구분`). 정정 표가 여기서 끝난다.
#: major·holding 의 `_is_body_start`(귀중·회 사 명)를 그대로 쓰면 안 된다 — exchange
#: 서식에는 그 표지가 **한 건도 없어서** 구간 끝이 문서 끝이 되고 본문이 통째로 날아간다.
_KIND_ITEM1 = re.compile(r"^\s*1\s*[.．]\s*\S")


def exchange_correction_span(rows: list[list[str]]) -> tuple[int, int] | None:
    """exchange 기재정정 표의 `(머리행, 끝)`. 없으면 None.

    실측(exchange 1,469건): 정정본 631건 전건에 `정정전`·`정정후` 머리행이 있고
    원공시 838건에는 **한 건도 없다**(오검출 0). 머리행 뒤에서 `1.` 로 시작하는
    본문 첫 행도 631/631 에서 찾힌다. 머리행 **뒤**부터 찾는 게 중요하다 —
    정정 메타의 `1. 정정관련 공시서류` 가 머리행 앞에 있다.
    """
    for index, row in enumerate(rows):
        flat = _WS_ALL.sub("", "".join(row))
        if "정정전" in flat and "정정후" in flat:
            end = next((i for i in range(index + 1, len(rows))
                        if rows[i] and _KIND_ITEM1.match(rows[i][0])), len(rows))
            return index, end
    return None


def exchange_body_rows(rows: list[list[str]]) -> list[list[str]]:
    """정정 표를 뺀 서식 본문. **exchange 필드 추출은 반드시 여기서 해야 한다.**

    정정 표의 한 행은 `[항목, 정정전, 정정후]` 이고 값 칸이 **서술 블록 통째**다.
    행 전체로 라벨을 찾는 `_labels` 는 row[:-1] 을 라벨로 보므로, 3칸 행에서는
    **정정 전 블록이 라벨이 된다** — 그 안의 키워드가 걸리고 값으로는 정정 후 블록의
    첫 숫자가 잡힌다. 실측: 크래프톤 20260306801011 의 `자기자본대비` 가 정정 전
    블록에서 걸리고 값이 정정 후 블록 첫머리 `(1) 조합명` 의 **1** 로 읽혀
    `ratio=1.0` 이 됐다 (본문은 2.93%).

    금액도 같은 행에서 어긋난다. `free_text_amount` 는 칸을 뒤에서부터 봐서 정정 후를
    먼저 읽지만, 정정 후 칸이 단위(`원`)를 빼먹으면 그 값이 기각되고 **같은 행의 정정
    전 칸**으로 흘러내린다. 실측: NAVER 20260130800888 의 매매대금이 정정 후
    `902,863,854,765`(원 없음) 대신 정정 전 `903,559,577,175원` 으로 굳었다.
    """
    span = exchange_correction_span(rows)
    return rows if span is None else rows[:span[0]] + rows[span[1]:]


# --- 자유서술 본문 (schema_matched=False 서식) ---------------------------------
# 🔴 정형 칸이 비면 값이 **없는 게 아니라 서술문 안에** 있다. KIND 투자판단 서식에는
# 금액 칸 자체가 없고, 계약 서식도 금액을 유보하면 `계약금액(원) | -` 이 되고 실제
# 내용이 `9. 기타 …` 로 내려간다. 실측(현대제철 20250325800001): 정형 필드가 전부
# 비어 근거가 108자 · 숫자 0개인데 원문 `2. 주요내용` 에 `○ 투자금액 : $58억` 이 있다.
#
# 세 라벨만 본다 — 이 서식이 서술문을 싣는 자리가 그 셋뿐이다. 나머지(참석 이사 수 ·
# 관련공시 목록)는 근거를 늘리기만 한다.
_FREETEXT_LABEL = re.compile(r"^\s*\d+\s*[.．]\s*(제\s*목|주요\s*내용|기타\s*\S*)")

#: 자유서술 한 칸의 상한. 필드값 상한(`FIELD_MAX_CHARS` 90)보다 **일부러 크다** —
#: 이 칸은 요약이 아니라 본문이고, 겨냥한 금액이 칸 **끝**에 오는 실측이 있다:
#: 20250325800001 의 `$58억` 은 240자 칸의 205번째 자리, 원화환산 `8조 5,080억원` 은
#: 501자 칸의 113번째 자리다. 90 으로 자르면 겨냥한 값이 그대로 잘려 나간다.
#:
#: 🔴 **v4 `R1-4` — 이 상한이 `Q-087` 의 정답을 자르는 것이 맞다. 그래도 안 올렸다.**
#: `20241114800538` 의 `2. 주요내용` 은 623자이고 `2) 매수인 : 올림푸스제일차` 가 440번째
#: 자리라 **500 이상**이어야 들어온다. 그런데 ① 그 레코드는 `schema_matched=True` 라
#: 게이트(`markdown._free_text_lines`)가 먼저 닫고 ② 300→500 만으로도 238건 중 **171건**의
#: 마크다운이 바뀐다. 겨냥 1문항에 코퍼스 절반 재작성이라 `A17` 을 취소했다 —
#: 사유는 `markdown._free_text_lines` 독스트링에, 수는 `tests/test_evidence_truncation.py` 에 있다.
FREETEXT_MAX_CHARS = 300
#: 한 레코드가 실을 자유서술 칸 수 상한. 근거 폭주 방지.
FREETEXT_MAX_CELLS = 4


def free_text_items(rows: list[list[str]]) -> list[tuple[str, str]]:
    """자유서술 본문 `(라벨, 값)` 목록. **정정 표는 뺀다.**

    라벨행은 두 모양으로 온다 — 값이 같은 행에 있는 것(`['2. 주요내용', '○ …']`)과
    라벨만 있고 다음 행이 값인 것(`['4. 기타 …']` + `['1) 본 건은 …']`). 실측
    238건에서 앞엣것 389칸 · 뒤엣것 230칸이라 **둘 다** 받아야 한다.

    정정 표를 `exchange_body_rows` 로 먼저 떼는 이유는 그 함수의 주석과 같다 —
    정정 표 행은 `[항목, 정정전, 정정후]` 라 그대로 읽으면 **대체된 옛 값**이 근거에
    실린다.
    """
    body = exchange_body_rows(rows)
    items: list[tuple[str, str]] = []
    used: set[int] = set()
    for index, row in enumerate(body):
        if len(items) >= FREETEXT_MAX_CELLS:
            break
        if index in used or not row or not _FREETEXT_LABEL.match(row[0]):
            continue
        label = _WS_ALL.sub(" ", _FREETEXT_LABEL.sub(r"\1", row[0])).strip()
        values = [c for c in row[1:] if _clean(c)]
        if not values:
            nxt = body[index + 1] if index + 1 < len(body) else None
            if not nxt or _FREETEXT_LABEL.match(nxt[0] or ""):
                continue
            values = [c for c in nxt if _clean(c)]
            used.add(index + 1)
        for value in values[:1]:            # 라벨 하나에 값 하나. 짝이 늘면 근거가 부푼다
            items.append((label, _clip_free(value)))
    return items


def _clip_free(value: str) -> str:
    text = _WS_ALL.sub(" ", value).strip()
    return text if len(text) <= FREETEXT_MAX_CHARS else text[:FREETEXT_MAX_CHARS] + "…"


def normalize_exchange(doc: dict) -> dict:
    html = read_document(_doc_path(doc))
    rows = extract_rows(html)
    body = exchange_body_rows(rows)           # 정정 표는 필드 추출에서 반드시 뺀다
    title = extract_title(html)

    # 해지 서식은 '2. 해지내역 | 해지금액(원)' 을 쓴다. 체결 서식의 '계약금액' 과 별개다.
    # 신규시설투자 서식은 '2. 투자내역 | 투자금액(원)' 이다 — 라벨만 달라서 43건이
    # 통째로 비어 있었다.
    amount, amount_type = None, "계약금액"
    for label in ("계약금액", "해지금액", "투자금액"):
        found = _find_number(body, label)
        if found is not None:
            amount, amount_type = found, label
            break

    # 그래도 없으면 자유서술문을 본다 (투자판단 서식).
    if amount is None:
        for row in body:
            free = free_text_amount(row)
            if free is not None:
                amount, amount_type = free
                break

    # 계약 서식은 '매출액대비(%)', 투자 서식은 '자기자본대비(%)' 다. 기준이 다르므로
    # ratio_base 를 같이 바꿔야 한다 — 안 그러면 자기자본 비율이 매출 비율로 읽힌다.
    ratio, ratio_base = _find_number(body, "매출액대비"), "매출액"
    if ratio is None:
        equity_ratio = _find_number(body, "자기자본대비")
        if equity_ratio is not None:
            ratio, ratio_base = equity_ratio, "자기자본"
    start = _find_date(body, "시작일")
    end = _find_date(body, "종료일")
    # 라벨은 '확정일자' 가 아니라 '7. 계약(수주)일자' 다. 해지 서식은 '6. 해지일자'.
    # 어느 쪽도 없으면 접수일로 폴백한다.
    event = (_find_date(body, "계약", "일자")
             or _find_date(body, "수주", "일자")
             or _find_date(body, "해지일자")
             or parse_date(doc["rcept_dt"]))

    # <title> 은 서식명('단일판매ㆍ공급계약체결')뿐이라 검색어가 되지 못한다.
    # 실제 내용은 '- 체결계약명 | 2500kVA 배전변압기 등 3,500대' 행에 있다.
    # 이걸 제목으로 올려야 "변압기 공급계약" 같은 질의에 걸린다.
    subject = (_find(body, "체결계약명")
               or _find(body, "해지계약명")
               or _find(body, "계약명")
               or _find(body, "투자대상")
               or _find(body, "사고발생내용"))

    raw_title = title.split("/")[-1].strip() if title else doc["report_nm"]
    form_name = _TITLE_DATE.sub("", raw_title).strip() or doc["report_nm"]
    clean_title = f"{form_name} — {subject}" if subject else form_name

    # 금액 칸이 있는데 값이 '-' 이고 유보사유가 붙어 있으면 발행사가 값을 비공개한
    # 것이다(경영상 비밀유지 등). 계약 서식 60건이 여기 해당한다. 이건 파서가 못
    # 읽은 게 아니라 원본에 값이 없는 것이라, Phase H 오류분석이 '파싱 실패' 와
    # 섞지 않도록 따로 표시한다. schema_matched 를 True 로 올리면 안 된다.
    reserved_reason = _find(body, "유보사유")
    withheld = amount is None and reserved_reason is not None

    record = _base_record(doc, rows)
    record.update({
        "title": clean_title,
        "form": doc["doc_subtype"],
        "amount": int(amount) if amount is not None else None,
        # 🔴 금액을 못 찾았으면 레이블도 없다. 종전에는 루프 초기값 `"계약금액"` 이
        # 그대로 남아, 금액 칸이 아예 없는 투자판단 서식이 `amount=None ·
        # amount_type="계약금액"` 이라는 **없는 사실**을 들고 있었다(20250325800001).
        # 마크다운은 한 글자도 안 바뀐다 — `_amount_line` 이 `amount is None` 에서
        # 먼저 빠진다. `critic._says_amount` 도 두 호출부가 `amount is not None`
        # 안에 있어 안 바뀐다.
        "amount_type": amount_type if amount is not None else None,
        "ratio": ratio,
        "ratio_base": ratio_base,
        "counterparty": _find(body, "계약상대"),
        "period_start": start.isoformat() if start else None,
        "period_end": end.isoformat() if end else None,
        "event_date": event.isoformat() if event else None,
        "reserved_reason": reserved_reason,
        "schema_matched": (doc["doc_subtype"] in CONTRACT_SUBTYPES | INVESTMENT_SUBTYPES
                           and amount is not None),
        # 값이 없는 이유를 구분한다. withheld=True 는 '원본에 값이 없음',
        # schema_matched=False 이면서 withheld=False 가 진짜 미회수다.
        "withheld": withheld,
    })
    return record


# =============================================================================
# 주요사항보고서(major) — 서식 28종
# =============================================================================
# manifest 의 doc_subtype 은 major 598건 **전부 None** 이다. 서식은 report_nm 괄호
# 안에만 있다. 서식마다 필드가 다르므로 한 벌의 스키마로 뭉개면 안 된다.

_FORM_TAIL = re.compile(r"\(([^()]*)\)\s*$")
_CORRECTION_TAG = re.compile(r"^\s*\[기재정정\]\s*")


def major_form(report_nm: str | None) -> str:
    """`[기재정정]주요사항보고서(유상증자결정)` -> `유상증자결정`."""
    match = _FORM_TAIL.search(report_nm or "")
    if match:
        return match.group(1).strip()
    return _CORRECTION_TAG.sub("", report_nm or "").strip()


#: 사채·증자 서식 공통 블록 '자금조달의 목적'. 하위 6개 항목은 rowspan 으로 갈라져
#: 머리 라벨('4. 자금조달의 목적')이 첫 행에만 붙는다. 그래서 하위 라벨만으로 찾는다.
_FUNDING = (
    ("시설자금", ("시설자금",)),
    ("영업양수자금", ("영업양수자금",)),
    ("운영자금", ("운영자금",)),
    ("채무상환자금", ("채무상환자금",)),
    ("타법인 증권취득자금", ("타법인", "취득자금")),
    ("기타자금", ("기타자금",)),
)
_FUNDING_LABELS = tuple(label for label, _ in _FUNDING)

_BOND = (
    ("사채의 종류", ("사채의종류",)),
    *_FUNDING,
    ("표면이자율", ("표면이자율",)),
    ("만기이자율", ("만기이자율",)),
    ("사채만기일", ("사채만기일",)),
    ("사채발행방법", ("사채발행방법",)),
)
_TREASURY_HOLDING = (("자기주식 보유(보통주식)", ("배당가능", "보통주식")),)

#: 서식 -> ((표시라벨, 라벨 키워드), ...). 값이 없는 필드는 레코드에 안 들어간다.
MAJOR_FIELDS: dict[str, tuple[tuple[str, tuple[str, ...]], ...]] = {
    "유상증자결정": (
        ("신주의 종류와 수(보통주식)", ("신주의종류와수", "보통주식")),
        ("1주당 액면가액", ("1주당액면가액",)),
        ("증자전 발행주식총수", ("증자전발행주식총수", "보통주식")),
        *_FUNDING,
        ("증자방식", ("증자방식",)),
        ("신주배정기준일", ("신주배정기준일",)),
    ),
    "무상증자결정": (
        ("신주의 종류와 수(보통주식)", ("신주의종류와수", "보통주식")),
        ("1주당 신주배정 주식수", ("1주당신주배정",)),
        ("신주배정기준일", ("신주배정기준일",)),
    ),
    "전환사채권발행결정": (
        *_BOND,
        ("전환비율", ("전환비율",)),
        ("전환가액", ("전환가액", "원/주")),
    ),
    "교환사채권발행결정": (
        *_BOND,
        ("교환비율", ("교환비율",)),
        ("교환가액", ("교환가액", "원/주")),
    ),
    "상각형조건부자본증권발행결정": _BOND,
    "자본으로인정되는채무증권발행결정": _BOND,
    "자기주식취득결정": (
        ("취득예정주식(보통주식)", ("취득예정주식", "보통주식")),
        ("취득목적", ("취득목적",)),
        ("취득방법", ("취득방법",)),
        ("위탁투자중개업자", ("위탁투자중개업자",)),
        *_TREASURY_HOLDING,
    ),
    "자기주식처분결정": (
        ("처분예정주식(보통주식)", ("처분예정주식", "보통주식")),
        ("처분 대상 주식가격", ("처분대상주식가격", "보통주식")),
        ("처분목적", ("처분목적",)),
        ("처분방법", ("처분방법",)),
        *_TREASURY_HOLDING,
    ),
    "자기주식취득신탁계약체결결정": (
        ("계약목적", ("계약목적",)),
        ("계약체결기관", ("계약체결기관",)),
        ("계약체결 예정일자", ("계약체결예정일자",)),
    ),
    "자기주식취득신탁계약해지결정": (
        ("해지목적", ("해지목적",)),
        ("해지기관", ("해지기관",)),
        ("해지예정일자", ("해지예정일자",)),
    ),
    # '1. 합병방법' 의 값은 '상법 제530조의2 내지 …' 로 시작하는 법조문 인용이라
    # 근거만 길어지고 판단에 안 쓰인다. 같은 표의 '- 합병형태'(흡수/신설)가 실질이다.
    "회사합병결정": (
        ("합병형태", ("합병형태",)),
        ("합병목적", ("합병목적",)),
        ("합병비율", ("합병비율",)),
        ("합병상대회사", ("합병상대회사", "회사명")),
        ("합병신주(보통주식)", ("합병신주의종류와수", "보통주식")),
        ("합병기일", ("합병기일",)),
    ),
    "회사분할결정": (
        ("분할 후 존속회사", ("분할후존속회사", "회사명")),
        ("분할설립회사", ("분할설립회사", "회사명")),
        ("분할목적", ("분할목적",)),
        ("분할비율", ("분할비율",)),
        ("분할기일", ("분할기일",)),
    ),
    "회사분할합병결정": (
        ("합병형태", ("합병형태",)),
        ("분할합병 목적", ("분할합병목적",)),
        ("분할합병 기일", ("분할합병기일",)),
    ),
    "주식교환ㆍ이전결정": (
        ("교환ㆍ이전 대상법인", ("교환ㆍ이전대상법인", "회사명")),
        ("교환ㆍ이전 비율", ("교환ㆍ이전비율",)),
        ("교환ㆍ이전 목적", ("교환ㆍ이전목적",)),
        ("교환ㆍ이전일자", ("교환ㆍ이전일자",)),
    ),
    "감자결정": (
        ("감자주식(보통주식)", ("감자주식의종류와수", "보통주식")),
        ("감자비율(보통주식)", ("감자비율", "보통주식")),
        ("감자기준일", ("감자기준일",)),
        ("감자방법", ("감자방법",)),
        ("감자사유", ("감자사유",)),
    ),
    "타법인주식및출자증권양수결정": (
        ("발행회사", ("발행회사", "회사명")),
        ("양수주식수", ("양수주식수",)),
        ("양수후 지분비율", ("지분비율",)),
        ("양수목적", ("양수목적",)),
        ("양수예정일자", ("양수예정일자",)),
    ),
    "타법인주식및출자증권양도결정": (
        ("발행회사", ("발행회사", "회사명")),
        ("양도주식수", ("양도주식수",)),
        ("양도후 지분비율", ("지분비율",)),
        ("양도목적", ("양도목적",)),
    ),
    "유형자산양수결정": (
        ("자산명", ("자산명",)),
        ("양수목적", ("양수목적",)),
        ("양수예정일자", ("양수예정일자", "계약체결일")),
    ),
    "유형자산양도결정": (
        ("자산명", ("자산명",)),
        ("양도목적", ("양도목적",)),
        ("양도예정일자", ("양도예정일자", "계약체결일")),
    ),
    "영업양수결정": (
        ("양수영업", ("1.양수영업",)),
        ("양수영업 주요내용", ("양수영업주요내용",)),
        ("양수목적", ("양수목적",)),
    ),
    "자기전환사채매도결정": (
        ("사채의 종류", ("사채의종류",)),
        ("매도 목적", ("매도목적",)),
        ("전환가액", ("전환가액",)),
    ),
    "제3자의전환사채매수선택권행사": (
        ("사채의 종류", ("사채의종류",)),
        ("행사비율", ("행사비율",)),
        ("전환가액", ("전환가액",)),
    ),
    "소송등의제기": (
        ("사건의 명칭", ("사건의명칭",)),
        ("원고ㆍ신청인", ("원고",)),
        ("청구내용", ("청구내용",)),
        ("관할법원", ("관할법원",)),
        ("제기일자", ("제기일자",)),
    ),
    "영업정지": (
        ("영업정지 분야", ("영업정지분야",)),
        ("영업정지 내용", ("영업정지내용",)),
        ("영업정지사유", ("영업정지사유",)),
    ),
    "해외증권시장주권등상장폐지결정": (
        ("상장폐지주식(보통주식)", ("상장폐지주식", "보통주식")),
        ("상장거래소", ("상장거래소",)),
        ("폐지(예정)일자", ("폐지",)),
        ("폐지사유", ("폐지사유",)),
    ),
    "해외증권시장주권등상장폐지": (
        ("상장거래소", ("상장거래소",)),
        ("상장폐지주식(보통주식)", ("상장폐지주식의종류", "보통주식")),
        ("매매거래종료일", ("매매거래종료일",)),
        ("폐지사유", ("폐지사유",)),
    ),
    "해외증권시장주권등상장결정": (
        ("상장예정주식(보통주식)", ("상장예정주식", "보통주식")),
        ("상장거래소", ("상장거래소",)),
        ("해외상장목적", ("해외상장목적",)),
        ("상장예정일자", ("상장예정일자",)),
    ),
    "해외증권시장주권등상장": (
        ("상장주식(보통주식)", ("상장주식종류및수", "보통주식")),
        ("상장거래소", ("상장거래소",)),
        ("상장일자", ("상장일자",)),
    ),
}

#: 서식 -> (표시할 금액 이름, 라벨 키워드, 형제 행 키워드).
#: 형제 행은 `보통주식/기타주식` 처럼 rowspan 으로 갈라진 같은 항목이라 **더한다**.
#: 여기 없는 서식은 금액 칸이 애초에 없다 (합병·분할·소송·상장폐지 등).
MAJOR_AMOUNT: dict[str, tuple[str, tuple[str, ...], tuple[str, ...] | None]] = {
    "전환사채권발행결정": ("권면(전자등록)총액", ("권면", "총액"), None),
    "교환사채권발행결정": ("권면(전자등록)총액", ("권면", "총액"), None),
    "상각형조건부자본증권발행결정": ("권면(전자등록)총액", ("권면", "총액"), None),
    "자본으로인정되는채무증권발행결정": ("권면(전자등록)총액", ("권면", "총액"), None),
    "자기전환사채매도결정": ("매도금액", ("8.매도금액",), None),
    "제3자의전환사채매수선택권행사": ("행사대가", ("7.행사대가",), None),
    "자기주식취득결정": ("취득예정금액", ("취득예정금액", "보통주식"), ("기타주식",)),
    "자기주식처분결정": ("처분예정금액", ("처분예정금액", "보통주식"), ("기타주식",)),
    "자기주식취득신탁계약체결결정": ("계약금액", ("1.계약금액",), None),
    # 해지 서식의 '해지 후' 칸은 43건 전부 '-' 다(계약이 끝났으니 당연하다).
    # 의미 있는 금액은 '해지 전' 이다.
    "자기주식취득신탁계약해지결정": ("해지 전 계약금액", ("1.계약금액", "해지전"), None),
    "타법인주식및출자증권양수결정": ("양수금액", ("양수금액",), None),
    "타법인주식및출자증권양도결정": ("양도금액", ("양도금액",), None),
    "유형자산양수결정": ("양수금액", ("양수금액",), None),
    "유형자산양도결정": ("양도금액", ("양도금액",), None),
    "영업양수결정": ("양수가액", ("양수가액",), None),
    "영업정지": ("영업정지금액", ("영업정지금액",), None),
}

#: 금액 대비 비율. exchange 와 같은 뜻이라 ratio/ratio_base 로 올린다.
_MAJOR_RATIO = (("자기자본대비", "자기자본"), ("자산총액대비", "자산총액"),
                ("매출액대비", "매출액"), ("총자산대비", "총자산"))

#: 기간 칸이 있는 서식만. 없는 서식에서 아무 `시작일/종료일` 이나 주우면 청약예정일이
#: '기간' 으로 나간다 — 유상증자 근거에 '기간: 2025-05-27 ~ 2025-05-27' 이 그 사고다.
_MAJOR_PERIOD = {
    "자기주식취득결정": "취득예상기간",
    "자기주식처분결정": "처분예정기간",
    "자기주식취득신탁계약체결결정": "2.계약기간",
    "자기주식취득신탁계약해지결정": "해지전계약기간",
    "전환사채권발행결정": "전환청구기간",
    "교환사채권발행결정": "교환청구기간",
}


#: 정정 표가 끝나고 서식 본문이 시작되는 지점의 표지. **첫 칸만** 본다 —
#: 정정 표의 '정정 후' 칸은 블록을 통째로 담고 있어서 그 안에도 '금융위원회 귀중'
#: 이 들어 있다. 행 전체를 보면 정정 표의 첫 행에서 본문이 시작했다고 오판한다.
_BODY_START = ("귀중", "회 사 명")


def _is_body_start(row: list[str]) -> bool:
    return bool(row) and any(mark in row[0] for mark in _BODY_START)


def _correction_span(rows: list[list[str]]) -> tuple[int, int] | None:
    """기재정정 표의 `(머리행, 끝)` 구간. 없으면 None.

    머리행 위치는 문서마다 다르다 — 앞에 '제출이력' 표가 붙으면 8행이 아니라 9행째다
    (삼성SDI 20250409001154). 그래서 고정 창이 아니라 **본문 시작 전까지**를 찾는다.
    """
    body = next((i for i, row in enumerate(rows) if _is_body_start(row)), len(rows))
    for index, row in enumerate(rows[:min(body, 25)]):
        flat = _WS_ALL.sub("", "".join(row))
        if "정정전" in flat and "정정후" in flat:
            end = next((i for i in range(index + 1, len(rows)) if _is_body_start(rows[i])),
                       len(rows))
            return index, end
    return None


def correction_pairs(rows: list[list[str]]) -> list[tuple[str, str, str]]:
    """기재정정 표의 `(항목, 정정 전, 정정 후)`. 없으면 빈 목록.

    Q-030 이 정확히 이 표에 걸린다 — 원공시(유상증자결정)만 보면 '증자 진행 중'으로
    답하게 되고, '철회' 라는 판정은 정정본의 '정정 후' 칸에만 있다.

    정정사유가 rowspan 으로 합쳐지면 칸 수가 4 -> 3 으로 줄어든다. 그래서 항목은
    맨 앞 칸, 값은 **끝 두 칸**으로 읽는다. 칸 위치를 고정하면 절반이 어긋난다.
    """
    span = _correction_span(rows)
    if span is None:
        return []

    pairs: list[tuple[str, str, str]] = []
    for row in rows[span[0] + 1: span[1]]:
        if len(row) < 3 or _is_body_start(row):
            continue
        before, after = _clean(row[-2]), _clean(row[-1])
        if before is None and after is None or before == after:
            continue          # 바뀐 게 없으면 근거가 아니다
        pairs.append((row[0].strip(), before or "-", after or "-"))
    return pairs


def body_rows(rows: list[list[str]]) -> list[list[str]]:
    """정정 표를 뺀 서식 본문. **필드 추출은 반드시 여기서 해야 한다.**

    정정 표의 한 행이 블록을 통째로 담는 경우가 있다.

        ['4. 자금조달의 목적',
         '시설자금 (원) 1,200,052,500,000 영업양수자금 (원) - 운영자금 (원) - …',
         '시설자금 (원) 700,020,800,000 영업양수자금 (원) - 운영자금 (원) - …']

    이 한 행에 6개 자금용도 라벨이 전부 들어 있어서, 라벨로 찾으면 6개 필드가
    **모두 같은 덩어리**를 값으로 집는다. 실측: 한화에어로스페이스 20250408000012 의
    조달금액이 7,000억 × 6 = 4조 2,001억으로 부풀었다.
    """
    span = _correction_span(rows)
    return rows if span is None else rows[:span[0]] + rows[span[1]:]


def normalize_major(doc: dict) -> dict:
    html = read_document(_doc_path(doc))
    rows = extract_rows(html)
    body = body_rows(rows)                    # 정정 표는 필드 추출에서 반드시 뺀다
    form = major_form(doc["report_nm"])

    fields = _collect(body, MAJOR_FIELDS.get(form, ()))

    # 유상증자는 총 조달 규모 칸이 없다. 자금용도별 금액의 합이 그 값이다(Q-022).
    amount, amount_type = None, None
    spec = MAJOR_AMOUNT.get(form)
    if spec is not None:
        amount_type, keywords, sibling = spec
        amount = _find_number(body, *keywords)
        if amount is not None and sibling is not None:
            extra = _to_number(_find_after(body, keywords, sibling) or "")
            if extra is not None:
                amount += extra
    else:
        parts = [n for k in _FUNDING_LABELS
                 if (n := _to_number(fields.get(k) or "")) is not None]
        if parts:
            amount, amount_type = sum(parts), "조달금액"

    # 값 칸이 마지막이 아닌 서식(발행가액)은 행 전체를 훑는 폴백으로 건진다.
    if form in {"유상증자결정", "무상증자결정"}:
        price = _find_loose(body, "발행가", "보통주식")
        if price:
            fields["신주 발행가액"] = price
    for anchor, label in (("전환에따라발행할주식", "전환에 따라 발행할 주식수"),
                          ("교환대상", "교환에 따라 발행할 주식수")):
        shares = _find_after(body, (anchor,), ("주식수",))
        if shares:
            fields[label] = shares

    ratio, ratio_base = None, None
    for keyword, base in _MAJOR_RATIO:
        found = _find_number(body, keyword)
        if found is not None:
            ratio, ratio_base = found, base
            break

    # 기간은 서식이 실제로 기간 칸을 가질 때만. 없는 서식에서 아무 시작일이나 주우면
    # 청약예정일이 계약기간처럼 나간다.
    start = end = None
    header = _MAJOR_PERIOD.get(form)
    if header:
        start = _find_date(body, header, "시작일")
        end = parse_date(_find_after(body, (header, "시작일"), ("종료일",)) or "")

    event = (_find_date(body, "이사회결의일")
             or _find_date(body, "결정일")
             or _find_date(body, "확인일자")
             or parse_date(doc["rcept_dt"]))

    reserved_reason = _find(body, "유보사유")
    title = _CORRECTION_TAG.sub("", doc["report_nm"] or "").strip() or form

    record = _base_record(doc, rows)
    record.update({
        "title": title,
        "form": form,
        "fields": fields,
        "corrections": correction_pairs(rows) if doc["is_correction"] else [],
        "amount": int(amount) if amount is not None else None,
        "amount_type": amount_type,
        "ratio": ratio,
        "ratio_base": ratio_base,
        "period_start": start.isoformat() if start else None,
        "period_end": end.isoformat() if end else None,
        "event_date": event.isoformat() if event else None,
        "reserved_reason": reserved_reason,
        # 서식을 알고 그 서식의 값을 실제로 뽑았을 때만 True.
        # 금액 칸이 있는 서식은 금액이, 없는 서식(합병·소송·상장폐지)은 고유 필드가 기준이다.
        "schema_matched": bool(
            form in MAJOR_FIELDS
            and (amount is not None if amount_type else bool(fields))
        ),
        "withheld": amount is None and reserved_reason is not None,
    })
    return record


# =============================================================================
# 지분공시(holding) — 대량보유상황보고서 요약정보
# =============================================================================
# 앞선 트랙은 '보유비율·주식수는 라벨로 안 나온다'고 봤다. 셀을 못 읽던 파서 기준의
# 관측이었다. 지금 실측하면 1,083건 중 99.7% 에 `직전/이번 보고서` 행이 있다.
#
# 다만 이 표는 **라벨:값이 아니라 격자**다.
#
#     ['보유주식등의 수 및 보유비율', '보유주식등의 수', '보유비율']   <- 머리행
#     ['직전 보고서', '196,452,737', '30.67']
#     ['이번 보고서', '196,488,145', '30.67']
#
# 값의 이름은 행이 아니라 **머리행의 열 위치**에 있다. row[-1] 을 값으로 쓰는
# 라벨:값 계약으로는 비율만 잡히고 주식수가 통째로 사라진다.

_SUMMARY_START = "요약정보"
#: 요약정보 블록의 끝. 이 확인문 뒤로는 같은 이름('직전보고서')의 상세표가 또 나오는데,
#: 그쪽은 칸이 9개라 위치가 다르다. 경계를 안 그으면 상세표 값을 요약값으로 집는다.
_SUMMARY_END = ("보고자 본인은", "회사코드", "법인구분")
_PREV, _CURRENT = "직전보고서", "이번보고서"


def summary_rows(rows: list[list[str]]) -> list[list[str]]:
    """요약정보 블록만 잘라낸다."""
    start = 0
    for index, row in enumerate(rows[:20]):
        if row and _WS_ALL.sub("", row[0]) == _SUMMARY_START:
            start = index + 1
            break
    block: list[list[str]] = []
    for row in rows[start:]:
        if any(mark in "".join(row) for mark in _SUMMARY_END):
            break
        block.append(row)
    return block


def holding_summary(rows: list[list[str]]) -> dict[str, tuple[str | None, str | None]]:
    """요약정보 격자 -> `{열 이름: (직전, 이번)}`.

    머리행(3칸 이상, 첫 칸이 항목명)을 만나면 그 아래 `직전/이번 보고서` 행을
    열 위치로 읽는다.

    같은 열 이름이 두 번 나온다 — `보유비율` 은 '보유주식등' 블록과 '의결권' 블록에
    모두 있다. **먼저 나온 블록만** 남긴다. 실측 사례(HMM 20250918000358)에서
    의결권 블록은 직전·이번이 둘 다 `-` 라, 덮어쓰면 앞 블록의 36.02/32.60 이
    통째로 지워진다.
    """
    out: dict[str, tuple[str | None, str | None]] = {}
    claimed: set[str] = set()
    columns: list[str | None] = []
    for row in summary_rows(rows):
        head = _WS_ALL.sub("", row[0]) if row else ""
        if head in (_PREV, _CURRENT):
            slot = 0 if head == _PREV else 1
            for offset, name in enumerate(columns):
                if name is None:
                    continue
                value = _clean(row[offset + 1]) if offset + 1 < len(row) else None
                pair = out.get(name, (None, None))
                out[name] = (value, pair[1]) if slot == 0 else (pair[0], value)
            claimed.update(n for n in columns if n)
            continue
        if len(row) >= 3 and "보고서" not in head:
            # 머리행: 첫 칸은 블록 이름, 나머지가 열 이름. 이미 쓴 이름은 비워 둔다.
            columns = [None if (n := c.strip()) in claimed else n for c in row[1:]]
    return out


#: 요약정보 블록의 단순 `라벨 | 값` 행들. (`발행회사명` 은 4칸 행이라 _find_beside 로 읽는다)
HOLDING_FIELDS = (
    ("보고구분", ("보고구분",)),
    ("보고사유", ("보고사유",)),
    ("보유목적", ("보유목적",)),
)
#: 라벨 옆 칸에서 읽는 필드.
HOLDING_BESIDE = (("발행회사명", "발행회사명"),
                  ("발행회사와의 관계", "발행회사와의 관계"),
                  ("의결권있는 발행주식 총수", "의결권있는 발행주식 총수(주)"))
#: 보고자 이름의 상한. 정정본은 요약 블록이 통째로 한 칸에 들어가는 경우가 있어,
#: 이름 자리에 문서 전문이 들어가면 제목이 폭주한다(실측 20250919000067, 891자).
_REPORTER_MAX = 40
#: 요약 격자에서 마크다운으로 올릴 열. 의결권 블록은 보유주식 블록과 값이 거의 같아
#: 근거만 길어지므로 싣지 않는다.
_STAKE_COLUMNS = (
    ("보유주식등의 수", "보유주식등의 수", "주"),
    ("보유비율", "보유비율", "%"),
    ("주요계약체결 주식등의 수", "주식등의 수", "주"),
    ("주요계약체결 비율", "비율", "%"),
)


def _pair_line(pair: tuple[str | None, str | None], unit: str) -> str | None:
    """`(직전, 이번)` -> `직전 A → 이번 B`. 한쪽만 있으면 있는 쪽만 쓴다."""
    prev, current = pair
    parts = []
    if prev:
        parts.append(f"직전 {prev}{unit}")
    if current:
        parts.append(f"이번 {current}{unit}")
    return " → ".join(parts) or None


def normalize_holding(doc: dict) -> dict:
    html = read_document(_doc_path(doc))
    rows = extract_rows(html)
    # 정정본은 앞에 정정 표가 붙고 그 안에 '요약정보' 라는 **항목명**이 있다.
    # 그대로 두면 요약 블록의 시작을 정정 표에서 잘못 잡는다.
    body = body_rows(rows)
    block = summary_rows(body)
    summary = holding_summary(body)

    reporter = _find_beside(body[:8], "보고자")
    if reporter and len(reporter) > _REPORTER_MAX:
        reporter = None

    fields = _collect(block, HOLDING_FIELDS)
    for label, keyword in HOLDING_BESIDE:
        value = _find_beside(body[:30], keyword)
        if value:
            fields[label] = _shorten(value)
    for label, column, unit in _STAKE_COLUMNS:
        line = _pair_line(summary.get(column, (None, None)), unit)
        if line:
            fields[label] = line

    shares_prev, shares_current = summary.get("보유주식등의 수", (None, None))
    ratio_prev, ratio_current = summary.get("보유비율", (None, None))

    title = doc["report_nm"]
    if reporter:
        title = f"{title} · 보고자 {reporter}"

    record = _base_record(doc, rows)
    record.update({
        "title": title,
        "form": doc["doc_subtype"] or "대량보유상황보고서",
        "reporter": reporter,
        "fields": fields,
        "corrections": correction_pairs(rows) if doc["is_correction"] else [],
        "shares_prev": shares_prev,
        "shares_current": shares_current,
        "ratio_prev": _to_number(ratio_prev or ""),
        "ratio_current": _to_number(ratio_current or ""),
        # ratio 는 일부러 비운다. 지분공시 질의는 '직전 대비 몇 %p' 처럼 **차이**를
        # 묻는 경우가 있어서, 이번 보유비율을 ratio 로 올리면 critic 이 정답인
        # 차이값(3.42)을 보유비율(32.60)과 대조해 틀렸다고 판정한다(Q-016).
        # 두 값은 이미 '보유비율' 필드 한 줄에 직전·이번이 다 들어 있다.
        "event_date": (_d.isoformat() if (_d := parse_date(doc["rcept_dt"])) else None),
        # 지분공시의 핵심 값은 '이번 보고서'의 주식수와 비율이다. 둘 다 있어야 매칭.
        "schema_matched": bool(shares_current and ratio_current),
    })
    return record


# =============================================================================


def normalize_doc(doc: dict) -> dict:
    """문서군에 맞는 정규화기로 보낸다. 모르는 군은 거래소 서식으로 시도한다."""
    return {"major": normalize_major,
            "holding": normalize_holding}.get(doc.get("doc_group"), normalize_exchange)(doc)
