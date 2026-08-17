"""Single entry point for HyperCLOVA X calls.

API constraints:
  HCX-005 requires the v3 endpoint; v1 returns 40084.
  Keys prefixed `nv-` accept Bearer authentication only; legacy headers
  return 40104.
  `tools` and `maxTokens` cannot be specified together; returns 40001.
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


# --- Request cache bypass signal (self-consistency sampling only) ---------------
#
# Raising the temperature does not diversify samples when the request body is
# identical. The runner keys its cache on sha256(url + body), so k samples of a
# self-consistency draw share one key and later samples replay the first
# response.
#
# The pipeline cannot import the runner, so the signal lives here as module
# state and the cache wrapper reads it. On paths without a cache installed
# nothing reads this flag and behaviour is unchanged.
#
# Both reads and writes are disabled while bypassing. Storing a bypassed
# response would let the next sample hit it and reintroduce the problem.

#: Nesting depth of active `no_cache` blocks. Zero means the cache is live.
_no_cache_depth = 0


def cache_bypassed() -> bool:
    """Whether the current call should bypass the request cache."""
    return _no_cache_depth > 0


class no_cache:
    """Context manager disabling both cache reads and writes.

    Nesting is counted rather than toggled so that leaving an inner block does
    not clear an enclosing bypass, including on exception paths.
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
