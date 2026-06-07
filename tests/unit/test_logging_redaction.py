"""Structured logging and redaction contracts for Stage 7.1.1."""

from __future__ import annotations

import io
import json
import logging
from types import SimpleNamespace

from fastapi.testclient import TestClient

from urdu_pipeline.domain import ArtifactStage, JobId, RunId, UserId


def _capture_logger() -> tuple[logging.Logger, io.StringIO]:
    stream = io.StringIO()
    logger = logging.getLogger(f"test-redaction-{id(stream)}")
    logger.handlers = [logging.StreamHandler(stream)]
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger, stream


def test_safe_log_event_removes_sensitive_top_level_fields():
    from urdu_pipeline.logging_utils import safe_log_event

    logger, stream = _capture_logger()

    safe_log_event(
        logger,
        "provider_call",
        model="fake-text",
        chars=123,
        api_key="sk-live-secret",
        authorization="Bearer raw-service-token",
        prompt="NEVER_LOG_PROMPT",
        raw_transcript="NEVER_LOG_TRANSCRIPT",
        translation="NEVER_LOG_TRANSLATION",
        article_body="NEVER_LOG_ARTICLE",
        object_key="artifacts/users/user_abc/runs/run_def/final/artifact.json",
    )

    output = stream.getvalue()
    assert "provider_call" in output
    assert "model=fake-text" in output
    assert "chars=123" in output
    for forbidden in (
        "sk-live-secret",
        "raw-service-token",
        "NEVER_LOG_PROMPT",
        "NEVER_LOG_TRANSCRIPT",
        "NEVER_LOG_TRANSLATION",
        "NEVER_LOG_ARTICLE",
        "artifacts/users/user_abc",
    ):
        assert forbidden not in output


def test_safe_log_event_redacts_nested_payloads_and_unknown_long_strings():
    from urdu_pipeline.logging_utils import safe_log_event

    logger, stream = _capture_logger()

    safe_log_event(
        logger,
        "stage_event",
        payload={
            "stage": "translator",
            "artifact_id": "artifact_safe123",
            "object_key": "artifacts/users/user_abc/runs/run_def/translator/artifact.json",
            "full_text_english": "NEVER_LOG_FULL_TRANSLATION",
            "segments": [{"text_urdu": "NEVER_LOG_URDU_TRANSCRIPT"}],
            "article": {"body_markdown": "NEVER_LOG_ARTICLE_BODY"},
            "operator_note": "NEVER_LOG_NEUTRAL_LONG_STRING_" + ("x" * 200),
        },
    )

    output = stream.getvalue()
    assert "stage_event" in output
    assert "translator" in output
    assert "artifact_safe123" in output
    for forbidden in (
        "artifacts/users/user_abc",
        "NEVER_LOG_FULL_TRANSLATION",
        "NEVER_LOG_URDU_TRANSCRIPT",
        "NEVER_LOG_ARTICLE_BODY",
        "NEVER_LOG_NEUTRAL_LONG_STRING",
    ):
        assert forbidden not in output


def test_redact_log_fields_preserves_safe_operational_metadata():
    from urdu_pipeline.logging_utils import redact_log_fields

    redacted = redact_log_fields(
        {
            "stage": "article_generator",
            "event_type": "stage_succeeded",
            "model_id": "fake-text",
            "provider": "fake",
            "attempt_number": 2,
            "cache_hit": False,
            "cost_usd": 0.0,
        }
    )

    assert redacted == {
        "stage": "article_generator",
        "event_type": "stage_succeeded",
        "model_id": "fake-text",
        "provider": "fake",
        "attempt_number": 2,
        "cache_hit": False,
        "cost_usd": 0.0,
    }


def test_api_request_logging_excludes_headers_query_and_body(monkeypatch):
    from urdu_pipeline.api import app as app_module

    calls: list[tuple[str, dict[str, object]]] = []

    def fake_safe_log_event(logger, event: str, **fields: object) -> None:
        calls.append((event, fields))

    monkeypatch.setattr(app_module, "safe_log_event", fake_safe_log_event)

    client = TestClient(app_module.create_app())
    response = client.get(
        "/health?token=NEVER_LOG_QUERY_TOKEN&prompt=NEVER_LOG_QUERY_PROMPT",
        headers={
            "Authorization": "Bearer NEVER_LOG_AUTH_HEADER",
            "Cookie": "session=NEVER_LOG_COOKIE",
            "X-CSRF-Token": "NEVER_LOG_CSRF",
        },
    )

    assert response.status_code == 200
    assert calls == [
        (
            "api_request",
            {
                "method": "GET",
                "path": "/health",
                "status_code": 200,
            },
        )
    ]
    serialized = json.dumps(calls, ensure_ascii=False)
    for forbidden in (
        "NEVER_LOG_QUERY_TOKEN",
        "NEVER_LOG_QUERY_PROMPT",
        "NEVER_LOG_AUTH_HEADER",
        "NEVER_LOG_COOKIE",
        "NEVER_LOG_CSRF",
    ):
        assert forbidden not in serialized


def test_processor_stage_events_sanitize_sensitive_message_and_payload():
    from urdu_pipeline.processor.runtime import _record_event

    captured = []

    class Store:
        def record_stage_event(self, record):
            captured.append(record)

    job = SimpleNamespace(
        user_id=UserId.new(),
        run_id=RunId.new(),
        job_id=JobId.new(),
    )

    _record_event(
        Store(),
        job,
        ArtifactStage.TRANSLATOR,
        "stage_failed",
        message="failed while handling NEVER_LOG_TRANSLATION",
        payload={
            "stage": "translator",
            "safe_count": 3,
            "object_key": "artifacts/users/user_abc/runs/run_def/translator/artifact.json",
            "full_text_english": "NEVER_LOG_FULL_TRANSLATION",
            "prompt": "NEVER_LOG_PROMPT",
        },
    )

    assert len(captured) == 1
    event = captured[0]
    assert event.message == "stage_failed"
    assert event.payload["stage"] == "translator"
    assert event.payload["safe_count"] == 3
    serialized = json.dumps(
        {"message": event.message, "payload": event.payload},
        ensure_ascii=False,
    )
    for forbidden in (
        "NEVER_LOG_TRANSLATION",
        "NEVER_LOG_FULL_TRANSLATION",
        "NEVER_LOG_PROMPT",
        "artifacts/users/user_abc",
    ):
        assert forbidden not in serialized
