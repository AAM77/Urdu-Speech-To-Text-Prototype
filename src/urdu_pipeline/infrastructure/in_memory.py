"""In-memory adapters for tests and local contract checks."""

from __future__ import annotations

import hashlib
import io
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import BinaryIO, Sequence

from urdu_pipeline.application.ports import (
    ArtifactRecord,
    JobRecord,
    MetadataStore,
    MultipartPart,
    MultipartUpload,
    ObjectInfo,
    ObjectMetadata,
    ObjectStore,
    RunRecord,
    ServiceIdentityRecord,
    SignedUrl,
    UploadRecord,
    UserRecord,
)
from urdu_pipeline.domain import ArtifactId, JobId, RunId, ServiceIdentityId, UploadId, UserId


@dataclass(frozen=True)
class _StoredObject:
    payload: bytes
    info: ObjectInfo


@dataclass(frozen=True)
class _PendingMultipartUpload:
    upload: MultipartUpload
    metadata: ObjectMetadata | None


def _validate_object_key(key: str) -> str:
    if not isinstance(key, str) or not key:
        raise ValueError("object key must be a non-empty string.")
    if key.startswith("/") or key.endswith("/") or "\\" in key or "//" in key:
        raise ValueError("object key must be a relative slash-separated key.")
    parts = key.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError("object key must not contain traversal segments.")
    return key


def _validate_prefix(prefix: str) -> str:
    if not isinstance(prefix, str) or not prefix:
        raise ValueError("object prefix must be a non-empty string.")
    probe = prefix[:-1] if prefix.endswith("/") else prefix
    _validate_object_key(probe)
    return prefix


def _object_info(
    *,
    key: str,
    payload: bytes,
    metadata: ObjectMetadata | None,
) -> ObjectInfo:
    checksum = hashlib.sha256(payload).hexdigest()
    return ObjectInfo(
        key=key,
        size_bytes=len(payload),
        etag=checksum,
        content_type=metadata.content_type if metadata else None,
        checksum_sha256=metadata.checksum_sha256 if metadata else None,
        user_metadata=dict(metadata.user_metadata) if metadata else {},
    )


class InMemoryObjectStore:
    """ObjectStore implementation backed by process memory."""

    def __init__(self) -> None:
        self._objects: dict[str, _StoredObject] = {}
        self._multipart_uploads: dict[str, _PendingMultipartUpload] = {}

    def put_stream(
        self,
        key: str,
        body: BinaryIO,
        *,
        metadata: ObjectMetadata | None = None,
    ) -> ObjectInfo:
        safe_key = _validate_object_key(key)
        payload = body.read()
        info = _object_info(key=safe_key, payload=payload, metadata=metadata)
        self._objects[safe_key] = _StoredObject(payload=payload, info=info)
        return info

    def get_stream(self, key: str) -> BinaryIO:
        safe_key = _validate_object_key(key)
        return io.BytesIO(self._objects[safe_key].payload)

    def head_object(self, key: str) -> ObjectInfo:
        safe_key = _validate_object_key(key)
        return self._objects[safe_key].info

    def create_signed_upload_url(
        self,
        key: str,
        *,
        expires_in: timedelta,
        metadata: ObjectMetadata | None = None,
    ) -> SignedUrl:
        safe_key = _validate_object_key(key)
        return SignedUrl(
            url=f"memory://upload/{safe_key}",
            method="PUT",
            expires_at=datetime.now(tz=timezone.utc) + expires_in,
        )

    def create_signed_download_url(
        self,
        key: str,
        *,
        expires_in: timedelta,
        filename: str | None = None,
    ) -> SignedUrl:
        safe_key = _validate_object_key(key)
        self.head_object(safe_key)
        return SignedUrl(
            url=f"memory://download/{safe_key}",
            method="GET",
            expires_at=datetime.now(tz=timezone.utc) + expires_in,
        )

    def delete_object(self, key: str) -> None:
        safe_key = _validate_object_key(key)
        self._objects.pop(safe_key, None)

    def list_prefix(self, prefix: str) -> Sequence[ObjectInfo]:
        safe_prefix = _validate_prefix(prefix)
        return [
            self._objects[key].info
            for key in sorted(self._objects)
            if key.startswith(safe_prefix)
        ]

    def delete_prefix(self, prefix: str) -> int:
        safe_prefix = _validate_prefix(prefix)
        keys = [key for key in self._objects if key.startswith(safe_prefix)]
        for key in keys:
            del self._objects[key]
        return len(keys)

    def create_multipart_upload(
        self,
        key: str,
        *,
        metadata: ObjectMetadata | None = None,
    ) -> MultipartUpload:
        safe_key = _validate_object_key(key)
        upload = MultipartUpload(key=safe_key, upload_id=uuid.uuid4().hex)
        self._multipart_uploads[upload.upload_id] = _PendingMultipartUpload(
            upload=upload,
            metadata=metadata,
        )
        return upload

    def create_signed_part_upload_url(
        self,
        upload: MultipartUpload,
        *,
        part_number: int,
        expires_in: timedelta,
    ) -> SignedUrl:
        self._require_multipart(upload)
        if part_number <= 0:
            raise ValueError("part_number must be positive.")
        return SignedUrl(
            url=f"memory://multipart/{upload.upload_id}/{part_number}",
            method="PUT",
            expires_at=datetime.now(tz=timezone.utc) + expires_in,
        )

    def complete_multipart_upload(
        self,
        upload: MultipartUpload,
        parts: Sequence[MultipartPart],
    ) -> ObjectInfo:
        pending = self._require_multipart(upload)
        if not parts:
            raise ValueError("multipart upload must include at least one part.")
        payload = b"".join(
            f"{part.part_number}:{part.etag}:{part.size_bytes or 0}\n".encode("utf-8")
            for part in sorted(parts, key=lambda item: item.part_number)
        )
        info = _object_info(
            key=upload.key,
            payload=payload,
            metadata=pending.metadata,
        )
        self._objects[upload.key] = _StoredObject(payload=payload, info=info)
        del self._multipart_uploads[upload.upload_id]
        return info

    def abort_multipart_upload(self, upload: MultipartUpload) -> None:
        self._require_multipart(upload)
        del self._multipart_uploads[upload.upload_id]

    def _require_multipart(self, upload: MultipartUpload) -> _PendingMultipartUpload:
        pending = self._multipart_uploads.get(upload.upload_id)
        if pending is None or pending.upload.key != upload.key:
            raise KeyError(f"multipart upload not found: {upload.upload_id}")
        return pending


