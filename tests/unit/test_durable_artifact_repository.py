"""Durable artifact repository used by the background processor."""

from __future__ import annotations

from io import BytesIO

from urdu_pipeline.application.ports.services import ArtifactRecord, RunRecord, UserRecord
from urdu_pipeline.domain import (
    ArtifactId,
    ArtifactStage,
    ArtifactType,
    JobId,
    JobStatus,
    RunId,
    RunStatus,
    UploadId,
    UserId,
    UserStatus,
)
from urdu_pipeline.infrastructure.in_memory import InMemoryMetadataStore, InMemoryObjectStore


def test_repository_persists_json_markdown_metadata_and_document_chunks():
    from urdu_pipeline.application.ports.services import JobRecord, RunRecord
    from urdu_pipeline.infrastructure.artifacts import ObjectStoreArtifactRepository

    metadata = InMemoryMetadataStore()
    objects = InMemoryObjectStore()
    user_id = UserId.new()
    run_id = RunId.new()
    job_id = JobId.new()
    artifact_id = ArtifactId.new()
    metadata.create_user(
        UserRecord(user_id=user_id, username="alice", status=UserStatus.ACTIVE)
    )
    metadata.create_run(
        RunRecord(
            user_id=user_id,
            run_id=run_id,
            status=RunStatus.RUNNING,
            upload_id=UploadId.new(),
        )
    )
    metadata.create_job(
        JobRecord(
            user_id=user_id,
            run_id=run_id,
            job_id=job_id,
            status=JobStatus.RUNNING,
        )
    )

    repo = ObjectStoreArtifactRepository(
        metadata_store=metadata,
        object_store=objects,
        job_id=job_id,
    )

    ref = repo.save_artifact(
        user_id=user_id,
        run_id=run_id,
        stage=ArtifactStage.TRANSLATOR,
        artifact_type=ArtifactType.ENGLISH_TRANSLATION,
        artifact_id=artifact_id,
        payload={"full_text_english": "translated text"},
        markdown="# Translation\n\ntranslated text",
    )

    assert ref.artifact_id == artifact_id
    assert ref.has_markdown is True
    record = metadata.get_artifact(user_id=user_id, artifact_id=artifact_id)
    assert record is not None
    assert record.user_id == user_id
    assert record.run_id == run_id
    assert record.stage == ArtifactStage.TRANSLATOR
    assert record.artifact_type == ArtifactType.ENGLISH_TRANSLATION
    assert record.has_markdown is True
    assert objects.get_stream(f"artifacts/{artifact_id}.json").read()
    assert objects.get_stream(f"artifacts/{artifact_id}.md").read()
    chunks = metadata.list_artifact_document_chunks(artifact_id=artifact_id)
    assert len(chunks) == 1
    assert chunks[0].text_content == "translated text"


def test_repository_loads_artifact_content_by_safe_format():
    from urdu_pipeline.infrastructure.artifacts import ObjectStoreArtifactRepository

    metadata = InMemoryMetadataStore()
    objects = InMemoryObjectStore()
    user_id = UserId.new()
    run_id = RunId.new()
    artifact_id = ArtifactId.new()
    metadata.create_user(
        UserRecord(user_id=user_id, username="alice", status=UserStatus.ACTIVE)
    )
    metadata.create_run(
        RunRecord(user_id=user_id, run_id=run_id, status=RunStatus.SUCCEEDED)
    )
    metadata.record_artifact(
        ArtifactRecord(
            user_id=user_id,
            run_id=run_id,
            artifact_id=artifact_id,
            stage=ArtifactStage.ARTICLE_GENERATOR,
            artifact_type=ArtifactType.FINAL_ARTICLE,
            has_markdown=True,
        )
    )
    objects.put_stream(f"artifacts/{artifact_id}.json", BytesIO(b'{"ok": true}'))
    objects.put_stream(f"artifacts/{artifact_id}.md", BytesIO(b"# Article"))

    repo = ObjectStoreArtifactRepository(
        metadata_store=metadata,
        object_store=objects,
        job_id=JobId.new(),
    )

    assert repo.load_artifact(
        user_id=user_id,
        artifact_id=artifact_id,
        artifact_format="json",
    ) == {"ok": True}
    assert repo.load_artifact(
        user_id=user_id,
        artifact_id=artifact_id,
        artifact_format="markdown",
    ) == "# Article"
