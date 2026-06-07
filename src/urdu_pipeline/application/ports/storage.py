"""Storage, workspace, and artifact ports.

These protocols define provider-neutral boundaries. Concrete filesystem,
S3-compatible, R2, or other adapters belong outside application code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, BinaryIO, Literal, Mapping, Protocol, Sequence, runtime_checkable

from pydantic import BaseModel

from urdu_pipeline.domain import ArtifactId, ArtifactStage, ArtifactType, RunId, UserId

ArtifactFormat = Literal["json", "markdown"]


@dataclass(frozen=True)
class ObjectMetadata:
    """Metadata supplied when writing an object."""

    content_type: str | None = None
    checksum_sha256: str | None = None
    user_metadata: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ObjectInfo:
    """Object metadata returned by an object store."""

    key: str
    size_bytes: int
    etag: str | None = None
    content_type: str | None = None
    checksum_sha256: str | None = None
    user_metadata: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class SignedUrl:
    """A short-lived URL for direct object-store access."""

    url: str
    method: str
    expires_at: datetime
    headers: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class MultipartUpload:
    """Object-store multipart upload handle."""

    key: str
    upload_id: str


@dataclass(frozen=True)
class MultipartPart:
    """Completed multipart upload part."""

    part_number: int
    etag: str
    size_bytes: int | None = None


@dataclass(frozen=True)
class ArtifactReference:
    """Durable artifact reference returned by an artifact repository."""

    user_id: UserId
    run_id: RunId
    stage: ArtifactStage
    artifact_type: ArtifactType
    artifact_id: ArtifactId
    has_markdown: bool = False


@runtime_checkable
class ObjectStore(Protocol):
    """Provider-neutral object storage interface."""

    def put_stream(
        self,
        key: str,
        body: BinaryIO,
        *,
        metadata: ObjectMetadata | None = None,
    ) -> ObjectInfo: ...

    def get_stream(self, key: str) -> BinaryIO: ...

    def head_object(self, key: str) -> ObjectInfo: ...

    def create_signed_upload_url(
        self,
        key: str,
        *,
        expires_in: timedelta,
        metadata: ObjectMetadata | None = None,
    ) -> SignedUrl: ...

    def create_signed_download_url(
        self,
        key: str,
        *,
        expires_in: timedelta,
        filename: str | None = None,
    ) -> SignedUrl: ...

    def delete_object(self, key: str) -> None: ...

    def list_prefix(self, prefix: str) -> Sequence[ObjectInfo]: ...

    def delete_prefix(self, prefix: str) -> int: ...

    def create_multipart_upload(
        self,
        key: str,
        *,
        metadata: ObjectMetadata | None = None,
    ) -> MultipartUpload: ...

    def create_signed_part_upload_url(
        self,
        upload: MultipartUpload,
        *,
        part_number: int,
        expires_in: timedelta,
    ) -> SignedUrl: ...

    def complete_multipart_upload(
        self,
        upload: MultipartUpload,
        parts: Sequence[MultipartPart],
    ) -> ObjectInfo: ...

    def abort_multipart_upload(self, upload: MultipartUpload) -> None: ...


@runtime_checkable
class RunWorkspace(Protocol):
    """Local scratch workspace for one run."""

    root: Path

    def ensure(self) -> None: ...

    def input_path(self, relative_path: str) -> Path: ...

    def chunk_path(self, relative_path: str) -> Path: ...

    def scratch_path(self, relative_path: str) -> Path: ...

    def cleanup(self) -> None: ...


@runtime_checkable
class ArtifactSink(Protocol):
    """Stage-facing artifact writer compatible with the existing stages."""

    def write_artifact(self, model: BaseModel, filename: str) -> Path: ...

    def write_markdown(self, text: str, filename: str) -> Path: ...


@runtime_checkable
class ArtifactRepository(Protocol):
    """Durable artifact storage interface."""

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
    ) -> ArtifactReference: ...

    def get_artifact_metadata(
        self,
        *,
        user_id: UserId,
        artifact_id: ArtifactId,
    ) -> ArtifactReference: ...

    def load_artifact(
        self,
        *,
        user_id: UserId,
        artifact_id: ArtifactId,
        artifact_format: ArtifactFormat,
    ) -> Mapping[str, Any] | str: ...

    def list_run_artifacts(
        self,
        *,
        user_id: UserId,
        run_id: RunId,
    ) -> Sequence[ArtifactReference]: ...


__all__ = [
    "ArtifactFormat",
    "ArtifactReference",
    "ArtifactRepository",
    "ArtifactSink",
    "MultipartPart",
    "MultipartUpload",
    "ObjectInfo",
    "ObjectMetadata",
    "ObjectStore",
    "RunWorkspace",
    "SignedUrl",
]
