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

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, status

from app.schemas import VoteRequest, VoteResponse
from app.services.voting import VotingService

router = APIRouter(prefix="/api/v1/swarm/audio", tags=["voting"])


def get_voting_service(request: Request) -> VotingService:
    return request.app.state.voting_service


_ERROR_RESPONSES = {
    400: {"description": "Invalid input"},
    403: {"description": "M+ Membership Required"},
    404: {"description": "Mix not found or not open for voting"},
    409: {"description": "User has already voted for this mix"},
    500: {"description": "Supabase unavailable after retries"},
}


@router.post(
    "/vote",
    response_model=VoteResponse,
    status_code=status.HTTP_200_OK,
    summary="Cast a Mangasm+ vote for a Friday/Saturday live mix",
    responses=_ERROR_RESPONSES,
)
async def cast_vote(body: VoteRequest, service: VotingService = Depends(get_voting_service)) -> VoteResponse:
    result = await service.cast_vote(user_id=body.user_id, mix_id=body.mix_id)
    return VoteResponse(
        user_id=result.user_id,
        mix_id=result.mix_id,
        vote_count=result.vote_count,
        vote_timestamp=result.vote_timestamp,
        rankings=result.rankings,
    )
