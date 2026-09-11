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

"""Test fixtures: an in-memory stand-in for Supabase wired through ``httpx.MockTransport``.

``FakeSupabase`` mimics the slice of PostgREST the service relies on (a filtered select on
``mangasm_subscriptions`` and the ``cast_mix_vote`` RPC, including its atomic semantics),
so the endpoint is exercised end to end without network access.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.config import Settings
from app.main import create_app
from app.supabase_client import SupabaseClient

PLUS_USER = "user-plus-001"
FREE_USER = "user-free-001"
LAPSED_USER = "user-lapsed-001"
UNKNOWN_USER = "user-unknown-001"
GOLDEN_HOUR = "golden-hour-progressive-house"
SECOND_MIX = "midnight-melodic-techno"
CLOSED_MIX = "archived-set"


def make_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = dict(
        supabase_url="https://fake.supabase.test",
        supabase_key=SecretStr("service-role-test-key"),
        supabase_max_attempts=3,
        supabase_retry_backoff_seconds=0,
        rankings_limit=20,
    )
    values.update(overrides)
    return Settings(_env_file=None, **values)


def _in(days: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()


@dataclass
class FakeSupabase:
    subscriptions: dict[str, dict[str, Any]] = field(default_factory=dict)
    mixes: dict[str, dict[str, Any]] = field(default_factory=dict)
    votes: set[tuple[str, str]] = field(default_factory=set)
    requests: list[httpx.Request] = field(default_factory=list)
    # Number of upcoming requests that should fail, and how (an exception class or HTTP status).
    fail_next: list[Any] = field(default_factory=list)

    @classmethod
    def seeded(cls) -> "FakeSupabase":
        fake = cls()
        fake.subscriptions = {
            PLUS_USER: {"tier": "plus", "status": "active", "current_period_end": _in(30)},
            FREE_USER: {"tier": "free", "status": "active", "current_period_end": None},
            LAPSED_USER: {"tier": "plus", "status": "active", "current_period_end": _in(-1)},
        }
        fake.mixes = {
            GOLDEN_HOUR: {"title": "Golden Hour Progressive House | 4K DJ Set", "artist": "MATRYXX & BERKO", "session_slot": "friday", "voting_open": True, "vote_count": 4},
            SECOND_MIX: {"title": "Midnight Melodic Techno", "artist": "Resident", "session_slot": "friday", "voting_open": True, "vote_count": 5},
            CLOSED_MIX: {"title": "Archived", "artist": None, "session_slot": "friday", "voting_open": False, "vote_count": 99},
        }
        return fake

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.fail_next:
            failure = self.fail_next.pop(0)
            if isinstance(failure, int):
                return httpx.Response(failure, json={"message": "upstream hiccup"})
            raise failure("injected transport failure", request=request)

        path = request.url.path
        if request.method == "GET" and path == "/rest/v1/mangasm_subscriptions":
            return self._select_subscription(request)
        if request.method == "POST" and path == "/rest/v1/rpc/cast_mix_vote":
            return self._cast_mix_vote(json.loads(request.content))
        return httpx.Response(404, json={"message": f"unhandled {request.method} {path}"})

    def _select_subscription(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["apikey"] == "service-role-test-key"
        assert request.headers["authorization"] == "Bearer service-role-test-key"
        user_filter = request.url.params.get("user_id", "")
        assert user_filter.startswith("eq.")
        row = self.subscriptions.get(user_filter[3:])
        return httpx.Response(200, json=[row] if row else [])

    def _cast_mix_vote(self, params: dict[str, Any]) -> httpx.Response:
        user_id, mix_id = params["p_user_id"], params["p_mix_id"]
        mix = self.mixes.get(mix_id)
        if mix is None or not mix["voting_open"]:
            return httpx.Response(404, json={"code": "PT404", "message": "MIX_NOT_FOUND", "details": None, "hint": None})
        if (user_id, mix_id) in self.votes:
            return httpx.Response(409, json={"code": "PT409", "message": "DUPLICATE_VOTE", "details": None, "hint": None})
        self.votes.add((user_id, mix_id))
        mix["vote_count"] += 1
        queue = sorted(
            ((mid, m) for mid, m in self.mixes.items() if m["voting_open"] and m["session_slot"] == mix["session_slot"]),
            key=lambda item: (-item[1]["vote_count"], item[0]),
        )[: params.get("p_rankings_limit", 20)]
        rankings = [
            {"rank": i, "mix_id": mid, "title": m["title"], "artist": m["artist"], "session_slot": m["session_slot"], "vote_count": m["vote_count"]}
            for i, (mid, m) in enumerate(queue, start=1)
        ]
        return httpx.Response(200, json={"mix_id": mix_id, "vote_count": mix["vote_count"], "rankings": rankings})


@pytest.fixture
def fake_supabase() -> FakeSupabase:
    return FakeSupabase.seeded()


@pytest.fixture
def settings() -> Settings:
    return make_settings()


@pytest.fixture
def client(fake_supabase: FakeSupabase, settings: Settings) -> Iterator[TestClient]:
    http = httpx.AsyncClient(transport=httpx.MockTransport(fake_supabase.handler))
    supabase = SupabaseClient(settings, http=http)
    app = create_app(settings=settings, supabase_client=supabase)
    with TestClient(app) as test_client:
        yield test_client
