"""근거 지지 판정. 생성보다 싸고 정확한 검증 수단.

4단 검증이고 앞의 세 단계는 공짜다 (policy §6).
  quick_check         — 근거 표기 정규식        LLM 0회
  verify_calculation  — 결정적 재계산 대조      LLM 0회
  verify_completeness — 근거 완전성 대조        LLM 0회
  judge               — 근거 지지 판정          LLM 1회
"""
from __future__ import annotations

import itertools
import json
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass

from argus.agent import compute
from argus.agent.budget import Budget
from argus.agent.prompts import (CITATION_RE, build_critic_messages,
                                 build_open_judge_messages)
from argus.clova import chat

_JSON = re.compile(r"\{.*\}", re.DOTALL)


@dataclass
class Verdict:
    supported: bool
    has_citation: bool
    hallucination: bool
    reason: str

    @property
    def passed(self) -> bool:
        return self.supported and self.has_citation and not self.hallucination


@dataclass
class OpenVerdict:
    """서술형 채점 결과 (02-golden-set B-5). 3항목을 합산하지 않는다."""
    covers_key_points: bool
    grounded: bool
    no_fabrication: bool
    reason: str

    @property
    def correct(self) -> bool:
        return self.covers_key_points and self.grounded and self.no_fabrication


def quick_check(answer: str) -> dict:
    """LLM 없이 하는 선검사. 여기서 걸리면 critic 호출을 아낀다."""
    return {"has_citation": bool(CITATION_RE.search(answer or ""))}


# --- G. 계산 검증 (LLM 0회) --------------------------------------------------
# policy §4 의 논리를 그대로 뒤집는다 — 계산이 Tool 의 일이면 계산 검증도 Tool 의 일이다.
# 답변에서 수치를 뽑아 compute.py 로 재계산한 값과 대조한다.

_PCT_RE = re.compile(r"(-?\d[\d,]*(?:\.\d+)?)\s*%")
_DUR_RE = re.compile(r"(\d+)\s*년\s*(\d+)\s*개월")
#: 배율 표지의 **단일 원천**. `_MONEY_RE` 와 `_unit_at` 이 같은 목록을 써야 한다 —
#: 같은 목록이 두 곳에 있으면 한쪽만 고쳐진다(이 저장소가 이미 다섯 번 겪은 실패 양식).
#: 🔴 **긴 표지가 앞에 와야 한다.** `만원` 이 `백만원` 앞에 있으면 `백|만원` 으로 갈려
#: 배율이 1e4 로 잡히고 100배를 놓친다. 같은 이유로 `천억원`·`백억원` 이 `억원` 앞이다.
#: **`_SCALE_UNITS` 의 순서가 그 규율의 정본이다** — 아래 `_SCALE_ALT` 는 이 순서를
#: 그대로 이어 붙인다.
#:
#: 🔴 **`천만원` 은 일부러 빼 뒀다 — 넣으면 거짓 경보가 난다.** 저장 답변 2,449건에서
#: `천만 원`·`천만원` 은 13회 쓰이는데 **13회 전부 복합 수사의 꼬리**다
#: (`33조 3,605억 9천만 원` 10회 · `1조 8,287억 5천만 원` · `5,157억 5천만 원` ·
#: `75억 8천만원`). 단독 금액으로 쓰인 예가 **0건**이다. 표지를 넣으면 `_COMPOUND_MONEY_RE`
#: 가 `조…억` 까지만 묶고 남은 꼬리가 **별개의 5천만원(5e7)** 이 되어, 실측 `Q-113`
#: (`v3/ORACLE` 밸류체인)에서 `amount: 답변 5천만 원 vs 재계산 499,950,000,000
#: (10^-4 배 자릿수 오류)` 라는 **거짓 경보 1건**이 새로 났다. 그 답변의 금액은 옳다.
_SCALE_UNITS = ("조원", "천억원", "백억원", "십억원", "억원",
                "백만원", "만원", "천원", "조", "억", "원")

#: 🔴 **표지 **안**의 공백을 허용한다** (v4 `R3b`). 종전에는 수와 표지 **사이**만
#: `\s*` 였고 표지 자체는 붙임형뿐이라, 정기공시가 흔히 쓰는 띄어쓰기 표기를 금액으로
#: **아예 못 봤다.** 저장 답변 2,449건 전수 실측(공백형 대 붙임형):
#:
#:     억 원      1,307 :   526      ← 붙임 `억` 대체 경로가 있어 값은 맞았다
#:     백만 원      608 :    86      🔴 대체 경로가 없다 — 통째로 안 보였다
#:     십억 원       36 :     0      🔴 표지 자체가 목록에 없었다
#:     천 원         19 :     0      🔴
#:     만 원         11 :     0      🔴
#:     천만 원       12 :     1      ⬜ 표지를 **일부러 안 넣는다** (`_SCALE_UNITS` 주석)
#:
#: `억 원`·`조 원` 은 붙임 `억`·`조` 가 같은 배율로 받아 값은 맞았지만 **표지가 원문에서
#: 잘려** 재기술 짝(`X원 (Y억 원)`)의 괄호 닫힘 판정이 서지 못했다 —
#: `_restatement_pairs` 가 `억` 다음의 ` 원)` 을 닫는 괄호로 읽지 못한다.
#:
#: 공백은 **가로 공백만** 허용한다. `\s` 로 넓히면 표 안에서 줄이 갈린 `1,000\n원` 이
#: 하나의 금액이 되어 없던 금액이 생긴다.
_SCALE_SP = "[ \t\u00a0]*"
_SCALE_ALT = "|".join(_SCALE_SP.join(unit) for unit in _SCALE_UNITS)
_MONEY_RE = re.compile(rf"(-?\d[\d,]*(?:\.\d+)?)\s*({_SCALE_ALT})")
_BARE_RE = re.compile(r"-?\d[\d,]*(?:\.\d+)?")
_NUMERIC_CELL = re.compile(r"^-?[\d,]+(?:\.\d+)?$")
_TOTAL_KW = re.compile(r"합계|합산|총계|총액|총합")
_MEAN_KW = re.compile(r"평균")
_WS = re.compile(r"\s+")

#: 답변이 스스로 나눗셈을 한 자리. `1척당`·`1건당`·`1호기당`.
#: **`1` 을 반드시 요구한다.** `건당`·`대당` 만 보면 `사건 당시`·`확대 당시` 같은 평문이
#: 걸려 전제가 헛서고, 그 문장에 건수까지 있으면 정답이 거짓 경보로 뒤집힌다.
_UNITS = "척|건|개|기|호기|대|동|기지"
_PER_UNIT_KW = re.compile(rf"1\s*({_UNITS})\s*당")
_COUNT_KW = re.compile(rf"(\d[\d,]*)\s*({_UNITS})")

#: 🔴 `백만원`·`천억원`·`백억원` 이 빠져 있었다 (2026-08-11 실측으로 보강).
#: 색인 jsonl 전량에서 `백만원` **91회** · `천억원` 7회 · `백억원` 2회가 쓰인다.
#: 빠진 동안 정기공시의 백만원 표가 파서에서 통째로 사라졌다 — SK 의
#: `97,146,675백만원` 이 `_MONEY_RE` 에 안 걸려 Q-047 의 검증이 아예 서지 못했다
#: (A8 88문항 중 답변 12건·근거 21건이 백만원을 쓴다).
#: 🔴 `십억원` 이 빠져 있었다 (v4 `R3b` 실측으로 보강). 저장 답변 2,449건에서 `십억 원`
#: 이 36회 쓰이는데(붙임형 0회) **표지 자체가 목록에 없어서** 대체 경로도 없이 통째로
#: 안 보였다 — `454십억 원` 은 `십원` 도 `억원` 도 매치하지 않아 아무것도 안 걸린다.
#: 실측 용례는 전부 단독 금액이다(`시설자금 454십억 원` · `2,990십억 원`).
#: **`천만원` 은 넣지 않았다** — 사유는 `_SCALE_UNITS` 주석에 있다.
_SCALE = {"원": 1.0, "천원": 1e3, "만원": 1e4, "백만원": 1e6,
          "억": 1e8, "억원": 1e8, "십억원": 1e9, "백억원": 1e10, "천억원": 1e11,
          "조": 1e12, "조원": 1e12}

#: 🔴 **`_SCALE_UNITS` 와 `_SCALE` 이 같은 목록을 말해야 한다.** 한쪽만 늘리면
#: `_money_spans` 가 `KeyError` 로 죽거나(표지만 늘린 경우) 새 표지가 조용히 안 보인다.
assert set(_SCALE_UNITS) == set(_SCALE), sorted(set(_SCALE_UNITS) ^ set(_SCALE))


def _scale_of(marker: str) -> float:
    """표지의 배율. **표지 안의 공백을 지우고** 찾는다 (`백만 원` → `백만원`)."""
    return _SCALE["".join(marker.split())]

#: 반올림 1자리까지 허용한다 (docs/pipeline/07-verifier.md G-2).
ROUND_TOLERANCE = 0.05

#: 계산 불일치는 Phase H Error Analysis 에서 이 단계로 귀속한다.
FAILURE_STAGE = "TOOL"

# --- 자릿수(단위) 오류 — 관용에서 **뺀다** ------------------------------------
# 종전 `_compare` 는 `min(claims, key=|c-expected|)` 로 **답변 수치 중 기대값에 가장
# 가까운 것 하나만** 보고 통과를 판정했다. 그 선택은 두 가지 일을 겹쳐서 하고 있었다.
#
#   ① 어느 수치가 이 레코드를 말한 것인지 고른다      — 필요하다. 근거가 여러 건이면
#      답변은 여러 금액을 말하고, 그중 이 레코드와 무관한 값이 섞이는 것이 정상이다.
#   ② 자릿수가 다른 후보를 '그냥 안 맞는 이웃' 으로 묻는다 — **이것이 결함이다.**
#
# 실측 Q-065. 근거 `4,928,968,079,043원`(49,289.68억)을 답변이 `492,909억원` 으로
# 옮겼다 (배율 10.0002). 같은 답변에 신한울 `23,381억원` 이 정확히 들어 있어서 ①이
# 그 값을 골라 amount 를 통과시켰고, 10배 오류는 판정에 흔적조차 남지 않았다.
# `docs/total_plan.md §0b ③` 이 이 문항의 처방을 두 번 뒤집은 이유가 여기 있다.
#
# **`10^k (k≠0)` 배는 무해한 이웃이 아니다.** 두 계약금액이 우연히 정확히 10배가
# 되는 일은 실측으로 없다 — 정정 체인 865건의 금액쌍 20,586개를 전수로 재 보니 이
# 창(`SCALE_LOG_TOLERANCE`)에 드는 쌍은 2개뿐이고 둘 다 금액이 `8원`·`1원` 인 파싱
# 잔재다 (`SCALE_MIN_VALUE` 로 뺀다). 그래서 자릿수 후보는 **다른 후보가 맞아도**
# 따로 보고하고(`hard`), 레코드 단위 관용으로도 지우지 않는다
# (`aggregate_calculation`).

#: `10^k` 에서 로그10 거리가 이 안이면 자릿수 실수로 본다 (배율 ±2.3%).
#: 실측 두 건이 이 창에 든다 — Q-065 `10.0002배`(거리 1.1e-5) ·
#: Q-047 `10.08배`(거리 3.6e-3, `333조 6,059억원` → `33,636,059억원`).
SCALE_LOG_TOLERANCE = 0.01

