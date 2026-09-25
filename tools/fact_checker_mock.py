"""
fact_checker.py

Stub implementation of the `fact_checker` tool. Day 8/9 wires this into the
real conflict-resolution and quantitative-triangulation logic described in
the architecture spec (Section 7 / A6.3). For now it returns a plausible
mock verification result.
"""

from datetime import datetime, timezone
from typing import List, Optional

from tools.tool_registry import ToolResult


def run(claim: str, sources: Optional[List[str]] = None) -> ToolResult:
    """Mock fact verification."""
    sources = sources or []
    return ToolResult(
        success=True,
        data={
            "claim": claim,
            "verification_status": "unverified_mock",
            "supporting_sources_checked": sources,
            "confidence_score": 0.5,
            "note": (
                "[MOCK DATA] Real cross-referencing logic (tier + recency "
                "based conflict resolution) is implemented in Day 8's "
                "synthesis engine, not in this stub."
            ),
        },
        source_name="Fact Checker (mock)",
        source_tier=1,
        retrieved_at=datetime.now(timezone.utc).isoformat(),
    )
