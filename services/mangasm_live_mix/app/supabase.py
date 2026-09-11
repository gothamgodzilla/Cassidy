# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements. See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership. The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License. You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Minimal asynchronous Supabase Data API client."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import httpx


@dataclass(frozen=True)
class SupabaseError(Exception):
    """A non-success response from Supabase."""

    status_code: int
    payload: Any


class SupabaseUnavailable(Exception):
    """Supabase remained unavailable after all retry attempts."""


class SupabaseClient:
    """Call the Supabase Data API with bounded transient retries."""

    def __init__(
        self,
        url: str,
        key: str,
        *,
        max_retries: int = 3,
        timeout_seconds: float = 5.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = url.rstrip("/")
        self._max_retries = max_retries
        self._client = httpx.AsyncClient(
            headers={
                "apikey": key,
                "authorization": f"Bearer {key}",
                "content-type": "application/json",
            },
            timeout=httpx.Timeout(timeout_seconds),
            transport=transport,
        )

    async def close(self) -> None:
        """Close the underlying connection pool."""

        await self._client.aclose()

    async def active_subscription(self, user_id: str) -> bool:
        """Return whether a current active Mangasm+ subscription exists."""

        encoded_user_id = quote(user_id, safe="")
        records = await self._request(
            "GET",
            (
                "/rest/v1/mangasm_plus_subscriptions"
                f"?select=user_id,expires_at&user_id=eq.{encoded_user_id}"
                "&status=eq.active"
                "&limit=1"
            ),
        )
        if not isinstance(records, list) or not records:
            return False
        expires_at = records[0].get("expires_at")
        if expires_at is None:
            return True
        try:
            expiration = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        except (AttributeError, ValueError):
            raise SupabaseUnavailable("Supabase returned an invalid subscription expiration")
        return expiration > datetime.now(timezone.utc)

    async def cast_vote(self, user_id: str, mix_id: str) -> dict[str, Any]:
        """Invoke the atomic vote RPC and return its response."""

        result = await self._request(
            "POST",
            "/rest/v1/rpc/cast_mix_vote",
            {"p_user_id": user_id, "p_mix_id": mix_id},
        )
        if not isinstance(result, dict):
            raise SupabaseUnavailable("cast_mix_vote returned an invalid response")
        return result

    async def _request(
        self, method: str, path: str, json: dict[str, Any] | None = None
    ) -> Any:
        last_error: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                response = await self._client.request(method, f"{self._base_url}{path}", json=json)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = exc
            else:
                if response.status_code < 400:
                    try:
                        return response.json()
                    except ValueError as exc:
                        raise SupabaseUnavailable("Supabase returned invalid JSON") from exc

                try:
                    payload: Any = response.json()
                except ValueError:
                    payload = {"message": response.text}

                if response.status_code not in {408, 429} and response.status_code < 500:
                    raise SupabaseError(response.status_code, payload)
                last_error = SupabaseError(response.status_code, payload)

            if attempt < self._max_retries:
                await asyncio.sleep(0.1 * (2**attempt))

        raise SupabaseUnavailable("Supabase request failed after 3 retries") from last_error