#: 이보다 작은 값은 자릿수 판정에서 뺀다. `8원`·`1원` 같은 파싱 잔재가 아무 큰 값과도
#: 10^k 관계를 맺어 버린다 (실측 20230914800071 `amount=8`, 20240402800113 `amount=1`).
SCALE_MIN_VALUE = 1e6

#: `10^7` 배를 넘는 관계는 단위 실수가 아니라 다른 값이다 (원↔조 가 10^12 이므로 넉넉하다).
SCALE_MAX_EXP = 7


def _scale_exponent(claimed: float, expected: float) -> int | None:
    """`claimed` 가 `expected` 의 `10^k (k≠0)` 배인가. 맞으면 k, 아니면 None."""
    if not claimed or not expected:
        return None
    if abs(claimed) < SCALE_MIN_VALUE or abs(expected) < SCALE_MIN_VALUE:
        return None
    if (claimed > 0) != (expected > 0):
        return None
    distance = math.log10(abs(claimed) / abs(expected))
    exponent = round(distance)
    if exponent == 0 or abs(exponent) > SCALE_MAX_EXP:
        return None
    if abs(distance - exponent) > SCALE_LOG_TOLERANCE:
        return None
    return exponent


#: 🔴 **A20 — 치환할 자리를 고르는 창.** 기대값과 로그10 거리가 이 안이면 "답변이 이 수치를
#: 말하려 한 자리" 로 본다 (배율 ±12%). 하나만 바꾸면 안 되기 때문에 있는 상수다 — 실측
#: Q-097 의 답변은 같은 값을 **두 표기로** 쓴다(`207,848,465,710원` 과 `약 2,078억 원`).
#: `_compare` 는 그중 기대값에 가장 가까운 하나만 불일치로 보고하므로, 보고된 literal 만
#: 갈아 끼우면 나머지 표기가 남아 **답변이 스스로 모순된다.**
#:
#: 창 밖은 건드리지 않는다. 같은 답변의 총액 `2,492,215,574,520원` 은 기대값의 12배
#: (로그10 거리 1.08)이고, 다른 계약의 금액은 자릿수부터 다르다.
SUBSTITUTE_LOG_TOLERANCE = 0.05


def _to_float(text) -> float | None:
    if isinstance(text, (int, float)):
        return float(text)
    if not isinstance(text, str) or not _NUMERIC_CELL.match(text.strip()):
        return None
    try:
        return float(text.strip().replace(",", ""))
    except ValueError:
        return None


def _half_ulp(literal: str, scale: float = 1.0) -> float:
    """답변이 쓴 자릿수의 절반. '227,648억원' 은 ±5천만원까지 같은 값이다."""
    decimals = len(literal.split(".")[1]) if "." in literal else 0
    return 0.5 * (10.0 ** -decimals) * scale


def _percent_claims(answer: str) -> list[tuple[float, float, str]]:
    out = []
    for m in _PCT_RE.finditer(answer):
        value = _to_float(m.group(1))
        if value is not None:
            out.append((value, max(ROUND_TOLERANCE, _half_ulp(m.group(1))), m.group(0)))
    return out


#: 🔴 **우리 프롬프트가 시키는 표기를 우리 검증기가 못 읽고 있었다.**
#: 규칙 9 가 조 단위를 `4조 9,290억원` 으로 쓰라고 하는데(W1 이 콤마 밀림을 막으려고
#: 도입), `_MONEY_RE` 는 그것을 `4조`(=4e12)와 `9,290억원`(=9.29e11) **두 조각**으로
#: 쪼갠다. 그래서 정답에 자릿수 거짓 경보가 났다 — 현 세대 `hard` 발화 3건 중 2건
#: (vc Q-107·Q-109)이 그것이다. `aggregate_calculation` 이 hard 를 일부러 관용에서
#: 빼 놓았으므로 완충이 없다.
#: 복합 표기를 **먼저** 잡아 하나의 값으로 합친다.
_COMPOUND_MONEY_RE = re.compile(
    r"(-?\d[\d,]*)\s*조\s*(\d[\d,]*)\s*억\s*원?")


def _money_spans(answer: str) -> list[tuple[int, int, float, float, str]]:
    """금액 표기를 **위치까지** 붙여 돌려준다 — `(시작, 끝, 값, 허용오차, 원문)`.

    `_money_claims` 가 값만 쓰던 것을 위치가 필요한 검사(`_restatement_mismatches`)와
    함께 쓰려고 갈랐다. 파싱 규칙은 한 곳에만 둔다.
    """
    out: list[tuple[int, int, float, float, str]] = []
    seen: list[tuple[int, int]] = []
    for m in _COMPOUND_MONEY_RE.finditer(answer or ""):
        jo, eok = _to_float(m.group(1)), _to_float(m.group(2))
        if jo is None or eok is None:
            continue
        value = jo * 1e12 + eok * 1e8
        out.append((m.start(), m.end(), value, _half_ulp(m.group(2), 1e8), m.group(0)))
        seen.append(m.span())
    for m in _MONEY_RE.finditer(answer or ""):
        # 복합 표기 안의 조각(`4조`·`9,290억원`)을 두 번 세지 않는다.
        if any(s <= m.start() and m.end() <= e for s, e in seen):
            continue
        value = _to_float(m.group(1))
        if value is not None:
            scale = _scale_of(m.group(2))
            out.append((m.start(), m.end(), value * scale,
                        _half_ulp(m.group(1), scale), m.group(0)))
    return sorted(out)


def _money_claims(answer: str) -> list[tuple[float, float, str]]:
    return [(value, tol, literal) for _, _, value, tol, literal in _money_spans(answer)]


def money_values(text: str) -> list[float]:
    """텍스트가 말한 금액의 **값만**. `_money_spans` 의 공개 창구다.

    🔴 채점기(`scripts/error_analysis.py`)가 `_money_spans` 를 직접 부르면 금액 파싱
    규칙을 읽는 자리가 둘이 된다. 표지 목록과 마찬가지로 창구를 하나로 둔다.
    """
    return [value for _, _, value, _, _ in _money_spans(text or "")]


def _bare_claims(answer: str) -> list[tuple[float, float, str]]:
    out = []
    for m in _BARE_RE.finditer(answer):
        value = _to_float(m.group(0))
        if value is not None:
            out.append((value, max(ROUND_TOLERANCE, _half_ulp(m.group(0))), m.group(0)))
    return out


def _fields(evidence) -> dict:
    """도구 반환 형태(`{"count":.., "events":[..]}`)와 단일 레코드를 모두 받는다."""
    if not isinstance(evidence, dict):
        return {}
    for key in ("events", "records", "chunks", "filings"):
        rows = evidence.get(key)
        if isinstance(rows, list) and rows and isinstance(rows[0], dict):
            merged = dict(rows[0])
            merged.update({k: v for k, v in evidence.items() if k != key})
            return merged
    return evidence


def _pick(fields: dict, *keys) -> float | None:
    for key in keys:
        value = fields.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
    return None


def _squash(text) -> str:
    """공백을 지운 사본. 답변은 `**총 조달 금액**` 처럼 레이블을 띄어 쓴다."""
    return _WS.sub("", text if isinstance(text, str) else "")


def _says_amount(answer: str, label) -> bool:
    """답변이 **이 레코드의 금액**을 말하고 있는가. 레이블(`amount_type`)로만 가른다.

    A6 실측 거짓 경보(Q-014). 근거 레코드는 유상증자 `조달금액` 2,000,113,200,000 이고
    답변은 질문이 물은 **하위 항목** `시설자금` 454,100,000,000원을 정확히 옮겼다.
    그런데 `_money_claims` 는 답변의 금액 표기를 **전부** 긁어 amount 와 대조하므로
    정답이 `TOOL` 오류로 뒤집혔다. 답변이 레코드의 금액 레이블을 한 번도 말하지
    않았다면 그 금액을 주장한 적이 없는 것이다 — 틀린 게 아니라 `skipped` 다.

    레이블이 없는 근거(`amount_type` 미기재·테스트 픽스처)는 판단할 재료가 없으므로
    게이트를 걸지 않는다. **확신할 수 없으면 검사하는 쪽이 아니라 통과시키는 쪽**이
    기본값이지만, 여기서는 게이트 자체를 못 세우는 것이라 종전 동작을 유지한다.
    """
    if not isinstance(label, str) or not label:
        return True
    return _squash(label) in _squash(answer)


def _targets(expected: float, claims: list[tuple[float, float, str]]) -> list[str]:
    """기대값과 **같은 자릿수**인데 값이 다른 후보의 원문. A20 이 갈아 끼울 자리다.

    불일치 보고(`reason`)는 후보 하나만 고르지만, 치환은 답변에 남은 같은 값의 **다른
    표기까지** 함께 바꿔야 한다 (`SUBSTITUTE_LOG_TOLERANCE` 주석 — 실측 Q-097).
    """
    out = []
    for value, tol, literal in claims:
        if abs(value - expected) <= tol + 1e-9:
            continue                          # 맞는 후보다 — 건드리지 않는다
        if not value or not expected or (value > 0) != (expected > 0):
            continue
        if abs(math.log10(abs(value) / abs(expected))) > SUBSTITUTE_LOG_TOLERANCE:
            continue                          # 다른 수치다 (총액·다른 계약)
        out.append(literal)
    return list(dict.fromkeys(out))


def _revenue(fields: dict) -> float | None:
    """비중의 분모. 명시 키가 없으면 원 표(raw_rows)의 '최근매출액' 을 쓴다."""
    explicit = _pick(fields, "revenue", "whole", "share_whole", "ratio_denominator")
    if explicit:
        return explicit
    for row in fields.get("raw_rows") or []:
        cells = [str(c) for c in row]
        if not any("최근매출액" in c for c in cells):
            continue
        for cell in cells:
            value = _to_float(cell)
            if value:
                return value
    return None


