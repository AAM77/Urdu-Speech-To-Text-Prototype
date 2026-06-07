"""Processor chunker stage — Step 5.2.2.

Orchestrates the chunking step inside the background processor:

1. Delegates audio splitting to a ``chunker_fn`` callable (keeps ffmpeg
   invocation out of this module so it remains unit-testable).
2. Uploads every produced chunk file to ``ObjectStore`` under the opaque key
   produced by ``ObjectKeyBuilder.run_chunk``.
3. Persists the ``ChunkManifestArtifact`` as a JSON payload via
   ``ArtifactRepository.save_artifact``.
4. Returns the ``ArtifactReference`` from the repository.

Object-key layout (under ``tmp/``)
───────────────────────────────────
Chunk keys follow the canonical ``ObjectKeyBuilder.run_chunk`` layout:

    tmp/users/{user_id}/runs/{run_id}/chunks/{chunk_id}.{audio_ext}

The ``tmp/`` prefix marks these as transient scratch objects — they can be
cleaned up once the transcription stage has consumed them.  Original filenames
and workspace paths never appear in the keys.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from urdu_pipeline.application.object_keys import ObjectKeyBuilder
from urdu_pipeline.application.ports.services import JobRecord
from urdu_pipeline.application.ports.storage import ArtifactReference, ArtifactRepository, ObjectStore, RunWorkspace
from urdu_pipeline.domain import ArtifactId, ArtifactStage, ArtifactType
from urdu_pipeline.schemas.chunks import ChunkManifestArtifact

_DEFAULT_KEY_BUILDER = ObjectKeyBuilder()


def run_chunker_stage(
    job_record: JobRecord,
    audio_path: Path,
    *,
    workspace: RunWorkspace,
    object_store: ObjectStore,
    artifact_repo: ArtifactRepository,
    chunker_fn: Callable[[Path], ChunkManifestArtifact],
    key_builder: ObjectKeyBuilder | None = None,
) -> ArtifactReference:
    """Run chunking, upload chunk files, persist the manifest artifact.

    Parameters
    ──────────
    job_record
        Current job — provides ``user_id`` and ``run_id`` for key scoping and
        artifact ownership.
    audio_path
        Local path to the materialized audio file (output of
        ``materialize_upload``).
    workspace
        Run workspace; provides ``root`` so chunk file paths can be resolved.
    object_store
        Object storage adapter for uploading chunk files.
    artifact_repo
        Artifact repository for persisting the chunk manifest.
    chunker_fn
        Callable that receives the audio path and returns a
        ``ChunkManifestArtifact`` with local chunk file paths populated.
        In production this wraps ``ChunkerStage.run``; in tests it is a fake.
    key_builder
        Optional ``ObjectKeyBuilder`` override (defaults to the module-level
        singleton).  Tests may pass a custom instance to verify key structure.

    Returns
    ───────
    ArtifactReference
        The reference returned by ``artifact_repo.save_artifact``.

    Raises
    ──────
    Any exception raised by ``chunker_fn`` propagates unchanged.  In particular,
    ``FatalJobError`` propagates so ``claim_and_run`` marks the job terminal.
    """
    keys = key_builder or _DEFAULT_KEY_BUILDER

    manifest = chunker_fn(audio_path)

    for chunk in manifest.chunks:
        chunk_file = workspace.root / chunk.file_path
        chunk_key = keys.run_chunk(
            user_id=job_record.user_id,
            run_id=job_record.run_id,
            chunk_id=chunk.chunk_id,
            audio_ext=chunk.audio_format,
        )
        with chunk_file.open("rb") as body:
            object_store.put_stream(chunk_key, body)

    artifact_id = ArtifactId.new()
    return artifact_repo.save_artifact(
        user_id=job_record.user_id,
        run_id=job_record.run_id,
        stage=ArtifactStage.CHUNKER,
        artifact_type=ArtifactType.CHUNK_MANIFEST,
        artifact_id=artifact_id,
        payload=manifest.model_dump(),
    )


__all__ = ["run_chunker_stage"]
