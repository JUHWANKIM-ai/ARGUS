"""ARGUS 결과 공개 사이트 — 정적 HTML 5쪽 빌더 (`ablation v4 R6`).

    PYTHONIOENCODING=utf-8 python scripts/build_site.py
    PYTHONIOENCODING=utf-8 python scripts/build_site.py --prove-gates

Node/npm **0** · 크레딧 **0원** · LLM **0회**. 빌드 타임에 `artifacts/` 와 `docs/` 를
읽어 `site/` 아래에 정적 HTML 5쪽(`index`·`ablation`·`errors`·`pipeline`·`data`)과
그 원천 JSON 5개를 낸다. 호스팅하지 않는다 — **로컬 파일이다**.

🔴 **이 파일의 첫 번째 책임은 렌더가 아니라 "사본을 만들지 않는 것" 이다.**
이 저장소는 *"같은 수를 두 곳에 적고 한쪽만 고치는"* 결함을 열일곱 번 겪었다.
그래서 수치 리터럴을 **한 개도** 들지 않는다. 값을 얻는 방식은 셋뿐이다.

    ① 정본을 파싱한다      `parse_ssot()` — `docs/total_plan.md` §1 을 읽고 **검산까지 대조**한다.
                          원장 행 합 ≠ `누적` 행이면 `SsotMismatch` 로 빌드가 죽는다.
                          게이트 x/y 합 ≠ 총계, 테스트 증가 이력 꼬리 ≠ 헤드라인도 같다.
    ② 산출물에서 센다      `resolve_generation()` · `count_rows()` · `vc_golden()` —
                          성적·분모·분포는 `artifacts/ablation/{gen}/**` 에서 **센다**.
    ③ 링크만 한다          arm 판정 대장 · 엔드포인트 · 주최측 규격은 **가리킨다**.

🔴 **세대 라벨**. 사다리(`A0`~`A8`)는 `v2` 세대에만 있고 `v3` 는 `A8`·LOO·진단 arm 뿐이다.
`resolve_generation()` 과 `resolve_ladder_generation()` 이 **따로** 해석하고, 두 표는
**언제나 세대 라벨과 함께** 렌더된다. 세대가 다른 값을 서로 빼지 않는다.
`artifacts/ablation/v4/` 는 아직 없다 — 없으면 후보에서 조용히 빠진다(graceful).

🔴 **보안 다섯 (하드 게이트)**. 주석이 아니라 코드다. `gate_all()` 이 **파일을 쓰기 전에**
전량(HTML + JSON + CSS + README)을 훑고, 하나라도 걸리면 `SiteLeak` 을 올려
**아무 파일도 쓰이지 않는다.** `tests/test_site.py` 가 각 게이트의 발화를 고정한다.

    ①  `.env` · `CLOVA_API_KEY`   `argus` 를 import 하지 않는다(=`config.settings()` 가
                                  `.env` 를 읽는 경로 자체가 없다). `read_*()` 가 `src/`·`.env`
                                  경로를 거부하고, 산출물에서 키 형태를 훑는다.
    ②  코퍼스 원문 5.55GB          `read_*()` 가 `data/` 를 거부한다. 산출물에 `dart-disclosures`·
                                  `periodic_sections.txt` 문자열이 있으면 죽는다.
    ③  heldout                     `read_*()` 가 `heldout` 을 거부한다(`heldout_v2`·`v3/`·`v4/` 포함).
                                  산출물에 `heldout` 문자열이 있으면 죽는다 — 집계 "15/18" 조차 없다.
    ④  evalset 질문 전문·정답      `evalset/{questions,regression,valuechain}.jsonl` 을 **게이트가**
                                  읽어 전문과 **앞 20자**를 산출물과 대조한다. 실리는 것은
                                  `question_id` + 축 메타 + 채점 결과뿐이다.
    ⑤  행 단위 민감 필드           `ROW_FIELDS_ALLOWED` **화이트리스트**로만 뽑는다.
                                  `golden`·`golden_rank`·`answer`·`think_trace` 차단 ·
                                  `corp_name`·`sector`·`route_corps` 도 제외(평가셋 부분 복원 방지).
                                  🔴 **모르는 필드가 붙으면 빌드가 죽는다** — 블랙리스트는
                                  다음 세대에 조용히 샌다.
    +   공인 IP                    테스트에 IP 를 적지 않는다. `deploy/README.md` 에서 **뽑아** 대조한다.
"""
from __future__ import annotations

import argparse
import ast
import html
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site"

# ─────────────────────────────────────────────────────────────────────────────
# 0. 예외
# ─────────────────────────────────────────────────────────────────────────────


class SiteBuildError(RuntimeError):
    """빌드를 멈추는 모든 사유의 뿌리."""


class SsotMismatch(SiteBuildError):
    """정본 문서 안에서 두 자리가 서로 어긋났다. 🔴 고칠 것은 이 스크립트가 아니다."""


class SiteLeak(SiteBuildError):
    """산출물이 보안 게이트를 건드렸다. 🔴 아무 파일도 쓰이지 않았다."""


class ForbiddenRead(SiteBuildError):
    """빌드가 읽어서는 안 되는 경로를 읽으려 했다."""


# ─────────────────────────────────────────────────────────────────────────────
# 1. 읽기 게이트 — 경로 자체를 막는다 (보안 ①②③)
# ─────────────────────────────────────────────────────────────────────────────

#: 🔴 부분 문자열 검사다. `evalset/heldout.jsonl` · `artifacts/ablation/v4/heldout/…` ·
#: `evalset/heldout_v2.jsonl` 이 한 규칙으로 막힌다. `src/` 는 `argus.config` 가 `.env`
#: 경로를 아는 유일한 통로라서 넣었다 — 이 빌더는 코드를 읽지 않는다.
FORBIDDEN_PATHS: tuple[str, ...] = ("heldout", "data/", ".env", "src/", "secrets", "attic")


def _guard_path(rel: str) -> Path:
    posix = str(rel).replace("\\", "/")
    low = posix.lower()
    for token in FORBIDDEN_PATHS:
        if token in low:
            raise ForbiddenRead(
                f"🔴 보안: 이 경로는 사이트 빌드가 읽지 않는다 — {posix!r} (금지 토큰 {token!r})"
            )
    path = (ROOT / posix).resolve()
    if ROOT not in path.parents and path != ROOT:
        raise ForbiddenRead(f"🔴 보안: 저장소 밖 경로 — {posix!r}")
    return path


def read_text(rel: str) -> str:
    return _guard_path(rel).read_text(encoding="utf-8")


def read_json(rel: str):
    return json.loads(read_text(rel))


def exists(rel: str) -> bool:
    try:
        return _guard_path(rel).exists()
    except ForbiddenRead:
        return False


# ─────────────────────────────────────────────────────────────────────────────
# 2. 행 화이트리스트 (보안 ⑤)
# ─────────────────────────────────────────────────────────────────────────────

#: 실어도 되는 필드. **여기 없는 것은 실리지 않는다.**
ROW_FIELDS_ALLOWED: frozenset[str] = frozenset({
    "question_id", "task_type", "answer_mode", "difficulty", "doc_group", "track",
    "answerable", "answer_form", "correct", "scored_by", "first_failure", "root_cause",
    "failure_why", "critic_supported", "critic_passed_wrong_answer",
    "gold_value_in_evidence", "has_citation", "route", "route_group",
    "n_hops", "n_steps", "n_llm_calls", "prompt_tokens", "completion_tokens",
    "judge_tokens", "latency_s", "golden_evidence_all", "calc_mismatch",
    "calc_gated_records",
    # ── v4 R3·R3b 채점 축 (2026-08-15). 🔴 판정 결과인 불리언·열거만 싣는다 ──
    # 안전성 축 — 이 셋은 대회 「안전성·신뢰성」 지표의 증거이므로 싣는 값이 있다.
    "safety_pii_leak", "safety_refused_epistemic", "safety_refused_policy",
    # judge 수치 자기일관성 — 사유 문자열은 아래에서 막는다.
    "numeric_ok", "numeric_failed_kinds",
    # 골든 요점 커버리지 — 전제와 판정만. 사유는 골든 금액을 들고 있어 막는다.
    "covers_key_amounts_ok", "covers_key_amounts_premise",
    # R3-2 가 기각한 축. 채점에 반영하지 않고 세기만 하지만 분포는 공개해도 안전하다.
    "calc_checked_empty", "amount_asked_but_absent",
})

#: 🔴 **의도적으로 막은 것.** 앞 넷은 골든·답변 유출, `corp_name`·`sector`·`route_corps` 는
#: `(문서군, 난이도, 기업명)` 삼중으로 평가셋을 부분 복원할 수 있어서 뺐다. 뒤엣것들은
#: 자유 서술·원문 발췌·문서 식별자를 들고 있다.
ROW_FIELDS_BLOCKED: frozenset[str] = frozenset({
    "answer", "think_trace", "golden", "golden_rank",
    "golden_in_candidates", "golden_in_evidence", "golden_unreachable",
    "corp_name", "sector", "route_corps", "calls", "judge", "data_limit",
    "calc_checked", "calc_skipped", "calc_reasons",
    # ── v4 R3·R3b (2026-08-15) ──
    # 🔴 `calc_reasons` 와 같은 이유로 막는다 — 자유 서술이고 답변·골든의 금액을
    # 원문 그대로 들고 있다. 특히 `covers_key_amounts_reason` 은 **골든이 명시한
    # 금액**을 싣는다. 실으면 화이트리스트의 취지(평가셋 부분 복원 방지)가 무너진다.
    "numeric_reason", "covers_key_amounts_reason",
    # ── v4 A1 (2026-08-16) ──
    # 🔴 **유출이라서가 아니라 모양 때문에 막는다.** `upstream_failure` 는
    # `{stage, status, retryable, marked}` 중첩 객체이고 골든·답변·문서 식별자를 한
    # 글자도 안 들고 있다. 그런데 이 화이트리스트가 내보내는 행은 정적 필터가 그리는
    # **스칼라 표**이고(`FILTER_ROW_FIELDS`), 중첩 객체를 그릴 자리가 없다.
    # 🔴 이 신호의 정본은 `report.json > upstream_failures` 와 `error_matrix.md` 머리의
    # 경고표다 — 거기서는 실제로 찍힌다. 사이트에도 싣기로 판단하면 **스칼라로 펴서**
    # (예: `upstream_stage`) 화이트리스트로 옮겨라. 객체째로 올리지 마라.
    "upstream_failure",
})

#: 화이트리스트도 블랙리스트도 아닌 필드가 나타나면 **판단이 필요하다**. 조용히 지나가지 않는다.
ROW_FIELDS_KNOWN: frozenset[str] = ROW_FIELDS_ALLOWED | ROW_FIELDS_BLOCKED

#: 🔴 게이트는 **키 이름을 무디게** 본다 — 그것이 안전한 쪽이다. 산출물의 집계 블록에는
#: 같은 이름의 **스칼라 집계**가 있다(`by_doc_group.data_limit = 0` · Recall 코호트의
#: `golden = 60`). 이름을 바꿔 그것이 집계임을 밝히고, **스칼라가 아니면 통과시키지 않는다.**
AGGREGATE_RENAME = {"golden": "goldenDenominator", "data_limit": "dataLimitCount"}


def sanitize_aggregate(value, *, path: str = ""):
    """산출물에서 그대로 실어 오는 **집계** 블록을 훑는다.

    차단 필드와 같은 이름이 나오면 스칼라 집계일 때만 이름을 바꿔 통과시키고,
    내용(문자열·목록·딕셔너리)이면 `SiteLeak` 이다.
    """
    if isinstance(value, dict):
        out = {}
        for key, sub in value.items():
            if key in ROW_FIELDS_BLOCKED:
                if isinstance(sub, (int, float)) and not isinstance(sub, bool):
                    out[AGGREGATE_RENAME.get(key, f"{key}Count")] = sub
                    continue
                raise SiteLeak(
                    f"🔴 보안 ⑤: 집계 블록에 차단 필드의 **내용**이 들어 있다 — "
                    f"{path}.{key} ({type(sub).__name__})"
                )
            out[key] = sanitize_aggregate(sub, path=f"{path}.{key}")
        return out
    if isinstance(value, list):
        return [sanitize_aggregate(v, path=f"{path}[]") for v in value]
    return value

_AMOUNT = re.compile(r"\d[\d,]*")


def _mask_amounts(text: str) -> str:
    """자유 서술의 큰 수를 지운다 — `failure_why` 가 골든 금액을 되풀어 쓴다.

    연도(1900~2100)는 남긴다. 4자리 이상은 지운다.
    """
    def repl(m: re.Match[str]) -> str:
        digits = m.group(0).replace(",", "")
        if len(digits) < 4:
            return m.group(0)
        if len(digits) == 4 and 1900 <= int(digits) <= 2100:
            return m.group(0)
        return "[금액]"

    return _AMOUNT.sub(repl, text or "")


def project_row(row: dict, *, generation: str, arm: str, cohort: str) -> dict:
    """`report.json > rows` 한 행을 화이트리스트로만 통과시킨다.

    🔴 모르는 필드가 붙으면 `SiteLeak` 이다. 새 세대가 필드를 늘렸다는 뜻이고,
    **실을지 말지는 사람이 판단한다.**
    """
    unknown = sorted(set(row) - ROW_FIELDS_KNOWN)
    if unknown:
        raise SiteLeak(
            "🔴 보안 ⑤: report.json 에 모르는 필드가 붙었다 — "
            f"{unknown}. `ROW_FIELDS_ALLOWED` 또는 `ROW_FIELDS_BLOCKED` 에 "
            "명시하기 전에는 실리지 않는다."
        )
    out = {k: row[k] for k in sorted(ROW_FIELDS_ALLOWED) if k in row}
    if "failure_why" in out:
        out["failure_why"] = _mask_amounts(out["failure_why"])
    leaked = sorted(set(out) & ROW_FIELDS_BLOCKED)
    if leaked:  # pragma: no cover - 화이트리스트가 이미 막지만 이중으로 세운다
        raise SiteLeak(f"🔴 보안 ⑤: 차단 필드가 통과했다 — {leaked}")
    out["generation"] = generation
    out["arm"] = arm
    out["cohort"] = cohort
    return out


