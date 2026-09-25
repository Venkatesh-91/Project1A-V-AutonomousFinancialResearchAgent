"""
earnings.py

REAL implementation of the `earnings_transcript` tool. There is no
genuinely free, no-signup earnings-transcript API (Financial Modeling
Prep's transcript endpoint requires a key; most transcript providers are
paid), so this tool instead does what a resourceful human researcher would:
search the free web for the transcript (via the same DuckDuckGo search
this project already uses for `web_search`) and extract readable text from
the most relevant result page.

This is inherently less reliable than a dedicated transcript API --
result quality depends on what's publicly indexed and how cleanly the
target page's HTML extracts. Every failure mode (no search results, a
page that doesn't parse, a paywall) falls through to earnings_mock.py via
the registry's fallback chain, same as this project's other real tools.
"""

from __future__ import annotations

from datetime import datetime, timezone

import httpx
from bs4 import BeautifulSoup
from ddgs import DDGS

from tools.http_utils import RateLimiter, ttl_cache
from tools.tool_registry import ToolResult

_search_rate_limiter = RateLimiter(min_interval_seconds=1.0)
_fetch_rate_limiter = RateLimiter(min_interval_seconds=1.0)

_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; ResearchAgent/1.0)"}


@ttl_cache(ttl_seconds=3600)
def _search_for_transcript(ticker: str, quarter: str, year: int) -> list:
    _search_rate_limiter.wait()
    query = f"{ticker} {quarter} {year} earnings call transcript"
    with DDGS() as ddgs:
        return list(ddgs.text(query, max_results=5))


def _fetch_page_text(url: str, max_chars: int = 4000) -> str:
    _fetch_rate_limiter.wait()
    response = httpx.get(url, headers=_HEADERS, timeout=10.0, follow_redirects=True)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer"]):
        tag.decompose()

    text = " ".join(soup.get_text(separator=" ").split())
    return text[:max_chars]


def run(ticker: str, quarter: str, year: int) -> ToolResult:
    """
    Find and extract earnings call transcript content via free web search.

    Args:
        ticker: Stock ticker symbol.
        quarter: 'Q1'-'Q4'.
        year: Fiscal year.
    """
    ticker = ticker.upper()

    try:
        results = _search_for_transcript(ticker, quarter, year)
    except Exception as exc:  # noqa: BLE001
        return ToolResult(
            success=False,
            data=None,
            source_name="Web Search (transcript lookup)",
            source_tier=3,
            retrieved_at=datetime.now(timezone.utc).isoformat(),
            error=f"Transcript search failed for {ticker} {quarter} {year}: {exc}",
        )

    if not results:
        return ToolResult(
            success=False,
            data=None,
            source_name="Web Search (transcript lookup)",
            source_tier=3,
            retrieved_at=datetime.now(timezone.utc).isoformat(),
            error=f"No search results found for {ticker} {quarter} {year} earnings call transcript.",
        )

    # Try each result in order until one's page fetches and parses cleanly.
    last_error = "no candidate pages succeeded"
    for result in results:
        url = result.get("href", "")
        if not url:
            continue
        try:
            excerpt = _fetch_page_text(url)
            if len(excerpt) < 200:
                # too little extracted text to be a real transcript --
                # likely a paywall, a listing page, or a parsing failure
                continue
            return ToolResult(
                success=True,
                data={
                    "ticker": ticker,
                    "quarter": quarter,
                    "year": year,
                    "source_url": url,
                    "transcript_excerpt": excerpt,
                },
                source_name=f"Web transcript source ({url})",
                source_tier=3,  # third-party transcript source, not the company itself
                retrieved_at=datetime.now(timezone.utc).isoformat(),
            )
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)
            continue

    return ToolResult(
        success=False,
        data=None,
        source_name="Web Search (transcript lookup)",
        source_tier=3,
        retrieved_at=datetime.now(timezone.utc).isoformat(),
        error=(
            f"Found search results for {ticker} {quarter} {year} but could not "
            f"extract a usable transcript from any of them: {last_error}"
        ),
    )
