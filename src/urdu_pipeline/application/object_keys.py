"""Provider-neutral object key construction.

Object keys are storage addresses, not display names. Original and sanitized
filenames stay in metadata and signed-download headers, never in these keys.
"""

from __future__ import annotations

import re

from urdu_pipeline.domain import ArtifactId, ArtifactStage, RunId, UploadId, UserId

_SEGMENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
_AUDIO_EXT_RE = re.compile(r"^[a-z0-9]{1,16}$")


def _safe_segment(field: str, value: str) -> str:
    if not isinstance(value, str) or not _SEGMENT_RE.fullmatch(value):
        raise ValueError(
            f"{field} must be a non-empty opaque segment containing only "
            "letters, numbers, underscores, and hyphens."
        )
    return value


def _audio_ext(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("audio_ext must be a string extension.")
    ext = value.strip().lower()
    if ext.startswith("."):
        ext = ext[1:]
    if not _AUDIO_EXT_RE.fullmatch(ext):
        raise ValueError(
            "audio_ext must be a simple extension such as 'mp3' or '.wav', "
            "not a filename or path."
        )
    return ext


class ObjectKeyBuilder:
    """Build opaque object keys for the provider-neutral storage layout."""

    def upload_source(self, *, user_id: UserId, upload_id: UploadId) -> str:
        return f"tmp/users/{user_id}/uploads/{upload_id}/source"

    def run_input_source(self, *, user_id: UserId, run_id: RunId) -> str:
        return f"tmp/users/{user_id}/runs/{run_id}/input/source"

    def run_chunk(
        self,
        *,
        user_id: UserId,
        run_id: RunId,
        chunk_id: str,
        audio_ext: str,
    ) -> str:
        safe_chunk_id = _safe_segment("chunk_id", chunk_id)
        safe_ext = _audio_ext(audio_ext)
        return f"tmp/users/{user_id}/runs/{run_id}/chunks/{safe_chunk_id}.{safe_ext}"

    def artifact_json(
        self,
        *,
        user_id: UserId,
        run_id: RunId,
        stage: ArtifactStage,
        artifact_id: ArtifactId,
    ) -> str:
        return self._artifact(
            user_id=user_id,
            run_id=run_id,
            stage=stage,
            artifact_id=artifact_id,
            suffix="json",
        )

    def artifact_markdown(
        self,
        *,
        user_id: UserId,
        run_id: RunId,
        stage: ArtifactStage,
        artifact_id: ArtifactId,
    ) -> str:
        return self._artifact(
            user_id=user_id,
            run_id=run_id,
            stage=stage,
            artifact_id=artifact_id,
            suffix="md",
        )

    def cache_entry(self, *, user_id: UserId, scope: str, cache_key: str) -> str:
        safe_scope = _safe_segment("scope", scope)
        safe_cache_key = _safe_segment("cache_key", cache_key)
        return f"cache/users/{user_id}/{safe_scope}/{safe_cache_key}.json"

    def _artifact(
        self,
        *,
        user_id: UserId,
        run_id: RunId,
        stage: ArtifactStage,
        artifact_id: ArtifactId,
        suffix: str,
    ) -> str:
        stage_value = ArtifactStage(stage).value
        return (
            f"artifacts/users/{user_id}/runs/{run_id}/"
            f"{stage_value}/{artifact_id}/artifact.{suffix}"
        )


__all__ = ["ObjectKeyBuilder"]
