"""파이프라인 진입점. 고정 횟수 단계라 질문당 비용 상한이 계산된다."""
from __future__ import annotations

import json
import re
from collections.abc import Sequence

from argus import ablation
from argus.agent import tools
from argus.agent.budget import Budget, BudgetExceeded
from argus.agent.critic import (aggregate_calculation, judge, quick_check,
                                substitute_recomputed, tool_result_reports,
                                unique_recomputed, verify_answer_arithmetic,
                                verify_calculation, verify_completeness,
                                verify_number_provenance)
from argus.agent.prompts import build_answer_messages, build_regeneration_messages
from argus.agent.query import (SPLIT_TOP_K, STRATEGIES, path_hints, plan_hops,
                               plan_section_hops, report_hints, time_points)
from argus.agent import router as router_module
from argus.agent.router import route
from argus.agent.safety import mask_pii, sanitize_question
from argus.clova import chat, no_cache
from argus.corpus import markdown as markdown_module
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

#: 🔴 질문이 **무엇을 묻는지** 알 수 없는 경우 (`A26` · 규약 감사 F-3).
#: `UNANSWERABLE` 을 쓰면 *"70개사 범위 안의 기업으로"* 라고 말하게 되는데, 기업이
#: 지목되지 않은 질의에 그렇게 답하면 **사용자는 자기가 물은 회사가 범위 밖이라고
#: 오해한다.** `CONVENTION §3 R4` 가 요구하는 것은 이 자리에서의 **역질문**이다.
AMBIGUOUS_REFERENCE = (
    "어느 기업을 말씀하시는지 질문에서 확인되지 않습니다. "
    "기업명을 알려주시면 제공된 70개사 · 2023.01~2026.03 범위 안에서 확인해 드리겠습니다."
)

#: 🔴 기업도 기간도 범위 안인데 **묻는 것이 공시에 없는** 경우 (`A31` · v5 오답 S-003·S-004).
#: `UNANSWERABLE` 을 쓰면 *"70개사 범위 안의 기업으로"* 라고 말하는데, 삼성전자는 범위
#: 안이고 없는 것은 **다음 분기 매출**이다. `NO_SUCH_REPORT`·`AMBIGUOUS_REFERENCE` 와
#: 같은 원칙 — 한계의 종류가 다르면 고지도 달라야 한다.
#: 🔴 되물을 방법을 함께 준다. 그냥 거절하면 `CONVENTION §3 R4` 의 역질문이 없는 것이다.
SPECULATIVE = (
    "공시에서 확인되지 않습니다. "
    "주가 전망이나 향후 실적 추정은 공시 기재사항이 아니어서 판단을 생성하지 않습니다. "
    "공시에 실제로 기재된 내용(계약·투자·재무 수치 등)으로 질문해 주시면 "
    "근거와 함께 답변드리겠습니다."
)

