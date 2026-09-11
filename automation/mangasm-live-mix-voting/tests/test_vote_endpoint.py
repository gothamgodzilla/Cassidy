"""Acceptance tests for ``POST /api/v1/swarm/audio/vote``."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from conftest import (
    CLOSED_MIX_ID,
    FREE_USER,
    MIX_ID,
    PLUS_USER,
    FakeSupabase,
    formatted,
    vote,
)

from app.errors import MEMBERSHIP_REQUIRED_DETAIL


class TestSuccessfulVote:
    """Acceptance: Mangasm+ users can cast a vote, updating the DB atomically."""

    def test_returns_updated_count_and_rankings(self, client, fake_supabase: FakeSupabase):
        response = vote(client)

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "recorded"
        assert body["user_id"] == PLUS_USER
        assert body["mix_id"] == MIX_ID
        assert body["vote_count"] == 42
        assert body["queue_degraded"] is False
        assert fake_supabase.mixes[MIX_ID]["vote_count"] == 42
        assert (PLUS_USER, MIX_ID) in fake_supabase.votes

        queue = body["queue"]
        assert queue[0] == {
            "rank": 1,
            "mix_id": MIX_ID,
            "title": "Berko - Golden Hour Progressive House | 4K DJ Set",
            "vote_count": 42,
        }
        assert [entry["vote_count"] for entry in queue] == sorted(
            (entry["vote_count"] for entry in queue), reverse=True
        )

    def test_vote_timestamp_is_returned(self, client):
        body = vote(client).json()
        parsed = datetime.fromisoformat(body["vote_timestamp"])
        assert parsed.tzinfo is not None
        assert abs((datetime.now(timezone.utc) - parsed).total_seconds()) < 60

    def test_ids_are_trimmed(self, client, fake_supabase: FakeSupabase):
        response = vote(client, user_id=f"  {PLUS_USER}  ", mix_id=f"\t{MIX_ID}\n")

        assert response.status_code == 200
        assert response.json()["user_id"] == PLUS_USER
        assert (PLUS_USER, MIX_ID) in fake_supabase.votes

    def test_tied_mixes_share_a_rank(self, client, fake_supabase: FakeSupabase):
        # Both mixes sit on 41; the vote lifts one to 42, leaving the other and
        # the closed mix to be ranked 2 and 3.
        queue = vote(client).json()["queue"]

        assert [(entry["mix_id"], entry["rank"]) for entry in queue] == [
            (MIX_ID, 1),
            ("matryxx-warmup", 2),
            (CLOSED_MIX_ID, 3),
        ]

    def test_concurrent_votes_do_not_lose_increments(self, client, fake_supabase: FakeSupabase):
        """The counter is owned by the RPC, so no increment may be lost."""
        voters = [f"plus-{index}" for index in range(25)]
        for user_id in voters:
            fake_supabase.memberships[user_id] = {
                "user_id": user_id,
                "tier": "plus",
                "status": "active",
                "current_period_end": None,
            }

        responses = [vote(client, user_id=user_id) for user_id in voters]

        assert all(response.status_code == 200 for response in responses)
        assert fake_supabase.mixes[MIX_ID]["vote_count"] == 41 + len(voters)
        assert sorted(response.json()["vote_count"] for response in responses) == list(
            range(42, 42 + len(voters))
        )


class TestMembershipEnforcement:
    """Acceptance: non-plus members are blocked from voting."""

    @pytest.mark.parametrize(
        "membership",
        [
            pytest.param(None, id="no_membership_record"),
            pytest.param({"tier": "free", "status": "active"}, id="free_tier"),
            pytest.param({"tier": "plus", "status": "canceled"}, id="canceled"),
            pytest.param({"tier": "plus", "status": "inactive"}, id="inactive"),
            pytest.param(
                {
                    "tier": "plus",
                    "status": "active",
                    "current_period_end": (
                        datetime.now(timezone.utc) - timedelta(days=1)
                    ).isoformat(),
                },
                id="lapsed_period",
            ),
        ],
    )
    def test_blocked_with_403(self, client, fake_supabase: FakeSupabase, membership):
        user_id = "candidate"
        if membership is not None:
            fake_supabase.memberships[user_id] = {"user_id": user_id, **membership}

        response = vote(client, user_id=user_id)

        assert response.status_code == 403
        assert response.json() == {
            "error": "membership_required",
            "detail": MEMBERSHIP_REQUIRED_DETAIL,
        }
        assert fake_supabase.votes == set()
        assert fake_supabase.mixes[MIX_ID]["vote_count"] == 41
        assert "cast_mix_vote" not in fake_supabase.calls

    def test_free_user_from_fixture_is_blocked(self, client):
        assert vote(client, user_id=FREE_USER).status_code == 403

    @pytest.mark.parametrize("tier", ["plus", "PLUS", "Mangasm_Plus", "m+"])
    def test_recognised_plus_tier_spellings(self, client, fake_supabase: FakeSupabase, tier):
        fake_supabase.memberships["spelling"] = {
            "user_id": "spelling",
            "tier": tier,
            "status": "ACTIVE",
        }
        assert vote(client, user_id="spelling").status_code == 200


class TestDuplicateVotes:
    """Acceptance: duplicate votes on the same mix are rejected."""

    def test_second_vote_returns_409(self, client, fake_supabase: FakeSupabase):
        assert vote(client).status_code == 200

        response = vote(client)

        assert response.status_code == 409
        assert response.json()["error"] == "duplicate_vote"
        assert fake_supabase.mixes[MIX_ID]["vote_count"] == 42

    def test_same_user_may_vote_for_a_different_mix(self, client):
        assert vote(client).status_code == 200
        assert vote(client, mix_id="matryxx-warmup").status_code == 200

    def test_unique_violation_from_postgrest_maps_to_409(
        self, client, fake_supabase: FakeSupabase
    ):
        """A raw ``23505`` is treated as a duplicate, not a 500."""
        fake_supabase.force(
            "cast_mix_vote",
            409,
            {"code": "23505", "message": "duplicate key value violates unique constraint"},
        )

        response = vote(client)

        assert response.status_code == 409
        assert response.json()["error"] == "duplicate_vote"


class TestInvalidInput:
    @pytest.mark.parametrize(
        "body",
        [
            pytest.param({}, id="empty_body"),
            pytest.param({"user_id": PLUS_USER}, id="missing_mix_id"),
            pytest.param({"mix_id": MIX_ID}, id="missing_user_id"),
            pytest.param({"user_id": "", "mix_id": MIX_ID}, id="empty_user_id"),
            pytest.param({"user_id": PLUS_USER, "mix_id": "   "}, id="blank_mix_id"),
            pytest.param({"user_id": 7, "mix_id": MIX_ID}, id="non_string_user_id"),
            pytest.param({"user_id": None, "mix_id": MIX_ID}, id="null_user_id"),
            pytest.param(
                {"user_id": PLUS_USER, "mix_id": MIX_ID, "votes": 99},
                id="unexpected_field",
            ),
            pytest.param({"user_id": "x" * 200, "mix_id": MIX_ID}, id="oversized_user_id"),
        ],
    )
    def test_returns_400(self, client, fake_supabase: FakeSupabase, body):
        response = client.post("/api/v1/swarm/audio/vote", json=body)

        assert response.status_code == 400
        assert response.json()["error"] == "invalid_request"
        assert fake_supabase.calls == []

    def test_malformed_json_returns_400(self, client):
        response = client.post(
            "/api/v1/swarm/audio/vote",
            content=b"{not json",
            headers={"content-type": "application/json"},
        )
        assert response.status_code == 400


class TestMixState:
    def test_unknown_mix_returns_404(self, client):
        response = vote(client, mix_id="never-scheduled")

        assert response.status_code == 404
        assert response.json()["error"] == "mix_not_found"

    def test_closed_voting_window_returns_409(self, client):
        response = vote(client, mix_id=CLOSED_MIX_ID)

        assert response.status_code == 409
        assert response.json()["error"] == "voting_closed"


class TestExternalFailureHandling:
    def test_transient_failures_are_retried_then_succeed(
        self, client, fake_supabase: FakeSupabase, sleep_calls
    ):
        fake_supabase.fail_transiently("cast_mix_vote", 2)

        response = vote(client)

        assert response.status_code == 200
        assert fake_supabase.calls.count("cast_mix_vote") == 3
        assert len(sleep_calls) == 2

    def test_exhausted_retries_return_500(
        self, client, fake_supabase: FakeSupabase, sleep_calls
    ):
        fake_supabase.fail_transiently("cast_mix_vote", 99)

        response = vote(client)

        assert response.status_code == 500
        assert response.json()["error"] == "upstream_unavailable"
        # One initial attempt plus the three configured retries.
        assert fake_supabase.calls.count("cast_mix_vote") == 4
        assert len(sleep_calls) == 3

    def test_membership_lookup_failure_returns_500(self, client, fake_supabase: FakeSupabase):
        fake_supabase.fail_transiently("fetch_membership", 99)

        response = vote(client)

        assert response.status_code == 500
        assert response.json()["error"] == "upstream_unavailable"
        assert fake_supabase.calls.count("fetch_membership") == 4
        assert "cast_mix_vote" not in fake_supabase.calls

    def test_client_errors_are_not_retried(self, client, fake_supabase: FakeSupabase):
        fake_supabase.force("cast_mix_vote", 400, {"code": "22P02", "message": "bad input"})

        response = vote(client)

        assert response.status_code == 500
        assert fake_supabase.calls.count("cast_mix_vote") == 1

    def test_upstream_auth_failure_does_not_leak_as_403(
        self, client, fake_supabase: FakeSupabase
    ):
        """A rejected service key is our misconfiguration, not the user's."""
        fake_supabase.force("cast_mix_vote", 401, {"message": "invalid api key"})

        response = vote(client)

        assert response.status_code == 500
        assert "invalid api key" not in response.text

    def test_queue_failure_still_reports_the_committed_vote(
        self, client, fake_supabase: FakeSupabase
    ):
        """The vote is already durable, so a ranking read failure is degraded, not fatal."""
        fake_supabase.fail_transiently("fetch_queue", 99)

        response = vote(client)

        assert response.status_code == 200
        body = response.json()
        assert body["vote_count"] == 42
        assert body["queue_degraded"] is True
        assert body["queue"] == [{"rank": 1, "mix_id": MIX_ID, "title": None, "vote_count": 42}]
        assert fake_supabase.mixes[MIX_ID]["vote_count"] == 42

    def test_unexpected_rpc_status_returns_500(self, client, fake_supabase: FakeSupabase):
        fake_supabase.force("cast_mix_vote", 200, {"status": "wat"})

        response = vote(client)

        assert response.status_code == 500
        assert response.json()["error"] == "upstream_unavailable"

    def test_unexpected_exception_returns_a_generic_500(self, client, monkeypatch):
        """Nothing internal leaks through the catch-all handler."""

        async def boom(*_args, **_kwargs):
            raise RuntimeError("secret internal detail")

        monkeypatch.setattr(
            client.app.state.service, "cast_vote", boom, raising=True
        )

        response = vote(client)

        assert response.status_code == 500
        assert response.json() == {
            "error": "internal_error",
            "detail": "Unexpected server error",
        }
        assert "secret internal detail" not in response.text


