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

"""HTTP contract tests for the Live Mix Voting Engine."""

import logging

from app.catalog import GOLDEN_HOUR_MIX_ID
from tests.conftest import FakeGateway

VOTE_URL = "/api/v1/swarm/audio/vote"


def test_health_check(client):
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == "wingman-os-core"
    assert body["component"] == "live-mix-voting"


def test_featured_mix_includes_mascot_premier(client):
    response = client.get("/api/v1/swarm/audio/featured")
    assert response.status_code == 200
    featured = response.json()["featured"]
    assert featured["mix_id"] == GOLDEN_HOUR_MIX_ID
    assert featured["djs"] == ["MATRYXX", "BERKO"]
    assert featured["mascot"]["id"] == "mangasm.mascot"
    assert featured["mascot"]["episode"] == "premier"
    assert "mangasm.app" in featured["mascot"]["welcome"]
    assert "friday" in featured["live_sessions"]


def test_plus_member_can_cast_vote(client, fake_gateway: FakeGateway):
    response = client.post(
        VOTE_URL,
        json={"user_id": "plus-user", "mix_id": GOLDEN_HOUR_MIX_ID},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["vote_count"] == 13
    assert body["rankings"][0]["mix_id"] == GOLDEN_HOUR_MIX_ID
    assert body["rankings"][0]["rank"] == 1
    assert body["mix"]["mascot"]["id"] == "mangasm.mascot"
    assert ("plus-user", GOLDEN_HOUR_MIX_ID) in fake_gateway.votes


def test_non_plus_member_is_forbidden(client, fake_gateway: FakeGateway):
    response = client.post(
        VOTE_URL,
        json={"user_id": "free-user", "mix_id": GOLDEN_HOUR_MIX_ID},
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "M+ Membership Required"
    assert fake_gateway.cast_calls == 0


def test_duplicate_vote_returns_conflict(client, fake_gateway: FakeGateway):
    payload = {"user_id": "plus-user", "mix_id": GOLDEN_HOUR_MIX_ID}
    first = client.post(VOTE_URL, json=payload)
    second = client.post(VOTE_URL, json=payload)
    assert first.status_code == 200
    assert second.status_code == 409
    body = second.json()
    assert "already voted" in body["detail"]
    assert body["user_id"] == "plus-user"
    assert body["mix_id"] == GOLDEN_HOUR_MIX_ID
    assert body["vote_count"] == 13


def test_invalid_input_returns_bad_request(client):
    missing = client.post(VOTE_URL, json={})
    empty = client.post(VOTE_URL, json={"user_id": " ", "mix_id": GOLDEN_HOUR_MIX_ID})
    bad_chars = client.post(VOTE_URL, json={"user_id": "plus user", "mix_id": GOLDEN_HOUR_MIX_ID})
    unknown_mix = client.post(VOTE_URL, json={"user_id": "plus-user", "mix_id": "not-a-live-mix"})
    assert missing.status_code == 400
    assert empty.status_code == 400
    assert bad_chars.status_code == 400
    assert unknown_mix.status_code == 400
    assert missing.json()["detail"] == "Invalid input"


def test_external_failure_returns_500(client, fake_gateway: FakeGateway):
    fake_gateway.fail_on = "membership"
    fake_gateway.failures_remaining = 1
    response = client.post(
        VOTE_URL,
        json={"user_id": "plus-user", "mix_id": GOLDEN_HOUR_MIX_ID},
    )
    assert response.status_code == 500
    assert response.json()["detail"] == "Voting service temporarily unavailable"


def test_vote_is_logged_with_required_fields(client):
    logger = logging.getLogger("wingman.os.vote")
    captured: list[logging.LogRecord] = []

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            captured.append(record)

    handler = Capture()
    logger.addHandler(handler)
    try:
        response = client.post(
            VOTE_URL,
            json={"user_id": "plus-user", "mix_id": GOLDEN_HOUR_MIX_ID},
        )
    finally:
        logger.removeHandler(handler)

    assert response.status_code == 200
    vote_records = [record for record in captured if getattr(record, "event", None) == "vote_cast"]
    assert vote_records
    record = vote_records[0]
    assert record.user_id == "plus-user"
    assert record.mix_id == GOLDEN_HOUR_MIX_ID
    assert record.vote_timestamp
