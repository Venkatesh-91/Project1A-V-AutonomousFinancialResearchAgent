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

LLM provider: Google Gemini (via the `google-genai` SDK), chosen because it
has a genuinely free API tier with no billing required -- see
agent/llm_client.py for the client wrapper.
"""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- LLM Provider (Google Gemini -- free tier) ---------------------- #
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.0-flash"

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

    def validate_required_for_live_run(self) -> None:
        """
        Call this before making any REAL (non-stub) LLM call. Day 2's tools
        are all mock stubs and don't need this -- but Day 4's agent loop,
        which actually talks to Gemini, should call this at startup so a
        missing API key fails with a clear message instead of a cryptic
        error three tool calls into a run.
        """
        missing = []
        if not self.gemini_api_key:
            missing.append("GEMINI_API_KEY")
        if missing:
            raise ValueError(
                f"Missing required environment variable(s): {', '.join(missing)}. "
                f"Copy .env.example to .env and fill these in. Get a free key "
                f"at https://aistudio.google.com"
            )

    def ensure_directories_exist(self) -> None:
        """Create the data/log directories this config points to, if absent."""
        Path(self.chroma_persist_dir).mkdir(parents=True, exist_ok=True)
        Path(self.log_dir).mkdir(parents=True, exist_ok=True)


# Module-level singleton -- import this everywhere instead of instantiating
# Settings() repeatedly, so the whole app shares one consistent config.
settings = Settings()
