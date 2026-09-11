"""Pydantic schemas for Mangasm+ Live Mix Voting Engine."""

from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, Field


class VoteRequest(BaseModel):
    user_id: str = Field(..., min_length=1, description="Unique identifier for the voter")
    mix_id: str = Field(..., min_length=1, description="Unique identifier for the DJ mix")


class QueueRankingItem(BaseModel):
    mix_id: str
    vote_count: int
    rank: int
    title: Optional[str] = None
    dj_name: Optional[str] = None


class VoteResponse(BaseModel):
    success: bool = True
    user_id: str
    mix_id: str
    updated_vote_count: int
    vote_timestamp: datetime
    queue_rankings: List[QueueRankingItem] = Field(default_factory=list)
    message: str = "Vote cast successfully"


class HealthCheckResponse(BaseModel):
    status: str = "healthy"
    version: str
    timestamp: datetime
