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

"""Shared fixtures: app TestClient with stubbed vote backend and live swarm ring."""

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings, reset_settings  # noqa: E402
from app.main import create_app  # noqa: E402


@pytest.fixture()
def highway_client(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_KEY", "test-key")
    monkeypatch.delenv("OZONE_HMAC_KEYS", raising=False)
    reset_settings()
    app = create_app()
    app.state.settings = Settings(_env_file=None)
    from app.service import VotingService

    class _Votes:
        def has_active_subscription(self, user_id: str) -> bool:
            return True

        def has_existing_vote(self, user_id: str, mix_id: str) -> bool:
            return False

        def cast_vote_rpc(self, user_id: str, mix_id: str) -> dict:
            return {"vote_count": 1}

        def get_rankings(self, limit: int) -> list[dict]:
            return [{"mix_id": "golden-hour-berko-4k", "title": "Berko", "votes": 1, "rank": 1}]

    app.state.voting_service = VotingService(app.state.settings, _Votes())  # type: ignore[arg-type]
    with TestClient(app) as client:
        yield client
    reset_settings()
