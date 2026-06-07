"""Seed functions for local stack setup.

Each function accepts the relevant store/client as its first argument so the
logic can be tested with in-memory fakes and wired to real adapters by the CLI.
"""

from __future__ import annotations

from typing import Any, Mapping, Protocol

from urdu_pipeline.application.ports.services import (
    ProviderConfigSnapshot,
    ServiceIdentityRecord,
    UserRecord,
)
from urdu_pipeline.domain import (
    ProviderConfigStatus,
    ProviderConfigVersionId,
    ServiceIdentityId,
    ServiceIdentityStatus,
    UserId,
    UserStatus,
)


# ---------------------------------------------------------------------------
# Minimal store protocols (only the methods each seed function needs)
# ---------------------------------------------------------------------------


class _UserStore(Protocol):
    def create_user(self, record: UserRecord) -> None: ...


class _ServiceIdentityStore(Protocol):
    def create_service_identity(self, record: ServiceIdentityRecord) -> None: ...


class _ProviderConfigStore(Protocol):
    def save_provider_config(self, snapshot: ProviderConfigSnapshot) -> None: ...


# ---------------------------------------------------------------------------
# Seed functions
# ---------------------------------------------------------------------------


def seed_user(store: _UserStore, *, username: str) -> UserRecord:
    """Create a new active user record in the metadata store.

    The caller is responsible for connecting to the real store.  Fails fast on
    a blank username so the CLI gives a clear error before attempting a DB write.
    """
    if not username or not username.strip():
        raise ValueError("username must be a non-empty string.")
    record = UserRecord(
        user_id=UserId.new(),
        username=username.strip(),
        status=UserStatus.ACTIVE,
    )
    store.create_user(record)
    return record


def seed_service_identity(
    store: _ServiceIdentityStore,
    *,
    name: str,
) -> ServiceIdentityRecord:
    """Create a new active service identity record in the metadata store."""
    if not name or not name.strip():
        raise ValueError("name must be a non-empty string.")
    get_by_name = getattr(store, "get_service_identity_by_name", None)
    if callable(get_by_name):
        existing = get_by_name(name.strip())
        if existing is not None:
            return existing
    record = ServiceIdentityRecord(
        service_identity_id=ServiceIdentityId.new(),
        name=name.strip(),
        status=ServiceIdentityStatus.ACTIVE,
    )
    store.create_service_identity(record)
    return record


def seed_provider_config(
    store: _ProviderConfigStore,
    *,
    provider_name: str,
    model_roles: Mapping[str, str],
) -> ProviderConfigSnapshot:
    """Create and persist a provider config snapshot.

    Every call creates a fresh version ID so repeated seeding produces a new
    versioned snapshot rather than overwriting an existing one.
    """
    if not provider_name or not provider_name.strip():
        raise ValueError("provider_name must be a non-empty string.")
    if not model_roles:
        raise ValueError("model_roles must not be empty.")
    snapshot = ProviderConfigSnapshot(
        config_version_id=ProviderConfigVersionId.new(),
        status=ProviderConfigStatus.ACTIVE,
        provider_name=provider_name.strip(),
        model_roles=dict(model_roles),
    )
    store.save_provider_config(snapshot)
    return snapshot


def seed_bucket(
    client: Any,
    *,
    bucket: str,
    region: str | None = None,
) -> bool:
    """Ensure the S3/MinIO bucket exists.

    Returns True if the bucket was created, False if it already existed.
    The caller provides an already-configured boto3-compatible S3 client.

    AWS S3 requires that ``us-east-1`` buckets omit the location constraint
    entirely; all other regions must include it.
    """
    try:
        client.head_bucket(Bucket=bucket)
        return False
    except Exception:
        kwargs: dict[str, Any] = {"Bucket": bucket}
        if region and region != "us-east-1":
            kwargs["CreateBucketConfiguration"] = {"LocationConstraint": region}
        client.create_bucket(**kwargs)
        return True


__all__ = [
    "seed_bucket",
    "seed_provider_config",
    "seed_service_identity",
    "seed_user",
]
