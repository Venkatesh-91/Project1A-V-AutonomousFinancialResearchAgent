"""
circuit_breaker.py

Per-tool circuit breaker for ARA-1 -- Day 9 deliverable.

Implements the circuit breaker pattern described in architecture_specification.md
glossary: "detects cascading failures and stops routing requests to a failed
component after a threshold number of failures."

States:
  CLOSED   -- normal operation; calls pass through
  OPEN     -- tool is failing; calls are blocked and immediately fail-fast
  HALF_OPEN -- cooldown expired; one probe call allowed to test recovery

This prevents a single broken tool from burning the agent's entire retry
budget and blocking other tools from running.

Usage::

    cb = CircuitBreaker()
    if cb.can_call("sec_filing_search"):
        result = tool.run(...)
        if result.success:
            cb.record_success("sec_filing_search")
        else:
            cb.record_failure("sec_filing_search")
    else:
        # Tool is OPEN -- skip or use fallback immediately
        ...
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Optional

logger = logging.getLogger(__name__)


class BreakerState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class BreakerStatus:
    state: BreakerState
    failure_count: int
    last_failure_time: float    # monotonic
    success_count: int          # successes since last reset


class CircuitBreaker:
    """
    Per-tool circuit breaker with configurable thresholds.

    Args:
        failure_threshold:  Number of consecutive failures before tripping OPEN.
        cooldown_seconds:   How long to stay OPEN before allowing a probe.
        probe_success_count: Successes in HALF_OPEN needed to close the circuit.
    """

    def __init__(
        self,
        failure_threshold: int = 3,
        cooldown_seconds: float = 60.0,
        probe_success_count: int = 1,
    ) -> None:
        self._failure_threshold = failure_threshold
        self._cooldown = cooldown_seconds
        self._probe_success_count = probe_success_count
        self._breakers: Dict[str, BreakerStatus] = {}

    # ------------------------------------------------------------------ #
    # State machine
    # ------------------------------------------------------------------ #

    def _get_or_create(self, tool_name: str) -> BreakerStatus:
        if tool_name not in self._breakers:
            self._breakers[tool_name] = BreakerStatus(
                state=BreakerState.CLOSED,
                failure_count=0,
                last_failure_time=0.0,
                success_count=0,
            )
        return self._breakers[tool_name]

    def _effective_state(self, tool_name: str) -> BreakerState:
        """Return the real current state, transitioning OPEN → HALF_OPEN if cooldown expired."""
        status = self._get_or_create(tool_name)
        if status.state == BreakerState.OPEN:
            if time.monotonic() - status.last_failure_time >= self._cooldown:
                status.state = BreakerState.HALF_OPEN
                status.success_count = 0
                logger.info(
                    "CircuitBreaker: '%s' OPEN→HALF_OPEN (cooldown expired).", tool_name
                )
        return status.state

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def can_call(self, tool_name: str) -> bool:
        """
        Returns True if the tool should be called right now.
        - CLOSED   → True (normal operation)
        - HALF_OPEN → True (one probe allowed)
        - OPEN     → False (blocked)
        """
        state = self._effective_state(tool_name)
        if state == BreakerState.OPEN:
            logger.debug(
                "CircuitBreaker: '%s' is OPEN -- call blocked (failure_count=%d).",
                tool_name,
                self._breakers[tool_name].failure_count,
            )
            return False
        return True

    def record_success(self, tool_name: str) -> None:
        """Call this after a successful tool invocation."""
        status = self._get_or_create(tool_name)
        state = self._effective_state(tool_name)

        if state == BreakerState.HALF_OPEN:
            status.success_count += 1
            if status.success_count >= self._probe_success_count:
                status.state = BreakerState.CLOSED
                status.failure_count = 0
                logger.info(
                    "CircuitBreaker: '%s' HALF_OPEN→CLOSED (probe succeeded).", tool_name
                )
        elif state == BreakerState.CLOSED:
            # Reset failure count on success to prevent old failures from accumulating
            status.failure_count = max(0, status.failure_count - 1)

    def record_failure(self, tool_name: str) -> None:
        """Call this after a failed tool invocation."""
        status = self._get_or_create(tool_name)
        status.failure_count += 1
        status.last_failure_time = time.monotonic()

        state = self._effective_state(tool_name)
        if state in (BreakerState.CLOSED, BreakerState.HALF_OPEN):
            if status.failure_count >= self._failure_threshold:
                status.state = BreakerState.OPEN
                logger.warning(
                    "CircuitBreaker: '%s' tripped OPEN after %d consecutive failures.",
                    tool_name,
                    status.failure_count,
                )

    def get_state(self, tool_name: str) -> BreakerState:
        return self._effective_state(tool_name)

    def get_all_states(self) -> Dict[str, str]:
        """Return the current state of every tracked tool (for trace logging)."""
        return {
            name: self._effective_state(name).value
            for name in self._breakers
        }

    def reset(self, tool_name: str) -> None:
        """Manually reset a breaker to CLOSED (useful in tests)."""
        if tool_name in self._breakers:
            self._breakers[tool_name] = BreakerStatus(
                state=BreakerState.CLOSED,
                failure_count=0,
                last_failure_time=0.0,
                success_count=0,
            )

    def reset_all(self) -> None:
        self._breakers.clear()

    # ------------------------------------------------------------------ #
    # Challenge 8: failure simulation
    # ------------------------------------------------------------------ #

    def inject_failure(self, tool_name: str, failure_count: int = 3) -> None:
        """
        Simulate `failure_count` failures on `tool_name` to test the
        circuit breaker and fallback chain.  Used by Challenge 8 (50% tool
        failure simulation).
        """
        for _ in range(failure_count):
            self.record_failure(tool_name)
        logger.warning(
            "CircuitBreaker: injected %d simulated failures on '%s' (state=%s).",
            failure_count, tool_name, self.get_state(tool_name).value,
        )


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_breaker: Optional[CircuitBreaker] = None


def get_circuit_breaker() -> CircuitBreaker:
    global _breaker
    if _breaker is None:
        _breaker = CircuitBreaker()
    return _breaker
