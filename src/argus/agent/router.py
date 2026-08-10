"""질의 라우터. 규칙으로 갈리면 LLM 을 쓰지 않는다."""
from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from functools import lru_cache

from argus.agent.query import report_hints
from argus.corpus.counterparty import canonical
from argus.corpus.manifest import filter_docs, load_universe, resolve_company

_YEAR = re.compile(r"(20\d{2})\s*년?")
# '대비'·'와 '·'과 ' 는 뺐다.
#   - '매출액 대비 비율' 은 단일 지표 표현인데 비교로 잡혀 CoT+SC 경로로 가서 비용이 수 배 뛴다.
#   - '와/과' 는 '계약과 ' 같은 데도 걸린다. 기업이 둘 이상인지는 len(companies) 로 이미 판정한다.
_COMPARE_HINT = ("비교", "중 어", "더 큰", "더 많", "vs")
_CALC_HINT = ("증감률", "증가율", "감소율", "비중", "몇 %", "몇 퍼센트", "합계", "총액", "차이")
_OPEN_HINT = ("정리해", "설명해", "어떻게 변화", "요약해", "알려줘")
_EXISTS_HINT = ("존재하는가", "있는가", "있나")

#: 제공 코퍼스 기간 2023.01~2026.03 (CONVENTION §2 G7).
CORPUS_YEARS = range(2023, 2027)

#: 상대 시점 표현. 이게 있으면 연도 숫자가 범위 밖이어도 차단하지 않는다 —
#: '2021년 대비 최근' 처럼 기준점만 범위 밖인 질의를 죽이면 답할 수 있는 걸 못 답한다.
_RELATIVE_HINT = ("최근", "작년", "올해", "지난", "전년", "직전", "현재", "이후", "까지", "부터")

#: 정기공시로 가야 하는 신호. **'보고서' 단독은 안 된다** — 지분공시가
#: '대량보유상황보고서' 라서 통째로 정기공시로 오라우팅된다 (실측 4문항).
_PERIODIC_HINT = (
    "사업보고서", "반기보고서", "분기보고서", "사업 보고서", "반기 보고서", "분기 보고서",
    "요약재무정보", "연결재무제표", "재무제표 주석", "재무에 관한 사항", "매출 및 수주",
    "경영진단", "감사의견", "내부통제", "회사의 개요", "사업의 내용", "주주에 관한 사항",
    "임원의 보수", "배당에 관한 사항", "연결기준", "연결 기준", "별도기준",
)
_PERIODIC_RE = re.compile(r"제\s*\d+\s*기|연결\s*(?:기준\s*)?(?:매출액|영업이익|당기순이익|자산총계)")

#: 지분공시(대량보유상황보고서) 신호. 정기공시보다 먼저 본다 — '보고서' 가 겹치기 때문이다.
_HOLDING_HINT = ("대량보유", "보유상황보고서", "보유비율", "보유주식", "지분 보유",
                 "소유상황", "보유 목적", "주식등의 대량", "지분율", "%p")

#: 주요사항보고 신호. 증자·사채·합병·자기주식 등 §7.2 유형 2 의 어휘다.
_MAJOR_HINT = ("유상증자", "무상증자", "전환사채", "신주인수권부사채", "교환사채",
               "조건부자본증권", "권면", "합병", "분할", "자기주식", "감자",
               "영업양수", "영업양도", "자산양수", "자산양도", "주요사항보고서",
               "사채", "표면이자율", "전환가액", "자금조달")


def doc_group(question: str) -> str:
    """질의를 문서군으로 가른다. **인덱스가 문서군마다 따로**라 이게 곧 인덱스 선택이다.

    순서가 중요하다 — 지분공시가 '대량보유상황보고서' 라서 '보고서' 를 먼저 보면
    지분 질의 4문항이 통째로 정기공시로 샌다. 실측 60문항 분류 정확도 60/60이고
    **exchange 24문항은 전부 exchange 로 남는다** (기존 성능을 건드리지 않는 조건).
    """
    text = question or ""
    if any(h in text for h in _HOLDING_HINT):
        return "holding"
    if is_periodic(text):
        return "periodic"
    if any(h in text for h in _MAJOR_HINT):
        return "major"
    return "exchange"


