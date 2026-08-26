r"""배달되는 답변 문면을 훑어 **판정기가 못 보는 결함**을 센다. LLM 호출 0회.

    PYTHONIOENCODING=utf-8 python scratch/answer_hygiene.py a86_server a86b_server

## 왜 필요한가

2026-08-23 에 사용자가 `answers_a84_server.json` 을 손으로 읽다 결함 넷을 찾았다. 그때
8축은 전부 만점이었다 — **채점기는 토큰이 있는지를 보지 문장이 무엇을 주장하는지는 안
본다.** 그 읽기를 기계로 되풀이하는 것이 이 도구다.

## 🔴 이 도구는 채점기가 아니다

여기서 세는 것은 정오가 아니라 **문면의 흠**이다. 「셋 다 0건」이 정답을 뜻하지 않고,
「1건」이 오답을 뜻하지도 않는다. 사람이 볼 자리를 좁혀 주는 것이 전부다.
오탐이 나오는 검사(`동일_주장`)에는 그렇게 적어 둔다.
"""
import argparse
import itertools
import json
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

AUDIT = Path("scratch/audit")
NUM = re.compile(r"\d{1,3}(?:,\d{3})+")

#: (이름, 정규식, 설명). 전부 **0건이어야 하는** 것들이다.
MUST_BE_ZERO = [
    ("검증_각주", re.compile(r"\(주의:"),
     "우리 검증기의 경고가 배달 문면에 실렸다"),
    ("빈_라벨_인용", re.compile(r"\(근거:\s*,"),
     "인용 제목이 비었다"),
    ("결정적줄_인용", re.compile(r"\(근거:\s*-?\s*\[결정적"),
     "결정적 줄 전문이 인용 라벨이 됐다"),
    ("질문이_물은_값", re.compile(r"따라서 질문이 물은 값은"),
     "state_total 이 답변에 값을 덧붙였다"),
    ("결정적줄_복사", re.compile(r"이 값(?:과 증감)?은 시스템이"),
     "결정적 줄의 괄호 지시문까지 답변에 베껴졌다"),
    ("재생성_흔적", re.compile(r"앞서 제시된 답변|다시 작성하겠습니다|수정된 답변"),
     "재생성 과정이 답변 문면에 드러났다"),
    ("내부_표지", re.compile(r"amount_gap|stale_value|picked=|sc k=|verdict="),
     "내부 표지가 답변에 샜다"),
]

#: 산문으로 증감을 주장하는 자리. 답변 안 어느 두 수의 차와도 안 맞으면 의심이다.
CLAIM = re.compile(r"(\d{1,3}(?:,\d{3})+)\s*(?:주|원|건)?\s*(?:를|을)?\s*"
                   r"(?:추가로\s*)?(?:취득|증가|감소|늘|줄)")
#: 「변동 없음」류 주장. 🔴 **오탐이 많다** — 정정 사슬에서 한 걸음만 가리키는 서술이
#: 정상적으로 여기 걸린다. 사람이 볼 자리를 좁히는 용도다.
SAME = re.compile(r"동일한?\s*수치|변동이?\s*없|변화가?\s*없|그대로\s*유지|동일하게\s*유지")
DURATION = re.compile(r"약?\s*(\d{1,3})\s*(개월|년)\s*(?:동안|간|만에)")


def load(tag):
    path = AUDIT / ("answers_%s.json" % tag)
    if not path.exists():
        sys.exit("🔴 없습니다: %s — scratch/dump_answers.py 로 만든다" % path)
    return json.loads(path.read_text(encoding="utf-8"))


