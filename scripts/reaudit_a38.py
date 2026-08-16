"""규약 감사 32문항을 **확정 베이스라인 `A38`** 로 다시 돌린다.

🔴 `docs/compliance-audit-v5-2026-08-16.md` 는 `A34` 로 쓰였는데 제출 구성은 `A38` 이다.
그 셋의 차이(`fix_unit_parens` · `counterparty_exempt` · `self_consistency_fix`)가
규약 판정을 흔들지 않는다고 **말할 수는 있어도 잰 적은 없다**. 여기서 잰다.

`RequestCache` 를 붙여 돈다 — 세 플래그가 전부 LLM 0회라 프롬프트가 바이트 동일이면
A34 판의 응답이 그대로 적중하고, 적중하지 않는 것이 있다면 그것 자체가 신호다.
"""
import io
import json
import os
import sys
from pathlib import Path

BASE = r"c:\Users\Juhwankim\Documents\Github\ARGUS"
os.chdir(BASE)
sys.path.insert(0, BASE + r"\src")
sys.path.insert(0, BASE + r"\scripts")

import ablation as runner                              # noqa: E402
from argus import ablation as flags                    # noqa: E402
from argus.agent.pipeline import answer_question       # noqa: E402

OUT = "artifacts/audit/v5_reaudit_A38.jsonl"
PREV = "artifacts/audit/v5_reaudit.jsonl"

cache = runner.RequestCache(runner.CACHE_DIR)
cache.install()

cases = []
for path in ("evalset/audit_questions.jsonl", "evalset/audit2_questions.jsonl"):
    cases += [json.loads(l) for l in io.open(path, encoding="utf-8")]

prev = {r["question_id"]: r for r in
        (json.loads(l) for l in io.open(PREV, encoding="utf-8"))}

print("arm = %s · 감사 %d문항" % (flags._current.name, len(cases)))
rows, changed = [], []
fh = io.open(OUT, "w", encoding="utf-8")
for case in cases:
    payload = answer_question(case["question_id"], case["question"])
    answer = payload["answer"]
    same = prev.get(case["question_id"], {}).get("answer") == answer
    if not same:
        changed.append(case["question_id"])
    rows.append((case, answer, same))
    fh.write(json.dumps({**payload, "expect": case["expect"], "axis": case.get("axis"),
                         "arm": flags._current.name, "same_as_A34": same},
                        ensure_ascii=False) + "\n")
    print("%s %-9s %s" % ("=  " if same else "🔴 ", case["question_id"],
                          answer[:70].replace("\n", " ")))
fh.close()

print()
print("캐시 적중 %d / 미적중 %d" % (cache.hits, cache.misses))
print("A34 와 답변이 다른 문항: %d건 %s" % (len(changed), changed))
print("저장:", OUT)
