"""파이프라인 진입점. 고정 횟수 단계라 질문당 비용 상한이 계산된다."""
from __future__ import annotations

import re
from collections.abc import Sequence

from argus import ablation
from argus.agent import tools
from argus.agent.budget import Budget, BudgetExceeded
from argus.agent.critic import (judge, quick_check, verify_calculation,
                                verify_completeness)
from argus.agent.prompts import build_answer_messages
from argus.agent.query import (SPLIT_TOP_K, STRATEGIES, path_hints, plan_hops,
                               plan_section_hops, report_hints, time_points)
from argus.agent import router as router_module
from argus.agent.router import route
from argus.agent.safety import mask_pii, sanitize_question
from argus.clova import chat
from argus.corpus.manifest import filter_docs
from argus.retrieve import bm25 as bm25_module

UNANSWERABLE = (
    "공시에서 확인되지 않습니다. "
    "제공된 70개사 · 2023.01~2026.03 범위 안의 기업과 기간으로 질문해 주시면 확인해 드리겠습니다."
)

#: 기업도 연도도 범위 안인데 **그 보고서만** 없는 경우 (실측 Q-040·Q-070).
#: 한계의 종류가 다르면 고지도 달라야 한다 — '범위 밖' 이라고 말하면 사용자가
#: 다시 물을 방법을 못 찾는다. 어느 보고서가 실재하는지 되묻는 것이 정확한 응대다.
NO_SUCH_REPORT = (
    "공시에서 확인되지 않습니다. "
    "질문이 지목한 보고서가 제공 코퍼스(70개사 · 2023.01~2026.03 접수분)에 없습니다. "
    "어느 사업연도·보고서 종류를 확인해 드릴지 알려주시면 실재하는 공시로 답해 드리겠습니다."
)

MAX_EVIDENCE = 10                       # 근거 절단 상한 (PCST 압축 대체)
_YM = re.compile(r"(20\d{2})\s*년\s*(1[0-2]|0?[1-9])\s*월")

# --- F-3 충분성 판정 (LLM 0회) -----------------------------------------------
# 판정에 LLM 을 쓰면 hop 마다 호출이 붙어 6회 상한을 금방 태운다.
# 후보 수 · BM25 점수 · 골든 필드 존재 · **정정 체인 완결성** 넷으로 가른다.
#
# 앞의 셋은 전부 '개수' 축이라 **덜 온 것을 다 왔다고 읽는다**. dev 42문항 중 18문항이
# 골든 2건 이상인데, 종전 판정은 첫 골든 1건만 오면 `sufficient` 를 냈다 —
# Phase H RETRIEVAL 실패 11건이 정확히 여기서 났다. 넷째 축이 '이 질문이 요구하는
# 문서 집합이 다 왔는가' 를 본다.

#: 라우트별 최소 근거 수. ReAct 단독은 CoT 보다 낮다 (HotpotQA 27.4 vs 29.4) —
#: 검색을 더 돌린다고 이기는 게 아니므로 "답이 나올 최소치"만 채우고 생성으로 넘긴다.
MIN_EVIDENCE = {"closed_lookup": 1, "multi_compare": 2, "complex_reason": 2}

#: BM25 최고 점수 임계. 실측 — 코퍼스와 무관한 질의('우주선 발사 로켓 엔진' 8.7 ·
#: '블록체인 가상자산 채굴' 6.6) 의 최고점과 평가셋 문항의 최고점 최솟값(10.8) 사이.
#: 이 밑이면 우연히 걸린 bigram 이라고 본다.
MIN_TOP_SCORE = 10.0

#: 넓혀도 새 후보가 두 번 연속 안 나오면 인덱스에 없는 것이다. 남은 hop 을 태우지 않는다.
MAX_STALLED_HOPS = 2

#: query_events 폴백에 붙이는 hop 번호 (검색 전략 4단 다음).
EVENTS_HOP = len(STRATEGIES)

#: 정정 체인 hop 번호. 재구성 4단·이벤트 폴백 다음이다.
CHAIN_HOP = EVENTS_HOP + 1

#: 발주처 hop 번호. `parse_hops` 가 **독립 레코드**로 되읽어야 Phase H 가 이 hop 에
#: 실패를 귀속할 수 있다 — hop 0 으로 찍으면 같은 hop 의 `strat=narrow` 가 덮어쓴다.
COUNTERPARTY_HOP = CHAIN_HOP + 1

# --- 정정·후속 공시 체인 hop --------------------------------------------------
# Phase H 1위 병목: 놓친 골든 14건을 실제 기간으로 재질의하니 **전부 top-3 안에 있었다**.
# 검색이 실패한 게 아니라 hop 0 에서 `sufficient` 를 내고 멈춘 것이다. 그중 7건은
# `related` 로 이미 연결돼 있는데 `get_history` 를 한 번도 부르지 않았다.
#
# 그래서 충분성 판정에 축을 하나 더 넣는다 — **집합 완결성**.
#   종전: 후보 수 · 점수 · 필드 · 기업 커버리지  (전부 '개수' 축이다)
#   추가: 근거에 담긴 문서가 **대체된 문서(is_latest=False)** 라면, 그 체인의 나머지가
#         근거에 없는 한 근거 집합은 완결되지 않았다.
#
# 🔴 과잉 탐색이 진짜 위험이다. 모든 문항에서 체인을 끌어오면 지금 맞히는 문항이 깨진다.
# 제동 셋을 실측으로 정했다 (dev 42문항, LLM 0회):
#
#   ① 씨앗은 **상위 2위 후보**까지만          — 3위까지 넓히면 Q-033(현재 정답)이
#                                              무관한 체인 11건을 끌고 온다
#   ② 씨앗은 **대체본(is_latest=False)만**    — 최신본이 씨앗이 되면 Q-008·Q-031·
#                                              Q-033·Q-034(전부 현재 정답)에 근거가 붙는다
#   ③ 체인에서 끌어올 문서는 **6건까지**       — 한화오션 잠수함 체인이 7건이라 상한이 없으면
#                                              근거 칸을 혼자 다 먹는다
#
# 실측 결과 (dev 42문항, LLM 0회): 골든 근거 적중 28 -> 39, `get_history` 호출 10회.

#: 체인 씨앗으로 볼 상위 후보 수. 제동 ①.
CHAIN_SEED_WINDOW = 2

#: 한 문항에서 체인으로 끌어올 문서 상한. 제동 ③.
MAX_CHAIN_DOCS = 6

# --- 다지점 질의 분해 (Phase H 3위 병목 MULTI_POINT_QUERY) ----------------------
# 체인 수정 뒤에도 적중이 안 변한 4문항의 기전은 하나다: 같은 질문이 **다른 시점 ·
# 다른 기업**을 한 번 더 물어야 하는데 hop 사다리가 질의를 한 번만 만든다.
# 사다리는 넓히기만 하고, 넓히기로는 "A사 2024년 것" 과 "B사 2025년 것" 을 동시에
# 얻을 수 없다 — 넓히면 둘 다 흐려진다. 그래서 **분해**한다.
#
# 🔴 여기서도 과잉 탐색이 진짜 위험이다. 제동 셋을 실측으로 정했다:
#
#   ① **시점 축이 2개 이상일 때만** 분해한다  — dev 42문항 중 7건만 해당(Q-012·014·
#      015·020·046·050·052). 단일 시점 33문항은 호출 인자까지 종전과 같다.
#   ② **셀마다 할당량**을 둔다 (`_cell_quota`) — 셀마다 top-k 를 그대로 담으면 근거가
#      셀 수만큼 부풀고 토큰이 직결로 늘어난다. 근거 칸을 셀 수로 나눠 쓴다.
#   ③ **hop 0 만** 쪼갠다 — 넓히는 hop 까지 곱하면 스텝 상한 8을 넘긴다. 실측 최대 셀은
#      4개(Q-014 = 2기업 × 2시점)라 hop 0 이 4스텝, 체인 1스텝으로 5스텝에 든다.


