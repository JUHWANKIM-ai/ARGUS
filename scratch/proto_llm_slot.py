r"""근거 칸 배분을 **LLM 이 고르면** 나아지는가. 원형이다 — 팔을 세우기 전에 잰다.

    PYTHONIOENCODING=utf-8 python scratch/proto_llm_slot.py

## 무엇을 묻는가

`Q-110` 은 파트너가 6곳이고 근거 칸이 6개라 회사당 1건이다. 두산퓨얼셀 차선에서 골든
(`20240329800342` LTSA)이 **2번째**라 잘린다. 지금 정렬은 *최신본 → 질문 관련도 →
도착 순서*인데, `_partner_order` 독스트링이 한계를 미리 적어 두었다 —
*"질문의 낱말을 하나도 안 가진 골든 레코드"* 는 관련도로 못 가른다. `Q-110` 이 그 경우다.

**그래서 낱말이 아니라 뜻으로 고르면 잡히는가**를 묻는다. 회수는 안 바꾼다 — 후보는
그대로 두고 **어느 것이 그 회사를 대표하는가**만 모델에게 묻는다.

## 🔴 이 원형이 답하지 못하는 것

`Q-110` 한 문항이다. 여기서 잘 골라도 **일반화의 근거는 아니다.** 미공개셋은 이미
검증에 써서 잣대로 못 쓴다(2026-08-26). 그러니 결과가 좋아도 곧바로 팔을 세우지 말고,
**같은 모양의 문항이 골든에 몇 개나 있는지**부터 세는 것이 다음 순서다.

## 값

파트너마다 1회. `Q-110` 은 6회다. 온도 0.0.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, ".")
sys.path.insert(0, "src")
sys.stdout.reconfigure(encoding="utf-8")

from argus import clova                                           # noqa: E402
from argus.agent import tools                                     # noqa: E402

QID = "Q-110"
HUB = "한국수력원자력(주)"
#: 골든이 회사마다 지목한 접수번호. `scratch/audit/golden_questions.json` 에서 왔다.
GOLDEN = {
    "20250829800405", "20230329801108", "20240329800342",
    "20230102900376", "20230417800192", "20231211800339",
}

PROMPT = """다음은 「{hub}」를 계약상대로 하는 {corp} 의 공시 목록이다.

{listing}

질문: {question}

이 질문에 답하려면 {corp} 의 공시 **한 건**을 근거로 실어야 한다. 그 회사가 이 질문에서
맡은 역할을 가장 잘 보여 주는 것을 하나 고르라.

번호만 답하라. 다른 말은 쓰지 마라."""


def listing(rows):
    out = []
    for i, r in enumerate(rows, start=1):
        amount = r.get("amount")
        out.append("%d. %s | %s | %s원 | 최신본=%s"
                   % (i, r.get("rcept_dt"), (r.get("title") or "")[:52],
                      format(amount, ",") if amount else "-",
                      "예" if r.get("is_latest", True) else "아니오"))
    return "\n".join(out)


def main() -> int:
    question = json.loads(
        Path("scratch/audit/golden_questions.json").read_text(encoding="utf-8"))
    case = next(c for c in question["문항"] if c["question_id"] == QID)
    rows = tools.query_by_counterparty(HUB) or []
    rows = rows.get("records", rows) if isinstance(rows, dict) else rows
    print("회수 %d건" % len(rows))

    lanes = {}
    for r in rows:
        lanes.setdefault(str(r.get("corp_name") or ""), []).append(r)
    print("파트너 %d곳: %s\n" % (len(lanes), " · ".join(sorted(lanes))))

    picked_now, picked_llm, spent = {}, {}, 0.0
    for corp, group in sorted(lanes.items()):
        group = sorted(group, key=lambda r: (not r.get("is_latest", True),
                                             str(r.get("rcept_dt", ""))))
        #: 지금 규칙이 고르는 것 — 정렬 뒤 첫 건.
        picked_now[corp] = group[0].get("rcept_no")
        if len(group) == 1:
            picked_llm[corp] = group[0].get("rcept_no")
            continue
        reply = clova.chat(
            [{"role": "user",
              "content": PROMPT.format(hub=HUB, corp=corp,
                                       listing=listing(group),
                                       question=case["question"])}],
            max_tokens=8, temperature=0.0, tag="proto_slot")
        spent += getattr(reply, "cost_won", 0) or 0
        digits = "".join(ch for ch in (reply.text or "") if ch.isdigit())
        index = int(digits) - 1 if digits else 0
        index = index if 0 <= index < len(group) else 0
        picked_llm[corp] = group[index].get("rcept_no")

    print("%-16s %-4s %-16s %-16s" % ("회사", "후보", "지금 규칙", "LLM 선택"))
    hit_now = hit_llm = 0
    for corp in sorted(lanes):
        now, llm = picked_now[corp], picked_llm[corp]
        hit_now += now in GOLDEN
        hit_llm += llm in GOLDEN
        print("  %-14s %-4d %-16s %-16s %s"
              % (corp, len(lanes[corp]), now + ("✔" if now in GOLDEN else " "),
                 llm + ("✔" if llm in GOLDEN else " "),
                 "" if now == llm else "← 달라짐"))
    print()
    print("골든 적중 — 지금 %d/%d · LLM %d/%d"
          % (hit_now, len(lanes), hit_llm, len(lanes)))
    print("이 원형이 쓴 크레딧: 약 %.1f원" % spent)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
