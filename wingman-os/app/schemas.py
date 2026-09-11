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

"""Request / response contracts for the voting API."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

# Identifiers are opaque strings (UUIDs, slugs, external ids). Restricting the alphabet keeps
# them safe to embed in PostgREST query strings and log lines.
_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.:@-]*$"
_ID_FIELD = dict(min_length=1, max_length=128, pattern=_ID_PATTERN)


class VoteRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    user_id: str = Field(**_ID_FIELD, description="Mangasm+ member casting the vote")
    mix_id: str = Field(**_ID_FIELD, description="Live mix (Friday/Saturday session) being voted for")


class RankingEntry(BaseModel):
    rank: int = Field(ge=1)
    mix_id: str
    title: str | None = None
    artist: str | None = None
    session_slot: str | None = None
    vote_count: int = Field(ge=0)


class VoteResponse(BaseModel):
    user_id: str
    mix_id: str
    vote_count: int = Field(ge=0)
    vote_timestamp: datetime
    rankings: list[RankingEntry]


class HealthResponse(BaseModel):
    status: str
    service: str
    component: str
    version: str
    timestamp: datetime
