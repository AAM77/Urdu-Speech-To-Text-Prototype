"""Final-article schemas."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from urdu_pipeline.schemas.base import ArtifactBase
from urdu_pipeline.schemas.manifests import ArtifactManifest


class Article(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    subtitle: str | None = None
    body_markdown: str
    warnings: list[str] = Field(default_factory=list)


class ArticleArtifact(ArtifactBase):
    artifact_type: str = "final_article"
    source_translation_artifact_id: str
    article: Article
    manifest: ArtifactManifest
