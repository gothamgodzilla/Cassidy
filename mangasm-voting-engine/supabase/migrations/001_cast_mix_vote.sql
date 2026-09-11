-- Licensed to the Apache Software Foundation (ASF) under one
-- or more contributor license agreements.  See the NOTICE file
-- distributed with this work for additional information
-- regarding copyright ownership.  The ASF licenses this file
-- to you under the Apache License, Version 2.0 (the
-- "License"); you may not use this file except in compliance
-- with the License.  You may obtain a copy of the License at
--
--   http://www.apache.org/licenses/LICENSE-2.0
--
-- Unless required by applicable law or agreed to in writing,
-- software distributed under the License is distributed on an
-- "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
-- KIND, either express or implied.  See the License for the
-- specific language governing permissions and limitations
-- under the License.

-- Mangasm+ Live Mix Voting schema + atomic vote RPC.
--
-- Apply with: supabase db push  (or psql < this file)
--
-- Tables:
--   m_plus_members (user_id PK, status, current_period_end)
--   mixes          (mix_id PK, title, votes, scheduled_for)
--   mix_votes      (vote_id PK, user_id, mix_id, voted_at, UNIQUE(user_id, mix_id))
--
-- The UNIQUE constraint is the duplicate-vote guard; cast_mix_vote() wraps
-- the insert + counter increment in a single transaction so concurrent votes
-- from different users never lose increments.

create table if not exists public.m_plus_members (
  user_id text primary key,
  status text not null default 'active'
    check (status in ('active', 'past_due', 'cancelled', 'expired')),
  current_period_end timestamptz,
  created_at timestamptz not null default now()
);

create table if not exists public.mixes (
  mix_id text primary key,
  title text not null default '',
  votes integer not null default 0 check (votes >= 0),
  scheduled_for text not null default ''
    check (scheduled_for in ('', 'friday', 'saturday', 'friday_saturday')),
  created_at timestamptz not null default now()
);

create table if not exists public.mix_votes (
  vote_id bigint generated always as identity primary key,
  user_id text not null references public.m_plus_members (user_id),
  mix_id text not null references public.mixes (mix_id) on delete cascade,
  voted_at timestamptz not null default now(),
  unique (user_id, mix_id)
);
create index if not exists mix_votes_mix_id_idx on public.mix_votes (mix_id);

-- Seed the Golden Hour premiere queue (idempotent).
insert into public.mixes (mix_id, title, votes, scheduled_for) values
  ('golden-hour-berko-4k', 'Berko - Golden Hour Progressive House | 4K DJ Set', 0, 'friday_saturday'),
  ('golden-hour-matryxx-b2b-berko', 'MATRYXX b2b BERKO - mangasm.mascot Episode Premiere (Live Visual Experience)', 0, 'friday_saturday')
on conflict (mix_id) do nothing;

-- Atomic vote: insert vote row, bump counter, return fresh count.
create or replace function public.cast_mix_vote(p_user_id text, p_mix_id text)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_count integer;
  v_member text;
begin
  if p_user_id is null or btrim(p_user_id) = '' then
    raise exception 'invalid user_id' using errcode = '22004';
  end if;
  if p_mix_id is null or btrim(p_mix_id) = '' then
    raise exception 'invalid mix_id' using errcode = '22004';
  end if;

  select user_id into v_member
    from public.m_plus_members
   where user_id = p_user_id
     and status = 'active';
  if not found then
    raise exception 'M+ Membership Required' using errcode = '42501';
  end if;

  perform 1 from public.mixes where mix_id = p_mix_id;
  if not found then
    raise exception 'mix_not_found: %', p_mix_id using errcode = 'P0002';
  end if;

  -- UNIQUE(user_id, mix_id) turns a repeat vote into SQLSTATE 23505,
  -- which PostgREST surfaces as HTTP 409.
  insert into public.mix_votes (user_id, mix_id) values (p_user_id, p_mix_id);

  update public.mixes
     set votes = votes + 1
   where mix_id = p_mix_id
  returning votes into v_count;

  return jsonb_build_object('mix_id', p_mix_id, 'vote_count', v_count, 'voted_at', now());
exception
  when unique_violation then
    raise exception 'duplicate vote for user % on mix %', p_user_id, p_mix_id using errcode = '23505';
end;
$$;

revoke all on function public.cast_mix_vote(text, text) from public;
grant execute on function public.cast_mix_vote(text, text) to anon, authenticated, service_role;
