# Mangasm+ Live Mix Voting Engine

Production-ready automation engine for Mangasm+ members to vote on Friday and Saturday live DJ mixes (e.g., *Golden Hour Progressive House & Melodic Techno DJ Set* by MATRYXX & BERKO).

Built for **Wingman.OS Core** on **FastAPI** with **Supabase**.

---

## Architecture & Workflow

1. **Trigger**: Member casts a vote via `POST /api/v1/swarm/audio/vote` with `user_id` and `mix_id`.
2. **Step 1 - Membership Validation**: Validates that `user_id` has an active `Mangasm+` tier subscription. Non-members or missing records receive `403 Forbidden` (`M+ Membership Required`).
3. **Step 2, 3, 4 - Atomic RPC**: Calls Supabase RPC `cast_mix_vote` with atomic conflict resolution:
   - Verifies if the user already voted for this mix. If yes, raises/returns `409 Conflict`.
   - Records the user's vote in `mix_votes` table (`user_id`, `mix_id`, `vote_timestamp`).
   - Atomically increments the mix's vote count in `mixes` table.
4. **Step 5 - Rankings & Response**: Returns the updated vote count and real-time live queue rankings sorted by vote count descending.
5. **Auditing**: Logs `user_id`, `mix_id`, and `vote_timestamp` for all successful votes.
6. **Error Handling**:
   - `400 Bad Request`: Invalid or missing payload inputs (`user_id`, `mix_id`).
   - `403 Forbidden`: User lacks active Mangasm+ membership.
   - `409 Conflict`: User has already voted for this mix.
   - `500 Internal Server Error`: Retries Supabase calls 3 times with exponential backoff on failure before returning 500.

---

## API Endpoints

### 1. Cast Mix Vote
- **Path**: `POST /api/v1/swarm/audio/vote`
- **Request Body**:
  ```json
  {
    "user_id": "usr_plus_88219",
    "mix_id": "mix_golden_hour_berko_01"
  }
  ```
- **Response `200 OK`**:
  ```json
  {
    "success": true,
    "user_id": "usr_plus_88219",
    "mix_id": "mix_golden_hour_berko_01",
    "updated_vote_count": 42,
    "vote_timestamp": "2026-09-11T20:00:00Z",
    "queue_rankings": [
      {
        "mix_id": "mix_golden_hour_berko_01",
        "title": "Berko - Golden Hour Progressive House | 4K DJ Set",
        "dj_name": "MATRYXX & BERKO",
        "vote_count": 42,
        "rank": 1
      }
    ],
    "message": "Vote cast successfully"
  }
  ```

### 2. Health Check
- **Path**: `GET /api/v1/health`
- **Response `200 OK`**:
  ```json
  {
    "status": "healthy",
    "version": "1.0.0",
    "timestamp": "2026-09-11T17:35:00Z"
  }
  ```

---

## Environment Variables & Secrets

| Variable | Description | Required |
|---|---|---|
| `SUPABASE_URL` | Supabase project REST URL | Yes |
| `SUPABASE_KEY` | Supabase Service Role Key or API Key | Yes |
| `HTTP_TIMEOUT_SECONDS` | HTTP request timeout (default: 5.0) | Optional |
| `MAX_RETRIES` | Max retries for external service (default: 3) | Optional |

---

## Database Schema & RPC

The SQL migration file is available at `mangasm_voting_engine/schema.sql`. It includes:
- `user_subscriptions` table for active member validation
- `mixes` table tracking DJ sets (e.g. *Berko - Golden Hour Progressive House | 4K DJ Set*) and vote tallies
- `mix_votes` table with `UNIQUE(user_id, mix_id)` constraint
- `cast_mix_vote(p_user_id, p_mix_id)` PL/pgSQL function executing the atomic voting transaction and queue ranking calculation

---

## Running the Application

### Local Development
```bash
pip install -r mangasm_voting_engine/requirements.txt
export SUPABASE_URL="https://your-project.supabase.co"
export SUPABASE_KEY="your-secret-key"

uvicorn mangasm_voting_engine.app:app --host 0.0.0.0 --port 8000 --reload
```

### Running Tests
```bash
pytest mangasm_voting_engine/tests -v
```

### Docker
```bash
docker build -t mangasm-voting-engine -f mangasm_voting_engine/Dockerfile .
docker run -p 8000:8000 -e SUPABASE_URL="..." -e SUPABASE_KEY="..." mangasm-voting-engine
```
