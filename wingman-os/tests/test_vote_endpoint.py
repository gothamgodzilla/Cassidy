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

"""End-to-end behaviour of POST /api/v1/swarm/audio/vote against the fake Supabase."""

from __future__ import annotations

import json
import logging
from datetime import datetime

import httpx
import pytest

from tests.conftest import CLOSED_MIX, FREE_USER, GOLDEN_HOUR, LAPSED_USER, PLUS_USER, SECOND_MIX, UNKNOWN_USER

VOTE_URL = "/api/v1/swarm/audio/vote"


def _vote(client, user_id, mix_id):
    return client.post(VOTE_URL, json={"user_id": user_id, "mix_id": mix_id})


# --- acceptance: Mangasm+ members can vote and the count is updated -------------------------


def test_plus_member_vote_is_accepted_and_counted(client, fake_supabase):
    response = _vote(client, PLUS_USER, GOLDEN_HOUR)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["user_id"] == PLUS_USER
    assert body["mix_id"] == GOLDEN_HOUR
    assert body["vote_count"] == 5
    datetime.fromisoformat(body["vote_timestamp"])  # ISO-8601 timestamp
    assert (PLUS_USER, GOLDEN_HOUR) in fake_supabase.votes
    assert fake_supabase.mixes[GOLDEN_HOUR]["vote_count"] == 5


def test_response_contains_current_queue_rankings(client):
    body = _vote(client, PLUS_USER, GOLDEN_HOUR).json()

    rankings = body["rankings"]
    assert [r["rank"] for r in rankings] == [1, 2]
    # Golden Hour (5) ties Midnight (5) and wins the tie-break; the closed mix is excluded.
    assert [r["mix_id"] for r in rankings] == [GOLDEN_HOUR, SECOND_MIX]
    assert rankings[0]["title"] == "Golden Hour Progressive House | 4K DJ Set"
    assert rankings[0]["artist"] == "MATRYXX & BERKO"
    assert rankings[0]["session_slot"] == "friday"
    assert rankings[0]["vote_count"] == 5
    assert all(r["mix_id"] != CLOSED_MIX for r in rankings)


def test_vote_uses_atomic_rpc_with_expected_arguments(client, fake_supabase, settings):
    _vote(client, PLUS_USER, GOLDEN_HOUR)

    subscription_check, rpc_call = fake_supabase.requests
    assert subscription_check.method == "GET"
    assert subscription_check.url.path == "/rest/v1/mangasm_subscriptions"
    assert subscription_check.url.params["user_id"] == f"eq.{PLUS_USER}"

    assert rpc_call.method == "POST"
    assert rpc_call.url.path == "/rest/v1/rpc/cast_mix_vote"
    params = json.loads(rpc_call.content)
    assert params["p_user_id"] == PLUS_USER
    assert params["p_mix_id"] == GOLDEN_HOUR
    assert params["p_rankings_limit"] == settings.rankings_limit
    datetime.fromisoformat(params["p_voted_at"])


# --- acceptance: non-plus members are blocked -----------------------------------------------


@pytest.mark.parametrize("user_id", [FREE_USER, LAPSED_USER, UNKNOWN_USER])
def test_non_plus_members_get_403(client, fake_supabase, user_id):
    response = _vote(client, user_id, GOLDEN_HOUR)

    assert response.status_code == 403
    assert response.json() == {"error": {"code": "MEMBERSHIP_REQUIRED", "message": "M+ Membership Required"}}
    assert not fake_supabase.votes
    assert fake_supabase.mixes[GOLDEN_HOUR]["vote_count"] == 4
    assert all(r.url.path != "/rest/v1/rpc/cast_mix_vote" for r in fake_supabase.requests), "RPC must not run for non-members"


# --- acceptance: duplicate votes are rejected -----------------------------------------------


def test_duplicate_vote_returns_409_and_does_not_double_count(client, fake_supabase):
    assert _vote(client, PLUS_USER, GOLDEN_HOUR).status_code == 200

    response = _vote(client, PLUS_USER, GOLDEN_HOUR)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "DUPLICATE_VOTE"
    assert fake_supabase.mixes[GOLDEN_HOUR]["vote_count"] == 5


def test_same_user_may_vote_for_different_mixes(client):
    assert _vote(client, PLUS_USER, GOLDEN_HOUR).status_code == 200
    assert _vote(client, PLUS_USER, SECOND_MIX).status_code == 200


