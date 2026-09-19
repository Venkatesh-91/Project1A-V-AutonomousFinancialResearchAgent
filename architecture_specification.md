# Agent Architecture Specification
## Autonomous Financial Research Agent

**Document version:** 1.0
**Date:** Day 1 Deliverable
**Author:** [YourName]

---

## Table of Contents
1. Executive Summary
2. Agent Pattern Selection and Rationale
3. Cognitive Loop Design
4. Memory Architecture
5. Tool Registry Design
6. Specialist Mode Decomposition
7. Source Reliability and Provenance Model
8. Cross-Jurisdictional and Multi-Fiscal-Year Handling
9. Event-Driven Extension
10. Evaluation Framework Overview
11. Risks and Open Questions

---

## 1. Executive Summary

This document specifies the cognitive architecture of an autonomous AI agent designed to replicate the research workflow of a junior financial analyst. The agent receives a natural-language research query (or an autonomously detected market event), independently formulates a multi-step research plan, executes that plan using a registry of 10+ external tools spanning regulatory filings, market data, transcripts, and news, resolves conflicting information across sources, and synthesizes findings into a structured investment research report that concludes with testable, evidence-linked hypotheses rather than a bare list of facts.

The architecture is built around five core principles established during the design phase:

- **Traceability first.** Every fact the agent states must be attributable to a specific source, with a timestamp and a reliability tier, from the moment it enters the system — not bolted on at the reporting stage.
- **Specialist decomposition.** A single monolithic "research everything" prompt underperforms three narrower specialist reasoning modes (financial, sentiment, risk) coordinated by a shared planner.
- **Explicit, documented principles over implicit prompting.** Behavioral constraints (source preference rules, hallucination guards, disambiguation rules) are stored as a versioned, machine-readable document the agent consults, not just informal prompt text.
- **Extensibility across jurisdictions.** The tool registry and data model are designed from day one to support non-US filing regimes (starting with India's MCA/SEBI/NSE/BSE ecosystem) rather than assuming US GAAP and EDGAR as universal defaults.
- **Reliability over speed, speed over completeness.** Every component has an explicit fallback; a degraded-but-honest answer is preferred to a fast but unverified one, but among two correct paths, the faster one is chosen.

---

## 2. Agent Pattern Selection and Rationale

### 2.1 Options considered

| Pattern | Description | Fit for this project |
|---|---|---|
| **Pure ReAct** | Interleaved Thought → Action → Observation, one step at a time | Simple to reason about, but strictly serial — cannot parallelize independent data pulls, and reasoning traces get long and expensive for multi-source research |
| **Pure Plan-and-Execute** | Upfront full plan, then execute steps (optionally in parallel), no re-reasoning mid-execution | Enables parallel tool calls, but brittle if the initial plan is wrong and new information (e.g., a filing reveals an unexpected subsidiary) isn't discovered until late |
| **Hybrid: Plan-and-Execute with bounded ReAct re-planning** | Upfront plan for parallelizable sub-tasks, but a re-planning checkpoint after each batch of results feeds new information back into the plan | Combines throughput of upfront planning with the adaptivity of ReAct, at the cost of added orchestration complexity |

### 2.2 Decision

**Selected pattern: Hybrid Plan-and-Execute with bounded re-planning checkpoints.**

**Rationale:**

1. Financial research has a large proportion of independent, parallelizable sub-tasks (fetch filing, fetch transcript, fetch news, fetch price history) that a strict ReAct loop would force into unnecessary serial execution, directly working against the "speed matters" requirement (event analysis in minutes, not hours).
2. Pure Plan-and-Execute risks committing to an incomplete plan — for example, a company's most recent 10-K might reveal a material acquisition that changes what should be researched next (a subsidiary's financials, a new segment). A single upfront plan with no re-planning would miss this.
3. A bounded re-planning checkpoint (triggered after each "wave" of parallel sub-tasks completes, capped at a maximum of 3 re-planning cycles per research run) gives the adaptivity benefit of ReAct without its serialization cost or its unbounded reasoning-loop risk (an agent that can re-plan indefinitely can loop without terminating).

### 2.3 Termination conditions

The agent terminates a research run when any of the following holds:
- The re-planning checkpoint returns no new sub-tasks (plan is judged complete).
- The maximum re-planning cycle count (3) is reached.
- A wall-clock time budget (configurable, default 5 minutes) is exceeded — at which point the agent synthesizes a report from whatever has been gathered and explicitly flags incompleteness.