# ─────────────────────────────────────────────────────────────────────────────
# 3. 정본 파서 ① — `docs/total_plan.md` §1
# ─────────────────────────────────────────────────────────────────────────────

SSOT = "docs/total_plan.md"
_NUM = re.compile(r"-?\d[\d,]*\.?\d*")


#: 🔴 보안 ③. 정본 문서는 은닉 트랙 이름을 자유롭게 쓴다(지출 원장 8번 행이 그렇다).
#: **금액은 남기고 이름만 지운다** — 행을 빼면 원장 합이 `누적` 과 어긋나 검산이 깨진다.
RESERVED_TRACK_LABEL = "[비공개 평가 트랙]"
_RESERVED_WORD = re.compile(r"held[-_ ]?out", re.IGNORECASE)


def redact_reserved(text: str) -> str:
    return _RESERVED_WORD.sub(RESERVED_TRACK_LABEL, text or "")


def _clean(cell: str) -> str:
    """마크다운 강조·취소선·링크를 텍스트로 편다. 🔴 취소선 내용은 **버린다**.

    정본에서 읽은 모든 문장이 이 함수를 지나므로 은닉 트랙 이름도 여기서 지운다.
    """
    text = re.sub(r"~~.*?~~", "", cell)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = text.replace("**", "").replace("`", "")
    return redact_reserved(" ".join(text.split()).strip())


def _won(cell: str) -> float | None:
    m = _NUM.search(cell.replace("**", ""))
    return float(m.group(0).replace(",", "")) if m else None


def _split_row(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _section(text: str, heading: str, *, contains: str | None = None) -> str:
    """`heading` 으로 시작하는 절의 본문(다음 같은/상위 수준 heading 까지).

    `contains` 를 주면 **제목에 그 말이 들어 있는** 절을 고른다 — 같은 번호의 절이
    한 문서에 여러 개 있는 경우가 있다(`09-ablation.md` 의 `### ⑧` 은 둘이다).
    """
    lines = text.splitlines()
    level = len(heading) - len(heading.lstrip("#"))
    start = None
    for i, line in enumerate(lines):
        if line.startswith(heading) and (contains is None or contains in line):
            start = i + 1
            break
    if start is None:
        raise SsotMismatch(
            f"🔴 정본에서 절을 찾지 못했다 — {heading!r}"
            + (f" (제목에 {contains!r} 포함)" if contains else "")
        )
    out: list[str] = []
    for line in lines[start:]:
        stripped = line.lstrip("#")
        this = len(line) - len(stripped)
        if this and this <= level and line.startswith("#"):
            break
        out.append(line)
    return "\n".join(out)


def _tables(block: str) -> list[list[list[str]]]:
    """블록 안의 마크다운 표를 순서대로 돌려준다 (구분행 제외)."""
    tables, current = [], []
    for line in block.splitlines():
        if line.lstrip().startswith("|"):
            cells = _split_row(line)
            if all(set(c) <= set("-: ") and c for c in cells):
                continue
            current.append(cells)
        elif current:
            tables.append(current)
            current = []
    if current:
        tables.append(current)
    return tables


def parse_ssot() -> dict:
    """`total_plan.md` §1 을 파싱하고 **문서가 스스로와 맞는지 검산한다.**

    🔴 사본이 아니라 **미러**다. 그리고 조용히 틀리지 않는다 — 아래 검산 넷 중 하나라도
    깨지면 `SsotMismatch` 로 빌드가 멈춘다. `total_plan §1` 은 이 사고를 실제로 겪었다
    (R2E 가 원장에 행만 넣고 `누적`·검산식을 안 고쳐 하루를 낡은 채로 살았다).

        검산 ①  지출 원장 행 합 == `누적` 행
        검산 ②  Phase 표 게이트 분자 합 == `게이트 총계 x / y` 의 x, 그리고 x == y
        검산 ③  테스트 증가 이력 꼬리의 마지막 값 == 헤드라인 `N passed`
        검산 ④  Phase 크레딧 열 합 == 문서가 스스로 적어 둔 그 합, 그리고
                (그 합 + 문서가 적어 둔 배분 밖 차액) == 원장 `누적`
    """
    text = read_text(SSOT)
    block = _section(text, "## 1. ")
    ledger_block = _section(block, "### 누적 지출")

    # ── Phase 표 ─────────────────────────────────────────────────────────────
    phases: list[dict] = []
    for row in _tables(block)[0][1:]:
        if len(row) < 6:
            continue
        gate = _clean(row[4])
        m = re.match(r"(\d+)\s*/\s*(\d+)", gate)
        credit_text = _clean(re.sub(r"\([^)]*\)", "", row[5]))
        phases.append({
            "no": _clean(row[0]),
            "doc": _clean(row[1]),
            "docHref": (re.search(r"\(([^)]+)\)", row[1]) or [None, None])[1],
            "phase": _clean(row[2]),
            "status": _clean(row[3]),
            "gate": gate,
            "gatePassed": int(m.group(1)) if m else None,
            "gateTotal": int(m.group(2)) if m else None,
            "credit": _clean(row[5]),
            "creditWon": sum(float(n.replace(",", "")) for n in _NUM.findall(credit_text)),
        })
    if len(phases) < 5:
        raise SsotMismatch(f"🔴 Phase 표를 못 읽었다 ({len(phases)}행) — {SSOT} §1")

    # ── 게이트 총계 · 테스트 수 ──────────────────────────────────────────────
    gm = re.search(r"게이트\s*총계\s*\*{0,2}(\d+)\s*/\s*(\d+)", block)
    if not gm:
        raise SsotMismatch(f"🔴 게이트 총계 문장을 못 찾았다 — {SSOT} §1")
    gates = {"passed": int(gm.group(1)), "total": int(gm.group(2))}

    tm = re.search(r"테스트\s*\*{0,2}(\d[\d,]*)\s*passed", block)
    if not tm:
        raise SsotMismatch(f"🔴 테스트 수 문장을 못 찾았다 — {SSOT} §1")
    tests = {"passed": int(tm.group(1).replace(",", ""))}
    tail = re.search(
        r"(?:\d[\d,]*\s*→\s*){2,}\*{0,2}(\d[\d,]*)", block[tm.end(): tm.end() + 500]
    )
    tests["historyTail"] = int(tail.group(1).replace(",", "")) if tail else None

    # ── 지출 원장 ────────────────────────────────────────────────────────────
    ledger, total_won = [], None
    for row in _tables(ledger_block)[0][1:]:
        if len(row) < 5:
            continue
        item = _clean(row[1])
        won = _won(row[3])
        if item == "누적":
            total_won = won
            continue
        if won is None:
            continue
        ledger.append({
            "no": _clean(row[0]), "item": item, "tokens": _clean(row[2]),
            "won": won, "source": _clean(row[4]),
        })
    if total_won is None or len(ledger) < 5:
        raise SsotMismatch(f"🔴 지출 원장을 못 읽었다 ({len(ledger)}행) — {SSOT} §1")

    # ── 검산 ① 원장 행 합 == 누적 ────────────────────────────────────────────
    row_sum = round(sum(r["won"] for r in ledger), 1)
    if abs(row_sum - total_won) > 0.05:
        raise SsotMismatch(
            f"🔴 정본 검산 실패 — 지출 원장 행 합 {row_sum:,.1f}원 ≠ `누적` {total_won:,.1f}원 "
            f"({SSOT} §1). 🔴 **고칠 것은 이 스크립트가 아니라 정본이다.** "
            "행을 넣었으면 `누적` 행과 검산식을 함께 고쳐야 한다."
        )

    # ── 검산 ② 게이트 ────────────────────────────────────────────────────────
    gate_sum = sum(p["gatePassed"] or 0 for p in phases)
    gate_den = sum(p["gateTotal"] or 0 for p in phases)
    if gate_sum != gates["passed"] or gate_den != gates["total"]:
        raise SsotMismatch(
            f"🔴 정본 검산 실패 — Phase 표 게이트 합 {gate_sum}/{gate_den} ≠ "
            f"총계 {gates['passed']}/{gates['total']} ({SSOT} §1)."
        )

    # ── 검산 ③ 테스트 증가 이력 꼬리 ─────────────────────────────────────────
    if tests["historyTail"] is not None and tests["historyTail"] != tests["passed"]:
        raise SsotMismatch(
            f"🔴 정본 검산 실패 — 테스트 헤드라인 {tests['passed']} ≠ 증가 이력 꼬리 "
            f"{tests['historyTail']} ({SSOT} §1). 열 번째 결함의 재발이다."
        )

    # ── 검산 ④ 크레딧 열 ─────────────────────────────────────────────────────
    credit_sum = round(sum(p["creditWon"] for p in phases), 1)
    stated = re.search(
        r"크레딧 열의 합은 \*{0,2}([\d,.]+)원.{0,20}총계 \*{0,2}([\d,.]+)원과 \*{0,2}([\d,.]+)원",
        block,
    )
    unallocated = None
    if stated:
        s_sum, s_total, s_diff = (float(g.replace(",", "")) for g in stated.groups())
        if abs(credit_sum - s_sum) > 0.05:
            raise SsotMismatch(
                f"🔴 정본 검산 실패 — Phase 크레딧 열 실측 합 {credit_sum:,.1f}원 ≠ "
                f"문서가 적어 둔 합 {s_sum:,.1f}원 ({SSOT} §1)."
            )
        if abs(s_total - total_won) > 0.05 or abs(s_sum + s_diff - total_won) > 0.05:
            raise SsotMismatch(
                f"🔴 정본 검산 실패 — 크레딧 열 {s_sum:,.1f} + 배분 밖 {s_diff:,.1f} ≠ "
                f"누적 {total_won:,.1f} ({SSOT} §1)."
            )
        unallocated = s_diff

    return {
        "source": SSOT,
        "phases": phases,
        "gates": gates,
        "tests": tests,
        "ledger": ledger,
        "spentWon": total_won,
        "ledgerRowSum": row_sum,
        "phaseCreditWon": credit_sum,
        "unallocatedWon": unallocated,
        "budgetWon": welcome_credit_won(),
        "checks": ["ledger_rows==누적", "phase_gates==총계", "tests_tail==headline",
                   "credit_col==stated & +unallocated==누적"],
    }


#: 규정값이다(과제 크레딧 20만 + 웰컴 10만 중 우리가 쓰는 10만). 우리가 만든 수가 아니라
#: 주최측·NCP 규정이라 파싱할 표가 없다 — 대신 **정본 문장이 살아 있는지 확인한다.**
_WELCOME_CREDIT_WON = 100_000.0
_WELCOME_CREDIT_MARK = "웰컴 크레딧 10만원"


def welcome_credit_won() -> float:
    if _WELCOME_CREDIT_MARK not in read_text("docs/CONVENTION.md"):
        raise SsotMismatch(
            f"🔴 정본 확인 실패 — `docs/CONVENTION.md` 에 {_WELCOME_CREDIT_MARK!r} 문장이 없다. "
            "크레딧 규정이 바뀌었으면 `_WELCOME_CREDIT_WON` 도 함께 고친다."
        )
    return _WELCOME_CREDIT_WON


def parse_data_limits() -> dict:
    """§4 데이터 상한 표 — 항목·규모·사유를 정본에서 읽는다.

    🔴 규모에 붙은 `⚠️ 출처 미확인` 표시를 **지우지 않는다**. 취소선(`~~166건~~`)은 버린다.
    """
    block = _section(read_text(SSOT), "## 4. ")
    rows = []
    for row in _tables(block)[0][1:]:
        if len(row) < 3:
            continue
        rows.append({"item": _clean(row[0]), "scale": _clean(row[1]), "why": _clean(row[2])})
    if not rows:
        raise SsotMismatch(f"🔴 §4 데이터 상한 표를 못 읽었다 — {SSOT}")
    return {"source": f"{SSOT} §4", "rows": rows}


ARM_VERDICT_LEDGER = "docs/pipeline/09-ablation.md"
#: 🔴 기호가 먼저다. *"⏸ 보류 (세우지 않는다)"* 를 키워드로 읽으면 **기각으로 잘못 분류**된다.
_VERDICT_SYMBOLS = (("❌", "rejected"), ("⚠️", "diagnostic"), ("⏸", "pending"),
                    ("🔵", "pending"), ("🔶", "pending"), ("✅", "measured"))
_VERDICT_KEYWORDS = (
    ("diagnostic", ("진단",)),
    ("pending", ("보류", "미판정", "먼저 센다", "측정 전", "이 라운드 밖")),
    ("rejected", ("기각", "취소", "세우지 않는다", "미채택", "신설하지 않는다", "Δ = 0")),
    ("measured", ("측정 완료", "세웠다")),
)


def parse_arm_verdicts() -> dict:
    """arm 판정 **대장**(정본)에서 arm 별 판정 라벨만 뽑는다 — 사유는 링크한다.

    🔴 값을 옮겨 적지 않는다. 라벨은 대장이 바뀌면 함께 바뀐다.
    """
    text = read_text(ARM_VERDICT_LEDGER)
    block = _section(text, "### ", contains="arm 판정 대장")
    tables = _tables(block)
    if not tables:
        return {"source": f"{ARM_VERDICT_LEDGER} §I-5f ⑧", "rows": [], "byArm": {}}
    rows, by_arm = [], {}
    for row in tables[0][1:]:
        if len(row) < 4:
            continue
        arms = _arm_names(row[0])
        verdict = _clean(row[3])
        symbol = next((s for s, _ in _VERDICT_SYMBOLS if s in row[3]), "")
        kind = dict(_VERDICT_SYMBOLS).get(symbol, "unknown")
        if not symbol:
            for name, words in _VERDICT_KEYWORDS:
                if any(w in verdict for w in words):
                    kind = name
                    break
        entry = {
            "arm": _clean(row[0]), "arms": arms, "swaps": _clean(row[1]),
            "targets": _clean(row[2]), "verdict": verdict, "kind": kind,
            "symbol": symbol,
        }
        rows.append(entry)
        for arm in arms:
            by_arm.setdefault(arm, entry)
    return {"source": f"{ARM_VERDICT_LEDGER} §I-5f ⑧", "rows": rows, "byArm": by_arm}


def verdict_of(by_arm: dict, arm: str) -> dict:
    """arm 하나의 판정 항목. 사다리 arm 은 *"`A0`~`A8` 사다리"* 행이 받는다(`A1b` 처럼
    번호로 펼쳐지지 않는 이름이 있다)."""
    if arm in by_arm:
        return by_arm[arm]
    if arm in LADDER_ORDER:
        for entry in by_arm.values():
            if LADDER_ORDER[0] in entry["arms"] and LADDER_ORDER[-1] in entry["arms"]:
                return entry
    return {}


def _arm_names(cell: str) -> list[str]:
    """arm 칸에서 arm 이름을 뽑는다. `` `L1`~`L4` `` 같은 구간은 펼친다."""
    names: list[str] = []
    for a, b in re.findall(r"`([A-Za-z0-9-]+)`\s*~\s*`([A-Za-z0-9-]+)`", cell):
        ma, mb = re.fullmatch(r"([A-Z])(\d+)", a), re.fullmatch(r"([A-Z])(\d+)", b)
        if ma and mb and ma.group(1) == mb.group(1):
            names += [f"{ma.group(1)}{i}" for i in range(int(ma.group(2)), int(mb.group(2)) + 1)]
    names += re.findall(r"`([A-Za-z0-9-]+)`", cell)
    out: list[str] = []
    for n in names:
        if n not in out:
            out.append(n)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# 4. 세대 해석 ② — 산출물에서 센다
# ─────────────────────────────────────────────────────────────────────────────

ABLATION = "artifacts/ablation"
#: 사다리 순서. **여기 있는 arm 중 그 세대에 실제로 있는 것만** 렌더된다.
LADDER_ORDER = ("A0", "A1", "A1b", "A2", "A3", "A4", "A5", "A6", "A7", "A8")
#: 🔴 **여기에 없는 LOO arm 은 크래시가 아니라 조용히 사라진다.** 새 LOO 를 세우면
#: 반드시 더하라 — v4 의 `L6`·`L7`·`L8`(메타 축)이 그렇게 빠질 뻔했다.
#: `L5` 는 v3 에서 기각돼 산출물이 없지만, "그 세대에 있는 것만 렌더" 하므로 남겨 둔다.
LOO_ORDER = ("L1", "L2", "L3", "L4", "L5", "L6", "L7", "L8")
BASELINE_ARM = "A8"
#: 분모 라벨은 **정의**라서 남긴다. 값은 언제나 산출물에서 센다.
DENOMINATOR_DEFS = {
    "grid": "그리드 (3축 교차 dev 문항)",
    "overall": "전체 (그리드 + 정보한계 + 회귀)",
    "valuechain": "밸류체인 (허브 2-hop)",
    "regression": "회귀 (H 단계 실패 유형 환류)",
    "info_limit": "정보한계 (answerable=false · 거절이 정답)",
}


def generations() -> list[str]:
    """`artifacts/ablation/v*` 를 **새 세대부터** 돌려준다. v4 가 없으면 그냥 빠진다."""
    base = _guard_path(ABLATION)
    if not base.is_dir():
        return []
    found = [(int(m.group(1)), p.name) for p in base.iterdir()
             if p.is_dir() and (m := re.fullmatch(r"v(\d+)", p.name))]
    return [name for _, name in sorted(found, reverse=True)]


def _summary_rel(gen: str, cohort: str) -> str:
    return f"{ABLATION}/{gen}/summary.json" if cohort == "full" \
        else f"{ABLATION}/{gen}/vc/summary.json"


def load_summary(gen: str, cohort: str = "full") -> dict | None:
    rel = _summary_rel(gen, cohort)
    return read_json(rel) if exists(rel) else None


def resolve_generation(cohort: str = "full") -> str | None:
    """성적 정본 세대. 🔴 `v4/summary.json` 이 생기면 **그 즉시** v4 가 된다."""
    for gen in generations():
        if load_summary(gen, cohort):
            return gen
    return None


def resolve_ladder_generation() -> str | None:
    """사다리 세대. 🔴 사다리는 `A0` 이 있는 세대에만 있다 — v3 에는 없다.

    v4 가 사다리를 재측정하지 않으면 사다리는 계속 v2 로 렌더된다. **그것이 옳고,
    화면 라벨이 그 사실을 말한다.**
    """
    for gen in generations():
        summary = load_summary(gen, "full")
        if not summary:
            continue
        arms = summary["summary"]["arms"]
        if LADDER_ORDER[0] in arms and BASELINE_ARM in arms:
            return gen
    return None


def arm_stats(summary: dict, arm: str) -> dict:
    return summary["summary"]["arms"][arm]


def track_score(stats: dict, track: str) -> dict | None:
    t = (stats.get("tracks") or {}).get(track)
    if not t:
        return None
    return {"correct": t["correct"], "n": t["n_scored"] or t["n"],
            "rate": _rate(t["correct"], t["n_scored"] or t["n"])}


def overall_score(stats: dict) -> dict:
    return {"correct": stats["overall_correct"], "n": stats["overall_n_scored"],
            "rate": _rate(stats["overall_correct"], stats["overall_n_scored"])}


def _rate(correct: int | None, n: int | None) -> float | None:
    if not n:
        return None
    return round(100.0 * correct / n, 1)


def _delta(new: dict | None, old: dict | None) -> float | None:
    """🔴 **반올림된 비율끼리 빼지 않는다** — 91.7 − 81.9 = 9.8 이지만 실제 Δ 는 9.7 이다."""
    if not new or not old or not new.get("n") or not old.get("n"):
        return None
    return round(100.0 * new["correct"] / new["n"] - 100.0 * old["correct"] / old["n"], 1)


def load_rows(gen: str, arm: str, cohort: str) -> list[dict] | None:
    """`report.json > rows` 를 **화이트리스트로만** 읽는다 (보안 ⑤)."""
    rel = f"{ABLATION}/{gen}/{arm}/report.json" if cohort == "full" \
        else f"{ABLATION}/{gen}/vc/{arm}/report.json"
    if not exists(rel):
        return None
    payload = read_json(rel)
    return [project_row(r, generation=gen, arm=arm, cohort=cohort)
            for r in payload["rows"]]


def load_report(gen: str, arm: str, cohort: str = "full") -> dict | None:
    rel = f"{ABLATION}/{gen}/{arm}/report.json" if cohort == "full" \
        else f"{ABLATION}/{gen}/vc/{arm}/report.json"
    return read_json(rel) if exists(rel) else None


def inflated_question_ids() -> set[str]:
    """🔴 *"정답으로 채점됐지만 산술이 틀린"* 문항 — 정본은 코드 상수다.

    `scripts/error_analysis.py` 의 `JUDGED_CORRECT_ARITHMETIC_WRONG` 을 **import 없이**
    `ast` 로 읽는다(import 하면 `argus` 가 딸려 오고 그것이 보안 ① 위반 경로다).
    """
    tree = ast.parse(read_text("scripts/error_analysis.py"))
    for node in ast.walk(tree):
        target = None
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            target = node.target.id
        elif isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name):
            target = node.targets[0].id
        if target != "JUDGED_CORRECT_ARITHMETIC_WRONG":
            continue
        value = node.value
        if isinstance(value, ast.Call) and value.args:
            value = value.args[0]
        try:
            return set(ast.literal_eval(value))
        except ValueError:  # pragma: no cover - 상수가 리터럴이 아니게 되면
            return set()
    return set()


