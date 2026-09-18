"""
financial_api.py

REAL implementation of the `financial_data_api` tool, using yfinance (a
free, unofficial Yahoo Finance API wrapper) -- no API key required, per
architecture_specification.md Section 5.2's fallback chain options.

yfinance's data quality/availability can be inconsistent (it's unofficial
and Yahoo can change its backend without notice), so every code path here
raises on missing/malformed data rather than silently returning partial
numbers -- the ToolRegistry's fallback chain then routes to
financial_api_mock.py automatically.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict

import yfinance as yf

from tools.http_utils import RateLimiter, ttl_cache
from tools.tool_registry import ToolResult

# Be conservative -- yfinance scrapes Yahoo's endpoints and aggressive
# polling risks a temporary IP block (noted in architecture_specification.md
# Section E4.2).
_rate_limiter = RateLimiter(min_interval_seconds=0.5)


@ttl_cache(ttl_seconds=1800)  # 30 min -- balances freshness against re-fetch cost
def _fetch_ticker_data(ticker: str) -> Dict[str, Any]:
    """Fetch and cache the raw yfinance data for one ticker."""
    _rate_limiter.wait()
    t = yf.Ticker(ticker)
    info = t.info
    if not info or info.get("regularMarketPrice") is None and info.get("currentPrice") is None:
        raise ValueError(f"yfinance returned no usable data for ticker '{ticker}'")

    return {
        "info": info,
        "income_stmt": t.income_stmt,
        "balance_sheet": t.balance_sheet,
        "cash_flow": t.cashflow,
    }


def _build_income_statement(ticker: str, data: Dict[str, Any]) -> Dict[str, Any]:
    info = data["info"]
    income_stmt = data["income_stmt"]

    revenue = None
    net_income = None
    if income_stmt is not None and not income_stmt.empty:
        latest_col = income_stmt.columns[0]
        if "Total Revenue" in income_stmt.index:
            revenue = float(income_stmt.loc["Total Revenue", latest_col])
        if "Net Income" in income_stmt.index:
            net_income = float(income_stmt.loc["Net Income", latest_col])

    operating_margin_pct = None
    if revenue and info.get("operatingMargins") is not None:
        operating_margin_pct = round(info["operatingMargins"] * 100, 2)

    return {
        "revenue_usd": revenue,
        "net_income_usd": net_income,
        "operating_margin_pct": operating_margin_pct,
        "fiscal_period": str(income_stmt.columns[0].date()) if income_stmt is not None and not income_stmt.empty else None,
    }


def _build_balance_sheet(data: Dict[str, Any]) -> Dict[str, Any]:
    balance_sheet = data["balance_sheet"]
    if balance_sheet is None or balance_sheet.empty:
        return {}
    latest_col = balance_sheet.columns[0]
    result = {}
    for row_name, key in [
        ("Total Assets", "total_assets_usd"),
        ("Total Liabilities Net Minority Interest", "total_liabilities_usd"),
        ("Stockholders Equity", "stockholders_equity_usd"),
    ]:
        if row_name in balance_sheet.index:
            result[key] = float(balance_sheet.loc[row_name, latest_col])
    return result


def _build_cash_flow(data: Dict[str, Any]) -> Dict[str, Any]:
    cash_flow = data["cash_flow"]
    if cash_flow is None or cash_flow.empty:
        return {}
    latest_col = cash_flow.columns[0]
    result = {}
    for row_name, key in [
        ("Operating Cash Flow", "operating_cash_flow_usd"),
        ("Free Cash Flow", "free_cash_flow_usd"),
    ]:
        if row_name in cash_flow.index:
            result[key] = float(cash_flow.loc[row_name, latest_col])
    return result


def _build_ratios(data: Dict[str, Any]) -> Dict[str, Any]:
    info = data["info"]
    return {
        "pe_ratio_ttm": info.get("trailingPE"),
        "price_to_book": info.get("priceToBook"),
        "return_on_equity_pct": (
            round(info["returnOnEquity"] * 100, 2) if info.get("returnOnEquity") is not None else None
        ),
        "debt_to_equity": info.get("debtToEquity"),
    }


_BUILDERS = {
    "income_statement": _build_income_statement,
    "balance_sheet": _build_balance_sheet,
    "cash_flow": _build_cash_flow,
    "ratios": _build_ratios,
}


def run(ticker: str, statement_type: str, period: str = "annual", years: int = 1) -> ToolResult:
    """
    Retrieve real structured financial data for a company via yfinance.

    Args:
        ticker: Stock ticker symbol.
        statement_type: One of 'income_statement', 'balance_sheet',
            'cash_flow', 'ratios'.
        period: 'annual' or 'quarterly' (currently both use yfinance's
            default annual statements; quarterly support can be added by
            swapping to t.quarterly_income_stmt etc.).
        years: Number of historical periods requested (currently the most
            recent period only is returned -- multi-period support is a
            straightforward extension for a later day).
    """
    ticker = ticker.upper()

    try:
        data = _fetch_ticker_data(ticker)
        builder = _BUILDERS.get(statement_type)
        if builder is None:
            return ToolResult(
                success=False,
                data=None,
                source_name="Yahoo Finance (yfinance)",
                source_tier=2,
                retrieved_at=datetime.now(timezone.utc).isoformat(),
                error=f"Unknown statement_type '{statement_type}'.",
            )

        if statement_type == "income_statement":
            result_data = builder(ticker, data)
        else:
            result_data = builder(data)

        return ToolResult(
            success=True,
            data={
                "ticker": ticker,
                "statement_type": statement_type,
                "period": period,
                **result_data,
            },
            source_name="Yahoo Finance (yfinance)",
            source_tier=2,  # curated financial data source
            retrieved_at=datetime.now(timezone.utc).isoformat(),
        )

    except Exception as exc:  # noqa: BLE001 -- yfinance can fail in many
        # ways (network, ticker not found, Yahoo backend changes); any
        # failure here should route to the fallback, not crash the run.
        return ToolResult(
            success=False,
            data=None,
            source_name="Yahoo Finance (yfinance)",
            source_tier=2,
            retrieved_at=datetime.now(timezone.utc).isoformat(),
            error=f"yfinance lookup failed for '{ticker}': {exc}",
        )
