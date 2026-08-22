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
    #: 🔴 2026-08-22 에 20,000 -> 100,000. 종전 값을 정한 두 근거 중 하나가 이미
    #: 없어졌다 — 08-08 당시엔 응답 타임아웃이 미확정이라 지연을 지키려는 몫이 있었는데,
    #: 08-12 주최측 답변으로 **문항당 300초**가 확정됐고 우리 실측 지연은 중앙값 4.3초 ·
    #: 최대 38.1초(상한의 12.7%)다. 지연 근거는 소멸했다.
    #:
    #: 남은 근거는 배급이다 — 답변 단계가 혼자 태우면 `critic` 과 검증이 굶는다. 그런데
    #: 20,000 은 그 배급조차 못 지켰다. 근거가 7~8천 토큰인 문항은 `답변 + critic +
    #: 재생성` 세 번만으로 21,000~24,000 이라, 실측에서 `A81` 이 재생성을 3회,
    #: `A83` 이 `critic` 을 15회 굶었다(`verify regen=skip_budget` · `critic 생략`).
    #:
    #: 🔴 이 값은 팔로 안 가른다. 모든 팔의 거동이 함께 바뀐다 — 배포본 `A65` 도 포함이다.
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