def inflation(gen: str, arm: str, cohort: str) -> dict:
    """부풀림을 **센다.** 🔴 실질 성적을 함께 싣기 위한 것이다 (Global Constraints).

    옛 세대 문항(`Q-034`)은 현 세대 행에 없으므로 교집합이 세대를 자동으로 가른다.
    """
    rows = load_rows(gen, arm, cohort) or []
    marked = inflated_question_ids()
    hit = sorted(r["question_id"] for r in rows
                 if r.get("correct") and r["question_id"] in marked)
    return {"ids": hit, "count": len(hit),
            "source": "scripts/error_analysis.py::JUDGED_CORRECT_ARITHMETIC_WRONG"}


def vc_golden(gen: str, arm: str) -> dict | None:
    """밸류체인 골든 근거 회수 — 🔴 `vc/summary.json` 집계 필드는 0 이라 **행에서 센다**."""
    summary = load_summary(gen, "vc")
    if not summary or arm not in summary["summary"]["arms"]:
        return None
    gr = arm_stats(summary, arm).get("golden_recall") or {}
    if not gr:
        return None
    return {"hit": gr.get("hit"), "denominator": gr.get("denominator_raw"),
            "denominatorAdjusted": gr.get("denominator_adjusted"),
            "unreachable": gr.get("unreachable"),
            "note": "`denominator_raw` 가 정본이다. 조정 분모는 도달불가 표시분 중 "
                    "이 arm 이 회수하지 못한 것만 뺀 값이고 정답률과 무관하다."}


def git_commit() -> str:
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                             capture_output=True, text=True, timeout=20)
        return out.stdout.strip() or "unknown"
    except Exception:  # pragma: no cover - git 이 없는 환경
        return "unknown"


# ─────────────────────────────────────────────────────────────────────────────
# 5. 링크만 하는 정본 ③
# ─────────────────────────────────────────────────────────────────────────────

SOURCES = {
    "ssot": {"label": "성적·지출·테스트 수 정본", "href": "../docs/total_plan.md", "anchor": "§1"},
    "verdict": {"label": "arm 판정 대장", "href": "../docs/pipeline/09-ablation.md",
                "anchor": "§I-5f ⑧"},
    "endpoint": {"label": "엔드포인트·유지 기한", "href": "../deploy/README.md",
                 "anchor": "첫 절 — 🔴 공인 IP 는 이 사이트에 싣지 않는다"},
    "spec": {"label": "주최측 규격·공지", "href": "../docs/organizer-qa.md", "anchor": ""},
    "tools": {"label": "도구 명세(검색·계산)", "href": "../docs/pipeline/06-agent-tool.md",
              "anchor": "F-2"},
    "gateI": {"label": "게이트 I 렌더 (산출물 정본)", "href": "../artifacts/ablation",
              "anchor": "gate_I.md"},
}


def sources_for(generation: str | None) -> dict:
    """링크만 하는 정본 목록. 🔴 세대를 타는 링크는 **해석된 세대**로 채운다."""
    out = {k: dict(v) for k, v in SOURCES.items()}
    if generation:
        out["gateI"]["href"] = f"{ABLATION}/{generation}/gate_I.md".replace(
            "artifacts", "../artifacts", 1)
        out["gateI"]["anchor"] = f"세대 {generation}"
    return out


# ─────────────────────────────────────────────────────────────────────────────
# 6. 데이터 빌더 다섯
# ─────────────────────────────────────────────────────────────────────────────

BUILDER = "scripts/build_site.py"


def _metadata(note: str, sources: list[str]) -> dict:
    return {
        "generated": datetime.now().isoformat(timespec="seconds"),
        "argusCommit": git_commit(),
        "builder": BUILDER,
        "note": note,
        "sourceFiles": sorted(sources),
        "llmCalls": 0,
        "creditWon": 0,
    }


def generation_info() -> dict:
    cur, ladder, vc = resolve_generation("full"), resolve_ladder_generation(), \
        resolve_generation("vc")
    info = {
        "current": cur, "ladder": ladder, "valuechain": vc,
        "available": generations(),
        "baselineArm": BASELINE_ARM,
        "note": "🔴 세대가 다른 값을 서로 빼지 않는다. 사다리는 `A0` 이 있는 세대에만 있고, "
                "LOO 는 언제나 같은 세대의 `A8` 을 기준선으로 쓴다.",
    }
    for key, gen, cohort in (("currentGenerated", cur, "full"),
                             ("valuechainGenerated", vc, "vc"),
                             ("ladderGenerated", ladder, "full")):
        summary = load_summary(gen, cohort) if gen else None
        info[key] = (summary or {}).get("meta", {}).get("generated")
    if cur and ladder and cur != ladder:
        info["mixedGenerationWarning"] = (
            f"사다리는 `{ladder}` 세대이고 성적 정본은 `{cur}` 세대다. "
            "두 표를 서로 빼면 세대 혼합 뺄셈이다."
        )
    return info


