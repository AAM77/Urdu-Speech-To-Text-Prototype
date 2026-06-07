"""In-memory adapter contract tests."""

from __future__ import annotations

import io

import pytest

from urdu_pipeline.application.ports import MetadataStore, ObjectMetadata, ObjectStore
from urdu_pipeline.application.ports.services import (
    ArtifactRecord,
    CacheScope,
    JobRecord,
    QueueMessage,
    RunRecord,
    UploadRecord,
    UsageRecord,
    UserRecord,
)
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
    UploadId,
    UploadStatus,
    UserId,
    UserStatus,
)


def test_in_memory_object_store_put_get_head_delete_and_list_prefix():
    from urdu_pipeline.infrastructure.in_memory import InMemoryObjectStore

    store = InMemoryObjectStore()
    assert isinstance(store, ObjectStore)

    first_key = "tmp/users/usr_1/uploads/upl_1/source"
    second_key = "tmp/users/usr_1/uploads/upl_2/source"
    other_key = "artifacts/users/usr_1/runs/run_1/chunker/art_1/artifact.json"
    metadata = ObjectMetadata(
        content_type="audio/mpeg",
        checksum_sha256="a" * 64,
        user_metadata={"purpose": "upload"},
    )

    first_info = store.put_stream(first_key, io.BytesIO(b"first"), metadata=metadata)
    store.put_stream(second_key, io.BytesIO(b"second"))
    store.put_stream(other_key, io.BytesIO(b"other"))

    assert first_info.key == first_key
    assert first_info.size_bytes == 5
    assert first_info.content_type == "audio/mpeg"
    assert first_info.checksum_sha256 == "a" * 64
    assert dict(first_info.user_metadata) == {"purpose": "upload"}
    assert store.get_stream(first_key).read() == b"first"
    assert store.head_object(first_key).size_bytes == 5
    assert [info.key for info in store.list_prefix("tmp/users/usr_1/uploads/")] == [
        first_key,
        second_key,
    ]

    store.delete_object(first_key)
    with pytest.raises(KeyError):
        store.get_stream(first_key)

    assert store.delete_prefix("tmp/users/usr_1/uploads/") == 1
    assert store.list_prefix("tmp/users/usr_1/uploads/") == []
    assert store.head_object(other_key).size_bytes == 5


def test_in_memory_object_store_rejects_missing_or_traversal_keys():
    from urdu_pipeline.infrastructure.in_memory import InMemoryObjectStore

    store = InMemoryObjectStore()

    for key in ("", "../secret", "tmp/users/../secret", "tmp//source", "tmp/users/x/"):
        with pytest.raises(ValueError):
            store.put_stream(key, io.BytesIO(b"bad"))

    with pytest.raises(KeyError):
        store.head_object("tmp/users/missing/source")


def test_in_memory_metadata_store_enforces_user_owned_reads():
    from urdu_pipeline.infrastructure.in_memory import InMemoryMetadataStore

    store = InMemoryMetadataStore()
    assert isinstance(store, MetadataStore)

    owner_id = UserId.new()
    other_id = UserId.new()
    upload_id = UploadId.new()
    run_id = RunId.new()
    job_id = JobId.new()
    artifact_id = ArtifactId.new()

    owner = UserRecord(user_id=owner_id, username="owner", status=UserStatus.ACTIVE)
    other = UserRecord(user_id=other_id, username="other", status=UserStatus.ACTIVE)
    store.create_user(owner)
    store.create_user(other)
    store.create_upload(
        UploadRecord(
            user_id=owner_id,
            upload_id=upload_id,
            status=UploadStatus.COMPLETED,
            original_filename="lecture final.mp3",
        )
    )
    store.create_run(
        RunRecord(user_id=owner_id, run_id=run_id, status=RunStatus.QUEUED)
    )
    store.create_job(
        JobRecord(
            user_id=owner_id,
            run_id=run_id,
            job_id=job_id,
            status=JobStatus.QUEUED,
        )
    )
    store.record_artifact(
        ArtifactRecord(
            user_id=owner_id,
            run_id=run_id,
            artifact_id=artifact_id,
            stage=ArtifactStage.CHUNKER,
            artifact_type=ArtifactType.CHUNK_MANIFEST,
        )
    )

    assert store.get_user(owner_id) == owner
    assert store.get_upload(user_id=owner_id, upload_id=upload_id).original_filename == "lecture final.mp3"
    assert store.get_upload(user_id=other_id, upload_id=upload_id) is None
    assert store.get_run(user_id=owner_id, run_id=run_id).status is RunStatus.QUEUED
    assert store.get_run(user_id=other_id, run_id=run_id) is None
    assert store.get_job(user_id=owner_id, job_id=job_id).run_id == run_id
    assert store.get_job(user_id=other_id, job_id=job_id) is None
    assert store.get_artifact(user_id=owner_id, artifact_id=artifact_id).run_id == run_id
    assert store.get_artifact(user_id=other_id, artifact_id=artifact_id) is None


