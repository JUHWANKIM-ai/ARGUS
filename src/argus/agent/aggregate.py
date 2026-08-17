"""Deterministic aggregation emitted as a single evidence line.

No LLM calls, no retrieval, no step budget.

Sums, differences and quotients are computed from the amount fields of the
evidence records rather than left to the model. Measurement showed a class of
failures where every term was present in the evidence and only the arithmetic
was wrong.

The module emits one line rather than widening the evidence window. Widening
was attempted three times and rejected each time, so the design constraint is
that this path must not consume evidence slots.
See docs/design-rationale.md#evidence-slot-vs-line

Nothing is emitted unless the computation is uniquely determined. Asserting a
wrong total with confidence is worse than staying silent, because the model
does not question the line. The early returns in the functions below are that
guard.
"""
from __future__ import annotations

import re

#: Operation requested by the question. Order matters: difference must be
#: tested before sum so that growth phrasings do not fall through to sum.
#: The difference group includes revision phrasings, which state that a
#: value changed without stating a direction. That matches `_delta_groups`
#: taking an absolute value.
_OPS = (("차", ("얼마나 증가", "얼마나 늘었", "얼마나 감소", "얼마나 줄었",
                "증가했는가", "감소했는가", "얼마나 늘어", "얼마나 줄어",
                "얼마나 바뀌", "얼마나 변경", "얼마나 달라", "얼마나 올랐",
                "얼마나 내렸", "변경되었는가", "바뀌었는가",
                "합계보다", "차이는")),
        ("몫", ("1척당", "1기당", "1대당", "1건당", "척당", "기당", "대당", "건당",
                "평균 투자금액", "평균 계약금액", "1척 당", "1기 당")),
        ("합", ("합계", "총합", "총액", "모두 더하", "다 더하", "합은", "총 얼마")))

#: Divisor for the quotient operation, read from the question. Unit counts
#: are not carried as labelled fields in the evidence.
_UNIT_COUNT = re.compile(r"(\d{1,4})\s*(?:척|기|대|건|개)")

#: Filing-year filter. Applies only when the question constrains the filing
#: date. A different axis from the fiscal year, which for periodic reports
#: is not the filing date.
_FILED_YEAR = re.compile(r"(20\d{2})\s*년\s*(?:에\s*)?공시")

#: Full filing-date filter, applied only when the question pins a single day.
_FILED_DATE = re.compile(r"(20\d{2})\s*년\s*(1[0-2]|0?[1-9])\s*월\s*(3[01]|[12]\d|0?[1-9])\s*일")

#: Item count stated by the question. Acts as the safety condition for the
#: sum operation. A counter word is required after a numeral, because a bare
#: character class matches single syllables inside unrelated proper nouns.
#: See docs/design-rationale.md#stated-count
_STATED_COUNT = re.compile(
    r"(\d{1,2})\s*(?:건|개\s*(?:상장사|회사|사)?|곳|군데)"
    r"|(?:^|[\s,.])(두|세|네|다섯|여섯)\s*(?:건|개|곳|군데|공시|회사|상장사)")
_WORD_COUNT = {"두": 2, "세": 3, "네": 4, "다섯": 5, "여섯": 6}


#: Disposition stated alongside the value. Supplying the number alone is not
#: sufficient: the model recomputes and trusts its own result. The directive
#: is placed inside the evidence rather than the system prompt, because a
#: system-level rule perturbs answers to unrelated questions.
#: See docs/design-rationale.md#model-recomputes
_DIRECTIVE = ("(이 값은 시스템이 위 근거의 금액 필드에서 직접 계산한 것이다. "
              "답변의 해당 수치는 이 값을 그대로 쓰고 다시 계산하지 않는다.)")


def _op(question: str) -> str:
    text = question or ""
    for name, hints in _OPS:
        if any(h in text for h in hints):
            return name
    return ""


#: Amounts eligible as substitution candidates in an answer. Comma groups
#: are required so that years and ratios are not treated as amounts.
_ANSWER_NUM = re.compile(r"\d{1,3}(?:,\d{3})+")

#: Openers of a concluding sentence. Candidate selection uses position
#: rather than magnitude: the figure an answer presents as its result
#: appears in the conclusion. A magnitude guard was tried and rejected,
#: since the size of the model's error cannot be bounded in advance.
_CONCLUSION = ("따라서", "그러므로", "결론적으로", "정리하면", "즉,")

#: Minimum magnitude treated as an amount. Below this a figure is a count,
#: a ratio or an index.
MIN_SUBSTITUTE_AMOUNT = 100_000_000

#: Marker recording that a filing-date filter applied. `_sum_rows` uses it
#: to separate the filing-date axis, which pins the set, from the
#: counterparty axis, which does not.
_DATE_NOTE = "공시분"


def substitute(answer: str, records, question: str, route) -> tuple[str, str]:
    """Replace a single incorrect total in the answer with the computed value.

    Returns (new answer, reason), or (original, "") when nothing applies.

    Emitting the aggregate line is not sufficient on its own: the model
    recomputes and trusts its own arithmetic.
    See docs/design-rationale.md#model-recomputes

    Complements equation-based verification, which fires only when the answer
    contains a parseable expression. The source here is the amount field of the
    evidence, so it is independent of how the answer is written.

    Substitution is skipped when more than one candidate exists. Choosing
    without knowing which figure is the total would corrupt a correct one.
    """
    total = _total(records, question, route)
    if total is None:
        return answer, ""
    known = {_amount(r) for r in (records or []) if _amount(r) is not None}
    known.add(total)
    candidates = {
        text for text in _ANSWER_NUM.findall(_conclusion(answer))
        if (value := int(text.replace(",", ""))) not in known
        and value >= MIN_SUBSTITUTE_AMOUNT}
    if len(candidates) != 1:
        return answer, ""
    wrong = candidates.pop()
    body, tail = split_tail(answer)
    return (body.replace(wrong, f"{total:,}") + _prune_notes(tail, wrong),
            f"{wrong} -> {total:,}")


