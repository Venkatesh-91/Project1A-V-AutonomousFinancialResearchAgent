"""
fact_checker.py

REAL (heuristic) implementation of the `fact_checker` tool. True fact
verification (e.g. against a curated database of known-true financial
facts) isn't available for free, so this implementation does the free
alternative: search the web for the claim and measure how much
corroborating language appears across the top results, as a rough
corroboration-based confidence signal -- explicitly NOT a guarantee of
truth, just an automatable proxy for "is this widely reported the same
way elsewhere."
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import List, Optional

from ddgs import DDGS

from tools.http_utils import RateLimiter, ttl_cache
from tools.tool_registry import ToolResult

_rate_limiter = RateLimiter(min_interval_seconds=1.0)

_STOPWORDS = {
    "the", "a", "an", "is", "was", "are", "were", "of", "in", "on", "for",
    "to", "and", "or", "with", "by", "at", "as", "that", "this", "its",
    "has", "have", "had", "be", "been", "from",
}


def _keywords(text: str) -> List[str]:
    """Extract lowercase alphanumeric tokens, excluding common stopwords."""
    tokens = re.findall(r"[a-zA-Z0-9%$.]+", text.lower())
    return [t for t in tokens if t not in _STOPWORDS and len(t) > 1]


@ttl_cache(ttl_seconds=600)
def _search_claim(claim: str) -> list:
    _rate_limiter.wait()
    with DDGS() as ddgs:
        return list(ddgs.text(claim, max_results=5))


def run(claim: str, sources: Optional[List[str]] = None) -> ToolResult:
    """
    Heuristically cross-reference a claim against web search results.

    Confidence is the fraction of the claim's keywords that appear in the
    combined text of the search results -- a rough corroboration signal,
    not a formal verification. Always report this limitation alongside
    the score (the tool's own description already frames it this way to
    the LLM, and the result data does too).
    """
    try:
        results = _search_claim(claim)
    except Exception as exc:  # noqa: BLE001
        return ToolResult(
            success=False,
            data=None,
            source_name="Fact Checker (web corroboration heuristic)",
            source_tier=4,
            retrieved_at=datetime.now(timezone.utc).isoformat(),
            error=f"Fact-check search failed: {exc}",
        )

    if not results:
        return ToolResult(
            success=True,
            data={
                "claim": claim,
                "verification_status": "no_corroboration_found",
                "confidence_score": 0.0,
                "supporting_sources_checked": sources or [],
                "note": "No web search results were found to cross-reference this claim against.",
            },
            source_name="Fact Checker (web corroboration heuristic)",
            source_tier=4,
            retrieved_at=datetime.now(timezone.utc).isoformat(),
        )

    claim_keywords = set(_keywords(claim))
    combined_text = " ".join(
        f"{r.get('title', '')} {r.get('body', '')}" for r in results
    ).lower()

    matched = {kw for kw in claim_keywords if kw in combined_text}
    confidence = round(len(matched) / len(claim_keywords), 3) if claim_keywords else 0.0

    if confidence >= 0.7:
        status = "well_corroborated"
    elif confidence >= 0.4:
        status = "partially_corroborated"
    else:
        status = "weakly_corroborated"

    return ToolResult(
        success=True,
        data={
            "claim": claim,
            "verification_status": status,
            "confidence_score": confidence,
            "supporting_sources_checked": [r.get("href", "") for r in results],
            "note": (
                "Confidence is a heuristic keyword-corroboration score across "
                "web search results, not a formal fact-verification guarantee."
            ),
        },
        source_name="Fact Checker (web corroboration heuristic)",
        source_tier=4,
        retrieved_at=datetime.now(timezone.utc).isoformat(),
    )
