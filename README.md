# Autonomous Financial Research Agent

An AI agent that replicates a junior financial analyst's research
workflow: receiving a query, forming a research plan, gathering data from
SEC EDGAR filings, financial data APIs, earnings transcripts, and news,
resolving conflicts across sources, and synthesizing a structured
investment research report — with a documented architecture, a 10+ tool
registry, three-layer memory, and a 20+ metric evaluation framework.

Built as a 15-day project. See `docs/architecture_specification_final.md`
for the full design rationale.

## Setup

**1. Clone and enter the repo**
```bash
git clone https://github.com/YOUR-USERNAME/Project1A-YourName-AutonomousFinancialResearchAgent.git
cd Project1A-YourName-AutonomousFinancialResearchAgent
```

**2. Create a virtual environment**
```bash
python -m venv venv
# Windows:
venv\Scripts\activate
# macOS/Linux:
source venv/bin/activate
```

**3. Install dependencies**
```bash
pip install -r requirements.txt
```

**4. Configure environment variables**
```bash
# Windows PowerShell:
Copy-Item .env.example .env
# macOS/Linux:
cp .env.example .env
```
Open `.env` and fill in `GEMINI_API_KEY` (get one at
[aistudio.google.com](https://aistudio.google.com)). Every other
setting has a sensible default and can be left as-is.

**5. Run the tests**
```bash
python -m pytest tests/ -v
```
Day 2's tool registry tests (20 tests) should pass with no API key
required, since they run entirely against mock tool data.

## Project structure

```
agent/          Core reasoning loop, prompts, LLM client, error handling
tools/          Tool registry + 10 individual tool implementations
memory/         Short-term, long-term (vector), and episodic memory
synthesis/      Multi-source conflict resolution and narrative synthesis
evaluation/     20+ metric evaluation framework
config/         Settings and logging configuration
results/        Output of each of the 8 progressive research challenges
docs/           Architecture spec, trace gallery, optimization log
tests/          Unit tests for every module
```

## Architecture

Hybrid **Plan-and-Execute** agent with bounded re-planning (max 3 cycles),
three-layer memory (short-term / episodic SQLite / long-term Chroma vector
store), and a source-tier-weighted synthesis engine. Full rationale in
`docs/architecture_specification_final.md`.

## Build log

| Day | Deliverable | Status |
|---|---|---|
| 1 | Architecture specification + diagram | Done |
| 2 | Tool registry (10 tools, schemas, tests) | Done |
| 3 | Environment setup + LLM integration | Done |
| 4 | Core agent loop | Pending |
| 5 | Real API integrations | Pending |
| 6 | Memory system | Pending |
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
architecture design, code implementation, and documentation, per the
project's AI Assistance Policy (Section E5.3). All architectural decisions
and their rationale were reviewed and understood by the author.