---

## 3. Cognitive Loop Design

```
 ┌─────────────────────────────────────────────────────────────────┐
 │                         COGNITIVE LOOP                           │
 └─────────────────────────────────────────────────────────────────┘

 1. INTAKE
    Query (user text) OR Event (autonomous trigger from event detector)
              │
              ▼
 2. ENTITY RESOLUTION
    NER extracts company/ticker mentions → disambiguated to canonical
    entity (ticker + CIK/jurisdiction ID) → jurisdiction determined
    (US / India / other)
              │
              ▼
 3. PLANNING
    Planner LLM (loaded with research_principles.md) decomposes the
    query into sub-tasks, each tagged with:
       - specialist mode (financial | sentiment | risk)
       - required tool category
       - jurisdiction
              │
              ▼
 4. EXECUTION (parallel wave)
    Executor dispatches sub-tasks concurrently to the Tool Registry.
    Each tool call follows: primary tool → on failure, fallback tool
    → on failure, log as unresolved gap (never silently drop).
    Raw results pass through NER/disambiguation + OCR-fallback
    preprocessing before being written to Short-Term Memory.
              │
              ▼
 5. RE-PLANNING CHECKPOINT (bounded, max 3 cycles)
    Planner reviews Short-Term Memory contents against the original
    plan: are there gaps, contradictions, or newly discovered facts
    that warrant new sub-tasks? If yes → return to step 4 with new
    sub-tasks. If no, or cycle limit reached → proceed.
              │
              ▼
 6. SYNTHESIS
    Synthesis Engine reads all Short-Term Memory facts (each tagged
    with source, tier, timestamp, mode) plus relevant Long-Term
    Memory (vector-retrieved prior context on this entity):
       - resolves conflicts by tier + recency
       - merges complementary facets per specialist mode
       - normalizes fiscal periods and currency across jurisdictions
       - drafts evidence-linked hypotheses
              │
              ▼
 7. REPORT GENERATION
    Structured report assembled: Executive Summary, Financial
    Analysis, Sentiment Analysis, Risk Assessment, Source Appendix
    (full provenance), Hypotheses section.
              │
              ▼
 8. EPISODIC LOGGING
    Full run (plan, tool calls, re-planning decisions, synthesis
    decisions, final report) written to episodic memory (SQLite)
    for future recall and for evaluation-harness replay.
```

### 3.1 Why a checkpoint-based loop rather than continuous reasoning

A continuously re-planning loop (reasoning after every single tool call) more closely resembles textbook ReAct, but was rejected for this design because: (a) it multiplies LLM calls linearly with tool calls, which is costly and slow, directly working against the speed objective; (b) most individual tool results do not, on their own, warrant a change of plan — batching re-planning after a full wave gives the planner more context per decision, producing better-reasoned plan changes with fewer total LLM calls.

---

## 4. Memory Architecture

The agent uses three memory layers with distinct lifetimes, storage backends, and purposes.

### 4.1 Short-Term Memory (per-run, in-process)

- **Storage:** in-memory Python structure (dict/dataclass), scoped to a single research run.
- **Contents:** raw and preprocessed tool results, entity resolutions, intermediate reasoning notes, the evolving plan.
- **Lifetime:** cleared at the end of a run, after being persisted to Episodic Memory.
- **Access pattern:** read/write by the Executor and Synthesis Engine during a single run only.

### 4.2 Episodic Memory (cross-run, structured log)

- **Storage:** SQLite database, one row per logged event.
- **Schema (core fields):** `run_id, timestamp, step_type (tool_call | reasoning | synthesis_decision | replan_decision), mode, detail (JSON), confidence, source_tier`.
- **Purpose:** (1) radical transparency — every run is fully replayable for human review or automated evaluation; (2) recall of prior runs on the same entity ("have we researched this company before, and what did we conclude?"); (3) the evaluation harness reads episodic logs directly to score process-quality metrics (tool efficiency, fallback trigger rate, re-planning frequency, latency).
- **Lifetime:** persistent, append-only.

### 4.3 Long-Term Memory (vector database)