#: Notes the system appends after an answer. Single definition of this rule;
#: duplicating the pattern lets copies drift when the note format changes.
_NOTE = re.compile(r"\n*\((?:주의|근거)[:：]")


def split_tail(answer: str) -> tuple[str, str]:
    """Split an answer into body and system-appended notes.

    Returns (answer, "") when no note is present. Only the body is eligible for
    rewriting: notes record what the verifier observed, and editing them would
    turn a record into an invention.
    """
    hit = _NOTE.search(answer or "")
    return (answer, "") if not hit else (answer[:hit.start()], answer[hit.start():])


def _prune_notes(tail: str, wrong: str) -> str:
    """Remove note entries that reference a value which was just substituted.

    Applying the substitution across the whole answer would make the note claim
    a figure the verifier never saw. Leaving a resolved defect listed as
    outstanding is equally wrong, so the referencing entry is dropped and the
    note is removed entirely when nothing remains.
    See docs/design-rationale.md#substitution-scope
    """
    if not tail or wrong not in tail:
        return tail

    def _fix(match: re.Match) -> str:
        items = [s.strip() for s in match.group(2).split(";")]
        kept = [s for s in items if wrong not in s]
        if not kept:
            return ""
        return f"{match.group(1)}{'; '.join(kept)})"

    pruned = re.sub(r"(\n*\(주의: 자동 검증에서 결함이 발견되었습니다 — )([^)]*)\)",
                    _fix, tail)
    return pruned


#: Phrasings asking for a revision history to be laid out. Kept disjoint
#: from the difference operation: that one yields a single value, this one
#: yields a sequence. Firing both on one question would place two
#: contradictory lines in the evidence.
_SERIES_ASK = ("시간순", "시계열", "어떻게 바뀌", "어떻게 변했", "변동 내역",
               "변화 과정", "추이", "정정 과정", "차례로", "순서대로")


def revision_series(records, question: str) -> str:
    """정정 체인의 금액을 **접수일 순서로** 한 줄에 편다. LLM 0회 · 검색 0회.

    🔴 `Q-023` 의 R0 실측(2026-08-16): 정정본 다섯 건의 금액이 근거에 **전부 있는데**
    답은 *"이후 정정 과정에서의 변동 사항은 없습니다"* 였다. 근거는 블록 다섯 개로
    흩어져 있었고 모델이 첫 블록만 읽었다. `stale_value` 검사가 그것을 정확히 발화
    했지만(*"근거에 최신본이 있는데 답이 대체본 값을 썼다"*) 각주는 답을 안 고친다 —
    v4 가 인용 축에서 만난 **탐지 100% · 교정 0%** 가 여기서도 되풀이됐다.
    시계열은 그 흩어짐을 없앤다.

    🔴 **체인은 검색 1위가 속한 것 하나만 편다.** `_delta_groups` 와 같은 규율이다 —
    근거에는 질문 범위 밖 체인이 섞이고, 여럿을 펴면 어느 계약의 이력인지 모른다.
    """
    if not any(h in (question or "") for h in _SERIES_ASK):
        return ""
    rows = [r for r in (records or []) if _amount(r) is not None]
    if len(rows) < 2:
        return ""
    rank = {r["rcept_no"]: i for i, r in enumerate(rows)}
    groups = [g for g in _chains(rows) if len(g) >= 2]
    if not groups:
        return ""
    groups.sort(key=lambda g: min(rank.get(r["rcept_no"], 1 << 30) for r in g))
    chain = sorted(groups[0],
                   key=lambda r: (str(r.get("rcept_dt", "")), r.get("rcept_no", "")))
    steps = " → ".join(f"{_ymd(r)} {_amount(r):,}원" for r in chain)
    return (f"- [결정적 집계] 접수일 순 금액 추이 ({len(chain)}건): {steps} "
            f"{_DIRECTIVE}")


def _ymd(record: dict) -> str:
    raw = str(record.get("rcept_dt", ""))
    return f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}" if len(raw) >= 8 else raw


#: 분포 줄에 담을 계약상대 수의 상한. 🔴 `MAX_ENUM_EVIDENCE`(5)와 **다른 축이다** —
#: 저쪽은 근거 블록 칸이고 이쪽은 한 줄 안의 항목 수다. 11개 그룹이 약 120자에 들어간다.
MAX_PARTNER_PROFILE = 12


