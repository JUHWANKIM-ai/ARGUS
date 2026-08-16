"""집계를 **우리가** 계산해 근거에 한 줄로 싣는다. LLM 0회 · 검색 0회 · 스텝 0.

v4 의 단일 발견이 *"모델이 숫자를 손에 쥐는 순간 틀린다"* 였다. `A20`(치환)이 이긴
이유는 재계산값을 근거에서 **우리가 직접 읽어** 모델의 타이핑을 안 거쳤기 때문이고,
`A25`(부착)가 이긴 이유도 접수번호 14자리를 타이핑시키지 않았기 때문이다.

집계도 같은 자리다. R0(2026-08-16)이 오답 7건에서 **항이 근거에 다 있는데 합·차·몫만
틀린 것**을 실측했고, 그 항으로 우리가 계산하면 일곱 건 다 골든이 정확히 나온다:

    Q-018  1,842,200,000,000 − 1,566,300,000,000        = 275,900,000,000
    Q-097  2,492,215,574,520 ÷ 12                       = 207,684,631,210
    Q-109  1,715,754,987,100 + 1,091,844,082,700        = 2,807,599,069,800
    Q-034  398,800억 + 72,200억 + 346,900억              = 817,900,000,000
    Q-115  1,652,800,000,000 + 106,796,802,000          = 1,759,596,802,000  (2025 공시분만)
    Q-107  4,928,968,079,043 + 711,106,288,572 + 1,250,850,298,678
                                                        = 6,890,924,666,293  (한수원 상대만)
    Q-113  (1,328,800,000,000 − 909,590,000,000)
         + (  499,950,000,000 − 392,590,000,000)        = 526,570,000,000    (체인별 차의 합)

## 🔴 근거 칸(`MAX_EVIDENCE`)을 안 먹는다 — 그것이 이 모듈의 설계 전부다

v4 는 같은 문항들을 **근거를 더 주는 방향**으로 세 번 겨냥했고 세 번 다 기각했다:

    A16      `MAX_EVIDENCE` 확대        `R0-3`(dac2bf0) 기각 · 테스트가 고정한다
    A17      성형 게이트 개방            코퍼스 3,150건 중 1,588건(50.4%) 재작성 → BM25 색인 무효
    depth↑   열거 깊이 확대              `MAX_ENUM_EVIDENCE` 가 먼저 차서 집합이 안 바뀐다

셋의 공통 사유가 *"칸을 더 달라"* 였다. **여기서 실리는 것은 블록이 아니라 한 줄**이라
그 사유에 걸리지 않는다.

## 🔴 유일하게 결정되지 않으면 아무것도 안 낸다

틀린 집계를 확신 있게 박아 주는 것은 지금보다 **나쁘다** — 모델은 그 줄을 의심하지
않는다. R0 이 여섯 건 다 **필터가 붙는다**는 것을 실측했다: 필터를 안 걸면 `Q-115` 는
5,230,496,802,000(3배) · `Q-107` 은 7,264,374,092,359 가 나온다. 그래서 침묵이 기본값이고,
발화 조건은 아래 세 함수의 이른 `return ""` 들이다.
"""
from __future__ import annotations

import re

#: 질문이 요구하는 연산. 🔴 **순서가 중요하다** — `차` 를 먼저 봐야 *"얼마나 증가했는가"*
#: 가 `합` 으로 안 샌다 (`pipeline._EVENT_SUBTYPE` 이 `해지` 를 먼저 두는 것과 같은 이유).
_OPS = (("차", ("얼마나 증가", "얼마나 늘었", "얼마나 감소", "얼마나 줄었",
                "증가했는가", "감소했는가", "얼마나 늘어", "얼마나 줄어",
                "합계보다", "차이는")),
        ("몫", ("1척당", "1기당", "1대당", "1건당", "척당", "기당", "대당", "건당",
                "평균 투자금액", "평균 계약금액", "1척 당", "1기 당")),
        ("합", ("합계", "총합", "총액", "모두 더하", "다 더하", "합은", "총 얼마")))

