"""`A18` = `T1-targeted`, 규칙이 못 파싱한 238건의 **원문**에서 n-ary 관계를 뽑는다.

    python scripts/build_nary.py --arity                 # 0원 사전 점검 (LLM 0회)
    python scripts/build_nary.py --limit 5 --budget-won 200    # 소규모 검증
    python scripts/build_nary.py --budget-won 5100             # 전량 238건

**`artifacts/*_records.jsonl` 을 고치지 않는다.** 산출물은 새 파일
`artifacts/nary_records.jsonl` 하나이고, 기존 레코드는 한 바이트도 안 바뀐다.
바꾸면 BM25 색인 문자열이 갈려 v1~v4 의 측정이 전량 무효가 된다. arm `A18` 이
켜졌을 때만 `agent/tools.py` 가 이 파일을 읽어 근거 마크다운에 **덧붙인다**
(덮어쓰기가 아니라 합집합, 규칙이 뽑은 줄은 그대로 둔다).

## 모집단, 왜 238건인가

`schema_matched` 는 `corpus/normalize.py` 가 *"계약·투자 서식인데 금액을 뽑았는가"* 로
정한다. `False` 인 238건(exchange 233 · major 5 · holding 0)이 **규칙이 못 뽑은 전량**이고,
나머지 2,912건은 규칙이 이미 구조화했다. 같은 표를 LLM 에 다시 먹이면 같은 값이 나오므로
정보 이득이 정의상 0 이다 (계획서 §0 `T1-md` 기각 사유).

## 이 스크립트가 재기 **전에** 답을 반쯤 알고 있다. `--arity`

HyperGraphRAG 자신의 ablation 이 *"arity ≤ 2 면 하이퍼그래프의 이득이 0"* 을 보고한다
(이항 관계는 일반 그래프로 그대로 표현된다). `--arity` 는 LLM 0회 · 0원으로 238건의
관계 논항 수를 세어 그 전제가 우리 데이터에 맞는지 먼저 판정한다. 지출은 그 다음이다.

## 단일 창구 · 이중 회계

`argus.clova.chat` 만 부른다. 직접 HTTP 를 치면 요청 캐시와 비용 계정을 우회한다.
캐시는 `scripts/ablation.RequestCache` 를 그대로 쓴다(같은 `scratch/ablation/cache`).
온도 0.0 이라 같은 원문은 같은 응답이고, 재실행이 크레딧을 두 번 쓰지 않는다.

**`--budget-won` 정지선이 필수다.** 238콜이 한 번에 나가므로 프롬프트가 잘못 서면
전액이 한 실행에서 날아간다. 상한에 닿으면 그 자리에서 멈추고 **부분 산출물을 남긴다.**

## 추출값을 믿지 않는다. 원문 대조를 함께 기록한다

`R0-1` 이 실측한 위험이 하나 있다: 238건 중 **67건이 발행사 유보**(`계약금액(원)` = `-`)이고
그 원문에 있는 큰 수는 **최근매출액·자기자본**이다. LLM 이 그것을 계약금액으로 태깅하면
`Q-077`(정답 580,000,000,000)이 정면으로 뒤집힌다. 그래서 필드마다 **원문에 그 문자열이
실제로 있는가**(`verbatim`)를 결정적으로 대조해 함께 적는다. **거르지는 않는다**.
거르면 Δ=0 이 측정이 아니라 필터의 성질이 된다. 판정은 측정 뒤에 한다.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from argus import clova                                            # noqa: E402
from argus.config import settings                                  # noqa: E402
from scripts import error_analysis as ea                           # noqa: E402
from scripts.ablation import CACHE_DIR, RequestCache               # noqa: E402

RECORD_GROUPS = ("exchange", "major", "holding")
#: **새 파일이다.** 기존 `*_records.jsonl` 을 덮지 않는다.
OUT_NAME = "nary_records.jsonl"

#: 추출 상한. 원문 p90 = 2,110자 · 최대 3,110자라 전건이 한 번에 들어간다 (`R0-1` §5).
MAX_SOURCE_CHARS = 6_000
MAX_OUTPUT_TOKENS = 512
#: 기본 정지선. `R0-1` 견적 2,809원 × 1.5 = 4,214 를 덮고 고점 3,361 × 1.5 = 5,042 도 덮는다.
DEFAULT_BUDGET_WON = 5_100.0


# --- Step 0, arity 사전 점검 (LLM 0회 · 0원) --------------------------------
#
# 슬롯 넷이 곧 `T1` 이 뽑으려는 것이다. arity = 1(주체 = 공시 기업) + 채워진 슬롯 수.
# 라벨만 있고 값이 `-` 인 칸은 세지 않는다. 유보 서식이 라벨을 전부 갖고 있어서
# 라벨로 세면 arity 가 통째로 부풀려진다.

ARITY_SLOTS = {
    "counterparty": re.compile(
        r"(계약\s*상대|거래\s*상대|상대방|매수인|매도인|발주처|발주자|수요처|공급처|"
        r"계약\s*당사자|투자\s*대상|양수인|양도인|피투자|출자\s*회사)"),
    "amount": re.compile(
        r"(계약\s*금액|거래\s*금액|투자\s*금액|취득\s*금액|공급\s*금액|양수\s*금액|"
        r"양도\s*금액|계약\s*규모|공급\s*가액|총\s*투자|투자\s*규모|출자\s*금액)"),
    "period": re.compile(
        r"(계약\s*기간|공급\s*기간|이행\s*기간|투자\s*기간|사업\s*기간|납품\s*기간|"
        r"시작\s*일|종료\s*일|착수일|준공|만기)"),
    "target": re.compile(
        r"(판매\s*[·/]?\s*공급\s*계약\s*내용|계약\s*내용|계약\s*목적|목적물|품목|공사\s*명|"
        r"사업\s*명|제품|대상\s*주식|투자\s*내용|주요\s*내용|취득\s*자산|양수\s*자산)"),
}
_EMPTY_CELL = {"", "-", "–", "—", "해당사항없음", "해당사항 없음", "N/A", "미정"}
_NUM_RE = re.compile(r"\d[\d,]{2,}")
_DATE_RE = re.compile(r"\d{4}[-.년/]\s*\d{1,2}")


def _cells(record: dict) -> list[str]:
    out: list[str] = []
    for row in record.get("raw_rows") or []:
        out.extend(str(c) for c in row) if isinstance(row, list) else out.append(str(row))
    return out


def _value_ok(slot: str, value: str) -> bool:
    value = value.strip()
    if not value or value in _EMPTY_CELL:
        return False
    if slot == "amount":
        return bool(_NUM_RE.search(value))
    if slot == "period":
        return bool(_DATE_RE.search(value) or _NUM_RE.search(value))
    return True


def slot_filled(record: dict, slot: str, pattern: re.Pattern) -> bool:
    """라벨이 있고 **그 라벨의 값 칸이 실제로 채워졌는가.**"""
    for row in record.get("raw_rows") or []:
        if not isinstance(row, list):
            continue
        for i, cell in enumerate(row):
            text = str(cell)
            if not pattern.search(text):
                continue
            values = [str(c) for c in row[i + 1:]]
            if not values and ":" in text:
                values = [text.split(":", 1)[1]]
            if any(_value_ok(slot, v) for v in values):
                return True
    for text in _cells(record):                       # 표가 아니라 산문 안에 있는 경우
        for m in pattern.finditer(text):
            tail = text[m.end():m.end() + 80]
            tail = tail.split(":", 1)[1] if ":" in tail[:12] else tail
            if _value_ok(slot, tail.strip(" :·-")):
                return True
    return False


def arity_of(record: dict) -> tuple[int, list[str]]:
    filled = [s for s, p in ARITY_SLOTS.items() if slot_filled(record, s, p)]
    return 1 + len(filled), filled


def report_arity(targets: list[dict], control: list[dict]) -> dict:
    hist, by_bucket, slots = Counter(), {}, Counter()
    for r in targets:
        arity, filled = arity_of(r)
        hist[arity] += 1
        slots.update(filled)
        by_bucket.setdefault("withheld" if r.get("withheld") else "open", Counter())[arity] += 1
    total = len(targets) or 1
    ge3 = sum(v for a, v in hist.items() if a >= 3)

    #: 대조군을 **같은 문서군으로 좁힌다.** holding 1,083건은 지분보고라 계약 슬롯이
    #: 애초에 없어(arity 1) 섞으면 대조군 arity 가 통째로 내려간다. 없는 차이를 만든다.
    groups = {r["_group"] for r in targets}
    control = [r for r in control if r["_group"] in groups]
    ctrl = Counter(arity_of(r)[0] for r in control)
    ctrl_total = len(control) or 1
    ctrl_ge3 = sum(v for a, v in ctrl.items() if a >= 3)

    print(f"\n=== arity 분포 · schema_matched=False {len(targets)}건 ===")
    for a in sorted(hist):
        print(f"  arity {a}: {hist[a]:5d}건 ({hist[a] / total * 100:5.1f}%)")
    print(f"  🔴 arity >= 3 : {ge3:5d}건 ({ge3 / total * 100:5.1f}%)")
    print(f"     arity <= 2 : {total - ge3:5d}건 ({(total - ge3) / total * 100:5.1f}%)")
    print("\n  슬롯별 실충전")
    for s in ARITY_SLOTS:
        print(f"    {s:14s} {slots[s]:5d}건 ({slots[s] / total * 100:5.1f}%)")
    print("\n  버킷별")
    for bucket, counter in sorted(by_bucket.items()):
        n = sum(counter.values())
        g = sum(v for a, v in counter.items() if a >= 3)
        print(f"    {bucket:9s} n={n:4d}  arity>=3 {g:4d} ({g / n * 100:5.1f}%)  "
              f"{dict(sorted(counter.items()))}")
    print(f"\n=== 대조군 schema_matched=True {len(control)}건 ===")
    for a in sorted(ctrl):
        print(f"  arity {a}: {ctrl[a]:5d}건 ({ctrl[a] / ctrl_total * 100:5.1f}%)")
    print(f"  🔴 arity >= 3 : {ctrl_ge3:5d}건 ({ctrl_ge3 / ctrl_total * 100:5.1f}%)")
    return {"target_hist": dict(hist), "target_ge3": ge3, "target_n": len(targets),
            "slots": dict(slots), "control_hist": dict(ctrl),
            "control_ge3": ctrl_ge3, "control_n": len(control),
            "by_bucket": {k: dict(v) for k, v in by_bucket.items()}}


# --- 모집단 --------------------------------------------------------------------

def load_records() -> tuple[list[dict], list[dict]]:
    """(`schema_matched=False` 전량, 대조군 `True` 전량). 읽기만 한다."""
    targets, control = [], []
    for group in RECORD_GROUPS:
        path = settings().artifacts_root / f"{group}_records.jsonl"
        if not path.exists():
            continue
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                record = json.loads(line)
                record["_group"] = group
                (control if record.get("schema_matched") else targets).append(record)
    targets.sort(key=lambda r: r["rcept_no"])
    return targets, control


def source_text(record: dict) -> str:
    """원문 = `raw_rows` 전량. `markdown` 이 아니다. 그것은 규칙의 산출물이다."""
    lines = []
    for row in record.get("raw_rows") or []:
        if isinstance(row, list):
            lines.append(" | ".join(str(c).strip() for c in row))
        else:
            lines.append(str(row).strip())
    return "\n".join(lines)[:MAX_SOURCE_CHARS]


# --- 추출 ----------------------------------------------------------------------

#: **최상위를 객체로 감싼 것은 실측 때문이다.** 첫 판(배열 직접 요구)에서 HCX-005 가
#: 238건 중 **135건에 `"["` 한 글자만 돌려주고 `finishReason=stop`** 으로 끝냈다. 같은
#: 원문에 객체 스키마 + 예시 하나를 주니 `{"relations": []}` 로 정확히 답한다(3/3 재현).
#: 즉 그 135건은 "뽑을 것이 없다" 가 맞았는데 **추출기가 그 사실을 말하지 못한 것**이고,
#: 그 상태로 재면 Δ=0 이 데이터의 성질이 아니라 **깨진 도구의 성질**이 된다.
SYSTEM = """당신은 한국 DART 공시 원문에서 n-ary 관계를 추출하는 도구입니다.
규칙 기반 파서가 이 문서에서 아무 필드도 뽑지 못했습니다. 원문만 보고 뽑으십시오.

