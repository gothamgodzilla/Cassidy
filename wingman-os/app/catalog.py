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

"""Featured Friday/Saturday live mix catalog for mangasm.mascot."""

GOLDEN_HOUR_MIX_ID = "golden-hour-progressive-house-berko"

GOLDEN_HOUR_MIX: dict = {
    "mix_id": GOLDEN_HOUR_MIX_ID,
    "title": "Golden Hour Progressive House & Melodic Techno DJ Set",
    "subtitle": "Berko - Golden Hour Progressive House | 4K DJ Set",
    "description": (
        "This DJ set captures the exact deep trance and melodic techno vibe "
        "perfect for fueling the Friday and Saturday live SoundCloud sessions "
        "on the platform. A golden hour session moving through progressive house "
        "& melodic sounds with new and unreleased music."
    ),
    "djs": ["MATRYXX", "BERKO"],
    "live_sessions": ["friday", "saturday"],
    "platform": "SoundCloud",
    "artist": "Berko",
    "views": 677,
    "links": {
        "spotify": "https://open.spotify.com/artist/5Lrm3iLbY5LEsjXecGd83x?si=c81J_cGATNOv73L1-I6t8w",
        "soundcloud": "https://soundcloud.com/sapir-berko",
        "instagram": "https://www.instagram.com/berko_ofc",
    },
    "mascot": {
        "id": "mangasm.mascot",
        "episode": "premier",
        "headline": "episode premier of online visual experience",
        "welcome": "all welcome mangasm.app",
        "url": "https://mangasm.app",
    },
}


FEATURED_MIXES: dict[str, dict] = {
    GOLDEN_HOUR_MIX_ID: GOLDEN_HOUR_MIX,
}


def get_mix_catalog_entry(mix_id: str) -> dict | None:
    return FEATURED_MIXES.get(mix_id)