#: `몫` 의 분모. **질문에서 읽는다** — 척수는 근거에 라벨로 안 실린다.
_UNIT_COUNT = re.compile(r"(\d{1,4})\s*(?:척|기|대|건|개)")

#: 접수일 연도 필터. *"2025년에 공시된"* 처럼 **접수일**을 한정할 때만 읽는다.
#: 🔴 사업연도(`base_year`)와 다른 축이다 — 정기공시의 연도는 접수일이 아니다.
_FILED_YEAR = re.compile(r"(20\d{2})\s*년\s*(?:에\s*)?공시")

#: 접수일 **전체 날짜** 필터. `Q-109` *"2023년 12월 11일 두 개 상장사가 각각 공시했다"*
#: 처럼 하루를 못박는 질의에서만 읽는다.
_FILED_DATE = re.compile(r"(20\d{2})\s*년\s*(1[0-2]|0?[1-9])\s*월\s*(3[01]|[12]\d|0?[1-9])\s*일")

#: 질문이 밝힌 **건수**. 🔴 이것이 `합` 의 안전장치다 — 아래 `_sum_rows` 주석 참조.
#:
#: 🔴 **수사 뒤에 셈낱말을 반드시 요구한다.** 첫 판은 `[두세네다섯여섯]` 이라고 썼는데
#: 그것은 **문자 클래스**라 낱자 하나에 걸린다 — *"체코 **두**코바니"* 가 "2건" 으로
#: 읽혔다(실측). 대안 묶음 `(?:두|세|…)` 과 셈낱말이 함께여야 한다.
_STATED_COUNT = re.compile(
    r"(\d{1,2})\s*(?:건|개\s*(?:상장사|회사|사)?|곳|군데)"
    r"|(?:^|[\s,.])(두|세|네|다섯|여섯)\s*(?:건|개|곳|군데|공시|회사|상장사)")
_WORD_COUNT = {"두": 2, "세": 3, "네": 4, "다섯": 5, "여섯": 6}


#: 🔴 **줄을 싣는 것만으로는 부족했다** (2026-08-16 실측). `Q-034` 에서 집계 줄이
#: 정확한 값(817,900,000,000)을 싣고 발화했는데도 모델이 **자기가 다시 더해**
#: 778,100,000,000 을 썼다. 직전 세대의 오답(757,900,000,000)과도 다른 값이다 —
#: 즉 모델은 우리 줄을 읽지 않은 것이 아니라 **자기 계산을 더 믿었다.**
#:
#: 그래서 값 옆에 **처분을 명시한다.** `calc_values`(A5)가 근거 괄호에 환산값을 박는
#: 것과 같은 자리이고, 다른 점은 *"다시 계산하지 말라"* 를 함께 적는다는 것뿐이다.
#: 🔴 시스템 프롬프트가 아니라 **근거 안**이다 — KDD '26 실측에서 시스템 프롬프트는
#: attention 의 36.9% 를 먹고 근거는 14.0% 인데, 규칙을 시스템으로 올리면 집계와
#: 무관한 문항의 답까지 흔든다 (`A27` 이 그렇게 역효과를 냈다).
_DIRECTIVE = ("(이 값은 시스템이 위 근거의 금액 필드에서 직접 계산한 것이다. "
              "답변의 해당 수치는 이 값을 그대로 쓰고 다시 계산하지 않는다.)")


def _op(question: str) -> str:
    text = question or ""
    for name, hints in _OPS:
        if any(h in text for h in hints):
            return name
    return ""


#: 답변에서 후보로 볼 금액. 콤마 세 자리 묶음을 요구한다 — `A25` 의 `_CITE_NUM` 과 같은
#: 이유로, 연도(2025)나 비율(2.93)이 금액으로 잡히면 안 된다.
_ANSWER_NUM = re.compile(r"\d{1,3}(?:,\d{3})+")