출력은 **JSON 객체 하나**입니다. 최상위 키는 `relations` 하나이고 그 값이 배열입니다.
설명·머리말·코드펜스를 절대 붙이지 마십시오.
관계가 여러 건이면 원소를 여러 개 쓰고, 하나도 없으면 `{"relations": []}` 를 쓰십시오.

`relations` 각 원소의 스키마 (모든 키를 항상 쓰고, 없으면 null):
{
  "relation": "관계 종류. 예: 공급계약체결 / 공급계약해지 / 신규시설투자 / 지분취득 / 기타",
  "counterparty": "계약상대·거래상대·발주처의 이름. 문자열 또는 null",
  "amount": "계약금액. 원 단위 정수를 콤마 없이 숫자만. 예: 1234567890. 없으면 null",
  "amount_label": "그 금액의 원문 라벨. 예: 계약금액 / 투자금액 / 취득금액. 없으면 null",
  "period_start": "계약기간 시작일 YYYY-MM-DD. 없으면 null",
  "period_end": "계약기간 종료일 YYYY-MM-DD. 없으면 null",
  "subject": "계약 대상·목적물. 60자 이내 한 줄. 없으면 null",
  "evidence": "위 값들의 근거가 된 원문 구절을 그대로 옮긴 것. 100자 이내"
}

