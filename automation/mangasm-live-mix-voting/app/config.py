"""Runtime configuration, sourced exclusively from environment variables.

``SUPABASE_URL`` and ``SUPABASE_KEY`` are mandatory: the process refuses to
start without them rather than failing on the first request. ``SUPABASE_KEY``
is a secret (service-role key) and must never be logged or echoed in a
response body.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


class ConfigurationError(RuntimeError):
    """Raised when required configuration is absent or malformed."""


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be an integer, got {raw!r}") from exc
    if not minimum <= value <= maximum:
        raise ConfigurationError(f"{name} must be between {minimum} and {maximum}, got {value}")
    return value


def _env_float(name: str, default: float, minimum: float, maximum: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be a number, got {raw!r}") from exc
    if not minimum <= value <= maximum:
        raise ConfigurationError(f"{name} must be between {minimum} and {maximum}, got {value}")
    return value


@dataclass(frozen=True)
class Settings:
    """Immutable snapshot of the process configuration."""

    supabase_url: str
    supabase_key: str = field(repr=False)

    membership_table: str = "mangasm_memberships"
    queue_view: str = "live_mix_queue"
    vote_rpc: str = "cast_mix_vote"

    # "retry 3 times" means three retries *after* the initial attempt.
    max_retries: int = 3
    backoff_base_seconds: float = 0.25
    backoff_max_seconds: float = 4.0
    request_timeout_seconds: float = 5.0
    queue_size: int = 25

    @property
    def rest_url(self) -> str:
        return f"{self.supabase_url}/rest/v1"

    def redacted(self) -> dict[str, object]:
        """Config snapshot that is safe to log or expose on the health check."""
        return {
            "supabase_url": self.supabase_url,
            "supabase_key": "***redacted***",
            "membership_table": self.membership_table,
            "queue_view": self.queue_view,
            "vote_rpc": self.vote_rpc,
            "max_retries": self.max_retries,
            "request_timeout_seconds": self.request_timeout_seconds,
        }


def load_settings(environ: dict[str, str] | None = None) -> Settings:
    """Build :class:`Settings` from ``environ`` (defaults to ``os.environ``)."""
    env = os.environ if environ is None else environ

    missing = [
        name
        for name in ("SUPABASE_URL", "SUPABASE_KEY")
        if not (env.get(name) or "").strip()
    ]
    if missing:
        raise ConfigurationError(
            "Missing required environment variable(s): " + ", ".join(sorted(missing))
        )

    supabase_url = env["SUPABASE_URL"].strip().rstrip("/")
    if not supabase_url.startswith(("http://", "https://")):
        raise ConfigurationError("SUPABASE_URL must be an absolute http(s) URL")

    return Settings(
        supabase_url=supabase_url,
        supabase_key=env["SUPABASE_KEY"],
        membership_table=env.get("MANGASM_MEMBERSHIP_TABLE") or "mangasm_memberships",
        queue_view=env.get("MANGASM_QUEUE_VIEW") or "live_mix_queue",
        vote_rpc=env.get("MANGASM_VOTE_RPC") or "cast_mix_vote",
        max_retries=_env_int("MANGASM_MAX_RETRIES", 3, 0, 10),
        backoff_base_seconds=_env_float("MANGASM_BACKOFF_BASE_SECONDS", 0.25, 0.0, 10.0),
        backoff_max_seconds=_env_float("MANGASM_BACKOFF_MAX_SECONDS", 4.0, 0.0, 60.0),
        request_timeout_seconds=_env_float("MANGASM_REQUEST_TIMEOUT_SECONDS", 5.0, 0.1, 60.0),
        queue_size=_env_int("MANGASM_QUEUE_SIZE", 25, 1, 200),
    )
