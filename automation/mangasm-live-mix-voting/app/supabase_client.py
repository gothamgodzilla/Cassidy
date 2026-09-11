"""Async Supabase (PostgREST) client used by the voting engine.

Only three operations are needed: read a membership row, invoke the
``cast_mix_vote`` RPC, and read the queue ranking view.

Transient failures — connection errors, timeouts, ``429`` and ``5xx`` — are
retried with exponential backoff plus jitter, up to
:attr:`~app.config.Settings.max_retries` times. Deterministic failures (any
other ``4xx``) are surfaced immediately, because retrying them would only
add latency.
"""

from __future__ import annotations

import asyncio
import random
from typing import Any, Awaitable, Callable, Mapping

import httpx

from .config import Settings
from .logging_config import get_logger

RETRYABLE_STATUS_CODES = frozenset({408, 425, 429, 500, 502, 503, 504})

SleepFn = Callable[[float], Awaitable[None]]


class SupabaseError(RuntimeError):
    """A Supabase response that the caller has to interpret.

    ``status_code`` is the HTTP status returned by PostgREST and ``payload`` is
    the decoded JSON body when one was present (PostgREST reports the
    PostgreSQL ``code``, e.g. ``23505`` for a unique violation).
    """

    def __init__(self, status_code: int, payload: Any, message: str | None = None) -> None:
        super().__init__(message or f"Supabase returned HTTP {status_code}")
        self.status_code = status_code
        self.payload = payload

    @property
    def pg_code(self) -> str | None:
        if isinstance(self.payload, Mapping):
            code = self.payload.get("code")
            if code is not None:
                return str(code)
        return None


class SupabaseUnavailable(RuntimeError):
    """Raised when a call still fails after the retry budget is exhausted."""


class SupabaseClient:
    """Thin, retrying wrapper over the Supabase REST surface."""

    def __init__(
        self,
        settings: Settings,
        client: httpx.AsyncClient | None = None,
        sleep: SleepFn | None = None,
    ) -> None:
        self._settings = settings
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=settings.rest_url,
            timeout=settings.request_timeout_seconds,
            headers=self._auth_headers(settings),
        )
        self._sleep: SleepFn = sleep or asyncio.sleep
        self._log = get_logger()

    @staticmethod
    def _auth_headers(settings: Settings) -> dict[str, str]:
        return {
            "apikey": settings.supabase_key,
            "Authorization": f"Bearer {settings.supabase_key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    # ------------------------------------------------------------------ core

    def _backoff_delay(self, attempt: int) -> float:
        """Full-jitter exponential backoff for retry number ``attempt`` (0-based)."""
        ceiling = min(
            self._settings.backoff_base_seconds * (2**attempt),
            self._settings.backoff_max_seconds,
        )
        return random.uniform(0.0, ceiling)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        operation: str,
        params: Mapping[str, str] | None = None,
        json_body: Mapping[str, Any] | None = None,
    ) -> Any:
        """Perform a request, retrying transient failures.

        Returns the decoded JSON body. Raises :class:`SupabaseError` for
        deterministic failures and :class:`SupabaseUnavailable` once the retry
        budget is spent.
        """
        attempts = self._settings.max_retries + 1
        last_reason = "unknown"

        for attempt in range(attempts):
            try:
                response = await self._client.request(
                    method, path, params=params, json=json_body
                )
            except httpx.HTTPError as exc:
                last_reason = f"{type(exc).__name__}: {exc}"
            else:
                if response.status_code not in RETRYABLE_STATUS_CODES:
                    if response.is_success:
                        return self._decode(response)
                    raise SupabaseError(response.status_code, self._decode_safe(response))
                last_reason = f"HTTP {response.status_code}"

            is_last = attempt == attempts - 1
            self._log.warning(
                "supabase_call_failed",
                extra={
                    "operation": operation,
                    "attempt": attempt + 1,
                    "attempts_allowed": attempts,
                    "reason": last_reason,
                    "will_retry": not is_last,
                },
            )
            if is_last:
                break
            await self._sleep(self._backoff_delay(attempt))

        raise SupabaseUnavailable(
            f"{operation} failed after {attempts} attempt(s): {last_reason}"
        )

    @staticmethod
    def _decode(response: httpx.Response) -> Any:
        if not response.content:
            return None
        return response.json()

    @staticmethod
    def _decode_safe(response: httpx.Response) -> Any:
        try:
            return SupabaseClient._decode(response)
        except ValueError:
            return {"message": response.text}

    # ------------------------------------------------------------ operations

    async def fetch_membership(self, user_id: str) -> dict[str, Any] | None:
        """Return the membership row for ``user_id``, or ``None`` if absent."""
        rows = await self._request(
            "GET",
            f"/{self._settings.membership_table}",
            operation="fetch_membership",
            params={
                "select": "user_id,tier,status,current_period_end",
                "user_id": f"eq.{user_id}",
                "limit": "1",
            },
        )
        if isinstance(rows, list) and rows:
            first = rows[0]
            return first if isinstance(first, dict) else None
        return None

    async def cast_vote(self, user_id: str, mix_id: str) -> dict[str, Any]:
        """Invoke the ``cast_mix_vote`` RPC.

        The RPC owns the atomic "insert vote record + increment counter"
        transaction and returns a JSON object with a ``status`` field.
        """
        payload = await self._request(
            "POST",
            f"/rpc/{self._settings.vote_rpc}",
            operation="cast_mix_vote",
            json_body={"p_user_id": user_id, "p_mix_id": mix_id},
        )
        # A `returns jsonb` function yields the object directly; a `returns
        # setof` function yields a single-element array.
        if isinstance(payload, list):
            payload = payload[0] if payload else None
        if not isinstance(payload, dict):
            raise SupabaseError(200, payload, "cast_mix_vote returned an unexpected payload")
        return payload

    async def fetch_queue(self, limit: int | None = None) -> list[dict[str, Any]]:
        """Return the current queue ordered by votes, highest first."""
        rows = await self._request(
            "GET",
            f"/{self._settings.queue_view}",
            operation="fetch_queue",
            params={
                "select": "mix_id,title,vote_count",
                "order": "vote_count.desc,mix_id.asc",
                "limit": str(limit or self._settings.queue_size),
            },
        )
        if not isinstance(rows, list):
            return []
        return [row for row in rows if isinstance(row, dict)]
