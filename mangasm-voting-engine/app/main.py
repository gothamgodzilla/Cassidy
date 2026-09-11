# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.

"""Mangasm+ Live Mix Voting Engine — Wingman.OS Core (FastAPI) runtime.

Endpoints:
  POST /api/v1/swarm/audio/vote — cast a vote for a Friday/Saturday live mix
  GET  /api/v1/health           — liveness probe
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .config import get_settings
from .models import ErrorResponse, HealthResponse, VoteRequest, VoteResponse
from .service import NotMemberError, VotingService
from .supabase_client import (
    DuplicateVoteError,
    MixNotFoundError,
    SupabaseClient,
    UpstreamError,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("mangasm.voting.api")


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings = get_settings()
        if not hasattr(app.state, "settings"):
            app.state.settings = settings
        else:
            settings = app.state.settings
        if not hasattr(app.state, "voting_service"):
            app.state.voting_service = VotingService(settings, SupabaseClient(settings))
        logger.info("mangasm voting engine started version=%s", settings.service_version)
        yield

    app = FastAPI(
        title="Mangasm+ Live Mix Voting Engine",
        description=(
            "Friday/Saturday live mix voting for Mangasm+ members. "
            "Golden Hour Progressive House & Melodic Techno — MATRYXX & BERKO, "
            "mangasm.mascot episode premiere at mangasm.app."
        ),
        version=get_settings().service_version,
        lifespan=lifespan,
    )

    def voting_service(request: Request) -> VotingService:
        return request.app.state.voting_service

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"detail": f"Invalid input: {exc.errors()}"},
        )

    @app.get(
        "/api/v1/health",
        response_model=HealthResponse,
        summary="Health check",
    )
    async def health(request: Request):
        settings = request.app.state.settings
        return HealthResponse(
            version=settings.service_version,
            supabase_configured=bool(settings.supabase_url and settings.supabase_key),
        )

    @app.post(
        "/api/v1/swarm/audio/vote",
        response_model=VoteResponse,
        responses={
            400: {"model": ErrorResponse, "description": "Invalid input"},
            403: {"model": ErrorResponse, "description": "M+ Membership Required"},
            404: {"model": ErrorResponse, "description": "Mix not found"},
            409: {"model": ErrorResponse, "description": "Duplicate vote"},
            500: {"model": ErrorResponse, "description": "Upstream failure"},
        },
        summary="Cast a live-mix vote (Mangasm+ members only)",
    )
    async def cast_vote(payload: VoteRequest, request: Request):
        if not payload.user_id.strip() or not payload.mix_id.strip():
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"detail": "user_id and mix_id must not be blank"},
            )
        try:
            return voting_service(request).cast_vote(payload.user_id, payload.mix_id)
        except NotMemberError as exc:
            return JSONResponse(
                status_code=status.HTTP_403_FORBIDDEN,
                content={"detail": str(exc)},
            )
        except DuplicateVoteError as exc:
            return JSONResponse(
                status_code=status.HTTP_409_CONFLICT,
                content={"detail": str(exc)},
            )
        except MixNotFoundError as exc:
            return JSONResponse(
                status_code=status.HTTP_404_NOT_FOUND,
                content={"detail": str(exc)},
            )
        except UpstreamError as exc:
            logger.exception("vote failed upstream user_id=%s mix_id=%s", payload.user_id, payload.mix_id)
            return JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content={"detail": f"Voting service unavailable: {exc}"},
            )
        except Exception:
            logger.exception("vote failed unexpectedly user_id=%s mix_id=%s", payload.user_id, payload.mix_id)
            return JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content={"detail": "Voting service unavailable"},
            )

    return app


app = create_app()
