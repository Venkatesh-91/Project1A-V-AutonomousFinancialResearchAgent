"""
news_sentiment.py

Stub implementation of the `news_sentiment` tool. Day 5 replaces this with
a real news API (NewsAPI.org, GDELT) plus a real sentiment scorer
(TextBlob/VADER, or a FinBERT model per the architecture spec).
"""

from datetime import datetime, timezone

from tools.tool_registry import ToolResult


def run(query: str, num_articles: int = 5, lookback_days: int = 30) -> ToolResult:
    """Mock news sentiment analysis."""
    return ToolResult(
        success=True,
        data={
            "query": query,
            "num_articles_analyzed": num_articles,
            "lookback_days": lookback_days,
            "overall_sentiment": "neutral_to_positive",
            "overall_sentiment_score": 0.15,  # -1.0 (very negative) to 1.0 (very positive)
            "article_summaries": [
                f"[MOCK DATA] Placeholder sentiment summary #{i + 1} for '{query}'."
                for i in range(min(num_articles, 3))
            ],
        },
        source_name="News Sentiment (mock)",
        source_tier=4,  # unverified/aggregated sentiment sits below news itself
        retrieved_at=datetime.now(timezone.utc).isoformat(),
    )
