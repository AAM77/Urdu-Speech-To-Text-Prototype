"""Optional MinIO smoke checks for the S3 object store adapter."""

from __future__ import annotations

import importlib.util
import io
import os
import uuid
from datetime import timedelta

import pytest

from urdu_pipeline.application.ports import ObjectMetadata
from urdu_pipeline.infrastructure.s3 import S3ObjectStore


def test_s3_object_store_runs_against_configured_minio():
    if os.environ.get("RUN_MINIO_OBJECT_STORE_SMOKE") != "1":
        pytest.skip("set RUN_MINIO_OBJECT_STORE_SMOKE=1 to run the MinIO smoke test")
    if importlib.util.find_spec("boto3") is None:
        pytest.skip("boto3 is not installed")

    endpoint_url = os.environ.get("OBJECT_STORE_ENDPOINT") or os.environ.get("MINIO_ENDPOINT")
    bucket = os.environ.get("OBJECT_STORE_BUCKET")
    access_key = os.environ.get("AWS_ACCESS_KEY_ID") or os.environ.get("MINIO_ROOT_USER")
    secret_key = os.environ.get("AWS_SECRET_ACCESS_KEY") or os.environ.get("MINIO_ROOT_PASSWORD")
    if not endpoint_url or not bucket or not access_key or not secret_key:
        pytest.skip("MinIO object-store connection settings are not configured")

    import boto3

    client = boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        region_name=os.environ.get("OBJECT_STORE_REGION") or "local",
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
    )
    existing = {item["Name"] for item in client.list_buckets().get("Buckets", [])}
    if bucket not in existing:
        client.create_bucket(Bucket=bucket)

    store = S3ObjectStore(bucket=bucket, client=client)
    prefix = f"smoke/{uuid.uuid4().hex}/"
    key = f"{prefix}source.txt"
    try:
        info = store.put_stream(
            key,
            io.BytesIO(b"minio smoke"),
            metadata=ObjectMetadata(content_type="text/plain", user_metadata={"smoke": "true"}),
        )
        assert info.size_bytes == len(b"minio smoke")
        assert store.get_stream(key).read() == b"minio smoke"
        assert store.head_object(key).content_type == "text/plain"
        assert store.create_signed_download_url(key, expires_in=timedelta(minutes=5)).url
        assert [item.key for item in store.list_prefix(prefix)] == [key]
    finally:
        store.delete_prefix(prefix)