def verify_calculation(answer: str, evidence: dict) -> dict:
    """답변에서 수치를 파싱해 compute.py 로 재계산하고 대조한다. LLM 0회.

    ① 답변 텍스트에서 수치·단위 추출 (`22,764,764,160,000원`·`7.6%`·`8년 5개월`)
    ② 근거 레코드 원값으로 재계산
    ③ 허용 오차(반올림 1자리) 밖이면 불일치로 보고하고 `TOOL` 실패로 귀속

    답변이 해당 수치를 아예 말하지 않았으면 불일치가 아니라 `skipped` 다 —
    검증할 대상이 없는 것과 틀린 것을 섞으면 Phase H 의 병목 판정이 오염된다.

    실측 근거: HCX-005 는 2025-07-24 ~ 2033-12-31 계약기간을 Direct·CoT 모두
    `9년 5개월` 로 답했다 (정답 `8년 5개월`). judge 로는 잡히지 않는 오류다.
    """
    text = answer or ""
    fields = _fields(evidence)
    checked: list[str] = []
    skipped: list[str] = []
    gated: list[str] = []
    mismatches: list[dict] = []

    def _compare(kind: str, expected: float, claims: list[tuple[float, float, str]],
                 detail: str, *, scale_guard: bool = False) -> None:
        """후보 중 하나가 맞으면 통과 — 단, **자릿수가 다른 후보는 따로 보고한다.**

        `scale_guard` 는 금액에만 건다. 비율은 `30.36%` 와 `3.11%` 처럼 **서로 다른
        레코드의 정당한 값**이 10배 근처에 흔히 놓이므로 같은 규칙을 걸 수 없다.
        """
        if not claims:
            skipped.append(kind)
            return
        checked.append(kind)

        if scale_guard:
            for value, tol, literal in claims:
                exponent = _scale_exponent(value, expected)
                if exponent is not None:
                    mismatches.append({
                        "kind": kind, "claimed": literal, "claimed_value": value,
                        "expected": expected, "tolerance": tol, "detail": detail,
                        "scale": exponent, "hard": True, "targets": [literal]})

        if any(abs(value - expected) <= tol + 1e-9 for value, tol, _ in claims):
            return
        if any(m["kind"] == kind and m.get("hard") for m in mismatches):
            return                       # 같은 값을 자릿수 오류로 이미 보고했다
        value, tol, literal = min(claims, key=lambda c: abs(c[0] - expected))
        mismatches.append({"kind": kind, "claimed": literal, "claimed_value": value,
                           "expected": expected, "tolerance": tol, "detail": detail,
                           "targets": _targets(expected, claims)})

    # ① 계약기간 — LLM 이 가장 자주 틀리는 영역
    start, end = fields.get("period_start"), fields.get("period_end")
    if start and end:
        expected_text = compute.period_text(str(start), str(end))
        found = [(int(y), int(mo)) for y, mo in _DUR_RE.findall(text)]
        if not (expected_text and found):
            skipped.append("period")
        else:
            checked.append("period")
            parts = compute.period_parts(str(start), str(end))
            if parts not in found:
                y, mo = found[0]
                mismatches.append({
                    "kind": "period", "claimed": f"{y}년 {mo}개월",
                    "claimed_value": y * 12 + mo, "expected": expected_text,
                    "expected_value": parts[0] * 12 + parts[1], "tolerance": 0,
                    # 기대값이 `"8년 5개월"` 이라 수치 치환의 대상이 아니다 (A20 제외).
                    "detail": f"{start} ~ {end}", "targets": []})

    # ② 금액 — 자릿수 실수를 잡는다. 단, 답변이 **이 레코드의 금액을 말했을 때만**
    #    (`_says_amount`). 근거 한 건에는 조달금액 밑에 시설자금·타법인증권취득자금이
    #    같이 실려 있어서, 레이블을 안 보면 하위 항목을 말한 정답이 오답이 된다.
    amount = _pick(fields, "amount")
    if amount is not None:
        if _says_amount(text, fields.get("amount_type")):
            _compare("amount", amount, _money_claims(text), "근거 레코드 amount",
                     scale_guard=True)
        else:
            # 🔴 I-2. **게이트가 닫힌 것을 `skipped` 만으로 적으면 침묵과 구별되지 않는다.**
            # "답변이 그 수치를 말하지 않았다"(정상)와 "레이블이 안 맞아 검사를 껐다"
            # (판단을 포기했다)는 서로 다른 사건이다. 뒤엣것은 세어야 한다 — 실측
            # v3/A8 에서 amount 를 가진 레코드 149건 중 **64건**이 이 자리에서 꺼진다.
            skipped.append("amount")
            gated.append("amount")

    # ②b 단위당 값 — **답변이 스스로 한 나눗셈**을 다시 한다.
    #    실측 Q-097: 투자금액 2,492,215,574,520원 / 12척 = 207,684,631,210원인데
    #    답변은 `207,838,797,710원` 을 냈다. 총액은 정확히 옮겼으므로 `amount` 는
    #    통과하고, 정작 질문이 물은 값만 틀린다 — 종전 검증에 이 축이 아예 없었다.
    #    분자는 이 레코드의 amount 이므로 `_says_amount` 게이트를 같이 통과해야 한다.
    if amount is not None and _says_amount(text, fields.get("amount_type")):
        unit = _PER_UNIT_KW.search(text)
        if unit:
            counts = sorted({int(m.group(1).replace(",", ""))
                             for m in _COUNT_KW.finditer(text)
                             if m.group(2) == unit.group(1)
                             and int(m.group(1).replace(",", "")) > 1})
            if len(counts) == 1:
                _compare("per_unit", amount / counts[0], _money_claims(text),
                         f"{compute.fmt_num(amount)} / {counts[0]}{unit.group(1)}")
            else:
                # 나눌 수를 못 박지 못했다. 틀렸다고 볼 재료가 없으므로 통과시킨다.
                skipped.append("per_unit")

    # ③ 비중 — 계약금액 / 최근매출액 으로 다시 계산한다
    part = _pick(fields, "share_part", "part", "amount")
    whole = _revenue(fields)
    ratio = _pick(fields, "ratio")
    fmt = compute.fmt_num
    if part is not None and whole:
        _compare("share", compute.share(part, whole), _percent_claims(text),
                 f"{fmt(part)} / {fmt(whole)} × 100")
    elif ratio is not None:
        _compare("share", ratio, _percent_claims(text), "근거 레코드 ratio")

    # ④ 증감률
    before, after = _pick(fields, "before"), _pick(fields, "after")
    if before is not None and after is not None and before != 0:
        _compare("pct_change", compute.pct_change(before, after), _percent_claims(text),
                 f"({fmt(after)} - {fmt(before)}) / {fmt(before)} × 100")

    # ⑤ CAGR
    begin, cagr_end, years = _pick(fields, "begin"), _pick(fields, "end"), _pick(fields, "years")
    if begin is not None and cagr_end is not None and years and begin > 0 and cagr_end >= 0:
        _compare("cagr", compute.cagr(begin, cagr_end, years), _percent_claims(text),
                 f"(({fmt(cagr_end)} / {fmt(begin)})^(1/{fmt(years)}) - 1) × 100")

    # ⑥ 합계·평균 — 같은 values 를 공유하므로 답변이 그 말을 했을 때만 검사한다
    values = fields.get("values")
    if isinstance(values, list) and values and all(isinstance(v, (int, float)) for v in values):
        if _TOTAL_KW.search(text):
            _compare("total", compute.total(values), _bare_claims(text), "values 합계")
        else:
            skipped.append("total")
        if _MEAN_KW.search(text):
            _compare("mean", compute.mean(values), _bare_claims(text), "values 평균")
        else:
            skipped.append("mean")

    failed = sorted({m["kind"] for m in mismatches})
    hard = sorted({m["kind"] for m in mismatches if m.get("hard")})
    return {
        "ok": not mismatches,
        "llm_calls": 0,
        "checked": checked,
        "skipped": skipped,
        "gated": gated,
        "mismatches": mismatches,
        "failed_kinds": failed,
        "hard_kinds": hard,
        "failure_stage": FAILURE_STAGE if mismatches else None,
        "reason": "; ".join(_mismatch_reason(m) for m in mismatches),
        # 🔴 A20 — 재계산값을 **구조화된 필드로** 함께 낸다. 종전에는 `_show(expected)` 로
        # `reason` 문장에만 박혀 있어서 치환에 쓸 수가 없었다 (탐지 100% · 교정 0%).
        # `reason` 의 형식은 한 글자도 바꾸지 않는다 — `scripts/error_analysis.py` 와
        # 게이트 문서가 그것을 읽는다.
        "recomputed": _recomputed(mismatches),
    }


def _recomputed(mismatches: Sequence[dict]) -> dict[str, float]:
    """**치환에 쓸 수 있는** 재계산값만 검사 종류별로 모은다. 조건이 둘이다.

    ① 기대값이 수치다 — `period` 의 기대값은 `"8년 5개월"` 이라 갈아 끼울 수가 없다.
    ② 답변에서 **바꿀 자리**(`targets`)를 못 박았다 — `restatement` 는 원값과 괄호
       환산값 중 **어느 쪽이 틀린지 검사가 모른다.** 실측 Q-018 은 괄호가 옳고 원값이
       13자리에서 한 자리 틀렸지만, 그 판정은 이 검사의 재료 밖이다 (계획서도 Q-018 을
       `A15` 로 돌렸다 — *"재계산이 중간값이라 A20 로는 안 됨"*).

    같은 종류에 값이 갈리면 그 종류를 뺀다. `aggregate_calculation` 이 레코드 사이에서
    같은 판정을 한 번 더 하고, 이유도 같다 — 어느 쪽으로 치환해도 근거가 없다.
    """
    values: dict[str, set[float]] = {}
    for mismatch in mismatches:
        expected = mismatch.get("expected")
        if isinstance(expected, bool) or not isinstance(expected, (int, float)):
            continue
        if not mismatch.get("targets"):
            continue
        values.setdefault(mismatch["kind"], set()).add(float(expected))
    return {kind: value.pop() for kind, value in values.items() if len(value) == 1}


def _mismatch_reason(mismatch: dict) -> str:
    text = (f"{mismatch['kind']}: 답변 {mismatch['claimed']} "
            f"vs 재계산 {_show(mismatch['expected'])}")
    exponent = mismatch.get("scale")
    if exponent:
        text += f" (10^{exponent:+d} 배 자릿수 오류)"
    return text


def aggregate_calculation(reports: Sequence[dict]) -> dict:
    """레코드별 판정을 하나로 묶는다. LLM 0회. **관용은 지키고 단위 오류만 뺀다.**

    호출부(`pipeline._verify` · `scripts/error_analysis.verify_answer_calc`)가 손으로
    쓰던 세 줄을 여기로 모은다. 종전 규칙은 이랬다.

        explained = any(rep["ok"] and rep["checked"] for rep in reports)
        calc_failed = bool(checked) and not explained

    **`explained` 의 관용은 그대로 둔다.** 정정 체인은 원공시와 정정본의 금액이 다른
    것이 정상이라(227,500 vs 114,800) 전건 일치를 요구하면 정당한 통과가 깨진다
    (`tests/test_error_analysis.py::test_calc_passes_when_any_golden_record_explains_the_answer`).

    바뀌는 것은 하나다. **자릿수 오류(`hard_kinds`)는 다른 레코드가 설명해도 살아남는다.**
    실측 Q-065 가 정확히 그 구멍으로 나갔다 — Dukovany 금액을 10배로 틀렸는데 같은
    답변의 신한울 금액이 정확해서 `explained=True` 가 되고 판정이 통과했다. 체인 관용은
    '같은 값의 다른 시점' 을 위한 것이고, 자릿수 오류는 시점 문제가 아니다.

    🔴 **A20 — `recomputed` 를 함께 낸다.** 검사 종류를 키로 하는 재계산값이고,
    **유일하지 않으면 `None`** 이다 (`unique_recomputed`).
    """
    rows = list(reports or ())
    checked = sorted({k for rep in rows for k in rep.get("checked") or ()})
    hard = sorted({k for rep in rows for k in rep.get("hard_kinds") or ()})
    explained = any(rep.get("ok") and rep.get("checked") for rep in rows)
    reasons = ([rep["reason"] for rep in rows if rep.get("hard_kinds") and rep.get("reason")]
               + [rep["reason"] for rep in rows
                  if not rep.get("hard_kinds") and rep.get("reason")])
    return {
        "llm_calls": 0,
        "n_records": len(rows),
        "checked": checked,
        # I-2 — 게이트가 검사를 끈 레코드 수. **0 이 아니면 그만큼 못 본 것이다.**
        "n_gated": sum(1 for rep in rows if rep.get("gated")),
        "explained": explained,
        "hard_kinds": hard,
        "failed": bool(hard) or (bool(checked) and not explained),
        "failure_stage": FAILURE_STAGE if (hard or (checked and not explained)) else None,
        "reasons": reasons[:3],
        "recomputed": unique_recomputed(rows),
    }