- **Storage:** Chroma (local, in-process, no server dependency — appropriate for the project timeline).
- **Contents:** embedded chunks of previously fetched filings, transcripts, and news articles, each chunk tagged with metadata: `entity_id (canonical ticker/CIK), jurisdiction, source_type, source_tier, publication_date, fiscal_period`.
- **Purpose:** (1) avoid redundant API calls for previously fetched documents (cache-by-semantic-content); (2) allow the Synthesis Engine to retrieve relevant historical context (e.g., "how did this company's management discuss supply chain issues last quarter?") without re-fetching.
- **Retrieval:** metadata-filtered similarity search — always filtered by `entity_id` first, then ranked by embedding similarity, so retrieval never crosses entities incorrectly (this is where accurate entity disambiguation from step 2 of the cognitive loop becomes load-bearing for memory correctness, not just for the current run).

### 4.4 Interaction between layers

During a run: the Executor writes to Short-Term Memory; before making a new tool call, it first checks Long-Term Memory for a sufficiently recent, relevant cached result (avoiding a redundant fetch). At the end of a run, Short-Term Memory is flushed into both Episodic Memory (as a full run log) and Long-Term Memory (new document chunks embedded and stored). Episodic Memory is never queried mid-run for research content — it exists for audit and evaluation, not as a research data source — which keeps the run's reasoning grounded in current retrieval rather than potentially stale past conclusions.

---

## 5. Tool Registry Design

### 5.1 Uniform tool interface

Every tool, regardless of jurisdiction or category, implements the same interface:

```
run(input: ToolInput) -> ToolResult {
    data: Any,
    source_name: str,
    source_url_or_id: str,
    retrieved_at: datetime,
    source_tier: int,       # 1 (highest trust) .. 5 (lowest)
    success: bool,
    fallback_used: bool
}
```

This uniformity is what allows the Executor, Synthesis Engine, and evaluation harness to treat all ten-plus tools identically rather than writing bespoke handling per tool.

### 5.2 Registry structure (jurisdiction-namespaced)

```
TOOL_REGISTRY = {
  "us": {
      "filings":      SECEdgarTool()          -> fallback: EDGARCompanyFactsTool()
      "financials":   XBRLCompanyFactsTool()  -> fallback: FinancialModelingPrepTool()
      "market_data":  AlphaVantageTool()      -> fallback: TwelveDataTool()
      "transcripts":  FMPTranscriptTool()     -> fallback: WebSearchScrapeTool()
      "insider":      EDGARForm4Tool()
  },
  "in": {
      "filings":      MCAFilingTool()         -> fallback: WebSearchScrapeTool()
      "regulatory":   SEBICircularTool()      -> fallback: WebSearchScrapeTool()
      "market_data":  NSEBSEDataTool()
  },
  "shared": {
      "news":          NewsAPITool()          -> fallback: WebSearchTool()
      "web_search":    TavilySearchTool()     -> fallback: SerpAPITool()
      "sentiment":     FinBERTSentimentTool() -> fallback: KeywordSentimentTool()
      "peers":         FMPPeersTool()         -> fallback: LLMDerivedPeerListTool()
      "ocr":           TesseractOCRTool()     # invoked as fallback, not primary
  }
}
```

### 5.3 Fallback chain policy

Every tool category has at least one fallback. A fallback is only invoked after the primary tool either raises an error, times out (default 10s per call), or returns empty/near-empty content. Fallback invocation is always logged to Episodic Memory with `fallback_used: true`, which becomes a first-class reliability metric in the evaluation harness rather than an invisible implementation detail.

### 5.4 Extensibility for new jurisdictions

Adding a new jurisdiction (e.g., UK, EU) requires: (1) a new top-level key in `TOOL_REGISTRY`, (2) implementations of at minimum a `filings` and `market_data` tool conforming to the uniform interface, (3) an entry in the fiscal-year-convention lookup table (Section 8). No changes to the Planner, Executor, or Synthesis Engine are required — jurisdiction routing is entirely data-driven off the entity resolution step.

---

## 6. Specialist Mode Decomposition

Rather than one generic research prompt, sub-tasks are tagged with one of three specialist modes, each with its own system prompt and tool affinity:

| Mode | Focus | Primary tools | Output feeds |
|---|---|---|---|
| **Financial Analyst** | Ratios, YoY/QoQ trends, balance sheet and cash flow health | filings, financials, market_data | Report's Financial Analysis section |
| **Sentiment Analyst** | Management tone, market reaction, analyst rating shifts | transcripts, news, sentiment | Report's Sentiment Analysis section |
| **Risk Analyst** | Insider activity, litigation/going-concern language, covenant risk | insider, filings (risk factors), news | Report's Risk Assessment section |

