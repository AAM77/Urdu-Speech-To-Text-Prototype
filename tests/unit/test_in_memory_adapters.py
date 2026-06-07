"""In-memory adapter contract tests."""

from __future__ import annotations

import io

import pytest

from urdu_pipeline.application.ports import MetadataStore, ObjectMetadata, ObjectStore
from urdu_pipeline.application.ports.services import (
    ArtifactRecord,
    JobRecord,
    RunRecord,
    UploadRecord,
    UserRecord,
)
from urdu_pipeline.domain import (
    ArtifactId,
    ArtifactStage,
    ArtifactType,
    JobId,
    JobStatus,
    RunId,
    RunStatus,
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