def unique_recomputed(reports: Sequence[dict]) -> dict[str, float] | None:
    """레코드들이 낸 재계산값이 **유일한가.** 갈리면 `None` — 치환하지 않는다.

    🔴 값이 갈리면 치환하지 않는다. `explained` 관용과 같은 이유다 — 정정 체인은
    원공시와 정정본 금액이 다른 것이 정상이고(227,500 vs 114,800), 어느 쪽으로
    치환해도 정당한 통과가 깨진다
    (`tests/test_error_analysis.py::test_calc_passes_when_any_golden_record_explains_the_answer`).

    🔴 `aggregate_calculation` 과 `pipeline._verify` 가 **같은 함수를 쓴다.** 유일성
    규칙이 두 곳에 있으면 갈린다 — 이 저장소가 열 번 겪은 실패 양식이다. 파이프라인은
    레코드 판정에 답변 산술 판정(`verify_answer_arithmetic`)을 더한 합집합으로 부른다.
    """
    values: dict[str, set[float]] = {}
    for rep in reports or ():
        for kind, value in (rep.get("recomputed") or {}).items():
            values.setdefault(kind, set()).add(value)
    return ({k: v.pop() for k, v in values.items() if len(v) == 1}
            if values and all(len(v) == 1 for v in values.values()) else None)


def _show(expected) -> str:
    """trace 에 실릴 기대값. 부동소수 17자리를 그대로 흘리지 않는다."""
    if isinstance(expected, (int, float)) and not isinstance(expected, bool):
        return compute.fmt_num(round(float(expected), 1))
    return str(expected)


# --- G. 답변의 내적 일관성 (LLM 0회) ------------------------------------------
# `verify_calculation` 은 **근거 레코드 대조**다. 그래서 레코드가 없으면 아무것도 못 본다 —
# 정기공시 문항은 `tools._record_of` 가 정형 레코드를 돌려주지 않는다. 실측 Q-044·Q-047 은
# 근거 접수번호가 2건인데 **레코드가 0건**이라 계산 검증이 통째로 서지 못했다.
#
# 여기서 보는 축은 다르다. **답변이 스스로 쓴 두 표기가 서로 어긋나는가.** 근거를 한 줄도
# 읽지 않으므로 레코드가 0건이어도 선다. LLM 0회다.
#
#   equation     인라인 등식      `1,699,923 + 665,945 = 1,365,868`     (실측 Q-044)
#   restatement  원값↔환산값 짝   `1,843,200,000,000원 (1조 8,422억원)` (실측 Q-018)
#
# 🔴 **`restatement` 는 규칙 8·9 가 만든 짝을 되받는 검사다.** W1 이 조 단위 콤마 밀림
# (10^k)을 막으려고 원값과 조·억 환산값을 **함께 쓰게** 했고, 그 순간 두 표기가 어긋날 수
# 있는 자리가 새로 생겼다. Q-018 이 정확히 그것이다 — `1조 8,422억원` 은 근거와 바이트까지
# 같고 **원 단위 13자리에서 한 자리만** 틀렸다(10^0). `_compare` 는 후보 중 하나만 맞으면
# 통과시키므로 **맞는 쪽이 틀린 쪽을 가려 준다.** 답변 안에서 둘을 직접 맞대야 보인다.
#
# 🔴 겨냥과 회귀 위험을 함께 적는다 (저장 응답 614건 재채점 · LLM 0회 · 지출 0원).
#
#   equation     현 세대 264 답변에서 등식 20개 · 불일치 3개(전부 Q-044 의 같은 등식)
#                옛 세대까지 614 답변 52개 등식 중 불일치 12개, 그중 정답 문항은
#                phase_h·A6 의 Q-034 뿐이다 — 그 답변의 합계는 **실제로 틀렸고**
#                (817,100 vs 817,900) 서술형 판정기가 통과시킨 것이다
#   restatement  614 답변에서 재기술쌍 87개 · 불일치 5개 · **정답 문항 0건**
#
# 허용 오차는 **표기 단위 1칸**이다(반이 아니다). 모델은 반올림도 절사도 하므로
# `2,590,850,129,043원 (2조 5,908억원)`(=25,908.50억을 절사) 같은 정당한 표기가 반칸
# 창에서는 거짓 경보가 됐다 — 실측 Q-065(정답)가 그 한 건이었다.

#: 등식은 **덧셈·뺄셈 사슬만** 본다. `×`·`÷` 가 섞이면 좌→우 계산이 연산 우선순위를
#: 틀리게 하고, 실측 614 답변의 등식 52개에 ×·÷ 사슬은 한 건도 없다. 사슬이 끊기면
#: 정규식이 아예 매치하지 않으므로 **조용히 안 보는 쪽**으로 실패한다.
#:
#: 🔴 **`=` 뒤의 강조 표기를 통과시킨다** (`[\s*_]*`). 종전에는 `\s*` 만 허용해서
#: `… = **4,967,110,535,840원**` 처럼 결과를 굵게 감싼 등식이 **아예 매치하지 않았다** —
#: 사슬이 끊긴 것과 구별되지 않고 조용히 안 보였다. 실측 Q-107(밸류체인 오답)이 그것이다:
#: 좌변합 5,047,859,524,850 vs 우변 4,967,110,535,840 으로 **덧셈이 실제로 틀렸는데**
#: `calc_checked` 에 `equation` 이 없었다. 같은 파일의 `_RESTATE_OPEN` 이 이미 같은 이유로
#: 같은 문자류를 쓴다 (아래 — 재기술 짝도 `**…원**(…)` 로 굵게가 끼어든다).
#: 저장 응답 878건 전수 대조: **신규 적중 3건**(vc/A8 Q-107 · vc/A8-lean Q-107 ·
#: vc/ORACLE Q-115 — 전부 이미 오답) · **적중 상실 0** · **정답 문항 거짓 경보 0**.
_EQ_NUM = r"-?\d[\d,]*(?:\.\d+)?"
_EQ_OP = r"[+\-−]"
_EQUATION_RE = re.compile(rf"({_EQ_NUM})((?:\s*{_EQ_OP}\s*{_EQ_NUM})+)\s*=[\s*_]*({_EQ_NUM})")
_EQ_TERM = re.compile(rf"({_EQ_OP})\s*({_EQ_NUM})")

#: 수 바로 뒤에 붙은 배율 표지. `_MONEY_RE` 와 **같은 목록**을 쓴다.
_UNIT_AT = re.compile(rf"\s*({_SCALE_ALT})")

#: 재기술 짝의 경계. `**276,900,000,000원**(2,769억원)` 처럼 굵게 표기가 끼어든다.
_RESTATE_OPEN = re.compile(r"^[\s*_]*\($")
_RESTATE_CLOSE = re.compile(r"^[\s*_]*\)")

#: 답변의 내적 일관성 검사 종류. `verify_calculation` 과 같은 이유로 `TOOL` 에 귀속한다 —
#: 옮겨 적기와 덧셈은 생성이 아니라 **계산 Tool 이 대신했어야 할 일**이다.
ARITHMETIC_KINDS = ("equation", "restatement")


def _unit_at(text: str, pos: int) -> str:
    """수 뒤의 배율 표지. **공백을 지운 정규형**으로 돌려준다 — 같은 등식이 한쪽은
    `817,100백만원`, 다른 쪽은 `817,100백만 원` 이면 종전에는 *단위가 섞였다* 로 읽혀
    검사가 조용히 통과했다 (`_equation_mismatches` 의 `units` 비교)."""
    match = _UNIT_AT.match(text, pos)
    return "".join(match.group(1).split()) if match else ""


def _equation_mismatches(text: str) -> list[dict]:
    """답변이 스스로 쓴 `a ± b ± c = d` 를 다시 계산한다."""
    out: list[dict] = []
    for m in _EQUATION_RE.finditer(text):
        total = _to_float(m.group(1))
        claimed = _to_float(m.group(3))
        if total is None or claimed is None:
            continue
        units = {_unit_at(text, m.end(1)), _unit_at(text, m.end(3))}
        tolerance = 2 * (_half_ulp(m.group(1)) + _half_ulp(m.group(3)))
        broken = False
        for term in _EQ_TERM.finditer(m.group(2)):
            value = _to_float(term.group(2))
            if value is None:
                broken = True
                break
            total = total + value if term.group(1) == "+" else total - value
            tolerance += 2 * _half_ulp(term.group(2))
            units.add(_unit_at(text, m.start(2) + term.end(2)))
        if broken:
            continue
        # 단위가 섞인 등식(`72,200 + … = 817,100백만원` 은 같은 단위지만
        # `100억 + 50억 = 15,000,000,000원` 은 환산이다)은 좌·우변이 달라도 정당하다.
        if len({u for u in units if u}) > 1:
            continue
        if abs(total - claimed) > tolerance + 1e-9:
            out.append({"kind": "equation", "claimed": " ".join(m.group(0).split()),
                        "claimed_value": claimed, "expected": total,
                        "tolerance": tolerance, "detail": "답변이 쓴 등식", "hard": True,
                        # 🔴 A20 의 치환 자리는 **우변만**이다. `claimed` 는 공백을
                        # 접은 등식 전문이라 답변의 부분문자열이 아니다 (실측 Q-044 는
                        # `$$…\\text{억원}$$` 안에 있다). 좌변 항들은 답변이 근거에서
                        # 옮겨 적은 값이고, 틀린 것은 그것들의 합이다.
                        "targets": [m.group(3)]})
    return out


def _restatement_pairs(text: str) -> list[tuple]:
    """`X원 (Y억원)` 꼴로 붙어 있는 금액 짝. **전제가 몇 번 섰는지**를 따로 센다."""
    spans = _money_spans(text)
    return [(a, b) for a, b in zip(spans, spans[1:])
            if _RESTATE_OPEN.match(text[a[1]:b[0]])
            and _RESTATE_CLOSE.match(text[b[1]:b[1] + 4])]


def _restatement_mismatches(text: str) -> list[dict]:
    """짝지어진 두 표기가 같은 금액인가. 규칙 8·9 가 만든 짝을 되받는다."""
    out: list[dict] = []
    for first, second in _restatement_pairs(text):
        tolerance = 2 * (first[3] + second[3])
        if abs(first[2] - second[2]) > tolerance + 1e-9:
            out.append({"kind": "restatement",
                        "claimed": f"{first[4]} ({second[4]})",
                        "claimed_value": first[2], "expected": second[2],
                        "tolerance": tolerance, "detail": "원값과 괄호 환산값이 다르다",
                        # 🔴 **치환 자리가 없다.** 원값과 괄호 환산값 중 어느 쪽이 틀린지
                        # 이 검사는 모른다 — 실측 Q-018 은 괄호가 옳고 원값이 틀렸는데,
                        # `Q-110` 은 원값이 옳고 괄호가 10배 틀렸다. 방향이 반대다.
                        "hard": True, "targets": []})
    return out


