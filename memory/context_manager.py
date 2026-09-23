"""
context_manager.py

Short-term memory management: keeping a single research run's conversation
within the reasoning model's context/token budget.

This is the "real" home for logic that started as an emergency stopgap
directly inside agent/core.py (added after a live run against Groq's free
tier hit a 413 "request too large" -- see the project's build notes). It's
promoted here, as its own module, per architecture_specification.md
Section 4.1's guidance: "Strategies for managing [short-term memory]
include summarization of earlier steps, selective retention of key
findings, and chunking of large tool outputs."

What this module does NOT do: permanently discard information. Trimmed
tool-call turns are dropped from the live conversation (to fit the next
LLM call), but agent/core.py is responsible for persisting the full,
untrimmed trace to episodic memory (memory/episodic.py) before any
trimming happens -- so nothing is genuinely lost, only removed from the
model's immediate context window.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


@dataclass
class ContextManager:
    """
    Usage:
        cm = ContextManager()
        content = cm.truncate_tool_result(json.dumps(payload))
        cm.trim_conversation_if_needed(conversation)  # mutates in place
    """

    max_tool_result_chars: int = 800
    max_conversation_chars_estimate: int = 20000  # ~5,000 tokens at ~4 chars/token
    min_kept_recent_turns: int = 6

    def truncate_tool_result(self, content: str) -> str:
        """Cap a single tool result's serialized size before it enters
        conversation history -- a single large web-search or filing-text
        result shouldn't dominate the token budget."""
        if len(content) <= self.max_tool_result_chars:
            return content
        return (
            content[: self.max_tool_result_chars]
            + f"... [truncated {len(content) - self.max_tool_result_chars} chars]"
        )

    def estimate_tokens(self, conversation: List[Dict[str, Any]]) -> int:
        """Rough token estimate (chars / 4) -- good enough to trigger
        trimming proactively; not meant to be exact."""
        return len(json.dumps(conversation)) // 4

    def trim_conversation_if_needed(self, conversation: List[Dict[str, Any]]) -> bool:
        """
        If the conversation has grown past the size budget, drop the
        OLDEST turns (keeping the original query intact at the start, and
        the most recent turns intact at the end) until it fits. Mutates
        `conversation` in place. Returns True if any trimming happened.

        This is a blunt but effective safeguard for a single run's
        immediate context window. It is NOT a substitute for long-term
        memory: agent/core.py logs the untrimmed trace to episodic memory
        before this runs, so trimmed detail remains queryable/auditable --
        it just isn't in the live conversation the model sees next.
        """
        if len(json.dumps(conversation)) <= self.max_conversation_chars_estimate:
            return False

        dropped_any = False
        while (
            len(json.dumps(conversation)) > self.max_conversation_chars_estimate
            and len(conversation) > self.min_kept_recent_turns + 1
        ):
            # index 1 is the oldest droppable turn (index 0 is the original query)
            del conversation[1]
            dropped_any = True

        if dropped_any:
            conversation.insert(
                1,
                {
                    "role": "user",
                    "content": (
                        "[Note: some earlier tool results were trimmed from this "
                        "conversation to stay within the model's context budget. "
                        "Continue researching with what remains.]"
                    ),
                },
            )
            logger.info(
                "Trimmed conversation history to stay under the token budget "
                "(estimated ~%d tokens after trim).",
                self.estimate_tokens(conversation),
            )

        return dropped_any
