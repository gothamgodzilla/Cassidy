"""Request and response schemas for the voting endpoint."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

_MAX_ID_LENGTH = 128


class VoteRequest(BaseModel):
    """Body of ``POST /api/v1/swarm/audio/vote``."""

    model_config = ConfigDict(extra="forbid")

    user_id: str = Field(..., min_length=1, max_length=_MAX_ID_LENGTH)
    mix_id: str = Field(..., min_length=1, max_length=_MAX_ID_LENGTH)

    @field_validator("user_id", "mix_id")
    @classmethod
    def _reject_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped


class QueueEntry(BaseModel):
    """One mix in the live queue ranking."""

    rank: int
    mix_id: str
    title: str | None = None
    vote_count: int


class VoteResponse(BaseModel):
    """Successful vote result plus the recalculated queue rankings."""

    status: str = "recorded"
    user_id: str
    mix_id: str
    vote_count: int
    vote_timestamp: datetime
    queue: list[QueueEntry]
    # Set when the vote was committed but the ranking view could not be read;
    # `queue` then holds only the mix that was just voted for.
    queue_degraded: bool = False


class ErrorResponse(BaseModel):
    """Uniform error envelope for every non-2xx response."""

    error: str
    detail: str


class HealthResponse(BaseModel):
    """Payload of ``GET /api/v1/health``."""

    status: str
    service: str
    version: str
    runtime: str
    config: dict[str, object]