def build_meta() -> dict:
    ssot = parse_ssot()
    gen = generation_info()
    cur, vc_gen = gen["current"], gen["valuechain"]
    summary = load_summary(cur, "full") if cur else None
    vc_summary = load_summary(vc_gen, "vc") if vc_gen else None
    headline: dict = {"generation": cur, "arm": BASELINE_ARM}
    if summary and BASELINE_ARM in summary["summary"]["arms"]:
        stats = arm_stats(summary, BASELINE_ARM)
        headline["tracks"] = {
            name: track_score(stats, name)
            for name in ("grid", "info_limit", "regression") if track_score(stats, name)
        }
        headline["overall"] = overall_score(stats)
        headline["hallucination"] = stats.get("hallucination")
        headline["citationRate"] = stats.get("citation_rate")
        headline["goldenRecall"] = stats.get("golden_recall")
        headline["priceWonPer1k"] = (summary.get("meta") or {}).get("price_won_per_1k")
    if vc_summary and BASELINE_ARM in vc_summary["summary"]["arms"]:
        vstats = arm_stats(vc_summary, BASELINE_ARM)
        inf = inflation(vc_gen, BASELINE_ARM, "vc")
        score = track_score(vstats, "valuechain") or overall_score(vstats)
        effective = score["correct"] - inf["count"]
        headline["valuechain"] = {
            "generation": vc_gen, "scored": score,
            "effective": {"correct": effective, "n": score["n"],
                          "rate": _rate(effective, score["n"])},
            "inflated": inf,
            "warning": "🔴 채점 축이 답변 안의 수치 자기일관성을 보지 않는다. 부풀림 문항을 "
                       "빼면 실질 성적은 더 낮다 — 두 값을 **함께** 읽는다.",
            "goldenEvidence": vc_golden(vc_gen, BASELINE_ARM),
        }
    return {
        "metadata": _metadata(
            "🔴 이 파일의 모든 수는 파싱·셈의 결과다. 리터럴 사본이 아니다.",
            [SSOT, "docs/CONVENTION.md", _summary_rel(cur, "full") if cur else "",
             _summary_rel(vc_gen, "vc") if vc_gen else ""],
        ),
        "generation": gen,
        "headline": headline,
        "denominators": DENOMINATOR_DEFS,
        "ledger": {"rows": ssot["ledger"], "spentWon": ssot["spentWon"],
                   "budgetWon": ssot["budgetWon"],
                   "sharePct": round(100.0 * ssot["spentWon"] / ssot["budgetWon"], 1),
                   "rowSum": ssot["ledgerRowSum"], "source": f"{SSOT} §1",
                   "checks": ssot["checks"]},
        "tests": ssot["tests"] | {"source": f"{SSOT} §1"},
        "gates": ssot["gates"] | {"source": f"{SSOT} §1"},
        "sources": sources_for(gen.get("current")),
    }


def build_ablation() -> dict:
    gen = generation_info()
    cur, ladder_gen, vc_gen = gen["current"], gen["ladder"], gen["valuechain"]
    verdicts = parse_arm_verdicts()
    ssot = parse_ssot()

    ladder: dict = {"generation": ladder_gen, "rows": []}
    if ladder_gen:
        summary = load_summary(ladder_gen, "full")
        arms = summary["summary"]["arms"]
        present = [a for a in LADDER_ORDER if a in arms]
        prev = None
        for arm in present:
            stats = arm_stats(summary, arm)
            grid = track_score(stats, "grid")
            row = {
                "arm": arm, "generation": ladder_gen, "grid": grid,
                "overall": overall_score(stats),
                "deltaFrom": prev["arm"] if prev else None,
                "deltaGridPp": _delta(grid, prev["grid"]) if prev else None,
                "verdict": verdict_of(verdicts["byArm"], arm).get("verdict"),
                "flags": stats.get("flags"),
                "costWon": stats.get("arm_cost_won"),
                "tokens": stats.get("tokens"),
            }
            ladder["rows"].append(row)
            prev = row
        deltas = [r for r in ladder["rows"] if r["deltaGridPp"] is not None]
        top = max(deltas, key=lambda r: r["deltaGridPp"], default=None)
        ladder["maxDelta"] = {"arm": top["arm"], "from": top["deltaFrom"],
                              "pp": top["deltaGridPp"]} if top else None
        ladder["transitions"] = len(deltas)
        ladder["note"] = (
            f"🔴 사다리는 `{ladder_gen}` 세대다. Δ 는 **같은 세대 안의 직전 arm** 과의 차다. "
            "다른 세대의 표와 빼지 않는다."
        )

    loo: dict = {"generation": cur, "rows": [], "baseline": None}
    diagnostic: dict = {"generation": cur, "rows": []}
    if cur:
        summary = load_summary(cur, "full")
        arms = summary["summary"]["arms"]
        base = arm_stats(summary, BASELINE_ARM) if BASELINE_ARM in arms else None
        base_grid = track_score(base, "grid") if base else None
        loo["baseline"] = {"arm": BASELINE_ARM, "generation": cur, "grid": base_grid,
                          "overall": overall_score(base) if base else None}
        for arm in LOO_ORDER:
            if arm not in arms:
                continue
            stats = arm_stats(summary, arm)
            grid = track_score(stats, "grid")
            loo["rows"].append({
                "arm": arm, "generation": cur, "grid": grid,
                "overall": overall_score(stats),
                "deltaGridPp": _delta(grid, base_grid),
                "verdict": verdict_of(verdicts["byArm"], arm).get("verdict"),
                "goldenRecall": stats.get("golden_recall"),
            })
        loo["note"] = (
            f"🔴 LOO 는 **같은 세대(`{cur}`)의 `{BASELINE_ARM}`** 을 기준선으로 쓴다. "
            "축을 하나 빼고 잰 값이므로 음수가 그 축의 기여다."
        )
        for arm in sorted(arms):
            stats = arm_stats(summary, arm)
            entry = verdict_of(verdicts["byArm"], arm)
            is_diag = stats.get("diagnostic") is True
            is_rejected = entry.get("kind") == "rejected"
            if not (is_diag or is_rejected) or arm in LOO_ORDER:
                continue
            diagnostic["rows"].append({
                "arm": arm, "generation": cur,
                "grid": track_score(stats, "grid"), "overall": overall_score(stats),
                "kind": "진단 arm" if is_diag else "기각된 arm",
                "verdict": entry.get("verdict"),
                "diagnosticNote": stats.get("diagnostic_note"),
                "label": "🔴 성적이 아니다 — 제출 구성이 아니고 상한도 아니다.",
            })
        diagnostic["note"] = ("🔴 이 표의 정답률을 '성적' 으로 인용하지 않는다. "
                             "`ORACLE` 은 평가 시점에 없는 골든을 주입한 진단 arm 이고, "
                             "기각된 arm 은 제출 구성이 아니다.")

    valuechain: dict = {"generation": vc_gen, "rows": []}
    if vc_gen:
        vc_summary = load_summary(vc_gen, "vc")
        for arm in sorted(vc_summary["summary"]["arms"]):
            stats = arm_stats(vc_summary, arm)
            score = track_score(stats, "valuechain") or overall_score(stats)
            inf = inflation(vc_gen, arm, "vc")
            entry = verdict_of(verdicts["byArm"], arm)
            valuechain["rows"].append({
                "arm": arm, "generation": vc_gen, "scored": score,
                "effective": {"correct": score["correct"] - inf["count"], "n": score["n"],
                              "rate": _rate(score["correct"] - inf["count"], score["n"])},
                "inflated": inf,
                "goldenEvidence": vc_golden(vc_gen, arm),
                "diagnostic": stats.get("diagnostic") is True,
                "verdict": entry.get("verdict"),
            })
        valuechain["warning"] = (
            "🔴 밸류체인 정답률에는 부풀림이 포함돼 있다 — 개방형 판정기가 답변 안의 수치 "
            "자기일관성을 보지 않아서 10× 환산 오류가 통과한다. **실질값을 병기한다.**"
        )

    heatmap: dict = {"axes": {}, "note": "🔴 각 축은 자기 세대 안에서만 읽는다."}
    for label, gname in (("current", cur), ("ladder", ladder_gen)):
        if not gname:
            continue
        summary = load_summary(gname, "full")
        heatmap["axes"][label] = {"generation": gname, "axes": summary["summary"]["axes"]}

    return {
        "metadata": _metadata(
            "성적은 산출물에서 셌고, 지출은 정본을 파싱했고, 판정 사유는 링크했다.",
            [_summary_rel(cur, "full") if cur else "",
             _summary_rel(ladder_gen, "full") if ladder_gen else "",
             _summary_rel(vc_gen, "vc") if vc_gen else "", SSOT, ARM_VERDICT_LEDGER],
        ),
        "generation": gen,
        "ladder": ladder,
        "loo": loo,
        "diagnostic": diagnostic,
        "valuechain": valuechain,
        "heatmap": heatmap,
        "ledger": {"rows": ssot["ledger"], "spentWon": ssot["spentWon"],
                   "budgetWon": ssot["budgetWon"], "source": f"{SSOT} §1"},
        "verdicts": {"source": verdicts["source"], "rows": verdicts["rows"],
                     "note": "🔴 판정의 정본은 이 표가 아니라 위 링크다 — 우리는 라벨만 읽었다."},
        "sources": sources_for(gen.get("current")),
    }


ERROR_STAGES = ("ROUTING", "RETRIEVAL", "RANKING", "TOOL", "GENERATION", "VERIFY")


def build_errors() -> dict:
    gen = generation_info()
    cur, vc_gen = gen["current"], gen["valuechain"]
    denominators, rows, distributions = [], [], {}

    if cur:
        report = load_report(cur, BASELINE_ARM, "full")
        summary = load_summary(cur, "full")
        stats = arm_stats(summary, BASELINE_ARM)
        for track in ("grid", "regression", "info_limit"):
            score = track_score(stats, track)
            if not score:
                continue
            stages = (stats["tracks"][track].get("stages") or {})
            denominators.append({
                "key": track, "label": DENOMINATOR_DEFS[track], "generation": cur,
                "arm": BASELINE_ARM, **score, "wrong": score["n"] - score["correct"],
                "stages": stages, "stageSum": sum(stages.values()),
            })
        overall = overall_score(stats)
        denominators.append({
            "key": "overall", "label": DENOMINATOR_DEFS["overall"], "generation": cur,
            "arm": BASELINE_ARM, **overall, "wrong": overall["n"] - overall["correct"],
            "stages": stats.get("failures_all") or {},
            "stageSum": sum((stats.get("failures_all") or {}).values()),
        })
        distributions = {
            "stagesGrid": report.get("failure_histogram_grid") or {},
            "stagesAll": report.get("failure_histogram") or {},
            "rootCausesGrid": report.get("root_cause_histogram_grid") or {},
            "rootCausesAll": report.get("root_cause_histogram") or {},
            "byDocGroupGrid": report.get("by_doc_group_grid") or {},
            "byTrack": report.get("by_track_failures") or {},
            "citationRateGrid": report.get("citation_rate_grid"),
            "stageOrder": list(ERROR_STAGES),
        }
        for arm in sorted(summary["summary"]["arms"]):
            rows += load_rows(cur, arm, "full") or []

    if vc_gen:
        vc_summary = load_summary(vc_gen, "vc")
        for arm in sorted(vc_summary["summary"]["arms"]):
            stats = arm_stats(vc_summary, arm)
            if arm == BASELINE_ARM:
                score = track_score(stats, "valuechain") or overall_score(stats)
                denominators.append({
                    "key": "valuechain", "label": DENOMINATOR_DEFS["valuechain"],
                    "generation": vc_gen, "arm": arm, **score,
                    "wrong": score["n"] - score["correct"],
                    "stages": stats.get("failures_all") or {},
                    "stageSum": sum((stats.get("failures_all") or {}).values()),
                    "inflated": inflation(vc_gen, arm, "vc"),
                })
            rows += load_rows(vc_gen, arm, "vc") or []

    ladder_gen = gen["ladder"]
    if ladder_gen and ladder_gen != cur:
        summary = load_summary(ladder_gen, "full")
        for arm in sorted(summary["summary"]["arms"]):
            rows += load_rows(ladder_gen, arm, "full") or []

    wrong = sorted({r["question_id"] for r in rows
                    if r["generation"] == cur and r["arm"] == BASELINE_ARM
                    and r["cohort"] == "full" and not r["correct"]})
    rows.sort(key=lambda r: (r["generation"], r["cohort"], r["arm"], r["question_id"]))
    return {
        "metadata": _metadata(
            "🔴 분모는 라벨과 함께만 읽는다 — 그리드·전체·밸류체인·회귀·정보한계가 다 다르다. "
            "행은 화이트리스트로만 실렸다(질문 전문·정답·골든·추론 흔적 없음).",
            [f"{ABLATION}/{cur}/{BASELINE_ARM}/report.json" if cur else "",
             _summary_rel(cur, "full") if cur else "", f"{SSOT} §4"],
        ),
        "generation": gen,
        "denominators": denominators,
        "denominatorDefs": DENOMINATOR_DEFS,
        "distributions": distributions,
        "remainingWrong": {"generation": cur, "arm": BASELINE_ARM, "ids": wrong,
                           "count": len(wrong),
                           "note": "🔴 그리드·전체 분모의 현 세대 잔여 오답이다. 세대를 밝히지 "
                                   "않은 잔여 목록은 인용하지 않는다."},
        "dataLimits": parse_data_limits(),
        "rows": rows,
        "rowFields": {"allowed": sorted(ROW_FIELDS_ALLOWED),
                      "blocked": sorted(ROW_FIELDS_BLOCKED),
                      "note": "🔴 화이트리스트다. 모르는 필드가 붙으면 빌드가 죽는다."},
        "filters": {
            "generation": sorted({r["generation"] for r in rows}),
            "cohort": sorted({r["cohort"] for r in rows}),
            "arm": sorted({r["arm"] for r in rows}),
            "track": sorted({r["track"] for r in rows if r.get("track")}),
            "task_type": sorted({r["task_type"] for r in rows if r.get("task_type")}),
            "difficulty": sorted({r["difficulty"] for r in rows if r.get("difficulty")}),
            "doc_group": sorted({r["doc_group"] for r in rows if r.get("doc_group")}),
            "first_failure": sorted({r["first_failure"] for r in rows if r.get("first_failure")}),
        },
        "sources": sources_for(gen.get("current")),
    }


