"""Application service ports for metadata, queues, auth, providers, and usage."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal, Mapping, Protocol, Sequence, runtime_checkable

from urdu_pipeline.domain import (
    ArtifactId,
    ArtifactStage,
    ArtifactType,
    JobId,
    JobStatus,
    ProviderConfigStatus,
    ProviderConfigVersionId,
    ProviderRunId,
    RunId,
    RunStatus,
    ServiceIdentityId,
    ServiceIdentityStatus,
    SessionId,
    TokenId,
    UploadId,
    UploadStatus,
    UserId,
    UserStatus,
)

PrincipalKind = Literal["user", "service"]
ModelRole = Literal["transcription", "translation", "article", "reconciliation"]


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


@dataclass(frozen=True)
class UserRecord:
    user_id: UserId
    username: str
    status: UserStatus
    password_hash: str | None = None
    created_at: datetime = field(default_factory=_utcnow)


@dataclass(frozen=True)
class ServiceIdentityRecord:
    service_identity_id: ServiceIdentityId
    name: str
    status: ServiceIdentityStatus
    created_at: datetime = field(default_factory=_utcnow)


@dataclass(frozen=True)
class UploadRecord:
    user_id: UserId
    upload_id: UploadId
    status: UploadStatus
    original_filename: str | None = None
    content_type: str | None = None
    size_bytes: int | None = None
    multipart_upload_id: str | None = None
    created_at: datetime = field(default_factory=_utcnow)


@dataclass(frozen=True)
class RunRecord:
    user_id: UserId
    run_id: RunId
    status: RunStatus
    provider_config_version_id: ProviderConfigVersionId | None = None
    created_at: datetime = field(default_factory=_utcnow)


@dataclass(frozen=True)
class JobRecord:
    user_id: UserId
    run_id: RunId
    job_id: JobId
    status: JobStatus
    created_at: datetime = field(default_factory=_utcnow)


@dataclass(frozen=True)
class ArtifactRecord:
    user_id: UserId
    run_id: RunId
    artifact_id: ArtifactId
    stage: ArtifactStage
    artifact_type: ArtifactType
    created_at: datetime = field(default_factory=_utcnow)


@dataclass(frozen=True)
class QueueMessage:
    """Queue payload limited to job ID plus safe routing metadata."""

    job_id: JobId
    routing: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class JobLease:
    job_id: JobId
    lease_id: str
    attempt_number: int
    expires_at: datetime
    routing: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class CacheScope:
    user_id: UserId
    name: str


@dataclass(frozen=True)
class CacheEntry:
    scope: CacheScope
    key: str
    payload: Mapping[str, Any]
    created_at: datetime = field(default_factory=_utcnow)


@dataclass(frozen=True)
class SessionRecord:
    """A server-side session associated with one authenticated user.

    ``token_hash`` is a SHA-256 hex digest of the raw session token.  The raw
    token is given to the client in an HTTP-only cookie; only the hash is
    persisted so a DB read never reveals the client's credential.
    """

    session_id: SessionId
    user_id: UserId
    token_hash: str
    expires_at: datetime
    created_at: datetime = field(default_factory=_utcnow)
    revoked_at: datetime | None = None


@dataclass(frozen=True)
class BearerTokenRecord:
    """A long-lived bearer token for programmatic API access.

    ``token_hash`` is a SHA-256 hex digest of the raw token.  The raw token is
    returned to the caller exactly once at creation time; only the hash is
    persisted.  On each successful resolution ``last_used_at`` is updated so
    operators can audit which tokens are in use.
    """

    token_id: TokenId
    user_id: UserId
    token_hash: str
    name: str
    description: str | None = None
    created_at: datetime = field(default_factory=_utcnow)
    expires_at: datetime | None = None
    revoked_at: datetime | None = None
    last_used_at: datetime | None = None


@dataclass(frozen=True)
class AuthPrincipal:
    principal_id: UserId | ServiceIdentityId
    kind: PrincipalKind
    scopes: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class SecretValue:
    name: str
    value: str

    def __repr__(self) -> str:
        return f"SecretValue(name={self.name!r}, value=<redacted>)"

    def __str__(self) -> str:
        return f"SecretValue(name={self.name!r}, value=<redacted>)"


@dataclass(frozen=True)
class ProviderConfigSnapshot:
    config_version_id: ProviderConfigVersionId
    status: ProviderConfigStatus
    provider_name: str
    model_roles: Mapping[str, str]
    prompt_versions: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class UsageRecord:
    provider_run_id: ProviderRunId
    user_id: UserId
    run_id: RunId
    job_id: JobId
    provider_name: str
    model_id: str
    cost_usd: float
    usage: Mapping[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=_utcnow)


@dataclass(frozen=True)
class BudgetDecision:
    allowed: bool
    blocked: bool
    warning: bool
    projected_total_usd: float
    hard_cap_usd: float
    reason: str


@runtime_checkable
class MetadataStore(Protocol):
    """Durable metadata store for owned application records."""

    def create_user(self, record: UserRecord) -> None: ...

    def get_user(self, user_id: UserId) -> UserRecord | None: ...

    def get_user_by_username(self, username: str) -> UserRecord | None: ...

    def update_user(self, record: UserRecord) -> None: ...

    def list_users(self) -> Sequence[UserRecord]: ...

    def create_session(self, record: SessionRecord) -> None: ...

    def get_session_by_token_hash(self, token_hash: str) -> SessionRecord | None: ...

    def revoke_session(self, session_id: SessionId, *, revoked_at: datetime) -> None: ...

    def create_bearer_token(self, record: BearerTokenRecord) -> None: ...

    def get_bearer_token_by_hash(self, token_hash: str) -> BearerTokenRecord | None: ...

    def get_bearer_token(self, token_id: TokenId) -> BearerTokenRecord | None: ...

    def update_bearer_token(self, record: BearerTokenRecord) -> None: ...

    def list_bearer_tokens_for_user(self, user_id: UserId) -> Sequence[BearerTokenRecord]: ...

    def create_service_identity(self, record: ServiceIdentityRecord) -> None: ...

    def get_service_identity(
        self,
        service_identity_id: ServiceIdentityId,
    ) -> ServiceIdentityRecord | None: ...

    def update_service_identity(self, record: ServiceIdentityRecord) -> None: ...

    def create_upload(self, record: UploadRecord) -> None: ...

    def get_upload(self, *, user_id: UserId, upload_id: UploadId) -> UploadRecord | None: ...

    def update_upload(self, record: UploadRecord) -> None: ...

    def list_uploads(self, *, user_id: UserId) -> Sequence[UploadRecord]: ...

    def create_run(self, record: RunRecord) -> None: ...

    def get_run(self, *, user_id: UserId, run_id: RunId) -> RunRecord | None: ...

    def list_runs(self, *, user_id: UserId) -> Sequence[RunRecord]: ...

    def create_job(self, record: JobRecord) -> None: ...

    def get_job(self, *, user_id: UserId, job_id: JobId) -> JobRecord | None: ...

    def record_artifact(self, record: ArtifactRecord) -> None: ...

    def get_artifact(
        self,
        *,
        user_id: UserId,
        artifact_id: ArtifactId,
    ) -> ArtifactRecord | None: ...


@runtime_checkable
class JobQueue(Protocol):
    """Queue port for job delivery and lifecycle signals."""

    def enqueue(self, message: QueueMessage) -> None: ...

    def claim(
        self,
        *,
        worker_id: ServiceIdentityId,
        lease_seconds: int,
    ) -> JobLease | None: ...

    def extend_lease(
        self,
        lease: JobLease,
        *,
        lease_seconds: int,
    ) -> JobLease: ...

    def retry(self, lease: JobLease, *, reason: str) -> None: ...

    def mark_terminal_failure(self, lease: JobLease, *, reason: str) -> None: ...

    def cancel(self, job_id: JobId, *, reason: str) -> None: ...

    def dead_letter(self, lease: JobLease, *, reason: str) -> None: ...


@runtime_checkable
class CacheStore(Protocol):
    """Scoped cache store."""

    def get(self, scope: CacheScope, key: str) -> CacheEntry | None: ...

    def put(self, scope: CacheScope, key: str, payload: Mapping[str, Any]) -> CacheEntry: ...

    def delete(self, scope: CacheScope, key: str) -> bool: ...


@runtime_checkable
class AuthService(Protocol):
    """Authentication and secret-hashing boundary."""

    def authenticate_password(self, username: str, password: str) -> AuthPrincipal | None: ...

    def authenticate_bearer_token(self, token: str) -> AuthPrincipal | None: ...

    def authenticate_service_token(self, token: str) -> AuthPrincipal | None: ...

    def hash_secret(self, secret: str) -> str: ...

    def verify_secret(self, secret: str, secret_hash: str) -> bool: ...


@runtime_checkable
class SecretProvider(Protocol):
    """Runtime secret lookup boundary."""

    def get_secret(self, name: str) -> SecretValue: ...


@runtime_checkable
class ProviderRegistry(Protocol):
    """Server-controlled provider/model/prompt configuration boundary."""

    def get_active_config(self) -> ProviderConfigSnapshot: ...

    def get_config(
        self,
        config_version_id: ProviderConfigVersionId,
    ) -> ProviderConfigSnapshot | None: ...

    def model_for_role(
        self,
        config_version_id: ProviderConfigVersionId,
        role: ModelRole,
    ) -> str: ...


@runtime_checkable
class UsageLedger(Protocol):
    """Usage and cost persistence boundary."""

    def record_usage(self, record: UsageRecord) -> None: ...

    def list_run_usage(self, *, user_id: UserId, run_id: RunId) -> Sequence[UsageRecord]: ...

    def total_run_cost_usd(self, *, user_id: UserId, run_id: RunId) -> float: ...


@runtime_checkable
class BudgetService(Protocol):
    """Budget decision boundary."""

    def check_run_budget(
        self,
        *,
        user_id: UserId,
        run_id: RunId,
        next_cost_usd: float,
    ) -> BudgetDecision: ...

    def record_actual_cost(
        self,
        *,
        user_id: UserId,
        run_id: RunId,
        cost_usd: float,
    ) -> None: ...


__all__ = [
    "ArtifactRecord",
    "AuthPrincipal",
    "AuthService",
    "BearerTokenRecord",
    "BudgetDecision",
    "BudgetService",
    "CacheEntry",
    "CacheScope",
    "CacheStore",
    "JobLease",
    "JobQueue",
    "JobRecord",
    "MetadataStore",
    "ModelRole",
    "PrincipalKind",
    "ProviderConfigSnapshot",
    "ProviderRegistry",
    "QueueMessage",
    "RunRecord",
    "SecretProvider",
    "SecretValue",
    "ServiceIdentityRecord",
    "SessionRecord",
    "UploadRecord",
    "UsageLedger",
    "UsageRecord",
    "UserRecord",
]
