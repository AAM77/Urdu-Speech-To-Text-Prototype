"""Shared schema base types."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

StageName = Literal[
    "chunker",
    "transcriber",
    "transcript_reconciler",
    "translator",
    "article_generator",
    "english_chunk_transcriber",
]

ARTIFACT_TYPES = {
    "chunker": "chunk_manifest",
    "transcriber": "raw_urdu_transcript",
    "transcript_reconciler": "reconciled_urdu_transcript",
    "translator": "english_translation",
    "article_generator": "final_article",
    "english_chunk_transcriber": "raw_am_english_transcript",
}

SCHEMA_VERSION = "1.0"


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


class ArtifactBase(BaseModel):
    """Base type for every saved artifact JSON.

    Subclasses set `artifact_type` to a unique string so we can identify the
    file independently of its filename.
    """

    model_config = ConfigDict(extra="forbid")

    artifact_type: str
    schema_version: str = Field(default=SCHEMA_VERSION)
    created_at: datetime = Field(default_factory=_utcnow)
