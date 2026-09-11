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

"""Shared fixtures for the Live Mix Voting Engine tests."""

import os

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-service-role-key-value")

from typing import Optional

import pytest
from fastapi.testclient import TestClient

from app.catalog import GOLDEN_HOUR_MIX_ID
from app.config import clear_settings_cache
from app.engine import LiveMixVotingEngine, set_engine
from app.exceptions import DuplicateVoteError, ExternalServiceError, MixNotFoundError
from app.main import app
from app.models import MixRanking
from app.services import CastVoteResult


class FakeGateway:
    def __init__(self) -> None:
        self.members = {"plus-user"}
        self.votes: set[tuple[str, str]] = set()
        self.counts = {GOLDEN_HOUR_MIX_ID: 12, "night-drive-melodic": 8}
        self.membership_calls = 0
        self.cast_calls = 0
        self.failures_remaining = 0
        self.fail_on: Optional[str] = None

    def _maybe_fail(self, stage: str) -> None:
        if self.fail_on in (stage, "any") and self.failures_remaining > 0:
            self.failures_remaining -= 1
            raise ExternalServiceError("supabase unavailable")

    def _rankings(self) -> list[MixRanking]:
        ordered = sorted(self.counts.items(), key=lambda item: (-item[1], item[0]))
        rankings = []
        for index, (mix_id, count) in enumerate(ordered, start=1):
            rankings.append(
                MixRanking(
                    rank=index,
                    mix_id=mix_id,
                    title="Golden Hour Progressive House & Melodic Techno DJ Set"
                    if mix_id == GOLDEN_HOUR_MIX_ID
                    else mix_id,
                    vote_count=count,
                    djs=["MATRYXX", "BERKO"] if mix_id == GOLDEN_HOUR_MIX_ID else [],
                    live_sessions=["friday", "saturday"],
                )
            )
        return rankings

    async def has_active_mangasm_plus(self, user_id: str) -> bool:
        self.membership_calls += 1
        self._maybe_fail("membership")
        return user_id in self.members

    async def cast_mix_vote(self, user_id: str, mix_id: str) -> CastVoteResult:
        self.cast_calls += 1
        self._maybe_fail("cast")
        if mix_id not in self.counts:
            raise MixNotFoundError("mix_id is not a Friday/Saturday live mix", user_id=user_id, mix_id=mix_id)
        key = (user_id, mix_id)
        if key in self.votes:
            raise DuplicateVoteError(
                user_id=user_id,
                mix_id=mix_id,
                vote_count=self.counts[mix_id],
                rankings=self._rankings(),
            )
        self.votes.add(key)
        self.counts[mix_id] += 1
        return CastVoteResult(vote_count=self.counts[mix_id], rankings=self._rankings())

    async def aclose(self) -> None:
        return None


@pytest.fixture
def fake_gateway() -> FakeGateway:
    return FakeGateway()


@pytest.fixture
def client(fake_gateway: FakeGateway):
    clear_settings_cache()
    with TestClient(app) as test_client:
        set_engine(LiveMixVotingEngine(fake_gateway))
        yield test_client