def test_in_memory_job_queue_claim_extend_retry_cancel_failure_and_dead_letter():
    from urdu_pipeline.application.ports import JobQueue
    from urdu_pipeline.infrastructure.in_memory import InMemoryJobQueue

    queue = InMemoryJobQueue()
    assert isinstance(queue, JobQueue)

    first_job_id = JobId.new()
    queue.enqueue(
        QueueMessage(
            job_id=first_job_id,
            routing={"queue": "default", "stage": "transcriber", "priority": "normal"},
        )
    )

    first_lease = queue.claim(worker_id=ServiceIdentityId.new(), lease_seconds=30)
    assert first_lease is not None
    assert first_lease.job_id == first_job_id
    assert first_lease.attempt_number == 1

    extended = queue.extend_lease(first_lease, lease_seconds=60)
    assert extended.lease_id == first_lease.lease_id
    assert extended.expires_at > first_lease.expires_at

    queue.retry(extended, reason="temporary provider failure")
    retried_lease = queue.claim(worker_id=ServiceIdentityId.new(), lease_seconds=30)
    assert retried_lease is not None
    assert retried_lease.job_id == first_job_id
    assert retried_lease.attempt_number == 2

    queue.mark_terminal_failure(retried_lease, reason="retry limit exceeded")
    assert queue.claim(worker_id=ServiceIdentityId.new(), lease_seconds=30) is None

    cancelled_job_id = JobId.new()
    queue.enqueue(QueueMessage(job_id=cancelled_job_id, routing={"queue": "default"}))
    queue.cancel(cancelled_job_id, reason="user requested cancellation")
    assert queue.claim(worker_id=ServiceIdentityId.new(), lease_seconds=30) is None

    dead_letter_job_id = JobId.new()
    queue.enqueue(QueueMessage(job_id=dead_letter_job_id, routing={"queue": "default"}))
    dead_letter_lease = queue.claim(worker_id=ServiceIdentityId.new(), lease_seconds=30)
    assert dead_letter_lease is not None
    queue.dead_letter(dead_letter_lease, reason="poison message")
    assert queue.claim(worker_id=ServiceIdentityId.new(), lease_seconds=30) is None


def test_in_memory_job_queue_rejects_unsafe_routing_metadata():
    from urdu_pipeline.infrastructure.in_memory import InMemoryJobQueue

    queue = InMemoryJobQueue()
    unsafe_messages = [
        QueueMessage(job_id=JobId.new(), routing={"user_id": str(UserId.new())}),
        QueueMessage(job_id=JobId.new(), routing={"object_key": "tmp/users/u/uploads/x/source"}),
        QueueMessage(job_id=JobId.new(), routing={"queue": "../default"}),
        QueueMessage(job_id=JobId.new(), routing={"prompt_id": "translation_v1"}),
        QueueMessage(job_id=JobId.new(), routing={"model_id": "gpt-5.5"}),
    ]

    for message in unsafe_messages:
        with pytest.raises(ValueError):
            queue.enqueue(message)


def test_in_memory_cache_store_keeps_user_scopes_isolated():
    from urdu_pipeline.application.ports import CacheStore
    from urdu_pipeline.infrastructure.in_memory import InMemoryCacheStore

    cache = InMemoryCacheStore()
    assert isinstance(cache, CacheStore)

    first_scope = CacheScope(user_id=UserId.new(), name="translator")
    second_scope = CacheScope(user_id=UserId.new(), name="translator")

    first_entry = cache.put(first_scope, "same_key", {"text": "first"})
    second_entry = cache.put(second_scope, "same_key", {"text": "second"})

    assert first_entry.payload["text"] == "first"
    assert second_entry.payload["text"] == "second"
    assert cache.get(first_scope, "same_key").payload["text"] == "first"
    assert cache.get(second_scope, "same_key").payload["text"] == "second"
    assert cache.delete(first_scope, "same_key") is True
    assert cache.get(first_scope, "same_key") is None
    assert cache.get(second_scope, "same_key").payload["text"] == "second"

    with pytest.raises(ValueError):
        cache.put(CacheScope(user_id=UserId.new(), name="../translator"), "key", {})
    with pytest.raises(ValueError):
        cache.put(first_scope, "../key", {})


