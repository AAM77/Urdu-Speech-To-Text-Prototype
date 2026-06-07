"""FastAPI application factory."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from urdu_pipeline.api.dependencies import AppState
from urdu_pipeline.api.routes.auth import router as auth_router
from urdu_pipeline.api.routes.health import router as health_router
from urdu_pipeline.api.routes.tokens import router as tokens_router
from urdu_pipeline.api.routes.runs import router as runs_router
from urdu_pipeline.api.routes.uploads import router as uploads_router


def create_app(
    *,
    state: AppState | None = None,
    allowed_origins: list[str] | None = None,
) -> FastAPI:
    """Create and configure the FastAPI application.

    Parameters
    ──────────
    state:
        Adapter instances for dependency injection.  Pass ``None`` only for
        import / schema checks where no request is served.
    allowed_origins:
        List of allowed CORS origins, e.g. ``["https://app.example.com"]``.
        Pass ``["*"]`` to allow all origins (development only).
        Defaults to an empty list, which disables CORS headers.
    """
    app = FastAPI(
        title="Urdu Pipeline API",
        version="0.1.0",
        docs_url="/docs",
        redoc_url=None,
        openapi_url="/openapi.json",
    )

    origins = allowed_origins or []
    if origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    if state is not None:
        app.state.app_state = state

    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(tokens_router)
    app.include_router(uploads_router)
    app.include_router(runs_router)

    return app


__all__ = ["create_app"]
