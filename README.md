# Autonomous Financial Research Agent

An AI agent that replicates a junior financial analyst's research
workflow: receiving a query, forming a research plan, gathering data from
SEC EDGAR filings, financial data APIs, earnings transcripts, and news,
resolving conflicts across sources, and synthesizing a structured
investment research report — with a documented architecture, a 12-tool
registry, three-layer memory, and a 20+ metric evaluation framework.

Built as a 15-day project. See `docs/architecture_specification_final.md`
for the full design rationale.

## Setup

**1. Clone and enter the repo**
```bash
git clone https://github.com/Venkatesh-91/Project1A-V-AutonomousFinancialResearchAgent.git
cd Project1A-V-AutonomousFinancialResearchAgent
```

**2. Create a virtual environment**
```bash
python -m venv .venv
# Windows:
.venv\Scripts\Activate.ps1
# macOS/Linux:
source .venv/bin/activate
```

**3. Install dependencies**
```bash
pip install -r requirements.txt
# Optional: install the project itself in editable mode
pip install -e .
```

**4. Configure environment variables**
```bash
# Windows PowerShell:
Copy-Item .env.example .env
# macOS/Linux:
cp .env.example .env
```
Open `.env` and fill in `GROQ_API_KEY` (get a free key at
[console.groq.com/keys](https://console.groq.com/keys) — no card
required). Also set `SEC_EDGAR_USER_AGENT` to your real name and email
(the SEC requires this to identify requesters). Every other setting has a
sensible default.

**5. Run the tests**
```bash
python -m pytest tests/ -v
```
Should show all tests passing with no API key required — everything at
the unit level runs against mock tool data or fully local infrastructure
(Chroma, SQLite).

**6. Run a real research challenge**
```bash
python scripts/run_challenge.py 1
```
This uses your real Groq API key and live data sources. Output is written
to `results/challenge_1.md`.

## Project structure

```
agent/          Core reasoning loop, prompts, LLM client, parser
tools/          Tool registry + 12 tool implementations (real + mock fallbacks)
memory/         Short-term (context manager), episodic (SQLite), long-term (Chroma vector)
synthesis/      Multi-source conflict resolution and narrative synthesis
evaluation/     20+ metric evaluation framework
config/         Settings and logging configuration
results/        Output of each of the 8 progressive research challenges
docs/           Architecture spec, trace gallery, optimization log
tests/          Unit tests for every module
```

## Architecture

Hybrid **Plan-and-Execute** agent with bounded re-planning (max 3 cycles),
three-layer memory (short-term context trimming / episodic SQLite /
long-term Chroma vector store), and a source-tier-weighted synthesis
engine. Full rationale in `docs/architecture_specification_final.md`.

## LLM provider

This project uses **Groq** (free tier, no billing required) as its
reasoning engine, via the OpenAI-compatible `groq` SDK. See
`agent/llm_client.py`'s module docstring for why — in short, after
repeated free-tier instability with other providers during development
(model deprecations, tight/blocked quotas), Groq's free tier proved the
most reliable no-cost option.

## Build log

| Day | Deliverable | Status |
|---|---|---|
| 1 | Architecture specification + diagram | Done |
| 2 | Tool registry (10+ tools, schemas, tests) | Done |
| 3 | Environment setup + LLM integration | Done |
| 4 | Core agent loop | Done |
| 5 | Real API integrations (SEC EDGAR, Yahoo Finance, DuckDuckGo search/news) | Done |
| 6 | Memory system (episodic SQLite + long-term Chroma vector + short-term context management) | Done |
| 7 | Remaining tool integrations | Pending |
| 8 | Synthesis engine | Pending |
| 9 | Error handling + graceful degradation | Pending |
| 10 | Query disambiguation | Pending |
| 11 | Evaluation framework | Pending |
| 12 | Challenge 8 + stress testing | Pending |
| 13 | Optimization | Pending |
| 14 | Documentation | Pending |
| 15 | Final submission | Pending |

## AI assistance disclosure

This project was built with assistance from Claude (Anthropic) for
architecture design, code implementation, debugging, and documentation,
per the project's AI Assistance Policy (Section E5.3). All architectural
decisions and their rationale were reviewed and understood by the author.
Notably, the project's LLM provider was changed from an initially-planned
option to Groq during development, after encountering genuine free-tier
reliability issues — documented as a real engineering decision, not a
default choice.