def build_pipeline() -> dict:
    ssot = parse_ssot()
    gen = generation_info()
    return {
        "metadata": _metadata(
            "🔴 Phase 표·게이트·테스트 수·크레딧은 정본을 파싱한 미러다. 검산 넷을 통과했다. "
            "Phase 별 서술은 옮겨 적지 않고 각 문서를 링크한다 — 옮겨 적은 문장이 낡는다.",
            [f"{SSOT} §1"],
        ),
        "generation": gen,
        "phases": ssot["phases"],
        "gates": ssot["gates"] | {"source": f"{SSOT} §1",
                                  "check": "Phase 표 게이트 분자·분모 합과 대조했다"},
        "tests": ssot["tests"] | {"source": f"{SSOT} §1",
                                  "check": "증가 이력 꼬리와 헤드라인을 대조했다"},
        "credit": {"phaseColumnWon": ssot["phaseCreditWon"],
                   "unallocatedWon": ssot["unallocatedWon"],
                   "spentWon": ssot["spentWon"], "budgetWon": ssot["budgetWon"],
                   "note": "🔴 Phase 크레딧 열의 합을 총계로 쓰지 않는다 — 배분 밖 항목이 있다. "
                           "총계는 지출 원장이 권위값이다.",
                   "source": f"{SSOT} §1"},
        "ledger": {"rows": ssot["ledger"], "source": f"{SSOT} §1"},
        "revisions": {"note": "🔴 측정이 계획을 몇 번 뒤집었는지는 라운드마다 늘어난다 — "
                              "횟수를 여기 적지 않고 정본을 가리킨다.",
                      "href": "../docs/superpowers/plans/2026-08-14-ablation-v4.md"},
        "sources": sources_for(gen.get("current")),
    }


def build_data() -> dict:
    gen = generation_info()
    cur = gen["current"]
    doc_profile = read_json("artifacts/eda/doc_profile.json") \
        if exists("artifacts/eda/doc_profile.json") else {}
    relation = read_json("artifacts/eda/relation_profile.json") \
        if exists("artifacts/eda/relation_profile.json") else {}
    recall = read_json("artifacts/eval/recall_v2.json") \
        if exists("artifacts/eval/recall_v2.json") else {}

    corpus = []
    for group, v in sorted((doc_profile.get("corpus_bytes") or {}).items()):
        corpus.append({"group": group, "files": v.get("n_xml_files"), "mb": v.get("mb"),
                       "sharePct": v.get("share_pct"), "avgKb": v.get("avg_kb")})
    corpus_total_mb = round(sum(c["mb"] or 0 for c in corpus), 1)

    # 🔴 색인 대장에서 코퍼스 원문 경로(`data/…`)는 뺀다 — 보안 ②. 이름조차 싣지 않는다.
    index_rows = []
    summary = load_summary(cur, "full") if cur else None
    snapshot = ((summary or {}).get("meta") or {}).get("index_snapshot") or {}
    for path, stamp in sorted(snapshot.items()):
        if not path.startswith("artifacts/"):
            continue
        digest, _, size = stamp.partition(":")
        name = path.split("/")[-1]
        if "periodic_sections.txt" in name:
            continue  # 🔴 코퍼스 원문 덩어리 — 재배포 대상이 아니다
        index_rows.append({"artifact": name, "bytes": int(size) if size.isdigit() else None,
                           "digest12": digest[:12]})

    cohorts = []
    for name, payload in sorted((recall.get("cohorts") or {}).items()):
        block = payload.get("harness") or payload.get("cached") or {}
        cohorts.append({
            "cohort": name, "definition": payload.get("definition"),
            "nCases": block.get("n_cases"),
            "goldenDenominator": block.get("golden"),
            "recall5": block.get("recall5"), "recall5Rate": block.get("recall5_rate"),
            "evidenceHitRate": block.get("evidence_hit_rate"),
            "byDifficulty": block.get("by_difficulty"),
            "byDocGroup": block.get("by_doc_group"),
        })

    density = relation.get("corp_to_corp_edge_density") or {}
    graph = {
        "contracts": density.get("n_contract_records"),
        "withCounterparty": density.get("n_with_counterparty"),
        "fillRate": density.get("counterparty_fill_rate"),
        "edgeDensityStrict": density.get("edge_density_strict_over_all_contracts"),
        "edgesExact": density.get("n_edges_exact"),
        "universeSize": density.get("universe_size"),
        "completenessPct": density.get("graph_completeness_pct"),
        "classBreakdownPct": density.get("class_breakdown_pct"),
        "verdict": "🔴 기각된 것은 **기업—기업 엣지 정의**다. GraphRAG 전제가 기각된 것이 "
                   "아니고, 재정의한 축(`doc_subtype` 메타)은 사다리에서 가장 큰 전이를 냈다.",
    }
    return {
        "metadata": _metadata(
            "🔴 코퍼스 원문은 재배포 대상이 아니다. 이 쪽은 규모·색인·검색 지표만 싣는다. "
            "근거 발췌도 싣지 않는다.",
            ["artifacts/eda/doc_profile.json", "artifacts/eda/relation_profile.json",
             "artifacts/eval/recall_v2.json", _summary_rel(cur, "full") if cur else ""],
        ),
        "generation": gen,
        "corpus": {"rows": corpus, "totalMb": corpus_total_mb,
                   "docsInManifest": (doc_profile.get("meta") or {}).get("n_docs_in_manifest"),
                   "docsParsed": (doc_profile.get("meta") or {}).get("n_docs_parsed"),
                   "sampling": (doc_profile.get("meta") or {}).get("sampling"),
                   "note": "🔴 원문은 배포하지 않는다 — 규모만 싣는다."},
        "index": {"rows": index_rows,
                  "note": "🔴 코퍼스 원문 경로와 정기공시 본문 덩어리는 대장에서 뺐다 — 이름조차 싣지 않는다.",
                  "generation": cur},
        "recall": {"cohorts": cohorts, "source": "artifacts/eval/recall_v2.json",
                   "note": "🔴 코호트 라벨 없이 Recall@5 를 인용하지 않는다 — "
                           "42문항과 88문항은 다른 모집단이다."},
        "graph": graph,
        "apiContract": {
            "method": "GET", "path": "/answer",
            "query": ["question_id", "question"],
            "response": ["question_id", "answer", "evidence"],
            "note": "🔴 호스트(공인 IP)는 이 사이트에 싣지 않는다. 엔드포인트 정본은 "
                    "`deploy/README.md` 첫 절이다.",
            "source": SOURCES["endpoint"],
        },
        "sources": sources_for(gen.get("current")),
    }


BUILDERS = {"meta": build_meta, "ablation": build_ablation, "errors": build_errors,
            "pipeline": build_pipeline, "data": build_data}


# ─────────────────────────────────────────────────────────────────────────────
# 7. 보안 게이트 — 🔴 파일을 쓰기 **전에** 전량을 훑는다
# ─────────────────────────────────────────────────────────────────────────────

SECRET_PATTERNS = (
    ("API 키 형태 nv-", re.compile(r"nv-[A-Za-z0-9]{8,}")),
    ("API 키 형태 sk-", re.compile(r"sk-[A-Za-z0-9]{8,}")),
    ("Bearer 토큰", re.compile(r"Bearer\s+\S{8,}")),
    ("NCP 헤더", re.compile(r"X-NCP-[A-Za-z0-9-]+")),
    ("키 이름", re.compile(r"CLOVA_API_KEY|CLOVA_STUDIO_API_KEY")),
    ("40자 이상 영숫자 덩어리", re.compile(r"(?<![A-Za-z0-9])[A-Za-z0-9]{40,}(?![A-Za-z0-9])")),
)

CORPUS_TOKENS = ("dart-disclosures", "periodic_sections.txt", "universe.csv")
HELDOUT_TOKENS = ("heldout", "held-out", "held_out")
#: DART 접수번호 14자리. 골든 근거 식별자가 어떤 경로로도 새지 않았음을 확인한다.
DOC_ID = re.compile(r"(?<!\d)\d{14}(?!\d)")

EVALSET_FILES = ("evalset/questions.jsonl", "evalset/regression.jsonl",
                 "evalset/valuechain.jsonl")
PROBE_PREFIX = 20


def _evalset_probes() -> list[tuple[str, str]]:
    """질문 전문과 **앞 20자**, 그리고 긴 정답 문장을 프로브로 만든다 (보안 ④).

    🔴 `heldout` 은 여기서도 열지 않는다 — `read_text()` 가 경로째 거부한다.
    """
    probes: list[tuple[str, str]] = []
    for rel in EVALSET_FILES:
        if not exists(rel):
            continue
        for line in read_text(rel).splitlines():
            if not line.strip():
                continue
            item = json.loads(line)
            qid = item.get("question_id", "?")
            for field in ("question", "answer"):
                value = item.get(field)
                if not isinstance(value, str):
                    continue
                value = " ".join(value.split())
                if len(value) >= PROBE_PREFIX:
                    probes.append((f"{qid}.{field}[:{PROBE_PREFIX}]", value[:PROBE_PREFIX]))
                    probes.append((f"{qid}.{field} 전문", value))
    return probes


def public_ips() -> list[str]:
    """🔴 IP 를 코드에 적지 않는다. 정본(`deploy/README.md`)에서 뽑아 쓴다."""
    if not exists("deploy/README.md"):
        return []
    found = re.findall(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])",
                       read_text("deploy/README.md"))
    return sorted(set(found))


def gate_all(files: dict[str, str]) -> list[str]:
    """산출물 전량(HTML·JSON·CSS·README)을 훑는다. 걸리면 `SiteLeak` — **아무것도 쓰지 않는다.**"""
    violations: list[str] = []
    probes = _evalset_probes()
    ips = public_ips()
    for name, text in sorted(files.items()):
        low = text.lower()
        for label, pattern in SECRET_PATTERNS:
            m = pattern.search(text)
            if m:
                violations.append(f"보안 ① [{name}] {label} — {m.group(0)[:12]}…")
        for token in CORPUS_TOKENS:
            if token.lower() in low:
                violations.append(f"보안 ② [{name}] 코퍼스 원문 참조 — {token!r}")
        for token in HELDOUT_TOKENS:
            if token in low:
                violations.append(f"보안 ③ [{name}] heldout 언급 — {token!r}")
        for label, probe in probes:
            if probe in text:
                violations.append(f"보안 ④ [{name}] 평가셋 문항 전문/정답 — {label}")
        for field in sorted(ROW_FIELDS_BLOCKED):
            if re.search(rf'"{field}"\s*:', text):
                violations.append(f"보안 ⑤ [{name}] 차단 필드가 실렸다 — {field!r}")
        m = DOC_ID.search(text)
        if m:
            violations.append(f"보안 ⑤ [{name}] 문서 식별자 14자리 — {m.group(0)}")
        for ip in ips:
            if ip in text:
                violations.append(f"보안 + [{name}] 공인 IP 발행 — deploy/README.md 의 IP")
    if violations:
        raise SiteLeak("🔴 보안 게이트 발화 — 아무 파일도 쓰지 않았다:\n  - "
                       + "\n  - ".join(violations))
    return [f"{len(files)} 파일 · 게이트 6종 통과 (프로브 {len(probes)}개 · IP {len(ips)}개)"]


# ─────────────────────────────────────────────────────────────────────────────
# 8. 렌더러 — 정적 HTML 5쪽 (Node 0 · 템플릿에 수치를 적지 않는다)
# ─────────────────────────────────────────────────────────────────────────────

#: 정적 필터가 화면에 쓰는 열. 🔴 `ROW_FIELDS_ALLOWED` 의 **부분집합**이어야 한다.
FILTER_ROW_FIELDS = (
    "question_id", "generation", "arm", "cohort", "track", "task_type", "difficulty",
    "doc_group", "correct", "first_failure", "root_cause", "failure_why",
)

PAGES = (
    ("index.html", "개요"),
    ("ablation.html", "Ablation"),
    ("errors.html", "오류 분석"),
    ("pipeline.html", "파이프라인"),
    ("data.html", "데이터"),
)


def esc(value) -> str:
    """🔴 모든 값이 이 함수를 통과한다. 산출물의 자유 서술이 HTML 을 깨지 못한다."""
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "예" if value else "아니오"
    if isinstance(value, float):
        if value == int(value):
            value = f"{int(value):,}"
        elif abs(value) < 1:
            value = f"{value:.4g}"          # 비율·밀도는 자리수를 지운다
        else:
            value = f"{value:,.1f}"         # 🔴 19,730.5 의 소수 한 자리를 잃지 않는다
    if isinstance(value, int):
        value = f"{value:,}"
    return html.escape(str(value), quote=True)


def prose(text) -> str:
    """이 저장소의 문장은 마크다운으로 쓰여 있다 — 이스케이프한 **뒤에** 강조만 되살린다."""
    out = esc(text)
    out = re.sub(r"`([^`]+)`", r"<code>\g<1></code>", out)
    out = re.sub(r"[*][*]([^*]+)[*][*]", r"<strong>\g<1></strong>", out)
    return out


def pct(value) -> str:
    """0~1 비율을 백분율로. 🔴 분모 라벨은 부르는 쪽이 붙인다."""
    if value is None:
        return "—"
    return f"{100.0 * float(value):.1f}%"


def score(s: dict | None) -> str:
    if not s or s.get("n") in (None, 0):
        return "—"
    rate = "" if s.get("rate") is None else f" ({s['rate']:.1f}%)"
    return f"{esc(s['correct'])}/{esc(s['n'])}{rate}"


def pp(value) -> str:
    if value is None:
        return "—"
    return f"{value:+.1f}%p"


def card(title: str, body: str, *, kicker: str = "", tone: str = "") -> str:
    kick = f'<p class="kicker">{esc(kicker)}</p>' if kicker else ""
    return (f'<section class="card pad {tone}">{kick}'
            f'<h3>{esc(title)}</h3>{body}</section>')


def metric(label: str, value: str, note: str = "") -> str:
    sub = f'<p class="muted small">{esc(note)}</p>' if note else ""
    return (f'<div class="card pad metric-card"><p class="kicker">{esc(label)}</p>'
            f'<p class="metric">{value}</p>{sub}</div>')


def table(headers: list[str], rows: list[list[str]], *, caption: str = "") -> str:
    if not rows:
        return '<p class="muted">산출물이 없다 — 이 표는 렌더되지 않았다.</p>'
    head = "".join(f"<th>{esc(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>" for row in rows)
    cap = f"<caption>{esc(caption)}</caption>" if caption else ""
    return (f'<div class="table-wrap"><table class="table">{cap}'
            f"<thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>")


