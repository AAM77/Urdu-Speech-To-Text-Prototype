"""Durable artifact repository backed by object storage and metadata."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from io import BytesIO
from typing import Any, Mapping, Sequence

from urdu_pipeline.application.ports.services import ArtifactRecord
from urdu_pipeline.application.ports.storage import (
    ArtifactFormat,
    ArtifactReference,
    ObjectMetadata,
    ObjectStore,
)
from urdu_pipeline.domain import ArtifactId, ArtifactStage, ArtifactType, JobId, RunId, UserId
from urdu_pipeline.infrastructure.db.metadata import ArtifactDocumentChunkRecord


class ObjectStoreArtifactRepository:
    """Persist artifact payloads to object storage and safe metadata to DB."""

    def __init__(
        self,
        *,
        metadata_store: Any,
        object_store: ObjectStore,
        job_id: JobId,
    ) -> None:
        self.metadata_store = metadata_store
        self.object_store = object_store
        self.job_id = job_id

    def save_artifact(
        self,
        *,
        user_id: UserId,
        run_id: RunId,
        stage: ArtifactStage,
        artifact_type: ArtifactType,
        artifact_id: ArtifactId,
        payload: Mapping[str, Any],
        markdown: str | None = None,
    ) -> ArtifactReference:
        json_key = _json_key(artifact_id)
        json_payload = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        ).encode("utf-8")
        self.object_store.put_stream(
            json_key,
            BytesIO(json_payload),
            metadata=ObjectMetadata(content_type="application/json"),
        )

        has_markdown = markdown is not None
        if markdown is not None:
            self.object_store.put_stream(
                _markdown_key(artifact_id),
                BytesIO(markdown.encode("utf-8")),
                metadata=ObjectMetadata(content_type="text/markdown; charset=utf-8"),
            )

        record = ArtifactRecord(
            user_id=user_id,
            run_id=run_id,
            artifact_id=artifact_id,
            stage=stage,
            artifact_type=artifact_type,
            has_markdown=has_markdown,
        )
        try:
            self.metadata_store.record_artifact(
                record,
                job_id=self.job_id,
                object_key=json_key,
            )
        except TypeError:
            self.metadata_store.record_artifact(record)

        self._persist_document_chunk(
            user_id=user_id,
            run_id=run_id,
            artifact_id=artifact_id,
            text=_document_text(payload, markdown),
        )
        return ArtifactReference(
            user_id=user_id,
            run_id=run_id,
            stage=stage,
            artifact_type=artifact_type,
            artifact_id=artifact_id,
            has_markdown=has_markdown,
        )

    def get_artifact_metadata(
        self,
        *,
        user_id: UserId,
        artifact_id: ArtifactId,
    ) -> ArtifactReference:
        record = self.metadata_store.get_artifact(
            user_id=user_id,
            artifact_id=artifact_id,
        )
        if record is None:
            raise KeyError(f"artifact not found: {artifact_id}")
        return _reference_from_record(record)

    def load_artifact(
        self,
        *,
        user_id: UserId,
        artifact_id: ArtifactId,
        artifact_format: ArtifactFormat,
    ) -> Mapping[str, Any] | str:
        self.get_artifact_metadata(user_id=user_id, artifact_id=artifact_id)
        if artifact_format == "json":
            raw = self.object_store.get_stream(_json_key(artifact_id)).read()
            return json.loads(raw.decode("utf-8"))
        if artifact_format == "markdown":
            raw = self.object_store.get_stream(_markdown_key(artifact_id)).read()
            return raw.decode("utf-8")
        raise ValueError(f"unsupported artifact format: {artifact_format}")

    def list_run_artifacts(
        self,
        *,
        user_id: UserId,
        run_id: RunId,
    ) -> Sequence[ArtifactReference]:
        return [
            _reference_from_record(record)
            for record in self.metadata_store.list_run_artifacts(
                user_id=user_id,
                run_id=run_id,
            )
        ]

    def _persist_document_chunk(
        self,
        *,
        user_id: UserId,
        run_id: RunId,
        artifact_id: ArtifactId,
        text: str,
    ) -> None:
        if not text or not hasattr(self.metadata_store, "put_artifact_document_chunk"):
            return
        # Keep each row well below the adapter's 256 KB document chunk limit.
        max_chars = 120_000
        for index, start in enumerate(range(0, len(text), max_chars)):
            self.metadata_store.put_artifact_document_chunk(
                ArtifactDocumentChunkRecord(
                    artifact_id=artifact_id,
                    chunk_index=index,
                    user_id=user_id,
                    run_id=run_id,
                    text_content=text[start : start + max_chars],
                    token_count=None,
                    metadata={"source": "artifact_repository"},
                    created_at=datetime.now(tz=timezone.utc),
                )
            )


def _reference_from_record(record: ArtifactRecord) -> ArtifactReference:
    return ArtifactReference(
        user_id=record.user_id,
        run_id=record.run_id,
        stage=record.stage,
        artifact_type=record.artifact_type,
        artifact_id=record.artifact_id,
        has_markdown=record.has_markdown,
    )


def _json_key(artifact_id: ArtifactId) -> str:
    return f"artifacts/{artifact_id}.json"


def _markdown_key(artifact_id: ArtifactId) -> str:
    return f"artifacts/{artifact_id}.md"


def _document_text(payload: Mapping[str, Any], markdown: str | None) -> str:
    for key in ("full_text_english", "full_text_urdu", "body_markdown", "text"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    article = payload.get("article")
    if isinstance(article, Mapping):
        body = article.get("body_markdown")
        if isinstance(body, str):
            return body
    if markdown:
        return markdown
    return json.dumps(payload, ensure_ascii=False, default=str)


__all__ = ["ObjectStoreArtifactRepository"]
