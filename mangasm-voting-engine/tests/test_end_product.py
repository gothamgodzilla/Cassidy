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

"""End-product tests: credential rotation, vote-to-ring loop, offline demo."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import security  # noqa: E402


def test_rotation_old_vectors_still_verify(monkeypatch):
    monkeypatch.setenv("OZONE_HMAC_KEYS", "kid-old:old-secret")
    sealed = security.sign_vector({"summary": "pre-rotation vector"})
    assert sealed["kid"] == "kid-old"

    monkeypatch.setenv("OZONE_HMAC_KEYS", "kid-new:new-secret,kid-old:old-secret")
    assert security.verify_vector(sealed) is True

    fresh = security.sign_vector({"summary": "post-rotation vector"})
    assert fresh["kid"] == "kid-new"
    assert security.verify_vector(fresh) is True


def test_rotation_fully_revoked_key_rejected(monkeypatch):
    monkeypatch.setenv("OZONE_HMAC_KEYS", "kid-old:old-secret")
    sealed = security.sign_vector({"summary": "revoked soon"})
    monkeypatch.setenv("OZONE_HMAC_KEYS", "kid-new:new-secret")
    assert security.verify_vector(sealed) is False


def test_vote_feeds_ring_for_visual_node(highway_client):
    response = highway_client.post(
        "/api/v1/swarm/audio/vote",
        json={"user_id": "mplus-user-1", "mix_id": "golden-hour-berko-4k"},
    )
    assert response.status_code == 200
    latest = highway_client.get("/api/v1/swarm/state/latest", params={"agent_id": "mix-voter"})
    assert latest.status_code == 200
    assert "Vote counted" in latest.json()["summary"]


def test_offline_demo_completes_full_cycle():
    import demo_ring

    summary = demo_ring.main()
    assert summary["ring"] == ["bpm-metronome", "sha-cache", "mix-voter", "visual-mascot"]
    assert summary["turns"] == [1, 2, 3, 4]
    assert summary["vote_count"] == 42
    assert summary["vote_vector_seen_by"] == "visual-mascot"
    assert summary["ozone"] == "HMAC-SHA256-v0"