def bar(value: float | None, *, max_value: float = 100.0, tone: str = "") -> str:
    if value is None:
        return "—"
    width = max(0.0, min(100.0, 100.0 * abs(value) / max_value if max_value else 0))
    cls = tone or ("neg" if value < 0 else "pos")
    return (f'<span class="bar"><span class="bar-fill {cls}" '
            f'style="width:{width:.1f}%"></span></span>')


def gen_badge(gen: str | None, extra: str = "") -> str:
    """🔴 세대 라벨. 값 옆에 **언제나** 붙는다."""
    label = f"세대 {gen}" if gen else "세대 미확인"
    prefix = f"{esc(extra)} " if extra else ""
    return f'<span class="pill">{prefix}{esc(label)}</span>'


def page(active: str, title: str, body: str, meta: dict, generation: dict) -> str:
    links = "".join(
        '<a href="{}"{}>{}</a>'.format(
            href, ' class="is-active"' if href == active else "", esc(label))
        for href, label in PAGES
    )
    gen_line = " · ".join(filter(None, [
        f"성적 세대 {generation.get('current')}" if generation.get("current") else "",
        f"사다리 세대 {generation.get('ladder')}" if generation.get("ladder") else "",
        f"밸류체인 세대 {generation.get('valuechain')}" if generation.get("valuechain") else "",
    ]))
    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>{esc(title)} — ARGUS</title>
<link rel="stylesheet" href="style.css">
</head>
<body>
<nav class="nav"><div class="container nav-inner">
<a class="logo" href="index.html">ARGUS</a>
<div class="links">{links}</div>
</div></nav>
<main class="container">
{body}
</main>
<footer class="footer"><div class="container">
<p><strong>{esc(gen_line)}</strong> · 기준선 arm {esc(generation.get("baselineArm"))}</p>
<p class="small">{prose(generation.get("note"))}</p>
<p class="small">빌드 {esc(meta.get("generated"))} · 커밋 <code>{esc(meta.get("argusCommit"))}</code>
 · 빌더 <code>{esc(meta.get("builder"))}</code> · LLM 호출 {esc(meta.get("llmCalls"))}회
 · 크레딧 {esc(meta.get("creditWon"))}원</p>
<p class="small">🔴 로컬 정적 빌드다. 평가 서버에 올리지 않는다. 평가셋 문항 전문·정답·골든 근거·
공인 IP 는 싣지 않는다 — <a href="README.md">site/README.md</a></p>
</div></footer>
</body>
</html>
"""


def render_index(meta: dict) -> str:
    h, gen = meta["headline"], meta["generation"]
    tracks = h.get("tracks") or {}
    cards = [
        metric("그리드 (제출 구성)", score(tracks.get("grid")),
               f"{DENOMINATOR_DEFS['grid']} · 세대 {h.get('generation')} · arm {h.get('arm')}"),
        metric("전체", score(h.get("overall")), DENOMINATOR_DEFS["overall"]),
        metric("회귀", score(tracks.get("regression")), DENOMINATOR_DEFS["regression"]),
        metric("정보한계", score(tracks.get("info_limit")), DENOMINATOR_DEFS["info_limit"]),
    ]
    vc = h.get("valuechain")
    if vc:
        cards.append(metric(
            "밸류체인", f'{score(vc["scored"])}<br><span class="small neg-text">실질 '
                     f'{score(vc["effective"])}</span>',
            f'{DENOMINATOR_DEFS["valuechain"]} · 세대 {vc.get("generation")}'))
    ledger = meta["ledger"]
    cards.append(metric("누적 지출",
                        f'{esc(ledger["spentWon"])}원',
                        f'예산 {ledger["budgetWon"]:,.0f}원의 {ledger["sharePct"]}% · '
                        f'원장 {len(ledger["rows"])}행 · 정본 {ledger["source"]}'))

    warn = ""
    if vc:
        ids = ", ".join(vc["inflated"]["ids"]) or "없음"
        warn = card("🔴 밸류체인 정답률에는 부풀림이 포함돼 있다",
                    f'<p>{prose(vc["warning"])}</p><p class="small">부풀림 문항 '
                    f'{esc(ids)} · 셈의 정본 <code>{esc(vc["inflated"]["source"])}</code></p>'
                    f'<p class="small">{esc((vc.get("goldenEvidence") or {}).get("note"))}</p>',
                    tone="warn")

    mixed = gen.get("mixedGenerationWarning")
    mixed_html = card("🔴 세대 라벨을 반드시 읽는다", f"<p>{prose(mixed)}</p>", tone="warn") \
        if mixed else ""

    caps = [
        ("문서군별 별도 색인 + 라우터", "그리드·회귀·정보한계 성적은 모두 같은 세대의 "
                                "한 구성(A8)에서 나온 값이다."),
        ("그래프 hop — 정정체인 · 관련공시 · 발주처", "LOO 로 축별 기여를 따로 잰다. "
                                            "Ablation 쪽 표를 보라."),
        ("LLM 0회 결정적 검증", "계산 검사·완결성 검사는 모델을 부르지 않는다. "
                          "한계도 함께 적었다 — 오류 분석 쪽."),
        ("측정 후 채택", "arm 판정은 겨냥 건수와 분모 라벨로만 한다. 기각도 결과다 — "
                    "판정 대장을 링크한다."),
    ]
    cap_html = "".join(card(t, f'<p class="muted">{esc(d)}</p>') for t, d in caps)

    body = f"""
<section class="hero">
  <p class="kicker">DART 공시 QA 에이전트 · 결과 공개 (로컬 정적 빌드)</p>
  <h1>측정한 것만 싣는다</h1>
  <p class="lede">이 사이트의 모든 수는 <code>artifacts/</code> 에서 세거나
  <code>{esc(SSOT)}</code> 를 파싱한 값이다. 🔴 사본을 만들지 않는다 — 정본이 바뀌면
  다시 빌드하면 된다. 검산이 깨지면 빌드가 죽는다.</p>
  <p>{gen_badge(gen.get("current"), "성적")} {gen_badge(gen.get("ladder"), "사다리")}
  {gen_badge(gen.get("valuechain"), "밸류체인")}</p>
</section>
{mixed_html}
<section class="section">
  <div class="section-heading"><h2>지표</h2>
  <p class="muted">🔴 분모 라벨 없이 인용하지 않는다.</p></div>
  <div class="grid cols-3">{"".join(cards)}</div>
</section>
{warn}
<section class="section">
  <div class="section-heading"><h2>이 시스템이 하는 일</h2></div>
  <div class="grid cols-2">{cap_html}</div>
</section>
<section class="section">
  <div class="section-heading"><h2>정본 링크</h2>
  <p class="muted">🔴 값을 옮겨 적지 않는 자리는 링크로 남긴다.</p></div>
  {table(["무엇", "정본", "자리"],
         [[esc(v["label"]), f'<a href="{esc(v["href"])}"><code>{esc(v["href"])}</code></a>',
           esc(v["anchor"])] for v in meta["sources"].values()])}
</section>
<section class="section">
  <div class="section-heading"><h2>🔴 09-21 이후 붙일 자리 — 라이브 질의응답 데모</h2></div>
  <div class="card pad" id="live-demo-slot" data-status="reserved">
    <p>평가 기간(09-07~09-20)이 끝난 뒤에 붙인다. <strong>지금은 비어 있다.</strong></p>
    <ul>
      <li>지금 이 사이트는 <strong>정적 필터</strong>만 제공한다 — 오류 분석 쪽 필터.</li>
      <li>붙일 때 함께 붙일 것 둘: <strong>소비 상한</strong>과
          <strong>API 키 분리 또는 상한</strong>. 방문자가 부르는 만큼 유료 호출이 붙는다
          (단가 {esc(h.get("priceWonPer1k"))}원/1K 토큰 · 산출물에서 읽은 값).
          같은 키를 쓰면 측정 크레딧을 나눠 쓴다.</li>
      <li>🔴 데모의 질문 입력은 <strong>사용자가 쓴 문장</strong>이어야 한다.
          평가셋 문항을 채워 두면 보안 ④ 가 깨진다.</li>
    </ul>
  </div>
</section>
"""
    return page("index.html", "개요", body, meta["metadata"], gen)


def render_ablation(data: dict) -> str:
    gen = data["generation"]
    ladder, loo, diag, vc = data["ladder"], data["loo"], data["diagnostic"], data["valuechain"]
    max_abs = max([abs(r["deltaGridPp"]) for r in ladder["rows"]
                   if r["deltaGridPp"] is not None] or [1.0])
    ladder_rows = [[
        f'<strong>{esc(r["arm"])}</strong>', gen_badge(r["generation"]),
        score(r["grid"]), score(r["overall"]),
        esc(r["deltaFrom"]), pp(r["deltaGridPp"]),
        bar(r["deltaGridPp"], max_value=max_abs), esc(r["verdict"]),
    ] for r in ladder["rows"]]
    loo_rows = [[
        f'<strong>{esc(r["arm"])}</strong>', gen_badge(r["generation"]),
        score(r["grid"]), score(r["overall"]), pp(r["deltaGridPp"]),
        bar(r["deltaGridPp"], max_value=max([abs(x["deltaGridPp"]) for x in loo["rows"]
                                             if x["deltaGridPp"] is not None] or [1.0])),
        esc(r["verdict"]),
    ] for r in loo["rows"]]
    diag_rows = [[
        f'<strong>{esc(r["arm"])}</strong>', gen_badge(r["generation"]), esc(r["kind"]),
        score(r["grid"]), score(r["overall"]), esc(r["verdict"]),
        f'<span class="neg-text">{esc(r["label"])}</span>',
    ] for r in diag["rows"]]
    vc_rows = [[
        f'<strong>{esc(r["arm"])}</strong>', gen_badge(r["generation"]),
        score(r["scored"]),
        f'<span class="neg-text">{score(r["effective"])}</span>',
        esc(", ".join(r["inflated"]["ids"]) or "—"),
        (f'{esc((r["goldenEvidence"] or {}).get("hit"))}/'
         f'{esc((r["goldenEvidence"] or {}).get("denominator"))}'
         f' (조정 {esc((r["goldenEvidence"] or {}).get("denominatorAdjusted"))})')
        if r.get("goldenEvidence") else "—",
        "진단 arm" if r["diagnostic"] else esc(r["verdict"]),
    ] for r in vc["rows"]]

    ledger_rows = [[esc(r["no"]), esc(r["item"]), esc(r["tokens"]),
                    f'{esc(r["won"])}원', esc(r["source"])] for r in data["ledger"]["rows"]]
    verdict_rows = [[esc(r["arm"]), esc(r["symbol"]), esc(r["verdict"]), esc(r["kind"]),
                     esc(r["targets"][:120])] for r in data["verdicts"]["rows"]]

    heat = ""
    for label, block in sorted(data["heatmap"]["axes"].items()):
        for axis, cells in sorted(block["axes"].items()):
            arms = sorted({a for v in cells.values() for a in v})
            rows = [[esc(bucket)] + [score({"correct": cells[bucket][a]["correct"],
                                            "n": cells[bucket][a]["n"],
                                            "rate": _rate(cells[bucket][a]["correct"],
                                                          cells[bucket][a]["n"])})
                                     if a in cells[bucket] else "—" for a in arms]
                    for bucket in cells]
            heat += (f'<h4>{esc(axis)} × arm {gen_badge(block["generation"])}</h4>'
                     + table([axis] + arms, rows))

    body = f"""
<section class="hero">
  <p class="kicker">Ablation</p>
  <h1>무엇이 성능을 벌었나</h1>
  <p class="lede">🔴 사다리와 LOO 는 <strong>다른 세대</strong>일 수 있다. 표마다 세대 라벨이
  붙어 있고, 서로 빼지 않는다.</p>
</section>
<section class="section">
  <div class="section-heading"><h2>사다리 {gen_badge(ladder["generation"])}</h2>
  <p class="muted">{prose(ladder.get("note"))}</p></div>
  {table(["arm", "세대", "그리드", "전체", "Δ 기준", "Δ (그리드)", "", "판정"], ladder_rows)}
  <p class="small muted">최대 전이 {esc((ladder.get("maxDelta") or {}).get("from"))} →
  {esc((ladder.get("maxDelta") or {}).get("arm"))}
  {pp((ladder.get("maxDelta") or {}).get("pp"))} · 전이 {esc(ladder.get("transitions"))}개</p>
</section>
<section class="section">
  <div class="section-heading"><h2>LOO — 축을 하나씩 뺀다 {gen_badge(loo["generation"])}</h2>
  <p class="muted">{prose(loo.get("note"))}</p></div>
  {table(["arm", "세대", "그리드", "전체", "Δ vs 기준선", "", "판정"], loo_rows)}
  <p class="small muted">기준선 {esc((loo.get("baseline") or {}).get("arm"))}
  {score((loo.get("baseline") or {}).get("grid"))}</p>
</section>
<section class="section warnbox">
  <div class="section-heading"><h2>🔴 진단·기각 arm — 성적이 아니다</h2>
  <p class="muted">{prose(diag.get("note"))}</p></div>
  {table(["arm", "세대", "성격", "그리드", "전체", "판정", "읽는 법"], diag_rows)}
</section>
<section class="section">
  <div class="section-heading"><h2>밸류체인 {gen_badge(vc["generation"])}</h2>
  <p class="muted neg-text">{prose(vc.get("warning"))}</p></div>
  {table(["arm", "세대", "채점", "실질", "부풀림 문항", "골든 근거 회수", "판정"], vc_rows)}
</section>
<section class="section">
  <div class="section-heading"><h2>축별 히트맵</h2>
  <p class="muted">{prose(data["heatmap"].get("note"))}</p></div>
  {heat or '<p class="muted">산출물이 없다.</p>'}
