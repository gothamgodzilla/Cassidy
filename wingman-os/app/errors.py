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

"""Domain errors and their HTTP mapping.

Every error surfaced to the caller has the same JSON shape::

    {"error": {"code": "DUPLICATE_VOTE", "message": "..."}}
"""

from __future__ import annotations

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class VotingError(Exception):
    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    code: str = "INTERNAL_ERROR"

    def __init__(self, message: str | None = None):
        self.message = message or self.__class__.__doc__ or self.code
        super().__init__(self.message)


class InvalidInputError(VotingError):
    """Invalid input"""

    status_code = status.HTTP_400_BAD_REQUEST
    code = "INVALID_INPUT"


class MembershipRequiredError(VotingError):
    """M+ Membership Required"""

    status_code = status.HTTP_403_FORBIDDEN
    code = "MEMBERSHIP_REQUIRED"


class MixNotFoundError(VotingError):
    """Mix not found or not open for voting"""

    status_code = status.HTTP_404_NOT_FOUND
    code = "MIX_NOT_FOUND"


class DuplicateVoteError(VotingError):
    """User has already voted for this mix"""

    status_code = status.HTTP_409_CONFLICT
    code = "DUPLICATE_VOTE"


class UpstreamUnavailableError(VotingError):
    """Voting service temporarily unavailable"""

    status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
    code = "UPSTREAM_UNAVAILABLE"


def error_response(status_code: int, code: str, message: str, **details: object) -> JSONResponse:
    body: dict[str, object] = {"code": code, "message": message}
    if details:
        body["details"] = details
    return JSONResponse(status_code=status_code, content={"error": body})


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(VotingError)
    async def _voting_error(_: Request, exc: VotingError) -> JSONResponse:
        return error_response(exc.status_code, exc.code, exc.message)

    # FastAPI answers malformed bodies with 422 by default; the contract for this endpoint is 400.
    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        problems = [
            {"field": ".".join(str(p) for p in err.get("loc", ()) if p != "body"), "issue": err.get("msg", "invalid")}
            for err in exc.errors()
        ]
        return error_response(status.HTTP_400_BAD_REQUEST, InvalidInputError.code, "Invalid input", problems=problems)

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, __: Exception) -> JSONResponse:
        return error_response(status.HTTP_500_INTERNAL_SERVER_ERROR, "INTERNAL_ERROR", "Internal server error")
