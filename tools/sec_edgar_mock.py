"""
sec_edgar.py

Fallback implementation of the `sec_filing_search` tool -- used when the
real EDGAR API (sec_edgar.py) is unreachable or returns no data. Returns
realistic mock data so a research run degrades gracefully instead of
failing outright.

Day 5 replaces this with a real call to SEC EDGAR's free full-text search
API (efts.sec.gov). For now this returns realistic, clearly-labeled mock
data so the rest of the pipeline (Executor, Synthesis Engine, evaluation
harness) can be built and tested end-to-end before any network calls are
wired in.
"""

from datetime import datetime, timezone
from typing import Optional

from tools.tool_registry import ToolResult

_MOCK_FILINGS = {
    ("AAPL", "10-K"): {
        "filing_date": "2024-11-01",
        "accession_number": "0000320193-24-000123",
        "excerpt": (
            "[MOCK DATA] Apple Inc. Annual Report on Form 10-K. Risk "
            "factors include supply chain concentration, foreign exchange "
            "exposure, and intense competition in smartphone and services "
            "markets."
        ),
    },
    ("TSLA", "10-K"): {
        "filing_date": "2024-10-23",
        "accession_number": "0001628280-24-045678",
        "excerpt": (
            "[MOCK DATA] Tesla, Inc. Annual Report on Form 10-K. Risk "
            "factors include production ramp risk, regulatory credit "
            "revenue dependency, and competitive pressure in the EV market."
        ),
    },
    ("MSFT", "10-K"): {
        "filing_date": "2024-07-30",
        "accession_number": "0000789019-24-000098",
        "excerpt": (
            "[MOCK DATA] Microsoft Corporation Annual Report on Form 10-K. "
            "Risk factors include cybersecurity threats, cloud services "
            "competition, and regulatory scrutiny of AI products."
        ),
    },
}


def run(ticker: str, filing_type: str, year: Optional[int] = None) -> ToolResult:
    """
    Mock SEC filing retrieval.

    Args:
        ticker: Stock ticker symbol, e.g. 'AAPL'.
        filing_type: One of '10-K', '10-Q', '8-K', 'DEF 14A'.
        year: Optional filing year; ignored in the mock (returns latest).
    """
    ticker = ticker.upper()
    key = (ticker, filing_type)

    if key not in _MOCK_FILINGS:
        return ToolResult(
            success=False,
            data=None,
            source_name="sec_filing_search",
            source_tier=1,
            retrieved_at=datetime.now(timezone.utc).isoformat(),
            error=(
                f"No mock filing available for ticker='{ticker}', "
                f"filing_type='{filing_type}'. Add an entry to "
                f"_MOCK_FILINGS, or wait for Day 5's real EDGAR integration."
            ),
        )

    filing = _MOCK_FILINGS[key]
    return ToolResult(
        success=True,
        data={
            "ticker": ticker,
            "filing_type": filing_type,
            "filing_date": filing["filing_date"],
            "accession_number": filing["accession_number"],
            "text_excerpt": filing["excerpt"],
        },
        source_name="SEC EDGAR (mock)",
        source_tier=1,  # SEC filings are the highest-trust tier
        retrieved_at=datetime.now(timezone.utc).isoformat(),
    )
