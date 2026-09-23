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
from memory.context_manager import ContextManager
from memory.episodic import EpisodicMemory
from tools.tool_registry import ToolRegistry

logger = logging.getLogger(__name__)


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

    `episodic_memory`, if provided, receives a full log of every run
    (plan, every tool call, final answer) after it completes -- see
    memory/episodic.py. This is optional so tests and quick scripts can
    construct a ResearchAgent without standing up a SQLite file, but
    scripts/run_challenge.py always provides one so real runs are logged.
    """

    def __init__(
        self,
        llm_client: LLMClient,
        tool_registry: ToolRegistry,
        episodic_memory: Optional[EpisodicMemory] = None,
        context_manager: Optional[ContextManager] = None,
    ) -> None:
        self.llm_client = llm_client
        self.tool_registry = tool_registry
        self.episodic_memory = episodic_memory
        self.context_manager = context_manager or ContextManager()
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

            self.context_manager.trim_conversation_if_needed(conversation)

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
                        "content": self.context_manager.truncate_tool_result(json.dumps(response_payload)),
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
        self.context_manager.trim_conversation_if_needed(conversation)

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

        result = AgentRunResult(
            query=query,
            plan=plan,
            final_answer=final_answer,
            trace=trace,
            tool_calls_made=tool_calls_made,
            replan_cycles_used=replan_cycles_used,
            termination_reason=stop_reason,
            token_usage=self.llm_client.get_usage_summary(),
        )

        # Radical-transparency logging (architecture_specification.md
        # Section 3.2): persist the full run, every trace step included,
        # to episodic memory -- independent of whatever the LLM itself
        # chose to store via the vector_db_store tool. A failure here
        # should never take down an otherwise-successful research run.
        if self.episodic_memory is not None:
            try:
                self.episodic_memory.log_run(result)
            except Exception:  # noqa: BLE001
                logger.exception("Failed to log run to episodic memory (run result is unaffected).")

        return result
