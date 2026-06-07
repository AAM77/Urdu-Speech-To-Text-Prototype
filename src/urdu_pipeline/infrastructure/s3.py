"""S3-compatible object store adapter for MinIO, AWS S3, and R2-like APIs."""

from __future__ import annotations

import io
import re
from datetime import datetime, timedelta, timezone
from typing import Any, BinaryIO, Mapping, Sequence

from urdu_pipeline.application.ports import (
    MultipartPart,
    MultipartUpload,
    ObjectInfo,
    ObjectMetadata,
    SignedUrl,
)

_SAFE_OBJECT_KEY_RE = re.compile(r"^[^\\]+$")
_CHECKSUM_METADATA_KEY = "checksum-sha256"


def _validate_object_key(key: str) -> str:
    if not isinstance(key, str) or not key:
        raise ValueError("object key must be a non-empty string.")
    if (
        key.startswith("/")
        or key.endswith("/")
        or "\\" in key
        or "//" in key
        or not _SAFE_OBJECT_KEY_RE.fullmatch(key)
    ):
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


class S3ObjectStore:
    """ObjectStore implementation backed by an S3-compatible client.

    A boto3 client can be injected for tests. If omitted, boto3 is imported
    lazily so package imports do not require optional object-store dependencies.
    """

    def __init__(
        self,
        *,
        bucket: str,
        client: Any | None = None,
        endpoint_url: str | None = None,
        region_name: str | None = None,
        aws_access_key_id: str | None = None,
        aws_secret_access_key: str | None = None,
        server_side_encryption: str | None = None,
        sse_kms_key_id: str | None = None,
    ) -> None:
        if not bucket:
            raise ValueError("bucket must be non-empty.")
        self.bucket = bucket
        self._encryption_kwargs = _encryption_kwargs(
            server_side_encryption=server_side_encryption,
            sse_kms_key_id=sse_kms_key_id,
        )
        self.client = client or _build_boto3_client(
            endpoint_url=endpoint_url,
            region_name=region_name,
            aws_access_key_id=aws_access_key_id,
            aws_secret_access_key=aws_secret_access_key,
        )

    def put_stream(
        self,
        key: str,
        body: BinaryIO,
        *,
        metadata: ObjectMetadata | None = None,
    ) -> ObjectInfo:
        safe_key = _validate_object_key(key)
        kwargs: dict[str, Any] = {
            "Bucket": self.bucket,
            "Key": safe_key,
            "Body": body,
        }
        kwargs.update(_metadata_to_put_kwargs(metadata))
        kwargs.update(self._encryption_kwargs)
        response = self.client.put_object(**kwargs) or {}
        info = self.head_object(safe_key)
        if info.etag is None and response.get("ETag") is not None:
            return ObjectInfo(
                key=info.key,
                size_bytes=info.size_bytes,
                etag=_clean_etag(response.get("ETag")),
                content_type=info.content_type,
                checksum_sha256=info.checksum_sha256,
                user_metadata=info.user_metadata,
            )
        return info

    def get_stream(self, key: str) -> BinaryIO:
        safe_key = _validate_object_key(key)
        response = self.client.get_object(Bucket=self.bucket, Key=safe_key)
        body = response["Body"]
        if hasattr(body, "read"):
            return io.BytesIO(body.read())
        return io.BytesIO(bytes(body))

    def head_object(self, key: str) -> ObjectInfo:
        safe_key = _validate_object_key(key)
        return _object_info_from_head(
            safe_key,
            self.client.head_object(Bucket=self.bucket, Key=safe_key),
        )

    def create_signed_upload_url(
        self,
        key: str,
        *,
        expires_in: timedelta,
        metadata: ObjectMetadata | None = None,
    ) -> SignedUrl:
        safe_key = _validate_object_key(key)
        params: dict[str, Any] = {"Bucket": self.bucket, "Key": safe_key}
        params.update(_metadata_to_presign_params(metadata))
        params.update(self._encryption_kwargs)
        seconds = _expires_seconds(expires_in)
        url = self.client.generate_presigned_url(
            "put_object",
            Params=params,
            ExpiresIn=seconds,
            HttpMethod="PUT",
        )
        return SignedUrl(
            url=url,
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
        params: dict[str, Any] = {"Bucket": self.bucket, "Key": safe_key}
        if filename:
            params["ResponseContentDisposition"] = (
                f'attachment; filename="{filename.replace(chr(34), "")}"'
            )
        seconds = _expires_seconds(expires_in)
        url = self.client.generate_presigned_url(
            "get_object",
            Params=params,
            ExpiresIn=seconds,
            HttpMethod="GET",
        )
        return SignedUrl(
            url=url,
            method="GET",
            expires_at=datetime.now(tz=timezone.utc) + expires_in,
        )

    def delete_object(self, key: str) -> None:
        safe_key = _validate_object_key(key)
        self.client.delete_object(Bucket=self.bucket, Key=safe_key)

    def list_prefix(self, prefix: str) -> Sequence[ObjectInfo]:
        safe_prefix = _validate_prefix(prefix)
        infos: list[ObjectInfo] = []
        continuation_token: str | None = None
        while True:
            kwargs: dict[str, Any] = {"Bucket": self.bucket, "Prefix": safe_prefix}
            if continuation_token:
                kwargs["ContinuationToken"] = continuation_token
            response = self.client.list_objects_v2(**kwargs)
            for item in response.get("Contents", []):
                key = item["Key"]
                try:
                    infos.append(self.head_object(key))
                except Exception:
                    infos.append(
                        ObjectInfo(
                            key=key,
                            size_bytes=int(item.get("Size") or 0),
                            etag=_clean_etag(item.get("ETag")),
                        )
                    )
            if not response.get("IsTruncated"):
                break
            continuation_token = response.get("NextContinuationToken")
            if not continuation_token:
                break
        return infos

    def delete_prefix(self, prefix: str) -> int:
        infos = list(self.list_prefix(prefix))
        for info in infos:
            self.delete_object(info.key)
        return len(infos)

    def create_multipart_upload(
        self,
        key: str,
        *,
        metadata: ObjectMetadata | None = None,
    ) -> MultipartUpload:
        safe_key = _validate_object_key(key)
        kwargs: dict[str, Any] = {"Bucket": self.bucket, "Key": safe_key}
        kwargs.update(_metadata_to_put_kwargs(metadata))
        kwargs.update(self._encryption_kwargs)
        response = self.client.create_multipart_upload(**kwargs)
        return MultipartUpload(key=safe_key, upload_id=response["UploadId"])

    def create_signed_part_upload_url(
        self,
        upload: MultipartUpload,
        *,
        part_number: int,
        expires_in: timedelta,
    ) -> SignedUrl:
        if part_number <= 0:
            raise ValueError("part_number must be positive.")
        safe_key = _validate_object_key(upload.key)
        seconds = _expires_seconds(expires_in)
        url = self.client.generate_presigned_url(
            "upload_part",
            Params={
                "Bucket": self.bucket,
                "Key": safe_key,
                "UploadId": upload.upload_id,
                "PartNumber": part_number,
            },
            ExpiresIn=seconds,
            HttpMethod="PUT",
        )
        return SignedUrl(
            url=url,
            method="PUT",
            expires_at=datetime.now(tz=timezone.utc) + expires_in,
        )

    def complete_multipart_upload(
        self,
        upload: MultipartUpload,
        parts: Sequence[MultipartPart],
    ) -> ObjectInfo:
        if not parts:
            raise ValueError("multipart upload must include at least one part.")
        safe_key = _validate_object_key(upload.key)
        response = self.client.complete_multipart_upload(
            Bucket=self.bucket,
            Key=safe_key,
            UploadId=upload.upload_id,
            MultipartUpload={
                "Parts": [
                    {"PartNumber": part.part_number, "ETag": part.etag}
                    for part in sorted(parts, key=lambda item: item.part_number)
                ]
            },
        ) or {}
        info = self.head_object(safe_key)
        if response.get("ETag") and info.etag != _clean_etag(response.get("ETag")):
            return ObjectInfo(
                key=info.key,
                size_bytes=info.size_bytes,
                etag=_clean_etag(response.get("ETag")),
                content_type=info.content_type,
                checksum_sha256=info.checksum_sha256,
                user_metadata=info.user_metadata,
            )
        return info

    def abort_multipart_upload(self, upload: MultipartUpload) -> None:
        safe_key = _validate_object_key(upload.key)
        self.client.abort_multipart_upload(
            Bucket=self.bucket,
            Key=safe_key,
            UploadId=upload.upload_id,
        )


def _build_boto3_client(
    *,
    endpoint_url: str | None,
    region_name: str | None,
    aws_access_key_id: str | None,
    aws_secret_access_key: str | None,
) -> Any:
    if bool(aws_access_key_id) != bool(aws_secret_access_key):
        raise ValueError("S3 static credentials require both access key and secret key.")
    try:
        import boto3
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "boto3 is required for S3ObjectStore. "
            "Install the object-store extra, for example: pip install -e '.[object-store]'."
        ) from exc
    kwargs: dict[str, Any] = {}
    if endpoint_url:
        kwargs["endpoint_url"] = endpoint_url
    if region_name:
        kwargs["region_name"] = region_name
    if aws_access_key_id and aws_secret_access_key:
        kwargs["aws_access_key_id"] = aws_access_key_id
        kwargs["aws_secret_access_key"] = aws_secret_access_key
    return boto3.client("s3", **kwargs)


