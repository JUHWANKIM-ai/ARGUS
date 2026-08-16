"""질의어 추출과 hop 별 재구성. LLM 호출 0회, 전부 규칙이다."""
from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field

# 전 문서에 공통으로 등장해 변별력이 없는 어휘 (실측 기반)
#
# '해지' 는 뺐다 — 실측 DF 0.014 (1,469건 중 21건) 로 오히려 최상위 변별 어휘다.
# 손으로 '체결'(DF 0.766) 과 나란히 넣은 것이 잘못이었다. 되살리면 두산퓨얼셀 해지 3건
# 질의의 상위 3위가 1건 → 3건으로 바뀐다 (16.9/16.2/16.1 vs 9.7/9.4/9.3).
STOPWORDS = {
    "공급계약", "단일판매", "판매공급계약", "계약금액", "체결",
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


# --- F-3 질의 재구성 ----------------------------------------------------------
# 검색이 실패하면 조건을 하나씩 푼다. 넓히는 축은 **필터**이고, 질의어는 반대로 **좁힌다** —
# 기간·기업 필터가 걸러주던 잡음을 필터를 풀면 질의어가 그대로 뒤집어쓰기 때문이다.

#: hop 별 전략. hop 0 은 최초 검색이라 예산을 쓰지 않고, hop 1~3 이 Budget.spend_hop() 을 쓴다.
#: (docs/pipeline/06-agent-tool.md F-3 의 "기간 필터 완화 → 기업 필터만 → 전역")
STRATEGIES = ("narrow", "relax_period", "corp_only", "global")

#: hop 별 top_k. 필터를 풀수록 후보를 넓게 받는다. MAX_EVIDENCE(10) 를 넘길 이유는 없다.
TOP_K = (5, 8, 10, 10)

#: 실측 문서빈도(DF) 0.30 이상 — 있어도 문서를 가르지 못하는 어휘.
#: artifacts/exchange_bm25.json 1,469건 기준: 계약 0.80 · 상대 0.77 · 금액 0.73 ·
#: 매출 0.72 · 대비 0.72 · 정정 0.43. 숫자 토큰(2023 0.34 · 2025 0.41)은 아래 _NUMERIC 로 뺀다.
COMMON_TERMS = frozenset({"계약", "상대", "금액", "매출", "대비", "정정"})

_NUMERIC = re.compile(r"^[0-9][0-9,.]*$")
#: 어절 그대로 남은 서술어 꼬리. '체결한'→'체결'(불용어) · '해지된'→'해지'(변별어) 로 가른다.
_TAIL = re.compile(r"(하는|되는|이는|한|된|인|는|나|줘|해)$")


def _stem(word: str) -> str:
    if len(word) <= 2:
        return word
    stem = _TAIL.sub("", word)
    return stem if len(stem) >= 2 else word


def core_terms(terms: Sequence[str]) -> list[str]:
    """필터를 풀 때 쓸 핵심어만 남긴다. 남는 게 없으면 빈 목록 — 추측하지 않는다."""
    out: list[str] = []
    for raw in terms:
        if _NUMERIC.match(raw):
            continue
        word = _stem(raw)
        if word in STOPWORDS or word in COMMON_TERMS:
            continue
        if word not in out:
            out.append(word)
    return out


def relax_period(period: dict) -> dict:
    """기간 필터를 한 단계만 넓힌다. 연월 → 연도 → 없음."""
    if "ym" in period:
        return {"year": int(str(period["ym"])[:4])}
    return {}


# --- 다지점 질의 분해 ----------------------------------------------------------
# 사다리는 한 질의를 **넓히기만** 한다. 그런데 "A사 2024년 것" 과 "B사 2025년 것" 은
# 넓혀서 동시에 얻을 수 없다 — 넓히면 둘 다 흐려진다. 필요한 것은 넓히기가 아니라 분해다.
#
# 실측 (dev 42문항, LLM 0회): 시점을 제대로 걸어 재질의하면 골든이 **전부 1위**다.
#   Q-012 ym=202503 -> 1위 (65.9) · ym=202603 -> 1위 (68.8)
#   Q-014 삼성SDI ym=202503 -> 1위 (33.7) · 고려아연 ym=202410 -> 1위 (41.6)
#   Q-015 ym=202507 -> 1위 (57.7) · ym=202405 -> 1위 (57.8)
# 그러니 셀마다 넓게 받을 이유가 없다. 셀 top_k 를 줄여 근거 부풀림을 막는다.
#
# 교차곱이 헛돌지도 실측했다 — 고려아연 ym=202503 은 후보 1건, 삼성SDI ym=202410 은 0건.
# 어긋난 셀은 필터가 스스로 비운다.

#: 시점 축 상한. 실측 dev 42문항에서 시점이 둘 이상인 문항은 7건이고 **전부 정확히 2개**다.
#: 3 은 여유값이다 — 이 위로 가면 셀이 기업 수(최대 3)와 곱해져 스텝 상한 8을 넘긴다.
MAX_TIME_POINTS = 3

#: 분해된 셀 하나의 top_k. 골든이 셀 안에서 1위라 3이면 남고, 근거 칸이 셀 수만큼 부풀지 않는다.
SPLIT_TOP_K = 3

_YM_ALL = re.compile(r"(20\d{2})\s*년\s*(1[0-2]|0?[1-9])\s*월")


def time_points(question: str, years: Sequence[int] = ()) -> list[dict]:
    """질문이 지목한 **서로 다른 시점**. 2개 미만이면 빈 목록 — 분해하지 않는다.

    연월이 우선이다. '2025년 3월과 2026년 3월' 을 연도로 뭉개면 두 보고서가 같은 셀에
    들어가 랭킹이 다시 한쪽을 덮는다. 연월이 하나뿐이면 연도 축으로 내려간다
    ('제53기(2021년)와 제57기(2025년)' 처럼 월이 아예 없는 질의).
    """
    yms = list(dict.fromkeys(f"{m.group(1)}{int(m.group(2)):02d}"
                             for m in _YM_ALL.finditer(question or "")))
    if len(yms) >= 2:
        return [{"ym": v} for v in yms[:MAX_TIME_POINTS]]
    picked = list(dict.fromkeys(int(y) for y in years))
    if len(picked) >= 2:
        return [{"year": y} for y in picked[:MAX_TIME_POINTS]]
    return []


@dataclass(frozen=True)
class HopPlan:
    """한 hop 의 검색 계획. LLM 없이 규칙으로 만든다."""

    hop: int
    strategy: str
    terms: tuple[str, ...] = ()
    period: dict = field(default_factory=dict)
    use_corp: bool = True
    top_k: int = 5
    #: 분해 셀. 비어 있으면 `period` 하나로 검색한다 (종전 동작).
    periods: tuple[dict, ...] = ()

    @property
    def query(self) -> str:
        return " ".join(self.terms)


def plan_hops(question: str, period: dict,
              fallback_terms: Sequence[str] = (),
              periods: Sequence[dict] = ()) -> list[HopPlan]:
    """좁은 것부터 넓히는 hop 계획 4단.

    hop 0 `narrow`       — 전체 어휘 + 연월(또는 연도) + 기업 필터
    hop 1 `relax_period` — 기간 필터 한 단계 완화 (연월 → 연도 → 없음)
    hop 2 `corp_only`    — 기간 필터 해제, 기업 필터만. 질의는 핵심어로 축소
    hop 3 `global`       — 기업 필터까지 해제. 마지막 수단

    `periods` 가 둘 이상이면 **hop 0 만** 셀로 쪼갠다. 넓히는 hop 까지 곱하면
    스텝이 상한 8을 넘고, 어차피 넓힌 뒤에는 시점이 필터에서 빠져 분해가 무의미하다.
    """
    terms = list(extract_terms(question))
    core = core_terms(terms)
    fallback = [t for t in fallback_terms if t]
    if not core:
        # 질문에 남는 내용어가 없다 — 기업명이 유일한 단서다.
        core = fallback or terms
        terms = terms + [t for t in fallback if t not in terms]
    if not terms:
        terms = core
    cells = tuple(dict(p) for p in periods) if len(periods) >= 2 else ()
    head_k = SPLIT_TOP_K if cells else TOP_K[0]
    return [
        HopPlan(0, "narrow", tuple(terms), dict(period), True, head_k, cells),
        HopPlan(1, "relax_period", tuple(terms), relax_period(period), True, TOP_K[1]),
        HopPlan(2, "corp_only", tuple(core), {}, True, TOP_K[2]),
        HopPlan(3, "global", tuple(core), {}, False, TOP_K[3]),
    ]


# --- 정기공시 질의 계획 --------------------------------------------------------
# 정기공시는 exchange 와 검색 축이 다르다. 실측으로 확인된 차이 셋:
#
# 1. **접수연도 ≠ 사업연도.** '2023년 사업보고서' 는 2024-03 에 접수된다. exchange 에서
#    결정적이었던 `rcept_dt` prefix 를 그대로 쓰면 골든이 통째로 필터에서 빠진다.
# 2. **본문이 거의 같다.** 같은 기업의 분기·반기·사업보고서가 같은 목차에 거의 같은 문장을
#    싣는다 — BM25 점수가 19.69 로 4중 동점이 된다. 변별은 `doc_subtype`·`base_year` 가 한다.
# 3. **목차가 전 기업 공통이다.** 그래서 질문 어휘를 목차 제목에 맞춰 `path` 를 먼저 고르는
#    결정적 선택(§7.7 ③④⑤)이 성립한다. 실측: 골든 회수 0.679 -> 0.929, 1위 11 -> 19.

#: 보고서 종류 표기 -> `doc_subtype`.
REPORT_SUBTYPES = (
    ("annual", re.compile(r"사업\s*보고서")),
    ("half", re.compile(r"반기\s*보고서")),
    ("quarter", re.compile(r"분기\s*보고서")),
)

#: '2023년 사업보고서' 처럼 **보고서 바로 앞에 붙은 연도**만 사업연도로 본다.
#: '2024년 3월 20일 제출한 2023년 사업보고서' 에서 2024 를 집으면 정반대 문서가 나온다.
_REPORT_YEAR = re.compile(
    r"(20\d{2})\s*년?\s*(?:도)?\s*(?:\d\s*분기\s*)?(?:사업|반기|분기)\s*보고서")
#: 제출·접수 시점 표기. 이건 `rcept_dt` 축이다. '일' 이 한글이라 일자 부분을 따로 흘려보낸다.
_FILED = re.compile(r"(20\d{2})\s*년\s*(1[0-2]|0?[1-9])\s*월"
                    r"(?:\s*\d{1,2}\s*일)?\s*(?:에\s*)?(?:제출|공시|접수)")

#: 🔴 **몇 분기인가** (재감사 `RF-1`). 바로 위 `_REPORT_YEAR` 이 `(?:\d\s*분기\s*)?` 로
#: 이 자리를 **건너뛰기만** 하고 값을 안 챙긴 것이 결함의 시작이다.
#:
#: 실측 — *"현대건설의 2026년 **3분기** 분기보고서"* 를 물으면 `doc_subtype=quarter` ·
#: `base_year=2026` 만 남는다. 현대건설의 2026년 quarter 문서는 `20260515`(1분기)
#: 하나뿐인데 그 하나가 조건을 만족시켜 **‘있는 보고서’ 로 통과**하고, 모델은 없는 3분기
#: 보고서를 있는 것처럼 서술하며 1분기 보고서를 근거로 달았다.
#:
#: 반기는 우연히 걸러졌다 — `doc_subtype=half` 라 종류 축 자체가 달랐다. 분기끼리는
#: 종류가 같아 **분기 번호 말고는 구별할 수단이 없다.**
_QUARTER = re.compile(r"([1-4])\s*분기")

#: 목차 제목에서 순번 표기를 떼어낸다 — 'III. ' · '4. ' · '7-1. ' · '12, '(오타).
_TOC_NUMBER = re.compile(r"^\s*(?:[IVXLC]+|\d+(?:-\d+)?)\s*[.,)]\s*")
_TOC_WORD = re.compile(r"[가-힣A-Za-z]{2,}")

#: 기수 표기. **사업연도 단위 표현**이라 종류가 안 적혀 있으면 사업보고서를 가리킨다.
#: 실측 — dev 정기공시에서 기수만 있고 종류 표기가 없는 문항 3건(Q-046·Q-049·Q-050)의
#: 골든 5건이 **전부 사업보고서**다. 종류가 적혀 있으면 그쪽이 이긴다 (Q-048 은 분기보고서).
#: 이 한 축이 Q-046 을 되살린다 — 종류 없이 목차만 걸면 골든이 3·5위인데 (근거 칸 4건에
#: 5위는 안 들어온다), `annual` 을 걸면 같은 질의에서 **1·2위**가 된다.
_GISU = re.compile(r"제\s*\d+\s*기")


def report_hints(question: str) -> dict:
    """질문에서 정기공시 축을 뽑는다 — 보고서 종류 · 사업연도 · 접수연월."""
    out: dict[str, str] = {}
    for name, pattern in REPORT_SUBTYPES:
        if pattern.search(question or ""):
            out["doc_subtype"] = name
            break
    if "doc_subtype" not in out and _GISU.search(question or ""):
        out["doc_subtype"] = "annual"
    year = _REPORT_YEAR.search(question or "")
    if year:
        out["base_year"] = year.group(1)
    filed = _FILED.search(question or "")
    if filed:
        out["filed"] = f"{filed.group(1)}{int(filed.group(2)):02d}"
    quarter = _QUARTER.search(question or "")
    if quarter:
        out["quarter"] = quarter.group(1)
    return out


#: 목차 매칭에서 지워야 할 **제출·서식 어휘**. 이게 남으면 내용이 아니라 형식이 경로를
#: 고른다. 실측으로 두 문항이 정확히 그렇게 틀렸다 (겹침 비율 = 맞은 글자수 / 제목 글자수):
#:   Q-046 '어느 보고서에서 확인했는지' 의 '확인' -> 【 전문가의 확인 】 0.400 > 매출및수주 0.333
#:   Q-052 '2023년 사업보고서' 의 '사업'          -> II. 사업의 내용   0.400 > 배당에관한사항 0.286
#: 두 오답 경로는 1,052 문서 **전부**에 있는 보일러플레이트라 변별력이 애초에 0 이다.
_PATH_NOISE = re.compile(r"사업\s*보고서|반기\s*보고서|분기\s*보고서|보고서|보고자"
                         r"|공시|제출|기재|확인|작성")

#: 같은 지표가 실리는 **짝 경로**. 실측 — dev 정기공시 골든 중 매출·손익 지표는 두 경로에
#: 갈려 있다: 'II. 사업의 내용 > 4. 매출 및 수주상황' 5건(Q-043·044·045·046·047·049·050),
#: 'III. 재무에 관한 사항 > 1. 요약재무정보' 3건(Q-048·049·051). Q-049 는 **한 질문 안에서**
#: 두 기업이 같은 '연결 매출액' 을 서로 다른 경로에 실었다 — 한 경로만 보면 한쪽이 통째로 빠진다.
SALES_PATH = "II. 사업의 내용 > 4. 매출 및 수주상황"
SUMMARY_PATH = "III. 재무에 관한 사항 > 1. 요약재무정보"
_SUMMARY_METRIC = re.compile(r"매출액|매출\s*및|영업이익|당기순이익|순이익"
                             r"|자산총계|부채총계|자본총계")

#: 정정 공시의 머리 섹션. 실측 1,052 문서 중 **157건**에만 있어 `tools.section_paths()` 의
#: '200문서 이상' 문턱에 걸려 공통 목차 모집단에서 빠진다. 그런데 dev 골든 3건
#: (Q-048·Q-052·Q-054)이 바로 이 경로다. 정정을 묻는 질의에서만 후보로 되살린다.
CORRECTION_PATH = "정 정 신 고 (보고)"
_ASK_CORRECTION = re.compile(r"정정|수정|최종\s*금액|반영\s*후|어떻게\s*바뀌")


def _twin_paths(question: str, pool) -> list[str]:
    """규칙으로 되살리는 짝 경로. 질문이 그 축을 묻지 않으면 하나도 넣지 않는다.

    정정 머리 섹션이 먼저다. 정정을 묻는 질의는 **그 섹션 자체가 답**이라 지표 짝보다
    급하다 — 뒤로 밀었더니 실측 Q-048 에서 지표 짝에 밀려 hop 0 후보에 못 들어왔다.
    """
    out: list[str] = []
    if _ASK_CORRECTION.search(question or ""):
        out.append(CORRECTION_PATH)          # 모집단 밖이라 pool 검사를 하지 않는다
    if _SUMMARY_METRIC.search(question or ""):
        out += [p for p in (SALES_PATH, SUMMARY_PATH) if p in pool]
    return out


def path_hints(question: str, paths, limit: int = 3) -> list[str]:
    """질문 어휘와 목차 제목의 겹침으로 후보 목차 경로를 고른다.

    목차 제목의 어절이 질문에 그대로 나오는지만 본다. 조사 한 글자는 떼고 본다 —
    '배당에 관한 사항' 의 '배당에' 는 질문의 '현금배당금' 과 안 맞지만 '배당' 은 맞는다.
    제출·서식 어휘(`_PATH_NOISE`)는 매칭 전에 지운다.

    겹침만으로는 **같은 지표가 여러 경로에 실리는 문제**가 풀리지 않는다 ('연결 매출액'
    질의는 '요약재무정보' 와 글자가 하나도 겹치지 않는다). 그래서 규칙으로 짝 경로를
    1순위 바로 뒤에 끼운다 — 1순위를 밀어내지 않으므로 지금 맞히는 문항이 깨지지 않는다.
    """
    text = _PATH_NOISE.sub(" ", question or "")
    scored: list[tuple[float, int, str]] = []
    for path in paths:
        title = _TOC_NUMBER.sub("", path.split(" > ")[-1])
        words = _TOC_WORD.findall(title)
        if not words:
            continue
        hit: list[str] = []
        for word in words:
            for candidate in (word, word[:-1] if len(word) > 2 else None):
                if candidate and candidate in text:
                    hit.append(candidate)
                    break
        if not hit:
            continue
        span = max(sum(len(w) for w in words), 1)
        scored.append((sum(len(w) for w in hit) / span, sum(len(w) for w in hit), path))
    scored.sort(key=lambda x: (-x[0], -x[1], len(x[2])))
    out = [p for _, _, p in scored[:limit]]
    for offset, twin in enumerate(_twin_paths(question, paths)):
        if twin not in out:
            out.insert(min(1 + offset, len(out)), twin)
    return out


#: 목차 필터가 걸린 hop 은 후보가 이미 수십 건으로 좁아져 있어 넓게 받아도 싸다.
SECTION_TOP_K = (8, 10, 8, 10)
#: 🔴 **`SECTION_TOP_K` 의 자리 이름이다 — 지우지 마라.** 코드 참조가 0건이라
#: '죽은 상수' 로 보이지만, 이 줄이 없으면 위 4-튜플이 설명 없는 `(8, 10, 8, 10)` 이
#: 되고 `section_plans` 의 hop 0~3 이 무엇이었는지 여기서 읽을 수 없게 된다.
#: 아래 `SectionPlan(...)` 의 전략 이름과 **자리 순서가 같아야 한다**.
SECTION_STRATEGIES = ("toc_narrow", "toc_corp", "doc_narrow", "corp_only")

#: hop 0 이 동시에 볼 목차 경로 수. 실측으로 2 면 충분하다 — 병목 2건이 요구하는 짝은
#: 정확히 2개다 (Q-049 매출및수주+요약재무정보 · Q-052 배당에관한사항+정정신고).
#: 3 으로 늘리면 기업 하나의 근거 칸(MAX_SECTION_EVIDENCE/기업수)이 경로당 1칸 밑으로
#: 쪼개져 골든이 셀 안에서 2위일 때 통째로 잘린다.
MAX_SECTION_PATHS = 2


@dataclass(frozen=True)
class SectionPlan:
    """정기공시 한 hop 의 검색 계획. 필터가 완전일치 축과 접두 축으로 갈린다."""

    hop: int
    strategy: str
    terms: tuple[str, ...] = ()
    filters: dict = field(default_factory=dict)
    prefix: dict = field(default_factory=dict)
    paths: tuple[str, ...] = ()
    top_k: int = 8

    @property
    def query(self) -> str:
        return " ".join(self.terms)


def plan_section_hops(question: str, hints: dict, paths: Sequence[str],
                      years: Sequence[int] = ()) -> list[SectionPlan]:
    """좁은 것부터 넓히는 정기공시 hop 계획 4단.

    hop 0 `toc_narrow` — 목차 후보 2개 + 보고서 종류 + 사업연도(축이 둘이면 셀로 분해)
    hop 1 `toc_corp`   — 목차 후보 전부, 기간·종류 해제 (수록 기수가 겹치지 않는 문항 대응)
    hop 2 `doc_narrow` — 목차 해제, 보고서 종류 + 사업연도 (목차 추정이 빗나간 경우)
    hop 3 `corp_only`  — 기업 필터만. 마지막 수단
    """
    terms = list(extract_terms(question))
    core = core_terms(terms) or terms
    doc_filters: dict[str, str] = {}
    if hints.get("doc_subtype"):
        doc_filters["doc_subtype"] = hints["doc_subtype"]
    base_year = hints.get("base_year") or (str(years[0]) if len(years) == 1 else None)
    if base_year:
        doc_filters["base_year"] = base_year

    # 접수 시점이 질문에 적혀 있으면 그게 가장 안전한 축이다. 사업연도는 추정이 섞인다 —
    # '2024년 3월 20일 제출한 제25기 사업보고서' 는 사업연도가 2023 인데 질문에는 2024
    # 밖에 없어서 base_year 로 걸면 정반대 문서만 남는다 (실측 Q-055).
    filed = {"rcept_dt": hints["filed"]} if hints.get("filed") else {}
    primary = ({}, filed) if filed else (doc_filters, {})

    # 사업연도 축은 **분해하지 않는다.** 실측이 그렇게 말한다 — Q-046 의 '제53기(2021년)'
    # 는 사업연도 2021 이 아니라 **제55기 사업보고서(사업연도 2023)** 에 실려 있고,
    # 코퍼스에 사업연도 2021 보고서는 아예 없다. 연도로 쪼개면 한쪽 셀이 통째로 빈다.
    # 같은 문항이 `doc_subtype=annual` + 목차 경로만으로 골든 둘을 1·2위로 회수한다.
    return [
        SectionPlan(0, "toc_narrow", tuple(terms), dict(primary[0]), dict(primary[1]),
                    tuple(paths[:MAX_SECTION_PATHS]), SECTION_TOP_K[0]),
        SectionPlan(1, "toc_corp", tuple(terms), {}, {}, tuple(paths[:3]),
                    SECTION_TOP_K[1]),
        SectionPlan(2, "doc_narrow", tuple(core), dict(doc_filters), {}, (),
                    SECTION_TOP_K[2]),
        SectionPlan(3, "corp_only", tuple(core), {}, {}, (), SECTION_TOP_K[3]),
    ]