@dataclass
class Route:
    kind: str
    difficulty: str = "low"
    companies: list[str] = field(default_factory=list)
    years: list[int] = field(default_factory=list)
    use_cot: bool = False
    use_sc: bool = False
    by: str = "rule"
    group: str = "exchange"
    reason: str = ""
    #: 질문이 지목한 **발주처**(계약상대). 기업(`companies`)과 축이 다르다 —
    #: 이쪽은 계약을 **주는** 쪽이고 공시를 낸 쪽이 아니다. 없으면 None.
    counterparty: str | None = None


def is_periodic(question: str) -> bool:
    """정기공시 섹션 검색으로 보낼 질의인가."""
    text = question or ""
    return any(h in text for h in _PERIODIC_HINT) or bool(_PERIODIC_RE.search(text))


def out_of_corpus(years: list[int], question: str = "") -> bool:
    """질문이 지목한 연도가 **전부** 코퍼스 밖인가.

    '전부' 가 중요하다. '제53기(2021년)와 제57기(2025년)' 는 2021 이 밖이지만
    2025 로 답이 나온다 — 하나라도 안에 있으면 차단하지 않는다.
    상대 시점 표현이 섞여 있어도 차단하지 않는다 (연도 숫자가 기준점일 뿐일 수 있다).
    """
    if not years or any(y in CORPUS_YEARS for y in years):
        return False
    return not any(h in (question or "") for h in _RELATIVE_HINT)


# --- A7 ① 지목한 보고서가 코퍼스에 없다 -----------------------------------------
# 환각 1건(Q-040)의 기전은 '범위 밖 연도' 가 아니다. 2026 은 코퍼스 안이고 기업도 안에
# 있어서 `out_of_corpus` 가 안 걸린다. 저장 응답이 보여주는 실제 경로는 이렇다.
#
#   hop 0  filters=base_year=2026,doc_subtype=half   n=0   ← 지목한 문서가 없다
#   hop 1  filters=-                                 n=10  ← 필터를 통째로 풀었다
#   generate → 2026년 1분기 분기보고서 값을 '2026년 반기보고서' 로 답했다
#
# 사다리는 '못 찾았다' 와 '없다' 를 구분하지 못한다. 그래서 **검색이 아니라 manifest**
# 에 물어본다 — 질문이 문서를 지목했는데 그 문서가 목록에 없으면 완화는 오답 제조다.
#
# 🔴 과잉 거절이 진짜 위험이라 문턱을 셋 둔다.
#   ① 보고서 **종류만으로는 막지 않는다** — '제57기 사업보고서'(Q-046)·'기아 사업
#      보고서'(Q-076) 처럼 종류만 있는 질의는 문서가 특정되지 않는다.
#   ② 사업연도·접수시점 축이 **둘 다 비어야** 막는다. `base_year` 는 추정이 섞이는
#      축이라(Q-052 '2024년 3월 20일 제출한 2023년 사업보고서') 한 축만 보면 오차단이다.
#   ③ 기업이 여럿이면 **한 곳이라도 문서가 있으면** 막지 않는다.
#
# 실측 (dev 78 + 회귀 10 = 88문항, LLM 0회): 발화 2건 — Q-040 · Q-070. 둘 다
# `answerable=false` 다. 답할 수 있는 문항에서의 발화는 **0건**이다.

def _report_axes(hints: dict) -> dict:
    """문서를 특정하는 축만 남긴다. 종류는 축이 아니라 전제다 (문턱 ①)."""
    return {k: v for k, v in hints.items() if k in ("base_year", "filed") and v}


def missing_named_report(question: str, companies: Sequence[str]) -> str:
    """질문이 지목한 정기보고서가 manifest 에 **없는가**. 있으면 빈 문자열. LLM 0회.

    검색이 아니라 목록 조회다 — 스텝을 쓰지 않고 인덱스도 열지 않는다.
    돌려주는 문자열은 `think_trace` 에 남길 사유이고, 비어 있으면 종전 경로 그대로다.
    """
    hints = report_hints(question or "")
    subtype = hints.get("doc_subtype")
    axes = _report_axes(hints)
    if not (subtype and axes and companies):
        return ""
    for corp in list(companies)[:3]:
        docs = filter_docs(corp_name=corp, doc_group="periodic", doc_subtype=subtype)
        for axis, value in axes.items():
            seen = (str(d.get("base_year", "")) if axis == "base_year"
                    else str(d.get("rcept_dt", ""))[:6] for d in docs)
            if any(v == value for v in seen):
                return ""                     # 문턱 ②③ — 한 축·한 기업이라도 맞으면 통과
    return f'{subtype}:' + ",".join(f"{k}={v}" for k, v in sorted(axes.items()))


