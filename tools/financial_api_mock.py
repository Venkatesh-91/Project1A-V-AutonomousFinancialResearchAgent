"""
financial_api.py

Stub implementation of the `financial_data_api` tool. Day 5 replaces this
with a real integration (Alpha Vantage free tier, Financial Modeling Prep,
or yfinance).
"""

from datetime import datetime, timezone

from tools.tool_registry import ToolResult

_MOCK_STATEMENTS = {
    ("AAPL", "income_statement"): {
        "revenue_usd_millions": 391035,
        "net_income_usd_millions": 93736,
        "operating_margin_pct": 31.5,
        "fiscal_period": "FY2024",
    },
    ("TSLA", "income_statement"): {
        "revenue_usd_millions": 97690,
        "net_income_usd_millions": 7130,
        "operating_margin_pct": 7.2,
        "fiscal_period": "FY2024",
    },
}


def run(ticker: str, statement_type: str, period: str, years: int = 1) -> ToolResult:
    """
    Mock structured financial data retrieval.

    Args:
        ticker: Stock ticker symbol.
        statement_type: One of 'income_statement', 'balance_sheet',
            'cash_flow', 'ratios'.
        period: 'annual' or 'quarterly'.
        years: Number of historical periods requested (mock ignores this
            and returns a single period).
    """
    ticker = ticker.upper()
    key = (ticker, statement_type)

    if key not in _MOCK_STATEMENTS:
        return ToolResult(
            success=False,
            data=None,
            source_name="financial_data_api",
            source_tier=2,
            retrieved_at=datetime.now(timezone.utc).isoformat(),
            error=(
                f"No mock financial data for ticker='{ticker}', "
                f"statement_type='{statement_type}'."
            ),
        )

    return ToolResult(
        success=True,
        data={
            "ticker": ticker,
            "statement_type": statement_type,
            "period": period,
            **_MOCK_STATEMENTS[key],
        },
        source_name="Financial Data API (mock)",
        source_tier=2,  # curated financial data APIs are tier 2
        retrieved_at=datetime.now(timezone.utc).isoformat(),
    )