def partner_profile(groups: dict) -> str:
    """계약상대별 공시 건수 분포를 한 줄로. LLM 0회 · 검색 0회.

    🔴 `_enumerate_events` 는 이 사전을 **이미 만들어 놓고** `MAX_ENUM_EVIDENCE`(5)
    칸만 담고 버린다. `Q-117` 은 그 버려진 분포를 묻는 문항이다 — 골든의 핵심이
    *"발주 구조가 두 갈래"* 이고, 실측(`_enumerate_events` 독스트링)이 pool 27 ·
    그룹 11 · 담을 칸 5 라 9번째 그룹부터 잘린다는 것을 이미 세어 뒀다.
    근거 칸을 늘리는 것은 `A16` 이 기각한 자리이므로 **줄로 낸다.**
    """
    rows = [(name, members) for name, members in (groups or {}).items() if name]
    if len(rows) < 2:
        return ""
    rows.sort(key=lambda x: (-len(x[1]), x[0]))
    total = sum(len(m) for _, m in rows)
    lines = [f"- [결정적 집계] 계약상대별 공시 분포 (전체 {total}건 · 상대 {len(rows)}곳)"]
    for name, members in rows[:MAX_PARTNER_PROFILE]:
        lines.append(f"  - {name}: {len(members)}건 — {_subject(members[0])}")
    lines.append(f"  {_DIRECTIVE}")
    return "\n".join(lines)


def _subject(event: dict) -> str:
    """대표 계약의 **대상**. 🔴 건수만으로는 갈래가 안 보인다.

    `Q-117` 실측(2026-08-16): 건수 분포만 실었더니 답이 *"한국수력원자력과 나머지
    기업들"* 로 갈렸다. 골든이 요구하는 갈래는 **재수주 구조**다 — 두산에너빌리티는
    발주 공기업이 아니라 주기기 공급사이고, 그 표지가 계약명에 있다:

        한국수력원자력   가동원전 계통설계 기술용역 …      (직접 수주)
        두산에너빌리티   원자로계통 설계용역 …            (재수주 — 계약명이 전부 같다)

    이름만으로는 그 구별이 안 서고 계약명이 그것을 그대로 말한다. 그래서 함께 싣는다.
    """
    title = str(event.get("title") or "")
    tail = title.split("—", 1)[-1].strip() if "—" in title else title.strip()
    return tail[:MAX_SUBJECT_CHARS] or "-"


#: 대표 계약명 길이 상한. 11곳 × 44자 ≈ 480자로, 근거 블록 하나보다 작다.
MAX_SUBJECT_CHARS = 44


#: 답변의 `1,234,567,890원(12억원)` 꼴. 🔴 마크다운 강조를 넘어야 한다 —
#: 실측에서 `**207,684,631,210원**(20,768억원)` 형태가 나온다.
_PAREN_UNIT = re.compile(r"(\d{1,3}(?:,\d{3}){2,})(\s*원?\s*\**\s*)"
                         r"[（(]\s*(?:약\s*)?([^)）]{1,24})[)）]")

#: 괄호 안 표기를 원 단위로 편다. `1조 2,345억원` · `9,714억원` · `2,000억`.
_EOK_TEXT = re.compile(r"^(?:([\d,]+)\s*조\s*)?(?:([\d,]+(?:\.\d+)?)\s*억)?\s*원?$")

#: 반올림을 봐주는 폭. 억 단위 표기는 1억 미만을 버리므로 그만큼은 오차가 아니다.
_PAREN_TOLERANCE = 10 ** 8


def fix_unit_parens(answer: str) -> tuple[str, list[str]]:
    """답변의 **괄호 환산을 우리가 다시 쓴다.** LLM 0회 · 검색 0회.

    돌려주는 것은 `(새 답변, 고친 목록)` 이고, 고칠 것이 없으면 `(원본, [])` 이다.

    🔴 **본 수치는 맞는데 괄호가 틀린다** (2026-08-16 실측 · 답변 132건 전수):

        Q-097  207,684,631,210원 → "(20,768억원)"    맞는 값 2,076.8억   **10배**
        Q-061    345,173,200,000원 → "(3400억원)"      맞는 값 3,451.7억
        Q-061  1,728,230,200,000원 → "(1조 6,549억원)"  맞는 값 1조 7,282억
        Q-115  1,759,596,802,000원 → "(1조 4,772억원)"  맞는 값 1조 7,596억

    괄호 환산 67개 중 **4개(3문항)** 가 틀렸다. v4 의 발견이 여기서 또 나온다 —
    *"모델이 숫자를 손에 쥐는 순간 틀린다."* 본문 수치는 근거에서 옮겨 적으니 맞고,
    괄호는 **자기가 나눠야 하니** 틀린다.

    🔴 **`Q-115` 는 우리가 만든 것이다.** `substitute()` 가 본 수치만 갈아 끼우고
    모델이 제 오답에서 계산한 괄호를 그대로 뒀다. 치환이 새 불일치를 낳은 셈이라
    이 함수는 그 뒤에 서야 한다.

    🔴 **표기 형식을 새로 만들지 않는다** — `prompts._split_jo` 를 그대로 쓴다.
    근거와 답변이 다른 형식을 쓰면 모델이 둘을 다른 값으로 읽는다.

    🔴 **못 읽는 괄호는 건드리지 않는다.** `(주당 1,000원)`·`(지분율 30%)` 처럼 환산이
    아닌 괄호가 답변에 흔하다. `_EOK_TEXT` 가 조·억으로 파싱되는 것만 대상이다.
    """
    from argus.agent.prompts import EOK_PER_JO, _split_jo

    fixed: list[str] = []

    def _sub(match: re.Match) -> str:
        won = int(match.group(1).replace(",", ""))
        inner = match.group(3).strip()
        hit = _EOK_TEXT.match(inner)
        if not hit or not (hit.group(1) or hit.group(2)):
            return match.group(0)               # 환산 괄호가 아니다 — 그대로 둔다
        stated = 0
        if hit.group(1):
            stated += int(hit.group(1).replace(",", "")) * EOK_PER_JO * 10 ** 8
        if hit.group(2):
            stated += int(float(hit.group(2).replace(",", "")) * 10 ** 8)
        if abs(stated - won) <= _PAREN_TOLERANCE:
            return match.group(0)               # 반올림 오차 — 맞는 값이다
        eok = won / 10 ** 8
        right = _split_jo(eok) if abs(eok) >= EOK_PER_JO else f"{eok:,.0f}억원"
        fixed.append(f"{inner} -> {right}")
        return f"{match.group(1)}{match.group(2)}({right})"

    body, tail = split_tail(answer or "")
    body = _PAREN_UNIT.sub(_sub, body)
    # 🔴 고친 표기를 말하던 각주 항목도 뺀다. 안 빼면 각주가 답변에 없는 수를
    #    가리킨다 — `Q-097` 실측: 괄호를 2,077억으로 고쳤는데 각주는 20,768억을 말했다.
    for note in fixed:
        tail = _prune_notes(tail, note.split(" -> ")[0])
    return body + tail, fixed


