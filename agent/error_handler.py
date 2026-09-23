"""
error_handler.py

Centralized error categorization and structured logging for ARA-1.

Implements the error taxonomy from architecture_specification.md Section A4.2:
  - Tool Execution Errors (API down, rate limit, auth, malformed, timeout)
  - Reasoning Errors (hallucination risk, circular reasoning, premature conclusion)
  - Data Quality Errors (stale data, conflicting sources, misattribution)

Every error that passes through here becomes an ErrorEvent dataclass,
which is:
  1. Logged to the Python logger (so it appears in trace logs)
  2. Appended to the run's error registry (for AB-2 Error Recovery Rate metric)
  3. Returned with a suggested recovery action

This module is the ONLY place error categorization logic lives -- agent/core.py
and the tools themselves just raise or return failures; this handler decides
what category they fall into and what to do about them.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Error taxonomy
# ---------------------------------------------------------------------------

class ErrorCategory(str, Enum):
    TOOL_API_UNAVAILABLE = "tool_api_unavailable"
    TOOL_RATE_LIMIT = "tool_rate_limit"
    TOOL_AUTH_FAILURE = "tool_auth_failure"
    TOOL_MALFORMED_RESPONSE = "tool_malformed_response"
    TOOL_TIMEOUT = "tool_timeout"
    TOOL_VALIDATION = "tool_validation"
    REASONING_HALLUCINATION_RISK = "reasoning_hallucination_risk"
    REASONING_CIRCULAR = "reasoning_circular"
    REASONING_PREMATURE_CONCLUSION = "reasoning_premature_conclusion"
    DATA_STALE = "data_stale"
    DATA_CONFLICT = "data_conflict"
    DATA_MISATTRIBUTION = "data_misattribution"
    DATA_UNIT_CONFUSION = "data_unit_confusion"
    UNKNOWN = "unknown"


class RecoveryAction(str, Enum):
    RETRY = "retry"               # Same tool, retry with backoff
    USE_FALLBACK = "use_fallback" # Try the registered fallback tool
    SKIP_AND_CONTINUE = "skip"   # Note the gap, continue without this data
    REPLAN = "replan"             # Ask the LLM to revise the research plan
    ABORT = "abort"               # Unrecoverable; return partial results


@dataclass
class ErrorEvent:
    """A single error encountered during an agent run."""
    category: ErrorCategory
    tool_name: str
    message: str
    recovery_action: RecoveryAction
    recovered: bool = False        # Set to True if recovery succeeded
    timestamp: str = ""
    context: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "category": self.category.value,
            "tool_name": self.tool_name,
            "message": self.message,
            "recovery_action": self.recovery_action.value,
            "recovered": self.recovered,
            "timestamp": self.timestamp,
            "context": self.context,
        }


# ---------------------------------------------------------------------------
# ErrorHandler
# ---------------------------------------------------------------------------

class ErrorHandler:
    """
    Categorizes errors from tool results and agent reasoning, suggests
    recovery actions, and maintains a run-level error registry.

    Usage::

        handler = ErrorHandler()
        event = handler.handle_tool_failure("sec_filing_search", "HTTP 503", args={})
        if event.recovery_action == RecoveryAction.USE_FALLBACK:
            # try the fallback tool
            ...
        # At end of run:
        stats = handler.get_stats()   # for AB-2 metric
    """

    def __init__(self) -> None:
        self._events: List[ErrorEvent] = []

    # ------------------------------------------------------------------ #
    # Tool failure classification
    # ------------------------------------------------------------------ #

    def handle_tool_failure(
        self,
        tool_name: str,
        error_message: str,
        args: Optional[Dict[str, Any]] = None,
        fallback_available: bool = True,
    ) -> ErrorEvent:
        """
        Classify a tool failure and recommend a recovery action.
        The ErrorEvent is appended to the internal registry and returned.
        """
        category, action = _classify_tool_error(error_message, fallback_available)
        event = ErrorEvent(
            category=category,
            tool_name=tool_name,
            message=error_message,
            recovery_action=action,
            context={"args": args or {}},
        )
        self._events.append(event)
        logger.warning(
            "ErrorHandler [%s] tool='%s': %s → recovery=%s",
            category.value, tool_name, error_message[:120], action.value,
        )
        return event

    def handle_reasoning_issue(
        self,
        issue_type: str,
        detail: str,
    ) -> ErrorEvent:
        """Flag a reasoning-level issue (hallucination risk, circular calls, etc.)."""
        category = _classify_reasoning_issue(issue_type)
        action = RecoveryAction.REPLAN if category == ErrorCategory.REASONING_CIRCULAR else RecoveryAction.SKIP_AND_CONTINUE
        event = ErrorEvent(
            category=category,
            tool_name="<reasoning>",
            message=detail,
            recovery_action=action,
        )
        self._events.append(event)
        logger.warning(
            "ErrorHandler [%s] reasoning issue: %s", category.value, detail[:120]
        )
        return event

    def mark_recovered(self, event: ErrorEvent) -> None:
        """Call this when recovery for `event` succeeded."""
        event.recovered = True
        logger.info(
            "ErrorHandler: recovered from [%s] on tool='%s'.",
            event.category.value, event.tool_name,
        )

    # ------------------------------------------------------------------ #
    # Statistics (for AB-2 Error Recovery Rate)
    # ------------------------------------------------------------------ #

    def get_stats(self) -> Dict[str, Any]:
        """
        Returns a dict suitable for AB-2 metric calculation:
          error_recovery_rate = recovered_count / total_count
        """
        total = len(self._events)
        recovered = sum(1 for e in self._events if e.recovered)
        by_category: Dict[str, int] = {}
        for e in self._events:
            by_category[e.category.value] = by_category.get(e.category.value, 0) + 1

        return {
            "total_errors": total,
            "recovered_errors": recovered,
            "error_recovery_rate": round(recovered / total, 3) if total > 0 else 1.0,
            "by_category": by_category,
        }

    def get_events(self) -> List[ErrorEvent]:
        return list(self._events)

    def clear(self) -> None:
        self._events.clear()


# ---------------------------------------------------------------------------
# Private classification logic
# ---------------------------------------------------------------------------

_RATE_LIMIT_KEYWORDS = ("rate limit", "429", "too many requests", "quota", "throttle")
_AUTH_KEYWORDS = ("auth", "unauthorized", "403", "api key", "invalid key", "expired")
_TIMEOUT_KEYWORDS = ("timeout", "timed out", "connection reset", "read timeout")
_UNAVAILABLE_KEYWORDS = ("503", "502", "500", "unavailable", "down", "connection error")
_MALFORMED_KEYWORDS = ("json", "parse", "decode", "unexpected", "schema", "missing field")


def _classify_tool_error(
    message: str,
    fallback_available: bool,
) -> tuple[ErrorCategory, RecoveryAction]:
    msg = message.lower()

    if any(k in msg for k in _RATE_LIMIT_KEYWORDS):
        return ErrorCategory.TOOL_RATE_LIMIT, RecoveryAction.RETRY

    if any(k in msg for k in _AUTH_KEYWORDS):
        # Auth failures won't succeed on retry -- go straight to fallback
        action = RecoveryAction.USE_FALLBACK if fallback_available else RecoveryAction.SKIP_AND_CONTINUE
        return ErrorCategory.TOOL_AUTH_FAILURE, action

    if any(k in msg for k in _TIMEOUT_KEYWORDS):
        action = RecoveryAction.RETRY
        return ErrorCategory.TOOL_TIMEOUT, action

    if any(k in msg for k in _UNAVAILABLE_KEYWORDS):
        action = RecoveryAction.USE_FALLBACK if fallback_available else RecoveryAction.SKIP_AND_CONTINUE
        return ErrorCategory.TOOL_API_UNAVAILABLE, action

    if any(k in msg for k in _MALFORMED_KEYWORDS):
        action = RecoveryAction.USE_FALLBACK if fallback_available else RecoveryAction.SKIP_AND_CONTINUE
        return ErrorCategory.TOOL_MALFORMED_RESPONSE, action

    if "validation" in msg or "required" in msg or "missing" in msg:
        return ErrorCategory.TOOL_VALIDATION, RecoveryAction.SKIP_AND_CONTINUE

    action = RecoveryAction.USE_FALLBACK if fallback_available else RecoveryAction.SKIP_AND_CONTINUE
    return ErrorCategory.UNKNOWN, action


def _classify_reasoning_issue(issue_type: str) -> ErrorCategory:
    mapping = {
        "hallucination": ErrorCategory.REASONING_HALLUCINATION_RISK,
        "circular": ErrorCategory.REASONING_CIRCULAR,
        "premature": ErrorCategory.REASONING_PREMATURE_CONCLUSION,
        "stale": ErrorCategory.DATA_STALE,
        "conflict": ErrorCategory.DATA_CONFLICT,
        "misattribution": ErrorCategory.DATA_MISATTRIBUTION,
        "unit": ErrorCategory.DATA_UNIT_CONFUSION,
    }
    for key, cat in mapping.items():
        if key in issue_type.lower():
            return cat
    return ErrorCategory.UNKNOWN
