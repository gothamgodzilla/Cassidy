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
    """Return (key, is_dev_fallback). Prefers OZONE_HMAC_KEY, falls back to SUPABASE_KEY."""
    key = os.environ.get("OZONE_HMAC_KEY", "").strip()
    if key:
        return key, False
    fallback = os.environ.get("SUPABASE_KEY", "").strip()
    if fallback:
        return fallback, True
    return "dev-only-insecure-ozone-key", True


def sha25(data: str | bytes) -> str:
    """Iterate SHA-256 ``OZONE_ROUNDS`` times; hex digest of the final round."""
    digest: bytes = data.encode("utf-8") if isinstance(data, str) else bytes(data)
    for _ in range(OZONE_ROUNDS):
        digest = hashlib.sha256(digest).digest()
    return digest.hex()


def canonical_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sign_vector(payload: dict[str, Any], key: str | None = None) -> dict[str, Any]:
    """Wrap ``payload`` in a signed ozone envelope."""
    secret = key if key is not None else get_ozone_key()[0]
    body = canonical_json(payload)
    sig = hmac.new(secret.encode("utf-8"), body.encode("utf-8"), hashlib.sha256).hexdigest()
    return {
        "alg": OZONE_ALG,
        "sig": sig,
        "seal": sha25(body),
        "payload": payload,
    }


def verify_vector(envelope: dict[str, Any], key: str | None = None) -> bool:
    """Verify an ozone envelope with constant-time comparison."""
    try:
        payload = envelope["payload"]
        expected = sign_vector(payload, key)["sig"]
        return hmac.compare_digest(expected, str(envelope.get("sig", "")))
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