def verify_answer_arithmetic(answer: str) -> dict:
    """답변의 **내적 일관성**을 재계산한다. 근거를 읽지 않는다. LLM 0회.

    `verify_calculation`·`verify_completeness` 와 같은 반환 규격이다. 전제가 안 서면
    (등식도 재기술 짝도 없으면) `skipped` 다 — 검사 대상이 없는 것과 결함을 섞으면
    Phase H 의 귀속이 오염된다.

    🔴 **`verify_calculation` 안에 넣지 않았다.** 그쪽은 근거 레코드마다 한 번 도는데
    이 검사는 답변당 한 번이고 레코드에 의존하지 않는다. 안에 넣으면 ① 레코드가 0건인
    문항(Q-044·Q-047)에서 아예 안 돌고, ② `aggregate_calculation` 의 `explained`
    (레코드 하나라도 설명하면 통과)가 이 검사 때문에 뒤집혀 **체인 관용이 망가진다.**
    """
    text = answer or ""
    mismatches = _equation_mismatches(text) + _restatement_mismatches(text)
    premises = {"equation": bool(_EQUATION_RE.search(text)),
                "restatement": bool(_restatement_pairs(text))}
    checked = [k for k in ARITHMETIC_KINDS if premises[k]]
    skipped = [k for k in ARITHMETIC_KINDS if not premises[k]]
    failed = sorted({m["kind"] for m in mismatches})
    return {
        "ok": not mismatches,
        "llm_calls": 0,
        "checked": checked,
        "skipped": skipped,
        "mismatches": mismatches,
        "failed_kinds": failed,
        "hard_kinds": failed,
        "failure_stage": FAILURE_STAGE if mismatches else None,
        "reason": "; ".join(_mismatch_reason(m) for m in mismatches),
        # 🔴 A20 — `verify_calculation` 과 **같은 규격**이다. `equation` 만 실린다
        # (`restatement` 는 치환 자리를 못 박는다 — `_recomputed` 참조).
        "recomputed": _recomputed(mismatches),
    }


def substitute_recomputed(answer: str, reports: Sequence[dict],
                          recomputed: dict[str, float] | None) -> tuple[str, list[str]]:
    """검사가 낸 재계산값으로 답변의 그 수치를 바꾼다. `(새 답변, 바꾼 자리)`. LLM 0회.

    🔴 **이것이 A20 의 전부다.** 검사는 이미 정답을 정확히 재계산해 놓고 있었고
    (실측 Q-097 의 `per_unit` 재계산값 207,684,631,210 은 골든과 **바이트까지 일치**),
    파이프라인이 그것을 각주로만 붙이고 버렸다 — **탐지 100% · 교정 0%.**

    🔴 **우리 착상이 아니라 CoT 논문 부록 B 의 처방이다** — *"모델이 스스로 쓴 등식마다
    사후 계산기를 돌리고, **문자열 매칭으로 결과를 전파**한다."* 마지막 절이 이 함수다.

    안전장치가 셋이다.
      ① `recomputed` 가 `None` 이면 아무것도 안 한다 (`unique_recomputed` 의 유일성 판정).
      ② 표기가 복합이면(`4조 9,671억원`) 건드리지 않는다 — 어느 자리를 바꿀지 정해지지
         않는다. 값이 둘 이상 실린 표기는 그것으로 가른다.
      ③ 치환은 **숫자 경계**에서만 한다 — `1,365,868` 이 `21,365,868` 의 부분문자열로
         걸리면 답변이 망가진다.

    🔴 **알려진 한계 — 한 번만 돈다.** 한 답변에서 종류가 둘 이상 발화하면(실측
    `vc/ORACLE` Q-115: `amount` + `equation`) `amount` 치환이 등식의 좌변 항을 바꿔
    놓아 등식의 재계산 총계가 낡는다. 되풀이해서 수렴시키지 않는다 — 그러면 이 arm 이
    재는 것이 '치환' 이 아니라 '반복 교정' 이 되고 통제 변수가 하나 더 늘어난다.
    실측 664 답변에서 이 겹침은 1건이고 그 문항은 치환 전후 모두 오답이다.
    """
    if not recomputed:
        return answer, []
    out, done = answer or "", []
    for rep in reports or ():
        for mismatch in rep.get("mismatches") or ():
            expected = recomputed.get(mismatch.get("kind"))
            if expected is None:
                continue
            for target in mismatch.get("targets") or ():
                rendered = _rendered(target, expected)
                if rendered is None or rendered == target:
                    continue
                pattern = re.compile(rf"(?<![\d.,]){re.escape(target)}(?![\d,]*\d)")
                swapped, hits = pattern.subn(rendered, out)
                if not hits:
                    continue
                out = swapped
                done.append(f"{mismatch['kind']}: {target} -> {rendered}")
    return out, done


def _rendered(target: str, expected: float) -> str | None:
    """`target` 의 **표기 그대로** 값만 갈아 끼운다. 못 하면 `None` (건드리지 않는다).

    `2,078억` → `2,077억` 처럼 답변이 쓴 배율·소수 자리를 유지한다. 재계산값을 원 단위로
    들이밀면 답변의 문장이 깨진다.

    🔴 **배율은 `target` 자신의 표지에서 읽는다.** 불일치 레코드의 `claimed_value` 로
    나누면 안 된다 — `targets` 에는 `claimed` 아닌 표기도 들어 있어서(실측 Q-097 은
    `2,078억` 이 보고되고 `207,848,465,710원` 이 함께 치환 대상이 된다) 배율이 1 이
    아니라 0.99977 로 잡히고 **재계산값이 미묘하게 어긋난 값으로 찍힌다.**
    """
    numbers = _BARE_RE.findall(target or "")
    if len(numbers) != 1:
        return None                        # 복합 표기 — 어느 자리를 바꿀지 정해지지 않는다
    if _to_float(numbers[0]) is None:
        return None
    money = _MONEY_RE.fullmatch((target or "").strip())
    scale = _scale_of(money.group(2)) if money else 1.0
    decimals = len(numbers[0].split(".")[1]) if "." in numbers[0] else 0
    value = round(expected / scale, decimals)
    text = f"{value:,.{decimals}f}" if decimals else compute.fmt_num(value)
    return target.replace(numbers[0], text, 1)


# --- F1b. 도구 결과 치환 (A24b · LLM 0회) --------------------------------------
# 🔴 **`A24` 의 기각 사유 ①을 정면으로 겨냥한다.** 그 arm 은 `calc_total` 이 골든과
# 바이트까지 같은 `2,365,868` 을 돌려줬는데 모델이 답변에 `2,366,968` 을 썼다 (독립
# 스모크에서는 `2,366,868` — **두 번 다 틀리고 틀린 자리가 다르다**). 도구는 계산을
# 대신해 줄 수 있지만 **옮겨 적기를 대신해 주지는 못한다.**
#
# 🔴 **`A20` 과 같은 기전이다 — 값을 코드가 문자열에 박는다.** 그래서 원천만 다르고
# 규율·코드는 그대로 쓴다:
#
#     A20   `verify_calculation`·`verify_answer_arithmetic` 의 재계산값으로 치환
#     A24b  🔴 **도구가 돌려준 값**으로 치환
#
# 두 arm 이 `_targets` · `_recomputed` · `unique_recomputed` · `substitute_recomputed` ·
# `_rendered` 를 **공유한다.** 같은 일을 두 곳에 따로 만들면 유일성 규칙이 갈리고, 그것이
# 이 저장소가 여러 번 겪은 실패 양식이다 (`unique_recomputed` 머리말).
#
# 🔴 **치환 조건 셋** (하나라도 어긋나면 손대지 않는다):
#   ① 도구 값이 답변에 **이미 실려 있으면** 아무것도 안 한다 — 모델이 제대로 옮겨 적었다.
#   ② 같은 자릿수 후보(`_targets`)의 **값이 정확히 하나**여야 한다. 둘 이상이면 어느
#      쪽을 말하려 한 것인지 정해지지 않는다.
#      🔴 *literal* 이 아니라 *값* 을 센다 — 실측 Q-097 처럼 같은 값이 두 표기로 실리면
#      (`207,848,465,710원` · `약 2,078억 원`) **둘 다 바꿔야** 답변이 스스로 모순되지
#      않는다 (`SUBSTITUTE_LOG_TOLERANCE` 주석).
#   ③ 같은 도구를 여러 번 불러 값이 갈리면 `unique_recomputed` 가 `None` 을 내고
#      **아무것도 치환되지 않는다** — 정정 체인 관용과 같은 이유다.

#: 도구 결과 검사의 종류 접두. 뒤에 도구 이름이 붙는다 (`tool_result:calc_total`) —
#: 🔴 도구별로 갈라야 `unique_recomputed` 가 *"같은 도구를 두 번 불러 값이 갈렸다"* 를
#: 볼 수 있다. 하나로 뭉치면 서로 다른 도구의 정당한 두 값이 충돌로 읽힌다.
TOOL_RESULT_KIND = "tool_result"


def tool_result_reports(answer: str, results: Sequence[tuple[str, float]]) -> list[dict]:
    """도구 호출 **하나당 판정 레코드 하나.** `verify_calculation` 과 같은 반환 규격이라
    `unique_recomputed`·`substitute_recomputed` 를 그대로 먹인다. LLM 0회.

    `results` 는 `(도구 이름, 수치 결과)` 의 나열이다. **무엇이 수치인가를 여기서 정하지
    않는다** — 호출부(`pipeline._numeric_tool_results`)가 계산 도구의 `value` 만 골라
    넘긴다. `calc_compare` 의 `value` 는 `"a"`/`"b"`/`"tie"` 문자열이고 `calc_period` 의
    `value` 는 `"8년 5개월"` 이라 갈아 끼울 자리가 없다.

    🔴 **레코드를 호출마다 따로 두는 것이 조건 ③이다.** 한 레코드에 뭉치면
    `_recomputed` 가 갈린 종류만 조용히 빼고 나머지를 치환하는데, 도구 결과는 그
    관용을 쓸 근거가 없다 (정정 체인처럼 '같은 값의 다른 시점' 이 아니다).

    🔴 **갈림 판정은 값 자체로 한다 — 불일치가 났는지로 하면 안 된다.** `calc_total` 을
    두 번 불러 `2,365,868` 과 `3` 이 나왔는데 뒤엣것이 답변의 어느 수와도 자릿수가 안
    맞으면 그 레코드는 불일치가 **없고**, `unique_recomputed` 는 갈림을 못 본다.
    그러면 갈린 도구의 앞엣값이 그대로 답변에 박힌다. 여기서 먼저 막는다.
    """
    rows = list(results or ())
    values: dict[str, set[float]] = {}
    for name, value in rows:
        values.setdefault(name, set()).add(float(value))
    split = {name for name, seen in values.items() if len(seen) > 1}
    return [_tool_result_report(answer, name, value, name in split)
            for name, value in rows]


