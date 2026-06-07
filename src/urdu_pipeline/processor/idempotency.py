"""Processor idempotency utilities — Step 5.3.1.

Provides two building blocks that make every processor stage safe to retry:

1. ``find_stage_artifact`` — checks whether a stage has already produced a
   durable artifact for the current run.  Stage functions call this at the top
   and return the existing reference without re-running any work.

2. ``stage_usage_key`` — constructs a deterministic string key for a usage
   record.  Passing this key to ``UsageRecord.idempotency_key`` ensures that
   ``InMemoryUsageLedger`` (and future persistent adapters) silently discard
   duplicate records produced by a retry.

Usage pattern in each stage function
──────────────────────────────────────
.. code-block:: python

    existing = artifact_repo.list_run_artifacts(user_id=job.user_id, run_id=job.run_id)
    if found := find_stage_artifact(existing, ArtifactStage.CHUNKER, ArtifactType.CHUNK_MANIFEST):
        return found   # already done — skip cleanly

    # … run the stage …

    usage_ledger.record_usage(UsageRecord(
        …,
        idempotency_key=stage_usage_key(job.run_id, "chunker"),
    ))
"""

from __future__ import annotations

from typing import Sequence

from urdu_pipeline.application.ports.storage import ArtifactReference
from urdu_pipeline.domain import ArtifactStage, ArtifactType, RunId


def find_stage_artifact(
    refs: Sequence[ArtifactReference],
    stage: ArtifactStage,
    artifact_type: ArtifactType,
) -> ArtifactReference | None:
    """Return the first artifact matching ``stage`` and ``artifact_type``, or ``None``.

    Parameters
    ──────────
    refs
        Sequence of ``ArtifactReference`` objects, typically returned by
        ``artifact_repo.list_run_artifacts(user_id=…, run_id=…)``.
    stage
        The stage whose artifact we are looking for (e.g. ``ArtifactStage.CHUNKER``).
    artifact_type
        The artifact type (e.g. ``ArtifactType.CHUNK_MANIFEST``).

    Returns
    ───────
    The first matching ``ArtifactReference``, or ``None`` if the stage has not
    yet produced a durable artifact.
    """
    for ref in refs:
        if ref.stage == stage and ref.artifact_type == artifact_type:
            return ref
    return None


def stage_usage_key(
    run_id: RunId,
    stage_label: str,
    item_id: str | None = None,
) -> str:
    """Return a deterministic idempotency key for a usage record.

    The key encodes the run, stage, and optional item (e.g. chunk ID) so that
    a second call with identical parameters is recognised as a duplicate.

    Parameters
    ──────────
    run_id
        The run this usage record belongs to.
    stage_label
        A stable stage name string (e.g. ``"transcriber"``, ``"translator"``).
    item_id
        Optional sub-item identifier (e.g. chunk ID for per-chunk transcription
        records).  Omit for single-record stages like translation.

    Returns
    ───────
    A colon-separated string suitable for ``UsageRecord.idempotency_key``.
    """
    if item_id is not None:
        return f"{run_id}:{stage_label}:{item_id}"
    return f"{run_id}:{stage_label}"


__all__ = ["find_stage_artifact", "stage_usage_key"]
