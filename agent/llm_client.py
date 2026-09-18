"""
llm_client.py

Thin wrapper around the Groq API (via the official `groq` SDK) that adds
the two things every call in this project needs and the raw SDK doesn't
give you for free:

  1. Retry with exponential backoff on transient errors (rate limits,
     timeouts, 5xx) -- per architecture_specification.md Section A4.3.
  2. Token usage tracking across the whole agent run.

Groq was chosen as the reasoning engine (replacing an earlier Gemini-based
version) after repeated free-tier instability on Google's side: models
renamed/deprecated mid-project, a 20-requests-PER-DAY cap on the only
model our account could still reach, and a newer model restricted to
"existing users only." Groq's free tier has been consistently generous
(30 requests/minute, no similar account restrictions observed) and its
API is OpenAI-compatible, which actually simplifies this client
considerably: no custom function-declaration types, no opaque
"thought signature" tokens to round-trip through conversation history --
plain OpenAI-style messages and tool_calls, which every tool schema in
tools/schemas/*.json already matches.

This module is intentionally the ONLY place that imports the `groq` SDK
directly. Every other module calls `LLMClient`, never the SDK -- so if the
reasoning engine ever needs to switch providers again, this is the one
file that changes.

Message format used throughout this project (standard OpenAI/Groq shape):

    {"role": "system" | "user" | "assistant" | "tool", "content": "..."}

    # an assistant turn that requests tool calls also carries:
    {"role": "assistant", "content": None or "...", "tool_calls": [
        {"id": "...", "type": "function",
         "function": {"name": "...", "arguments": "<json string>"}}
    ]}

    # a tool result is sent back referencing that same call's id:
    {"role": "tool", "tool_call_id": "...", "content": "<string result>"}

agent/parser.py builds these shapes from a raw Groq response; agent/core.py
appends them to the running conversation.
"""

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from groq import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    BadRequestError,
    Groq,
    InternalServerError,
    RateLimitError,
)

from config.settings import settings
from tools.http_utils import RateLimiter

logger = logging.getLogger(__name__)


class LLMCallFailedError(Exception):
    """Raised when every retry attempt for an LLM call has been exhausted."""


@dataclass
class TokenUsage:
    """Running token usage totals for one agent run (or the whole process)."""

    input_tokens: int = 0
    output_tokens: int = 0
    call_count: int = 0
    per_call_history: List[Dict[str, int]] = field(default_factory=list)

    def record(self, input_tokens: int, output_tokens: int) -> None:
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        self.call_count += 1
        self.per_call_history.append(
            {"input_tokens": input_tokens, "output_tokens": output_tokens}
        )

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def summary(self) -> Dict[str, Any]:
        return {
            "total_calls": self.call_count,
            "total_input_tokens": self.input_tokens,
            "total_output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
        }


