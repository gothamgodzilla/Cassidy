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

"""Phase A tests: ozone v0, swarm ring, compressor, highway endpoints, frontend."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import llm_adapter, security  # noqa: E402
from app.swarm import PublishRequest, SwarmService, default_topology  # noqa: E402


def test_ozone_seal_roundtrip_and_tamper_rejected():
    payload = {"loop_id": "golden-hour-ring", "summary": "tempo locked"}
    envelope = security.sign_vector(payload, key="test-key")
    assert envelope["alg"] == "HMAC-SHA256-v0"
    assert security.verify_vector(envelope, key="test-key") is True
    tampered = dict(envelope)
    tampered["payload"] = {"loop_id": "golden-hour-ring", "summary": "forged"}
    assert security.verify_vector(tampered, key="test-key") is False
    assert security.verify_vector(envelope, key="wrong-key") is False


def test_sha25_deterministic_and_25_rounds():
    first = security.sha25("golden-hour")
    assert first == security.sha25("golden-hour")
    assert len(first) == 64
    assert first != security.sha25("golden-hour!")
    assert security.OZONE_ROUNDS == 25


def test_redact_keeps_secrets_out_of_logs():
    cleaned = security.redact({"supabase_key": "sekret", "nested": {"token": "abc"}, "mix_id": "x"})
    assert cleaned["supabase_key"] == "[REDACTED]"
    assert cleaned["nested"]["token"] == "[REDACTED]"
    assert cleaned["mix_id"] == "x"


def test_ring_topology_is_a_closed_loop():
    topology = default_topology()
    assert topology.loop_id == "golden-hour-ring"
    assert [n.agent_id for n in topology.nodes] == [
        "bpm-metronome",
        "sha-cache",
        "mix-voter",
        "visual-mascot",
    ]
    nxt = {n.agent_id: n.next for n in topology.nodes}
    assert nxt["visual-mascot"] == "bpm-metronome"


def test_publish_and_catch_cycle():
    service = SwarmService()
    vector = service.publish(
        PublishRequest(agent_id="sha-cache", summary="Sealed 3 payloads", key_points=["hit=0.9"])
    )
    assert vector.turn == 1
    assert len(vector.cache_seal) == 64
    assert len(vector.sig) == 64
    caught = service.next_for("mix-voter")
    assert caught is not None and caught.agent_id == "sha-cache"
    assert service.next_for("sha-cache") is None  # own vectors are not self-served


def test_compressor_truck_in_sports_car_out():
    topics = ["tempo", "cache seals", "vote queue", "visual phase", "bassline", "lighting rig"]
    messages = []
    for i in range(24):
        topic = topics[i % len(topics)]
        messages.append(
            {
                "role": "user" if i % 2 == 0 else "assistant",
                "content": (
                    f"Update {i}: the {topic} for the Golden Hour live set must stay locked "
                    f"at 122 BPM tonight, and the {topic} report confirms the ring is in sync. "
                    f"Please acknowledge update {i} and carry the {topic} forward."
                ),
            }
        )
    car = llm_adapter.compress_history(messages, agent_id="bpm-metronome")
    assert car["tokens_after"] < car["tokens_before"]
    assert car["compression_ratio"] > 1
    assert 1 <= len(car["key_points"]) <= 5


def test_highway_endpoints_cycle(highway_client):
    topology = highway_client.get("/api/v1/swarm/topology")
    assert topology.status_code == 200
    assert topology.json()["kind"] == "ring"

    published = highway_client.post(
        "/api/v1/swarm/state",
        json={"agent_id": "sha-cache", "summary": "Cache sealed 3 payloads"},
    )
    assert published.status_code == 200, published.text
    body = published.json()
    assert body["agent_id"] == "sha-cache" and body["turn"] == 1

    caught = highway_client.get("/api/v1/swarm/state/next", params={"agent_id": "mix-voter"})
    assert caught.status_code == 200
    assert caught.json()["agent_id"] == "sha-cache"

    empty = highway_client.get("/api/v1/swarm/state/next", params={"agent_id": "sha-cache"})
    assert empty.status_code in (200, 204)


def test_compress_endpoint(highway_client):
    messages = [
        {"role": "user", "content": f"Ring update {i}: hold 122 BPM and seal the cache state."}
        for i in range(12)
    ]
    response = highway_client.post(
        "/api/v1/swarm/compress",
        json={"agent_id": "bpm-metronome", "messages": messages},
    )
    assert response.status_code == 200
    body = response.json()
    assert 1 <= len(body["key_points"]) <= 5
    assert body["tokens_after"] < body["tokens_before"]


def test_ozone_status_endpoint(highway_client):
    response = highway_client.get("/api/v1/security/ozone")
    assert response.status_code == 200
    body = response.json()
    assert body["artifact"] == "security-ozone-v0"
    assert body["sha_rounds"] == 25


def test_frontend_served(highway_client):
    response = highway_client.get("/")
    assert response.status_code == 200
    assert "Wingman.OS" in response.text
    assert "25SHA" in response.text or "25SHA Cache" in response.text
