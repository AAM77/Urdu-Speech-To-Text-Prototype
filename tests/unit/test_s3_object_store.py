"""S3-compatible object store adapter contract tests."""

from __future__ import annotations

import io
import sys
from types import SimpleNamespace
from datetime import timedelta
from typing import Any

import pytest

from urdu_pipeline.application.ports import (
    MultipartPart,
    ObjectMetadata,
    ObjectStore,
)
from urdu_pipeline.infrastructure.s3 import S3ObjectStore


class FakeBody:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    def read(self) -> bytes:
        return self.payload


class FakeS3Client:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], dict[str, Any]] = {}
        self.multipart: dict[str, dict[str, Any]] = {}
        self.put_calls: list[dict[str, Any]] = []
        self.multipart_calls: list[dict[str, Any]] = []
        self.presigned_calls: list[tuple[str, dict[str, Any], int, str | None]] = []

    def put_object(self, **kwargs: Any) -> dict[str, str]:
        self.put_calls.append(dict(kwargs))
        bucket = kwargs["Bucket"]
        key = kwargs["Key"]
        payload = kwargs["Body"].read()
        metadata = dict(kwargs.get("Metadata") or {})
        self.objects[(bucket, key)] = {
            "payload": payload,
            "content_type": kwargs.get("ContentType"),
            "metadata": metadata,
            "etag": f"etag-{len(payload)}",
        }
        return {"ETag": f"etag-{len(payload)}"}

    def get_object(self, **kwargs: Any) -> dict[str, Any]:
        stored = self.objects[(kwargs["Bucket"], kwargs["Key"])]
        return {"Body": FakeBody(stored["payload"])}

    def head_object(self, **kwargs: Any) -> dict[str, Any]:
        stored = self.objects[(kwargs["Bucket"], kwargs["Key"])]
        return {
            "ContentLength": len(stored["payload"]),
            "ETag": stored["etag"],
            "ContentType": stored["content_type"],
            "Metadata": dict(stored["metadata"]),
        }

    def delete_object(self, **kwargs: Any) -> None:
        self.objects.pop((kwargs["Bucket"], kwargs["Key"]), None)

    def list_objects_v2(self, **kwargs: Any) -> dict[str, Any]:
        bucket = kwargs["Bucket"]
        prefix = kwargs["Prefix"]
        contents = [
            {
                "Key": key,
                "Size": len(stored["payload"]),
                "ETag": stored["etag"],
            }
            for (stored_bucket, key), stored in sorted(self.objects.items())
            if stored_bucket == bucket and key.startswith(prefix)
        ]
        return {"Contents": contents, "IsTruncated": False}

    def generate_presigned_url(
        self,
        operation_name: str,
        *,
        Params: dict[str, Any],
        ExpiresIn: int,
        HttpMethod: str | None = None,
    ) -> str:
        self.presigned_calls.append((operation_name, Params, ExpiresIn, HttpMethod))
        return f"https://objects.example/{operation_name}/{Params['Key']}"

    def create_multipart_upload(self, **kwargs: Any) -> dict[str, str]:
        self.multipart_calls.append(dict(kwargs))
        upload_id = f"upload-{len(self.multipart) + 1}"
        self.multipart[upload_id] = {
            "bucket": kwargs["Bucket"],
            "key": kwargs["Key"],
            "metadata": dict(kwargs.get("Metadata") or {}),
            "content_type": kwargs.get("ContentType"),
        }
        return {"UploadId": upload_id}

    def complete_multipart_upload(self, **kwargs: Any) -> dict[str, str]:
        upload = self.multipart.pop(kwargs["UploadId"])
        parts = kwargs["MultipartUpload"]["Parts"]
        payload = b"".join(
            f"{part['PartNumber']}:{part['ETag']}\n".encode("utf-8")
            for part in sorted(parts, key=lambda item: item["PartNumber"])
        )
        self.objects[(upload["bucket"], upload["key"])] = {
            "payload": payload,
            "content_type": upload["content_type"],
            "metadata": upload["metadata"],
            "etag": "etag-multipart",
        }
        return {"ETag": "etag-multipart"}

    def abort_multipart_upload(self, **kwargs: Any) -> None:
        self.multipart.pop(kwargs["UploadId"], None)


def test_s3_object_store_put_get_head_delete_and_list_prefix_with_metadata():
    client = FakeS3Client()
    store = S3ObjectStore(bucket="urdu-pipeline-local", client=client)
    assert isinstance(store, ObjectStore)
    metadata = ObjectMetadata(
        content_type="audio/mpeg",
        checksum_sha256="a" * 64,
        user_metadata={"purpose": "upload"},
    )

    first = store.put_stream(
        "tmp/users/usr_1/uploads/upl_1/source",
        io.BytesIO(b"first"),
        metadata=metadata,
    )
    store.put_stream("tmp/users/usr_1/uploads/upl_2/source", io.BytesIO(b"second"))
    store.put_stream("artifacts/users/usr_1/runs/run_1/artifact.json", io.BytesIO(b"other"))

    assert first.key == "tmp/users/usr_1/uploads/upl_1/source"
    assert first.size_bytes == 5
    assert first.content_type == "audio/mpeg"
    assert first.checksum_sha256 == "a" * 64
    assert dict(first.user_metadata) == {"purpose": "upload"}
    assert store.get_stream(first.key).read() == b"first"
    assert store.head_object(first.key).size_bytes == 5
    assert [info.key for info in store.list_prefix("tmp/users/usr_1/uploads/")] == [
        "tmp/users/usr_1/uploads/upl_1/source",
        "tmp/users/usr_1/uploads/upl_2/source",
    ]

    store.delete_object(first.key)
    assert store.delete_prefix("tmp/users/usr_1/uploads/") == 1
    assert store.list_prefix("tmp/users/usr_1/uploads/") == []
    assert store.head_object("artifacts/users/usr_1/runs/run_1/artifact.json").size_bytes == 5


