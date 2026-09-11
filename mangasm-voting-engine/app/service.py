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

"""Voting orchestration: membership gate, atomic vote, rankings."""

import logging
from datetime import datetime, timezone

from .config import Settings
from .models import RankingEntry, VoteResponse
from .supabase_client import DuplicateVoteError, MixNotFoundError, SupabaseClient

logger = logging.getLogger("mangasm.voting.service")


class NotMemberError(PermissionError):
    """user_id has no active Mangasm+ subscription."""


class VotingService:
    def __init__(self, settings: Settings, client: SupabaseClient | None = None) -> None:
        self._settings = settings
        self._client = client or SupabaseClient(settings)

    def cast_vote(self, user_id: str, mix_id: str) -> VoteResponse:
        vote_timestamp = datetime.now(timezone.utc)
        logger.info(
            "vote requested user_id=%s mix_id=%s vote_timestamp=%s",
            user_id,
            mix_id,
            vote_timestamp.isoformat(),
        )

        if not self._client.has_active_subscription(user_id):
            logger.info(
                "vote rejected (not M+) user_id=%s mix_id=%s vote_timestamp=%s",
                user_id,
                mix_id,
                vote_timestamp.isoformat(),
            )
            raise NotMemberError("M+ Membership Required")

        if self._client.has_existing_vote(user_id, mix_id):
            logger.info(
                "vote rejected (duplicate) user_id=%s mix_id=%s vote_timestamp=%s",
                user_id,
                mix_id,
                vote_timestamp.isoformat(),
            )
            raise DuplicateVoteError(f"user {user_id} already voted for mix {mix_id}")

        try:
            receipt = self._client.cast_vote_rpc(user_id, mix_id)
        except DuplicateVoteError:
            # RPC won the race with a concurrent voter: same 409 outcome.
            logger.info(
                "vote rejected (duplicate at commit) user_id=%s mix_id=%s vote_timestamp=%s",
                user_id,
                mix_id,
                vote_timestamp.isoformat(),
            )
            raise
        except MixNotFoundError:
            raise

        rankings_raw = self._client.get_rankings(self._settings.rankings_limit)
        vote_count = int(receipt.get("vote_count", 0))
        if vote_count == 0:
            match = next((r for r in rankings_raw if r["mix_id"] == mix_id), None)
            if match is not None:
                vote_count = int(match["votes"])

        logger.info(
            "vote counted user_id=%s mix_id=%s vote_timestamp=%s vote_count=%d",
            user_id,
            mix_id,
            vote_timestamp.isoformat(),
            vote_count,
        )
        return VoteResponse(
            mix_id=mix_id,
            vote_count=vote_count,
            rankings=[RankingEntry(**row) for row in rankings_raw],
            voted_at=vote_timestamp,
        )
