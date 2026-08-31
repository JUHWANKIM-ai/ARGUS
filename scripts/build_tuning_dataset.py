"""A92 골든셋 응답을 CLOVA Studio 인스트럭션 튜닝 데이터셋으로 옮긴다.

프롬프트는 새로 짜지 않고 `build_answer_messages` 를 그대로 불러, 학습 문면이
런타임 문면과 바이트 단위로 같게 만든다. 저장된 `retrieved_context` 는 그 호출에
들어간 `evidence` 와 같은 변수다 (`pipeline._respond`).

규격은 `System_Prompt`(선택, 첫 열) · `C_ID` · `T_ID` · `Text` · `Completion`.
한 문항이 한 세션이므로 전부 싱글턴 — `C_ID` 는 문항마다 하나, `T_ID` 는 0.
"""
from __future__ import annotations

import csv
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, "src")

from argus import ablation
from argus.agent.prompts import build_answer_messages
from argus.agent.safety import sanitize_question

SRC = Path("scratch/ablation/v6/server_full/A92/responses")
OUT = Path("data/tuning")
STEM = "argus_golden_A92_20260831"
FIELDS = ["System_Prompt", "C_ID", "T_ID", "Text", "Completion"]

#: 라우팅이 정하는 값이고 프롬프트 꼬리(`_DIRECT` / `_COT`)를 가른다. 재현하려면
#: 라우터를 다시 돌리는 대신 그때 남은 흔적에서 읽는 편이 정확하다.
_COT = re.compile(r"\bcot=(True|False)\b")

#: 재구성이 맞았는지 대조할 값. 흔적의 `generate tokens=590+82` 앞자리가 그때
#: 실제로 청구된 프롬프트 토큰이다.
_GEN_TOKENS = re.compile(r"\bgenerate tokens=(\d+)\+\d+")


def rows() -> list[dict]:
    """한 응답 파일당 한 턴. 근거가 빈 행은 빼고 돌려준다."""
    out, skipped = [], []
    for path in sorted(SRC.glob("*.json")):
        response = json.loads(path.read_text(encoding="utf-8"))["response"]
        evidence = response["retrieved_context"]
        # 근거가 비면 파이프라인이 모델을 부르지 않고 상수 거절문을 돌려준다
        # (`pipeline.UNANSWERABLE`). 모델이 런타임에 겪지 않는 행동이라 뺀다.
        if not evidence.strip():
            skipped.append(response["question_id"])
            continue
        clean, _ = sanitize_question(response["question"])
        cot = _COT.search(response["think_trace"])
        messages = build_answer_messages(
            clean, evidence, use_cot=(cot is not None and cot.group(1) == "True"),
            lean_rules=ablation.enabled("lean_rules"),
            strict_refusal=ablation.enabled("strict_refusal"))
        system, user = messages[0]["content"], messages[1]["content"]
        out.append({"question_id": response["question_id"],
                    "System_Prompt": system,
                    "Text": user,
                    "Completion": response["answer"],
                    "trace": response["think_trace"]})
    return out, skipped


def check(turns: list[dict]) -> None:
    """규격이 요구하는 것만 본다. 어기면 파일을 남기지 않고 죽는다."""
    seen = set()
    for turn in turns:
        assert turn["C_ID"] not in seen, f"C_ID 중복: {turn['C_ID']}"
        seen.add(turn["C_ID"])
        assert isinstance(turn["C_ID"], int) and isinstance(turn["T_ID"], int)
        assert turn["T_ID"] == 0, "싱글턴이므로 T_ID 는 0 이다"
        for field in ("System_Prompt", "Text", "Completion"):
            assert turn[field].strip(), f"{turn['C_ID']} 의 {field} 가 비었다"
    assert seen == set(range(len(turns))), "C_ID 가 0 부터 이어지지 않는다"


def main() -> None:
    built, skipped = rows()
    turns = [{"System_Prompt": r["System_Prompt"], "C_ID": i, "T_ID": 0,
              "Text": r["Text"], "Completion": r["Completion"]}
             for i, r in enumerate(built)]
    check(turns)

    OUT.mkdir(parents=True, exist_ok=True)
    jsonl = OUT / f"{STEM}.jsonl"
    with jsonl.open("w", encoding="utf-8", newline="\n") as handle:
        for turn in turns:
            handle.write(json.dumps(turn, ensure_ascii=False) + "\n")
    csv_path = OUT / f"{STEM}.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(turns)

    # 대조: 재구성한 프롬프트 길이가 그때 청구된 토큰과 같은 자리에 있는가.
    ratio = []
    for row, turn in zip(built, turns):
        recorded = _GEN_TOKENS.search(row["trace"])
        if recorded:
            chars = len(turn["System_Prompt"]) + len(turn["Text"])
            ratio.append(chars / int(recorded.group(1)))

    text_len = sorted(len(t["Text"]) + len(t["System_Prompt"]) for t in turns)
    comp_len = sorted(len(t["Completion"]) for t in turns)
    print(f"턴 {len(turns)} · 뺀 문항 {len(skipped)} ({', '.join(skipped)})")
    print(f"입력 글자수  최소 {text_len[0]} · 중앙 {text_len[len(text_len) // 2]} "
          f"· 최대 {text_len[-1]}")
    print(f"출력 글자수  최소 {comp_len[0]} · 중앙 {comp_len[len(comp_len) // 2]} "
          f"· 최대 {comp_len[-1]}")
    if ratio:
        ratio.sort()
        print(f"글자/청구토큰 비  최소 {ratio[0]:.2f} · 중앙 "
              f"{ratio[len(ratio) // 2]:.2f} · 최대 {ratio[-1]:.2f}  (n={len(ratio)})")
    print(f"400턴 하한까지 {max(0, 400 - len(turns))}턴 모자란다")
    print(f"{jsonl}\n{csv_path}")


if __name__ == "__main__":
    main()
