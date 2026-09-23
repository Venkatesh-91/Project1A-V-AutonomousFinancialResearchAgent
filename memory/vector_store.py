"""
vector_store.py

Long-term (semantic) memory, implemented with Chroma -- a free, local,
zero-setup vector database (see architecture_specification.md Section 4.3
and the project brief's Section E2.1, which recommends Chroma for exactly
this reason).

Chroma's default embedding function (all-MiniLM-L6-v2, run locally via
ONNX) requires no API key and no network calls once its model file has
been downloaded once -- consistent with this project's "genuinely free,
no signup" constraint on every dependency.

This module implements the schema from architecture_specification.md
Section 4.3: every stored record carries its ticker, source_type, date,
and confidence alongside the embedded content, so retrieval can be
filtered by entity (not just similarity) -- the same entity-disambiguation
principle the tool registry's provenance model already follows.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import chromadb
from chromadb.api.models.Collection import Collection

from config.settings import settings

logger = logging.getLogger(__name__)

COLLECTION_NAME = "research_memory"


@dataclass
class VectorSearchResult:
    """One retrieved record from the vector store."""

    document_id: str
    content: str
    metadata: Dict[str, Any]
    similarity_score: float  # higher = more similar (0..1, roughly)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "document_id": self.document_id,
            "content": self.content,
            "metadata": self.metadata,
            "similarity_score": self.similarity_score,
        }


class VectorStore:
    """
    Wraps a Chroma collection with the storage/retrieval schema this
    project's architecture spec defines.

    Usage:
        store = VectorStore()
        doc_id = store.store(
            content="Apple's Q3 revenue grew 15% YoY per the 10-Q.",
            ticker="AAPL",
            source_type="sec_filing",
            confidence=0.95,
        )
        results = store.search("Apple revenue growth", ticker="AAPL", top_k=3)
    """

    def __init__(
        self,
        persist_dir: Optional[str] = None,
        embedding_function: Optional[Any] = None,
    ) -> None:
        """
        Args:
            persist_dir: On-disk path for Chroma's data. Defaults to
                settings.chroma_persist_dir.
            embedding_function: Override the embedding function (used by
                tests to avoid Chroma's default model download). Production
                code should leave this as None to use Chroma's free, local
                default (all-MiniLM-L6-v2).
        """
        path = persist_dir or settings.chroma_persist_dir
        self._client = chromadb.PersistentClient(path=path)

        collection_kwargs: Dict[str, Any] = {"name": COLLECTION_NAME}
        if embedding_function is not None:
            collection_kwargs["embedding_function"] = embedding_function

        self._collection: Collection = self._client.get_or_create_collection(**collection_kwargs)

    # ------------------------------------------------------------------ #
    # Storage
    # ------------------------------------------------------------------ #
    def store(
        self,
        content: str,
        ticker: Optional[str] = None,
        source_type: str = "analysis",
        confidence: float = 0.8,
        verified: bool = False,
        researcher_session: Optional[str] = None,
        extra_metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Store one finding in long-term memory. Returns the generated
        document id.

        Fields mirror architecture_specification.md Section 4.3's vector
        DB schema: ticker, source_type, date (auto-set to now), confidence,
        researcher_session, verified.
        """
        document_id = str(uuid.uuid4())
        metadata: Dict[str, Any] = {
            "ticker": ticker or "",
            "source_type": source_type,
            "date": datetime.now(timezone.utc).isoformat(),
            "confidence": confidence,
            "verified": verified,
            "researcher_session": researcher_session or "",
        }
        if extra_metadata:
            metadata.update(extra_metadata)

        self._collection.add(documents=[content], metadatas=[metadata], ids=[document_id])
        logger.debug("Stored vector memory record %s (ticker=%s)", document_id, ticker)
        return document_id

    def store_many(self, records: List[Dict[str, Any]]) -> List[str]:
        """Batch version of store() -- each dict in `records` takes the
        same keyword arguments store() accepts."""
        return [self.store(**record) for record in records]

    # ------------------------------------------------------------------ #
    # Retrieval
    # ------------------------------------------------------------------ #
    def search(
        self,
        query: str,
        top_k: int = 5,
        ticker: Optional[str] = None,
        source_type: Optional[str] = None,
    ) -> List[VectorSearchResult]:
        """
        Semantic search over stored findings, optionally filtered by
        ticker and/or source_type (metadata filters, applied before
        similarity ranking -- per architecture_specification.md Section
        4.3's "always filter by entity_id first" retrieval rule).
        """
        where: Dict[str, Any] = {}
        if ticker:
            where["ticker"] = ticker
        if source_type:
            where["source_type"] = source_type

        query_kwargs: Dict[str, Any] = {"query_texts": [query], "n_results": top_k}
        if where:
            # Chroma requires $and for multiple filter keys
            query_kwargs["where"] = (
                where if len(where) == 1 else {"$and": [{k: v} for k, v in where.items()]}
            )

        results = self._collection.query(**query_kwargs)

        output: List[VectorSearchResult] = []
        ids = results.get("ids", [[]])[0]
        documents = results.get("documents", [[]])[0]
        metadatas = results.get("metadatas", [[]])[0]
        distances = results.get("distances", [[]])[0]

        for doc_id, content, metadata, distance in zip(ids, documents, metadatas, distances):
            # Chroma returns a distance (lower = more similar); convert to
            # a similarity score (higher = more similar) for a more
            # intuitive API surface.
            similarity = 1.0 / (1.0 + distance) if distance is not None else 0.0
            output.append(
                VectorSearchResult(
                    document_id=doc_id,
                    content=content,
                    metadata=metadata,
                    similarity_score=round(similarity, 4),
                )
            )

        return output

    def count(self) -> int:
        """Number of records currently stored -- useful for tests and diagnostics."""
        return self._collection.count()
