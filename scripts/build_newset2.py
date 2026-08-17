"""1회용 검증 2판, 규약 §6 의 `다중 조회·비교·연산` · `복합 문서 추론` 만 겨눈다.

**1판(`newset_20260816`)이 못 잰 칸이다.** 그 판은 단순조회 10 · 거절 6 에 쏠려서
규약 6유형 중 둘을 처음 보는 문항으로 재 본 적이 없다. 우리 평가셋의 유일한 실점 2건
(`Q-107`·`Q-117`)도 정확히 `complex_reason` 칸이다.

    python scripts/build_newset2.py

**골든은 레코드에서 결정적으로 계산한다**. 세는 것·비교하는 것·잇는 것만 묻는다.
서술 평가가 필요한 문항은 만들지 않는다. 1회용이라 재현 가능한 잣대여야 한다.
**기존 평가셋(heldout·1판 포함)이 쓴 접수번호는 제외한다.**
"""
from __future__ import annotations

import collections
import io
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
OUT = ROOT / "evalset" / "newset2_20260816.jsonl"

used = set()
for name in ("questions.jsonl", "valuechain.jsonl", "safety.jsonl",
             "regression.jsonl", "heldout.jsonl", "newset_20260816.jsonl"):
    path = ROOT / "evalset" / name
    if not path.exists():
        continue
    for line in io.open(path, encoding="utf-8"):
        for e in (json.loads(line).get("evidence") or []):
            if e.get("rcept_no"):
                used.add(e["rcept_no"])

docs = [json.loads(l) for l in
        io.open(ROOT / "artifacts" / "exchange_records.jsonl", encoding="utf-8")]
by_rcept = {d["rcept_no"]: d for d in docs}
cases: list[dict] = []


def ymd(record):
    raw = str(record.get("rcept_dt", ""))
    return f"{raw[:4]}년 {int(raw[4:6])}월 {int(raw[6:8])}일"


def subject(record):
    title = str(record.get("title") or "")
    return title.split("—", 1)[-1].strip() if "—" in title else title.strip()


def add(qid, task, mode, question, answer="", key_points=None, evidence=()):
    cases.append({"question_id": qid, "task_type": task, "answer_mode": mode,
                  "track": "grid", "difficulty": "상", "question": question,
                  "answer": answer, "answer_key_points": key_points,
                  "evidence": [{"rcept_no": r} for r in evidence]})


# --- A. 다중 조회·비교·연산 (Closed), 두 기업 중 어느 쪽이 큰가 -----------------
# 규약 §6 의 "2차전지 기업 A와 B 중 2025년 설비투자 규모가 더 큰 기업은?" 형태.
year_sum = collections.defaultdict(int)
year_ids = collections.defaultdict(list)
for d in docs:
    if (d.get("doc_subtype") == "단일판매공급계약체결" and d.get("amount")
            and d.get("is_latest") and str(d.get("rcept_dt", "")).startswith("2025")):
        year_sum[d["corp_name"]] += d["amount"]
        year_ids[d["corp_name"]].append(d["rcept_no"])
# 합이 뚜렷이 갈리는 짝만 쓴다. 근소하면 반올림 서술로도 맞아 판정이 흐려진다.
ranked = sorted(year_sum.items(), key=lambda kv: -kv[1])
pairs = [(ranked[i], ranked[i + 3]) for i in range(0, 6, 3)
         if i + 3 < len(ranked) and ranked[i][1] > ranked[i + 3][1] * 1.5]
for i, ((a, av), (b, bv)) in enumerate(pairs[:2], 1):
    add(f"M-{i:03d}", "multi_compare", "closed",
        f"{a}와(과) {b} 중 2025년에 공시한 단일판매·공급계약의 계약금액 합계가 "
        f"더 큰 기업은 어디인가?",
        answer=a, evidence=(year_ids[a] + year_ids[b])[:8])

# --- B. 다중 조회·비교·연산 (Open), 유형별로 정리 ------------------------------
# 규약 §6 의 "자금조달 내역을 유형별로 정리해줘" 형태. 서식이 곧 유형이다.
kind_of = collections.defaultdict(collections.Counter)
for d in docs:
    if str(d.get("rcept_dt", "")).startswith("2025"):
        kind_of[d["corp_name"]][d.get("doc_subtype") or "?"] += 1
mixed = sorted((c for c, k in kind_of.items() if len(k) >= 3),
               key=lambda c: -sum(kind_of[c].values()))
for i, corp in enumerate(mixed[:2], 3):
    kinds = kind_of[corp]
    add(f"M-{i:03d}", "multi_compare", "open",
        f"{corp}이(가) 2025년에 공시한 거래소 공시를 서식 유형별로 건수와 함께 "
        f"정리해줘.",
        key_points=[f"{k} {v}건" for k, v in kinds.most_common()],
        evidence=[d["rcept_no"] for d in docs
                  if d["corp_name"] == corp
                  and str(d.get("rcept_dt", "")).startswith("2025")][:8])

