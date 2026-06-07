"""Optional AWS S3 smoke checks for the S3 object store adapter."""

from __future__ import annotations

import importlib.util
import io
import os
import uuid
from datetime import timedelta

import pytest

from urdu_pipeline.application.ports import ObjectMetadata
from urdu_pipeline.infrastructure.s3 import S3ObjectStore


def test_s3_object_store_runs_against_configured_aws_s3_bucket():
    if os.environ.get("RUN_S3_OBJECT_STORE_SMOKE") != "1":
        pytest.skip("set RUN_S3_OBJECT_STORE_SMOKE=1 to run the AWS S3 smoke test")
    if importlib.util.find_spec("boto3") is None:
        pytest.skip("boto3 is not installed")

    bucket = os.environ.get("AWS_S3_OBJECT_STORE_BUCKET") or os.environ.get("OBJECT_STORE_BUCKET")
    region = os.environ.get("AWS_REGION") or os.environ.get("OBJECT_STORE_REGION") or "us-east-1"
    access_key = os.environ.get("AWS_ACCESS_KEY_ID") or os.environ.get("OBJECT_STORE_ACCESS_KEY")
    secret_key = os.environ.get("AWS_SECRET_ACCESS_KEY") or os.environ.get("OBJECT_STORE_SECRET_KEY")
    if not bucket:
        pytest.skip("AWS S3 bucket is not configured")
    if bool(access_key) != bool(secret_key):
        pytest.skip("AWS S3 smoke requires both static credentials or neither for IAM role auth")

    import boto3

    client_kwargs = {"region_name": region}
    if access_key and secret_key:
        client_kwargs["aws_access_key_id"] = access_key
        client_kwargs["aws_secret_access_key"] = secret_key

    store = S3ObjectStore(
        bucket=bucket,
        client=boto3.client("s3", **client_kwargs),
        server_side_encryption=os.environ.get("OBJECT_STORE_SERVER_SIDE_ENCRYPTION") or None,
        sse_kms_key_id=os.environ.get("OBJECT_STORE_SSE_KMS_KEY_ID") or None,
    )
    prefix = f"smoke/aws/{uuid.uuid4().hex}/"
    key = f"{prefix}source.txt"
    try:
        info = store.put_stream(
            key,
            io.BytesIO(b"aws s3 smoke"),
            metadata=ObjectMetadata(content_type="text/plain", user_metadata={"smoke": "aws"}),
        )
        assert info.size_bytes == len(b"aws s3 smoke")
        assert store.get_stream(key).read() == b"aws s3 smoke"
        assert store.head_object(key).content_type == "text/plain"
        assert store.create_signed_download_url(key, expires_in=timedelta(minutes=5)).url
        assert [item.key for item in store.list_prefix(prefix)] == [key]
    finally:
        store.delete_prefix(prefix)
