"""
news_sentiment.py

REAL implementation of the `news_sentiment` tool. Combines two free,
no-key-required pieces:

  1. DuckDuckGo News search (via `ddgs`) to fetch recent article headlines
     and snippets for a query.
  2. TextBlob for sentiment scoring of that text -- a simple, local,
     free polarity scorer. This is explicitly a lighter-weight substitute
     for the FinBERT-based scorer architecture_specification.md Section 6
     calls for; swapping in a real finance-tuned sentiment model later is
     a drop-in replacement for `_score_sentiment` below, since the rest of
     this module doesn't care how the score is produced.
"""

from __future__ import annotations

from datetime import datetime, timezone

from ddgs import DDGS
from textblob import TextBlob

from tools.http_utils import RateLimiter, ttl_cache
from tools.tool_registry import ToolResult

_rate_limiter = RateLimiter(min_interval_seconds=1.0)


@ttl_cache(ttl_seconds=600)
def _fetch_news(query: str, num_articles: int) -> list:
    _rate_limiter.wait()
    with DDGS() as ddgs:
        return list(ddgs.news(query, max_results=num_articles))


def _score_sentiment(text: str) -> float:
    """Return a polarity score from -1.0 (very negative) to 1.0 (very positive)."""
    return round(TextBlob(text).sentiment.polarity, 3)


def _classify(score: float) -> str:
    if score > 0.15:
        return "positive"
    if score < -0.15:
        return "negative"
    return "neutral"


def run(query: str, num_articles: int = 5, lookback_days: int = 30) -> ToolResult:
    """
    Analyze real news sentiment for a query.

    Args:
        query: Company name, ticker, or topic to analyze sentiment for.
        num_articles: Number of articles to fetch and analyze.
        lookback_days: Currently informational only -- ddgs's news search
            doesn't expose a day-count filter directly; this is passed
            through in the result for downstream logging/traceability.
    """
    try:
        articles = _fetch_news(query, num_articles)
    except Exception as exc:  # noqa: BLE001
        return ToolResult(
            success=False,
            data=None,
            source_name="News Sentiment (DuckDuckGo News + TextBlob)",
            source_tier=4,
            retrieved_at=datetime.now(timezone.utc).isoformat(),
            error=f"News search failed for query '{query}': {exc}",
        )

    if not articles:
        return ToolResult(
            success=False,
            data=None,
            source_name="News Sentiment (DuckDuckGo News + TextBlob)",
            source_tier=4,
            retrieved_at=datetime.now(timezone.utc).isoformat(),
            error=f"No news articles found for query '{query}'.",
        )

    article_summaries = []
    scores = []
    for article in articles:
        text = f"{article.get('title', '')}. {article.get('body', '')}"
        score = _score_sentiment(text)
        scores.append(score)
        article_summaries.append(
            {
                "title": article.get("title", ""),
                "url": article.get("url", ""),
                "date": article.get("date", ""),
                "sentiment_score": score,
                "sentiment_label": _classify(score),
            }
        )

    overall_score = round(sum(scores) / len(scores), 3)

    return ToolResult(
        success=True,
        data={
            "query": query,
            "num_articles_analyzed": len(articles),
            "lookback_days": lookback_days,
            "overall_sentiment": _classify(overall_score),
            "overall_sentiment_score": overall_score,
            "article_summaries": article_summaries,
        },
        source_name="News Sentiment (DuckDuckGo News + TextBlob)",
        source_tier=4,
        retrieved_at=datetime.now(timezone.utc).isoformat(),
    )
