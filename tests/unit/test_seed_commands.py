"""Unit tests for seed/admin commands."""

from __future__ import annotations

from typing import Any

import pytest

from urdu_pipeline.application.ports.services import (
    ProviderConfigSnapshot,
    ServiceIdentityRecord,
    UserRecord,
)
from urdu_pipeline.domain import (
    ProviderConfigStatus,
    ProviderConfigVersionId,
    ServiceIdentityStatus,
    UserStatus,
)
from urdu_pipeline.infrastructure.in_memory import InMemoryMetadataStore


# ---------------------------------------------------------------------------
# seed_user
# ---------------------------------------------------------------------------


def test_seed_user_creates_active_user_in_store():
    from urdu_pipeline.admin.seed import seed_user

    store = InMemoryMetadataStore()
    record = seed_user(store, username="alice")

    assert record.username == "alice"
    assert record.status == UserStatus.ACTIVE
    assert store.get_user(record.user_id) == record


def test_seed_user_assigns_unique_user_ids():
    from urdu_pipeline.admin.seed import seed_user

    store = InMemoryMetadataStore()
    a = seed_user(store, username="alice")
    b = seed_user(store, username="bob")

    assert a.user_id != b.user_id


def test_seed_user_rejects_blank_username():
    from urdu_pipeline.admin.seed import seed_user

    store = InMemoryMetadataStore()

    with pytest.raises(ValueError, match="username"):
        seed_user(store, username="")

    with pytest.raises(ValueError, match="username"):
        seed_user(store, username="   ")


# ---------------------------------------------------------------------------
# seed_service_identity
# ---------------------------------------------------------------------------


def test_seed_service_identity_creates_active_identity_in_store():
    from urdu_pipeline.admin.seed import seed_service_identity

    store = InMemoryMetadataStore()
    record = seed_service_identity(store, name="processor")

    assert record.name == "processor"
    assert record.status == ServiceIdentityStatus.ACTIVE
    assert store.get_service_identity(record.service_identity_id) == record


def test_seed_service_identity_assigns_unique_ids():
    from urdu_pipeline.admin.seed import seed_service_identity

    store = InMemoryMetadataStore()
    a = seed_service_identity(store, name="processor")
    b = seed_service_identity(store, name="cleanup-worker")

    assert a.service_identity_id != b.service_identity_id


def test_seed_service_identity_rejects_blank_name():
    from urdu_pipeline.admin.seed import seed_service_identity

    store = InMemoryMetadataStore()

    with pytest.raises(ValueError, match="name"):
        seed_service_identity(store, name="")

    with pytest.raises(ValueError, match="name"):
        seed_service_identity(store, name="   ")


# ---------------------------------------------------------------------------
# seed_provider_config
# ---------------------------------------------------------------------------


class _FakeProviderConfigStore:
    """Minimal fake for provider config persistence testing."""

    def __init__(self) -> None:
        self._configs: dict[str, ProviderConfigSnapshot] = {}

    def save_provider_config(self, snapshot: ProviderConfigSnapshot) -> None:
        self._configs[str(snapshot.config_version_id)] = snapshot

    def get_config(
        self, config_version_id: ProviderConfigVersionId
    ) -> ProviderConfigSnapshot | None:
        return self._configs.get(str(config_version_id))


def test_seed_provider_config_creates_active_snapshot_with_model_roles():
    from urdu_pipeline.admin.seed import seed_provider_config

    store = _FakeProviderConfigStore()
    model_roles = {
        "transcription": "fake-transcribe",
        "translation": "fake-text",
        "article": "fake-text",
        "reconciliation": "fake-text",
    }

    snapshot = seed_provider_config(
        store,
        provider_name="fake",
        model_roles=model_roles,
    )

    assert snapshot.provider_name == "fake"
    assert snapshot.status == ProviderConfigStatus.ACTIVE
    assert dict(snapshot.model_roles) == model_roles
    assert store.get_config(snapshot.config_version_id) == snapshot


