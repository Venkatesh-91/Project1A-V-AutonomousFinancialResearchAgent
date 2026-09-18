"""
settings.py

Centralized configuration management. Every other module in this project
reads its configuration from here rather than calling os.environ directly
-- that keeps all the "what env vars exist and what do they default to"
knowledge in one place, and makes it trivial to override settings in tests
without touching real environment variables.

Uses pydantic-settings so type coercion and validation happen automatically
(e.g. MAX_REPLAN_CYCLES from the .env file arrives as a real int, not a
string, and a malformed .env fails loudly at startup instead of causing a
confusing bug three modules deep).

LLM provider: Groq (via the official `groq` SDK), chosen after repeated
free-tier instability with Google Gemini -- see agent/llm_client.py's
module docstring for the full story. Groq's free tier (30 req/min, no
observed account-age restrictions) has been the more reliable no-cost
option for this project.
"""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- LLM Provider (Groq -- free tier) -------------------------------- #
    groq_api_key: str = ""
    groq_model: str = "llama-3.3-70b-versatile"

    # --- Vector Database ------------------------------------------------ #
    chroma_persist_dir: str = "./data/chroma"

    # --- Agent Behavior Limits ------------------------------------------ #
    # These map directly onto the termination conditions described in
    # architecture_specification.md Section 2.3.
    max_replan_cycles: int = 3
    max_tool_calls_per_run: int = 20
    research_time_budget_seconds: int = 300

    # --- Retry / Backoff -------------------------------------------------- #
    max_retry_attempts: int = 5
    retry_initial_delay_seconds: int = 1

    # --- Logging ----------------------------------------------------- #
    log_level: str = "INFO"
    log_dir: str = "./logs"

    # --- Optional external tool API keys (unused until Day 5+) --------- #
    tavily_api_key: str = ""
    fmp_api_key: str = ""
    newsapi_key: str = ""

    # --- SEC EDGAR (Day 5) ---------------------------------------------- #
    # The SEC requires every request to identify the requester via a
    # descriptive User-Agent (name + contact email). This has a harmless
    # placeholder default so nothing breaks in tests, but should be set to
    # your real name/email in .env before making real EDGAR calls.
    sec_edgar_user_agent: str = "Student Research Project contact@example.com"

    def validate_required_for_live_run(self) -> None:
        """
        Call this before making any REAL (non-stub) LLM call. Day 2's tools
        are all mock stubs and don't need this -- but Day 4's agent loop,
        which actually talks to Groq, should call this at startup so a
        missing API key fails with a clear message instead of a cryptic
        error three tool calls into a run.
        """
        missing = []
        if not self.groq_api_key:
            missing.append("GROQ_API_KEY")
        if missing:
            raise ValueError(
                f"Missing required environment variable(s): {', '.join(missing)}. "
                f"Copy .env.example to .env and fill these in. Get a free key "
                f"at https://console.groq.com/keys"
            )

    def ensure_directories_exist(self) -> None:
        """Create the data/log directories this config points to, if absent."""
        Path(self.chroma_persist_dir).mkdir(parents=True, exist_ok=True)
        Path(self.log_dir).mkdir(parents=True, exist_ok=True)


# Module-level singleton -- import this everywhere instead of instantiating
# Settings() repeatedly, so the whole app shares one consistent config.
settings = Settings()
