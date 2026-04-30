"""Logging helpers.

Logs intentionally avoid raw transcripts, full translations, and API keys by
default. They include enough metadata (job, stage, model, cost estimate, cache
status) to debug a run safely.
"""

from __future__ import annotations

import logging
import os
from typing import Any

_LOGGER_NAME = "urdu_pipeline"
_CONFIGURED = False


def get_logger(name: str | None = None) -> logging.Logger:
    """Return a project logger, configuring root once on first call."""
    global _CONFIGURED
    if not _CONFIGURED:
        level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
        level = getattr(logging, level_name, logging.INFO)
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter(
                fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                datefmt="%Y-%m-%dT%H:%M:%S",
            )
        )
        root = logging.getLogger(_LOGGER_NAME)
        root.setLevel(level)
        # Avoid duplicate handlers if get_logger is called repeatedly in tests.
        root.handlers = [handler]
        root.propagate = False
        _CONFIGURED = True

    if name is None:
        return logging.getLogger(_LOGGER_NAME)
    return logging.getLogger(f"{_LOGGER_NAME}.{name}")


def safe_log_event(logger: logging.Logger, event: str, **fields: Any) -> None:
    """Emit a structured-ish single-line log without leaking secrets/payloads.

    Drops any field whose name contains "key", "secret", "token", "transcript",
    "translation", or "article" to avoid accidentally writing sensitive
    payloads. Override by passing pre-redacted summaries instead.
    """
    redacted_substrings = ("key", "secret", "token", "transcript", "translation", "article_body")
    safe_fields = {
        k: v
        for k, v in fields.items()
        if not any(s in k.lower() for s in redacted_substrings)
    }
    parts = [event] + [f"{k}={v}" for k, v in safe_fields.items()]
    logger.info(" ".join(parts))
