r"""한 팔을 **112문항 3회 전수**로 재고 축별로 가른다. 🔴 유료다.

## 왜 합성이 아니라 전수인가

`scratch/compose_a62.py` 는 90문항이 글자 그대로 같음을 증명하고 그 값을 서버에서 빌려
왔다. 이번 캠페인(`A68`~`A77`)은 팔이 아홉이고 그중 `strip_marker_citation` 은 **답변
후처리**라 근거가 같아도 답 문면이 바뀐다. 어느 문항이 안 바뀌는지를 아홉 겹으로 증명하는
비용이 그냥 다 사는 비용과 비슷해졌고, 합성이면 「서버 값 + 로컬 값」이라는 전제를 또
얹어야 한다. 그래서 전수로 산다.

각 팔은 자기 사정거리에서 3회씩 이미 통과했다. 이 실행이 답하는 것은 **합쳐도 그런가** 다.

Usage:
    GATE_ARM=A77 python scripts/full_run.py --rounds 3
    GATE_ARM=A77 python scripts/full_run.py --rounds 3 --start 2   # 이미 산 회차는 건너뛴다
"""
import argparse
import collections
import json
import os
import socket
import sys
import time
import urllib.error
from pathlib import Path

sys.path.insert(0, ".")
sys.path.insert(0, "src")
sys.stdout.reconfigure(encoding="utf-8")

from argus import ablation                                        # noqa: E402
from scripts import error_analysis as ea                          # noqa: E402

ARM = os.environ.get("GATE_ARM", "A77")
BASE = Path("scratch/ablation/v9")
OLD = Path("scratch/ablation/v6")
COHORTS = ("full", "valuechain", "safety")
#: 축 이름 → `error_analysis` 행의 어느 칸을 보는가. `open-issues` 문서가 쓰는 여덟 축과
#: 같은 순서를 지킨다.
AXES = (
    ("정확성", lambda r: not r.get("correct")),
    ("요구사항 충족", lambda r: not (r.get("judge") or {}).get("covers_key_points", True)),
    ("추론 논리성", lambda r: not (r.get("judge") or {}).get("grounded", True)),
    ("근거 표시(골든)", lambda r: not r.get("has_citation")),
    ("환각 없음", lambda r: not (r.get("judge") or {}).get("no_fabrication", True)),
    ("수치 검산", lambda r: not r.get("numeric_ok", True)),
    ("금액 요점", lambda r: not r.get("covers_key_amounts_ok", True)),
)