def tool_schema_to_groq_tool(schema: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convert one of this project's tool schemas into Groq/OpenAI's tool
    format. Our schemas (tools/schemas/*.json) are already written in this
    shape's "function" sub-object, so this is a one-line wrap -- unlike the
    Gemini version of this client, no field-by-field translation is needed.
    """
    return {
        "type": "function",
        "function": {
            "name": schema["name"],
            "description": schema.get("description", ""),
            "parameters": schema.get("parameters", {"type": "object", "properties": {}}),
        },
    }


def _extract_retry_after_seconds(exc: RateLimitError) -> Optional[float]:
    """
    Groq (like OpenAI) returns a `Retry-After` header on 429 responses.
    Prefer honoring that exact value over guessing our own backoff timing.
    Returns None if it can't be found, so the caller falls back to its own
    exponential backoff.
    """
    response = getattr(exc, "response", None)
    if response is None:
        return None
    retry_after = response.headers.get("retry-after")
    if retry_after is None:
        return None
    try:
        return float(retry_after)
    except ValueError:
        return None


class LLMClient:
    """
    Wraps the Groq API with retry-with-backoff and token tracking.

    Usage:
        client = LLMClient()
        response = client.call(
            messages=[{"role": "user", "content": "Research AAPL"}],
            tool_schemas=registry.get_tool_definitions(),
        )
    """

    # Errors worth retrying -- a transient server/connection/timeout issue
    # can succeed on retry. RateLimitError is handled separately below
    # since it carries its own suggested wait time.
    _RETRYABLE_EXCEPTIONS = (InternalServerError, APIConnectionError, APITimeoutError)

    # Groq's free tier allows 30 requests/minute (org-wide, not per-model,
    # and with no account-age restrictions observed -- unlike the Gemini
    # tier this replaced). Spacing calls 2.2 seconds apart caps at ~27
    # calls/minute, safely under that ceiling with a small margin.
    _rate_limiter = RateLimiter(min_interval_seconds=2.2)

    def __init__(self, api_key: Optional[str] = None) -> None:
        settings.validate_required_for_live_run()
        self._client = Groq(api_key=api_key or settings.groq_api_key)
        self.usage = TokenUsage()

    def call(
        self,
        messages: List[Dict[str, Any]],
        system: Optional[str] = None,
        tool_schemas: Optional[List[Dict[str, Any]]] = None,
        max_tokens: int = 1024,
        model: Optional[str] = None,
    ):
        """
        Make a single LLM call, retrying transient failures with
        exponential backoff + jitter, and recording token usage on success.

        `messages` uses this project's OpenAI/Groq-compatible message
        format (see module docstring). If `system` is given, it's
        prepended as a system message for this call only -- it does not
        need to be stored in the caller's persistent conversation list.

        `max_tokens` defaults to 1024, not a larger value, deliberately --
        Groq's free tier caps gpt-oss-120b/20b at 8,000 tokens PER MINUTE
        shared across input and output. Requesting a large completion
        budget on every call eats into that shared budget fast, especially
        alongside a growing multi-turn research conversation. 1024 is
        enough for this project's reasoning/planning/report text; raise it
        per-call if a specific step genuinely needs more.
        """
        model = model or settings.groq_model
        delay = settings.retry_initial_delay_seconds

        full_messages = list(messages)
        if system:
            full_messages = [{"role": "system", "content": system}] + full_messages

        request_kwargs: Dict[str, Any] = {
            "model": model,
            "messages": full_messages,
            "max_tokens": max_tokens,
        }
        if tool_schemas:
            request_kwargs["tools"] = [tool_schema_to_groq_tool(s) for s in tool_schemas]

        for attempt in range(1, settings.max_retry_attempts + 1):
            self._rate_limiter.wait()
            try:
                response = self._client.chat.completions.create(**request_kwargs)

                usage = response.usage
                self.usage.record(
                    input_tokens=usage.prompt_tokens or 0,
                    output_tokens=usage.completion_tokens or 0,
                )
                logger.debug(
                    "LLM call succeeded on attempt %d (in=%d, out=%d tokens)",
                    attempt,
                    usage.prompt_tokens or 0,
                    usage.completion_tokens or 0,
                )
                return response

            except self._RETRYABLE_EXCEPTIONS as exc:
                if attempt == settings.max_retry_attempts:
                    logger.error("LLM call failed after %d attempts: %s", attempt, exc)
                    raise LLMCallFailedError(
                        f"LLM call failed after {attempt} attempts: {exc}"
                    ) from exc

                jitter = random.uniform(0, 0.5)
                sleep_time = delay + jitter
                logger.warning(
                    "LLM call attempt %d/%d failed (%s). Retrying in %.2fs...",
                    attempt,
                    settings.max_retry_attempts,
                    type(exc).__name__,
                    sleep_time,
                )
                time.sleep(sleep_time)
                delay *= 2

            except RateLimitError as exc:
                if attempt == settings.max_retry_attempts:
                    logger.error("Rate limit exceeded after %d attempts: %s", attempt, exc)
                    raise LLMCallFailedError(
                        f"Rate limit exceeded after {attempt} attempts: {exc}"
                    ) from exc

                suggested_delay = _extract_retry_after_seconds(exc)
                sleep_time = suggested_delay if suggested_delay is not None else delay
                sleep_time += random.uniform(0, 0.5)
                logger.warning(
                    "LLM call attempt %d/%d hit the rate limit (429). Retrying in %.2fs...",
                    attempt,
                    settings.max_retry_attempts,
                    sleep_time,
                )
                time.sleep(sleep_time)
                delay *= 2

            except BadRequestError as exc:
                # A malformed request will fail identically every time
                # (bad schema, invalid model name, etc.) -- fail fast
                # instead of burning the retry budget.
                logger.error("Non-retryable bad request: %s", exc)
                raise LLMCallFailedError(f"Non-retryable bad request: {exc}") from exc

            except APIStatusError as exc:
                # Catch-all for any other 4xx (auth failure, not found,
                # etc.) -- also non-retryable.
                logger.error("Non-retryable API error: %s", exc)
                raise LLMCallFailedError(f"Non-retryable API error: {exc}") from exc

        raise LLMCallFailedError("LLM call failed for an unknown reason.")

    def get_usage_summary(self) -> Dict[str, Any]:
        return self.usage.summary()
