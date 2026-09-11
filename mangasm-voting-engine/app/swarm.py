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

"""Phase A — agent topology and cyclical communication highway.

Architecture ("highway and tunnel"):
  * Agents are nodes on a geometric ring (the highway). The default loop is
    bpm-metronome -> sha-cache -> mix-voter -> visual-mascot -> (back).
  * Instead of passing token-heavy conversation histories (heavy trucks)
    between local LLMs, each node compresses its findings into a lightweight
    JSON state vector (a fast sports car) and publishes it to the Supabase
    tunnel (Postgres + pgvector table ``swarm_state_vectors``).
  * The next agent in the loop pulls the freshest unclaimed vector, continues
    the cycle, and publishes its own. Claimed vectors stay queryable as the
    loop's audit trail.

This module holds the API models plus an in-process service used when
Supabase is unreachable (tests, local dev). The Supabase-backed path is in
``supabase_client.SwarmTunnel``; the service prefers it and degrades to
memory so the loop never hard-fails in dev.
"""

import logging
import time
import uuid
from collections import defaultdict, deque

from pydantic import BaseModel, Field, field_validator

from . import security

logger = logging.getLogger("mangasm.swarm")

RING_LOOP_ID = "golden-hour-ring"
RING_AGENTS = ("bpm-metronome", "sha-cache", "mix-voter", "visual-mascot")
MAX_VECTOR_CHARS = 8_000


class StateVector(BaseModel):
    """Lightweight JSON state car travelling through the tunnel."""

    loop_id: str = Field(default=RING_LOOP_ID, min_length=1, max_length=128)
    agent_id: str = Field(min_length=1, max_length=128)
    turn: int = Field(default=0, ge=0)
    summary: str = Field(min_length=1, max_length=2_000)
    key_points: list[str] = Field(default_factory=list, max_length=8)
    next_action: str = Field(default="", max_length=1_000)
    cache_seal: str = Field(default="", max_length=128)
    kid: str = Field(default="", max_length=64)
    sig: str = Field(default="", max_length=256)

    @field_validator("key_points")
    @classmethod
    def _clean_points(cls, points: list[str]) -> list[str]:
        return [p.strip()[:280] for p in points if p and p.strip()][:8]


class PublishRequest(BaseModel):
    loop_id: str = Field(default=RING_LOOP_ID, min_length=1, max_length=128)
    agent_id: str = Field(min_length=1, max_length=128)
    summary: str = Field(min_length=1, max_length=2_000)
    key_points: list[str] = Field(default_factory=list, max_length=8)
    next_action: str = Field(default="", max_length=1_000)
    embedding: list[float] | None = Field(default=None, max_length=1_536)


class TopologyNode(BaseModel):
    agent_id: str
    role: str
    model: str
    next: str


class TopologyResponse(BaseModel):
    loop_id: str
    kind: str = "ring"
    nodes: list[TopologyNode]
    tunnel: str = "supabase-pgvector:swarm_state_vectors"


NODE_ROLES = {
    "bpm-metronome": ("Keeps global tempo; emits beat phase + BPM state.", "local:qwen-metronome"),
    "sha-cache": ("Seals payloads with 25SHA cache IDs; serves cache hits.", "local:llama3-cache"),
    "mix-voter": ("Turns crowd votes into queue moves for the live mix.", "local:qwen-vote"),
    "visual-mascot": ("Renders the mangasm.mascot visual episode state.", "local:llama3-visual"),
}


def default_topology(loop_id: str = RING_LOOP_ID) -> TopologyResponse:
    nodes = []
    for index, agent_id in enumerate(RING_AGENTS):
        role, model = NODE_ROLES[agent_id]
        nodes.append(
            TopologyNode(
                agent_id=agent_id,
                role=role,
                model=model,
                next=RING_AGENTS[(index + 1) % len(RING_AGENTS)],
            )
        )
    return TopologyResponse(loop_id=loop_id, nodes=nodes)


class SwarmService:
    """In-process ring buffer for state vectors (dev/test fallback)."""

    def __init__(self) -> None:
        self._queues: dict[str, deque] = defaultdict(deque)
        self._turns: dict[str, int] = defaultdict(int)

    def publish(self, request: PublishRequest) -> StateVector:
        total_chars = len(request.summary) + sum(len(p) for p in request.key_points)
        if total_chars > MAX_VECTOR_CHARS:
            raise ValueError(f"state vector too large ({total_chars} chars, max {MAX_VECTOR_CHARS})")
        loop_key = request.loop_id.strip() or RING_LOOP_ID
        self._turns[loop_key] += 1
        payload = {
            "loop_id": loop_key,
            "agent_id": request.agent_id.strip(),
            "turn": self._turns[loop_key],
            "summary": request.summary.strip(),
            "key_points": [p.strip() for p in request.key_points if p.strip()][:8],
            "next_action": request.next_action.strip(),
            "ts": int(time.time()),
        }
        payload["cache_seal"] = security.sha25(security.canonical_json(payload))
        envelope = security.sign_vector(payload)
        vector = StateVector(
            loop_id=payload["loop_id"],
            agent_id=payload["agent_id"],
            turn=payload["turn"],
            summary=payload["summary"],
            key_points=payload["key_points"],
            next_action=payload["next_action"],
            cache_seal=payload["cache_seal"],
            kid=envelope["kid"],
            sig=envelope["sig"],
        )
        self._queues[loop_key].append(vector)
        logger.info(
            "swarm publish loop=%s agent=%s turn=%d seal=%s",
            loop_key,
            vector.agent_id,
            vector.turn,
            vector.cache_seal[:12],
        )
        return vector

    def next_for(self, agent_id: str, loop_id: str = RING_LOOP_ID) -> StateVector | None:
        """Freshest unclaimed vector not authored by ``agent_id`` (peek, FIFO)."""
        queue = self._queues.get(loop_id, deque())
        for vector in queue:
            if vector.agent_id != agent_id:
                return vector
        return None

    def latest_by(self, agent_id: str, loop_id: str = RING_LOOP_ID) -> StateVector | None:
        """Newest vector authored by ``agent_id`` (audit trail read)."""
        queue = self._queues.get(loop_id, deque())
        for vector in reversed(queue):
            if vector.agent_id == agent_id:
                return vector
        return None

    def claim(self, vector_id: str = "", agent_id: str = "", loop_id: str = RING_LOOP_ID) -> int:
        """Drop vectors authored by others (they are now consumed). Returns count."""
        queue = self._queues.get(loop_id, deque())
        before = len(queue)
        self._queues[loop_id] = deque(v for v in queue if v.agent_id == agent_id)
        return before - len(self._queues[loop_id])

    def new_id(self) -> str:
        return f"vec_{uuid.uuid4().hex[:12]}"
