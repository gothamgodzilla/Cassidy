"""Configuration loading and the retry/backoff policy in isolation."""

from __future__ import annotations

import asyncio

import httpx
import pytest
from conftest import MIX_ID, PLUS_USER

from app.config import ConfigurationError, Settings, load_settings
from app.supabase_client import SupabaseClient, SupabaseUnavailable

BASE_ENV = {
    "SUPABASE_URL": "https://project.supabase.co",
    "SUPABASE_KEY": "service-role-key",
}


class TestConfiguration:
    def test_loads_required_variables(self):
        settings = load_settings(dict(BASE_ENV))

        assert settings.supabase_url == "https://project.supabase.co"
        assert settings.rest_url == "https://project.supabase.co/rest/v1"
        assert settings.max_retries == 3

    @pytest.mark.parametrize("missing", ["SUPABASE_URL", "SUPABASE_KEY"])
    def test_missing_variable_is_fatal(self, missing):
        env = dict(BASE_ENV)
        del env[missing]

        with pytest.raises(ConfigurationError, match=missing):
            load_settings(env)

    @pytest.mark.parametrize("value", ["", "   ", "\t"])
    def test_blank_key_is_fatal(self, value):
        with pytest.raises(ConfigurationError, match="SUPABASE_KEY"):
            load_settings({**BASE_ENV, "SUPABASE_KEY": value})

    def test_trailing_slash_is_normalised(self):
        settings = load_settings({**BASE_ENV, "SUPABASE_URL": "https://project.supabase.co/"})

        assert settings.rest_url == "https://project.supabase.co/rest/v1"

    def test_non_http_url_is_rejected(self):
        with pytest.raises(ConfigurationError, match="absolute http"):
            load_settings({**BASE_ENV, "SUPABASE_URL": "project.supabase.co"})

    def test_key_is_not_in_the_repr(self):
        settings = load_settings(dict(BASE_ENV))

        assert "service-role-key" not in repr(settings)
        assert settings.redacted()["supabase_key"] == "***redacted***"

    def test_retry_count_is_overridable(self, monkeypatch):
        monkeypatch.setenv("MANGASM_MAX_RETRIES", "5")

        assert load_settings(dict(BASE_ENV)).max_retries == 5

    @pytest.mark.parametrize("value", ["abc", "-1", "99"])
    def test_bad_retry_count_is_fatal(self, value, monkeypatch):
        monkeypatch.setenv("MANGASM_MAX_RETRIES", value)

        with pytest.raises(ConfigurationError):
            load_settings(dict(BASE_ENV))


class TestRetryPolicy:
    @staticmethod
    def _client(handler, sleeps: list[float], **overrides) -> SupabaseClient:
        settings = Settings(
            supabase_url="https://project.supabase.co",
            supabase_key="k",
            **overrides,
        )

        async def _sleep(delay: float) -> None:
            sleeps.append(delay)

        http_client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler), base_url=settings.rest_url
        )
        return SupabaseClient(settings, client=http_client, sleep=_sleep)

    def test_connection_errors_are_retried(self):
        attempts: list[int] = []
        sleeps: list[float] = []

        def handler(request: httpx.Request) -> httpx.Response:
            attempts.append(1)
            raise httpx.ConnectError("connection refused", request=request)

        client = self._client(handler, sleeps, backoff_base_seconds=0.0, backoff_max_seconds=0.0)

        with pytest.raises(SupabaseUnavailable, match="cast_mix_vote"):
            asyncio.run(client.cast_vote(PLUS_USER, MIX_ID))

        assert len(attempts) == 4
        assert len(sleeps) == 3

    def test_timeouts_are_retried(self):
        sleeps: list[float] = []

        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("too slow", request=request)

        client = self._client(handler, sleeps, backoff_base_seconds=0.0, backoff_max_seconds=0.0)

        with pytest.raises(SupabaseUnavailable):
            asyncio.run(client.fetch_membership(PLUS_USER))

        assert len(sleeps) == 3

    @pytest.mark.parametrize("status_code", [408, 429, 500, 502, 503, 504])
    def test_transient_statuses_are_retried(self, status_code):
        sleeps: list[float] = []
        calls: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(1)
            if len(calls) < 3:
                return httpx.Response(status_code, json={"message": "later"})
            return httpx.Response(200, json={"status": "recorded", "vote_count": 1})

        client = self._client(handler, sleeps, backoff_base_seconds=0.0, backoff_max_seconds=0.0)
        result = asyncio.run(client.cast_vote(PLUS_USER, MIX_ID))

        assert result["status"] == "recorded"
        assert len(calls) == 3

    @pytest.mark.parametrize("status_code", [400, 401, 403, 404, 409, 422])
    def test_deterministic_statuses_are_not_retried(self, status_code):
        from app.supabase_client import SupabaseError

        sleeps: list[float] = []
        calls: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(1)
            return httpx.Response(status_code, json={"code": "23505"})

        client = self._client(handler, sleeps)

        with pytest.raises(SupabaseError) as excinfo:
            asyncio.run(client.cast_vote(PLUS_USER, MIX_ID))

        assert excinfo.value.status_code == status_code
        assert excinfo.value.pg_code == "23505"
        assert len(calls) == 1
        assert sleeps == []

    def test_retries_can_be_disabled(self):
        sleeps: list[float] = []
        calls: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(1)
            return httpx.Response(503)

        client = self._client(handler, sleeps, max_retries=0)

        with pytest.raises(SupabaseUnavailable):
            asyncio.run(client.fetch_queue())

        assert len(calls) == 1
        assert sleeps == []

    def test_backoff_grows_exponentially_and_is_capped(self):
        client = self._client(
            lambda request: httpx.Response(200, json=[]),
            [],
            backoff_base_seconds=0.5,
            backoff_max_seconds=2.0,
        )

        for attempt, ceiling in enumerate([0.5, 1.0, 2.0, 2.0, 2.0]):
            delays = [client._backoff_delay(attempt) for _ in range(50)]
            assert all(0.0 <= delay <= ceiling for delay in delays)
            assert max(delays) > ceiling / 2  # jitter actually spreads the delay

    def test_single_row_rpc_array_is_unwrapped(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=[{"status": "recorded", "vote_count": 3}])

        client = self._client(handler, [])

        assert asyncio.run(client.cast_vote(PLUS_USER, MIX_ID))["vote_count"] == 3

    def test_empty_membership_result_is_none(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=[])

        client = self._client(handler, [])

        assert asyncio.run(client.fetch_membership("nobody")) is None