🔴 절대 규칙 넷
1. 원문에 없는 값을 만들지 마십시오. 조금이라도 불확실하면 null 입니다.
2. `계약금액(원)` 칸이 `-` 이고 유보사유가 적혀 있으면 amount 는 **반드시 null** 입니다.
   발행사가 의도적으로 비공개한 것이고, 그것을 추정하는 것은 오답입니다.
3. **최근매출액·자기자본·시가총액·자산총액은 계약금액이 아닙니다.** 절대 amount 에
   넣지 마십시오. 그 수들은 계약 규모의 비교 기준으로 같은 표에 실려 있을 뿐입니다.
4. 금액 단위를 원 단위로 환산하십시오 (1억원 = 100000000). 외화면 amount 는 null 로
   두고 evidence 에 원문 표기를 남기십시오.

출력 예시 (형식만 보십시오 — 내용은 따라 쓰지 마십시오):
{"relations": [{"relation": "공급계약체결", "counterparty": "삼성전자", \
"amount": 1000000000, "amount_label": "계약금액", "period_start": "2024-01-01", \
"period_end": "2025-12-31", "subject": "반도체 장비", \
"evidence": "계약상대 삼성전자, 계약금액 10억원"}]}"""

USER = """다음은 공시 원문입니다.

기업: {corp_name}
서식: {form}
공시일: {rcept_dt}
접수번호: {rcept_no}

