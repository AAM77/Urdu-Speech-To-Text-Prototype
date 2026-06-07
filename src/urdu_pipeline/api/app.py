"""FastAPI application factory."""

from __future__ import annotations

from fastapi import FastAPI

from urdu_pipeline.api.dependencies import AppState
from urdu_pipeline.api.routes.auth import router as auth_router
from urdu_pipeline.api.routes.health import router as health_router
from urdu_pipeline.api.routes.tokens import router as tokens_router


def create_app(*, state: AppState | None = None) -> FastAPI:
    """Create and configure the FastAPI application.

    An ``AppState`` can be injected to swap in-memory adapters for tests.
    If omitted, the app is created without wired adapters (suitable for
    import checks and health-only usage).
    """
    app = FastAPI(
        title="Urdu Pipeline API",
        version="0.1.0",
        docs_url="/docs",
        redoc_url=None,
        openapi_url="/openapi.json",
    )

    if state is not None:
        app.state.app_state = state

    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(tokens_router)

    return app


__all__ = ["create_app"]
