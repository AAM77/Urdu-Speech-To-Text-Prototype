"""Tests for processor cleanup — Step 5.3.2.

Design under test
─────────────────
Two focused functions plus one orchestrating function in
``src/urdu_pipeline/processor/cleanup.py``:

``cleanup_run_tmp_objects(job_record, *, object_store, key_builder=None) -> int``
    Deletes all temporary objects for a run from object storage by calling
    ``object_store.delete_prefix`` with the run's ``tmp/`` prefix
    (``tmp/users/{user_id}/runs/{run_id}/``).  Returns the number of deleted
    objects as reported by the store.

``cleanup_workspace(workspace) -> None``
    Removes the entire workspace root directory from the local filesystem via
    ``shutil.rmtree(workspace.root, ignore_errors=True)``.  Safe to call even
    when the directory does not exist (``ignore_errors=True`` swallows the error).

``cleanup_after_run(job_record, *, workspace, object_store, is_retry, key_builder=None) -> None``
    Orchestrates the two primitives according to the run outcome:

    * **Success or fatal failure** (``is_retry=False``):
        Remove both the ``tmp/`` objects and the local workspace.
    * **Transient failure / retry pending** (``is_retry=True``):
        Preserve the ``tmp/`` objects so that the idempotent stage functions
        can reuse already-uploaded chunks on the next attempt.  The local
        workspace is ALWAYS cleaned (each attempt allocates its own workspace).
    * **Workspace cleanup is always performed** (``finally`` block):
        Even when ``delete_prefix`` raises an exception, the local workspace
        is still removed.
"""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

import pytest

from urdu_pipeline.application.ports.services import JobRecord
from urdu_pipeline.domain import JobId, JobStatus, RunId, UserId
from urdu_pipeline.infrastructure.in_memory import InMemoryObjectStore
from urdu_pipeline.processor.cleanup import (
    cleanup_after_run,
    cleanup_run_tmp_objects,
    cleanup_workspace,
)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _uid() -> UserId:
    return UserId.new()


def _rid() -> RunId:
    return RunId.new()


def _job(user_id: UserId | None = None, run_id: RunId | None = None) -> JobRecord:
    return JobRecord(
        user_id=user_id or _uid(),
        run_id=run_id or _rid(),
        job_id=JobId.new(),
        status=JobStatus.RUNNING,
    )


