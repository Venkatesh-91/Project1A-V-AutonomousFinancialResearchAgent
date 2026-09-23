"""
vector_db_store.py

Tool wrapper around memory/vector_store.py's VectorStore.store(), exposed
to the LLM as the `vector_db_store` tool. Shares the same lazily
-initialized, module-level VectorStore instance as vector_db_search.py so
both tools operate on the same on-disk Chroma collection within a process.
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


def run(content: str, ticker: Optional[str] = None, source_type: Optional[str] = None) -> ToolResult:
    """Store a research finding in long-term memory for future retrieval."""
    try:
        store = _get_store()
        document_id = store.store(
            content=content,
            ticker=ticker,
            source_type=source_type or "analysis",
            confidence=0.8,
        )
    except Exception as exc:  # noqa: BLE001
        return ToolResult(
            success=False,
            data=None,
            source_name="Long-Term Memory (Chroma)",
            source_tier=1,
            retrieved_at=datetime.now(timezone.utc).isoformat(),
            error=f"Vector memory store failed: {exc}",
        )

    return ToolResult(
        success=True,
        data={"document_id": document_id, "stored": True},
        source_name="Long-Term Memory (Chroma)",
        source_tier=1,  # this is the agent recording its own finding
        retrieved_at=datetime.now(timezone.utc).isoformat(),
    )
