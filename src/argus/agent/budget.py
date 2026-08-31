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
    #: Raised from 20,000 to 100,000 on 2026-08-22. One of the two reasons
    #: behind the old value is already gone: on 08-08 the response timeout was
    #: still undecided, so part of the ceiling was there to protect latency. The
    #: organisers fixed it at **300s per question** on 08-12, and our measured
    #: latency is a 4.3s median with a 38.1s maximum (12.7% of the ceiling). The
    #: latency argument no longer holds.
    #:
    #: What remains is rationing: if the answer step burns the whole budget,
    #: `critic` and verification starve. 20,000 failed even at that. A question
    #: whose evidence runs 7-8k tokens reaches 21,000-24,000 on `answer +
    #: critic + regenerate` alone, and in measurement `A81` starved
    #: regeneration 3 times and `A83` starved `critic` 15 times
    #: (`verify regen=skip_budget`, `critic omitted`).
    #:
    #: This value is not split per arm. Every arm's behaviour moves together,
    #: the deployed `A65` included.
    max_tokens: int = 100_000

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
