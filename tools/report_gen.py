"""
report_gen.py

Stub implementation of the `report_generator` tool. This is a genuinely
working (not mocked) simple markdown formatter -- it takes whatever
sections dict it's given and renders a structured markdown report. Day 8-11
add the source appendix, hypothesis section, and confidence annotations
described in the architecture spec.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from tools.tool_registry import ToolResult


def run(
    template: str,
    sections: Dict[str, Any],
    sources: Optional[List[str]] = None,
) -> ToolResult:
    """Render sections into a structured markdown report."""
    sources = sources or []

    lines = [f"# Research Report ({template})", ""]
    for heading, content in sections.items():
        lines.append(f"## {heading}")
        lines.append(str(content))
        lines.append("")

    if sources:
        lines.append("## Sources")
        for src in sources:
            lines.append(f"- {src}")

    markdown = "\n".join(lines)

    return ToolResult(
        success=True,
        data={"template": template, "markdown": markdown},
        source_name="Report Generator",
        source_tier=1,  # this is agent-authored, not an external source
        retrieved_at=datetime.now(timezone.utc).isoformat(),
    )
