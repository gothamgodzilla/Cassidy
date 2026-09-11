"""Service layer coordinating validation, voting logic, and rankings."""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from mangasm_voting_engine.schemas import (
    QueueRankingItem,
    VoteRequest,
    VoteResponse,
)
from mangasm_voting_engine.supabase_client import (
    DuplicateVoteError,
    MembershipCheckError,
    SupabaseClient,
)

logger = logging.getLogger("mangasm_voting_engine")


class VotingService:
    def __init__(self, supabase_client: Optional[SupabaseClient] = None):
        self.supabase = supabase_client or SupabaseClient()

    async def cast_vote(self, request: VoteRequest) -> VoteResponse:
        user_id = request.user_id.strip()
        mix_id = request.mix_id.strip()

        # Step 1: Validate user_id has an active Mangasm+ subscription
        is_member = await self.supabase.check_membership(user_id=user_id)
        if not is_member:
            logger.warning("User %s rejected: active Mangasm+ membership required", user_id)
            raise MembershipCheckError("M+ Membership Required")

        # Step 2, 3, 4: Call Supabase RPC `cast_mix_vote` to atomically check duplicate,
        # increment vote count, and store user's vote record
        rpc_result = await self.supabase.cast_mix_vote_rpc(user_id=user_id, mix_id=mix_id)

        # Parse timestamp from response or default to current UTC time
        timestamp_str = rpc_result.get("vote_timestamp")
        if timestamp_str:
            try:
                vote_timestamp = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
            except Exception:
                vote_timestamp = datetime.now(timezone.utc)
        else:
            vote_timestamp = datetime.now(timezone.utc)

        # Log required audit fields: user_id, mix_id, and vote_timestamp
        logger.info(
            "Vote cast successfully - user_id: %s, mix_id: %s, vote_timestamp: %s",
            user_id,
            mix_id,
            vote_timestamp.isoformat(),
        )

        # Step 5: Format updated vote count and current queue rankings
        updated_vote_count = int(rpc_result.get("updated_vote_count", 1))
        rankings_data = rpc_result.get("queue_rankings", [])
        queue_rankings: List[QueueRankingItem] = []

        for item in rankings_data:
            queue_rankings.append(
                QueueRankingItem(
                    mix_id=str(item.get("mix_id")),
                    vote_count=int(item.get("vote_count", 0)),
                    rank=int(item.get("rank", 0)),
                    title=item.get("title"),
                    dj_name=item.get("dj_name"),
                )
            )

        return VoteResponse(
            success=True,
            user_id=user_id,
            mix_id=mix_id,
            updated_vote_count=updated_vote_count,
            vote_timestamp=vote_timestamp,
            queue_rankings=queue_rankings,
            message="Vote cast successfully",
        )