# --- 발주처(counterparty) 판정 --------------------------------------------------
# 밸류체인 문항은 '한 발주처와 계약한 상장사 전체' 를 요구한다. 그 집합은 검색으로
# 좁혀지는 것이 아니라 **그래프가 이미 갖고 있는 것**이다 (Task 3 산출물, 노드 145 ·
# 허브 25). 그래서 판정은 정규식으로 회사명을 추측하지 않고 **노드 이름과의 일치**로만 한다.
#
# 🔴 노드 이름만 보면 과잉 발화한다. 노드 이름이 질문에 들어 있는 문항 수 실측
# (분모를 명시한다 — canonical 기준):
#
#   dev 42문항(측정 코호트) **20** · dev 78문항(파일 전체) **24** · 회귀 10문항 **3**
#
# 삼성전자 · 두산에너빌리티 · HMM · SK하이닉스처럼 **공시를 낸 쪽**으로 등장하는
# 이름이 그대로 노드이기 때문이다. 그대로 발화시키면 종전 성능이 통째로 흔들린다.
# 제동 둘을 건다.
#
#   ① 문서군이 **exchange** 일 것 — 그래프 모집단이 거래소공시다 (§7.7 ②, `query_events`
#      폴백에 건 것과 같은 제동). 실측 Q-045 가 이 경계다: '발주처·품목·계약기간·수주총액'
#      이라 어휘가 걸리지만 근거는 사업보고서 '매출 및 수주상황' 섹션이고, 그 골든은
#      3,679자라 발주처 레코드가 끼면 근거 칸에서 밀린다.
#   ② **역할 결속** — 발주 방향 표지(`계약상대`·`발주`)가 **노드 이름 바로 뒤**에 와야 한다.
#
# ② 가 어휘 존재 검사가 아니라 결속 검사인 이유는 반례가 실재하기 때문이다.
#
#     "삼성전자와 SK하이닉스와 현대자동차가 2024년에 **수주**한 계약의 계약금액을 비교해줘"
#
# 셋 다 노드이고 어휘도 있지만 셋 다 **공시한 쪽**이다. 어휘를 문장 어디서나 찾으면
# `sk하이닉스` 가 발주처로 잡힌다. 노드 뒤에 붙은 것이 '가 … 수주한' 이면 그 노드는
# 받는 쪽이다 — 결속을 보면 걸러진다.
#
# 반대로 `route.companies` 를 통째로 배제하는 방법은 **쓰지 않았다.** 실측하면 밸류체인
# 발화가 9 → 5 로 줄고 Q-113·Q-114·Q-115 가 함께 빠진다 — 그 셋은 발주처가 질문에도
# 등장하는 universe 기업(삼성전자 · 현대자동차)이라 배제하면 정작 겨냥한 Q-114 가
# 0/5 로 되돌아간다. 배제해야 하는 것은 '질문에 나온 기업' 이 아니라 '받는 쪽으로
# 나온 기업' 이고, 그 둘을 가르는 것이 역할 결속이다.
#
# 실측 발화 (LLM 0회): dev 42 **0건** · dev 78 **0건** · 회귀 10 **0건** ·
# 밸류체인 12 중 **8건** (Q-107·109·110·113·114·115·116·118).
# Q-108 은 결속에서 빠진다 — 그 문장에서 두산에너빌리티는 '한전기술과 함께 **공시한**
# 기업' 으로 나온다. 발주처인 것은 본문이 아니라 답에서 드러나는 사실이라 규칙으로는
# 안 잡히고, 실측상 그 hop 이 Q-108 에 기여한 골든도 0건이다 (3/4 로 불변).
#
# 어휘를 더 넓히지 않았다. '계약상대' 만 있고 노드가 없는 dev 문항이 4건 있는데
# (Q-008·Q-033·Q-034·Q-041) 전부 지금 맞히는 문항이고, Q-033·Q-034 는 체인 제동이
# 지키고 있는 문항이라 노드 일치 없이 어휘만으로 발화시키면 그것들이 먼저 깨진다.

#: 발주 방향 표지. 이게 없으면 노드 이름은 그냥 등장하는 회사 이름이다.
#: `canonical()` 이 공백을 지우므로 '계약 상대' 도 이 형태가 된다.
_COUNTERPARTY_HINT = ("계약상대", "발주")

