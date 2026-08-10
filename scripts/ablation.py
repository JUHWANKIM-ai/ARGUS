"""Phase I — Ablation 러너. arm 을 하나씩 돌리고 Δ 를 축별로 분해한다.

    python scripts/ablation.py --dry                 # LLM 0회 리허설 (검색 차이만 본다)
    python scripts/ablation.py --arm A8              # arm 하나 실행 (크레딧 지출)
    python scripts/ablation.py --run                 # RUN_ORDER 전부 (A8 -> A0)
    python scripts/ablation.py --heldout             # 🔴 최종 구성 1회만
    python scripts/ablation.py                       # 캐시만으로 재집계 (LLM 0회)

v2 (88문항 · A8 추가):

    python scripts/ablation.py --cohort full \\
        --arms A8,L1,L2,L3,A7,A6,A5,A4,A1b,A1,A0 --out artifacts/ablation/v2 --budget 20000
    python scripts/ablation.py --cohort valuechain --arms A8,L3 --out artifacts/ablation/v2/vc
    python scripts/ablation.py --report --out artifacts/ablation/v2   # 게이트 I, LLM 0회

## 코호트와 산출 폴더를 인자로 뺀 이유

v1 은 dev 42문항을 `artifacts/ablation/<arm>/` 에 남겼고 그것이 비교 기준선이다.
v2 는 88문항을 재면서 **같은 이름을 덮지 않아야** 한다 — 덮으면 v1 과 대조할 축이 사라진다.
그래서 `--out` 이 arm 폴더의 부모를 갈고, `--cohort` 가 무엇을 재는지 고른다.
**요청 캐시(`CACHE_DIR`)는 갈지 않는다** — 프롬프트가 같은 요청을 두 번 사지 않는 것이
`--out` 을 가르는 것보다 중요하다.

🔴 크레딧 규칙을 코드로 강제한다.

1. **heldout 은 `--heldout` 없이는 열리지 않는다.** arm 루프는 dev 42문항만 본다.
2. **두 겹으로 캐시한다.**
   ① 응답 파일 — arm 별 `artifacts/ablation/<arm>/responses/Q-xxx.json`.
   ② **요청 캐시** — 요청 본문 해시로 CLOVA 응답 원문을 저장한다. 온도 0.0 이라
      같은 프롬프트는 같은 답이 나오는데 크레딧은 두 번 나간다. arm 8개 중 다수가
      **정기공시 18문항에서 프롬프트가 글자 하나 안 바뀌므로** 이 캐시가 없으면
      같은 값을 여덟 번 산다.
3. **실측 usage 만 센다.** `--budget-won` 을 넘으면 그 자리에서 멈추고 부분 결과를 남긴다.
4. 채점기는 `scripts/error_analysis.py` 를 **그대로 재사용**한다. Phase H 가 버그 3건을
   잡아 회귀 테스트로 고정해 둔 채점기라 새로 만들면 그 수정이 통째로 날아간다.

## 비용을 두 가지로 보고하는 이유

요청 캐시 덕분에 **실지출**과 **arm 의 고유 비용**이 갈린다.

    spent_won   — 이번 실행에서 실제로 나간 돈 (캐시 적중 제외)
    arm_cost_won — 그 arm 을 혼자 돌렸을 때의 비용 (캐시 적중 포함, 실측 usage 합)

성능/비용 곡선의 x축은 뒤엣것이다. 앞엣것은 상한 12,000원을 지키기 위한 회계다.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from argus import ablation                                        # noqa: E402
from scripts import error_analysis as ea                          # noqa: E402

OUT_DIR = ROOT / "artifacts" / "ablation"
#: 🔴 **`--out` 으로 갈리지 않는다.** 요청 캐시는 프롬프트 해시 -> 응답이라 실행 사이에
#: 공유돼야 값을 한다. v2 폴더에 새 캐시를 파면 v1·T7 이 이미 산 응답을 다시 산다.
CACHE_DIR = ROOT / "artifacts" / "ablation" / "cache"

#: 인덱스 스냅샷. §I-3 — "중간에 재빌드하면 비교가 깨진다." 해시를 arm 시작 전에 찍는다.
INDEX_FILES = (
    "artifacts/exchange_bm25.json", "artifacts/major_bm25.json",
    "artifacts/holding_bm25.json", "artifacts/section_index.json",
    "artifacts/section_index.npz", "artifacts/exchange_records.jsonl",
    "artifacts/major_records.jsonl", "artifacts/holding_records.jsonl",
    "artifacts/periodic_sections.jsonl", "artifacts/periodic_sections.txt",
    "artifacts/references.jsonl",
    "data/dart-disclosures/manifest.jsonl", "data/dart-disclosures/universe.csv",
)

BUDGET_WON = 12_000.0
#: 상한의 80%. 여기 닿으면 멈추고 부분 결과를 보고한다.
SOFT_STOP_WON = 9_600.0


# --- 요청 캐시 (크레딧 이중 지출 방지) -----------------------------------------

class RequestCache:
    """CLOVA 요청 본문 해시 -> 응답 원문. **API 키는 저장하지 않는다.**

    `clova._post(url, headers, body)` 를 감싼다. 키에는 url 과 body 만 넣는다 —
    headers 에는 Authorization 이 들어 있고, 그걸 디스크에 남기면 아티팩트가 비밀정보가 된다.
    """

    def __init__(self, root: Path, offline: bool = False) -> None:
        self.root = root
        self.offline = offline
        self.hits = self.misses = 0
        self.fresh_prompt = self.fresh_completion = 0

    @staticmethod
    def _key(url: str, body: dict) -> str:
        blob = json.dumps({"url": url, "body": body}, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def install(self):
        from argus import clova

        original = clova._post

        def cached(url, headers, body, timeout=60):
            path = self.root / f"{self._key(url, body)}.json"
            if path.exists():
                saved = json.loads(path.read_text(encoding="utf-8"))
                self.hits += 1
                return saved["status"], saved["payload"], saved["latency_s"]
            if self.offline:
                raise RuntimeError("요청 캐시 미적중인데 --offline 입니다 (호출 금지)")
            status, payload, latency = original(url, headers, body, timeout)
            if status == 200:
                self.root.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(
                    {"status": status, "payload": payload, "latency_s": latency},
                    ensure_ascii=False), encoding="utf-8")
                usage = (payload.get("result") or {}).get("usage") or {}
                self.misses += 1
                self.fresh_prompt += usage.get("promptTokens", 0)
                self.fresh_completion += usage.get("completionTokens", 0)
            return status, payload, latency

        clova._post = cached
        return original

    @property
    def fresh_won(self) -> float:
        return ea.cost_won(self.fresh_prompt, self.fresh_completion)


def install_dry_stub() -> None:
    """LLM 0회 리허설. 검색·성형·검증의 arm 차이만 본다 — 크레딧 0원."""
    from argus import clova

    def stub(url, headers, body, timeout=60):
        text = ("리허설 응답입니다. (근거: 사업보고서 2025.12, 접수번호 20250101000001, 본문)"
                if "critic" not in json.dumps(body)[:200] else
                '{"supported":true,"has_citation":true,"hallucination":false,"reason":"dry"}')
        if "심사자" in json.dumps(body, ensure_ascii=False)[:2000]:
            text = ('{"covers_key_points":1,"grounded":1,"no_fabrication":1,"reason":"dry"}')
        return 200, {"result": {"message": {"content": text},
                                "usage": {"promptTokens": 0, "completionTokens": 0},
                                "finishReason": "stop"}}, 0.0

    clova._post = stub


# --- 입력 ---------------------------------------------------------------------

#: 🔴 A2 측정 스냅샷. 다른 트랙이 dev 를 42 -> 78 로 늘리는 중이라, v1 A6 과 비교하려면
#: **같은 42문항**으로 재야 한다. 안 그러면 재는 것이 arm 이 아니라 문항 구성의 변화다.
SNAPSHOT_PATH = OUT_DIR / "A2" / "evalset_snapshot.json"


def load_dev(snapshot: str | Path | None = None) -> list[dict]:
    cases = ea.load_dev_cases()
    if not snapshot:
        return cases
    meta = json.loads(Path(snapshot).read_text(encoding="utf-8"))
    wanted = set(meta["question_ids"])
    picked = [c for c in cases if c["question_id"] in wanted]
    missing = wanted - {c["question_id"] for c in picked}
    if missing:
        raise SystemExit(f"스냅샷 문항이 questions.jsonl 에서 사라졌습니다: {sorted(missing)}")
    print(f'스냅샷 {meta["n"]}문항 고정 (sha256 {meta["sha256_of_joined_ids"][:16]}…)')
    return picked


def load_cases(cohort: str = "", snapshot: str | Path | None = None) -> list[dict]:
    """이번 실행의 문항 집합. `--cohort` 가 있으면 채점기와 **같은 출처**를 쓴다.

    코호트를 손으로 적지 않는 것이 요점이다 — `ea.load_cohort` 가 `measured` 를 v1
    산출물에서 읽으므로, 여기서 별도 목록을 두면 게이트 D 의 분모와 조용히 갈라진다.
    """
    if cohort:
        return ea.load_cohort(cohort)
    return load_dev(snapshot)


def load_heldout() -> list[dict]:
    """🔴 heldout 18문항. **최종 구성 1회 전용.** arm 루프는 이 함수를 부르지 않는다."""
    path = ROOT / "evalset" / "heldout.jsonl"
    with path.open(encoding="utf-8") as fh:
        cases = [json.loads(line) for line in fh if line.strip()]
    bad = sorted({c.get("split") for c in cases} - {"heldout"})
    if bad:
        raise SystemExit(f"heldout 아닌 split 이 섞였습니다: {bad}")
    return cases


def index_snapshot() -> dict:
    """인덱스 파일 해시. arm 간 비교의 전제라 실행마다 다시 찍고 대조한다."""
    out = {}
    for name in INDEX_FILES:
        path = ROOT / name
        if not path.exists():
            out[name] = None
            continue
        digest = hashlib.sha256()
        with path.open("rb") as fh:
            for block in iter(lambda: fh.read(1 << 20), b""):
                digest.update(block)
        out[name] = f"{digest.hexdigest()[:16]}:{path.stat().st_size}"
    return out


# --- arm 실행 -----------------------------------------------------------------

def arm_dir(arm: str) -> Path:
    return OUT_DIR / arm


def _use_dirs(arm: str) -> None:
    """채점기의 캐시 경로를 arm 폴더로 돌린다. 채점 로직은 그대로 재사용한다."""
    base = arm_dir(arm)
    ea.RESPONSE_DIR = base / "responses"
    ea.JUDGE_DIR = base / "judge"
    ea.OUT_DIR = base


def run_arm(arm: str, cases: list[dict], cache: RequestCache,
            budget_won: float, force: bool = False, config: str | None = None) -> dict:
    """한 arm 을 전수로 돌린다. **문항마다 즉시 디스크에 남긴다** — 끊겨도 잃지 않는다.

    `config` 는 폴더 이름과 arm 구성을 가르기 위한 것이다. heldout 실행은 결과를
    `A6_heldout_run/` 에 남기지만 구성은 **A6 그대로**여야 한다.
    """
    _use_dirs(arm)
    base = arm_dir(arm)
    base.mkdir(parents=True, exist_ok=True)
    started_won = cache.fresh_won
    rows, stopped = [], None

    with ablation.use(config or arm):
        for case in cases:
            run, cached = ea.with_retry(ea.run_case, case, force=force)
            judged = None
            if case.get("answer_mode") == "open":
                judged, judge_cached = ea.with_retry(ea.run_judge, case,
                                                     run["response"], force=force)
                if not judge_cached:
                    time.sleep(ea.PACE_S)
            if not cached:
                time.sleep(ea.PACE_S)
            rows.append({
                "question_id": case["question_id"],
                "llm_calls": run["llm_calls"],
                "prompt_tokens": run["prompt_tokens"],
                "completion_tokens": run["completion_tokens"],
                "judge_tokens": (judged or {}).get("prompt_tokens", 0)
                                + (judged or {}).get("completion_tokens", 0),
                "latency_s": run["latency_s"],
            })
            spent = cache.fresh_won - started_won
            print(f'  [{arm}] {case["question_id"]} calls={run["llm_calls"]} '
                  f'tok={run["prompt_tokens"]}+{run["completion_tokens"]} '
                  f'· 이번 arm 실지출 {spent:,.0f}원 · 누적 {cache.fresh_won:,.0f}원')
            if cache.fresh_won > budget_won:
                stopped = case["question_id"]
                print(f"\n🔴 예산 상한 {budget_won:,.0f}원 도달 — {arm} 을 "
                      f"{stopped} 에서 중단합니다 (누적 {cache.fresh_won:,.0f}원)")
                break

    return {"arm": arm, "n_run": len(rows), "stopped_at": stopped,
            "spent_won": round(cache.fresh_won - started_won, 1), "runs": rows}


def score_arm(arm: str, cases: list[dict], records: dict) -> dict:
    """캐시된 응답만으로 집계한다. LLM 0회 — 재집계는 공짜여야 한다."""
    _use_dirs(arm)
    rows = ea.analyse(cases, records)
    report = ea.build_report(cases, rows)
    report["arm"] = arm
    report["arm_config"] = vars(ablation.ARMS[arm]) if arm in ablation.ARMS else {}
    base = arm_dir(arm)
    base.mkdir(parents=True, exist_ok=True)
    (base / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    (base / "error_matrix.md").write_text(
        ea.render_matrix(rows, report), encoding="utf-8")
    return report


# --- 검색 단계만 재기 (LLM 0회) --------------------------------------------------
# 🔴 **정답률보다 검색 지표를 먼저 잰다.** 생성은 검색 결과의 함수이므로, 골든 근거 적중과
# Recall@5 가 하나도 안 움직이면 정답률도 안 움직인다 — 그 경우 생성 호출은 크레딧을
# 태워 잡음을 사는 것이다. A2 는 정확히 그 위험이 큰 arm 이라(Phase H 겨냥 실패 0건)
# 이 모드가 먼저 돈다.
#
# 채점기는 `scripts/error_analysis.py` 를 그대로 재사용한다 —
# `golden_positions`(후보 안 순위) 와 `evidence_hit`(프롬프트에 실렸는가) 둘 다.

RECALL_AT = 5


def retrieval_metrics(arm: str, cases: list[dict]) -> dict:
    """한 arm 의 검색 지표. **LLM 0회 · 생성 프롬프트를 만들지 않는다.**"""
    from argus.agent import pipeline
    from argus.agent.budget import Budget
    from argus.agent.router import route
    from argus.embed import USAGE

    rows = []
    started_tokens = USAGE.tokens
    with ablation.use(arm):
        for case in cases:
            budget = Budget()
            evidence, trace_lines = pipeline._gather_evidence(
                route(case["question"]), case["question"], budget)
            evidence = ablation.shape_evidence(evidence)
            trace = " | ".join(trace_lines)
            hops = pipeline.parse_hops(trace)
            positions = ea.golden_positions(case, hops)
            hit = ea.evidence_hit(case, evidence)
            golden = positions["golden"]
            ranked = positions["rank"]
            rows.append({
                "question_id": case["question_id"],
                "difficulty": case.get("difficulty"),
                "doc_group": case.get("doc_group"),
                "n_golden": len(golden),
                "n_recall1": sum(1 for g in golden if ranked.get(g) == 1),
                "n_recall5": sum(1 for g in golden
                                 if ranked.get(g) and ranked[g] <= RECALL_AT),
                "n_evidence_hit": hit["n_present"],
                "evidence_all": hit["all"],
                "golden_rank": ranked,
                "n_blocks": evidence.count("## "),
                "n_chars": len(evidence),
                "n_hops": budget.hops, "n_steps": budget.steps,
                "dense_calls": trace.count("fusion=rrf"),
                "dense_skipped": trace.count("fusion=skip_pool"),
                "dense_gain": sum(int(m) for m in
                                  re.findall(r"dense_gain=(\d+)", trace)),
                "dense_new": sum(int(m) for m in
                                 re.findall(r"dense_new=(\d+)", trace)),
                "think_trace": trace,
            })
    return {"arm": arm, "n_cases": len(cases), "rows": rows,
            "embed_tokens": USAGE.tokens - started_tokens,
            "totals": _retrieval_totals(rows)}


def _retrieval_totals(rows: list[dict]) -> dict:
    def by(field, values):
        out = {}
        for value in values:
            sub = [r for r in rows if r["difficulty"] == value] if field == "difficulty" \
                else [r for r in rows if r["doc_group"] == value]
            golden = sum(r["n_golden"] for r in sub)
            out[value] = {"golden": golden,
                          "recall5": sum(r["n_recall5"] for r in sub),
                          "evidence_hit": sum(r["n_evidence_hit"] for r in sub),
                          "rate": round(sum(r["n_recall5"] for r in sub) / golden, 3)
                          if golden else None}
        return out

    golden = sum(r["n_golden"] for r in rows)
    return {
        "golden": golden,
        "evidence_hit": sum(r["n_evidence_hit"] for r in rows),
        "evidence_all_questions": sum(1 for r in rows if r["evidence_all"]),
        "recall1": sum(r["n_recall1"] for r in rows),
        "recall5": sum(r["n_recall5"] for r in rows),
        "recall5_rate": round(sum(r["n_recall5"] for r in rows) / golden, 3)
        if golden else None,
        "blocks": sum(r["n_blocks"] for r in rows),
        "chars": sum(r["n_chars"] for r in rows),
        "hops": sum(r["n_hops"] for r in rows),
        "steps_max": max((r["n_steps"] for r in rows), default=0),
        "dense_calls": sum(r["dense_calls"] for r in rows),
        "dense_skipped": sum(r["dense_skipped"] for r in rows),
        "dense_gain": sum(r["dense_gain"] for r in rows),
        "dense_new": sum(r["dense_new"] for r in rows),
        "by_difficulty": by("difficulty", ("하", "중", "상")),
        "by_group": by("doc_group", ("exchange", "major", "holding", "periodic")),
    }


def diff_retrieval(base: dict, other: dict) -> dict:
    """두 arm 의 문항별 차이. **어느 문항이 어떻게 바뀌었는지**가 결론의 근거다."""
    left = {r["question_id"]: r for r in base["rows"]}
    changed = []
    for row in other["rows"]:
        old = left.get(row["question_id"])
        if not old:
            continue
        keys = ("n_recall5", "n_recall1", "n_evidence_hit", "n_blocks", "n_chars")
        delta = {k: row[k] - old[k] for k in keys if row[k] != old[k]}
        if delta or old["golden_rank"] != row["golden_rank"]:
            changed.append({"question_id": row["question_id"],
                            "difficulty": row["difficulty"],
                            "doc_group": row["doc_group"], "delta": delta,
                            "rank_before": old["golden_rank"],
                            "rank_after": row["golden_rank"]})
    a, b = base["totals"], other["totals"]
    return {"base": base["arm"], "arm": other["arm"],
            "delta": {k: b[k] - a[k] for k in
                      ("evidence_hit", "evidence_all_questions", "recall1",
                       "recall5", "blocks", "chars", "hops")},
            "changed_questions": changed}


def render_retrieval(reports: dict[str, dict], diffs: list[dict], meta: dict) -> str:
    out = [f'# A2 — 검색 단계 Δ (LLM 0회)\n',
           f'생성 {time.strftime("%Y-%m-%d %H:%M")} · 자동 생성물 '
           f'(`scripts/ablation.py --retrieval`)\n',
           f'평가셋 스냅샷 **{meta["n_cases"]}문항** · sha256 `{meta["snapshot_sha"][:16]}…`\n',
           "\n## 1. arm 별 검색 지표\n",
           "| arm | 골든 근거 적중 | 전건 적중 문항 | Recall@1 | Recall@5 | 근거 블록 | 근거 글자 | hop | 스텝 최대 | dense 호출 | dense 신규 |",
           "|---|---|---|---|---|---|---|---|---|---|---|"]
    for arm, report in reports.items():
        t = report["totals"]
        out.append(f'| **{arm}** | {t["evidence_hit"]}/{t["golden"]} '
                   f'| {t["evidence_all_questions"]}/{report["n_cases"]} '
                   f'| {t["recall1"]}/{t["golden"]} '
                   f'| {t["recall5"]}/{t["golden"]} ({t["recall5_rate"]}) '
                   f'| {t["blocks"]} | {t["chars"]:,} | {t["hops"]} | {t["steps_max"]} '
                   f'| {t["dense_calls"]} | {t["dense_new"]} |')

    out.append("\n## 2. 난이도별 Recall@5\n")
    out.append("| arm | 하 | 중 | 상 |")
    out.append("|---|---|---|---|")
    for arm, report in reports.items():
        cells = []
        for level in ("하", "중", "상"):
            cell = report["totals"]["by_difficulty"][level]
            cells.append(f'{cell["recall5"]}/{cell["golden"]} ({cell["rate"]})')
        out.append(f'| **{arm}** | ' + " | ".join(cells) + " |")

    out.append("\n## 3. 문서군별 골든 근거 적중\n")
    out.append("| arm | exchange | major | holding | periodic |")
    out.append("|---|---|---|---|---|")
    for arm, report in reports.items():
        cells = [f'{report["totals"]["by_group"][g]["evidence_hit"]}'
                 f'/{report["totals"]["by_group"][g]["golden"]}'
                 for g in ("exchange", "major", "holding", "periodic")]
        out.append(f'| **{arm}** | ' + " | ".join(cells) + " |")

    out.append("\n## 4. Δ — 기준선 대비\n")
    for diff in diffs:
        out.append(f'\n### {diff["arm"]} vs {diff["base"]}\n')
        deltas = ", ".join(f"{k} {v:+d}" for k, v in diff["delta"].items() if v) or "**전부 0**"
        out.append(f"총계 Δ: {deltas}\n")
        if not diff["changed_questions"]:
            out.append("문항 단위 변화 **0건**. 후보 순위·근거 구성이 글자 하나 안 바뀌었다.\n")
            continue
        out.append("| 문항 | 난이도 | 문서군 | 변화 | 골든 순위 (전 -> 후) |")
        out.append("|---|---|---|---|---|")
        for row in diff["changed_questions"]:
            delta = ", ".join(f"{k} {v:+d}" for k, v in row["delta"].items()) or "순위만"
            out.append(f'| {row["question_id"]} | {row["difficulty"]} | {row["doc_group"]} '
                       f'| {delta} | `{row["rank_before"]}` -> `{row["rank_after"]}` |')
    return "\n".join(out) + "\n"


# --- 집계 ---------------------------------------------------------------------

def _rate(report: dict, path: tuple) -> dict:
    node = report["crosstab"]
    for key in path:
        node = node[key]
    return node


AXES = {"difficulty": ("하", "중", "상"),
        "answer_mode": ("closed", "open"),
        "task_type": ("search_extract", "multi_compare", "complex_reason")}
AXIS_LABELS = {"difficulty": "난이도", "answer_mode": "답변형식", "task_type": "과제유형"}

#: 🔴 v1 에서 세 난이도가 전부 1~2/2 로 진동했다. 셀이 커진 v2 에서 **다시 확인**하고,
#: 여전하면 이 행의 Δ 는 읽지 않는다.
BLIND_CELL = "multi_compare|closed"


def summarise(reports: dict[str, dict]) -> dict:
    """arm 별 정답률·비용과 축별 분해를 한 표로 모은다.

    🔴 정답률·히스토그램은 **그리드 기준**을 쓴다. 회귀 10·밸류체인 12는
    `answerable=true` 이고 3축 라벨도 채워져 있어 전체 기준으로 세면 분모에 섞여 들고,
    그러면 v1 42문항과 나란히 읽을 축이 하나도 안 남는다. 트랙별 수치는 `tracks` 에
    **따로** 담는다 — 합치지 않는다.
    """
    out: dict = {"arms": {}, "axes": {a: {} for a in AXES}, "cells": {}}
    for arm, report in reports.items():
        cross = report["crosstab"]
        grid = cross["grid_overall"]
        out["arms"][arm] = {
            "correct": grid["correct"], "n_scored": grid["n_scored"], "rate": grid["rate"],
            "n_grid": grid["n"], "data_limit": grid["data_limit"],
            "overall": cross["overall"]["rate"],
            "overall_correct": cross["overall"]["correct"],
            "overall_n_scored": cross["overall"]["n_scored"],
            "info_limit_correct": cross["info_limit"]["correct"],
            "info_limit_n": cross["info_limit"]["n"],
            "hallucination": cross["info_limit"]["stages"].get("HALLUCINATION", 0),
            "tracks": cross.get("by_track", {}),
            "tokens": report["totals"]["total_tokens"],
            "arm_cost_won": report["totals"]["cost_won"],
            "llm_calls": report["totals"]["llm_calls"],
            "latency_s_mean": report["totals"]["latency_s_mean"],
            # 🔴 `_grid` 변종을 쓴다 (Task 7 이 만들어 둔 것). 전체 기준을 쓰면
            #    같은 표 안에서 분모가 둘이 된다.
            "failures": report.get("failure_histogram_grid") or report["failure_histogram"],
            "failures_all": report["failure_histogram"],
            "root_causes": (report.get("root_cause_histogram_grid")
                            or report["root_cause_histogram"]),
            "citation_rate": report.get("citation_rate_grid") or report["citation_rate"],
            "flags": report.get("arm_config") or {},
            "golden_evidence_all": sum(1 for r in ea.grid_rows(report["rows"])
                                       if r["golden_evidence_all"]),
            "golden_evidence_hit": sum(len(r["golden_in_evidence"])
                                       for r in ea.grid_rows(report["rows"])),
            "golden_total": sum(len(r["golden"]) for r in ea.grid_rows(report["rows"])),
        }
        for axis, values in AXES.items():
            for value in values:
                cell = cross["marginals"][axis][value]
                out["axes"][axis].setdefault(value, {})[arm] = {
                    "correct": cell["correct"], "n": cell["n_scored"], "rate": cell["rate"]}
        for level in ("하", "중", "상"):
            cell = cross["cells"][f"{BLIND_CELL}|{level}"]
            out["cells"].setdefault(level, {})[arm] = {
                "correct": cell["correct"], "n": cell["n_scored"]}
    return out


def _delta(a: dict | None, b: dict | None) -> float | None:
    if not a or not b or a["rate"] is None or b["rate"] is None:
        return None
    return round((b["rate"] - a["rate"]) * 100, 1)


def render_summary(summary: dict, meta: dict) -> str:
    ladder = [a for a in ablation.LADDER if a in summary["arms"]]
    loo = [a for a in ablation.LOO_REMOVED if a in summary["arms"]]
    base_name = ladder[-1] if ladder else ""
    out = [f'# Phase I — Ablation ({meta.get("cohort") or "dev"} '
           f'{meta.get("n_cases", 0)}문항)\n',
           f'생성 {time.strftime("%Y-%m-%d %H:%M")} · 자동 생성물 (`scripts/ablation.py`)\n',
           f'단가 {ea.PRICE_WON_PER_1K}원/1K · 실지출 합계 **{meta["spent_won"]:,.0f}원** '
           f'/ 상한 {meta["budget_won"]:,.0f}원 · 요청 캐시 적중 {meta["cache_hits"]}회\n']

    out.append("\n## 1. 누적 곡선 — 정답률과 Δ (**그리드 기준**)\n")
    out.append("| arm | 정답 / 채점 | 정답률 | Δ(직전) | 골든 전건적중 | LLM 호출 | 토큰 | arm 비용(원) |")
    out.append("|---|---|---|---|---|---|---|---|")
    previous = None
    for arm in ladder:
        row = summary["arms"][arm]
        # 🔴 밸류체인 코호트는 그리드 문항이 0건이라 `rate` 가 None 이다. 여기서 죽으면
        #    A8 의 진짜 겨냥점을 담은 실행이 **집계 단계에서 통째로 날아간다** —
        #    응답은 디스크에 남았는데 표가 안 나오는 것이 가장 헷갈리는 실패다.
        out.append(f'| **{arm}** | {row["correct"]}/{row["n_scored"]} | {_pct(row["rate"])} '
                   f'| {_pp(previous, row["rate"])} '
                   f'| {row["golden_evidence_all"]}/{row["n_grid"]} '
                   f'| {row["llm_calls"]} | {row["tokens"]:,} | {row["arm_cost_won"]:,.1f} |')
        previous = row["rate"]

    if loo and base_name:
        base = summary["arms"][base_name]
        out.append(f"\n## 2. Leave-one-out — 완성 구성({base_name})에서의 필수성\n")
        out.append(f'| 구성 | 뺀 것 | 정답 / 채점 | 정답률 | Δ({base_name} 대비) |')
        out.append("|---|---|---|---|---|")
        out.append(f'| {base_name} | — | {base["correct"]}/{base["n_scored"]} '
                   f'| {_pct(base["rate"])} | — |')
        for arm in loo:
            row = summary["arms"][arm]
            out.append(f'| {arm} | {ablation.LOO_REMOVED[arm]} '
                       f'| {row["correct"]}/{row["n_scored"]} | {_pct(row["rate"])} '
                       f'| {_pp(base["rate"], row["rate"])} |')

    out.append("\n## 3. 축별 Δ 분해\n")
    for axis, table in summary["axes"].items():
        out.append(f'\n### {AXIS_LABELS[axis]}\n')
        out.append("| 값 | " + " | ".join(ladder) + f' | Δ {ladder[0]}→{base_name} |')
        out.append("|---|" + "---|" * (len(ladder) + 1))
        for value, per_arm in table.items():
            cells = []
            for arm in ladder:
                cell = per_arm.get(arm)
                cells.append(f'{cell["correct"]}/{cell["n"]}' if cell else "—")
            span = _delta(per_arm.get(ladder[0]), per_arm.get(base_name))
            out.append(f'| {value} | ' + " | ".join(cells)
                       + f' | {"—" if span is None else f"{span:+.1f}%p"} |')

    out.append("\n## 4. 트랙별 (**합치지 않는다**)\n")
    tracks = [t for t in (ea.GRID_TRACK,) + ea.OFF_GRID_TRACKS
              if any(t in summary["arms"][a]["tracks"] for a in summary["arms"])]
    if tracks:
        out.append("| arm | " + " | ".join(tracks) + " |")
        out.append("|---|" + "---|" * len(tracks))
        for arm in ladder + loo:
            cells = []
            for track in tracks:
                cell = summary["arms"][arm]["tracks"].get(track)
                cells.append(f'{cell["correct"]}/{cell["n_scored"]}' if cell else "—")
            out.append(f"| {arm} | " + " | ".join(cells) + " |")

    out.append("\n## 5. 실패 단계 분포 (**그리드 기준**)\n")
    out.append("| arm | " + " | ".join(ea.ALL_LABELS) + " |")
    out.append("|---|" + "---|" * len(ea.ALL_LABELS))
    for arm in ladder + loo:
        hist = summary["arms"][arm]["failures"]
        out.append(f"| {arm} | " + " | ".join(str(hist.get(s, 0)) for s in ea.ALL_LABELS) + " |")
    return "\n".join(out) + "\n"


# --- 게이트 I 보고서 (LLM 0회) --------------------------------------------------
# 🔴 `scripts/ablation_report.py` 를 고치지 않는다 — 그 스크립트는 v1 `gate_I.md` 를
# **재생성**하는 유일한 수단이고, 출력 경로가 v1 자리에 박혀 있다. v2 는 여기 붙인다.

#: 부록 D 의 논문 보고값. 우리 Δ 가 타당한 크기인지 판정하는 기준이다 (D-1 · D-2).
PAPER_EFFECTS = (
    ("A0", "메타 프리필터", "우리 실측 Q-001", "14/1,332 → 1/1", "—"),
    ("A1", "BM25", "G-Retriever w/o retrieval", "-9.4%", "제거"),
    ("A1b", "인덱스 커버리지", "ToG-2 vs Vanilla RAG (ToG-FinQA)", "0% → 34.0%", "조건 비교"),
    ("A4", "Agent 루프", "ToG-2 → ToG-2 (AdvHotpotQA)", "+16.6%", "추가"),
    ("A4", "Agent 루프", "HippoRAG + IRCoT (2Wiki R@5)", "+18%", "추가"),
    ("A5", "계산 Tool", "— (해당 논문 없음, policy §4)", "—", "—"),
    ("A6", "Verifier", "Self-Consistency (GSM8K)", "+17.9%", "추가"),
    ("A7", "거절 강제 (플래그 없음)", "— (§I-5d 가 42문항으로 이미 쟀다)", "—", "—"),
    ("A8", "발주처 2-hop", "HippoRAG PPR 이웃까지 (2Wiki R@2) 59.2 → 70.7", "+11.5", "대체"),
    ("L1", "체인 hop 제거", "Relink w/o query-driven ranker", "-19.4% EM", "제거"),
    ("L2", "major·holding 필드 제거", "G-Retriever w/o textualized graph", "-19.2%", "제거"),
    ("L3", "발주처 hop 제거", "Relink w/o 명시 그래프 (Gb)", "-12.9% EM", "제거"),
)

#: 🔴 A8 의 성공 기준은 전체 정답률이 아니다. 밸류체인 12문항을 세 갈래로 가른다 —
#: ①에서 안 오르면 A8 은 실패이고, ③에서 떨어지면 Δ 가 양수여도 넣지 않는다.
VC_FAN_IN = ("Q-110", "Q-114", "Q-117")       # 완전 열거 — A8 의 진짜 겨냥점
VC_UNVERIFIABLE = ("Q-116",)                  # 완결성 확인 불가


def _load_report(root: Path, arm: str) -> dict | None:
    path = root / arm / "report.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def _pct(value) -> str:
    return "—" if value is None else f"{value * 100:.1f}%"


def _pp(before, after) -> str:
    if before is None or after is None:
        return "—"
    return f"{(after - before) * 100:+.1f}%p"


def spend_from_runs(root: Path) -> dict:
    """이 폴더의 arm 들이 실제로 결제한 몫. `run.json` 의 `spent_won` 합이다.

    요청 캐시 파일 전체 합(`ablation_report.spend_from_cache`)을 쓰면 v1·T7 이 산 것까지
    더해져 **v2 의 지출이 부풀어 오른다.** v2 는 자기 실행분만 센다.
    """
    total, per_arm = 0.0, {}
    for path in sorted(root.glob("*/run.json")):
        run = json.loads(path.read_text(encoding="utf-8"))
        per_arm[path.parent.name] = run.get("spent_won", 0.0)
        total += run.get("spent_won", 0.0)
    return {"won": round(total, 1), "per_arm": per_arm}


def _grid_rate(rows: list[dict], wanted: set[str] | None = None) -> dict:
    sub = [r for r in ea.grid_rows(rows) if wanted is None or r["question_id"] in wanted]
    return ea._rate(sub)


def render_gate(root: Path, budget_cap: float) -> str:
    """게이트 I 5항목(heldout 제외)을 표로 낸다. **LLM 0회.**"""
    reports = {a: _load_report(root, a) for a in ablation.RUN_ORDER}
    reports = {k: v for k, v in reports.items() if v}
    ladder = [a for a in ablation.LADDER if a in reports]
    loo = [a for a in ablation.LOO_REMOVED if a in reports]
    full = ladder[-1] if ladder else ""
    summary = summarise(reports)
    arms = summary["arms"]
    spend = spend_from_runs(root)
    meta_path = root / "summary.json"
    meta = (json.loads(meta_path.read_text(encoding="utf-8"))["meta"]
            if meta_path.exists() else {})
    vc = {a: _load_report(root / "vc", a) for a in ("A8", "L3", "A7")}
    vc = {k: v for k, v in vc.items() if v}

    o: list[str] = []
    o.append("# Phase I — Ablation v2 결과 (게이트 I)\n")
    o.append(f'생성 {time.strftime("%Y-%m-%d %H:%M")} · 자동 생성물 '
             "(`scripts/ablation.py --report`) · **LLM 0회 재생성 가능**\n")
    o.append(f'실측 단가 {ea.PRICE_WON_PER_1K}원/1K · 코호트 `{meta.get("cohort", "full")}` '
             f'**{meta.get("n_cases", 0)}문항** (dev 78 + 회귀 10) · 모델 HCX-005 · 온도 0.0\n')
    o.append("🔴 **heldout 낙폭은 이 문서에 없다 — Task 10 몫이다.** "
             "여기서 `evalset/heldout.jsonl` 을 열지 않았다.\n")

    # --- 0. arm 구성 확인 — 무엇이 실제로 켜졌는가 ---
    o.append("\n## 0. arm 구성 — 켠 것이 실제로 켜졌는가\n")
    o.append("Δ 를 읽기 전에 스위치를 확인한다. **끈 줄 알았는데 안 꺼진 것**이 "
             "가장 비싼 실패다 (Δ 가 0 으로 나오고 우리는 그것을 '기여 없음' 으로 읽는다).\n")
    flags = [f for f in ablation.FLAG_NAMES if f != "dense"]
    o.append("| arm | " + " | ".join(f"`{f}`" for f in flags) + " |")
    o.append("|---|" + "---|" * len(flags))
    for arm in ladder + loo:
        cells = []
        for f in flags:
            value = arms[arm]["flags"].get(f)
            cells.append(str(value) if isinstance(value, str)
                         else ("✓" if value else "✗"))
        o.append(f"| **{arm}** | " + " | ".join(cells) + " |")
    o.append("\n🔴 **`A7` 은 플래그가 A6 과 같다** — 거절 강제·시점 지정은 무조건 실행되는 "
             "코드이고(`router.missing_named_report` · `pipeline._as_of_ym`) 플래그로 끌 수 "
             "없다. 그래서 사다리의 `Δ(A6→A7)` 은 **측정값이 아니라 정의상 0** 이다. "
             "A7 의 실효는 §I-5d 가 42문항 코호트로 이미 쟀다 (grid +2.8%p · 환각 1→0).\n")
    o.append("🔴 **`L3` 은 플래그가 A7 과 같다** (`A8 − counterparty_hop = A7`). "
             "두 수는 부호만 다른 같은 값이어야 하고, 아니면 어딘가 틀렸다.\n")

    # --- 1. 비용 ---
    o.append("\n## 1. arm 별 비용 실측\n")
    o.append("`arm 비용` = 그 arm 을 혼자 돌렸을 때의 실측 usage 합 (캐시 적중 포함). "
             "`신규 지출` = 요청 캐시가 못 막아 **이번에 결제된** 몫.\n")
    o.append("| arm | LLM 호출 | prompt | completion | judge | 총 토큰 | arm 비용(원) | 신규 지출(원) |")
    o.append("|---|---|---|---|---|---|---|---|")
    arm_sum = 0.0
    for arm in ladder + loo:
        t = reports[arm]["totals"]
        arm_sum += t["cost_won"]
        fresh = spend["per_arm"].get(arm)
        o.append(f'| {arm} | {t["llm_calls"]} | {t["prompt_tokens"]:,} '
                 f'| {t["completion_tokens"]:,} | {t["judge_tokens"]:,} '
                 f'| {t["total_tokens"]:,} | {t["cost_won"]:,.1f} '
                 f'| {"—" if fresh is None else f"{fresh:,.1f}"} |')
    for arm, report in vc.items():
        t = report["totals"]
        fresh = spend_from_runs(root / "vc")["per_arm"].get(arm)
        o.append(f'| {arm}/밸류체인 (12문항) | {t["llm_calls"]} | {t["prompt_tokens"]:,} '
                 f'| {t["completion_tokens"]:,} | {t["judge_tokens"]:,} '
                 f'| {t["total_tokens"]:,} | {t["cost_won"]:,.1f} '
                 f'| {"—" if fresh is None else f"{fresh:,.1f}"} |')
        arm_sum += t["cost_won"]
    vc_spend = spend_from_runs(root / "vc")["won"] if (root / "vc").exists() else 0.0
    total_spend = spend["won"] + vc_spend
    o.append(f'| **합계 (캐시 없었다면)** | | | | | | **{arm_sum:,.0f}** | |')
    o.append(f'| **실제 결제** | | | | | | | **{total_spend:,.0f}** |')
    o.append(f'\n상한 {budget_cap:,.0f}원 대비 **{total_spend / budget_cap * 100:.1f}%** '
             f'사용 (80% 정지선 {budget_cap * 0.8:,.0f}원). '
             f'요청 캐시가 **{arm_sum - total_spend:,.0f}원**을 막았다.\n')

    # --- 2. 누적 곡선 ---
    n_scored = arms[full]["n_scored"] if full else 0
    o.append(f"\n## 2. 누적 곡선 — A0 → {full} (**그리드 기준**)\n")
    o.append(f'정답률은 **채점 대상 {n_scored}문항** 기준이다. 그리드 밖 트랙'
             f'(회귀 10 · 정보한계)은 §4 에 따로 낸다 — 섞으면 v1 42문항과 나란히 '
             "읽을 축이 하나도 안 남는다.\n")
    o.append("| arm | 정답 | 정답률 | Δ(직전) | 골든 근거 적중 | 전건 적중 문항 "
             "| arm 비용(원) | Δ정답률/Δ비용 |")
    o.append("|---|---|---|---|---|---|---|---|")
    prev_rate = prev_cost = None
    for arm in ladder:
        row = arms[arm]
        cost = row["arm_cost_won"]
        eff = "—"
        if prev_rate is not None and prev_cost is not None and cost != prev_cost:
            eff = f'{(row["rate"] - prev_rate) * 100 / (cost - prev_cost):+.3f}%p/원'
        o.append(f'| **{arm}** | {row["correct"]}/{row["n_scored"]} | {_pct(row["rate"])} '
                 f'| {_pp(prev_rate, row["rate"])} '
                 f'| {row["golden_evidence_hit"]}/{row["golden_total"]} '
                 f'| {row["golden_evidence_all"]}/{row["n_grid"]} | {cost:,.0f} | {eff} |')
        prev_rate, prev_cost = row["rate"], cost

    # --- 3. 축별 분해 ---
    o.append("\n## 3. 축별 Δ 분해 — 전체 Δ 하나만 보면 '왜 올랐는지' 가 사라진다\n")
    for axis, values in AXES.items():
        o.append(f'\n### {AXIS_LABELS[axis]}\n')
        o.append("| 값 | " + " | ".join(ladder)
                 + f' | Δ {ladder[0]}→{full} | 최대 단일 Δ |')
        o.append("|---|" + "---|" * (len(ladder) + 2))
        for value in values:
            cells, rates = [], []
            for arm in ladder:
                cell = summary["axes"][axis][value][arm]
                cells.append(f'{cell["correct"]}/{cell["n"]}')
                rates.append(cell["rate"])
            steps = [(rates[i + 1] - rates[i], ladder[i + 1])
                     for i in range(len(rates) - 1)
                     if rates[i] is not None and rates[i + 1] is not None]
            best = max(steps, key=lambda s: s[0]) if steps else None
            o.append(f'| {value} | ' + " | ".join(cells)
                     + f' | {_pp(rates[0], rates[-1])} | '
                     + (f'{best[1]} {best[0] * 100:+.1f}%p |' if best else "— |"))

    o.append(f'\n### ⚠ 변별력 재확인 — `{BLIND_CELL}`\n')
    o.append("v1 에서 이 셀은 세 난이도가 전부 1~2/2 로 진동했다. **셀 n 이 늘어난 "
             "v2 에서 다시 확인한다** — n 이 2 에 머물러 있으면 이 행의 Δ 는 읽지 않는다.\n")
    o.append("| arm | 하 | 중 | 상 |")
    o.append("|---|---|---|---|")
    for arm in ladder:
        cells = [summary["cells"][d][arm] for d in ("하", "중", "상")]
        o.append(f'| {arm} | ' + " | ".join(f'{c["correct"]}/{c["n"]}' for c in cells) + " |")
    cell_n = {summary["cells"][d][full]["n"] for d in ("하", "중", "상")} if full else set()
    biggest = max(cell_n or {0})
    if biggest <= 2:
        verdict = "**여전히 2 이하다 — 이 행의 Δ 는 읽지 않는다.**"
    else:
        verdict = (f"v1 의 2 에서 **{biggest} 로 늘었다.** 다만 한 문항이 뒤집히면 "
                   f"{100 / biggest:.0f}%p 가 움직인다 — **부호(방향)만 읽고 크기는 "
                   f"읽지 않는다.** 이 셀 하나로 결론을 내지 않는다 (02-golden-set B-7).")
    o.append(f'\n셀 n = {sorted(cell_n)}. {verdict}\n')

    # --- 4. 트랙별 ---
    o.append("\n## 4. 트랙별 — **합치지 않는다**\n")
    o.append("회귀는 고친 실패의 감시이고 정보한계는 거절이 정답이다. 같은 분모에 "
             "넣으면 둘 다 거짓말이 된다.\n")
    tracks = [t for t in (ea.GRID_TRACK,) + ea.OFF_GRID_TRACKS
              if any(t in arms[a]["tracks"] for a in arms)]
    o.append("| arm | " + " | ".join(tracks) + " | 정보한계 거절정답 | 환각 |")
    o.append("|---|" + "---|" * (len(tracks) + 2))
    for arm in ladder + loo:
        cells = []
        for track in tracks:
            cell = arms[arm]["tracks"].get(track)
            cells.append(f'{cell["correct"]}/{cell["n_scored"]}' if cell else "—")
        o.append(f'| {arm} | ' + " | ".join(cells)
                 + f' | {arms[arm]["info_limit_correct"]}/{arms[arm]["info_limit_n"]} '
                 f'| {arms[arm]["hallucination"]} |')

    # --- 5. LOO ---
    if loo and full:
        base = arms[full]
        o.append(f"\n## 5. Leave-one-out **3구성** — 완성 구성({full})에서의 필수성\n")
        o.append(f'v1 은 2개였다. v2 는 발주처 hop 을 더해 3개다.\n')
        o.append(f'| 구성 | 뺀 것 | 정답 | 정답률 | Δ({full}) | 골든 근거 적중 |')
        o.append("|---|---|---|---|---|---|")
        o.append(f'| **{full}** | — | {base["correct"]}/{base["n_scored"]} '
                 f'| {_pct(base["rate"])} | — '
                 f'| {base["golden_evidence_hit"]}/{base["golden_total"]} |')
        for arm in loo:
            row = arms[arm]
            o.append(f'| {arm} | {ablation.LOO_REMOVED[arm]} '
                     f'| {row["correct"]}/{row["n_scored"]} | {_pct(row["rate"])} '
                     f'| {_pp(base["rate"], row["rate"])} '
                     f'| {row["golden_evidence_hit"]}/{row["golden_total"]} |')
        o.append("\n### LOO 축별 분해\n")
        for axis, values in AXES.items():
            o.append(f'\n**{AXIS_LABELS[axis]}**\n')
            o.append(f'| 값 | {full} | ' + " | ".join(f"{a} | Δ{a}" for a in loo) + " |")
            o.append("|---|---|" + "---|" * (2 * len(loo)))
            for value in values:
                b = summary["axes"][axis][value][full]
                cells = [f'{b["correct"]}/{b["n"]}']
                for arm in loo:
                    r = summary["axes"][axis][value][arm]
                    cells += [f'{r["correct"]}/{r["n"]}', _pp(b["rate"], r["rate"])]
                o.append(f'| {value} | ' + " | ".join(cells) + " |")

    # --- 6. A8 — 밸류체인 세 갈래 ---
    o.append("\n## 6. 🔴 A8 의 성공 기준은 정답률이 아니라 완결성이다\n")
    o.append("밸류체인 12문항 중 8건은 A8 없이도 근거가 닿는다 (BM25 자유텍스트). "
             "전체 정답률로만 재면 Δ 가 작게 나오고 오판한다. 세 갈래로 가른다.\n")
    o.append(f'**① 발주처 hop 발화 문항 (LLM 0회 실측)**: 88문항 그리드에서 **0건**, '
             f'밸류체인 12문항에서 **8건** (Q-107 Q-109 Q-110 Q-113 Q-114 Q-115 '
             f'Q-116 Q-118).\n')
    o.append("→ 그래서 **88문항 그리드에서 `Δ(A7→A8)` 은 정의상 0 이다** — 근거가 "
             "글자 하나 안 바뀌므로 프롬프트가 같고 답이 같다. A8 의 Δ 는 밸류체인 "
             "트랙에서만 읽을 수 있다.\n")
    if vc.get("A8"):
        groups = (("① 완전 열거 (A8 의 진짜 겨냥점)", set(VC_FAN_IN)),
                  ("② 완결성 확인 불가", set(VC_UNVERIFIABLE)),
                  ("③ 이미 닿는 나머지 (깨지면 순손실)",
                   {r["question_id"] for r in vc["A8"]["rows"]}
                   - set(VC_FAN_IN) - set(VC_UNVERIFIABLE)))
        names = [a for a in ("A8", "L3", "A7") if a in vc]
        o.append("\n### 밸류체인 12문항 — 세 갈래\n")
        o.append("| 갈래 | 문항 | " + " | ".join(names) + " |")
        o.append("|---|---|" + "---|" * len(names))
        for label, ids in groups:
            cells = []
            for name in names:
                rate = ea._rate([r for r in vc[name]["rows"]
                                 if r["question_id"] in ids])
                cells.append(f'{rate["correct"]}/{rate["n_scored"]}')
            o.append(f'| {label} | {" ".join(sorted(ids))} | ' + " | ".join(cells) + " |")
        o.append("\n### 문항별 (A8 vs L3 = 발주처 hop 없음)\n")
        o.append("🔴 `hop 발화` 칸을 먼저 본다. **발화하지 않은 문항에서 A8 의 Δ 는 정의상 "
                 "0** 이고, 그것은 hop 이 약한 것이 아니라 **라우터가 발주처를 못 알아본 "
                 "것**이다 — 처방이 정반대다 (hop 개선이 아니라 노드 이름 인식).\n")
        o.append("| 문항 | 갈래 | hop 발화 | A8 | L3 | 판정 |")
        o.append("|---|---|---|---|---|---|")
        l3 = {r["question_id"]: r for r in (vc.get("L3") or {}).get("rows", [])}
        for row in sorted(vc["A8"]["rows"], key=lambda r: r["question_id"]):
            qid = row["question_id"]
            other = l3.get(qid)
            fired = "strat=counterparty" in (row.get("think_trace") or "")
            branch = ("①열거" if qid in VC_FAN_IN else
                      "②확인불가" if qid in VC_UNVERIFIABLE else "③이미닿음")
            mark = {(True, True): "둘 다 정답", (False, False): "둘 다 오답",
                    (True, False): "**A8 이 벌었다**", (False, True): "🔴 **A8 이 잃었다**"}
            verdict = ("—" if other is None
                       else mark[(bool(row["correct"]), bool(other["correct"]))])
            if not fired and other is not None:
                verdict += " (hop 미발화 — Δ 는 정의상 0)"
            o.append(f'| {qid} | {branch} | {"✓" if fired else "**✗**"} '
                     f'| {"✓" if row["correct"] else "✗"} '
                     f'| {"—" if other is None else ("✓" if other["correct"] else "✗")} '
                     f'| {verdict} |')
        n_fired = sum(1 for r in vc["A8"]["rows"]
                      if "strat=counterparty" in (r.get("think_trace") or ""))
        no_fire = sorted(r["question_id"] for r in vc["A8"]["rows"]
                         if "strat=counterparty" not in (r.get("think_trace") or ""))
        o.append(f'\n밸류체인 12문항 중 hop 발화 **{n_fired}건** · 미발화 '
                 f'{len(no_fire)}건 ({" ".join(no_fire)}).\n')

    # --- 7. 실패 단계 · 근본 원인 ---
    o.append("\n## 7. 실패 단계 · 근본 원인 분포 (**그리드 기준**)\n")
    o.append("| arm | " + " | ".join(ea.ALL_LABELS) + " |")
    o.append("|---|" + "---|" * len(ea.ALL_LABELS))
    for arm in ladder + loo:
        hist = arms[arm]["failures"]
        o.append(f"| {arm} | " + " | ".join(str(hist.get(s, 0))
                                           for s in ea.ALL_LABELS) + " |")
    if full:
        o.append(f'\n### {full} 잔여 실패 — 근본 원인 (그리드)\n')
        o.append("| 문항 | 난이도 | 단계 | 원인 | 내용 |")
        o.append("|---|---|---|---|---|")
        for row in ea.grid_rows(reports[full]["rows"]):
            if row["first_failure"]:
                o.append(f'| {row["question_id"]} | {row["difficulty"]} '
                         f'| {row["first_failure"]} | {row["root_cause"]} '
                         f'| {row["failure_why"][:64]} |')
        o.append(f'\n**근본 원인 히스토그램 (그리드)**: '
                 f'`{json.dumps(arms[full]["root_causes"], ensure_ascii=False)}`\n')

    # --- 8. 뒤집힌 문항 ---
    o.append("\n## 8. arm 사이에서 뒤집힌 문항 — Δ 가 어느 문항에서 왔는가\n")
    o.append("| 전이 | 새로 맞힌 문항 | 잃은 문항 |")
    o.append("|---|---|---|")
    pairs = list(zip(ladder, ladder[1:])) + [(full, a) for a in loo]
    for lower, upper in pairs:
        a = {r["question_id"]: r["correct"] for r in ea.grid_rows(reports[lower]["rows"])}
        b = {r["question_id"]: r["correct"] for r in ea.grid_rows(reports[upper]["rows"])}
        gained = sorted(q for q in a if b.get(q) and not a[q])
        lost = sorted(q for q in a if a[q] and not b.get(q))
        o.append(f'| {lower} → {upper} | {" ".join(gained) or "—"} '
                 f'| {" ".join(lost) or "—"} |')

    # --- 9. 겨냥과 Δ 대조 ---
    o.append("\n## 9. arm 겨냥 배분 vs 실제 Δ — **안 맞으면 그것이 발견이다**\n")
    o.append("Task 7 이 오답 13건을 근거 원문까지 대조해 배분한 표다. 🔴 이 표는 "
             "**앞을 보는 처방**이지 v2 의 Δ 예측이 아니다 — 겨냥 문항은 정의상 "
             f'`{full}` 에서 **아직 틀리고 있는** 문항이므로, 사다리의 어느 arm 도 그것을 '
             "건지지 못했다. 그래서 두 칸을 따로 낸다: `실제로 건진 문항`(그 arm 이 "
             "이번에 번 것)과 `겨냥 문항의 현재 상태`(그 arm 의 **다음 판**이 해야 할 것).\n")
    targeted = (("A4", ("Q-023", "Q-076", "Q-098", "Q-105", "Q-066"), "루프·추론"),
                ("A5", ("Q-044", "Q-047", "Q-097", "Q-065"), "계산·단위 정규화"),
                ("A1b", ("Q-099", "Q-101"), "인덱스 필드"),
                ("A6", ("Q-087",), "Verifier"),
                ("A3", ("Q-094",), "리랭커 (arm 제외 — 보류)"))
    final_ok = {r["question_id"]: r["correct"]
                for r in reports[full]["rows"]} if full else {}
    o.append("| arm | 겨냥 | 겨냥 문항 | 사다리 Δ | 실제로 건진 문항 "
             f'| 겨냥 문항의 {full} 상태 |')
    o.append("|---|---|---|---|---|---|")
    idx = {a: i for i, a in enumerate(ladder)}
    for arm, ids, label in targeted:
        status = " ".join(f'{q}{"✓" if final_ok.get(q) else "✗"}' for q in ids)
        if arm not in idx or idx[arm] == 0:
            o.append(f'| {arm} ({label}) | {len(ids)} | {" ".join(ids)} | — | — '
                     f'| {status} |')
            continue
        lower = ladder[idx[arm] - 1]
        a = {r["question_id"]: r["correct"] for r in ea.grid_rows(reports[lower]["rows"])}
        b = {r["question_id"]: r["correct"] for r in ea.grid_rows(reports[arm]["rows"])}
        gained = sorted(q for q in a if b.get(q) and not a[q])
        hit = [q for q in ids if q in gained]
        o.append(f'| {arm} ({label}) | {len(ids)} | {" ".join(ids)} '
                 f'| {_pp(arms[lower]["rate"], arms[arm]["rate"])} '
                 f'| {len(gained)}건 (겨냥 적중 {len(hit)}) | {status} |')
    o.append("\n**읽는 법**: 어느 arm 도 자기 겨냥 문항을 하나도 못 건졌다 — 그것이 "
             "겨냥 문항의 정의이므로 동어반복이다. 읽을 값은 `사다리 Δ`(그 arm 이 실제로 "
             "번 것)와, 겨냥 문항이 **여전히 틀리고 있다**는 사실이다. 후자는 "
             "'그 arm 을 더 밀어야 한다' 는 뜻이고, 우선순위는 겨냥 건수 순서 그대로다 "
             "(A4 5 → A5 4 → A1b 2 → A6 1).\n")

    # --- 9b. 겨냥 문항을 LOO 짝과 대조한다 — 여기서 처방이 뒤집힌다 ---
    o.append("\n### 9b. 🔴 겨냥 문항을 **그 arm 을 끈 구성**과 대조한다\n")
    o.append("겨냥표만 보면 '그 arm 을 더 밀어라' 로 읽힌다. 그런데 **그 arm 을 끄면 맞는** "
             "문항이 있으면 처방이 정반대다 — 그 arm 이 **원인**이라는 뜻이다. "
             "사다리에서 한 칸 아래 구성과 문항 단위로 대조한다.\n")
    o.append("| arm | 겨냥 문항 | 한 칸 아래 | 아래는 맞고 위는 틀린 문항 (= **그 arm 이 원인**) |")
    o.append("|---|---|---|---|")
    for arm, ids, label in targeted:
        if arm not in idx or idx[arm] == 0:
            continue
        lower = ladder[idx[arm] - 1]
        a = {r["question_id"]: r["correct"] for r in reports[lower]["rows"]}
        b = {r["question_id"]: r["correct"] for r in reports[arm]["rows"]}
        # 겨냥 문항만이 아니라 **전 트랙**에서 본다 — 회귀 트랙에 숨어 있을 수 있다.
        caused = sorted(q for q in a if a[q] and not b.get(q))
        o.append(f'| {arm} ({label}) | {" ".join(ids)} | {lower} '
                 f'| {" ".join(f"**{q}**" for q in caused) or "없다"} |')
    o.append("\n이 칸이 비지 않은 arm 은 **겨냥 배분을 다시 읽어야 한다** — 그 arm 을 "
             "고치는 것이 아니라, 그 arm 이 만든 부작용을 없애는 것이 처방이다.\n")

    # --- 10. 논문 대조 ---
    o.append("\n## 10. 부록 D 대조 — 우리 Δ 가 타당한 크기인가\n")
    ours: dict[str, float | None] = {}
    prev = None
    for arm in ladder:
        r = arms[arm]["rate"]
        ours[arm] = None if prev is None else (r - prev) * 100
        prev = r
    for arm in loo:
        if full:
            ours[arm] = (arms[arm]["rate"] - arms[full]["rate"]) * 100
    o.append("| 우리 arm | 우리 Δ | 논문 | 논문 보고값 | 방식 |")
    o.append("|---|---|---|---|---|")
    for arm, label, paper, value, method in PAPER_EFFECTS:
        mine = ours.get(arm)
        o.append(f'| {arm} ({label}) | {"—" if mine is None else f"{mine:+.1f}%p"} '
                 f'| {paper} | {value} | {method} |')

    # --- 11. 꺾이는 지점 ---
    o.append("\n## 11. 성능/비용 곡선의 꺾이는 지점 → 제출 구성\n")
    o.append("🔴 `Δ/원` 은 **Δ 가 양수일 때만 읽는다.** Δ 와 Δ비용이 **둘 다 음수**면 몫이 "
             "양수로 나와 '효율이 좋다' 로 보이는데, 실제로는 **싸지고 못 맞히게 된 것**이다. "
             "그 칸은 수를 쓰지 않고 이름을 붙였다.\n")
    o.append("| 전이 | Δ 정답률 | Δ 비용(원) | Δ/원 | 판정 |")
    o.append("|---|---|---|---|---|")
    for lower, upper in zip(ladder, ladder[1:]):
        ra, rb = arms[lower]["rate"], arms[upper]["rate"]
        ca, cb = arms[lower]["arm_cost_won"], arms[upper]["arm_cost_won"]
        d_rate, d_cost = (rb or 0) - (ra or 0), cb - ca
        # 1원 미만의 비용 차는 0 으로 본다 — 반올림 잡음이 '정상' 과 '꺾이는 지점' 을 가르면 안 된다.
        flat_cost = abs(d_cost) < 1.0
        if d_rate < 0:
            eff = "—"
            note = ("🔴 **싸지고 못 맞힌다** — 몫을 읽으면 안 된다" if d_cost < 0
                    else "🔴 **비싸지고 못 맞힌다**")
        elif d_rate == 0:
            eff = "—"
            note = ("**꺾이는 지점** — Δ 0 · 한계 비용 0" if flat_cost
                    else "Δ 0 인데 비용은 움직였다 — 뺄 후보")
        elif flat_cost:
            eff, note = "∞", "**공짜 이득** — 비용 0 에 정답률 상승"
        else:
            eff = f'{d_rate * 100 / d_cost:+.3f}'
            note = "정상" if d_cost > 0 else "**공짜 이득** — 싸지고 더 맞힌다"
        o.append(f'| {lower} → {upper} | {_pp(ra, rb)} | {d_cost:+,.0f} | {eff} | {note} |')
    o.append("\n**꺾이는 지점은 `A5`** — 정답률 Δ 가 여기서 0 이 되고 A6·A7·A8 은 그리드에서 "
             "0 이다. 그럼에도 **셋을 다 넣는다**: 세 arm 모두 그리드에서 **한계 비용이 0** "
             "이고(A6·A7 은 LLM 0회, A8 은 발화 0건 → 토큰 0 증가), A8 은 밸류체인 트랙에서 "
             "**+16.7%p** 를 번다 (§6). A2 를 뺀 기준(\"Δ 0 이면 뺀다\")이 여기 오지 않는 "
             "이유는 A2 가 **모든 질의에** 임베딩 비용을 물렸던 것과 달리 A8 은 발주처를 "
             "지목한 질의에서만 움직이기 때문이다.\n")
    o.append("**뺄 것은 여전히 `A0`·`A1` 방향이다.** `A0→A1` 이 v1 의 +2.8%p 에서 "
             "**−8.3%p 로 뒤집혔고**, 골든 근거 적중도 60/118 → 39/118 로 같이 떨어진다 — "
             "exchange 인덱스만 보면 periodic·major·holding 을 통째로 잃는다 (§I-1).\n")

    # --- 12. measured 42 — v1 과 비교 가능한 유일한 축 ---
    o.append("\n## 12. `measured 42` 코호트 — v1 과 비교 가능한 유일한 축\n")
    o.append("🔴 **이것은 재생(replay)이다.** Task 7 의 measured 42 는 답변 42/42 가 A7 과 "
             "바이트 동일이고 토큰 167,914 가 전 항목 일치했다. 온도 0.0 하에 타당하나 "
             "**'현재 시스템의 독립 측정' 이 아니다** — 새로 얻은 정보는 '현재 코드가 그 "
             "42문항에 같은 요청을 만든다' 는 사실뿐이다.\n")
    try:
        from scripts.validate_evalset import measured_dev_cohort
        wanted = set(measured_dev_cohort())
    except Exception:                                     # noqa: BLE001
        wanted = set()
    o.append("🔴 **'v1 42문항' 이라는 이름의 기준선이 둘 있다 — 섞으면 Δ 가 2배로 부푼다.**\n")
    o.append("| 출처 | 무엇 | grid | 이 표에서 |")
    o.append("|---|---|---|---|")
    ph_path = ROOT / "artifacts" / "eval" / "error_analysis.json"
    if ph_path.exists():
        ph = json.loads(ph_path.read_text(encoding="utf-8"))
        g = ph["crosstab"]["grid_overall"]
        o.append(f'| `artifacts/eval/error_analysis.json` | **Phase H v1** — 체인 hop · '
                 f'스키마 필드가 붙기 **전** | {g["correct"]}/{g["n_scored"]} '
                 f'({_pct(g["rate"])}) | 쓰지 않는다 |')
    v1 = _load_report(ROOT / "artifacts" / "ablation", "A6")
    if v1:
        g = v1["crosstab"]["grid_overall"]
        o.append(f'| `artifacts/ablation/A6/report.json` | **Ablation v1 A6** — 사다리의 '
                 f'그때 완성 구성 | {g["correct"]}/{g["n_scored"]} ({_pct(g["rate"])}) '
                 f'| ✅ **이것과 비교한다** |')
    o.append("\nT7 이 보고한 `+38.9%p` 는 위쪽(Phase H v1, 19/36)과의 차이다 — 그것은 "
             "**시스템 전체가 좋아진 폭**이다. 아래 표가 재는 것은 **같은 사다리의 완성 "
             "구성끼리**(Ablation v1 A6 vs v2 A8) 이고, 두 수를 더하거나 섞으면 안 된다.\n")
    if wanted and full:
        o.append("| 구성 | 코호트 | 정답 / 채점 | 정답률 | Δ(v1 A6) |")
        o.append("|---|---|---|---|---|")
        base_rate = v1["crosstab"]["grid_overall"]["rate"] if v1 else None
        if v1:
            g = v1["crosstab"]["grid_overall"]
            o.append(f'| **Ablation v1 A6** (2026-08-09) | measured 42 '
                     f'| {g["correct"]}/{g["n_scored"]} | {_pct(g["rate"])} | — |')
        for arm in ladder:
            r = _grid_rate(reports[arm]["rows"], wanted)
            o.append(f'| v2 {arm} | measured 42 | {r["correct"]}/{r["n_scored"]} '
                     f'| {_pct(r["rate"])} | {_pp(base_rate, r["rate"])} |')

    # --- 13. 읽을 때 주의 ---
    o.append("\n## 13. 이 표를 읽을 때 주의할 것\n")
    o.append("- **`Δ(A5→A6)` · `Δ(A6→A7)` · `Δ(A7→A8)` 은 88문항 그리드에서 정의상 0 이다.** "
             "A6(Verifier)은 경고만 붙이고 답을 갈아엎지 않으며(LLM 0회), A7 은 플래그가 "
             "없고, A8 의 발주처 hop 은 그리드 88문항에서 발화가 0건이다. 셋 다 "
             "**생성 프롬프트가 A5 와 바이트 동일**임을 LLM 0회로 확인했다.\n")
    o.append("- `DATA_LIMIT` 은 정답률 분모에서 뺀다. 정보한계 문항은 `answerable=false` 로 "
             "따로 세고, 지어내면 `HALLUCINATION` 으로 잡는다 (§4).\n")
    o.append("- 근본 원인 히스토그램은 **그리드 기준**이다 (`root_cause_histogram_grid`). "
             "전체 88 기준을 쓰면 회귀 트랙이 섞여 겨냥 건수가 부푼다.\n")
    o.append("- **A2·A3 는 v1 에서 실측으로 제외했다** — A2 는 dense 가 top_k 에 31건을 "
             "새로 올렸는데 골든 0건, A3 는 겨냥점 1건. 다만 **A3 겨냥점의 종류가 바뀌었다**: "
             "v1 의 `WRONG_SECTION` 은 해소됐고, 신규 섹터에서 `RANKING/LOW_RANK` "
             "(Q-094, rank 8·9) 가 새로 섰다.\n")
    return "\n".join(o) + "\n"


def write_gate_report(root: Path, budget_cap: float) -> None:
    root.mkdir(parents=True, exist_ok=True)
    text = render_gate(root, budget_cap)
    (root / "gate_I.md").write_text(text, encoding="utf-8")
    print(text)
    print(f'\n-> {root / "gate_I.md"}')


# --- CLI ----------------------------------------------------------------------

def run_retrieval(args) -> None:
    """🔴 크레딧 규칙: 생성 0회. 임베딩만 실측 usage 로 집계한다."""
    from argus.embed import USAGE

    snapshot = args.snapshot or (SNAPSHOT_PATH if SNAPSHOT_PATH.exists() else "")
    cases = load_dev(snapshot)
    if args.only:
        wanted = {q.strip() for q in args.only.split(",") if q.strip()}
        cases = [c for c in cases if c["question_id"] in wanted]
    arms = [a.strip() for a in args.retrieval.split(",") if a.strip()]
    USAGE.limit_won = args.budget_won

    base = OUT_DIR / "A2"
    base.mkdir(parents=True, exist_ok=True)
    reports: dict[str, dict] = {}
    for arm in arms:
        started = time.time()
        reports[arm] = retrieval_metrics(arm, cases)
        total = reports[arm]["totals"]
        print(f'  [{arm}] 골든 근거 {total["evidence_hit"]}/{total["golden"]} '
              f'· Recall@5 {total["recall5"]}/{total["golden"]} '
              f'· dense 호출 {total["dense_calls"]} 신규벡터 {total["dense_new"]} '
              f'· 임베딩 누적 {USAGE.won:,.1f}원 · {time.time() - started:.0f}s')

    diffs = []
    for pair in (("A6", "A2F"), ("A1b", "A2")):
        if pair[0] in reports and pair[1] in reports:
            diffs.append(diff_retrieval(reports[pair[0]], reports[pair[1]]))
    meta = {"generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "n_cases": len(cases),
            "snapshot_sha": (json.loads(Path(snapshot).read_text(encoding="utf-8"))
                             ["sha256_of_joined_ids"] if snapshot else "-"),
            "index_snapshot": index_snapshot(),
            "embed_usage": USAGE.totals()}
    (base / "retrieval.json").write_text(
        json.dumps({"meta": meta, "reports": reports, "diffs": diffs},
                   ensure_ascii=False, indent=1), encoding="utf-8")
    (base / "retrieval.md").write_text(
        render_retrieval(reports, diffs, meta), encoding="utf-8")
    print(f'\n임베딩 실측 {USAGE.tokens:,}토큰 = {USAGE.won:,.1f}원 · 생성 0회')
    for diff in diffs:
        moved = {k: v for k, v in diff["delta"].items() if v}
        print(f'  {diff["arm"]} vs {diff["base"]}: '
              f'{moved or "Δ 전부 0"} · 변화 문항 {len(diff["changed_questions"])}건')
    print(f'-> {base / "retrieval.md"}')


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase I ablation (dev 전용)")
    parser.add_argument("--arm", "--arms", dest="arm", default="",
                        help="쉼표 구분 arm 이름. 비면 실행 안 함")
    parser.add_argument("--run", action="store_true", help="RUN_ORDER 전부 실행")
    parser.add_argument("--only", default="", help="쉼표 구분 question_id 만")
    parser.add_argument("--dry", action="store_true", help="LLM 0회 리허설")
    parser.add_argument("--offline", action="store_true", help="요청 캐시 미적중 시 중단")
    parser.add_argument("--force", action="store_true", help="응답 파일 캐시 무시")
    parser.add_argument("--heldout", action="store_true",
                        help="🔴 heldout 18문항을 최종 구성으로 1회 실행")
    parser.add_argument("--retrieval", default="",
                        help="검색 지표만 LLM 0회로 잰다. 쉼표 구분 arm (기준선을 앞에)")
    parser.add_argument("--snapshot", default="",
                        help="평가셋 스냅샷 json. 비면 questions.jsonl 전체")
    parser.add_argument("--cohort", default="", choices=("",) + ea.COHORTS,
                        help="measured=v1 이 잰 42문항 · full=dev 78+회귀 10 · valuechain=12")
    parser.add_argument("--out", default="",
                        help="arm 폴더의 부모. 비면 artifacts/ablation (v1 자리)")
    parser.add_argument("--report", action="store_true",
                        help="게이트 I 보고서만 생성한다 (LLM 0회 · 크레딧 0원)")
    parser.add_argument("--budget-won", "--budget", dest="budget_won", type=float,
                        default=None,
                        help="상한(원). 러너는 그 80%%(=정지선)에서 멈춘다")
    args = parser.parse_args()

    global OUT_DIR
    if args.out:
        OUT_DIR = Path(args.out) if Path(args.out).is_absolute() else ROOT / args.out

    # 🔴 상한과 정지선을 가른다. 브리프의 규칙은 "상한 N, 80% 에서 중단" 이므로
    #    러너에 넘기는 값은 정지선이고, 보고에 쓰는 값은 상한이다.
    budget_cap = args.budget_won if args.budget_won else BUDGET_WON
    stop_won = round(budget_cap * 0.8, 1)

    if args.report:
        write_gate_report(OUT_DIR, budget_cap)
        return

    if args.retrieval:
        run_retrieval(args)
        return

    if args.dry:
        # 리허설 응답을 본 실행 캐시에 섞으면 가짜 답이 영원히 굳는다. 폴더부터 가른다.
        OUT_DIR = ROOT / "artifacts" / "ablation_dry"
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    cache = RequestCache(CACHE_DIR, offline=args.offline)
    if args.dry:
        install_dry_stub()
    else:
        cache.install()

    records = ea._load_records()
    snapshot = index_snapshot()
    (OUT_DIR / "index_snapshot.json").write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=1), encoding="utf-8")

    if args.heldout:
        raise SystemExit("🔴 heldout 은 이 트랙(Task 9)의 몫이 아닙니다 — Task 10 이 엽니다.")

    cases = load_cases(args.cohort, args.snapshot)
    if args.only:
        wanted = {q.strip() for q in args.only.split(",") if q.strip()}
        cases = [c for c in cases if c["question_id"] in wanted]

    targets = []
    if args.run:
        targets = list(ablation.RUN_ORDER)
    elif args.arm:
        targets = [a.strip() for a in args.arm.split(",") if a.strip()]

    live: dict[str, dict] = {}
    for arm in targets:
        if cache.fresh_won > stop_won:
            print(f"\n🔴 정지선 도달 — {arm} 이하를 실행하지 않습니다")
            break
        print(f"\n=== {arm} · {len(cases)}문항 · 누적 {cache.fresh_won:,.0f}원 "
              f"/ 정지선 {stop_won:,.0f}원 (상한 {budget_cap:,.0f}) ===")
        live[arm] = run_arm(arm, cases, cache, stop_won, args.force)
        (arm_dir(arm) / "run.json").write_text(
            json.dumps(live[arm], ensure_ascii=False, indent=1), encoding="utf-8")

    scored_cases = load_cases(args.cohort, args.snapshot)
    reports = {}
    for arm in ablation.RUN_ORDER:
        if (arm_dir(arm) / "responses").exists():
            reports[arm] = score_arm(arm, scored_cases, records)

    summary = summarise(reports)
    meta = {"generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "cohort": args.cohort or "dev",
            "n_cases": len(scored_cases),
            "question_ids": [c["question_id"] for c in scored_cases],
            "spent_won": round(cache.fresh_won, 1),
            "budget_won": budget_cap, "stop_won": stop_won,
            "cache_hits": cache.hits, "cache_misses": cache.misses,
            "fresh_prompt_tokens": cache.fresh_prompt,
            "fresh_completion_tokens": cache.fresh_completion,
            "price_won_per_1k": ea.PRICE_WON_PER_1K,
            "index_snapshot": snapshot, "this_run": live}
    (OUT_DIR / "summary.json").write_text(
        json.dumps({"meta": meta, "summary": summary}, ensure_ascii=False, indent=1),
        encoding="utf-8")
    (OUT_DIR / "summary.md").write_text(render_summary(summary, meta), encoding="utf-8")

    print(f"\n실지출 {cache.fresh_won:,.1f}원 · 캐시 적중 {cache.hits} / 미적중 {cache.misses}")
    for arm, row in summary["arms"].items():
        print(f'  {arm:<4} {row["correct"]:>2}/{row["n_scored"]:<2} '
              f'rate={row["rate"]} tokens={row["tokens"]:,} arm_cost={row["arm_cost_won"]:,.1f}원')
    print(f'-> {OUT_DIR / "summary.md"}')


if __name__ == "__main__":
    main()
