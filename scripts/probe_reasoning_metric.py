"""「추론 논리성」, **기준을 정하고 검사기를 시험한다.** LLM 0회 · 0원.

    PYTHONIOENCODING=utf-8 python scripts/probe_reasoning_metric.py

**이것이 없으면 "잰다" 고 말할 수 없다.** 검사를 하나 만들어 실패 하나를 잡았다고
지표를 측정한 것이 아니다. 먼저 **실패 양식을 정의**하고, 각 양식마다 **결함을 심은
답변**을 만들어, 우리 검사기가 그것을 잡는지 본다. 못 잡는 칸이 곧 미측정 범위다.

정의는 규약 §5 의 한 줄에서 편다. *"추론·답변 생성과정이 논리적인가"*.
"""
from __future__ import annotations

import io
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

#: **실패 양식 일곱.** 저장 답변에서 실제로 본 것(①②③⑥)과, 지표 문면이 요구하지만
#: 아직 본 적 없는 것(④⑤⑦)을 함께 둔다. 안 본 것을 빼면 커버리지가 부풀려진다.
FORMS = [
    ("① 수치 자기정합", "답변 안의 수가 서로 어긋난다",
     "차이는 7,591,628,000원입니다.\n"
     "362,307,038,000 - 354,725,410,000 = 7,581,628,000원"),
    ("② 식 불성립", "좌변과 우변이 안 맞는다",
     "합계는 100,000,000,000 + 200,000,000,000 = 250,000,000,000원입니다."),
    ("③ 근거 밖 수치", "근거에 없는 수를 결론으로 쓴다",
     "계약금액은 987,654,321,000원입니다."),
    ("④ 전제 누락", "결론에 필요한 중간 단계가 빠졌다",
     "따라서 두산에너빌리티가 더 큽니다."),
    ("⑤ 비약·무관", "근거와 결론이 논리적으로 안 이어진다",
     "계약금액이 532,040,000,000원이므로 이 회사는 재무구조가 건전합니다."),
    ("⑥ 순서 오류", "시계열·인과가 뒤집혔다",
     "2025년 정정으로 532,040,000,000원이 되었고, 이후 2023년 최초 공시에서 "
     "532,040,000,000원으로 확정됐습니다."),
    ("⑦ 자기부정", "없다고 하고 이어서 답한다",
     "공시에서 확인되지 않습니다. 계약금액은 532,040,000,000원입니다."),
]

# ④~⑦ 은 **근거에 있는 수**만 쓴다. 첫 판은 임의의 수를 심었고 그러면
#    `verify_number_provenance` 가 *"근거에 없다"* 로 발화한다. 그것은 논리 결함을
#    잡은 것이 아니라 **내가 만든 인공물**을 잡은 것이다. 시험이 그렇게 자기를 속였다.

#: 검사기가 볼 근거. ③⑤⑦ 의 판정에 쓴다.
EVIDENCE = ("## [공급계약 체결] 두산에너빌리티 – 단일판매ㆍ공급계약체결\n"
            "- 공시일: 2023-03-29 (접수번호 20230329801112)\n"
            "- 계약금액: 532,040,000,000원 (5,320억원)\n"
            "- 계약상대: 한국수력원자력(주)\n")


def main() -> int:
    from argus.agent import aggregate, critic

    print("규약 「추론 논리성」 — 실패 양식 %d종에 검사기를 걸어 본다\n" % len(FORMS))
    rows = []
    for name, what, answer in FORMS:
        caught: list[str] = []

        # ① 자기정합 (A38)
        _fixed, aligned = aggregate.fix_self_contradiction(answer)
        if aligned:
            caught.append("A38 자기정합")

        # ②③ 산술·출처 (기존 critic).
        # 호출이 죽으면 그것은 **잡은 것이 아니다.** 첫 판이 예외 문자열을
        #    `caught` 에 넣어 7/7 이라는 가짜 만점을 냈다. 시험이 스스로를 속인 것이다.
        calc = critic.verify_answer_arithmetic(answer)
        if not calc.get("ok", True):
            caught.append("critic 산술(%s)" % ",".join(calc.get("failed") or []))
        prov = critic.verify_number_provenance(answer, EVIDENCE)
        if not prov.get("ok", True):
            caught.append("critic 출처(%s)" % ",".join(prov.get("failed") or []))

        # ⑦ 자기부정, 거절 문구와 실답이 한 답변에 함께 있는가 (신규 후보)
        refuse = any(k in answer for k in ("확인되지 않습니다", "제공하지 않습니다"))
        import re
        if refuse and re.search(r"\d{1,3}(?:,\d{3})+\s*원", answer):
            caught.append("거절-실답 공존(미구현 · 후보)")

        rows.append((name, what, caught))
        mark = "✅" if caught else "🔴"
        print("%s %-16s %s" % (mark, name, what))
        print("     잡은 검사: %s" % (" · ".join(caught) or "**없음**"))
    print()
    hit = sum(1 for _n, _w, c in rows if c)
    print("커버리지 %d/%d 양식" % (hit, len(rows)))
    print("🔴 못 잡는 양식:", [n for n, _w, c in rows if not c] or "없음")

    out = ROOT / "artifacts/audit/reasoning_metric_probe.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    io.open(out, "w", encoding="utf-8").write(json.dumps(
        {"지표": "추론 논리성", "성격": "검사기 시험 — 결함을 심어 잡히는지 본다",
         "양식": [{"이름": n, "무엇": w, "잡은 검사": c, "잡혔나": bool(c)}
                for n, w, c in rows]}, ensure_ascii=False, indent=2) + "\n")
    print("저장:", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
