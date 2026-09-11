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

"""Live mix voting workflow.

1. Confirm the caller holds an active Mangasm+ subscription (``mangasm_subscriptions`` table).
2. Call the ``cast_mix_vote`` RPC, which - inside a single transaction - records the vote
   (unique per user/mix), increments the mix's counter and returns the fresh queue rankings.
3. Translate the outcome into a :class:`VoteResult` or a domain error.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.errors import DuplicateVoteError, MembershipRequiredError, MixNotFoundError, UpstreamUnavailableError
from app.schemas import RankingEntry
from app.supabase_client import SupabaseClient, SupabaseError, SupabaseRequestError, SupabaseUnavailableError

logger = logging.getLogger(__name__)

SUBSCRIPTIONS_TABLE = "mangasm_subscriptions"
CAST_VOTE_RPC = "cast_mix_vote"

PLUS_TIERS = frozenset({"plus", "mangasm_plus", "m+"})
ACTIVE_STATUSES = frozenset({"active", "trialing"})

# Error markers raised by the cast_mix_vote SQL function (see supabase/migrations).
_RPC_DUPLICATE_MESSAGE = "DUPLICATE_VOTE"
_RPC_MIX_NOT_FOUND_MESSAGE = "MIX_NOT_FOUND"
_PG_UNIQUE_VIOLATION = "23505"


@dataclass(frozen=True)
class VoteResult:
    user_id: str
    mix_id: str
    vote_count: int
    vote_timestamp: datetime
    rankings: list[RankingEntry]


class VotingService:
    def __init__(self, client: SupabaseClient, rankings_limit: int = 20):
        self._client = client
        self._rankings_limit = rankings_limit

    async def cast_vote(self, user_id: str, mix_id: str) -> VoteResult:
        vote_timestamp = datetime.now(timezone.utc)
        audit = {"user_id": user_id, "mix_id": mix_id, "vote_timestamp": vote_timestamp.isoformat()}

        try:
            await self._ensure_plus_member(user_id, audit)
            payload = await self._cast(user_id, mix_id, vote_timestamp, audit)
            result = VoteResult(
                user_id=user_id,
                mix_id=mix_id,
                vote_count=_as_int(payload.get("vote_count"), "vote_count"),
                vote_timestamp=vote_timestamp,
                rankings=_parse_rankings(payload.get("rankings")),
            )
        except SupabaseUnavailableError as exc:
            logger.error("vote rejected: supabase unavailable", extra={**audit, "outcome": "upstream_unavailable", "error": str(exc)})
            raise UpstreamUnavailableError() from exc
        except SupabaseError as exc:
            logger.error("vote rejected: unexpected supabase response", extra={**audit, "outcome": "upstream_error", "error": str(exc)})
            raise UpstreamUnavailableError() from exc

        logger.info("vote cast", extra={**audit, "outcome": "accepted", "vote_count": result.vote_count})
        return result

    async def _ensure_plus_member(self, user_id: str, audit: dict[str, Any]) -> None:
        rows = await self._client.select(
            SUBSCRIPTIONS_TABLE,
            {"select": "tier,status,current_period_end", "user_id": f"eq.{user_id}", "limit": "1"},
        )
        if not rows or not is_active_plus(rows[0]):
            logger.info(
                "vote rejected: membership required",
                extra={**audit, "outcome": "membership_required", "subscription_found": bool(rows)},
            )
            raise MembershipRequiredError()

    async def _cast(self, user_id: str, mix_id: str, vote_timestamp: datetime, audit: dict[str, Any]) -> dict[str, Any]:
        try:
            payload = await self._client.rpc(
                CAST_VOTE_RPC,
                {
                    "p_user_id": user_id,
                    "p_mix_id": mix_id,
                    "p_voted_at": vote_timestamp.isoformat(),
                    "p_rankings_limit": self._rankings_limit,
                },
            )
        except SupabaseRequestError as exc:
            if exc.code == _PG_UNIQUE_VIOLATION or exc.status_code == 409 or exc.message == _RPC_DUPLICATE_MESSAGE:
                logger.info("vote rejected: duplicate", extra={**audit, "outcome": "duplicate"})
                raise DuplicateVoteError() from exc
            if exc.status_code == 404 or exc.message == _RPC_MIX_NOT_FOUND_MESSAGE:
                logger.info("vote rejected: mix not found", extra={**audit, "outcome": "mix_not_found"})
                raise MixNotFoundError() from exc
            raise
        if not isinstance(payload, dict):
            raise SupabaseError(f"{CAST_VOTE_RPC} returned {type(payload).__name__}, expected an object")
        return payload


def is_active_plus(subscription: dict[str, Any]) -> bool:
    """A member is Mangasm+ when the tier is a plus tier, the status is live and the period has not lapsed."""
    tier = str(subscription.get("tier") or "").strip().lower()
    status = str(subscription.get("status") or "").strip().lower()
    if tier not in PLUS_TIERS or status not in ACTIVE_STATUSES:
        return False
    period_end = subscription.get("current_period_end")
    if period_end in (None, ""):
        return True
    try:
        ends_at = datetime.fromisoformat(str(period_end).replace("Z", "+00:00"))
    except ValueError:
        return False
    if ends_at.tzinfo is None:
        ends_at = ends_at.replace(tzinfo=timezone.utc)
    return ends_at > datetime.now(timezone.utc)


def _parse_rankings(raw: Any) -> list[RankingEntry]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise SupabaseError("rankings must be a list")
    entries: list[RankingEntry] = []
    for position, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            raise SupabaseError("ranking entries must be objects")
        entries.append(
            RankingEntry(
                rank=_as_int(item.get("rank", position), "rank"),
                mix_id=str(item.get("mix_id")),
                title=item.get("title"),
                artist=item.get("artist"),
                session_slot=item.get("session_slot"),
                vote_count=_as_int(item.get("vote_count"), "vote_count"),
            )
        )
    return entries


def _as_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise SupabaseError(f"{field} missing or not numeric in RPC response")
    try:
        return int(value)
    except ValueError as exc:
        raise SupabaseError(f"{field} is not numeric in RPC response") from exc
