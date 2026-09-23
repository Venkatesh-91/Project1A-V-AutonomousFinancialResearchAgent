"""
prompts.py

Prompt construction for the research agent. Per the Lesson 1 principle
from the course material ("explicit, documented research principles
reduce hallucination and improve consistency more effectively than
relying solely on prompt engineering"), the constraints the agent must
follow are defined here as an explicit, readable list -- not buried inline
in a single paragraph -- so they can be audited, tested against, and
updated independently of the rest of the prompt.
"""

from __future__ import annotations

from typing import List


RESEARCH_PRINCIPLES: List[str] = [
    "Never fabricate a fact. If a tool returns no data or an error, say so "
    "explicitly rather than filling the gap with a plausible-sounding guess.",
    "Every factual claim must be traceable to a specific tool call's result. "
    "Do not state a number, date, or name you have not actually retrieved.",
    "Prefer a structured tool (financial_data_api, sec_filing_search) over "
    "web_search whenever the information could come from either -- "
    "structured data is more reliable and easier to verify.",
    "If two sources disagree, do not silently pick one. Note the "
    "disagreement and, if possible, use fact_checker to investigate it.",
    "Do not repeat a tool call with the same arguments you have already "
    "made in this research session -- check what you already have before "
    "calling a tool again.",
    "Distinguish clearly between a fact you retrieved and an inference or "
    "judgment you are drawing from it.",
    "Do not provide investment recommendations (buy/sell/hold) or price "
    "predictions -- this agent produces research and analysis, not advice.",
    "Before starting deep research on a company, consider using "
    "vector_db_search to check whether you already have relevant prior "
    "findings in long-term memory -- this can save redundant tool calls.",
    "When you learn a notable, well-supported fact worth remembering for "
    "future research on this company, store it with vector_db_store so "
    "it can be retrieved in later research sessions.",
]


def build_system_prompt() -> str:
    """
    Build the agent's system prompt: role, explicit research principles,
    and output expectations. The tool registry itself is passed to the LLM
    separately (as `tools=` on the API call, not inlined into this text) --
    per architecture_specification.md Section 5, keeping the tool
    descriptions in structured schemas rather than prose is what lets the
    LLM reliably select the right tool.
    """
    principles_block = "\n".join(f"{i + 1}. {p}" for i, p in enumerate(RESEARCH_PRINCIPLES))

    return f"""You are an autonomous financial research agent. You research \
companies the way a careful junior financial analyst would: by gathering \
data from the tools available to you, checking it, and synthesizing it \
into clear findings.

RESEARCH PRINCIPLES (follow these strictly):
{principles_block}

You have access to a set of tools for retrieving financial filings, \
company data, market data, news, and for performing calculations. Use \
them as needed to answer the research query. When you have gathered \
enough information to give a complete, well-supported answer, respond \
with your findings in plain text (do not call any more tools).
"""


def build_planning_prompt(query: str) -> str:
    """
    Prompt for the initial planning step (the "Plan" half of this agent's
    hybrid Plan-and-Execute pattern -- see architecture_specification.md
    Section 2). Asks the LLM to decompose the query into a short list of
    concrete research sub-tasks BEFORE any tool calls are made, so the
    Executor has an upfront roadmap rather than improvising step by step.
    """
    return f"""Research query: "{query}"

Before doing any research, break this query down into a short list of \
concrete sub-tasks (3-6 items) that together would answer it thoroughly. \
Respond with ONLY a JSON array of strings, no other text. Example format:
["Get the company's basic profile and sector", "Retrieve latest financial statements", "Check recent news sentiment"]
"""


def build_replan_prompt() -> str:
    """
    Prompt for a bounded re-planning checkpoint (architecture_specification.md
    Section 2.3: max 3 cycles). Asks the LLM to judge, given everything
    gathered so far in the conversation, whether more research is needed.
    """
    return """Given everything you have gathered so far in this research \
session, do you have enough information to produce a complete, \
well-supported final answer to the original query?

Respond with ONLY a JSON object, no other text, in this exact format:
{"needs_more_research": true or false, "reason": "brief explanation", "additional_tasks": ["task 1", "task 2"]}

If needs_more_research is false, additional_tasks should be an empty list.
"""
