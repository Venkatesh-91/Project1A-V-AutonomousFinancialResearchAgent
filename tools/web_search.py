"""
web_search.py

Stub implementation of the `web_search` tool. Day 5 replaces this with a
real integration (Tavily, SerpAPI, or Brave Search).
"""

from datetime import datetime, timezone
from typing import Optional

from tools.tool_registry import ToolResult


def run(query: str, num_results: int = 10, date_range: Optional[str] = None) -> ToolResult:
    """Mock web search. Returns a small list of plausible-looking results."""
    mock_results = [
        {
            "title": f"[MOCK] Analysis: {query}",
            "url": "https://example-news.com/mock-article-1",
            "snippet": (
                f"[MOCK DATA] This is a placeholder search result for the "
                f"query '{query}'. Day 5 wires this up to a real search API."
            ),
        },
        {
            "title": f"[MOCK] Recent developments related to {query}",
            "url": "https://example-news.com/mock-article-2",
            "snippet": "[MOCK DATA] Second placeholder result for testing multi-result handling.",
        },
    ]

    return ToolResult(
        success=True,
        data={"query": query, "results": mock_results[:num_results]},
        source_name="Web Search (mock)",
        source_tier=5,  # general web search is the lowest-trust tier
        retrieved_at=datetime.now(timezone.utc).isoformat(),
    )
