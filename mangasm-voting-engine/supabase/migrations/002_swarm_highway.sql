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

-- Phase A — swarm tunnel: cyclical agent communication over Supabase.
--
-- The tunnel carries lightweight JSON state vectors (fast sports cars),
-- never raw conversation histories (heavy trucks). pgvector powers
-- semantic recall so the next agent in the geometric ring can catch
-- contextually relevant vectors, not just the freshest one.

create extension if not exists vector;

create table if not exists public.swarm_agents (
  agent_id text primary key,
  role text not null default '',
  model text not null default '',
  loop_id text not null default 'golden-hour-ring',
  created_at timestamptz not null default now()
);

create table if not exists public.swarm_state_vectors (
  vector_id bigint generated always as identity primary key,
  loop_id text not null default 'golden-hour-ring',
  agent_id text not null references public.swarm_agents (agent_id),
  turn integer not null default 0,
  summary text not null,
  key_points jsonb not null default '[]'::jsonb,
  next_action text not null default '',
  cache_seal text not null default '',
  sig text not null default '',
  embedding vector(1536),
  claimed_by text not null default '',
  created_at timestamptz not null default now()
);
create index if not exists swarm_vectors_loop_idx on public.swarm_state_vectors (loop_id, created_at desc);
create index if not exists swarm_vectors_agent_idx on public.swarm_state_vectors (agent_id);

insert into public.swarm_agents (agent_id, role, model, loop_id) values
  ('bpm-metronome', 'Keeps global tempo; emits beat phase + BPM state.', 'local:qwen-metronome', 'golden-hour-ring'),
  ('sha-cache', 'Seals payloads with 25SHA cache IDs; serves cache hits.', 'local:llama3-cache', 'golden-hour-ring'),
  ('mix-voter', 'Turns crowd votes into queue moves for the live mix.', 'local:qwen-vote', 'golden-hour-ring'),
  ('visual-mascot', 'Renders the mangasm.mascot visual episode state.', 'local:llama3-visual', 'golden-hour-ring')
on conflict (agent_id) do nothing;

-- Semantic recall over the tunnel: nearest vectors in the same loop.
create or replace function public.match_state_vectors(
  p_loop_id text,
  p_embedding vector(1536),
  p_match_count integer default 5
)
returns setof public.swarm_state_vectors
language sql
stable
set search_path = public
as $$
  select *
    from public.swarm_state_vectors
   where loop_id = p_loop_id
     and embedding is not null
   order by embedding <=> p_embedding
   limit greatest(1, least(p_match_count, 25));
$$;

revoke all on function public.match_state_vectors(text, vector, integer) from public;
grant execute on function public.match_state_vectors(text, vector, integer)
  to anon, authenticated, service_role;
