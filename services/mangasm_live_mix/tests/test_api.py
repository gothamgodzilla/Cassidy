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
from typing import Any

from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.main import VoteRequest, VoteService, create_app
from app.supabase import SupabaseUnavailable

AUTHORIZATION = {"Authorization": "Bearer valid-access-token"}


class StubVoteService:
    def __init__(self, result: dict[str, Any] | Exception) -> None:
        self.result = result
        self.closed = False

    async def close(self) -> None:
        self.closed = True

    async def cast(self, _vote: VoteRequest, _access_token: str) -> dict[str, Any]:
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class StubSupabaseClient:
    def __init__(self, authenticated_user_id: str) -> None:
        self.user_id = authenticated_user_id
        self.membership_checked = False

    async def authenticated_user_id(self, _access_token: str) -> str:
        return self.user_id

    async def active_subscription(self, _user_id: str) -> bool:
        self.membership_checked = True
        return True

    async def cast_vote(self, user_id: str, mix_id: str) -> dict[str, Any]:
        return {
            "mix_id": mix_id,
            "vote_count": 1,
            "queue_rankings": [{"mix_id": mix_id, "vote_count": 1, "rank": 1}],
        }

    async def close(self) -> None:
        pass


def test_health_check() -> None:
    service = StubVoteService({})
    with TestClient(create_app(service)) as client:
        response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "mangasm-live-mix-voting",
    }
    assert service.closed


def test_cast_vote_returns_updated_count_and_rankings() -> None:
    service = StubVoteService(
        {
            "mix_id": "golden-hour",
            "vote_count": 42,
            "queue_rankings": [
                {"mix_id": "golden-hour", "vote_count": 42, "rank": 1}
            ],
        }
    )
    with TestClient(create_app(service)) as client:
        response = client.post(
            "/api/v1/swarm/audio/vote",
            json={"user_id": "member-1", "mix_id": "golden-hour"},
            headers=AUTHORIZATION,
        )

    assert response.status_code == 200
    assert response.json()["vote_count"] == 42
    assert response.json()["queue_rankings"][0]["rank"] == 1


def test_non_member_is_forbidden() -> None:
    service = StubVoteService(HTTPException(403, "M+ Membership Required"))
    with TestClient(create_app(service)) as client:
        response = client.post(
            "/api/v1/swarm/audio/vote",
            json={"user_id": "free-user", "mix_id": "golden-hour"},
            headers=AUTHORIZATION,
        )

    assert response.status_code == 403
    assert response.json() == {"detail": "M+ Membership Required"}


def test_duplicate_vote_returns_conflict() -> None:
    service = StubVoteService(HTTPException(409, "User already voted for this mix"))
    with TestClient(create_app(service)) as client:
        response = client.post(
            "/api/v1/swarm/audio/vote",
            json={"user_id": "member-1", "mix_id": "golden-hour"},
            headers=AUTHORIZATION,
        )

    assert response.status_code == 409


def test_invalid_input_returns_bad_request() -> None:
    service = StubVoteService({})
    with TestClient(create_app(service)) as client:
        response = client.post(
            "/api/v1/swarm/audio/vote",
            json={"user_id": "", "mix_id": "contains whitespace"},
            headers=AUTHORIZATION,
        )

    assert response.status_code == 400


def test_external_failure_returns_internal_server_error() -> None:
    service = StubVoteService(SupabaseUnavailable("unavailable"))
    with TestClient(create_app(service)) as client:
        response = client.post(
            "/api/v1/swarm/audio/vote",
            json={"user_id": "member-1", "mix_id": "golden-hour"},
            headers=AUTHORIZATION,
        )

    assert response.status_code == 500
    assert response.json() == {"detail": "Unable to process vote"}


def test_missing_access_token_is_forbidden() -> None:
    service = StubVoteService({})
    with TestClient(create_app(service)) as client:
        response = client.post(
            "/api/v1/swarm/audio/vote",
            json={"user_id": "member-1", "mix_id": "golden-hour"},
        )

    assert response.status_code == 403
    assert response.json() == {"detail": "M+ Membership Required"}


def test_request_user_must_match_authenticated_identity() -> None:
    client = StubSupabaseClient("member-2")
    service = VoteService(client)  # type: ignore[arg-type]

    try:
        asyncio.run(
            service.cast(
                VoteRequest(user_id="member-1", mix_id="golden-hour"),
                "valid-access-token",
            )
        )
    except HTTPException as exc:
        assert exc.status_code == 403
        assert exc.detail == "M+ Membership Required"
    else:
        raise AssertionError("expected HTTPException")

    assert not client.membership_checked
