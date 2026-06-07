"""Runtime adapter wiring for the containerized API."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class _Settings:
    database_url: str = "postgresql://user:pass@postgres:5432/app"
    object_store_bucket: str = "urdu-pipeline-local"
    object_store_endpoint_url: str = "http://minio:9000"
    object_store_region: str = "local"
    object_store_access_key: str = "access"
    object_store_secret_key: str = "secret"
    redis_url: str = "redis://redis:6379/0"
    service_auth_token: str = "service-token"


def test_build_app_state_from_settings_wires_runtime_adapters(monkeypatch):
    from urdu_pipeline.api import runtime
    from urdu_pipeline.infrastructure.db.metadata import PostgresMetadataStore
    from urdu_pipeline.infrastructure.redis_queue import RedisJobQueue
    from urdu_pipeline.infrastructure.s3 import S3ObjectStore
    from urdu_pipeline.infrastructure.secrets import EnvSecretProvider

    connection = object()
    s3_client = object()
    redis_client = object()

    monkeypatch.setattr(runtime, "connect_postgres", lambda url: connection)
    monkeypatch.setattr(runtime, "_build_s3_client", lambda settings: s3_client)
    monkeypatch.setattr(runtime, "_build_redis_client", lambda settings: redis_client)

    state = runtime.build_app_state_from_settings(_Settings())

    assert isinstance(state.metadata_store, PostgresMetadataStore)
    assert state.metadata_store.connection is connection
    assert isinstance(state.object_store, S3ObjectStore)
    assert state.object_store.client is s3_client
    assert isinstance(state.job_queue, RedisJobQueue)
    assert state.job_queue.redis_client is redis_client
    assert state.job_queue.metadata_store is state.metadata_store
    assert state.cache_store is state.metadata_store
    assert isinstance(state.secret_provider, EnvSecretProvider)
    assert state.service_auth_token == "service-token"


def test_create_runtime_app_installs_app_state(monkeypatch):
    from urdu_pipeline.api import runtime
    from urdu_pipeline.api.dependencies import AppState
    from urdu_pipeline.infrastructure.in_memory import (
        InMemoryCacheStore,
        InMemoryMetadataStore,
        InMemoryObjectStore,
        InMemorySecretProvider,
    )

    state = AppState(
        metadata_store=InMemoryMetadataStore(),
        object_store=InMemoryObjectStore(),
        cache_store=InMemoryCacheStore(),
        secret_provider=InMemorySecretProvider(),
        service_auth_token="service-token",
    )
    monkeypatch.setattr(runtime, "build_app_state_from_settings", lambda settings=None: state)

    app = runtime.create_runtime_app()

    assert app.state.app_state is state
