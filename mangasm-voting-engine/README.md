# Mangasm+ Live Mix Voting Engine

Production-ready automation for Friday/Saturday live-mix voting on Mangasm+.
Runtime: **Wingman.OS Core (FastAPI)**. Backend: **Supabase** (PostgREST + RPC).

Golden Hour feature: *Progressive House & Melodic Techno DJ Set* —
**Berko – Golden Hour Progressive House | 4K DJ Set** with new & unreleased music,
fuelling the Friday and Saturday live SoundCloud sessions.
**DJ: MATRYXX & BERKO.** Includes the **mangasm.mascot episode premiere**
online visual experience for all — welcome at [mangasm.app](https://mangasm.app).

Follow Berko: [Spotify](https://open.spotify.com/artist/5Lrm3iLbY5LEsjXecGd83x?si=c81J_cGATNOv73L1-I6t8w) ·
[SoundCloud](https://soundcloud.com/sapir-berko) ·
[Instagram](https://www.instagram.com/berko_ofc)

## Endpoints

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/v1/swarm/audio/vote` | Cast a vote (`user_id`, `mix_id`) |
| `GET` | `/api/v1/health` | Liveness probe |

## Quickstart

```bash
cd mangasm-voting-engine
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in SUPABASE_URL / SUPABASE_KEY
uvicorn app.main:app --reload --port 8000
```

Health:

```bash
curl http://localhost:8000/api/v1/health
```

Vote (Mangasm+ member):

```bash
curl -X POST http://localhost:8000/api/v1/swarm/audio/vote \
  -H 'Content-Type: application/json' \
  -d '{"user_id":"mplus-user-1","mix_id":"golden-hour-berko-4k"}'
```

Success response:

```json
{
  "mix_id": "golden-hour-berko-4k",
  "vote_count": 42,
  "rankings": [{"mix_id": "golden-hour-berko-4k", "title": "Berko - Golden Hour ...", "votes": 42, "rank": 1}],
  "voted_at": "2026-09-11T17:00:00Z",
  "message": "Vote counted. Thank you for shaping the Friday/Saturday live mix.",
  "promo": {"show": "Golden Hour Progressive House & Melodic Techno DJ Set", "djs": "MATRYXX & BERKO", "...": "..."}
}
```

## Flow

1. Validate `user_id` / `mix_id` (blank → `400`).
2. Check `m_plus_members` for `status = 'active'` (missing → `403 "M+ Membership Required"`).
3. Pre-check `mix_votes` for an existing `(user_id, mix_id)` row (found → `409`).
4. Call Supabase RPC `cast_mix_vote(p_user_id, p_mix_id)` which atomically
   inserts the vote row and increments `mixes.votes` in one transaction.
   A concurrent duplicate surfaces as unique-violation → `409`.
   Unknown `mix_id` → `404`.
5. Return the fresh `vote_count` plus ordered queue `rankings`.
6. Every step logs `user_id`, `mix_id`, `vote_timestamp`.

Supabase setup: apply `supabase/migrations/001_cast_mix_vote.sql`
(`supabase db push` or `psql`), which creates the tables, the
`UNIQUE(user_id, mix_id)` guard, the atomic RPC, and seeds the Golden Hour queue.

## Error handling

| Case | Status |
|---|---|
| Invalid / blank input | `400 Bad Request` |
| Not a Mangasm+ member / missing record | `403 Forbidden` (`M+ Membership Required`) |
| Duplicate vote on same mix | `409 Conflict` |
| Unknown mix | `404 Not Found` |
| Supabase 5xx / network failure | retried 3× with exponential backoff, then `500` |

Required secrets: `SUPABASE_URL`, `SUPABASE_KEY` (Wingman.OS secrets).
Never commit real values — use `.env.example` as the template.

## Tests

```bash
pytest -q
```

Covers: health, successful atomic vote + rankings, 403 gate,
409 duplicates, 400 validation, 404 unknown mix, 500 after retries,
and the retry helper itself.

## Deployment (Wingman.OS Core)

Built as a standard FastAPI service; `Dockerfile` runs
`uvicorn app.main:app` as non-root on port 8000 with a
`GET /api/v1/health` healthcheck. Set `SUPABASE_URL` / `SUPABASE_KEY`
in the platform secret store.

## Phase A — agent highway, ozone v0, frontend

**Agent topology** (`GET /api/v1/swarm/topology`): a closed ring —
`bpm-metronome → sha-cache → mix-voter → visual-mascot → …` — each node
running a local model (Llama 3 / Qwen via Ollama).

**Tunnel** (Supabase pgvector, `supabase/migrations/002_swarm_highway.sql`):
nodes publish lightweight JSON state vectors (sports cars), never raw
histories (heavy trucks).
`POST /api/v1/swarm/state` publishes (HMAC-sealed, 25SHA-stamped);
`GET /api/v1/swarm/state/next?agent_id=…` is how the next node catches;
`match_state_vectors()` RPC gives cosine recall over `embedding`.
`POST /api/v1/swarm/compress` reduces a chat history to a tunnel-ready
payload offline (extractive heuristic; `OLLAMA_URL` payload builder included
for continuing the cycle on a model server).

**Ozone v0** (`GET /api/v1/security/ozone`, `app/security.py`): HMAC-SHA256
envelopes on every vector, 25-round SHA-256 cache seals, secret redaction
in logs. v1 path: AES-256-GCM with KMS-wrapped per-loop keys.

**Frontend** (`/`, `frontend/`): dark-luxury Wingman.OS console — live
25SHA cache simulation, audio-reactive BPM metronome (WebAudio + canvas,
one-click beat publish), vote panel, and ring visualizer with live tunnel feed.
