"""Budget guard. Bounds both credit spend and response latency."""
from __future__ import annotations

from dataclasses import dataclass, field


class BudgetExceeded(RuntimeError):
    """Raised when a budget is exhausted.

    The caller answers with the evidence gathered so far or reports a limit.
    """


@dataclass
class Budget:
    max_steps: int = 8          # organiser reference architecture ceiling
    max_hops: int = 3           # ToG-2 iteration depth
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
    """Natural-language stop signal, paired with the hard ceilings above."""
    return "terminate" in (text or "").lower()