def test_duplicate_detected_via_postgres_unique_violation_code(client, fake_supabase):
    # If the RPC ever lets the insert race through to the primary key, PostgREST reports 23505.
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/rpc/cast_mix_vote"):
            return httpx.Response(
                409,
                json={"code": "23505", "message": 'duplicate key value violates unique constraint "mix_votes_pkey"'},
            )
        return fake_supabase.handler(request)

    client.app.state.supabase._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    response = _vote(client, PLUS_USER, GOLDEN_HOUR)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "DUPLICATE_VOTE"


# --- input validation → 400 -----------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"user_id": PLUS_USER},
        {"mix_id": GOLDEN_HOUR},
        {"user_id": "", "mix_id": GOLDEN_HOUR},
        {"user_id": "   ", "mix_id": GOLDEN_HOUR},
        {"user_id": PLUS_USER, "mix_id": "bad id with spaces"},
        {"user_id": PLUS_USER, "mix_id": "x" * 129},
        {"user_id": 42, "mix_id": GOLDEN_HOUR},
        {"user_id": PLUS_USER, "mix_id": GOLDEN_HOUR, "extra": "nope"},
    ],
)
def test_invalid_input_returns_400(client, fake_supabase, payload):
    response = client.post(VOTE_URL, json=payload)

    assert response.status_code == 400
    body = response.json()["error"]
    assert body["code"] == "INVALID_INPUT"
    assert body["message"] == "Invalid input"
    assert body["details"]["problems"]
    assert fake_supabase.requests == [], "invalid requests must never reach Supabase"


def test_malformed_json_returns_400(client):
    response = client.post(VOTE_URL, content=b"{not json", headers={"Content-Type": "application/json"})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_INPUT"


# --- other outcomes -------------------------------------------------------------------------


def test_unknown_or_closed_mix_returns_404(client):
    assert _vote(client, PLUS_USER, "does-not-exist").status_code == 404
    response = _vote(client, PLUS_USER, CLOSED_MIX)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "MIX_NOT_FOUND"


# --- external failures: retry 3 times then 500 ----------------------------------------------


def test_transient_failures_are_retried_then_succeed(client, fake_supabase):
    fake_supabase.fail_next = [httpx.ConnectError, 503]

    response = _vote(client, PLUS_USER, GOLDEN_HOUR)

    assert response.status_code == 200
    subscription_attempts = [r for r in fake_supabase.requests if r.url.path.endswith("/mangasm_subscriptions")]
    assert len(subscription_attempts) == 3


def test_persistent_failure_returns_500_after_three_attempts(client, fake_supabase):
    fake_supabase.fail_next = [httpx.ConnectTimeout, 502, 500]

    response = _vote(client, PLUS_USER, GOLDEN_HOUR)

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "UPSTREAM_UNAVAILABLE"
    assert len(fake_supabase.requests) == 3
    assert not fake_supabase.votes


def test_rpc_failure_after_membership_check_returns_500(client, fake_supabase):
    # Membership lookup succeeds on the first request, then the RPC is unreachable on every attempt.
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/rpc/cast_mix_vote"):
            fake_supabase.requests.append(request)
            raise httpx.ReadTimeout("rpc timed out", request=request)
        return fake_supabase.handler(request)

    client.app.state.supabase._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    response = _vote(client, PLUS_USER, GOLDEN_HOUR)

    assert response.status_code == 500
    assert len([r for r in fake_supabase.requests if r.url.path.endswith("/rpc/cast_mix_vote")]) == 3


def test_malformed_rpc_payload_returns_500(client, fake_supabase):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/rpc/cast_mix_vote"):
            return httpx.Response(200, json={"rankings": "not-a-list"})
        return fake_supabase.handler(request)

    client.app.state.supabase._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    response = _vote(client, PLUS_USER, GOLDEN_HOUR)

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "UPSTREAM_UNAVAILABLE"


# --- audit logging --------------------------------------------------------------------------


def test_vote_audit_fields_are_logged(client, caplog):
    with caplog.at_level(logging.INFO, logger="app.services.voting"):
        _vote(client, PLUS_USER, GOLDEN_HOUR)
        _vote(client, FREE_USER, GOLDEN_HOUR)
        _vote(client, PLUS_USER, GOLDEN_HOUR)

    outcomes = [r.outcome for r in caplog.records if hasattr(r, "outcome")]
    assert outcomes == ["accepted", "membership_required", "duplicate"]
    for record in caplog.records:
        if hasattr(record, "outcome"):
            assert record.user_id in {PLUS_USER, FREE_USER}
            assert record.mix_id == GOLDEN_HOUR
            datetime.fromisoformat(record.vote_timestamp)
