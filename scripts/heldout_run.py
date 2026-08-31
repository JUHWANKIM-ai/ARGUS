r"""미공개셋 54문항을 한 팔로 **N회** 돈다. 🔴 **재는 데만 쓴다.**

## 왜 이 실행이 필요한가

캠페인은 팔을 **골든셋 어긋남을 보고** 깎았다. 골든셋에 맞춰진 것인지를 골든셋으로는 알
수 없다. 처음 보는 문항에서 재야 한다.

## 🔴 여기서 드러난 실패는 고치지 않는다

`scratch/heldout_a62.py` 의 규율을 그대로 따른다 — *"여기서 드러난 실패를 고치면 그 순간
처음 보는 문항이 아니게 된다."* 이 실행의 산출물은 **성적을 적는 데만** 쓴다.

## 🔴 왜 회차가 필요한가

종전 판(`scratch/heldout_a81.py`)은 r1 하나만 돌았다. 그것으로는 ±1~2 를 못 거른다 —
실제로 `A65`↔`A81` 이 **프롬프트가 54문항 전부 바이트까지 같은데** 세 자리가 뒤집혔다
(v3 §10.12). 회차를 세야 「고정 결함」과 「뽑기」가 갈린다.

`scripts/score_heldout.py` 의 채점 규칙을 **그대로 불러 쓴다** — 두 곳에 따로 적으면
한쪽만 고쳐진다.

Usage:
    HELDOUT_ARM=A84 python scripts/heldout_run.py --rounds 3
"""
import argparse
import importlib.util
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

_spec = importlib.util.spec_from_file_location("_score_heldout",
                                               "scripts/score_heldout.py")
_sh = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_sh)

ARM = os.environ.get("HELDOUT_ARM", "A84")
#: 튜닝 모델은 파이프라인 팔이 A92 그대로이고 엔드포인트만 다르다. 팔 이름으로는
#: 갈리지 않으므로 산출물 뿌리로 가른다. 안 가르면 기본 모델 산출물과 한 디렉터리에
#: 섞여서 어느 쪽 성적인지 못 가린다.
ROOT = Path(os.environ.get("HELDOUT_ROOT", "scratch/ablation/v9"))
NET = (TimeoutError, socket.timeout, urllib.error.URLError, ConnectionError)


def buy(fn, *args, **kwargs):
    """`ea.with_retry` 는 429 만 재시도한다. 망 오류는 이 러너가 되풀이한다."""
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


def one_round(records: dict, round_no: int) -> list:
    """(이름, 정답, 분모, A56저장, 오답목록)"""
    rows = []
    for cohort, path, before, _when in _sh.SUBSETS:
        cases = _sh.load(path)
        base = ROOT / ("%s_%s_r%d" % (ARM, cohort, round_no)) / ARM
        ea.RESPONSE_DIR, ea.JUDGE_DIR, ea.OUT_DIR = (base / "responses",
                                                     base / "judge", base)
        ea.RESPONSE_DIR.mkdir(parents=True, exist_ok=True)
        ea.JUDGE_DIR.mkdir(parents=True, exist_ok=True)
        started = time.time()
        with ablation.use(ARM):
            for index, case in enumerate(cases, 1):
                done = base / "responses" / ("%s.json" % case["question_id"])
                if done.exists():
                    continue
                run = buy(ea.run_case, case, force=True)[0]
                if case.get("answer_mode") == "open":
                    buy(ea.run_judge, case, run["response"], force=True)
                if index % 6 == 0 or index == len(cases):
                    print("  r%d %-11s %2d/%-2d %.0f초"
                          % (round_no, cohort, index, len(cases),
                             time.time() - started), flush=True)
                time.sleep(ea.PACE_S)
        made = _sh.judge(cases)
        if made:
            print("  %s 판정 %d건 신규" % (cohort, made), flush=True)
        scored = ea.analyse(cases, records)
        ok = sum(1 for r in scored if r["correct"])
        wrong = sorted(r["question_id"] for r in scored if not r["correct"])
        rows.append((cohort, ok, len(scored), before, wrong))
        print("r%d %s  %d/%d  (저장 %s) · 오답 %s\n"
              % (round_no, cohort, ok, len(scored), before, wrong or "없다"),
              flush=True)
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=3)
    args = ap.parse_args()
    records = ea._load_records()
    grand = [one_round(records, n) for n in range(1, args.rounds + 1)]

    print("\n%s" % ("=" * 70))
    print("미공개셋 %s — 회차마다\n" % ARM)
    header = "  ".join("r%d" % n for n in range(1, args.rounds + 1))
    print("  %-12s %s" % ("부분집합", header))
    for at, name in enumerate(c for c, *_ in grand[0]):
        print("  %-12s %s"
              % (name, "  ".join("%d/%d" % (r[at][1], r[at][2]) for r in grand)))
    print("  %-12s %s"
          % ("합계", "  ".join("%d/%d" % (sum(x[1] for x in r), sum(x[2] for x in r))
                              for r in grand)))

    per = [set(q for x in r for q in x[4]) for r in grand]
    fixed = set.intersection(*per) if per else set()
    union = set.union(*per) if per else set()
    print("\n  매 회차 오답 (고정 결함): %s" % (" ".join(sorted(fixed)) or "없다"))
    print("  한 번 이상 오답        : %s" % (" ".join(sorted(union)) or "없다"))
    print("  🔴 앞줄이 겨냥할 것이고 뒷줄과의 차이가 뽑기다.")

    out = Path("scratch/audit/heldout_%s_rounds.json" % ARM.lower())
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(
        {"생성": "scripts/heldout_run.py",
         "성격": "🔴 재는 데만 쓴다 — 여기서 드러난 실패를 고치면 처음 보는 문항이 아니게 된다",
         "팔": ARM, "회차": args.rounds,
         "회차별": [{"r": n + 1,
                  "합계": "%d/%d" % (sum(x[1] for x in r), sum(x[2] for x in r)),
                  "부분집합": [{"이름": c, "정답": ok, "분모": d, "오답": w}
                          for c, ok, d, _b, w in r]}
                 for n, r in enumerate(grand)],
         "매회차오답": sorted(fixed), "한번이상오답": sorted(union)},
        ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print("\n%s 에 적었다" % out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