def _cell_quota(n_cells: int, n_corps: int) -> int:
    """분해 셀 하나가 가져갈 근거 상한. 분해가 없으면 종전대로 상한을 다 준다.

    두 가지로 조인다 — 근거 칸(`MAX_EVIDENCE`)의 셀별 공평 몫, 그리고 `SPLIT_TOP_K`.
    뒤엣것이 필요한 이유는 셀이 적을 때 공평 몫이 헐거워지기 때문이다 (2셀 × 1기업이면
    5칸). 실측상 골든은 **셀 안에서 1위**라 3칸이면 남고, 그 위로는 근거만 부푼다.
    """
    if n_cells < 2:
        return MAX_EVIDENCE
    return max(1, min(SPLIT_TOP_K, MAX_EVIDENCE // (n_cells * max(n_corps, 1))))


#: 제공 코퍼스 기간 2023.01~2026.03 (CONVENTION §2 G7).
#: 이 밖의 연도를 물으면 기간 필터를 푸는 순간 **엉뚱한 연도 공시가 근거로 둔갑한다**.
#: 넓히는 것이 늘 이득은 아니다 — 범위 밖은 못 찾은 게 아니라 없는 것이다.
#: 라우터가 먼저 단락시키지만(스텝 0), `_gather_evidence` 를 직접 부르는 경로도 있어
#: 검색 단계 가드는 남겨 둔다.
CORPUS_YEARS = router_module.CORPUS_YEARS

#: (필드명, 질문이 그 필드를 요구하는가, 근거에 그 필드가 있는가) — **문서군별**로 다르다.
#:
#: 종전에는 exchange 마크다운 스키마 한 벌뿐이었다. 그래서 정기공시 질의는 근거를 제대로
#: 찾아 놓고도 `- 계약금액:` 라벨이 없다는 이유로 '필드 없음' 판정을 받아 hop 을 상한까지
#: 태웠다. 문서군마다 근거의 생김새가 다르니 판정도 갈라야 한다.
#:
#: * `exchange` — `corpus/markdown.py` 가 `- 계약금액: …` 형태로 라벨을 찍는다. 라벨 존재만 본다.
#:                실측 1,469건: 계약금액 0.767 · 계약상대 0.766 · 매출액 대비 0.723 · 기간 0.650.
#: * `major`    — 서식이 **28종**이고 서식마다 금액 칸의 이름이 다르다. 실측 598건에서
#:                `계약금액:` 은 47건(0.079)뿐이고 조달금액 52 · 처분예정금액 156 ·
#:                취득예정금액 90 · 권면(전자등록)총액 102 · 시설자금 30 으로 흩어져 있다.
#:                한 벌의 라벨로 뭉개면 안 된다 — `금액:` 하나만 보면 0.674, 실측 라벨을
#:                다 넣으면 0.846 이다. 나머지 0.154 는 합병·분할·교환이전처럼 **원본에
#:                단일 금액 칸이 없는** 서식이라 여기서 더 올릴 것이 없다.
#:                `대비:` 는 14건(0.023) 뿐이라 **비율은 요구하지 않는다** — 요구하면
#:                97.7% 의 질의가 영원히 불충족이 되어 hop 만 태운다.
#: * `holding`  — 종전에 '요구 필드 없음' 으로 비워 뒀는데, 그 관측은 표 머리행을 못 읽던
#:                파서 기준이었다. 직접 다시 센 결과 **라벨은 처음부터 다 있었다** (1,083건):
#:                `보유비율:` 1,083(1.000) · `보유주식등의 수:` 1,083(1.000) ·
#:                `의결권있는 발행주식 총수:` 1,081(0.998) · `보고사유:` 1,083(1.000).
#:                반면 `주요계약체결 비율:` 507(0.468) · `보유목적:` 476(0.440) 은
#:                절반이 없으므로 요구하지 않는다. 1.000 인 것만 요구 필드로 쓴다.
#: * `periodic` — 근거가 목차 섹션 본문(표 마크다운)이라 `- 라벨:` 구조 자체가 없다.
#:                판정은 값의 생김새로 한다 — 천 단위 구분 숫자 · `%`. 실측: 정기공시 골든
#:                22개 섹션 중 20개에 천 단위 구분 숫자가 있고, 없는 2개는 '기재 생략' 스텁이다.
_LABEL_AMOUNT = re.compile(r"금액\s*:")
_LABEL_RATIO = re.compile(r"대비\s*:")
_LABEL_PERIOD = re.compile(r"기간\s*:")

#: major 28종 서식의 금액 칸을 **이름을 가리지 않고** 잡는다. 실측 커버리지 0.846.
_LABEL_MAJOR_AMOUNT = re.compile(r"(?:금액|총액|자금|가격)\s*:")
#: holding 요약정보. 값이 `직전 … → 이번 …` 형태라 라벨 존재만으로 두 시점이 다 온다.
_LABEL_HOLD_RATIO = re.compile(r"보유비율\s*:")
_LABEL_HOLD_SHARES = re.compile(r"보유주식등의\s*수\s*:")

_ASK_AMOUNT = re.compile(r"금액|얼마")
_ASK_RATIO = re.compile(r"매출액\s*대비|비중|비율|퍼센트|몇\s*%")
_ASK_PERIOD = re.compile(r"계약\s*기간|기간은|몇\s*년|개월")
#: 지분공시 질의의 두 축. '지분율'·'몇 %' 는 보유비율을, '주식수'·'몇 주' 는 보유주식수를 묻는다.
_ASK_HOLD_RATIO = re.compile(r"보유\s*비율|지분율|지분\s*비율|비중|비율|퍼센트|몇\s*%|%p")
_ASK_HOLD_SHARES = re.compile(r"보유\s*주식|주식\s*수|주식등의\s*수|주식을|몇\s*주")

_EXCHANGE_HINTS = (
    ("금액", _ASK_AMOUNT, _LABEL_AMOUNT),
    ("비율", _ASK_RATIO, _LABEL_RATIO),
    ("기간", _ASK_PERIOD, _LABEL_PERIOD),
    ("계약상대", re.compile(r"계약\s*상대|상대방|거래처"), re.compile(r"계약상대\s*:")),
)

FIELD_HINTS = {
    "exchange": _EXCHANGE_HINTS,
    "major": (
        ("금액", _ASK_AMOUNT, _LABEL_MAJOR_AMOUNT),
        ("기간", _ASK_PERIOD, _LABEL_PERIOD),
    ),
    "holding": (
        ("보유비율", _ASK_HOLD_RATIO, _LABEL_HOLD_RATIO),
        ("보유주식수", _ASK_HOLD_SHARES, _LABEL_HOLD_SHARES),
    ),
    "periodic": (
        ("수치", re.compile(r"금액|얼마|매출액|영업이익|순이익|자산총계|자본금|배당금|주식수"),
         re.compile(r"\d{1,3}(?:,\d{3})+")),
        ("비율", re.compile(r"비중|비율|퍼센트|몇\s*%"), re.compile(r"%")),
    ),
}

#: 라우터가 문서군을 못 가른 경우의 기본값. 종전 동작 그대로다.
_FIELD_HINTS = _EXCHANGE_HINTS


def _period_args(r, question: str) -> dict:
    """검색 기간 필터. 좁은 것부터 — 연월 > 연도 > 없음.

    Route 는 연도만 들고 있지만 랭킹을 가르는 것은 연월이다.
    Task 0 실측: corp 필터 + 연도만으로는 골든이 top-5 밖이고, 연월 prefix 를 걸면 1위다.
    연도가 둘 이상이면 접두 하나로 표현할 수 없으므로 기간 필터를 걸지 않는다.
    """
    match = _YM.search(question or "")
    if match:
        return {"ym": f"{match.group(1)}{int(match.group(2)):02d}"}
    if len(r.years) == 1:
        return {"year": r.years[0]}
    return {}


def _required_fields(question: str, group: str = "exchange") -> list[tuple[str, re.Pattern]]:
    """질문이 요구하는 골든 필드. 이게 근거에 없으면 답을 만들 수 없다."""
    hints = FIELD_HINTS.get(group, _FIELD_HINTS)
    return [(name, has) for name, asks, has in hints if asks.search(question or "")]


def _out_of_corpus_period(r) -> bool:
    """질문이 지목한 연도가 전부 코퍼스 밖인가. 그러면 재검색은 오답 제조다."""
    return bool(r.years) and all(y not in CORPUS_YEARS for y in r.years)


def _needed_evidence(r) -> int:
    """최소 근거 수. 비교 질의는 기업 수만큼, CoT 경로는 최소 2건."""
    need = max(MIN_EVIDENCE.get(r.kind, 1), len(r.companies))
    return max(need, 2) if r.use_cot else need


def _sufficiency(r, picked: list[dict], required,
                 chain_gap: Sequence[str] = ()) -> tuple[bool, list[str]]:
    """근거가 충분한가. LLM 0회 — 후보 수 · 점수 · 필드 존재 · **집합 완결성**을 본다.

    `chain_gap` 은 근거 안의 문서가 가리키는데 아직 근거에 없는 정정·후속 공시다.
    개수 규칙이 아무리 만족돼도 이게 남아 있으면 '이 질문이 요구하는 문서 집합' 은
    아직 다 오지 않았다 — Phase H 실패 7건이 전부 여기서 났다.
    """
    if not picked:
        return False, ["no_candidate"]

    reasons: list[str] = []
    need = _needed_evidence(r)
    if len(picked) < need:
        reasons.append(f"few:{len(picked)}<{need}")

    scores = [p["score"] for p in picked if isinstance(p["score"], (int, float))]
    if scores and max(scores) < MIN_TOP_SCORE:
        reasons.append(f"low_score:{max(scores):.1f}<{MIN_TOP_SCORE:.1f}")

    text = "\n".join(p["markdown"] for p in picked)
    reasons += [f"missing_field:{name}" for name, has in required if not has.search(text)]

    if len(r.companies) >= 2:                 # 비교 질의는 한쪽만 찾으면 답이 안 된다
        covered = {p["corp"] for p in picked}
        reasons += [f"missing_corp:{c}" for c in r.companies[:3] if c not in covered]

    reasons += [f"chain_open:{rcept_no}" for rcept_no in list(chain_gap)[:3]]

    return not reasons, reasons


# --- A7 ② 질문이 못박은 시점 --------------------------------------------------
# 체인 hop 이 붙은 뒤 파이프라인이 **항상 최신값으로 수렴**한다 (실측 Q-022). 프롬프트
# 규칙만으로는 안 뒤집혔다 — 근거 블록이 원공시를 `대체됨` 한 낱말로만 표시하니 모델이
# 그걸 '쓰면 안 되는 값' 으로 읽는다. 그래서 **질문의 연월을 블록에 되비춘다.**
#
# 🔴 반대 방향이 진짜 위험이다. Q-005("2023년 6월에 공시한 … 정정공시에서 얼마로
# 바뀌었는가")·Q-006·Q-061 은 **같은 문장에 날짜가 있는데 정답이 최신값**이다. 날짜만
# 보고 표식을 달면 지금 맞히는 문항이 뒤집힌다. 그래서 표식은 질문이 정정 이후 값이나
# 이력 서술을 요구하지 **않을 때만** 붙인다 — 그 경우 시점은 값의 기준점이 맞다.
#
# 실측 (dev 78 + 회귀 10 = 88문항, LLM 0회): 표식이 붙는 문항은 Q-022 하나다.

#: 질문이 체인의 **결과·경과**를 요구하는가. 걸리면 시점 표식을 붙이지 않는다.
_ASK_CHAIN_OUTCOME = re.compile(r"정정|해지|철회|변경계약|최종|최신|반영\s*후|"
                                r"바뀌|변해|변화|추이|이력|시간순|차례")


def _as_of_ym(question: str) -> str:
    """질문이 못박은 연월. 정정 이후 값·이력을 요구하는 질의에는 빈 문자열."""
    if _ASK_CHAIN_OUTCOME.search(question or ""):
        return ""
    match = _YM.search(question or "")
    return f"{match.group(1)}{int(match.group(2)):02d}" if match else ""


def _as_of_note(entry: dict, as_of: str) -> str:
    """이 체인 항목이 질문이 지목한 시점의 공시인가. 아니면 빈 문자열."""
    if not as_of or str(entry.get("rcept_dt", "")).replace("-", "")[:6] != as_of:
        return ""
    return f" · 질문이 지목한 {as_of[:4]}-{as_of[4:]} 시점의 공시"


def _chain_note(entry: dict, index: int, total: int, as_of: str = "") -> str:
    """근거 블록 안에 체인 위치를 박아 둔다.

    별도 요약 블록으로 빼지 않는다 — 요약 블록에 접수번호를 나열하면 원문을 안 실어도
    '골든 근거가 실렸다' 로 세어져 자기 성적표가 부풀어 오른다.

    1번 자리에 **`원공시`** 를 덧붙인다. `대체됨` 한 낱말만 붙어 있으면 모델은 그 블록을
    '쓰면 안 되는 값' 으로 읽는다 — 실측 Q-022 가 그랬다. '최초 공시 당시' 를 묻는 질의
    (Q-069)와 시점을 못박은 질의(Q-022)에서는 그 블록이 **정답의 출처**다.

    **`대체됨`·`최신본` 은 그대로 둔다.** `critic._STALE_NOTE` / `_LATEST_NOTE` 가 이 두
    낱말로 블록의 체인 위치를 읽는다 — 갈아치우면 `stale_value` 전제가 통째로 무너진다.
    """
    if entry.get("kind") == "followup":
        # 후속 공시는 정정이 아니다. 체인 번호를 붙이면 '정정 n번째' 로 읽힌다.
        return "- 정정체인: 후속 공시 (※관련공시로 이어짐)"
    mark = _as_of_note(entry, as_of)
    if entry.get("is_latest"):
        return f"- 정정체인: {index}/{total} (최신본{mark})"
    role = "원공시 · 대체됨" if index == 1 else "대체됨"
    return f"- 정정체인: {index}/{total} ({role}{mark})"


def _annotate(markdown: str, note: str) -> str:
    """`## ` 헤더 바로 밑에 한 줄 끼운다. 블록 경계(`^## `)를 건드리지 않는다."""
    if not markdown:
        return markdown
    head, sep, rest = markdown.partition("\n")
    if not sep:
        return f"{markdown}\n{note}"
    return f"{head}\n{note}\n{rest}"


def _chain_seeds(picked: list[dict]) -> list[dict]:
    """체인을 밟을 씨앗. 제동 ①② — 상위 후보 안의 **대체본**만.

    `tools.chain_probe` 는 도구 호출이 아니라 레코드 조회라 스텝을 쓰지 않는다.
    이게 없으면 링크가 없는 문서에도 `get_history` 를 걸어 스텝만 태운다.
    """
    seeds = []
    for item in picked[:CHAIN_SEED_WINDOW]:
        probe = tools.chain_probe(item.get("doc_id"))
        if not probe or probe["is_latest"] or not probe["links"]:
            continue
        seeds.append({**probe, "corp": item.get("corp")})
    return seeds


def _expand_chain(picked: list[dict], seen: set, budget: Budget,
                  trace: list[str], take, as_of: str = "") -> tuple[list[str], set]:
    """정정·후속 공시 체인을 근거에 넣는다. LLM 0회.

    돌려주는 값은 (닫지 못한 링크, 체인에 속한 doc_id). 앞엣것은 충분성 판정이
    '집합 미완결' 로 읽고, 뒤엣것은 근거 절단에서 체인을 먼저 살리는 데 쓴다.
    """
    gap: list[str] = []
    chain_ids: set = set()
    added = 0
    for seed in _chain_seeds(picked):
        missing = [r for r in seed["links"] if not any(r in d for d in seen)]
        if not missing:
            # 체인이 이미 다 와 있다. 스텝을 쓰지 않되 절단에서는 함께 살린다.
            known = (seed["rcept_no"], *seed["links"])
            chain_ids.update(d for d in seen if any(x in d for x in known))
            continue
        try:
            budget.spend_step()
        except BudgetExceeded as exc:
            trace.append(f"hop={CHAIN_HOP} stop=step_budget ({exc})")
            gap += missing
            break
        out = tools.execute("get_history", {"rcept_no": seed["rcept_no"]})
        if "error" in out:
            trace.append(f'hop={CHAIN_HOP} strat=chain tool=get_history '
                         f'corp="{seed["corp"] or "-"}" skip=tool_error '
                         f'건너뜀({out["error"]})')
            gap += missing
            continue
        entries = list(out.get("chain") or []) + list(out.get("followups") or [])
        total = len(out.get("chain") or [])
        taken: list[str] = []
        for position, entry in enumerate(entries, start=1):
            note = _chain_note(entry, min(position, total), total, as_of)
            if entry["rcept_no"] == seed["rcept_no"]:
                for item in picked:                    # 씨앗은 이미 근거에 있다
                    if entry["doc_id"] == item["doc_id"]:
                        item["markdown"] = _annotate(item["markdown"], note)
                        chain_ids.add(item["doc_id"])
                continue
            if added >= MAX_CHAIN_DOCS:                # 제동 ③
                break
            if take(entry["doc_id"], _annotate(entry["markdown"], note),
                    None, seed["corp"]):
                added += 1
                taken.append(entry["doc_id"])
                chain_ids.add(entry["doc_id"])
        gap += [r for r in missing if not any(r in d for d in seen)]
        trace.append(f'hop={CHAIN_HOP} strat=chain tool=get_history '
                     f'corp="{seed["corp"] or "-"}" rcept={seed["rcept_no"]} '
                     f'latest={out.get("latest", "-")} chain_length={out.get("chain_length", 0)} '
                     f'n={len(taken)} top_score=- ids={",".join(taken) or "-"}')
    return gap, chain_ids


# --- 발주처 2-hop (밸류체인 fan-in / fan-out) ----------------------------------
# 밸류체인 문항이 요구하는 것은 순위가 아니라 **집합의 경계**다 — "이 발주처와 계약한
# 상장사는 누구누구인가". 자유텍스트 BM25 는 이걸 못 준다. 실측(밸류체인 12문항, LLM 0회):
# 골든 근거 25/46 인데 fan-in/fan-out 3문항은 Q-110 2/6 · Q-114 0/5 · Q-117 2/7 이다.
# Q-114 가 0인 이유가 기전을 그대로 보여 준다 — '삼성전자' 가 universe 기업이라
# `corp_name=삼성전자` 필터가 걸리는데, 삼성전자는 그 계약을 **공시한 쪽이 아니라
# 발주한 쪽**이라 필터가 정답 문서를 통째로 배제한다.
#
# 🔴 이 hop 의 진짜 위험은 **지금 맞히는 문항을 밀어내는 것**이다. 근거 칸도 hop 예산도
# 유한하다. 그래서 이 hop 은 **자기 차선(lane)** 에서만 움직인다. 셋 다 지킨다.
#
#   ① 근거 칸을 뺏지 않는다   — 검색 근거는 종전대로 `MAX_EVIDENCE` 만큼 담고, 발주처
#                              근거는 그 **뒤에** `MAX_COUNTERPARTY_EVIDENCE` 칸을 따로 쓴다.
#   ② 충분성 판정에 안 낀다   — `_sufficiency` 는 검색 차선만 본다. 발주처 근거로 hop 0 이
#                              `sufficient` 를 내면 재검색이 마르고 Q-113(정정 체인 문항)
#                              같은 문항이 뒤 hop 에서 찾던 골든을 잃는다.
#   ③ 체인 씨앗이 안 된다     — `_chain_seeds` 는 검색 상위 2건이다(제동 ①). 발주처 열거는
#                              순위가 아니므로 씨앗 자격이 없다. 차선을 나누면 자동으로 지켜진다.
#
# 그 결과 **검색 차선은 발주처 hop 이 있든 없든 비트 단위로 같다** — 회귀가 구조적으로
# 불가능하다. 실측으로도 밸류체인 9문항 전부에서 종전 근거 블록이 그대로 남는다.
#
# **연도로 거르지 않는다.** `query_by_counterparty` 에 `year` 인자가 있지만 쓰지 않는다 —
# 연도를 걸면 그 해에 계약이 없던 파트너가 집합에서 사라져 '몇 곳인가' 의 답이 틀린다.
# 이 hop 이 그리는 것은 시점이 아니라 경계다.

#: 발주처 근거가 쓸 수 있는 칸. 그래프 최대 파트너가 **6**이라(실측: 방위사업청·
#: 한국수력원자력, 나머지 143 노드는 3 이하) 파트너당 1건은 항상 이 안에 든다.
MAX_COUNTERPARTY_EVIDENCE = 6


def _partner_quota(n_partners: int) -> int:
    """파트너 하나가 가져갈 근거 수. `_cell_quota` 와 같은 방식 — 칸을 셀 수로 나눈다.

    나누는 대상은 근거 칸의 **절반**이다. 파트너가 6이면 몫이 1이라 **모든 파트너가
    한 칸씩** 받는다 — fan-in 문항이 요구하는 것이 정확히 그 한 칸씩이다
    (실측 Q-110 2/6 → 5/6).

    더 깊이 파지 않는 이유는 실측이다. 파트너가 둘뿐인 노드(삼성전자 66건 → 삼성E&A ·
    삼성중공업)에서 quota 를 2 → 3 으로 늘려도 골든 적중은 그대로고(Q-114 1/5),
    5 로 늘리면 +1(2/5) 이지만 그 1건 값으로 **지금 맞히는 문항**에 무관 레코드가
    실린다 — 밸류체인 전수 실측에서 근거 블록이 105 → 118 로 늘고 그 증가분은
    Q-108(+2)·Q-113(+5) 처럼 골든이 0건 늘어난 문항에 쏠린다. 8 까지 늘리면 4/5 지만
    한 문항에 16블록이라 선을 한참 넘는다. 열거에 필요한 만큼만 담는다.
    """
    return max(1, MAX_EVIDENCE // 2 // max(n_partners, 1))


def _partner_order(records: list[dict]) -> list[dict]:
    """파트너 안에서의 순서. **최신본 먼저, 그 다음 도착 순서**다.

    도착 순서를 흔들지 않는 것은 제동 ⑦ 그대로다. 그 앞에 최신본만 올린다 —
    대체된 공시로 파트너를 대표시키면 열거가 옛 값을 말한다 (`query_events` 가
    `is_latest=True` 를 기본으로 두는 것과 같은 이유다).

    실측(밸류체인 9문항, 파트너당 1건): 도착 순서 9건 → 최신본 우선 11건.
    금액 내림차순(12건)·접수일 내림차순(13건)이 합계로는 높지만 둘 다 fan-in 문항
    Q-110 에서 2/6 이다 — 합계가 높은 정렬이 **겨냥한 문항을 못 푼다**. 이 hop 의
    목적은 총합이 아니라 열거이므로 Q-110 5/6 을 주는 쪽을 택했다.
    """
    return sorted(records, key=lambda r: not r.get("is_latest", True))


def _counterparty_note(display: str, roster: str, n_partners: int) -> str:
    """근거 블록 안에 발주 관계를 박아 둔다. **별도 요약 블록으로 빼지 않는다** —
    요약 블록은 원문 없이도 '골든이 실렸다' 로 세어져 성적표를 부풀린다 (`_chain_note` 주석).

    접수번호를 한 자도 싣지 않는다. 그래서 이 줄은 근거 적중 집계를 흔들 수 없고,
    '상장사가 몇 곳인가' 처럼 어느 한 레코드에도 안 적혀 있는 사실만 전한다.
    """
    return f"- 발주처 관계: {display} 를 계약상대로 한 공시 상장사 {n_partners}곳 — {roster}"


def _gather_counterparty(r, budget: Budget, trace: list[str], take) -> int:
    """발주처 그래프에서 파트너별 근거를 담는다. LLM 0회 · 스텝 1.

    `plan_hops` 사다리 **밖의 스텝 1개**다 (정정 체인 hop 과 같은 선례) — 재검색
    예산(hop 3)을 쓰지 않는다. 그래프 조회는 질의 재구성이 아니라 인접 노드 열거라서
    '넓히기' 사다리의 칸을 차지할 이유가 없다.
    """
    try:
        budget.spend_step()
    except BudgetExceeded as exc:
        trace.append(f"hop={COUNTERPARTY_HOP} stop=step_budget ({exc})")
        return 0
    out = tools.execute("query_by_counterparty", {"name": r.counterparty})
    if "error" in out:
        trace.append(f'hop={COUNTERPARTY_HOP} strat=counterparty '
                     f'tool=query_by_counterparty via={r.counterparty} '
                     f'skip=tool_error 건너뜀({out["error"]})')
        return 0

    partners = list(out.get("partners") or [])
    by_partner: dict[str, list[dict]] = {}
    for record in out.get("records") or []:
        by_partner.setdefault(record.get("corp_name"), []).append(record)
    lanes = {corp: _partner_order(by_partner.get(corp, [])) for corp in partners}
    roster = " · ".join(f"{corp} {len(lanes[corp])}건" for corp in partners)
    note = _counterparty_note(out.get("counterparty") or r.counterparty,
                              roster, len(partners))

    taken: list[str] = []
    for depth in range(_partner_quota(len(partners))):
        for corp in partners:
            if len(taken) >= MAX_COUNTERPARTY_EVIDENCE:
                break
            items = lanes[corp]
            if depth >= len(items):
                continue
            record = items[depth]
            if take(record.get("doc_id"), _annotate(record.get("markdown", ""), note),
                    None, corp):
                taken.append(record["doc_id"])

    trace.append(f'hop={COUNTERPARTY_HOP} strat=counterparty tool=query_by_counterparty '
                 f'via={r.counterparty} partners={len(partners)} '
                 f'corp="{",".join(partners) or "-"}" n={len(taken)} top_score=- '
                 f'ids={",".join(taken) or "-"}')
    return len(taken)


# --- F-3 hop 로그 (Phase H 입력) ---------------------------------------------
# think_trace 는 사람이 읽는 문자열이지만 Phase H 의 RETRIEVAL/RANKING 판정도 여기서 나온다.
# 그래서 hop 별 질의어·필터·후보 수·후보 doc_id 를 key=value 로 남기고 parse_hops() 로 되읽는다.

_KV = re.compile(r'(\w+)=("[^"]*"|[^\s|]*)')


# --- A2 흔적 -------------------------------------------------------------------
# "끈 줄 알았는데 안 꺼진 것" 이 ablation 의 진짜 위험이다 (tests/test_ablation 머리말).
# 반대로 **켰는데 안 켜진 것**도 같은 크기의 위험이라, 융합이 실제로 돌았는지를
# `think_trace` 에 남긴다. arm 이 꺼져 있으면 `LAST_FUSION` 이 None 이라 아무것도 안 붙는다.

def _fusion_note() -> str:
    fusion = bm25_module.LAST_FUSION
    if not fusion:
        return ""
    return (f' fusion={fusion["mode"]} pool={fusion["pool"]} '
            f'dense_n={fusion["dense_n"]} dense_new={fusion["added"]} '
            f'dense_gain={fusion["gain"]}')


def _fmt_period(period: dict) -> str:
    if "ym" in period:
        return f"ym:{period['ym']}"
    if "year" in period:
        return f"year:{period['year']}"
    return "none"


def _log_search(plan, corp, hits: list[dict], period: dict | None = None) -> str:
    ids = ",".join(str(h.get("doc_id") or "?") for h in hits)
    scores = [h["score"] for h in hits if isinstance(h.get("score"), (int, float))]
    top = f"{max(scores):.3f}" if scores else "-"
    cell = plan.period if period is None else period
    return (f'hop={plan.hop} strat={plan.strategy} tool=search_chunks '
            f'corp="{corp or "-"}" period={_fmt_period(cell)} top_k={plan.top_k} '
            f'q="{plan.query}" n={len(hits)} top_score={top} ids={ids or "-"}'
            f'{_fusion_note()}')


def _log_verdict(hop: int, ok: bool, count: int, reasons: list[str]) -> str:
    line = f'hop={hop} verdict={"sufficient" if ok else "insufficient"} evidence={count}'
    return line if ok else f'{line} reason={",".join(reasons)}'


def parse_hops(think_trace: str) -> list[dict]:
    """think_trace 를 hop 레코드로 되돌린다. Phase H 의 RETRIEVAL/RANKING 판정 입력.

        ids = [d for h in parse_hops(resp["think_trace"]) for d in h["ids"]]
        RETRIEVAL  — 골든 rcept_no 가 ids 어디에도 없음
        RANKING    — ids 에는 있으나 상위 3위 밖

    hop 로그가 없는 trace (범위 밖 질의 등) 는 빈 목록을 돌려준다.
    """
    hops: dict[int, dict] = {}
    order: list[int] = []
    for line in (think_trace or "").split(" | "):
        kv = {k: v.strip('"') for k, v in _KV.findall(line)}
        raw = kv.get("hop", "")
        if not raw.isdigit():
            continue
        hop = int(raw)
        rec = hops.get(hop)
        if rec is None:
            rec = {"hop": hop, "strategy": "", "corp": [], "period": "", "queries": [],
                   "ids": [], "n": 0, "top_score": None, "verdict": "", "reason": "",
                   "notes": []}
            hops[hop] = rec
            order.append(hop)
        rec["strategy"] = kv.get("strat") or rec["strategy"]
        rec["period"] = kv.get("period") or rec["period"]
        rec["verdict"] = kv.get("verdict") or rec["verdict"]
        rec["reason"] = kv.get("reason") or rec["reason"]
        if kv.get("corp") and kv["corp"] != "-" and kv["corp"] not in rec["corp"]:
            rec["corp"].append(kv["corp"])
        if kv.get("q"):
            rec["queries"].append(kv["q"])
        if kv.get("n", "").isdigit():
            rec["n"] += int(kv["n"])
        for doc_id in (kv.get("ids") or "").split(","):
            if doc_id and doc_id != "-" and doc_id not in rec["ids"]:
                rec["ids"].append(doc_id)
        try:
            score = float(kv.get("top_score", ""))
        except ValueError:
            score = None
        if score is not None:
            rec["top_score"] = score if rec["top_score"] is None else max(rec["top_score"], score)
        for key in ("skip", "stop"):
            if kv.get(key):
                rec["notes"].append(f"{key}={kv[key]}")
    return [hops[h] for h in order]


# --- 상류 장애 표식 -----------------------------------------------------------
# 게이트 F 상 `/answer` 는 어떤 경우에도 5키 스키마를 돌려줘야 한다. 그래서 429 를 받아도
# 파이프라인은 죽지 않고 한계 고지를 채워 내보낸다. 문제는 **그게 모델의 거절과 구분되지
# 않는다**는 것이다 — 호출부가 그대로 캐시하면 전송 실패가 영원히 오답으로 굳는다.
# 실측으로 그 사고가 났다 (Q-051·Q-053).
#
# 스키마는 그대로 두고 `think_trace` 에 기계가 읽는 표식을 남긴다. 호출부는
# `upstream_failure()` 로 되읽어 "우리 답" 과 "전송 실패" 를 가른다.

#: 상류 장애 표식. 사람이 읽는 `생성 실패: …` 문구는 그대로 둔다 (기존 호출부 호환).
_UPSTREAM = "upstream_error"
_UPSTREAM_RE = re.compile(r"upstream_error stage=(\w+) status=(\S+) retryable=(\w+)")
_STATUS_RE = re.compile(r"실패 (\d{3})")

#: 재시도로 넘길 수 있는 상태. 429(rate limit)·408(timeout)·5xx 는 우리 답이 아니다.
RETRYABLE_STATUS = frozenset({"408", "425", "429", "500", "502", "503", "504"})


def _upstream_mark(stage: str, exc: Exception) -> str:
    match = _STATUS_RE.search(str(exc))
    status = match.group(1) if match else "-"
    retryable = status in RETRYABLE_STATUS or status == "-"
    return f"{_UPSTREAM} stage={stage} status={status} retryable={retryable}"


def upstream_failure(think_trace: str) -> dict | None:
    """상류 장애로 한계 고지가 나갔는가. 아니면 None.

    호출부(평가 러너·API)가 **오답으로 캐시하지 않기 위해** 쓰는 값이다.
    `answer` 만 보면 모델의 정당한 거절과 전송 실패가 똑같이 생겼다.
    """
    match = _UPSTREAM_RE.search(think_trace or "")
    if not match:
        return None
    return {"stage": match.group(1), "status": match.group(2),
            "retryable": match.group(3) == "True"}


#: 섹션 근거는 한 건이 최대 8,000자다. exchange 레코드 10건과 같은 상한(MAX_EVIDENCE)을
#: 쓰면 프롬프트가 8만자가 된다 — 20,000 토큰 예산을 혼자 넘긴다.
MAX_SECTION_EVIDENCE = 4
#: 섹션 근거 총량 상한. 12,000자 ≈ HCX 7~8천 토큰으로, 생성 800 토큰과 critic 을 남긴다.
MAX_SECTION_CHARS = 12_000


def _log_section(plan, corp, path, hits: list[dict]) -> str:
    ids = ",".join(str(h.get("doc_id") or "?") for h in hits)
    scores = [h["score"] for h in hits if isinstance(h.get("score"), (int, float))]
    top = f"{max(scores):.3f}" if scores else "-"
    filters = ",".join(f"{k}={v}" for k, v in sorted(plan.filters.items())) or "-"
    return (f'hop={plan.hop} strat={plan.strategy} tool=search_chunks group=periodic '
            f'corp="{corp or "-"}" filters={filters} path="{path or "-"}" '
            f'top_k={plan.top_k} q="{plan.query}" n={len(hits)} '
            f'top_score={top} ids={ids or "-"}{_fusion_note()}')


def _path_cap(per_corp: int, index: int, n_paths: int) -> int:
    """경로 하나가 한 기업에게서 가져갈 근거 칸 수.

    1순위 경로가 결정적 축이고(목차 축 실측 회수 0.679 -> 0.929) 짝 경로는 '같은 지표가
    다른 목차에 실렸을 때' 를 대비한 **보험 한 칸**이다. 그래서 칸을 반씩 나누지 않고
    짝 경로에 1칸씩만 떼어 준다. 반씩 나눴더니 실측 Q-050 의 골든(1순위 경로 안에서 3위)이
    잘렸다 — 4칸을 2/2 로 쪼개면 3위가 못 들어온다.
    """
    if n_paths <= 1:
        return per_corp
    return max(1, per_corp - (n_paths - 1)) if index == 0 else 1


def _cover_order(picked: list[dict]) -> list[dict]:
    """기업별 라운드로빈. 기업 안에서는 **도착 순서 그대로**다.

    글자 예산이 근거 칸의 진짜 제약이라 순서가 곧 회수율이다. 그런데 정렬 기준을
    잘못 잡으면 정확히 반대로 간다 — 짧은 블록부터 담아 봤더니 Q-045·Q-046 의 골든
    (3,679자 · 3,371자) 이 뒤로 밀려 잘렸다. 도착 순서는 이미 목차 순위와 BM25 점수로
    정해진 순서이므로 그것을 흔들 이유가 없다.

    바꾸는 것은 **기업 축뿐**이다. 비교 질의에서 한 기업의 큰 섹션이 예산을 먼저 먹으면
    다른 기업이 통째로 빠진다 (실측 Q-049 — 두산에너빌리티 `매출 및 수주상황` 6,649자).
    기업을 번갈아 담으면 모든 기업이 한 칸씩 먼저 확보된다. 기업이 하나면 항등 변환이다.
    """
    by_corp: dict = {}
    for item in picked:
        by_corp.setdefault(item.get("corp"), []).append(item)
    lanes = list(by_corp.values())
    out: list[dict] = []
    for depth in range(max((len(v) for v in lanes), default=0)):
        out += [items[depth] for items in lanes if depth < len(items)]
    return out


def _join_section_evidence(picked: list[dict]) -> str:
    """섹션 근거를 총량 상한 안에서 이어 붙인다. 상한은 토큰 예산에서 역산한 값이다.

    예산을 넘기는 블록은 **건너뛰고 계속 채운다**. 종전처럼 거기서 끊으면 큰 블록 하나가
    뒤에 오는 작은 블록 전부를 같이 버린다 — 상한은 그대로이므로 토큰은 늘지 않는다.
    실측 Q-049: 끊으면 3블록 12,316자에서 골든이 빠지고, 건너뛰면 3블록 11,381자에
    골든 둘이 다 든다.
    """
    out: list[str] = []
    total = 0
    for item in _cover_order(picked)[:MAX_SECTION_EVIDENCE]:
        markdown = item["markdown"]
        if out and total + len(markdown) > MAX_SECTION_CHARS:
            continue
        out.append(markdown)
        total += len(markdown)
    return "\n\n".join(out)


def _gather_sections(r, question: str, budget: Budget,
                     trace: list[str], take) -> int:
    """정기공시 섹션 ReAct 루프. LLM 0회.

    exchange 루프와 **hop 사다리가 다르다**. 목차 경로를 먼저 정하고(§7.7 ③④⑤)
    보고서 종류·사업연도로 문서를 고른 뒤 BM25 가 섹션을 고른다. 실측 근거는
    docs/pipeline/04-retriever.md D-5 — 목차 축을 빼면 골든 회수가 0.929 -> 0.679 다.

    hop 0 은 **경로 후보 여러 개를 함께** 본다 (병목 B). 같은 지표가 서로 다른 목차에
    실리는 문항이 실재하기 때문이다 — Q-049 는 한 질문 안에서 두 기업이 '연결 매출액' 을
    `매출 및 수주상황` 과 `요약재무정보` 에 나눠 실었다. 다만 경로 하나가 기업 칸을 다
    먹으면 짝 경로를 찾아 놓고도 담지 못하므로 **(기업 × 경로) 셀마다 할당량**을 둔다.
    """
    paths = path_hints(question, tools.section_paths())
    plans = plan_section_hops(question, report_hints(question), paths, r.years)
    if not ablation.current().hop_ladder:
        plans = plans[:1]                       # A1b 이하 — 재검색 사다리를 태우지 않는다
    corps = r.companies[:3] or [None]
    # 비교 질의는 한쪽만 채우면 답이 안 된다. 한 기업이 근거 칸을 다 먹지 않게 나눈다.
    per_corp = max(1, MAX_SECTION_EVIDENCE // len(corps))
    quota = {corp: per_corp for corp in corps}
    taken = 0
    for plan in plans:
        if plan.hop > 0:
            try:
                budget.spend_hop()
            except BudgetExceeded as exc:
                trace.append(f"hop={plan.hop} stop=hop_budget ({exc})")
                break
        hop_paths = plan.paths or (None,)
        for index, path in enumerate(hop_paths):
            cell_cap = _path_cap(per_corp, index, len(hop_paths))
            for corp in corps:
                if taken >= MAX_SECTION_EVIDENCE:
                    break
                if quota[corp] <= 0:
                    continue
                try:
                    budget.spend_step()
                except BudgetExceeded as exc:
                    trace.append(f"hop={plan.hop} stop=step_budget ({exc})")
                    return taken
                args = {"query": plan.query, "top_k": plan.top_k,
                        "group": "periodic", **plan.filters, **plan.prefix}
                if corp:
                    args["corp_name"] = corp
                if path:
                    args["path"] = path
                out = tools.execute("search_chunks", args)
                if "error" in out:
                    trace.append(f'hop={plan.hop} strat={plan.strategy} '
                                 f'tool=search_chunks group=periodic skip=tool_error '
                                 f'건너뜀({out["error"]})')
                    return taken
                hits = out.get("chunks") or []
                trace.append(_log_section(plan, corp, path, hits))
                # 한 셀에서 같은 섹션의 조각을 걸러내는 규칙은 **넣지 않았다.** 실측이
                # 말렸다 — (문서, 목차) 중복 제거는 골든 적중을 하나도 못 올리면서
                # hop 10 -> 12, 근거 1,079자 증가만 남겼다 (Q-054 한 문항의 변화가 전부다).
                taken_here = 0
                for chunk in hits:
                    if quota[corp] <= 0 or taken_here >= cell_cap:
                        break
                    if take(chunk.get("chunk_id"), chunk.get("markdown"),
                            chunk.get("score"), corp):
                        taken += 1
                        taken_here += 1
                        quota[corp] -= 1
        if all(v <= 0 for v in quota.values()):
            break
    return taken


def _gather_meta(r, question: str, trace: list[str], take) -> str | None:
    """A0 — 메타 프리필터만. **BM25 를 한 번도 부르지 않는다.**

    §I-1 의 실측이 이 arm 의 근거다: `corp_name` + `rcept_dt` 접두만으로 후보가
    1,469 → 1건이 되고 골든이 1위가 된다. 그러니 검색의 바닥은 Vector RAG 가 아니라
    **여기**이고, 여기를 안 재면 가장 큰 기여 요소가 측정에서 통째로 빠진다.

    순위가 없다는 것이 이 arm 의 정의다. manifest 순서 그대로 앞에서부터 담는다 —
    점수를 매기는 순간 그건 A1(BM25) 이다. 정기공시도 같은 규칙이라 목차 선택 없이
    문서의 앞 섹션을 담는다 (실측상 거의 못 맞히는데, 그게 A1b 의 Δ 로 잡히는 값이다).
    """
    period = _period_args(r, question)
    group = getattr(r, "group", "exchange")
    year = int(str(period["ym"])[:4]) if "ym" in period else period.get("year")
    docs = []
    for corp in (r.companies[:3] or [None]):
        docs += filter_docs(corp_name=corp, doc_group=group, year=year)
    if "ym" in period:
        docs = [d for d in docs if str(d.get("rcept_dt", ""))[:6] == period["ym"]] or docs
    trace.append(f'hop=0 strat=meta tool=filter_docs corp="{",".join(r.companies) or "-"}" '
                 f'period={_fmt_period(period)} group={group} n={len(docs)} top_score=- '
                 f'ids={",".join(str(d.get("doc_id") or "?") for d in docs[:MAX_EVIDENCE]) or "-"}')

    sections: list[dict] = []
    for doc in docs:
        if len(sections) >= MAX_SECTION_EVIDENCE and group == "periodic":
            break
        record = tools._record_of(doc.get("rcept_no") or doc.get("doc_id"))
        if record is not None:
            take(record["doc_id"], record.get("markdown", ""), None, record.get("corp_name"))
            continue
        if group != "periodic":
            continue
        index = tools._section_index()
        if index is None:
            continue
        for chunk in tools._sections_of(index, doc["doc_id"]):
            if chunk["part"] or chunk["is_stub"] or len(sections) >= MAX_SECTION_EVIDENCE:
                continue
            entry = {"doc_id": chunk["chunk_id"], "markdown": tools._section_markdown(chunk),
                     "score": None, "corp": chunk["corp_name"]}
            sections.append(entry)
            take(entry["doc_id"], entry["markdown"], None, entry["corp"])
    return _join_section_evidence(sections) if sections else None


def _gather_evidence(r, question: str = "",
                     budget: Budget | None = None) -> tuple[str, list[str]]:
    """ReAct 반복 루프. 검색 → 충분성 판정 → 부족하면 질의 재구성 후 재검색. LLM 0회.

        검색 → 근거 충분? ─ 예 → 생성
                  └─ 아니오 → 질의 재구성 → 재검색 (hop ≤ 3, 총 스텝 ≤ 8)

    hop 0 은 최초 검색이라 예산을 쓰지 않고, 재검색 3회가 `Budget.spend_hop()` 을 쓴다
    (ReAct 논문: 정답 궤적 중 7스텝을 쓴 것은 0.84% — 깊이를 더 줘도 얻는 게 없다).
    도구 호출은 `Budget.spend_step()` 으로 총 8스텝을 지킨다 (주최 측 참조 아키텍처).
    상한을 넘기면 예외를 밖으로 던지지 않고 **그 시점 근거로 진행**한다.

    도구가 error 를 돌려주면 (예: periodic_sections.jsonl 이 없어 list_sections 실패)
    조용히 건너뛰고 다음 수단으로 넘어간다. 파이프라인은 죽지 않는다.
    """
    arm = ablation.current()
    budget = budget or Budget()
    trace: list[str] = []
    picked: list[dict] = []
    seen: set[str] = set()
    period = _period_args(r, question)
    corps = r.companies[:3] or [None]
    group = getattr(r, "group", "exchange")
    # A1 은 exchange 인덱스 1,469건만 본다. 라우터가 major·holding·periodic 으로
    # 갈라도 검색은 exchange 로 되돌린다 — 그것이 A1b(인덱스 커버리지) 의 Δ 다.
    if not arm.uses_group_index:
        group = "exchange"
    required = _required_fields(question, group)
    stalled = 0
    out_of_steps = False
    chain_done = False
    chain_gap: list[str] = []
    chain_ids: set = set()

    # 발주처 차선. `seen` 을 **공유하지 않는다** — 공유하면 발주처 레코드가 검색 상위와
    # 겹칠 때 검색 차선의 도착 순서가 밀리고, 그러면 체인 씨앗(상위 2건)이 바뀐다.
    # 차선을 완전히 나눠야 검색 경로가 종전과 비트 단위로 같다.
    cp_picked: list[dict] = []
    cp_seen: set[str] = set()

    def _take(doc_id, markdown, score, corp) -> bool:
        if markdown and doc_id and doc_id not in seen:
            seen.add(doc_id)
            picked.append({"doc_id": doc_id, "markdown": markdown,
                           "score": score, "corp": corp})
            return True
        return False

    def _take_cp(doc_id, markdown, score, corp) -> bool:
        if markdown and doc_id and doc_id not in cp_seen:
            cp_seen.add(doc_id)
            cp_picked.append({"doc_id": doc_id, "markdown": markdown,
                              "score": score, "corp": corp})
            return True
        return False

    def _joined() -> str:
        """검색 차선 먼저, 발주처 차선은 그 뒤. 검색 칸(MAX_EVIDENCE)은 그대로 남는다."""
        extra = [p for p in cp_picked if p["doc_id"] not in seen]
        return "\n\n".join(p["markdown"] for p in
                           picked[:MAX_EVIDENCE] + extra[:MAX_COUNTERPARTY_EVIDENCE])

    if not arm.uses_bm25:                      # A0 — 검색 없이 메타 프리필터만
        if _out_of_corpus_period(r):
            trace.append(f"hop=0 stop=out_of_range "
                         f"(질문 기간 {r.years} 가 코퍼스 2023.01~2026.03 밖 — 완화해도 답이 없다)")
            return "", trace
        sections = _gather_meta(r, question, trace, _take)
        ok, reasons = _sufficiency(r, picked, required)
        trace.append(_log_verdict(0, ok, len(picked), reasons))
        if sections is not None:
            return sections, trace
        return "\n\n".join(p["markdown"] for p in picked[:MAX_EVIDENCE]), trace

    # 정기공시는 검색 축이 다르다 — 접수일이 아니라 사업연도, 그리고 목차 경로.
    # 같은 사다리를 태우면 골든이 필터에서 통째로 빠진다 (04-retriever D-5).
    if group == "periodic":
        if _out_of_corpus_period(r):
            trace.append(f"hop=0 stop=out_of_range "
                         f"(질문 기간 {r.years} 가 코퍼스 2023.01~2026.03 밖 — 완화해도 답이 없다)")
            return "", trace
        _gather_sections(r, question, budget, trace, _take)
        ok, reasons = _sufficiency(r, picked, required)
        trace.append(_log_verdict(EVENTS_HOP, ok, len(picked), reasons))
        if ok or picked:
            return _join_section_evidence(picked), trace
        # 섹션에서 아무것도 못 찾으면 정형 레코드로 내려간다 (거래소공시에도 실려 있을 수 있다).
        group = "exchange"

    # 발주처 hop 은 hop 0 에서 한 번. 사다리 밖의 스텝 1개라 재검색 예산을 쓰지 않는다.
    if getattr(r, "counterparty", None) and arm.hop_ladder:
        _gather_counterparty(r, budget, trace, _take_cp)

    cells = time_points(question, r.years) if arm.split_query else []
    plans = plan_hops(question, period, fallback_terms=r.companies, periods=cells)
    if not arm.hop_ladder:
        plans = plans[:1]                       # A1b 이하 — 최초 검색 한 번으로 끝낸다
    for plan in plans:
        # 기업이 특정된 질의에서 이미 근거가 있으면 기업 필터를 풀지 않는다.
        # 다른 회사 공시가 섞이면 요약·비교가 통째로 오염된다 — 넓히는 것이 늘 이득은 아니다.
        if plan.strategy == "global" and picked and r.companies:
            trace.append(f'hop={plan.hop} strat=global skip=corp_scoped '
                         f'(기업 특정 질의에 근거 {len(picked)}건 확보 — 전역 검색은 오염)')
            break
        if plan.hop > 0 and _out_of_corpus_period(r):
            trace.append(f"hop={plan.hop} stop=out_of_range "
                         f"(질문 기간 {r.years} 가 코퍼스 2023.01~2026.03 밖 — 완화해도 답이 없다)")
            break
        if plan.hop > 0:
            try:
                budget.spend_hop()
            except BudgetExceeded as exc:
                trace.append(f"hop={plan.hop} stop=hop_budget ({exc})")
                break

        before = len(picked)
        hop_cells = plan.periods or (plan.period,)
        targets = corps if plan.use_corp else [None]
        quota = _cell_quota(len(hop_cells), len(targets))
        for cell in hop_cells:
            for corp in targets:
                if len(picked) >= MAX_EVIDENCE or out_of_steps:
                    break
                try:
                    budget.spend_step()
                except BudgetExceeded as exc:
                    trace.append(f"hop={plan.hop} stop=step_budget ({exc})")
                    out_of_steps = True
                    break
                args = {"query": plan.query, "top_k": plan.top_k, **cell}
                if group != "exchange":
                    args["group"] = group
                if corp:
                    args["corp_name"] = corp
                out = tools.execute("search_chunks", args)
                if "error" in out:
                    trace.append(f'hop={plan.hop} strat={plan.strategy} '
                                 f'tool=search_chunks corp="{corp or "-"}" '
                                 f'skip=tool_error 건너뜀({out["error"]})')
                    continue
                hits = out.get("chunks") or []
                if plan.strategy == "global":
                    # 필터가 하나도 없는 검색은 점수만이 관련성의 보증이다.
                    # 잡음 바닥(MIN_TOP_SCORE) 밑을 담으면 무관한 회사 공시 10건이 근거가 된다.
                    hits = [h for h in hits
                            if isinstance(h.get("score"), (int, float))
                            and h["score"] >= MIN_TOP_SCORE]
                trace.append(_log_search(plan, corp, hits, cell))
                taken_here = 0
                for chunk in hits:
                    if taken_here >= quota:            # 제동 ② — 셀별 할당량
                        break
                    if _take(chunk.get("doc_id"), chunk.get("markdown"),
                             chunk.get("score"), corp):
                        taken_here += 1

        # 집합 완결성. 후보가 대체된 문서면 체인의 나머지가 와야 근거가 완결된다.
        # **판정 전에** 한 번만 밟는다 — 판정 뒤에 두면 hop 0 이 `sufficient` 를 내고
        # 멈춰서 정정본이 영영 안 온다 (Phase H 실패 7건의 정확한 기전).
        if arm.chain_hop and not chain_done and picked and not out_of_steps:
            chain_done = True
            chain_gap, chain_ids = _expand_chain(picked, seen, budget, trace, _take,
                                                 _as_of_ym(question))

        ok, reasons = _sufficiency(r, picked, required, chain_gap)
        trace.append(_log_verdict(plan.hop, ok, len(picked), reasons))
        if ok or out_of_steps:
            break
        if chain_gap and all(x.startswith("chain_open:") for x in reasons):
            # 못 닫은 링크는 재검색으로 안 닫힌다 (도구 오류이거나 스텝이 마른 것이다).
            # 남은 hop 을 태우지 않는다.
            trace.append(f"hop={CHAIN_HOP} stop=chain_unresolved "
                         f"(체인 링크 {len(chain_gap)}건 미해소 — 재검색 대상이 아니다)")
            break
        # 근거가 하나도 없을 때는 수렴이 아니라 못 찾은 것이다 — 계속 넓힌다.
        stalled = stalled + 1 if picked and len(picked) == before else 0
        if stalled >= MAX_STALLED_HOPS:
            trace.append(f"hop={plan.hop} stop=stalled "
                         f"({MAX_STALLED_HOPS}회 연속 새 후보 없음)")
            break

    # 마지막 수단: 검색이 아무것도 못 찾으면 정형 이벤트 열거로 보완.
    # 연도가 둘 이상이면 query_events 에 기간 조건을 걸 수 없으므로 범위 밖 질의는 여기서도 막는다.
    # `query_events` 모집단은 거래소공시다 (§7.7 ②) — 다른 문서군 질의에 붙이면 오답 근거다.
    if (arm.hop_ladder and not picked and group == "exchange"
            and not _out_of_corpus_period(r)):
        for corp in r.companies[:3]:
            try:
                budget.spend_step()
            except BudgetExceeded as exc:
                trace.append(f"hop={EVENTS_HOP} stop=step_budget ({exc})")
                break
            args: dict = {"corp_name": corp, "is_latest": True}
            if len(r.years) == 1:
                args["year"] = r.years[0]
            out = tools.execute("query_events", args)
            if "error" in out:
                trace.append(f'hop={EVENTS_HOP} strat=events tool=query_events '
                             f'corp="{corp}" skip=tool_error 건너뜀({out["error"]})')
                continue
            events = (out.get("events") or [])[:5]
            ids = ",".join(str(e.get("doc_id") or "?") for e in events)
            trace.append(f'hop={EVENTS_HOP} strat=events tool=query_events corp="{corp}" '
                         f'period={_fmt_period({"year": args["year"]} if "year" in args else {})} '
                         f'n={out.get("count", 0)} top_score=- ids={ids or "-"}')
            for event in events:
                _take(event.get("doc_id"), event.get("markdown"), None, corp)

    # 근거 절단은 체인을 먼저 살린다. 한화오션 잠수함은 검색 5건 + 체인 6건 = 11건이라
    # 도착 순서대로 자르면 **체인의 마지막 정정본**(=정답)이 잘려 나간다.
    if chain_ids:
        picked.sort(key=lambda p: 0 if p["doc_id"] in chain_ids else 1)

    return _joined(), trace


def answer_question(question_id: str, question: str) -> dict:
    budget = Budget()
    clean, warnings = sanitize_question(question)
    trace: list[str] = list(warnings)

    r = route(clean)
    trace.append(f"route={r.kind} difficulty={r.difficulty} cot={r.use_cot} sc={r.use_sc}")

    if r.kind == "unanswerable":
        if r.reason.startswith("no_such_report"):
            trace.append(f"{r.reason} — 지목한 보고서가 목록에 없다 (LLM 호출 없이 한계 고지)")
            return _respond(question_id, question, "", trace, NO_SUCH_REPORT, budget)
        trace.append("범위 밖 — LLM 호출 없이 한계 고지")
        return _respond(question_id, question, "", trace, UNANSWERABLE, budget)

    evidence, tool_trace = _gather_evidence(r, clean, budget)
    trace.extend(tool_trace)
    # 근거 성형은 **검색이 끝난 뒤**다. 여기 위로 올리면 A5·L2 의 Δ 에 검색 변화가
    # 섞여 단일 변수 조작이 아니게 된다 (`ablation.shape_evidence` 주석).
    evidence = ablation.shape_evidence(evidence)

    if not evidence:
        trace.append("근거 없음 — LLM 호출 없이 한계 고지")
        return _respond(question_id, question, "", trace, UNANSWERABLE, budget)

    try:
        result = chat(build_answer_messages(clean, evidence, r.use_cot),
                      max_tokens=800 if r.use_cot else 300,
                      temperature=0.0, tag="answer")
        budget.spend_llm(result.prompt_tokens, result.completion_tokens)
        answer = result.text
        trace.append(f"generate tokens={result.prompt_tokens}+{result.completion_tokens}")
    except BudgetExceeded as exc:
        trace.append(f"예산 초과: {exc}")
        return _respond(question_id, question, evidence, trace, UNANSWERABLE, budget)
    except RuntimeError as exc:
        # /answer 는 어떤 경우에도 5키 스키마를 돌려줘야 한다. 상류 장애로 500 을 내면 0점이다.
        # 다만 **삼키지는 않는다** — 표식을 남겨 호출부가 전송 실패를 가려낼 수 있게 한다.
        trace.append(f"{_upstream_mark('generate', exc)} 생성 실패: {exc}")
        return _respond(question_id, question, evidence, trace, UNANSWERABLE, budget)

    if not quick_check(answer)["has_citation"]:
        trace.append("근거 표기 누락 — critic 호출")
        try:
            verdict = judge(evidence, answer, budget)
            trace.append(f"critic supported={verdict.supported} reason={verdict.reason}")
            if not verdict.passed:
                answer += f"\n\n(주의: 근거 검증을 통과하지 못했습니다 — {verdict.reason})"
        except BudgetExceeded as exc:
            trace.append(f"critic 생략: {exc}")            # 우리 상한이다 — 장애가 아니다
        except RuntimeError as exc:
            trace.append(f"{_upstream_mark('critic', exc)} critic 생략: {exc}")

    answer = _verify(clean, answer, evidence, r, trace)
    return _respond(question_id, question, evidence, trace, mask_pii(answer), budget)


# --- A6 Verifier — 결정적 검증 2종. LLM 0회 ------------------------------------
# `judge` 는 "답이 근거로 지지되는가" 만 본다. 그래서 정정 전 금액만 근거에 있으면
# 모델이 그 값을 답하고 critic 은 통과시킨다 — 근거 자체가 불완전하다는 것을 못 본다
# (07-verifier G-4, 실측 오답 5건). 두 검증은 축이 다르다.
#
#   verify_calculation  — 답변 수치를 근거 레코드 원값으로 재계산 대조   (TOOL)
#   verify_completeness — 근거가 충분한가                              (RETRIEVAL·GENERATION)
#
# **답을 갈아엎지 않는다.** 결함을 찾아도 재생성하지 않고 경고만 붙인다 — 재생성은
# 호출을 하나 더 쓰는 별개의 조작이고, 이 arm 이 재는 것은 '판정' 의 기여다
# (SWE-Lancer 부록 C: 판단 44.9% > 생성 26.2%).

_EVIDENCE_RCEPT = re.compile(r"접수번호\s*(\d{14})")


def _evidence_records(evidence: str) -> list[dict]:
    """근거에 실린 접수번호로 정형 레코드를 되찾는다. 검색이 아니라 조회다 (스텝 0)."""
    out = []
    for rcept_no in dict.fromkeys(_EVIDENCE_RCEPT.findall(evidence or "")):
        record = tools._record_of(rcept_no)
        if record is not None:
            out.append(record)
    return out


def _verify(question: str, answer: str, evidence: str, r, trace: list[str]) -> str:
    """A6. 결함이 있으면 답변에 경고를 붙이고 trace 에 판정을 남긴다."""
    if not ablation.current().verifier:
        return answer

    # 레코드가 여러 건이면 **하나라도 설명되면 통과**다. 체인 안에서 원공시와 정정본의
    # 금액이 갈리므로(227,500 vs 114,800) 전건 일치를 요구하면 정답이 오답이 된다.
    reports = [verify_calculation(answer, record) for record in _evidence_records(evidence)]
    checked = sorted({k for rep in reports for k in rep["checked"]})
    explained = any(rep["ok"] and rep["checked"] for rep in reports)
    calc_failed = bool(checked) and not explained

    complete = verify_completeness(question, answer, evidence, companies=r.companies)

    kinds = sorted({*(["calculation"] if calc_failed else []), *complete["failed_kinds"]})
    trace.append(f'verify calc_checked={",".join(checked) or "-"} '
                 f'complete_checked={",".join(complete["checked"]) or "-"} '
                 f'ok={not kinds} kinds={",".join(kinds) or "-"}')
    if not kinds:
        return answer

    reasons = [rep["reason"] for rep in reports if rep["reason"]][:1] if calc_failed else []
    if complete["reason"]:
        reasons.append(complete["reason"])
    return answer + f'\n\n(주의: 자동 검증에서 결함이 발견되었습니다 — {"; ".join(reasons)})'


def _respond(question_id: str, question: str, evidence: str, trace: list[str],
             answer: str, budget: Budget | None = None) -> dict:
    """5키 스키마. 어떤 경로로 나가든 마지막에 예산 실사용량을 남긴다 (게이트 F)."""
    if budget is not None:
        trace = trace + [f"budget steps={budget.steps} hops={budget.hops} "
                         f"llm={budget.llm_calls} tokens={budget.tokens}"]
    return {
        "question_id": question_id,
        "question": question,
        "retrieved_context": evidence,
        "think_trace": " | ".join(trace),
        "answer": answer,
    }
