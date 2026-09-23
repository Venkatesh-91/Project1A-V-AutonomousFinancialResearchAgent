"""
test_agent.py

Tests the ResearchAgent cognitive loop end-to-end using a scripted fake
LLM client and the REAL tool registry from Day 2 (with its mock tool data
-- no network calls, no API key required). This mirrors Day 4's task 5:
"Test the agent with a simple query (Challenge 1: Microsoft company
profile) using mock tool data."

The fake LLM client returns a pre-scripted sequence of fake "raw response"
objects (shaped like a groq ChatCompletion) regardless of input, which lets
us deterministically drive the agent through:
    plan -> one tool call -> final answer -> re-plan checkpoint (says done)
and assert on the exact resulting AgentRunResult -- something that isn't
possible against a real, non-deterministic LLM.
"""

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import pytest

from agent.core import ResearchAgent
from tools.tool_registry import build_default_registry


# ---------------------------------------------------------------------- #
# Fakes -- these stand in for a real groq ChatCompletion response without
# making any network calls.
# ---------------------------------------------------------------------- #
@dataclass
class FakeFunction:
    name: str
    arguments: str  # JSON string, matching the real SDK's shape


@dataclass
class FakeToolCall:
    id: str
    function: FakeFunction


@dataclass
class FakeMessage:
    content: Optional[str]
    tool_calls: Optional[List[FakeToolCall]] = None


@dataclass
class FakeChoice:
    message: FakeMessage


@dataclass
class FakeUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0


@dataclass
class FakeChatCompletion:
    choices: List[FakeChoice]
    usage: FakeUsage = field(default_factory=FakeUsage)


def make_text_response(text: str) -> FakeChatCompletion:
    return FakeChatCompletion(choices=[FakeChoice(message=FakeMessage(content=text))])


def make_tool_call_response(call_id: str, name: str, args: Dict[str, Any]) -> FakeChatCompletion:
    return FakeChatCompletion(
        choices=[
            FakeChoice(
                message=FakeMessage(
                    content=None,
                    tool_calls=[
                        FakeToolCall(id=call_id, function=FakeFunction(name=name, arguments=json.dumps(args)))
                    ],
                )
            )
        ]
    )


class FakeLLMClient:
    """Returns pre-scripted fake responses in sequence, regardless of what
    messages/tools it's called with."""

    def __init__(self, scripted_responses: List[FakeChatCompletion]) -> None:
        self._responses = list(scripted_responses)
        self.calls_made: List[Dict[str, Any]] = []

    def call(self, messages, system=None, tool_schemas=None, max_tokens=4096, model=None):
        self.calls_made.append(
            {"messages": messages, "system": system, "tool_schemas": tool_schemas}
        )
        if not self._responses:
            raise AssertionError("FakeLLMClient ran out of scripted responses")
        return self._responses.pop(0)

    def get_usage_summary(self) -> Dict[str, Any]:
        return {"total_calls": 0, "total_input_tokens": 0, "total_output_tokens": 0, "total_tokens": 0}


@pytest.fixture
def default_registry():
    return build_default_registry()


