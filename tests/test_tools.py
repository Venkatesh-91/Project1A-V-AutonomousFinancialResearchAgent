"""
test_tools.py

Unit tests for the tool registry and a representative sample of tool
implementations. Run with:

    pytest tests/test_tools.py -v

These tests intentionally require no API keys and no network access --
everything under test today is either pure logic (the registry) or a
mock-data stub, so this suite should run in well under a second and pass
in CI without any secrets configured.
"""

import pytest

from tools.tool_registry import (
    ToolNotFoundError,
    ToolRegistry,
    ToolResult,
    ToolValidationError,
    build_default_registry,
)


# ---------------------------------------------------------------------- #
# Fixtures
# ---------------------------------------------------------------------- #
@pytest.fixture
def sample_schema():
    return {
        "name": "sample_tool",
        "description": "A sample tool for testing.",
        "parameters": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string"},
                "year": {"type": "integer"},
                "filing_type": {
                    "type": "string",
                    "enum": ["10-K", "10-Q"],
                },
            },
            "required": ["ticker"],
        },
    }


@pytest.fixture
def working_registry(sample_schema):
    def always_succeeds(**kwargs) -> ToolResult:
        return ToolResult(
            success=True,
            data={"echo": kwargs},
            source_name="sample_tool",
            source_tier=1,
            retrieved_at="2025-01-01T00:00:00+00:00",
        )

    registry = ToolRegistry()
    registry.register("sample_tool", sample_schema, always_succeeds)
    return registry


# ---------------------------------------------------------------------- #
# ToolRegistry: registration + introspection
# ---------------------------------------------------------------------- #
def test_register_and_list(working_registry):
    assert "sample_tool" in working_registry.list_tool_names()


def test_get_tool_definitions_returns_schema(working_registry, sample_schema):
    defs = working_registry.get_tool_definitions()
    assert len(defs) == 1
    assert defs[0]["name"] == "sample_tool"
    assert defs[0] == sample_schema


# ---------------------------------------------------------------------- #
# ToolRegistry: validation
# ---------------------------------------------------------------------- #
def test_validate_arguments_passes_for_valid_call(working_registry):
    # should not raise
    working_registry.validate_arguments("sample_tool", {"ticker": "AAPL"})


def test_validate_arguments_missing_required_field(working_registry):
    with pytest.raises(ToolValidationError, match="missing required"):
        working_registry.validate_arguments("sample_tool", {"year": 2024})


def test_validate_arguments_wrong_type(working_registry):
    with pytest.raises(ToolValidationError, match="expected type"):
        working_registry.validate_arguments("sample_tool", {"ticker": "AAPL", "year": "not_an_int"})


def test_validate_arguments_invalid_enum_value(working_registry):
    with pytest.raises(ToolValidationError, match="not one of the allowed values"):
        working_registry.validate_arguments(
            "sample_tool", {"ticker": "AAPL", "filing_type": "20-F"}
        )


def test_validate_arguments_unexpected_extra_argument(working_registry):
    with pytest.raises(ToolValidationError, match="unexpected argument"):
        working_registry.validate_arguments(
            "sample_tool", {"ticker": "AAPL", "not_a_real_param": 123}
        )


def test_validate_arguments_unknown_tool_raises(working_registry):
    with pytest.raises(ToolNotFoundError):
        working_registry.validate_arguments("nonexistent_tool", {})


# ---------------------------------------------------------------------- #
# ToolRegistry: dispatch + fallback chains
# ---------------------------------------------------------------------- #
def test_dispatch_returns_successful_result(working_registry):
    result = working_registry.dispatch("sample_tool", ticker="AAPL")
    assert isinstance(result, ToolResult)
    assert result.success is True
    assert result.data["echo"]["ticker"] == "AAPL"
    assert result.fallback_used is False


def test_dispatch_unknown_tool_raises():
    registry = ToolRegistry()
    with pytest.raises(ToolNotFoundError):
        registry.dispatch("does_not_exist", ticker="AAPL")


def test_dispatch_invalid_arguments_raises_before_execution(working_registry):
    with pytest.raises(ToolValidationError):
        working_registry.dispatch("sample_tool")  # missing required 'ticker'


def test_dispatch_falls_back_on_primary_failure(sample_schema):
    def primary_fails(**kwargs) -> ToolResult:
        raise RuntimeError("simulated primary tool failure")

    def fallback_succeeds(**kwargs) -> ToolResult:
        return ToolResult(
            success=True,
            data={"source": "fallback"},
            source_name="fallback_tool",
            source_tier=3,
            retrieved_at="2025-01-01T00:00:00+00:00",
        )

    registry = ToolRegistry()
    registry.register("primary_tool", sample_schema, primary_fails)
    registry.register("fallback_tool", sample_schema, fallback_succeeds, fallback_of="primary_tool")

    result = registry.dispatch("primary_tool", ticker="AAPL")

    assert result.success is True
    assert result.fallback_used is True
    assert result.data["source"] == "fallback"


def test_dispatch_returns_last_failure_when_entire_chain_fails(sample_schema):
    def always_fails(**kwargs) -> ToolResult:
        raise RuntimeError("nothing works")

    registry = ToolRegistry()
    registry.register("primary_tool", sample_schema, always_fails)
    registry.register("fallback_tool", sample_schema, always_fails, fallback_of="primary_tool")

    result = registry.dispatch("primary_tool", ticker="AAPL")

    assert result.success is False
    assert result.error is not None


def test_dispatch_records_call_log(working_registry):
    working_registry.dispatch("sample_tool", ticker="AAPL")
    log = working_registry.get_call_log()
    assert len(log) == 1
    assert log[0]["requested_tool"] == "sample_tool"
    assert log[0]["result"]["success"] is True


# ---------------------------------------------------------------------- #
# Real tool implementations (via build_default_registry)
# ---------------------------------------------------------------------- #
@pytest.fixture
def default_registry():
    return build_default_registry()


def test_default_registry_has_at_least_ten_tools(default_registry):
    assert len(default_registry.list_tool_names()) >= 10


def test_sec_filing_search_known_ticker(default_registry):
    result = default_registry.dispatch("sec_filing_search", ticker="AAPL", filing_type="10-K")
    assert result.success is True
    assert result.source_tier == 1  # SEC filings must be tier 1
    assert result.data["ticker"] == "AAPL"


def test_sec_filing_search_unknown_ticker_fails_gracefully(default_registry):
    result = default_registry.dispatch("sec_filing_search", ticker="ZZZZ", filing_type="10-K")
    assert result.success is False
    assert result.error is not None


def test_calculation_engine_growth_rate_is_correct(default_registry):
    result = default_registry.dispatch(
        "calculation_engine",
        calculation_type="growth_rate",
        inputs={"previous_value": 100, "current_value": 115},
    )
    assert result.success is True
    assert result.data["result"] == 15.0  # (115-100)/100 * 100


def test_calculation_engine_rejects_zero_denominator(default_registry):
    result = default_registry.dispatch(
        "calculation_engine",
        calculation_type="margin",
        inputs={"numerator": 50, "denominator": 0},
    )
    assert result.success is False
    assert "zero" in result.error.lower()


def test_report_generator_produces_markdown(default_registry):
    result = default_registry.dispatch(
        "report_generator",
        template="company_profile",
        sections={"Executive Summary": "Test content here."},
        sources=["SEC EDGAR (mock)"],
    )
    assert result.success is True
    assert "# Research Report" in result.data["markdown"]
    assert "Test content here." in result.data["markdown"]
    assert "SEC EDGAR (mock)" in result.data["markdown"]