# --- A38 — 자기정합: 답변이 스스로와 어긋나지 않는가 -------------------------------
#
# 🔴 **이것은 「추론 논리성」의 측정 수단이 아니다 — 그 축의 실패 양식 하나를 잡는
# 검사다.** 첫 판에서 *"그 지표를 재는 결정적 수단"* 이라고 적었는데 **과장이었다.**
# 실측이 그것을 바로 반증한다: **저장 답변 132건 중 이 검사가 적용되는 것은 9건(6.8%)**
# 이다 — 답변에 식이 없으면 아무 말도 하지 않는다.
#
# 지표가 묻는 것은 *"추론·답변 생성과정이 논리적인가"* 이고, 이 검사가 보는 것은
# **답변 안 수치의 자기정합** 하나다. 안 보는 것이 최소한 넷이다 —
# 전제 누락 · 비약 · 순서 오류 · 자기부정. 그 목록과 각각을 무엇이 잡는지는
# `docs/reasoning-metric-2026-08-16.md` 에 적었다.
#
# 진단 결과(2026-08-16 · closed 69문항에 judge 를 걸어 봄): EM 과 갈린 7건 중 **여섯이
# 오탐**이었다 — 하나는 긴 정기공시 섹션에서 값을 못 찾은 것이고 다섯은 거절 문항에
# 요점 대조를 건 적용 착오다. **judge 는 이 축의 도구가 아니다.**
#
# 그런데 그 하나가 진짜였다:
#
#     Q-111  머리말 "7,591,628,000원 적습니다"
#            풀이   "362,307,038,000 − 354,725,410,000 = 7,581,628,000원"   ← 골든
#
# **답변이 자기와 모순인데** EM 도(골든 문자열이 어딘가 있으므로) 우리 수치검사도
# (식 자체는 맞으므로) 못 잡았다. 그리고 이것은 **결정적으로 잡힌다** — 저장 답변
# 132건 전수에서 검출 1건 · 오탐 0건이다.

#: 답변이 스스로 쓴 식. 좌변 두 항과 우변 결과.
_SELF_EQ = re.compile(r"([\d,]{7,})\s*([+\-−])\s*([\d,]{7,})\s*=\s*\**\s*([\d,]{7,})")

#: 같은 양을 가리키는 것으로 볼 근접도. 자릿수가 같고 5% 이내면 오타로 본다.
#: 🔴 넓히면 서로 다른 금액 둘을 같은 것으로 오인한다 — 오탐이 곧 새 오답이다.
_SELF_NEAR = 0.05


def fix_self_contradiction(answer: str) -> tuple[str, list[str]]:
    """답변이 **스스로 쓴 식과 어긋나는 수**를 식 결과로 맞춘다. LLM 0회.

    사유·실측은 위 구역 주석에 적었다.

    🔴 **식 결과를 믿는 근거**: 좌변 두 항은 근거에서 옮겨 적은 값이고
    `critic.verify_answer_arithmetic` 이 이미 그 식의 성립을 확인한다. 어긋나는 쪽은
    식을 안 거친 서술 수치다 — `Q-111` 에서도 식 결과가 골든과 일치했다.

    🔴 **각주는 안 건드린다** (`substitute` 와 같은 규율). 각주는 검사기가 무엇을
    봤는가의 기록이다.
    """
    body, tail = split_tail(answer or "")
    equations = list(_SELF_EQ.finditer(body))
    if not equations:
        return answer, []

    fixed: list[str] = []
    for match in equations:
        result = int(match.group(4).replace(",", ""))
        operands = {int(match.group(1).replace(",", "")),
                    int(match.group(3).replace(",", ""))}
        for text in set(_ANSWER_NUM.findall(body)):
            value = int(text.replace(",", ""))
            if value == result or value in operands:
                continue
            if len(str(value)) != len(str(result)):
                continue
            if 0 < abs(value - result) / result < _SELF_NEAR:
                body = body.replace(text, f"{result:,}")
                fixed.append(f"{text} -> {result:,}")
    return body + tail, fixed


def _conclusion(answer: str) -> str:
    """답변이 **최종 답으로 내세우는** 대목. 없으면 본문 전체.

    🔴 각주·근거 표기(`(주의: …)` · `(근거: …)`)는 **우리가 붙인 것**이라 잘라낸다.
    거기 실린 수를 치환하면 우리 글을 우리가 고치는 꼴이고, 각주의 재계산값이
    치환 대상으로 잡히면 후보가 둘이 되어 침묵으로 빠진다.
    """
    body, tail = split_tail(answer or "")
    for opener in _CONCLUSION:
        index = body.rfind(opener)
        if index >= 0:
            return body[index:]
    return body


