"""Static presentation metadata for the current live-mix episode.

This is display copy only — it is never used for authorisation and never
affects vote counting. The canonical vote totals always come from Supabase.
Keeping the episode blurb here (rather than in the database) lets the mascot
overlay and the voting client read the same source.
"""

from __future__ import annotations

from typing import Any

MASCOT_SLUG = "mangasm.mascot"

MASCOT_ANNOUNCEMENT: dict[str, Any] = {
    "slug": MASCOT_SLUG,
    "headline": "Episode premiere of the online visual experience",
    "invitation": "All welcome",
    "destination": "https://mangasm.app",
}

FEATURED_MIX: dict[str, Any] = {
    "mix_id": "berko-golden-hour-progressive-house",
    "title": "Berko - Golden Hour Progressive House | 4K DJ Set",
    "djs": ["MATRYXX", "BERKO"],
    "genres": ["progressive house", "melodic techno", "deep trance"],
    "session_nights": ["friday", "saturday"],
    "blurb": (
        "A golden hour session moving through progressive house and melodic "
        "sounds, with new and unreleased music."
    ),
    "links": {
        "spotify": "https://open.spotify.com/artist/5Lrm3iLbY5LEsjXecGd83x",
        "soundcloud": "https://soundcloud.com/sapir-berko",
        "instagram": "https://www.instagram.com/berko_ofc",
    },
    "mascot": MASCOT_ANNOUNCEMENT,
}


def featured_payload() -> dict[str, Any]:
    """Return a defensive copy so callers cannot mutate module state."""
    payload = dict(FEATURED_MIX)
    payload["djs"] = list(FEATURED_MIX["djs"])
    payload["genres"] = list(FEATURED_MIX["genres"])
    payload["session_nights"] = list(FEATURED_MIX["session_nights"])
    payload["links"] = dict(FEATURED_MIX["links"])
    payload["mascot"] = dict(MASCOT_ANNOUNCEMENT)
    return payload
