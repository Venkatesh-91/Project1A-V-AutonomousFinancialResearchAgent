"""
company_profile.py

REAL implementation of the `company_profile` tool, using yfinance (same
free, no-key library as tools/financial_api.py). yfinance's `.info` field
includes sector, industry, market cap, a business description, and a list
of company officers -- everything this tool's schema promises.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict

import yfinance as yf

from tools.http_utils import RateLimiter, ttl_cache
from tools.tool_registry import ToolResult

_rate_limiter = RateLimiter(min_interval_seconds=0.5)


@ttl_cache(ttl_seconds=3600)  # company profile data changes infrequently
def _fetch_profile(ticker: str) -> Dict[str, Any]:
    _rate_limiter.wait()
    info = yf.Ticker(ticker).info
    if not info or (info.get("regularMarketPrice") is None and info.get("currentPrice") is None):
        raise ValueError(f"yfinance returned no usable profile data for ticker '{ticker}'")
    return info


def run(ticker: str) -> ToolResult:
    """Retrieve a real company profile via yfinance."""
    ticker = ticker.upper()

    try:
        info = _fetch_profile(ticker)
    except Exception as exc:  # noqa: BLE001
        return ToolResult(
            success=False,
            data=None,
            source_name="Yahoo Finance (yfinance)",
            source_tier=2,
            retrieved_at=datetime.now(timezone.utc).isoformat(),
            error=f"Company profile lookup failed for '{ticker}': {exc}",
        )

    # yfinance's companyOfficers list gives name + title for each named
    # executive; take the first few (usually CEO/CFO/other top officers,
    # in the order yfinance returns them -- not guaranteed to be seniority
    # order, so this is best-effort rather than authoritative).
    officers = info.get("companyOfficers", []) or []
    key_executives = [
        {"name": o.get("name"), "title": o.get("title")}
        for o in officers[:5]
        if o.get("name")
    ]

    return ToolResult(
        success=True,
        data={
            "ticker": ticker,
            "name": info.get("longName") or info.get("shortName"),
            "sector": info.get("sector"),
            "industry": info.get("industry"),
            "market_cap_usd": info.get("marketCap"),
            "description": info.get("longBusinessSummary"),
            "key_executives": key_executives,
            "website": info.get("website"),
            "headquarters": ", ".join(
                filter(None, [info.get("city"), info.get("state"), info.get("country")])
            ),
        },
        source_name="Yahoo Finance (yfinance)",
        source_tier=2,
        retrieved_at=datetime.now(timezone.utc).isoformat(),
    )
