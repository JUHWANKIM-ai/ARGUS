"""Single entry point for HyperCLOVA X calls.

API constraints:
  HCX-005 requires the v3 endpoint; v1 returns 40084.
  Keys prefixed `nv-` accept Bearer authentication only; legacy headers
  return 40104.
  `tools` and `maxTokens` cannot be specified together; returns 40001.
"""
from __future__ import annotations

import json
import re
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


class _PostResult(tuple):
    """The (status, payload, latency) triple, with response headers attached.

    Unpacking into three values works exactly as before, so every existing
    caller of `_post` (including test doubles that stand in for it) is
    unaffected. New code that needs the response headers reads the
    `.headers` attribute instead of a fourth unpacked value.
    """

    def __new__(cls, status, payload, latency, headers):
        self = super().__new__(cls, (status, payload, latency))
        self.headers = headers
        return self


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
            payload = json.loads(resp.read().decode("utf-8"))
            return _PostResult(resp.status, payload, time.time() - t0,
                                dict(resp.headers.items()))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        resp_headers = dict(exc.headers.items()) if exc.headers else {}
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = {"raw": raw}
        return _PostResult(exc.code, payload, time.time() - t0, resp_headers)


# --- Chat rate-limit retry ----------------------------------------------------
# Only 429 is retried. Every other non-200 status raises immediately, unchanged
# from before: swallowing a real defect (a bad request, an auth failure, a
# server bug) would hide it instead of surfacing it.
# See docs/design-rationale.md#chat-rate-limit

#: Total attempts for one `chat` call, the first try plus retries.
_RETRY_ATTEMPTS = 3
#: Wait applied when the response carries no usable reset header.
_FALLBACK_WAIT_S = 60.0
#: Ceiling on a single wait, so a malformed or oversized header value cannot
#: stall a question by itself.
_MAX_WAIT_S = 65.0
#: Ceiling on the time spent waiting across all attempts of one call.
_MAX_TOTAL_WAIT_S = 150.0
#: Header names checked for a reset hint, in order. The first one present and
#: parseable wins.
_RESET_HEADER_CANDIDATES = ("x-ratelimit-reset-requests", "retry-after")

#: Sleep hook. A module attribute rather than a `chat` parameter so tests can
#: replace it without changing the call signature every caller already uses.
_sleep = time.sleep

_DURATION_RE = re.compile(
    r"(?:(\d+(?:\.\d+)?)h)?(?:(\d+(?:\.\d+)?)m)?(?:(\d+(?:\.\d+)?)s)?$")


def _parse_duration_s(raw: str) -> float | None:
    """Parse a rate-limit reset value into seconds.

    Accepts a bare number ("53"), a number with a seconds suffix ("53s"), and
    a compound duration ("1m30s"). Returns None when the text matches none of
    these, so the caller falls back to a fixed wait instead of guessing.
    """
    text = str(raw).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        pass
    match = _DURATION_RE.fullmatch(text)
    if not match or not any(match.groups()):
        return None
    hours, minutes, seconds = (float(g) if g else 0.0 for g in match.groups())
    return hours * 3600 + minutes * 60 + seconds


def _reset_wait_s(headers: dict) -> float | None:
    """Seconds until the rate limit window resets, read from the response
    headers. Returns None when no candidate header is present or parseable.
    """
    if not headers:
        return None
    lower = {str(k).lower(): v for k, v in headers.items()}
    for name in _RESET_HEADER_CANDIDATES:
        raw = lower.get(name)
        if raw is None:
            continue
        wait = _parse_duration_s(raw)
        if wait is not None:
            return wait
    return None


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

    auth_headers = {"Authorization": f"Bearer {cfg.clova_api_key}"}
    total_wait = 0.0
    for attempt in range(_RETRY_ATTEMPTS):
        outcome = _post(cfg.clova_url, auth_headers, body)
        status, payload, latency = outcome
        if status != 429 or attempt == _RETRY_ATTEMPTS - 1:
            break
        wait = _reset_wait_s(getattr(outcome, "headers", None) or {})
        if wait is None:
            wait = _FALLBACK_WAIT_S
        wait = max(0.0, min(wait, _MAX_WAIT_S))
        if total_wait + wait > _MAX_TOTAL_WAIT_S:
            break
        total_wait += wait
        _sleep(wait)
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
