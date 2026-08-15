"""HyperCLOVA X 호출 단일 창구.

주의:
  - HCX-005는 v3 API 필수 (v1은 40084 Unsupported API for model)
  - nv- 신규 키는 Bearer 인증만 허용 (레거시 헤더는 40104)
  - tools 와 maxTokens 는 동시 지정 불가 (40001)
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from argus.config import settings


@dataclass
class ChatResult:
    text: str
    prompt_tokens: int
    completion_tokens: int
    latency_s: float
    finish: str
    tool_calls: list | None = None
    tag: str = ""


@dataclass
class CallLog:
    records: list[ChatResult] = field(default_factory=list)

    def clear(self) -> None:
        self.records.clear()

    def add(self, r: ChatResult) -> None:
        self.records.append(r)

    def totals(self) -> dict:
        return {
            "calls": len(self.records),
            "prompt_tokens": sum(r.prompt_tokens for r in self.records),
            "completion_tokens": sum(r.completion_tokens for r in self.records),
            "latency_s": sum(r.latency_s for r in self.records),
        }


CALL_LOG = CallLog()


# --- 요청 캐시 우회 신호 (`A10` 자기일관성 표본 전용) ----------------------------
# 🔴 **온도를 올려도 요청 본문이 같으면 표본이 전부 같아진다.** 러너의 요청 캐시
# (`scripts/ablation.py::RequestCache`)는 `sha256(url + body)` 로 응답을 저장하는데,
# SC 의 k 개 표본은 프롬프트도 온도도 같으므로 **키가 하나**다. 그러면 2·3회차가
# 1회차 응답을 그대로 적중시키고 다양성이 0이 된다 — 다수결이 항상 만장일치가 되고
# `A10` 의 Δ 가 **조용히 0** 으로 보고된다. 이 저장소가 `ablation.py` 전체로 막으려는
# 바로 그 사고이고, 같은 사고를 judge 재현 편차에서 이미 한 번 겪어
# `RequestCache.no_cache()` 가 그 자리에 있다.
#
# 🔴 **파이프라인이 러너를 import 할 수는 없다.** 그래서 신호를 여기 모듈 전역에 두고
# 캐시 쪽이 읽는다 (`RequestCache.install` 의 `cached`). 캐시가 안 걸린 운영 경로에서는
# 이 플래그를 아무도 안 보므로 동작이 한 글자도 안 바뀐다.
#
# 🔴 **읽기·쓰기 양쪽 다 끈다.** 우회 응답을 캐시에 얹으면 다음 표본이 그것을 적중시켜
# 같은 문제가 되돌아온다 (`RequestCache.no_cache` 독스트링과 같은 사유).
_no_cache_depth = 0


def cache_bypassed() -> bool:
    """지금 호출이 요청 캐시를 우회해야 하는가. 러너의 캐시 래퍼가 읽는다."""
    return _no_cache_depth > 0


class no_cache:
    """`with clova.no_cache(): ...` — 이 블록의 호출은 요청 캐시를 읽지도 쓰지도 않는다.

    중첩을 세는 이유는 예외 경로다. 단순 불리언이면 안쪽 블록이 끝날 때 바깥 블록의
    우회까지 함께 꺼진다.
    """

    def __enter__(self) -> None:
        global _no_cache_depth
        _no_cache_depth += 1

    def __exit__(self, *exc) -> None:
        global _no_cache_depth
        _no_cache_depth = max(0, _no_cache_depth - 1)


def _post(url: str, headers: dict, body: dict, timeout: int = 60):
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json", **headers},
    )
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8")), time.time() - t0
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            return exc.code, json.loads(raw), time.time() - t0
        except json.JSONDecodeError:
            return exc.code, {"raw": raw}, time.time() - t0


def chat(
    messages: list[dict],
    max_tokens: int = 512,
    temperature: float = 0.0,
    top_p: float = 0.8,
    repeat_penalty: float = 1.1,
    tools: list | None = None,
    tag: str = "",
) -> ChatResult:
    cfg = settings()
    body: dict = {
        "messages": messages,
        "temperature": temperature,
        "topP": top_p,
        "repeatPenalty": repeat_penalty,
    }
    if tools:
        body["tools"] = tools
        body["toolChoice"] = "auto"
    else:
        body["maxTokens"] = max_tokens

    status, payload, latency = _post(
        cfg.clova_url, {"Authorization": f"Bearer {cfg.clova_api_key}"}, body
    )
    if status != 200:
        raise RuntimeError(f"CLOVA 호출 실패 {status}: {payload}")

    result = payload.get("result") or {}
    message = result.get("message") or {}
    usage = result.get("usage") or {}
    record = ChatResult(
        text=message.get("content", ""),
        prompt_tokens=usage.get("promptTokens", 0),
        completion_tokens=usage.get("completionTokens", 0),
        latency_s=latency,
        finish=result.get("finishReason", ""),
        tool_calls=message.get("toolCalls"),
        tag=tag,
    )
    CALL_LOG.add(record)
    return record