All three modes share the same underlying LLM and tool registry — decomposition is at the prompt and task-routing level, not a multi-model or multi-agent-process architecture. This keeps infrastructure complexity low while still realizing the quality benefit of specialist framing (per C2.2 Lesson 2 and the specialist-decomposition lesson).

---

## 7. Source Reliability and Provenance Model

### 7.1 Source tier table

| Tier | Source type | Example |
|---|---|---|
| 1 | Primary regulatory filing | 10-K, 10-Q, MCA annual filing |
| 2 | Earnings call transcript | Management commentary |
| 3 | Analyst estimate / peer data | FMP analyst consensus |
| 4 | Financial news | Reuters, Bloomberg news article |
| 5 | General web search result | Unverified secondary source |

### 7.2 Conflict resolution rule

Given two conflicting facts about the same entity/period: prefer the lower tier number (higher trust) **unless** it is stale relative to the higher-tier-number source (i.e., a tier-1 filing that predates a tier-4 news article reporting a subsequent restatement) — in which case the discrepancy is explicitly flagged in the report rather than silently resolved.

### 7.3 Provenance schema

Every fact object carried through the system has the shape:

```
Fact {
  value: Any,
  entity_id: str,
  fiscal_period: {fiscal_year, quarter, period_start, period_end},
  source_name: str,
  source_url_or_id: str,
  retrieved_at: datetime,
  source_tier: int,
  mode: "financial" | "sentiment" | "risk"
}
```

No claim reaches the final report without a populated `Fact` object behind it. This directly supports the evaluation harness's citation-validity metric and the "radical transparency" principle.

---

## 8. Cross-Jurisdictional and Multi-Fiscal-Year Handling

- Every fiscal period is stored as explicit calendar `period_start`/`period_end` dates, never as a bare label like "Q4," to avoid misalignment between jurisdictions with different fiscal-year conventions (Jan–Dec, Apr–Mar, Oct–Sep).
- A lookup table maps each known entity to its fiscal-year-end convention; unknown entities default to calendar year and are flagged as an assumption in the report.
- Cross-jurisdictional comparisons (e.g., a US and an Indian company) trigger a normalization step in the Synthesis Engine: periods are aligned by calendar date range, and an explicit note is added if accounting standards differ (GAAP vs. Ind AS) that could affect comparability.

---

## 9. Event-Driven Extension

In addition to query-driven intake, an **Event Detector** component polls (on a configurable interval) for new filings or news on a set of watched entities. When a new event is detected, it is passed into the cognitive loop at step 1 in place of a user query, with the event content used to seed initial entity resolution. This shares the entire downstream pipeline (steps 2–8) with query-driven research — no separate architecture is needed for event-driven mode, only a new intake source.

---

## 10. Evaluation Framework Overview

The evaluation harness scores each research run across four metric groups (5 metrics each, 20 total minimum):

1. **Correctness** — factual accuracy vs. ground truth, numeric precision, citation validity, hallucination rate, period/date correctness.
2. **Completeness** — sub-task coverage, required report sections present, source-type diversity, conflicting-data flagged, cross-jurisdictional handling (where applicable).
3. **Process quality** — plan coherence, tool-call efficiency, fallback-trigger rate, re-planning frequency, end-to-end latency.
4. **Output quality** — readability, structural adherence, actionable-insight count, hypothesis quality (evidence-linked, falsifiable), appropriate hedging.

Metrics are computed by replaying the Episodic Memory log for a run plus scoring the final report text, across 8 progressively harder test challenges (from a single well-covered large-cap US company through to a cross-jurisdictional, data-sparse small-cap comparison).

---

## 11. Risks and Open Questions

- **India-specific tool coverage** (MCA filing retrieval, SEBI circular search) lacks clean free APIs; MVP scope may need to rely on web-search-based fallbacks for these rather than dedicated scrapers within the project timeline.
- **Re-planning cycle cap (3)** is a heuristic, not derived from data — may need tuning once real runs are observed to be terminating too early or too late.
- **OCR-derived facts** are expected to have materially lower accuracy than native text extraction; their tier weighting in the Synthesis Engine may need adjustment after initial testing.
- **Wall-clock time budget (5 minutes default)** trades off against completeness on data-sparse entities; this may need to be per-challenge-difficulty configurable rather than a single global constant.