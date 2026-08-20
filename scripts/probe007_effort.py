"""Re-run the HCX-007 generator experiment across thinking effort levels.

Diagnostic only. Does not touch the submission configuration.

The first run of this experiment carried a defect. It capped
`maxCompletionTokens` at 1024, and thinking tokens draw from that same budget,
so a long reasoning trace could exhaust it and leave `finishReason=length` with
no visible answer. Two of the twelve answers were empty in that run, which was
attributed to the model but may have been the adapter. The cap here is large
enough that thinking cannot starve the output.

    PYTHONIOENCODING=utf-8 python scripts/probe007_effort.py
"""
from __future__ import annotations

import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

BASE = r"c:\Users\Juhwankim\Documents\Github\ARGUS"
os.chdir(BASE)
sys.path.insert(0, BASE + r"\src")

from argus import clova, config                            # noqa: E402

EFFORTS = ("none", "low", "medium")
#: No completion cap. Thinking draws from the same budget.
INTERVAL = 8.0
RETRIES = 4
BACKOFF = 15.0
OUT = "scratch/audit/hcx007_effort.json"

_orig_post = clova._post
STAT: dict = {}


def make_post(effort: str):
    """Wrap the transport so the pipeline reaches HCX-007 at a fixed effort."""

    def _post(url, headers, body, timeout=60):
        body = dict(body)
        body.pop("maxTokens", None)
        body["thinking"] = {"effort": effort}
        time.sleep(INTERVAL)
        payload = None
        for attempt in range(RETRIES):
            if attempt:
                time.sleep(BACKOFF * attempt)
            try:
                status, payload, latency = _orig_post(url, headers, body,
                                                      max(timeout, 300))
                break
            except Exception:                               # noqa: BLE001
                payload = None
        if payload is None:
            raise RuntimeError("007 호출이 재시도 후에도 실패했다")
        usage = ((payload or {}).get("result") or {}).get("usage") or {}
        bucket = STAT.setdefault(effort, {"calls": 0, "prompt": 0, "completion": 0,
                                          "thinking": 0, "truncated": 0})
        bucket["calls"] += 1
        bucket["prompt"] += usage.get("promptTokens", 0) or 0
        bucket["completion"] += usage.get("completionTokens", 0) or 0
        bucket["thinking"] += (usage.get("completionTokensDetails") or {}).get(
            "thinkingTokens", 0) or 0
        if ((payload or {}).get("result") or {}).get("finishReason") == "length":
            bucket["truncated"] += 1
        return status, payload, latency

    return _post


def digits(text: str) -> set[str]:
    return {t.replace(",", "") for t in re.findall(r"\d{1,3}(?:,\d{3})+", text or "")}


def norm(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def score(case: dict, answer: str) -> tuple[bool, str]:
    points = case.get("answer_key_points")
    if points:
        def hit(point: str) -> bool:
            wanted = digits(point)
            return (bool(wanted & digits(answer)) if wanted
                    else norm(point.split()[0]) in norm(answer))

        got = [p for p in points if hit(p)]
        return len(got) == len(points), "요점 %d/%d" % (len(got), len(points))
    gold = str(case.get("answer") or "").replace(",", "")
    if gold.isdigit():
        return gold in digits(answer), "골든 수치"
    return norm(gold)[:8] in norm(answer), "골든 문자열"


cases = [json.loads(line) for line in
         io.open("evalset/valuechain.jsonl", encoding="utf-8")]
base = config.settings()
results: dict = {}

for effort in EFFORTS:
    import dataclasses

    patched = dataclasses.replace(
        base, clova_url=config.CLOVA_HOST + "/testapp/v3/chat-completions/HCX-007")
    clova.settings = (lambda p=patched: p)
    clova._post = make_post(effort)

    from argus.agent.pipeline import answer_question

    rows, correct = [], 0
    print("=== thinking.effort = %s ===" % effort)
    for case in cases:
        started = time.time()
        try:
            payload = answer_question(case["question_id"], case["question"])
        except Exception as exc:                            # noqa: BLE001
            print("%-7s 오류 %s" % (case["question_id"], str(exc)[:70]))
            rows.append({"question_id": case["question_id"], "correct": False,
                         "why": "오류", "answer": str(exc)[:200]})
            continue
        answer = payload["answer"]
        ok, why = score(case, answer)
        correct += ok
        rows.append({"question_id": case["question_id"], "correct": ok, "why": why,
                     "seconds": round(time.time() - started, 1),
                     "answer": answer[:400]})
        print("%s %-7s %5.1f초 %-10s %s"
              % ("OK " if ok else "NG ", case["question_id"], time.time() - started,
                 why, answer[:52].replace("\n", " ")))
    results[effort] = {"correct": correct, "n": len(cases), "문항": rows,
                       "비용": STAT.get(effort, {})}
    print("  %s: %d/%d\n" % (effort, correct, len(cases)))

print("=== 정리 ===")
print("%-8s %-8s %-10s %-10s %-8s %s" % ("effort", "정답", "prompt", "completion",
                                         "thinking", "잘림"))
for effort in EFFORTS:
    r = results[effort]
    c = r["비용"]
    print("%-8s %-8s %-10s %-10s %-8s %s"
          % (effort, "%d/%d" % (r["correct"], r["n"]),
             format(c.get("prompt", 0), ","), format(c.get("completion", 0), ","),
             format(c.get("thinking", 0), ","), c.get("truncated", 0)))

io.open(OUT, "w", encoding="utf-8").write(json.dumps(
    {"생성": "scripts/probe007_effort.py",
     "성격": "진단 전용. 제출 구성은 A42 이고 이 실험은 그것을 바꾸지 않는다",
     "예산": "maxCompletionTokens 를 지정하지 않았다",
     "첫 실험의 결함": "1024 로 잡아 thinking 이 예산을 먹고 답이 잘렸을 수 있다",
     "결과": results}, ensure_ascii=False, indent=2) + "\n")
print("\n저장:", OUT)
