"""
logging_config.py

Sets up the logging infrastructure that captures every agent reasoning
trace, tool call, and error -- the raw material for the "radical
transparency" principle in architecture_specification.md Section 3
(episodic memory) and later the Trace Gallery deliverable (Day 14).

Two handlers are configured:
  1. A console handler (human-readable, colorized via `rich`) for
     watching a run live during development.
  2. A rotating file handler (plain, structured) that persists every run's
     full log to disk under LOG_DIR -- this is what Day 6's episodic
     memory and Day 12's stress-test analysis will read back.

Call `setup_logging()` once, at the very start of the program (e.g. the
top of a `main.py` or the first line of a test session), then get loggers
anywhere else in the codebase with `logging.getLogger(__name__)` as usual.
"""

from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

from rich.logging import RichHandler

from config.settings import settings


def setup_logging() -> None:
    """Configure root logging once for the whole application."""
    settings.ensure_directories_exist()

    log_path = Path(settings.log_dir) / "agent.log"

    root_logger = logging.getLogger()
    root_logger.setLevel(settings.log_level.upper())

    # Avoid duplicate handlers if setup_logging() is accidentally called
    # more than once (e.g. once by a test fixture, once by the app).
    if root_logger.handlers:
        return

    console_handler = RichHandler(rich_tracebacks=True, show_path=False)
    console_handler.setLevel(settings.log_level.upper())
    console_formatter = logging.Formatter("%(message)s")
    console_handler.setFormatter(console_formatter)

    file_handler = logging.handlers.RotatingFileHandler(
        filename=log_path,
        maxBytes=10 * 1024 * 1024,  # 10 MB per file
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)  # file gets everything, console gets LOG_LEVEL
    file_formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )
    file_handler.setFormatter(file_formatter)

    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)

    logging.getLogger(__name__).info(
        "Logging initialized. Console level=%s, file=%s (DEBUG+)",
        settings.log_level.upper(),
        log_path,
    )
