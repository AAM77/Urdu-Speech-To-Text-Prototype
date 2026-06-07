"""Workspace materialization and audio validation for the processor.

Before running any pipeline stage the processor must:

1. Materialize the upload — download the audio file from object storage to a
   local ``RunWorkspace`` scratch area.
2. Validate the audio — run ``ffprobe`` to confirm the file is parseable and
   has a positive duration.

Both operations raise ``FatalJobError`` subclasses on failure so that
``claim_and_run`` (see ``processor.lifecycle``) routes them to the terminal-
failure path immediately, without retrying an unrecoverable input problem.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

from urdu_pipeline.application.ports.services import JobRecord, MetadataStore
from urdu_pipeline.application.ports.storage import ObjectStore, RunWorkspace
from urdu_pipeline.domain import UploadId
from urdu_pipeline.processor.lifecycle import FatalJobError


class AudioValidationError(FatalJobError):
    """The audio file failed ``ffprobe`` validation.

    Raised when ffprobe is absent, exits non-zero, or reports a zero/negative
    duration.  All cases are treated as fatal — the upload is unprocessable
    and retrying will not help.
    """


# ── Object key ────────────────────────────────────────────────────────────────

# This must stay in sync with the key produced by the API upload routes
# (``POST /uploads/init``, ``POST /uploads/direct``, etc.).
# Both the API and the processor derive the key from the opaque upload ID —
# never from a filename or user-supplied value.


def _upload_object_key(upload_id: UploadId) -> str:
    return f"uploads/{upload_id}"


# ── Filename sanitization ─────────────────────────────────────────────────────

_UNSAFE_CHAR_RE = re.compile(r"[^\w.\-]")


def _safe_input_filename(original_filename: str) -> str:
    """Return a single workspace-safe filename from an original upload filename.

    - Strips any directory parts (guards against path-traversal in stored filenames).
    - Replaces non-alphanumeric/extension characters with underscores.
    - Falls back to ``audio.bin`` if the result would be empty or start with a dot.
    """
    name = Path(original_filename).name  # keep only the last component
    name = _UNSAFE_CHAR_RE.sub("_", name)
    if not name or name.startswith("."):
        name = "audio.bin"
    return name


# ── Workspace materialization ─────────────────────────────────────────────────


def materialize_upload(
    job_record: JobRecord,
    *,
    metadata_store: MetadataStore,
    object_store: ObjectStore,
    workspace: RunWorkspace,
) -> Path:
    """Download the audio upload associated with ``job_record`` to ``workspace``.

    Steps
    ─────
    1. Look up the ``RunRecord`` to find the ``upload_id``.
    2. Look up the ``UploadRecord`` to get the original filename.
    3. Derive the object-store key from the upload ID.
    4. Stream the object to ``workspace.input_path(<safe_filename>)``.
    5. Return the local ``Path``.

    Raises
    ──────
    FatalJobError
        If the run, upload record, or object cannot be found.  All failures
        are unrecoverable for the current job (the input data is missing).
    """
    run = metadata_store.get_run(user_id=job_record.user_id, run_id=job_record.run_id)
    if run is None:
        raise FatalJobError(
            f"run not found: {job_record.run_id} (user {job_record.user_id})"
        )
    if run.upload_id is None:
        raise FatalJobError(
            f"run {job_record.run_id} has no associated upload_id; "
            "cannot materialize workspace."
        )

    upload = metadata_store.get_upload(
        user_id=job_record.user_id, upload_id=run.upload_id
    )
    if upload is None:
        raise FatalJobError(
            f"upload record not found: {run.upload_id} (user {job_record.user_id})"
        )

    original_filename = upload.original_filename or f"{upload.upload_id}.audio"
    safe_filename = _safe_input_filename(original_filename)

    workspace.ensure()
    local_path = workspace.input_path(safe_filename)
    local_path.parent.mkdir(parents=True, exist_ok=True)

    object_key = _upload_object_key(upload.upload_id)
    try:
        body = object_store.get_stream(object_key)
    except Exception as exc:
        raise FatalJobError(
            f"audio object not found in object store "
            f"(upload_id={upload.upload_id}, key={object_key}): {exc}"
        ) from exc

    with local_path.open("wb") as dst:
        shutil.copyfileobj(body, dst)

    return local_path


# ── Audio validation ──────────────────────────────────────────────────────────


def validate_audio(audio_path: Path) -> float:
    """Run ``ffprobe`` to validate the audio file and return its duration.

    Parameters
    ──────────
    audio_path
        Absolute or workspace-relative path to the audio file.

    Returns
    ───────
    Duration in seconds (positive float).

    Raises
    ──────
    AudioValidationError
        - ``ffprobe`` is not found on ``PATH``.
        - ``ffprobe`` exits with a non-zero code (corrupt/unsupported file).
        - Duration is zero or negative (silent/empty file).
        - ``ffprobe`` output cannot be parsed.
    """
    try:
        proc = subprocess.run(
            [
                "ffprobe",
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "json",
                str(audio_path),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
    except FileNotFoundError as exc:
        raise AudioValidationError(
            "ffprobe is not available on PATH; "
            "install ffmpeg to enable audio validation."
        ) from exc
    except subprocess.CalledProcessError as exc:
        raise AudioValidationError(
            f"ffprobe rejected the audio file "
            f"(exit {exc.returncode}): {audio_path.name}"
        ) from exc

    try:
        payload = json.loads(proc.stdout or "{}")
        raw = payload.get("format", {}).get("duration", None)
        if raw is None:
            raise AudioValidationError(
                f"ffprobe output missing 'duration' field for {audio_path.name}"
            )
        duration = float(raw)
    except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        raise AudioValidationError(
            f"ffprobe output could not be parsed for {audio_path.name}: {exc}"
        ) from exc

    if duration <= 0.0:
        raise AudioValidationError(
            f"audio file has zero or negative duration ({duration}s): {audio_path.name}"
        )

    return duration


__all__ = [
    "AudioValidationError",
    "materialize_upload",
    "validate_audio",
    "_safe_input_filename",
]
