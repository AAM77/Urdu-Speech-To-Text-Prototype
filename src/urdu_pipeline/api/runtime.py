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

    return boto3.client(
        "s3",
        endpoint_url=settings.object_store_endpoint_url or None,
        region_name=settings.object_store_region,
        aws_access_key_id=settings.object_store_access_key or None,
        aws_secret_access_key=settings.object_store_secret_key or None,
    )


def _build_redis_client(settings: Settings) -> Any:
    import redis

    return redis.Redis.from_url(settings.redis_url)


__all__ = ["build_app_state_from_settings", "create_runtime_app"]