def test_in_memory_secret_provider_fails_closed_for_missing_secret():
    from urdu_pipeline.application.ports import SecretProvider
    from urdu_pipeline.infrastructure.in_memory import InMemorySecretProvider

    secrets = InMemorySecretProvider({"OPENAI_API_KEY": "sk-test"})
    assert isinstance(secrets, SecretProvider)
    assert secrets.get_secret("OPENAI_API_KEY").value == "sk-test"

    with pytest.raises(KeyError):
        secrets.get_secret("MISSING_SECRET")


def test_in_memory_provider_registry_usage_ledger_and_budget_service():
    from urdu_pipeline.application.ports import BudgetService, ProviderRegistry, UsageLedger
    from urdu_pipeline.application.ports.services import ProviderConfigSnapshot
    from urdu_pipeline.infrastructure.in_memory import (
        InMemoryBudgetService,
        InMemoryProviderRegistry,
        InMemoryUsageLedger,
    )

    config_version_id = ProviderConfigVersionId.new()
    config = ProviderConfigSnapshot(
        config_version_id=config_version_id,
        status=ProviderConfigStatus.ACTIVE,
        provider_name="fake",
        model_roles={"translation": "fake-text", "transcription": "fake-transcribe"},
        prompt_versions={"translation": "v1"},
    )
    registry = InMemoryProviderRegistry(active_config=config)
    usage = InMemoryUsageLedger()
    budget = InMemoryBudgetService(usage_ledger=usage, hard_cap_usd=1.00)

    assert isinstance(registry, ProviderRegistry)
    assert isinstance(usage, UsageLedger)
    assert isinstance(budget, BudgetService)
    assert registry.get_active_config() == config
    assert registry.get_config(config_version_id) == config
    assert registry.get_config(ProviderConfigVersionId.new()) is None
    assert registry.model_for_role(config_version_id, "translation") == "fake-text"

    user_id = UserId.new()
    run_id = RunId.new()
    usage.record_usage(
        UsageRecord(
            provider_run_id=ProviderRunId.new(),
            user_id=user_id,
            run_id=run_id,
            job_id=JobId.new(),
            provider_name="fake",
            model_id="fake-text",
            cost_usd=0.25,
            usage={"input_tokens": 10},
        )
    )

    assert usage.total_run_cost_usd(user_id=user_id, run_id=run_id) == 0.25
    assert budget.check_run_budget(user_id=user_id, run_id=run_id, next_cost_usd=0.50).allowed
    blocked = budget.check_run_budget(user_id=user_id, run_id=run_id, next_cost_usd=0.80)
    assert blocked.blocked
    assert not blocked.allowed

    budget.record_actual_cost(user_id=user_id, run_id=run_id, cost_usd=0.10)
    assert usage.total_run_cost_usd(user_id=user_id, run_id=run_id) == 0.35


def test_in_memory_metadata_store_rejects_cross_owner_or_missing_parent_records():
    from urdu_pipeline.infrastructure.in_memory import InMemoryMetadataStore

    store = InMemoryMetadataStore()
    owner_id = UserId.new()
    other_id = UserId.new()
    run_id = RunId.new()

    store.create_user(UserRecord(user_id=owner_id, username="owner", status=UserStatus.ACTIVE))
    store.create_user(UserRecord(user_id=other_id, username="other", status=UserStatus.ACTIVE))
    store.create_run(RunRecord(user_id=owner_id, run_id=run_id, status=RunStatus.QUEUED))

    with pytest.raises(ValueError, match="user"):
        store.create_upload(
            UploadRecord(
                user_id=UserId.new(),
                upload_id=UploadId.new(),
                status=UploadStatus.INITIALIZED,
            )
        )

    with pytest.raises(ValueError, match="run"):
        store.create_job(
            JobRecord(
                user_id=other_id,
                run_id=run_id,
                job_id=JobId.new(),
                status=JobStatus.QUEUED,
            )
        )

    with pytest.raises(ValueError, match="run"):
        store.record_artifact(
            ArtifactRecord(
                user_id=other_id,
                run_id=run_id,
                artifact_id=ArtifactId.new(),
                stage=ArtifactStage.CHUNKER,
                artifact_type=ArtifactType.CHUNK_MANIFEST,
            )
        )
