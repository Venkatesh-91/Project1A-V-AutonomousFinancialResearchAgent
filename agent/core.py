"""
core.py

The agent's cognitive loop: hybrid Plan-and-Execute with bounded
re-planning, as chosen and justified in architecture_specification.md
Section 2.

    query -> plan -> [execute tool calls until the model stops requesting
    them] -> bounded re-planning checkpoint (max N cycles) -> final answer

This module owns the control flow only. It does NOT know how to talk to
Groq (that's agent/llm_client.py) or how to interpret a raw response
(that's agent/parser.py) or what a tool does (that's tools/tool_registry.py).
That separation is what makes each piece independently testable -- see
tests/test_agent.py, which drives ResearchAgent with fake LLM and tool
implementations and never touches the network.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from agent.llm_client import LLMClient
from agent.parser import ParsedResponse, parse_json_from_text, parse_response
from agent.prompts import build_planning_prompt, build_replan_prompt, build_system_prompt
from config.settings import settings
from tools.tool_registry import ToolRegistry

logger = logging.getLogger(__name__)

# Groq's free tier enforces a hard tokens-PER-MINUTE cap that is shared
# across input + output (as of this writing, 8,000 TPM for the gpt-oss
# models this project defaults to -- see agent/llm_client.py's model
# comment). A multi-tool-call research conversation can easily exceed this
# just from accumulated tool-result history, well before hitting any
# request-count limit. Two safeguards address this:
#   1. Individual tool results are truncated before being added to the
#      conversation (a single large web-search or filing-text result
#      shouldn't dominate the budget).
#   2. The full conversation is checked against a token-count ESTIMATE
#      before every call and trimmed (oldest tool-turns first) if it's
#      grown too large -- a lightweight, in-memory precursor to the real
#      context-window management Day 6's memory system implements
#      properly (see architecture_specification.md Section 4.1).
MAX_TOOL_RESULT_CHARS = 800
MAX_CONVERSATION_CHARS_ESTIMATE = 20000  # ~5,000 tokens at a ~4 chars/token rough estimate


def _truncate_tool_result_content(content: str, max_chars: int = MAX_TOOL_RESULT_CHARS) -> str:
    """Cap a single tool result's serialized size before it enters conversation history."""
    if len(content) <= max_chars:
        return content
    return content[:max_chars] + f"... [truncated {len(content) - max_chars} chars]"


def _estimate_tokens(conversation: List[Dict[str, Any]]) -> int:
    """Rough token estimate (chars / 4) -- good enough to trigger trimming
    proactively; not meant to be exact."""
    return len(json.dumps(conversation)) // 4


def _trim_conversation_if_needed(
    conversation: List[Dict[str, Any]], max_chars: int = MAX_CONVERSATION_CHARS_ESTIMATE
) -> None:
    """
    If the conversation has grown past the size budget, drop the OLDEST
    tool-call/tool-result turn pairs (keeping the original query intact at
    the start, and the most recent turns intact at the end) until it fits.
    Mutates `conversation` in place. This is a blunt but effective
    safeguard -- Day 6 replaces the dropped detail with real long-term
    memory (vector-retrievable) rather than simply discarding it.
    """
    if len(json.dumps(conversation)) <= max_chars:
        return

    # Never trim the very first message (the original query + plan) or the
    # most recent few turns (the model needs recent context to keep
    # reasoning coherently). Drop from just after the first message inward.
    MIN_KEPT_RECENT_TURNS = 6
    dropped_any = False

    while len(json.dumps(conversation)) > max_chars and len(conversation) > MIN_KEPT_RECENT_TURNS + 1:
        # index 1 is the oldest droppable turn (index 0 is the original query)
        del conversation[1]
        dropped_any = True

    if dropped_any:
        conversation.insert(
            1,
            {
                "role": "user",
                "content": (
                    "[Note: some earlier tool results were trimmed from this "
                    "conversation to stay within the model's context budget. "
                    "Continue researching with what remains.]"
                ),
            },
        )
        logger.info(
            "Trimmed conversation history to stay under the token budget "
            "(estimated ~%d tokens after trim).",
            _estimate_tokens(conversation),
        )


@dataclass
class TraceStep:
    """
    One entry in the agent's reasoning trace. Every run produces a list of
    these -- this is the raw material for Day 6's episodic memory (SQLite
    persistence) and Day 14's Trace Gallery deliverable. Kept as a plain
    dataclass (not tied to any storage backend) so it's easy to serialize
    to whatever Day 6 ends up using.
    """

    step_type: str  # "plan" | "reasoning" | "tool_call" | "replan_decision" | "final_answer"
    detail: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {"step_type": self.step_type, "detail": self.detail}


