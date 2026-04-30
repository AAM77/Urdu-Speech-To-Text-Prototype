"""Audio chunk + chunk manifest schemas."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from urdu_pipeline.schemas.base import ArtifactBase
from urdu_pipeline.schemas.manifests import ArtifactManifest


class AudioChunk(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chunk_id: str
    source_audio_hash: str
    chunk_index: int = Field(ge=1)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    duration_ms: int = Field(ge=0)
    overlap_before_ms: int = Field(default=0, ge=0)
    overlap_after_ms: int = Field(default=0, ge=0)
    file_path: str
    file_hash: str
    file_size_bytes: int = Field(default=0, ge=0)
    audio_format: str = "mp3"


class ChunkManifestArtifact(ArtifactBase):
    artifact_type: str = "chunk_manifest"
    source_audio_path: str
    source_audio_hash: str
    source_audio_duration_ms: int = Field(ge=0)
    source_audio_format: str = "mp3"
    chunk_length_seconds: int = Field(gt=0)
    overlap_seconds: int = Field(ge=0)
    chunks: list[AudioChunk]
    manifest: ArtifactManifest
