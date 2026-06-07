"""Stable domain state enums for API and processor workflows."""

from __future__ import annotations

from enum import Enum


class StableStrEnum(str, Enum):
    """String enum with values intended for persisted records and API payloads."""

    def __str__(self) -> str:
        return self.value


class UploadStatus(StableStrEnum):
    INITIALIZED = "initialized"
    UPLOADING = "uploading"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class RunStatus(StableStrEnum):
    PENDING = "pending"
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobStatus(StableStrEnum):
    QUEUED = "queued"
    CLAIMED = "claimed"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    DEAD_LETTERED = "dead_lettered"


class JobAttemptStatus(StableStrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


class ArtifactStage(StableStrEnum):
    CHUNKER = "chunker"
    TRANSCRIBER = "transcriber"
    TRANSCRIPT_RECONCILER = "transcript_reconciler"
    TRANSLATOR = "translator"
    ARTICLE_GENERATOR = "article_generator"
    ENGLISH_CHUNK_TRANSCRIBER = "english_chunk_transcriber"


class ArtifactType(StableStrEnum):
    CHUNK_MANIFEST = "chunk_manifest"
    RAW_URDU_TRANSCRIPT = "raw_urdu_transcript"
    RECONCILED_URDU_TRANSCRIPT = "reconciled_urdu_transcript"
    ENGLISH_TRANSLATION = "english_translation"
    FINAL_ARTICLE = "final_article"
    RAW_AM_ENGLISH_TRANSCRIPT = "raw_am_english_transcript"


class ProviderConfigStatus(StableStrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    DISABLED = "disabled"
    RETIRED = "retired"


class CleanupTaskStatus(StableStrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    RETRYING = "retrying"
    CANCELLED = "cancelled"


class UserStatus(StableStrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"
    LOCKED = "locked"
    DELETED = "deleted"


class ServiceIdentityStatus(StableStrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"
    REVOKED = "revoked"


__all__ = [
    "ArtifactStage",
    "ArtifactType",
    "CleanupTaskStatus",
    "JobAttemptStatus",
    "JobStatus",
    "ProviderConfigStatus",
    "RunStatus",
    "ServiceIdentityStatus",
    "StableStrEnum",
    "UploadStatus",
    "UserStatus",
]
