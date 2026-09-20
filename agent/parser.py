"""
parser.py

Parses raw Gemini responses (as returned by agent/llm_client.py's
LLMClient.call()) into a structured, provider-agnostic shape the rest of
the agent can work with: plain text reasoning, plus a list of requested
tool calls.

This is where "LLM response parsing to extract tool calls and reasoning
traces" (Day 4, task 3) actually happens. Keeping it in its own module
means agent/core.py's control-flow logic never has to know the shape of a
raw google.genai response object -- it only ever sees ParsedResponse.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ToolCallRequest:
    """One tool call the LLM asked to make."""

    name: str
    args: Dict[str, Any]


@dataclass
class ParsedResponse:
    """
    The result of parsing one LLM turn.

    `text` is the reasoning/answer text in this turn, if any (a turn can
    have text AND tool calls, or just one of the two).
    `tool_calls` is every function call the LLM requested in this turn --
    Gemini can request more than one in parallel.
    """

    text: Optional[str]
    tool_calls: List[ToolCallRequest] = field(default_factory=list)

    @property
    def has_tool_calls(self) -> bool:
        return len(self.tool_calls) > 0

    def to_model_message(self) -> Dict[str, Any]:
        """
        Convert this parsed turn back into the lightweight message-dict
        format LLMClient expects, so it can be appended to the running
        conversation history as a "model" turn.
        """
        parts: List[Dict[str, Any]] = []
        if self.text:
            parts.append({"text": self.text})
        for call in self.tool_calls:
            parts.append({"function_call": {"name": call.name, "args": call.args}})
        return {"role": "model", "parts": parts}


def parse_response(response: Any) -> ParsedResponse:
    """
    Parse a raw google.genai response object into a ParsedResponse.

    Gemini's response.candidates[0].content.parts is a list where each
    part is EITHER a text part OR a function_call part (never both on the
    same part, though a turn can contain multiple parts of each kind).
    """
    text_chunks: List[str] = []
    tool_calls: List[ToolCallRequest] = []

    candidate = response.candidates[0]
    parts = candidate.content.parts or []

    for part in parts:
        if getattr(part, "function_call", None) is not None:
            fc = part.function_call
            tool_calls.append(ToolCallRequest(name=fc.name, args=dict(fc.args or {})))
        elif getattr(part, "text", None):
            text_chunks.append(part.text)

    combined_text = "\n".join(text_chunks) if text_chunks else None
    return ParsedResponse(text=combined_text, tool_calls=tool_calls)


def parse_json_from_text(text: str) -> Optional[Any]:
    """
    Best-effort extraction of a JSON value from LLM output text.

    LLMs asked to "respond with ONLY a JSON array/object" occasionally
    still wrap it in markdown code fences or add a stray sentence before
    or after it. This strips common wrapping before falling back to a
    direct parse, and returns None (rather than raising) if nothing
    parseable is found -- callers are expected to handle that gracefully
    (e.g. by falling back to a single-task plan) rather than crashing the
    whole research run over a formatting slip.
    """
    if text is None:
        return None

    stripped = text.strip()

    # Strip a leading ```json / ``` fence and a trailing ``` fence, if present.
    fence_match = re.match(r"^```(?:json)?\s*(.*?)\s*```$", stripped, re.DOTALL)
    if fence_match:
        stripped = fence_match.group(1).strip()

    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass

    # Last resort: find the first '[' or '{' and the matching last ']' or '}'.
    for open_char, close_char in (("[", "]"), ("{", "}")):
        start = stripped.find(open_char)
        end = stripped.rfind(close_char)
        if start != -1 and end != -1 and end > start:
            candidate = stripped[start : end + 1]
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                continue

    return None