#: 노드 이름 **바로 뒤**에 와야 하는 것. 조사는 있어도 없어도 된다.
#:
#: 🔴 **`와`·`과` 를 넣지 마라.** 조사라는 이유로 넣고 싶어지지만 그 둘은 격조사가 아니라
#: **접속·공동격**이다. `"X와 발주처 Y"` 는 `"X 그리고 발주처 Y"` 라서 발주처는 Y 인데,
#: 결속으로 읽으면 X 가 잡힌다. 실측 오탐 3건 (전부 앞의 기업이 **공시한 쪽**이다):
#:
#:   "삼성전자와 발주처 한국수력원자력의 계약 관계를 정리해줘"     -> 삼성전자
#:   "SK하이닉스와 발주처가 겹치는 공급계약이 있는가?"             -> sk하이닉스
#:   "두산에너빌리티와 발주 구조가 같은 계약을 공시한 상장사는?"   -> 두산에너빌리티
#:
#: 긴 이름 우선 정렬로도 안 막힌다 — 첫 문장에서 `한국수력원자력` 뒤에는 `의계약관계`
#: (≠`계약상대`)가 와서 탈락하고 `삼성전자와발주` 가 통과한다.
#:
#: 빼는 값은 0이다 (실측): 밸류체인 발화 8건이 전부 `을/를/이/가` 를 쓴다.
#: `의` 는 남긴다 — `"한국수력원자력의 발주"` 는 관형격이라 그 노드가 발주 주체가 맞다.
_COUNTERPARTY_ROLE = re.compile(r"^(?:을|를|이|가|은|는|의)?(?:계약상대|발주)")

# --- 기각한 확장: 표지 **선행**형 (`"발주처 한국수력원자력"`) --------------------
# 표지가 노드 뒤가 아니라 앞에 오는 어순은 지금 못 잡는다. 그래서 `"발주처 X"` 같은
# 지극히 평범한 표현이 미탐이 되고, 미탐은 `out_of_universe` 단락으로 이어져 **답을
# 아예 못 낸다**. 부드럽게 실패하지 않으므로 넓힐 값이 있어 보였고, 평가셋 전 코호트에서
# 추가 발화가 0인 것도 확인했다.
#
# 🔴 그런데 **반례를 직접 지어 돌리니 오탐 3건**이 나왔다. 기전은 `와|과` 때와 같다 —
# **떨어져 있는 것을 결속으로 읽는다.** 다만 이번엔 정규식이 허용한 게 아니라
# `canonical()` 이 문장부호(`.` `,` `·`)와 공백을 지워 **없던 인접이 생긴다.**
#
#   "이 계약의 핵심 변수는 발주처. 삼성전자가 공시한 건수는?"  -> 삼성전자
#   "관건은 계약상대. 두산에너빌리티가 낸 공시를 정리해줘."     -> 두산에너빌리티
#   "주요 발주처, 삼성전자가 공시한 계약 건수는?"              -> 삼성전자
#
# 셋 다 그 노드가 **공시한 쪽**이다. 우리 평가셋에도 문장이 둘인 문항이 있어
# (Q-052·Q-054·Q-113 — 셋 다 문장 중간에 `.` 가 있다) 지어낸 어순이 아니다.
#
# 미탐 1건을 없애자고 오탐 3건을 만들 수는 없다. **채택하지 않았다.**
# 절 단위로 쪼갠 뒤 결속을 보면(문장부호로 split) 오탐 3건이 사라지고 발화도 그대로인
# 것까지 실측했지만, 그건 검증받은 적 없는 기전을 하나 더 얹는 일이라 남겨 둔다.
# `tests/test_router.py::test_marker_before_the_node_is_not_bound` 가 이 경계를 고정한다.


@lru_cache(maxsize=1)
def _counterparty_nodes() -> tuple[str, ...]:
    """그래프 노드의 canonical 이름을 **긴 것부터**. 짧은 이름이 긴 이름 안에 들어 있을 때
    (`두산에너빌리티` ⊃ `두산`) 긴 쪽이 이겨야 한다 — `_find_companies` 와 같은 규칙이다.

    그래프가 없으면 빈 튜플이다. 라우터는 죽지 않고 종전 경로로 간다.
    """
    from argus.agent.tools import _counterparty_graph

    graph = _counterparty_graph()
    if not graph:
        return ()
    nodes = sorted(graph.get("nodes") or (),
                   key=lambda n: (-len(n.get("canonical") or ""), -(n.get("count") or 0)))
    return tuple(n["canonical"] for n in nodes if n.get("canonical"))


