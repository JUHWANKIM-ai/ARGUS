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
