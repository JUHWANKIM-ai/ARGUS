r"""근거 칸 상한을 6 → 12 로 올리면 무엇이 실리는가. **LLM 0회 · 0원.**

    PYTHONIOENCODING=utf-8 python scratch/proto_slot_cap.py

## 왜 산술만 먼저 재나

칸 배분은 결정적이다 — `_partner_order` 로 줄을 세우고 `_partner_quota` 깊이만큼 돌며
`limit` 에서 끊는다. 그러니 **무엇이 실릴지는 사기 전에 계산할 수 있다.** 3회를 사는 것은
그 다음이다.

## 묻는 것 둘

    ① `Q-110` 의 골든(20240329800342)이 실리는가 — 근거 완전성 167 → 168 이 되는가
    ② 나머지 일곱 문항의 골든이 **안 밀려나는가** — 칸이 늘면 순서가 아니라 개수만 는다

🔴 ②가 이 실험의 핵심이다. 칸을 늘리면 근거가 길어지고, `ORACLE` 실측에서 근거를
**치웠더니** 세 문항이 정답이 된 전례가 있다(희석). 산술로는 밀려남만 보고, 희석은
사서 재야 안다.
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, ".")
sys.path.insert(0, "src")
sys.stdout.reconfigure(encoding="utf-8")

from argus.agent import pipeline as P                             # noqa: E402
from argus.agent import tools                                     # noqa: E402

TARGETS = ("Q-107", "Q-109", "Q-110", "Q-111", "Q-114", "Q-115", "Q-116", "Q-118")
#: 허브는 `via=` 에 적힌다. `corp=` 는 **파트너 목록**이라 그걸 잡으면 안 된다.
_HUB = re.compile(r"tool=query_by_counterparty[^|]*?via=([^\s|]+)")
#: 그 hop 이 **실제로 실은** doc. 재구성보다 이쪽이 정본이다.
_IDS = re.compile(r"tool=query_by_counterparty[^|]*?ids=([^\s|]+)")
#: 배분 방식. `relevance` 는 파트너별 배분을 안 쓴다.
_ALLOC = re.compile(r"tool=query_by_counterparty[^|]*?alloc=(\w+)")


def trace_of(qid: str) -> tuple[str, set, str]:
    """(허브, 지금 실린 접수번호, 배분 방식). 트레이스가 정본이다."""
    for coh in ("full", "valuechain", "safety"):
        path = Path("scratch/ablation/v6/server_%s/A86/responses/%s.json" % (coh, qid))
        if not path.exists():
            continue
        trace = json.loads(path.read_text(encoding="utf-8"))["response"]["think_trace"]
        hub = _HUB.search(trace)
        if not hub:
            continue
        ids = _IDS.search(trace)
        loaded = {x.split("_")[-1] for x in ids.group(1).split(",")} if ids else set()
        how = _ALLOC.search(trace)
        return hub.group(1), loaded, (how.group(1) if how else "?")
    return "", set(), "?"


def allocate(lanes: dict, partners: list, quota: int, limit: int) -> list:
    """`pipeline` 의 배분 루프와 같은 차례로 doc 을 고른다."""
    taken = []
    for depth in range(quota):
        for corp in partners:
            if len(taken) >= limit:
                return taken
            items = lanes.get(corp) or []
            if depth < len(items):
                taken.append(items[depth])
    return taken


def main() -> int:
    golden = {c["question_id"]: c for c in json.loads(
        Path("scratch/audit/golden_questions.json").read_text(encoding="utf-8"))["문항"]}

    print("%-7s %-22s %-6s %-14s %-14s %s"
          % ("문항", "허브", "파트너", "지금(6칸)", "넓힘(12칸)", "비고"))
    print("-" * 96)
    for qid in TARGETS:
        case = golden.get(qid) or {}
        want = {e["rcept_no"] for e in (case.get("evidence") or [])}
        hub, loaded, how = trace_of(qid)
        if not hub:
            print("  %-7s %-22s (허브를 트레이스에서 못 읽었다 — 건너뛴다)" % (qid, "?"))
            continue
        got = tools.query_by_counterparty(hub) or {}
        rows = got.get("records") if isinstance(got, dict) else got
        rows = [r for r in (rows or []) if isinstance(r, dict)]
        if not rows:
            print("  %-7s %-22s (계약상대 레코드가 없다 — 건너뛴다)" % (qid, hub[:20]))
            continue
        lanes = {}
        for record in rows:
            lanes.setdefault(str(record.get("corp_name") or ""), []).append(record)
        for corp in lanes:
            lanes[corp] = P._partner_order(lanes[corp], case.get("question", ""))
        partners = sorted(lanes)

        wide = allocate(lanes, partners, max(2, P._partner_quota(len(partners))), 12)
        now_ids = loaded                       # 트레이스가 적은 실제 적재
        wide_ids = {r.get("rcept_no") for r in wide}
        if how == "relevance":
            #: 관련도 배분은 파트너별 깊이를 안 쓴다. 이 실험의 대상이 아니다.
            print("  %-7s %-16s %-6d alloc=relevance — 이 실험 밖" % (qid, hub[:14], len(partners)))
            continue
        lost = (now_ids & want) - (wide_ids & want)
        gained = (wide_ids & want) - (now_ids & want)
        note = []
        if gained:
            note.append("🟢 골든 +%d %s" % (len(gained), " ".join(sorted(gained))))
        if lost:
            note.append("🔴 골든 −%d %s" % (len(lost), " ".join(sorted(lost))))
        print("  %-7s %-22s %-6d %-14s %-14s %s"
              % (qid, hub[:20], len(partners),
                 "%d건 골든%d" % (len(now_ids), len(now_ids & want)),
                 "%d건 골든%d" % (len(wide), len(wide_ids & want)),
                 " · ".join(note)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
