"""
llm_client.py

Thin wrapper around the Google Gemini API (via the `google-genai` SDK) that
adds the three things every call in this project needs and the raw SDK
doesn't give you for free:

  1. Retry with exponential backoff on transient errors (rate limits,
     timeouts, 5xx) -- per architecture_specification.md Section A4.3.
  2. Token usage tracking across the whole agent run.
  3. A simple, provider-agnostic message format that supports multi-turn
     tool-calling conversations (text + function_call + function_response
     parts), so agent/core.py never has to import google.genai directly.

Gemini was chosen as the reasoning engine because it has a genuinely free
API tier that still supports function/tool calling.

This module is intentionally the ONLY place that imports the `google.genai`
SDK directly.

Message format used throughout this project (NOT the raw Gemini format):

    {"role": "user" | "model", "parts": [<part>, ...]}

where <part> is one of:
    {"text": "..."}
    {"function_call": {"name": "...", "args": {...}}}
    {"function_response": {"name": "...", "response": {...}}}

agent/parser.py converts a raw Gemini response back into this same
lightweight format so the round-trip stays provider-agnostic.
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
    """Convert one of this project's tool schemas into a Gemini FunctionDeclaration."""
    return types.FunctionDeclaration(
        name=schema["name"],
        description=schema.get("description", ""),
        parameters=schema.get("parameters", {"type": "object", "properties": {}}),
    )


def _part_dict_to_gemini_part(part: Dict[str, Any]) -> types.Part:
    """Convert one of this project's lightweight part-dicts into a genai Part."""
    if "text" in part:
        return types.Part(text=part["text"])
    if "function_call" in part:
        fc = part["function_call"]
        return types.Part(
            function_call=types.FunctionCall(name=fc["name"], args=fc.get("args", {}))
        )
    if "function_response" in part:
        fr = part["function_response"]
        return types.Part(
            function_response=types.FunctionResponse(
                name=fr["name"], response=fr.get("response", {})
            )
        )
    raise ValueError(f"Unrecognized part shape: {part!r}")


def _message_dict_to_gemini_content(message: Dict[str, Any]) -> types.Content:
    """Convert one of this project's message dicts into a genai Content object."""
    # Backward-compatible convenience: a plain {"role": ..., "content": "..."}
    # message (as used for simple single-turn calls) is treated as one text part.
    if "content" in message and "parts" not in message:
        parts = [{"text": message["content"]}]
    else:
        parts = message["parts"]

    return types.Content(
        role=message["role"],
        parts=[_part_dict_to_gemini_part(p) for p in parts],
    )


class LLMClient:
    """
    Wraps the Gemini API with retry-with-backoff, token tracking, and a
    provider-agnostic multi-turn message format.

    Usage (simple, single-turn):
        client = LLMClient()
        response = client.call(
            messages=[{"role": "user", "content": "Hello"}],
        )

    Usage (multi-turn, with tools -- what agent/core.py uses):
        response = client.call(
            messages=[
                {"role": "user", "parts": [{"text": "Research AAPL"}]},
                {"role": "model", "parts": [{"function_call": {...}}]},
                {"role": "user", "parts": [{"function_response": {...}}]},
            ],
            tool_schemas=registry.get_tool_definitions(),
        )
    """

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
        Make a single LLM call (which may itself be one turn of a longer
        multi-turn conversation the caller is managing), retrying transient
        failures with exponential backoff + jitter, and recording token
        usage on success.
        """
        model = model or settings.gemini_model
        delay = settings.retry_initial_delay_seconds

        contents = [_message_dict_to_gemini_content(m) for m in messages]

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
                delay *= 2

            except ClientError as exc:
                logger.error("Non-retryable client error: %s", exc)
                raise LLMCallFailedError(f"Non-retryable client error: {exc}") from exc

            except APIError as exc:
                logger.error("Gemini API error: %s", exc)
                raise LLMCallFailedError(f"Gemini API error: {exc}") from exc

        raise LLMCallFailedError("LLM call failed for an unknown reason.")

    def get_usage_summary(self) -> Dict[str, Any]:
        return self.usage.summary()
