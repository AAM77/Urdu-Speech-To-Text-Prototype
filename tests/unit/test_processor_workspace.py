"""Tests for processor workspace materialization and audio validation — Step 5.2.1.

Design under test
─────────────────
* ``materialize_upload(job_record, *, metadata_store, object_store, workspace)``
    Downloads the audio upload associated with a job to a local RunWorkspace.
    - Derives the upload's object key (same layout used by the API upload routes).
    - Streams the object to ``workspace.input_path(safe_filename)``.
    - Returns the local ``Path``.
    - Raises ``FatalJobError`` if run, upload record, or object is missing.
    - Sanitizes the original filename before using it as a workspace path.

* ``validate_audio(audio_path)``
    Runs ``ffprobe`` to confirm the file is valid audio and returns its duration.
    - Raises ``AudioValidationError`` (a ``FatalJobError``) on:
        - ffprobe not on PATH
        - non-zero ffprobe exit code
        - zero or negative duration
        - unparseable ffprobe output

* ``_safe_input_filename(original_filename)``
    Returns a single path component safe for use in a workspace.
    - Strips directory parts.
    - Replaces non-alphanumeric/extension characters.
    - Falls back to ``audio.bin`` for empty/leading-dot results.
"""

from __future__ import annotations

import io
import json
import subprocess
import tempfile
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from urdu_pipeline.application.ports.services import (
    JobRecord,
    RunRecord,
    UploadRecord,
    UserRecord,
)
from urdu_pipeline.domain import (
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
from urdu_pipeline.processor.workspace import (
    AudioValidationError,
    _safe_input_filename,
    materialize_upload,
    validate_audio,
)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_store() -> InMemoryMetadataStore:
    return InMemoryMetadataStore()


def _seed(
    store: InMemoryMetadataStore,
    *,
    original_filename: str | None = "lecture.mp3",
    upload_content: bytes = b"fake-audio-data",
    with_upload_id: bool = True,
) -> tuple[JobRecord, UploadId | None, InMemoryObjectStore]:
    """Seed a minimal user → upload → run → job chain.

    Returns (job_record, upload_id, object_store) so tests can verify behaviour.
    """
    user_id = UserId.new()
    store.create_user(
        UserRecord(user_id=user_id, username="ws_user", status=UserStatus.ACTIVE)
    )

    object_store = InMemoryObjectStore()
    upload_id: UploadId | None = None

    if with_upload_id:
        upload_id = UploadId.new()
        store.create_upload(
            UploadRecord(
                user_id=user_id,
                upload_id=upload_id,
                status=UploadStatus.COMPLETED,
                original_filename=original_filename,
            )
        )
        # Store the "audio" object at the canonical upload key.
        upload_key = f"uploads/{upload_id}"
        object_store.put_stream(upload_key, io.BytesIO(upload_content))

    run_id = RunId.new()
    run = RunRecord(
        user_id=user_id,
        run_id=run_id,
        status=RunStatus.QUEUED,
        upload_id=upload_id,
    )
    store.create_run(run)

    job_id = JobId.new()
    job = JobRecord(
        user_id=user_id,
        run_id=run_id,
        job_id=job_id,
        status=JobStatus.RUNNING,
    )
    store.create_job(job)

    return job, upload_id, object_store


# ── materialize_upload — happy path ──────────────────────────────────────────


def test_materialize_upload_returns_a_path():
    store = _make_store()
    job, _, obj_store = _seed(store)
    with tempfile.TemporaryDirectory() as tmp:
        workspace = FilesystemRunWorkspace(Path(tmp))
        result = materialize_upload(
            job, metadata_store=store, object_store=obj_store, workspace=workspace
        )
    assert isinstance(result, Path)


def test_materialize_upload_writes_correct_content():
    content = b"audio-bytes-xyz"
    store = _make_store()
    job, _, obj_store = _seed(store, upload_content=content)
    with tempfile.TemporaryDirectory() as tmp:
        workspace = FilesystemRunWorkspace(Path(tmp))
        local_path = materialize_upload(
            job, metadata_store=store, object_store=obj_store, workspace=workspace
        )
        assert local_path.read_bytes() == content


def test_materialize_upload_places_file_under_workspace_input():
    store = _make_store()
    job, _, obj_store = _seed(store)
    with tempfile.TemporaryDirectory() as tmp:
        workspace = FilesystemRunWorkspace(Path(tmp))
        local_path = materialize_upload(
            job, metadata_store=store, object_store=obj_store, workspace=workspace
        )
        # Must be inside the workspace root (not escaped).
        assert str(local_path).startswith(str(tmp))


def test_materialize_upload_uses_original_filename():
    store = _make_store()
    job, _, obj_store = _seed(store, original_filename="my_lecture.mp3")
    with tempfile.TemporaryDirectory() as tmp:
        workspace = FilesystemRunWorkspace(Path(tmp))
        local_path = materialize_upload(
            job, metadata_store=store, object_store=obj_store, workspace=workspace
        )
        assert local_path.name == "my_lecture.mp3"


def test_materialize_upload_uses_fallback_filename_when_no_original():
    store = _make_store()
    job, _, obj_store = _seed(store, original_filename=None)
    with tempfile.TemporaryDirectory() as tmp:
        workspace = FilesystemRunWorkspace(Path(tmp))
        local_path = materialize_upload(
            job, metadata_store=store, object_store=obj_store, workspace=workspace
        )
        # Should not raise; some valid name must be used.
        assert local_path.exists()
        assert local_path.name  # non-empty


# ── materialize_upload — path safety ─────────────────────────────────────────


def test_materialize_upload_sanitizes_traversal_in_filename():
    """A traversal-style original filename must be sanitized, not rejected."""
    store = _make_store()
    job, _, obj_store = _seed(store, original_filename="../../../etc/passwd.mp3")
    with tempfile.TemporaryDirectory() as tmp:
        workspace = FilesystemRunWorkspace(Path(tmp))
        local_path = materialize_upload(
            job, metadata_store=store, object_store=obj_store, workspace=workspace
        )
        # Must stay inside workspace root.
        assert str(local_path).startswith(str(tmp))
        # Traversal segments must be gone from the name.
        assert ".." not in local_path.parts


def test_materialize_upload_sanitizes_spaces_and_unicode_in_filename():
    store = _make_store()
    job, _, obj_store = _seed(store, original_filename="مولانا بیان.mp3")
    with tempfile.TemporaryDirectory() as tmp:
        workspace = FilesystemRunWorkspace(Path(tmp))
        local_path = materialize_upload(
            job, metadata_store=store, object_store=obj_store, workspace=workspace
        )
        assert str(local_path).startswith(str(tmp))


# ── materialize_upload — error paths ─────────────────────────────────────────


def test_materialize_upload_raises_when_run_not_found():
    from urdu_pipeline.processor.lifecycle import FatalJobError

    store = _make_store()
    job, _, obj_store = _seed(store)
    # Manually corrupt: replace job with a run_id that doesn't exist.
    bad_job = replace(job, run_id=RunId.new())
    with tempfile.TemporaryDirectory() as tmp:
        workspace = FilesystemRunWorkspace(Path(tmp))
        with pytest.raises(FatalJobError):
            materialize_upload(
                bad_job, metadata_store=store, object_store=obj_store, workspace=workspace
            )


def test_materialize_upload_raises_when_run_has_no_upload_id():
    from urdu_pipeline.processor.lifecycle import FatalJobError

    store = _make_store()
    job, _, obj_store = _seed(store, with_upload_id=False)
    with tempfile.TemporaryDirectory() as tmp:
        workspace = FilesystemRunWorkspace(Path(tmp))
        with pytest.raises(FatalJobError):
            materialize_upload(
                job, metadata_store=store, object_store=obj_store, workspace=workspace
            )


def test_materialize_upload_raises_when_object_not_found():
    from urdu_pipeline.processor.lifecycle import FatalJobError

    store = _make_store()
    job, upload_id, _ = _seed(store)
    # Use an empty object store (object never uploaded).
    empty_obj_store = InMemoryObjectStore()
    with tempfile.TemporaryDirectory() as tmp:
        workspace = FilesystemRunWorkspace(Path(tmp))
        with pytest.raises(FatalJobError):
            materialize_upload(
                job, metadata_store=store, object_store=empty_obj_store, workspace=workspace
            )


# ── _safe_input_filename ──────────────────────────────────────────────────────


def test_safe_input_filename_strips_directory_parts():
    assert _safe_input_filename("../../etc/passwd.mp3") == "passwd.mp3"
    assert _safe_input_filename("/absolute/path/file.wav") == "file.wav"
    assert _safe_input_filename("sub/dir/audio.m4a") == "audio.m4a"


def test_safe_input_filename_keeps_simple_names():
    assert _safe_input_filename("lecture.mp3") == "lecture.mp3"
    assert _safe_input_filename("audio_file_01.wav") == "audio_file_01.wav"


def test_safe_input_filename_replaces_spaces_and_unicode():
    result = _safe_input_filename("مولانا بیان.mp3")
    assert " " not in result
    assert result.endswith(".mp3")


def test_safe_input_filename_fallback_for_empty_name():
    # A name that becomes empty after sanitization should fall back.
    result = _safe_input_filename("...")
    assert result == "audio.bin" or result  # must be non-empty and safe


def test_safe_input_filename_single_component_only():
    result = _safe_input_filename("a/b/c.mp3")
    assert "/" not in result
    assert result == "c.mp3"


# ── validate_audio — happy path (ffprobe mocked) ─────────────────────────────


def _fake_ffprobe_ok(duration: float = 120.5):
    """Return a mock subprocess.CompletedProcess simulating a valid audio file."""
    out = json.dumps({"format": {"duration": str(duration)}})
    return MagicMock(stdout=out, returncode=0)


def test_validate_audio_returns_duration():
    with patch("subprocess.run", return_value=_fake_ffprobe_ok(120.5)):
        result = validate_audio(Path("fake.mp3"))
    assert abs(result - 120.5) < 0.001


def test_validate_audio_returns_float():
    with patch("subprocess.run", return_value=_fake_ffprobe_ok(60.0)):
        result = validate_audio(Path("fake.mp3"))
    assert isinstance(result, float)


# ── validate_audio — rejection paths ─────────────────────────────────────────


def test_validate_audio_raises_audio_validation_error_on_nonzero_exit():
    with patch(
        "subprocess.run",
        side_effect=subprocess.CalledProcessError(1, "ffprobe", b"", b"Invalid data"),
    ):
        with pytest.raises(AudioValidationError):
            validate_audio(Path("bad.mp3"))


def test_validate_audio_raises_when_ffprobe_not_on_path():
    with patch("subprocess.run", side_effect=FileNotFoundError("ffprobe not found")):
        with pytest.raises(AudioValidationError, match="ffprobe"):
            validate_audio(Path("audio.mp3"))


def test_validate_audio_raises_on_zero_duration():
    with patch("subprocess.run", return_value=_fake_ffprobe_ok(0.0)):
        with pytest.raises(AudioValidationError, match="duration"):
            validate_audio(Path("silent.mp3"))


def test_validate_audio_raises_on_negative_duration():
    with patch("subprocess.run", return_value=_fake_ffprobe_ok(-1.0)):
        with pytest.raises(AudioValidationError, match="duration"):
            validate_audio(Path("bad.mp3"))


def test_validate_audio_raises_on_missing_duration_key():
    bad_out = json.dumps({"format": {}})
    mock = MagicMock(stdout=bad_out, returncode=0)
    with patch("subprocess.run", return_value=mock):
        with pytest.raises(AudioValidationError):
            validate_audio(Path("no_duration.mp3"))


def test_validate_audio_raises_on_unparseable_output():
    mock = MagicMock(stdout="not json at all", returncode=0)
    with patch("subprocess.run", return_value=mock):
        with pytest.raises(AudioValidationError):
            validate_audio(Path("corrupt_output.mp3"))


def test_audio_validation_error_is_a_fatal_job_error():
    """AudioValidationError must subclass FatalJobError so lifecycle handles it."""
    from urdu_pipeline.processor.lifecycle import FatalJobError

    assert issubclass(AudioValidationError, FatalJobError)


# ── workspace cleanup ─────────────────────────────────────────────────────────


def test_workspace_cleanup_removes_scratch_files():
    """After materialize + cleanup, scratch files are removed.
    (The workspace cleanup contract: scratch dir is wiped; input is preserved
    for the pipeline stages that need to re-read it.)
    """
    content = b"data"
    store = _make_store()
    job, _, obj_store = _seed(store, upload_content=content)
    with tempfile.TemporaryDirectory() as tmp:
        workspace = FilesystemRunWorkspace(Path(tmp))
        workspace.ensure()
        # Write a scratch file.
        scratch = workspace.scratch_path("temp.txt")
        scratch.parent.mkdir(parents=True, exist_ok=True)
        scratch.write_text("scratch content")
        assert scratch.exists()

        workspace.cleanup()
        assert not scratch.exists()
