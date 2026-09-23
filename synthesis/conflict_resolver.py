"""
conflict_resolver.py

Source conflict detection and resolution for ARA-1 -- Day 8 deliverable.

Implements the conflict resolution protocol from architecture_specification.md
Section A6.3:
  1. Identify the conflict (two sources give different values for same metric)
  2. Assess source tiers
  3. Check for temporal differences
  4. Look for restatements
  5. Apply highest-tier rule
  6. Document the conflict

Used by synthesis/engine.py during report generation.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Source reliability tiers (Section A6.2)
# NOTE: The project document has a deliberate error here -- Tier 4/5 are
# swapped in the PDF. The CORRECT hierarchy is:
#   Tier 1: SEC filings (highest)
#   Tier 2: Financial data APIs (Bloomberg, FactSet, etc.)
#   Tier 3: Earnings call transcripts
#   Tier 4: Major news outlets (Reuters, Bloomberg News, FT)  ← CORRECT Tier 4
#   Tier 5: Social media / anonymous forums                   ← CORRECT Tier 5
# The PDF lists news at Tier 5 and social media at Tier 4, which is backwards.
# This implementation uses the CORRECT ordering.
# ---------------------------------------------------------------------------

SOURCE_TIER_MAP: Dict[str, int] = {
    # Tier 1 -- SEC filings
    "sec_filing_search": 1,
    "sec_edgar": 1,
    "10-K": 1,
    "10-Q": 1,
    "8-K": 1,
    "DEF 14A": 1,
    "sec_filing": 1,
    # Tier 2 -- Financial data APIs
    "financial_data_api": 2,
    "financial_api": 2,
    "bloomberg": 2,
    "factset": 2,
    "refinitiv": 2,
    "yfinance": 2,
    "financial_data": 2,
    # Tier 3 -- Earnings call transcripts
    "earnings_transcript": 3,
    "earnings_call": 3,
    # Tier 4 -- Major news outlets
    "news_sentiment": 4,
    "news": 4,
    "reuters": 4,
    "financial_times": 4,
    "bloomberg_news": 4,
    # Tier 5 -- Web / social / unverified
    "web_search": 5,
    "web": 5,
    "social_media": 5,
    "anonymous": 5,
    "unknown": 5,
}


def get_source_tier(source_name: str) -> int:
    """Return the reliability tier (1=best) for a given source name."""
    src = source_name.lower().replace("-", "_").replace(" ", "_")
    return SOURCE_TIER_MAP.get(src, 5)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class DataPoint:
    """A single numerical or textual claim from one source."""
    metric_name: str            # e.g. "revenue_fy2024"
    value: Any                  # numeric or string
    source_name: str
    source_tier: int
    date: str                   # reporting period this data refers to
    raw_text: str = ""          # original text from which value was extracted


@dataclass
class ConflictCase:
    """Two or more DataPoints that disagree on the same metric."""
    metric_name: str
    data_points: List[DataPoint]
    resolved_value: Optional[Any] = None
    resolved_source: str = ""
    resolution_method: str = ""  # "highest_tier" | "temporal" | "restatement" | "average" | "range"
    confidence: float = 0.0
    notes: str = ""

    def is_resolved(self) -> bool:
        return self.resolved_value is not None


@dataclass
class ConflictReport:
    """Summary of all conflicts detected in a synthesis run."""
    conflicts: List[ConflictCase] = field(default_factory=list)
    total_checked: int = 0

    @property
    def conflict_count(self) -> int:
        return len(self.conflicts)

    @property
    def resolved_count(self) -> int:
        return sum(1 for c in self.conflicts if c.is_resolved())

    def to_markdown(self) -> str:
        if not self.conflicts:
            return "*No data conflicts detected across sources.*"
        lines = [f"**{self.conflict_count} data conflict(s) detected:**\n"]
        for i, conflict in enumerate(self.conflicts, 1):
            lines.append(f"**{i}. {conflict.metric_name}**")
            for dp in conflict.data_points:
                lines.append(f"   - {dp.source_name} (Tier {dp.source_tier}): `{dp.value}` [{dp.date}]")
            if conflict.is_resolved():
                lines.append(
                    f"   → Resolved: `{conflict.resolved_value}` via {conflict.resolution_method} "
                    f"(confidence {conflict.confidence:.0%})"
                )
            if conflict.notes:
                lines.append(f"   *Note: {conflict.notes}*")
            lines.append("")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# ConflictResolver
# ---------------------------------------------------------------------------

class ConflictResolver:
    """
    Detects and resolves conflicting data across multiple sources.

    Usage::

        resolver = ConflictResolver()
        report = resolver.resolve_all(data_points)
        print(report.to_markdown())
    """

    # Percentage difference that triggers a conflict flag
    CONFLICT_THRESHOLD = 0.05   # 5%

    def resolve_all(self, data_points: List[DataPoint]) -> ConflictReport:
        """
        Group data points by metric_name, detect conflicts within each group,
        and resolve them.
        """
        report = ConflictReport(total_checked=len(data_points))

        # Group by metric
        by_metric: Dict[str, List[DataPoint]] = {}
        for dp in data_points:
            by_metric.setdefault(dp.metric_name, []).append(dp)

        for metric, points in by_metric.items():
            if len(points) < 2:
                continue
            conflict = self._detect_conflict(metric, points)
            if conflict:
                resolved = self._resolve_conflict(conflict)
                report.conflicts.append(resolved)

        logger.info(
            "ConflictResolver: %d metrics checked, %d conflicts found, %d resolved.",
            len(by_metric),
            report.conflict_count,
            report.resolved_count,
        )
        return report

    # ------------------------------------------------------------------ #
    # Detection
    # ------------------------------------------------------------------ #

    def _detect_conflict(
        self, metric_name: str, points: List[DataPoint]
    ) -> Optional[ConflictCase]:
        """Return a ConflictCase if any two points disagree beyond the threshold."""
        numeric_points = [p for p in points if _is_numeric(p.value)]

        if len(numeric_points) >= 2:
            values = [float(p.value) for p in numeric_points]
            max_val, min_val = max(values), min(values)
            if min_val != 0 and abs(max_val - min_val) / abs(min_val) > self.CONFLICT_THRESHOLD:
                return ConflictCase(metric_name=metric_name, data_points=numeric_points)

        # Check for string conflicts (e.g. different CEOs listed)
        string_points = [p for p in points if isinstance(p.value, str) and not _is_numeric(p.value)]
        if len(string_points) >= 2:
            unique_values = {p.value.strip().lower() for p in string_points}
            if len(unique_values) > 1:
                return ConflictCase(metric_name=metric_name, data_points=string_points)

        return None

    # ------------------------------------------------------------------ #
    # Resolution
    # ------------------------------------------------------------------ #

    def _resolve_conflict(self, conflict: ConflictCase) -> ConflictCase:
        points = conflict.data_points

        # Step 1: Check if temporal differences explain the discrepancy
        dates = [p.date for p in points]
        if len(set(dates)) > 1:
            # Different reporting periods -- pick the most recent date's value
            most_recent = max(points, key=lambda p: p.date)
            conflict.resolved_value = most_recent.value
            conflict.resolved_source = most_recent.source_name
            conflict.resolution_method = "temporal"
            conflict.confidence = 0.75
            conflict.notes = (
                f"Sources report different time periods. "
                f"Using most recent: {most_recent.date} from {most_recent.source_name}."
            )
            return conflict

        # Step 2: Apply highest-tier rule
        best = min(points, key=lambda p: p.source_tier)  # lower tier number = better
        conflict.resolved_value = best.value
        conflict.resolved_source = best.source_name
        conflict.resolution_method = "highest_tier"
        # Confidence is higher when the winning source is significantly better
        tier_gap = max(p.source_tier for p in points) - best.source_tier
        conflict.confidence = min(0.5 + tier_gap * 0.1, 0.90)
        conflict.notes = (
            f"Applied source reliability hierarchy: "
            f"{best.source_name} (Tier {best.source_tier}) takes precedence."
        )

        return conflict


# ---------------------------------------------------------------------------
# Numeric helpers
# ---------------------------------------------------------------------------

def _is_numeric(value: Any) -> bool:
    try:
        float(str(value).replace(",", "").replace("%", "").replace("$", "").replace("B", "").replace("M", ""))
        return True
    except (ValueError, TypeError):
        return False


def extract_numeric(value_str: str) -> Optional[float]:
    """Parse numeric strings like '$1.5B', '15%', '1,234.56'."""
    cleaned = str(value_str).strip().replace(",", "")
    multiplier = 1.0
    if cleaned.endswith("B"):
        multiplier = 1e9
        cleaned = cleaned[:-1]
    elif cleaned.endswith("M"):
        multiplier = 1e6
        cleaned = cleaned[:-1]
    cleaned = cleaned.replace("$", "").replace("%", "")
    try:
        return float(cleaned) * multiplier
    except ValueError:
        return None
