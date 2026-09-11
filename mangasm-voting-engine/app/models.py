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

"""Pydantic request/response models for the voting API."""

from datetime import datetime

from pydantic import BaseModel, Field, field_validator


class VoteRequest(BaseModel):
    user_id: str = Field(min_length=1, max_length=128)
    mix_id: str = Field(min_length=1, max_length=128)

    @field_validator("user_id", "mix_id")
    @classmethod
    def _strip_and_reject_blank(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("must not be blank")
        return cleaned


class RankingEntry(BaseModel):
    mix_id: str
    title: str = ""
    votes: int
    rank: int


class PromoBlock(BaseModel):
    show: str = "Golden Hour Progressive House & Melodic Techno DJ Set"
    djs: str = "MATRYXX & BERKO"
    episode: str = "mangasm.mascot episode premiere — online visual experience for all, welcome at mangasm.app"
    sessions: str = "Fuelling the Friday and Saturday live SoundCloud sessions"
    set_title: str = "Berko - Golden Hour Progressive House | 4K DJ Set"
    links: dict[str, str] = {
        "mangasm": "https://mangasm.app",
        "spotify": "https://open.spotify.com/artist/5Lrm3iLbY5LEsjXecGd83x?si=c81J_cGATNOv73L1-I6t8w",
        "soundcloud": "https://soundcloud.com/sapir-berko",
        "instagram": "https://www.instagram.com/berko_ofc",
    }


class VoteResponse(BaseModel):
    mix_id: str
    vote_count: int
    rankings: list[RankingEntry]
    voted_at: datetime
    message: str = "Vote counted. Thank you for shaping the Friday/Saturday live mix."
    promo: PromoBlock = Field(default_factory=PromoBlock)


class HealthResponse(BaseModel):
    status: str = "ok"
    service: str = "mangasm-live-mix-voting-engine"
    version: str = "1.0.0"
    supabase_configured: bool = False


class ErrorResponse(BaseModel):
    detail: str
