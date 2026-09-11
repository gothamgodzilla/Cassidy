-- Mangasm+ Live Mix Voting Engine
-- Apply in the Supabase SQL editor with a service-role capable role.
-- The FastAPI service calls public.cast_mix_vote(p_user_id, p_mix_id).

create table if not exists public.mangasm_plus_subscriptions (
    user_id text primary key,
    status text not null check (status in ('active', 'canceled', 'past_due', 'expired')),
    plan text not null default 'mangasm_plus',
    current_period_end timestamptz,
    created_at timestamptz not null default now()
);

create table if not exists public.live_mixes (
    id text primary key,
    title text not null,
    djs jsonb not null default '[]'::jsonb,
    live_sessions text[] not null,
    vote_count integer not null default 0 check (vote_count >= 0),
    metadata jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now()
);

create table if not exists public.mix_vote_records (
    user_id text not null,
    mix_id text not null references public.live_mixes (id),
    voted_at timestamptz not null default now(),
    primary key (user_id, mix_id)
);

create index if not exists live_mixes_vote_count_idx
    on public.live_mixes (vote_count desc, title);

alter table public.mangasm_plus_subscriptions enable row level security;
alter table public.live_mixes enable row level security;
alter table public.mix_vote_records enable row level security;

insert into public.live_mixes (id, title, djs, live_sessions, metadata)
values (
    'golden-hour-progressive-house-berko',
    'Golden Hour Progressive House & Melodic Techno DJ Set',
    '["MATRYXX", "BERKO"]'::jsonb,
    array['friday', 'saturday'],
    jsonb_build_object(
        'subtitle', 'Berko - Golden Hour Progressive House | 4K DJ Set',
        'artist', 'Berko',
        'platform', 'SoundCloud',
        'mascot', jsonb_build_object(
            'id', 'mangasm.mascot',
            'episode', 'premier',
            'headline', 'episode premier of online visual experience',
            'welcome', 'all welcome mangasm.app',
            'url', 'https://mangasm.app'
        ),
        'links', jsonb_build_object(
            'spotify', 'https://open.spotify.com/artist/5Lrm3iLbY5LEsjXecGd83x?si=c81J_cGATNOv73L1-I6t8w',
            'soundcloud', 'https://soundcloud.com/sapir-berko',
            'instagram', 'https://www.instagram.com/berko_ofc'
        )
    )
)
on conflict (id) do update
set title = excluded.title,
    djs = excluded.djs,
    live_sessions = excluded.live_sessions,
    metadata = excluded.metadata;

create or replace function public.mix_queue_rankings()
returns jsonb
language sql
stable
security definer
set search_path = public
as $$
    select coalesce(
        jsonb_agg(to_jsonb(ranked) order by ranked.rank),
        '[]'::jsonb
    )
    from (
        select
            row_number() over (order by vote_count desc, title asc) as rank,
            id as mix_id,
            title,
            vote_count,
            djs,
            live_sessions
        from public.live_mixes
        where live_sessions && array['friday', 'saturday']
    ) ranked;
$$;

create or replace function public.cast_mix_vote(p_user_id text, p_mix_id text)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
    v_inserted integer := 0;
    v_vote_count integer;
    v_period_end timestamptz;
    v_status text;
begin
    if p_user_id is null or btrim(p_user_id) = '' or p_mix_id is null or btrim(p_mix_id) = '' then
        return jsonb_build_object('status', 'mix_not_found');
    end if;

    select status, current_period_end
      into v_status, v_period_end
      from public.mangasm_plus_subscriptions
     where user_id = p_user_id;

    if v_status is distinct from 'active'
       or (v_period_end is not null and v_period_end <= now()) then
        return jsonb_build_object('status', 'membership_required');
    end if;

    select vote_count
      into v_vote_count
      from public.live_mixes
     where id = p_mix_id
       and live_sessions && array['friday', 'saturday']
     for update;

    if not found then
        return jsonb_build_object('status', 'mix_not_found');
    end if;

    insert into public.mix_vote_records (user_id, mix_id, voted_at)
    values (p_user_id, p_mix_id, now())
    on conflict (user_id, mix_id) do nothing;

    get diagnostics v_inserted = row_count;
    if v_inserted = 0 then
        select vote_count into v_vote_count
          from public.live_mixes
         where id = p_mix_id;
        return jsonb_build_object(
            'status', 'already_voted',
            'vote_count', v_vote_count,
            'rankings', public.mix_queue_rankings()
        );
    end if;

    update public.live_mixes
       set vote_count = vote_count + 1
     where id = p_mix_id
     returning vote_count into v_vote_count;

    return jsonb_build_object(
        'status', 'ok',
        'vote_count', v_vote_count,
        'rankings', public.mix_queue_rankings()
    );
end;
$$;

revoke all on function public.cast_mix_vote(text, text) from public;
revoke all on function public.mix_queue_rankings() from public;
grant execute on function public.cast_mix_vote(text, text) to service_role;
grant execute on function public.mix_queue_rankings() to service_role;
