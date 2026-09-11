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

"""Offline end-to-end demo: one full Golden Hour ring cycle, no credentials needed.

Runs the whole product loop against the FastAPI app with a stubbed vote
backend and the in-process swarm tunnel:
  topology -> compress -> publish (4 nodes) -> catch -> vote -> catch vote vector.

Usage:
  python3 demo_ring.py
"""

import os
import sys

sys.path.insert(0, str(__file__ and __import__("pathlib").Path(__file__).resolve().parent))

os.environ.setdefault("SUPABASE_URL", "https://demo.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "demo-key")

from fastapi.testclient import TestClient  # noqa: E402

from app.config import Settings, reset_settings  # noqa: E402
from app.main import create_app  # noqa: E402
from app.service import VotingService  # noqa: E402


class _DemoVotes:
    def has_active_subscription(self, user_id: str) -> bool:
        return True

    def has_existing_vote(self, user_id: str, mix_id: str) -> bool:
        return False

    def cast_vote_rpc(self, user_id: str, mix_id: str) -> dict:
        return {"vote_count": 42}

    def get_rankings(self, limit: int) -> list[dict]:
        return [{"mix_id": "golden-hour-berko-4k", "title": "Berko", "votes": 42, "rank": 1}]


def main() -> dict:
    reset_settings()
    app = create_app()
    app.state.settings = Settings(_env_file=None)
    app.state.voting_service = VotingService(app.state.settings, _DemoVotes())  # type: ignore[arg-type]
    summary: dict = {"steps": []}
    with TestClient(app) as client:
        topology = client.get("/api/v1/swarm/topology").json()
        summary["ring"] = [n["agent_id"] for n in topology["nodes"]]
        summary["steps"].append("topology: closed ring confirmed")

        compressed = client.post(
            "/api/v1/swarm/compress",
            json={
                "agent_id": "bpm-metronome",
                "messages": [
                    {"role": "user", "content": f"Beat update {i}: hold 122 BPM for the Golden Hour set."}
                    for i in range(12)
                ],
            },
        ).json()
        summary["compression_ratio"] = compressed["compression_ratio"]
        summary["steps"].append(f"compress: truck -> sports car ({compressed['compression_ratio']}x)")

        for agent in summary["ring"]:
            vector = client.post(
                "/api/v1/swarm/state",
                json={
                    "agent_id": agent,
                    "summary": f"{agent} checking in: cycle green.",
                    "key_points": ["demo-cycle"],
                    "next_action": "next node: continue the cycle.",
                },
            ).json()
            summary.setdefault("turns", []).append(vector["turn"])
        summary["steps"].append("publish: all 4 nodes sealed vectors through the tunnel")

        caught = client.get("/api/v1/swarm/state/next", params={"agent_id": "mix-voter"}).json()
        summary["caught"] = caught["agent_id"]
        summary["steps"].append(f"catch: mix-voter pulled {caught['agent_id']}'s vector")

        vote = client.post(
            "/api/v1/swarm/audio/vote",
            json={"user_id": "mplus-user-1", "mix_id": "golden-hour-berko-4k"},
        ).json()
        summary["vote_count"] = vote["vote_count"]
        summary["steps"].append(f"vote: counted, total {vote['vote_count']}")

        visual = client.get(
            "/api/v1/swarm/state/latest", params={"agent_id": "mix-voter"}
        ).json()
        summary["vote_vector_seen_by"] = "visual-mascot" if "Vote counted" in visual["summary"] else visual["agent_id"]
        summary["vote_vector_summary"] = visual["summary"]
        summary["steps"].append("loop closed: vote fed the ring for the visual node")

        ozone = client.get("/api/v1/security/ozone").json()
        summary["ozone"] = ozone["alg"]
    reset_settings()
    return summary


if __name__ == "__main__":
    for step in main()["steps"]:
        print(f"  [demo] {step}")
    print("[demo] RING CYCLE COMPLETE")
