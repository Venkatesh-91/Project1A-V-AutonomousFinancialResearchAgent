"""
company_profile.py

Stub implementation of the `company_profile` tool. Day 7 replaces this with
a real financial data API call.
"""

from datetime import datetime, timezone

from tools.tool_registry import ToolResult

_MOCK_PROFILES = {
    "AAPL": {
        "name": "Apple Inc.",
        "sector": "Technology",
        "industry": "Consumer Electronics",
        "market_cap_usd_billions": 3400,
        "ceo": "[MOCK] Tim Cook",
    },
    "TSLA": {
        "name": "Tesla, Inc.",
        "sector": "Consumer Discretionary",
        "industry": "Automobiles",
        "market_cap_usd_billions": 850,
        "ceo": "[MOCK] Elon Musk",
    },
    "MSFT": {
        "name": "Microsoft Corporation",
        "sector": "Technology",
        "industry": "Software",
        "market_cap_usd_billions": 3100,
        "ceo": "[MOCK] Satya Nadella",
    },
}


def run(ticker: str) -> ToolResult:
    """Mock company profile lookup."""
    ticker = ticker.upper()
    profile = _MOCK_PROFILES.get(ticker)

    if profile is None:
        return ToolResult(
            success=False,
            data=None,
            source_name="company_profile",
            source_tier=2,
            retrieved_at=datetime.now(timezone.utc).isoformat(),
            error=f"No mock profile for ticker='{ticker}'.",
        )

    return ToolResult(
        success=True,
        data={"ticker": ticker, **profile},
        source_name="Company Profile (mock)",
        source_tier=2,
        retrieved_at=datetime.now(timezone.utc).isoformat(),
    )
