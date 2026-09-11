"""FastAPI Application for Mangasm+ Live Mix Voting Engine.

Runtime: Wingman.OS Core (FastAPI)
Endpoints:
- POST /api/v1/swarm/audio/vote
- GET /api/v1/health
"""

import logging
from datetime import datetime, timezone
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from mangasm_voting_engine.config import settings
from mangasm_voting_engine.schemas import (
    HealthCheckResponse,
    VoteRequest,
    VoteResponse,
)
from mangasm_voting_engine.service import VotingService
from mangasm_voting_engine.supabase_client import (
    ConfigurationError,
    DuplicateVoteError,
    MembershipCheckError,
    SupabaseServiceError,
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("mangasm_voting_engine")

app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="Production-ready voting engine for Mangasm+ Friday/Saturday live mixes on Wingman.OS Core.",
)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Handle 400 Bad Request for invalid inputs."""
    logger.warning("Invalid request parameters: %s", exc.errors())
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={
            "detail": "Invalid input",
            "errors": exc.errors(),
        },
    )


@app.get(
    "/api/v1/health",
    response_model=HealthCheckResponse,
    tags=["System"],
    summary="Health check",
)
async def health_check():
    """Health check endpoint required by Wingman.OS Core deployment."""
    return HealthCheckResponse(
        status="healthy",
        version=settings.app_version,
        timestamp=datetime.now(timezone.utc),
    )


@app.post(
    "/api/v1/swarm/audio/vote",
    response_model=VoteResponse,
    status_code=status.HTTP_200_OK,
    tags=["Audio Voting"],
    summary="Cast vote for a Friday/Saturday live mix",
)
async def cast_vote(vote_request: VoteRequest):
    """Cast a vote for a live DJ mix.

    Steps:
    1. Validate user_id has an active Mangasm+ subscription.
    2. Call Supabase RPC cast_mix_vote to atomically increment vote count for mix_id.
    3. If user already voted for this mix, return 409 Conflict.
    4. Store user's vote record to prevent duplicates.
    5. Return updated vote count and current queue rankings.
    """
    voting_service = VotingService()

    try:
        response = await voting_service.cast_vote(vote_request)
        return response

    except MembershipCheckError as exc:
        # Missing record / Not Mangasm+ -> 403 Forbidden: "M+ Membership Required"
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="M+ Membership Required",
        ) from exc

    except DuplicateVoteError as exc:
        # If user already voted for this mix -> 409 Conflict
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc) or "User has already voted for this mix",
        ) from exc

    except (SupabaseServiceError, ConfigurationError) as exc:
        # External service failure -> retry 3 times, then return 500
        logger.error("External service failure while casting vote: %s", str(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="External service failure",
        ) from exc
