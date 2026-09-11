"""Unit and integration tests for Mangasm+ Live Mix Voting Engine."""

import pytest
import httpx
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from mangasm_voting_engine.app import app
from mangasm_voting_engine.config import settings
from mangasm_voting_engine.schemas import VoteRequest
from mangasm_voting_engine.service import VotingService
from mangasm_voting_engine.supabase_client import (
    DuplicateVoteError,
    MembershipCheckError,
    SupabaseClient,
    SupabaseServiceError,
)


@pytest.fixture(autouse=True)
def setup_env(monkeypatch):
    """Set test environment variables."""
    monkeypatch.setattr(settings, "supabase_url", "https://test.supabase.co")
    monkeypatch.setattr(settings, "supabase_key", "test-secret-key")
    monkeypatch.setattr(settings, "max_retries", 3)
    monkeypatch.setattr(settings, "http_timeout_seconds", 2.0)


@pytest.mark.asyncio
async def test_health_check():
    """Verify GET /api/v1/health returns 200 and healthy status."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert "version" in data
        assert "timestamp" in data


@pytest.mark.asyncio
async def test_vote_invalid_input_empty_payload():
    """Verify invalid input yields 400 Bad Request."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # Empty body
        response = await client.post("/api/v1/swarm/audio/vote", json={})
        assert response.status_code == 400
        data = response.json()
        assert "Invalid input" in data.get("detail", "")


