"""
episodic.py

Episodic (process-level) memory, implemented with SQLite -- a persistent,
queryable, append-only log of every research run: its plan, every tool
call and reasoning step, its final answer, and outcome metadata.

This is the concrete implementation of the "radical transparency" logging
principle from architecture_specification.md Section 3.2 and the C2.2/
Bridgewater lesson set: every decision the agent makes should be logged
and auditable, not just the final output. It's also the raw material for
Day 14's Trace Gallery deliverable and Day 11's evaluation harness (which
can replay a run's full trace to score it after the fact).

Schema (two tables):
    runs          -- one row per ResearchAgent.run() call
    trace_steps   -- one row per TraceStep within a run (FK to runs.run_id)
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id              TEXT PRIMARY KEY,
    query               TEXT NOT NULL,
    plan                TEXT NOT NULL,          -- JSON array of strings
    final_answer        TEXT,
    tool_calls_made      INTEGER NOT NULL,
    replan_cycles_used   INTEGER NOT NULL,
    termination_reason  TEXT NOT NULL,
    token_usage         TEXT NOT NULL,          -- JSON object
    started_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS trace_steps (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      TEXT NOT NULL REFERENCES runs(run_id),
    step_index  INTEGER NOT NULL,
    step_type   TEXT NOT NULL,
    detail      TEXT NOT NULL                  -- JSON object
);

CREATE INDEX IF NOT EXISTS idx_trace_steps_run_id ON trace_steps(run_id);
CREATE INDEX IF NOT EXISTS idx_runs_query ON runs(query);
"""


@dataclass
class RunSummary:
    """A lightweight summary row from the `runs` table (no trace detail)."""

    run_id: str
    query: str
    plan: List[str]
    final_answer: Optional[str]
    tool_calls_made: int
    replan_cycles_used: int
    termination_reason: str
    token_usage: Dict[str, Any]
    started_at: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "query": self.query,
            "plan": self.plan,
            "final_answer": self.final_answer,
            "tool_calls_made": self.tool_calls_made,
            "replan_cycles_used": self.replan_cycles_used,
            "termination_reason": self.termination_reason,
            "token_usage": self.token_usage,
            "started_at": self.started_at,
        }


class EpisodicMemory:
    """
    Usage:
        episodic = EpisodicMemory()
        run_id = episodic.log_run(agent_run_result)
        summary = episodic.get_run(run_id)
        recent = episodic.get_recent_runs(limit=10)
        past = episodic.find_runs_mentioning("Microsoft")
    """

    def __init__(self, db_path: Optional[str] = None) -> None:
        self.db_path = db_path or "./data/episodic_memory.db"
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ------------------------------------------------------------------ #
    # Writing
    # ------------------------------------------------------------------ #
    def log_run(self, result: Any) -> str:
        """
        Persist a full AgentRunResult (from agent/core.py) as one `runs`
        row plus one `trace_steps` row per step in its trace. Accepts
        anything with the same shape as AgentRunResult (duck-typed
        deliberately, to avoid a circular import between memory/ and
        agent/).
        """
        run_id = str(uuid.uuid4())
        started_at = datetime.now(timezone.utc).isoformat()

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO runs
                    (run_id, query, plan, final_answer, tool_calls_made,
                     replan_cycles_used, termination_reason, token_usage, started_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    result.query,
                    json.dumps(result.plan),
                    result.final_answer,
                    result.tool_calls_made,
                    result.replan_cycles_used,
                    result.termination_reason,
                    json.dumps(result.token_usage),
                    started_at,
                ),
            )
            for i, step in enumerate(result.trace):
                conn.execute(
                    "INSERT INTO trace_steps (run_id, step_index, step_type, detail) VALUES (?, ?, ?, ?)",
                    (run_id, i, step.step_type, json.dumps(step.detail, default=str)),
                )

        logger.info(
            "Logged run %s to episodic memory (%d trace steps).", run_id, len(result.trace)
        )
        return run_id

    # ------------------------------------------------------------------ #
    # Reading
    # ------------------------------------------------------------------ #
    def _row_to_summary(self, row: sqlite3.Row) -> RunSummary:
        return RunSummary(
            run_id=row["run_id"],
            query=row["query"],
            plan=json.loads(row["plan"]),
            final_answer=row["final_answer"],
            tool_calls_made=row["tool_calls_made"],
            replan_cycles_used=row["replan_cycles_used"],
            termination_reason=row["termination_reason"],
            token_usage=json.loads(row["token_usage"]),
            started_at=row["started_at"],
        )

    def get_run(self, run_id: str) -> Optional[RunSummary]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        return self._row_to_summary(row) if row else None

    def get_trace(self, run_id: str) -> List[Dict[str, Any]]:
        """Full trace-step detail for a run, in original order."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT step_type, detail FROM trace_steps WHERE run_id = ? ORDER BY step_index",
                (run_id,),
            ).fetchall()
        return [{"step_type": r["step_type"], "detail": json.loads(r["detail"])} for r in rows]

    def get_recent_runs(self, limit: int = 10) -> List[RunSummary]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM runs ORDER BY started_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._row_to_summary(r) for r in rows]

    def find_runs_mentioning(self, text: str, limit: int = 10) -> List[RunSummary]:
        """
        Simple substring search over past queries -- lets the agent (or a
        human) check "have we researched something like this before?"
        without needing the vector store for a quick lookup. Case-insensitive.
        """
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM runs WHERE query LIKE ? ORDER BY started_at DESC LIMIT ?",
                (f"%{text}%", limit),
            ).fetchall()
        return [self._row_to_summary(r) for r in rows]

    def count_runs(self) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS c FROM runs").fetchone()
        return row["c"]
