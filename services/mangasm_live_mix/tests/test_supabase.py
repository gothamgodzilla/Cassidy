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

import asyncio
import json
from uuid import UUID

import httpx

from app.supabase import SupabaseClient, SupabaseError, SupabaseUnavailable


def run(coroutine):
    return asyncio.run(coroutine)


def test_active_subscription_uses_encoded_user_filter() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["user_id"] == "eq.member+1@example.com"
        assert request.headers["apikey"] == "secret"
        return httpx.Response(200, json=[{"user_id": "member+1@example.com", "expires_at": None}])

    client = SupabaseClient(
        "https://example.supabase.co",
        "secret",
        transport=httpx.MockTransport(handler),
    )
    try:
        assert run(client.active_subscription("member+1@example.com"))
    finally:
        run(client.close())


def test_expired_subscription_is_inactive() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[{"user_id": "member-1", "expires_at": "2020-01-01T00:00:00Z"}],
        )

    client = SupabaseClient(
        "https://example.supabase.co",
        "secret",
        transport=httpx.MockTransport(handler),
    )
    try:
        assert not run(client.active_subscription("member-1"))
    finally:
        run(client.close())


def test_cast_vote_posts_rpc_parameters() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/rest/v1/rpc/cast_mix_vote"
        assert request.method == "POST"
        payload = json.loads(request.content)
        assert payload["p_user_id"] == "member-1"
        assert payload["p_mix_id"] == "golden-hour"
        UUID(payload["p_operation_id"])
        return httpx.Response(
            200,
            json={
                "mix_id": "golden-hour",
                "vote_count": 1,
                "queue_rankings": [],
            },
        )

    client = SupabaseClient(
        "https://example.supabase.co",
        "secret",
        transport=httpx.MockTransport(handler),
    )
    try:
        result = run(client.cast_vote("member-1", "golden-hour"))
    finally:
        run(client.close())

    assert result["vote_count"] == 1


def test_authenticated_user_uses_callers_access_token() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/auth/v1/user"
        assert request.headers["authorization"] == "Bearer caller-access-token"
        assert request.headers["apikey"] == "secret"
        return httpx.Response(200, json={"id": "member-1"})

    client = SupabaseClient(
        "https://example.supabase.co",
        "secret",
        transport=httpx.MockTransport(handler),
    )
    try:
        assert run(client.authenticated_user_id("caller-access-token")) == "member-1"
    finally:
        run(client.close())


def test_transient_failure_is_retried_three_times() -> None:
    attempts = 0
    operation_ids: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        operation_ids.append(json.loads(request.content)["p_operation_id"])
        return httpx.Response(503, json={"message": "unavailable"})

    client = SupabaseClient(
        "https://example.supabase.co",
        "secret",
        transport=httpx.MockTransport(handler),
    )
    try:
        try:
            run(client.cast_vote("member-1", "golden-hour"))
        except SupabaseUnavailable:
            pass
        else:
            raise AssertionError("expected SupabaseUnavailable")
    finally:
        run(client.close())

    assert attempts == 4
    assert len(set(operation_ids)) == 1


def test_conflict_is_not_retried() -> None:
    attempts = 0

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(409, json={"message": "duplicate"})

    client = SupabaseClient(
        "https://example.supabase.co",
        "secret",
        transport=httpx.MockTransport(handler),
    )
    try:
        try:
            run(client.cast_vote("member-1", "golden-hour"))
        except SupabaseError as exc:
            assert exc.status_code == 409
        else:
            raise AssertionError("expected SupabaseError")
    finally:
        run(client.close())

    assert attempts == 1
