"""
test_memory.py

Tests for Day 6's three memory components. Unlike the real API tools from
Day 5, these run FULLY LOCALLY with no network dependency once Chroma's
default embedding model is cached -- so, unusually for this project, these
tests exercise the real implementations directly rather than needing
mocks. A lightweight, deterministic embedding function is injected into
VectorStore for speed and to avoid any first-run model download during
test collection; production code (tools/vector_db_search.py,
tools/vector_db_store.py) uses Chroma's real default embedding function.
"""

from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path

import pytest
from chromadb import Documents, EmbeddingFunction

from agent.core import AgentRunResult, TraceStep
from memory.context_manager import ContextManager
from memory.episodic import EpisodicMemory
from memory.vector_store import VectorStore


# ---------------------------------------------------------------------- #
# A deterministic, dependency-free embedding function for tests -- avoids
# needing to download Chroma's default model during test collection.
# Similar texts won't cluster meaningfully with this (it's not a real
# embedding), so tests here check storage/retrieval MECHANICS, not
# semantic search QUALITY.
# ---------------------------------------------------------------------- #
class _DeterministicTestEmbeddingFunction(EmbeddingFunction):
    def __init__(self) -> None:
        pass

    def __call__(self, input: Documents):
        vectors = []
        for text in input:
            digest = hashlib.sha256(text.encode("utf-8")).digest()
            vectors.append([b / 255.0 for b in digest[:32]])
        return vectors

    @staticmethod
    def name() -> str:
        return "deterministic_test_embedding_function"


# ---------------------------------------------------------------------- #
# VectorStore
# ---------------------------------------------------------------------- #
@pytest.fixture
def vector_store(tmp_path):
    return VectorStore(
        persist_dir=str(tmp_path / "chroma_test"),
        embedding_function=_DeterministicTestEmbeddingFunction(),
    )


def test_vector_store_store_returns_document_id(vector_store):
    doc_id = vector_store.store(content="Apple's revenue grew 15% YoY.", ticker="AAPL")
    assert isinstance(doc_id, str) and len(doc_id) > 0


def test_vector_store_count_reflects_stored_records(vector_store):
    assert vector_store.count() == 0
    vector_store.store(content="Finding one", ticker="AAPL")
    vector_store.store(content="Finding two", ticker="MSFT")
    assert vector_store.count() == 2


def test_vector_store_search_finds_stored_content(vector_store):
    vector_store.store(content="Apple's Q3 revenue grew 15% year over year.", ticker="AAPL")
    results = vector_store.search("Apple revenue growth", top_k=5)
    assert len(results) == 1
    assert results[0].content == "Apple's Q3 revenue grew 15% year over year."
    assert results[0].metadata["ticker"] == "AAPL"


def test_vector_store_search_filters_by_ticker(vector_store):
    vector_store.store(content="Apple finding", ticker="AAPL")
    vector_store.store(content="Microsoft finding", ticker="MSFT")

    aapl_only = vector_store.search("finding", ticker="AAPL", top_k=10)
    assert len(aapl_only) == 1
    assert aapl_only[0].metadata["ticker"] == "AAPL"


def test_vector_store_search_returns_empty_list_when_store_is_empty(vector_store):
    results = vector_store.search("anything", top_k=5)
    assert results == []


def test_vector_store_metadata_includes_full_provenance_schema(vector_store):
    vector_store.store(
        content="A finding",
        ticker="AAPL",
        source_type="sec_filing",
        confidence=0.95,
        verified=True,
        researcher_session="session-123",
    )
    results = vector_store.search("finding", top_k=1)
    metadata = results[0].metadata
    assert metadata["ticker"] == "AAPL"
    assert metadata["source_type"] == "sec_filing"
    assert metadata["confidence"] == 0.95
    assert metadata["verified"] is True
    assert metadata["researcher_session"] == "session-123"
    assert "date" in metadata  # auto-populated


def test_vector_store_persists_across_instances(tmp_path):
    """A new VectorStore instance pointed at the same directory should see
    data written by a prior instance -- confirms real on-disk persistence,
    not just in-memory state."""
    persist_dir = str(tmp_path / "persist_test")
    store1 = VectorStore(persist_dir=persist_dir, embedding_function=_DeterministicTestEmbeddingFunction())
    store1.store(content="Persisted finding", ticker="AAPL")

    store2 = VectorStore(persist_dir=persist_dir, embedding_function=_DeterministicTestEmbeddingFunction())
    assert store2.count() == 1


# ---------------------------------------------------------------------- #
# EpisodicMemory
# ---------------------------------------------------------------------- #
@pytest.fixture
def episodic(tmp_path):
    return EpisodicMemory(db_path=str(tmp_path / "episodic_test.db"))


