"""Shared fixtures.

Supabase is replaced with an in-memory PostgREST double wired in through
``httpx.MockTransport``, so the real :class:`~app.supabase_client.SupabaseClient`
(including its retry loop), the real service and the real FastAPI routing are
all exercised end to end. Only the network hop is faked.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import httpx
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings  # noqa: E402
from app.logging_config import LOGGER_NAME, configure_logging  # noqa: E402
from app.main import create_app  # noqa: E402
from app.supabase_client import SupabaseClient  # noqa: E402

SUPABASE_URL = "https://project.supabase.co"
SUPABASE_KEY = "service-role-key-do-not-log"

PLUS_USER = "user-plus-1"
FREE_USER = "user-free-1"
MIX_ID = "berko-golden-hour-progressive-house"
CLOSED_MIX_ID = "closed-mix"


def _now() -> datetime:
    return datetime.now(timezone.utc)


class FakeSupabase:
    """Minimal PostgREST stand-in with the RPC's transactional semantics."""

    def __init__(self) -> None:
        self.memberships: dict[str, dict[str, Any]] = {
            PLUS_USER: {
                "user_id": PLUS_USER,
                "tier": "plus",
                "status": "active",
                "current_period_end": (_now() + timedelta(days=20)).isoformat(),
            },
            FREE_USER: {
                "user_id": FREE_USER,
                "tier": "free",
                "status": "active",
                "current_period_end": None,
            },
        }
        self.mixes: dict[str, dict[str, Any]] = {
            MIX_ID: {
                "mix_id": MIX_ID,
                "title": "Berko - Golden Hour Progressive House | 4K DJ Set",
                "vote_count": 41,
                "open": True,
            },
            "matryxx-warmup": {
                "mix_id": "matryxx-warmup",
                "title": "MATRYXX - Warmup",
                "vote_count": 41,
                "open": True,
            },
            CLOSED_MIX_ID: {
                "mix_id": CLOSED_MIX_ID,
                "title": "Last week",
                "vote_count": 7,
                "open": False,
            },
        }
        self.votes: set[tuple[str, str]] = set()

        # operation -> number of remaining transient failures to inject.
        self.transient_failures: dict[str, int] = {}
        # operation -> httpx.Response returned instead of normal handling.
        self.forced_responses: dict[str, httpx.Response] = {}
        self.calls: list[str] = []
        self.seen_headers: list[httpx.Headers] = []

    def fail_transiently(self, operation: str, times: int) -> None:
        self.transient_failures[operation] = times

    def force(self, operation: str, status_code: int, payload: Any) -> None:
        self.forced_responses[operation] = httpx.Response(status_code, json=payload)

    # ------------------------------------------------------------------ routing

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/rpc/cast_mix_vote"):
            operation = "cast_mix_vote"
        elif "/mangasm_memberships" in path:
            operation = "fetch_membership"
        elif "/live_mix_queue" in path:
            operation = "fetch_queue"
        else:  # pragma: no cover - guards against a typo in a route
            return httpx.Response(404, json={"message": f"no route for {path}"})

        self.calls.append(operation)
        self.seen_headers.append(request.headers)

        remaining = self.transient_failures.get(operation, 0)
        if remaining > 0:
            self.transient_failures[operation] = remaining - 1
            return httpx.Response(503, json={"message": "service unavailable"})

        forced = self.forced_responses.get(operation)
        if forced is not None:
            return httpx.Response(
                forced.status_code, content=forced.content, headers={"content-type": "application/json"}
            )

        if operation == "fetch_membership":
            return self._membership(request)
        if operation == "cast_mix_vote":
            return self._cast_vote(request)
        return self._queue()

    # --------------------------------------------------------------- endpoints

    def _membership(self, request: httpx.Request) -> httpx.Response:
        wanted = request.url.params.get("user_id", "")
        user_id = wanted.removeprefix("eq.")
        row = self.memberships.get(user_id)
        return httpx.Response(200, json=[row] if row else [])

    def _cast_vote(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        user_id = body.get("p_user_id")
        mix_id = body.get("p_mix_id")

        mix = self.mixes.get(mix_id)
        if mix is None:
            return httpx.Response(200, json={"status": "unknown_mix", "mix_id": mix_id})
        if not mix["open"]:
            return httpx.Response(200, json={"status": "closed", "mix_id": mix_id})

        key = (user_id, mix_id)
        if key in self.votes:
            return httpx.Response(
                200,
                json={
                    "status": "duplicate",
                    "mix_id": mix_id,
                    "vote_count": mix["vote_count"],
                },
            )

        self.votes.add(key)
        mix["vote_count"] += 1
        return httpx.Response(
            200,
            json={
                "status": "recorded",
                "mix_id": mix_id,
                "vote_count": mix["vote_count"],
                "vote_timestamp": _now().isoformat(),
            },
        )

    def _queue(self) -> httpx.Response:
        rows = sorted(
            (
                {"mix_id": m["mix_id"], "title": m["title"], "vote_count": m["vote_count"]}
                for m in self.mixes.values()
            ),
            key=lambda row: (-row["vote_count"], row["mix_id"]),
        )
        return httpx.Response(200, json=rows)


@pytest.fixture
def settings() -> Settings:
    return Settings(
        supabase_url=SUPABASE_URL,
        supabase_key=SUPABASE_KEY,
        backoff_base_seconds=0.0,
        backoff_max_seconds=0.0,
    )


@pytest.fixture
def fake_supabase() -> FakeSupabase:
    return FakeSupabase()


@pytest.fixture
def sleep_calls() -> list[float]:
    return []


@pytest.fixture
def supabase_client(
    settings: Settings, fake_supabase: FakeSupabase, sleep_calls: list[float]
) -> SupabaseClient:
    async def _sleep(delay: float) -> None:
        sleep_calls.append(delay)

    http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(fake_supabase.handler),
        base_url=settings.rest_url,
        headers=SupabaseClient._auth_headers(settings),
    )
    return SupabaseClient(settings, client=http_client, sleep=_sleep)


@pytest.fixture
def client(settings: Settings, supabase_client: SupabaseClient):
    app = create_app(settings=settings, supabase_client=supabase_client)
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


@pytest.fixture
def log_records() -> list[logging.LogRecord]:
    """Capture records from the service logger, which does not propagate."""
    configure_logging("DEBUG")
    logger = logging.getLogger(LOGGER_NAME)
    records: list[logging.LogRecord] = []

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    handler = Capture()
    logger.addHandler(handler)
    try:
        yield records
    finally:
        logger.removeHandler(handler)


def vote(client: TestClient, user_id: Any = PLUS_USER, mix_id: Any = MIX_ID, **kwargs: Any):
    body: dict[str, Any] = {"user_id": user_id, "mix_id": mix_id}
    body.update(kwargs)
    return client.post("/api/v1/swarm/audio/vote", json=body)


def formatted(record: logging.LogRecord) -> dict[str, Any]:
    """Render a captured record through the production JSON formatter."""
    from app.logging_config import JsonFormatter

    return json.loads(JsonFormatter().format(record))


Handler = Callable[[httpx.Request], httpx.Response]
