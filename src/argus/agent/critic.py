"""근거 지지 판정. 생성보다 싸고 정확한 검증 수단.

4단 검증이고 앞의 세 단계는 공짜다 (policy §6).
  quick_check         — 근거 표기 정규식        LLM 0회
  verify_calculation  — 결정적 재계산 대조      LLM 0회
  verify_completeness — 근거 완전성 대조        LLM 0회
  judge               — 근거 지지 판정          LLM 1회
"""
from __future__ import annotations

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
#: 🔴 **긴 표지가 앞에 와야 한다.** `만원` 이 `백만원` 앞에 있으면 `백|만원` 으로 갈려
#: 배율이 1e4 로 잡히고 100배를 놓친다. 같은 이유로 `천억원`·`백억원` 이 `억원` 앞이다.
_MONEY_RE = re.compile(
    r"(-?\d[\d,]*(?:\.\d+)?)\s*(조원|천억원|백억원|억원|백만원|만원|천원|조|억|원)")
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
_SCALE = {"원": 1.0, "천원": 1e3, "만원": 1e4, "백만원": 1e6,
          "억": 1e8, "억원": 1e8, "백억원": 1e10, "천억원": 1e11,
          "조": 1e12, "조원": 1e12}

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


def _money_claims(answer: str) -> list[tuple[float, float, str]]:
    out = []
    seen: list[tuple[int, int]] = []
    for m in _COMPOUND_MONEY_RE.finditer(answer):
        jo, eok = _to_float(m.group(1)), _to_float(m.group(2))
        if jo is None or eok is None:
            continue
        value = jo * 1e12 + eok * 1e8
        out.append((value, _half_ulp(m.group(2), 1e8), m.group(0)))
        seen.append(m.span())
    for m in _MONEY_RE.finditer(answer):
        # 복합 표기 안의 조각(`4조`·`9,290억원`)을 두 번 세지 않는다.
        if any(s <= m.start() and m.end() <= e for s, e in seen):
            continue
        value = _to_float(m.group(1))
        if value is not None:
            scale = _SCALE[m.group(2)]
            out.append((value * scale, _half_ulp(m.group(1), scale), m.group(0)))
    return out


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
                        "scale": exponent, "hard": True})

        if any(abs(value - expected) <= tol + 1e-9 for value, tol, _ in claims):
            return
        if any(m["kind"] == kind and m.get("hard") for m in mismatches):
            return                       # 같은 값을 자릿수 오류로 이미 보고했다
        value, tol, literal = min(claims, key=lambda c: abs(c[0] - expected))
        mismatches.append({"kind": kind, "claimed": literal, "claimed_value": value,
                           "expected": expected, "tolerance": tol, "detail": detail})

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
                    "detail": f"{start} ~ {end}"})

    # ② 금액 — 자릿수 실수를 잡는다. 단, 답변이 **이 레코드의 금액을 말했을 때만**
    #    (`_says_amount`). 근거 한 건에는 조달금액 밑에 시설자금·타법인증권취득자금이
    #    같이 실려 있어서, 레이블을 안 보면 하위 항목을 말한 정답이 오답이 된다.
    amount = _pick(fields, "amount")
    if amount is not None:
        if _says_amount(text, fields.get("amount_type")):
            _compare("amount", amount, _money_claims(text), "근거 레코드 amount",
                     scale_guard=True)
        else:
            skipped.append("amount")

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
        "mismatches": mismatches,
        "failed_kinds": failed,
        "hard_kinds": hard,
        "failure_stage": FAILURE_STAGE if mismatches else None,
        "reason": "; ".join(_mismatch_reason(m) for m in mismatches),
    }


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
        "explained": explained,
        "hard_kinds": hard,
        "failed": bool(hard) or (bool(checked) and not explained),
        "failure_stage": FAILURE_STAGE if (hard or (checked and not explained)) else None,
        "reasons": reasons[:3],
    }


def _show(expected) -> str:
    """trace 에 실릴 기대값. 부동소수 17자리를 그대로 흘리지 않는다."""
    if isinstance(expected, (int, float)) and not isinstance(expected, bool):
        return compute.fmt_num(round(float(expected), 1))
    return str(expected)


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
