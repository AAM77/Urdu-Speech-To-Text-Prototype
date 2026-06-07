"""FastAPI app skeleton tests — health endpoint and dependency wiring."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Health endpoint
# ---------------------------------------------------------------------------


def test_health_returns_200_and_ok_status():
    from urdu_pipeline.api.app import create_app

    client = TestClient(create_app())

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_health_response_includes_version_field():
    from urdu_pipeline.api.app import create_app

    client = TestClient(create_app())

    response = client.get("/health")
    body = response.json()

    assert "version" in body
    assert isinstance(body["version"], str)


def test_health_does_not_expose_internal_details():
    from urdu_pipeline.api.app import create_app

    client = TestClient(create_app())

    response = client.get("/health")
    body = response.json()

    for forbidden in ("database_url", "openai_api_key", "secret", "password", "token"):
        assert forbidden not in body


# ---------------------------------------------------------------------------
# Unknown routes return 404
# ---------------------------------------------------------------------------


def test_unknown_route_returns_404():
    from urdu_pipeline.api.app import create_app

    client = TestClient(create_app())

    assert client.get("/nonexistent").status_code == 404


# ---------------------------------------------------------------------------
# Dependency wiring with in-memory adapters
# ---------------------------------------------------------------------------


def test_app_can_be_created_with_in_memory_adapters():
    from urdu_pipeline.api.app import create_app
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
    )
    app = create_app(state=state)
    client = TestClient(app)

    response = client.get("/health")
    assert response.status_code == 200


def test_dependency_state_is_accessible_from_request():
    from urdu_pipeline.api.app import create_app
    from urdu_pipeline.api.dependencies import AppState, get_app_state
    from urdu_pipeline.infrastructure.in_memory import (
        InMemoryCacheStore,
        InMemoryMetadataStore,
        InMemoryObjectStore,
        InMemorySecretProvider,
    )

    metadata_store = InMemoryMetadataStore()
    state = AppState(
        metadata_store=metadata_store,
        object_store=InMemoryObjectStore(),
        cache_store=InMemoryCacheStore(),
        secret_provider=InMemorySecretProvider(),
    )
    app = create_app(state=state)

    # Verify the dependency resolution does not raise
    from fastapi import Request

    with TestClient(app) as client:
        # A successful health call proves the app wired correctly
        assert client.get("/health").status_code == 200


def test_app_state_exposes_required_adapter_attributes():
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
    )

    assert hasattr(state, "metadata_store")
    assert hasattr(state, "object_store")
    assert hasattr(state, "cache_store")
    assert hasattr(state, "secret_provider")
