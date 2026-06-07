"""FastAPI dependency wiring — cloud-neutral adapter injection."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Annotated

from fastapi import Depends, Request

from urdu_pipeline.application.ports import (
    CacheStore,
    MetadataStore,
    ObjectStore,
    SecretProvider,
)
from urdu_pipeline.auth.hashing import BcryptHasher, PasswordHasher


@dataclass
class AppState:
    """Holder for all adapter instances injected into the application.

    Pass an ``AppState`` to ``create_app`` to swap in-memory fakes for tests
    or real adapters for production without any changes to route code.

    ``password_hasher`` defaults to ``BcryptHasher()`` so production callers
    only need to supply the stores/providers.  Tests can override it with a
    deterministic fake to avoid bcrypt's intentional cost.
    """

    metadata_store: MetadataStore
    object_store: ObjectStore
    cache_store: CacheStore
    secret_provider: SecretProvider
    password_hasher: PasswordHasher = field(default_factory=BcryptHasher)


def get_app_state(request: Request) -> AppState:
    """FastAPI dependency that resolves the AppState stored on ``app.state``."""
    return request.app.state.app_state


def get_metadata_store(
    state: Annotated[AppState, Depends(get_app_state)],
) -> MetadataStore:
    return state.metadata_store


def get_object_store(
    state: Annotated[AppState, Depends(get_app_state)],
) -> ObjectStore:
    return state.object_store


def get_cache_store(
    state: Annotated[AppState, Depends(get_app_state)],
) -> CacheStore:
    return state.cache_store


def get_secret_provider(
    state: Annotated[AppState, Depends(get_app_state)],
) -> SecretProvider:
    return state.secret_provider


def get_password_hasher(
    state: Annotated[AppState, Depends(get_app_state)],
) -> PasswordHasher:
    return state.password_hasher


__all__ = [
    "AppState",
    "get_app_state",
    "get_cache_store",
    "get_metadata_store",
    "get_object_store",
    "get_password_hasher",
    "get_secret_provider",
]
