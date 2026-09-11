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

"""Runtime configuration, sourced from environment variables (or a local ``.env`` file)."""

from __future__ import annotations

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Service settings.

    ``SUPABASE_URL`` and ``SUPABASE_KEY`` are mandatory; the process refuses to start without them.
    ``SUPABASE_KEY`` must be the *service role* key (it bypasses row level security) and therefore
    must only ever live in the server-side secret store, never in a client bundle.
    """

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    supabase_url: str = Field(description="Base URL of the Supabase project, e.g. https://xyz.supabase.co")
    supabase_key: SecretStr = Field(description="Supabase service role key")

    supabase_timeout_seconds: float = Field(default=5.0, gt=0)
    supabase_max_attempts: int = Field(default=3, ge=1, le=10, description="Attempts per Supabase call before giving up")
    supabase_retry_backoff_seconds: float = Field(default=0.2, ge=0, description="Base delay; doubles after every failed attempt")

    rankings_limit: int = Field(default=20, ge=1, le=100, description="Number of queue entries returned with each vote")
    log_level: str = Field(default="INFO")

    @field_validator("supabase_url")
    @classmethod
    def _normalise_url(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        if not value.startswith(("http://", "https://")):
            raise ValueError("SUPABASE_URL must start with http:// or https://")
        return value

    @field_validator("supabase_key")
    @classmethod
    def _non_empty_key(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("SUPABASE_KEY must not be empty")
        return value

    @field_validator("log_level")
    @classmethod
    def _upper_log_level(cls, value: str) -> str:
        value = value.upper()
        if value not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError(f"unsupported LOG_LEVEL {value!r}")
        return value
