-- Licensed to the Apache Software Foundation (ASF) under one
-- or more contributor license agreements.  See the NOTICE file
-- distributed with this work for additional information
-- regarding copyright ownership.  The ASF licenses this file
-- to you under the Apache License, Version 2.0 (the
-- "License"); you may not use this file except in compliance
-- with the License.  You may obtain a copy of the License at
--
--     http://www.apache.org/licenses/LICENSE-2.0
--
-- Unless required by applicable law or agreed to in writing, software
-- distributed under the License is distributed on an "AS IS" BASIS,
-- WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
-- See the License for the specific language governing permissions and
-- limitations under the License.

-- Mangasm+ Live Mix Voting Engine schema and the atomic cast_mix_vote RPC.
--
-- Apply with the Supabase CLI (`supabase db push`) or paste into the SQL editor.

-- ---------------------------------------------------------------------------
-- Membership
-- ---------------------------------------------------------------------------
create table if not exists public.mangasm_subscriptions (
    user_id            text primary key,
    tier               text not null default 'free',       -- 'free' | 'plus'
    status             text not null default 'inactive',   -- 'active' | 'trialing' | 'past_due' | 'canceled' | 'inactive'
    current_period_end timestamptz,                        -- null = does not expire
    updated_at         timestamptz not null default now()
);

-- ---------------------------------------------------------------------------
-- Live mixes (the Friday / Saturday SoundCloud session queue)
-- ---------------------------------------------------------------------------
create table if not exists public.live_mixes (
    mix_id       text primary key,
    title        text not null,
    artist       text,
    session_slot text not null check (session_slot in ('friday', 'saturday')),
    source_url   text,
    description  text,
    voting_open  boolean not null default true,
    vote_count   integer not null default 0 check (vote_count >= 0),
    created_at   timestamptz not null default now()
);

create index if not exists live_mixes_queue_idx
    on public.live_mixes (session_slot, vote_count desc, created_at asc);

-- ---------------------------------------------------------------------------
-- One vote per member per mix. The primary key is what makes duplicates impossible,
-- even under concurrent requests.
-- ---------------------------------------------------------------------------
create table if not exists public.mix_votes (
    user_id  text not null,
    mix_id   text not null references public.live_mixes (mix_id) on delete cascade,
    voted_at timestamptz not null default now(),
    primary key (user_id, mix_id)
);

create index if not exists mix_votes_mix_idx on public.mix_votes (mix_id);

-- The service talks to Supabase with the service_role key, which bypasses RLS.
-- Enabling RLS with no policies locks the tables for anon / authenticated clients.
alter table public.mangasm_subscriptions enable row level security;
alter table public.live_mixes            enable row level security;
alter table public.mix_votes             enable row level security;

-- ---------------------------------------------------------------------------
-- cast_mix_vote: record the vote, bump the counter and return the rankings atomically.
--
-- Errors use PostgREST's custom "PTxxx" SQLSTATE convention so they surface as the
-- matching HTTP status: PT404 -> 404 MIX_NOT_FOUND, PT409 -> 409 DUPLICATE_VOTE.
-- ---------------------------------------------------------------------------
create or replace function public.cast_mix_vote(
    p_user_id        text,
    p_mix_id         text,
    p_voted_at       timestamptz default now(),
    p_rankings_limit integer     default 20
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
    v_slot     text;
    v_count    integer;
    v_rankings jsonb;
begin
    if p_user_id is null or btrim(p_user_id) = '' or p_mix_id is null or btrim(p_mix_id) = '' then
        raise exception 'INVALID_INPUT' using errcode = 'PT400';
    end if;

    -- Row lock serialises concurrent voters on the same mix for the duration of the transaction.
    select session_slot
      into v_slot
      from live_mixes
     where mix_id = p_mix_id
       and voting_open
       for update;

    if not found then
        raise exception 'MIX_NOT_FOUND' using errcode = 'PT404';
    end if;

    insert into mix_votes (user_id, mix_id, voted_at)
    values (p_user_id, p_mix_id, coalesce(p_voted_at, now()))
    on conflict (user_id, mix_id) do nothing;

    if not found then
        raise exception 'DUPLICATE_VOTE' using errcode = 'PT409';
    end if;

    update live_mixes
       set vote_count = vote_count + 1
     where mix_id = p_mix_id
    returning vote_count into v_count;

    select coalesce(
               jsonb_agg(
                   jsonb_build_object(
                       'rank',         r.rank,
                       'mix_id',       r.mix_id,
                       'title',        r.title,
                       'artist',       r.artist,
                       'session_slot', r.session_slot,
                       'vote_count',   r.vote_count
                   )
                   order by r.rank
               ),
               '[]'::jsonb
           )
      into v_rankings
      from (
          select row_number() over (order by vote_count desc, created_at asc, mix_id asc) as rank,
                 mix_id, title, artist, session_slot, vote_count
            from live_mixes
           where session_slot = v_slot
             and voting_open
           order by vote_count desc, created_at asc, mix_id asc
           limit greatest(coalesce(p_rankings_limit, 20), 1)
      ) r;

    return jsonb_build_object(
        'mix_id',     p_mix_id,
        'vote_count', v_count,
        'rankings',   v_rankings
    );
end;
$$;

revoke all on function public.cast_mix_vote(text, text, timestamptz, integer) from public;
revoke all on function public.cast_mix_vote(text, text, timestamptz, integer) from anon, authenticated;
grant execute on function public.cast_mix_vote(text, text, timestamptz, integer) to service_role;
