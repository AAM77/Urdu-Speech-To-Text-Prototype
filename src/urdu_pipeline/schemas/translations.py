"""English translation schema."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from urdu_pipeline.schemas.base import ArtifactBase
from urdu_pipeline.schemas.manifests import ArtifactManifest


class EnglishTranslationSegment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    segment_id: str
    source_segment_id: str
    text_english: str
    preserved_uncertainty: bool = False
    terminology_notes: list[str] = Field(default_factory=list)


class EnglishTranslationArtifact(ArtifactBase):
    artifact_type: str = "english_translation"
    reconciled_transcript_artifact_id: str
    segments: list[EnglishTranslationSegment]
    full_text_english: str
    manifest: ArtifactManifest
