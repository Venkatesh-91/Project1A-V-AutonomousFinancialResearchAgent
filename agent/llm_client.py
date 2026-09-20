"""
llm_client.py

Thin wrapper around the Google Gemini API (via the `google-genai` SDK) that
adds the two things every call in this project needs and the raw SDK
doesn't give you for free:

  1. Retry with exponential backoff on transient errors (rate limits,
     timeouts, 5xx) -- per architecture_specification.md Section A4.3:
     "initial retry delay 1 second, doubling with each attempt, max 5
     retries, with jitter."
  2. Token usage tracking across the whole agent run, so Day 12's token
     usage analysis and Day 13's cost-optimization work have real numbers
     to look at.

Gemini was chosen as the reasoning engine because it has a genuinely free
API tier (no billing required) that still supports function/tool calling,
which this project's tool registry depends on.

This module is intentionally the ONLY place that imports the `google.genai`
SDK directly. Every other module calls `LLMClient`, never the SDK -- so if
the reasoning engine ever needs to switch providers, this is the one file
that changes.
"""

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from google import genai
from google.genai import types
from google.genai.errors import APIError, ClientError, ServerError

from config.settings import settings

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


def tool_schema_to_gemini_function(schema: Dict[str, Any]) -> types.FunctionDeclaration:
    """
    Convert one of this project's tool schemas (OpenAI/Anthropic-style
    function-calling format, as produced by tools/tool_registry.py) into a
    Gemini FunctionDeclaration. Keeping this conversion in one small
    function means tool_registry.py itself stays provider-agnostic -- it
    doesn't need to know Gemini exists.
    """
    return types.FunctionDeclaration(
        name=schema["name"],
        description=schema.get("description", ""),
        parameters=schema.get("parameters", {"type": "object", "properties": {}}),
    )


class LLMClient:
    """
    Wraps the Gemini API with retry-with-backoff and token tracking.

    Usage:
        client = LLMClient()
        response = client.call(
            messages=[{"role": "user", "content": "Hello"}],
            tool_schemas=registry.get_tool_definitions(),
        )
    """

    # Errors worth retrying -- a transient server/rate-limit error can
    # succeed on retry. A ClientError (e.g. bad request, invalid API key)
    # will fail identically every time, so it's raised immediately instead
    # of wasting retry budget on a call that can never succeed.
    _RETRYABLE_EXCEPTIONS = (ServerError,)

    def __init__(self, api_key: Optional[str] = None) -> None:
        settings.validate_required_for_live_run()
        self._client = genai.Client(api_key=api_key or settings.gemini_api_key)
        self.usage = TokenUsage()

    def call(
        self,
        messages: List[Dict[str, Any]],
        system: Optional[str] = None,
        tool_schemas: Optional[List[Dict[str, Any]]] = None,
        max_tokens: int = 4096,
        model: Optional[str] = None,
    ):
        """
        Make a single LLM call, retrying transient failures with
        exponential backoff + jitter, and recording token usage on success.

        `messages` uses this project's simple internal format:
            [{"role": "user"|"assistant", "content": "..."}]
        which is converted to Gemini's expected `contents` format here, so
        the rest of the codebase never has to think about Gemini's specific
        message shape.
        """
        model = model or settings.gemini_model
        delay = settings.retry_initial_delay_seconds

        contents = [
            types.Content(
                role="model" if m["role"] == "assistant" else "user",
                parts=[types.Part.from_text(text=m["content"])],
            )
            for m in messages
        ]

        config_kwargs: Dict[str, Any] = {"max_output_tokens": max_tokens}
        if system:
            config_kwargs["system_instruction"] = system
        if tool_schemas:
            function_declarations = [tool_schema_to_gemini_function(s) for s in tool_schemas]
            config_kwargs["tools"] = [types.Tool(function_declarations=function_declarations)]

        generate_config = types.GenerateContentConfig(**config_kwargs)

        for attempt in range(1, settings.max_retry_attempts + 1):
            try:
                response = self._client.models.generate_content(
                    model=model,
                    contents=contents,
                    config=generate_config,
                )

                usage = response.usage_metadata
                self.usage.record(
                    input_tokens=usage.prompt_token_count or 0,
                    output_tokens=usage.candidates_token_count or 0,
                )
                logger.debug(
                    "LLM call succeeded on attempt %d (in=%d, out=%d tokens)",
                    attempt,
                    usage.prompt_token_count or 0,
                    usage.candidates_token_count or 0,
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
                delay *= 2  # exponential backoff

            except ClientError as exc:
                # Non-retryable: bad request, invalid API key, quota
                # exceeded permanently, etc. Retrying would just fail
                # identically every time.
                logger.error("Non-retryable client error: %s", exc)
                raise LLMCallFailedError(f"Non-retryable client error: {exc}") from exc

            except APIError as exc:
                # Catch-all for any other API error type the SDK raises.
                logger.error("Gemini API error: %s", exc)
                raise LLMCallFailedError(f"Gemini API error: {exc}") from exc

        raise LLMCallFailedError("LLM call failed for an unknown reason.")

    def get_usage_summary(self) -> Dict[str, Any]:
        return self.usage.summary()
