"""Measure judge variance for HCX-005 and HCX-007 on identical inputs.

Step 1 of docs/hcx007-as-judge.md. Diagnostic only. Does not touch the
submission configuration and does not change any scoring contract.

The request cache is deliberately not installed. With the cache in place the
three repeats would read the same stored file and report zero variance, which
would be a property of the cache rather than a measurement.

    PYTHONIOENCODING=utf-8 python scripts/probe_judge_variance.py
"""
from __future__ import annotations

import dataclasses
import glob
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

from argus import config                                  # noqa: E402
from argus.agent.prompts import build_critic_messages     # noqa: E402

REPEATS = 3
OUT = "artifacts/audit/judge_variance.json"

#: Questions chosen because the current judge already fails on them. Four of the
#: seven closed-judge disagreements returned an empty reason, and four sit on the
#: safety track where a legitimate refusal was read as unsupported.
TARGETS = ("S-001", "S-007", "S-011", "Q-111", "Q-001", "Q-018")

_JSON = re.compile(r"\{[^{}]*\}", re.DOTALL)


def load_answers() -> dict:
    out = {}
    for run in ("a42_full", "a42_valuechain", "a42_safety"):
        for p in glob.glob(f"artifacts/ablation/v6/{run}/A42/responses/*.json"):
            qid = os.path.basename(p)[:-5]
            if qid not in TARGETS:
                continue
            body = (json.load(io.open(p, encoding="utf-8")).get("response") or {})
            out[qid] = (body.get("retrieved_context") or "", body.get("answer") or "")
    return out


def call(model: str, messages: list[dict]) -> tuple[dict | None, int, int, float]:
    """One judge call. Returns (parsed verdict, prompt tokens, completion, seconds)."""
    cfg = config.settings()
    url = config.CLOVA_HOST + f"/testapp/v3/chat-completions/{model}"
    body: dict = {"messages": messages, "temperature": 0.0, "topP": 0.8}
    # HCX-007 rejects maxTokens and accepts maxCompletionTokens instead.
    if model == "HCX-007":
        # Thinking tokens draw from this same budget. At 512 the reasoning trace
        # consumed all of it and finishReason came back as length with no visible
        # content, so every parse failed.
        body["maxCompletionTokens"] = 4096
    else:
        body["maxTokens"] = 512
    req = urllib.request.Request(
        url, data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {cfg.clova_api_key}",
                 "Content-Type": "application/json"},
        method="POST")
    started = time.time()
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            payload = json.load(resp)
    except urllib.error.HTTPError as exc:
        return {"error": exc.read().decode("utf-8")[:160]}, 0, 0, time.time() - started
    elapsed = time.time() - started
    result = payload.get("result") or {}
    usage = result.get("usage") or {}
    text = ((result.get("message") or {}).get("content")) or ""
    hit = _JSON.search(text)
    parsed = None
    if hit:
        try:
            parsed = json.loads(hit.group(0))
        except json.JSONDecodeError:
            parsed = None
    return (parsed, usage.get("promptTokens", 0) or 0,
            usage.get("completionTokens", 0) or 0, elapsed)


def verdict_key(parsed: dict | None) -> str:
    if not parsed or "error" in parsed:
        return "PARSE_FAIL"
    return "%s|%s|%s" % (bool(parsed.get("supported")),
                         bool(parsed.get("has_citation")),
                         bool(parsed.get("hallucination")))


answers = load_answers()
missing = [q for q in TARGETS if q not in answers]
if missing:
    print("응답을 못 찾은 문항:", missing)

rows = []
totals = {"HCX-005": [0, 0, 0.0], "HCX-007": [0, 0, 0.0]}

for qid in TARGETS:
    if qid not in answers:
        continue
    evidence, answer = answers[qid]
    messages = build_critic_messages(evidence, answer)
    for model in ("HCX-005", "HCX-007"):
        keys, reasons = [], []
        for _ in range(REPEATS):
            parsed, pt, ct, el = call(model, messages)
            keys.append(verdict_key(parsed))
            reasons.append((parsed or {}).get("reason", "") if parsed else "")
            totals[model][0] += pt
            totals[model][1] += ct
            totals[model][2] += el
        stable = len(set(keys)) == 1
        empty = sum(1 for r in reasons if not str(r).strip())
        rows.append({"question_id": qid, "model": model, "판정": keys,
                     "일관": stable, "사유 빈 회차": empty,
                     "사유": [str(r)[:110] for r in reasons]})
        print("%-7s %-8s %s  사유공백 %d/%d  %s"
              % (qid, model, "일관" if stable else "갈림", empty, REPEATS,
                 " / ".join(keys)))

print()
for model, (pt, ct, sec) in totals.items():
    n = sum(1 for r in rows if r["model"] == model) * REPEATS
    stable = sum(1 for r in rows if r["model"] == model and r["일관"])
    total_q = sum(1 for r in rows if r["model"] == model)
    print("%-8s 일관 %d/%d · 호출 %d · 토큰 %s+%s · 평균 %.1f초 · 약 %.0f원"
          % (model, stable, total_q, n, format(pt, ","), format(ct, ","),
             sec / max(n, 1), (pt + ct) / 1000 * 3.5))

io.open(OUT, "w", encoding="utf-8").write(json.dumps(
    {"생성": "scripts/probe_judge_variance.py",
     "성격": "진단 전용. 채점 계약을 바꾸지 않는다",
     "반복": REPEATS,
     "캐시": "설치하지 않았다. 붙이면 세 회차가 같은 파일을 읽어 분산 0 이 된다",
     "문항": rows}, ensure_ascii=False, indent=2) + "\n")
print("\n저장:", OUT)
