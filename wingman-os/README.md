# Wingman.OS Core — Mangasm+ Live Mix Voting Engine

FastAPI automation that records Friday/Saturday live mix votes for Mangasm+ members.

## Trigger

`POST /api/v1/swarm/audio/vote` when a Mangasm+ member votes for a live mix.

## Inputs

| Field | Source |
| --- | --- |
| `user_id` | JSON body |
| `mix_id` | JSON body |
| `SUPABASE_URL` | environment |
| `SUPABASE_KEY` | environment (service role; server-side only) |

## Flow

1. Validate `user_id` and `mix_id`.
2. Confirm the caller has an active Mangasm+ subscription.
3. Call Supabase RPC `cast_mix_vote` to insert the vote row and increment the mix count in one transaction.
4. Reject a second vote from the same member on the same mix with `409 Conflict`.
5. Return the updated vote count and queue rankings.

Featured mix: **Golden Hour Progressive House & Melodic Techno DJ Set** by MATRYXX & BERKO — `mangasm.mascot` episode premier of the online visual experience, all welcome at [mangasm.app](https://mangasm.app).

## HTTP contract

| Status | Meaning |
| --- | --- |
| 200 | Vote recorded; body includes `vote_count` and `rankings` |
| 400 | Invalid input |
| 403 | Not Mangasm+ / missing membership (`M+ Membership Required`) |
| 409 | Duplicate vote |
| 500 | Supabase still failing after 3 retries |

Health check: `GET /api/v1/health`

Featured catalog: `GET /api/v1/swarm/audio/featured`

## Local run

```bash
cd wingman-os
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env   # then set SUPABASE_URL and SUPABASE_KEY
pytest
uvicorn app.main:app --host 0.0.0.0 --port 8080
```

Apply `sql/001_live_mix_voting.sql` in Supabase before serving production traffic.

## Deploy

Runtime: Wingman.OS Core (FastAPI). Required secret: `SUPABASE_KEY`. Image build:

```bash
docker build -t wingman-os-live-mix-voting ./wingman-os
```
