"""
vector_db_search.py

Tool wrapper around memory/vector_store.py's VectorStore.search(), exposed
to the LLM as the `vector_db_search` tool. Uses a lazily-initialized,
module-level VectorStore instance -- the same pattern this project's other
stateful tools use (e.g. tools/http_utils.py's rate limiters/caches) --
so every call within a process shares one on-disk Chroma store rather than
re-opening it each time.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from memory.vector_store import VectorStore
from tools.tool_registry import ToolResult

_store: Optional[VectorStore] = None


def _get_store() -> VectorStore:
    global _store
    if _store is None:
        _store = VectorStore()
    return _store


def run(query: str, ticker: Optional[str] = None, top_k: int = 5) -> ToolResult:
    """Search long-term memory for previously stored research findings."""
    try:
        store = _get_store()
        results = store.search(query=query, top_k=top_k, ticker=ticker)
    except Exception as exc:  # noqa: BLE001 -- a memory-lookup failure
        # should never crash a research run; report it as a soft failure
        # instead (there is no meaningful fallback for this tool).
        return ToolResult(
            success=False,
            data=None,
            source_name="Long-Term Memory (Chroma)",
            source_tier=2,
            retrieved_at=datetime.now(timezone.utc).isoformat(),
            error=f"Vector memory search failed: {exc}",
        )

    return ToolResult(
        success=True,
        data={
            "query": query,
            "num_results": len(results),
            "results": [r.to_dict() for r in results],
        },
        source_name="Long-Term Memory (Chroma)",
        source_tier=2,  # the agent's own prior, presumably-verified findings
        retrieved_at=datetime.now(timezone.utc).isoformat(),
    )
