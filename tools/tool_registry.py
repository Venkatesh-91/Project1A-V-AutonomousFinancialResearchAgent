"""
tool_registry.py

Central tool registry for the Autonomous Financial Research Agent.

The registry is the single source of truth for:
  1. Tool metadata (JSON schemas, following the OpenAI/Anthropic function-calling spec)
  2. Tool dispatch (routing a tool call, by name, to its implementation)
  3. Input validation (checking a call's arguments against the tool's schema
     before executing it)
  4. A uniform result envelope so every tool -- regardless of what it wraps --
     returns the same shape of object, which is what lets the Executor,
     Synthesis Engine, and evaluation harness treat all tools identically.

This module has ZERO dependency on any specific LLM SDK. It's pure Python +
the standard library, so it can be unit tested without API keys and reused
whether the reasoning engine ends up being Claude, GPT, or anything else.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


SCHEMA_DIR = Path(__file__).parent / "schemas"


class ToolValidationError(Exception):
    """Raised when a tool call's arguments don't match its registered schema."""


class ToolNotFoundError(Exception):
    """Raised when the registry is asked to dispatch a tool name it doesn't know."""


@dataclass
class ToolResult:
    """
    Uniform result envelope every tool call returns.

    Every field here maps directly onto the provenance model described in
    the architecture specification (Section 7): every fact the agent uses
    downstream must carry its source, tier, and timestamp.
    """

    success: bool
    data: Any
    source_name: str
    source_tier: int  # 1 (highest trust) .. 5 (lowest trust)
    retrieved_at: str  # ISO-8601 timestamp
    fallback_used: bool = False
    error: Optional[str] = None
    latency_ms: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "data": self.data,
            "source_name": self.source_name,
            "source_tier": self.source_tier,
            "retrieved_at": self.retrieved_at,
            "fallback_used": self.fallback_used,
            "error": self.error,
            "latency_ms": self.latency_ms,
        }


@dataclass
class RegisteredTool:
    """A single entry in the registry: schema + implementation + fallback chain."""

    name: str
    schema: Dict[str, Any]
    implementation: Callable[..., ToolResult]
    fallback: Optional["RegisteredTool"] = None