--- 원문 시작 ---
{body}
--- 원문 끝 ---

`relations` 키를 가진 JSON 객체만 출력하십시오."""

_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.M)
_OBJECT = re.compile(r"\{.*\}", re.S)
_ARRAY = re.compile(r"\[.*\]", re.S)
#: **JSON 이 허용하지 않는 이스케이프.** 실측으로 걸렸다. `20250325800001`(`Q-101` 의
#: 근거 레코드)에서 모델이 `"… 투자금액 : \$58억"` 을 냈고, `\$` 는 JSON 규격에 없는
#: 이스케이프라 통째로 `bad_json` 이 됐다. 나머지 필드는 전부 멀쩡했다.
#: **되돌릴 수 있는 서식 오류는 되돌린다**. 안 되돌리면 그 레코드의 추출이 통째로
#: 사라지고, 하필 그것이 평가셋 근거에 노출된 9건 중 하나다. 즉 `Δ=0` 이 데이터가
#: 아니라 **파서의 성질**이 될 뻔했다. 값은 한 글자도 안 고친다. 백슬래시만 뗀다.
_BAD_ESCAPE = re.compile(r'\\(?=[^"\\/bfnrtu])')


def parse_relations(text: str) -> tuple[list[dict], str]:
    """모델 응답 -> 관계 목록. 실패는 예외가 아니라 **사유 문자열**로 돌려준다.

    **파싱 실패를 조용히 빈 목록으로 접으면 안 된다.** 첫 판에서 135건이 `"["` 한
    글자로 돌아왔는데, 사유를 안 남겼으면 그것이 *"뽑을 것이 없었다"* 로 보고돼
    Δ=0 의 근거가 됐을 것이다. 사유가 남아 있어서 도구 결함임을 잡아냈다.

    배열도 받는다. 옛 산출물과 형식이 갈리면 안 된다.
    """
    raw = _FENCE.sub("", text or "").strip()
    if not raw:
        return [], "empty"
    inner = [m.group(0) for m in (_OBJECT.search(raw), _ARRAY.search(raw)) if m]
    parsed = None
    for candidate in (raw, *inner, *(_BAD_ESCAPE.sub("", c) for c in (raw, *inner))):
        try:
            parsed = json.loads(candidate)
            break
        except json.JSONDecodeError:
            continue
    if parsed is None:
        return [], "no_json" if not (_OBJECT.search(raw) or _ARRAY.search(raw)) else "bad_json"
    if isinstance(parsed, dict):
        parsed = parsed["relations"] if isinstance(parsed.get("relations"), list) else [parsed]
    if not isinstance(parsed, list):
        return [], "not_array"
    return [r for r in parsed if isinstance(r, dict)], ""


_DIGITS = re.compile(r"\D")
#: 모델이 "없음" 을 스키마의 `null` 대신 **원문 서식의 빈 칸 기호**로 돌려준다
#: (실측 스모크 5건에서 `period_start: "-"` 가 나왔다). 그대로 두면 근거에
#: `- 기간: - ~ -` 같은 줄이 실린다. 값이 아니라 서식이므로 여기서 없앤다.
_NULLISH = {"-", "–", "—", "null", "none", "n/a", "na", "미정", "없음", "해당없음",
            "해당사항없음", "해당사항 없음", "확인되지 않음", "미확정", "비공개", "유보"}


def clean_value(value):
    """빈 칸 기호를 `None` 으로 접는다. **값을 고치지는 않는다**. 판정은 측정 뒤다."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return value
    text = " ".join(str(value).split())
    return None if not text or text.lower() in _NULLISH else text


