"""Tests for processor chunker stage integration — Step 5.2.2.

Design under test
─────────────────
``run_chunker_stage(job_record, audio_path, *, workspace, object_store,
                   artifact_repo, chunker_fn, key_builder=None)``

    Orchestrates the chunking stage inside the processor:

    1. Calls ``chunker_fn(audio_path)`` to produce chunk files and a
       ``ChunkManifestArtifact`` (delegates actual ffmpeg work).
    2. Uploads each chunk file to ``object_store`` under the opaque key returned
       by ``ObjectKeyBuilder.run_chunk(user_id, run_id, chunk_id, audio_ext)``.
    3. Persists the manifest (as JSON payload) via ``artifact_repo.save_artifact``
       with ``stage=CHUNKER`` and ``artifact_type=CHUNK_MANIFEST``.
    4. Returns the ``ArtifactReference`` from the repository.

Design decisions
────────────────
* ``chunker_fn`` is injectable so tests can provide a fake that creates real
  local files without invoking ffmpeg.
* Chunk object keys are opaque, scoped under ``tmp/``, and never contain
  original filenames or user-visible paths.
* Zero chunks is a valid outcome (very short audio) — the manifest is still
  persisted; no object-store uploads occur.
* If ``chunker_fn`` raises ``FatalJobError``, it propagates unchanged so
  ``claim_and_run`` marks the job terminal.
"""

from __future__ import annotations

import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import pytest

from urdu_pipeline.application.ports.storage import ArtifactFormat, ArtifactReference
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
from urdu_pipeline.infrastructure.filesystem import FilesystemRunWorkspace
from urdu_pipeline.infrastructure.in_memory import (
    InMemoryMetadataStore,
    InMemoryObjectStore,
)
from urdu_pipeline.application.ports.services import (
    JobRecord,
    RunRecord,
    UploadRecord,
    UserRecord,
)
from urdu_pipeline.processor.lifecycle import FatalJobError
from urdu_pipeline.processor.chunker import run_chunker_stage
from urdu_pipeline.schemas.chunks import AudioChunk, ChunkManifestArtifact
from urdu_pipeline.schemas.manifests import ArtifactManifest


# ── Minimal in-test ArtifactRepository ────────────────────────────────────────


@dataclass
class _FakeArtifactRepo:
    """Minimal ArtifactRepository that records save_artifact calls for assertions."""

    saved: list[dict[str, Any]] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.saved is None:
            self.saved = []

    def save_artifact(
        self,
        *,
        user_id: UserId,
        run_id: RunId,
        stage: ArtifactStage,
        artifact_type: ArtifactType,
        artifact_id: ArtifactId,
        payload: Mapping[str, Any],
        markdown: str | None = None,
    ) -> ArtifactReference:
        self.saved.append(
            {
                "user_id": user_id,
                "run_id": run_id,
                "stage": stage,
                "artifact_type": artifact_type,
                "artifact_id": artifact_id,
                "payload": dict(payload),
                "markdown": markdown,
            }
        )
        return ArtifactReference(
            user_id=user_id,
            run_id=run_id,
            stage=stage,
            artifact_type=artifact_type,
            artifact_id=artifact_id,
            has_markdown=markdown is not None,
        )

    def get_artifact_metadata(
        self,
        *,
        user_id: UserId,
        artifact_id: ArtifactId,
    ) -> ArtifactReference:  # pragma: no cover
        raise NotImplementedError

    def load_artifact(
        self,
        *,
        user_id: UserId,
        artifact_id: ArtifactId,
        artifact_format: ArtifactFormat,
    ) -> Mapping[str, Any] | str:  # pragma: no cover
        raise NotImplementedError

    def list_run_artifacts(
        self,
        *,
        user_id: UserId,
        run_id: RunId,
    ) -> Sequence[ArtifactReference]:  # pragma: no cover
        return []


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_job(user_id: UserId | None = None) -> JobRecord:
    uid = user_id or UserId.new()
    return JobRecord(
        user_id=uid,
        run_id=RunId.new(),
        job_id=JobId.new(),
        status=JobStatus.RUNNING,
    )