# ---------------------------------------------------------------------- #
# Challenge 1 style test: Microsoft company profile, one tool call,
# no re-planning needed.
# ---------------------------------------------------------------------- #
def test_agent_run_simple_company_profile_query(default_registry):
    scripted = [
        # 1. Planning call
        make_text_response(json.dumps(["Get Microsoft's company profile"])),
        # 2. Execution call #1 -- LLM requests the company_profile tool
        make_tool_call_response("call_abc123", "company_profile", {"ticker": "MSFT"}),
        # 3. Execution call #2 -- LLM has the tool result, gives final answer
        make_text_response(
            "Microsoft Corporation (MSFT) is a Technology sector company "
            "in the Software industry, with a market cap of roughly "
            "$3.1 trillion."
        ),
        # 4. Re-plan checkpoint -- LLM says research is complete
        make_text_response(
            json.dumps(
                {"needs_more_research": False, "reason": "Profile is complete.", "additional_tasks": []}
            )
        ),
    ]
    fake_llm = FakeLLMClient(scripted)

    agent = ResearchAgent(llm_client=fake_llm, tool_registry=default_registry)
    result = agent.run(
        "Create a comprehensive profile of Microsoft Corporation",
        max_tool_calls=20,
        time_budget_seconds=300,
        max_replan_cycles=3,
    )

    # --- Plan ---
    assert result.plan == ["Get Microsoft's company profile"]

    # --- Tool execution actually happened via the REAL tool registry ---
    assert result.tool_calls_made == 1
    tool_call_steps = [s for s in result.trace if s.step_type == "tool_call"]
    assert len(tool_call_steps) == 1
    assert tool_call_steps[0].detail["tool"] == "company_profile"
    assert tool_call_steps[0].detail["result"]["success"] is True
    assert tool_call_steps[0].detail["result"]["data"]["name"] == "Microsoft Corporation"

    # --- Final answer ---
    assert "Microsoft" in result.final_answer
    assert result.termination_reason == "no_further_tool_calls"

    # --- Re-planning ran exactly once and correctly decided to stop ---
    assert result.replan_cycles_used == 1
    replan_steps = [s for s in result.trace if s.step_type == "replan_decision"]
    assert len(replan_steps) == 1
    assert replan_steps[0].detail["needs_more_research"] is False

    # --- Trace is a complete, ordered record of the run ---
    step_types = [s.step_type for s in result.trace]
    assert step_types[0] == "plan"
    assert step_types[-1] == "final_answer"
    assert "tool_call" in step_types
    assert "replan_decision" in step_types

    # --- The conversation correctly threaded the tool_call_id through to
    # the tool result message (required by Groq/OpenAI's protocol) ---
    last_call_messages = fake_llm.calls_made[-1]["messages"]
    tool_messages = [m for m in last_call_messages if m.get("role") == "tool"]
    assert len(tool_messages) == 1
    assert tool_messages[0]["tool_call_id"] == "call_abc123"


def test_agent_falls_back_to_single_task_plan_on_unparseable_plan(default_registry):
    """If the planning call doesn't return valid JSON, the agent should
    fall back to a one-item plan (the original query) rather than crash."""
    scripted = [
        make_text_response("Sure, I'll get started on that right away!"),  # not JSON
        make_text_response("Here is a brief summary."),
        make_text_response(
            json.dumps({"needs_more_research": False, "reason": "done", "additional_tasks": []})
        ),
    ]
    fake_llm = FakeLLMClient(scripted)
    agent = ResearchAgent(llm_client=fake_llm, tool_registry=default_registry)

    result = agent.run("What is Tesla's risk profile?", max_replan_cycles=3)

    assert result.plan == ["What is Tesla's risk profile?"]
    assert result.final_answer == "Here is a brief summary."


def test_agent_respects_max_tool_calls_limit(default_registry):
    """The agent must stop calling tools once the limit is hit, even if
    the (fake, uncooperative) LLM keeps requesting more."""
    scripted = [
        make_text_response(json.dumps(["Look things up repeatedly"])),
    ]
    for i in range(5):
        scripted.append(make_tool_call_response(f"call_{i}", "company_profile", {"ticker": "MSFT"}))

    fake_llm = FakeLLMClient(scripted)
    agent = ResearchAgent(llm_client=fake_llm, tool_registry=default_registry)

    result = agent.run("Loop forever", max_tool_calls=2, time_budget_seconds=300, max_replan_cycles=0)

    assert result.tool_calls_made <= 2
    assert result.termination_reason == "max_tool_calls_reached"


# Note: context-budget management tests (truncation/trimming) moved to
# tests/test_memory.py as of Day 6 -- that logic now lives in
# memory/context_manager.py rather than as free functions in agent/core.py.