def _tool_result_report(answer: str, name: str, value: float,
                        split: bool = False) -> dict:
    kind = f"{TOOL_RESULT_KIND}:{name}"
    text = answer or ""
    claims = [] if split else _bare_claims(text)
    expected = float(value)
    # ① 이미 옮겨 적었다 — 반올림 창(`_bare_claims` 의 허용오차) 안이면 맞는 것이다.
    stated = any(abs(claimed - expected) <= tol + 1e-9 for claimed, tol, _ in claims)
    targets = [] if stated else _targets(expected, claims)
    values = {v for v in (_to_float(t) for t in targets) if v is not None}
    mismatches: list[dict] = []
    # ② 후보의 **값**이 정확히 하나일 때만 손댄다.
    if targets and len(values) == 1:
        mismatches.append({
            "kind": kind, "claimed": targets[0], "claimed_value": next(iter(values)),
            "expected": expected, "tolerance": 0.0, "hard": True,
            "detail": f"{name} 이 돌려준 값", "targets": targets})
    failed = [kind] if mismatches else []
    # 🔴 갈린 도구·수치 없는 답변은 **검사한 적이 없다** — `skipped` 다. 그것을
    # `checked` 로 세면 게이트 F 의 `calc_checked` 가 거짓을 말한다.
    return {
        "ok": not mismatches, "llm_calls": 0,
        "checked": [] if split or not claims else [kind],
        "skipped": [kind] if split or not claims else [],
        "mismatches": mismatches, "failed_kinds": failed, "hard_kinds": failed,
        "failure_stage": FAILURE_STAGE if mismatches else None,
        "reason": "; ".join(_mismatch_reason(m) for m in mismatches),
        "recomputed": _recomputed(mismatches),
    }


# --- F2. 수치 출처 게이트 (A21 · LLM 0회) --------------------------------------
# 🔴 **`A15` 와 묻는 것이 다르다.** `verify_calculation`·`verify_answer_arithmetic` 은
# *"계산이 맞는가"* 를 묻고 이 검사는 *"이 숫자가 근거에 있는가"* 를 묻는다. 실측
# `Q-018` 은 근거에 없는 13자리 수(`1,843,200,000,000`)를 답에 썼고 — 근거의 값은
# `1,842,200,000,000` 이다 — 그 한 자리는 계산이 아니라 **옮겨 적기**가 틀린 것이다.
#
# 🔴 **`verify_calculation` 안에 넣지 않았다.** `verify_answer_arithmetic` 과 같은
# 이유다 (그 머리말 참조): 그쪽은 근거 레코드마다 한 번 도는데 이 검사는 답변당 한 번이고,
# 안에 넣으면 `aggregate_calculation` 의 '레코드 하나라도 설명하면 통과' 관용이 뒤집힌다.
#
# 🔴 **`skipped` 판별이 이 검사의 절반이다.** 우리가 계산해서 낸 수(`A20` 의 재계산값
# 포함)는 근거에 없는 것이 **정상**이다. 그것을 위반으로 세면 `A20` 과 정면으로 싸운다 —
# `A20` 은 재계산값을 답변에 **집어넣는** arm 이고 이 게이트는 그 값을 근거에서 못 찾는다.
# 그래서 계산으로 설명되는 수는 위반이 아니라 `skipped` 로 뺀다.

#: 게이트가 보는 자릿수 하한. **12자리 미만은 검사하지 않는다** — 연도(`2024`)·건수
#: (`3건`)·비율(`7.6%`)은 근거에 없는 것이 정상이고, 그것을 세면 오탐만 는다.
#: 우리 도메인의 계약금액은 원 단위라 11~13자리다 (`1,842,200,000,000` = 13자리).
PROVENANCE_MIN_DIGITS = 12

PROVENANCE_KIND = "provenance"

#: 🔴 출처 결함은 **생성 단계**에 귀속한다. `verify_calculation`(`TOOL`)과 다른 갈래다 —
#: 계산기가 대신했어야 할 일이 아니라 근거에 있는 수를 옮겨 적는 데 실패한 것이고,
#: `COMPLETENESS_STAGES["stale_value"]`(`GENERATION`)와 같은 종류의 실패다.
PROVENANCE_FAILURE_STAGE = "GENERATION"

#: 답변이 **그 줄에서 나눗셈을 말한** 자리. 걸리면 그 수는 우리가 나눠서 낸 값이므로
#: 근거에 없는 것이 정상이다 (`verify_calculation` 의 `per_unit` 재계산값이 정확히 이것).
#: 🔴 `_PER_UNIT_KW` 와 달리 앞의 `1` 을 요구하지 않는다 — 실측 답변은 `1척 당`(Q-097)과
#: `척당`(A20 치환 후) 둘 다 쓴다. 대신 **같은 줄의 수치 앞쪽**으로 범위를 좁힌다:
#: 답변 전체에서 찾으면 문단 하나만 나눗셈이어도 답변의 모든 수가 조용히 면제된다.
_QUOTIENT_LEAD = re.compile(rf"({_UNITS})\s*당")

#: 재계산으로 설명할 때 허용하는 항 수. 실측 답변의 합산은 최대 3항이다
#: (`v3/ORACLE` Q-034 = 해지 3건의 금액 합). 4항 이상으로 넓히면 조합이 폭증하면서
#: **우연한 설명**이 늘어 게이트가 조용히 무력해진다.
PROVENANCE_MAX_TERMS = 3

#: 재계산 일치의 허용 오차(원). 답변도 근거도 정수 원 단위라 0 이면 충분하지만 부동소수
#: 잔차만 흡수한다. 🔴 **넓히면 안 된다** — 실측 겨냥 `Q-109` 의 덧셈 오차는 998,000원
#: (상대 3.6e-7)이고, 거짓 경보로 남은 `Q-034`(6e10)·`Q-023`(1e10)보다 **더 작다.**
#: 즉 겨냥을 지키면서 그 둘을 관용으로 지울 수 있는 창은 존재하지 않는다.
PROVENANCE_TOLERANCE = 1.0

#: 재계산의 재료로 쓸 최소 금액. `_scale_exponent` 가 `SCALE_MIN_VALUE` 를 쓰는 것과
#: 같은 이유다 — `8원`·`1원` 같은 파싱 잔재가 조합에 끼면 아무 값이나 설명된다.
PROVENANCE_MIN_OPERAND = SCALE_MIN_VALUE

_ANY_NUMBER = re.compile(r"\d[\d,]*")


def _digits(text) -> str:
    return re.sub(r"\D", "", str(text or ""))


def _evidence_digits(evidence) -> set[str]:
    """근거에 실린 수의 **자릿수 문자열** 집합. 콤마를 지워 표기 차이를 흡수한다.

    수 하나를 통째로 비교한다 — 부분문자열로 찾으면 13자리 금액이 14자리 접수번호
    안에서 우연히 발견돼 게이트가 조용히 열린다.
    """
    text = evidence if isinstance(evidence, str) else str(evidence or "")
    return {_digits(m.group(0)) for m in _ANY_NUMBER.finditer(text)}


def _provenance_operands(spans, evidence_digits: set[str]) -> list[float]:
    """재계산의 재료 — 답변이 말한 금액 중 **근거에 그대로 있는** 값만.

    🔴 **근거에 없는 값을 재료로 쓰면 겨냥이 조용히 사라진다.** 실측 `Q-018` 은
    `1,566,300,000,000 + 276,900,000,000 = 1,843,200,000,000` 이 산술로는 정확히
    맞는데, 두 항 중 `276,900,000,000` 이 이미 근거 밖이다. 재료를 근거 있는 값으로
    제한하는 것이 그 자기참조를 끊는다.
    """
    out = {value for _, _, value, _, literal in spans
           if value >= PROVENANCE_MIN_OPERAND and _digits(literal) in evidence_digits}
    return sorted(out)


def _explained_by_arithmetic(value: float, operands: Sequence[float]) -> bool:
    """근거 있는 금액 2~3개의 덧셈·뺄셈으로 이 값이 나오는가. 나오면 우리가 낸 수다."""
    for size in range(2, PROVENANCE_MAX_TERMS + 1):
        if size > len(operands):
            break
        for combo in itertools.combinations(operands, size):
            for signs in itertools.product((1, -1), repeat=size):
                total = sum(sign * term for sign, term in zip(signs, combo))
                if abs(total - value) <= PROVENANCE_TOLERANCE:
                    return True
    return False


def _line_before(text: str, pos: int) -> str:
    return text[text.rfind("\n", 0, pos) + 1:pos]


def verify_number_provenance(answer: str, evidence) -> dict:
    """답변의 **긴 수치가 근거에 있는가**. 근거를 읽지만 재계산은 안 한다. LLM 0회.

    `verify_calculation`·`verify_answer_arithmetic` 과 같은 반환 규격이다.
    검사할 수치가 하나도 안 남으면(짧거나 전부 우리가 계산해서 낸 수) `skipped` 다 —
    검사 대상이 없는 것과 결함을 섞으면 Phase H 의 귀속이 오염된다.

    한 수치가 통과하는 길이 셋이다.
      ① 근거에 **완전일치**로 있다 (콤마 정규화 후 자릿수 문자열 동일)
      ② 답변이 그 줄에서 **나눗셈**을 말했다 (`1척 당 … = X`) — 우리가 낸 몫이다
      ③ 근거에 있는 금액 2~3개의 **덧셈·뺄셈**으로 정확히 나온다 — 우리가 낸 합/차다

    ②·③ 은 `skipped` 쪽이다. 🔴 **`recomputed` 는 항상 비어 있다** — 이 검사는 옳은
    수가 무엇인지 모른다(근거의 어느 수를 말하려던 것인지 정할 수 없다). 그래서
    `A20` 의 치환에 재료를 대지 않는다. 두 arm 은 판정만 OR 로 합쳐야 한다.
    """
    text = answer or ""
    evidence_digits = _evidence_digits(evidence)
    spans = _money_spans(text)
    operands = _provenance_operands(spans, evidence_digits)

    gated: list[str] = []
    mismatches: list[dict] = []
    for start, _end, value, _tol, literal in spans:
        digits = _digits(literal)
        if len(digits) < PROVENANCE_MIN_DIGITS:
            continue
        if digits in evidence_digits:
            gated.append(literal)                      # 검사했고 통과했다
            continue
        if _QUOTIENT_LEAD.search(_line_before(text, start)):
            continue                                   # ② 우리가 나눠서 낸 몫
        if _explained_by_arithmetic(value, operands):
            continue                                   # ③ 우리가 더해서 낸 합·차
        gated.append(literal)
        mismatches.append({
            "kind": PROVENANCE_KIND,
            "claimed": literal,
            "claimed_value": value,
            # 🔴 기대값이 없다. 근거의 어느 수를 말하려던 것인지 이 검사는 모른다 —
            #    그래서 `targets` 도 비운다 (`_recomputed` 가 그것으로 치환을 막는다).
            "expected": None,
            "tolerance": 0.0,
            "detail": "근거에 없는 수치",
            "hard": True,
            "targets": [],
        })

    failed = sorted({m["kind"] for m in mismatches})
    return {
        "ok": not mismatches,
        "llm_calls": 0,
        "checked": [PROVENANCE_KIND] if gated else [],
        "skipped": [] if gated else [PROVENANCE_KIND],
        "mismatches": mismatches,
        "failed_kinds": failed,
        "hard_kinds": failed,
        "failure_stage": PROVENANCE_FAILURE_STAGE if mismatches else None,
        "reason": "; ".join(f"{PROVENANCE_KIND}: 답변 {m['claimed']} 이 근거에 없다"
                            for m in mismatches),
        # 🔴 항상 비어 있다 — 위 독스트링 참조. `A20` 과 재료를 나눠 갖지 않는다.
        "recomputed": {},
    }