@dataclass
class AgentRunResult:
    """Everything produced by one call to ResearchAgent.run()."""

    query: str
    plan: List[str]
    final_answer: Optional[str]
    trace: List[TraceStep]
    tool_calls_made: int
    replan_cycles_used: int
    termination_reason: str
    token_usage: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "query": self.query,
            "plan": self.plan,
            "final_answer": self.final_answer,
            "trace": [step.to_dict() for step in self.trace],
            "tool_calls_made": self.tool_calls_made,
            "replan_cycles_used": self.replan_cycles_used,
            "termination_reason": self.termination_reason,
            "token_usage": self.token_usage,
        }


class ResearchAgent:
    """
    The main agent. Construct once with an LLMClient and a ToolRegistry,
    then call .run(query) for each research task.
    """

    def __init__(self, llm_client: LLMClient, tool_registry: ToolRegistry) -> None:
        self.llm_client = llm_client
        self.tool_registry = tool_registry
        self.system_prompt = build_system_prompt()

    # ------------------------------------------------------------------ #
    # Step 1: Planning
    # ------------------------------------------------------------------ #
    def _make_plan(self, query: str) -> List[str]:
        """
        Ask the LLM to decompose the query into sub-tasks before any tool
        calls are made. Falls back to a single-item plan (the original
        query, verbatim) if the LLM's response can't be parsed as a JSON
        list -- a formatting slip here should degrade gracefully, not
        crash the whole run (per the graceful-degradation principle in
        architecture_specification.md Section A4).
        """
        response = self.llm_client.call(
            messages=[{"role": "user", "content": build_planning_prompt(query)}],
            system=self.system_prompt,
        )
        parsed = parse_response(response)
        plan = parse_json_from_text(parsed.text)

        if isinstance(plan, list) and all(isinstance(item, str) for item in plan) and plan:
            return plan

        logger.warning(
            "Could not parse a task list from the planning response; "
            "falling back to a single-task plan. Raw text: %r",
            parsed.text,
        )
        return [query]

    # ------------------------------------------------------------------ #
    # Step 2: Execution (tool-calling loop)
    # ------------------------------------------------------------------ #
    def _execute(
        self,
        conversation: List[Dict[str, Any]],
        trace: List[TraceStep],
        tool_calls_made: int,
        max_tool_calls: int,
        deadline: float,
    ) -> tuple[ParsedResponse, int, str]:
        """
        Run the tool-calling loop: call the LLM, dispatch any tool calls it
        requests, feed the results back, repeat -- until the LLM responds
        with no further tool calls, or a limit is hit.

        Returns (final_parsed_response, updated_tool_calls_made, stop_reason).
        Appends every turn (assistant + tool results) to `conversation` as
        it goes, so the caller always sees the full history reflected.
        """
        tool_defs = self.tool_registry.get_tool_definitions()

        while True:
            if tool_calls_made >= max_tool_calls:
                return (
                    ParsedResponse(text=None),
                    tool_calls_made,
                    "max_tool_calls_reached",
                )
            if time.monotonic() >= deadline:
                return ParsedResponse(text=None), tool_calls_made, "time_budget_exceeded"

            _trim_conversation_if_needed(conversation)

            response = self.llm_client.call(
                messages=conversation,
                system=self.system_prompt,
                tool_schemas=tool_defs,
            )
            parsed = parse_response(response)

            trace.append(TraceStep("reasoning", {"text": parsed.text}))

            # Reconstructing the assistant message from parsed fields is
            # the standard, correct pattern for OpenAI-compatible tool use
            # -- no opaque signature to preserve here (unlike the Gemini
            # version of this project).
            conversation.append(parsed.to_assistant_message())

            if not parsed.has_tool_calls:
                return parsed, tool_calls_made, "no_further_tool_calls"

            for call in parsed.tool_calls:
                if tool_calls_made >= max_tool_calls:
                    break
                result = self.tool_registry.dispatch(call.name, **call.args)
                tool_calls_made += 1

                trace.append(
                    TraceStep(
                        "tool_call",
                        {"tool": call.name, "args": call.args, "result": result.to_dict()},
                    )
                )

                response_payload = (
                    {"data": result.data, "source": result.source_name}
                    if result.success
                    else {"error": result.error}
                )
                # Groq/OpenAI requires one "tool" role message per tool
                # call, each referencing that call's own id. Truncate large
                # results (a big web-search or filing-text payload) before
                # it enters conversation history -- see the module-level
                # note on Groq's tight free-tier TPM budget.
                conversation.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.call_id,
                        "content": _truncate_tool_result_content(json.dumps(response_payload)),
                    }
                )

    # ------------------------------------------------------------------ #
    # Step 3: Bounded re-planning checkpoint
    # ------------------------------------------------------------------ #
    def _replan_checkpoint(self, conversation: List[Dict[str, Any]], trace: List[TraceStep]) -> bool:
        """
        Ask the LLM whether more research is needed. Returns True if the
        loop should continue (more sub-tasks were added to the
        conversation), False if research is judged complete.
        """
        _trim_conversation_if_needed(conversation)

        response = self.llm_client.call(
            messages=conversation + [{"role": "user", "content": build_replan_prompt()}],
            system=self.system_prompt,
        )
        parsed = parse_response(response)
        decision = parse_json_from_text(parsed.text) or {}

        needs_more = bool(decision.get("needs_more_research", False))
        additional_tasks = decision.get("additional_tasks", []) or []

        trace.append(
            TraceStep(
                "replan_decision",
                {
                    "needs_more_research": needs_more,
                    "reason": decision.get("reason", ""),
                    "additional_tasks": additional_tasks,
                },
            )
        )

        if needs_more and additional_tasks:
            task_text = "\n".join(f"- {t}" for t in additional_tasks)
            conversation.append(
                {
                    "role": "user",
                    "content": f"Continue researching. Additional tasks identified:\n{task_text}",
                }
            )
            return True

        return False

    # ------------------------------------------------------------------ #
    # Public entry point
    # ------------------------------------------------------------------ #
    def run(
        self,
        query: str,
        max_tool_calls: Optional[int] = None,
        time_budget_seconds: Optional[int] = None,
        max_replan_cycles: Optional[int] = None,
    ) -> AgentRunResult:
        """
        Run the full cognitive loop for one research query. See
        architecture_specification.md Section 3 for the full design this
        implements.
        """
        max_tool_calls = max_tool_calls or settings.max_tool_calls_per_run
        time_budget_seconds = time_budget_seconds or settings.research_time_budget_seconds
        max_replan_cycles = (
            max_replan_cycles if max_replan_cycles is not None else settings.max_replan_cycles
        )

        deadline = time.monotonic() + time_budget_seconds
        trace: List[TraceStep] = []
        tool_calls_made = 0

        # --- Step 1: Plan ------------------------------------------------ #
        plan = self._make_plan(query)
        trace.append(TraceStep("plan", {"tasks": plan}))

        plan_text = "\n".join(f"- {task}" for task in plan)
        conversation: List[Dict[str, Any]] = [
            {
                "role": "user",
                "content": (
                    f'Research query: "{query}"\n\n'
                    f"Planned sub-tasks:\n{plan_text}\n\n"
                    f"Begin researching using the available tools."
                ),
            }
        ]

        # --- Step 2: Execute ---------------------------------------------- #
        final_parsed, tool_calls_made, stop_reason = self._execute(
            conversation, trace, tool_calls_made, max_tool_calls, deadline
        )

        # --- Step 3: Bounded re-planning ------------------------------- #
        # Note: _execute() already appends the assistant's own turn to
        # `conversation` before returning -- no need to append it again here.
        replan_cycles_used = 0
        if stop_reason == "no_further_tool_calls":
            while replan_cycles_used < max_replan_cycles:
                if tool_calls_made >= max_tool_calls or time.monotonic() >= deadline:
                    break
                should_continue = self._replan_checkpoint(conversation, trace)
                replan_cycles_used += 1
                if not should_continue:
                    break
                final_parsed, tool_calls_made, stop_reason = self._execute(
                    conversation, trace, tool_calls_made, max_tool_calls, deadline
                )

        # --- Final answer -------------------------------------------------- #
        final_answer = final_parsed.text
        if final_answer is None:
            # The loop terminated on a limit rather than a natural stop --
            # ask once more, with tools disabled, to force a text summary
            # of whatever was gathered rather than returning nothing. This
            # is the "graceful degradation" path: partial results, clearly
            # produced, beats no output at all.
            wrap_up = self.llm_client.call(
                messages=conversation
                + [
                    {
                        "role": "user",
                        "content": (
                            "Research must stop here due to a resource limit. "
                            "Summarize your findings so far as clearly as "
                            "possible, and explicitly note what could not be "
                            "completed."
                        ),
                    }
                ],
                system=self.system_prompt,
            )
            final_answer = parse_response(wrap_up).text

        trace.append(TraceStep("final_answer", {"text": final_answer}))

        return AgentRunResult(
            query=query,
            plan=plan,
            final_answer=final_answer,
            trace=trace,
            tool_calls_made=tool_calls_made,
            replan_cycles_used=replan_cycles_used,
            termination_reason=stop_reason,
            token_usage=self.llm_client.get_usage_summary(),
        )