# --- C. 복합 문서 추론 (Closed), 해지된 계약이 존재하는가 ----------------------
# 규약 §6 의 "체결한 주요 계약 이후 해지된 계약이 존재하는가?" 형태.
term = [d for d in docs
        if d.get("doc_subtype") == "단일판매공급계약해지"
        and d["rcept_no"] not in used and d.get("amount")]
term.sort(key=lambda d: d["rcept_no"])
for i, d in enumerate(term[:2], 5):
    add(f"M-{i:03d}", "complex_reason", "closed",
        f"{d['corp_name']}이(가) {str(d['rcept_dt'])[:4]}년에 공시한 "
        f"단일판매·공급계약 해지가 존재하는가? 있다면 해지 금액은 얼마인가?",
        answer=f"{d['amount']:,}", evidence=[d["rcept_no"]])

# --- D. 복합 문서 추론 (Closed), 몇 차례 정정됐는가 ----------------------------
chains = []
for d in docs:
    if not d.get("is_latest"):
        continue
    seen, cur, depth = set(), d, 0
    while cur and (cur.get("related") or []):
        nxt = by_rcept.get(cur["related"][0])
        if not nxt or nxt["rcept_no"] in seen:
            break
        seen.add(nxt["rcept_no"])
        cur, depth = nxt, depth + 1
    if depth >= 2 and cur["rcept_no"] not in used and d["rcept_no"] not in used:
        chains.append((cur, d, depth, sorted(seen | {d["rcept_no"]})))
chains.sort(key=lambda c: (-c[2], c[1]["rcept_no"]))
for i, (origin, latest, depth, members) in enumerate(chains[:2], 7):
    add(f"M-{i:03d}", "complex_reason", "closed",
        f"{origin['corp_name']}이(가) {ymd(origin)} 공시한 "
        f"'{subject(origin)}' 계약은 이후 몇 차례 정정됐는가?",
        answer=str(depth), evidence=members[:8])

# --- E. 복합 문서 추론 (Open), 정정 이력 서술 ----------------------------------
for i, (origin, latest, depth, members) in enumerate(chains[2:4], 9):
    add(f"M-{i:03d}", "complex_reason", "open",
        f"{origin['corp_name']}이(가) {ymd(origin)} 공시한 "
        f"'{subject(origin)}' 계약의 정정 이력을 계약금액 변화와 함께 정리해줘.",
        key_points=[f"최초 {origin['amount']:,}원" if origin.get("amount") else "최초 공시",
                    f"최종 {latest['amount']:,}원" if latest.get("amount") else "최종본",
                    f"정정 {depth}회"],
        evidence=members[:8])

# --- F. 다중 조회·비교·연산 (Closed), 같은 발주처 두 계약의 차 ------------------
by_hub = collections.defaultdict(list)
for d in docs:
    if (d.get("counterparty") and d.get("amount") and d.get("is_latest")
            and d["rcept_no"] not in used):
        by_hub[(d["corp_name"], d["counterparty"])].append(d)
duos = sorted((v for v in by_hub.values() if len(v) == 2
               and v[0]["amount"] != v[1]["amount"]),
              key=lambda v: v[0]["rcept_no"])
for i, duo in enumerate(duos[:2], 11):
    a, b = sorted(duo, key=lambda d: -d["amount"])
    add(f"M-{i:03d}", "multi_compare", "closed",
        f"{a['corp_name']}이(가) {a['counterparty']}을(를) 계약상대로 공시한 "
        f"단일판매·공급계약 두 건의 계약금액 차이는 얼마인가?",
        answer=f"{a['amount'] - b['amount']:,}",
        evidence=[a["rcept_no"], b["rcept_no"]])


def jong(word):
    for ch in reversed(word):
        if "가" <= ch <= "힣":
            return (ord(ch) - 0xAC00) % 28 != 0
        if ch.isdigit():
            return ch in "0136780"
        if ch.isalpha():
            return ch.lower() in "lmnrq"
    return False


for case in cases:
    q = case["question"]
    for pat, (a, b) in (("이(가)", ("이", "가")), ("을(를)", ("을", "를")),
                        ("와(과)", ("과", "와"))):
        while pat in q:
            i = q.index(pat)
            q = q[:i] + (a if jong(q[:i].rstrip()) else b) + q[i + len(pat):]
    case["question"] = q

io.open(OUT, "w", encoding="utf-8").write(
    "".join(json.dumps(c, ensure_ascii=False) + "\n" for c in cases))
print("저장 %s · %d문항" % (OUT, len(cases)))
print("칸별:", dict(collections.Counter((c["task_type"], c["answer_mode"])
                                        for c in cases)))
for c in cases:
    print("  %-7s %-15s %-7s %s" % (c["question_id"], c["task_type"],
                                    c["answer_mode"], c["question"][:74]))
    print("           골든: %s" % (c["answer"] or
                                 " / ".join(c["answer_key_points"] or []))[:96])
