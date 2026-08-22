r"""문항별 **우리 답변만** 모은다. 근거도 트레이스도 안 싣는다.

## 왜 따로 만드나

응답 산출물은 한 문항이 한 파일이고, 그 안에 `retrieved_context`(근거 전문)와
`think_trace` 가 함께 들어 있어 한 파일이 수십 KB 다. 답변 문면만 훑고 싶을 때 112개
파일을 열어야 하고, 회차별로 견주려면 세 곳을 오간다. 이 스크립트는 답변 문자열만
뽑아 한 파일로 만든다.

## 🔴 회차를 접지 않는다

한 회차만 싣고 「우리 답변」이라 부르면 안 된다. 상류가 온도 0 에서도 결정적이지 않아
같은 프롬프트에서 답변 문면이 회차마다 갈린다(v3 §11 실측 — 문면 43~48%, 금액 34%).
그래서 세 회차를 **나란히** 싣고, 세 회차가 글자 그대로 같은지를 `회차동일` 로 표시한다.
그 칸이 `false` 인 문항은 「우리 답변」이 하나로 정해지지 않는 문항이라는 뜻이다.

## 앞선 도구와 무엇이 다른가

`scratch/build_golden_audit.py` 도 3회 답변을 싣지만 **문제 문항만** 담고 골든 · 축 ·
누락 토큰을 함께 붙인다. 이 스크립트는 112문항 전부를 담되 **답변 말고는 아무것도 안
붙인다.** 문항 쪽은 `scratch/dump_golden_questions.py` 가 따로 낸다 — `question_id` 로
맞물린다.

## 채점은 안 한다

정답 여부는 이 파일에 없다. 채점은 `scripts/error_analysis` 의 몫이고 그 산출물은
`report.json` 이다. 여기에 정오를 적으면 두 곳이 갈라진다.

Usage:
    python scratch/dump_answers.py                      # A84 · 서버 3회
    python scratch/dump_answers.py --arm A65
    python scratch/dump_answers.py --source local
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, ".")
sys.path.insert(0, "src")
sys.stdout.reconfigure(encoding="utf-8")

from scripts import error_analysis as ea                          # noqa: E402

COHORTS = ("full", "valuechain", "safety")


def round_dirs(source: str, arm: str, rounds: int) -> list[tuple[int, list[Path]]]:
    """회차마다 응답 디렉터리 목록을 준다. 두 수집 경로의 배치가 다르다.

    server  `v6/server_<코호트><접미>/<팔>/responses` — 코호트마다 디렉터리가 따로다.
    local   `v9/full_<팔>_r<n>/<팔>/responses`       — 112문항이 한 디렉터리에 있다.
    """
    out = []
    for n in range(1, rounds + 1):
        if source == "server":
            suffix = "" if n == 1 else "_r%d" % n
            paths = [Path("scratch/ablation/v6/server_%s%s/%s/responses" % (c, suffix, arm))
                     for c in COHORTS]
        else:
            paths = [Path("scratch/ablation/v9/full_%s_r%d/%s/responses" % (arm, n, arm))]
        out.append((n, [p for p in paths if p.exists()]))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", default="A84")
    ap.add_argument("--source", default="server", choices=("server", "local"))
    ap.add_argument("--rounds", type=int, default=3)
    args = ap.parse_args()

    order = [c["question_id"] for c in
             (ea.load_cohort("full") + ea.load_cohort("valuechain")
              + ea.load_cohort("safety"))]
    asked = {c["question_id"]: c["question"] for c in
             (ea.load_cohort("full") + ea.load_cohort("valuechain")
              + ea.load_cohort("safety"))}

    answers: dict[str, dict[str, str]] = {}
    found = {}
    for n, dirs in round_dirs(args.source, args.arm, args.rounds):
        if not dirs:
            sys.exit("🔴 r%d 의 수집 산출물이 없습니다 (--source %s --arm %s)"
                     % (n, args.source, args.arm))
        seen = 0
        for base in dirs:
            for path in base.glob("*.json"):
                doc = json.loads(path.read_text(encoding="utf-8"))
                text = (doc.get("response") or {}).get("answer")
                if text is None:
                    continue
                answers.setdefault(doc["question_id"], {})["r%d" % n] = text
                seen += 1
        found[n] = seen

    rows, uneven = [], []
    for qid in order:
        got = answers.get(qid)
        if not got:
            uneven.append(qid)
            continue
        texts = [got.get("r%d" % n) for n in range(1, args.rounds + 1)]
        rows.append({
            "question_id": qid,
            "question": asked[qid],
            "회차동일": len({t for t in texts if t is not None}) == 1,
            "답변": got,
        })

    same = sum(1 for r in rows if r["회차동일"])
    out = {
        "생성": "scratch/dump_answers.py",
        "성격": "🔴 답변 문면만 싣는다 — 근거·트레이스·정오는 없다",
        "팔": args.arm,
        "수집": args.source,
        "회차": args.rounds,
        "문항수": len(rows),
        "회차별_응답수": found,
        "세_회차_글자까지_동일": "%d/%d" % (same, len(rows)),
        "회차마다_문면이_갈리는_문항": [r["question_id"] for r in rows if not r["회차동일"]],
        "문항": rows,
    }
    if uneven:
        out["응답이_없는_문항"] = uneven

    dest = Path("scratch/audit/answers_%s_%s.json" % (args.arm.lower(), args.source))
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

    print("%s 에 %d문항 · 회차 %d" % (dest, len(rows), args.rounds))
    print("  회차별 응답수      %s" % found)
    print("  세 회차 동일       %s" % out["세_회차_글자까지_동일"])
    if uneven:
        print("  🔴 응답 없음      %s" % uneven)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
