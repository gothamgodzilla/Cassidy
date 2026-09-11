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

"""Minimal async client for the Supabase REST (PostgREST) API with bounded retries.

Only the two operations the voting engine needs are exposed: table reads and RPC calls.
Transient failures (network errors, timeouts, HTTP 5xx / 429) are retried up to
``Settings.supabase_max_attempts`` times with exponential backoff; anything else is
surfaced immediately as :class:`SupabaseRequestError` so callers can map it to a
domain outcome (e.g. a 409 from a unique-violation inside the RPC).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)

_RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})


class SupabaseError(Exception):
    """Base class for Supabase transport/API failures."""


class SupabaseUnavailableError(SupabaseError):
    """Raised once every retry attempt has failed with a transient error."""

    def __init__(self, operation: str, attempts: int, last_error: str):
        self.operation = operation
        self.attempts = attempts
        self.last_error = last_error
        super().__init__(f"{operation} failed after {attempts} attempt(s): {last_error}")


class SupabaseRequestError(SupabaseError):
    """A non-retryable (4xx) response. ``code`` is the PostgREST/PostgreSQL error code, if any."""

    def __init__(self, status_code: int, code: str | None, message: str, details: Any = None, hint: Any = None):
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details
        self.hint = hint
        super().__init__(f"HTTP {status_code} [{code}]: {message}")


class SupabaseClient:
    def __init__(
        self,
        settings: Settings,
        http: httpx.AsyncClient | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        key = settings.supabase_key.get_secret_value()
        self._base_url = f"{settings.supabase_url}/rest/v1"
        self._headers = {
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        self._max_attempts = settings.supabase_max_attempts
        self._backoff = settings.supabase_retry_backoff_seconds
        self._sleep = sleep
        self._owns_http = http is None
        self._http = http or httpx.AsyncClient(timeout=settings.supabase_timeout_seconds)

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    async def select(self, table: str, params: Mapping[str, str]) -> list[dict[str, Any]]:
        """``GET /rest/v1/<table>?<params>`` returning the JSON row list."""
        data = await self._request("GET", f"/{table}", params=dict(params), operation=f"select {table}")
        if not isinstance(data, list):
            raise SupabaseError(f"expected a row list from {table}, got {type(data).__name__}")
        return data

    async def rpc(self, function: str, params: Mapping[str, Any]) -> Any:
        """``POST /rest/v1/rpc/<function>`` returning the function's JSON result."""
        return await self._request("POST", f"/rpc/{function}", json=dict(params), operation=f"rpc {function}")

    async def _request(
        self,
        method: str,
        path: str,
        *,
        operation: str,
        json: Mapping[str, Any] | None = None,
        params: Mapping[str, str] | None = None,
    ) -> Any:
        last_error = "unknown error"
        for attempt in range(1, self._max_attempts + 1):
            try:
                response = await self._http.request(
                    method, f"{self._base_url}{path}", headers=self._headers, json=json, params=params
                )
            except httpx.HTTPError as exc:
                last_error = f"{type(exc).__name__}: {exc}"
            else:
                if response.status_code < 400:
                    return _parse_body(response)
                if response.status_code not in _RETRYABLE_STATUS:
                    raise _request_error(response)
                last_error = f"HTTP {response.status_code}"

            if attempt < self._max_attempts:
                delay = self._backoff * (2 ** (attempt - 1))
                logger.warning(
                    "supabase call failed, retrying",
                    extra={"operation": operation, "attempt": attempt, "max_attempts": self._max_attempts, "error": last_error, "retry_in_seconds": delay},
                )
                await self._sleep(delay)

        logger.error(
            "supabase call exhausted retries",
            extra={"operation": operation, "attempts": self._max_attempts, "error": last_error},
        )
        raise SupabaseUnavailableError(operation, self._max_attempts, last_error)


def _parse_body(response: httpx.Response) -> Any:
    if not response.content:
        return None
    try:
        return response.json()
    except ValueError as exc:
        raise SupabaseError(f"non-JSON response from Supabase (HTTP {response.status_code})") from exc


def _request_error(response: httpx.Response) -> SupabaseRequestError:
    try:
        body = response.json()
    except ValueError:
        body = {}
    if not isinstance(body, dict):
        body = {}
    return SupabaseRequestError(
        status_code=response.status_code,
        code=body.get("code"),
        message=str(body.get("message") or response.reason_phrase or "request failed"),
        details=body.get("details"),
        hint=body.get("hint"),
    )