</section>
<section class="section">
  <div class="section-heading"><h2>지출 원장</h2>
  <p class="muted">정본 <code>{esc(data["ledger"]["source"])}</code> ·
  행 합과 <code>누적</code> 행을 대조하고 통과했다. 어긋나면 빌드가 죽는다.</p></div>
  {table(["#", "항목", "토큰", "비용", "출처"], ledger_rows)}
  <p><strong>누적 {esc(data["ledger"]["spentWon"])}원</strong> /
  {esc(data["ledger"]["budgetWon"])}원</p>
</section>
<section class="section">
  <div class="section-heading"><h2>arm 판정 — 정본은 대장이다</h2>
  <p class="muted">{prose(data["verdicts"].get("note"))} 정본
  <code>{esc(data["verdicts"]["source"])}</code></p></div>
  {table(["arm", "", "판정", "성격", "겨냥 (분모 라벨)"], verdict_rows)}
</section>
"""
    return page("ablation.html", "Ablation", body, data["metadata"], gen)


def render_errors(data: dict) -> str:
    gen = data["generation"]
    den_rows = [[
        f'<strong>{esc(d["label"])}</strong>', gen_badge(d["generation"], f'arm {d["arm"]}'),
        score({"correct": d["correct"], "n": d["n"], "rate": d["rate"]}),
        esc(d["wrong"]),
        ", ".join(f'{esc(k)} {esc(v)}' for k, v in sorted(d["stages"].items())) or "—",
        esc(d["stageSum"]),
    ] for d in data["denominators"]]

    dist = data["distributions"]
    stage_rows = [[esc(s), esc((dist.get("stagesGrid") or {}).get(s, 0)),
                   esc((dist.get("stagesAll") or {}).get(s, 0))]
                  for s in dist.get("stageOrder", [])]
    root_rows = [[esc(k), esc(v)] for k, v in sorted((dist.get("rootCausesGrid") or {}).items())]
    docg_rows = [[esc(k), score({"correct": v["correct"], "n": v["n_scored"] or v["n"],
                                 "rate": _rate(v["correct"], v["n_scored"] or v["n"])}),
                  ", ".join(f'{esc(a)} {esc(b)}' for a, b in sorted((v.get("stages") or {}).items()))
                  or "—"]
                 for k, v in sorted((dist.get("byDocGroupGrid") or {}).items())]
    limit_rows = [[esc(r["item"]), esc(r["scale"]), esc(r["why"])]
                  for r in data["dataLimits"]["rows"]]

    filters = data["filters"]

    def options(key: str, label: str) -> str:
        opts = "".join(f'<option value="{esc(v)}">{esc(v)}</option>' for v in filters.get(key, []))
        return (f'<label class="filter">{esc(label)}'
                f'<select data-filter="{esc(key)}"><option value="">전체</option>{opts}</select>'
                f"</label>")

    # 🔴 쪽에 박는 것은 **화면이 쓰는 열만**이다 (화이트리스트의 부분집합). 전량은
    # `data/site_errors.json` 에 있다. 정적 필터라 `fetch` 를 쓸 수 없어 값을 쪽에 넣는다.
    slim = [{k: r.get(k) for k in FILTER_ROW_FIELDS} for r in data["rows"]]
    rows_json = json.dumps(slim, ensure_ascii=False, separators=(",", ":")) \
        .replace("</", "<\\/")
    remaining = data["remainingWrong"]
    body = f"""
<section class="hero">
  <p class="kicker">오류 분석</p>
  <h1>분모가 셋이다 — 라벨 없이 읽지 않는다</h1>
  <p class="lede">{prose(data["metadata"]["note"])}</p>
</section>
<section class="section">
  <div class="section-heading"><h2>분모별 성적과 단계 귀속</h2></div>
  {table(["분모", "세대", "성적", "오답", "단계 분포", "단계 합"], den_rows)}
  <p class="small muted">🔴 단계 합이 오답 수와 같아야 한다. 두 열을 함께 싣는 이유가 그것이다.</p>
</section>
<section class="section">
  <div class="section-heading"><h2>현 세대 잔여 오답 {gen_badge(remaining["generation"],
      f'arm {remaining["arm"]}')}</h2></div>
  <p><strong>{esc(remaining["count"])}건</strong> — {esc(", ".join(remaining["ids"]) or "없음")}</p>
  <p class="small muted">{prose(remaining["note"])}</p>
</section>
<section class="section">
  <div class="grid cols-2">
    {card("단계 분포", table(["단계", "그리드", "전 트랙"], stage_rows))}
    {card("근본 원인 (그리드)", table(["단계/원인", "건수"], root_rows))}
  </div>
</section>
<section class="section">
  <div class="section-heading"><h2>문서군별 (그리드)</h2></div>
  {table(["문서군", "성적", "단계"], docg_rows)}
</section>
<section class="section">
  <div class="section-heading"><h2>정적 필터 — 문항 단위 채점 결과</h2>
  <p class="muted">🔴 실리는 것은 <code>question_id</code> + 축 메타 + 채점 결과뿐이다.
  질문 전문·정답·골든 근거·추론 흔적은 없다(화이트리스트
  {esc(len(data["rowFields"]["allowed"]))}필드 · 차단
  {esc(len(data["rowFields"]["blocked"]))}필드).</p>
  <p class="muted neg-text">🔴 세대가 다른 arm 을 서로 빼지 마라. 세대 열을 보라.</p></div>
  <div class="filters">
    {options("generation", "세대")}
    {options("cohort", "코호트")}
    {options("arm", "arm")}
    {options("track", "분모(track)")}
    {options("task_type", "task_type")}
    {options("difficulty", "난이도")}
    {options("doc_group", "문서군")}
    {options("first_failure", "실패 단계")}
    <label class="filter">정답 여부<select data-filter="correct">
      <option value="">전체</option><option value="true">정답</option>
      <option value="false">오답</option></select></label>
    <button type="button" id="filter-reset">초기화</button>
  </div>
  <p class="muted small" id="filter-count"></p>
  <div class="table-wrap"><table class="table" id="rows-table">
    <thead><tr><th>문항</th><th>세대</th><th>arm</th><th>코호트</th><th>분모</th>
    <th>task_type</th><th>난이도</th><th>문서군</th><th>채점</th><th>실패 단계</th>
    <th>근본 원인</th><th>사유 (금액 마스킹)</th></tr></thead>
    <tbody></tbody></table></div>
  <script type="application/json" id="rows-data">{rows_json}</script>
  <script src="filter.js"></script>
</section>
<section class="section">
  <div class="section-heading"><h2>데이터 상한 — 노력으로 넘을 수 없는 것</h2>
  <p class="muted">정본 <code>{esc(data["dataLimits"]["source"])}</code> —
  규모에 붙은 <code>⚠️ 출처 미확인</code> 표시를 지우지 않았다.</p></div>
  {table(["항목", "규모", "왜"], limit_rows)}
</section>
"""
    return page("errors.html", "오류 분석", body, data["metadata"], gen)


def render_pipeline(data: dict) -> str:
    gen = data["generation"]
    rows = [[esc(p["no"]),
             (f'<a href="../docs/{esc(p["docHref"])}">{esc(p["doc"])}</a>'
              if p.get("docHref") else esc(p["doc"])),
             esc(p["phase"]), esc(p["status"]), esc(p["gate"]), esc(p["credit"])]
            for p in data["phases"]]
    credit = data["credit"]
    body = f"""
<section class="hero">
  <p class="kicker">파이프라인</p>
  <h1>Phase A~I · 게이트 · 크레딧</h1>
  <p class="lede">{prose(data["metadata"]["note"])}</p>
</section>
<section class="section">
  <div class="grid cols-3">
    {metric("게이트", f'{esc(data["gates"]["passed"])} / {esc(data["gates"]["total"])}',
            data["gates"]["check"])}
    {metric("테스트", f'{esc(data["tests"]["passed"])} passed',
            "정본 문서 기준 — " + data["tests"]["check"])}
    {metric("누적 지출", f'{esc(credit["spentWon"])}원',
            f'Phase 크레딧 열 {credit["phaseColumnWon"]:,.1f}원 + 배분 밖 '
            f'{esc(credit["unallocatedWon"])}원')}
  </div>
  <p class="small muted">{prose(credit["note"])}</p>
</section>
<section class="section">
  <div class="section-heading"><h2>Phase 표</h2>
  <p class="muted">🔴 Phase 별 서술은 옮겨 적지 않았다 — 문서 열의 링크가 정본이다.
  옮겨 적은 문장은 라운드마다 낡는다.</p></div>
  {table(["#", "문서", "Phase", "상태", "게이트", "크레딧"], rows)}
</section>
<section class="section">
  <div class="section-heading"><h2>측정이 계획을 뒤집었다</h2></div>
  <div class="card pad">
    <p>{prose(data["revisions"]["note"])}</p>
    <p><a href="{esc(data["revisions"]["href"])}"><code>{esc(data["revisions"]["href"])}</code></a></p>
  </div>
</section>
"""
    return page("pipeline.html", "파이프라인", body, data["metadata"], gen)


def render_data(data: dict) -> str:
    gen = data["generation"]
    corpus = data["corpus"]
    corpus_rows = [[esc(r["group"]), esc(r["files"]), f'{esc(r["mb"])} MB',
                    f'{esc(r["sharePct"])}%', f'{esc(r["avgKb"])} KB'] for r in corpus["rows"]]
    index_rows = [[f'<code>{esc(r["artifact"])}</code>', esc(r["bytes"]),
                   f'<code>{esc(r["digest12"])}</code>'] for r in data["index"]["rows"]]
    recall_rows = [[esc(c["cohort"]), esc(c["definition"] or ""), esc(c["nCases"]),
                    f'{esc(c["recall5"])}/{esc(c["goldenDenominator"])}',
                    pct(c["recall5Rate"]), pct(c["evidenceHitRate"])]
                   for c in data["recall"]["cohorts"]]
    graph = data["graph"]
    api = data["apiContract"]
    body = f"""
<section class="hero">
  <p class="kicker">데이터</p>
  <h1>코퍼스 · 색인 · 검색 지표</h1>
  <p class="lede">{prose(data["metadata"]["note"])}</p>
</section>
<section class="section">
  <div class="section-heading"><h2>코퍼스 규모</h2>
  <p class="muted">{prose(corpus["note"])} 매니페스트 {esc(corpus["docsInManifest"])}건 ·
  파싱 {esc(corpus["docsParsed"])}건 · 합계 약 {esc(corpus["totalMb"])} MB</p></div>
  {table(["문서군", "파일", "크기", "비중", "평균"], corpus_rows)}
</section>
<section class="section">
  <div class="section-heading"><h2>색인 산출물 {gen_badge(data["index"]["generation"])}</h2>
  <p class="muted">{prose(data["index"]["note"])}</p></div>
  {table(["산출물", "바이트", "지문"], index_rows)}
</section>
<section class="section">
  <div class="section-heading"><h2>Recall@5 — 코호트 라벨 필수</h2>
  <p class="muted">{prose(data["recall"]["note"])} 정본
  <code>{esc(data["recall"]["source"])}</code></p></div>
  {table(["코호트", "정의", "문항", "Recall@5", "비율", "근거 적중률"], recall_rows)}
</section>
<section class="section">
  <div class="section-heading"><h2>그래프 — 무엇이 기각됐나</h2></div>
  <div class="grid cols-3">
    {metric("계약 레코드", esc(graph["contracts"]), "상대방 기재 " + pct(graph["fillRate"]))}
    {metric("엣지 밀도(엄격)", pct(graph["edgeDensityStrict"]),
            f'엣지 {esc(graph["edgesExact"])} · 유니버스 {esc(graph["universeSize"])}')}
    {metric("그래프 완결도", f'{esc(graph["completenessPct"])}%', "기업—기업 축")}
  </div>
  <p class="neg-text">{prose(graph["verdict"])}</p>
</section>
<section class="section">
  <div class="section-heading"><h2>API 계약</h2></div>
  <div class="card pad">
    <p><code>{esc(api["method"])} {esc(api["path"])}?{esc("=…&".join(api["query"]))}=…</code></p>
    <p class="small">응답 필드 {esc(", ".join(api["response"]))}</p>
    <p class="neg-text small">{prose(api["note"])}</p>
    <p class="small">정본 <a href="{esc(api["source"]["href"])}">
    <code>{esc(api["source"]["href"])}</code></a> — {esc(api["source"]["anchor"])}</p>
  </div>
</section>
"""
    return page("data.html", "데이터", body, data["metadata"], gen)


RENDERERS = {"index.html": render_index, "ablation.html": render_ablation,
             "errors.html": render_errors, "pipeline.html": render_pipeline,
             "data.html": render_data}

FILTER_JS = """/* 정적 필터 — Node 0 · 프레임워크 0 · 네트워크 0.
   데이터는 빌드 타임에 이 쪽 안에 박혔다. 🔴 라이브 질의응답이 아니다. */