# --- G. 완전성 검증 (LLM 0회) ------------------------------------------------
# **지지(support)와 완전성(completeness)은 다른 축이다.** `judge` 는 "답이 근거로
# 지지되는가"만 본다. 그래서 정정 전 금액만 근거에 있으면 모델이 그 값을 답하고
# critic 은 "근거에 있으니 지지됨" 으로 통과시킨다 — 근거 자체가 불완전하다는 것을
# 못 본다. Phase H dev 42문항 실측에서 정확히 이 기전으로 **오답 5건**
# (Q-005·Q-006·Q-018·Q-035·Q-049)이 `supported=True` 로 빠져나갔다.
#
# 설계 원칙 두 가지.
#   ① **과잉 차단이 오답을 놓치는 것보다 나쁘다.** 확신할 수 없으면 통과시킨다.
#      Q-026 이 그 경계다 — 근거에 정정본이 없는 것은 Q-005 와 똑같지만, 질문이
#      '정정공시가 **있었는가**' 라서 부재 자체가 정답이다. 값을 요구하는 질문
#      (`_OUTCOME_ASK`)에만 규칙을 건다.
#   ② SWE-Lancer 부록 C — 판단(45%)이 생성(26%)보다 잘 된다. 완벽한 답을 요구하지
#      말고 **명백한 결함**만 잡는다. 규칙은 전부 결정적 문자열/수치 대조다.

#: 완전성 결함의 실패 단계 귀속. 근거가 **안 온 것**과 온 근거를 **잘못 고른 것**을
#: 가른다 — Phase I A6 은 이 두 갈래에 서로 다른 처방을 붙인다.
COMPLETENESS_STAGES = {
    "evidence_gap": "RETRIEVAL",     # 정정·해지 레코드가 근거에 아예 없다
    "missing_corp": "RETRIEVAL",     # 비교 질의인데 한쪽 기업 근거가 없다
    "stale_value": "GENERATION",     # 최신본이 왔는데 답이 대체본 값을 썼다
    "interim_source": "GENERATION",  # 연간 근거가 왔는데 답이 반기·분기 값을 썼다
}

#: 질문이 정정·해지 축을 건드리는가.
_CHAIN_ASK = re.compile(r"정정|해지|철회|변경계약")

#: 그 축의 **결과값**을 요구하는가. 존재 여부만 묻는 질문과 가르는 유일한 신호다.
_OUTCOME_ASK = re.compile(r"얼마로\s*바뀌|반영\s*후|얼마나\s*(증가|감소|줄|늘|커|작아)|"
                          r"어떻게\s*(바뀌|달라|변했|처리)|이력|무엇이\s*왜|"
                          r"몇\s*차례|여러\s*차례|과정에서")

#: '정정공시가 있었는가' 류. 정정본의 **부재가 곧 정답**이라 절대 걸면 안 된다.
_EXISTENCE_ASK = re.compile(r"(정정공시|정정|해지|철회)\s*(가|이|는|은|를|을)?\s*"
                            r"(있었|없었|되었|됐|하였|했)(는가|나|는지|습니까)")

#: 답변이 스스로 '근거가 이걸 안 담고 있다' 고 말한 자리. 모델의 자백은 공짜 신호다.
_ADMIT = re.compile(r"근거에\s*(는\s*)?없|포함되어\s*있지\s*않|언급이\s*없|"
                    r"확인되지\s*않|확인할\s*수\s*없|답변(을)?\s*(드릴|할)\s*수\s*없|"
                    r"별도의?\s*(검색|조회|확인)이?\s*필요")

_BLOCK_SPLIT = re.compile(r"^##\s+", re.M)
#: `## [기재정정] [공급계약 체결] 삼성전자 – …` 에서 기업명만. 구분자는 en/em dash 다.
_HEAD_CORP = re.compile(r"\]\s*([^\[\]–—]+?)\s*[–—]")
#: 블록이 어느 **문서**에서 왔는가. 정기공시는 한 문서를 여러 절로 잘라 싣는다.
_BLOCK_RCEPT = re.compile(r"접수번호\s*(\d{14})")
_CORRECTION_HEAD = re.compile(r"\[기재정정\]|정\s*정\s*신\s*고")
_STALE_NOTE = re.compile(r"정정체인:.*대체됨")
_LATEST_NOTE = re.compile(r"정정체인:.*최신본")
_INTERIM_HEAD = re.compile(r"반기보고서|분기보고서")
_ANNUAL_HEAD = re.compile(r"사업보고서")
_INTERIM_ASK = re.compile(r"반기|분기")

#: 5자리 이상만 본다. 연도(2025)·건수(3건)를 값으로 세면 오탐만 는다.
_FIGURE = re.compile(r"\d[\d,]{4,}")
#: 14자리는 접수번호다. 값이 아니라 주소이므로 대조 대상에서 뺀다.
_RCEPT = re.compile(r"^\d{14}$")
#: 8자리 `YYYYMMDD` 는 공시일이다. 접수번호와 **같은 이유로** 값이 아니라 주소다.
#: A6 실측 거짓 경보(Q-048): 답변이 인용에 적은 공시일 `20230515` 가 '대체본 값
#: 20,230,515' 로 보고됐다. 근거 머리글의 `(공시일 20230515)` 와 짝이 맞아 버린다.
_DATE8 = re.compile(r"^(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])$")


def _figures(text: str) -> set[str]:
    out = set()
    for match in _FIGURE.finditer(text or ""):
        raw = match.group(0).replace(",", "")
        if not (_RCEPT.match(raw) or _DATE8.match(raw)):
            out.add(raw)
    return out


def _record_text(record: dict) -> str:
    if record.get("markdown"):
        return str(record["markdown"])
    return "\n".join(f"{k}: {v}" for k, v in record.items()
                     if not isinstance(v, (dict, list)))


def _blocks(evidence) -> list[dict]:
    """근거를 블록 목록으로. 마크다운 문자열·레코드 리스트·도구 반환형을 모두 받는다."""
    if isinstance(evidence, str):
        rows = [{"markdown": "## " + part} for part in _BLOCK_SPLIT.split(evidence)[1:]]
    elif isinstance(evidence, dict):
        for key in ("events", "records", "chunks", "filings", "picked"):
            inner = evidence.get(key)
            if isinstance(inner, list):
                return _blocks(inner)
        rows = [evidence]
    elif isinstance(evidence, Sequence):
        rows = [r if isinstance(r, dict) else {"markdown": str(r)} for r in evidence]
    else:
        return []

    blocks = []
    for row in rows:
        text = _record_text(row)
        head = text.split("\n", 1)[0]
        corp = row.get("corp") or row.get("corp_name") or ""
        if not corp:
            found = _HEAD_CORP.search(head)
            corp = found.group(1).strip() if found else ""
        is_latest = row.get("is_latest")
        if is_latest is None:
            if _STALE_NOTE.search(text):
                is_latest = False
            elif _LATEST_NOTE.search(text):
                is_latest = True
        rcept = str(row.get("rcept_no") or "")
        if not rcept:
            found = _BLOCK_RCEPT.search(text)
            rcept = found.group(1) if found else ""
        blocks.append({"head": head, "text": text, "corp": corp, "is_latest": is_latest,
                       "rcept": rcept,
                       "correction": bool(_CORRECTION_HEAD.search(head))})
    return blocks


def _split_chain(blocks: list[dict]) -> tuple[list[dict], list[dict]]:
    """(대체본, 최신본). `is_latest` 가 없으면 `[기재정정]` 머리글로 대신 가른다.

    머리글 대체는 **기업 단위 추정**이라 뒤로 한 발 물러서야 하는 자리다.
    같은 접수번호를 단 블록은 **같은 문서**이므로 서로 대체본·최신본일 수 없다.
    A6 실측 거짓 경보(Q-048): 정기공시는 원공시와 정정본이 같은 `path` 를 갖고
    값만 다른데, 정정본의 본문 블록과 `정 정 신 고` 블록이 접수번호를 공유한다.
    기업 단위 추정은 이 둘을 갈라 **정정본 본문을 대체본으로** 몰았다.
    """
    corrected_corps = {b["corp"] for b in blocks if b["correction"] and b["corp"]}
    corrected_rcepts = {b["rcept"] for b in blocks if b["correction"] and b["rcept"]}
    stale = [b for b in blocks
             if b["is_latest"] is False
             or (b["is_latest"] is None and not b["correction"]
                 and b["corp"] in corrected_corps
                 and b["rcept"] not in corrected_rcepts)]
    # 같은 블록이 두 번 실려도 값 비교로 뭉개지지 않게 **동일성**으로 뺀다.
    stale_ids = {id(b) for b in stale}
    latest = [b for b in blocks
              if id(b) not in stale_ids
              and (b["is_latest"] is True or b["correction"]
                   or (b["rcept"] and b["rcept"] in corrected_rcepts))]
    return stale, latest


def _union(blocks: list[dict]) -> set[str]:
    out: set[str] = set()
    for block in blocks:
        out |= _figures(block["text"])
    return out


def _fmt_figures(values) -> str:
    return ", ".join(f"{int(v):,}" for v in sorted(values, key=int)[:3])


