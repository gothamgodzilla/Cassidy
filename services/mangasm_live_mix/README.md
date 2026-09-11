<!--
 Licensed to the Apache Software Foundation (ASF) under one or more
 contributor license agreements. See the NOTICE file distributed with
 this work for additional information regarding copyright ownership.
 The ASF licenses this file to you under the Apache License, Version 2.0.
 You may obtain a copy at http://www.apache.org/licenses/LICENSE-2.0
-->

# Mangasm+ Live Mix Voting Engine

FastAPI automation for the Wingman.OS Core runtime. It verifies a Mangasm+
subscription and delegates the vote write to one PostgreSQL transaction.

## API

`POST /api/v1/swarm/audio/vote`

The caller must send a valid Supabase access token. The token's verified user ID
must match `user_id`; the request body cannot be used to impersonate another
member.

```text
Authorization: Bearer SUPABASE_ACCESS_TOKEN
Idempotency-Key: 58bb881b-3360-42a6-819e-8e32fdcb1b85
```

Generate one UUID per intended vote and reuse it whenever retrying the same HTTP
request. Reusing it after a lost response returns the original committed vote
result; a different key for an existing user/mix pair returns `409 Conflict`.

```json
{
  "user_id": "member-123",
  "mix_id": "golden-hour"
}
```

A successful response contains the atomically updated count and live queue:

```json
{
  "mix_id": "golden-hour",
  "vote_count": 42,
  "queue_rankings": [
    {
      "mix_id": "golden-hour",
      "vote_count": 42,
      "rank": 1
    }
  ]
}
```

`GET /api/v1/health` is the container health check.

## Database setup

Apply the timestamped files in `supabase/migrations/` to the target Supabase
project in order. They create the subscription, live-mix, and vote tables,
install the `cast_mix_vote` RPC, and safely upgrade installations that applied
the initial migration before retry hardening was added.

The RPC performs the membership recheck, duplicate-protected vote insert,
counter update, and ranking query in the same database transaction. Membership
and mix rows are locked against revocation during the transaction. A generated
operation ID makes retries idempotent if a response is lost after commit. Table
RLS is enabled, direct anonymous/authenticated access is revoked, and RPC
execution is restricted to the server-side `service_role`.

Load subscriptions and Friday/Saturday mixes before enabling traffic. The
`content/mangasm-mascot-golden-hour.json` file contains the supplied Golden Hour
episode-premiere metadata for the `mangasm.mascot` publishing workflow.

## Runtime

Set both variables in the Wingman.OS secret manager:

```text
SUPABASE_URL=https://PROJECT.supabase.co
SUPABASE_KEY=SERVER_SIDE_SECRET_OR_SERVICE_ROLE_KEY
```

`SUPABASE_KEY` bypasses RLS and must never be exposed to clients. Restrict this
credential to this service and rotate it through the secret manager. The
service requires TLS for Supabase, validates each caller's access token, and
binds the vote to that verified identity. Apply rate limits at the Wingman
ingress using the verified principal.

Build and run:

```bash
docker build -t mangasm-live-mix-voting .
docker run --rm -p 8000:8000 \
  -e SUPABASE_URL \
  -e SUPABASE_KEY \
  mangasm-live-mix-voting
```

The client retries HTTP 408, 429, 5xx, timeout, and transport failures three
times with exponential backoff. Permanent Supabase errors are not retried.

## Tests

```bash
python -m pytest
```