def test_seed_provider_config_assigns_unique_version_ids():
    from urdu_pipeline.admin.seed import seed_provider_config

    store = _FakeProviderConfigStore()
    roles = {"transcription": "m", "translation": "m", "article": "m", "reconciliation": "m"}

    first = seed_provider_config(store, provider_name="fake", model_roles=roles)
    second = seed_provider_config(store, provider_name="fake", model_roles=roles)

    assert first.config_version_id != second.config_version_id


def test_seed_provider_config_rejects_empty_provider_name():
    from urdu_pipeline.admin.seed import seed_provider_config

    store = _FakeProviderConfigStore()

    with pytest.raises(ValueError, match="provider_name"):
        seed_provider_config(store, provider_name="", model_roles={})


def test_seed_provider_config_rejects_empty_model_roles():
    from urdu_pipeline.admin.seed import seed_provider_config

    store = _FakeProviderConfigStore()

    with pytest.raises(ValueError, match="model_roles"):
        seed_provider_config(store, provider_name="fake", model_roles={})


# ---------------------------------------------------------------------------
# seed_bucket
# ---------------------------------------------------------------------------


class _FakeS3Client:
    """Minimal fake S3 client for bucket seed testing."""

    def __init__(self, *, bucket_exists: bool = False) -> None:
        self._bucket_exists = bucket_exists
        self.head_bucket_calls: list[str] = []
        self.create_bucket_calls: list[dict[str, Any]] = []

    def head_bucket(self, **kwargs: Any) -> dict[str, Any]:
        bucket = kwargs["Bucket"]
        self.head_bucket_calls.append(bucket)
        if not self._bucket_exists:
            raise _FakeBucketNotFoundError(f"bucket not found: {bucket}")
        return {}

    def create_bucket(self, **kwargs: Any) -> None:
        self.create_bucket_calls.append(dict(kwargs))
        self._bucket_exists = True


class _FakeBucketNotFoundError(Exception):
    pass


def test_seed_bucket_creates_bucket_when_not_exists():
    from urdu_pipeline.admin.seed import seed_bucket

    client = _FakeS3Client(bucket_exists=False)

    created = seed_bucket(client=client, bucket="urdu-pipeline-local")

    assert created is True
    assert client.head_bucket_calls == ["urdu-pipeline-local"]
    assert len(client.create_bucket_calls) == 1
    assert client.create_bucket_calls[0]["Bucket"] == "urdu-pipeline-local"


def test_seed_bucket_skips_creation_when_bucket_already_exists():
    from urdu_pipeline.admin.seed import seed_bucket

    client = _FakeS3Client(bucket_exists=True)

    created = seed_bucket(client=client, bucket="urdu-pipeline-local")

    assert created is False
    assert client.head_bucket_calls == ["urdu-pipeline-local"]
    assert client.create_bucket_calls == []


def test_seed_bucket_includes_location_constraint_for_non_us_east_1():
    from urdu_pipeline.admin.seed import seed_bucket

    client = _FakeS3Client(bucket_exists=False)

    seed_bucket(client=client, bucket="urdu-pipeline-local", region="eu-west-1")

    assert client.create_bucket_calls[0] == {
        "Bucket": "urdu-pipeline-local",
        "CreateBucketConfiguration": {"LocationConstraint": "eu-west-1"},
    }


def test_seed_bucket_omits_location_constraint_for_us_east_1():
    from urdu_pipeline.admin.seed import seed_bucket

    client = _FakeS3Client(bucket_exists=False)

    seed_bucket(client=client, bucket="urdu-pipeline-local", region="us-east-1")

    assert "CreateBucketConfiguration" not in client.create_bucket_calls[0]


def test_seed_bucket_omits_location_constraint_when_region_is_none():
    from urdu_pipeline.admin.seed import seed_bucket

    client = _FakeS3Client(bucket_exists=False)

    seed_bucket(client=client, bucket="urdu-pipeline-local", region=None)

    assert "CreateBucketConfiguration" not in client.create_bucket_calls[0]
