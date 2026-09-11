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

"""Security ozone layer v0 — signed state-vector envelopes + 25SHA cache seal.

v0 scope (stdlib only, no new dependencies):
  * HMAC-SHA256 envelope signing for every swarm state vector ("ozone seal").
  * 25-round SHA-256 chain hash ("25SHA") used as deterministic cache IDs.
  * Secret redaction helper so logs never leak keys or tokens.
  * Constant-time signature comparison.

Upgrade path to v1 (documented, not implemented): AES-256-GCM payload
encryption with per-loop data keys wrapped by a KMS key, plus key rotation.
v0 already defines the envelope fields v1 will reuse (alg, kid, nonce).
"""

import hashlib
import hmac
import json
import logging
import os
from typing import Any

logger = logging.getLogger("mangasm.ozone")

OZONE_ALG = "HMAC-SHA256-v0"
OZONE_ROUNDS = 25
REDACTED = "[REDACTED]"
_SENSITIVE_KEYS = {"supabase_key", "api_key", "token", "authorization", "secret", "password"}


def get_ozone_key() -> tuple[str, bool]:
    """Return (primary key, is_dev_fallback). Prefers OZONE_HMAC_KEY, falls back to SUPABASE_KEY."""
    keys = get_ozone_keys()
    return keys[0][1], keys[0][2]


def _kid_for(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:8]


def get_ozone_keys() -> list[tuple[str, str, bool]]:
    """All active ozone keys as (kid, key, is_dev) — primary first.

    Supports rotation across multiple active credentials:
      OZONE_HMAC_KEYS="kid-new:AAAA,kid-old:BBBB"  (comma-separated; kid prefix optional)
    Falls back to OZONE_HMAC_KEY, then SUPABASE_KEY, then an insecure dev key.
    New vectors are signed with the primary; verification tries every active key
    so vectors sealed before rotation still validate.
    """
    raw = os.environ.get("OZONE_HMAC_KEYS", "").strip()
    if raw:
        entries: list[tuple[str, str, bool]] = []
        for item in raw.split(","):
            item = item.strip()
            if not item:
                continue
            if ":" in item:
                kid, _, key = item.partition(":")
                entries.append((kid.strip() or _kid_for(key.strip()), key.strip(), False))
            else:
                entries.append((_kid_for(item), item, False))
        if entries:
            return entries
    key = os.environ.get("OZONE_HMAC_KEY", "").strip()
    if key:
        return [(_kid_for(key), key, False)]
    fallback = os.environ.get("SUPABASE_KEY", "").strip()
    if fallback:
        return [(_kid_for(fallback), fallback, True)]
    return [("dev", "dev-only-insecure-ozone-key", True)]


def sha25(data: str | bytes) -> str:
    """Iterate SHA-256 ``OZONE_ROUNDS`` times; hex digest of the final round."""
    digest: bytes = data.encode("utf-8") if isinstance(data, str) else bytes(data)
    for _ in range(OZONE_ROUNDS):
        digest = hashlib.sha256(digest).digest()
    return digest.hex()


def canonical_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sign_vector(payload: dict[str, Any], key: str | None = None) -> dict[str, Any]:
    """Wrap ``payload`` in a signed ozone envelope (signed with the primary key)."""
    if key is not None:
        secret, kid = key, _kid_for(key)
    else:
        kid, secret, _ = get_ozone_keys()[0]
    body = canonical_json(payload)
    sig = hmac.new(secret.encode("utf-8"), body.encode("utf-8"), hashlib.sha256).hexdigest()
    return {
        "alg": OZONE_ALG,
        "kid": kid,
        "sig": sig,
        "seal": sha25(body),
        "payload": payload,
    }


def verify_vector(envelope: dict[str, Any], key: str | None = None) -> bool:
    """Verify an ozone envelope with constant-time comparison.

    With an explicit key, only that key is tried. Otherwise every active
    rotation key is tried (kid match first), so pre-rotation vectors validate.
    """
    try:
        payload = envelope["payload"]
        body = canonical_json(payload)
        if key is not None:
            candidates = [key]
        else:
            keys = get_ozone_keys()
            ordered = sorted(keys, key=lambda k: k[0] != envelope.get("kid"))
            candidates = [k for _, k, _ in ordered]
        presented = str(envelope.get("sig", ""))
        for candidate in candidates:
            expected = hmac.new(candidate.encode("utf-8"), body.encode("utf-8"), hashlib.sha256).hexdigest()
            if hmac.compare_digest(expected, presented):
                return True
        return False
    except Exception:
        logger.warning("ozone verification failed: malformed envelope")
        return False


def redact(mapping: dict[str, Any]) -> dict[str, Any]:
    """Return a copy with sensitive values replaced (safe for logs)."""
    cleaned: dict[str, Any] = {}
    for key, value in mapping.items():
        if key.lower() in _SENSITIVE_KEYS:
            cleaned[key] = REDACTED
        elif isinstance(value, dict):
            cleaned[key] = redact(value)
        else:
            cleaned[key] = value
    return cleaned
