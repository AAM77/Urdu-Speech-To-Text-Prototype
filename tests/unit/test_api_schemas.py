"""Strict public schema tests — Step 4.1.2.

Security constraints verified here:

1. All request schemas carry ``extra="forbid"`` so unknown fields are
   rejected with a validation error (never silently ignored).

2. No public schema (request or response) exposes any of the following
   forbidden field names:
       user_id, object_key, provider, provider_name, model, model_id,
       prompt, text, transcript, translation, article, artifact_json.

3. Specific forbidden fields that callers must never be allowed to inject
   are rejected by name on every mutable request schema (double-check that
   the extra=forbid rule really blocks them).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from urdu_pipeline.api.schemas import (
    ArtifactDownloadResponse,
    ArtifactListResponse,
    ArtifactSummary,
    CancelRunResponse,
    CompleteUploadRequest,
    CreateRunRequest,
    CreateTokenRequest,
    CreateTokenResponse,
    EventListResponse,
    EventResponse,
    InitUploadRequest,
    InitUploadResponse,
    LoginRequest,
    RevokeTokenResponse,
    RunListResponse,
    RunResponse,
    SessionResponse,
    TokenListResponse,
    TokenSummary,
    UploadPartInfo,
    UploadResponse,
)

# ── fixtures ──────────────────────────────────────────────────────────────────

_FORBIDDEN_FIELD_NAMES = frozenset(
    {
        "user_id",
        "object_key",
        "provider",
        "provider_name",
        "model",
        "model_id",
        "prompt",
        "text",
        "transcript",
        "translation",
        "article",
        "artifact_json",
    }
)

_ALL_SCHEMA_CLASSES = [
    ArtifactDownloadResponse,
    ArtifactListResponse,
    ArtifactSummary,
    CancelRunResponse,
    CompleteUploadRequest,
    CreateRunRequest,
    CreateTokenRequest,
    CreateTokenResponse,
    EventListResponse,
    EventResponse,
    InitUploadRequest,
    InitUploadResponse,
    LoginRequest,
    RevokeTokenResponse,
    RunListResponse,
    RunResponse,
    SessionResponse,
    TokenListResponse,
    TokenSummary,
    UploadPartInfo,
    UploadResponse,
]

_REQUEST_SCHEMA_CLASSES = [
    CompleteUploadRequest,
    CreateRunRequest,
    CreateTokenRequest,
    InitUploadRequest,
    LoginRequest,
    UploadPartInfo,
]

# Fields that a caller must never be able to inject into any mutable request.
_CALLER_CANNOT_INJECT = [
    "user_id",
    "provider",
    "provider_name",
    "model",
    "model_id",
    "prompt",
    "text",
    "transcript",
    "translation",
    "article",
    "object_key",
]


def _all_field_names(model_cls) -> set[str]:
    return set(model_cls.model_fields.keys())


def _minimal_valid_data(schema_cls) -> dict:
    """Return the minimum required fields for each request schema."""
    defaults: dict = {
        InitUploadRequest: {
            "filename": "audio.mp3",
            "content_type": "audio/mpeg",
            "size_bytes": 1024,
        },
        UploadPartInfo: {"part_number": 1, "etag": "abc123"},
        CompleteUploadRequest: {},
        CreateRunRequest: {"upload_id": "upl_" + "a" * 32},
        CreateTokenRequest: {"name": "my-token"},
        LoginRequest: {"username": "alice", "password": "s3cret"},
    }
    return dict(defaults.get(schema_cls, {}))


# ── 1. unknown fields are rejected on all request schemas ─────────────────────


class TestUnknownFieldsRejected:
    @pytest.mark.parametrize("schema_cls", _REQUEST_SCHEMA_CLASSES)
    def test_unknown_field_raises_validation_error(self, schema_cls):
        data = _minimal_valid_data(schema_cls)
        data["__injected_unknown__"] = "should be rejected"
        with pytest.raises(ValidationError) as exc_info:
            schema_cls(**data)
        error_types = {e["type"] for e in exc_info.value.errors()}
        assert "extra_forbidden" in error_types, (
            f"{schema_cls.__name__} did not reject an unknown field"
        )

    @pytest.mark.parametrize("schema_cls", _REQUEST_SCHEMA_CLASSES)
    @pytest.mark.parametrize("forbidden_field", _CALLER_CANNOT_INJECT)
    def test_specific_forbidden_field_rejected(self, schema_cls, forbidden_field):
        """Named forbidden fields must be rejected, not silently accepted."""
        data = _minimal_valid_data(schema_cls)
        data[forbidden_field] = "attacker-supplied"
        with pytest.raises(ValidationError) as exc_info:
            schema_cls(**data)
        error_types = {e["type"] for e in exc_info.value.errors()}
        assert "extra_forbidden" in error_types, (
            f"{schema_cls.__name__} accepted forbidden field {forbidden_field!r}"
        )


# ── 2. no forbidden field names appear in any schema ─────────────────────────


class TestNoForbiddenFieldsExposed:
    @pytest.mark.parametrize("schema_cls", _ALL_SCHEMA_CLASSES)
    def test_schema_has_no_forbidden_field_names(self, schema_cls):
        leaked = _all_field_names(schema_cls) & _FORBIDDEN_FIELD_NAMES
        assert not leaked, (
            f"{schema_cls.__name__} exposes forbidden field(s): {leaked!r}"
        )


# ── 3. extra='forbid' config is set on every schema ──────────────────────────


class TestExtraForbidConfig:
    @pytest.mark.parametrize("schema_cls", _ALL_SCHEMA_CLASSES)
    def test_schema_config_forbids_extra(self, schema_cls):
        assert schema_cls.model_config.get("extra") == "forbid", (
            f"{schema_cls.__name__} does not declare extra='forbid'"
        )


# ── 4. valid data round-trips correctly ──────────────────────────────────────


class TestValidDataAccepted:
    def test_init_upload_request_valid(self):
        req = InitUploadRequest(
            filename="interview.mp3",
            content_type="audio/mpeg",
            size_bytes=5_242_880,
        )
        assert req.filename == "interview.mp3"
        assert req.size_bytes == 5_242_880

    def test_create_run_request_valid(self):
        upload_id = "upl_" + "b" * 32
        req = CreateRunRequest(upload_id=upload_id, description="weekly episode")
        assert req.upload_id == upload_id
        assert req.description == "weekly episode"

    def test_create_run_request_no_description(self):
        req = CreateRunRequest(upload_id="upl_" + "c" * 32)
        assert req.description is None

    def test_create_token_request_minimal(self):
        req = CreateTokenRequest(name="ci-token")
        assert req.name == "ci-token"
        assert req.description is None
        assert req.expires_in_days is None

    def test_login_request_valid(self):
        req = LoginRequest(username="alice", password="hunter2")
        assert req.username == "alice"
        assert req.password == "hunter2"

    def test_complete_upload_request_empty(self):
        req = CompleteUploadRequest()
        assert req.parts is None

    def test_complete_upload_request_with_parts(self):
        req = CompleteUploadRequest(
            parts=[
                {"part_number": 1, "etag": "etag-1"},
                {"part_number": 2, "etag": "etag-2"},
            ]
        )
        assert len(req.parts) == 2
        assert req.parts[0].part_number == 1