def find_counterparty(question: str, group: str = "exchange") -> str:
    """질문이 **발주처로 지목한** 그래프 노드의 canonical 이름. 아니면 빈 문자열.
    LLM 0회 · 검색 0회.

    `canonical()` 이 표기 변이(국호 접두 · 법인격 접미 · 괄호 약칭 · 공백)를 접으므로
    '한국수력원자력' · '한국수력원자력(주)' · '한국수력원자력㈜' 가 같은 키로 걸린다
    (03-graph-schema C-0b 실측 5유형). 이름은 그래프에서 오고 **역할만** 문장에서 읽는다 —
    회사명을 정규식으로 추측하지 않는다.
    """
    if group != "exchange":
        return ""
    key = canonical(question or "")
    if not any(h in key for h in _COUNTERPARTY_HINT):
        return ""
    for node in _counterparty_nodes():          # 긴 이름부터
        start = 0
        while (index := key.find(node, start)) >= 0:
            if _COUNTERPARTY_ROLE.match(key[index + len(node):]):
                return node
            start = index + 1
    return ""


def _find_companies(question: str) -> list[str]:
    """universe 의 모든 별칭을 긴 것부터 매칭한다 (부분문자열 오탐 방지)."""
    found: list[str] = []
    aliases: list[tuple[str, str]] = []
    for corp_name, row in load_universe().items():
        for alias in (corp_name, row.get("listed_name"), row.get("corp_eng_name")):
            if alias and len(alias) >= 2:
                aliases.append((alias, corp_name))
    for alias, corp_name in sorted(aliases, key=lambda x: -len(x[0])):
        if alias in question and corp_name not in found:
            found.append(corp_name)
    return found


def route(question: str) -> Route:
    companies = _find_companies(question)
    years = sorted({int(y) for y in _YEAR.findall(question)})
    group = doc_group(question)
    counterparty = find_counterparty(question, group) or None

    is_open = any(h in question for h in _OPEN_HINT)
    is_calc = any(h in question for h in _CALC_HINT)
    is_compare = len(companies) >= 2 or any(h in question for h in _COMPARE_HINT)
    is_exists = any(h in question for h in _EXISTS_HINT)

    def made(kind, difficulty="low", cot=False, sc=False, reason="") -> Route:
        return Route(kind=kind, difficulty=difficulty, companies=companies, years=years,
                     use_cot=cot, use_sc=sc, group=group, reason=reason,
                     counterparty=counterparty)

    if not companies and not counterparty:
        # 기업명이 없어도 '~존재하는가' 는 코퍼스 전역 존재 질의다.
        # 답변 불가로 단락시키면 근거가 있는데도 못 찾는다 (해지 사례 404건 실재).
        if is_exists:
            return made("complex_reason", "high", cot=True, sc=True)
        return made("unanswerable", reason="out_of_universe")

    # 발주처는 universe 밖일 수 있다 — 한국수력원자력·한국중부발전 같은 공공기관이
    # 최대 허브다(03-graph-schema C-0 이 기각한 전제가 실제로는 참이었다). 기업명이
    # 없다고 단락시키면 **파트너가 전부 상장사인데도** 답을 못 낸다 (실측 Q-107·109·116).

    # 코퍼스는 2023.01~2026.03 이다. 범위 밖 연도만 지목한 질의는 **검색으로 구제되지
    # 않는다** — 기간 필터를 푸는 순간 엉뚱한 연도 공시가 근거로 둔갑한다. 검색 단계에서
    # 막으면 스텝을 태우고 나서야 한계를 고지하므로 여기서 단락시킨다 (스텝 0).
    if out_of_corpus(years, question):
        return made("unanswerable", reason=f"out_of_range:{years}")

    # 질문이 **문서를 지목**했는데 그 문서가 목록에 없다. 필터를 푸는 순간 같은 기업의
    # 다른 보고서가 근거로 둔갑하므로(실측 Q-040) 여기서 단락시킨다 (스텝 0 · LLM 0회).
    if group == "periodic":
        missing = missing_named_report(question, companies)
        if missing:
            return made("unanswerable", reason=f"no_such_report:{missing}")

    if is_open and (len(years) >= 2 or "변화" in question):
        return made("complex_reason", "high", cot=True)
    if is_compare or is_calc:
        return made("multi_compare", "high", cot=True, sc=not is_open)
    if is_exists:
        return made("complex_reason", "high", cot=True, sc=True)
    if is_open:
        return made("closed_lookup", "high", cot=True)
    return made("closed_lookup", "low")
