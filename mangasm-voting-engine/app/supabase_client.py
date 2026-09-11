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

"""Thin Supabase PostgREST/RPC client with bounded retries.

Uses plain httpx instead of supabase-py to keep the runtime dependency
footprint minimal. All upstream 5xx / network failures are retried up to
``max_retries`` with exponential backoff; 4xx responses are returned to the
caller without retry so duplicate-vote (409) and auth errors stay precise.
"""

import logging
import time
from typing import Any, Callable, TypeVar

import httpx

from .config import Settings

logger = logging.getLogger("mangasm.voting.supabase")

T = TypeVar("T")

MEMBERSHIP_TABLE = "m_plus_members"
VOTES_TABLE = "mix_votes"
MIXES_TABLE = "mixes"
CAST_VOTE_RPC = "cast_mix_vote"


class UpstreamError(RuntimeError):
    """Supabase (or network) failed after exhausting retries."""


class DuplicateVoteError(ValueError):
    """Caller already voted for this mix."""


class MixNotFoundError(ValueError):
    """Requested mix_id does not exist."""


def _is_retryable_status(status: int) -> bool:
    return 500 <= status <= 599


def call_with_retry(
    operation: Callable[[], T],
    *,
    max_retries: int,
    base_delay: float,
    what: str,
) -> T:
    """Run ``operation`` with retries on UpstreamError."""
    last_error: UpstreamError | None = None
    for attempt in range(1, max_retries + 1):
        try:
            return operation()
        except UpstreamError as exc:
            last_error = exc
            if attempt == max_retries:
                break
            delay = base_delay * (2 ** (attempt - 1))
            logger.warning(
                "supabase %s attempt %d/%d failed, retrying in %.2fs: %s",
                what,
                attempt,
                max_retries,
                delay,
                exc,
            )
            time.sleep(delay)
    raise last_error or UpstreamError(f"supabase {what} failed")


class SupabaseClient:
    def __init__(self, settings: Settings, http: httpx.Client | None = None) -> None:
        self._settings = settings
        self._http = http or httpx.Client(
            base_url=settings.supabase_url.rstrip("/"),
            timeout=settings.upstream_timeout_seconds,
            headers={
                "apikey": settings.supabase_key,
                "Authorization": f"Bearer {settings.supabase_key}",
                "Content-Type": "application/json",
            },
        )

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            response = self._http.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise UpstreamError(f"network error calling {path}: {exc}") from exc
        if _is_retryable_status(response.status_code):
            raise UpstreamError(f"{path} returned {response.status_code}: {response.text[:500]}")
        return response

    def _call(self, what: str, method: str, path: str, **kwargs: Any) -> httpx.Response:
        return call_with_retry(
            lambda: self._request(method, path, **kwargs),
            max_retries=self._settings.max_retries,
            base_delay=self._settings.retry_base_delay_seconds,
            what=what,
        )

    def has_active_subscription(self, user_id: str) -> bool:
        """True when user_id holds an active Mangasm+ membership."""
        response = self._call(
            "membership-check",
            "GET",
            f"/rest/v1/{MEMBERSHIP_TABLE}",
            params={"user_id": f"eq.{user_id}", "status": "eq.active", "select": "user_id"},
        )
        if response.status_code == 200:
            return len(response.json()) > 0
        raise UpstreamError(f"membership check returned {response.status_code}: {response.text[:500]}")

    def has_existing_vote(self, user_id: str, mix_id: str) -> bool:
        response = self._call(
            "duplicate-check",
            "GET",
            f"/rest/v1/{VOTES_TABLE}",
            params={"user_id": f"eq.{user_id}", "mix_id": f"eq.{mix_id}", "select": "user_id"},
        )
        if response.status_code == 200:
            return len(response.json()) > 0
        raise UpstreamError(f"duplicate check returned {response.status_code}: {response.text[:500]}")

    def cast_vote_rpc(self, user_id: str, mix_id: str) -> dict[str, Any]:
        """Atomically insert the vote row and increment the mix counter.

        The Postgres function enforces UNIQUE(user_id, mix_id); a unique
        violation surfaces as PostgREST 409 and is mapped to
        DuplicateVoteError. Missing mixes surface as 404 -> MixNotFoundError.
        """
        response = self._call(
            "cast_mix_vote",
            "POST",
            f"/rest/v1/rpc/{CAST_VOTE_RPC}",
            json={"p_user_id": user_id, "p_mix_id": mix_id},
        )
        if response.status_code in (200, 201):
            payload = response.json()
            if isinstance(payload, list):
                payload = payload[0] if payload else {}
            return payload if isinstance(payload, dict) else {"vote_count": int(payload)}
        body = response.text
        if response.status_code == 409 or "duplicate" in body.lower() or "23505" in body:
            raise DuplicateVoteError(f"user {user_id} already voted for mix {mix_id}")
        if response.status_code == 404 or "mix_not_found" in body.lower():
            raise MixNotFoundError(f"mix {mix_id} not found")
        raise UpstreamError(f"cast_mix_vote returned {response.status_code}: {body[:500]}")

    def get_rankings(self, limit: int) -> list[dict[str, Any]]:
        response = self._call(
            "rankings",
            "GET",
            f"/rest/v1/{MIXES_TABLE}",
            params={
                "select": "mix_id,title,votes",
                "order": "votes.desc",
                "limit": str(limit),
            },
        )
        if response.status_code == 200:
            rows = response.json()
            ranked = []
            for index, row in enumerate(rows, start=1):
                ranked.append(
                    {
                        "mix_id": row.get("mix_id", ""),
                        "title": row.get("title", ""),
                        "votes": int(row.get("votes", 0)),
                        "rank": index,
                    }
                )
            return ranked
        raise UpstreamError(f"rankings returned {response.status_code}: {response.text[:500]}")
