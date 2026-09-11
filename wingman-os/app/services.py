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

"""Supabase PostgREST client with retry for the voting engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Protocol

import httpx
from tenacity import (
    RetryError,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.config import Settings
from app.exceptions import (
    DuplicateVoteError,
    ExternalServiceError,
    MembershipRequiredError,
    MixNotFoundError,
)
from app.models import MixRanking


class MixVotingGateway(Protocol):
    async def has_active_mangasm_plus(self, user_id: str) -> bool:
        ...

    async def cast_mix_vote(self, user_id: str, mix_id: str) -> "CastVoteResult":
        ...

    async def aclose(self) -> None:
        ...


@dataclass
class CastVoteResult:
    vote_count: int
    rankings: list[MixRanking] = field(default_factory=list)
    mix: Optional[dict[str, Any]] = None


class RetryableHttpError(ExternalServiceError):
    """Transient HTTP failure that should be retried."""


def _rankings_from_payload(raw: Any) -> list[MixRanking]:
    if not isinstance(raw, list):
        return []
    rankings: list[MixRanking] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        rankings.append(
            MixRanking(
                rank=int(item.get("rank") or 0),
                mix_id=str(item.get("mix_id") or item.get("id") or ""),
                title=str(item.get("title") or ""),
                vote_count=int(item.get("vote_count") or 0),
                djs=list(item.get("djs") or []),
                live_sessions=list(item.get("live_sessions") or []),
            )
        )
    return rankings


class SupabaseMixVotingGateway:
    """Talks to Supabase REST + RPC. The service role key never leaves this process."""

    def __init__(self, settings: Settings, client: Optional[httpx.AsyncClient] = None):
        self._settings = settings
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=settings.supabase_url,
            timeout=settings.http_timeout_seconds,
            headers={
                "apikey": settings.supabase_key,
                "Authorization": f"Bearer {settings.supabase_key}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        )
        self._retry_attempts = settings.supabase_retry_attempts

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def has_active_mangasm_plus(self, user_id: str) -> bool:
        payload = await self._request_with_retry(
            "GET",
            "/rest/v1/mangasm_plus_subscriptions",
            params={
                "user_id": f"eq.{user_id}",
                "status": "eq.active",
                "select": "user_id,status,current_period_end",
                "limit": "1",
            },
        )
        if not isinstance(payload, list) or not payload:
            return False
        row = payload[0]
        period_end = row.get("current_period_end")
        if not period_end:
            return str(row.get("status")) == "active"
        # Period end is compared in Postgres via RPC as well; REST rows with
        # status=active are treated as current unless explicitly expired.
        return str(row.get("status")) == "active"

    async def cast_mix_vote(self, user_id: str, mix_id: str) -> CastVoteResult:
        payload = await self._request_with_retry(
            "POST",
            "/rest/v1/rpc/cast_mix_vote",
            json={"p_user_id": user_id, "p_mix_id": mix_id},
        )
        if not isinstance(payload, dict):
            raise ExternalServiceError("cast_mix_vote returned an unexpected payload")

        status = str(payload.get("status") or "")
        rankings = _rankings_from_payload(payload.get("rankings"))
        vote_count = int(payload.get("vote_count") or 0)

        if status == "already_voted":
            raise DuplicateVoteError(user_id=user_id, mix_id=mix_id, vote_count=vote_count, rankings=rankings)
        if status == "mix_not_found":
            raise MixNotFoundError("mix_id is not a Friday/Saturday live mix", user_id=user_id, mix_id=mix_id)
        if status == "membership_required":
            raise MembershipRequiredError("M+ Membership Required", user_id=user_id, mix_id=mix_id)
        if status != "ok":
            raise ExternalServiceError(f"cast_mix_vote returned status={status}")

        return CastVoteResult(
            vote_count=vote_count,
            rankings=rankings,
            mix=payload.get("mix") if isinstance(payload.get("mix"), dict) else None,
        )

    async def _request_with_retry(
        self,
        method: str,
        path: str,
        *,
        params: Optional[dict[str, str]] = None,
        json: Optional[dict[str, Any]] = None,
    ) -> Any:
        retrying = retry(
            stop=stop_after_attempt(self._retry_attempts),
            wait=wait_exponential(multiplier=0.2, min=0.05, max=1),
            retry=retry_if_exception_type((httpx.TransportError, RetryableHttpError)),
            reraise=True,
        )(self._request_once)
        try:
            return await retrying(method, path, params=params, json=json)
        except RetryError as exc:
            last = exc.last_attempt.exception() if exc.last_attempt else exc
            raise ExternalServiceError(str(last) or "Supabase request failed") from exc
        except (httpx.TransportError, RetryableHttpError) as exc:
            raise ExternalServiceError(str(exc) or "Supabase request failed") from exc

    async def _request_once(
        self,
        method: str,
        path: str,
        *,
        params: Optional[dict[str, str]] = None,
        json: Optional[dict[str, Any]] = None,
    ) -> Any:
        try:
            response = await self._client.request(method, path, params=params, json=json)
        except httpx.TransportError as exc:
            raise RetryableHttpError(f"Supabase transport error: {exc}") from exc

        if response.status_code >= 500:
            raise RetryableHttpError(f"Supabase HTTP {response.status_code}")
        if response.status_code == 429:
            raise RetryableHttpError("Supabase rate limited")
        if response.status_code >= 400:
            raise ExternalServiceError(f"Supabase HTTP {response.status_code}")

        if not response.content:
            return None
        try:
            return response.json()
        except ValueError as exc:
            raise ExternalServiceError("Supabase returned non-JSON") from exc