def _build_fake_chunker(
    workspace_root: Path,
    *,
    num_chunks: int = 2,
    audio_format: str = "mp3",
) -> "Callable[[Path], ChunkManifestArtifact]":  # type: ignore[name-defined]
    """Return a chunker_fn that creates real chunk files and a manifest.

    Files are written to ``workspace_root/chunks/`` so that the production
    code can open them for upload.
    """

    def _chunker(audio_path: Path) -> ChunkManifestArtifact:
        chunks_dir = workspace_root / "chunks"
        chunks_dir.mkdir(parents=True, exist_ok=True)

        chunks: list[AudioChunk] = []
        for i in range(1, num_chunks + 1):
            chunk_id = f"chunk_{i:04d}"
            filename = f"{chunk_id}.{audio_format}"
            chunk_path = chunks_dir / filename
            chunk_path.write_bytes(b"fake-audio-chunk-data")
            chunks.append(
                AudioChunk(
                    chunk_id=chunk_id,
                    source_audio_hash="src_hash_abc",
                    chunk_index=i,
                    start_ms=(i - 1) * 10_000,
                    end_ms=i * 10_000,
                    duration_ms=10_000,
                    file_path=f"chunks/{filename}",
                    file_hash=f"chunk_hash_{i:04d}",
                    file_size_bytes=21,
                    audio_format=audio_format,
                )
            )

        manifest = ArtifactManifest(
            artifact_id=f"chunk_manifest_{uuid.uuid4().hex[:12]}",
            stage_name="chunker",
            artifact_type="chunk_manifest",
            source_input_hash="src_hash_abc",
            chunk_length_seconds=10,
            overlap_seconds=0,
            cache_hit=False,
        )
        return ChunkManifestArtifact(
            source_audio_path="input/lecture.mp3",
            source_audio_hash="src_hash_abc",
            source_audio_duration_ms=num_chunks * 10_000,
            source_audio_format=audio_format,
            chunk_length_seconds=10,
            overlap_seconds=0,
            chunks=chunks,
            manifest=manifest,
        )

    return _chunker


# ── Return value tests ────────────────────────────────────────────────────────


def test_run_chunker_stage_returns_artifact_reference():
    job = _make_job()
    with tempfile.TemporaryDirectory() as tmp:
        workspace = FilesystemRunWorkspace(Path(tmp))
        chunker_fn = _build_fake_chunker(Path(tmp))
        result = run_chunker_stage(
            job,
            Path(tmp) / "input" / "audio.mp3",
            workspace=workspace,
            object_store=InMemoryObjectStore(),
            artifact_repo=_FakeArtifactRepo(),
            chunker_fn=chunker_fn,
        )
    assert isinstance(result, ArtifactReference)


def test_run_chunker_stage_returns_chunker_stage_and_type():
    job = _make_job()
    with tempfile.TemporaryDirectory() as tmp:
        workspace = FilesystemRunWorkspace(Path(tmp))
        chunker_fn = _build_fake_chunker(Path(tmp))
        result = run_chunker_stage(
            job,
            Path(tmp) / "input" / "audio.mp3",
            workspace=workspace,
            object_store=InMemoryObjectStore(),
            artifact_repo=_FakeArtifactRepo(),
            chunker_fn=chunker_fn,
        )
    assert result.stage == ArtifactStage.CHUNKER
    assert result.artifact_type == ArtifactType.CHUNK_MANIFEST


# ── Manifest persistence tests ────────────────────────────────────────────────


def test_run_chunker_stage_saves_manifest_to_artifact_repo():
    job = _make_job()
    repo = _FakeArtifactRepo()
    with tempfile.TemporaryDirectory() as tmp:
        workspace = FilesystemRunWorkspace(Path(tmp))
        chunker_fn = _build_fake_chunker(Path(tmp), num_chunks=2)
        run_chunker_stage(
            job,
            Path(tmp) / "input" / "audio.mp3",
            workspace=workspace,
            object_store=InMemoryObjectStore(),
            artifact_repo=repo,
            chunker_fn=chunker_fn,
        )
    assert len(repo.saved) == 1
    saved = repo.saved[0]
    assert saved["stage"] == ArtifactStage.CHUNKER
    assert saved["artifact_type"] == ArtifactType.CHUNK_MANIFEST


