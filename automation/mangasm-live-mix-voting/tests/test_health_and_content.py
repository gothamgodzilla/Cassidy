"""Health check and mascot/episode metadata."""

from __future__ import annotations

from conftest import SUPABASE_KEY

from app import __version__
from app.content import MASCOT_SLUG, featured_payload


class TestHealth:
    def test_reports_ok(self, client):
        response = client.get("/api/v1/health")

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["service"] == "mangasm-live-mix-voting"
        assert body["version"] == __version__
        assert body["runtime"] == "Wingman.OS Core"

    def test_never_exposes_the_service_key(self, client):
        response = client.get("/api/v1/health")

        assert SUPABASE_KEY not in response.text
        assert response.json()["config"]["supabase_key"] == "***redacted***"

    def test_needs_no_upstream_call(self, client, fake_supabase):
        client.get("/api/v1/health")

        assert fake_supabase.calls == []


class TestFeaturedEpisode:
    def test_includes_mascot_announcement(self, client):
        response = client.get("/api/v1/swarm/audio/featured")

        assert response.status_code == 200
        mascot = response.json()["mascot"]
        assert mascot["slug"] == MASCOT_SLUG
        assert mascot["destination"] == "https://mangasm.app"
        assert mascot["invitation"] == "All welcome"

    def test_describes_the_current_set(self, client):
        body = client.get("/api/v1/swarm/audio/featured").json()

        assert body["mix_id"] == "berko-golden-hour-progressive-house"
        assert body["djs"] == ["MATRYXX", "BERKO"]
        assert body["session_nights"] == ["friday", "saturday"]
        assert set(body["links"]) == {"spotify", "soundcloud", "instagram"}

    def test_payload_is_a_defensive_copy(self):
        first = featured_payload()
        first["djs"].append("someone else")

        assert featured_payload()["djs"] == ["MATRYXX", "BERKO"]