@pytest.fixture
def sample_run_result():
    return AgentRunResult(
        query="Research Microsoft",
        plan=["Get company profile", "Get financials"],
        final_answer="Microsoft is a technology company with strong financials.",
        trace=[
            TraceStep("plan", {"tasks": ["Get company profile", "Get financials"]}),
            TraceStep("tool_call", {"tool": "company_profile", "args": {"ticker": "MSFT"}}),
            TraceStep("final_answer", {"text": "Microsoft is a technology company."}),
        ],
        tool_calls_made=1,
        replan_cycles_used=0,
        termination_reason="no_further_tool_calls",
        token_usage={"total_calls": 3, "total_tokens": 500},
    )


def test_episodic_log_run_returns_run_id(episodic, sample_run_result):
    run_id = episodic.log_run(sample_run_result)
    assert isinstance(run_id, str) and len(run_id) > 0


def test_episodic_get_run_returns_matching_summary(episodic, sample_run_result):
    run_id = episodic.log_run(sample_run_result)
    summary = episodic.get_run(run_id)

    assert summary is not None
    assert summary.query == "Research Microsoft"
    assert summary.plan == ["Get company profile", "Get financials"]
    assert summary.final_answer == sample_run_result.final_answer
    assert summary.tool_calls_made == 1
    assert summary.termination_reason == "no_further_tool_calls"
    assert summary.token_usage == {"total_calls": 3, "total_tokens": 500}


def test_episodic_get_run_returns_none_for_unknown_id(episodic):
    assert episodic.get_run("does-not-exist") is None


def test_episodic_get_trace_returns_full_ordered_trace(episodic, sample_run_result):
    run_id = episodic.log_run(sample_run_result)
    trace = episodic.get_trace(run_id)

    assert len(trace) == 3
    assert trace[0]["step_type"] == "plan"
    assert trace[1]["step_type"] == "tool_call"
    assert trace[1]["detail"]["tool"] == "company_profile"
    assert trace[2]["step_type"] == "final_answer"


def test_episodic_get_recent_runs_orders_newest_first(episodic, sample_run_result):
    import copy

    first = copy.deepcopy(sample_run_result)
    first.query = "First query"
    second = copy.deepcopy(sample_run_result)
    second.query = "Second query"

    episodic.log_run(first)
    episodic.log_run(second)

    recent = episodic.get_recent_runs(limit=10)
    assert len(recent) == 2
    assert recent[0].query == "Second query"  # most recently logged first
    assert recent[1].query == "First query"


def test_episodic_find_runs_mentioning_matches_substring(episodic, sample_run_result):
    episodic.log_run(sample_run_result)
    matches = episodic.find_runs_mentioning("Microsoft")
    assert len(matches) == 1

    no_matches = episodic.find_runs_mentioning("Nonexistent Company XYZ")
    assert no_matches == []


def test_episodic_count_runs(episodic, sample_run_result):
    assert episodic.count_runs() == 0
    episodic.log_run(sample_run_result)
    assert episodic.count_runs() == 1


def test_episodic_persists_across_instances(tmp_path, sample_run_result):
    db_path = str(tmp_path / "persist_episodic.db")
    ep1 = EpisodicMemory(db_path=db_path)
    run_id = ep1.log_run(sample_run_result)

    ep2 = EpisodicMemory(db_path=db_path)
    summary = ep2.get_run(run_id)
    assert summary is not None
    assert summary.query == "Research Microsoft"


# ---------------------------------------------------------------------- #
# ContextManager
# ---------------------------------------------------------------------- #
@pytest.fixture
def context_manager():
    return ContextManager(
        max_tool_result_chars=100,
        max_conversation_chars_estimate=2000,
        min_kept_recent_turns=2,
    )


def test_truncate_tool_result_leaves_short_content_unchanged(context_manager):
    short = '{"data": "small"}'
    assert context_manager.truncate_tool_result(short) == short


def test_truncate_tool_result_truncates_long_content(context_manager):
    long_content = "x" * 500
    result = context_manager.truncate_tool_result(long_content)
    assert len(result) < len(long_content)
    assert "truncated" in result


def test_trim_conversation_leaves_small_conversation_unchanged(context_manager):
    conversation = [
        {"role": "user", "content": "Research AAPL"},
        {"role": "assistant", "content": "Sure, looking into it."},
    ]
    original = list(conversation)
    was_trimmed = context_manager.trim_conversation_if_needed(conversation)
    assert was_trimmed is False
    assert conversation == original


def test_trim_conversation_drops_oldest_turns_when_too_large(context_manager):
    conversation = [{"role": "user", "content": "Research a company in depth"}]
    for i in range(30):
        conversation.append({"role": "tool", "tool_call_id": f"call_{i}", "content": "y" * 200})
    conversation.append({"role": "assistant", "content": "Recent turn 1"})
    conversation.append({"role": "assistant", "content": "Recent turn 2"})

    original_length = len(conversation)
    was_trimmed = context_manager.trim_conversation_if_needed(conversation)

    assert was_trimmed is True
    assert len(conversation) < original_length
    assert conversation[0]["content"] == "Research a company in depth"
    assert any("trimmed" in str(m.get("content", "")) for m in conversation)
    assert conversation[-1]["content"] == "Recent turn 2"
    assert conversation[-2]["content"] == "Recent turn 1"