def _total(records, question: str, route):
    """`line()` 이 싣는 것과 **같은 규칙으로** 계산한 총액. 없으면 `None`.

    🔴 두 곳에서 따로 계산하면 갈린다 — `critic.unique_recomputed` 가 한 함수로
    묶여 있는 것과 같은 이유다. 여기가 그 한 곳이다.
    """
    op = _op(question)
    if op not in ("합", "차"):
        return None
    picked, note = select(records, question, route, op)
    if not picked:
        return None
    if op == "합":
        rows = _sum_rows(picked, question, note)
        return sum(_amount(r) for r in rows) if rows else None
    groups = _delta_groups(picked, question)
    if not groups:
        return None
    total = 0
    for group in groups:
        total += _amount(group[-1]) - _amount(group[0])
    # 🔴 **크기로 답한다.** 질문이 *"얼마나 증가/감소했는가"* 이므로 답은 크기이지
    #    부호가 아니다. 첫 판은 부호를 그대로 뒀고, `Q-061`(지금 정답)의
    #    345,173,200,000 을 **−345,173,200,000** 으로 갈아 끼울 뻔했다 (저장 답변
    #    전수 검사에서 0원으로 잡았다). 정답을 오답으로 만드는 것이 이 모듈의
    #    가장 비싼 실패다.
    return abs(total) or None


def _delta_groups(picked: list[dict], question: str):
    """`차` 의 대상 체인들. 🔴 **개수가 안 맞으면 `None` 을 돌려 침묵시킨다.**

    `_sum_rows` 와 같은 규율이고, 같은 실측이 요구했다 — 살아 있는 경로가 보는 것은
    골든 몇 건이 아니라 **근거 전량**이다. `Q-113` 의 근거는 12건이고 그중 골든이 4건
    이다. 체인을 다 잡아 더하면 질문이 묻지 않은 계약의 변동까지 섞인다.

        질문이 건수를 밝혔으면   그 수만큼의 체인일 때만   ("두 회사")
        안 밝혔으면              체인이 정확히 하나일 때만  (`Q-018`)
    """
    groups = [g for g in _chains(picked) if len(g) >= 2
              and all(_amount(r) is not None for r in g)]
    for group in groups:
        group.sort(key=lambda r: (str(r.get("rcept_dt", "")), r.get("rcept_no", "")))

    # 🔴 **날짜는 체인의 최신본에 건다** — 레코드 전량에 걸면 원공시가 빠져 체인이
    #    끊긴다(첫 판의 실패). `Q-113` *"두 회사 모두 2025년 9월 24일 정정공시를 냈다"*
    #    는 **정정일**을 말하는 것이고, 그 날짜를 가진 최신본을 둔 체인이 정확히 둘이다.
    #    실측: 근거 10건에 체인 그룹이 넷인데 이 필터로 둘이 된다.
    day = _FILED_DATE.search(question or "")
    if day:
        want_dt = "%s%02d%02d" % (day.group(1), int(day.group(2)), int(day.group(3)))
        narrowed = [g for g in groups if str(g[-1].get("rcept_dt", "")) == want_dt]
        if narrowed:
            groups = narrowed

    want = _stated_count(question) or 1
    if len(groups) == want:
        return groups
    if len(groups) < want:
        return None
    # 🔴 **넘치면 검색 상위 체인을 쓴다.** `Q-018`(*"FPU 1기 … 얼마나 증가했는가"*)의
    #    근거에는 같은 달의 다른 공급계약 체인이 함께 실려 체인이 둘이다(실측).
    #    1위 근거가 속한 체인이 질문이 가리키는 것이다.
    rank = {r["rcept_no"]: i for i, r in enumerate(picked)}
    groups.sort(key=lambda g: min(rank.get(r["rcept_no"], 1 << 30) for r in g))
    return groups[:want]


def _amount(record: dict):
    value = (record or {}).get("amount")
    return value if isinstance(value, int) and value > 0 else None