@pytest.mark.asyncio
async def test_vote_invalid_input_missing_fields():
    """Verify missing user_id or mix_id yields 400 Bad Request."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # Missing mix_id
        response = await client.post(
            "/api/v1/swarm/audio/vote",
            json={"user_id": "usr_123"},
        )
        assert response.status_code == 400

        # Missing user_id
        response = await client.post(
            "/api/v1/swarm/audio/vote",
            json={"mix_id": "mix_golden_hour_berko_01"},
        )
        assert response.status_code == 400

        # Empty string user_id
        response = await client.post(
            "/api/v1/swarm/audio/vote",
            json={"user_id": "", "mix_id": "mix_golden_hour_berko_01"},
        )
        assert response.status_code == 400


@pytest.mark.asyncio
async def test_vote_non_member_forbidden():
    """Verify non-Mangasm+ member is blocked with 403 Forbidden: M+ Membership Required."""
    with patch.object(
        SupabaseClient, "check_membership", new_callable=AsyncMock
    ) as mock_check:
        mock_check.return_value = False

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/v1/swarm/audio/vote",
                json={
                    "user_id": "non_member_user",
                    "mix_id": "mix_golden_hour_berko_01",
                },
            )
            assert response.status_code == 403
            assert response.json()["detail"] == "M+ Membership Required"


@pytest.mark.asyncio
async def test_vote_duplicate_conflict():
    """Verify duplicate vote yields 409 Conflict."""
    with patch.object(
        SupabaseClient, "check_membership", new_callable=AsyncMock
    ) as mock_check, patch.object(
        SupabaseClient, "cast_mix_vote_rpc", new_callable=AsyncMock
    ) as mock_rpc:
        mock_check.return_value = True
        mock_rpc.side_effect = DuplicateVoteError("User user_456 already voted for mix mix_01")

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/v1/swarm/audio/vote",
                json={
                    "user_id": "user_456",
                    "mix_id": "mix_01",
                },
            )
            assert response.status_code == 409
            assert "already voted" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_vote_successful():
    """Verify successful vote casts atomically, logs audit fields, and returns updated count + rankings."""
    vote_time = "2026-09-11T20:00:00Z"
    expected_rankings = [
        {
            "mix_id": "mix_golden_hour_berko_01",
            "title": "Berko - Golden Hour Progressive House | 4K DJ Set",
            "dj_name": "MATRYXX & BERKO",
            "vote_count": 42,
            "rank": 1,
        },
        {
            "mix_id": "mix_friday_sunset_02",
            "title": "Sunset Trance Session",
            "dj_name": "DJ Sunset",
            "vote_count": 28,
            "rank": 2,
        },
    ]

    with patch.object(
        SupabaseClient, "check_membership", new_callable=AsyncMock
    ) as mock_check, patch.object(
        SupabaseClient, "cast_mix_vote_rpc", new_callable=AsyncMock
    ) as mock_rpc:
        mock_check.return_value = True
        mock_rpc.return_value = {
            "success": True,
            "user_id": "user_vip_001",
            "mix_id": "mix_golden_hour_berko_01",
            "updated_vote_count": 42,
            "vote_timestamp": vote_time,
            "queue_rankings": expected_rankings,
        }

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/v1/swarm/audio/vote",
                json={
                    "user_id": "user_vip_001",
                    "mix_id": "mix_golden_hour_berko_01",
                },
            )
            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert data["user_id"] == "user_vip_001"
            assert data["mix_id"] == "mix_golden_hour_berko_01"
            assert data["updated_vote_count"] == 42
            assert len(data["queue_rankings"]) == 2
            assert data["queue_rankings"][0]["mix_id"] == "mix_golden_hour_berko_01"
            assert data["queue_rankings"][0]["rank"] == 1


@pytest.mark.asyncio
async def test_external_service_failure_retry_and_500():
    """Verify external service failure retries 3 times then returns 500."""
    with patch.object(
        SupabaseClient, "check_membership", new_callable=AsyncMock
    ) as mock_check:
        mock_check.side_effect = SupabaseServiceError(
            "External service failure after 3 attempts"
        )

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/v1/swarm/audio/vote",
                json={
                    "user_id": "user_vip_001",
                    "mix_id": "mix_golden_hour_berko_01",
                },
            )
            assert response.status_code == 500
            assert response.json()["detail"] == "External service failure"


@pytest.mark.asyncio
async def test_supabase_client_retry_logic():
    """Test that SupabaseClient _execute_with_retry actually retries 3 times on 500."""
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    # Simulate 500 responses
    mock_response = httpx.Response(
        status_code=500,
        content=b'{"error": "Internal Server Error"}',
        request=httpx.Request("POST", "https://test.supabase.co/rest/v1/rpc/cast_mix_vote"),
    )
    mock_client.request.return_value = mock_response

    client = SupabaseClient(
        supabase_url="https://test.supabase.co",
        supabase_key="test-key",
        max_retries=3,
        client=mock_client,
    )

    with pytest.raises(SupabaseServiceError):
        await client._execute_with_retry("POST", "/rest/v1/rpc/cast_mix_vote", json_data={})

    assert mock_client.request.call_count == 3


@pytest.mark.asyncio
async def test_supabase_check_membership_logic():
    """Test check_membership handles active, inactive, and plan tiers correctly."""
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    client = SupabaseClient(
        supabase_url="https://test.supabase.co",
        supabase_key="test-key",
        client=mock_client,
    )

    # Case 1: Valid Mangasm+ active member
    mock_client.request.return_value = httpx.Response(
        status_code=200,
        json=[{"user_id": "u1", "plan_tier": "Mangasm+ Premium", "status": "active", "is_active": True}],
        request=httpx.Request("GET", "https://test.supabase.co/rest/v1/user_subscriptions"),
    )
    assert await client.check_membership("u1") is True

    # Case 2: Free tier member (not plus)
    mock_client.request.return_value = httpx.Response(
        status_code=200,
        json=[{"user_id": "u2", "plan_tier": "free", "status": "active", "is_active": True}],
        request=httpx.Request("GET", "https://test.supabase.co/rest/v1/user_subscriptions"),
    )
    assert await client.check_membership("u2") is False

    # Case 3: Mangasm+ cancelled/inactive
    mock_client.request.return_value = httpx.Response(
        status_code=200,
        json=[{"user_id": "u3", "plan_tier": "mangasm+", "status": "cancelled", "is_active": False}],
        request=httpx.Request("GET", "https://test.supabase.co/rest/v1/user_subscriptions"),
    )
    assert await client.check_membership("u3") is False

    # Case 4: No record found
    mock_client.request.return_value = httpx.Response(
        status_code=200,
        json=[],
        request=httpx.Request("GET", "https://test.supabase.co/rest/v1/user_subscriptions"),
    )
    assert await client.check_membership("u4") is False
