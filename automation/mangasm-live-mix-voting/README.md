# Mangasm+ Live Mix Voting Engine

FastAPI service that lets Mangasm+ members vote for the Friday/Saturday live
mix. A vote is atomic, idempotent per `(user_id, mix_id)`, and gated on an
active Mangasm+ subscription.

> **Relationship to this repository.** This directory is a self-contained
> Python service. It is not part of the Apache Cassandra build: it adds no Java
> sources, no `lib/` jars, and no Ant targets, and `ant`/Checkstyle never
> traverse it. Install and test it with the Python commands below.

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/api/v1/swarm/audio/vote` | Cast a vote for a live mix |
| `GET` | `/api/v1/swarm/audio/featured` | Episode metadata for the `mangasm.mascot` overlay |
| `GET` | `/api/v1/health` | Health check (no upstream calls) |

### Cast a vote

```bash
curl -X POST http://localhost:8000/api/v1/swarm/audio/vote \
  -H 'content-type: application/json' \
  -d '{"user_id": "usr_123", "mix_id": "berko-golden-hour-progressive-house"}'
```

```json
{
  "status": "recorded",
  "user_id": "usr_123",
  "mix_id": "berko-golden-hour-progressive-house",
  "vote_count": 42,
  "vote_timestamp": "2026-09-11T21:14:02.881000Z",
  "queue": [
    {"rank": 1, "mix_id": "berko-golden-hour-progressive-house", "title": "Berko - Golden Hour Progressive House | 4K DJ Set", "vote_count": 42},
    {"rank": 2, "mix_id": "matryxx-warmup", "title": "MATRYXX - Warmup", "vote_count": 41}
  ],
  "queue_degraded": false
}
```

Ties share a rank and the next rank is skipped, so two mixes on 41 votes are
both rank 2 and the following mix is rank 4.

## Responses

Every non-2xx response uses the same envelope: `{"error": "<code>", "detail": "<message>"}`.

| Status | `error` | When |
| --- | --- | --- |
| `200` | — | Vote recorded |
| `400` | `invalid_request` | Missing, blank, non-string, oversized or unexpected fields |
| `403` | `membership_required` | No membership row, or not an active Mangasm+ — detail is always `M+ Membership Required` |
| `404` | `mix_not_found` | `mix_id` does not exist |
| `409` | `duplicate_vote` | This member already voted for this mix |
| `409` | `voting_closed` | The mix exists but its voting window is not open |
| `500` | `upstream_unavailable` | Supabase still failing after the retry budget |
| `500` | `internal_error` | Unexpected error; no internal detail is leaked |

A Supabase `401`/`403` means *this service's* key is wrong, so it maps to
`500`, never to a `403` that would wrongly blame the member.

## How a vote is processed

1. **Validate input.** Pydantic rejects anything that is not a non-blank
   string of at most 128 characters, and FastAPI's validation error is
   converted to `400` (instead of the default `422`).
2. **Check membership.** Read one row from `mangasm_memberships`. The member
   must hold a recognised plus tier (`plus`, `mangasm_plus`, `mangasm+`, `m+`;
   case-insensitive), a status of `active`, `trialing` or `past_due_grace`, and
   a `current_period_end` that has not passed. Anything else is `403`.
3. **Call the RPC.** `cast_mix_vote(p_user_id, p_mix_id)` inserts the vote
   record and increments the counter in one transaction.
4. **Reject duplicates.** The RPC reports `duplicate` when the
   `(user_id, mix_id)` primary key already exists; a raw PostgreSQL `23505` is
   treated identically. Either way the answer is `409`.
5. **Return rankings.** Read the `live_mix_queue` view and rank it.

### Atomicity

The counter is never read into Python and written back — that would lose
increments when two members vote for the same mix at the same time. Both the
duplicate check and the increment happen inside `cast_mix_vote`, so the
database is the single arbiter. See [`sql/001_mix_voting.sql`](sql/001_mix_voting.sql).

### Retries

Connection errors, timeouts, `408`, `425`, `429` and `5xx` are retried three
times after the initial attempt (four attempts total), with full-jitter
exponential backoff. Every other `4xx` is deterministic and fails immediately
rather than burning latency on a doomed retry.

One exception exists by design: if the *ranking read* fails, the vote has
already been committed, so returning `500` would be wrong — the client would
retry and get a `409`. Instead the response is `200` with
`"queue_degraded": true` and a single-entry queue, and the failure is logged.

### Logging

Logs are single-line JSON on stdout. Every vote attempt — successful or not —
emits one `mix_vote` record containing `user_id`, `mix_id`, `vote_timestamp`,
the outcome and the duration:

```json
{"timestamp":"2026-09-11T21:14:02+0000","level":"INFO","logger":"mangasm.voting","message":"mix_vote","user_id":"usr_123","mix_id":"berko-golden-hour-progressive-house","vote_timestamp":"2026-09-11T21:14:02.881000+00:00","outcome":"recorded","vote_count":42,"queue_degraded":false,"duration_ms":37.3}
```

`SUPABASE_KEY` is kept out of `repr(Settings)`, the health payload and all log
records.

## Configuration

`SUPABASE_URL` and `SUPABASE_KEY` are required; the process refuses to start
without them rather than failing on the first request. `SUPABASE_KEY` must be
the service-role key and must be supplied as a secret, never committed.

| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| `SUPABASE_URL` | yes | — | Project URL, e.g. `https://abc.supabase.co` |
| `SUPABASE_KEY` | yes | — | Service-role key (secret) |
| `LOG_LEVEL` | no | `INFO` | Logger level |
| `MANGASM_MEMBERSHIP_TABLE` | no | `mangasm_memberships` | Membership table |
| `MANGASM_QUEUE_VIEW` | no | `live_mix_queue` | Ranking view |
| `MANGASM_VOTE_RPC` | no | `cast_mix_vote` | RPC name |
| `MANGASM_MAX_RETRIES` | no | `3` | Retries after the first attempt |
| `MANGASM_BACKOFF_BASE_SECONDS` | no | `0.25` | Backoff base |
| `MANGASM_BACKOFF_MAX_SECONDS` | no | `4.0` | Backoff ceiling |
| `MANGASM_REQUEST_TIMEOUT_SECONDS` | no | `5.0` | Per-request timeout |
| `MANGASM_QUEUE_SIZE` | no | `25` | Queue entries returned |