class ToolRegistry:
    """
    The central catalog of every tool available to the agent.

    Usage:
        registry = ToolRegistry()
        registry.register("sec_filing_search", schema, sec_edgar.run)
        result = registry.dispatch("sec_filing_search", ticker="AAPL",
                                    filing_type="10-K")
    """

    def __init__(self) -> None:
        self._tools: Dict[str, RegisteredTool] = {}
        self._call_log: List[Dict[str, Any]] = []

    # ------------------------------------------------------------------ #
    # Registration
    # ------------------------------------------------------------------ #
    def register(
        self,
        name: str,
        schema: Dict[str, Any],
        implementation: Callable[..., ToolResult],
        fallback_of: Optional[str] = None,
    ) -> None:
        """
        Register a tool. If fallback_of is given, this tool is attached to
        the end of an already-registered tool's fallback chain instead of
        being registered as its own top-level entry.
        """
        entry = RegisteredTool(name=name, schema=schema, implementation=implementation)

        if fallback_of:
            if fallback_of not in self._tools:
                raise ToolNotFoundError(
                    f"Cannot attach fallback '{name}': primary tool "
                    f"'{fallback_of}' is not registered yet. Register the "
                    f"primary tool first."
                )
            node = self._tools[fallback_of]
            while node.fallback is not None:
                node = node.fallback
            node.fallback = entry
        else:
            self._tools[name] = entry

    def load_schema_from_file(self, tool_name: str) -> Dict[str, Any]:
        """Load a tool's JSON schema from tools/schemas/<tool_name>.json."""
        path = SCHEMA_DIR / f"{tool_name}.json"
        if not path.exists():
            raise FileNotFoundError(f"No schema file found for tool '{tool_name}' at {path}")
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    # ------------------------------------------------------------------ #
    # Introspection (what the LLM sees)
    # ------------------------------------------------------------------ #
    def get_tool_definitions(self) -> List[Dict[str, Any]]:
        """
        Return every top-level (primary) tool's schema, ready to inject into
        a system prompt or a `tools=[...]` API parameter. Fallback tools are
        NOT exposed to the LLM -- they're an internal reliability mechanism
        the registry invokes automatically on failure, not something the
        LLM should choose to call directly.
        """
        return [entry.schema for entry in self._tools.values()]

    def list_tool_names(self) -> List[str]:
        return list(self._tools.keys())

    # ------------------------------------------------------------------ #
    # Validation
    # ------------------------------------------------------------------ #
    def validate_arguments(self, tool_name: str, arguments: Dict[str, Any]) -> None:
        """
        Check `arguments` against the tool's JSON schema `parameters` block.
        Lightweight, dependency-free validation: required fields present,
        basic type checks, enum checks. Swap in the `jsonschema` package
        later if stricter validation becomes necessary.
        """
        tool = self._tools.get(tool_name)
        if tool is None:
            raise ToolNotFoundError(f"Unknown tool: '{tool_name}'")

        params_schema = tool.schema.get("parameters", {})
        properties = params_schema.get("properties", {})
        required = params_schema.get("required", [])

        missing = [f for f in required if f not in arguments]
        if missing:
            raise ToolValidationError(
                f"Tool '{tool_name}' call is missing required argument(s): {missing}"
            )

        type_map = {
            "string": str,
            "integer": int,
            "number": (int, float),
            "boolean": bool,
            "array": list,
            "object": dict,
        }
        for key, value in arguments.items():
            if key not in properties:
                raise ToolValidationError(
                    f"Tool '{tool_name}' received unexpected argument '{key}'"
                )
            expected_type = type_map.get(properties[key].get("type"))
            if expected_type and not isinstance(value, expected_type):
                raise ToolValidationError(
                    f"Tool '{tool_name}' argument '{key}' expected type "
                    f"'{properties[key].get('type')}' but got {type(value).__name__}"
                )
            enum = properties[key].get("enum")
            if enum and value not in enum:
                raise ToolValidationError(
                    f"Tool '{tool_name}' argument '{key}'={value!r} is not "
                    f"one of the allowed values: {enum}"
                )

    # ------------------------------------------------------------------ #
    # Dispatch
    # ------------------------------------------------------------------ #
    def dispatch(self, tool_name: str, **arguments: Any) -> ToolResult:
        """
        Validate and execute a tool call, walking the fallback chain on
        failure. Every attempt (success or failure, primary or fallback) is
        recorded in the call log -- the raw material for the "radical
        transparency" episodic logging the architecture spec calls for;
        Day 6 wires this into persistent SQLite storage.
        """
        tool = self._tools.get(tool_name)
        if tool is None:
            raise ToolNotFoundError(f"Unknown tool: '{tool_name}'")

        self.validate_arguments(tool_name, arguments)

        node: Optional[RegisteredTool] = tool
        attempt = 0
        last_result: Optional[ToolResult] = None

        while node is not None:
            attempt += 1
            start = time.perf_counter()
            try:
                result = node.implementation(**arguments)
                result.latency_ms = round((time.perf_counter() - start) * 1000, 2)
                result.fallback_used = attempt > 1
            except Exception as exc:  # noqa: BLE001 -- a failing tool must
                # never crash the whole agent run; capture it as a failed
                # ToolResult instead so the fallback chain (or graceful
                # degradation) can take over.
                result = ToolResult(
                    success=False,
                    data=None,
                    source_name=node.name,
                    source_tier=5,
                    retrieved_at=datetime.now(timezone.utc).isoformat(),
                    fallback_used=attempt > 1,
                    error=str(exc),
                    latency_ms=round((time.perf_counter() - start) * 1000, 2),
                )

            self._call_log.append(
                {
                    "tool_name": node.name,
                    "requested_tool": tool_name,
                    "attempt": attempt,
                    "arguments": arguments,
                    "result": result.to_dict(),
                }
            )

            last_result = result
            if result.success:
                return result

            node = node.fallback  # try the next link in the fallback chain

        # Every link in the chain failed. Return the last (worst) result
        # rather than raising, so the caller can implement graceful
        # degradation instead of the whole run crashing.
        return last_result  # type: ignore[return-value]

    def get_call_log(self) -> List[Dict[str, Any]]:
        return list(self._call_log)


def build_default_registry() -> ToolRegistry:
    """
    Convenience factory: builds a ToolRegistry with every stub tool in this
    project registered, schemas loaded from tools/schemas/*.json. Imports
    the tool modules lazily to avoid a circular import (a tool module never
    needs to know about the registry that will call it).
    """
    from tools import (
        sec_edgar,
        financial_api,
        web_search,
        news_sentiment,
        earnings,
        company_profile,
        peer_comparison,
        calculator,
        fact_checker,
        report_gen,
    )

    registry = ToolRegistry()

    tool_modules = {
        "sec_filing_search": sec_edgar,
        "financial_data_api": financial_api,
        "web_search": web_search,
        "news_sentiment": news_sentiment,
        "earnings_transcript": earnings,
        "company_profile": company_profile,
        "peer_comparison": peer_comparison,
        "calculation_engine": calculator,
        "fact_checker": fact_checker,
        "report_generator": report_gen,
    }

    for tool_name, module in tool_modules.items():
        schema = registry.load_schema_from_file(tool_name)
        registry.register(tool_name, schema, module.run)

    return registry
