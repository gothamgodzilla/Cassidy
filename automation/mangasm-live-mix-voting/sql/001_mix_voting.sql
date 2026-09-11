-- Schema and RPC backing the Mangasm+ Live Mix Voting Engine.
--
-- Apply with `psql "$SUPABASE_DB_URL" -f sql/001_mix_voting.sql` or by pasting
-- into the Supabase SQL editor. The script is idempotent.
--
-- Atomicity contract: `cast_mix_vote` performs the duplicate check, the vote
-- insert and the counter increment inside a single statement each, in one
-- transaction. The application never reads a count and writes it back, so
-- concurrent votes for the same mix cannot lose an increment.

create extension if not exists pgcrypto;

-- Mangasm+ subscription state, one row per member.
create table if not exists public.mangasm_memberships (
    user_id             text primary key,
    tier                text not null default 'free',
    status              text not null default 'inactive',
    current_period_end  timestamptz,
    updated_at          timestamptz not null default now()
);

-- Mixes eligible for a Friday/Saturday live session.
create table if not exists public.live_mixes (
    mix_id          text primary key,
    title           text not null,
    session_night   text not null check (session_night in ('friday', 'saturday')),
    voting_opens_at timestamptz not null default now(),
    voting_closes_at timestamptz,
    vote_count      integer not null default 0 check (vote_count >= 0),
    created_at      timestamptz not null default now()
);

-- One row per (member, mix). The primary key is what makes a second vote
-- impossible, regardless of how many app instances are running.
create table if not exists public.mix_votes (
    user_id         text not null,
    mix_id          text not null references public.live_mixes (mix_id) on delete cascade,
    vote_timestamp  timestamptz not null default now(),
    primary key (user_id, mix_id)
);

create index if not exists mix_votes_mix_id_idx on public.mix_votes (mix_id);

-- Ranking view read by the API after a successful vote.
create or replace view public.live_mix_queue as
select mix_id,
       title,
       session_night,
       vote_count,
       rank() over (order by vote_count desc, mix_id asc) as queue_rank
from public.live_mixes;

-- Atomically record a vote and return the new total.
--
-- Returns jsonb with a `status` of:
--   recorded    -- vote stored, `vote_count` is the new total
--   duplicate   -- this member already voted for this mix (HTTP 409)
--   unknown_mix -- no such mix (HTTP 404)
--   closed      -- the voting window is not open (HTTP 409)
create or replace function public.cast_mix_vote(p_user_id text, p_mix_id text)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
    v_mix        public.live_mixes;
    v_now        timestamptz := now();
    v_new_count  integer;
    v_timestamp  timestamptz;
begin
    if p_user_id is null or btrim(p_user_id) = ''
       or p_mix_id is null or btrim(p_mix_id) = '' then
        return jsonb_build_object('status', 'invalid_input');
    end if;

    select * into v_mix from public.live_mixes where mix_id = p_mix_id;
    if not found then
        return jsonb_build_object('status', 'unknown_mix', 'mix_id', p_mix_id);
    end if;

    if v_now < v_mix.voting_opens_at
       or (v_mix.voting_closes_at is not null and v_now >= v_mix.voting_closes_at) then
        return jsonb_build_object('status', 'closed', 'mix_id', p_mix_id);
    end if;

    -- The insert both stores the vote record and detects duplicates: on
    -- conflict nothing is written and no row is returned.
    insert into public.mix_votes (user_id, mix_id, vote_timestamp)
    values (p_user_id, p_mix_id, v_now)
    on conflict (user_id, mix_id) do nothing
    returning vote_timestamp into v_timestamp;

    if v_timestamp is null then
        return jsonb_build_object(
            'status', 'duplicate',
            'mix_id', p_mix_id,
            'vote_count', v_mix.vote_count
        );
    end if;

    update public.live_mixes
       set vote_count = vote_count + 1
     where mix_id = p_mix_id
    returning vote_count into v_new_count;

    return jsonb_build_object(
        'status', 'recorded',
        'mix_id', p_mix_id,
        'vote_count', v_new_count,
        'vote_timestamp', to_char(v_timestamp at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"')
    );
end;
$$;

-- The service authenticates with the service-role key, so lock the tables down
-- to it and expose only what members need to read.
alter table public.mangasm_memberships enable row level security;
alter table public.live_mixes enable row level security;
alter table public.mix_votes enable row level security;

drop policy if exists live_mixes_readable on public.live_mixes;
create policy live_mixes_readable on public.live_mixes for select using (true);

revoke all on function public.cast_mix_vote(text, text) from public;
grant execute on function public.cast_mix_vote(text, text) to service_role;

-- Seed row for the current episode (see app/content.py).
insert into public.live_mixes (mix_id, title, session_night)
values (
    'berko-golden-hour-progressive-house',
    'Berko - Golden Hour Progressive House | 4K DJ Set',
    'friday'
)
on conflict (mix_id) do nothing;
