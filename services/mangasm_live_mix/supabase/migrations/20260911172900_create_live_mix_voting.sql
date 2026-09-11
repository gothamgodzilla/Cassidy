-- Licensed to the Apache Software Foundation (ASF) under one
-- or more contributor license agreements. See the NOTICE file
-- distributed with this work for additional information
-- regarding copyright ownership. The ASF licenses this file
-- to you under the Apache License, Version 2.0 (the
-- "License"); you may not use this file except in compliance
-- with the License. You may obtain a copy of the License at
--
--     http://www.apache.org/licenses/LICENSE-2.0
--
-- Unless required by applicable law or agreed to in writing, software
-- distributed under the License is distributed on an "AS IS" BASIS,
-- WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
-- See the License for the specific language governing permissions and
-- limitations under the License.

create table if not exists public.mangasm_plus_subscriptions (
    user_id text primary key,
    status text not null check (status in ('active', 'canceled', 'expired')),
    expires_at timestamptz,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create table if not exists public.live_mixes (
    id text primary key,
    title text not null,
    artist text not null,
    scheduled_day text not null check (scheduled_day in ('friday', 'saturday')),
    vote_count bigint not null default 0 check (vote_count >= 0),
    is_votable boolean not null default true,
    created_at timestamptz not null default now()
);

create table if not exists public.mix_votes (
    user_id text not null references public.mangasm_plus_subscriptions (user_id),
    mix_id text not null references public.live_mixes (id),
    operation_id uuid not null unique,
    voted_at timestamptz not null default now(),
    primary key (user_id, mix_id)
);

create index if not exists live_mixes_queue_rank_idx
    on public.live_mixes (is_votable, vote_count desc, id);

alter table public.mangasm_plus_subscriptions enable row level security;
alter table public.live_mixes enable row level security;
alter table public.mix_votes enable row level security;

revoke all on table public.mangasm_plus_subscriptions from public, anon, authenticated;
revoke all on table public.live_mixes from public, anon, authenticated;
revoke all on table public.mix_votes from public, anon, authenticated;

grant select on table public.mangasm_plus_subscriptions to service_role;
grant select, update on table public.live_mixes to service_role;
grant select, insert on table public.mix_votes to service_role;

drop function if exists public.cast_mix_vote(text, text);

create or replace function public.cast_mix_vote(
    p_user_id text,
    p_mix_id text,
    p_operation_id uuid
)
returns jsonb
language plpgsql
security invoker
set search_path = ''
as $$
declare
    inserted_vote boolean;
    replayed_operation boolean;
    updated_vote_count bigint;
    rankings jsonb;
begin
    if p_user_id is null or btrim(p_user_id) = ''
       or p_mix_id is null or btrim(p_mix_id) = ''
       or p_operation_id is null then
        raise sqlstate 'PGRST' using
            message = jsonb_build_object('message', 'Invalid input')::text,
            detail = jsonb_build_object('status', 400)::text;
    end if;

    perform 1
    from public.mangasm_plus_subscriptions
    where user_id = p_user_id
      and status = 'active'
      and (expires_at is null or expires_at > now())
    for share;

    if not found then
        raise sqlstate 'PGRST' using
            message = jsonb_build_object('message', 'M+ Membership Required')::text,
            detail = jsonb_build_object('status', 403)::text;
    end if;

    perform 1
    from public.live_mixes
    where id = p_mix_id
      and is_votable
    for share;

    if not found then
        raise sqlstate 'PGRST' using
            message = jsonb_build_object('message', 'M+ Membership Required')::text,
            detail = jsonb_build_object('status', 403)::text;
    end if;

    with inserted as (
        insert into public.mix_votes (user_id, mix_id, operation_id)
        values (p_user_id, p_mix_id, p_operation_id)
        on conflict (user_id, mix_id) do nothing
        returning true
    )
    select coalesce(bool_or(true), false)
    into inserted_vote
    from inserted;

    if not inserted_vote then
        select operation_id = p_operation_id
        into replayed_operation
        from public.mix_votes
        where user_id = p_user_id
          and mix_id = p_mix_id;

        if not coalesce(replayed_operation, false) then
            raise sqlstate 'PGRST' using
                message = jsonb_build_object('message', 'User already voted for this mix')::text,
                detail = jsonb_build_object('status', 409)::text;
        end if;
    end if;

    if inserted_vote then
        update public.live_mixes
        set vote_count = vote_count + 1
        where id = p_mix_id
        returning vote_count into updated_vote_count;
    else
        select vote_count
        into updated_vote_count
        from public.live_mixes
        where id = p_mix_id;
    end if;

    with ranked as (
        select
            id as mix_id,
            vote_count,
            row_number() over (order by vote_count desc, id) as rank
        from public.live_mixes
        where is_votable
    )
    select coalesce(
        jsonb_agg(
            jsonb_build_object(
                'mix_id', mix_id,
                'vote_count', vote_count,
                'rank', rank
            )
            order by rank
        ),
        '[]'::jsonb
    )
    into rankings
    from ranked;

    return jsonb_build_object(
        'mix_id', p_mix_id,
        'vote_count', updated_vote_count,
        'queue_rankings', rankings
    );
end;
$$;

revoke execute on function public.cast_mix_vote(text, text, uuid)
    from public, anon, authenticated;
grant execute on function public.cast_mix_vote(text, text, uuid) to service_role;
