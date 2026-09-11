"""FastAPI application for the Mangasm+ Live Mix Voting Engine.

Runtime: Wingman.OS Core (FastAPI).

Routes
    ``POST /api/v1/swarm/audio/vote``      cast a vote for a live mix
    ``GET  /api/v1/swarm/audio/featured``  episode metadata for the mascot overlay
    ``GET  /api/v1/health``                liveness / readiness probe
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from . import __version__
from .config import Settings, load_settings
from .content import featured_payload
from .errors import VotingError
from .logging_config import configure_logging, get_logger
from .models import ErrorResponse, HealthResponse, VoteRequest, VoteResponse
from .service import VotingService
from .supabase_client import SupabaseClient

SERVICE_NAME = "mangasm-live-mix-voting"
RUNTIME = "Wingman.OS Core"

_ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    400: {"model": ErrorResponse, "description": "Invalid input"},
    403: {"model": ErrorResponse, "description": "M+ Membership Required"},
    404: {"model": ErrorResponse, "description": "Unknown mix"},
    409: {"model": ErrorResponse, "description": "Duplicate vote or voting closed"},
    500: {"model": ErrorResponse, "description": "Upstream failure after retries"},
}


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Fail fast on bad configuration, then own the HTTP client's lifetime."""
    configure_logging(os.environ.get("LOG_LEVEL", "INFO"))
    settings: Settings = app.state.settings or load_settings()
    app.state.settings = settings

    client: SupabaseClient = app.state.supabase_client or SupabaseClient(settings)
    owns_client = app.state.supabase_client is None
    app.state.supabase_client = client
    app.state.service = VotingService(settings, client)

    get_logger().info(
        "service_started",
        extra={"service": SERVICE_NAME, "version": __version__, **settings.redacted()},
    )
    try:
        yield
    finally:
        if owns_client:
            await client.aclose()


def create_app(
    settings: Settings | None = None,
    supabase_client: SupabaseClient | None = None,
) -> FastAPI:
    """Build the application.

    ``settings`` and ``supabase_client`` are injection points for tests; in
    production both are derived from the environment during startup.
    """
    app = FastAPI(
        title="Mangasm+ Live Mix Voting Engine",
        version=__version__,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.supabase_client = supabase_client
    app.state.service = None

    @app.exception_handler(VotingError)
    async def _handle_voting_error(_: Request, exc: VotingError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": exc.error_code, "detail": exc.detail},
        )

    @app.exception_handler(RequestValidationError)
    async def _handle_validation_error(
        _: Request, exc: RequestValidationError
    ) -> JSONResponse:
        """Return 400 rather than FastAPI's default 422 for malformed input."""
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"error": "invalid_request", "detail": _summarise(exc)},
        )

    @app.exception_handler(Exception)
    async def _handle_unexpected_error(_: Request, exc: Exception) -> JSONResponse:
        get_logger().exception("unhandled_error", extra={"reason": type(exc).__name__})
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": "internal_error", "detail": "Unexpected server error"},
        )

    @app.post(
        "/api/v1/swarm/audio/vote",
        response_model=VoteResponse,
        responses=_ERROR_RESPONSES,
        summary="Cast a Mangasm+ vote for a Friday/Saturday live mix",
    )
    async def cast_vote(payload: VoteRequest) -> VoteResponse:
        service: VotingService = app.state.service
        result = await service.cast_vote(payload.user_id, payload.mix_id)
        return VoteResponse(
            user_id=result.user_id,
            mix_id=result.mix_id,
            vote_count=result.vote_count,
            vote_timestamp=result.vote_timestamp,
            queue=result.queue,
            queue_degraded=result.queue_degraded,
        )

    @app.get(
        "/api/v1/swarm/audio/featured",
        summary="Episode metadata for the mangasm.mascot visual experience",
    )
    async def featured() -> dict[str, object]:
        return featured_payload()

    @app.get("/api/v1/health", response_model=HealthResponse, summary="Health check")
    async def health() -> HealthResponse:
        settings_snapshot: Settings = app.state.settings
        return HealthResponse(
            status="ok",
            service=SERVICE_NAME,
            version=__version__,
            runtime=RUNTIME,
            config=settings_snapshot.redacted() if settings_snapshot else {},
        )

    return app


def _summarise(exc: RequestValidationError) -> str:
    parts = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error.get("loc", ()) if part != "body")
        parts.append(f"{location or 'body'}: {error.get('msg', 'invalid value')}")
    return "; ".join(parts) or "Invalid request body"


app = create_app()
