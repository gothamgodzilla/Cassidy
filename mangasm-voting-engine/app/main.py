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
  POST /api/v1/swarm/audio/vote  — cast a vote for a Friday/Saturday live mix
  GET  /api/v1/health            — liveness probe
  GET  /api/v1/swarm/topology    — Phase A agent ring topology
  POST /api/v1/swarm/state       — publish a signed state vector to the tunnel
  GET  /api/v1/swarm/state/next  — next agent catches a vector from the tunnel
  POST /api/v1/swarm/compress    — compress a heavy history into a sports car
  GET  /api/v1/security/ozone    — ozone v0 status (seal alg, key state)
  GET  /                        — dark-luxury frontend (static)
"""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import llm_adapter, security
from .config import get_settings
from .models import ErrorResponse, HealthResponse, VoteRequest, VoteResponse
from .service import NotMemberError, VotingService
from .supabase_client import (
    DuplicateVoteError,
    MixNotFoundError,
    SupabaseClient,
    UpstreamError,
)
from .swarm import PublishRequest, SwarmService, TopologyResponse, default_topology

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("mangasm.voting.api")

FRONTEND_DIR = Path(__file__).resolve().parents[1] / "frontend"


class CompressRequest(BaseModel):
    agent_id: str = Field(min_length=1, max_length=128)
    messages: list[dict[str, str]] = Field(min_length=1, max_length=200)


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
        if not hasattr(app.state, "swarm_service"):
            app.state.swarm_service = SwarmService()
        _, ozone_dev = security.get_ozone_key()
        if ozone_dev:
            logger.warning("ozone HMAC key is a dev fallback; set OZONE_HMAC_KEY in production")
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

    def swarm_service(request: Request) -> SwarmService:
        if not hasattr(request.app.state, "swarm_service"):
            request.app.state.swarm_service = SwarmService()
        return request.app.state.swarm_service

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
            result = voting_service(request).cast_vote(payload.user_id, payload.mix_id)
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
        # Close the loop: feed the counted vote into the ring as a mix-voter
        # vector so downstream nodes (visual-mascot) react to crowd energy.
        # Best-effort — a tunnel hiccup must never fail an accepted vote.
        try:
            swarm_service(request).publish(
                PublishRequest(
                    agent_id="mix-voter",
                    summary=f"Vote counted for {result.mix_id}: {result.vote_count} total.",
                    key_points=[f"{result.mix_id}={result.vote_count}"],
                    next_action="visual-mascot: ride this crowd energy in the next episode beat.",
                )
            )
        except Exception:
            logger.warning(
                "swarm feed failed after accepted vote user_id=%s mix_id=%s",
                payload.user_id,
                payload.mix_id,
            )
        return result

    @app.get(
        "/api/v1/swarm/topology",
        response_model=TopologyResponse,
        summary="Phase A agent ring topology",
    )
    async def topology():
        return default_topology()

    @app.post(
        "/api/v1/swarm/state",
        responses={
            400: {"model": ErrorResponse, "description": "Invalid vector"},
            500: {"model": ErrorResponse, "description": "Tunnel failure"},
        },
        summary="Publish a signed state vector to the tunnel",
    )
    async def publish_state(payload: PublishRequest, request: Request):
        try:
            return swarm_service(request).publish(payload)
        except ValueError as exc:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"detail": str(exc)},
            )
        except Exception:
            logger.exception("swarm publish failed agent=%s", payload.agent_id)
            return JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content={"detail": "Tunnel unavailable"},
            )

    @app.get(
        "/api/v1/swarm/state/next",
        responses={400: {"model": ErrorResponse, "description": "Invalid input"}},
        summary="Next agent catches a vector from the tunnel",
    )
    async def next_state(agent_id: str, request: Request, loop_id: str = "golden-hour-ring"):
        if not agent_id.strip():
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"detail": "agent_id must not be blank"},
            )
        vector = swarm_service(request).next_for(agent_id.strip(), loop_id.strip())
        if vector is None:
            return JSONResponse(status_code=status.HTTP_204_NO_CONTENT, content=None)
        return vector

    @app.get(
        "/api/v1/swarm/state/latest",
        responses={400: {"model": ErrorResponse, "description": "Invalid input"}},
        summary="Newest vector authored by an agent (audit read)",
    )
    async def latest_state(agent_id: str, request: Request, loop_id: str = "golden-hour-ring"):
        if not agent_id.strip():
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"detail": "agent_id must not be blank"},
            )
        vector = swarm_service(request).latest_by(agent_id.strip(), loop_id.strip())
        if vector is None:
            return JSONResponse(status_code=status.HTTP_204_NO_CONTENT, content=None)
        return vector

    @app.post(
        "/api/v1/swarm/compress",        responses={400: {"model": ErrorResponse, "description": "Invalid input"}},
        summary="Compress a heavy history into a tunnel-ready sports car",
    )
    async def compress(payload: CompressRequest):
        try:
            return llm_adapter.compress_history(payload.messages, agent_id=payload.agent_id)
        except ValueError as exc:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"detail": str(exc)},
            )

    @app.get("/api/v1/security/ozone", summary="Ozone v0 encryption status")
    async def ozone_status():
        _, is_dev = security.get_ozone_key()
        return {
            "artifact": "security-ozone-v0",
            "alg": security.OZONE_ALG,
            "sha_rounds": security.OZONE_ROUNDS,
            "key_configured": not is_dev,
            "dev_fallback": is_dev,
            "upgrade_path": "v1: AES-256-GCM payload encryption with KMS-wrapped per-loop keys",
        }

    if FRONTEND_DIR.is_dir():
        app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")

    return app


app = create_app()