class InMemoryMetadataStore:
    """MetadataStore implementation backed by process memory."""

    def __init__(self) -> None:
        self._users: dict[UserId, UserRecord] = {}
        self._service_identities: dict[ServiceIdentityId, ServiceIdentityRecord] = {}
        self._uploads: dict[UploadId, UploadRecord] = {}
        self._runs: dict[RunId, RunRecord] = {}
        self._jobs: dict[JobId, JobRecord] = {}
        self._artifacts: dict[ArtifactId, ArtifactRecord] = {}

    def create_user(self, record: UserRecord) -> None:
        self._users[record.user_id] = record

    def get_user(self, user_id: UserId) -> UserRecord | None:
        return self._users.get(user_id)

    def create_service_identity(self, record: ServiceIdentityRecord) -> None:
        self._service_identities[record.service_identity_id] = record

    def get_service_identity(
        self,
        service_identity_id: ServiceIdentityId,
    ) -> ServiceIdentityRecord | None:
        return self._service_identities.get(service_identity_id)

    def create_upload(self, record: UploadRecord) -> None:
        self._require_user(record.user_id)
        self._uploads[record.upload_id] = record

    def get_upload(self, *, user_id: UserId, upload_id: UploadId) -> UploadRecord | None:
        record = self._uploads.get(upload_id)
        if record is None or record.user_id != user_id:
            return None
        return record

    def create_run(self, record: RunRecord) -> None:
        self._require_user(record.user_id)
        self._runs[record.run_id] = record

    def get_run(self, *, user_id: UserId, run_id: RunId) -> RunRecord | None:
        record = self._runs.get(run_id)
        if record is None or record.user_id != user_id:
            return None
        return record

    def create_job(self, record: JobRecord) -> None:
        self._require_user(record.user_id)
        self._require_run_owner(user_id=record.user_id, run_id=record.run_id)
        self._jobs[record.job_id] = record

    def get_job(self, *, user_id: UserId, job_id: JobId) -> JobRecord | None:
        record = self._jobs.get(job_id)
        if record is None or record.user_id != user_id:
            return None
        return record

    def record_artifact(self, record: ArtifactRecord) -> None:
        self._require_user(record.user_id)
        self._require_run_owner(user_id=record.user_id, run_id=record.run_id)
        self._artifacts[record.artifact_id] = record

    def get_artifact(
        self,
        *,
        user_id: UserId,
        artifact_id: ArtifactId,
    ) -> ArtifactRecord | None:
        record = self._artifacts.get(artifact_id)
        if record is None or record.user_id != user_id:
            return None
        return record

    def _require_user(self, user_id: UserId) -> None:
        if user_id not in self._users:
            raise ValueError(f"user does not exist: {user_id}")

    def _require_run_owner(self, *, user_id: UserId, run_id: RunId) -> None:
        record = self._runs.get(run_id)
        if record is None or record.user_id != user_id:
            raise ValueError(f"run does not exist for user: {run_id}")


__all__ = ["InMemoryMetadataStore", "InMemoryObjectStore"]
