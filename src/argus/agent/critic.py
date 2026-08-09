"""근거 지지 판정. 생성보다 싸고 정확한 검증 수단.

3단 검증이고 앞의 두 단계는 공짜다 (policy §6).
  quick_check        — 근거 표기 정규식        LLM 0회
  verify_calculation — 결정적 재계산 대조      LLM 0회
  judge              — 근거 지지 판정          LLM 1회
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from argus.agent import compute
from argus.agent.budget import Budget
from argus.agent.prompts import CITATION_RE, build_critic_messages
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


def quick_check(answer: str) -> dict:
    """LLM 없이 하는 선검사. 여기서 걸리면 critic 호출을 아낀다."""
    return {"has_citation": bool(CITATION_RE.search(answer or ""))}


# --- G. 계산 검증 (LLM 0회) --------------------------------------------------
# policy §4 의 논리를 그대로 뒤집는다 — 계산이 Tool 의 일이면 계산 검증도 Tool 의 일이다.
# 답변에서 수치를 뽑아 compute.py 로 재계산한 값과 대조한다.

_PCT_RE = re.compile(r"(-?\d[\d,]*(?:\.\d+)?)\s*%")
_DUR_RE = re.compile(r"(\d+)\s*년\s*(\d+)\s*개월")
_MONEY_RE = re.compile(r"(-?\d[\d,]*(?:\.\d+)?)\s*(조원|억원|만원|천원|조|억|원)")
_BARE_RE = re.compile(r"-?\d[\d,]*(?:\.\d+)?")
_NUMERIC_CELL = re.compile(r"^-?[\d,]+(?:\.\d+)?$")
_TOTAL_KW = re.compile(r"합계|합산|총계|총액|총합")
_MEAN_KW = re.compile(r"평균")

_SCALE = {"원": 1.0, "천원": 1e3, "만원": 1e4, "억": 1e8, "억원": 1e8,
          "조": 1e12, "조원": 1e12}

#: 반올림 1자리까지 허용한다 (docs/pipeline/07-verifier.md G-2).
ROUND_TOLERANCE = 0.05

#: 계산 불일치는 Phase H Error Analysis 에서 이 단계로 귀속한다.
FAILURE_STAGE = "TOOL"


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


def _money_claims(answer: str) -> list[tuple[float, float, str]]:
    out = []
    for m in _MONEY_RE.finditer(answer):
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
                 detail: str) -> None:
        if not claims:
            skipped.append(kind)
            return
        checked.append(kind)
        value, tol, literal = min(claims, key=lambda c: abs(c[0] - expected))
        if abs(value - expected) <= tol + 1e-9:
            return
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

    # ② 금액 — 자릿수 실수를 잡는다
    amount = _pick(fields, "amount")
    if amount is not None:
        _compare("amount", amount, _money_claims(text), "근거 레코드 amount")

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
    return {
        "ok": not mismatches,
        "llm_calls": 0,
        "checked": checked,
        "skipped": skipped,
        "mismatches": mismatches,
        "failed_kinds": failed,
        "failure_stage": FAILURE_STAGE if mismatches else None,
        "reason": "; ".join(f"{m['kind']}: 답변 {m['claimed']} vs 재계산 {_show(m['expected'])}"
                            for m in mismatches),
    }


def _show(expected) -> str:
    """trace 에 실릴 기대값. 부동소수 17자리를 그대로 흘리지 않는다."""
    if isinstance(expected, (int, float)) and not isinstance(expected, bool):
        return compute.fmt_num(round(float(expected), 1))
    return str(expected)


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
