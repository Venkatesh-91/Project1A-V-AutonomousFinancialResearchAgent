"""
query_analyzer.py

Query analysis and classification for ARA-1 -- Day 10 deliverable.

Implements the query analysis step described in architecture_specification.md
Section A7.3: classifies incoming queries by type, complexity, and ambiguity
BEFORE the planning step, so the agent can:
  - Choose an appropriate research depth
  - Load relevant strategies from episodic memory
  - Decide whether disambiguation is needed (routed to disambiguation.py)

Classification uses keyword heuristics (no LLM call -- this runs fast and
for free before any API is involved).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class QueryType(str, Enum):
    COMPANY_PROFILE = "company_profile"    # "Create a profile of MSFT"
    EARNINGS_ANALYSIS = "earnings"         # "Analyze Apple's Q3 earnings"
    RISK_ASSESSMENT = "risk"               # "Assess risks for Tesla"
    INDUSTRY_COMPARISON = "comparison"     # "Compare AWS vs Azure vs GCP"
    CONTRADICTORY_DATA = "contradictory"   # "Investigate the contradiction..."
    AMBIGUOUS = "ambiguous"                # "What's happening with the banks?"
    SECTOR_ANALYSIS = "sector"             # "Themes across tech sector"
    FULL_RESEARCH = "full_research"        # "Full investment report on NVIDIA"
    OTHER = "other"


class ComplexityLevel(int, Enum):
    TRIVIAL = 1       # Single data point retrieval
    SIMPLE = 2        # Single company, 1-2 tools needed
    MODERATE = 3      # Multiple tools or slight ambiguity
    COMPLEX = 4       # Multi-company or memory-dependent
    VERY_COMPLEX = 5  # Full report, degraded environment, or sector-wide


class AmbiguityLevel(str, Enum):
    CLEAR = "clear"           # Query is unambiguous
    SLIGHTLY_AMBIGUOUS = "slightly_ambiguous"  # Minor disambiguation needed
    AMBIGUOUS = "ambiguous"   # Significant disambiguation required
    VERY_AMBIGUOUS = "very_ambiguous"  # Cannot proceed without clarification


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------

@dataclass
class QueryProfile:
    """Structured analysis of an incoming research query."""
    original_query: str
    query_type: QueryType
    complexity: ComplexityLevel
    ambiguity: AmbiguityLevel
    extracted_tickers: List[str]          # e.g. ["MSFT", "AAPL"]
    extracted_companies: List[str]        # e.g. ["Microsoft", "Apple"]
    time_sensitive: bool                  # True if "recent", "latest", "current" appear
    requires_memory: bool                 # True if "you've researched" or "sector themes"
    requires_disambiguation: bool
    suggested_tools: List[str]            # Recommended primary tools for this query type
    max_suggested_tool_calls: int         # Soft limit based on complexity

    def describe(self) -> str:
        return (
            f"Type={self.query_type.value}, "
            f"Complexity={self.complexity.value}/5, "
            f"Ambiguity={self.ambiguity.value}, "
            f"Tickers={self.extracted_tickers or 'none detected'}, "
            f"Time-sensitive={self.time_sensitive}, "
            f"Needs-memory={self.requires_memory}"
        )


# ---------------------------------------------------------------------------
# QueryAnalyzer
# ---------------------------------------------------------------------------

# Known tickers (expanded set covering the project's 8 challenges)
_KNOWN_TICKERS = {
    "MSFT", "AAPL", "TSLA", "AMZN", "GOOGL", "GOOG", "NVDA", "PLTR",
    "JPM", "GS", "MS", "BAC", "C", "WFC", "META", "NFLX", "AMD",
}

_TICKER_RE = re.compile(r'\b([A-Z]{1,5})\b')

_COMPANY_ALIASES = {
    "microsoft": "MSFT",
    "apple": "AAPL",
    "tesla": "TSLA",
    "amazon": "AMZN",
    "aws": "AMZN",
    "google": "GOOGL",
    "alphabet": "GOOGL",
    "gcp": "GOOGL",
    "azure": "MSFT",
    "nvidia": "NVDA",
    "palantir": "PLTR",
    "jpmorgan": "JPM",
    "jp morgan": "JPM",
    "goldman sachs": "GS",
    "morgan stanley": "MS",
    "bank of america": "BAC",
    "citigroup": "C",
    "wells fargo": "WFC",
    "meta": "META",
    "facebook": "META",
    "netflix": "NFLX",
}

# Keyword patterns for query type detection
_TYPE_PATTERNS = {
    QueryType.EARNINGS_ANALYSIS: [
        "earnings", "quarterly", "q1", "q2", "q3", "q4", "earnings call",
        "revenue beat", "eps", "consensus", "guidance", "transcript",
    ],
    QueryType.RISK_ASSESSMENT: [
        "risk", "risks", "threat", "danger", "vulnerability", "exposure",
        "downside", "concern", "challenge", "regulatory", "compliance",
    ],
    QueryType.INDUSTRY_COMPARISON: [
        "compare", "comparison", "vs", "versus", "competitive", "peers",
        "market share", "league table", "benchmark", "relative to",
    ],
    QueryType.CONTRADICTORY_DATA: [
        "contradiction", "contradict", "investigate", "discrepancy",
        "apparent", "despite", "however", "struggling but", "growth but",
    ],
    QueryType.SECTOR_ANALYSIS: [
        "sector", "industry", "theme", "themes", "cross-cutting",
        "throughout", "across companies", "pattern", "you've researched",
        "already researched", "based on",
    ],
    QueryType.FULL_RESEARCH: [
        "full report", "complete report", "investment report", "full analysis",
        "comprehensive report", "initiation", "coverage",
    ],
    QueryType.COMPANY_PROFILE: [
        "profile", "overview", "about", "describe", "introduction",
        "who is", "what does", "business overview", "key executives",
    ],
}

_AMBIGUOUS_INDICATORS = [
    "what's happening", "what is happening", "tell me about",
    "thoughts on", "how are", "what about", "any news on",
    "update on", "look into", "analyse", "analyze",
]

_TIME_SENSITIVE_KEYWORDS = [
    "recent", "latest", "current", "now", "today", "this quarter",
    "this year", "just", "new", "updated", "2024", "2025", "2026",
]

_MEMORY_KEYWORDS = [
    "you've researched", "already researched", "you know", "from before",
    "based on", "themes", "sector themes", "cross-cutting",
]

_TOOL_MAP = {
    QueryType.COMPANY_PROFILE: ["company_profile", "financial_data_api", "web_search"],
    QueryType.EARNINGS_ANALYSIS: ["financial_data_api", "earnings_transcript", "web_search", "news_sentiment"],
    QueryType.RISK_ASSESSMENT: ["sec_filing_search", "web_search", "news_sentiment", "financial_data_api", "earnings_transcript"],
    QueryType.INDUSTRY_COMPARISON: ["financial_data_api", "sec_filing_search", "earnings_transcript", "web_search", "peer_comparison", "calculation_engine"],
    QueryType.CONTRADICTORY_DATA: ["financial_data_api", "sec_filing_search", "web_search", "fact_checker", "news_sentiment"],
    QueryType.AMBIGUOUS: ["web_search", "financial_data_api", "news_sentiment"],
    QueryType.SECTOR_ANALYSIS: ["vector_db_search", "web_search", "financial_data_api"],
    QueryType.FULL_RESEARCH: ["company_profile", "financial_data_api", "sec_filing_search", "earnings_transcript", "web_search", "news_sentiment", "peer_comparison", "calculation_engine", "fact_checker", "report_generator"],
    QueryType.OTHER: ["web_search", "financial_data_api"],
}

_COMPLEXITY_MAP = {
    QueryType.COMPANY_PROFILE: ComplexityLevel.SIMPLE,
    QueryType.EARNINGS_ANALYSIS: ComplexityLevel.MODERATE,
    QueryType.RISK_ASSESSMENT: ComplexityLevel.MODERATE,
    QueryType.INDUSTRY_COMPARISON: ComplexityLevel.COMPLEX,
    QueryType.CONTRADICTORY_DATA: ComplexityLevel.COMPLEX,
    QueryType.AMBIGUOUS: ComplexityLevel.MODERATE,
    QueryType.SECTOR_ANALYSIS: ComplexityLevel.COMPLEX,
    QueryType.FULL_RESEARCH: ComplexityLevel.VERY_COMPLEX,
    QueryType.OTHER: ComplexityLevel.SIMPLE,
}

_MAX_TOOL_CALLS = {
    ComplexityLevel.TRIVIAL: 5,
    ComplexityLevel.SIMPLE: 8,
    ComplexityLevel.MODERATE: 12,
    ComplexityLevel.COMPLEX: 16,
    ComplexityLevel.VERY_COMPLEX: 20,
}


class QueryAnalyzer:
    """
    Analyzes a natural-language research query and returns a QueryProfile.

    All classification uses keyword heuristics -- no LLM call.
    """

    def analyze(self, query: str) -> QueryProfile:
        q_lower = query.lower()

        tickers = self._extract_tickers(query)
        companies = self._extract_companies(q_lower)

        # Merge companies → tickers
        for company, ticker in _COMPANY_ALIASES.items():
            if company in q_lower and ticker not in tickers:
                tickers.append(ticker)

        query_type = self._detect_type(q_lower)
        ambiguity = self._detect_ambiguity(q_lower, query_type, tickers)
        complexity = _COMPLEXITY_MAP.get(query_type, ComplexityLevel.MODERATE)

        # Escalate complexity if multi-company
        if len(tickers) >= 3 and complexity.value < ComplexityLevel.COMPLEX.value:
            complexity = ComplexityLevel.COMPLEX

        time_sensitive = any(kw in q_lower for kw in _TIME_SENSITIVE_KEYWORDS)
        requires_memory = any(kw in q_lower for kw in _MEMORY_KEYWORDS)

        profile = QueryProfile(
            original_query=query,
            query_type=query_type,
            complexity=complexity,
            ambiguity=ambiguity,
            extracted_tickers=list(dict.fromkeys(tickers)),  # deduplicate preserving order
            extracted_companies=companies,
            time_sensitive=time_sensitive,
            requires_memory=requires_memory,
            requires_disambiguation=(ambiguity in (AmbiguityLevel.AMBIGUOUS, AmbiguityLevel.VERY_AMBIGUOUS)),
            suggested_tools=_TOOL_MAP.get(query_type, ["web_search"]),
            max_suggested_tool_calls=_MAX_TOOL_CALLS[complexity],
        )

        logger.info("QueryAnalyzer: %s", profile.describe())
        return profile

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    def _extract_tickers(self, query: str) -> List[str]:
        """Extract known ticker symbols from the query."""
        return [t for t in _TICKER_RE.findall(query) if t in _KNOWN_TICKERS]

    def _extract_companies(self, q_lower: str) -> List[str]:
        return [name for name in _COMPANY_ALIASES if name in q_lower]

    def _detect_type(self, q_lower: str) -> QueryType:
        scores = {qt: 0 for qt in QueryType}
        for qt, keywords in _TYPE_PATTERNS.items():
            for kw in keywords:
                if kw in q_lower:
                    scores[qt] += 1

        best = max(scores, key=scores.get)
        if scores[best] == 0:
            # No keyword matched -- check for ambiguous indicators
            if any(ind in q_lower for ind in _AMBIGUOUS_INDICATORS):
                return QueryType.AMBIGUOUS
            return QueryType.OTHER
        return best

    def _detect_ambiguity(
        self,
        q_lower: str,
        query_type: QueryType,
        tickers: List[str],
    ) -> AmbiguityLevel:
        if query_type == QueryType.AMBIGUOUS:
            return AmbiguityLevel.VERY_AMBIGUOUS

        if any(ind in q_lower for ind in _AMBIGUOUS_INDICATORS) and not tickers:
            return AmbiguityLevel.AMBIGUOUS

        word_count = len(q_lower.split())
        if word_count <= 6 and not tickers:
            return AmbiguityLevel.SLIGHTLY_AMBIGUOUS

        return AmbiguityLevel.CLEAR


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_analyzer: Optional[QueryAnalyzer] = None


def get_query_analyzer() -> QueryAnalyzer:
    global _analyzer
    if _analyzer is None:
        _analyzer = QueryAnalyzer()
    return _analyzer