def baseline_rows() -> dict:
    """`A65` 서버 3회의 행. 회차별로 담는다."""
    out = {}
    for round_no in (1, 2, 3):
        rows = {}
        for cohort in COHORTS:
            name = "server_%s" % cohort
            if round_no > 1:
                name = "%s_r%d" % (name, round_no)
            path = OLD / name / "A65" / "report.json"
            if path.exists():
                for row in json.loads(path.read_text(encoding="utf-8"))["rows"]:
                    rows[row["question_id"]] = row
        out[round_no] = rows
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--start", type=int, default=1)
    args = ap.parse_args()

    ea._use_v2_dirs()
    cases = []
    seen = set()
    for cohort in COHORTS:
        for case in ea.load_cohort(cohort):
            if case["question_id"] not in seen:
                seen.add(case["question_id"])
                cases.append(case)
    records = ea._load_records()
    print("문항 %d개 · 팔 %s · 회차 %d~%d\n" % (len(cases), ARM, args.start, args.rounds),
          flush=True)

    #: 🔴 `ea.with_retry` 는 **429 만** 재시도한다(그 독스트링: "다른 실패는 그대로 올린다.
    #: 조용히 삼키면 안 된다"). 그 규율은 옳다 — 우리가 안 본 실패를 삼키면 성적이 조용히
    #: 거짓이 된다. 다만 **망 읽기 타임아웃**은 성적과 무관한 사고이고, 112×3 을 도는 동안
    #: 한 번만 나도 회차가 통째로 죽는다(첫 실행이 r3 91/112 에서 그렇게 죽었다).
    #: 그래서 그 한 갈래만 이 러너 안에서 되풀이한다. `with_retry` 는 손대지 않는다.
    NET = (TimeoutError, socket.timeout, urllib.error.URLError, ConnectionError)

    def buy(fn, *args, **kwargs):
        for attempt in range(4):
            try:
                return ea.with_retry(fn, *args, **kwargs)
            except NET as exc:
                if attempt == 3:
                    raise
                wait = 10 * (attempt + 1)
                print("    망 오류(%s) — %d초 뒤 다시 (%d/3)"
                      % (type(exc).__name__, wait, attempt + 1), flush=True)
                time.sleep(wait)
        raise RuntimeError("도달 불가")

    per_round = {}
    for round_no in range(args.start, args.rounds + 1):
        base = BASE / ("full_%s_r%d" % (ARM, round_no)) / ARM
        ea.RESPONSE_DIR, ea.JUDGE_DIR, ea.OUT_DIR = (base / "responses", base / "judge",
                                                     base)
        ea.RESPONSE_DIR.mkdir(parents=True, exist_ok=True)
        ea.JUDGE_DIR.mkdir(parents=True, exist_ok=True)
        started = time.time()
        with ablation.use(ARM):
            for at, case in enumerate(cases, start=1):
                done = base / "responses" / ("%s.json" % case["question_id"])
                if done.exists():
                    continue
                run = buy(ea.run_case, case, force=True)[0]
                if case.get("answer_mode") == "open":
                    buy(ea.run_judge, case, run["response"], force=True)
                if at % 10 == 0 or at == len(cases):
                    print("  r%d %3d/%d  %s  %.0f초 경과"
                          % (round_no, at, len(cases), case["question_id"],
                             time.time() - started), flush=True)
                time.sleep(ea.PACE_S)
        rows = {r["question_id"]: r for r in ea.analyse(cases, records)}
        per_round[round_no] = rows
        ok = sum(1 for r in rows.values() if r.get("correct"))
        print("  r%d 완료 — 정답 %d/%d · %.0f초\n"
              % (round_no, ok, len(rows), time.time() - started), flush=True)

    before = baseline_rows()
    print("\n%s" % ("=" * 72))
    print("총점 — A65 서버 3회 대비\n")
    print("  %-6s %-18s %s" % ("회차", "A65(서버)", "%s(로컬)" % ARM))
    for round_no in sorted(per_round):
        base_ok = sum(1 for r in before.get(round_no, {}).values() if r.get("correct"))
        now_ok = sum(1 for r in per_round[round_no].values() if r.get("correct"))
        print("  r%-5d %-18s %d/%d"
              % (round_no, "%d/%d" % (base_ok, len(before.get(round_no, {})) or 0),
                 now_ok, len(per_round[round_no])))

    print("\n축별 결함 건수 (회차마다)\n")
    print("  %-16s %-14s %s" % ("축", "A65(서버)", "%s(로컬)" % ARM))
    for name, bad in AXES:
        b = [sum(1 for r in before.get(n, {}).values() if bad(r)) for n in sorted(per_round)]
        a = [sum(1 for r in per_round[n].values() if bad(r)) for n in sorted(per_round)]
        print("  %-16s %-14s %s" % (name, "·".join(map(str, b)), "·".join(map(str, a))))

    print("\n뒤집힌 문항\n")
    flips = collections.defaultdict(list)
    for round_no in sorted(per_round):
        for qid, row in per_round[round_no].items():
            was = (before.get(round_no, {}).get(qid) or {}).get("correct")
            now = row.get("correct")
            if was is None or bool(was) == bool(now):
                continue
            flips["🔴 퇴행" if was else "🟢 개선"].append("%s r%d" % (qid, round_no))
    for kind in ("🔴 퇴행", "🟢 개선"):
        print("  %s %d건: %s" % (kind, len(flips[kind]), " ".join(flips[kind]) or "없다"))

    print("\n상시 오답 (3회 모두 틀림)\n")
    always = [q for q in per_round[min(per_round)]
              if all(not per_round[n].get(q, {}).get("correct") for n in per_round)]
    print("  %d건: %s" % (len(always), " ".join(sorted(always)) or "없다"))

    out = Path("scratch/audit/full_%s.json" % ARM.lower())
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(
        {"생성": "scripts/full_run.py", "팔": ARM, "문항": len(cases),
         "회차": {str(n): {"정답": sum(1 for r in per_round[n].values()
                                   if r.get("correct")),
                        "행": {q: {"correct": r.get("correct"),
                                  "kinds": r.get("numeric_failed_kinds"),
                                  "covers_key_amounts_ok": r.get("covers_key_amounts_ok")}
                              for q, r in per_round[n].items()}}
                for n in sorted(per_round)},
         "뒤집힘": {k: v for k, v in flips.items()},
         "상시오답": sorted(always)}, ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8")
    print("\n%s 에 적었다" % out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