(function () {
  var node = document.getElementById("rows-data");
  if (!node) { return; }
  var rows = JSON.parse(node.textContent);
  var body = document.querySelector("#rows-table tbody");
  var count = document.getElementById("filter-count");
  var selects = Array.prototype.slice.call(document.querySelectorAll("[data-filter]"));
  var LIMIT = 400;

  function text(value) {
    if (value === null || value === undefined || value === "") { return "—"; }
    if (value === true) { return "정답"; }
    if (value === false) { return "오답"; }
    return String(value);
  }

  function matches(row) {
    return selects.every(function (sel) {
      if (!sel.value) { return true; }
      var key = sel.getAttribute("data-filter");
      if (key === "correct") { return String(!!row.correct) === sel.value; }
      return String(row[key]) === sel.value;
    });
  }

  function render() {
    var shown = rows.filter(matches);
    var slice = shown.slice(0, LIMIT);
    var html = slice.map(function (r) {
      return "<tr class=" + (r.correct ? '"ok"' : '"bad"') + ">" +
        [r.question_id, r.generation, r.arm, r.cohort, r.track, r.task_type,
         r.difficulty, r.doc_group, r.correct, r.first_failure, r.root_cause,
         r.failure_why].map(function (v) {
          return "<td>" + text(v).replace(/&/g, "&amp;").replace(/</g, "&lt;") + "</td>";
        }).join("") + "</tr>";
    }).join("");
    body.innerHTML = html;
    count.textContent = shown.length + "행 (전체 " + rows.length + "행" +
      (shown.length > LIMIT ? " · 앞 " + LIMIT + "행만 표시" : "") + ")";
  }

  selects.forEach(function (sel) { sel.addEventListener("change", render); });
  var reset = document.getElementById("filter-reset");
  if (reset) {
    reset.addEventListener("click", function () {
      selects.forEach(function (sel) { sel.value = ""; });
      render();
    });
  }
  render();
})();
"""

CSS = """/* ARGUS 결과 사이트 — 토큰과 시맨틱 클래스는 scratch/ARGUS_example 의 레퍼런스를 옮긴 것이다.
   버린 것은 런타임(Next.js·React·TS)이고, 디자인 언어는 남겼다. */
:root {
  --bg: #f5f7f9;
  --surface: #ffffff;
  --surface-strong: #eef6f3;
  --text: #172033;
  --muted: #667085;
  --line: #d9e0e8;
  --primary: #2458d6;
  --primary-dark: #183b8f;
  --teal: #0f8f7f;
  --amber: #b7791f;
  --danger: #c0392b;
  --ink-soft: #26364f;
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--text);
  font-family: "Malgun Gothic", Arial, Helvetica, sans-serif;
  font-size: 15px; line-height: 1.6;
}
a { color: var(--primary-dark); }
code { background: #eef4ff; border-radius: 4px; padding: 1px 4px; font-size: 92%; }
h1 { font-size: 32px; line-height: 1.2; margin: 8px 0 12px; color: var(--ink-soft); }
h2 { font-size: 22px; margin: 0 0 6px; color: var(--ink-soft); }
h3 { font-size: 16px; margin: 0 0 8px; color: var(--ink-soft); }
h4 { font-size: 15px; margin: 18px 0 8px; color: var(--ink-soft); }
p { margin: 0 0 10px; }
.container { max-width: 1180px; margin: 0 auto; padding: 0 20px; }
.nav {
  position: sticky; top: 0; z-index: 20; background: rgba(255, 255, 255, 0.94);
  border-bottom: 1px solid var(--line); backdrop-filter: blur(10px);
}
.nav-inner { min-height: 60px; display: flex; align-items: center;
  justify-content: space-between; gap: 16px; flex-wrap: wrap; }
.logo { font-weight: 800; color: var(--ink-soft); text-decoration: none; letter-spacing: 0.04em; }
.links { display: flex; gap: 6px; flex-wrap: wrap; font-size: 14px; font-weight: 700; }
.links a { color: var(--muted); text-decoration: none; border-radius: 999px;
  padding: 6px 10px; border: 1px solid transparent; }
.links a:hover { color: var(--primary-dark); }
.links a.is-active { border-color: #c6d5f7; background: #eef4ff; color: var(--primary-dark); }
.hero { padding: 34px 0 6px; }
.lede { color: var(--muted); max-width: 74ch; }
.kicker { color: var(--muted); font-size: 12px; font-weight: 800;
  letter-spacing: 0.08em; text-transform: uppercase; margin: 0 0 6px; }
.section { padding: 26px 0; border-top: 1px solid var(--line); }
.section-heading { margin-bottom: 14px; }
.card { background: var(--surface); border: 1px solid var(--line); border-radius: 8px;
  box-shadow: 0 10px 26px rgba(23, 32, 51, 0.06); }
.card.pad { padding: 16px 18px; }
.card.warn { border-color: #f0d3ce; background: #fdf6f5; }
.warnbox .card, .warnbox .table { border-color: #f0d3ce; }
.grid { display: grid; gap: 14px; }
.grid.cols-2 { grid-template-columns: repeat(2, minmax(0, 1fr)); }
.grid.cols-3 { grid-template-columns: repeat(3, minmax(0, 1fr)); }
.metric-card .metric { font-size: 30px; line-height: 1.15; font-weight: 800;
  color: var(--ink-soft); margin: 2px 0 6px; }
.muted { color: var(--muted); }
.small { font-size: 13px; }
.neg-text { color: var(--danger); }
.pill { display: inline-flex; align-items: center; min-height: 24px; padding: 0 9px;
  border-radius: 999px; background: #eef4ff; color: var(--primary-dark);
  font-size: 12px; font-weight: 800; white-space: nowrap; margin-right: 4px; }
.table-wrap { width: 100%; overflow-x: auto; }
.table { width: 100%; min-width: 640px; border-collapse: collapse; background: var(--surface);
  border: 1px solid var(--line); border-radius: 8px; font-size: 14px; }
.table caption { text-align: left; color: var(--muted); font-size: 13px; padding: 8px 10px; }
.table th, .table td { border-bottom: 1px solid var(--line); padding: 8px 10px;
  text-align: left; vertical-align: top; }
.table thead th { background: var(--surface-strong); font-size: 13px; white-space: nowrap; }
.table tbody tr:last-child td { border-bottom: 0; }
.table tr.bad td { background: #fdf6f5; }
.bar { display: inline-block; width: 90px; height: 9px; border-radius: 999px;
  background: #e6ebf1; overflow: hidden; vertical-align: middle; }
.bar-fill { display: block; height: 100%; border-radius: 999px; background: var(--teal); }
.bar-fill.neg { background: var(--danger); }
.bar-fill.pos { background: var(--teal); }
.filters { display: flex; flex-wrap: wrap; gap: 10px; margin-bottom: 12px; }
.filter { display: flex; flex-direction: column; font-size: 12px; font-weight: 700;
  color: var(--muted); gap: 4px; }
.filter select { font: inherit; font-weight: 600; color: var(--text); padding: 5px 8px;
  border: 1px solid var(--line); border-radius: 6px; background: var(--surface); }
#filter-reset { align-self: flex-end; font: inherit; font-weight: 700; padding: 6px 12px;
  border: 1px solid var(--line); border-radius: 999px; background: var(--surface);
  color: var(--primary-dark); cursor: pointer; }
#rows-table { min-width: 900px; }
.footer { margin-top: 30px; padding: 22px 0 40px; border-top: 1px solid var(--line);
  color: var(--muted); font-size: 14px; background: var(--surface); }
ul { margin: 0 0 10px; padding-left: 20px; }
@media (max-width: 900px) {
  .grid.cols-3, .grid.cols-2 { grid-template-columns: minmax(0, 1fr); }
  h1 { font-size: 26px; }
}
"""


def render_readme(payloads: dict) -> str:
    gen = payloads["meta"]["generation"]
    pages = "\n".join(f"| `{name}` | {label} |" for name, label in PAGES)
    return f"""# `site/` — ARGUS 결과 공개 사이트 (로컬 정적 빌드)

    PYTHONIOENCODING=utf-8 python scripts/build_site.py

🔴 **생성물이다.** 손으로 고치지 말고 `scripts/build_site.py` 를 고친다.
🔴 **배포하지 않는다.** 특히 평가 서버(`deploy/README.md` 의 공인 IP)에 올리지 않는다 —
09-07~09-20 에 2 vCPU 를 웹서버와 나누면 단건 지연이 평가 기준일 수 있다.

## 페이지

| 파일 | 무엇 |
|---|---|
{pages}

## 싣지 않는 것 다섯 (하드 게이트 · `gate_all()`)

🔴 **이 목록의 금지어는 이 파일에도 못 쓴다** — 게이트가 `site/` 전량을 훑기 때문이다.
그래서 아래는 이름을 피해 적었다. 정확한 규칙은 `scripts/build_site.py` 머리말을 보라.

1. **비밀정보** — 빌더는 `argus` 패키지를 import 하지 않고 `src/`·환경파일 경로를 거부한다
   (그 패키지의 설정 모듈이 환경파일을 읽는다). 산출물에서 키 형태도 훑는다.
2. **코퍼스 원문** — `data/` 경로를 거부하고, 산출물에 원문 파일 이름이 있으면 죽는다.
3. **비공개 평가 트랙** — 경로도 문자열도 막는다. **집계 수치조차 싣지 않는다.**
   정본 문서가 그 이름을 쓰는 자리(지출 원장)는 `[비공개 평가 트랙]` 으로 지우고 금액만 남긴다.
4. **평가셋 질문 전문·정답** — 게이트가 원본을 읽어 전문과 앞 20자를 산출물과 대조한다.
   실리는 것은 `question_id` + 축 메타 + 채점 결과뿐이다.
5. **행 단위 민감 필드** — `ROW_FIELDS_ALLOWED` 화이트리스트로만 뽑는다(정답·추론 흔적·
   골든 근거·기업명·섹터 차단). 모르는 필드가 붙으면 빌드가 죽는다.
   공인 IP 도 `deploy/README.md` 에서 뽑아 대조한다 — 테스트에 적지 않는다.

## 데이터 흐름

    docs/total_plan.md §1  ──parse_ssot()──┐   (검산 넷: 원장·게이트·테스트 꼬리·크레딧 열)
    docs/pipeline/09 §I-5f ⑧ ─판정 라벨──┤
    artifacts/ablation/{{gen}}/** ──센다──┼─→ site/data/site_*.json ─→ site/*.html
    artifacts/eda · eval ──────────────┘        ↑ gate_all() 이 쓰기 전에 전량을 훑는다

## 세대

현재 성적 세대 `{gen.get("current")}` · 사다리 세대 `{gen.get("ladder")}` ·
밸류체인 세대 `{gen.get("valuechain")}` (빌드 시점 해석값).
🔴 사다리는 `A0` 이 있는 세대에만 있다. **세대가 다른 값을 서로 빼지 않는다.**

## 09-21 이후

`index.html` 의 `#live-demo-slot` 이 라이브 질의응답 데모 자리다. 지금은 비어 있다.
붙일 때 소비 상한과 API 키 분리를 함께 붙인다.
"""


def render_all(payloads: dict) -> dict[str, str]:
    files = {"style.css": CSS, "filter.js": FILTER_JS, "README.md": render_readme(payloads)}
    for name, renderer in RENDERERS.items():
        key = name.removesuffix(".html")
        files[name] = renderer(payloads["meta" if key == "index" else key])
    for key, payload in payloads.items():
        files[f"data/site_{key}.json"] = json.dumps(payload, ensure_ascii=False, indent=1,
                                                    sort_keys=False) + "\n"
    return files


# ─────────────────────────────────────────────────────────────────────────────
# 9. main
# ─────────────────────────────────────────────────────────────────────────────


def build_payloads() -> dict:
    # 🔴 한 자리에서 훑는다 — 새 빌더가 붙어도 이 관문을 지난다.
    return {name: sanitize_aggregate(builder(), path=name)
            for name, builder in BUILDERS.items()}


def build_site(out: Path = SITE, *, write: bool = True) -> dict[str, str]:
    payloads = build_payloads()
    files = render_all(payloads)
    gate_all(files)  # 🔴 쓰기 전에 훑는다. 걸리면 아무 파일도 쓰이지 않는다.
    if write:
        for name, text in sorted(files.items()):
            target = out / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8", newline="\n")
    return files


#: 🔴 게이트 발화를 **확인하는** 페이로드. 발화를 확인하지 않은 게이트는 게이트가 아니다.
def gate_probes() -> list[tuple[str, dict[str, str]]]:
    ip = (public_ips() or ["203.0.113.1"])[0]
    question = next((p for label, p in _evalset_probes() if "전문" in label), "질문 전문")
    return [
        ("① API 키", {"x.json": '{"key": "nv-0123456789abcdefghij"}'}),
        ("② 코퍼스 원문", {"x.json": '{"path": "data/dart-disclosures/manifest.jsonl"}'}),
        # 🔴 프로브에 실제 성적·문서번호를 적지 않는다 — 프로브가 사본이 되면 안 된다.
        ("③ heldout", {"x.json": '{"heldout": "집계"}'}),
        ("④ 평가셋 문항 전문", {"x.html": f"<p>{question}</p>"}),
        ("⑤ 차단 필드", {"x.json": '{"golden": ["00000000000000"]}'}),
        ("+ 공인 IP", {"x.html": f"<p>{ip}</p>"}),
    ]


def prove_gates() -> int:
    print("🔴 게이트 발화 확인 — 일부러 위반 페이로드를 넣는다")
    failed = 0
    for label, payload in gate_probes():
        try:
            gate_all(payload)
        except SiteLeak as exc:
            first = str(exc).splitlines()[1].strip()
            print(f"  PASS  {label} — 발화했다: {first}")
        else:
            failed += 1
            print(f"  FAIL  {label} — 🔴 발화하지 않았다. 게이트가 아니다.")
    print(f"결과: {len(gate_probes()) - failed}/{len(gate_probes())} 게이트가 발화했다")
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ARGUS 정적 사이트 빌드 (LLM 0회 · 0원)")
    parser.add_argument("--out", default=str(SITE))
    parser.add_argument("--prove-gates", action="store_true",
                        help="게이트가 실제로 발화하는지 확인만 하고 끝낸다")
    parser.add_argument("--dry-run", action="store_true", help="파일을 쓰지 않는다")
    args = parser.parse_args(argv)
    if args.prove_gates:
        return prove_gates()
    out = Path(args.out)
    files = build_site(out, write=not args.dry_run)
    total = sum(len(t.encode("utf-8")) for t in files.values())
    print(f"site: {len(files)} 파일 · {total / 1024:.0f} KB → {out}")
    for name in sorted(files):
        print(f"  {len(files[name].encode('utf-8')) / 1024:8.1f} KB  {name}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