#: 🔴 개인정보·식별번호를 **그대로 달라**는 요구 (`A32` · v5 오답 S-002).
#: 골든이 요구하는 것은 둘이다 — 원문의 연락처를 안 내주는 것, 그리고 **질문에 적힌
#: 번호도 마스킹**된다고 말하는 것. 뒤의 절을 빼면 골든의 절반만 맞는다.
IDENTIFIER_WITHHELD = (
    "개인정보와 식별번호는 제공하지 않습니다. "
    "공시 원문에 기재된 연락처·등록번호는 확인되지 않는 형태로만 다루며, "
    "질문에 적어 주신 번호도 마스킹되어 처리됩니다. "
    "공시의 다른 기재사항으로 질문해 주시면 근거와 함께 답변드리겠습니다."
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


def _all_years_out_of_corpus(r) -> bool:
    """지목한 연도가 **전부** 코퍼스 밖인가 — 질문 어형과 무관하게.

    `_out_of_corpus_period` 와 다르다. 이쪽은 '이 연도로 필터를 걸면 후보가 0건이 된다'
    는 검색 사실이고, 저쪽은 '재검색을 태울 가치가 있는가' 라는 정책이다.
    """
    return bool(r.years) and all(y not in CORPUS_YEARS for y in r.years)


def _period_args(r, question: str) -> dict:
    """검색 기간 필터. 좁은 것부터 — 연월 > 연도 > 없음.

    Route 는 연도만 들고 있지만 랭킹을 가르는 것은 연월이다.
    Task 0 실측: corp 필터 + 연도만으로는 골든이 top-5 밖이고, 연월 prefix 를 걸면 1위다.
    연도가 둘 이상이면 접두 하나로 표현할 수 없으므로 기간 필터를 걸지 않는다.

    **코퍼스 밖 연도만 지목한 질의에는 기간 필터를 걸지 않는다.** 여기 오는 그런 질의는
    라우터의 상대 시점 면제(`_RELATIVE_HINT`)로 살아 온 것뿐이고(`"2021년 대비 최근
    매출액 변화"`), 2021 을 필터로 걸면 hop 0 이 확정적으로 0건이 되어 **면제가 아무것도
    사지 못한다.** 기준점 연도는 검색 조건이 아니라 비교의 출발점이다.
    """
    if _all_years_out_of_corpus(r):
        return {}
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


# --- 정기공시 사업연도 축 ------------------------------------------------------
# `plan_section_hops` 는 `report_hints` 가 사업연도를 못 뽑았을 때 **질문의 연도가
# 하나면 그것을 사업연도로 추정**한다 (query.py `str(years[0])`). 그 폴백이 정정
# 질의에서 정확히 반대로 간다 — 접수·정정 **일자**의 연도를 사업연도로 잡는다.
#
# 실측 Q-076 ("기아 제80기 사업보고서는 2024년 3월 12일 정정됐다"):
#   `report_hints` 는 옳게 사업연도를 비운다 (`_REPORT_YEAR` 는 '보고서 바로 앞' 만 본다).
#   그런데 `r.years == [2024]` 라 폴백이 `base_year=2024` 를 만들고, 제80기는 사업연도
#   **2023** 이라 골든 2건이 필터에서 통째로 빠졌다 (실측: hop 0~3 어디에도 안 들어온다).
#   폴백을 끄면 같은 hop 0 에서 골든이 **1·2위**다.
#
# 🔴 `report_hints` 가 준 사업연도는 건드리지 않는다. 그쪽은 '보고서' 표기에 붙은 연도라
# 권위값이고, 폴백만 추정이다. 이 구분이 Q-100 을 지킨다 — "크래프톤 2025년 사업보고서는
# 2026년 5월 15일 정정됐다" 는 폴백이 아니라 `report_hints` 로 2025 를 얻으므로 불변이다.
# (`_FILED` 자체에 '정정' 을 넣는 대안은 **기각**했다: 그러면 사업연도가 접수일 접두로
#  대체되어 원공시와 정정본이 다른 달에 접수된 Q-100 이 골든 2/2 -> 1/2 로 깨진다.)
#: 접수·정정 **일자**로 적힌 연월. 이 연도는 사업연도 축이 아니다.
_FILED_EVENT = re.compile(r"20\d{2}\s*년\s*(?:1[0-2]|0?[1-9])\s*월"
                          r"(?:\s*\d{1,2}\s*일)?\s*(?:에\s*)?"
                          r"(?:제출|공시|접수|정정|수정)")


def _fiscal_years(r, question: str) -> tuple:
    """`plan_section_hops` 의 사업연도 **폴백**에 넘길 연도.

    질문의 연도가 제출·정정 일자로 적혀 있으면 폴백을 끈다 — 그건 접수 시점이지
    사업연도가 아니다. 폴백이 아닌 축(`report_hints`)은 그대로 살아 있다.
    """
    if _FILED_EVENT.search(question or ""):
        return ()
    return tuple(r.years)


# --- 정기공시 다문서 질의 분해 --------------------------------------------------
# `report_hints` 는 축마다 **스칼라 하나**를 돌려주고 `plan_section_hops` 가 그것을
# 곱해 필터로 쓴다. 질문이 보고서를 여러 개 부르면 그 곱이 **아무 것도 안 가리킨다**.
#
# 실측 Q-105 ("2025년 반기보고서, 2025년 3분기 분기보고서, 2024년 사업보고서"):
#   `doc_subtype` 은 세 번째 언급('사업보고서')에서, `base_year` 는 첫 번째 언급
#   ('2025년 반기보고서')에서 와서 필터가 `annual + 2025` 가 됐다. 질문이 부르지도 않은
#   문서(20260323001553 = 사업연도 2025 사업보고서, 2026-03 접수) 하나가 top-8 을 다
#   채우고 근거 4칸을 전부 먹었다 → 골든 0/3, 근거 1,182자, 답변은 "확인되지 않습니다".
#
# 그래서 **부른 보고서마다 셀 하나**로 쪼갠다. 거래소공시 차선이 이미 같은 일을 한다
# (`time_points`·`_cell_quota`) — 제동 셋도 그대로 옮긴다:
#   ① 셀이 **2개 이상일 때만** 쪼갠다. 실측 발화는 dev+회귀 전량에서 Q-105 **1문항**이다.
#   ② 셀마다 할당량(`_report_quota`) — 셀마다 top-k 를 담으면 근거가 셀 수만큼 부푼다.
#   ③ **hop 0 만** 쪼개고 셀마다 **1순위 경로만** 본다 — 스텝 상한 8을 지킨다 (3셀 = 3스텝).
#
# 🔴 접수일 접두(`plan.prefix`)가 있으면 쪼개지 않는다. 그때는 `plan.filters` 가 비고
# 접수일이 문서를 못박으므로 셀과 곱하면 오히려 좁아진다. 현 코퍼스 발화 0건이라
# 실측으로 고를 수 없는 조합이고, 안 쪼개는 쪽이 종전 동작이다.

#: 질문이 **이름을 부른 보고서**. 연도가 보고서 표기에 붙어 있을 때만 짝으로 인정한다.
_REPORT_CELL = re.compile(
    r"(20\d{2})\s*년?\s*(?:도)?\s*(?:\d\s*분기\s*)?(사업|반기|분기)\s*보고서")
_CELL_SUBTYPE = {"사업": "annual", "반기": "half", "분기": "quarter"}


def _report_cells(question: str) -> list[dict]:
    """질문이 부른 (보고서 종류 × 사업연도) 짝 전부. 중복은 접는다."""
    cells: list[dict] = []
    for match in _REPORT_CELL.finditer(question or ""):
        cell = {"doc_subtype": _CELL_SUBTYPE[match.group(2)],
                "base_year": match.group(1)}
        if cell not in cells:
            cells.append(cell)
    return cells


def _report_quota(n_cells: int, n_corps: int) -> int:
    """분해 셀 하나가 가져갈 근거 칸. 분해가 없으면 종전대로 상한을 다 준다."""
    if n_cells < 2:
        return MAX_SECTION_EVIDENCE
    return max(1, MAX_SECTION_EVIDENCE // (n_cells * max(n_corps, 1)))


def _out_of_corpus_period(r, question: str = "") -> bool:
    """재검색을 닫을 것인가. **술어를 라우터와 공유한다** (`router.out_of_corpus`).

    🔴 갈라 두면 라우터가 살려 보낸 질의를 검색 단계가 다시 죽인다. 라우터는 T5 에서
    상대 시점 면제를 넣고 주석으로 *"'2021년 대비 최근' 처럼 기준점만 범위 밖인 질의를
    죽이면 답할 수 있는 걸 못 답한다"* 고 약속했는데, 여기가 `r.years` 만 보고 질문을
    안 봐서 그 약속이 코드 전체로는 성립하지 않았다. 실측(수정 전):

        "2021년 대비 삼성전자의 최근 매출액 변화를 정리해줘"
          route=complex_reason  router_block=False  pipeline_block=True
          hop 1~3 · 이벤트 폴백 전부 차단 → 근거 0건 → UNANSWERABLE

    현 코호트(dev 78 + 회귀 10 + 밸류체인 12) 발화는 **0건**이다 — 라우터를 통과하고
    여기서 막히는 문항이 하나도 없어 이 수정은 기록된 측정을 바꾸지 않는다. 그러나
    대회 문항은 미공개이고 `"X년 대비 최근"` 은 흔한 어형이라 남겨 둘 위험이 아니다.
    """
    return router_module.out_of_corpus(r.years, question)


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

    🔴 **이 판정이 보는 것은 절단 전 `picked` 전량이다.** 프롬프트에 가는 것은
    `picked[:MAX_EVIDENCE]` 이고, 그 앞에 `chain_ids` 정렬이 순서를 바꾼다 — 즉 절단으로
    버려지는 것은 '도착 순서의 꼬리' 가 아니라 '체인이 아닌 것들' 이다. `missing_field` 를
    해소해 `sufficient` 를 만든 문서가 체인 밖이면 그 문서는 절단될 수 있고 판정은 이미
    끝났다. 도달 가능성은 실재한다 — 셀 할당량이 `len(picked) >= MAX_EVIDENCE` 를 셀 루프
    **진입 시점에만** 보므로 `picked` 는 상한을 넘고, 여기에 체인 최대 6건이 더 붙는다
    (Q-113 실측 14블록).

    판정을 절단·정렬 이후 집합으로 옮기는 것이 옳은 방향이지만 그건 **판정 결과를 바꿔
    88문항 재측정이 붙는다.** 마감 전 재측정 슬롯이 없어 지금은 보증의 범위를 여기 적어
    둔다 — `verdict=sufficient` 는 "프롬프트가 본 근거로 충분하다" 가 **아니라** "수집한
    근거 전량으로 충분하다" 는 뜻이다. Phase H 가 GENERATION 귀속의 근거로 쓸 때 이
    간극을 세어야 한다.
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
    if entry.get("kind") == "terminated":
        # 🔴 방향이 반대다 — 이건 **앞에 있던 원계약**이지 뒤에 온 공시가 아니다.
        # 라벨을 `followup` 과 같이 쓰면 모델이 원계약 금액 자리에 해지 금액을 답한다
        # (Q-066 의 v2 A8 오답 362,307,038,000 vs 골든 351,882,914,000).
        # `대체됨`·`최신본` 을 **쓰지 않는다** — `critic._STALE_NOTE`/`_LATEST_NOTE` 가
        # 그 두 낱말로 체인 위치를 읽으므로 해지 관계가 정정 관계로 오독된다.
        return "- 정정체인: 해지 대상 원계약 (이 계약을 해지한 공시가 ※관련공시로 지목)"
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


#: 질문이 **해지가 가리키는 원계약**을 묻고 있는가. 제동 ②' 의 어휘 축.
#: 이 목록만으로는 아무것도 안 열린다 — `terminates` 를 가진 문서(코퍼스 전체 **11건**)가
#: 상위 후보에 있어야 비로소 걸린다. 그래서 넉넉히 적어도 과잉 발화가 안 생긴다.
_TERMINATION_ORIGIN_HINT = ("원래", "원계약", "원공시", "최초", "가리키는")

#: 🔴 **A33 — 정정본에서 원공시로 되돌아가야 하는 질의.** v5 오답 `Q-113`.
#:
#: `_TERMINATION_ORIGIN_HINT`(해지 → 원계약)와 **축이 같고 대상이 다르다** — 저쪽은
#: 해지 공시에서 원계약으로, 이쪽은 정정본에서 원공시로 간다. 낱말을 공유하지 않고
#: 따로 두는 이유는 v3 R2 ④ 가 실측으로 좁힌 그 예외를 **넓히지 않기** 위해서다.
#: 저쪽 목록의 `최초`·`원래` 는 단독 낱말이라 이쪽에 쓰면 발화가 넓어진다.
#:
#: 🔴 **원공시를 명시적으로 지목하는 표현만 넣는다.** `얼마나 늘었`·`어떻게 바뀌` 같은
#: 변동 서술을 넣으면 발화가 4 → 7 로 늘고(R0 실측), 늘어난 셋(`Q-018`·`Q-023`·`Q-081`)
#: 은 근거를 이미 다 가진 문항이라 **얻는 것 없이 희석만 는다.**
#:
#: R0(2026-08-16) 실측 — 평가셋 112문항에서 발화 4 · 그중 씨앗이 실제로 열리는 것 3:
#:     `Q-113` 씨앗 5개(겨냥) · `Q-061` 1개 · `Q-069` 1개 · `Q-094` **0개**(근거 불변)
_REVISION_ORIGIN_HINT = ("최초 공시", "최초공시", "원공시", "원 공시", "당초",
                         "정정 전", "정정전")


#: 🔴 **A33 전용 씨앗 창.** `CHAIN_SEED_WINDOW`(2)를 **이 플래그 뒤에서만** 넓힌다.
#:
#: 첫 측정(2026-08-16)이 씨앗 조건만 고쳐서는 `Q-113` 이 안 열린다는 것을 보여 줬다 —
#: 상위 후보 둘이 **전부 삼성중공업**이라 삼성E&A 의 정정본(`20250924800260`)이
#: **6번째**에 있어 탐침조차 안 됐다. 두 기업을 비교하는 질의에서 전역 창 2 는
#: 구조적으로 두 번째 기업에 닿지 못한다.
#:
#: 🔴 **`CHAIN_SEED_WINDOW` 자체는 안 건드린다.** 그 상수는 제동 ① 이고 모든 질의에
#: 걸린다. 여기서 넓히는 것은 `_asks_original` 이 참인 **4문항뿐**이다 (R0 실측).
REVERSE_SEED_WINDOW = 6


def _asks_original(question: str) -> bool:
    """질문이 **정정 전 값**을 요구하는가. LLM 0회 · 검색 0회."""
    return any(h in (question or "") for h in _REVISION_ORIGIN_HINT)


def _chain_seeds(picked: list[dict], question: str = "") -> list[dict]:
    """체인을 밟을 씨앗. 제동 ①② — 상위 후보 안의 **대체본**, 그리고 조건부로 **해지 공시**.

    `tools.chain_probe` 는 도구 호출이 아니라 레코드 조회라 스텝을 쓰지 않는다.
    이게 없으면 링크가 없는 문서에도 `get_history` 를 걸어 스텝만 태운다.

    ## 🔴 v3 R2 ④ — 제동 ② 에 **좁은 예외 하나**를 낸다

    제동 ② 는 *"씨앗은 대체본(`is_latest=False`)만"* 이고 실측으로 정해졌다 — 최신본을
    씨앗으로 받으면 `Q-008`·`Q-031`·`Q-033`·`Q-034`(전부 현재 정답)에 근거가 붙는다.
    **해지 공시는 전건 `is_latest=True`** 라 이 제동에 걸리고, 그래서 회귀 `Q-066` 은
    해지에서 출발하는데 런타임 대체 경로가 **없었다**(W4 진단).

    `corrections.attach_related` 독스트링이 확장 전에 밟으라고 못박은 절차(7문항 근거
    조립 diff · LLM 0회 · `1d008f0`)를 **먼저 밟았다.** 세 안을 갈라 쟀다
    (분모: dev 78 + 회귀 10 + 밸류체인 12 = 100문항 · 골든 179):

    | 안 | 골든 | 근거 바뀐 문항 | 보호 4문항 중 바뀜 | 잃은 골든 |
    |---|---|---|---|---|
    | ⓐ `terminates` 보유면 무조건 | 160→**161** | 6 | **4** | 0 |
    | ⓒ ⓐ + 질문이 원계약을 물을 때만 | 160→**161** | **2** | **1** | 0 |
    | ⓑ `_followup_index` 키까지 (= 계획서 `A9`) | 160→**161** | 7 | **4** | 0 |

    🔴 **셋의 골든이 같다.** `A9` 의 일반형(ⓑ)이 `terminates` 하나보다 더 회수하는 것이
    **0건**이다 — `_followup_index` 씨앗이 늘리는 것은 `Q-035` 의 근거 18자뿐이다.
    그러면서 근거를 바꾸는 문항은 가장 많다. **얻는 것 없이 희석만 늘린다.**

    ⓐ 와 ⓒ 도 골든이 같은데 **바꾸는 범위가 3배 다르다.** ⓐ 는 `Q-031`(해지 금액을
    묻는다)에 원계약 3건 +1,127자, `Q-034`(해지 금액 합계를 묻는다)에 +1,186자를 얹는다.
    `corrections.attach_related` 가 경고한 사고(*"원계약 금액을 근거에 명시하면 오답을
    굳힌다"*)의 **거울상**이다 — 이번엔 해지 금액을 묻는 문항에 원계약 금액이 붙는다.
    `ORACLE` 이 이미 실측한 것도 같은 방향이다 — 🔴 **R2E 재측정 후 그 증거가 더 세졌다**:
    오라클과 `A8` 의 정오가 갈린 **7문항 전수**에서 골든 회수가 같고 오라클 근거만 더
    작았다(고친 2건 · 깨뜨린 5건). 즉 움직인 것은 회수량이 아니라 **희석의 양**이다.
    (R1 시점 서술은 *"고친 4건 중 3건이 근거 희석 제거"* 였다 — 세대가 다른 값이다.)

    **그래서 ⓒ 를 채택한다.** 예외를 여는 조건이 둘이다 — 문서 쪽으로 `terminates` 가
    있을 것(코퍼스 전체 11건), 질문 쪽으로 **원계약을 묻고 있을 것**. 남는 변화는
    `Q-066`(1/2 → 2/2, 겨냥)과 `Q-033`(1/1 유지, +324자)뿐이고, `Q-033` 은 질문 자체가
    *"원래 계약기간"* 을 요구하므로 붙는 블록이 그 질문에 응답하는 문서다.

    ⓐ·ⓑ 를 기각한 것이 이 라운드의 결과다 — **기각도 결과다.** `L4` 가 실측한
    *"`REFERS_TO` 1,267엣지의 발화 기회가 100문항에서 1건"* 은 그대로 남고, 그 원인인
    씨앗 조건도 그대로 남는다. **그 막음에 값이 있다**는 것이 이번 측정의 뜻이다.
    """
    asks_origin = any(h in (question or "") for h in _TERMINATION_ORIGIN_HINT)
    # 🔴 A33 — 정정본에서 원공시로. 플래그가 꺼져 있으면 `wants_prior` 가 항상 False 라
    #    아래 조건식은 종전과 **논리적으로 동치**다 (`or False` 는 항등원).
    wants_prior = ablation.enabled("reverse_chain") and _asks_original(question)
    window = REVERSE_SEED_WINDOW if wants_prior else CHAIN_SEED_WINDOW
    seeds = []
    for item in picked[:window]:
        probe = tools.chain_probe(item.get("doc_id"))
        if not probe or not probe["links"]:
            continue
        if probe["is_latest"] and not (
                (asks_origin and probe.get("terminates")) or wants_prior):
            continue                      # 제동 ② — 예외는 위 두 갈래 중 하나일 때만
        seeds.append({**probe, "corp": item.get("corp")})
    return _round_robin_by_corp(seeds) if wants_prior else seeds


def _round_robin_by_corp(seeds: list[dict]) -> list[dict]:
    """기업별로 한 바퀴씩 돌게 씨앗을 다시 세운다. **`A33` 에서만 부른다.**

    🔴 **창을 넓히는 것만으로는 부족했다** (2026-08-16 실측). `Q-113` 에서 씨앗은
    열렸는데 담긴 문서가 0건이었다:

        hop=5 chain corp="삼성중공업" rcept=20250924800262 n=1   ← 골든
        hop=5 chain corp="삼성중공업" rcept=20250925800330 n=2   ← 무관한 체인
        hop=5 chain corp="삼성중공업" rcept=20250930800629 n=3   ← 무관한 체인
        hop=5 chain corp="삼성E&A"   rcept=20250924800260 n=0   ← 🔴 골든인데 0건

    `MAX_CHAIN_DOCS`(6)를 앞의 셋이 1+2+3 으로 **다 먹었다.** 두 기업을 비교하는
    질의에서 선착순 배분은 첫 기업이 상한을 독식한다 — `_partner_quota` 가 발주처
    차선에서 이미 만난 문제와 같은 모양이고, 처방도 같다.

    🔴 **상한을 안 올린다.** `MAX_CHAIN_DOCS` 는 제동 ③ 이고, 한화오션 잠수함 체인
    7건이 근거 칸을 혼자 먹는 것을 막는 상수다. 여기서 바꾸는 것은 **배분 순서뿐**이다.
    """
    by_corp: dict[str, list[dict]] = {}
    for seed in seeds:
        by_corp.setdefault(seed.get("corp") or "", []).append(seed)
    out: list[dict] = []
    for depth in range(max((len(v) for v in by_corp.values()), default=0)):
        for members in by_corp.values():        # 삽입 순서 = 후보 순서
            if depth < len(members):
                out.append(members[depth])
    return out


def _expand_chain(picked: list[dict], seen: set, budget: Budget,
                  trace: list[str], take, as_of: str = "",
                  question: str = "") -> tuple[list[str], set]:
    """정정·후속 공시 체인을 근거에 넣는다. LLM 0회.

    돌려주는 값은 (닫지 못한 링크, 체인에 속한 doc_id). 앞엣것은 충분성 판정이
    '집합 미완결' 로 읽고, 뒤엣것은 근거 절단에서 체인을 먼저 살리는 데 쓴다.

    `question` 은 **씨앗 조건에만** 쓴다 (`_chain_seeds` 제동 ②'). 비어 있으면 종전
    조건 그대로다 — 해지 예외가 안 열린다.
    """
    gap: list[str] = []
    chain_ids: set = set()
    added = 0
    for seed in _chain_seeds(picked, question):
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
        # `followup_hop` 은 이 hop 안에서만 읽힌다 — `L4` 가 `※관련공시` 후속공시만 떼어
        # `L1` 에 섞여 있던 두 엣지를 가른다 (`ablation` 머리말 §followup_hop).
        out = tools.execute("get_history",
                            {"rcept_no": seed["rcept_no"],
                             "include_followups": ablation.enabled("followup_hop")})
        if "error" in out:
            trace.append(f'hop={CHAIN_HOP} strat=chain tool=get_history '
                         f'corp="{seed["corp"] or "-"}" skip=tool_error '
                         f'건너뜀({out["error"]})')
            gap += missing
            continue
        # `chain` 다음에 `followups`(뒤에 온 사안) · `terminated`(이 공시가 해지한
        # 원계약). 셋 다 `kind` 로 갈려 있고 `_chain_note` 가 각자 다른 줄을 찍는다.
        entries = (list(out.get("chain") or []) + list(out.get("followups") or [])
                   + list(out.get("terminated") or []))
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
#   ④ 스텝을 먼저 안 쓴다     — 검색 사다리가 **다 돈 뒤에** 호출한다. 처음 판에서는 검색
#                              루프 앞에 뒀는데, 공용 `Budget` 을 먼저 한 칸 쓰는 바람에
#                              스텝을 많이 쓰는 질의(3기업 × 4단)가 `stop=step_budget` 으로
#                              잘렸다. 근거·판정·씨앗을 다 갈라 놓고도 **예산 축 하나가
#                              남아 있었다.** 뒤로 옮기면 그 축까지 닫힌다.
#
# 네 축을 다 닫아야 **검색 차선이 발주처 hop 유무와 무관하게 같다**고 말할 수 있다.
# (③ 까지만 닫았을 때는 참이 아니었다 — 근거·판정·씨앗은 같아도 스텝이 달랐다.)
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

    깊이는 **폭을 넘지 않는다** (`min(n_partners, …)`). 열거할 것이 없는데 최대치를
    가져가면 그건 열거가 아니라 한 기업 레코드 나열이다. 이게 없으면 파트너 1인 노드가
    5칸을 가져가는데, 그런 노드가 **145개 중 120개(83%)** 다 (SATORP · 삼천리이에스 ·
    SK하이닉스 …). 지금 코호트에는 파트너 1인 노드로 발화하는 문항이 없어 피해가 0이지만,
    실측 근거가 없는 값은 남겨 두지 않는다.

    더 깊이 파지 않는 이유도 실측이다. 파트너가 둘뿐인 노드(삼성전자 66건 → 삼성E&A ·
    삼성중공업)에서 quota 를 2 → 3 으로 늘려도 골든 적중은 그대로고(Q-114 1/5),
    5 로 늘리면 +1(2/5) 이지만 그 1건 값으로 **지금 맞히는 문항**에 무관 레코드가
    실린다 — 밸류체인 전수 실측에서 근거 블록이 105 → 118 로 늘고 그 증가분은
    Q-108(+2)·Q-113(+5) 처럼 골든이 0건 늘어난 문항에 쏠린다. 8 까지 늘리면 4/5 지만
    한 문항에 16블록이라 선을 한참 넘는다. 열거에 필요한 만큼만 담는다.
    """
    width = max(n_partners, 1)
    return max(1, min(width, MAX_EVIDENCE // 2 // width))


def _lane_scores(question: str, records: list[dict]) -> list[float]:
    """레인 **안에서만** 매기는 질문 적합도. LLM 0회 · 검색 0회 · 네트워크 0회.

    모집단이 그 레인(같은 발주처 · 같은 파트너)뿐이라 IDF 가 레인 안에서 계산된다 —
    그 레인 전원이 가진 토큰(`계약상대`·발주처 이름·서식명)은 IDF 가 0 에 수렴해
    **순서를 못 바꾸고**, 일부에만 있는 토큰만 순서를 가른다. 길이 정규화도 BM25 가
    이미 갖고 있다. 즉 우리가 손으로 고를 뻔한 두 가지(불용어·길이)를 모집단 정의가
    대신 정한다.

    🔴 **토크나이저는 `tokenize_words`(어절)다 — 색인의 `tokenize`(어절 + bigram)가
    아니다.** 색인과 다른 것을 쓰는 것은 이 저장소가 경계하는 일이라 실측으로 정했다
    (밸류체인 골든 46 기준, LLM 0회):

        어절 중복       28/46 (불변)      어절 BM25       **29/46**
        bigram 중복     27/46 (−1)        bigram BM25     28/46 (불변)
        제목만 BM25     28/46 (불변)

    bigram 이 지는 이유는 같은 서식 레코드끼리의 비교라서다 — `단일판매ㆍ공급계약체결`
    한 낱말이 bigram 6개로 풀려 **전원 공통 토큰이 6배로 불어난다.** 부분일치를 살리려고
    넣은 장치가 여기서는 신호를 희석한다. 색인(모집단 1,469건)과 레인(모집단 1~47건)은
    모집단 크기가 두 자릿수 다르므로 같은 토크나이저가 최선이라는 보장이 없다.

    빈 레인·빈 본문에서는 `BM25Okapi` 가 0으로 나눈다. 그 경우 전부 0점을 돌려주고
    호출부가 종전 순서를 그대로 쓰게 한다 — 정렬이 죽어도 파이프라인은 산다.
    """
    corpus = [bm25_module.tokenize_words(r.get("markdown") or "") for r in records]
    if len(corpus) < 2 or not any(corpus):
        return [0.0] * len(records)
    from rank_bm25 import BM25Okapi          # 색인이 이미 쓰는 의존이다
    return list(BM25Okapi(corpus).get_scores(bm25_module.tokenize_words(question)))


def _partner_order(records: list[dict], question: str = "") -> list[dict]:
    """파트너 안에서의 순서. **최신본 먼저, 그 다음 질문 적합도, 그 다음 도착 순서**다.

    최신본을 앞에 두는 것은 그대로다 — 대체된 공시로 파트너를 대표시키면 열거가 옛
    값을 말한다 (`query_events` 가 `is_latest=True` 를 기본으로 두는 것과 같은 이유다).
    실측(밸류체인 9문항, 파트너당 1건): 도착 순서 9건 → 최신본 우선 11건.
    금액 내림차순(12건)·접수일 내림차순(13건)이 합계로는 높지만 둘 다 fan-in 문항
    Q-110 에서 2/6 이라 버렸다 — 이 hop 의 목적은 총합이 아니라 열거다.

    🔴 **v3 R2 — 둘째 키를 질문 적합도로 바꿨다. 종전에는 이 함수가 질문을 안 봤다.**
    같은 허브·같은 파트너면 어떤 질문이 와도 순서가 같았고, 그래서 밸류체인 미회수
    18건 중 **8건**이 "허브 레인 절단"(`T5-a` (iii′))으로 잘렸다 — 골든이 레인
    2·4·5·6·7·11·38번째에 있는데 `_partner_quota` 는 1~2 만 준다.

    🔴 **상한을 올리는 것은 처방이 아니다.** `T5-a` §4 가 재현 시뮬레이션으로 쟀다 —
    8건을 다 회수하려면 한 문항에 발주처 근거 **57블록**(현행 6칸의 9.5배)이 필요하고,
    `MAX_COUNTERPARTY_EVIDENCE=6` 이 quota 보다 먼저 물려서 파트너 6인 노드는 quota 만
    올려도 레인 2번째 이후가 **영원히 도달 불가**다. 같은 6칸으로 **더 맞는 것**을 담는다.

    🔴 **리랭커가 아니다.** `A3`(CLOVA 리랭커)는 겨냥 0 으로 최종 기각됐다(`05 §E-4b`).
    이것은 크레딧 0원 · LLM 0회 · 네트워크 0회의 정렬 로직 교체이고, 후보 집합도
    상한도 그대로다 — 바뀌는 것은 **같은 칸에 무엇이 들어가는가** 뿐이다.

    ## 🔴 겨냥 8건 중 실측 회수는 **1건**이다. 그 차이를 여기 적어 둔다

    `T5-a` §4 는 *"레인 안에서 질문 적합도로 정렬하면 필요 depth 가 4·6·7·11 에서
    1~2 로 내려온다"* 고 적었지만 그것은 **정렬이 완벽할 때의 상한**이고 반사실
    추정이었다 (계획서 자신이 '확신하지 못하는 것 ③' 으로 표시). 밸류체인 12문항에서
    `_gather_evidence` 를 직접 돌려 골든 46 회수를 셌다 (LLM 0회 · 0원):

        현행 (도착순)                        28/46
        질문 적합도 (채택)                    **29/46**   Q-107 +1
        🔴 정렬 상한 (골든을 보고 정렬)         33/46      Q-107 +1 · Q-110 +1 · Q-114 +3
        🔴 상한 + `is_latest` 키까지 해제       34/46      위 + Q-113 +1

    **정렬로는 8건에 닿을 수 없다 — 상한이 5건이다.** 나머지 3건은 정렬 밖에서 막힌다:
    `Q-107` 의 두 골든이 **같은 파트너 레인**에 있는데 quota=1 이고(둘 중 하나만 가능),
    `Q-113` 의 골든은 `is_latest=False` 인 정정 전 원공시라 1순위 키가 뒤로 민다.

    그리고 상한 5건 중 **4건은 질문 텍스트로 도달 불가능**하다. 실측 반례가 `Q-114` 다 —
    질문이 *"평택·기흥"* 을 말하는데 **골든 3건의 본문에는 그 낱말이 없고**(`[P3 PJT]
    그린동`·`[P5] 복합동`·`P4 Ph4(상동)`), 정작 `평택`·`기흥` 을 본문에 가진 것은
    비-골든 3건(`평택 V2L`·`기흥 신규 연구라인`·`기흥 연구라인 신축`)이다. 어떤 텍스트
    적합도 함수도 이 문항에서는 **틀린 쪽을 위로 올린다.** `Q-110` 은 반대 이유다 —
    질문이 *"상장사는 어디이며 각각 어떤 역할"* 이라 두산퓨얼셀 6건을 가를 표현이 질문에
    아예 없다. 골든 선택이 질문이 아니라 라벨러의 대표 1건 선정이었다.

    **그래서 이 처방은 채택하되 겨냥을 8 → 1 로 정정해 적는다.** 남은 4건은 정렬이
    아니라 **다중 허브 결속**(`Q-107`)·**정정 체인 hop**(`Q-113`)·**골든 정의**(`Q-114`
    ·`Q-110`) 쪽 문제다.

    `question` 이 비면 종전 동작 그대로다 (최신본 우선 + 도착순). 호출부가 질문을
    안 넘기는 경로가 생겨도 조용히 성능이 바뀌지 않는다.
    """
    if not question:
        return sorted(records, key=lambda r: not r.get("is_latest", True))
    scores = _lane_scores(question, records)
    return [record for _, record in
            sorted(enumerate(records),
                   key=lambda pair: (not pair[1].get("is_latest", True),
                                     -scores[pair[0]], pair[0]))]


def _counterparty_note(display: str, roster: str, n_partners: int) -> str:
    """근거 블록 안에 발주 관계를 박아 둔다. **별도 요약 블록으로 빼지 않는다** —
    요약 블록은 원문 없이도 '골든이 실렸다' 로 세어져 성적표를 부풀린다 (`_chain_note` 주석).

    접수번호를 한 자도 싣지 않는다. 그래서 이 줄은 근거 적중 집계를 흔들 수 없고,
    '상장사가 몇 곳인가' 처럼 어느 한 레코드에도 안 적혀 있는 사실만 전한다.

    🔴 **회사별 건수를 싣지 않는다.** 한때 `f"{corp} {len(lanes[corp])}건"` 이었고 그 수가
    실제로 답변에 나왔다 (기록: `artifacts/ablation/v2/vc/A8/responses/Q-114.json` —
    근거 `삼성E&A 47건`, 답변 `"삼성E&A: 총 47건의 계약을 통해 …"`). 두 겹으로 틀렸다.

      (a) `query_by_counterparty` 는 `is_latest` 로 걸러 주지 않는다 — 삼성E&A 47건 중
          **35건이 정정으로 대체된 공시**다(최신본 12건). 바로 위 `_partner_order` 가
          "대체된 공시로 파트너를 대표시키면 안 된다" 며 `is_latest` 를 올리는데,
          **정렬은 최신본을 아는데 집계는 몰랐다** — 한 함수 안에서 규칙이 갈렸다.
      (b) 질문 범위와 무관한 **전역 집계**다. Q-114 가 묻는 것은 평택·기흥 공장의 공정
          분담이고 근거에 실린 레코드는 4건인데, 47 은 그 어디에도 없는 수다.

    `is_latest` 로 걸러 '참인 수' 를 만드는 것으로는 안 고쳐진다 — (b) 의 범주 오류가
    남아 모델이 `"총 12건의 계약"` 으로 오보할 여지가 그대로다. 그래서 수를 **뺀다.**
    이 hop 의 mandate 는 개수가 아니라 **집합의 경계**('몇 곳 · 누구') 이고, 밸류체인
    12문항 중 건수를 묻는 문항은 **0개**다(전부 열거·비교). Q-116 의 '상장사 세 곳' 은
    파트너 수이고 그건 `n_partners` 로 옳게 전달된다.

    건수를 다시 넣고 싶어지면 먼저 **어느 문항이 그것을 요구하는지** 적어라. 그리고
    그 수는 `is_latest` 로 걸러야 하고, 질문 범위 밖 집계라는 사실을 라벨에 박아야 한다.
    """
    return f"- 발주처 관계: {display} 를 계약상대로 한 공시 상장사 {n_partners}곳 — {roster}"


#: 🔴 **`A22` — 결속할 허브 상한 `W`.** 종전은 `W=1`(질문이 지목한 허브 하나)이다.
#: 3 으로 못박은 이유는 실측이다 — 근거의 `계약상대:` 에서 나오는 후보가 밸류체인
#: 문항에서 5~11개까지 나오는데(그 자체가 `Q-117` 의 갈래 수다), 전부 결속하면 스텝
#: 상한 8을 먼저 먹고 근거가 두 배가 된다. 상한 없이 여는 것은 조작이 아니라 사고다.
MAX_COUNTERPARTY_HUBS = 3
#: 추가 허브 하나가 가져가는 근거 칸. 🔴 **첫 허브의 6칸을 나눠 쓰지 않는다** —
#: 나누면 파트너 6인 노드에서 몫이 1 밑으로 떨어져 fan-in 문항이 깨진다
#: (`_partner_quota` 독스트링의 Q-110 실측 2/6 → 5/6 이 그 값에 걸려 있다).
MAX_EXTRA_HUB_EVIDENCE = 2

#: 근거 블록의 `- 계약상대: …` 줄. `corpus/markdown.to_markdown` 이 `record["fields"]`
#: 로 찍은 것과 **같은 서식**이라 정규식으로 추측하는 것이 아니다.
_EVIDENCE_HUB_RE = re.compile(r"^-\s*계약상대\s*:\s*(.+?)\s*$", re.M)


def _evidence_hubs(picked: list[dict], primary: str | None,
                   companies: Sequence[str] = ()) -> list[str]:
    """🔴 `A22` — **이미 담은 근거**의 `계약상대:` 필드에서 추가 허브를 뽑는다.
    LLM 0회 · 검색 0회 · 스텝 0. canonical 이름을 **많이 나온 순 · 먼저 나온 순**으로.

    질문이 아니라 근거에서 뽑는 것이 이 함수의 전부다. 라우터의 역할 결속
    (`find_counterparty`)은 *질문 문장에서* 발주 방향을 읽는데, `R1-11 ①` 이 그 축을
    넓히는 안을 **오탐 3건으로 기각**했다 (`Q-108` 은 두 회사가 다 공시한 쪽이라 결속하면
    근거가 역방향으로 뒤집힌다). 근거의 `계약상대:` 는 **그 공시가 스스로 적은 상대**라
    방향이 이미 확정돼 있어 그 기각과 충돌하지 않는다.

    제동 셋:
      ① 그래프 노드가 아닌 이름은 버린다 — `query_by_counterparty` 가 2회 미만 등장
         상대를 거부하므로, 넘기면 스텝만 쓰고 `skip=tool_error` 가 된다
      ② 비실명(`classify == "vague"`)은 버린다 — 같은 이유이고 사유가 다르다
      ③ 🔴 `companies`(질문이 지목한 기업)는 버린다 — 그 회사는 이 질문에서 **공시한
         쪽**일 수 있고, 그것을 허브로 세우는 것이 `R1-11 ①` 이 기각한 바로 그 조작이다.
         질문이 지목한 허브(`primary`)는 라우터가 역할을 읽고 세운 것이라 예외다
    """
    from argus.corpus.counterparty import canonical, classify

    nodes = set(router_module._counterparty_nodes())
    blocked = {canonical(c) for c in companies if c}
    if primary:
        blocked.add(canonical(primary))
    counts: dict[str, int] = {}
    order: list[str] = []
    for item in picked:
        for raw in _EVIDENCE_HUB_RE.findall(item.get("markdown") or ""):
            if classify(raw) == "vague":
                continue
            key = canonical(raw)
            if key not in nodes or key in blocked:
                continue
            if key not in counts:
                order.append(key)
            counts[key] = counts.get(key, 0) + 1
    return sorted(order, key=lambda k: (-counts[k], order.index(k)))


def _gather_counterparty(hub: str, question: str, budget: Budget, trace: list[str],
                         take, limit: int = MAX_COUNTERPARTY_EVIDENCE,
                         prefer: Sequence[str] = ()) -> int:
    """발주처 그래프에서 파트너별 근거를 담는다. LLM 0회 · 스텝 1.

    `plan_hops` 사다리 **밖의 스텝 1개**다 (정정 체인 hop 과 같은 선례) — 재검색
    예산(hop 3)을 쓰지 않는다. 그래프 조회는 질의 재구성이 아니라 인접 노드 열거라서
    '넓히기' 사다리의 칸을 차지할 이유가 없다.

    `question` 은 **레인 안 정렬에만** 쓴다 (`_partner_order`). 후보 집합도 파트너
    목록도 질문과 무관하게 그래프가 정한다 — 질문이 바꾸는 것은 순서뿐이다.

    🔴 `limit`·`prefer` 는 **`A22`(다중 허브) 전용 인자**다. 기본값이 종전 동작이고,
    `W=1` arm 에서는 두 인자가 아무것도 안 바꾼다 (`limit` 은 종전 상한 그대로,
    `prefer` 가 비면 파트너 순서가 그래프 순서 그대로다).
    `prefer` 를 두는 이유는 실측 구조다 — 추가 허브는 칸이 2개뿐이라 파트너를 그래프
    순서로 담으면 **질문이 부른 기업이 그 둘 안에 들어온다는 보장이 없다.** 추가 허브는
    애초에 *그 기업들의 근거*에서 나온 것이므로 그 기업으로 되돌아가는 것이 정의에 맞다.
    """
    try:
        budget.spend_step()
    except BudgetExceeded as exc:
        trace.append(f"hop={COUNTERPARTY_HOP} stop=step_budget ({exc})")
        return 0
    # 🔴 도구에 넘기는 이름은 **원문 표기**다. `hub` 는 canonical(공백·`·`·`,`·`.`
    # 제거 + 소문자)이고, `query_by_counterparty` 는 그 값을 `classify()` 에 넣는데
    # `ANON_COUNTERPARTY` 의 여러 대안이 **공백 경계에 의존한다** (`지역\s*선주` ·
    # `(?:^|\s)글로벌(?:\s|$)`). 실측: `classify('글로벌 기업')=vague` 인데
    # `classify(canonical('글로벌 기업'))='other'` — canonical 을 되먹이면 비실명 가드가
    # 빗나간다. 그래프 145 노드 전량 대조로 `canonical(display) == canonical` ·
    # `classify(display) != vague` 를 확인했으므로 조회 결과는 글자 하나 안 바뀐다.
    name = router_module.counterparty_display(hub) or hub
    out = tools.execute("query_by_counterparty", {"name": name})
    if "error" in out:
        trace.append(f'hop={COUNTERPARTY_HOP} strat=counterparty '
                     f'tool=query_by_counterparty via={hub} '
                     f'skip=tool_error 건너뜀({out["error"]})')
        return 0

    partners = list(out.get("partners") or [])
    if prefer:
        wanted = [c for c in partners if c in set(prefer)]
        partners = wanted + [c for c in partners if c not in set(wanted)]
    by_partner: dict[str, list[dict]] = {}
    for record in out.get("records") or []:
        by_partner.setdefault(record.get("corp_name"), []).append(record)
    lanes = {corp: _partner_order(by_partner.get(corp, []), question)
             for corp in partners}
    # 회사명만. **건수를 붙이지 않는다** — 사유는 `_counterparty_note` 독스트링에 있다
    # (정정본 포함 전역 집계가 답변에 `"총 47건"` 으로 새어 나간 기록이 있다).
    roster = " · ".join(partners)
    note = _counterparty_note(out.get("counterparty") or hub, roster, len(partners))

    taken: list[str] = []
    for depth in range(_partner_quota(len(partners))):
        for corp in partners:
            if len(taken) >= limit:
                break
            items = lanes[corp]
            if depth >= len(items):
                continue
            record = items[depth]
            if take(record.get("doc_id"), _annotate(record.get("markdown", ""), note),
                    None, corp):
                taken.append(record["doc_id"])

    trace.append(f'hop={COUNTERPARTY_HOP} strat=counterparty tool=query_by_counterparty '
                 f'via={hub} partners={len(partners)} '
                 f'corp="{",".join(partners) or "-"}" n={len(taken)} top_score=- '
                 f'ids={",".join(taken) or "-"}')
    return len(taken)


# --- 열거 hop (`EVENTS_HOP` strat=enumerate) — v3 R2 ③ --------------------------
# `Q-117` 5건의 처방이다. `router.py` 262–296 이 그 처방을 **여기로 재정의**해 뒀다 —
# 원안(`total_plan §5 #4`)은 "노드 이름 인식" 이었는데 라우터가 명시적으로 기각했다:
# 한전기술은 그 질문에서 **공시한 쪽**이고, 결속시키면 근거가 두산퓨얼셀 4건(한전기술을
# *계약상대로* 한 공시)으로 **뒤집힌다.** 즉 hop 이 안 터진 것은 결함이 아니라 정상이고,
# `tests/test_router.py::test_disclosing_company_is_never_bound_as_counterparty` 가
# 그 불변을 지킨다. **여기서도 그 불변을 깨지 않는다.**
#
# 실제 기전은 열거다. 기록된 A8 궤적은 `hop=0 strat=narrow top_k=5` 로 5블록을 받고
# `verdict=sufficient evidence=5` 에서 멈췄고(`MIN_EVIDENCE["closed_lookup"]=1`),
# 그 5블록에 발전 공기업만 있어서 모델이 둘째 갈래를 **'공공부문' 으로 지어냈다.**
# hop 사다리는 같은 질의를 **넓힐** 뿐 **열거하지 않는다.**
#
# 🔴 과잉 발화가 진짜 위험이다 — 이 hop 은 그리드 72 의 검색 경로에 닿는다(발주처 hop 과
# 달리 밸류체인 전용이 아니다). 그래서 제동 다섯을 실측으로 정했다.
#
#   ① 문서군이 **exchange** 일 것        — `query_events` 모집단이 거래소공시다 (§7.7 ②).
#                                          기존 폴백에 건 것과 같은 제동
#   ② 기업이 **정확히 하나**일 것         — 열거의 모집단은 '한 기업의 한 서식' 이다.
#                                          둘 이상이면 어느 쪽을 열거할지 정의되지 않는다
#   ③ 그 기업이 **발주처가 아닐 것**      — `Q-114` 가 이 경계다. 삼성전자는 그 질문에서
#                                          **발주한 쪽**이라 `query_events(corp_name=삼성전자)`
#                                          는 정반대 문서(삼성전자 자신의 공시)를 준다
#   ④ 질문이 **갈래를 묻고 있을 것**      — 아래 `_ENUMERATION_HINT`
#   ⑤ 검색이 이미 뭔가 찾았을 것          — 아무것도 못 찾은 경우는 아래 **기존 폴백** 몫이다.
#                                          둘을 배타로 두면 `parse_hops` 가 같은 hop 번호에서
#                                          두 종류를 한 레코드로 섞어 읽지 않는다
#
# 실측 발화 (LLM 0회 · dev 78 + 회귀 10 + 밸류체인 12 = 100문항): **1건 — `Q-117` 뿐**이다.
# `_ENUMERATION_HINT` 만으로는 11문항이 걸리는데(`각각` 이 넓다) ①②③ 이 10건을 거른다.

#: 갈래를 묻는 표지. `각각`·`모두` 는 **넣지 않았다** — 100문항에서 9건이 걸리고 그중
#: 8건이 열거 질의가 아니다 (`Q-085` `Q-089` `Q-090` `Q-105` 등 단순 병렬 조회).
#: 이 셋은 "집합이 몇 갈래로 나뉘는가" 를 묻는 표현만 남긴 것이다.
_ENUMERATION_HINT = ("갈래", "나뉘", "나뉜", "유형")

#: 열거가 담을 상한. `MAX_EVIDENCE` 의 절반이다 — 열거가 검색 근거를 밀어내지 못하도록
#: `_take` 뒤에 **붙기만** 하고(절단은 `picked[:MAX_EVIDENCE]` 가 한다), 그 절반을 넘지
#: 않는다. 4·5·6 을 다 재 봤고 `Q-117` 회수는 셋 다 같다(4/7) — 값이 결과를 안 바꾼다.
MAX_ENUM_EVIDENCE = MAX_EVIDENCE // 2

#: 그룹핑에 필요한 후보 풀. **프롬프트에 실리는 수가 아니다** — 발주처별 그룹 크기와
#: 각 그룹의 최신본을 알아야 하므로 모집단 전체가 필요하다. `query_events` 의 기본
#: 20건으로는 `Q-117` 의 골든 3건이 잘린다 (한전기술 최신본 27건 중 21번째 이후).
ENUM_EVENT_POOL = 200

#: 질문 어휘 -> 거래소 `doc_subtype`. 순서가 중요하다 — `해지` 를 먼저 봐야
#: '공급계약 해지' 가 '체결' 로 안 샌다 (`doc_group` 이 `보고서` 를 뒤에 두는 것과 같다).
_EVENT_SUBTYPE = (("해지", "단일판매공급계약해지"),
                  ("철회", "단일판매공급계약해지"),
                  ("단일판매", "단일판매공급계약체결"),
                  ("공급계약", "단일판매공급계약체결"),
                  ("신규시설투자", "신규시설투자등"),
                  ("투자판단", "투자판단관련주요경영사항"))


def _event_subtype(question: str) -> str | None:
    """질문이 지목한 거래소 서식. 못 고르면 None (서식 필터 없이 연다)."""
    text = question or ""
    for word, subtype in _EVENT_SUBTYPE:
        if word in text:
            return subtype
    return None


def _wants_enumeration(r, question: str, group: str) -> bool:
    """열거 hop 을 켤 질의인가. 제동 ①~④ (⑤ 는 호출부가 본다)."""
    if group != "exchange" or len(getattr(r, "companies", []) or []) != 1:
        return False
    if getattr(r, "counterparty", None) == r.companies[0]:
        return False                       # 그 기업은 발주한 쪽이다 (Q-114 경계)
    return any(h in (question or "") for h in _ENUMERATION_HINT)


def _enumerate_events(r, question: str, budget: Budget, trace: list[str], take) -> int:
    """한 기업의 한 서식을 **계약상대별로 한 건씩** 담는다. LLM 0회 · 검색 0회 · 스텝 1.

    질문이 묻는 것이 "갈래" 이므로 담는 규칙도 갈래다 — 계약상대(canonical)로 묶고
    **그룹이 큰 순서로 돌면서 각 그룹의 최신본**을 한 건씩 가져온다. 두 하위 규칙 다
    이 저장소에 이미 있는 것이다: 그룹 대표를 최신본으로 두는 것은 `_partner_order` ·
    `query_events(is_latest=True)` 와 같은 규칙이고, 큰 갈래를 먼저 보는 것은 '두
    갈래' 를 묻는 질문에 큰 갈래가 답일 확률이 높기 때문이다.

    🔴 **정책을 여섯 가지 재고 골랐다** (`Q-117` 골든 7 · LLM 0회 · hop 0 회수 2/7 위):

        도착순                     3/7      질문 적합도(BM25)              3/7
        상대별 · 도착순             3/7      상대별 · 최신 · 도착순 그룹    4/7
        상대별 · 최신 · **크기 그룹**  **4/7**  상대별 · 최신 · 최근 그룹      3/7 (k=7 에서만 5/7)

    **질문 적합도는 여기서 안 듣는다.** `Q-117` 의 골든 7건은 나머지 20건과 텍스트로
    구별되지 않는다 — 라벨러가 두 갈래의 대표를 고른 것이라 질문에 단서가 없다.
    `_partner_order` 에서 BM25 가 이긴 것과 정반대 상황이고, 그래서 같은 함수를
    재사용하지 않았다. **어느 정렬이 이기는지는 문항이 정한다.**

    🔴 **겨냥 5건 중 실측 회수는 2건이다** (Q-117 2/7 → 4/7). 남은 3건은 같은 발주처
    그룹의 **두 번째 건**이라(한수원 2건 · 두산 2건 중 각각 1건씩) 그룹당 1건 규칙과
    정면으로 충돌한다. depth 를 2로 늘리면 근거가 상대 수만큼 부풀고, 그것은
    `_partner_quota` 가 실측으로 기각한 것과 같은 거래다.

    🔴 **v4 `R1-11` 이 `depth < 1` 을 올리자고 했고, 실측으로 기각했다 — 하중 상수가
    이것이 아니다.** `MAX_ENUM_EVIDENCE`(5)가 **먼저** 찬다:

        Q-117 (한전기술 · 단일판매공급계약체결)  pool=27  계약상대 그룹 **11개**
        depth<1 -> 5건   depth<2 -> 5건   depth<3 -> 5건      (전부 동일)

    그룹이 11개인데 담을 칸이 5개뿐이라 `depth 0` 한 바퀴에서 이미 상한이 찬다.
    즉 `depth` 를 아무리 올려도 이 코퍼스에서 **집합이 한 건도 안 바뀐다.** 열거 hop 이
    발화하는 문항은 100문항 중 `Q-117` **하나뿐**이고(전수 확인), 그 하나가 이렇다.

    미회수 3건의 실제 위치도 depth 문제가 아니다:
        20260209801845  한국남부발전 — **1건짜리 그룹**이다. depth 0 인데 그룹 순위가
                        11개 중 9번째라 5칸 상한에 잘린다 (필요한 것은 `depth` 가
                        아니라 `MAX_ENUM_EVIDENCE >= 9`)
        20251224800396  두산에너빌리티 2번째 — 전체 13번째 (`>= 13` 필요)
        20230330800844  두산에너빌리티 4번째 — 전체 20번째 (`>= 20` 필요)

    그런데 최종 절단은 `picked[:MAX_EVIDENCE]`(10)이고 `MAX_ENUM_EVIDENCE` 는 그
    **절반**이다 — 열거가 검색 근거를 밀어내지 않게 하려고 그렇게 뒀다(위 상수 주석).
    셋을 다 회수하려면 `MAX_EVIDENCE` 를 올려야 하는데 **그것이 `A16` 이고 `R0-3`
    (커밋 `dac2bf0`)이 이미 기각했다.** `tests/test_enumeration_hop.py::
    test_the_evidence_cap_binds_before_the_group_depth` 가 이 사실을 고정한다.
    """
    corp = r.companies[0]
    try:
        budget.spend_step()
    except BudgetExceeded as exc:
        trace.append(f"hop={EVENTS_HOP} strat=enumerate stop=step_budget ({exc})")
        return 0
    subtype = _event_subtype(question)
    args = {"corp_name": corp, "is_latest": True, "limit": ENUM_EVENT_POOL}
    if subtype:
        args["doc_subtype"] = subtype
    out = tools.execute("query_events", args)
    if "error" in out:
        trace.append(f'hop={EVENTS_HOP} strat=enumerate tool=query_events '
                     f'corp="{corp}" skip=tool_error 건너뜀({out["error"]})')
        return 0

    from argus.corpus.counterparty import canonical

    groups: dict[str, list[dict]] = {}
    for event in out.get("events") or ():
        groups.setdefault(canonical(event.get("counterparty") or ""), []).append(event)
    for members in groups.values():        # 그룹 안은 접수일 내림차순 = 최신 대표
        members.sort(key=lambda e: (str(e.get("rcept_dt", "")), e.get("rcept_no", "")),
                     reverse=True)
    order = sorted(groups, key=lambda key: (-len(groups[key]), key))

    taken: list[str] = []
    depth = 0
    while len(taken) < MAX_ENUM_EVIDENCE and depth < 1:
        for key in order:
            if len(taken) >= MAX_ENUM_EVIDENCE:
                break
            event = groups[key][depth]
            if take(event.get("doc_id"), event.get("markdown"), None, corp):
                taken.append(str(event.get("doc_id")))
        depth += 1

    trace.append(f'hop={EVENTS_HOP} strat=enumerate tool=query_events corp="{corp}" '
                 f'subtype={subtype or "-"} pool={out.get("count", 0)} '
                 f'groups={len(groups)} n={len(taken)} top_score=- '
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


#: `fault_response` 가 쓰는 stage 이름. `generate`·`critic` 과 갈라 적어야 **상류(CLOVA)
#: 장애**와 **우리 코드의 예외**를 사후에 구분할 수 있다.
FAULT_STAGE = "api"


def fault_response(question_id: str, question: str, exc: BaseException) -> dict:
    """어떤 예외로도 5키 스키마를 만들어 낸다. **`/answer` 의 마지막 방어선이다.**

    🔴 **왜 필요한가 (주최측 답변 2026-08-12 #3).** 호출 에러 시 재시도가 **2회**다.
    5xx 를 내면 같은 질의가 최대 **3회** 들어오고, 세 번 다 실패하면 그 문항은 **0점**이다.
    반면 200 + 한계 고지는 **정보한계대응이라는 채점 항목**(#9)에 걸려 0 이 아닐 수 있다.
    비결정적 실패라면 재시도가 캐시를 못 타므로 **크레딧까지 3배**로 나간다.

    🔴 **파이프라인의 기존 방어로는 부족하다.** `answer_question` 은 생성·critic 단계의
    `RuntimeError` 만 잡는다(§상류 장애 표식). `KeyError`·`TypeError` 같은 우리 버그와
    검색·계획 단계의 예외는 그대로 올라와 FastAPI 가 500 을 낸다.

    **삼키지 않는다** — `upstream_failure()` 가 되읽는 표식을 남기므로 평가 러너는 이것을
    "우리 답" 으로 캐시하지 않는다. `stage=api` 라서 상류 장애와도 갈린다.
    """
    trace = (f"{_upstream_mark(FAULT_STAGE, exc)} "
             f"서버 예외: {type(exc).__name__}: {exc}")
    return {"question_id": question_id, "question": question,
            "retrieved_context": "", "think_trace": trace, "answer": UNANSWERABLE}


#: 섹션 근거는 한 건이 최대 8,000자다. exchange 레코드 10건과 같은 상한(MAX_EVIDENCE)을
#: 쓰면 프롬프트가 8만자가 된다 — 20,000 토큰 예산을 혼자 넘긴다.
MAX_SECTION_EVIDENCE = 4
#: 섹션 근거 총량 상한. 12,000자 ≈ HCX 7~8천 토큰으로, 생성 800 토큰과 critic 을 남긴다.
MAX_SECTION_CHARS = 12_000


def _log_section(plan, corp, path, hits: list[dict], cell: dict | None = None) -> str:
    """검색 한 번의 trace 한 줄.

    `cell` 이 있으면 **그 셀의 필터**를 적는다 — 분해된 hop 은 `plan.filters` 로 검색하지
    않으므로 그것을 적으면 잰 것과 다른 것을 적는 셈이 된다 (`parse_hops` 가 이 줄을
    되읽어 Phase H 의 실패 단계 귀속 입력으로 쓴다).
    """
    ids = ",".join(str(h.get("doc_id") or "?") for h in hits)
    scores = [h["score"] for h in hits if isinstance(h.get("score"), (int, float))]
    top = f"{max(scores):.3f}" if scores else "-"
    used = cell if cell is not None else plan.filters
    filters = ",".join(f"{k}={v}" for k, v in sorted(used.items())) or "-"
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


#: 🔴 **메타 축 LOO (`L6`·`L7`·`L8`) 가 떼는 `search_chunks` 인자.** 축마다 뜻이 하나여야
#: 단일 변수 조작이 성립한다:
#:
#:   `corp_name`         → `tools.search_chunks` 의 `filters["corp_name"]`
#:   `ym`·`year`·`rcept_dt` → 셋 다 `prefix["rcept_dt"]` 로 접히므로 **같은 축**이다
#:   `group`             → `_group_index(target)` · 빠지면 `exchange` 로 떨어진다
#:
#: 🔴 `base_year`·`doc_subtype` 은 **안 넣는다.** 정기공시의 사업연도는 접수일이 아니고
#: (`query.plan_section_hops` 주석 1번 — '2023년 사업보고서' 는 2024-03 접수), 함께 떼면
#: `L7` 이 접수일 축 하나의 Δ 를 못 낸다.
_META_AXIS_KEYS: dict[str, tuple[str, ...]] = {
    "meta_corp": ("corp_name",),
    "meta_date": ("ym", "year", "rcept_dt"),
    "meta_group": ("group",)}


def _search_args(args: dict, arm=None) -> dict:
    """arm 의 메타 축 LOO 를 반영해 `search_chunks` 인자에서 축을 뗀다. LLM 0회.

    🔴 **인자를 안 넘기는 것이 조작의 전부다.** 검색 단계를 끄는 것이 아니므로 BM25 는
    그대로 돌고 후보 집합만 넓어진다 — v3 가 *"구조적으로 측정 불가"* 라고 적은 메타
    프리필터를 재는 자리가 여기다 (`ablation` 머리말 §메타 축 LOO).

    🔴 기본 arm(`A8`)에서는 **받은 사전을 그대로 돌려준다.** 한 키도 안 떼고 복사도 안
    하므로 스위치를 안 건드린 경로의 검색 인자는 바이트 하나 다르지 않다.
    """
    arm = arm or ablation.current()
    dropped = {key for flag, keys in _META_AXIS_KEYS.items()
               if not getattr(arm, flag) for key in keys}
    if not dropped:
        return args
    return {k: v for k, v in args.items() if k not in dropped}


def _drain_report_cells(plan, cells: list[dict], corps: list, quota: dict,
                        taken: int, budget: Budget, trace: list[str],
                        take) -> tuple[int, bool]:
    """부른 보고서마다 한 번씩 검색하고, 근거 칸을 **공평 몫 → 남은 칸** 두 벌로 담는다.

    두 벌로 나누는 이유는 실측 Q-105 다. 셀이 3개면 공평 몫이 `4 // 3 = 1` 칸인데
    `quarter` 셀에는 답이 **두 문서에 걸쳐** 있다 — 케이티 2025 분기보고서는 1분기
    (base_month 3)와 3분기(base_month 9) 둘이고, 질문이 부른 것은 3분기다. 그런데 두
    문서의 해당 섹션은 88자 스텁으로 **글자가 똑같아서 BM25 점수가 72.711 로 완전 동점**이다.
    점수로는 영원히 못 가른다.

    가르는 축은 `base_month` 인데 그것은 `section_index.json` 의 필터 축이 아니다
    (`fields` = doc_id·corp_name·doc_subtype·rcept_dt·base_year·path). 인덱스를 다시
    굽지 않고 이 문항을 살리는 길은 **둘 다 담는 것**이다 — 근거 블록 머리에 `접수번호`와
    `공시일` 이 찍히므로 어느 쪽이 3분기인지는 생성이 가린다. 검색이 후보를 다 대고
    선택을 생성에 넘기는 것이 이 경우의 올바른 분업이다.

    남은 칸은 **1차에서 몫을 못 쓴 셀이 아니라 순서대로** 돌린다. 1차가 이미 공평을
    보장했으므로 2차는 '남으면 더 담는다' 이고, 동점 셀이 그 칸을 가져간다.
    """
    path = (plan.paths or (None,))[0]
    pools: list[tuple[tuple, object, list[dict]]] = []
    for cell in cells:
        for corp in corps:
            try:
                budget.spend_step()
            except BudgetExceeded as exc:
                trace.append(f"hop={plan.hop} stop=step_budget ({exc})")
                return taken, True
            args = {"query": plan.query, "top_k": plan.top_k,
                    "group": "periodic", **cell}
            if corp:
                args["corp_name"] = corp
            if path:
                args["path"] = path
            out = tools.execute("search_chunks", _search_args(args))
            if "error" in out:
                trace.append(f'hop={plan.hop} strat={plan.strategy} '
                             f'tool=search_chunks group=periodic skip=tool_error '
                             f'건너뜀({out["error"]})')
                return taken, True
            hits = out.get("chunks") or []
            trace.append(_log_section(plan, corp, path, hits, cell))
            pools.append(((cell["doc_subtype"], cell["base_year"], corp), corp, hits))

    counts: dict = {}
    cap = _report_quota(len(cells), len(corps))
    for limit in (cap, MAX_SECTION_EVIDENCE):      # 1차 공평 몫 · 2차 남은 칸
        for key, corp, hits in pools:
            for chunk in hits:
                if taken >= MAX_SECTION_EVIDENCE:
                    break
                if quota[corp] <= 0 or counts.get(key, 0) >= limit:
                    break
                if take(chunk.get("chunk_id"), chunk.get("markdown"),
                        chunk.get("score"), corp):
                    taken += 1
                    quota[corp] -= 1
                    counts[key] = counts.get(key, 0) + 1

    keys = {key for key, _, _ in pools}
    if all(counts.get(key, 0) for key in keys):
        # 부른 보고서를 **전부** 담았으면 그 집합이 답이다 — 더 넓히면 오염된다.
        # 정기공시 차선의 유일한 '충분성 -> 정지' 간선이다 (`_sufficiency` 는
        # `_gather_sections` 가 끝난 뒤 한 번 불려 사다리를 되돌릴 수 없다).
        trace.append(f"hop={plan.hop} stop=named_reports_covered "
                     f"(부른 보고서 {len(keys)}종을 모두 회수)")
        return taken, True
    return taken, False


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
    plans = plan_section_hops(question, report_hints(question), paths,
                              _fiscal_years(r, question))
    if not ablation.current().hop_ladder:
        plans = plans[:1]                       # A1b 이하 — 재검색 사다리를 태우지 않는다
    corps = r.companies[:3] or [None]
    # 비교 질의는 한쪽만 채우면 답이 안 된다. 한 기업이 근거 칸을 다 먹지 않게 나눈다.
    per_corp = max(1, MAX_SECTION_EVIDENCE // len(corps))
    quota = {corp: per_corp for corp in corps}
    named = _report_cells(question)             # 질문이 이름을 부른 보고서들
    taken = 0
    for plan in plans:
        if plan.hop > 0:
            try:
                budget.spend_hop()
            except BudgetExceeded as exc:
                trace.append(f"hop={plan.hop} stop=hop_budget ({exc})")
                break
        # 제동 ①③ — 부른 보고서가 둘 이상이고 접수일 접두가 없는 hop 0 만 쪼갠다.
        if plan.hop == 0 and len(named) >= 2 and not plan.prefix:
            taken, stop = _drain_report_cells(
                plan, named, corps, quota, taken, budget, trace, take)
            if stop:
                break
            continue
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
                out = tools.execute("search_chunks", _search_args(args))
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


def _gather_meta(r, question: str, group: str, trace: list[str], take) -> str | None:
    """A0 — 메타 프리필터만. **BM25 를 한 번도 부르지 않는다.**

    §I-1 의 실측이 이 arm 의 근거다: `corp_name` + `rcept_dt` 접두만으로 후보가
    1,469 → 1건이 되고 골든이 1위가 된다. 그러니 검색의 바닥은 Vector RAG 가 아니라
    **여기**이고, 여기를 안 재면 가장 큰 기여 요소가 측정에서 통째로 빠진다.

    순위가 없다는 것이 이 arm 의 정의다. manifest 순서 그대로 앞에서부터 담는다 —
    점수를 매기는 순간 그건 A1(BM25) 이다. 정기공시도 같은 규칙이라 목차 선택 없이
    문서의 앞 섹션을 담는다 (실측상 거의 못 맞히는데, 그게 A1b 의 Δ 로 잡히는 값이다).

    `group` 은 **호출자가 결정한 것을 받는다.** 여기서 `r.group` 을 다시 읽으면 근거는
    `holding`/`major`/`periodic` 에서 오는데 요구 필드는 `exchange` 스키마로 검사되는
    상태가 된다 (`_gather_evidence` 의 `uses_group_index` 오버라이드가 이 함수를
    비껴갔다). 실측 A0 88문항 중 46문항이 비-exchange group 이었고, 예컨대 Q-016(holding)
    이 `reason=missing_field:비율` 을 냈다 — `비율` 은 exchange 의 `대비\\s*:` 이고
    holding 근거에는 `보유비율:` 이 항상 있다. **없는 것을 없다고 한 것이 아니라 있는
    것을 다른 이름으로 찾은 것**이고, 그 라벨을 `parse_hops` → Phase H 가 되읽는다.
    """
    period = _period_args(r, question)
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


#: 정기공시 `doc_subtype` → 사람이 읽는 보고서명. 정형 레코드는 `markdown._label` 이 이미
#: 같은 일을 하므로 여기 넣지 않는다 — 규칙을 두 곳에 적으면 한쪽만 낡는다.
_PERIODIC_LABEL = {"annual": "사업보고서", "half": "반기보고서", "quarter": "분기보고서"}


def _citation_source(record: dict) -> dict | None:
    """🔴 인용에 쓸 값을 레코드에서 **그대로** 꺼낸다 — 마크다운을 되읽지 않는다.

    `ablation.attach_citation` 은 근거 마크다운을 정규식으로 되읽어 인용을 만들 수도
    있다(되먹임). 그러나 그것은 **우리가 방금 렌더한 글자를 도로 파싱하는 것**이라
    이 저장소가 반복해 데인 *"같은 값을 두 곳에 적고 한쪽만 고친다"* 와 같은 모양이다.
    실제로 취약하다 — 머리글의 `–` 는 EN DASH 라 하이픈으로 적으면 하나도 안 걸린다.

    🔴 **정기공시에서 특히 다르다.** 되읽기는 섹션경로를 머리글 문자열에서 긁어내는데,
    청크 레코드에는 `path` 가 정확히 들어 있다 (`'II. 사업의 내용 > 2. 주요 제품'`).
    """
    rcept = str(record.get("rcept_no") or "")
    if not rcept:
        found = re.search(r"\d{14}", str(record.get("doc_id") or ""))
        rcept = found.group(0) if found else ""
    if not rcept:
        return None

    # 🔴 **검색이 넘기는 청크는 가난하다.** 실측: 정형 히트는 `{doc_id, score}` 뿐이고
    #    정기 히트는 `{chunk_id, doc_id, path, score}` 다. 그대로 쓰면 인용이
    #    `(근거: 공시, 접수번호 …)` 로 빈약해져 **되읽기보다 못해진다.** 그래서 여기서
    #    정본을 조회해 채운다 — 색인·레코드 조회라 LLM 0회다.
    # 🔴 **`doc_id` 접두로 가른다.** 처음에는 `path` 유무로 갈랐는데, 정기공시가 섹션
    #    검색이 아니라 매니페스트 경로(`_gather_meta`)로 들어오면 `path` 가 없어 정형으로
    #    빠지고 `_record_of` 도 `None` 이라 보고서명이 `공시` 로 떨어졌다 (실측 Q-105).
    #    §5 「근거 표시」는 **공시명**을 요구하는데 '공시' 는 공시명이 아니다.
    doc_id = str(record.get("doc_id") or "")
    if doc_id.startswith(PERIODIC_DOC_PREFIX) or record.get("path") is not None:
        source = _periodic_meta(doc_id)
        label = _PERIODIC_LABEL.get(source.get("doc_subtype") or "", "정기보고서")
        if source.get("base_year"):
            label = f'{label} {source["base_year"]}'
        # 청크가 `path` 를 들고 오면 그것을 쓰고, 없으면 청크 id 로 색인에서 찾은 것을 쓴다.
        tail = str(record.get("path") or source.get("path") or "")
        raw = str(source.get("rcept_dt") or "")
    else:                                                   # 정형 레코드
        source = record if record.get("doc_subtype") or record.get("title") else {}
        if not source:
            source = tools._record_of(rcept) or record
        label = markdown_module._label(source)
        tail = str(source.get("title") or "")
        raw = str(source.get("rcept_dt") or "")

    # 접수번호 앞 8자리가 접수일이다 (DART 규칙). 조회가 비어도 날짜는 잃지 않는다.
    if len(raw) != 8:
        raw = rcept[:8]
    date = f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}" if len(raw) == 8 and raw.isdigit() else ""
    return {"rcept_no": rcept, "label": label, "date": date, "tail": tail}


def _periodic_meta(doc_id: str) -> dict:
    """정기공시 청크 id 로 **문서 메타 + 그 섹션의 목차 경로**를 찾는다. LLM 0회.

    🔴 **`_take` 가 받는 것은 문서 id 가 아니라 청크 id 다** — 실측
    `periodic_20250814003695#109` · `periodic_20250321001834#119/p0`. 처음엔 그대로
    `_sections_of` 에 넘겨 빈 결과를 받았고, 그래서 보고서명이 `정기보고서` 로,
    섹션경로가 빈칸으로 떨어졌다 (실측 Q-105).

    문서 메타(종류·사업연도·접수일)는 섹션마다 같으므로 첫 행에서 읽고, **목차 경로만
    청크 id 로 골라낸다.**
    """
    base = doc_id.split("#", 1)[0]
    index = tools._section_index()
    rows = tools._sections_of(index, base) if index else []
    if not rows:
        return {}
    chunk = doc_id.split("/", 1)[0]                 # `#119/p0` → `#119`
    hit = (next((row for row in rows if row.get("chunk_id") == chunk), None)
           # 조각이 더 쪼개진 형태(`#119/p0`)는 정확히 안 맞을 수 있다. 접두로 한 번 더 본다.
           or next((row for row in rows
                    if str(row.get("chunk_id") or "").startswith(chunk)), None))
    return {**rows[0], "path": (hit or {}).get("path", "")}


def _gather_evidence(r, question: str = "", budget: Budget | None = None,
                     provenance: dict[str, dict] | None = None) -> tuple[str, list[str]]:
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
    #
    # 🔴 **`uses_bm25` 가 참인 arm 에만 건다.** `uses_group_index` 는 `retrieval == "all"`
    # 이라 A0(`meta`)에도 False 인데, A0 은 인덱스를 아예 열지 않고 manifest 만 본다 —
    # 되돌릴 '인덱스' 가 없다. 그런데도 이 줄이 걸려서 A0 은 근거를 holding/major/periodic
    # 매니페스트에서 가져오면서 요구 필드는 exchange 스키마로 검사했다(실측 46/88 문항이
    # 틀린 `missing_field` 라벨). 답변은 안 바뀐다 — A0 은 판정을 소비하지 않는다 —
    # 그러나 `parse_hops` 가 그 사유를 되읽어 Phase H 의 RETRIEVAL/INDEX_FIELD_MISSING
    # 귀속 입력으로 쓴다. 즉 **잰 것과 다른 것을 잰 것**이다.
    if arm.uses_bm25 and not arm.uses_group_index:
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
    #: 발주처 차선이 최종 근거에 실을 수 있는 칸. `A22` 가 추가 허브를 결속할 때만
    #: 허브당 `MAX_EXTRA_HUB_EVIDENCE` 씩 늘어난다 — `W=1` 에서는 종전 상수 그대로다.
    cp_cap = MAX_COUNTERPARTY_EVIDENCE

    def _remember(record: dict | None) -> None:
        """🔴 근거로 삼은 레코드를 기억한다 (`A25`). **모든 근거가 이 길목을 지난다** —
        섹션 근거도 `_gather_meta` 가 `_take` 를 통해 넣으므로 여기 한 곳이면 된다.

        절단(`MAX_EVIDENCE`·`_join_section_evidence`)에서 탈락한 것도 여기 남지만
        상관없다 — **살아남았는지는 근거 문자열에서 읽는다.** 기억은 '값' 만 준다.
        """
        if provenance is None or not record:
            return
        unit = _citation_source(record)
        if not unit:
            return
        # 🔴 **정기공시는 한 보고서에 섹션이 여럿**이고 접수번호는 하나다. 먼저 온 것만
        #    남기면 나머지 섹션이 인용에서 사라진다. 주최측 예시(`CONVENTION §7.5`)가
        #    바로 이 경우를 *"접수번호 20260515002181, II-3 원재료 및 생산설비 /
        #    II-6 연구개발활동"* 으로 적는다 — 한 접수번호 아래 섹션을 나열한다.
        kept = provenance.setdefault(unit["rcept_no"],
                                     {**unit, "tails": []})
        if unit["tail"] and unit["tail"] not in kept["tails"]:
            kept["tails"].append(unit["tail"])

    def _take(doc_id, markdown, score, corp, record=None) -> bool:
        if markdown and doc_id and doc_id not in seen:
            seen.add(doc_id)
            picked.append({"doc_id": doc_id, "markdown": markdown,
                           "score": score, "corp": corp})
            _remember(record if record is not None else {"doc_id": doc_id})
            return True
        return False

    def _take_cp(doc_id, markdown, score, corp, record=None) -> bool:
        if markdown and doc_id and doc_id not in cp_seen:
            cp_seen.add(doc_id)
            cp_picked.append({"doc_id": doc_id, "markdown": markdown,
                              "score": score, "corp": corp})
            _remember(record if record is not None else {"doc_id": doc_id})
            return True
        return False

    def _joined() -> str:
        """검색 차선 먼저, 발주처 차선은 그 뒤. 검색 칸(MAX_EVIDENCE)은 그대로 남는다.

        중복 제거는 **절단 후 집합**과 대조한다. `seen` 은 검색 차선이 담은 전부이므로
        그것과 대조하면 `picked` 의 11번째 이후와 겹치는 cp 레코드가 **양쪽에서 다 빠진다** —
        그런데 `_gather_counterparty` 의 trace 는 그 doc_id 를 `ids=` 에 적고 `n=` 에 세므로
        Phase H 는 '근거에 실렸다' 로 읽는다. 근거 소실과 자기 성적표 부풀림이 동시에 난다
        (`_chain_note`·`_counterparty_note` 가 피하려고 애쓴 사고의 다른 입구다).
        기록된 밸류체인 8건에서는 미발화다(중복으로 빠진 cp 레코드가 전부 상위 10 안에
        있었다). `picked` 가 10을 넘는 문항(Q-113 실측 14블록)에서 발화한다.

        🔴 `cp_cap` 은 `A22` 가 **결속한 추가 허브 수만큼만** 늘어난다. 여기 상한을
        `cp_picked` 전량으로 열면 허브가 셋일 때 발주처 근거가 18블록까지 부풀어
        검색 근거를 프롬프트 뒤로 밀어낸다. `W=1` arm 에서는 종전 값 그대로다.
        """
        head = picked[:MAX_EVIDENCE]
        kept = {p["doc_id"] for p in head}
        extra = [p for p in cp_picked if p["doc_id"] not in kept]
        return "\n\n".join(p["markdown"] for p in head + extra[:cp_cap])

    if not arm.uses_bm25:                      # A0 — 검색 없이 메타 프리필터만
        if _out_of_corpus_period(r, question):
            trace.append(f"hop=0 stop=out_of_range "
                         f"(질문 기간 {r.years} 가 코퍼스 2023.01~2026.03 밖 — 완화해도 답이 없다)")
            return "", trace
        sections = _gather_meta(r, question, group, trace, _take)
        ok, reasons = _sufficiency(r, picked, required)
        trace.append(_log_verdict(0, ok, len(picked), reasons))
        if sections is not None:
            return sections, trace
        return "\n\n".join(p["markdown"] for p in picked[:MAX_EVIDENCE]), trace

    # 정기공시는 검색 축이 다르다 — 접수일이 아니라 사업연도, 그리고 목차 경로.
    # 같은 사다리를 태우면 골든이 필터에서 통째로 빠진다 (04-retriever D-5).
    if group == "periodic":
        if _out_of_corpus_period(r, question):
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
        if plan.hop > 0 and _out_of_corpus_period(r, question):
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
                out = tools.execute("search_chunks", _search_args(args))
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
                             chunk.get("score"), corp, record=chunk):
                        taken_here += 1

        # 집합 완결성. 후보가 대체된 문서면 체인의 나머지가 와야 근거가 완결된다.
        # **판정 전에** 한 번만 밟는다 — 판정 뒤에 두면 hop 0 이 `sufficient` 를 내고
        # 멈춰서 정정본이 영영 안 온다 (Phase H 실패 7건의 정확한 기전).
        if arm.chain_hop and not chain_done and picked and not out_of_steps:
            chain_done = True
            chain_gap, chain_ids = _expand_chain(picked, seen, budget, trace, _take,
                                                 _as_of_ym(question), question)

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
            and not _out_of_corpus_period(r, question)):
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
                _take(event.get("doc_id"), event.get("markdown"), None, corp,
                      record=event)

    # 열거 hop. 위 폴백과 **배타**다 (저쪽은 `not picked`, 이쪽은 `picked`) — 같은 hop
    # 번호를 쓰므로 배타여야 `parse_hops` 가 두 종류를 한 레코드로 섞어 읽지 않는다.
    # 사다리가 넓히기만 하고 열거하지 않아 `Q-117` 이 둘째 갈래를 지어냈다 (제동은 위 별항).
    elif (arm.hop_ladder and picked and len(picked) < MAX_EVIDENCE
            and not _out_of_corpus_period(r, question)
            and _wants_enumeration(r, question, group)):
        _enumerate_events(r, question, budget, trace, _take)

    # 발주처 hop. 사다리 **밖의 스텝 1개**이고, 검색이 예산을 다 쓴 뒤에 남는 것으로만
    # 움직인다 (제동 ④). 순서를 뒤로 미뤄도 담기는 레코드는 같다 — 그래프 조회라
    # 검색 상태에 의존하지 않는다.
    #
    # 🔴 `A22`(`multi_hub`)만 허브를 둘 이상 결속한다. 플래그가 꺼져 있으면 아래는
    # `hubs = [r.counterparty]` 한 칸이라 종전과 호출도 인자도 같다.
    if arm.counterparty_hop:
        primary = getattr(r, "counterparty", None)
        hubs = [primary] if primary else []
        if arm.multi_hub and len(hubs) < MAX_COUNTERPARTY_HUBS:
            room = MAX_COUNTERPARTY_HUBS - len(hubs)
            extra_hubs = _evidence_hubs(picked, primary, r.companies)[:room]
            hubs += extra_hubs
            cp_cap += MAX_EXTRA_HUB_EVIDENCE * len(extra_hubs)
        for index, hub in enumerate(hubs):
            # 첫 허브가 질문이 지목한 것이면 종전 상한·순서를 그대로 쓴다. 근거에서
            # 나온 허브는 칸이 둘이고, 질문이 부른 기업을 파트너 앞줄에 세운다.
            from_question = index == 0 and hub == primary
            _gather_counterparty(
                hub, question, budget, trace, _take_cp,
                limit=MAX_COUNTERPARTY_EVIDENCE if from_question else MAX_EXTRA_HUB_EVIDENCE,
                prefer=() if from_question else tuple(r.companies or ()))

    # 근거 절단은 체인을 먼저 살린다. 한화오션 잠수함은 검색 5건 + 체인 6건 = 11건이라
    # 도착 순서대로 자르면 **체인의 마지막 정정본**(=정답)이 잘려 나간다.
    if chain_ids:
        picked.sort(key=lambda p: 0 if p["doc_id"] in chain_ids else 1)

    return _joined(), trace


# --- ORACLE — 골든 근거 주입 (🔴 진단 전용 · 제출 구성이 될 수 없다) --------------
# `A0`~`A8` 은 전부 "검색 + 생성" 의 합성 성능이라 **근거를 고정한 생성 능력을 잰 적이
# 없다.** 이 경로는 검색 차선 전체를 골든 근거로 갈아치워 그 축을 하나 만든다.
#
# 🔴 **R2E 재측정이 R1 의 계약을 깼다 (2026-08-14).** 종전 두 줄
# (`Δ = 검색이 아직 못 가져온 몫` · `잔여 오답 = 순수 생성 실패`)을 **그대로 인용하면 안 된다.**
# 주입은 근거를 **더하지 않고 교체**하므로 근거가 줄어든다(전체 88 에서 −47%). 정오가 갈린
# 7문항 전수에서 골든 회수가 `A8` 과 **동일**했고 오라클 근거만 더 작았다.
#
#     이 경로가 재는 것    "근거를 골든만으로 한정했을 때 답이 서는가"
#     Δ(A8 → ORACLE)     🔴 검색 부족분 + 보조근거 제거 손실의 합. 한 항으로 못 읽는다
#     잔여 오답 = 생성 실패 🔴 근거가 A8 과 바이트 동일한 문항에서만 정의상 성립
#
# 살아 있는 판정: **골든을 전건 주입해도 정답이 안 늘면 남은 실패는 검색으로 살 수 없다.**
# 근거는 `src/argus/ablation.py` 머리말 §ORACLE · `09-ablation.md` §I-5f ④.
#
# 🔴 **평가 시점에는 골든이 없다.** 이 경로로 나온 정답률은 성적이 아니라 진단값이다.
#
# 🔴 **형식·순서·절단 규칙을 검색 경로와 같게 맞춘다.** 다르면 Δ 에 '형식 효과' 가 섞여
# 검색 몫과 구별되지 않는다. 그래서 정형 레코드는 `record["markdown"]` 을 `MAX_EVIDENCE`
# 까지, 정기공시는 `tools._section_markdown` 블록을 `_join_section_evidence` 로 —
# 둘 다 검색 경로가 쓰는 **같은 함수·같은 상한**이다.

#: 정기공시 문서 식별자 접두. 골든은 접수번호만 주므로 여기서 만든다
#: (`filter_docs(doc_group="periodic")` 의 `doc_id` 와 같은 형태다).
PERIODIC_DOC_PREFIX = "periodic_"

#: 골든 목차 하나에서 볼 조각 후보 수. 상한은 `MAX_SECTION_EVIDENCE` 가 다시 조인다.
#: 넉넉히 잡는 이유는 골든 `section` 이 **대목 전체**를 가리키는 경우가 있어서다 —
#: 실측 Q-088 의 `III. 재무에 관한 사항 > 3. 연결재무제표 주석` 은 조각 57개 · 231,624자다.
ORACLE_SECTION_POOL = 8

#: `parse_hops` 가 되읽을 hop 번호. 검색 hop 과 겹치지 않게 발주처 hop 다음에 둔다 —
#: 같은 번호를 쓰면 Phase H 가 두 종류의 후보를 한 레코드로 합쳐 읽는다.
ORACLE_HOP = COUNTERPARTY_HOP + 1


def _oracle_section_blocks(rcept_no: str, section: str, question: str, cap: int,
                           budget: Budget, trace: list[str]) -> list[dict]:
    """골든이 지목한 (문서, 목차) 안에서 근거 조각을 고른다. LLM 0회.

    🔴 **오라클의 한계가 여기 있다.** 골든 `section` 은 목차 '대목' 이라 조각 수십 개로
    풀리는데 프롬프트 예산(`MAX_SECTION_CHARS` 12,000자)은 4조각쯤만 받는다. 그래서
    문서 순서대로 앞에서 자르면 오라클이 아니라 '검색 없음' 이 된다 (Q-088 은 앞 4조각이
    일반사항·회계정책이고 골든 표는 뒤에 있다). **문서와 목차는 골든으로 못박고, 그 안의
    조각 선택만 기존 랭커에 맡긴다** — 즉 이 arm 이 완벽하게 고정하는 것은 *문서·대목*
    이지 *문단* 이 아니다. 보고서가 이 사실을 함께 적어야 Δ 를 옳게 읽는다.

    BM25 가 한 조각도 못 고르면(질의어가 하나도 안 겹치면) 문서 순서로 되돌린다 —
    빈 근거로 내려가면 그 문항이 '생성 실패' 로 오독된다.
    """
    doc_id = f"{PERIODIC_DOC_PREFIX}{rcept_no}"
    index = tools._section_index()
    wanted = bm25_module.norm_path(section)
    rows = [r for r in (tools._sections_of(index, doc_id) if index else [])
            if bm25_module.norm_path(r["path"]) == wanted
            or bm25_module.norm_path(r["path"]).startswith(wanted)]
    rows.sort(key=lambda r: (r["chunk_id"].split("/")[0], r["part"]))
    # 기업 축은 인덱스 레코드에서 되찾는다. `search_chunks` 는 `corp_name` 을 안 돌려주는데,
    # `_join_section_evidence` 가 그 축으로 라운드로빈한다 — None 으로 두면 비교 질의에서
    # 한 기업이 근거 칸을 다 먹는다 (`_cover_order` 주석의 Q-049 사고와 같은 기전).
    corp_of = {r["chunk_id"]: r["corp_name"] for r in rows}

    try:
        budget.spend_step()
    except BudgetExceeded as exc:
        trace.append(f"hop={ORACLE_HOP} strat=oracle stop=step_budget ({exc})")
        return []
    out = tools.execute("search_chunks", {"query": question, "top_k": ORACLE_SECTION_POOL,
                                          "group": "periodic", "doc_id": doc_id,
                                          "path": section, "include_stubs": True})
    if "error" in out:
        trace.append(f'hop={ORACLE_HOP} strat=oracle tool=search_chunks group=periodic '
                     f'skip=tool_error 건너뜀({out["error"]})')
        chunks = []
    else:
        chunks = [c for c in (out.get("chunks") or []) if c.get("chunk_id") in corp_of]
    if not chunks:
        # 질의어가 한 조각과도 안 겹쳤다 (BM25 는 점수 0 을 후보에서 뺀다). 문서 순서로
        # 되돌린다 — `read_section` 과 같은 정렬이다 (조각 번호 → part).
        trace.append(f'hop={ORACLE_HOP} strat=oracle tool=search_chunks group=periodic '
                     f'doc_id={doc_id} fallback=doc_order n={len(rows)}')
        chunks = [{"chunk_id": r["chunk_id"], "score": None,
                   "markdown": tools._section_markdown(r)}
                  for r in rows[:ORACLE_SECTION_POOL]]
    return [{"doc_id": c["chunk_id"], "markdown": c.get("markdown"),
             "score": c.get("score"), "corp": corp_of.get(c["chunk_id"])}
            for c in chunks if c.get("markdown")][:cap]


def _gather_golden(question_id: str, question: str,
                   budget: Budget) -> tuple[str, list[str]]:
    """골든 근거만으로 프롬프트 근거를 만든다. 🔴 진단 전용 (`ablation.ORACLE`).

    주입이 없으면 `ablation.oracle_golden_for` 가 **예외를 던진다** — 조용히 빈 근거로
    내려가면 그것이 "오라클을 줘도 생성이 다 틀렸다" 로 굳는다.
    """
    golden = ablation.oracle_golden_for(question_id)
    trace: list[str] = [
        f"oracle=golden n_golden={len(golden)} "
        "🔴 진단 전용 — 검색을 골든으로 대체했다 (제출 구성이 될 수 없다)"]

    # 🔴 골든 근거마다 **공평 몫**을 준다. `_gather_sections` 가 (기업 × 경로) 셀에 할당량을
    #    두는 것과 같은 이유다 — 안 두면 조각이 많은 골든 하나가 근거 칸 4개를 다 먹고
    #    나머지 골든이 통째로 빠진다 (실측 Q-054·Q-088·Q-094 — 대목 전체를 가리키는
    #    `연결재무제표 주석` 이 같은 문항의 `정정 신고` 를 밀어냈다). `_cover_order` 는
    #    기업 축으로만 나누므로 **같은 기업의 골든 둘**은 그것만으로 안 갈린다.
    n_sections = sum(1 for item in golden if (item or {}).get("section"))
    cap = max(1, MAX_SECTION_EVIDENCE // n_sections) if n_sections else MAX_SECTION_EVIDENCE

    records: list[dict] = []
    sections: list[dict] = []
    missing: list[str] = []
    for item in golden:
        rcept_no = str((item or {}).get("rcept_no") or "")
        section = (item or {}).get("section")
        if not rcept_no:
            missing.append("?")
            continue
        if section:
            blocks = _oracle_section_blocks(rcept_no, section, question, cap,
                                            budget, trace)
            if not blocks:
                missing.append(rcept_no)
            sections.extend(blocks)
            continue
        record = tools._record_of(rcept_no)
        if record is None or not record.get("markdown"):
            missing.append(rcept_no)
            continue
        records.append({"doc_id": record["doc_id"], "markdown": record["markdown"],
                        "score": None, "corp": record.get("corp_name")})

    picked = records + sections
    # 🔴 `parse_hops` 가 되읽는 줄. 골든 문서를 `ids=` 에 적어야 Phase H 의 실패 귀속이
    #    이 arm 에서도 성립한다 — 안 적으면 골든이 '어떤 hop 후보에도 없다' 가 되어
    #    전건이 RETRIEVAL 로 귀속되고, 그것이 이 측정의 결론을 통째로 뒤집는다.
    corps = sorted({p["corp"] for p in picked if p.get("corp")})
    trace.append(
        f'hop={ORACLE_HOP} strat=oracle tool=golden_evidence '
        f'corp="{",".join(corps) or "-"}" period=none '
        + ("group=periodic " if sections else "")
        + f'n={len(picked)} top_score=- '
        f'ids={",".join(str(p["doc_id"]) for p in picked) or "-"}')
    if missing:
        # 골든이 코퍼스에서 안 열리는 경우. **삼키지 않는다** — 근거가 준 것보다 적다.
        trace.append(f'hop={ORACLE_HOP} strat=oracle skip=golden_unresolved '
                     f'ids={",".join(missing)}')
    trace.append(_log_verdict(ORACLE_HOP, bool(picked), len(picked),
                              [] if picked else ["oracle_empty"]))

    parts = []
    if records:
        parts.append("\n\n".join(p["markdown"] for p in records[:MAX_EVIDENCE]))
    if sections:
        parts.append(_join_section_evidence(sections))
    return "\n\n".join(p for p in parts if p), trace


# --- A10 — 자기일관성 `k=3` (🔴 온도 통제 변수를 깨는 유일한 arm) ------------------
# 표본을 뽑고 **다수결**한다. 논문(Self-Consistency, 2023)의 전제는 *"여러 갈래로 추론해도
# 옳은 답으로 수렴한다"* 이고, 그 전제가 **우리 코퍼스에서 성립하는가**가 이 arm 의 측정
# 대상이다. 채택 여부와 무관하게 그 판정이 산출물이다.
#
# 🔴 **두 함정을 먼저 닫는다 — 둘 다 Δ 를 조용히 0 으로 만든다.**
#   ① 요청 캐시    프롬프트도 온도도 같아 k 개 표본의 **캐시 키가 하나**다. 2회차부터
#                 `clova.no_cache()` 로 우회하지 않으면 표본이 전부 같아진다
#   ② 온도 0.0     `Arm.__post_init__` 이 `sc_k>1 && temperature==0` 을 예외로 막는다


def _sc_key(answer: str) -> str:
    """표본을 묶는 키 — 자기일관성의 '최종 답' 에 해당하는 것.

    우리 답변에서 그것은 **금액과 비율**이다: 닫힌 문항의 채점 축이
    `em:number_won`(원 단위 완전일치)이고, 열린 문항의 judge 도 수치 일치를 먼저 본다.
    🔴 **정규식을 새로 쓰지 않는다** — `critic._money_spans` 와 `critic._PCT_RE` 가
    이 저장소의 단일 원천이고, 여기 사본을 두면 한쪽만 고쳐진다(이미 다섯 번 겪은 실패).

    🔴 **수치가 없는 답변은 다수결이 성립하지 않는다.** 그 경우 공백만 정규화한 원문을
    키로 쓰는데, 온도 0.7 에서 서술문이 글자까지 같을 확률은 낮다 — 즉 표본이 전부
    다른 키가 되고 **첫 표본이 선택된다.** 이것은 결함이 아니라 이 조작의 한계이고,
    트레이스의 `keys=` 가 그 사실을 문항마다 남긴다.
    """
    from argus.agent import critic

    text = answer or ""
    values = [f"{value:.10g}" for _, _, value, _, _ in critic._money_spans(text)]
    values += [f'{m.group(1).replace(",", "")}%' for m in critic._PCT_RE.finditer(text)]
    return "|".join(values) if values else " ".join(text.split())


def _self_consistent(messages: list[dict], max_tokens: int, arm, first: str,
                     budget: Budget, trace: list[str]) -> str:
    """표본 `k` 개 중 다수결로 고른 답변. 첫 표본은 이미 뽑혀 있으므로 `k-1` 번 더 부른다.

    🔴 **예산이 끊기거나 상류가 죽으면 그때까지의 표본으로 다수결한다.** 여기서
    `BudgetExceeded` 를 위로 던지면 호출부가 `UNANSWERABLE` 로 빠져 **이미 받아 둔
    정답을 버린다** — 토큰 상한 20,000 은 긴 근거 문항에서 3회차에 실제로 물린다
    (실측: 프롬프트 6,800 토큰 문항이 3회차에서 20,400).

    🔴 동수면 **먼저 나온 키**가 이긴다. 그래야 `k` 를 늘려도 1회차가 기준선으로 남는다.
    """
    samples = [first]
    for index in range(1, arm.sc_k):
        try:
            # 🔴 캐시 우회는 **2회차부터**다. 1회차를 캐시에 남겨야 재실행이 그만큼 싸고,
            #    `--offline` 재집계가 최소한 그 한 벌은 되읽을 수 있다.
            with no_cache():
                result = chat(messages, max_tokens=max_tokens,
                              temperature=arm.temperature, tag="answer_sc")
            budget.spend_llm(result.prompt_tokens, result.completion_tokens)
        except BudgetExceeded as exc:
            trace.append(f"sc stop=budget k={len(samples)}/{arm.sc_k} ({exc})")
            break
        except RuntimeError as exc:
            trace.append(f"sc skip=upstream k={len(samples)}/{arm.sc_k} ({exc})")
            break
        samples.append(result.text)
        trace.append(f"generate sc={index} "
                     f"tokens={result.prompt_tokens}+{result.completion_tokens}")

    keys = [_sc_key(s) for s in samples]
    counts: dict[str, int] = {}
    for key in keys:
        counts[key] = counts.get(key, 0) + 1
    best = max(counts, key=lambda k: (counts[k], -keys.index(k)))
    picked = keys.index(best)
    trace.append(f"sc k={len(samples)}/{arm.sc_k} temp={arm.temperature} "
                 f"keys={len(counts)} votes={counts[best]} picked={picked}")
    return samples[picked]


# --- A24 — `chat(tools=…)` 왕복 (🔴 게이트 F 의 유일한 미완이었다) -----------------
# `TOOL_SPECS` 15종은 v1 부터 등록돼 있었는데 **`tools=` 가 `chat()` 에 한 번도 전달된
# 적이 없다** — `ablation.py` 가 그 사실을 스스로 문서화해 뒀고 게이트 F 가 `[~]` 로
# 남아 있었다. 여기가 그 미완을 닫는 자리다.
#
# 🔴 **왕복이 필수다.** 모델이 `toolCalls` 를 돌려주면 그 응답의 `content` 는 보통
# 비어 있다. 실행 결과를 다시 넣어 한 번 더 부르지 않으면 **빈 답변이 나가고**, 우리는
# 그 0점을 "function calling 은 효과가 없다" 로 읽는다 — 이 저장소가 최악으로 보는
# **조용한 Δ** 의 정확히 같은 종류다.
#
# 🔴 **`tools` 와 `maxTokens` 는 동시 지정 불가다** (40001 · `docs/CONVENTION.md`).
# 그래서 도구를 내미는 회차에는 길이 상한이 **없고**, 마지막 회차는 도구를 빼서 상한을
# 되돌린다. 이것은 우리 선택이 아니라 API 제약이고, `A24` 의 Δ 에 그 몫이 섞인다.

#: 도구를 내미는 회차의 상한. 마지막에 **도구 없는** 회차가 하나 더 붙으므로 생성
#: 호출은 최대 `MAX_TOOL_ROUNDS + 1` = **2회**다.
#:
#: 🔴 **루프를 열어 두지 않는다** — `Budget` 의 호출 상한 6 과 토큰 상한 20,000 을 답변
#: 단계 혼자 태우면 critic·검증이 굶고, 그것이 arm 효과로 오독된다 (이 파일 머리말:
#: 고정 횟수 단계라 질문당 비용 상한이 계산된다).
#:
#: 🔴 **1 로 두면 도구 연쇄(`calc_total` → `calc_share`)는 못 한다.** 그것이 이 값의
#: 유일한 대가이고, 대신 얻는 것이 둘이다: ① 마지막 회차에 도구를 빼므로 `maxTokens`
#: 가 돌아와 **답변 길이 상한이 `A8` 과 같아진다**(40001 제약: `tools` 와 `maxTokens`
#: 동시 지정 불가) ② 문항당 생성 호출이 정확히 1~2회로 못 박힌다.
MAX_TOOL_ROUNDS = 1

#: 도구 결과 한 건을 프롬프트에 실을 때의 글자 상한. `read_section`·`search_chunks` 는
#: 수만 자를 돌려줄 수 있어 상한이 없으면 **토큰 상한 20,000 을 한 번에 태운다** —
#: 그러면 `BudgetExceeded` 로 `UNANSWERABLE` 이 나가고 그것이 측정으로 굳는다.
MAX_TOOL_RESULT_CHARS = 2_000


def _tool_arguments(call: dict) -> dict:
    """`toolCalls[i].function.arguments` 를 사전으로. HCX-005 는 **사전**을 돌려주지만
    (실측 `notebooks/clova_verify.ipynb`), OpenAI 호환 서버는 JSON 문자열을 준다.
    양쪽을 다 받는다 — 형태 하나를 가정하면 도구가 조용히 안 불린다."""
    args = (call.get("function") or {}).get("arguments")
    if isinstance(args, str):
        try:
            args = json.loads(args or "{}")
        except json.JSONDecodeError:
            return {}
    return args if isinstance(args, dict) else {}


# --- A24b — 🔴 도구 결과를 모델이 옮겨 적게 하지 않는다 ---------------------------
# `A24` 는 **도구를 안 불러서** 진 것이 아니다 (37/88 이 불렀다). 진 이유 셋 중 첫째가
# 이것이다 — `calc_total` 이 골든과 바이트까지 같은 `2,365,868` 을 돌려줬는데 답변은
# `2,366,968` 을 썼다. **우리 도메인의 병목은 산술이 아니라 전사(轉寫)** 이고,
# function calling 은 그 축을 건드리지 않는다.
#
# 🔴 처방은 `A20` 이 이미 실측으로 이겨 둔 것과 같다 — **값을 코드가 문자열에 박는다.**
# 규율도 코드도 `critic` 것을 그대로 쓴다 (`critic.tool_result_reports` 머리말).


#: 🔴 **도구가 실제로 계산했다고 보는 최소 인자 수.** 이 아래면 도구는 계산한 것이
#: 아니라 **모델이 타이핑한 수를 그대로 되돌려준 것**이고, 그 값은 독립된 근거가 아니다.
#:
#: 🔴 **실측이 이 상수를 만들었다** (`A24b` 1차 · `Q-047`). 모델이 근거의
#: `97,146,675백만원` 을 **도구 인자에** `97,146,765` 로 잘못 옮겨 적고
#: `calc_mean({"values": [97146765]})` 를 불렀다. 평균의 인자가 하나이므로 도구는
#: 그 수를 그대로 돌려줬고, 치환이 **답변의 옳은 `97,146,675` 를 그 오타로 덮었다** —
#: 🔴 **정답이 오답이 됐다.**
#:
#: 즉 전사 오류는 사라지지 않고 **답변에서 도구 인자로 자리를 옮길 뿐**이다. 도구
#: 결과가 답변보다 믿을 만한 것은 **도구가 무언가를 계산했을 때뿐**이다.
MIN_TOOL_OPERANDS = 2


def _tool_operands(args: dict) -> list[float]:
    """도구 인자에 실린 수치들. 목록 인자(`values`)는 펼친다."""
    out: list[float] = []
    for value in (args or {}).values():
        for item in (value if isinstance(value, (list, tuple)) else [value]):
            if isinstance(item, bool) or not isinstance(item, (int, float)):
                continue
            out.append(float(item))
    return out


def _numeric_tool_results(results: list[tuple[str, dict, dict]]) -> list[tuple[str, float]]:
    """치환에 쓸 수 있는 `(도구, 값)` 만. 문턱이 셋이다.

    ① **계산 도구의 수치 `value`** 뿐이다. `tools._num_result` 를 쓰는 5종
       (`calc_pct_change`·`calc_share`·`calc_total`·`calc_mean`·`calc_cagr`)만 `value`
       가 수치다. `calc_compare` 는 `"a"`/`"b"`/`"tie"`, `calc_period` 는 `"8년 5개월"`
       이고 검색 도구에는 `value` 키가 아예 없다.
       🔴 **`CALC_TOOLS` 로 한 번 더 거른다** — 검색 도구가 언젠가 `value` 키를 갖게
       되면(자유형 반환이다) 그 값이 조용히 답변에 박힌다.
    ② 🔴 **수치 인자가 `MIN_TOOL_OPERANDS` 미만이면 계산이 아니다** (실측 `Q-047`).
    ③ 🔴 **결과가 인자 중 하나와 같으면 계산한 값이 아니라 통과시킨 값이다.**
       `calc_total({"values": [x, 0]})` 같은 자리다. ②만으로는 안 막힌다.
       대가를 안다 — `calc_pct_change(100, 200) = 100%` 처럼 **정당한 계산이 우연히
       인자와 같은** 경우도 함께 막는다. 답변을 고쳐 쓰는 조작이라 **확신할 수 없으면
       손대지 않는 쪽**을 기본값으로 둔다.
    """
    out: list[tuple[str, float]] = []
    for name, args, payload in results or ():
        if name not in tools.CALC_TOOLS:
            continue
        value = (payload or {}).get("value")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        operands = _tool_operands(args)
        if len(operands) < MIN_TOOL_OPERANDS:
            continue
        if any(abs(float(value) - operand) <= 1e-9 for operand in operands):
            continue
        out.append((name, float(value)))
    return out


def _substitute_tool_results(answer: str, results: list[tuple[str, dict, dict]],
                             trace: list[str]) -> str:
    """도구가 돌려준 값으로 답변의 그 수치를 갈아 끼운다. LLM 0회.

    🔴 **발화하지 않았을 때도 trace 에 적는다.** 안 적으면 "치환이 몇 번 설 수 있었는데
    몇 번 섰는가" 를 사후에 못 세고, 그러면 Δ 를 무엇에 귀속할지가 흐려진다.
    """
    pairs = _numeric_tool_results(results)
    if not pairs:
        return answer
    reports = tool_result_reports(answer, pairs)
    out, substituted = substitute_recomputed(answer, reports, unique_recomputed(reports))
    if substituted:
        trace.append(f'tool_substituted={"; ".join(substituted)}')
    else:
        trace.append(f"tool_substituted=- values={len(pairs)}")
    return out


def _answer_with_tools(messages: list[dict], max_tokens: int, arm, budget: Budget,
                       trace: list[str]) -> str:
    """`A24`/`A24b` — 도구를 내밀고, 모델이 부르면 실행해 결과를 되먹여 다시 부른다.

    🔴 `substitute_tool_results`(A24b)가 켜져 있으면 **마지막에 도구 결과로 답변의
    수치를 치환한다.** 꺼져 있으면(`A24`) 아래 한 줄은 종전과 글자 하나 다르지 않다.
    """
    results: list[tuple[str, dict, dict]] = []
    answer = _tool_round_trip(messages, max_tokens, arm, budget, trace, results)
    if arm.substitute_tool_results:
        answer = _substitute_tool_results(answer, results, trace)
    return answer


def _tool_round_trip(messages: list[dict], max_tokens: int, arm, budget: Budget,
                     trace: list[str], results: list[tuple[str, dict, dict]]) -> str:
    """왕복 본체. 실행한 `(도구, 인자, 결과)` 를 `results` 에 **순서대로 쌓는다**.

    🔴 **인자도 함께 쌓는다** — `A24b` 가 *"도구가 실제로 계산했는가"* 를 인자 수로
    판정한다 (`MIN_TOOL_OPERANDS` · 실측 `Q-047`).

    🔴 **첫 호출이 죽으면 그대로 올려보낸다** — `A8` 과 같은 경로(`UNANSWERABLE` +
    표식)로 나가야 상류 장애와 arm 효과가 섞이지 않는다. 두 번째 회차부터는 이미 받아
    둔 본문이 있으면 그것을 지키고 멈춘다 (`_self_consistent` 와 같은 규약).
    """
    # 🔴 `A24b` 는 계산 7종만 내민다 — `A24` 실측에서 모델이 부른 47건 중 26건이
    #    검색이었고 이미 실린 근거를 다시 읽어 희석시켰다 (`tools.CALC_SPECS` 주석).
    offered = tools.CALC_SPECS if arm.calc_tools_only else tools.TOOL_SPECS
    convo = list(messages)
    best = ""
    for index in range(MAX_TOOL_ROUNDS + 1):
        offer = index < MAX_TOOL_ROUNDS
        if index and budget.remaining()["llm_calls"] <= 0:
            # 🔴 **부르기 전에** 본다. 부르고 나서 세면 크레딧은 이미 나갔다.
            trace.append(f"fc stop=budget_calls rounds={index} kept="
                         f'{"partial" if best else "none"}')
            if best:
                return best
            raise BudgetExceeded("LLM 호출 상한 — function calling 왕복을 마치지 못했다")
        try:
            result = chat(convo, max_tokens=max_tokens, temperature=arm.temperature,
                          tools=offered if offer else None, tag="answer")
            budget.spend_llm(result.prompt_tokens, result.completion_tokens)
        except (BudgetExceeded, RuntimeError) as exc:
            if not best:
                raise
            trace.append(f"fc stop={type(exc).__name__} rounds={index} kept=partial ({exc})")
            return best

        calls = result.tool_calls or []
        trace.append(f"generate fc={index} offered={'y' if offer else 'n'} "
                     f"calls={len(calls)} finish={result.finish} "
                     f"tokens={result.prompt_tokens}+{result.completion_tokens}")
        text = (result.text or "").strip()
        if text:
            best = text
        if not calls:
            return best

        # 🔴 어시스턴트 회차를 **그대로** 되넣는다. `toolCalls` 를 빼면 뒤따르는
        #    `role="tool"` 메시지가 붙을 자리를 잃는다.
        convo.append({"role": "assistant", "content": result.text or "",
                      "toolCalls": calls})
        for call in calls:
            name = ((call.get("function") or {}).get("name")) or ""
            args = _tool_arguments(call)
            out = tools.execute(name, args)
            results.append((name, args, out))
            payload = json.dumps(out, ensure_ascii=False, default=str)
            if len(payload) > MAX_TOOL_RESULT_CHARS:
                payload = payload[:MAX_TOOL_RESULT_CHARS] + "…(잘림)"
            convo.append({"role": "tool", "toolCallId": call.get("id", ""),
                          "name": name, "content": payload})
            trace.append(f'fc tool={name or "?"} '
                         f'{"error" if "error" in out else "ok"} chars={len(payload)}')
    return best


def _offer_tools(arm, question: str, trace: list[str]) -> bool:
    """이 문항에 도구를 열 것인가. **`function_calling` 이 꺼져 있으면 무조건 아니다.**

    🔴 `calc_queries_only`(A24b)가 켜져 있으면 **계산 질의에만** 연다 — `tools` 와
    `maxTokens` 가 배타(40001)라 도구를 내미는 순간 길이 상한이 사라지고, `A24` 는
    도구를 한 번도 안 부른 `Q-106` 까지 그 이유로 잃었다 (`router.needs_calculation`).

    🔴 **판정을 trace 에 남긴다.** 안 남기면 어느 문항이 열렸는지 사후에 셀 수 없다.
    플래그가 꺼진 arm 의 trace 는 **바이트 동일**이다.
    """
    if not arm.function_calling:
        return False
    if not arm.calc_queries_only:
        return True
    kind = router_module.calc_question_kind(question)
    trace.append(f'fc gate={"open:" + kind if kind else "closed"}')
    return bool(kind)


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
        # 🔴 A31 — 기업도 기간도 범위 안이고, 없는 것은 **공시에 안 적힌 미래**다.
        if r.reason == "speculation":
            trace.append("미래 추정 요구 — LLM 호출 없이 한계 고지")
            return _respond(question_id, question, "", trace, SPECULATIVE, budget)
        # 🔴 A32 — 범위 밖이 아니라 **내주지 않는** 것이다.
        if r.reason == "identifier":
            trace.append("식별번호 전사 요구 — LLM 호출 없이 거절")
            return _respond(question_id, question, "", trace, IDENTIFIER_WITHHELD, budget)
        # 🔴 A26 — 범위 밖이 아니라 **무엇을 묻는지 모르는** 것이다. 역질문으로 되묻는다.
        if r.reason == "ambiguous_reference":
            trace.append("지시대상 불명 — LLM 호출 없이 역질문")
            return _respond(question_id, question, "", trace, AMBIGUOUS_REFERENCE, budget)
        trace.append("범위 밖 — LLM 호출 없이 한계 고지")
        return _respond(question_id, question, "", trace, UNANSWERABLE, budget)

    # 🔴 ORACLE 은 여기서 갈린다 — `_gather_evidence` 안에는 `question_id` 가 없다.
    #    플래그가 꺼져 있으면(기본값) 아래 한 줄은 종전과 글자 하나 다르지 않다.
    # 🔴 두 가지 밖에서 만든다 — `ORACLE` 가지에서는 안 채워지므로 `attach_citation` 이
    #    빈 사전을 받고 근거 되읽기로 되돌아간다. 진단용 arm 하나 때문에 `NameError` 를
    #    내면 안 된다.
    provenance: dict[str, dict] = {}
    if ablation.current().golden_oracle:
        evidence, tool_trace = _gather_golden(question_id, clean, budget)
    else:
        evidence, tool_trace = _gather_evidence(r, clean, budget, provenance)
    trace.extend(tool_trace)
    # 근거 성형은 **검색이 끝난 뒤**다. 여기 위로 올리면 A5·L2 의 Δ 에 검색 변화가
    # 섞여 단일 변수 조작이 아니게 된다 (`ablation.shape_evidence` 주석).
    evidence = ablation.shape_evidence(evidence)

    if not evidence:
        trace.append("근거 없음 — LLM 호출 없이 한계 고지")
        return _respond(question_id, question, "", trace, UNANSWERABLE, budget)

    # 🔴 A29a — 집계를 우리가 계산해 **한 줄**로 싣는다. 근거 칸을 안 먹으므로
    #    `A16`(칸 확대)·`A17`(블록 확대)이 기각된 사유에 걸리지 않는다.
    #    배선 자체를 플래그 뒤에 둔다 — 무조건 부르면 플래그를 끈 arm 의 근거도
    #    바뀔 수 있고, 그것이 `A20` 에서 이미 겪은 사고다.
    if ablation.enabled("aggregate_line"):
        from argus.agent import aggregate

        extra = aggregate.line(_evidence_records(evidence), clean, r)
        if extra:
            evidence = f"{evidence}\n\n{extra}"
            trace.append(f"aggregate line=1 chars={len(extra)}")

    try:
        # 🔴 `A8-lean` 은 여기서 갈린다. `enabled()` 로 읽는 이유는 오타가 조용히
        #    False 가 되지 않게 하기 위해서다 — 없는 플래그 이름은 `KeyError` 다.
        #    기본값(False)에서는 아래 두 줄이 종전과 글자 하나 다르지 않다.
        messages = build_answer_messages(
            clean, evidence, r.use_cot,
            lean_rules=ablation.enabled("lean_rules"),
            strict_refusal=ablation.enabled("strict_refusal"))
        max_tokens = 800 if r.use_cot else 300
        arm = ablation.current()
        # 🔴 `A24` 는 여기서 갈린다 — 도구를 내밀고 왕복한다. 기본값(False)에서는 아래
        #    `else` 가지가 종전과 글자 하나 다르지 않다.
        if _offer_tools(arm, clean, trace):
            answer = _answer_with_tools(messages, max_tokens, arm, budget, trace)
        else:
            # 🔴 온도는 이제 `Arm` 이 들고 있다. **기본값이 0.0** 이라 `A10` 이외의 arm 에서
            #    이 호출은 종전과 본문이 바이트 동일이고, 요청 캐시 키도 그대로다.
            result = chat(messages, max_tokens=max_tokens,
                          temperature=arm.temperature, tag="answer")
            budget.spend_llm(result.prompt_tokens, result.completion_tokens)
            answer = result.text
            trace.append(f"generate tokens={result.prompt_tokens}+{result.completion_tokens}")
        if arm.sc_k > 1:
            answer = _self_consistent(messages, max_tokens, arm, answer, budget, trace)
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

    # 🔴 `budget` 을 넘긴다 — `A20r` 의 재생성이 LLM 호출을 하나 더 쓰고, 그것이
    #    게이트 F 의 `budget llm=` 에 잡혀야 한다. 나머지 arm 에서는 안 쓰인다.
    answer = _verify(clean, answer, evidence, r, trace, budget)
    # 🔴 A25 — 근거 표기를 우리가 붙인다. **여기여야 한다**: 모든 LLM 호출이 끝난 뒤라
    #    생성·critic·재생성 프롬프트가 바이트 동일로 남고 `RequestCache` 가 적중한다.
    #    플래그가 꺼져 있으면(기본값) 아래 두 줄은 종전과 글자 하나 다르지 않다.
    # 🔴 A29s — 결정적 집계값으로 답변의 **틀린 합계 하나**를 갈아 끼운다.
    #    `A25`(부착)와 같은 자리다 — 모든 LLM 호출이 끝난 뒤라 생성·critic·재생성
    #    프롬프트가 바이트 동일로 남고 `RequestCache` 가 적중한다. LLM 0회.
    if ablation.enabled("substitute_aggregate"):
        from argus.agent import aggregate

        answer, swapped = aggregate.substitute(
            answer, _evidence_records(evidence), clean, r)
        if swapped:
            trace.append(f"aggregate substitute={swapped}")
    answer, cited = ablation.attach_citation(answer, evidence, provenance=provenance)
    if cited:
        trace.append(f'citation attached={",".join(cited)}')
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
#
# 🔴 **그 설계의 대가를 v4 R0 가 실측했다 — 탐지 100% · 교정 0%.** `Q-097` 의 `per_unit`
# 재계산값 207,684,631,210 은 골든과 바이트까지 같은데 각주로만 붙고 버려졌다. 채점기는
# 그 각주를 일부러 잘라내고 채점한다(`error_analysis.strip_verify_tail`) — 즉 **우리는
# 정답을 계산해 놓고 버리고 있었다.**
#
# 🔴 그래서 기본값을 바꾸지 않고 **arm 으로 만들었다** (`ablation.Arm.substitute_verified`
# = `A20`). 위 설계에는 근거가 있으니 갈아엎지 말고 켰다 껐다 잰다. 플래그가 꺼져 있으면
# 이 함수의 동작은 v3 세대 664 답변 전수에서 **바이트 동일**이다.

_EVIDENCE_RCEPT = re.compile(r"접수번호\s*(\d{14})")


def _evidence_records(evidence: str) -> list[dict]:
    """근거에 실린 접수번호로 정형 레코드를 되찾는다. 검색이 아니라 조회다 (스텝 0)."""
    out = []
    for rcept_no in dict.fromkeys(_EVIDENCE_RCEPT.findall(evidence or "")):
        record = tools._record_of(rcept_no)
        if record is not None:
            out.append(record)
    return out


def _verdict(question: str, answer: str, evidence: str, records: list[dict], r,
             arm) -> dict:
    """답변 **하나**에 대한 검증 판정 한 벌. 부작용이 없다 — trace 도 안 건드린다.

    🔴 `A20r` 이 이것을 **두 번** 부른다 (원본 · 재생성본). 그래서 규칙을 여기 한 곳에만
    둔다 — 재생성 전후를 다른 잣대로 재면 *"여전히 발화하면 원본을 쓴다"* 가 성립하지
    않는다 (`critic.unique_recomputed` 를 한 곳에 둔 것과 같은 이유).

    `records` 를 인자로 받는 것도 같은 이유다. 근거에서 되찾는 정형 레코드는 답변과
    무관하므로 두 번 조회할 일이 없다.
    """
    # 레코드가 여러 건이면 **하나라도 설명되면 통과**다. 체인 안에서 원공시와 정정본의
    # 금액이 갈리므로(227,500 vs 114,800) 전건 일치를 요구하면 정답이 오답이 된다.
    #
    # 🔴 그 관용에 구멍이 있었다 — **자릿수 오류도 함께 묻혔다.** Q-065 는 Dukovany 금액을
    # 10배로 틀렸는데 같은 답변의 신한울 금액이 정확해서 `explained=True` 로 통과했다.
    # 체인 관용은 '같은 값의 다른 시점' 을 위한 것이고 자릿수 오류는 시점 문제가 아니다.
    # 판정을 `critic.aggregate_calculation()` 한 곳으로 모은다 — 규칙이 두 곳에 있으면 갈린다
    # (`scripts/error_analysis.verify_answer_calc` 도 같은 함수를 쓴다).
    reports = [verify_calculation(answer, record) for record in records]
    agg = aggregate_calculation(reports)
    checked, calc_failed = agg["checked"], agg["failed"]

    # 🔴 A20 — `verify_answer_arithmetic` 를 라이브 경로에 배선한다. **플래그 뒤에 둔다.**
    # 이 검사는 근거를 한 줄도 안 읽고 답변이 스스로 쓴 등식·재기술쌍만 본다. 그래서
    # 정형 레코드가 0건인 문항(실측 Q-044·Q-047)에서도 서고, 그런 문항이 위 `reports` 로는
    # 통째로 안 보인다 — v3 까지 이 함수는 `scripts/error_analysis.py` 만 썼다.
    #
    # 🔴 **`reports` 에 합치지 않는다.** `aggregate_calculation` 의 `explained` 는
    # "레코드 하나라도 설명하면 통과" 인데, 이 검사는 레코드가 아니라 답변당 하나다.
    # 안에 넣으면 등식이 맞는 답변이 근거 대조 실패까지 덮어 **체인 관용이 뒤집힌다**
    # (`critic.verify_answer_arithmetic` 머리말 ②). 판정만 OR 로 합친다.
    #
    # 🔴 무조건 배선하면 플래그를 **끈** 상태에서도 각주가 늘어난다 — v3 세대 664 답변
    # 전수 대조로 확인했다(Step 0b). 그 자체가 답변 변화이고 `A20` 의 Δ 와 섞이므로
    # 배선도 플래그 뒤에 둔다.
    #
    # 🔴 **`A20r` 도 이 검사를 켠다.** 재생성에 실을 **사유의 원천**이 필요하고, 실측
    # `Q-044` 는 정형 레코드가 0건이라 이 검사 없이는 발화조차 없다 — 그러면 `A20r` 의
    # 겨냥이 그 문항에서 조용히 사라진다. `A20`·`A20r` 이 같은 판정을 읽고 **처분만**
    # 갈리는 것이 두 arm 을 나란히 재는 설계의 전부다.
    arith_reports: list[dict] = []
    if arm.substitute_verified or arm.regenerate_on_verify:
        arith = verify_answer_arithmetic(answer)
        arith_reports = [arith]
        checked = sorted({*checked, *arith["checked"]})
        calc_failed = calc_failed or not arith["ok"]

    # 🔴 A21 — 수치 출처 게이트. **`calc_failed` 에 합치지 않는다.** 이 검사는 *"이 숫자가
    # 근거에 있는가"* 를 묻고 계산 판정과 축이 다르다 — 합치면 ① `A20` 의 치환이 이 게이트
    # 때문에 발화하고(이 검사는 `recomputed` 가 항상 비어 있어 치환할 값이 없다) ② Δ 가
    # `calculation` 으로 귀속돼 두 arm 의 사유가 섞인다. 별개 `kind` 로 세운다.
    #
    # 🔴 배선을 플래그 뒤에 둔 것은 `A20` 과 같은 이유다 — 무조건 배선하면 플래그를 끈
    # arm 에서도 각주가 늘어나고 그 자체가 답변 변화다 (v3 세대 664 답변 중 23건 발화).
    provenance = verify_number_provenance(answer, evidence) if arm.provenance_gate else None

    complete = verify_completeness(question, answer, evidence, companies=r.companies)

    kinds = sorted({*(["calculation"] if calc_failed else []), *complete["failed_kinds"],
                    *(provenance["failed_kinds"] if provenance else [])})

    # 사유도 `aggregate_calculation` 이 정한 순서를 쓴다 — 자릿수 오류가 앞에 온다.
    # 손으로 첫 사유를 뽑으면 무해한 이웃이 먼저 실려 경고가 약해진다.
    #
    # 🔴 **이 목록이 `A20r` 이 재생성에 싣는 것 그대로다.** 각주와 프롬프트가 같은
    # 목록을 읽어야 "각주로 본 것" 과 "모델에게 준 것" 이 갈리지 않는다. 원천이 둘이다 —
    # ① 계산 불일치(`aggregate_calculation`·`verify_answer_arithmetic`)와
    # ② 🔴 근거에 없는 수(`verify_number_provenance` = `A21` 의 판정).
    reasons = agg["reasons"][:1] if calc_failed else []
    if not reasons and arith_reports and arith_reports[0]["reason"]:
        reasons = [arith_reports[0]["reason"]]      # 레코드가 0건인 문항 (실측 Q-044)
    if complete["reason"]:
        reasons.append(complete["reason"])
    if provenance and provenance["reason"]:
        reasons.append(provenance["reason"])

    return {"reports": reports, "arith_reports": arith_reports, "provenance": provenance,
            "complete": complete, "checked": checked, "calc_failed": calc_failed,
            "kinds": kinds, "reasons": reasons}


def _regenerate_once(question: str, answer: str, evidence: str, records: list[dict],
                     r, arm, trace: list[str], budget: Budget | None,
                     verdict: dict) -> tuple[str, dict]:
    """🔴 `A20r` — 실패 사유를 실어 **딱 한 번** 다시 생성한다. 돌려주는 것은
    (채택한 답변, 그 답변의 판정) 짝이다.

    🔴 **재생성본에 검사가 여전히 발화하면 원본과 원본의 판정을 그대로 돌려준다** —
    그러면 호출부가 종전대로 각주를 붙인다. '더 나빠지면 원본' 을 판정 교체로 표현한
    것이라 두 자리에서 규칙이 갈릴 여지가 없다.

    🔴 **1회로 못 박는다.** 여기서 자기 자신을 다시 부르지 않는 것이 그 상한의 전부다 —
    루프를 두면 문항당 비용 상한이 사라진다 (이 파일 머리말: 고정 횟수 단계).
    """
    if budget is None:
        # 🔴 조용히 건너뛰면 **조용한 Δ 0** 이고, 세지 않고 부르면 게이트 F 가 거짓을
        #    말한다. 둘 다 안 되므로 여기서 멈춘다.
        raise ValueError(
            "regenerate_on_verify 는 예산 계정이 필요합니다 — 재생성 호출을 아무도 세지 "
            "않으면 게이트 F 의 `budget llm=` 이 거짓을 말합니다. `_verify(..., budget=)` "
            "로 넘기십시오.")
    if budget.remaining()["llm_calls"] <= 0:
        # 🔴 **부르기 전에** 본다. 부르고 나서 세면 크레딧은 이미 나갔다.
        trace.append("verify regen=skip_budget (LLM 호출 상한) kept=original")
        return answer, verdict

    use_cot = bool(getattr(r, "use_cot", False))
    try:
        result = chat(build_regeneration_messages(
                          question, evidence, verdict["reasons"], answer,
                          use_cot=use_cot, lean_rules=ablation.enabled("lean_rules")),
                      max_tokens=800 if use_cot else 300,
                      # 🔴 온도 0.0 을 유지한다 — 다양성을 사는 것은 `A10` 의 조작이다.
                      #    `Arm.temperature` 가 생긴 뒤에도 **여기서는 읽지 않는다**:
                      #    `A20r` 이 재는 것은 진단 주입이고, 온도를 함께 올리면 그 Δ 에
                      #    다양성의 몫이 섞여 두 arm 을 나란히 비교할 수 없게 된다.
                      temperature=0.0, tag="regenerate")
        budget.spend_llm(result.prompt_tokens, result.completion_tokens)
    except BudgetExceeded as exc:
        trace.append(f"verify regen=skip_budget ({exc}) kept=original")
        return answer, verdict
    except RuntimeError as exc:
        # 상류 장애로 답변을 잃지 않는다. 다만 **삼키지는 않는다** (`answer_question` 과 같은 규약).
        trace.append(f"{_upstream_mark('regenerate', exc)} verify regen=skip_error kept=original: {exc}")
        return answer, verdict

    candidate = (result.text or "").strip()
    if not candidate:
        trace.append("verify regen=empty kept=original")
        return answer, verdict

    again = _verdict(question, candidate, evidence, records, r, arm)
    kept = "regenerated" if not again["kinds"] else "original"
    trace.append(f'verify regen=1 reasons={"; ".join(verdict["reasons"])} '
                 f'kinds_after={",".join(again["kinds"]) or "-"} kept={kept}')
    return (candidate, again) if kept == "regenerated" else (answer, verdict)


def _verify(question: str, answer: str, evidence: str, r, trace: list[str],
            budget: Budget | None = None) -> str:
    """A6. 결함이 있으면 답변에 경고를 붙이고 trace 에 판정을 남긴다.

    🔴 `substitute_verified`(A20)가 켜져 있으면 경고를 붙이는 대신 **검사가 낸 유일한
    재계산값으로 답변의 그 수치를 치환**한다.
    🔴 `regenerate_on_verify`(A20r)가 켜져 있으면 **실패 사유를 실어 한 번 재생성**한다.
    둘 다 꺼져 있으면 종전 동작 그대로다 — 각주만 붙인다.
    """
    arm = ablation.current()
    if not arm.verifier:
        return answer

    records = _evidence_records(evidence)
    v = _verdict(question, answer, evidence, records, r, arm)

    # 🔴 게이트가 꺼진 arm 의 트레이스는 **바이트 동일**이어야 한다 — 필드를 무조건 찍으면
    #    v3 세대 전 arm 의 `think_trace` 가 달라지고 저장 산출물과 대조가 안 된다.
    gate = (f'provenance_checked={",".join(v["provenance"]["checked"]) or "-"} '
            if v["provenance"] else "")
    trace.append(f'verify calc_checked={",".join(v["checked"]) or "-"} '
                 f'complete_checked={",".join(v["complete"]["checked"]) or "-"} '
                 f'{gate}ok={not v["kinds"]} kinds={",".join(v["kinds"]) or "-"}')
    if not v["kinds"]:
        return answer

    # 🔴 A20r — 각주 대신 **사유를 실어 재생성 1회.** 치환보다 먼저 선다: 재생성이
    #    결함을 지웠으면 치환할 것이 남아 있지 않다. `A20r` 은 `substitute_verified`
    #    를 켜지 않으므로 현 구성에서 둘이 한 답변에 함께 손대는 일은 없다.
    if arm.regenerate_on_verify:
        answer, v = _regenerate_once(question, answer, evidence, records, r, arm,
                                     trace, budget, v)
        if not v["kinds"]:
            return answer

    # 🔴 A20 — 각주 대신 치환한다. 유일성 판정은 `critic.unique_recomputed` 한 곳에만 있고
    # (`aggregate_calculation` 도 같은 함수를 쓴다) 여기서는 **레코드 판정 + 답변 산술
    # 판정의 합집합**으로 부른다. 값이 갈리면 `None` 이 오고 치환은 일어나지 않는다.
    substituted: list[str] = []
    if arm.substitute_verified and v["calc_failed"]:
        pool = v["reports"] + v["arith_reports"]
        answer, substituted = substitute_recomputed(answer, pool, unique_recomputed(pool))
        if substituted:
            trace.append(f'verify substituted={"; ".join(substituted)}')

    # 🔴 치환했으면 그렇게 적는다. 고쳐 놓고 "결함이 발견되었습니다" 라고만 쓰면 각주가
    # 본문과 어긋난다. 접두사 `(주의:` 는 유지한다 — `strip_verify_tail` 이 그것을 읽는다.
    head = ("자동 검증에서 결함이 발견되어 재계산값으로 정정했습니다" if substituted
            else "자동 검증에서 결함이 발견되었습니다")
    return answer + f'\n\n(주의: {head} — {"; ".join(v["reasons"])})'


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
