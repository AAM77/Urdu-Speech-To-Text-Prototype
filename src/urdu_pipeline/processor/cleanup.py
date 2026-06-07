"""Processor resource cleanup — Step 5.3.2.

Provides three functions for cleaning up the ephemeral resources created
during a single processor run attempt:

``cleanup_run_tmp_objects``
    Deletes all temporary objects for a run from the object store.  These are
    the chunk files uploaded under ``tmp/users/{user_id}/runs/{run_id}/``.
    On a normal completion (success or fatal failure) these are no longer needed
    and should be removed to control storage costs.

``cleanup_workspace``
    Removes the local run workspace directory.  The workspace holds the
    downloaded audio file, local chunk copies, and any scratch files.  It is
    per-attempt and should always be cleaned after every attempt.

``cleanup_after_run``
    Orchestrates the two primitives based on the run outcome:

    * ``is_retry=False`` (success or fatal/permanent failure):
        Delete both the ``tmp/`` objects and the workspace.
    * ``is_retry=True`` (transient failure, retry pending):
        Preserve the ``tmp/`` objects so that idempotent stage functions can
        reuse already-uploaded chunks on the next attempt (see
        ``processor/idempotency.py``).  The workspace is always cleaned
        because each retry allocates its own workspace.

    Workspace cleanup is guaranteed via a ``finally`` block: even when
    ``delete_prefix`` fails (e.g. the object store is unavailable), the
    workspace is still removed from the local filesystem.
"""

from __future__ import annotations

from urdu_pipeline.application.object_keys import ObjectKeyBuilder
from urdu_pipeline.application.ports.services import JobRecord
from urdu_pipeline.application.ports.storage import ObjectStore, RunWorkspace

_DEFAULT_KEY_BUILDER = ObjectKeyBuilder()


def cleanup_run_tmp_objects(
    job_record: JobRecord,
    *,
    object_store: ObjectStore,
    key_builder: ObjectKeyBuilder | None = None,
) -> int:
    """Delete all ``tmp/`` objects for the run from the object store.

    Parameters
    ──────────
    job_record
        The job whose temporary objects should be deleted.  Only objects under
        ``tmp/users/{user_id}/runs/{run_id}/`` are affected.
    object_store
        The object store to delete from.
    key_builder
        Optional ``ObjectKeyBuilder`` instance.  The default instance is used
        when not provided.

    Returns
    ───────
    The number of objects deleted as reported by ``object_store.delete_prefix``.
    """
    keys = key_builder or _DEFAULT_KEY_BUILDER
    prefix = f"tmp/users/{job_record.user_id}/runs/{job_record.run_id}/"
    return object_store.delete_prefix(prefix)


def cleanup_workspace(workspace: RunWorkspace) -> None:
    """Remove the local run workspace directory.

    Delegates to ``workspace.cleanup()``, which removes the workspace root
    (or its scratch subdirectory, depending on the workspace implementation).
    Safe to call when the directory no longer exists.

    Parameters
    ──────────
    workspace
        The ``RunWorkspace`` whose local directory should be cleaned up.
    """
    workspace.cleanup()


def cleanup_after_run(
    job_record: JobRecord,
    *,
    workspace: RunWorkspace,
    object_store: ObjectStore,
    is_retry: bool,
    key_builder: ObjectKeyBuilder | None = None,
) -> None:
    """Clean up resources after a run attempt.

    The cleanup strategy depends on whether the processor will retry:

    * ``is_retry=False`` — success or permanent/fatal failure.
        Both temporary object store objects and the local workspace are removed.
    * ``is_retry=True`` — transient failure, another attempt will be scheduled.
        Temporary object store objects are preserved so that idempotent stage
        functions can detect already-completed chunks and skip re-uploading.
        The workspace is still cleaned because each retry creates its own.

    Workspace cleanup is unconditional: if the object store deletion raises,
    the workspace is still removed before the exception propagates.

    Parameters
    ──────────
    job_record
        The job whose resources should be cleaned up.
    workspace
        The local workspace to remove.
    object_store
        The object store from which to remove temporary objects (when not
        retrying).
    is_retry
        ``True`` if the job will be retried (transient failure).
        ``False`` for success or fatal/permanent failures.
    key_builder
        Optional ``ObjectKeyBuilder`` instance.
    """
    try:
        if not is_retry:
            cleanup_run_tmp_objects(
                job_record, object_store=object_store, key_builder=key_builder
            )
    finally:
        cleanup_workspace(workspace)


__all__ = ["cleanup_run_tmp_objects", "cleanup_workspace", "cleanup_after_run"]
