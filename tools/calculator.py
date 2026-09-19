"""
calculator.py

Stub implementation of the `calculation_engine` tool. Unlike the other
tools, this one is not really a "stub" in the sense of returning fake data
-- financial ratio and growth-rate math is deterministic, so it's already
a real, working implementation. Note this tool is gated behind Challenge 6
completion per the simulation's progression-unlock mechanic.
"""

from datetime import datetime, timezone
from typing import Any, Dict

from tools.tool_registry import ToolResult


def _growth_rate(inputs: Dict[str, Any]) -> float:
    old, new = inputs["previous_value"], inputs["current_value"]
    if old == 0:
        raise ValueError("previous_value cannot be zero for a growth-rate calculation")
    return round(((new - old) / old) * 100, 2)


def _margin(inputs: Dict[str, Any]) -> float:
    numerator, denominator = inputs["numerator"], inputs["denominator"]
    if denominator == 0:
        raise ValueError("denominator cannot be zero for a margin calculation")
    return round((numerator / denominator) * 100, 2)


def _ratio(inputs: Dict[str, Any]) -> float:
    numerator, denominator = inputs["numerator"], inputs["denominator"]
    if denominator == 0:
        raise ValueError("denominator cannot be zero for a ratio calculation")
    return round(numerator / denominator, 4)


def _dcf(inputs: Dict[str, Any]) -> Dict[str, Any]:
    """Very simplified DCF: sum of discounted cash flows over N years."""
    cash_flows = inputs["projected_cash_flows"]  # list of floats
    discount_rate = inputs["discount_rate"]  # e.g. 0.09 for 9%
    present_values = [
        cf / ((1 + discount_rate) ** (year + 1)) for year, cf in enumerate(cash_flows)
    ]
    return {
        "present_values": [round(pv, 2) for pv in present_values],
        "total_present_value": round(sum(present_values), 2),
    }


_CALCULATIONS = {
    "growth_rate": _growth_rate,
    "margin": _margin,
    "ratio": _ratio,
    "dcf": _dcf,
}


def run(calculation_type: str, inputs: Dict[str, Any]) -> ToolResult:
    """
    Perform a real financial calculation. This tool does NOT return mock
    data -- given valid inputs, the math is genuinely correct.
    """
    func = _CALCULATIONS.get(calculation_type)
    if func is None:
        return ToolResult(
            success=False,
            data=None,
            source_name="calculation_engine",
            source_tier=1,
            retrieved_at=datetime.now(timezone.utc).isoformat(),
            error=f"Unknown calculation_type='{calculation_type}'.",
        )

    try:
        result = func(inputs)
    except (KeyError, ValueError, ZeroDivisionError, TypeError) as exc:
        return ToolResult(
            success=False,
            data=None,
            source_name="calculation_engine",
            source_tier=1,
            retrieved_at=datetime.now(timezone.utc).isoformat(),
            error=f"Calculation failed: {exc}",
        )

    return ToolResult(
        success=True,
        data={"calculation_type": calculation_type, "inputs": inputs, "result": result},
        source_name="Calculation Engine",
        source_tier=1,  # derived directly from the agent's own verified inputs
        retrieved_at=datetime.now(timezone.utc).isoformat(),
    )
