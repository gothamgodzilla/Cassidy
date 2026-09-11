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

"""FastAPI routers for health, featured mix, and vote casting."""

from fastapi import APIRouter, Depends, status

from app import __version__
from app.catalog import GOLDEN_HOUR_MIX
from app.engine import LiveMixVotingEngine, get_engine
from app.models import HealthResponse, VoteRequest, VoteResponse

health_router = APIRouter(tags=["health"])
vote_router = APIRouter(tags=["live-mix-voting"])


@health_router.get("/api/v1/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        service="wingman-os-core",
        component="live-mix-voting",
        version=__version__,
    )


@vote_router.get("/api/v1/swarm/audio/featured")
async def featured_mix() -> dict:
    """mangasm.mascot episode premier — Golden Hour live mix."""
    return {
        "status": "ok",
        "featured": GOLDEN_HOUR_MIX,
        "live_sessions": ["friday", "saturday"],
    }


@vote_router.post(
    "/api/v1/swarm/audio/vote",
    response_model=VoteResponse,
    status_code=status.HTTP_200_OK,
)
async def cast_vote(
    payload: VoteRequest,
    engine: LiveMixVotingEngine = Depends(get_engine),
) -> VoteResponse:
    return await engine.cast_vote(payload.user_id, payload.mix_id)
