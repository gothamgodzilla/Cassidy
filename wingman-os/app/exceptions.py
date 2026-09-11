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

"""Domain and HTTP errors for the voting engine."""

from typing import Optional


class VotingError(Exception):
    """Base class for voting-engine failures."""

    def __init__(self, message: str, *, user_id: Optional[str] = None, mix_id: Optional[str] = None):
        super().__init__(message)
        self.message = message
        self.user_id = user_id
        self.mix_id = mix_id


class MembershipRequiredError(VotingError):
    """Caller is not an active Mangasm+ member."""


class DuplicateVoteError(VotingError):
    """The same member already voted for this mix."""

    def __init__(
        self,
        message: str = "Conflict: user already voted for this mix",
        *,
        user_id: Optional[str] = None,
        mix_id: Optional[str] = None,
        vote_count: Optional[int] = None,
        rankings: Optional[list] = None,
    ):
        super().__init__(message, user_id=user_id, mix_id=mix_id)
        self.vote_count = vote_count
        self.rankings = rankings or []


class MixNotFoundError(VotingError):
    """mix_id is not a Friday/Saturday live mix."""


class ExternalServiceError(VotingError):
    """Supabase or another dependency failed after retries are exhausted."""
