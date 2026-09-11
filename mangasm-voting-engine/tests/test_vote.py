# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.

"""Regression tests for the Mangasm+ Live Mix Voting Engine.

Uses an in-memory fake Supabase client so the API contract
(membership gate, atomic vote, duplicate 409, rankings, retries)
is verified without network access.
"""

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings, reset_settings  # noqa: E402
from app.main import create_app  # noqa: E402
from app.supabase_client import (  # noqa: E402
    DuplicateVoteError,
    MixNotFoundError,
    UpstreamError,
)


class FakeSupabase:
    """In-memory stand-in for SupabaseClient."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.members = {"mplus-user-1"}
        self.votes: dict[tuple[str, str], int] = {}
        self.counts = {
            "golden-hour-berko-4k": 41,
            "golden-hour-matryxx-b2b-berko": 37,
        }
        self.failures_remaining = 0

    def _maybe_fail(self, what: str) -> None:
        if self.failures_remaining > 0:
            self.failures_remaining -= 1
            raise UpstreamError(f"injected {what} failure")

    def has_active_subscription(self, user_id: str) -> bool:
        self._maybe_fail("membership")
        return user_id in self.members

    def has_existing_vote(self, user_id: str, mix_id: str) -> bool:
        self._maybe_fail("duplicate-check")
        return (user_id, mix_id) in self.votes

    def cast_vote_rpc(self, user_id: str, mix_id: str) -> dict:
        self._maybe_fail("rpc")
        if mix_id not in self.counts:
            raise MixNotFoundError(f"mix {mix_id} not found")
        if (user_id, mix_id) in self.votes:
            raise DuplicateVoteError(f"user {user_id} already voted for mix {mix_id}")
        self.counts[mix_id] += 1
        self.votes[(user_id, mix_id)] = self.counts[mix_id]
        return {"mix_id": mix_id, "vote_count": self.counts[mix_id]}

    def get_rankings(self, limit: int) -> list[dict]:
        self._maybe_fail("rankings")
        rows = sorted(self.counts.items(), key=lambda kv: kv[1], reverse=True)[:limit]
        titles = {
            "golden-hour-berko-4k": "Berko - Golden Hour Progressive House | 4K DJ Set",
            "golden-hour-matryxx-b2b-berko": "MATRYXX b2b BERKO - mangasm.mascot Episode Premiere",
        }
        return [
            {"mix_id": mix_id, "title": titles.get(mix_id, mix_id), "votes": count, "rank": i + 1}
            for i, (mix_id, count) in enumerate(rows)
        ]


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_KEY", "test-key")
    reset_settings()
    settings = Settings(_env_file=None)
    fake = FakeSupabase(settings)
    app = create_app()
    app.state.settings = settings
    from app.service import VotingService

    app.state.voting_service = VotingService(settings, fake)  # type: ignore[arg-type]
    app.state.fake = fake
    with TestClient(app) as test_client:
        yield test_client
    reset_settings()


def test_health_check(client):
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == "mangasm-live-mix-voting-engine"
    assert body["supabase_configured"] is True


def test_plus_member_vote_updates_count_and_rankings(client):
    response = client.post(
        "/api/v1/swarm/audio/vote",
        json={"user_id": "mplus-user-1", "mix_id": "golden-hour-berko-4k"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["mix_id"] == "golden-hour-berko-4k"
    assert body["vote_count"] == 42
    assert body["rankings"][0]["mix_id"] == "golden-hour-berko-4k"
    assert body["rankings"][0]["rank"] == 1
    assert body["promo"]["djs"] == "MATRYXX & BERKO"
    assert "mangasm.app" in body["promo"]["episode"]


def test_non_plus_member_blocked_with_403(client):
    response = client.post(
        "/api/v1/swarm/audio/vote",
        json={"user_id": "free-user-9", "mix_id": "golden-hour-berko-4k"},
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "M+ Membership Required"


def test_duplicate_vote_rejected_with_409(client):
    first = client.post(
        "/api/v1/swarm/audio/vote",
        json={"user_id": "mplus-user-1", "mix_id": "golden-hour-matryxx-b2b-berko"},
    )
    assert first.status_code == 200
    second = client.post(
        "/api/v1/swarm/audio/vote",
        json={"user_id": "mplus-user-1", "mix_id": "golden-hour-matryxx-b2b-berko"},
    )
    assert second.status_code == 409


def test_invalid_input_returns_400(client):
    response = client.post("/api/v1/swarm/audio/vote", json={"user_id": "   ", "mix_id": ""})
    assert response.status_code == 400


def test_unknown_mix_returns_404(client):
    response = client.post(
        "/api/v1/swarm/audio/vote",
        json={"user_id": "mplus-user-1", "mix_id": "no-such-mix"},
    )
    assert response.status_code == 404


def test_upstream_failure_returns_500_after_retries(client):
    fake = client.app.state.fake

    real_rpc = fake.cast_vote_rpc

    def always_fail(user_id: str, mix_id: str):
        raise UpstreamError("supabase down")

    fake.cast_vote_rpc = always_fail  # type: ignore[method-assign]
    try:
        response = client.post(
            "/api/v1/swarm/audio/vote",
            json={"user_id": "mplus-user-1", "mix_id": "golden-hour-berko-4k"},
        )
    finally:
        fake.cast_vote_rpc = real_rpc
    assert response.status_code == 500


def test_retry_helper_retries_then_succeeds():
    from app.supabase_client import call_with_retry

    attempts = {"n": 0}

    def flaky():
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise UpstreamError("transient 503")
        return {"vote_count": 7}

    result = call_with_retry(flaky, max_retries=3, base_delay=0, what="unit-test")
    assert result == {"vote_count": 7}
    assert attempts["n"] == 3