def _chains(records: list[dict]) -> list[list[dict]]:
    """`related` 로 이어진 레코드를 한 묶음으로 센다 (합집합 찾기).

    🔴 `corp_name` 으로 묶지 않는다 — 한 기업이 서로 무관한 계약을 여럿 공시한다
    (`Q-113` 의 삼성중공업은 근거에 다섯 건이 있고 그중 둘만 같은 체인이다).
    """
    index = {r["rcept_no"]: i for i, r in enumerate(records)}
    parent = list(range(len(records)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, record in enumerate(records):
        for other in (record.get("related") or ()):
            j = index.get(other)
            if j is not None:
                parent[find(i)] = find(j)
    groups: dict[int, list[dict]] = {}
    for i, record in enumerate(records):
        groups.setdefault(find(i), []).append(record)
    return list(groups.values())


def select(records, question: str, route, op: str = "") -> tuple[list[dict], str]:
    """집계 대상과 **필터를 밝힌 설명**. 대상이 없으면 `([], "")`.

    필터는 두 축뿐이다 — 접수일 연도와 계약상대. R0 이 오답 7건에서 실제로 필요한
    것이 이 둘임을 실측했다. 축을 늘리면 유일성이 깨지고, 유일성이 깨지면 이 모듈은
    침묵해야 한다.
    """
    picked = [r for r in (records or []) if _amount(r) is not None]
    if not picked:
        return [], ""
    notes: list[str] = []

    # 🔴 **`합` 에서만 레코드 전량을 날짜로 좁힌다.** `차` 문항의 날짜는 정정일을
    #    가리키는 경우가 많고(`Q-113`), 레코드 전량에 걸면 원공시가 빠져 **체인이
    #    끊긴다** — 차를 낼 짝이 사라진다(실측). `차` 쪽 날짜는 `_delta_groups` 가
    #    **체인의 최신본**에 건다.
    day = _FILED_DATE.search(question or "") if op == "합" else None
    if day:
        want = "%s%02d%02d" % (day.group(1), int(day.group(2)), int(day.group(3)))
        narrowed = [r for r in picked if str(r.get("rcept_dt", "")) == want]
        if narrowed:
            picked = narrowed
            notes.append(f"{want[:4]}-{want[4:6]}-{want[6:]} 공시분")

    year = _FILED_YEAR.search(question or "")
    if year and not day:
        want = year.group(1)
        picked = [r for r in picked if str(r.get("rcept_dt", ""))[:4] == want]
        notes.append(f"{want}년 공시분")

    party = getattr(route, "counterparty", None)
    if party:
        from argus.corpus.counterparty import canonical

        key = canonical(party)
        narrowed = [r for r in picked
                    if canonical(r.get("counterparty") or "") == key]
        if narrowed:
            picked = narrowed
            notes.append(f"계약상대 {party}")

    return picked, " · ".join(notes)


def _stated_count(question: str):
    """질문이 밝힌 건수. 없으면 `None`."""
    hit = _STATED_COUNT.search(question or "")
    if not hit:
        return None
    if hit.group(1):
        return int(hit.group(1))
    return _WORD_COUNT.get((hit.group(2) or "").replace(" 개", ""))


def _sum_rows(picked: list[dict], question: str, note: str = ""):
    """`합` 의 대상. 🔴 **집합이 안 정해지면 `None` 을 돌려 침묵시킨다.**

    실측(2026-08-16)이 이 게이트를 요구했다. 게이트 없이 근거 전량을 더했더니
    `Q-109` 에서 2,807,598,070,800(모델의 오기)을 **4,349,583,514,756** 으로 갈아
    끼웠다 — 골든은 2,807,599,069,800 이다. 질문은 *"2023년 12월 11일 두 개 상장사"*
    를 물었는데 근거에는 같은 기업의 다른 계약이 함께 실려 있었다.

    🔴 **틀린 집계를 확신 있게 박아 주는 것은 지금보다 나쁘다.** 모델은 우리 줄을
    의심하지 않고, 치환은 아예 답을 갈아 끼운다. 그래서 대상이 다음 둘 중 하나로
    **못박힐 때만** 더한다:

        ① 질문이 건수를 밝혔고 그 수와 맞는다   ("두 공시" · "해지 3건")
        ② 필터가 걸렸다                        (접수일 · 계약상대)

    둘 다 아니면 `None`. 침묵이 기본값이라는 모듈 계약이 여기서 지켜진다.
    """
    rows = _latest_per_chain(picked)
    if len(rows) < 2:
        return None
    stated = _stated_count(question)
    if stated is not None:
        # 🔴 **넘치면 상위 N 을 쓴다.** 근거에는 질문 범위 밖 공시가 섞인다 —
        #    `Q-034`(*"해지 3건"*)의 근거는 5건이고 그중 상위 3이 골든이다(실측).
        #    순서는 `_latest_per_chain` 이 지켜 준 검색 순위다. 모자라면 침묵한다 —
        #    질문이 셋을 말하는데 둘밖에 없으면 그 합은 답이 아니다.
        return rows[:stated] if len(rows) >= stated else None
    # 🔴 **계약상대 필터 하나로는 못 박힌 것이 아니다.** 허브는 계약을 여럿 가진다 —
    #    `Q-107`(*"체코 두코바니 … 한국수력원자력을 계약상대로"*)의 근거에는 같은
    #    발주처의 **신한울** 계약이 함께 실려 있고, 다 더하면 8,018,130,248,423 이
    #    나온다 (골든 6,890,924,666,293). 저장 답변 전수 검사에서 0원으로 잡았다.
    #    질문이 좁히는 것은 **사업**인데 우리에겐 그 축이 없다 — 그러면 침묵이다.
    return rows if _DATE_NOTE in note else None


def _latest_per_chain(records: list[dict]) -> list[dict]:
    """🔴 같은 체인의 원공시와 정정본을 **함께 더하지 않는다** (이중 계상).

    실측한 일곱 문항에서는 이 규칙이 아무것도 안 바꾼다(합 문항의 근거가 전부
    `related=[]` 이다). 그래도 두는 이유는, 안 두면 정정 체인이 하나라도 근거에
    들어오는 순간 **조용히 두 배가 되기** 때문이다.
    """
    order = {r["rcept_no"]: i for i, r in enumerate(records)}
    out = []
    for group in _chains(records):
        group.sort(key=lambda r: (str(r.get("rcept_dt", "")), r.get("rcept_no", "")))
        out.append(group[-1])
    # 🔴 **근거 순서를 지킨다** — 그것이 검색 순위이고, 질문이 건수를 밝혔을 때
    #    상위 N 을 고르는 근거가 된다 (`_sum_rows`). 접수일로 다시 정렬하면 그
    #    순위가 사라진다.
    return sorted(out, key=lambda r: order.get(r["rcept_no"], 1 << 30))


def line(records, question: str, route) -> str:
    """근거에 실을 한 줄. 낼 것이 없으면 빈 문자열."""
    op = _op(question)
    if not op:
        return ""
    picked, note = select(records, question, route, op)
    if not picked:
        return ""
    scope = f" ({note})" if note else ""

    if op == "합":
        rows = _sum_rows(picked, question, note)
        if not rows:
            return ""
        total = sum(_amount(r) for r in rows)
        terms = " + ".join(f"{_amount(r):,}" for r in rows)
        return (f"- [결정적 집계] 위 근거 {len(rows)}건의 금액 합계{scope}: "
                f"{terms} = **{total:,}원** {_DIRECTIVE}")

    if op == "차":
        groups = _delta_groups(picked, question)
        if not groups:
            return ""
        parts, total = [], 0
        for group in groups:
            older, newer = group[0], group[-1]
            delta = _amount(newer) - _amount(older)
            total += delta
            parts.append(f"{newer.get('corp_name', '')} {_amount(newer):,} − "
                         f"{_amount(older):,} = {delta:,}")
        if len(parts) == 1:
            return (f"- [결정적 집계] 정정 전후 금액 변동{scope}: {parts[0]}원 "
                    f"{_DIRECTIVE}")
        return (f"- [결정적 집계] 정정 전후 금액 변동{scope} — "
                + " · ".join(parts)
                + f" · 합계 **{total:,}원** {_DIRECTIVE}")

    if op == "몫":
        rows = _latest_per_chain(picked)
        if len(rows) != 1:
            return ""
        hit = _UNIT_COUNT.search(question or "")
        if not hit:
            return ""
        n = int(hit.group(1))
        total = _amount(rows[0])
        if n <= 1 or total % n:
            # 🔴 나누어떨어지지 않으면 침묵한다. 몫을 반올림해 실으면 그 자체가
            #    새 오답이 되고, 모델은 우리가 준 수를 의심하지 않는다.
            return "" if n <= 1 else (
                f"- [결정적 집계] 단위당 금액{scope}: {total:,} ÷ {n} = "
                f"**{total // n:,}원** (나머지 {total % n:,}) {_DIRECTIVE}")
        return (f"- [결정적 집계] 단위당 금액{scope}: {total:,} ÷ {n} = "
                f"**{total // n:,}원** {_DIRECTIVE}")

    return ""


#: 원값 없이 홀로 선 조·억 표기. `1,234억원` · `1조 2,345억원` · `7,257 억 원`.
#: 🔴 앞에 숫자·콤마가 붙은 것은 안 잡는다 — `257,732,228,908원 (2,577억원)` 처럼
#: **이미 원값이 있는** 자리는 `fix_unit_parens` 의 몫이다.
_BARE_EOK = re.compile(
    r"(?<![\d,])(?:(\d{1,3}(?:,\d{3})*|\d+)\s*조\s*)?"
    r"(\d{1,3}(?:,\d{3})*|\d+)\s*억\s*원")

#: 반올림 복원의 유효 범위. 억 미만은 반올림해도 잃는 것이 없고, 여기서 다룰 값도 아니다.
MIN_EXPAND_AMOUNT = 100_000_000


def expand_rounded(answer: str, records) -> tuple[str, list[str]]:
    """**반올림만 적힌 금액을 근거의 원값으로 되살린다.** LLM 0회 · 검색 0회.

    돌려주는 것은 `(새 답변, 고친 목록)` 이고, 고칠 것이 없으면 `(원본, [])` 이다.

    🔴 **실측 `N-011`** (2026-08-17). 근거에 정확한 두 수가 **둘 다 실려 있는데**
    모델이 억으로만 답했다:

        골든   정정 전 257,732,228,908원 · 정정 후 725,693,799,075원
        답변   "2,577억원"                · "7,257억원"

    값은 맞다 — **정밀도만 잃었다.** 그런데 규약 「정확성」은 *수치가 정확한가*를 묻고,
    골든과 대조하는 채점은 원값을 찾는다. `fix_unit_parens` 가 *"원값은 맞고 괄호가
    틀린"* 자리를 고친다면 이쪽은 *"괄호만 있고 원값이 없는"* 자리를 고친다.

    🔴 **유일하게 결정되지 않으면 아무것도 안 한다** — `substitute` 와 같은 규율이다.
    반올림해서 같은 억이 되는 근거 금액이 둘 이상이면 어느 것을 되살릴지 모른다.
    확신 없이 원값을 박으면 *맞는 근사*를 *틀린 정확*으로 바꾸는 꼴이 된다.

    🔴 **본문만 본다.** 각주와 근거 표기는 우리가 쓴 것이다 (`split_tail` 독스트링).
    """
    body, tail = split_tail(answer or "")
    if not body:
        return answer, []
    known = sorted({a for r in (records or [])
                    if (a := _amount(r)) and a >= MIN_EXPAND_AMOUNT})
    if not known:
        return answer, []

    fixed: list[str] = []

    def _sub(match: re.Match) -> str:
        jo = int((match.group(1) or "0").replace(",", ""))
        eok = int(match.group(2).replace(",", ""))
        stated = jo * 10_000 + eok                      # 억 단위 정수
        hits = [a for a in known if round(a / 100_000_000) == stated]
        if len(hits) != 1:
            return match.group(0)
        exact = f"{hits[0]:,}"
        if exact in body:                               # 이미 원값이 있다
            return match.group(0)
        fixed.append(f"{match.group(0)} -> {exact}원")
        return f"{exact}원 ({match.group(0)})"

    return _BARE_EOK.sub(_sub, body) + tail, fixed


def state_total(answer: str, records, question: str, route) -> tuple[str, str]:
    """**우리가 아는 결과값이 답변에 없으면 결론 한 문장을 우리가 적는다.** LLM 0회.

    돌려주는 것은 `(새 답변, 사유)` 이고, 적을 것이 없으면 `(원본, "")` 이다.

    🔴 **`substitute` 가 못 잡는 자리다** (2026-08-17 · `H-008` 실측). 저쪽은 답변에
    *"근거 밖 수 하나"* 가 있을 때만 발화한다. 여기서 모델은 **틀린 수를 쓴 게 아니라
    맞는 수를 틀리게 엮었다**:

        집계 줄  1,046,363,500,000 − 400,119,500,000 = 646,244,000,000원
        답변     "400,119,500,000원이 삭제되고 … 총 1,046,363,500,000원으로 변경"
                  ↑ 두 수 다 근거에 있다. 그래서 치환의 발화 조건에 안 걸린다.
                  ↑ 그런데 질문이 물은 것(얼마나 바뀌었는가)은 답에 없다.

    trace 가 그 경위를 남긴다 — 1차 생성은 근거 밖 수(`246,125,500,000`)를 써서
    provenance 가 잡았고, **재생성본이 근거 안의 수만 써서 망을 통과했다.** 검사망이
    '근거 밖' 축만 보고 '질문에 답했는가' 축을 안 봤다.

    🔴 **모델의 문장을 고치지 않는다.** 그 서사는 그 자체로 틀리지 않았다(총액이
    1,046,363,500,000 이 된 것은 사실이다). 없는 것은 **질문이 부른 값**이므로 그것만
    한 문장으로 덧붙인다. 지우는 것보다 더하는 쪽이 잃는 것이 적다.

    🔴 **`substitute` 뒤에 서야 한다.** 저쪽이 먼저 갈아 끼우면 값이 이미 실려 이 함수는
    침묵한다 — 그것이 옳은 순서다. 치환할 수 있으면 치환이 낫다(문장이 하나 안 는다).
    """
    total = _total(records, question, route)
    if total is None:
        return answer, ""
    body, tail = split_tail(answer or "")
    if not body or f"{total:,}" in body:
        return answer, ""
    #: 🔴 **연산이 못박힌 질의에만 적는다.** `_op` 가 빈 문자열이면 `_total` 도 None 이라
    #: 여기까지 못 오지만, 조건을 눈에 보이게 남긴다 — 뒷사람이 `_total` 을 넓힐 때
    #: 이 함수가 함께 넓어지면 안 된다.
    sentence = (f"\n\n따라서 질문이 물은 값은 {total:,}원이다. "
                f"(이 값은 시스템이 위 근거의 금액 필드에서 직접 계산한 것이다.)")
    return body + sentence + tail, f"stated {total:,}"


def date_disambiguation(records, question: str) -> str:
    """질문이 못박은 날짜가 **어느 공시를 지목하는가**를 한 줄로 밝힌다. LLM 0회.

    발화하지 않으면 빈 문자열이다.

    🔴 **실측 `K-004`** (2026-08-17). 삼성바이오로직스가 2023-07-04 에 *같은 제목*으로
    두 건을 공시했고 **둘 다 근거에 실렸다**:

        20230704800001  495,278,073,440  정정=True   원공시 2023-03-02
        20230704800004  922,746,708,000  정정=False  원공시 없음(신규)

    모델은 앞엣것을 골랐다. 검색이 아니라 **선택**이 틀렸다.

    🔴 **가르는 규칙은 날짜의 뜻이다.** *"N월 D일 공시한 계약"* 이 지목하는 것은 그날
    체결·공시된 계약이지, **다른 날 계약의 정정본**이 아니다. 정정본의 접수일은 그날이
    맞지만 그 계약이 공시된 날은 원공시일이다. `related` 가 그 사실을 들고 있다.

    🔴 **모호할 때만 발화한다.** 그날 공시가 하나뿐이면 가를 것이 없고, 신규가 둘 이상이면
    우리도 못 가른다 — 둘 다 침묵이다. *"정정"* 을 묻는 질의에서도 침묵한다: 그때는
    정정본이 답이다.
    """
    hit = _FILED_DATE.search(question or "")
    if not hit or any(k in (question or "") for k in ("정정", "수정", "변경 전", "변경 후")):
        return ""
    want = "%s%02d%02d" % (hit.group(1), int(hit.group(2)), int(hit.group(3)))
    same = [r for r in (records or []) if str(r.get("rcept_dt") or "") == want]
    if len(same) < 2:
        return ""
    fresh = [r for r in same if not r.get("is_correction")]
    amended = [r for r in same if r.get("is_correction")]
    if len(fresh) != 1 or not amended:
        return ""
    pick = fresh[0]
    amount = _amount(pick)
    return (f"[결정적 조회] {hit.group(1)}년 {int(hit.group(2))}월 {int(hit.group(3))}일에 "
            f"**공시된** 계약은 접수번호 {pick.get('rcept_no')} 이다"
            + (f" (계약금액 {amount:,}원)" if amount else "")
            + f". 같은 날 접수된 "
            + " · ".join(str(r.get("rcept_no")) for r in amended)
            + " 는 **다른 날 계약의 정정본**이므로 이 질문이 지목한 계약이 아니다.\n"
            + "(이 판정은 시스템이 근거의 접수일과 정정 관계에서 직접 가른 것이다.)")