def test_s3_object_store_signed_urls_include_operation_method_and_metadata():
    client = FakeS3Client()
    store = S3ObjectStore(bucket="bucket", client=client)

    upload = store.create_signed_upload_url(
        "tmp/users/usr_1/uploads/upl_1/source",
        expires_in=timedelta(minutes=10),
        metadata=ObjectMetadata(content_type="audio/mpeg", user_metadata={"p": "u"}),
    )
    download = store.create_signed_download_url(
        "tmp/users/usr_1/uploads/upl_1/source",
        expires_in=timedelta(minutes=5),
        filename="source.mp3",
    )

    assert upload.method == "PUT"
    assert download.method == "GET"
    assert client.presigned_calls[0][0] == "put_object"
    assert client.presigned_calls[0][3] == "PUT"
    assert client.presigned_calls[0][1]["ContentType"] == "audio/mpeg"
    assert client.presigned_calls[0][1]["Metadata"] == {"p": "u"}
    assert client.presigned_calls[1][0] == "get_object"
    assert client.presigned_calls[1][3] == "GET"
    assert client.presigned_calls[1][1]["ResponseContentDisposition"] == (
        'attachment; filename="source.mp3"'
    )


def test_s3_object_store_multipart_lifecycle():
    client = FakeS3Client()
    store = S3ObjectStore(bucket="bucket", client=client)

    upload = store.create_multipart_upload(
        "tmp/users/usr_1/uploads/upl_1/source",
        metadata=ObjectMetadata(content_type="audio/mpeg", checksum_sha256="b" * 64),
    )
    signed = store.create_signed_part_upload_url(
        upload,
        part_number=1,
        expires_in=timedelta(minutes=5),
    )
    completed = store.complete_multipart_upload(
        upload,
        [
            MultipartPart(part_number=2, etag="etag-2", size_bytes=10),
            MultipartPart(part_number=1, etag="etag-1", size_bytes=10),
        ],
    )
    aborted = store.create_multipart_upload("tmp/users/usr_1/uploads/upl_2/source")
    store.abort_multipart_upload(aborted)

    assert upload.upload_id == "upload-1"
    assert signed.method == "PUT"
    assert client.presigned_calls[-1][0] == "upload_part"
    assert completed.key == upload.key
    assert completed.etag == "etag-multipart"
    assert completed.content_type == "audio/mpeg"
    assert completed.checksum_sha256 == "b" * 64
    assert "upload-2" not in client.multipart


def test_s3_object_store_supports_aws_server_side_encryption_headers():
    client = FakeS3Client()
    store = S3ObjectStore(
        bucket="bucket",
        client=client,
        server_side_encryption="aws:kms",
        sse_kms_key_id="arn:aws:kms:us-east-1:123456789012:key/test",
    )

    store.put_stream("artifacts/art_1.json", io.BytesIO(b"{}"))
    store.create_signed_upload_url(
        "uploads/upl_1",
        expires_in=timedelta(minutes=10),
    )
    store.create_multipart_upload("tmp/users/usr_1/runs/run_1/chunk.wav")

    assert client.put_calls[-1]["ServerSideEncryption"] == "aws:kms"
    assert client.put_calls[-1]["SSEKMSKeyId"] == "arn:aws:kms:us-east-1:123456789012:key/test"
    assert client.presigned_calls[-1][1]["ServerSideEncryption"] == "aws:kms"
    assert client.presigned_calls[-1][1]["SSEKMSKeyId"] == "arn:aws:kms:us-east-1:123456789012:key/test"
    assert client.multipart_calls[-1]["ServerSideEncryption"] == "aws:kms"
    assert client.multipart_calls[-1]["SSEKMSKeyId"] == "arn:aws:kms:us-east-1:123456789012:key/test"


def test_s3_object_store_rejects_kms_key_without_kms_encryption():
    with pytest.raises(ValueError, match="SSEKMSKeyId"):
        S3ObjectStore(
            bucket="bucket",
            client=FakeS3Client(),
            server_side_encryption="AES256",
            sse_kms_key_id="arn:aws:kms:us-east-1:123456789012:key/test",
        )


def test_build_boto3_client_omits_static_credentials_for_iam_role(monkeypatch):
    calls: list[tuple[str, dict[str, Any]]] = []

    def fake_client(service_name: str, **kwargs: Any) -> object:
        calls.append((service_name, kwargs))
        return object()

    monkeypatch.setitem(sys.modules, "boto3", SimpleNamespace(client=fake_client))

    from urdu_pipeline.infrastructure import s3

    s3._build_boto3_client(
        endpoint_url=None,
        region_name="us-east-1",
        aws_access_key_id=None,
        aws_secret_access_key=None,
    )

    assert calls == [("s3", {"region_name": "us-east-1"})]


def test_build_boto3_client_rejects_partial_static_credentials(monkeypatch):
    monkeypatch.setitem(sys.modules, "boto3", SimpleNamespace(client=lambda *args, **kwargs: object()))

    from urdu_pipeline.infrastructure import s3

    with pytest.raises(ValueError, match="both access key and secret key"):
        s3._build_boto3_client(
            endpoint_url=None,
            region_name="us-east-1",
            aws_access_key_id="access",
            aws_secret_access_key=None,
        )


def test_s3_object_store_rejects_missing_or_traversal_keys():
    store = S3ObjectStore(bucket="bucket", client=FakeS3Client())

    for key in ("", "../secret", "tmp/users/../secret", "tmp//source", "tmp/users/x/"):
        with pytest.raises(ValueError):
            store.put_stream(key, io.BytesIO(b"bad"))
