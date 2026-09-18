"""
run_challenge.py

Run this LOCALLY (on your machine, with real internet access and your
.env configured) to actually exercise the real tool integrations against
live APIs and produce results/challenge_1.md and results/challenge_2.md --
this cannot be run inside the sandboxed environment that generated this
codebase, since that environment's network is restricted to package
registries only (no access to sec.gov, Yahoo Finance, or search engines).

Usage:
    python scripts/run_challenge.py 1   # Challenge 1: Microsoft company profile
    python scripts/run_challenge.py 2   # Challenge 2: Apple earnings analysis

This script calls the REAL ResearchAgent (agent/core.py) with your REAL
Gemini API key, so it will use a small amount of your free-tier quota.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from agent.core import ResearchAgent  # noqa: E402
from agent.llm_client import LLMClient  # noqa: E402
from config.logging_config import setup_logging  # noqa: E402
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
}


def format_result_as_markdown(challenge_number: str, result) -> str:
    lines = [
        f"# Challenge {challenge_number} Result",
        "",
        f"**Query:** {result.query}",
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
        print("Usage: python scripts/run_challenge.py [1|2]")
        sys.exit(1)

    challenge_number = sys.argv[1]
    challenge = CHALLENGES[challenge_number]

    setup_logging()

    print(f"Running Challenge {challenge_number}...")
    print(f"Query: {challenge['query']}\n")

    llm_client = LLMClient()
    tool_registry = build_default_registry()
    agent = ResearchAgent(llm_client=llm_client, tool_registry=tool_registry)

    result = agent.run(challenge["query"])

    output_path = Path(__file__).parent.parent / challenge["output_file"]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(format_result_as_markdown(challenge_number, result), encoding="utf-8")

    print(f"\nDone. Result written to {output_path}")
    print(f"Tool calls made: {result.tool_calls_made}")
    print(f"Termination reason: {result.termination_reason}")
    print(f"\n--- Final Answer ---\n{result.final_answer}")


if __name__ == "__main__":
    main()