def test_run_chunker_stage_manifest_payload_contains_chunks():
    job = _make_job()
    repo = _FakeArtifactRepo()
    with tempfile.TemporaryDirectory() as tmp:
        workspace = FilesystemRunWorkspace(Path(tmp))
        chunker_fn = _build_fake_chunker(Path(tmp), num_chunks=3)
        run_chunker_stage(
            job,
            Path(tmp) / "input" / "audio.mp3",
            workspace=workspace,
            object_store=InMemoryObjectStore(),
            artifact_repo=repo,
            chunker_fn=chunker_fn,
        )
    payload = repo.saved[0]["payload"]
    assert "chunks" in payload
    assert len(payload["chunks"]) == 3


def test_run_chunker_stage_manifest_user_and_run_ids_match_job():
    job = _make_job()
    repo = _FakeArtifactRepo()
    with tempfile.TemporaryDirectory() as tmp:
        workspace = FilesystemRunWorkspace(Path(tmp))
        chunker_fn = _build_fake_chunker(Path(tmp))
        run_chunker_stage(
            job,
            Path(tmp) / "input" / "audio.mp3",
            workspace=workspace,
            object_store=InMemoryObjectStore(),
            artifact_repo=repo,
            chunker_fn=chunker_fn,
        )
    saved = repo.saved[0]
    assert saved["user_id"] == job.user_id
    assert saved["run_id"] == job.run_id


# ── Object-store upload tests ─────────────────────────────────────────────────


def test_run_chunker_stage_uploads_all_chunks():
    job = _make_job()
    obj_store = InMemoryObjectStore()
    with tempfile.TemporaryDirectory() as tmp:
        workspace = FilesystemRunWorkspace(Path(tmp))
        chunker_fn = _build_fake_chunker(Path(tmp), num_chunks=3)
        run_chunker_stage(
            job,
            Path(tmp) / "input" / "audio.mp3",
            workspace=workspace,
            object_store=obj_store,
            artifact_repo=_FakeArtifactRepo(),
            chunker_fn=chunker_fn,
        )
    # InMemoryObjectStore exposes _objects dict we can inspect.
    assert len(obj_store._objects) == 3


def test_run_chunker_stage_uploaded_chunk_content_matches_file():
    job = _make_job()
    obj_store = InMemoryObjectStore()
    with tempfile.TemporaryDirectory() as tmp:
        workspace = FilesystemRunWorkspace(Path(tmp))
        chunker_fn = _build_fake_chunker(Path(tmp), num_chunks=1)
        run_chunker_stage(
            job,
            Path(tmp) / "input" / "audio.mp3",
            workspace=workspace,
            object_store=obj_store,
            artifact_repo=_FakeArtifactRepo(),
            chunker_fn=chunker_fn,
        )
    stored_bytes = list(obj_store._objects.values())[0].payload
    assert stored_bytes == b"fake-audio-chunk-data"


def test_run_chunker_stage_chunk_keys_do_not_contain_workspace_path():
    """The local temp-dir path must never leak into the object-store key."""
    job = _make_job()
    obj_store = InMemoryObjectStore()
    with tempfile.TemporaryDirectory() as tmp:
        workspace = FilesystemRunWorkspace(Path(tmp))
        chunker_fn = _build_fake_chunker(Path(tmp), num_chunks=2, audio_format="mp3")
        run_chunker_stage(
            job,
            Path(tmp) / "input" / "audio.mp3",
            workspace=workspace,
            object_store=obj_store,
            artifact_repo=_FakeArtifactRepo(),
            chunker_fn=chunker_fn,
        )
    for key in obj_store._objects:
        assert str(tmp) not in key


def test_run_chunker_stage_chunk_keys_do_not_contain_original_user_filename():
    """The original audio filename must never appear in chunk object keys."""
    job = _make_job()
    obj_store = InMemoryObjectStore()
    original_filename = "my_special_lecture.mp3"
    with tempfile.TemporaryDirectory() as tmp:
        workspace = FilesystemRunWorkspace(Path(tmp))
        audio_path = Path(tmp) / "input" / original_filename
        audio_path.parent.mkdir(parents=True, exist_ok=True)
        audio_path.write_bytes(b"dummy")
        chunker_fn = _build_fake_chunker(Path(tmp), num_chunks=2, audio_format="mp3")
        run_chunker_stage(
            job,
            audio_path,
            workspace=workspace,
            object_store=obj_store,
            artifact_repo=_FakeArtifactRepo(),
            chunker_fn=chunker_fn,
        )
    for key in obj_store._objects:
        assert original_filename not in key
        assert "my_special_lecture" not in key


