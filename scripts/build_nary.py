"""`A18` = `T1-targeted`. Extract n-ary relations from the **source text** of the
238 filings the rules failed to parse.

    python scripts/build_nary.py --arity                 # free pre-check (no LLM call)
    python scripts/build_nary.py --limit 5 --budget-won 200    # small validation
    python scripts/build_nary.py --budget-won 5100             # all 238

**`artifacts/*_records.jsonl` is never modified.** The output is one new file,
`artifacts/nary_records.jsonl`; not a byte of the existing records changes.
Changing them would alter the BM25 index strings and invalidate every measurement
from v1 through v4. Only when arm `A18` is on does `agent/tools.py` read this file
and **append** to the evidence markdown -- a union, not an overwrite, leaving the
lines the rules extracted in place.

## The population, and why it is 238

`schema_matched` is decided by `corpus/normalize.py` as *"this is a contract or
investment form, and was an amount extracted?"*. The 238 where it is `False`
(exchange 233, major 5, holding 0) are **everything the rules failed to extract**;
the remaining 2,912 the rules already structured. Feeding the same table to an LLM
returns the same values, so the information gain is zero by definition (the
rejection ground for `T1-md`, plan section 0).

## The script half-knows the answer **before** it measures: `--arity`

HyperGraphRAG's own ablation reports that *"with arity <= 2 the hypergraph gains
nothing"* (a binary relation is expressible in an ordinary graph as it is).
`--arity` counts the argument counts of the 238 relations with no LLM call and no
cost, deciding first whether that premise holds for our data. Spending comes
after.

## One gateway, double accounting

Only `argus.clova.chat` is called. Issuing HTTP directly would bypass the request
cache and the cost accounting. The cache is `scripts/ablation.RequestCache` as is
(the same `scratch/ablation/cache`). At temperature 0.0 the same source yields the
same response, so a rerun does not spend credits twice.

**The `--budget-won` stop line is mandatory.** All 238 calls go out in one run, so
a badly formed prompt burns the whole amount at once. On reaching the ceiling the
run stops where it is and **keeps the partial output**.

## The extracted values are not trusted: a source check is recorded alongside

`R0-1` measured one risk: of the 238, **67 are issuer-withheld** (`계약금액(원)` =
`-`), and the large numbers present in their source text are **recent revenue and
equity**. If the LLM tags one of those as the contract amount, `Q-077` (correct
answer 580,000,000,000) inverts outright. So for every field a deterministic check
records **whether that exact string is actually in the source** (`verbatim`).
Nothing is **filtered out**: filtering would make a delta of 0 a property of the
filter rather than a measurement. Judgement comes after measurement.
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
#: **A new file.** The existing `*_records.jsonl` are never overwritten.
OUT_NAME = "nary_records.jsonl"

#: Extraction ceiling. Source text runs to a p90 of 2,110 characters and a
#: maximum of 3,110, so every case fits in one call (`R0-1` section 5).
MAX_SOURCE_CHARS = 6_000
MAX_OUTPUT_TOKENS = 512
#: The default stop line. It covers `R0-1`'s estimate of 2,809 won x 1.5 =
#: 4,214, and the high case of 3,361 x 1.5 = 5,042 as well.
DEFAULT_BUDGET_WON = 5_100.0


# --- Step 0, arity 사전 점검 (LLM 0회 · 0원) --------------------------------
#
# The four slots are exactly what `T1` sets out to extract. arity = 1 (the
# subject, the filing company) + the number of filled slots. A cell that has a
# label but a value of `-` is not counted: withheld forms carry every label, so
# counting by label inflates arity wholesale.

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
    """Whether the label is present **and its value cell is actually filled.**"""
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

    #: The control group is **narrowed to the same document groups.** The 1,083
    #: holding filings are ownership reports with no contract slots at all
    #: (arity 1); mixing them in drags the control's arity down wholesale and
    #: manufactures a difference that is not there.
    groups = {r["_group"] for r in targets}
    control = [r for r in control if r["_group"] in groups]
    ctrl = Counter(arity_of(r)[0] for r in control)
    ctrl_total = len(control) or 1
    ctrl_ge3 = sum(v for a, v in ctrl.items() if a >= 3)

    print(f"\n=== arity 분포 · schema_matched=False {len(targets)}건 ===")
    for a in sorted(hist):
        print(f"  arity {a}: {hist[a]:5d}건 ({hist[a] / total * 100:5.1f}%)")
    print(f"  arity >= 3 : {ge3:5d}건 ({ge3 / total * 100:5.1f}%)")
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
    print(f"  arity >= 3 : {ctrl_ge3:5d}건 ({ctrl_ge3 / ctrl_total * 100:5.1f}%)")
    return {"target_hist": dict(hist), "target_ge3": ge3, "target_n": len(targets),
            "slots": dict(slots), "control_hist": dict(ctrl),
            "control_ge3": ctrl_ge3, "control_n": len(control),
            "by_bucket": {k: dict(v) for k, v in by_bucket.items()}}


# --- Population -----------------------------------------------------------------

def load_records() -> tuple[list[dict], list[dict]]:
    """(all `schema_matched=False`, and all `True` as the control group). Read only."""
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
    """The source is all of `raw_rows`, not `markdown` -- the latter is the rules'
    own output."""
    lines = []
    for row in record.get("raw_rows") or []:
        if isinstance(row, list):
            lines.append(" | ".join(str(c).strip() for c in row))
        else:
            lines.append(str(row).strip())
    return "\n".join(lines)[:MAX_SOURCE_CHARS]


# --- Extraction -----------------------------------------------------------------

#: **Wrapping the top level in an object came out of measurement.** In the first
#: version, which asked for a bare array, HCX-005 returned **the single character
#: `"["` with `finishReason=stop` for 135 of the 238**. Given an object schema
#: plus one example on the same source, it answers `{"relations": []}` exactly
#: (reproduced 3/3). So those 135 were right that there was nothing to extract,
#: but **the extractor could not say so**, and measuring in that state would make
#: a delta of 0 a property of **a broken tool** rather than of the data.
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
#: **An escape JSON does not allow.** Caught by measurement. On `20250325800001`
#: (the evidence record for `Q-101`) the model emitted `"… 투자금액 : \$58억"`, and
#: `\$` is not a JSON escape, so the whole response became `bad_json`. Every other
#: field was intact.
#: **A recoverable formatting error is recovered.** Left alone, that record's
#: extraction vanishes entirely -- and it happens to be one of the nine exposed in
#: the evaluation set's evidence. A delta of 0 would have become a property of
#: **the parser** rather than of the data. Not one character of the value is
#: changed; only the backslash is removed.
_BAD_ESCAPE = re.compile(r'\\(?=[^"\\/bfnrtu])')


def parse_relations(text: str) -> tuple[list[dict], str]:
    """Model response -> relation list. A failure returns a **reason string**, not an
    exception.

    **A parse failure must not fold silently into an empty list.** In the first
    version 135 responses came back as the single character `"["`; with no reason
    recorded, that would have been reported as *"there was nothing to extract"*
    and become the grounds for a delta of 0. The reason is what caught it as a
    tooling defect.

    A bare array is accepted too: the format must not diverge from the earlier
    output.
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
#: The model returns "absent" as **the source form's empty-cell marker** rather
#: than the schema's `null` (a five-case smoke test produced
#: `period_start: "-"`). Left as is, the evidence carries lines like
#: `- 기간: - ~ -`. That is formatting, not a value, so it is removed here.
_NULLISH = {"-", "–", "—", "null", "none", "n/a", "na", "미정", "없음", "해당없음",
            "해당사항없음", "해당사항 없음", "확인되지 않음", "미확정", "비공개", "유보"}


