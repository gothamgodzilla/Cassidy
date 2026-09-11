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

"""Retry and RPC mapping tests for the Supabase gateway."""

import httpx
import pytest

from app.catalog import GOLDEN_HOUR_MIX_ID
from app.config import Settings
from app.exceptions import DuplicateVoteError, ExternalServiceError, MixNotFoundError
from app.services import SupabaseMixVotingGateway


def _settings() -> Settings:
    return Settings(supabase_url="https://example.supabase.co", supabase_key="test-service-role-key-value")


def _gateway(handler) -> SupabaseMixVotingGateway:
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(
        transport=transport,
        base_url="https://example.supabase.co",
        headers={"apikey": "test-key", "Authorization": "Bearer test-key"},
    )
    return SupabaseMixVotingGateway(_settings(), client=client)


@pytest.mark.asyncio
async def test_cast_mix_vote_retries_then_succeeds():
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] < 3:
            return httpx.Response(503, json={"message": "unavailable"})
        return httpx.Response(
            200,
            json={
                "status": "ok",
                "vote_count": 13,
                "rankings": [
                    {
                        "rank": 1,
                        "mix_id": GOLDEN_HOUR_MIX_ID,
                        "title": "Golden Hour Progressive House & Melodic Techno DJ Set",
                        "vote_count": 13,
                        "djs": ["MATRYXX", "BERKO"],
                        "live_sessions": ["friday", "saturday"],
                    }
                ],
            },
        )

    gateway = _gateway(handler)
    try:
        result = await gateway.cast_mix_vote("plus-user", GOLDEN_HOUR_MIX_ID)
        assert calls["count"] == 3
        assert result.vote_count == 13
        assert result.rankings[0].mix_id == GOLDEN_HOUR_MIX_ID
    finally:
        await gateway.aclose()


@pytest.mark.asyncio
async def test_cast_mix_vote_exhausts_retries():
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return httpx.Response(500, json={"message": "down"})

    gateway = _gateway(handler)
    try:
        with pytest.raises(ExternalServiceError):
            await gateway.cast_mix_vote("plus-user", GOLDEN_HOUR_MIX_ID)
        assert calls["count"] == 3
    finally:
        await gateway.aclose()


@pytest.mark.asyncio
async def test_already_voted_maps_to_conflict():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"status": "already_voted", "vote_count": 13, "rankings": []},
        )

    gateway = _gateway(handler)
    try:
        with pytest.raises(DuplicateVoteError) as exc:
            await gateway.cast_mix_vote("plus-user", GOLDEN_HOUR_MIX_ID)
        assert exc.value.vote_count == 13
    finally:
        await gateway.aclose()


@pytest.mark.asyncio
async def test_unknown_mix_maps_to_bad_request_domain_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "mix_not_found"})

    gateway = _gateway(handler)
    try:
        with pytest.raises(MixNotFoundError):
            await gateway.cast_mix_vote("plus-user", "missing-mix")
    finally:
        await gateway.aclose()


@pytest.mark.asyncio
async def test_membership_query_active_member():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        return httpx.Response(
            200,
            json=[{"user_id": "plus-user", "status": "active", "current_period_end": None}],
        )

    gateway = _gateway(handler)
    try:
        assert await gateway.has_active_mangasm_plus("plus-user") is True
    finally:
        await gateway.aclose()


@pytest.mark.asyncio
async def test_membership_query_missing_record():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    gateway = _gateway(handler)
    try:
        assert await gateway.has_active_mangasm_plus("free-user") is False
    finally:
        await gateway.aclose()
