"""계약 상대(counterparty) 문자열의 정규화와 분류.

노드로 승격할 수 있는 것과 없는 것을 가른다. 비실명(`"글로벌 대형기업"`)을 노드로
만들면 서로 다른 실체가 한 노드로 뭉쳐 그래프가 오염된다.

정규화·비실명 판정 규칙은 여기서 새로 만든 것이 아니라 원래 `scripts/validate_evalset.py`
(`normalize_counterparty` · `ANON_COUNTERPARTY`)에 있던 것을 그대로 옮긴 것이다.
03-graph-schema C-0b 가 실측한 표기 변이 5유형(국호 접두 · 법인격 접미/접두 · 공백 ·
괄호 약칭)을 겨냥해 만들어졌고, `tests/test_evalset.py::COUNTERPARTY_VARIANTS` 가 그
규칙을 고정한다. `scripts/validate_evalset.py` 는 이제 이 모듈을 얇게 감싼 별칭만 남긴다
— 규칙이 두 곳으로 갈리면 문항 검증(C12)과 런타임 분류가 서로 어긋난다.
"""
from __future__ import annotations

import re
from functools import lru_cache

from argus.corpus.manifest import load_universe

#: 표기 변이 접기 — scripts/validate_evalset.py 의 _CP_PAREN/_CP_SUFFIX/_CP_PREFIX 와
#: 동일 규칙(실측 정본). 여기가 이제 단일 원천이다.
_PAREN = re.compile(r"\([^)]*\)")
#: 한글 법인격 표지. **앵커를 걸지 않는다** — 한국 표기는 접두("주식회사 유토개발2차")·
#: 접미("삼성전자㈜") 양쪽에 다 붙고, 조사 없이 고유명사에 바로 붙는다("현대제철주식회사").
#: `\(주\)`·`\(유\)` 는 위 `_PAREN` 이 먼저 지우므로 사실상 죽은 항목이지만, `_PAREN` 을
#: 건드리는 변경에 대한 방어로 남긴다.
_SUFFIX_KO = re.compile(r"(주식회사|\(주\)|㈜|\(유\))")
#: 로마자 법인격 표지. **글자 경계를 요구한다**(리뷰 라운드 3, B/I-12).
#: 종전 `Corp\.?|Inc\.?|Limited` 는 앵커가 없어 단어 **내부**에서도 매치했다 —
#: 실측 훼손: `Corporation`→`oration` · `Lincoln`→`loln`(L-inc-oln) ·
#: `Incheon`→`heon`(Inc-heon) · `Princeton`→`preton` · `Sinclair`→`slair` ·
#: `UNLIMITED`→`UN`(UN-limited) · `Corning Incorporated`→`corningorporated`.
#: 오병합이 가장 나쁜 결함이다 — 서로 다른 두 회사가 한 노드로 접히면 `via` 가 **없는
#: 밸류체인 연결을 근거로 인용**한다. `Incheon`/`인천` 은 한국 공시에 충분히 나올 이름이다.
#: 앵커는 `(?<![A-Za-z]) … (?![A-Za-z])` 로 잡았다 — 리뷰어 처방(`(?:[\s,·]|^)…\s*$`,
#: 문자열 끝 앵커)은 채택하지 않았다. 실측으로 기각했다: 그 정규식은 한글 표지의 접미
#: 매치를 깨서 462종 중 **80종**의 키가 바뀌고 canonical 그룹이 407→418 로 분열하며
#: `삼성전자㈜`·`두산에너빌리티㈜`·`현대제철주식회사` 3건이 `universe`→`other` 로
#: 오분류된다(한글 앞에는 `[\s,·]` 가 없어 앵커가 안 맞는다).
_SUFFIX_EN = re.compile(
    r"(?<![A-Za-z])(?:Co\.,?\s*Ltd\.?|Corp(?:oration)?\.?|Inc(?:orporated)?\.?"
    r"|Limited|LLC|S\.A\.)(?![A-Za-z])", re.I)
_PREFIX = re.compile(r"^대한민국\s*")

#: 비실명 계약상대 — 어느 기업인지 원문에 없어 노드로 못 올린다.
#: scripts/validate_evalset.py 의 ANON_COUNTERPARTY 와 동일 규칙(실측 정본)에서 출발했으나,
#: 리뷰 라운드 2(Task 2, 2026-08-10)에서 exchange_records.jsonl 462개 유일 표기 **전량**을
#: 대조해 오탐 1건·미탐 1건을 실측으로 확인해 고쳤다.
#:   · `지역\s*선사` 추가 — "미주 지역 선사"(미탐, 1건)를 놓쳤다. `지역\s*선주`·`소재\s*선사`
#:     는 있었는데 이 조합만 빠져 있었다. 전량 대조로 이 조합 외 다른 미탐은 없음을 확인했다.
#:   · `글로벌` 을 공백/문자열 경계로 감쌌다 — "(주)청주글로벌"(오탐, 1건, 실명 회사)이
#:     서브스트링만으로 걸렸다. "글로벌 대형기업" 처럼 **단어 앞머리 수식어**일 때만 비실명
#:     신호이지 "청주글로벌" 처럼 고유명사 끝에 붙으면 회사명 일부다. 전량 대조로 이 정규식이
#:     매치하는 4건(글로벌 X 3건 + 청주글로벌 1건) 중 이 1건만 오탐임을 확인했다.
ANON_COUNTERPARTY = re.compile(
    r"(지역\s*선주|지역\s*선사|소재\s*선사|(?:^|\s)글로벌(?:\s|$)|익명|비공개|"
    r"대형기업|중견기업|중소기업|업체$|제약사$|고객사|완성차)")

