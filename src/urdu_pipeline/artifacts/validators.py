"""Artifact loading + cross-stage validation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from urdu_pipeline.schemas.articles import ArticleArtifact
from urdu_pipeline.schemas.chunks import ChunkManifestArtifact
from urdu_pipeline.schemas.transcripts import (
    RawAmericanEnglishTranscriptArtifact,
    RawTranscriptArtifact,
    ReconciledTranscriptArtifact,
)
from urdu_pipeline.schemas.translations import EnglishTranslationArtifact

# Maps `artifact_type` -> Pydantic model.
_ARTIFACT_MODELS = {
    "chunk_manifest": ChunkManifestArtifact,
    "raw_urdu_transcript": RawTranscriptArtifact,
    "raw_am_english_transcript": RawAmericanEnglishTranscriptArtifact,
    "reconciled_urdu_transcript": ReconciledTranscriptArtifact,
    "english_translation": EnglishTranslationArtifact,
    "final_article": ArticleArtifact,
}


class ArtifactValidationError(ValueError):
    """Raised when a file does not match its claimed/required artifact type."""


def detect_artifact_type(payload: dict) -> str | None:
    return payload.get("artifact_type")


def _load_payload(source: Path | str | dict) -> dict:
    if isinstance(source, dict):
        return source
    p = Path(source)
    if not p.exists():
        raise ArtifactValidationError(f"Artifact file not found: {p}")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ArtifactValidationError(f"Artifact is not valid JSON: {p} ({e})") from e
    if not isinstance(data, dict):
        raise ArtifactValidationError(f"Artifact root must be an object, got {type(data).__name__}.")
    return data


def load_and_validate_artifact(source: Path | str | dict) -> Any:
    """Load JSON, look up the right schema, and return a parsed model."""
    payload = _load_payload(source)
    artifact_type = detect_artifact_type(payload)
    if not artifact_type:
        raise ArtifactValidationError("Artifact is missing required 'artifact_type' field.")
    model = _ARTIFACT_MODELS.get(artifact_type)
    if model is None:
        raise ArtifactValidationError(f"Unknown artifact_type: {artifact_type!r}.")
    try:
        return model.model_validate(payload)
    except ValidationError as e:
        raise ArtifactValidationError(
            f"Artifact failed schema validation for type {artifact_type!r}: {e}"
        ) from e


def require_artifact_type(source: Path | str | dict, expected_type: str) -> Any:
    """Load + validate, then assert the artifact_type matches the expected one."""
    artifact = load_and_validate_artifact(source)
    if artifact.artifact_type != expected_type:
        raise ArtifactValidationError(
            f"Wrong artifact type: expected {expected_type!r}, got {artifact.artifact_type!r}."
        )
    return artifact
