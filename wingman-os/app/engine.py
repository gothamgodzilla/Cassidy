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

"""Vote orchestration: membership gate, atomic RPC, structured logs."""

from datetime import datetime, timezone
from typing import Optional

from app.catalog import get_mix_catalog_entry
from app.exceptions import MembershipRequiredError
from app.logging_config import configure_logging, vote_extra
from app.models import VoteResponse
from app.services import MixVotingGateway

logger = configure_logging()


class LiveMixVotingEngine:
    def __init__(self, gateway: MixVotingGateway):
        self._gateway = gateway

    async def cast_vote(self, user_id: str, mix_id: str) -> VoteResponse:
        vote_timestamp = datetime.now(timezone.utc)
        extra = vote_extra(user_id, mix_id, vote_timestamp.isoformat())
        logger.info("vote_requested", extra={**extra, "event": "vote_requested"})

        if not await self._gateway.has_active_mangasm_plus(user_id):
            logger.info("vote_forbidden", extra={**extra, "event": "vote_forbidden"})
            raise MembershipRequiredError("M+ Membership Required", user_id=user_id, mix_id=mix_id)

        result = await self._gateway.cast_mix_vote(user_id, mix_id)
        mix = result.mix or get_mix_catalog_entry(mix_id)

        logger.info("vote_cast", extra={**extra, "event": "vote_cast"})
        return VoteResponse(
            status="ok",
            user_id=user_id,
            mix_id=mix_id,
            vote_count=result.vote_count,
            vote_timestamp=vote_timestamp,
            rankings=result.rankings,
            mix=mix,
        )


_engine: Optional[LiveMixVotingEngine] = None


def get_engine() -> LiveMixVotingEngine:
    if _engine is None:
        raise RuntimeError("LiveMixVotingEngine is not initialized")
    return _engine


def set_engine(engine: LiveMixVotingEngine) -> None:
    global _engine
    _engine = engine
