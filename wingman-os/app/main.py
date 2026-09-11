# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Wingman.OS Core FastAPI application for Mangasm+ live mix voting."""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app import __version__
from app.config import get_settings
from app.engine import LiveMixVotingEngine, set_engine
from app.exceptions import (
    DuplicateVoteError,
    ExternalServiceError,
    MembershipRequiredError,
    MixNotFoundError,
)
from app.logging_config import configure_logging
from app.routers import health_router, vote_router
from app.services import SupabaseMixVotingGateway

MEMBERSHIP_DETAIL = "M+ Membership Required"


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings.log_level)
    gateway = SupabaseMixVotingGateway(settings)
    set_engine(LiveMixVotingEngine(gateway))
    app.state.gateway = gateway
    try:
        yield
    finally:
        await gateway.aclose()


app = FastAPI(
    title="Wingman.OS Core — Mangasm+ Live Mix Voting Engine",
    version=__version__,
    lifespan=lifespan,
)


def _safe_validation_errors(exc: RequestValidationError) -> list[dict]:
    errors = []
    for error in exc.errors():
        errors.append({"loc": [str(part) for part in error.get("loc", ())], "msg": error.get("msg")})
    return errors


@app.exception_handler(RequestValidationError)
async def invalid_input_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=400,
        content={"detail": "Invalid input", "errors": _safe_validation_errors(exc)},
    )


@app.exception_handler(MembershipRequiredError)
async def membership_handler(request: Request, exc: MembershipRequiredError) -> JSONResponse:
    return JSONResponse(status_code=403, content={"detail": MEMBERSHIP_DETAIL})


@app.exception_handler(DuplicateVoteError)
async def duplicate_vote_handler(request: Request, exc: DuplicateVoteError) -> JSONResponse:
    return JSONResponse(
        status_code=409,
        content={
            "detail": "Conflict: user already voted for this mix",
            "user_id": exc.user_id,
            "mix_id": exc.mix_id,
            "vote_count": exc.vote_count,
            "rankings": [r.model_dump() for r in exc.rankings],
        },
    )


@app.exception_handler(MixNotFoundError)
async def mix_not_found_handler(request: Request, exc: MixNotFoundError) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": "Invalid input", "errors": [exc.message]})


@app.exception_handler(ExternalServiceError)
async def external_service_handler(request: Request, exc: ExternalServiceError) -> JSONResponse:
    return JSONResponse(status_code=500, content={"detail": "Voting service temporarily unavailable"})


app.include_router(health_router)
app.include_router(vote_router)