def clean_amount(value):
    """`"1,234,567원"` 처럼 와도 정수로 접는다. **자릿수를 보정하지 않는다.**"""
    value = clean_value(value)
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    digits = _DIGITS.sub("", str(value))
    return int(digits) if digits else None


def verbatim_flags(relation: dict, body: str) -> dict:
    """**결정적 원문 대조.** 뽑힌 값이 원문에 실제로 있는가. 거르지는 않는다.

    `R0-1` 이 지목한 위험(유보 67건에서 최근매출액을 계약금액으로 오인)이 실제로
    일어났는지를 나중에 셀 수 있어야 한다. 판정은 측정 뒤에 한다.
    """
    flat = " ".join(body.split())
    digits = _DIGITS.sub("", flat)
    out = {}
    amount = relation.get("amount")
    if amount not in (None, ""):
        token = _DIGITS.sub("", str(amount))
        out["amount"] = bool(token) and token in digits
    for key in ("counterparty", "subject"):
        value = relation.get(key)
        if value:
            out[key] = " ".join(str(value).split()) in flat
    for key in ("period_start", "period_end"):
        value = relation.get(key)
        if value:
            token = _DIGITS.sub("", str(value))
            out[key] = bool(token) and token in digits
    return out


def extract_one(record: dict, max_tokens: int = MAX_OUTPUT_TOKENS) -> dict:
    body = source_text(record)
    messages = [{"role": "system", "content": SYSTEM},
                {"role": "user", "content": USER.format(
                    corp_name=record.get("corp_name", ""),
                    form=record.get("form") or record.get("doc_subtype") or "",
                    rcept_dt=record.get("rcept_dt", ""),
                    rcept_no=record["rcept_no"], body=body)}]
    #: 온도 0.0, §I-3 의 통제 변수다. 여기서 올리면 재실행이 재현되지 않는다.
    result = clova.chat(messages, max_tokens=max_tokens, temperature=0.0, tag="nary")
    relations, error = parse_relations(result.text)
    for relation in relations:
        for key in ("relation", "counterparty", "amount_label", "period_start",
                    "period_end", "subject", "evidence"):
            relation[key] = clean_value(relation.get(key))
        relation["amount"] = clean_amount(relation.get("amount"))
        relation["verbatim"] = verbatim_flags(relation, body)
    return {"rcept_no": record["rcept_no"], "doc_id": record["doc_id"],
            "group": record["_group"], "corp_name": record.get("corp_name", ""),
            "withheld": bool(record.get("withheld")),
            "doc_subtype": record.get("doc_subtype"),
            "arity_precheck": arity_of(record)[0],
            "relations": relations, "parse_error": error,
            "prompt_tokens": result.prompt_tokens,
            "completion_tokens": result.completion_tokens,
            "finish": result.finish}