@dataclass
class _FakeWorkspace:
    """Minimal RunWorkspace for testing workspace cleanup."""

    root: Path
    cleanup_calls: int = field(default=0, init=False)

    def ensure(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    def input_path(self, relative_path: str) -> Path:  # pragma: no cover
        return self.root / "input" / relative_path

    def chunk_path(self, relative_path: str) -> Path:  # pragma: no cover
        return self.root / "chunks" / relative_path

    def scratch_path(self, relative_path: str) -> Path:  # pragma: no cover
        return self.root / "scratch" / relative_path

    def cleanup(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)
        self.cleanup_calls += 1


@dataclass
class _SpyObjectStore:
    """ObjectStore that records ``delete_prefix`` calls and returns a fixed count."""

    delete_prefix_calls: list[str] = field(default_factory=list)
    delete_prefix_return: int = 0
    raise_on_delete: Exception | None = None

    def delete_prefix(self, prefix: str) -> int:
        self.delete_prefix_calls.append(prefix)
        if self.raise_on_delete is not None:
            raise self.raise_on_delete
        return self.delete_prefix_return

    # Stubs for protocol compliance (unused in these tests)
    def put_object(self, key, data, **kw): ...  # pragma: no cover
    def get_object_stream(self, key): ...  # pragma: no cover
    def create_signed_upload_url(self, key, **kw): ...  # pragma: no cover
    def create_signed_download_url(self, key, **kw): ...  # pragma: no cover
    def delete_object(self, key): ...  # pragma: no cover
    def list_prefix(self, prefix): ...  # pragma: no cover
    def create_multipart_upload(self, key, **kw): ...  # pragma: no cover
    def create_signed_part_upload_url(self, upload, **kw): ...  # pragma: no cover
    def complete_multipart_upload(self, upload, parts): ...  # pragma: no cover
    def abort_multipart_upload(self, upload): ...  # pragma: no cover
    def object_exists(self, key) -> bool: ...  # pragma: no cover


# ── cleanup_run_tmp_objects ───────────────────────────────────────────────────


def test_cleanup_run_tmp_objects_calls_delete_prefix_with_run_prefix():
    job = _job()
    store = _SpyObjectStore(delete_prefix_return=5)
    count = cleanup_run_tmp_objects(job, object_store=store)
    assert len(store.delete_prefix_calls) == 1
    prefix = store.delete_prefix_calls[0]
    assert str(job.user_id) in prefix
    assert str(job.run_id) in prefix
    assert prefix.startswith("tmp/")


def test_cleanup_run_tmp_objects_returns_deleted_count():
    job = _job()
    store = _SpyObjectStore(delete_prefix_return=7)
    count = cleanup_run_tmp_objects(job, object_store=store)
    assert count == 7


def test_cleanup_run_tmp_objects_returns_zero_when_nothing_deleted():
    job = _job()
    store = _SpyObjectStore(delete_prefix_return=0)
    count = cleanup_run_tmp_objects(job, object_store=store)
    assert count == 0


def test_cleanup_run_tmp_objects_prefix_scoped_to_run():
    """Two runs must have different prefixes so one cleanup doesn't affect the other."""
    uid = _uid()
    job_a = _job(user_id=uid)
    job_b = _job(user_id=uid)
    store_a = _SpyObjectStore()
    store_b = _SpyObjectStore()
    cleanup_run_tmp_objects(job_a, object_store=store_a)
    cleanup_run_tmp_objects(job_b, object_store=store_b)
    assert store_a.delete_prefix_calls[0] != store_b.delete_prefix_calls[0]


def test_cleanup_run_tmp_objects_with_real_in_memory_store():
    """Integration check: InMemoryObjectStore.delete_prefix removes the right objects."""
    import io
    job = _job()
    store = InMemoryObjectStore()
    prefix = f"tmp/users/{job.user_id}/runs/{job.run_id}/"
    store.put_stream(f"{prefix}chunks/chunk_0001.mp3", io.BytesIO(b"data1"))
    store.put_stream(f"{prefix}chunks/chunk_0002.mp3", io.BytesIO(b"data2"))
    store.put_stream("tmp/other/key", io.BytesIO(b"unrelated"))

    count = cleanup_run_tmp_objects(job, object_store=store)

    assert count == 2
    # After deletion, listing the prefix returns nothing
    assert len(store.list_prefix(prefix)) == 0
    # Unrelated key still present
    assert len(store.list_prefix("tmp/other/")) == 1


# ── cleanup_workspace ─────────────────────────────────────────────────────────


def test_cleanup_workspace_removes_directory():
    with tempfile.TemporaryDirectory() as tmpdir:
        ws_root = Path(tmpdir) / "run_workspace"
        ws_root.mkdir()
        (ws_root / "audio.mp3").write_bytes(b"audio")
        workspace = _FakeWorkspace(root=ws_root)
        cleanup_workspace(workspace)
        assert not ws_root.exists()


def test_cleanup_workspace_safe_when_directory_missing():
    workspace = _FakeWorkspace(root=Path("/tmp/nonexistent_workspace_12345"))
    cleanup_workspace(workspace)  # must not raise


def test_cleanup_workspace_calls_workspace_cleanup_method():
    with tempfile.TemporaryDirectory() as tmpdir:
        ws_root = Path(tmpdir) / "run_ws"
        ws_root.mkdir()
        workspace = _FakeWorkspace(root=ws_root)
        cleanup_workspace(workspace)
        assert workspace.cleanup_calls == 1


# ── cleanup_after_run — success / fatal failure (is_retry=False) ──────────────


def test_cleanup_after_run_removes_tmp_objects_on_success():
    job = _job()
    store = _SpyObjectStore()
    with tempfile.TemporaryDirectory() as tmpdir:
        ws_root = Path(tmpdir) / "ws"
        ws_root.mkdir()
        workspace = _FakeWorkspace(root=ws_root)
        cleanup_after_run(job, workspace=workspace, object_store=store, is_retry=False)
    assert len(store.delete_prefix_calls) == 1


def test_cleanup_after_run_removes_workspace_on_success():
    job = _job()
    store = _SpyObjectStore()
    with tempfile.TemporaryDirectory() as tmpdir:
        ws_root = Path(tmpdir) / "ws"
        ws_root.mkdir()
        workspace = _FakeWorkspace(root=ws_root)
        cleanup_after_run(job, workspace=workspace, object_store=store, is_retry=False)
    assert workspace.cleanup_calls == 1


def test_cleanup_after_run_removes_both_on_fatal_failure():
    """is_retry=False covers fatal failures as well as success."""
    job = _job()
    store = _SpyObjectStore()
    with tempfile.TemporaryDirectory() as tmpdir:
        ws_root = Path(tmpdir) / "ws"
        ws_root.mkdir()
        workspace = _FakeWorkspace(root=ws_root)
        cleanup_after_run(job, workspace=workspace, object_store=store, is_retry=False)
    assert store.delete_prefix_calls, "tmp objects must be deleted on fatal failure"
    assert workspace.cleanup_calls == 1


# ── cleanup_after_run — transient failure (is_retry=True) ────────────────────


def test_cleanup_after_run_preserves_tmp_objects_on_retry():
    """On transient failure, tmp/ objects must be preserved for the next attempt."""
    job = _job()
    store = _SpyObjectStore()
    with tempfile.TemporaryDirectory() as tmpdir:
        ws_root = Path(tmpdir) / "ws"
        ws_root.mkdir()
        workspace = _FakeWorkspace(root=ws_root)
        cleanup_after_run(job, workspace=workspace, object_store=store, is_retry=True)
    assert not store.delete_prefix_calls, "delete_prefix must NOT be called on retry"


def test_cleanup_after_run_still_removes_workspace_on_retry():
    """Local workspace is always cleaned, even when object store objects are preserved."""
    job = _job()
    store = _SpyObjectStore()
    with tempfile.TemporaryDirectory() as tmpdir:
        ws_root = Path(tmpdir) / "ws"
        ws_root.mkdir()
        workspace = _FakeWorkspace(root=ws_root)
        cleanup_after_run(job, workspace=workspace, object_store=store, is_retry=True)
    assert workspace.cleanup_calls == 1


# ── cleanup_after_run — workspace cleaned even when delete_prefix raises ──────


def test_cleanup_after_run_workspace_cleaned_even_if_delete_prefix_raises():
    """Workspace cleanup must execute even when object store deletion fails."""
    job = _job()
    store = _SpyObjectStore(raise_on_delete=RuntimeError("S3 unavailable"))
    with tempfile.TemporaryDirectory() as tmpdir:
        ws_root = Path(tmpdir) / "ws"
        ws_root.mkdir()
        workspace = _FakeWorkspace(root=ws_root)
        with pytest.raises(RuntimeError, match="S3 unavailable"):
            cleanup_after_run(job, workspace=workspace, object_store=store, is_retry=False)
    assert workspace.cleanup_calls == 1, "workspace must be cleaned even after store error"
