"""Vote orchestration.

``VotingService.cast_vote`` implements the five steps of the automation:

1. validate the input,
2. confirm the caller holds an active Mangasm+ subscription,
3. invoke the ``cast_mix_vote`` RPC, which atomically inserts the vote record
   and increments the counter,
4. translate a duplicate vote into ``409 Conflict``,
5. return the new count together with the recalculated queue rankings.

Atomicity and duplicate detection deliberately live in the database (see
``sql/001_mix_voting.sql``): a read-then-write in Python would race with
concurrent votes for the same mix.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from .config import Settings
from .errors import (
    DuplicateVoteError,
    InvalidInputError,
    MembershipRequiredError,
    MixNotFoundError,
    UpstreamUnavailableError,
    VotingClosedError,
)
from .logging_config import get_logger
from .models import QueueEntry
from .supabase_client import SupabaseClient, SupabaseError, SupabaseUnavailable

ACTIVE_MEMBERSHIP_STATUSES = frozenset({"active", "trialing", "past_due_grace"})
PLUS_TIERS = frozenset({"plus", "mangasm_plus", "mangasm+", "m+"})

_MAX_ID_LENGTH = 128

# Values the RPC may return in its `status` field.
_RPC_RECORDED = frozenset({"recorded", "ok", "success"})
_RPC_DUPLICATE = frozenset({"duplicate", "already_voted", "duplicate_vote"})
_RPC_UNKNOWN_MIX = frozenset({"unknown_mix", "mix_not_found", "not_found"})
_RPC_CLOSED = frozenset({"closed", "voting_closed"})

# PostgreSQL / PostgREST error codes worth interpreting.
_PG_UNIQUE_VIOLATION = "23505"
_PG_NO_DATA_FOUND = "P0002"


@dataclass(frozen=True)
class VoteResult:
    user_id: str
    mix_id: str
    vote_count: int
    vote_timestamp: datetime
    queue: list[QueueEntry]
    queue_degraded: bool = False


class VotingService:
    def __init__(self, settings: Settings, client: SupabaseClient) -> None:
        self._settings = settings
        self._client = client
        self._log = get_logger()

    # --------------------------------------------------------------- helpers

    @staticmethod
    def _validated_id(value: Any, field: str) -> str:
        if not isinstance(value, str):
            raise InvalidInputError(f"{field} must be a string")
        cleaned = value.strip()
        if not cleaned:
            raise InvalidInputError(f"{field} must not be empty")
        if len(cleaned) > _MAX_ID_LENGTH:
            raise InvalidInputError(f"{field} must be at most {_MAX_ID_LENGTH} characters")
        return cleaned

    @staticmethod
    def _is_active_plus(membership: Mapping[str, Any]) -> bool:
        tier = str(membership.get("tier") or "").strip().lower()
        status = str(membership.get("status") or "").strip().lower()
        if tier not in PLUS_TIERS or status not in ACTIVE_MEMBERSHIP_STATUSES:
            return False
        return not _is_expired(membership.get("current_period_end"))

    @staticmethod
    def _rank(rows: list[dict[str, Any]]) -> list[QueueEntry]:
        """Rank rows already ordered by ``vote_count`` descending.

        Ties share a rank and the following rank is skipped (standard
        competition ranking), so two mixes on 40 votes are both rank 1 and the
        next mix is rank 3.
        """
        entries: list[QueueEntry] = []
        previous_votes: int | None = None
        current_rank = 0
        for index, row in enumerate(rows, start=1):
            votes = _as_int(row.get("vote_count"), default=0)
            if votes != previous_votes:
                current_rank = index
                previous_votes = votes
            entries.append(
                QueueEntry(
                    rank=current_rank,
                    mix_id=str(row.get("mix_id", "")),
                    title=row.get("title"),
                    vote_count=votes,
                )
            )
        return entries

    # ------------------------------------------------------------------ steps

    async def _assert_active_plus(self, user_id: str) -> None:
        """Step 1/2: reject anyone without an active Mangasm+ subscription."""
        try:
            membership = await self._client.fetch_membership(user_id)
        except SupabaseUnavailable as exc:
            raise UpstreamUnavailableError("Membership lookup unavailable") from exc
        except SupabaseError as exc:
            raise _translate_supabase_error(exc, context="membership lookup") from exc

        if membership is None or not self._is_active_plus(membership):
            raise MembershipRequiredError()

    async def _invoke_rpc(self, user_id: str, mix_id: str) -> dict[str, Any]:
        """Steps 3/4: atomically record the vote, mapping RPC outcomes to errors."""
        try:
            payload = await self._client.cast_vote(user_id, mix_id)
        except SupabaseUnavailable as exc:
            raise UpstreamUnavailableError() from exc
        except SupabaseError as exc:
            raise _translate_supabase_error(exc, context="cast_mix_vote") from exc

        status = str(payload.get("status") or "").strip().lower()
        if status in _RPC_DUPLICATE:
            raise DuplicateVoteError()
        if status in _RPC_UNKNOWN_MIX:
            raise MixNotFoundError(f"Unknown mix_id {mix_id!r}")
        if status in _RPC_CLOSED:
            raise VotingClosedError()
        if status and status not in _RPC_RECORDED:
            raise UpstreamUnavailableError(f"Unexpected vote status {status!r}")
        return payload

    async def _queue_snapshot(self, mix_id: str, vote_count: int) -> tuple[list[QueueEntry], bool]:
        """Step 5: best-effort queue rankings.

        The vote is already committed at this point, so a ranking read failure
        must not turn into a ``500`` — that would invite a client retry which
        would then be rejected as a duplicate. The vote is reported as
        successful with ``queue_degraded`` set instead.
        """
        try:
            rows = await self._client.fetch_queue()
        except (SupabaseUnavailable, SupabaseError) as exc:
            self._log.error(
                "queue_snapshot_unavailable",
                extra={"mix_id": mix_id, "reason": str(exc)},
            )
            return [QueueEntry(rank=1, mix_id=mix_id, vote_count=vote_count)], True
        return self._rank(rows), False

    # ------------------------------------------------------------ entry point

    async def cast_vote(self, user_id: Any, mix_id: Any) -> VoteResult:
        started = time.monotonic()
        clean_user_id = self._validated_id(user_id, "user_id")
        clean_mix_id = self._validated_id(mix_id, "mix_id")
        vote_timestamp = datetime.now(timezone.utc)

        try:
            await self._assert_active_plus(clean_user_id)
            payload = await self._invoke_rpc(clean_user_id, clean_mix_id)
        except Exception as exc:
            self._log.warning(
                "mix_vote",
                extra={
                    "user_id": clean_user_id,
                    "mix_id": clean_mix_id,
                    "vote_timestamp": vote_timestamp.isoformat(),
                    "outcome": type(exc).__name__,
                    "duration_ms": round((time.monotonic() - started) * 1000, 2),
                },
            )
            raise

        vote_count = _as_int(payload.get("vote_count"), default=0)
        recorded_at = _as_datetime(payload.get("vote_timestamp")) or vote_timestamp
        queue, degraded = await self._queue_snapshot(clean_mix_id, vote_count)

        self._log.info(
            "mix_vote",
            extra={
                "user_id": clean_user_id,
                "mix_id": clean_mix_id,
                "vote_timestamp": recorded_at.isoformat(),
                "outcome": "recorded",
                "vote_count": vote_count,
                "queue_degraded": degraded,
                "duration_ms": round((time.monotonic() - started) * 1000, 2),
            },
        )
        return VoteResult(
            user_id=clean_user_id,
            mix_id=clean_mix_id,
            vote_count=vote_count,
            vote_timestamp=recorded_at,
            queue=queue,
            queue_degraded=degraded,
        )


def _translate_supabase_error(exc: SupabaseError, *, context: str):
    """Map a deterministic Supabase failure onto a domain error.

    Authentication failures mean *this service* is misconfigured, not that the
    caller is unauthorised, so they surface as ``500`` and never leak upstream
    detail to the client.
    """
    if exc.pg_code == _PG_UNIQUE_VIOLATION or exc.status_code == 409:
        return DuplicateVoteError()
    if exc.pg_code == _PG_NO_DATA_FOUND or exc.status_code == 404:
        return MixNotFoundError("Unknown mix_id")
    return UpstreamUnavailableError(f"{context} failed")


def _as_int(value: Any, *, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


def _is_expired(period_end: Any) -> bool:
    """``True`` only when a parsable period end lies in the past."""
    parsed = _as_datetime(period_end)
    if parsed is None:
        return False
    return parsed < datetime.now(timezone.utc)
