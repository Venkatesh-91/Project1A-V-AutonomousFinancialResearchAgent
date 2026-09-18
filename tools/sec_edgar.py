"""
sec_edgar.py

REAL implementation of the `sec_filing_search` tool, using the SEC's free,
public EDGAR APIs -- no API key required. Two endpoints are used:

  1. https://www.sec.gov/files/company_tickers.json
     A free, public ticker -> CIK (Central Index Key) lookup table,
     refreshed periodically by the SEC. Cached for 24 hours since it
     rarely changes.

  2. https://data.sec.gov/submissions/CIK{cik:010d}.json
     A company's full filing history. Cached for 1 hour per company.

The SEC's fair-access policy requires every request to identify the
requester via a descriptive User-Agent header (name + contact email) --
see https://www.sec.gov/os/webmaster-faq#developers. Set
SEC_EDGAR_USER_AGENT in your .env to your own name/email before using this
in anything beyond local testing.

If either the network call or the parsing fails for any reason, this
raises -- the ToolRegistry's fallback-chain mechanism (see
tools/tool_registry.py and the registration in build_default_registry())
automatically routes to sec_edgar_mock.py so a research run degrades
gracefully instead of crashing.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

import httpx

from config.settings import settings
from tools.http_utils import RateLimiter, ttl_cache
from tools.tool_registry import ToolResult

# SEC asks for max 10 requests/second; staying well under that.
_rate_limiter = RateLimiter(min_interval_seconds=0.15)

_TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
_SUBMISSIONS_URL_TEMPLATE = "https://data.sec.gov/submissions/CIK{cik:010d}.json"


def _headers() -> Dict[str, str]:
    return {"User-Agent": settings.sec_edgar_user_agent}


@ttl_cache(ttl_seconds=86400)  # ticker->CIK mapping rarely changes; cache 24h
def _get_ticker_to_cik_map() -> Dict[str, int]:
    """Fetch and cache the SEC's full ticker -> CIK lookup table."""
    _rate_limiter.wait()
    response = httpx.get(_TICKER_MAP_URL, headers=_headers(), timeout=10.0)
    response.raise_for_status()
    raw = response.json()
    # raw is like {"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}, ...}
    return {entry["ticker"].upper(): entry["cik_str"] for entry in raw.values()}


@ttl_cache(ttl_seconds=3600)  # a company's filing history changes infrequently within an hour
def _get_submissions(cik: int) -> Dict[str, Any]:
    """Fetch and cache a company's full submission/filing history."""
    _rate_limiter.wait()
    url = _SUBMISSIONS_URL_TEMPLATE.format(cik=cik)
    response = httpx.get(url, headers=_headers(), timeout=10.0)
    response.raise_for_status()
    return response.json()


def run(ticker: str, filing_type: str, year: Optional[int] = None) -> ToolResult:
    """
    Retrieve a real SEC filing's metadata and a link to the filing itself.

    Args:
        ticker: Stock ticker symbol, e.g. 'AAPL'.
        filing_type: One of '10-K', '10-Q', '8-K', 'DEF 14A'.
        year: Optional filing year to prefer; if omitted, the most recent
            matching filing is returned.
    """
    ticker = ticker.upper()

    ticker_map = _get_ticker_to_cik_map()
    cik = ticker_map.get(ticker)
    if cik is None:
        return ToolResult(
            success=False,
            data=None,
            source_name="SEC EDGAR",
            source_tier=1,
            retrieved_at=datetime.now(timezone.utc).isoformat(),
            error=f"No CIK found for ticker '{ticker}' in SEC's ticker table.",
        )

    submissions = _get_submissions(cik)
    recent = submissions.get("filings", {}).get("recent", {})

    forms = recent.get("form", [])
    filing_dates = recent.get("filingDate", [])
    accession_numbers = recent.get("accessionNumber", [])
    primary_documents = recent.get("primaryDocument", [])

    matches = []
    for i, form in enumerate(forms):
        if form != filing_type:
            continue
        filing_date = filing_dates[i]
        if year is not None and not filing_date.startswith(str(year)):
            continue
        matches.append(
            {
                "filing_date": filing_date,
                "accession_number": accession_numbers[i],
                "primary_document": primary_documents[i],
            }
        )

    if not matches:
        return ToolResult(
            success=False,
            data=None,
            source_name="SEC EDGAR",
            source_tier=1,
            retrieved_at=datetime.now(timezone.utc).isoformat(),
            error=(
                f"No '{filing_type}' filing found for {ticker}"
                + (f" in {year}" if year else "")
                + "."
            ),
        )

    # matches are already in the order SEC returns them (most recent first)
    best = matches[0]
    accession_no_dashes = best["accession_number"].replace("-", "")
    filing_url = (
        f"https://www.sec.gov/Archives/edgar/data/{cik}/"
        f"{accession_no_dashes}/{best['primary_document']}"
    )

    return ToolResult(
        success=True,
        data={
            "ticker": ticker,
            "cik": cik,
            "filing_type": filing_type,
            "filing_date": best["filing_date"],
            "accession_number": best["accession_number"],
            "filing_url": filing_url,
        },
        source_name="SEC EDGAR",
        source_tier=1,  # SEC filings are the highest-trust tier
        retrieved_at=datetime.now(timezone.utc).isoformat(),
    )
