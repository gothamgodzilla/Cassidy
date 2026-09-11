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

"""Local-LLM compressor: heavy truck in, sports car out.

Each ring node runs a local model (Llama 3 / Qwen via Ollama). Conversation
histories are token-heavy trucks that must never enter the tunnel. This
adapter reduces a history to the lightweight JSON state vector the next
agent needs: summary, key points, next action, and a token estimate.

Runs fully offline with an extractive heuristic so tests and edge nodes
work without a model server. When OLLAMA_URL is configured, callers may use
``build_ollama_prompt`` to continue the cycle on their own model server;
the HTTP call itself stays outside this helper to keep retries explicit.
"""

import os
import re

_WORD = re.compile(r"[A-Za-z0-9']+")


def estimate_tokens(text: str) -> int:
    return max(1, len(_WORD.findall(text)) // 3 + len(text) // 400)


def split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return [p.strip() for p in parts if p.strip()]


def compress_history(
    messages: list[dict[str, str]],
    *,
    agent_id: str,
    max_points: int = 5,
    max_summary_chars: int = 600,
) -> dict:
    """Compress a chat history into a tunnel-ready state payload."""
    if not messages:
        raise ValueError("messages must not be empty")
    full = "\n".join(f"{m.get('role', 'user')}: {m.get('content', '')}" for m in messages)
    before_tokens = estimate_tokens(full)
    sentences = []
    for message in messages:
        sentences.extend(split_sentences(str(message.get("content", ""))))
    scored = sorted(
        ((len(set(_WORD.findall(s.lower()))), -index, s) for index, s in enumerate(sentences)),
        reverse=True,
    )
    points = []
    seen: set[str] = set()
    for _, _, sentence in scored:
        key = sentence.lower()[:60]
        if key in seen:
            continue
        seen.add(key)
        points.append(sentence[:280])
        if len(points) >= max_points:
            break
    summary = " ".join(points[:2])[:max_summary_chars] or full[:max_summary_chars]
    last_user = next(
        (m.get("content", "") for m in reversed(messages) if m.get("role") == "user"),
        "",
    )
    compressed = {
        "agent_id": agent_id,
        "summary": summary,
        "key_points": points,
        "next_action": f"Continue from: {(last_user or summary)[:200]}",
        "tokens_before": before_tokens,
        "tokens_after": estimate_tokens(summary + " ".join(points)),
    }
    compressed["compression_ratio"] = round(compressed["tokens_before"] / compressed["tokens_after"], 2)
    return compressed


def build_ollama_prompt(state: dict, model_hint: str = "llama3") -> dict:
    """Build an Ollama /api/chat payload that continues the ring cycle."""
    base_url = os.environ.get("OLLAMA_URL", "http://localhost:11434").rstrip("/")
    prompt = (
        "You are a node in the Wingman.OS agent ring. Previous node state:\n"
        f"Summary: {state.get('summary', '')}\n"
        f"Key points: {'; '.join(state.get('key_points', []))}\n"
        f"Suggested next action: {state.get('next_action', '')}\n"
        "Reply with a compact JSON state vector: summary, key_points (<=5), next_action."
    )
    return {
        "url": f"{base_url}/api/chat",
        "body": {"model": model_hint, "messages": [{"role": "user", "content": prompt}], "stream": False},
    }
