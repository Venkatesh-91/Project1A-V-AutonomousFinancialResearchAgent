"""
peer_comparison.py

REAL (but honestly limited) implementation of the `peer_comparison` tool.

There is no free, no-key API that returns "peer companies for ticker X" --
that's normally a paid data product (Bloomberg, FactSet, etc.). This
implementation instead uses a small curated mapping of well-known large-cap
peers per sector (covering the tickers this project's own test challenges
use: tech, EV/auto, etc.) and pulls REAL, live financial metrics for each
peer via yfinance -- so the comparison numbers are genuine even though the
peer *selection* is a curated list rather than a dynamically discovered
one.

For any ticker not in the curated map, this raises (rather than guessing),
which routes to peer_comparison_mock.py via the registry's fallback chain
-- an honest gap flagged via a fallback rather than silently returning
nothing.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import yfinance as yf

from tools.http_utils import RateLimiter, ttl_cache
from tools.tool_registry import ToolResult

_rate_limiter = RateLimiter(min_interval_seconds=0.5)

# Curated, non-exhaustive sector-peer groupings for well-known large-caps.
# Expanding this table is the main way to broaden real (non-mock) coverage.
_PEER_MAP: Dict[str, List[str]] = {
    "AAPL": ["MSFT", "GOOGL"],
    "MSFT": ["AAPL", "GOOGL"],
    "GOOGL": ["MSFT", "META"],
    "META": ["GOOGL", "SNAP"],
    "TSLA": ["F", "GM"],
    "AMZN": ["WMT", "TGT"],
    "NVDA": ["AMD", "INTC"],
}


@ttl_cache(ttl_seconds=3600)
def _fetch_metric_snapshot(ticker: str) -> Dict[str, Any]:
    _rate_limiter.wait()
    info = yf.Ticker(ticker).info
    if not info or (info.get("regularMarketPrice") is None and info.get("currentPrice") is None):
        raise ValueError(f"yfinance returned no usable data for peer ticker '{ticker}'")
    return {
        "market_cap_usd": info.get("marketCap"),
        "trailing_pe": info.get("trailingPE"),
        "operating_margin_pct": (
            round(info["operatingMargins"] * 100, 2) if info.get("operatingMargins") is not None else None
        ),
        "revenue_growth_pct": (
            round(info["revenueGrowth"] * 100, 2) if info.get("revenueGrowth") is not None else None
        ),
    }


def run(
    ticker: str,
    num_peers: int = 3,
    metrics: Optional[List[str]] = None,
) -> ToolResult:
    """Identify real peers (from a curated map) and pull live comparison metrics."""
    ticker = ticker.upper()

    peers = _PEER_MAP.get(ticker)
    if not peers:
        return ToolResult(
            success=False,
            data=None,
            source_name="Peer Comparison (curated map + yfinance)",
            source_tier=2,
            retrieved_at=datetime.now(timezone.utc).isoformat(),
            error=(
                f"No curated peer mapping for ticker '{ticker}'. This tool's "
                f"peer coverage is a small hand-curated list, not a "
                f"comprehensive discovery mechanism."
            ),
        )

    peers = peers[:num_peers]
    comparison: Dict[str, Any] = {}
    fetch_errors: Dict[str, str] = {}

    for peer_ticker in peers:
        try:
            comparison[peer_ticker] = _fetch_metric_snapshot(peer_ticker)
        except Exception as exc:  # noqa: BLE001
            fetch_errors[peer_ticker] = str(exc)

    if not comparison:
        return ToolResult(
            success=False,
            data=None,
            source_name="Peer Comparison (curated map + yfinance)",
            source_tier=2,
            retrieved_at=datetime.now(timezone.utc).isoformat(),
            error=f"Could not fetch live data for any peer of '{ticker}': {fetch_errors}",
        )

    return ToolResult(
        success=True,
        data={
            "ticker": ticker,
            "peers": list(comparison.keys()),
            "comparison": comparison,
            "fetch_errors": fetch_errors or None,
        },
        source_name="Peer Comparison (curated map + yfinance)",
        source_tier=2,
        retrieved_at=datetime.now(timezone.utc).isoformat(),
    )
