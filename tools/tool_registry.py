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
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


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
    def validate_arguments(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """
        Check `arguments` against the tool's JSON schema `parameters` block
        and return a CLEANED copy safe to pass to the tool implementation.

        Lightweight, dependency-free validation: required fields present,
        basic type checks, enum checks. Swap in the `jsonschema` package
        later if stricter validation becomes necessary.

        Design note: a MISSING required argument is a genuinely broken call
        and raises ToolValidationError -- there's no reasonable way to
        proceed. An UNRECOGNIZED extra argument, on the other hand, is
        something real LLMs actually do (observed in practice: an
        open-weight model calling web_search with an extra 'source' field
        no schema defined) and is not fatal -- the tool can still do
        useful work with the arguments it does recognize. Per the
        graceful-degradation principle in architecture_specification.md
        Section A4, this method drops unrecognized arguments (logging a
        warning so it's visible in the trace) rather than crashing the
        whole research run over it.
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
        cleaned: Dict[str, Any] = {}
        for key, value in arguments.items():
            if key not in properties:
                logger.warning(
                    "Tool '%s' call included unrecognized argument '%s' "
                    "(value=%r) -- dropping it and proceeding with the "
                    "arguments that are recognized.",
                    tool_name,
                    key,
                    value,
                )
                continue

            # A property's "type" may be a single string (e.g. "string") or
            # a list (e.g. ["string", "null"]) -- the latter is how this
            # project marks an OPTIONAL field as explicitly null-tolerant
            # (see tools/schemas/*.json). This matters in practice: models
            # commonly pass `null` for an optional parameter they're not
            # using rather than omitting it entirely, and some providers
            # (observed: Groq) validate tool-call arguments server-side
            # against the schema and reject the whole call with a 400 if
            # the schema doesn't explicitly allow null.
            declared_type = properties[key].get("type")
            allowed_type_names = declared_type if isinstance(declared_type, list) else [declared_type]

            if value is None:
                if "null" not in allowed_type_names:
                    raise ToolValidationError(
                        f"Tool '{tool_name}' argument '{key}' was null, but "
                        f"this field's schema does not allow null."
                    )
                # Drop the key entirely rather than passing None through --
                # the tool implementation's own Python default (e.g.
                # `num_results: int = 10`) is a safer fallback than a
                # literal None reaching code that expects a real value.
                continue

            non_null_type_names = [t for t in allowed_type_names if t != "null"]
            expected_types = tuple(
                type_map[t] for t in non_null_type_names if t in type_map
            )
            if expected_types and not isinstance(value, expected_types):
                raise ToolValidationError(
                    f"Tool '{tool_name}' argument '{key}' expected type "
                    f"'{declared_type}' but got {type(value).__name__}"
                )
            enum = properties[key].get("enum")
            if enum and value not in enum:
                raise ToolValidationError(
                    f"Tool '{tool_name}' argument '{key}'={value!r} is not "
                    f"one of the allowed values: {enum}"
                )
            cleaned[key] = value

        return cleaned

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

        arguments = self.validate_arguments(tool_name, arguments)

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
    Convenience factory: builds a ToolRegistry with every tool registered,
    schemas loaded from tools/schemas/*.json. Imports the tool modules
    lazily to avoid a circular import (a tool module never needs to know
    about the registry that will call it).

    As of Day 5, four tools (sec_filing_search, financial_data_api,
    web_search, news_sentiment) have REAL implementations backed by free
    public APIs/libraries (SEC EDGAR, yfinance, DuckDuckGo). Each is
    registered with its Day 2 mock implementation attached as a fallback,
    per architecture_specification.md Section 5.3 -- if the real call
    fails (network issue, rate limit, unexpected response shape), the
    registry automatically falls back to the mock rather than the whole
    tool call failing outright. `fallback_used=True` on the returned
    ToolResult makes this visible to the Executor and, later, the
    evaluation harness.

    The remaining tools (earnings_transcript, company_profile,
    peer_comparison, calculation_engine, fact_checker, report_generator)
    are still mock/deterministic implementations pending Day 7.
    """
    from tools import (
        sec_edgar,
        sec_edgar_mock,
        financial_api,
        financial_api_mock,
        web_search,
        web_search_mock,
        news_sentiment,
        news_sentiment_mock,
        earnings,
        company_profile,
        peer_comparison,
        calculator,
        fact_checker,
        report_gen,
        vector_db_search,
        vector_db_store,
    )

    registry = ToolRegistry()

    # Tools with a real implementation + a registered mock fallback.
    real_tools_with_fallback = {
        "sec_filing_search": (sec_edgar, sec_edgar_mock),
        "financial_data_api": (financial_api, financial_api_mock),
        "web_search": (web_search, web_search_mock),
        "news_sentiment": (news_sentiment, news_sentiment_mock),
    }
    for tool_name, (real_module, mock_module) in real_tools_with_fallback.items():
        schema = registry.load_schema_from_file(tool_name)
        registry.register(tool_name, schema, real_module.run)
        registry.register(tool_name, schema, mock_module.run, fallback_of=tool_name)

    # Tools still on mock-only implementations (real integrations land Day 7).
    mock_only_tools = {
        "earnings_transcript": earnings,
        "company_profile": company_profile,
        "peer_comparison": peer_comparison,
        "calculation_engine": calculator,
        "fact_checker": fact_checker,
        "report_generator": report_gen,
    }
    for tool_name, module in mock_only_tools.items():
        schema = registry.load_schema_from_file(tool_name)
        registry.register(tool_name, schema, module.run)

    # Day 6: real, working long-term memory tools (Chroma-backed, no mock
    # needed -- a local vector store has no external dependency to fail
    # over from in the same way an API-backed tool does).
    memory_tools = {
        "vector_db_search": vector_db_search,
        "vector_db_store": vector_db_store,
    }
    for tool_name, module in memory_tools.items():
        schema = registry.load_schema_from_file(tool_name)
        registry.register(tool_name, schema, module.run)

    return registry
