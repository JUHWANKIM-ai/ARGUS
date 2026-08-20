"""Phase I, 게이트 I 6항목 보고서 생성. **LLM 0회.**

`scripts/ablation.py` 가 남긴 arm 별 report.json 만 읽어 표를 만든다. 여기서 크레딧을
쓰면 보고서를 고칠 때마다 돈이 나간다. 집계는 공짜여야 한다.

    python scripts/ablation_report.py
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from argus import ablation                                        # noqa: E402
from scripts import error_analysis as ea                          # noqa: E402
#: **정밀도 규칙은 한 곳에서만 정한다** (`scripts/ablation._exact_rate`). 여기서 같은
#: 규칙을 다시 적으면 서식지가 둘이 되고, 실제로 그렇게 이 스크립트가 v1 표 전량에서
#: 이중 반올림을 냈다. `report.json` 의 `rate` 는 이미 소수 셋째 자리 반올림값인데
#: 그것을 서로 빼서 Δ 를 만들었다.
from scripts.ablation import _exact_rate as exact_rate            # noqa: E402

OUT = ROOT / "scratch" / "ablation"

#: **v1 세션의 실제 결제액. 재현 불가라서 상수로 박는다.**
#:
#: `spend_from_cache()` 는 `scratch/ablation/cache/` **전부**를 더한다. 그 캐시는
#: `--out` 으로 갈리지 않는 **공용 캐시**이고(설계 의도대로, 같은 프롬프트를 두 번 사지
#: 않으려면 공유돼야 한다), v1 게이트를 쓴 시점엔 309건이었으나 지금은 985건이다
#: (v2 · vc · heldout · T7 이 함께 들어 있다). 즉 **같은 함수가 시간이 지나면 다른 수를
#: 낸다**. 지금 재생성하면 3,708.0 → 11,524.2원, "상한 12,000원 대비 30.9%" 가
#: **"96.0%"** 로 뛴다. 문서가 인용하는 값이라(`docs/total_plan.md`) 조용히 틀리면
#: 제출물이 크레딧을 거의 다 쓴 것처럼 보인다.
#:
#: 그래서 v1 의 지출은 **측정 시점의 값을 상수로 고정**한다. 이 수는 그때의 캐시 상태에
#: 의존하는 관측값이고 그 상태는 이제 존재하지 않는다. 재계산으로 되살릴 수 없다.
#: **원 단위 값이 아니라 실측 usage 를 고정한다.** 원 값을 박으면 표시 반올림
#: (`3,708`)만 남아 `arm_sum − 지출` 이 1원 어긋난다(4,873 → 4,872). 토큰을 고정하면
#: 단가와 함께 원래 값(3,707.73936원)이 그대로 복원되고, 커밋된 v1 `gate_I.md` 가
#: **숫자 하나까지 재현된다.** 출처는 그 문서의 §1 행이다.
V1_PAID_REQUESTS = 309
V1_PAID_PROMPT_TOKENS = 493_629
V1_PAID_COMPLETION_TOKENS = 34_539
V1_PAID_WON = ea.cost_won(V1_PAID_PROMPT_TOKENS, V1_PAID_COMPLETION_TOKENS)
V1_PAID_MEASURED = "2026-08-09 (요청 캐시 309건 시점)"
LADDER = ("A0", "A1", "A1b", "A4", "A5", "A6")
LOO = ("L1", "L2")
AXES = (("difficulty", "난이도", ("하", "중", "상")),
        ("answer_mode", "답변형식", ("closed", "open")),
        ("task_type", "과제유형", ("search_extract", "multi_compare", "complex_reason")))

#: Phase H 관측, 세 난이도가 전부 1/2 였다. 이 행의 Δ 는 읽지 않는다.
BLIND_CELL = "multi_compare|closed"

#: 부록 D 의 논문 보고값. 우리 Δ 가 타당한 크기인지 판정하는 기준이다.
PAPER_EFFECTS = (
    ("A0 (메타 프리필터)", "우리 실측 Q-001", "14/1,332 → 1/1", "—"),
    ("A1 (BM25)", "G-Retriever w/o retrieval", "-9.4%", "제거"),
    ("A1b (인덱스 커버리지)", "ToG-2 vs Vanilla RAG (ToG-FinQA)", "0% → 34.0%", "조건 비교"),
    ("A4 (Agent 루프)", "ToG-2 → ToG-2 (AdvHotpotQA)", "+16.6%", "추가"),
    ("A4 (Agent 루프)", "HippoRAG + IRCoT (2Wiki R@5)", "+18%", "추가"),
    ("A5 (계산 Tool)", "— (해당 논문 없음, policy §4)", "—", "—"),
    ("A6 (Verifier)", "Self-Consistency (GSM8K)", "+17.9%", "추가"),
    ("A6 (Verifier)", "SWE-Lancer 판단 vs 생성", "44.9% vs 26.2%", "조건 비교"),
    ("L1 (체인 hop 제거)", "Relink w/o query-driven ranker", "-19.4% EM", "제거"),
    ("L2 (major·holding 필드 제거)", "G-Retriever w/o textualized graph", "-19.2%", "제거"),
)


def load(arm: str) -> dict | None:
    path = OUT / arm / "report.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def rate(report: dict, axis: str, value: str) -> dict:
    return report["crosstab"]["marginals"][axis][value]


def cell(report: dict, key: str) -> dict:
    return report["crosstab"]["cells"][key]


def spend_from_cache() -> dict:
    """**공용 캐시 전체(모든 판의 합)**. v1 의 지출이 아니다.

    요청 캐시 파일 1개 = 실제로 산 요청 1건이다. 그래서 v1 세션 직후에는 이 합이 곧 v1 의
    실제 결제액이었다(309건 = 3,708.0원). 그런데 이 캐시는 `--out` 으로 갈리지 않으므로
    v2 · vc · heldout · T7 이 같은 폴더에 쌓였고, 지금 이 함수는 **985건 = 11,524.2원**을
    돌려준다. 함수는 안 바뀌었는데 답이 바뀌었다. 입력이 공용이라서다.

    그러므로 이 값을 **"v1 이 쓴 돈" 이나 "상한 대비 사용률" 로 읽지 않는다.**
    v1 의 지출은 `V1_PAID_WON` 에 고정돼 있다.
    """
    prompt = completion = files = 0
    for path in (OUT / "cache").glob("*.json"):
        usage = (json.loads(path.read_text(encoding="utf-8"))["payload"]
                 .get("result") or {}).get("usage") or {}
        prompt += usage.get("promptTokens", 0)
        completion += usage.get("completionTokens", 0)
        files += 1
    return {"requests": files, "prompt_tokens": prompt, "completion_tokens": completion,
            "total_tokens": prompt + completion,
            "won": round(ea.cost_won(prompt, completion), 1)}


def pct(value) -> str:
    return "—" if value is None else f"{value * 100:.1f}%"


def delta(before, after) -> str:
    if before is None or after is None:
        return "—"
    return f"{(after - before) * 100:+.1f}%p"


def render() -> str:
    """게이트 I(v1) 본문. **파일을 쓰지 않는다**. 렌더와 쓰기를 갈라 두면 산출물을
    덮지 않고 결과를 검사할 수 있다 (`main()` 이 쓴다)."""
    reports = {a: load(a) for a in LADDER + LOO}
    reports = {k: v for k, v in reports.items() if v}
    heldout = load("A6_heldout_run")
    spend = spend_from_cache()
    out: list[str] = []

    out.append("# Phase I — Ablation v1 결과\n")
    out.append(f'생성 {time.strftime("%Y-%m-%d %H:%M")} · 자동 생성물 '
               "(`scripts/ablation_report.py`) · LLM 0회\n")
    out.append(f'실측 단가 {ea.PRICE_WON_PER_1K}원/1K · dev 42문항 · 모델 HCX-005 · 온도 0.0\n')

    # --- 1. 비용 ---
    out.append("\n## 1. arm 별 비용 실측\n")
    out.append("`arm 비용` = 그 arm 을 혼자 돌렸을 때의 실측 usage 합. "
               "`신규 지출` = 요청 캐시가 못 막아 실제로 결제된 몫.\n")
    out.append("| arm | LLM 호출 | prompt | completion | judge | 총 토큰 | arm 비용(원) |")
    out.append("|---|---|---|---|---|---|---|")
    for arm in LADDER + LOO:
        if arm not in reports:
            continue
        t = reports[arm]["totals"]
        out.append(f'| {arm} | {t["llm_calls"]} | {t["prompt_tokens"]:,} '
                   f'| {t["completion_tokens"]:,} | {t["judge_tokens"]:,} '
                   f'| {t["total_tokens"]:,} | {t["cost_won"]:,.1f} |')
    if heldout:
        t = heldout["totals"]
        out.append(f'| A6/heldout (18문항) | {t["llm_calls"]} | {t["prompt_tokens"]:,} '
                   f'| {t["completion_tokens"]:,} | {t["judge_tokens"]:,} '
                   f'| {t["total_tokens"]:,} | {t["cost_won"]:,.1f} |')
    arm_sum = sum(reports[a]["totals"]["cost_won"] for a in reports)
    arm_sum += heldout["totals"]["cost_won"] if heldout else 0
    out.append(f'| **합계 (캐시 없었다면)** | | | | | | **{arm_sum:,.0f}** |')
    out.append(f'| **v1 실제 결제 (측정 시점 고정값)** | {V1_PAID_REQUESTS} '
               f'| {V1_PAID_PROMPT_TOKENS:,} | {V1_PAID_COMPLETION_TOKENS:,} | '
               f'| {V1_PAID_PROMPT_TOKENS + V1_PAID_COMPLETION_TOKENS:,} '
               f'| **{V1_PAID_WON:,.0f}** |')
    out.append(f'| 참고 · 공용 캐시 전체 (v1·v2·vc·heldout·T7 합 — **v1 지출이 아니다**) '
               f'| {spend["requests"]} | {spend["prompt_tokens"]:,} '
               f'| {spend["completion_tokens"]:,} | | {spend["total_tokens"]:,} '
               f'| {spend["won"]:,.0f} |')
    out.append(f'\n상한 12,000원 대비 **{V1_PAID_WON / 12000 * 100:.1f}%** 사용 '
               f'(80% 정지선 9,600원 미도달). 캐시 전체가 '
               f'{arm_sum - V1_PAID_WON:,.0f}원을 막았다 — 요청 캐시만이 아니라 '
               "**응답 파일 캐시와 프롬프트 동일성까지 합친** 절감액이다.\n")
    # 두 수가 왜 다른지를 표 옆에 적어 둔다. 이 스크립트는 v1 `gate_I.md` 를 재생성하는
    #    유일한 수단이라 언제든 다시 돌려지고, 그때 큰 쪽이 "v1 지출" 로 읽히면
    #    "예산 96% 소진" 이라는 틀린 서술이 남는다.
    out.append(f'🔴 **아래 두 수를 섞지 않는다.** v1 실제 결제 '
               f'**{V1_PAID_WON:,.1f}원({V1_PAID_REQUESTS}요청)** 은 '
               f'{V1_PAID_MEASURED} 의 관측값이고 `scripts/ablation_report.V1_PAID_WON` '
               "에 **상수로 고정**돼 있다. 요청 캐시는 `--out` 으로 갈리지 않는 공용 "
               f'캐시라, 지금 다시 세면 {spend["requests"]}건 · '
               f'{spend["won"]:,.1f}원(= 상한의 {spend["won"] / 12000 * 100:.1f}%)이 나온다 '
               "— 그 안에 v2·vc·heldout·T7 이 섞여 있다. **v1 의 지출은 재계산으로 "
               "되살릴 수 없다**(그때의 캐시 상태가 이제 없다). "
               "v2 판의 지출은 `scratch/ablation/v2/gate_I.md` §1 이 arm 별 "
               "`run.json` 합으로 따로 낸다.\n")

    # --- 2. 누적 곡선 ---
    out.append("\n## 2. 누적 곡선 — A0 → A6\n")
    out.append("정답률은 **채점 대상 36문항**(정보한계 6문항 분리) 기준. "
               "`DATA_LIMIT` 은 이번 실행에서 0건이다.\n")
    out.append("| arm | 정답/36 | 정답률 | Δ(직전) | 골든 근거 적중 | 전건 적중 문항 | "
               "arm 비용(원) | Δ정답률/Δ비용 |")
    out.append("|---|---|---|---|---|---|---|---|")
    previous = None
    prev_cost = None
    for arm in LADDER:
        if arm not in reports:
            continue
        report = reports[arm]
        grid = report["crosstab"]["grid_overall"]
        hit = sum(len(r["golden_in_evidence"]) for r in report["rows"])
        golden = sum(len(r["golden"]) for r in report["rows"])
        allhit = sum(1 for r in report["rows"] if r["golden_evidence_all"])
        cost = report["totals"]["cost_won"]
        # Δ 는 원 분자/분모에서 계산한다 (`exact_rate`), `grid["rate"]` 는 이미
        #    소수 셋째 자리 반올림값이라 서로 빼면 이중 반올림이 된다.
        #    이름을 `rate` 로 두면 모듈의 `rate(report, axis, value)` 함수를 가린다.
        grid_rate = exact_rate(grid)
        step = delta(previous, grid_rate)
        eff = "—"
        if previous is not None and prev_cost is not None and cost != prev_cost:
            eff = f"{(grid_rate - previous) * 100 / (cost - prev_cost):+.3f}%p/원"
        out.append(f'| **{arm}** | {grid["correct"]}/36 | {pct(grid_rate)} | {step} '
                   f'| {hit}/{golden} | {allhit}/42 | {cost:,.0f} | {eff} |')
        previous, prev_cost = grid_rate, cost

    out.append("\n### 정보한계 6문항 (거절이 정답)\n")
    out.append("| arm | 거절 정답 | HALLUCINATION |")
    out.append("|---|---|---|")
    for arm in LADDER:
        if arm not in reports:
            continue
        info = reports[arm]["crosstab"]["info_limit"]
        out.append(f'| {arm} | {info["correct"]}/6 | '
                   f'{info["stages"].get(ea.HALLUCINATION, 0)} |')

    # --- 3. 축별 분해 ---
    out.append("\n## 3. 축별 Δ 분해\n")
    out.append("전체 Δ 하나만 보면 '왜 올랐는지' 가 사라진다. 축마다 다르게 움직인다.\n")
    for axis, label, values in AXES:
        out.append(f"\n### {label}\n")
        arms = [a for a in LADDER if a in reports]
        out.append("| 값 | " + " | ".join(arms) + " | Δ A0→A6 | 최대 단일 Δ |")
        out.append("|---|" + "---|" * (len(arms) + 2))
        for value in values:
            cells, rates = [], []
            for arm in arms:
                r = rate(reports[arm], axis, value)
                cells.append(f'{r["correct"]}/{r["n_scored"]}')
                rates.append(exact_rate(r))
            steps = [(rates[i + 1] - rates[i], arms[i + 1]) for i in range(len(rates) - 1)
                     if rates[i] is not None and rates[i + 1] is not None]
            best = max(steps, key=lambda s: s[0]) if steps else None
            out.append(f'| {value} | ' + " | ".join(cells)
                       + f' | {delta(rates[0], rates[-1])} | '
                       + (f'{best[1]} {best[0] * 100:+.1f}%p |' if best else "— |"))

    out.append(f"\n### ⚠ 변별력 경고 — `{BLIND_CELL}`\n")
    out.append("Phase H 에서 이 셀은 세 난이도가 전부 1/2 였다. 이번 실행값은 아래와 같으나 "
               "**이 행의 Δ 는 읽지 않는다** (표본 2, 상한 효과).\n")
    out.append("| arm | 하 | 중 | 상 |")
    out.append("|---|---|---|---|")
    for arm in [a for a in LADDER if a in reports]:
        cells = [cell(reports[arm], f"{BLIND_CELL}|{d}") for d in ("하", "중", "상")]
        out.append(f'| {arm} | ' + " | ".join(f'{c["correct"]}/{c["n_scored"]}'
                                              for c in cells) + " |")

    # --- 4. LOO ---
    out.append("\n## 4. Leave-one-out — 완성 구성에서의 필수성\n")
    base = reports["A6"]["crosstab"]["grid_overall"]
    out.append("| 구성 | 뺀 것 | 정답/36 | 정답률 | Δ(A6) | 골든 근거 적중 | 비고 |")
    out.append("|---|---|---|---|---|---|---|")
    hit6 = sum(len(r["golden_in_evidence"]) for r in reports["A6"]["rows"])
    base_rate = exact_rate(base)
    out.append(f'| A6 | — | {base["correct"]}/36 | {pct(base_rate)} | — | {hit6}/60 | 기준 |')
    note = {"L1": "Phase H 병목 1위 (실패의 41%)", "L2": "Phase H 병목 2위 (실패의 24%)"}
    removed = {"L1": "정정체인 hop", "L2": "major·holding 필드"}
    for arm in LOO:
        if arm not in reports:
            continue
        grid = reports[arm]["crosstab"]["grid_overall"]
        hit = sum(len(r["golden_in_evidence"]) for r in reports[arm]["rows"])
        out.append(f'| {arm} | {removed[arm]} | {grid["correct"]}/36 '
                   f'| {pct(exact_rate(grid))} '
                   f'| {delta(base_rate, exact_rate(grid))} | {hit}/60 | {note[arm]} |')

    out.append("\n### LOO 축별 분해\n")
    for axis, label, values in AXES:
        out.append(f"\n**{label}**\n")
        out.append("| 값 | A6 | L1 | ΔL1 | L2 | ΔL2 |")
        out.append("|---|---|---|---|---|---|")
        for value in values:
            b = rate(reports["A6"], axis, value)
            row = [f'{b["correct"]}/{b["n_scored"]}']
            for arm in LOO:
                r = rate(reports[arm], axis, value)
                row += [f'{r["correct"]}/{r["n_scored"]}',
                        delta(exact_rate(b), exact_rate(r))]
            out.append(f'| {value} | ' + " | ".join(row) + " |")

    # --- 5. 실패 분포 ---
    out.append("\n## 5. 실패 단계 · 근본 원인 분포\n")
    labels = [s for s in ea.ALL_LABELS]
    out.append("| arm | " + " | ".join(labels) + " |")
    out.append("|---|" + "---|" * len(labels))
    for arm in LADDER + LOO:
        if arm not in reports:
            continue
        hist = reports[arm]["failure_histogram"]
        out.append(f"| {arm} | " + " | ".join(str(hist.get(s, 0)) for s in labels) + " |")

    out.append("\n### A6 잔여 실패 (근본 원인)\n")
    out.append("| 문항 | 단계 | 원인 | 내용 |")
    out.append("|---|---|---|---|")
    for row in reports["A6"]["rows"]:
        if row["first_failure"]:
            out.append(f'| {row["question_id"]} | {row["first_failure"]} '
                       f'| {row["root_cause"]} | {row["failure_why"][:70]} |')

    # --- 6. 논문 대조 ---
    out.append("\n## 6. 부록 D 대조 — 우리 Δ 가 타당한 크기인가\n")
    ours = {}
    prev = None
    for arm in LADDER:
        if arm not in reports:
            continue
        r = exact_rate(reports[arm]["crosstab"]["grid_overall"])
        ours[arm] = None if prev is None else (r - prev) * 100
        prev = r
    a6 = exact_rate(reports["A6"]["crosstab"]["grid_overall"])
    for arm in LOO:
        if arm in reports:
            ours[arm] = (exact_rate(reports[arm]["crosstab"]["grid_overall"]) - a6) * 100
    out.append("| 우리 arm | 우리 Δ | 논문 | 논문 보고값 | 방식 |")
    out.append("|---|---|---|---|---|")
    for label, paper, value, method in PAPER_EFFECTS:
        key = label.split()[0]
        mine = ours.get(key)
        out.append(f'| {label} | {"—" if mine is None else f"{mine:+.1f}%p"} '
                   f'| {paper} | {value} | {method} |')

    # --- 7. arm 사이에서 뒤집힌 문항 ---
    out.append("\n## 7. arm 사이에서 뒤집힌 문항 — Δ 가 어느 문항에서 왔는가\n")
    out.append("평균만 보면 Δ 가 '어디서' 왔는지 사라진다. 문항 단위로 짚는다.\n")
    out.append("| 전이 | 새로 맞힌 문항 | 잃은 문항 |")
    out.append("|---|---|---|")
    pairs = [(LADDER[i], LADDER[i + 1]) for i in range(len(LADDER) - 1)]
    pairs += [("A6", "L1"), ("A6", "L2")]
    for lower, upper in pairs:
        if lower not in reports or upper not in reports:
            continue
        a = {r["question_id"]: r["correct"] for r in reports[lower]["rows"]}
        b = {r["question_id"]: r["correct"] for r in reports[upper]["rows"]}
        gained = sorted(q for q in a if b.get(q) and not a[q])
        lost = sorted(q for q in a if a[q] and not b.get(q))
        out.append(f'| {lower} → {upper} | {" ".join(gained) or "—"} '
                   f'| {" ".join(lost) or "—"} |')

    # --- 8. 꺾이는 지점 ---
    out.append("\n## 8. 성능/비용 곡선의 꺾이는 지점 → 제출 구성\n")
    out.append("| 전이 | Δ 정답률 | Δ 비용(원) | 판정 |")
    out.append("|---|---|---|---|")
    verdicts = {
        ("A0", "A1"): "**사실상 0** — 정답률 +2.8%p 는 1문항(Q-045)이고, 골든 근거 적중은 "
                      "26/60 → 18/60 으로 **떨어졌다**. 랭킹을 얻고 커버리지를 잃었다 (§I-1)",
        ("A1", "A1b"): "**최대 기여** — 커버리지가 랭킹보다 먼저다",
        ("A1b", "A4"): "**두 번째 기여** — 검색이 아니라 '한 번 더 물어보기' 가 벌었다",
        ("A4", "A5"): "**공짜 이득** — 정답률은 오르고 비용은 줄었다",
        ("A5", "A6"): "**꺾이는 지점** — 정답률 Δ 가 0 이 된다. 비용도 0 (LLM 0회)",
    }
    for lower, upper in pairs[:len(LADDER) - 1]:
        if lower not in reports or upper not in reports:
            continue
        ra = exact_rate(reports[lower]["crosstab"]["grid_overall"])
        rb = exact_rate(reports[upper]["crosstab"]["grid_overall"])
        ca = reports[lower]["totals"]["cost_won"]
        cb = reports[upper]["totals"]["cost_won"]
        out.append(f'| {lower} → {upper} | {(rb - ra) * 100:+.1f}%p | {cb - ca:+,.0f} '
                   f'| {verdicts.get((lower, upper), "—")} |')

    # --- 9. Verifier 검출력 ---
    if "A6" in reports:
        fired = [(r["question_id"], r["correct"]) for r in reports["A6"]["rows"]
                 if "ok=False" in r["think_trace"]]
        wrong = [r["question_id"] for r in reports["A6"]["rows"] if not r["correct"]]
        tp = [q for q, ok in fired if not ok]
        fp = [q for q, ok in fired if ok]
        out.append("\n## 9. A6 Verifier 검출력 — 정답률 Δ 가 0 이어도 재는 값이 있다\n")
        out.append("| 항목 | 값 |")
        out.append("|---|---|")
        out.append(f'| 발화 | {len(fired)}건 ({" ".join(q for q, _ in fired)}) |')
        out.append(f'| 적중 (오답에 발화) | {len(tp)} / {len(wrong)} = '
                   f'recall {len(tp) / max(len(wrong), 1):.2f} |')
        out.append(f'| 거짓 경보 (정답에 발화) | {len(fp)} '
                   f'({" ".join(fp) or "—"}) → precision {len(tp) / max(len(fired), 1):.2f} |')
        out.append(f'| 추가 LLM 호출 | 0 (`verify_calculation`·`verify_completeness` 둘 다 결정적) |')
        out.append(f'| 정답률 영향 | 0.0%p — 경고만 붙이고 답을 갈아엎지 않는다 |')

    # --- 10. heldout ---
    if heldout:
        out.append("\n## 10. heldout 낙폭 — A6 최종 구성 1회\n")
        out.append("🔴 heldout 18문항은 이 측정 **1회**에만 열었다. arm 별로 돌리지 않았다.\n")
        dev, held = reports["A6"], heldout
        out.append("| split | 전체 | 채점 대상(answerable) | 정보한계 | 인용률 |")
        out.append("|---|---|---|---|---|")
        for name, report in (("dev (42)", dev), ("heldout (18)", held)):
            cross = report["crosstab"]
            grid, info, overall = cross["grid_overall"], cross["info_limit"], cross["overall"]
            out.append(f'| {name} | {overall["correct"]}/{overall["n_scored"]} '
                       f'({pct(exact_rate(overall))}) | {grid["correct"]}/{grid["n_scored"]} '
                       f'({pct(exact_rate(grid))}) | {info["correct"]}/{info["n"]} | '
                       f'{pct(report["citation_rate"])} |')
        # 낙폭도 원 분자/분모에서 뺀다. 이 수가 v1 게이트의 결론이다.
        gap = (exact_rate(held["crosstab"]["grid_overall"])
               - exact_rate(dev["crosstab"]["grid_overall"]))
        gap_all = (exact_rate(held["crosstab"]["overall"])
                   - exact_rate(dev["crosstab"]["overall"]))
        out.append(f'\n**낙폭 (answerable 기준) {gap * 100:+.1f}%p · '
                   f'(전체 기준) {gap_all * 100:+.1f}%p**\n')
        out.append("heldout 18문항에는 정보한계 문항이 없다. 같은 잣대로 볼 값은 "
                   "`answerable` 행이다.\n")
        out.append("\n| heldout 실패 | 단계 | 원인 |")
        out.append("|---|---|---|")
        for row in held["rows"]:
            if row["first_failure"]:
                out.append(f'| {row["question_id"]} | {row["first_failure"]} '
                           f'| {row["root_cause"]} |')

    # --- 11. 읽을 때 주의 ---
    out.append("\n## 11. 이 표를 읽을 때 주의할 것\n")
    out.append("- **A1 의 `ROUTING` 17건은 라우터 실패가 아니다.** arm 정의상 검색이 "
               "exchange 인덱스로 강제되므로 `parse_trace` 가 문서군 불일치로 읽는다 "
               "(periodic 10 · holding 4 · major 3). 실체는 **인덱스 미커버리지**이고, "
               "그것이 곧 A1b 의 Δ 다.\n")
    out.append("- **A0 의 비용이 A1b 보다 크지 않은 것에 속으면 안 된다.** A0 은 순위가 "
               "없어 근거를 통째로 싣기 때문에 토큰이 126K 로 A1(54K)의 2.3배다 — "
               "**싸지도 않고 맞히지도 못한다**.\n")
    out.append("- `multi_compare|closed` 행의 Δ 는 읽지 않는다 (§3 경고).\n")
    out.append("- `DATA_LIMIT` 은 전 arm 0건이다. 정보한계 6문항은 `answerable=false` 로 "
               "따로 세고, 지어내면 `HALLUCINATION` 으로 잡힌다.\n")

    return "\n".join(out) + "\n"


def main() -> None:
    text = render()
    (OUT / "gate_I.md").write_text(text, encoding="utf-8")
    print(text)
    print(f'\n-> {OUT / "gate_I.md"}')


if __name__ == "__main__":
    main()
