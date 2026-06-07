"""Environment-backed FastAPI factory for container/runtime deployment."""

from __future__ import annotations

from typing import Any

from urdu_pipeline.api.app import create_app
from urdu_pipeline.api.dependencies import AppState
from urdu_pipeline.config.settings import Settings, get_settings
from urdu_pipeline.infrastructure.db.migrations import connect_postgres
from urdu_pipeline.infrastructure.db.metadata import PostgresMetadataStore
from urdu_pipeline.infrastructure.redis_queue import RedisJobQueue
from urdu_pipeline.infrastructure.s3 import S3ObjectStore
from urdu_pipeline.infrastructure.secrets import EnvSecretProvider


def build_app_state_from_settings(settings: Settings | None = None) -> AppState:
    """Build runtime adapters from environment-backed settings."""
    s = settings or get_settings()
    connection = connect_postgres(s.database_url)
    metadata_store = PostgresMetadataStore(connection)
    object_store = S3ObjectStore(
        bucket=s.object_store_bucket,
        client=_build_s3_client(s),
        server_side_encryption=getattr(s, "object_store_server_side_encryption", None),
        sse_kms_key_id=getattr(s, "object_store_sse_kms_key_id", None),
    )
    job_queue = RedisJobQueue(
        metadata_store=metadata_store,
        redis_client=_build_redis_client(s),
    )
    return AppState(
        metadata_store=metadata_store,
        object_store=object_store,
        cache_store=metadata_store,
        secret_provider=EnvSecretProvider(),
        job_queue=job_queue,
        service_auth_token=s.service_auth_token,
    )


def create_runtime_app() -> Any:
    """Create the FastAPI app with real environment-backed adapters."""
    return create_app(state=build_app_state_from_settings())


def _build_s3_client(settings: Settings) -> Any:
    import boto3

    if bool(settings.object_store_access_key) != bool(settings.object_store_secret_key):
        raise ValueError("S3 static credentials require both access key and secret key.")
    kwargs: dict[str, Any] = {}
    if settings.object_store_endpoint_url:
        kwargs["endpoint_url"] = settings.object_store_endpoint_url
    if settings.object_store_region:
        kwargs["region_name"] = settings.object_store_region
    if settings.object_store_access_key and settings.object_store_secret_key:
        kwargs["aws_access_key_id"] = settings.object_store_access_key
        kwargs["aws_secret_access_key"] = settings.object_store_secret_key
    return boto3.client("s3", **kwargs)


def _build_redis_client(settings: Settings) -> Any:
    import redis

    return redis.Redis.from_url(settings.redis_url)


__all__ = ["build_app_state_from_settings", "create_runtime_app"]
