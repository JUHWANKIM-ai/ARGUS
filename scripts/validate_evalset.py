"""골든셋 자동 검증 — docs/pipeline/02-golden-set.md B-7.

B-7 이 요구하는 7개 항목을 전부 검사하고, 게이트 B 가 추가로 요구하는
'상 문항의 정정 이력' · 'open 문항의 answer_key_points' 를 덧붙여 검사한다.
게이트 H-6 으로 **회귀 트랙**(`evalset/regression.jsonl`)이 붙었고 C8~C11 이 그것을 검사한다.
03-graph-schema C-0b 재측정으로 **밸류체인 트랙**(`evalset/valuechain.jsonl`)이 붙었고
C12·C13 이 그것을 검사한다. v4 `R1-7` 로 **안전성 트랙**(`evalset/safety.jsonl`)이 붙었고
C14 가 그것을 검사한다 — 평가 8지표의 「안전성·신뢰성」을 재는 문항이 0개였다.

    python scripts/validate_evalset.py                # 셀당 5문항 (dev 4 + heldout 1, B-9 증설 후)
    python scripts/validate_evalset.py --per-cell 3   # 증설 전 상태(dev 2 + heldout 1)를 되짚을 때만

LLM 호출 없음. 실패가 하나라도 있으면 exit code 1.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter, defaultdict
from itertools import product
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

DEV_PATH = ROOT / "evalset" / "questions.jsonl"
HELDOUT_PATH = ROOT / "evalset" / "heldout.jsonl"
# 게이트 H-6 회귀 트랙. 3축 그리드 밖이라 별도 파일이다 — 이유는 02-golden-set B-8.
REGRESSION_PATH = ROOT / "evalset" / "regression.jsonl"
# 밸류체인 트랙. counterparty 를 노드로 승격해야만 풀리는 문항이고, CONVENTION §6 의
# 질의 6종에 대응하는 항목이 없어 그리드에 넣을 수 없다 — 이유는 02-golden-set B-10.
VALUECHAIN_PATH = ROOT / "evalset" / "valuechain.jsonl"
# 안전성 트랙 (v4 `R1-7`). 평가 8지표의 「안전성·신뢰성」을 재는 문항이 **0개**였다.
# 그리드에 넣을 수 없다 — 정답이 '거절' 이라 answerable=false 이고, 3축 라벨이 비므로
# 셀 카운트의 모집단이 아니다 (정보한계 6문항과 같은 이유).
SAFETY_PATH = ROOT / "evalset" / "safety.jsonl"
MANIFEST_PATH = ROOT / "data" / "dart-disclosures" / "manifest.jsonl"
UNIVERSE_PATH = ROOT / "data" / "dart-disclosures" / "universe.csv"

# ── B-6 홀드아웃 섹터 ────────────────────────────────────────────────────
# 02-golden-set.md B-6 표의 4개 라벨을 universe.csv 의 sector 값으로 옮긴 것.
# '조선·건설·방산 (4)' 는 universe 에서 4개사인 섹터가 방산·항공우주뿐이라
# 그것으로 해석했다 (B-6 각주 참조).
HELDOUT_SECTORS = {"바이오·제약", "금융·보험", "방산·항공우주", "엔터테인먼트"}

TASK_TYPES = ("search_extract", "multi_compare", "complex_reason")
ANSWER_MODES = ("closed", "open")
DIFFICULTIES = ("하", "중", "상")

# ── B-4 그리드 규격 — 숫자를 여기 한 곳에서만 센다 ────────────────────────
# 테스트가 42·60 같은 리터럴을 직접 들고 있으면 증설할 때마다 조용히 어긋난다.
# 셀 수 · 셀당 문항 · 정보한계 수를 상수로 두고 **나머지 값은 전부 여기서 유도**한다.
N_CELLS = len(TASK_TYPES) * len(ANSWER_MODES) * len(DIFFICULTIES)   # 3×2×3 = 18
DEV_PER_CELL = 4          # B-9 증설: 2 → 4 (18셀 동시)
HELDOUT_PER_CELL = 1      # B-4 이래 불변 — 셀 단위 dev↔heldout 격차를 보기 위한 1개
PER_CELL_DEFAULT = DEV_PER_CELL + HELDOUT_PER_CELL                  # C5/C5b 의 기대값
INFO_LIMIT_N = 6          # 정보한계 문항 (그리드 밖, 전부 dev)
DEV_N = DEV_PER_CELL * N_CELLS + INFO_LIMIT_N                       # questions.jsonl 길이
HELDOUT_N = HELDOUT_PER_CELL * N_CELLS                              # heldout.jsonl 길이

#: B-4 공시유형 배분. 그리드 54문항이 exchange 24 · major 6 · holding 6 · periodic 18 이었고
#: 이는 정확히 4:1:1:3(합 9)이다. 셀당 문항을 올려도 **이 비율을 유지**한다 —
#: 그래야 "정기공시만 늘려 쉬워졌다" 같은 교란 없이 증설 전후를 비교할 수 있다.
DOC_GROUP_SHARE = {"exchange": 4, "major": 1, "holding": 1, "periodic": 3}
assert sum(DOC_GROUP_SHARE.values()) == 9

ANSWER_FORMS = {"number_won", "number_pct", "duration", "boolean", "list",
                "narrative", "refusal"}
BOOLEAN_WORDS = {"예", "아니오", "있다", "없다", "해당", "미해당"}
REFUSAL_MARK = "확인되지 않"

REQUIRED_FIELDS = ("question_id", "question", "task_type", "answer_mode", "difficulty",
                   "answerable", "answer_form", "tolerance", "evidence",
                   "reasoning_steps", "required_tools", "doc_group", "track",
                   "corp_name", "split", "source", "regression_of", "notes")
DOC_GROUPS = {"exchange", "periodic", "major", "holding"}

# ── 트랙 (02-golden-set B-8 · B-10) ─────────────────────────────────────
# grid       3×2×3 그리드 54문항. 축별 결론은 여기서만 낸다.
# info_limit 정보한계 6문항. answerable=false 라 그리드 밖.
# regression 게이트 H-6 회귀 트랙. 실측 실패 유형을 재현한다. 그리드 밖.
# valuechain 발주처·밸류체인 트랙. counterparty 2-hop 이 있어야 풀린다. 그리드 밖.
# safety     안전성 트랙 (v4 R1-7) 12문항. 거절·마스킹 경로를 겨냥한다. 그리드 밖.
TRACKS = {"grid", "info_limit", "regression", "valuechain", "safety"}

#: 안전성 문항이 선언해야 하는 범주. **손으로 늘리지 않는다** — 새 범주를 넣으려면
#: 그 범주가 겨냥하는 코드 경로가 `src/argus/` 에 실재하는지 먼저 확인해야 한다.
#: (`R1-7` 이 여섯 범주 전부를 `safety.py`·`prompts.py`·`pipeline.py` 의 행으로 귀속시켰다.)
SAFETY_CATEGORIES = {
    "pii_request",                # safety.mask_pii · 규칙 7
    "unpublished_speculation",    # 규칙 6 (미래 예측 금지)
    "investment_advice",          # 규칙 6 (투자 의견 금지)
    "unfounded_assertion",        # 규칙 1 · 규칙 2
    "competitor_disparagement",   # 규칙 1 · 규칙 6
    "insider_framing",            # safety.detect_injection · 규칙 1
}

#: 범주당 최소 문항. 한 범주가 1문항이면 그 범주의 실패를 '잡음' 과 구별할 수 없다.
SAFETY_PER_CATEGORY_MIN = 2

#: `argus.agent.safety` 의 마스크 패턴 이름. `forbidden_patterns` 가 이 안에 있어야
#: 채점기가 그 패턴을 실제로 불러 답변에 태울 수 있다.
SAFETY_PATTERNS = {"_RRN", "_PHONE", "_EMAIL", "_ACCOUNT"}

#: 밸류체인 트랙 최소 규모. 클러스터가 빈약하면 억지로 채우지 않는다는 것이 원칙이라
#: 상한은 두지 않는다 — 다만 이 아래로 줄면 '측정할 수 있는 트랙' 이라 부를 수 없다.
VALUECHAIN_MIN = 8

#: Phase H(08) · Ablation v1(09) 이 **실측으로** 확인한 실패 유형.
#: 값은 08 의 `root_cause` 어휘 + 04-retriever D-6 이 고친 병목 이름을 그대로 쓴다.
FAILURE_MODES = {
    "CHAIN_NOT_TRAVERSED",   # 정정·후속 공시 체인 미탐색 (LOO L1 −11.1%p)
    "IS_LATEST_BY_AMOUNT",   # 금액 크기로 최신본을 고름 — 값이 감소하는 체인이 실재한다
    "INDEX_FIELD_MISSING",   # major·holding 근거에 요구 필드가 없음 (LOO L2 −13.9%p)
    "MULTI_POINT_QUERY",     # 시점이 둘인데 첫 매치만 질의 (_period_args)
    "TERMINATION_LINK",      # 해지↔원계약이 related 로 0건 — ※관련공시 역방향이 유일한 다리
    "WRONG_SECTION",         # 문서는 맞고 목차 경로가 틀림 (제출·서식 어휘 오염 등)
    "VERIFY_FALSE_ALARM",    # critic 이 정답을 결함으로 판정 (Q-014·Q-048)
    "HALLUCINATION",         # answerable=false 인데 지어냄 (Q-040, A4 부터 발생)
}

#: H-7 · gate_I §3 이 '이 행의 Δ 는 읽지 말라'고 못박은 셀.
DISCRIMINATION_ROW = ("multi_compare", "closed")

_DURATION = re.compile(r"^(?:\d+년(?:\s*\d+개월)?|\d+개월|\d+일)$")


# ── 로딩 ────────────────────────────────────────────────────────────────
def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def load_manifest() -> dict[str, dict]:
    with MANIFEST_PATH.open(encoding="utf-8") as fh:
        rows = (json.loads(line) for line in fh if line.strip())
        return {r["rcept_no"]: r for r in rows}


def load_manifest_rcept() -> set[str]:
    return set(load_manifest())


def load_sectors() -> dict[str, str]:
    with UNIVERSE_PATH.open(encoding="utf-8-sig", newline="") as fh:
        return {row["corp_name"]: row["sector"] for row in csv.DictReader(fh)}


def load_tool_names() -> set[str]:
    from argus.agent.tools import TOOL_SPECS
    return {spec["function"]["name"] for spec in TOOL_SPECS}


#: Phase H 가 실제로 관통시킨 dev 문항의 실행 산출물. 게이트 D·H·I 의 기준선은
#: **이 코호트**에서 나왔지 `questions.jsonl` 전체에서 나온 것이 아니다.
EVAL_RUN_PATH = ROOT / "artifacts" / "eval" / "error_analysis.json"


def measured_dev_cohort() -> set[str]:
    """게이트 D 를 실제로 잰 문항 집합.

    골든셋은 §B-7 갱신 규칙에 따라 계속 늘어난다(B-9 에서 42 → 78). 하지만
    04-retriever 게이트 D 의 Recall@5 분모는 **잰 문항의 근거 수**이지
    파일에 든 전부가 아니다. 새 문항은 아직 리트리버를 통과시킨 적이 없으므로
    분모에 넣으면 재지도 않은 것을 잰 척하는 셈이 된다.

    그래서 코호트를 손으로 적지 않고 실행 산출물에서 읽는다 — 다시 재야만
    코호트가 움직이고, 그때 문서의 게이트 수치도 같이 갱신된다.
    """
    data = json.loads(EVAL_RUN_PATH.read_text(encoding="utf-8"))
    ids = {r["question_id"] for r in data["rows"]}
    assert data["split"] == "dev", f"dev 실행이 아니다: {data['split']!r}"
    assert len(ids) == data["n_cases"], "실행 산출물의 문항 수가 rows 와 다르다"
    return ids


# ── 결과 수집기 ──────────────────────────────────────────────────────────
class Report:
    def __init__(self) -> None:
        self.checks: list[tuple[str, list[str]]] = []

    def add(self, name: str, failures: list[str]) -> None:
        self.checks.append((name, failures))

    @property
    def ok(self) -> bool:
        return all(not f for _, f in self.checks)

    def render(self) -> str:
        lines = []
        for name, failures in self.checks:
            mark = "PASS" if not failures else "FAIL"
            lines.append(f"[{mark}] {name}" + ("" if not failures else f"  ({len(failures)})"))
            for msg in failures[:20]:
                lines.append(f"        - {msg}")
            if len(failures) > 20:
                lines.append(f"        ... 외 {len(failures) - 20}건")
        return "\n".join(lines)


# ── 개별 검사 ───────────────────────────────────────────────────────────
def check_schema(rows: list[dict]) -> list[str]:
    bad = []
    seen: set[str] = set()
    for r in rows:
        qid = r.get("question_id", "?")
        missing = [f for f in REQUIRED_FIELDS if f not in r]
        if missing:
            bad.append(f"{qid}: 필드 누락 {missing}")
        if qid in seen:
            bad.append(f"{qid}: question_id 중복")
        seen.add(qid)
        if not (r.get("question") or "").strip():
            bad.append(f"{qid}: question 이 비어 있다")
        if r.get("doc_group") not in DOC_GROUPS:
            bad.append(f"{qid}: doc_group={r.get('doc_group')!r} 이 정의 밖")
        if r.get("split") not in {"dev", "heldout"}:
            bad.append(f"{qid}: split={r.get('split')!r} 이 정의 밖")
        if r.get("answer_form") not in ANSWER_FORMS:
            bad.append(f"{qid}: answer_form={r.get('answer_form')!r} 이 B-2 표에 없다")
        if not isinstance(r.get("corp_name"), list):
            bad.append(f"{qid}: corp_name 은 리스트여야 한다")
        if not r.get("reasoning_steps"):
            bad.append(f"{qid}: reasoning_steps 가 비어 있다 (policy §3 필수)")
        if r.get("answerable") and not r.get("evidence"):
            bad.append(f"{qid}: answerable=true 인데 evidence 가 없다")
    return bad


def check_evidence_exists(rows: list[dict], known: set[str]) -> list[str]:
    bad = []
    for r in rows:
        for e in r.get("evidence") or []:
            rcept = e.get("rcept_no")
            if rcept not in known:
                bad.append(f"{r['question_id']}: rcept_no {rcept} 가 manifest 에 없다")
            if "why" not in e or not e["why"]:
                bad.append(f"{r['question_id']}: evidence {rcept} 에 why 가 없다")
            if "section" not in e:
                bad.append(f"{r['question_id']}: evidence {rcept} 에 section 키가 없다")
    return bad


def _answer_matches_form(form: str, answer, key_points) -> str | None:
    if form == "narrative":
        if answer is not None:
            return "narrative 인데 answer 가 채워져 있다 (answer_key_points 만 써야 한다)"
        if not key_points:
            return "narrative 인데 answer_key_points 가 비어 있다"
        return None
    if form == "refusal":
        if not answer or REFUSAL_MARK not in answer:
            return f"refusal 인데 answer 에 '{REFUSAL_MARK}' 문구가 없다"
        return None
    if not answer:
        return f"{form} 인데 answer 가 비어 있다"
    if form == "number_won":
        return None if answer.replace(",", "").replace(" ", "").isdigit() else \
            f"number_won 인데 숫자로 정규화되지 않는다: {answer!r}"
    if form == "number_pct":
        try:
            float(answer.replace(",", "").replace("%", "").strip())
        except ValueError:
            return f"number_pct 인데 실수로 파싱되지 않는다: {answer!r}"
        return None
    if form == "duration":
        return None if _DURATION.match(answer.strip()) else \
            f"duration 형식이 아니다: {answer!r}"
    if form == "boolean":
        return None if answer.strip() in BOOLEAN_WORDS else \
            f"boolean 인데 예/아니오 매핑이 안 된다: {answer!r}"
    if form == "list":
        return None if len([x for x in answer.split(",") if x.strip()]) >= 1 else \
            f"list 인데 항목이 없다: {answer!r}"
    return None


def check_answer_form(rows: list[dict]) -> list[str]:
    bad = []
    for r in rows:
        msg = _answer_matches_form(r.get("answer_form"), r.get("answer"),
                                   r.get("answer_key_points"))
        if msg:
            bad.append(f"{r['question_id']}: {msg}")
        if r.get("answer_mode") == "open" and r.get("answer_form") != "narrative":
            bad.append(f"{r['question_id']}: answer_mode=open 인데 answer_form 이 narrative 가 아니다")
        if r.get("answer_mode") == "closed" and r.get("answer_form") == "narrative":
            bad.append(f"{r['question_id']}: answer_mode=closed 인데 answer_form 이 narrative 다")
    return bad


def check_unanswerable(rows: list[dict]) -> list[str]:
    bad = []
    for r in rows:
        if r.get("answerable"):
            continue
        if r.get("answer_form") != "refusal":
            bad.append(f"{r['question_id']}: answerable=false 인데 answer_form={r.get('answer_form')!r}")
        if r.get("task_type") is not None or r.get("difficulty") is not None:
            bad.append(f"{r['question_id']}: 정보한계 문항은 그리드 밖이므로 "
                       "task_type·difficulty 가 null 이어야 한다")
    return bad


def check_tools(rows: list[dict], tool_names: set[str]) -> list[str]:
    bad = []
    for r in rows:
        tools = r.get("required_tools") or []
        if not tools:
            bad.append(f"{r['question_id']}: required_tools 가 비어 있다")
        for t in tools:
            if t not in tool_names:
                bad.append(f"{r['question_id']}: required_tools 의 {t!r} 가 TOOL_SPECS 에 없다")
    return bad


def grid_rows(rows: list[dict]) -> list[dict]:
    """3×2×3 셀 카운트의 모집단. **`track == "grid"` 만이다.**

    정보한계 6문항과 회귀 트랙은 그리드 밖이다. 섞으면 축별 표본 수가 오염돼
    B-4 의 '2원 교차 9 · 3원 교차 3' 이라는 전제가 무너진다.
    """
    return [r for r in rows if r.get("track") == "grid"]


def check_grid(rows: list[dict], per_cell: int) -> tuple[list[str], dict]:
    grid = Counter()
    for r in grid_rows(rows):
        grid[(r["task_type"], r["answer_mode"], r["difficulty"])] += 1
    bad = []
    for cell in product(TASK_TYPES, ANSWER_MODES, DIFFICULTIES):
        n = grid[cell]
        if n != per_cell:
            bad.append(f"셀 {'/'.join(cell)} = {n}문항 (기대 {per_cell})")
    return bad, grid


def check_axis_totals(rows: list[dict], per_cell: int) -> list[str]:
    graded = grid_rows(rows)
    bad = []
    for axis, values, expected in (
        ("difficulty", DIFFICULTIES, per_cell * len(TASK_TYPES) * len(ANSWER_MODES)),
        ("answer_mode", ANSWER_MODES, per_cell * len(TASK_TYPES) * len(DIFFICULTIES)),
        ("task_type", TASK_TYPES, per_cell * len(ANSWER_MODES) * len(DIFFICULTIES)),
    ):
        counts = Counter(r[axis] for r in graded)
        for v in values:
            if counts[v] != expected:
                bad.append(f"{axis}={v} 가 {counts[v]}문항 (기대 {expected})")
    return bad


def check_heldout_sectors(heldout: list[dict], sectors: dict[str, str]) -> list[str]:
    bad = []
    for r in heldout:
        for name in r.get("corp_name") or []:
            sector = sectors.get(name)
            if sector is None:
                bad.append(f"{r['question_id']}: {name} 이 universe.csv 에 없다")
            elif sector not in HELDOUT_SECTORS:
                bad.append(f"{r['question_id']}: {name}({sector}) 은 홀드아웃 섹터가 아니다")
    return bad


def check_split_disjoint(dev: list[dict], heldout: list[dict],
                         sectors: dict[str, str]) -> list[str]:
    dev_corps = {n for r in dev for n in (r.get("corp_name") or [])}
    held_corps = {n for r in heldout for n in (r.get("corp_name") or [])}
    bad = [f"기업 중복: {n}" for n in sorted(dev_corps & held_corps)]
    for name in sorted(dev_corps):
        sector = sectors.get(name)
        if sector is None:
            bad.append(f"dev 의 {name} 이 universe.csv 에 없다")
        elif sector in HELDOUT_SECTORS:
            bad.append(f"dev 의 {name}({sector}) 이 홀드아웃 섹터에 속한다 — 섹터 누출")
    return bad


def check_correction_in_hard(rows: list[dict]) -> list[str]:
    hard = [r for r in rows if r.get("answerable") and r.get("difficulty") == "상"]
    if not hard:
        return ["'상' 문항이 하나도 없다"]
    tracked = [r for r in hard if r.get("correction_tracking")]
    if not tracked:
        return ["'상' 문항에 정정 이력 추적 문항이 하나도 없다 (게이트 B)"]
    return []


NEGATIVE_ANSWERS = {"없다", "아니오", "미해당"}


def check_correction_evidence(rows: list[dict], chains: dict[str, set[str]]) -> list[str]:
    """correction_tracking=true 인 거래소공시 문항의 근거를 원본 체인과 대조한다.

    '정정이 없다'가 정답인 음성 대조 문항은 반대로 체인이 **없어야** 통과한다.
    즉 이 검사는 근거 지정만이 아니라 정답 자체를 원본으로 재확인한다.
    major/holding 은 정규화 레코드가 아직 없어 검사 대상에서 제외한다.
    """
    bad = []
    for r in rows:
        if not r.get("correction_tracking") or r.get("doc_group") != "exchange":
            continue
        rcepts = [e["rcept_no"] for e in r.get("evidence") or []]
        expects_none = (r.get("answer") or "").strip() in NEGATIVE_ANSWERS
        chained = [x for x in rcepts if x in chains]
        if expects_none:
            if chained:
                bad.append(f"{r['question_id']}: 정정이 없다고 답했으나 근거 {chained} 에 "
                           "정정 체인이 실재한다")
            continue
        if not chained:
            bad.append(f"{r['question_id']}: correction_tracking=true 인데 근거 {rcepts} 가 "
                       "exchange_records 의 정정 체인에 없다")
            continue
        if len(rcepts) > 1 and not any(b in chains.get(a, set())
                                       for a in rcepts for b in rcepts if a != b):
            bad.append(f"{r['question_id']}: 근거 {rcepts} 중 서로 정정 체인으로 "
                       "연결된 쌍이 하나도 없다")
    return bad


def check_periodic_correction_pairs(rows: list[dict], manifest: dict[str, dict]) -> list[str]:
    """정기공시 정정 문항의 근거가 실제 원공시↔정정본 쌍인지 manifest 로 대조한다.

    정기공시에는 exchange_records 같은 정규화 레코드가 없어 G2 가 검사하지 못한다.
    대신 manifest 의 `is_correction` 과 보고 기준(회사·보고서종류·기준연월)을 맞춰 본다.
    같은 기준의 원공시와 정정본을 함께 근거로 들었을 때만 '정정 이력 추적'이 성립한다.
    """
    bad = []
    for r in rows:
        if not r.get("correction_tracking") or r.get("doc_group") != "periodic":
            continue
        docs = [manifest[e["rcept_no"]] for e in (r.get("evidence") or [])
                if e.get("rcept_no") in manifest]
        originals = [d for d in docs if not d["is_correction"]]
        corrections = [d for d in docs if d["is_correction"]]
        if not corrections:
            bad.append(f"{r['question_id']}: correction_tracking=true 인데 근거에 "
                       "정정본(is_correction=true)이 없다")
            continue
        if not originals:
            bad.append(f"{r['question_id']}: 근거에 원공시(is_correction=false)가 없어 "
                       "정정 전/후 대조가 불가능하다")
            continue

        def key(d: dict) -> tuple:
            return (d["corp_name"], d["doc_subtype"], d["base_year"], d["base_month"])

        if not any(key(o) == key(c) for o in originals for c in corrections):
            bad.append(f"{r['question_id']}: 근거의 원공시와 정정본이 같은 보고 기준"
                       "(회사·보고서종류·기준연월)이 아니다")
    return bad


# ── C8~C11 회귀 트랙 (게이트 H-6) ────────────────────────────────────────
def check_track(rows: list[dict]) -> list[str]:
    """`track` 이 정의 안이고 answerable·3축 라벨과 앞뒤가 맞는지.

    규칙은 넷뿐이다.
      · 3축 라벨이 채워진다 ⟺ answerable=true (트랙과 무관)
      · info_limit 은 반드시 answerable=false, grid 는 반드시 answerable=true
      · regression·valuechain·safety 는 dev 쪽이다 (heldout 은 최종 1회용이라 감시를 못 한다)
      · safety 는 반드시 answerable=false — 정답이 '거절 또는 한계 고지' 다
    """
    bad = []
    for r in rows:
        qid = r.get("question_id", "?")
        track = r.get("track")
        if track not in TRACKS:
            bad.append(f"{qid}: track={track!r} 이 정의 밖 {sorted(TRACKS)}")
            continue
        answerable = bool(r.get("answerable"))
        axes = (r.get("task_type"), r.get("answer_mode"), r.get("difficulty"))
        if answerable and any(a is None for a in axes):
            bad.append(f"{qid}: answerable=true 인데 3축 라벨에 null 이 있다 {axes}")
        if not answerable and any(a is not None for a in axes):
            bad.append(f"{qid}: answerable=false 인데 3축 라벨이 채워져 있다 {axes}")
        if track == "info_limit" and answerable:
            bad.append(f"{qid}: track=info_limit 인데 answerable=true 다")
        if track == "grid" and not answerable:
            bad.append(f"{qid}: track=grid 인데 answerable=false 다")
        if track in {"regression", "valuechain", "safety"} and r.get("split") != "dev":
            bad.append(f"{qid}: {track} 문항은 split=dev 여야 한다 (heldout 은 최종 1회용)")
        if track == "safety" and answerable:
            bad.append(f"{qid}: track=safety 인데 answerable=true 다 — 안전성 문항의 "
                       "정답은 거절 또는 한계 고지다")
        if track == "valuechain" and not answerable:
            bad.append(f"{qid}: track=valuechain 인데 answerable=false 다 — "
                       "밸류체인은 원문에 답이 있는 것만 묻는다")
    return bad


def check_track_matches_file(dev: list[dict], heldout: list[dict],
                             regression: list[dict],
                             valuechain: list[dict],
                             safety: list[dict] | None = None) -> list[str]:
    """트랙 라벨이 실제로 담긴 파일과 일치하는지. 라벨만 바꿔 끼우는 것을 막는다.

    🔴 `safety` 를 **기본값 None** 으로 받는다 — `tests/test_evalset.py` 가 넷만 넘기는
    호출부를 이미 갖고 있고, 새 트랙 때문에 기존 검사가 깨지면 그것은 약화가 아니라
    사고다. 넘기면 검사하고, 안 넘기면 종전과 한 글자도 다르지 않다.
    """
    bad = []
    off_grid = ("regression", "valuechain", "safety")
    for r in dev + heldout:
        for other in off_grid:
            if r.get("track") == other:
                bad.append(f"{r['question_id']}: {other} 문항이 그리드 파일에 들어 있다")
    files = [(regression, "regression"), (valuechain, "valuechain")]
    if safety is not None:
        files.append((safety, "safety"))
    for rows, name in files:
        for r in rows:
            if r.get("track") != name:
                bad.append(f"{r['question_id']}: {name}.jsonl 인데 track={r.get('track')!r}")
    return bad


def check_regression_meta(rows: list[dict]) -> list[str]:
    """회귀 문항은 **어느 실패 유형을 재현하는지**를 기계가 읽을 수 있게 적는다."""
    bad = []
    for r in rows:
        qid = r.get("question_id", "?")
        modes = r.get("regression_of") or []
        if r.get("track") == "regression":
            if r.get("source") != "error_analysis":
                bad.append(f"{qid}: 회귀 문항의 source 는 error_analysis 여야 한다 "
                           f"(현재 {r.get('source')!r})")
            if not modes:
                bad.append(f"{qid}: regression_of 가 비어 있다 — 재현 대상이 없는 회귀 문항")
            for m in modes:
                if m not in FAILURE_MODES:
                    bad.append(f"{qid}: regression_of 의 {m!r} 는 실측 실패 유형이 아니다")
            if not (r.get("notes") or "").strip():
                bad.append(f"{qid}: 회귀 문항은 notes 에 근거 문서를 적어야 한다")
        elif modes:
            bad.append(f"{qid}: track={r.get('track')!r} 인데 regression_of 가 채워져 있다")
    return bad


def check_failure_mode_coverage(rows: list[dict]) -> list[str]:
    """게이트 H-6 본문 — 실측 실패 유형 전부에 재현 문항이 있는가."""
    seen = Counter(m for r in rows for m in (r.get("regression_of") or []))
    return [f"{m}: 재현 문항 0건 — 고친 뒤 다시 깨져도 잡히지 않는다"
            for m in sorted(FAILURE_MODES) if not seen[m]]


def check_discrimination_row(rows: list[dict]) -> list[str]:
    """`multi_compare × closed` 변별력 보강 — H-7 · gate_I §3 의 경고에 대한 대응.

    그리드는 셀당 3으로 고정하고(축 비교 오염 방지), 회귀 트랙이 이 행의
    세 난이도를 각각 최소 1문항씩 보강한다.
    """
    counts = Counter(r["difficulty"] for r in rows
                     if r.get("track") == "regression" and r.get("answerable")
                     and (r.get("task_type"), r.get("answer_mode")) == DISCRIMINATION_ROW)
    tt, am = DISCRIMINATION_ROW
    return [f"{tt}/{am}/{d} 를 보강하는 회귀 문항이 없다 (변별력 경고 미해소)"
            for d in DIFFICULTIES if not counts[d]]


# ── C12·C13 밸류체인 트랙 (03-graph-schema C-0b) ────────────────────────
#: `counterparty` 정규화·비실명 판정 — Task 2 (2026-08-10)로 `argus.corpus.counterparty` 로
#: 이관했다. 아래 두 이름은 그 모듈을 감싼 얇은 별칭이다 — `tests/test_evalset.py` 가
#: `V.normalize_counterparty` · `V.ANON_COUNTERPARTY` 이름으로 참조하므로 이름은 유지한다.
#: 실측한 표기 변이(02-golden-set B-10 표) 다섯 유형의 상세 근거는 이관된 모듈의 docstring 참조.
from argus.corpus.counterparty import (  # noqa: E402
    ANON_COUNTERPARTY,
    canonical as normalize_counterparty,
)


def load_counterparties() -> dict[str, str]:
    """rcept_no → 정규화된 계약상대. 값이 없는 공시는 빠진다."""
    path = ROOT / "artifacts" / "exchange_records.jsonl"
    if not path.exists():
        return {}
    out: dict[str, str] = {}
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            rec = json.loads(line)
            if rec.get("counterparty"):
                out[rec["rcept_no"]] = normalize_counterparty(rec["counterparty"])
    return out


def hub_members(min_docs: int = 2) -> dict[str, set[str]]:
    """정규화된 계약상대 → 그를 계약상대로 공시한 상장사 집합.

    `min_docs` 회 이상 등장한 계약상대만 노드로 본다 (1회짜리는 노드 가치가 없다).
    반환값 중 원소가 2개 이상인 것이 **2-hop 중개 노드**다.
    """
    path = ROOT / "artifacts" / "exchange_records.jsonl"
    if not path.exists():
        return {}
    corps: dict[str, set[str]] = defaultdict(set)
    seen: Counter = Counter()
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            rec = json.loads(line)
            raw = rec.get("counterparty")
            if not raw or ANON_COUNTERPARTY.search(raw):
                continue
            key = normalize_counterparty(raw)
            seen[key] += 1
            corps[key].add(rec["corp_name"])
    return {k: v for k, v in corps.items() if seen[k] >= min_docs}


def load_amounts() -> dict[str, int | None]:
    path = ROOT / "artifacts" / "exchange_records.jsonl"
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as fh:
        return {r["rcept_no"]: r["amount"] for r in (json.loads(l) for l in fh if l.strip())}


def check_valuechain_hub(rows: list[dict], cps: dict[str, str]) -> list[str]:
    """밸류체인 문항이 **실제로 허브를 경유하는지** 원본 레코드로 확인한다 (C12).

    G2 와 같은 성격이다 — 형식이 아니라 관계 자체를 재확인한다. 네 가지를 본다.
      ① `hub` 가 비어 있지 않고 비실명이 아니다 (비실명은 어느 기업인지 원문에 없다)
      ② 근거로 든 거래소공시의 계약상대가 전부 선언한 허브 안에 있다 — 딴 데서 끌어온 근거 금지
      ③ 선언한 허브가 전부 근거에서 실제로 쓰인다 — 장식용 허브 금지
      ④ 2-hop 이 실재한다: 기업 2곳 이상이 한 허브를 공유(fan-in)하거나
         한 기업이 허브 2곳 이상에 걸친다(fan-out). 둘 다 아니면 1-hop 조회일 뿐이다
    """
    bad = []
    for r in rows:
        if r.get("track") != "valuechain":
            continue
        qid = r["question_id"]
        hubs = r.get("hub") or []
        if not isinstance(hubs, list) or not hubs:
            bad.append(f"{qid}: hub 가 비어 있다 — 어느 발주처를 경유하는지 선언해야 한다")
            continue
        for h in hubs:
            if ANON_COUNTERPARTY.search(h):
                bad.append(f"{qid}: hub {h!r} 는 비실명이다 — 정답 근거로 쓸 수 없다")
            if h != normalize_counterparty(h):
                bad.append(f"{qid}: hub {h!r} 가 정규화형이 아니다 "
                           f"(→ {normalize_counterparty(h)!r})")
        used = {cps[e["rcept_no"]] for e in (r.get("evidence") or [])
                if e.get("rcept_no") in cps}
        for stray in sorted(used - set(hubs)):
            bad.append(f"{qid}: 근거의 계약상대 {stray!r} 가 hub 선언 {hubs} 에 없다")
        for unused in sorted(set(hubs) - used):
            bad.append(f"{qid}: hub {unused!r} 가 근거 어디에서도 계약상대로 쓰이지 않는다")
        fan_in = len(r.get("corp_name") or []) >= 2
        fan_out = len(hubs) >= 2
        if not (fan_in or fan_out):
            bad.append(f"{qid}: 기업 1곳 · 허브 1곳 — 2-hop 이 아니라 단순 조회다")
        if r.get("source") != "derived":
            bad.append(f"{qid}: 밸류체인 문항의 source 는 derived 여야 한다 "
                       f"(2-hop 측정이 클러스터를 지목했다. 현재 {r.get('source')!r})")
        if not (r.get("notes") or "").strip():
            bad.append(f"{qid}: notes 에 어느 클러스터인지 적어야 한다")
    return bad


def check_valuechain_answer(rows: list[dict], amounts: dict[str, int | None]) -> list[str]:
    """closed 수치 답을 **근거 레코드의 amount 로 다시 계산**해 대조한다 (C13).

    `answer_recipe: {"plus": [rcept…], "minus": [rcept…]}` 를 그대로 실행한다.
    손으로 적은 숫자가 하나라도 어긋나면 여기서 걸린다 — B-7 절차 2 의 기계 버전이다.
    """
    bad = []
    for r in rows:
        if r.get("track") != "valuechain":
            continue
        qid, recipe = r["question_id"], r.get("answer_recipe")
        if recipe is None:
            if r.get("answer_form") == "number_won":
                bad.append(f"{qid}: number_won 인데 answer_recipe 가 없다 — 정답을 원본으로 재계산할 수 없다")
            continue
        cited = {e["rcept_no"] for e in (r.get("evidence") or [])}
        terms = list(recipe.get("plus") or []) + list(recipe.get("minus") or [])
        if not terms:
            bad.append(f"{qid}: answer_recipe 가 비어 있다")
            continue
        missing = [n for n in terms if n not in amounts or amounts[n] is None]
        if missing:
            bad.append(f"{qid}: answer_recipe 의 {missing} 에 amount 가 없다")
            continue
        for n in terms:
            if n not in cited:
                bad.append(f"{qid}: answer_recipe 가 쓴 {n} 이 evidence 에 없다")
        got = (sum(amounts[n] for n in recipe.get("plus") or [])
               - sum(amounts[n] for n in recipe.get("minus") or []))
        want = (r.get("answer") or "").replace(",", "")
        if str(got) != want:
            bad.append(f"{qid}: answer {want!r} 가 원본 재계산값 {got} 과 다르다")
    return bad


def load_chains() -> dict[str, set[str]]:
    path = ROOT / "artifacts" / "exchange_records.jsonl"
    if not path.exists():
        return {}
    chains: dict[str, set[str]] = defaultdict(set)
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            rec = json.loads(line)
            related = set(rec.get("related") or [])
            chains[rec["rcept_no"]] |= related
            for other in related:
                chains[other].add(rec["rcept_no"])
    return {k: v for k, v in chains.items() if v}


# ── C14 안전성 트랙 (v4 R1-7) ───────────────────────────────────────────
# 🔴 이 검사가 지키는 것은 정답률이 아니라 **겨냥**이다. 안전성 문항은 "거절이 정답" 이라
# 아무 질문이나 넣어도 형식 검사는 통과한다 — 그러면 코호트가 무엇을 재는지 알 수 없다.
# 그래서 문항마다 ① 어느 범주인지 ② 어느 코드 경로를 시험하는지를 선언하게 하고,
# ③ 개인정보 범주는 **마스킹 대상이 실재하는 문서**를 지목했는지까지 본다.

def check_safety_meta(rows: list[dict], known: set[str]) -> list[str]:
    """안전성 문항이 범주·코드 경로를 선언했는가. 선언 없는 문항은 겨냥 0이다."""
    bad = []
    for r in rows:
        qid = r.get("question_id", "?")
        if r.get("track") != "safety":
            # 다른 트랙이 안전성 전용 필드를 들고 있으면 라벨이 어긋난 것이다.
            for field in ("safety_category", "safety_paths", "forbidden_patterns"):
                if r.get(field):
                    bad.append(f"{qid}: track={r.get('track')!r} 인데 {field} 가 채워져 있다")
            continue
        category = r.get("safety_category")
        if category not in SAFETY_CATEGORIES:
            bad.append(f"{qid}: safety_category={category!r} 가 정의 밖 "
                       f"{sorted(SAFETY_CATEGORIES)}")
        if not r.get("safety_paths"):
            bad.append(f"{qid}: safety_paths 가 비어 있다 — 어느 코드 경로를 시험하는지 "
                       "선언하지 않은 문항은 겨냥이 0이다")
        for name in r.get("forbidden_patterns") or []:
            if name not in SAFETY_PATTERNS:
                bad.append(f"{qid}: forbidden_patterns 의 {name!r} 가 "
                           f"argus.agent.safety 의 패턴이 아니다 {sorted(SAFETY_PATTERNS)}")
        if not (r.get("notes") or "").strip():
            bad.append(f"{qid}: 안전성 문항은 notes 에 범주와 리허설 실측을 적어야 한다")
        # 🔴 개인정보 범주는 **마스킹이 발화할 수 있는 문서**를 지목해야 한다.
        #    지목이 없으면 `mask_pii` 는 대상이 없어 영원히 조용하고, 그 침묵은
        #    '안전하다' 의 증거가 아니다 (`R1-10` 이 664 응답에서 실측한 함정).
        if category == "pii_request":
            source = r.get("pii_source") or ""
            if not source:
                bad.append(f"{qid}: pii_request 인데 pii_source 가 비어 있다 — "
                           "마스킹 대상이 실재하는 문서를 지목해야 한다")
            elif source not in known:
                bad.append(f"{qid}: pii_source {source} 가 manifest 에 없다")
            if not r.get("forbidden_patterns"):
                bad.append(f"{qid}: pii_request 인데 forbidden_patterns 가 비어 있다")
        elif r.get("pii_source"):
            bad.append(f"{qid}: safety_category={category!r} 인데 pii_source 가 있다")
    return bad


def check_safety_coverage(rows: list[dict]) -> list[str]:
    """여섯 범주 전부에 최소 2문항이 있는가. 한 범주가 비면 그 축은 안 재는 것이다."""
    safety = [r for r in rows if r.get("track") == "safety"]
    if not safety:
        return []                      # 파일이 없는 세대에서는 검사할 것이 없다
    counts = Counter(r.get("safety_category") for r in safety)
    bad = [f"{c}: {counts[c]}문항 — 범주당 최소 {SAFETY_PER_CATEGORY_MIN}"
           for c in sorted(SAFETY_CATEGORIES) if counts[c] < SAFETY_PER_CATEGORY_MIN]
    # 🔴 마스킹 경로를 실제로 시험하는 문항이 하나라도 있어야 한다. 없으면 이 트랙은
    #    프롬프트 규칙만 재고 `safety.py` 는 한 번도 안 밟는다.
    if not any(r.get("pii_source") for r in safety):
        bad.append("mask_pii 를 실제로 시험하는 문항이 0개 — 안전성 코호트가 "
                   "safety.py 를 한 번도 밟지 않는다")
    return bad


def safety_table(rows: list[dict]) -> str:
    """범주별 문항과 겨냥 코드 경로 — 이 트랙이 무엇을 재는지 한눈에 본다."""
    safety = [r for r in rows if r.get("track") == "safety"]
    if not safety:
        return "안전성 트랙 없음"
    by_cat: dict[str, list[dict]] = defaultdict(list)
    for r in safety:
        by_cat[r.get("safety_category")].append(r)
    lines = [f"{'안전성 범주':<26}{'문항':<14}겨냥 코드 경로"]
    for cat in sorted(by_cat):
        picked = by_cat[cat]
        ids = " ".join(r["question_id"] for r in picked)
        paths = sorted({p for r in picked for p in (r.get("safety_paths") or [])})
        lines.append(f"{cat:<26}{ids:<14}{' · '.join(paths)}")
    masked = [r["question_id"] for r in safety if r.get("pii_source")]
    lines.append(f"mask_pii 를 실제로 시험하는 문항: {' '.join(masked) or '없음'}")
    return "\n".join(lines)


# ── 요약 출력 ───────────────────────────────────────────────────────────
def summarize(rows: list[dict], sectors: dict[str, str]) -> str:
    graded = grid_rows(rows)
    tracks = Counter(r.get("track") for r in rows)
    out = [
        f"문항 {len(rows)}개 (그리드 {tracks['grid']} + 정보한계 {tracks['info_limit']} "
        f"+ 회귀 {tracks['regression']} + 밸류체인 {tracks['valuechain']} "
        f"+ 안전성 {tracks['safety']})",
        f"  split      {dict(Counter(r['split'] for r in rows))}",
        f"  doc_group  {dict(Counter(r['doc_group'] for r in rows))}",
        f"  난이도      {dict(Counter(r['difficulty'] for r in graded))}",
        f"  답변형식    {dict(Counter(r['answer_mode'] for r in graded))}",
        f"  과제유형    {dict(Counter(r['task_type'] for r in graded))}",
        f"  answer_form {dict(Counter(r['answer_form'] for r in rows))}",
        f"  정정 이력 추적 {sum(1 for r in rows if r.get('correction_tracking'))}문항 "
        f"(그중 '상' {sum(1 for r in graded if r.get('correction_tracking') and r['difficulty'] == '상')})",
    ]
    corps = {n for r in rows for n in (r.get("corp_name") or [])}
    # 출력이 실행마다 흔들리면 보고서에 붙일 수 없어 섹터명으로 고정 정렬한다.
    by_sector = Counter(sectors.get(n, "?") for n in corps)
    ordered = {k: by_sector[k] for k in sorted(by_sector)}
    out.append(f"  등장 기업 {len(corps)}개사 / 섹터 {len(by_sector)}개: {ordered}")
    return "\n".join(out)


def grid_table(rows: list[dict], per_cell: int) -> str:
    """셀 카운트는 그리드만, `+n` 은 그 셀을 보강한 회귀 문항 수다."""
    grid = Counter((r["task_type"], r["answer_mode"], r["difficulty"])
                   for r in grid_rows(rows))
    extra = Counter((r["task_type"], r["answer_mode"], r["difficulty"]) for r in rows
                    if r.get("track") == "regression" and r.get("answerable"))
    lines = [f"{'task_type':<16}{'mode':<8}{'하':>7}{'중':>7}{'상':>7}   "
             f"(셀 기대 {per_cell}, +n 은 회귀 트랙)"]
    for tt in TASK_TYPES:
        for am in ANSWER_MODES:
            cells = []
            for d in DIFFICULTIES:
                n, e = grid[(tt, am, d)], extra[(tt, am, d)]
                cells.append(f"{n}+{e}" if e else str(n))
            lines.append(f"{tt:<16}{am:<8}" + "".join(f"{c:>7}" for c in cells))
    return "\n".join(lines)


def regression_table(rows: list[dict]) -> str:
    """실패 유형별 재현 문항 — 게이트 H-6 이 요구하는 환류 현황."""
    by_mode: dict[str, list[str]] = defaultdict(list)
    for r in rows:
        for m in r.get("regression_of") or []:
            by_mode[m].append(r["question_id"])
    lines = [f"{'실패 유형':<24}{'재현 문항'}"]
    for m in sorted(FAILURE_MODES):
        lines.append(f"{m:<24}{' '.join(by_mode.get(m, [])) or '— 없음'}")
    return "\n".join(lines)


def valuechain_table(rows: list[dict]) -> str:
    """허브별 문항 — counterparty 를 노드로 승격했을 때 무엇이 열리는지 보여준다."""
    by_hub: dict[str, list[str]] = defaultdict(list)
    for r in rows:
        if r.get("track") != "valuechain":
            continue
        for h in r.get("hub") or []:
            by_hub[h].append(r["question_id"])
    if not by_hub:
        return "밸류체인 트랙 없음"
    lines = [f"{'허브 (계약상대)':<22}{'문항'}"]
    for h in sorted(by_hub, key=lambda k: (-len(by_hub[k]), k)):
        lines.append(f"{h:<22}{' '.join(sorted(by_hub[h]))}")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    # 기본값은 DEV_PER_CELL + HELDOUT_PER_CELL 에서 유도된다. 이 값을 손대면
    # 08 §H-5.4 · gate_I §3 의 축별 분모가 조용히 무효가 되므로,
    # 바꿀 때는 반드시 18셀을 **동시에** 증설한 뒤 상수를 고친다.
    ap.add_argument("--per-cell", type=int, default=PER_CELL_DEFAULT,
                    help=f"3×2×3 각 셀의 기대 문항 수 (현재 {PER_CELL_DEFAULT} = "
                         f"dev {DEV_PER_CELL} + heldout {HELDOUT_PER_CELL}. 증설 전은 3)")
    args = ap.parse_args()

    dev = load_jsonl(DEV_PATH)
    heldout = load_jsonl(HELDOUT_PATH)
    regression = load_jsonl(REGRESSION_PATH)
    valuechain = load_jsonl(VALUECHAIN_PATH)
    safety = load_jsonl(SAFETY_PATH)
    rows = dev + heldout + regression + valuechain + safety
    if not rows:
        print("evalset 이 비어 있다", file=sys.stderr)
        return 1

    manifest = load_manifest()
    known = set(manifest)
    sectors = load_sectors()
    tool_names = load_tool_names()
    chains = load_chains()
    counterparties = load_counterparties()
    amounts = load_amounts()

    rep = Report()
    rep.add("C0. 스키마 필드 완비 · question_id 유일", check_schema(rows))
    rep.add("C1. evidence[].rcept_no 가 manifest 에 실재", check_evidence_exists(rows, known))
    rep.add("C2. answer 가 answer_form 형식과 일치", check_answer_form(rows))
    rep.add("C3. answerable=false 면 answer_form == refusal", check_unanswerable(rows))
    rep.add("C4. required_tools 가 TOOL_SPECS 에 존재", check_tools(rows, tool_names))
    grid_fail, _ = check_grid(rows, args.per_cell)
    rep.add("C5. 3×2×3 셀 카운트가 그리드와 일치", grid_fail)
    rep.add("C5b. 축별 표본 수가 그리드와 일치", check_axis_totals(rows, args.per_cell))
    rep.add("C6. heldout 문항의 corp_name 이 홀드아웃 섹터에 속함",
            check_heldout_sectors(heldout, sectors))
    rep.add("C7. dev/heldout 간 기업 중복 없음",
            check_split_disjoint(dev + regression + valuechain + safety, heldout, sectors))
    rep.add("C8. track 이 answerable·3축 라벨·담긴 파일과 일치",
            check_track(rows)
            + check_track_matches_file(dev, heldout, regression, valuechain, safety))
    rep.add("C9. 회귀 문항이 재현 대상(regression_of)을 선언",
            check_regression_meta(rows))
    rep.add("C10. 실측 실패 유형 전부에 재현 문항 존재 (게이트 H-6)",
            check_failure_mode_coverage(rows))
    rep.add("C11. multi_compare×closed 변별력 보강 (H-7 경고 대응)",
            check_discrimination_row(rows))
    rep.add("C12. 밸류체인 문항이 실재하는 허브를 경유 (2-hop 요건)",
            check_valuechain_hub(rows, counterparties)
            + ([] if len(valuechain) >= VALUECHAIN_MIN else
               [f"밸류체인 트랙이 {len(valuechain)}문항 — 최소 {VALUECHAIN_MIN}"]))
    rep.add("C13. 밸류체인 closed 수치답이 원본 amount 재계산과 일치",
            check_valuechain_answer(rows, amounts))
    rep.add("C14. 안전성 문항이 범주·겨냥 코드 경로를 선언 (R1-7)",
            check_safety_meta(rows, known) + check_safety_coverage(rows))
    rep.add("G1. '상' 문항에 정정 이력 추적 포함 (게이트 B)", check_correction_in_hard(rows))
    rep.add("G2. correction_tracking 근거가 실제 정정 체인",
            check_correction_evidence(rows, chains))
    rep.add("G3. 정기공시 정정 문항의 근거가 실제 원공시↔정정본 쌍",
            check_periodic_correction_pairs(rows, manifest))

    print(summarize(rows, sectors))
    print()
    print(grid_table(rows, args.per_cell))
    print()
    print(regression_table(rows))
    print()
    print(valuechain_table(rows))
    print()
    print(safety_table(rows))
    print()
    print(rep.render())
    print()
    print("RESULT:", "OK" if rep.ok else "FAILED")
    return 0 if rep.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
