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

from __future__ import annotations

import asyncio

import httpx
import pytest

from app.supabase_client import SupabaseClient, SupabaseRequestError, SupabaseUnavailableError
from tests.conftest import make_settings


def _client(handler, **settings_overrides):
    slept: list[float] = []

    async def fake_sleep(delay: float) -> None:
        slept.append(delay)

    settings = make_settings(supabase_retry_backoff_seconds=0.1, **settings_overrides)
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return SupabaseClient(settings, http=http, sleep=fake_sleep), slept


def test_retries_transient_failures_with_exponential_backoff():
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ConnectError("boom", request=request)
        if calls == 2:
            return httpx.Response(503)
        return httpx.Response(200, json={"vote_count": 1})

    client, slept = _client(handler)

    assert asyncio.run(client.rpc("cast_mix_vote", {})) == {"vote_count": 1}
    assert calls == 3
    assert slept == [0.1, 0.2]


def test_gives_up_after_max_attempts():
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500, json={"message": "down"})

    client, slept = _client(handler, supabase_max_attempts=3)

    with pytest.raises(SupabaseUnavailableError) as excinfo:
        asyncio.run(client.select("mangasm_subscriptions", {"select": "tier"}))
    assert calls == 3
    assert excinfo.value.attempts == 3
    assert slept == [0.1, 0.2], "no sleep after the final attempt"


def test_client_errors_are_not_retried():
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(409, json={"code": "PT409", "message": "DUPLICATE_VOTE", "details": "d", "hint": "h"})

    client, slept = _client(handler)

    with pytest.raises(SupabaseRequestError) as excinfo:
        asyncio.run(client.rpc("cast_mix_vote", {}))
    assert calls == 1
    assert slept == []
    err = excinfo.value
    assert (err.status_code, err.code, err.message, err.details, err.hint) == (409, "PT409", "DUPLICATE_VOTE", "d", "h")


def test_sends_service_role_credentials_and_targets_rest_v1():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=[])

    client, _ = _client(handler)
    asyncio.run(client.select("mangasm_subscriptions", {"user_id": "eq.u1", "select": "tier"}))

    (request,) = seen
    assert str(request.url) == "https://fake.supabase.test/rest/v1/mangasm_subscriptions?user_id=eq.u1&select=tier"
    assert request.headers["apikey"] == "service-role-test-key"
    assert request.headers["authorization"] == "Bearer service-role-test-key"


def test_trailing_slash_in_url_is_normalised():
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(204)

    client, _ = _client(handler, supabase_url="https://fake.supabase.test/")
    assert asyncio.run(client.rpc("cast_mix_vote", {})) is None
    assert seen == ["https://fake.supabase.test/rest/v1/rpc/cast_mix_vote"]