#: 답변 뒤에 붙는 우리 부속물. 치환 대상을 찾을 때 **본문만 본다** — 각주와 근거 표기는
#: 우리가 쓴 것이라 거기서 수를 고르면 우리 글을 우리가 고치는 꼴이 된다.
_TAIL = re.compile(r"\n*\((?:근거|주의)[:：]")

#: 결론 문장을 여는 말. 🔴 **첫 판은 배율 범위(0.5~2배)로 후보를 좁혔는데 그것이
#: 틀린 잣대였다** — `Q-113` 에서 모델이 1,237,510,000,000 을 냈고(정답 526,570,000,000)
#: 2.35배라 가드에 걸려 치환이 안 돌았다. 모델의 오차 크기는 우리가 못 정한다.
#: 대신 **자리**를 본다: 답변이 최종 답으로 내세우는 수는 결론 문장에 있다.
_CONCLUSION = ("따라서", "그러므로", "결론적으로", "정리하면", "즉,")

#: 금액으로 볼 최소 규모. 이보다 작으면 척수·비율·연번이다.
MIN_SUBSTITUTE_AMOUNT = 100_000_000

#: 접수일 필터가 걸렸다는 표시. `_sum_rows` 가 이 낱말로 **접수일 축과 계약상대 축을
#: 가른다** — 앞은 집합을 못박고 뒤는 못박지 못한다 (아래 주석 참조).
_DATE_NOTE = "공시분"


def substitute(answer: str, records, question: str, route) -> tuple[str, str]:
    """답변의 **틀린 합계 하나**를 결정적 집계값으로 갈아 끼운다. LLM 0회.

    돌려주는 것은 `(새 답변, 사유)` 이고, 갈아 끼울 것이 없으면 `(원본, "")` 이다.

    🔴 **왜 줄을 싣는 것으로 끝내지 않는가.** 실측(2026-08-16)이 세 번 말했다 —
    집계 줄에 정확한 값을 싣고 *"다시 계산하지 말라"* 는 지시까지 붙여도 모델은
    자기가 다시 더한다 (`Q-034` 817,100,000,000 · `Q-109` 2,807,598,070,800 ·
    `Q-115`). v4 의 발견 그대로다: **모델이 숫자를 손에 쥐는 순간 틀린다.**

    🔴 **`A20`(`substitute_verified`)이 못 잡는 자리를 잡는다.** 저쪽은 `critic` 이
    답변에서 **식을 파싱해** 재계산할 때만 발화한다. `Q-109` 는 두 금액을 줄바꿈으로
    나열하고 `=` 를 안 써서 식이 없다 — 그래서 `equation` 이 안 걸린다. 이쪽의 원천은
    답변이 아니라 **근거의 `amount` 필드**라 답변의 형태와 무관하다.

    🔴 **유일성 조건은 `critic.unique_recomputed` 와 같은 규칙이다.** 후보가 둘 이상이면
    치환하지 않는다 — 어느 것이 합계인지 모르는 채로 고르면 멀쩡한 수를 망가뜨린다.
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
    return answer.replace(wrong, f"{total:,}"), f"{wrong} -> {total:,}"


#: 정정 이력을 **펼쳐** 달라는 표현. 🔴 `_OPS` 의 `차` 와 겹치지 않게 둔다 — 저쪽은
#: *"얼마나 증가했는가"*(값 하나)이고 이쪽은 *"어떻게 바뀌었는지 시간순으로"*(줄 하나에
#: 여러 값)다. 같은 질문에 둘 다 발화하면 근거에 모순되는 두 줄이 실린다.
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


def _conclusion(answer: str) -> str:
    """답변이 **최종 답으로 내세우는** 대목. 없으면 본문 전체.

    🔴 각주·근거 표기(`(주의: …)` · `(근거: …)`)는 **우리가 붙인 것**이라 잘라낸다.
    거기 실린 수를 치환하면 우리 글을 우리가 고치는 꼴이고, 각주의 재계산값이
    치환 대상으로 잡히면 후보가 둘이 되어 침묵으로 빠진다.
    """
    body = _TAIL.split(answer or "", 1)[0]
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