def _encryption_kwargs(
    *,
    server_side_encryption: str | None,
    sse_kms_key_id: str | None,
) -> dict[str, str]:
    if not server_side_encryption and not sse_kms_key_id:
        return {}
    if server_side_encryption not in {"AES256", "aws:kms"}:
        raise ValueError("server_side_encryption must be 'AES256' or 'aws:kms'.")
    if sse_kms_key_id and server_side_encryption != "aws:kms":
        raise ValueError("SSEKMSKeyId requires server_side_encryption='aws:kms'.")
    kwargs = {"ServerSideEncryption": server_side_encryption}
    if sse_kms_key_id:
        kwargs["SSEKMSKeyId"] = sse_kms_key_id
    return kwargs


def _metadata_to_put_kwargs(metadata: ObjectMetadata | None) -> dict[str, Any]:
    if metadata is None:
        return {}
    kwargs: dict[str, Any] = {}
    if metadata.content_type:
        kwargs["ContentType"] = metadata.content_type
    converted = _metadata_to_s3_metadata(metadata)
    if converted:
        kwargs["Metadata"] = converted
    return kwargs


def _metadata_to_presign_params(metadata: ObjectMetadata | None) -> dict[str, Any]:
    if metadata is None:
        return {}
    params: dict[str, Any] = {}
    if metadata.content_type:
        params["ContentType"] = metadata.content_type
    converted = _metadata_to_s3_metadata(metadata)
    if converted:
        params["Metadata"] = converted
    return params


def _metadata_to_s3_metadata(metadata: ObjectMetadata) -> dict[str, str]:
    converted = {str(key): str(value) for key, value in metadata.user_metadata.items()}
    if metadata.checksum_sha256:
        converted[_CHECKSUM_METADATA_KEY] = metadata.checksum_sha256
    return converted


def _object_info_from_head(key: str, response: Mapping[str, Any]) -> ObjectInfo:
    metadata = {str(key): str(value) for key, value in dict(response.get("Metadata") or {}).items()}
    checksum = metadata.pop(_CHECKSUM_METADATA_KEY, None)
    return ObjectInfo(
        key=key,
        size_bytes=int(response.get("ContentLength") or 0),
        etag=_clean_etag(response.get("ETag")),
        content_type=response.get("ContentType"),
        checksum_sha256=checksum,
        user_metadata=metadata,
    )


def _clean_etag(value: object) -> str | None:
    if value is None:
        return None
    return str(value).strip('"')


def _expires_seconds(expires_in: timedelta) -> int:
    seconds = int(expires_in.total_seconds())
    if seconds <= 0:
        raise ValueError("expires_in must be positive.")
    return seconds


__all__ = ["S3ObjectStore"]
