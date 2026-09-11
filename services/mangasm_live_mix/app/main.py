# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements. See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership. The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License. You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Wingman.OS FastAPI entry point for Mangasm+ live mix voting."""

from __future__ import annotations

import json
import logging
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, AsyncIterator
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.supabase import SupabaseClient, SupabaseError, SupabaseUnavailable

LOGGER = logging.getLogger("mangasm.live_mix_vote")


class JsonFormatter(logging.Formatter):
    """Format vote audit events as single-line JSON."""

    def format(self, record: logging.LogRecord) -> str:
        event = {
            "level": record.levelname,
            "message": record.getMessage(),
            "user_id": getattr(record, "user_id", None),
            "mix_id": getattr(record, "mix_id", None),
            "vote_timestamp": getattr(record, "vote_timestamp", None),
        }
        return json.dumps(event, separators=(",", ":"))


def configure_logging() -> None:
    """Configure structured application logging once."""

    if LOGGER.handlers:
        return
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    LOGGER.addHandler(handler)
    LOGGER.setLevel(os.getenv("LOG_LEVEL", "INFO").upper())
    LOGGER.propagate = False


@dataclass(frozen=True)
class Settings:
    """Validated runtime settings."""

    supabase_url: str
    supabase_key: str

    @classmethod
    def from_environment(cls) -> "Settings":
        """Load required settings and fail startup on invalid configuration."""

        url = os.getenv("SUPABASE_URL", "").strip()
        key = os.getenv("SUPABASE_KEY", "").strip()
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise RuntimeError("SUPABASE_URL must be a valid HTTP(S) URL")
        if not key:
            raise RuntimeError("SUPABASE_KEY is required")
        return cls(url, key)


class VoteRequest(BaseModel):
    """Vote request payload."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    user_id: str = Field(min_length=1, max_length=128)
    mix_id: str = Field(min_length=1, max_length=128)

    @field_validator("user_id", "mix_id")
    @classmethod
    def reject_control_characters(cls, value: str) -> str:
        if any(character.isspace() for character in value):
            raise ValueError("must not contain whitespace")
        return value


class QueueRanking(BaseModel):
    """One mix's current position in the live queue."""

    mix_id: str
    vote_count: int = Field(ge=0)
    rank: int = Field(ge=1)


class VoteResponse(BaseModel):
    """Successful vote response."""

    mix_id: str
    vote_count: int = Field(ge=1)
    queue_rankings: list[QueueRanking]


class HealthResponse(BaseModel):
    """Health-check response."""

    status: str
    service: str


class VoteService:
    """Coordinate membership checks and atomic vote writes."""

    def __init__(self, client: SupabaseClient) -> None:
        self._client = client

    async def close(self) -> None:
        await self._client.close()

    async def cast(self, vote: VoteRequest) -> dict[str, Any]:
        try:
            active_subscription = await self._client.active_subscription(vote.user_id)
        except SupabaseError as exc:
            raise SupabaseUnavailable("Supabase rejected the membership check") from exc

        if not active_subscription:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="M+ Membership Required",
            )

        try:
            return await self._client.cast_vote(vote.user_id, vote.mix_id)
        except SupabaseError as exc:
            if exc.status_code == status.HTTP_409_CONFLICT:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="User already voted for this mix",
                ) from exc
            if exc.status_code == status.HTTP_403_FORBIDDEN:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="M+ Membership Required",
                ) from exc
            raise SupabaseUnavailable("Supabase rejected cast_mix_vote") from exc


def create_app(vote_service: VoteService | None = None) -> FastAPI:
    """Create the application, optionally with an injected service for tests."""

    configure_logging()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        if vote_service is None:
            settings = Settings.from_environment()
            application.state.vote_service = VoteService(
                SupabaseClient(settings.supabase_url, settings.supabase_key)
            )
        else:
            application.state.vote_service = vote_service
        try:
            yield
        finally:
            await application.state.vote_service.close()

    application = FastAPI(
        title="Mangasm+ Live Mix Voting Engine",
        version="1.0.0",
        lifespan=lifespan,
    )

    @application.exception_handler(RequestValidationError)
    async def invalid_request(
        _request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content=jsonable_encoder({"detail": exc.errors()}),
        )

    @application.get("/api/v1/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        return HealthResponse(status="ok", service="mangasm-live-mix-voting")

    @application.post(
        "/api/v1/swarm/audio/vote",
        response_model=VoteResponse,
        status_code=status.HTTP_200_OK,
        responses={
            400: {"description": "Invalid input"},
            403: {"description": "M+ Membership Required"},
            409: {"description": "Duplicate vote"},
            500: {"description": "Supabase unavailable"},
        },
    )
    async def cast_vote(payload: VoteRequest, request: Request) -> VoteResponse:
        vote_timestamp = datetime.now(timezone.utc).isoformat()
        log_context = {
            "user_id": payload.user_id,
            "mix_id": payload.mix_id,
            "vote_timestamp": vote_timestamp,
        }
        try:
            result = await request.app.state.vote_service.cast(payload)
            response = VoteResponse.model_validate(result)
        except SupabaseUnavailable as exc:
            LOGGER.exception("vote_external_service_failure", extra=log_context)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Unable to process vote",
            ) from exc
        except HTTPException:
            LOGGER.info("vote_rejected", extra=log_context)
            raise
        else:
            LOGGER.info("vote_cast", extra=log_context)
            return response

    return application


app = create_app()
