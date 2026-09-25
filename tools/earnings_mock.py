"""
earnings.py

Stub implementation of the `earnings_transcript` tool. Day 7 replaces this
with a real integration (Financial Modeling Prep transcripts endpoint, or a
scraper for publicly available transcripts).
"""

from datetime import datetime, timezone

from tools.tool_registry import ToolResult


def run(ticker: str, quarter: str, year: int) -> ToolResult:
    """Mock earnings call transcript retrieval."""
    ticker = ticker.upper()
    return ToolResult(
        success=True,
        data={
            "ticker": ticker,
            "quarter": quarter,
            "year": year,
            "transcript_excerpt": (
                f"[MOCK DATA] {ticker} {quarter} {year} earnings call. "
                f"Prepared remarks: management expressed confidence in "
                f"continued growth. Q&A: an analyst asked about margin "
                f"pressure; management attributed it to input costs and "
                f"expects normalization next quarter."
            ),
            "speakers": ["CEO (mock)", "CFO (mock)", "Analyst 1 (mock)"],
        },
        source_name="Earnings Transcript (mock)",
        source_tier=3,  # management commentary: credible but subject to spin
        retrieved_at=datetime.now(timezone.utc).isoformat(),
    )
