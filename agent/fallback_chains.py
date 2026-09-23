"""
fallback_chains.py

Explicit documentation and extension of the fallback chains for all tools.

The tool registry (tools/tool_registry.py) already wires mock fallbacks for
the four real-API tools.  This module extends that with:
  1. A human-readable map of every tool's complete fallback chain
     (primary → fallback 1 → fallback 2) for documentation and reporting.
  2. A `FallbackChainAdvisor` that the agent can query to understand WHY
     a fallback was used and what confidence penalty to apply.
  3. Helper functions for Challenge 8's simulated-failure mode.

Per architecture_specification.md Section A4.3 fallback chain example:
  financial_data_api → SEC filing extraction → web search → vector DB cache
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Fallback chain definitions
# ---------------------------------------------------------------------------

@dataclass
class FallbackLink:
    tool_name: str
    description: str
    confidence_penalty: float  # subtract from source confidence (0–1 scale)


# The authoritative fallback chain map.
# Key: primary tool name.
# Value: ordered list of fallback links (first is tried before second).
FALLBACK_CHAINS: Dict[str, List[FallbackLink]] = {
    "sec_filing_search": [
        FallbackLink(
            "sec_edgar_mock",
            "SEC EDGAR mock -- returns realistic stub data when the real EDGAR API is unavailable.",
            confidence_penalty=0.25,
        ),
        FallbackLink(
            "web_search",
            "Web search for SEC filing summaries -- less structured but broadly available.",
            confidence_penalty=0.40,
        ),
        FallbackLink(
            "vector_db_search",
            "Long-term memory -- previously retrieved SEC data for this company.",
            confidence_penalty=0.15,
        ),
    ],
    "financial_data_api": [
        FallbackLink(
            "financial_api_mock",
            "Financial data mock -- realistic stub financials when yfinance is unavailable.",
            confidence_penalty=0.25,
        ),
        FallbackLink(
            "sec_filing_search",
            "SEC 10-K/10-Q filings -- extract financials directly from regulatory filings.",
            confidence_penalty=0.20,
        ),
        FallbackLink(
            "web_search",
            "Web search for financial summary pages -- least reliable fallback.",
            confidence_penalty=0.45,
        ),
    ],
    "web_search": [
        FallbackLink(
            "web_search_mock",
            "Web search mock -- stub results when DuckDuckGo is blocked or unavailable.",
            confidence_penalty=0.30,
        ),
        FallbackLink(
            "vector_db_search",
            "Long-term memory -- previously retrieved news and web content.",
            confidence_penalty=0.20,
        ),
    ],
    "news_sentiment": [
        FallbackLink(
            "news_sentiment_mock",
            "News sentiment mock -- stub sentiment when the real analysis fails.",
            confidence_penalty=0.30,
        ),
        FallbackLink(
            "web_search",
            "Web search for recent news -- raw articles without pre-computed sentiment.",
            confidence_penalty=0.35,
        ),
    ],
    "earnings_transcript": [
        FallbackLink(
            "web_search",
            "Web search for earnings call summaries from financial news sites.",
            confidence_penalty=0.35,
        ),
        FallbackLink(
            "sec_filing_search",
            "SEC 8-K filings -- sometimes contain earnings call text or key excerpts.",
            confidence_penalty=0.30,
        ),
    ],
    "company_profile": [
        FallbackLink(
            "financial_data_api",
            "Financial data API -- includes company sector, market cap, and basic profile.",
            confidence_penalty=0.10,
        ),
        FallbackLink(
            "web_search",
            "Web search for company overview pages.",
            confidence_penalty=0.30,
        ),
    ],
    "peer_comparison": [
        FallbackLink(
            "financial_data_api",
            "Financial data API -- retrieve peer metrics individually and construct comparison.",
            confidence_penalty=0.20,
        ),
    ],
    "calculation_engine": [
        FallbackLink(
            "web_search",
            "Web search for pre-computed ratios from financial data sites.",
            confidence_penalty=0.40,
        ),
    ],
    "fact_checker": [
        FallbackLink(
            "web_search",
            "Web search to cross-reference the claim against external sources.",
            confidence_penalty=0.25,
        ),
    ],
    "report_generator": [],  # No fallback -- the LLM itself can format text if this fails
}


# ---------------------------------------------------------------------------
# FallbackChainAdvisor
# ---------------------------------------------------------------------------

class FallbackChainAdvisor:
    """
    Query the fallback chain map to understand what fallbacks are available
    and what confidence penalty to apply when one is used.

    This is used by the evaluation framework to compute AB-2 (Error Recovery
    Rate) and to annotate the trace gallery with fallback usage explanations.
    """

    def get_chain(self, tool_name: str) -> List[FallbackLink]:
        """Return the registered fallback links for a tool (empty list if none)."""
        return FALLBACK_CHAINS.get(tool_name, [])

    def has_fallback(self, tool_name: str) -> bool:
        return bool(self.get_chain(tool_name))

    def get_confidence_penalty(self, primary_tool: str, fallback_used_name: str) -> float:
        """
        Return the confidence penalty (0–1) for having used `fallback_used_name`
        instead of `primary_tool`.  Returns 0.5 if the fallback isn't in the
        registered chain (unknown fallback situation).
        """
        for link in self.get_chain(primary_tool):
            if link.tool_name == fallback_used_name:
                return link.confidence_penalty
        return 0.5

    def describe_fallback(self, primary_tool: str, fallback_index: int = 0) -> Optional[str]:
        """
        Return a human-readable description of the Nth fallback for
        `primary_tool`, or None if no fallback exists at that index.
        Used for generating graceful degradation notes in reports.
        """
        chain = self.get_chain(primary_tool)
        if fallback_index < len(chain):
            link = chain[fallback_index]
            return (
                f"[Fallback used] '{primary_tool}' was unavailable. "
                f"Using '{link.tool_name}': {link.description} "
                f"(confidence reduced by {link.confidence_penalty:.0%})"
            )
        return None

    def summarize_all(self) -> Dict[str, List[str]]:
        """Return a summary of all fallback chains (tool → list of fallback names)."""
        return {
            tool: [link.tool_name for link in chain]
            for tool, chain in FALLBACK_CHAINS.items()
        }


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_advisor: Optional[FallbackChainAdvisor] = None


def get_fallback_advisor() -> FallbackChainAdvisor:
    global _advisor
    if _advisor is None:
        _advisor = FallbackChainAdvisor()
    return _advisor
