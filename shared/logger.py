"""Simple project logger.

Logs to console and to data/reports/pipeline.log (rules.yaml §6.5).
Each call to get_logger(name) returns a named logger that shares the same handlers.
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from shared.settings import get_path

_configured = False


def _configure_root_handlers() -> None:
    """Attach console + file handlers once to the shared project logger."""
    global _configured
    if _configured:
        return

    root = logging.getLogger("cap_proj")
    root.setLevel(logging.INFO)
    root.handlers.clear()
    root.propagate = False

    fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    root.addHandler(console)

    log_file = get_path("pipeline_log")
    log_file.parent.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=2_000_000,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)

    _configured = True


def get_logger(name: str = "cap_proj") -> logging.Logger:
    """Return a named logger under the shared cap_proj handlers."""
    _configure_root_handlers()
    if name == "cap_proj":
        return logging.getLogger("cap_proj")
    # Child loggers inherit handlers via the cap_proj parent
    logger = logging.getLogger(f"cap_proj.{name}")
    logger.setLevel(logging.INFO)
    return logger
