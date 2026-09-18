"""
web_search.py

REAL implementation of the `web_search` tool, using DuckDuckGo's search
engine via the free `ddgs` package -- no API key, no signup, no billing.
Chosen specifically because it requires no account at all, unlike Tavily/
SerpAPI/Brave (all of which need a signup even on their free tiers).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from ddgs import DDGS

from tools.http_utils import RateLimiter, ttl_cache
from tools.tool_registry import ToolResult

# Be a polite client -- DuckDuckGo has no official published rate limit
# for this unofficial library, so this is a conservative default.
_rate_limiter = RateLimiter(min_interval_seconds=1.0)


@ttl_cache(ttl_seconds=600)  # 10 min -- search results change slowly enough to cache briefly
def _search(query: str, num_results: int) -> list:
    _rate_limiter.wait()
    with DDGS() as ddgs:
        return list(ddgs.text(query, max_results=num_results))


def run(query: str, num_results: int = 10, date_range: Optional[str] = None) -> ToolResult:
    """
    Perform a real web search via DuckDuckGo.

    Args:
        query: Search query text.
        num_results: Number of results to return.
        date_range: Currently unused by this implementation (ddgs supports
            a `timelimit` parameter -- 'd'/'w'/'m'/'y' -- which can be
            wired in here if date-scoped search becomes necessary).
    """
    try:
        raw_results = _search(query, num_results)
    except Exception as exc:  # noqa: BLE001
        return ToolResult(
            success=False,
            data=None,
            source_name="DuckDuckGo Search",
            source_tier=5,
            retrieved_at=datetime.now(timezone.utc).isoformat(),
            error=f"Web search failed for query '{query}': {exc}",
        )

    if not raw_results:
        return ToolResult(
            success=False,
            data=None,
            source_name="DuckDuckGo Search",
            source_tier=5,
            retrieved_at=datetime.now(timezone.utc).isoformat(),
            error=f"No search results found for query '{query}'.",
        )

    results = [
        {
            "title": r.get("title", ""),
            "url": r.get("href", ""),
            "snippet": r.get("body", ""),
        }
        for r in raw_results
    ]

    return ToolResult(
        success=True,
        data={"query": query, "results": results},
        source_name="DuckDuckGo Search",
        source_tier=5,  # general web search is the lowest-trust tier
        retrieved_at=datetime.now(timezone.utc).isoformat(),
    )
