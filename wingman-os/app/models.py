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

"""Request and response models for live-mix voting."""

import re
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator

IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9._:-]+$")
MAX_IDENTIFIER_LENGTH = 128


def _normalize_identifier(value: Any, field_name: str) -> str:
    if value is None:
        raise ValueError(f"{field_name} is required")
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    stripped = value.strip()
    if not stripped:
        raise ValueError(f"{field_name} must not be empty")
    if len(stripped) > MAX_IDENTIFIER_LENGTH:
        raise ValueError(f"{field_name} exceeds {MAX_IDENTIFIER_LENGTH} characters")
    if not IDENTIFIER_PATTERN.fullmatch(stripped):
        raise ValueError(f"{field_name} contains unsupported characters")
    return stripped


class VoteRequest(BaseModel):
    user_id: str = Field(..., description="Mangasm member identifier")
    mix_id: str = Field(..., description="Friday/Saturday live mix identifier")

    @field_validator("user_id", mode="before")
    @classmethod
    def validate_user_id(cls, value: Any) -> str:
        return _normalize_identifier(value, "user_id")

    @field_validator("mix_id", mode="before")
    @classmethod
    def validate_mix_id(cls, value: Any) -> str:
        return _normalize_identifier(value, "mix_id")


class MixRanking(BaseModel):
    rank: int
    mix_id: str
    title: str
    vote_count: int
    djs: list[str] = Field(default_factory=list)
    live_sessions: list[str] = Field(default_factory=list)


class VoteResponse(BaseModel):
    status: str = "ok"
    user_id: str
    mix_id: str
    vote_count: int
    vote_timestamp: datetime
    rankings: list[MixRanking]
    mix: Optional[dict[str, Any]] = None


class ConflictResponse(BaseModel):
    detail: str
    user_id: str
    mix_id: str
    vote_count: Optional[int] = None
    rankings: list[MixRanking] = Field(default_factory=list)


class HealthResponse(BaseModel):
    status: str
    service: str
    component: str
    version: str
