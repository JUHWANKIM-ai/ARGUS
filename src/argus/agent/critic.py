"""근거 지지 판정. 생성보다 싸고 정확한 검증 수단."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from argus.agent.budget import Budget
from argus.agent.prompts import CITATION_RE, build_critic_messages
from argus.clova import chat

_JSON = re.compile(r"\{.*\}", re.DOTALL)


@dataclass
class Verdict:
    supported: bool
    has_citation: bool
    hallucination: bool
    reason: str

    @property
    def passed(self) -> bool:
        return self.supported and self.has_citation and not self.hallucination


def quick_check(answer: str) -> dict:
    """LLM 없이 하는 선검사. 여기서 걸리면 critic 호출을 아낀다."""
    return {"has_citation": bool(CITATION_RE.search(answer or ""))}


def judge(evidence: str, answer: str, budget: Budget | None = None) -> Verdict:
    result = chat(build_critic_messages(evidence, answer),
                  max_tokens=200, temperature=0.0, tag="critic")
    if budget:
        budget.spend_llm(result.prompt_tokens, result.completion_tokens)

    match = _JSON.search(result.text or "")
    if not match:
        return Verdict(False, False, True, "critic 응답 파싱 실패")
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return Verdict(False, False, True, "critic 응답 파싱 실패")

    return Verdict(
        supported=bool(data.get("supported")),
        has_citation=bool(data.get("has_citation")),
        hallucination=bool(data.get("hallucination")),
        reason=str(data.get("reason", "")),
    )
