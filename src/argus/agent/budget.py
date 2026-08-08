"""예산 가드. 크레딧과 응답 지연을 동시에 지킨다."""
from __future__ import annotations

from dataclasses import dataclass, field


class BudgetExceeded(RuntimeError):
    """예산 초과. 호출부는 이 시점의 근거로 답변하거나 한계를 고지한다."""


@dataclass
class Budget:
    max_steps: int = 8          # 주최 측 참조 아키텍처 상한
    max_hops: int = 3           # ToG-2 반복 깊이
    max_llm_calls: int = 6
    max_tokens: int = 20_000

    steps: int = field(default=0, init=False)
    hops: int = field(default=0, init=False)
    llm_calls: int = field(default=0, init=False)
    tokens: int = field(default=0, init=False)

    def spend_step(self) -> None:
        if self.steps >= self.max_steps:
            raise BudgetExceeded(f"스텝 상한 {self.max_steps} 초과")
        self.steps += 1

    def spend_hop(self) -> None:
        if self.hops >= self.max_hops:
            raise BudgetExceeded(f"hop 상한 {self.max_hops} 초과")
        self.hops += 1

    def spend_llm(self, prompt_tokens: int, completion_tokens: int) -> None:
        if self.llm_calls >= self.max_llm_calls:
            raise BudgetExceeded(f"LLM 호출 상한 {self.max_llm_calls} 초과")
        total = self.tokens + prompt_tokens + completion_tokens
        if total > self.max_tokens:
            raise BudgetExceeded(f"토큰 상한 {self.max_tokens} 초과 ({total})")
        self.llm_calls += 1
        self.tokens = total

    def remaining(self) -> dict:
        return {
            "steps": self.max_steps - self.steps,
            "hops": self.max_hops - self.hops,
            "llm_calls": self.max_llm_calls - self.llm_calls,
            "tokens": self.max_tokens - self.tokens,
        }


def should_terminate(text: str) -> bool:
    """자연어 종료 신호. 코드 상한과 함께 쓰는 이중 장치의 한 축."""
    return "terminate" in (text or "").lower()