## Running it

Apply the schema once:

```bash
psql "$SUPABASE_DB_URL" -f sql/001_mix_voting.sql
```

Then start the service:

```bash
pip install -r requirements.txt
export SUPABASE_URL="https://<project>.supabase.co"
export SUPABASE_KEY="<service-role-key>"
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Interactive API docs are served at `/docs`.

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

The suite covers the acceptance criteria plus the error matrix, the retry
budget, backoff bounds, configuration validation and secret redaction.
Supabase is replaced by an in-memory PostgREST double wired in through
`httpx.MockTransport`, so the real client, retry loop, service and routing are
all exercised — only the network hop is faked. No credentials or network
access are needed to run the tests.

## Layout

```
app/
  main.py             FastAPI app, routes, exception handlers
  service.py          the five vote steps
  supabase_client.py  PostgREST calls plus the retry policy
  models.py           request/response schemas
  errors.py           domain errors and their HTTP mapping
  config.py           environment-driven settings
  logging_config.py   JSON log formatting
  content.py          episode / mangasm.mascot metadata
sql/001_mix_voting.sql  tables, ranking view, cast_mix_vote RPC, RLS
tests/                  acceptance and unit tests
```

## Current episode

The seeded mix is **Berko - Golden Hour Progressive House | 4K DJ Set** by
MATRYXX & BERKO — progressive house and melodic techno for the Friday and
Saturday live sessions. `GET /api/v1/swarm/audio/featured` serves this metadata
together with the `mangasm.mascot` announcement for the episode premiere of the
online visual experience at [mangasm.app](https://mangasm.app).