def verify_completeness(question: str, answer: str, evidence,
                        *, companies: Sequence[str] | None = None) -> dict:
    """근거가 **충분한가**를 본다. LLM 0회. `verify_calculation` 과 같은 반환 규격이다.

    `judge` 가 보는 축(지지)과 겹치지 않는다. 여기서 잡는 것은 넷이다.

    | kind | 전제 | 결함 | 귀속 |
    |---|---|---|---|
    | `evidence_gap` | 질문이 정정·해지 **결과값**을 요구 | 답이 근거 부족을 자백 | RETRIEVAL |
    | `stale_value` | 대체본과 최신본이 **서로 다른 문서로** 함께 있음 | 답이 대체본 값만 씀 | GENERATION |
    | `interim_source` | 연간 질의 + 반기·분기와 사업보고서가 함께 옴 | 답이 반기 값만 씀 | GENERATION |
    | `missing_corp` | `companies` 를 넘겼고 2곳 이상 | 근거에 없는 기업이 있음 | RETRIEVAL |

    전제가 안 서면 `skipped` 다 — 검증 대상이 없는 것과 결함을 섞으면 Phase H·I 의
    귀속이 오염된다 (`verify_calculation` 의 `skipped` 설계와 같은 이유).

    **확신할 수 없으면 통과시킨다.** 저장 응답 두 스냅샷 전수에서 정답 문항 거짓
    경보 0건이다 (`COMPLETENESS_SNAPSHOTS`). 수치는 스냅샷과 함께 읽어야 한다 —
    같은 규칙이 Phase H(골든 근거 38/60)에서는 7건, A6(59/60)에서는 1건 발화한다.
    """
    text = answer or ""
    blocks = _blocks(evidence)
    said = _figures(text)
    checked: list[str] = []
    skipped: list[str] = []
    gaps: list[dict] = []

    def _record(kind: str, ran: bool, detail: str = "", found=()) -> None:
        (checked if ran else skipped).append(kind)
        if detail:
            gaps.append({"kind": kind, "detail": detail,
                         "failure_stage": COMPLETENESS_STAGES[kind],
                         "figures": list(found)})

    # ① 근거 공백 — 정정·해지 결과값을 요구했는데 답이 근거 부족을 자백했다.
    #    존재 질의(Q-026)는 부재가 정답이라 전제에서 뺀다.
    wants_outcome = bool(_CHAIN_ASK.search(question or "")
                         and _OUTCOME_ASK.search(question or "")
                         and not _EXISTENCE_ASK.search(question or ""))
    if not wants_outcome:
        _record("evidence_gap", False)
    else:
        hit = _ADMIT.search(text)
        _record("evidence_gap", True,
                f"정정·해지 결과값을 물었으나 답이 근거 부족을 자백했다 ('{hit.group(0)}')"
                if hit else "")

    # ② 대체본 사용 — 체인이 통째로 왔을 때만 성립한다. 다른 트랙이 골든 근거 적중을
    #    38/60 → 59/60 으로 올리면서 정정 전·후 금액이 둘 다 보이게 됐고, 그 바람에
    #    이 전제가 1회 → 7회로 자주 서게 됐다 (`COMPLETENESS_SNAPSHOTS`).
    #    **전제가 자주 서는 것 자체는 정상이다.** 그때 발화가 정확한지를 다시 재야 한다.
    stale, latest = _split_chain(blocks)
    if not (wants_outcome and stale and latest and said):
        _record("stale_value", False)
    else:
        stale_only = _union(stale) - _union(latest)
        latest_only = _union(latest) - _union(stale)
        used_stale = said & stale_only
        _record("stale_value", True,
                f"근거에 최신본이 있는데 답이 대체본 값 {_fmt_figures(used_stale)} 을 썼다"
                if used_stale and not (said & latest_only) else "", used_stale)

    # ③ 반기·분기 출처 — 연간 값을 물었는데 같은 기업의 사업보고서가 함께 왔는데도
    #    답이 반기·분기 블록에만 있는 수치를 골랐다 (Q-049 실측: 11,830,403).
    interim = [b for b in blocks if _INTERIM_HEAD.search(b["head"])]
    annual = [b for b in blocks if _ANNUAL_HEAD.search(b["head"])]
    shared = {b["corp"] for b in interim} & {b["corp"] for b in annual} - {""}
    if _INTERIM_ASK.search(question or "") or not (shared and said):
        _record("interim_source", False)
    else:
        interim_ids = {id(b) for b in interim}
        interim_only = (_union([b for b in interim if b["corp"] in shared])
                        - _union([b for b in blocks if id(b) not in interim_ids]))
        used_interim = said & interim_only
        _record("interim_source", True,
                f"연간 값을 물었으나 반기·분기 보고서에만 있는 수치 "
                f"{_fmt_figures(used_interim)} 을 썼다" if used_interim else "", used_interim)

    # ④ 비교 축 — 기업 목록을 **넘겼을 때만** 돈다. 근거 없는 차단은 하지 않는다.
    names = [c for c in (companies or []) if c]
    if len(names) < 2 or not blocks:
        _record("missing_corp", False)
    else:
        covered = {b["corp"] for b in blocks if b["corp"]}
        missing = [c for c in names
                   if not any(c in cov or cov in c for cov in covered)]
        _record("missing_corp", True,
                f"비교 질의인데 근거에 {', '.join(missing)} 이(가) 없다" if missing else "")

    failed = sorted({g["kind"] for g in gaps})
    return {
        "ok": not gaps,
        "llm_calls": 0,
        "checked": checked,
        "skipped": skipped,
        "gaps": gaps,
        "failed_kinds": failed,
        "failure_stage": gaps[0]["failure_stage"] if gaps else None,
        "reason": "; ".join(f"{g['kind']}: {g['detail']}" for g in gaps),
    }


# --- G. 완전성 규칙이 낡는 것을 드러내는 계기 ----------------------------------
# 이 트랙에서 배운 것 하나. **규칙이 틀린 게 아니라 규칙이 가정한 세계가 바뀐다.**
# `verify_completeness` 는 만들 때 정밀도 7/7 이었는데, 다른 트랙이 골든 근거 적중을
# 38/60 → 59/60 으로 올리자 `stale_value` 의 전제(대체본+최신본 공존)가 **1회 → 7회**
# 로 늘면서 거짓 경보가 생겼다. 발화 수만 보면 이 변화가 안 보인다.
#
# 그래서 **전제가 몇 번 섰는지(`checked`)와 몇 번 발화했는지를 분리해** 센다.
# 발화는 그대로인데 `checked` 만 뛰면 규칙이 낡고 있다는 뜻이다. 다음 트랙이 Recall 을
# 더 올리면 같은 일이 또 생긴다 — 그때 이 표가 먼저 어긋난다.

#: `checked` 가 기준선의 몇 배가 되면 규칙을 다시 보라고 표시할 것인가.
PREMISE_DRIFT_FACTOR = 2.0

#: **수치는 스냅샷과 함께만 의미가 있다.** 07-verifier 의 7/7 이 Phase H 응답 기준인
#: 줄 모르고 A6 에 그대로 적용된 것이 이번 혼선의 직접적 원인이었다. 어느 응답
#: 묶음에서 잰 값인지 여기에 못 박고, `tests/test_critic.py` 가 전수로 재확인한다.
COMPLETENESS_SNAPSHOTS = {
    "phase_h": {
        "responses": "artifacts/eval/responses",
        "labels": "artifacts/eval/error_analysis.json",
        "golden_evidence": "38/60",
        "n": 42, "correct": 25,
        "checked": {"evidence_gap": 7, "stale_value": 1,
                    "interim_source": 4, "missing_corp": 3},
        "fired": {"evidence_gap": 5, "stale_value": 1,
                  "interim_source": 1, "missing_corp": 0},
    },
    "ablation_a6": {
        "responses": "artifacts/ablation/A6/responses",
        "labels": "artifacts/ablation/A6/report.json",
        "golden_evidence": "59/60",
        "n": 42, "correct": 36,
        "checked": {"evidence_gap": 7, "stale_value": 7,
                    "interim_source": 3, "missing_corp": 3},
        "fired": {"evidence_gap": 0, "stale_value": 1,
                  "interim_source": 0, "missing_corp": 0},
    },
    # A7 — 구성은 A6 그대로다 (플래그가 하나도 다르지 않다). 바뀐 것은 **답변**이다.
    # 거절 강제(라우터 게이트)와 시점 규칙이 답변 문장을 바꾸자 전제 성립 수가 움직였다.
    #
    #   stale_value    7 -> 6   Q-040 이 근거 없이 거절로 빠져 체인 블록이 사라졌다
    #   interim_source 3 -> 2   같은 이유
    #
    # **규칙은 한 줄도 고치지 않았다.** 배율이 0.86·0.67 로 `PREMISE_DRIFT_FACTOR`(2.0)
    # 밖이라 드리프트 경보는 안 뜨지만, 수치는 스냅샷과 함께만 의미가 있으므로 새로 박는다.
    # 정밀도는 그대로 1.00 이다 — 발화 1건(Q-023)이 오답이고 정답 문항 거짓 경보는 0건.
    "ablation_a7": {
        "responses": "artifacts/ablation/A7/responses",
        "labels": "artifacts/ablation/A7/report.json",
        "golden_evidence": "59/60",
        "n": 42, "correct": 38,
        "checked": {"evidence_gap": 7, "stale_value": 6,
                    "interim_source": 2, "missing_corp": 3},
        "fired": {"evidence_gap": 0, "stale_value": 1,
                  "interim_source": 0, "missing_corp": 0},
    },
}


def completeness_audit(reports: Sequence[dict], *, baseline=None) -> dict:
    """`verify_completeness` 결과 묶음에서 **전제 성립과 발화를 따로** 센다. LLM 0회.

    `fire_rate` 는 발화 / 전제 성립이다. 전체 문항 수로 나누지 않는다 — 전제가
    안 선 문항은 검사 대상이 아니었으므로 분모에 넣으면 규칙이 좋아진 것처럼 보인다.

    `baseline` 에 `COMPLETENESS_SNAPSHOTS` 의 키(또는 같은 모양의 dict)를 주면
    기준선 대비 `checked` 배율을 함께 낸다. `PREMISE_DRIFT_FACTOR` 이상 벌어진
    kind 는 `drifted` 에 실린다 — **그 규칙의 전제가 다른 세계에서 서고 있다는 뜻**이라
    거짓 경보를 다시 재야 한다.
    """
    checked = {kind: 0 for kind in COMPLETENESS_STAGES}
    fired = {kind: 0 for kind in COMPLETENESS_STAGES}
    for report in reports or ():
        for kind in report.get("checked") or ():
            if kind in checked:
                checked[kind] += 1
        for kind in report.get("failed_kinds") or ():
            if kind in fired:
                fired[kind] += 1

    out = {
        "n": len(reports or ()),
        "checked": checked,
        "fired": fired,
        "fire_rate": {kind: (fired[kind] / checked[kind] if checked[kind] else None)
                      for kind in checked},
        "drift": {},
        "drifted": [],
    }
    if baseline is None:
        return out

    ref = COMPLETENESS_SNAPSHOTS[baseline] if isinstance(baseline, str) else baseline
    for kind, now in checked.items():
        then = (ref.get("checked") or {}).get(kind, 0)
        factor = (now / then) if then else (float("inf") if now else 1.0)
        out["drift"][kind] = {"checked_then": then, "checked_now": now,
                              "fired_then": (ref.get("fired") or {}).get(kind, 0),
                              "fired_now": fired[kind], "factor": factor}
        if factor >= PREMISE_DRIFT_FACTOR:
            out["drifted"].append(kind)
    out["drifted"].sort()
    return out


def judge(evidence: str, answer: str, budget: Budget | None = None) -> Verdict:
    result = chat(build_critic_messages(evidence, answer),
                  max_tokens=200, temperature=0.0, tag="critic")
    if budget:
        budget.spend_llm(result.prompt_tokens, result.completion_tokens)

    match = _JSON.search(result.text or "")
    if not match:
        return Verdict(False, False, True, "critic 응답 파싱 실패")
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return Verdict(False, False, True, "critic 응답 파싱 실패")

    return Verdict(
        supported=bool(data.get("supported")),
        has_citation=bool(data.get("has_citation")),
        hallucination=bool(data.get("hallucination")),
        reason=str(data.get("reason", "")),
    )


def judge_open(question: str, evidence: str, key_points: list[str] | None,
               answer: str, budget: Budget | None = None) -> OpenVerdict:
    """서술형 채점. LLM 1회 — closed 는 정규화 EM 이라 여기 오지 않는다.

    파싱 실패는 **오답이 아니라 판정 실패**다. 3항목을 모두 False 로 두고 이유를 남긴다.
    """
    result = chat(build_open_judge_messages(question, evidence, key_points, answer),
                  max_tokens=200, temperature=0.0, tag="judge_open")
    if budget:
        budget.spend_llm(result.prompt_tokens, result.completion_tokens)

    match = _JSON.search(result.text or "")
    if not match:
        return OpenVerdict(False, False, False, "judge 응답 파싱 실패")
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return OpenVerdict(False, False, False, "judge 응답 파싱 실패")

    return OpenVerdict(
        covers_key_points=bool(data.get("covers_key_points")),
        grounded=bool(data.get("grounded")),
        no_fabrication=bool(data.get("no_fabrication")),
        reason=str(data.get("reason", "")),
    )
