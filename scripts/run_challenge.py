"""
run_challenge.py

Run this LOCALLY (on your machine, with real internet access and your
.env configured) to actually exercise the real tool integrations against
live APIs and produce results/challenge_N.md -- this cannot be run inside
the sandboxed environment that generated this codebase, since that
environment's network is restricted to package registries only (no access
to sec.gov, Yahoo Finance, or search engines).

Usage:
    python scripts/run_challenge.py 1   # Challenge 1: Microsoft company profile
    python scripts/run_challenge.py 2   # Challenge 2: Apple earnings analysis
    python scripts/run_challenge.py 7   # Challenge 7: sector themes from memory
                                         #   (run 1 and/or 2 first -- this is
                                         #   the Day 6 memory test: it only
                                         #   finds something interesting if
                                         #   there's prior research to recall)

This script calls the REAL ResearchAgent (agent/core.py) with your REAL
Groq API key, so it will use a small amount of your free-tier quota. Every
run is also logged to episodic memory (./data/episodic_memory.db) and,
depending on what the agent chooses to store, to long-term vector memory
(./data/chroma) -- see memory/episodic.py and memory/vector_store.py.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from agent.core import ResearchAgent  # noqa: E402
from agent.llm_client import LLMClient  # noqa: E402
from config.logging_config import setup_logging  # noqa: E402
from memory.episodic import EpisodicMemory  # noqa: E402
from tools.tool_registry import build_default_registry  # noqa: E402

CHALLENGES = {
    "1": {
        "query": (
            "Create a comprehensive profile of Microsoft Corporation "
            "including business overview, financial summary, key "
            "executives, and recent developments."
        ),
        "output_file": "results/challenge_1.md",
    },
    "2": {
        "query": (
            "Analyze Apple Inc.'s most recent quarterly earnings. Compare "
            "actual results to consensus estimates and identify key "
            "takeaways from the earnings call."
        ),
        "output_file": "results/challenge_2.md",
    },
    "7": {
        "query": (
            "Based on the companies you've already researched, what "
            "themes emerge across the technology sector? Identify "
            "cross-cutting risks and opportunities. Use vector_db_search "
            "to check your long-term memory for prior research before "
            "concluding you have nothing to draw on."
        ),
        "output_file": "results/challenge_7.md",
    },
}


def format_result_as_markdown(challenge_number: str, result, run_id: str) -> str:
    lines = [
        f"# Challenge {challenge_number} Result",
        "",
        f"**Query:** {result.query}",
        "",
        f"**Episodic memory run_id:** `{run_id}`",
        "",
        "## Plan",
        "",
    ]
    for task in result.plan:
        lines.append(f"- {task}")

    lines += [
        "",
        "## Final Answer",
        "",
        result.final_answer or "*(no final answer produced)*",
        "",
        "## Run Metadata",
        "",
        f"- Tool calls made: {result.tool_calls_made}",
        f"- Re-planning cycles used: {result.replan_cycles_used}",
        f"- Termination reason: {result.termination_reason}",
        f"- Token usage: {result.token_usage}",
        "",
        "## Full Trace",
        "",
    ]
    for i, step in enumerate(result.trace, start=1):
        lines.append(f"### Step {i}: {step.step_type}")
        lines.append("```")
        lines.append(str(step.detail))
        lines.append("```")
        lines.append("")

    return "\n".join(lines)


def main() -> None:
    if len(sys.argv) != 2 or sys.argv[1] not in CHALLENGES:
        print(f"Usage: python scripts/run_challenge.py [{'|'.join(CHALLENGES)}]")
        sys.exit(1)

    challenge_number = sys.argv[1]
    challenge = CHALLENGES[challenge_number]

    setup_logging()

    print(f"Running Challenge {challenge_number}...")
    print(f"Query: {challenge['query']}\n")

    llm_client = LLMClient()
    tool_registry = build_default_registry()
    episodic_memory = EpisodicMemory()  # ./data/episodic_memory.db by default
    agent = ResearchAgent(
        llm_client=llm_client,
        tool_registry=tool_registry,
        episodic_memory=episodic_memory,
    )

    result = agent.run(challenge["query"])

    # The run_id isn't returned by agent.run() directly (AgentRunResult
    # doesn't carry it, to keep that dataclass storage-agnostic) -- but
    # it's the most recently logged run, so this is a reliable way to
    # surface it for the output file without changing that contract.
    recent = episodic_memory.get_recent_runs(limit=1)
    run_id = recent[0].run_id if recent else "(not logged)"

    output_path = Path(__file__).parent.parent / challenge["output_file"]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        format_result_as_markdown(challenge_number, result, run_id), encoding="utf-8"
    )

    print(f"\nDone. Result written to {output_path}")
    print(f"Tool calls made: {result.tool_calls_made}")
    print(f"Termination reason: {result.termination_reason}")
    print(f"Logged to episodic memory as run {run_id}")
    print(f"\n--- Final Answer ---\n{result.final_answer}")


if __name__ == "__main__":
    main()
