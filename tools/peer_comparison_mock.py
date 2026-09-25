"""
peer_comparison.py

Stub implementation of the `peer_comparison` tool. Day 7 replaces this with
a real integration -- note this tool is gated behind Challenge 4 completion
per the simulation's progression-unlock mechanic (Section B4.4), so the
Executor should not offer it to the LLM until that point in a real run.
"""

from datetime import datetime, timezone
from typing import List, Optional

from tools.tool_registry import ToolResult

_MOCK_PEERS = {
    "AAPL": ["MSFT", "GOOGL", "SAMSUNG (mock)"],
    "TSLA": ["RIVIAN (mock)", "FORD (mock)", "BYD (mock)"],
}


def run(
    ticker: str,
    num_peers: int = 3,
    metrics: Optional[List[str]] = None,
) -> ToolResult:
    """Mock peer identification and comparison."""
    ticker = ticker.upper()
    peers = _MOCK_PEERS.get(ticker, ["PEER_A (mock)", "PEER_B (mock)"])[:num_peers]
    metrics = metrics or ["revenue_growth_pct", "operating_margin_pct"]

    comparison = {
        peer: {metric: round(10 + i * 2.5, 1) for i, metric in enumerate(metrics)}
        for peer in peers
    }

    return ToolResult(
        success=True,
        data={"ticker": ticker, "peers": peers, "comparison": comparison},
        source_name="Peer Comparison (mock)",
        source_tier=2,
        retrieved_at=datetime.now(timezone.utc).isoformat(),
    )