def scan(tag):
    doc = load(tag)
    rows = doc["문항"]
    total = sum(len(r["답변"]) for r in rows)
    out = {"응답수": total, "문항수": len(rows)}

    print("=" * 66)
    print("%s — %d문항 × %d회 = %d응답" % (tag, len(rows), doc["회차"], total))
    print("=" * 66)

    print("\n[1] 0건이어야 하는 것")
    zero = {}
    for name, pat, why in MUST_BE_ZERO:
        hits = [(r["question_id"], rnd) for r in rows
                for rnd, t in sorted(r["답변"].items()) if pat.search(t)]
        zero[name] = hits
        mark = "✅" if not hits else "🔴"
        print("  %s %-16s %3d건  %s" % (mark, name, len(hits), why))
        for qid, rnd in hits[:6]:
            print("        %s %s" % (qid, rnd))
        if len(hits) > 6:
            print("        … 외 %d건" % (len(hits) - 6))
    out["0건검사"] = {k: v for k, v in zero.items()}

    print("\n[2] 산문 증감이 답변 내 어느 두 수의 차와도 안 맞는 자리")
    bad = []
    for r in rows:
        for rnd, t in sorted(r["답변"].items()):
            vals = {int(x.replace(",", "")) for x in NUM.findall(t)}
            for m in CLAIM.finditer(t):
                d = int(m.group(1).replace(",", ""))
                if not d or d >= 10 ** 9:
                    continue
                if not any(abs(x - y) == d for x, y in itertools.combinations(vals, 2)):
                    bad.append((r["question_id"], rnd, m.group(1)))
    print("  %s %d건" % ("✅" if not bad else "🔴", len(bad)))
    for qid, rnd, v in bad:
        print("      %s %s  %s" % (qid, rnd, v))
    out["산술_불일치"] = bad

    print("\n[3] 「변동 없음」 주장 옆에 서로 다른 수 (🔴 오탐 많음 — 사람이 본다)")
    same = []
    for r in rows:
        for rnd, t in sorted(r["답변"].items()):
            for m in SAME.finditer(t):
                lo = max(t.rfind("\n\n", 0, m.start()), 0)
                hi = t.find("\n\n", m.end())
                seg = t[lo:hi if hi > 0 else len(t)]
                vals = sorted(set(NUM.findall(seg)))
                if len(vals) >= 2:
                    same.append((r["question_id"], rnd, m.group(0), vals[:3]))
    seen = sorted({q for q, *_ in same})
    print("  %d건 · 문항 %d개  %s" % (len(same), len(seen), " ".join(seen)))
    out["동일_주장"] = same

    print("\n[4] 기간 서술")
    for r in rows:
        for rnd, t in sorted(r["답변"].items()):
            for m in DURATION.finditer(t):
                print("      %s %s  %s" % (r["question_id"], rnd, m.group(0)))
    return out


def check_fixed(tag, doc_rows):
    """`Q-005` · `Q-012` 가 실제로 고쳐졌는지 값으로 확인한다."""
    by = {r["question_id"]: r["답변"] for r in doc_rows}
    print("\n[5] 고친 두 문항 — 값으로 확인")
    ok = True
    for rnd, t in sorted(by.get("Q-005", {}).items()):
        good = "114,800,000,000" in t and "112,700,000,000" not in t
        ok &= good
        print("  %s Q-005 %s  골든 114,800,000,000 %s · 틀린 112,700,000,000 %s"
              % ("✅" if good else "🔴", rnd,
                 "있음" if "114,800,000,000" in t else "없음",
                 "없음" if "112,700,000,000" not in t else "🔴 있음"))
    for rnd, t in sorted(by.get("Q-012", {}).items()):
        good = ("18,650" in t and "18,607" not in t and "18,652" not in t
                and "18개월" not in t and "동일한 수치" not in t)
        ok &= good
        print("  %s Q-012 %s  18,650 %s · 틀린 뺄셈 %s · 18개월 %s · 동일한수치 %s"
              % ("✅" if good else "🔴", rnd,
                 "있음" if "18,650" in t else "🔴 없음",
                 "없음" if ("18,607" not in t and "18,652" not in t) else "🔴 있음",
                 "없음" if "18개월" not in t else "🔴 있음",
                 "없음" if "동일한 수치" not in t else "🔴 있음"))
    return ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("tags", nargs="+")
    args = ap.parse_args()
    report = {}
    for tag in args.tags:
        report[tag] = scan(tag)
        check_fixed(tag, load(tag)["문항"])
        print()
    dest = AUDIT / "answer_hygiene.json"
    dest.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print("%s 에 적었다" % dest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