class TestLogging:
    def test_vote_is_logged_with_required_fields(self, client, log_records):
        vote(client)

        records = [formatted(r) for r in log_records if r.getMessage() == "mix_vote"]
        assert len(records) == 1
        payload = records[0]
        assert payload["user_id"] == PLUS_USER
        assert payload["mix_id"] == MIX_ID
        assert payload["outcome"] == "recorded"
        datetime.fromisoformat(payload["vote_timestamp"])

    def test_rejected_vote_is_logged_with_required_fields(self, client, log_records):
        vote(client, user_id=FREE_USER)

        records = [formatted(r) for r in log_records if r.getMessage() == "mix_vote"]
        assert len(records) == 1
        payload = records[0]
        assert payload["user_id"] == FREE_USER
        assert payload["mix_id"] == MIX_ID
        assert payload["outcome"] == "MembershipRequiredError"
        datetime.fromisoformat(payload["vote_timestamp"])

    def test_retry_attempts_are_logged(self, client, fake_supabase: FakeSupabase, log_records):
        fake_supabase.fail_transiently("cast_mix_vote", 1)

        vote(client)

        retries = [
            formatted(r) for r in log_records if r.getMessage() == "supabase_call_failed"
        ]
        assert len(retries) == 1
        assert retries[0]["operation"] == "cast_mix_vote"
        assert retries[0]["reason"] == "HTTP 503"
        assert retries[0]["will_retry"] is True

    def test_service_key_is_never_logged(self, client, fake_supabase, log_records):
        import json

        from conftest import SUPABASE_KEY

        fake_supabase.force("cast_mix_vote", 401, {"message": "invalid api key"})
        vote(client)

        assert log_records
        for record in log_records:
            assert SUPABASE_KEY not in json.dumps(formatted(record))

    def test_key_is_sent_to_supabase(self, client, fake_supabase: FakeSupabase):
        from conftest import SUPABASE_KEY

        vote(client)

        headers = fake_supabase.seen_headers[0]
        assert headers["apikey"] == SUPABASE_KEY
        assert headers["authorization"] == f"Bearer {SUPABASE_KEY}"


class TestServiceDirectly:
    def test_non_string_input_raises_invalid_input(self, settings, supabase_client):
        from app.errors import InvalidInputError
        from app.service import VotingService

        service = VotingService(settings, supabase_client)

        with pytest.raises(InvalidInputError):
            asyncio.run(service.cast_vote(None, MIX_ID))