def test_run_chunker_stage_chunk_keys_scoped_under_tmp():
    job = _make_job()
    obj_store = InMemoryObjectStore()
    with tempfile.TemporaryDirectory() as tmp:
        workspace = FilesystemRunWorkspace(Path(tmp))
        chunker_fn = _build_fake_chunker(Path(tmp), num_chunks=2)
        run_chunker_stage(
            job,
            Path(tmp) / "input" / "audio.mp3",
            workspace=workspace,
            object_store=obj_store,
            artifact_repo=_FakeArtifactRepo(),
            chunker_fn=chunker_fn,
        )
    for key in obj_store._objects:
        assert key.startswith("tmp/")


def test_run_chunker_stage_chunk_keys_include_run_id():
    job = _make_job()
    obj_store = InMemoryObjectStore()
    with tempfile.TemporaryDirectory() as tmp:
        workspace = FilesystemRunWorkspace(Path(tmp))
        chunker_fn = _build_fake_chunker(Path(tmp), num_chunks=1)
        run_chunker_stage(
            job,
            Path(tmp) / "input" / "audio.mp3",
            workspace=workspace,
            object_store=obj_store,
            artifact_repo=_FakeArtifactRepo(),
            chunker_fn=chunker_fn,
        )
    for key in obj_store._objects:
        assert str(job.run_id) in key


# ── Zero-chunk edge case ──────────────────────────────────────────────────────


def test_run_chunker_stage_handles_zero_chunks():
    """Zero chunks (very short audio) is valid — manifest is still saved."""
    job = _make_job()
    repo = _FakeArtifactRepo()
    obj_store = InMemoryObjectStore()
    with tempfile.TemporaryDirectory() as tmp:
        workspace = FilesystemRunWorkspace(Path(tmp))
        chunker_fn = _build_fake_chunker(Path(tmp), num_chunks=0)
        run_chunker_stage(
            job,
            Path(tmp) / "input" / "audio.mp3",
            workspace=workspace,
            object_store=obj_store,
            artifact_repo=repo,
            chunker_fn=chunker_fn,
        )
    # No chunks uploaded.
    assert len(obj_store._objects) == 0
    # But manifest still persisted.
    assert len(repo.saved) == 1
    assert repo.saved[0]["payload"]["chunks"] == []


# ── Error propagation ─────────────────────────────────────────────────────────


def test_run_chunker_stage_propagates_fatal_job_error_from_chunker():
    job = _make_job()

    def _failing_chunker(audio_path: Path) -> ChunkManifestArtifact:
        raise FatalJobError("ffmpeg not available")

    with tempfile.TemporaryDirectory() as tmp:
        workspace = FilesystemRunWorkspace(Path(tmp))
        with pytest.raises(FatalJobError, match="ffmpeg not available"):
            run_chunker_stage(
                job,
                Path(tmp) / "input" / "audio.mp3",
                workspace=workspace,
                object_store=InMemoryObjectStore(),
                artifact_repo=_FakeArtifactRepo(),
                chunker_fn=_failing_chunker,
            )


def test_run_chunker_stage_propagates_unexpected_errors_from_chunker():
    job = _make_job()

    def _bad_chunker(audio_path: Path) -> ChunkManifestArtifact:
        raise RuntimeError("unexpected ffmpeg crash")

    with tempfile.TemporaryDirectory() as tmp:
        workspace = FilesystemRunWorkspace(Path(tmp))
        with pytest.raises(RuntimeError, match="unexpected ffmpeg crash"):
            run_chunker_stage(
                job,
                Path(tmp) / "input" / "audio.mp3",
                workspace=workspace,
                object_store=InMemoryObjectStore(),
                artifact_repo=_FakeArtifactRepo(),
                chunker_fn=_bad_chunker,
            )
