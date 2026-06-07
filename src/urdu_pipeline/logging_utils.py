"""Logging helpers.

Logs intentionally avoid raw transcripts, full translations, and API keys by
default. They include enough metadata (job, stage, model, cost estimate, cache
status) to debug a run safely.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from collections.abc import Mapping, Sequence
from typing import Any

_LOGGER_NAME = "urdu_pipeline"
_CONFIGURED = False
_REDACTED = "<redacted>"
_MAX_SAFE_STRING_CHARS = 96

_SAFE_STRING_KEYS = {
    "artifact_id",
    "artifact_type",
    "cache",
    "event_type",
    "job_id",
    "language",
    "method",
    "model",
    "model_id",
    "path",
    "provider",
    "run",
    "run_id",
    "severity",
    "stage",
    "stage_name",
    "status",
    "upload_id",
}
_SAFE_SCALAR_KEYS = _SAFE_STRING_KEYS | {
    "attempt",
    "attempt_number",
    "cache_hit",
    "chars",
    "chunk",
    "chunk_count",
    "chunk_len_s",
    "chunks",
    "cost_usd",
    "duration_s",
    "input_chars",
    "output_chars",
    "overlap_s",
    "prompt_chars",
    "safe_count",
    "status_code",
}
_SENSITIVE_KEY_PARTS = {
    "access_key",
    "api_key",
    "authorization",
    "body_markdown",
    "cookie",
    "credential",
    "csrf",
    "developer_instructions",
    "full_text",
    "input_text",
    "instruction",
    "markdown",
    "object_key",
    "output_text",
    "password",
    "prompt",
    "r2_key",
    "raw_text",
    "raw_transcript",
    "s3_key",
    "schema_instructions",
    "secret",
    "secret_key",
    "source_data",
    "source_text",
    "storage_key",
    "system_instructions",
    "text_content",
    "text_english",
    "text_urdu",
    "token",
    "transcript",
    "translation",
}
_SAFE_SEGMENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,95}$")
_SENSITIVE_TEXT_MARKERS = (
    "artifacts/users/",
    "bearer ",
    "cache/users/",
    "never_log",
    "object_key",
    "prompt",
    "sk-",
    "tmp/users/",
    "transcript",
    "translation",
    "article",
)


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


def redact_log_fields(fields: Mapping[str, Any]) -> dict[str, Any]:
    """Return a copy of structured fields safe for logs or public events."""
    return {str(key): _redact_value(str(key), value) for key, value in fields.items()}


def redact_event_message(message: str | None, *, fallback: str) -> str | None:
    """Keep short safe event messages; fall back to event type otherwise."""
    if message is None:
        return None
    if len(message) > _MAX_SAFE_STRING_CHARS or _looks_sensitive_text(message):
        return fallback
    return message


def safe_log_event(logger: logging.Logger, event: str, **fields: Any) -> None:
    """Emit a structured-ish single-line log without leaking secrets/payloads.

    Values are recursively sanitized. Known operational metadata is preserved;
    secrets, prompts, object keys, transcript/translation/article text, and
    unknown free-form strings are redacted or summarized.
    """
    safe_fields = redact_log_fields(fields)
    parts = [event] + [f"{k}={_format_log_value(v)}" for k, v in safe_fields.items()]
    logger.info(" ".join(parts))


def _redact_value(key: str, value: Any) -> Any:
    if _is_sensitive_key(key):
        return _summarize_redacted(value)
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, bytes):
        return _summarize_bytes(value)
    if isinstance(value, str):
        return _redact_string(key, value)
    if isinstance(value, Mapping):
        return {
            str(child_key): _redact_value(str(child_key), child_value)
            for child_key, child_value in value.items()
        }
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [_redact_value(key, item) for item in list(value)[:20]]
    if hasattr(value, "model_dump"):
        return _redact_value(key, value.model_dump(mode="json"))
    return _redact_string(key, str(value))


def _redact_string(key: str, value: str) -> str:
    if _normalize_key(key) == "path" and value.startswith("/") and "?" not in value:
        return value[:_MAX_SAFE_STRING_CHARS]
    if _is_safe_string_key(key) and len(value) <= _MAX_SAFE_STRING_CHARS:
        if _SAFE_SEGMENT_RE.fullmatch(value) or " " in value:
            return value
    if _looks_sensitive_text(value):
        return _summarize_redacted(value)
    return _summarize_redacted(value)


def _is_safe_string_key(key: str) -> bool:
    return _normalize_key(key) in _SAFE_STRING_KEYS


def _is_sensitive_key(key: str) -> bool:
    normalized = _normalize_key(key)
    if normalized in _SAFE_SCALAR_KEYS:
        return False
    return any(part in normalized for part in _SENSITIVE_KEY_PARTS)


def _normalize_key(key: str) -> str:
    return key.replace("-", "_").replace(".", "_").lower()


def _looks_sensitive_text(value: str) -> bool:
    lower = value.lower()
    return any(marker in lower for marker in _SENSITIVE_TEXT_MARKERS)


def _summarize_redacted(value: Any) -> str:
    if value is None:
        return _REDACTED
    text = value if isinstance(value, str) else str(value)
    digest = hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:12]
    return f"{_REDACTED}:chars={len(text)}:sha256={digest}"


def _summarize_bytes(value: bytes) -> str:
    digest = hashlib.sha256(value).hexdigest()[:12]
    return f"<bytes:len={len(value)}:sha256={digest}>"


def _format_log_value(value: Any) -> str:
    if isinstance(value, Mapping | list | tuple):
        return json.dumps(value, ensure_ascii=True, sort_keys=True, default=str)
    return str(value)


__all__ = [
    "get_logger",
    "redact_event_message",
    "redact_log_fields",
    "safe_log_event",
]
