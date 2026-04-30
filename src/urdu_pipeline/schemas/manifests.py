"""Manifest schema (one manifest per artifact)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from urdu_pipeline.schemas.base import StageName

HumanReviewStatus = Literal["unreviewed", "approved", "needs_review", "rejected"]


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


class ArtifactManifest(BaseModel):
    """Manifest metadata persisted alongside each stage output.

    All fields below are required by the prototype's data-model specification
    in the planning docs. Optional fields are explicitly typed `... | None`.
    """

    model_config = ConfigDict(extra="forbid")

    artifact_id: str
    schema_version: str = "1.0"
    stage_name: StageName
    artifact_type: str
    created_at: datetime = Field(default_factory=_utcnow)
    source_input_hash: str | None = None
    upstream_artifact_ids: list[str] = Field(default_factory=list)
    model_provider: str | None = None
    model_id: str | None = None
    prompt_id: str | None = None
    prompt_version: str | None = None
    chunk_length_seconds: int | None = None
    overlap_seconds: int | None = None
    context_mode: str | None = None
    estimated_cost_usd: float | None = None
    actual_usage: dict[str, Any] | None = None
    cache_hit: bool = False
    checksum: str = ""
    warnings: list[str] = Field(default_factory=list)
    human_review_status: HumanReviewStatus = "unreviewed"