#: 해외 법인 힌트 — 로마자 3자 이상 연속. 국문 표기가 아예 없는 이름을 잡는다.
_OVERSEAS = re.compile(r"[A-Za-z]{3,}")

#: 국내 공공기관. universe.csv(상장사 유니버스) 밖이라 `classify` 의 `universe` 판정으로는
#: 안 잡히는 것들을 손으로 추가한다. artifacts/exchange_records.jsonl 의 counterparty 실측
#: (Task 2, 2026-08-10)으로 확인해 6곳을 보강했다 — 국방과학연구소(12건) · 한국토지주택공사
#: (8건) · 대한무역투자진흥공사(2건) · 국가철도공단(1건) · 한국항공우주연구원(1건) ·
#: 국방기술진흥연구소(1건). `국방부`·`산업통상자원부`·`한국도로공사`는 같은 실측에서 0건이라
#: 죽은 항목이지만, 향후 데이터에서 나올 수 있는 정당한 공공기관명이라 남겨 둔다.
#: `산림청`은 리뷰 라운드 2 에서 추가 — 아래 `classify` 의 접두일치가 있어야 의미가 있다
#: ("산림청산림항공본부" 1건은 완전일치로는 못 잡는다).
PUBLIC_HINTS = frozenset({
    "방위사업청", "한국수력원자력", "한국중부발전", "한국남부발전", "한국동서발전",
    "한국서부발전", "한국남동발전", "한국전력공사", "한국가스공사", "조달청",
    "국방부", "산업통상자원부", "한국도로공사", "한국철도공사",
    "국방과학연구소", "한국토지주택공사", "대한무역투자진흥공사", "국가철도공단",
    "한국항공우주연구원", "국방기술진흥연구소", "산림청",
})


def canonical(s: str) -> str:
    """표기 변이를 하나로 접는다. 비교 전용이며 표시용이 아니다.

    접기가 문자열을 **전부** 먹어치우면(예: `"(삭제)"` · `"㈜"` · `"주식회사"`) 접기 전
    형태로 되돌린다. 빈 문자열을 키로 쓰면 서로 다른 원문이 한 키로 접혀 오병합이 되고,
    `classify` 의 접두일치(`key.startswith(...)`)도 빈 키에서 의미를 잃는다. 실측:
    exchange_records.jsonl 462종 중 `"(삭제)"` 1종이 여기 걸린다(그래프 빌더의
    `BOILERPLATE_NOTE` 가 먼저 걸러내므로 노드 수에는 영향이 없다).

    **2자 이하를 걸러내지는 않는다** — `"기아(주)"`→`"기아"` · `"주식회사 SR"`→`"sr"` 는
    둘 다 실명 회사다. 길이로 자르면 정당한 노드를 잃는다.
    """
    folded = _PAREN.sub("", s.strip())
    folded = _SUFFIX_KO.sub("", folded)
    folded = _SUFFIX_EN.sub("", folded)
    folded = _PREFIX.sub("", folded)
    out = re.sub(r"[\s·,\.]+", "", folded)
    if not out:
        out = re.sub(r"\s+", "", s.strip())
    return out.lower()


@lru_cache(maxsize=1)
def _universe_canonical() -> frozenset[str]:
    """상장사 유니버스 이름의 canonical 집합. `classify` 를 문항마다 불러도 매번
    universe.csv 전체를 다시 접지 않도록 한다. `load_universe()` 자체도 이미
    `lru_cache` 라 파일 재읽기는 없으니 이건 그 위에 얹는 방어적 캐시일 뿐이다.
    """
    return frozenset(canonical(name) for name in load_universe())


def classify(s: str) -> str:
    """`universe` | `public` | `overseas` | `vague` | `other`."""
    if ANON_COUNTERPARTY.search(s):
        return "vague"
    key = canonical(s)
    # 완전일치가 아니라 접두일치다 — "산림청산림항공본부"처럼 기관명 뒤에 하위조직명이
    # 붙는 실측 표기(리뷰 라운드 2)를 잡기 위해서다. `국방부`처럼 짧은 힌트가 다른 나라
    # 정부기관("루마니아국방부" 등 7건)을 오탐할 위험이 있어, exchange_records.jsonl
    # 전체(378 canonical 그룹) 대조로 그런 부작용이 없는지 확인했다 — 외국 기관명은 전부
    # 접두가 아니라 "국가명+국방부" 형태라 접두일치를 피해 간다.
    if any(key.startswith(canonical(h)) for h in PUBLIC_HINTS):
        return "public"
    if key in _universe_canonical():
        return "universe"
    if _OVERSEAS.search(s):
        return "overseas"
    return "other"