def clean_value(value):
    """Fold empty-cell markers into `None`. **Values are never corrected** --
    judgement comes after measurement."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return value
    text = " ".join(str(value).split())
    return None if not text or text.lower() in _NULLISH else text


def clean_amount(value):
    """Fold a value like `"1,234,567원"` into an integer. **No digit correction is
    applied.**"""
    value = clean_value(value)
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    digits = _DIGITS.sub("", str(value))
    return int(digits) if digits else None


def verbatim_flags(relation: dict, body: str) -> dict:
    """**A deterministic source check.** Is the extracted value actually in the source
    text? Nothing is filtered out.

    Whether the risk `R0-1` named -- mistaking recent revenue for the contract
    amount in the 67 withheld filings -- actually occurred has to remain countable
    afterwards. Judgement comes after measurement.
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
    #: Temperature 0.0, the control variable of section I-3. Raising it here
    #: would stop reruns from reproducing.
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
                    help="0원 사전 점검만 한다 (LLM 0회). 지출 전에 이것부터.")
    ap.add_argument("--limit", type=int, default=0,
                    help="앞에서 N 건만 추출한다. 소규모 검증용.")
    ap.add_argument("--budget-won", type=float, default=DEFAULT_BUDGET_WON,
                    help="정지선. 넘으면 그 자리에서 멈추고 부분 산출물을 남긴다.")
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
    #: An existing corpus file is never overwritten. One slip invalidates every
    #: measurement of the previous generations.
    if out_path.name.endswith("_records.jsonl") and out_path.name != OUT_NAME:
        raise SystemExit(f"코퍼스 파일에 쓰려 합니다: {out_path}. 새 파일에만 씁니다.")

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
            print(f"\n정지선 {args.budget_won:,.0f}원 도달 — {stopped} 앞에서 멈춥니다 "
                  f"(누적 {cache.fresh_won:,.1f}원)")
            break
        #: **A 429 is carried through by retrying.** Dying on one rate limit
        #: mid-sweep throws away the credits spent so far with no output (the
        #: responses stay in the cache, but the rest never runs).
        #: `error_analysis.with_retry` is reused as is: writing the backoff
        #: constants in two places is this repository's standing defect, where
        #: only one of them gets fixed.
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
    print(f"  원문 대조 실패 {dict(verbatim_bad)}")
    print(f"  토큰 {meta['prompt_tokens']:,} + {meta['completion_tokens']:,} · "
          f"실지출 {meta['spent_won']:,.1f}원 (단가 {ea.PRICE_WON_PER_1K}원/1K)")
    print(f"  meta -> {meta_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
