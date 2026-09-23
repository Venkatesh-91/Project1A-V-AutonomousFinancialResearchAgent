"""
disambiguation.py

Query disambiguation for ARA-1 -- Day 10 deliverable.

Handles Challenge 6: "What's happening with the banks?" -- a deliberately
vague, ambiguous query that the agent must resolve into a concrete, focused
research direction BEFORE it starts calling tools.

Strategy:
  1. Use the QueryProfile from query_analyzer.py to detect ambiguity level
  2. For VERY_AMBIGUOUS queries, apply a set of disambiguation rules to
     generate reasonable assumptions (rather than asking the user interactively,
     which isn't possible in an autonomous agent)
  3. Document every assumption made -- per architecture_specification.md A7.3:
     "documents assumptions made when proceeding with a specific interpretation"
  4. Return a DisambiguatedQuery with a clarified intent and a assumptions log
     that must be included in the final report's methodology section

This module does NOT call the LLM for disambiguation (that would use quota and
add latency). Heuristic disambiguation is fast and covers the patterns in the
project's 8 challenges. An LLM-assisted disambiguation path is stubbed for
future enhancement.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from agent.query_analyzer import AmbiguityLevel, QueryProfile, QueryType

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class DisambiguatedQuery:
    """
    The result of running disambiguation on an ambiguous query.

    `clarified_query` is a rewritten, concrete version of the original that
    the agent can actually execute.  `assumptions` lists every interpretive
    decision made (MUST be included in the research report's methodology).
    """
    original_query: str
    clarified_query: str
    assumptions: List[str]
    confidence: float                   # 0–1: how confident we are in the interpretation
    fallback_scope: str = ""            # what the agent should do if assumptions are wrong


# ---------------------------------------------------------------------------
# Disambiguation rules
# ---------------------------------------------------------------------------

# Pattern: (keywords that trigger this rule) → (clarification template, assumption text, confidence)
_AMBIGUOUS_SECTOR_RULES: List[Tuple[List[str], str, str, float]] = [
    (
        ["bank", "banks", "banking", "financial sector"],
        "Research recent developments in the US banking sector, covering the major banks "
        "(JPMorgan Chase, Bank of America, Goldman Sachs) including recent earnings, "
        "regulatory developments, and macroeconomic context.",
        "Interpreted 'banks' as the US commercial and investment banking sector, "
        "focusing on the largest institutions by assets.",
        0.80,
    ),
    (
        ["tech", "technology", "tech sector"],
        "Research recent developments in the US technology sector, covering leading companies "
        "(Microsoft, Apple, Alphabet/Google, Meta, Amazon) with emphasis on AI investments, "
        "revenue growth, and competitive dynamics.",
        "Interpreted 'tech/technology' as the US large-cap technology sector.",
        0.75,
    ),
    (
        ["market", "markets", "stock market"],
        "Analyze recent developments in US equity markets including major index performance "
        "(S&P 500, Nasdaq), sector rotations, and key macro drivers.",
        "Interpreted 'market/markets' as US equity markets and stock indices.",
        0.70,
    ),
    (
        ["oil", "energy", "oil sector"],
        "Research recent developments in the energy sector including oil price movements, "
        "major energy companies (ExxonMobil, Chevron), and supply/demand dynamics.",
        "Interpreted as the global energy sector with focus on oil & gas.",
        0.75,
    ),
    (
        ["pharma", "pharmaceutical", "biotech", "healthcare"],
        "Research recent developments in the pharmaceutical and biotech sector including "
        "major drug approvals, clinical trial results, and pricing legislation impacts.",
        "Interpreted as the US pharmaceutical/biotech sector.",
        0.75,
    ),
]

# Temporal disambiguation: "recent" without a year → assume current calendar year
_TEMPORAL_ASSUMPTIONS = [
    ("recent", "Interpreted 'recent' as the past 12 months from the query date."),
    ("latest", "Interpreted 'latest' as the most recently reported period."),
    ("current", "Interpreted 'current' as the present quarter."),
]

# Company disambiguation: multi-meaning names
_ENTITY_DISAMBIGUATION = {
    "amazon": (
        "Amazon (ticker: AMZN) -- the e-commerce, cloud (AWS), and logistics conglomerate",
        0.85,
    ),
    "apple": (
        "Apple Inc. (ticker: AAPL) -- the consumer electronics and software company",
        0.90,
    ),
    "meta": (
        "Meta Platforms (ticker: META) -- formerly Facebook, the social media conglomerate",
        0.85,
    ),
    "alphabet": (
        "Alphabet Inc. (ticker: GOOGL) -- parent company of Google and YouTube",
        0.90,
    ),
}


# ---------------------------------------------------------------------------
# Disambiguator
# ---------------------------------------------------------------------------

class QueryDisambiguator:
    """
    Resolves ambiguous research queries into concrete, executable queries
    with documented assumptions.

    Usage::

        disambiguator = QueryDisambiguator()
        result = disambiguator.disambiguate(profile)
        print(result.clarified_query)
        print("Assumptions:", result.assumptions)
    """

    def disambiguate(self, profile: QueryProfile) -> DisambiguatedQuery:
        """
        Main entry point.  Returns a DisambiguatedQuery even if the original
        was not ambiguous (in that case, clarified_query == original_query
        and assumptions is empty).
        """
        if profile.ambiguity == AmbiguityLevel.CLEAR:
            return DisambiguatedQuery(
                original_query=profile.original_query,
                clarified_query=profile.original_query,
                assumptions=[],
                confidence=1.0,
            )

        assumptions: List[str] = []
        clarified = profile.original_query
        confidence = 1.0

        # Step 1: Resolve sector/entity ambiguity
        clarified, new_assumptions, confidence = self._resolve_sector(
            profile.original_query, assumptions, confidence
        )

        # Step 2: Add temporal assumptions if time-sensitive words are vague
        temporal_assumptions = self._resolve_temporal(profile.original_query)
        assumptions.extend(temporal_assumptions)

        # Step 3: Add entity disambiguation notes
        entity_assumptions = self._resolve_entities(profile.original_query)
        assumptions.extend(entity_assumptions)

        # Step 4: If VERY_AMBIGUOUS and we still have no clarification, apply generic fallback
        if profile.ambiguity == AmbiguityLevel.VERY_AMBIGUOUS and clarified == profile.original_query:
            clarified, fallback_assumptions = self._apply_generic_fallback(profile)
            assumptions.extend(fallback_assumptions)
            confidence = max(0.4, confidence - 0.3)

        result = DisambiguatedQuery(
            original_query=profile.original_query,
            clarified_query=clarified,
            assumptions=assumptions,
            confidence=confidence,
            fallback_scope=(
                "If assumptions prove incorrect, re-run with explicit company name or sector."
                if assumptions else ""
            ),
        )

        logger.info(
            "Disambiguated query (confidence=%.2f, assumptions=%d): %r → %r",
            result.confidence,
            len(result.assumptions),
            profile.original_query[:60],
            result.clarified_query[:60],
        )
        return result

    # ------------------------------------------------------------------ #
    # Private helpers
    # ------------------------------------------------------------------ #

    def _resolve_sector(
        self,
        query: str,
        existing_assumptions: List[str],
        base_confidence: float,
    ) -> Tuple[str, List[str], float]:
        q_lower = query.lower()
        for keywords, clarification, assumption, rule_confidence in _AMBIGUOUS_SECTOR_RULES:
            if any(kw in q_lower for kw in keywords):
                existing_assumptions.append(assumption)
                return clarification, existing_assumptions, base_confidence * rule_confidence
        return query, existing_assumptions, base_confidence

    def _resolve_temporal(self, query: str) -> List[str]:
        q_lower = query.lower()
        return [
            assumption
            for keyword, assumption in _TEMPORAL_ASSUMPTIONS
            if keyword in q_lower
        ]

    def _resolve_entities(self, query: str) -> List[str]:
        q_lower = query.lower()
        notes = []
        for entity, (clarification, _) in _ENTITY_DISAMBIGUATION.items():
            if entity in q_lower:
                notes.append(f"Interpreted '{entity}' as: {clarification}.")
        return notes

    def _apply_generic_fallback(self, profile: QueryProfile) -> Tuple[str, List[str]]:
        """Last-resort disambiguation: pick the top 3 companies by market cap."""
        fallback_query = (
            "Research the current state of the US financial markets, covering "
            "major developments at Microsoft (MSFT), Apple (AAPL), and Amazon (AMZN) "
            "as representative large-cap examples."
        )
        assumption = (
            "Query was too vague to resolve to a specific sector or company. "
            "Defaulted to a broad US markets overview with three representative "
            "large-cap companies (MSFT, AAPL, AMZN). The user should re-issue the "
            "query with more specifics for a targeted analysis."
        )
        return fallback_query, [assumption]


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_disambiguator: Optional[QueryDisambiguator] = None


def get_disambiguator() -> QueryDisambiguator:
    global _disambiguator
    if _disambiguator is None:
        _disambiguator = QueryDisambiguator()
    return _disambiguator


def format_assumptions_for_report(result: DisambiguatedQuery) -> str:
    """
    Format the disambiguation result as a markdown section for inclusion
    in the final research report's methodology notes.
    """
    if not result.assumptions:
        return ""

    lines = [
        "### Query Disambiguation Notes",
        "",
        f"**Original query:** {result.original_query}",
        "",
        f"**Interpreted as:** {result.clarified_query}",
        "",
        f"**Interpretation confidence:** {result.confidence:.0%}",
        "",
        "**Assumptions made:**",
    ]
    for i, assumption in enumerate(result.assumptions, 1):
        lines.append(f"{i}. {assumption}")

    if result.fallback_scope:
        lines += ["", f"*{result.fallback_scope}*"]

    return "\n".join(lines)