# --- CLI -----------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="A18 T1-targeted n-ary 추출")
    ap.add_argument("--arity", action="store_true",
                    help="🔴 0원 사전 점검만 한다 (LLM 0회). 지출 전에 이것부터.")
    ap.add_argument("--limit", type=int, default=0,
                    help="앞에서 N 건만 추출한다. 소규모 검증용.")
    ap.add_argument("--budget-won", type=float, default=DEFAULT_BUDGET_WON,
                    help="🔴 정지선. 넘으면 그 자리에서 멈추고 부분 산출물을 남긴다.")
    ap.add_argument("--out", default="", help=f"산출 경로 (기본 artifacts/{OUT_NAME})")
    ap.add_argument("--offline", action="store_true",
                    help="요청 캐시만 쓴다. 미적중이면 예외 — 크레딧 0원 증명.")
    ap.add_argument("--dry", action="store_true",
                    help="LLM 0회 리허설. 프롬프트만 만들고 호출하지 않는다.")
    args = ap.parse_args(argv)

    targets, control = load_records()
    print(f"schema_matched=False {len(targets)}건 "
          f"({Counter(r['_group'] for r in targets)}) · 대조군 {len(control)}건")

    arity = report_arity(targets, control)
    if args.arity:
        return 0

    out_path = Path(args.out) if args.out else settings().artifacts_root / OUT_NAME
    #: 기존 코퍼스 파일을 절대 덮지 않는다. 실수 하나가 전 세대 측정을 무효로 만든다.
    if out_path.name.endswith("_records.jsonl") and out_path.name != OUT_NAME:
        raise SystemExit(f"🔴 코퍼스 파일에 쓰려 합니다: {out_path}. 새 파일에만 씁니다.")

    chosen = targets[:args.limit] if args.limit else targets
    if args.dry:
        body = source_text(chosen[0])
        print(f"\n--dry · {len(chosen)}건 · 첫 건 원문 {len(body)}자 · "
              f"프롬프트 {len(SYSTEM) + len(USER) + len(body)}자")
        print(body[:600])
        return 0

    cache = RequestCache(CACHE_DIR, offline=args.offline)
    cache.install()

    rows, stopped = [], ""
    started = time.time()
    for i, record in enumerate(chosen, 1):
        if cache.fresh_won > args.budget_won:
            stopped = record["rcept_no"]
            print(f"\n🔴 정지선 {args.budget_won:,.0f}원 도달 — {stopped} 앞에서 멈춥니다 "
                  f"(누적 {cache.fresh_won:,.1f}원)")
            break
        #: **429 를 재시도로 넘긴다.** 전수 실행 중 rate limit 한 번에 죽으면 그때까지
        #: 쓴 크레딧이 산출물 없이 날아간다 (응답은 캐시에 남지만 나머지가 안 돈다).
        #: `error_analysis.with_retry` 를 그대로 쓴다. 백오프 상수를 두 곳에 적으면
        #: 한쪽만 고쳐지는 이 저장소의 단골 결함이 된다.
        hits_before = cache.hits
        rows.append(ea.with_retry(extract_one, record))
        if cache.hits == hits_before:          # 캐시 적중이면 API 를 안 쳤으니 쉬지 않는다
            time.sleep(ea.PACE_S)
        if i % 20 == 0 or i == len(chosen):
            print(f"  {i}/{len(chosen)} · 실지출 {cache.fresh_won:,.1f}원 · "
                  f"캐시 적중 {cache.hits} / 미적중 {cache.misses} · "
                  f"{time.time() - started:,.0f}초")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    filled = Counter()
    verbatim_bad = Counter()
    for row in rows:
        for relation in row["relations"]:
            for key in ("counterparty", "amount", "period_start", "period_end", "subject"):
                if relation.get(key) not in (None, ""):
                    filled[key] += 1
            for key, ok in (relation.get("verbatim") or {}).items():
                if not ok:
                    verbatim_bad[key] += 1
    with_any = sum(1 for r in rows if any(
        any(rel.get(k) not in (None, "") for k in
            ("counterparty", "amount", "period_start", "period_end", "subject"))
        for rel in r["relations"]))

    meta = {"records": len(rows), "with_any_value": with_any,
            "relations": sum(len(r["relations"]) for r in rows),
            "filled": dict(filled), "verbatim_failed": dict(verbatim_bad),
            "parse_errors": dict(Counter(r["parse_error"] for r in rows if r["parse_error"])),
            "prompt_tokens": sum(r["prompt_tokens"] for r in rows),
            "completion_tokens": sum(r["completion_tokens"] for r in rows),
            "spent_won": round(cache.fresh_won, 1),
            "cache_hits": cache.hits, "cache_misses": cache.misses,
            "budget_won": args.budget_won, "stopped_at": stopped,
            "arity_precheck": arity}
    meta_path = out_path.with_suffix(".meta.json")
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"\n{out_path} · {len(rows)}행")
    print(f"  값이 하나라도 나온 레코드 {with_any} / {len(rows)}")
    print(f"  필드별 {dict(filled)}")
    print(f"  🔴 원문 대조 실패 {dict(verbatim_bad)}")
    print(f"  토큰 {meta['prompt_tokens']:,} + {meta['completion_tokens']:,} · "
          f"실지출 {meta['spent_won']:,.1f}원 (단가 {ea.PRICE_WON_PER_1K}원/1K)")
    print(f"  meta -> {meta_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
