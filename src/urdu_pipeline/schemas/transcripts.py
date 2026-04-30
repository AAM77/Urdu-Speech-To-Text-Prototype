"""Raw + reconciled Urdu transcript schemas."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from urdu_pipeline.schemas.base import ArtifactBase
from urdu_pipeline.schemas.manifests import ArtifactManifest


class RawTranscriptChunk(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chunk_id: str
    chunk_index: int = Field(ge=1)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    text_urdu: str
    uncertainty_markers: list[str] = Field(default_factory=list)
    provider_metadata: dict[str, Any] = Field(default_factory=dict)


class RawTranscriptArtifact(ArtifactBase):
    artifact_type: str = "raw_urdu_transcript"
    source_audio_hash: str
    chunk_manifest_artifact_id: str
    chunks: list[RawTranscriptChunk]
    manifest: ArtifactManifest


class RawAmericanEnglishChunk(BaseModel):
    """Chunk of English audio transcribed as American English prose (parallel to RawTranscriptChunk)."""

    model_config = ConfigDict(extra="forbid")

    chunk_id: str
    chunk_index: int = Field(ge=1)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    text_english: str
    uncertainty_markers: list[str] = Field(default_factory=list)
    provider_metadata: dict[str, Any] = Field(default_factory=dict)


class RawAmericanEnglishTranscriptArtifact(ArtifactBase):
    artifact_type: str = "raw_am_english_transcript"
    source_audio_hash: str
    chunk_manifest_artifact_id: str
    chunks: list[RawAmericanEnglishChunk]
    manifest: ArtifactManifest


class ReconciledSegment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    segment_id: str
    source_chunk_ids: list[str]
    approx_start_ms: int | None = None
    approx_end_ms: int | None = None
    text_urdu: str
    warnings: list[str] = Field(default_factory=list)


class ReconciledTranscriptArtifact(ArtifactBase):
    artifact_type: str = "reconciled_urdu_transcript"
    source_audio_hash: str | None = None
    raw_transcript_artifact_id: str
    segments: list[ReconciledSegment]
    full_text_urdu: str
    manifest: ArtifactManifest
