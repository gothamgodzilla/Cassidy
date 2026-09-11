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

-- Sample data for local development / staging. Not for production.

insert into public.live_mixes (mix_id, title, artist, session_slot, source_url, description)
values (
    'golden-hour-progressive-house',
    'Golden Hour Progressive House | 4K DJ Set',
    'MATRYXX & BERKO',
    'friday',
    'https://soundcloud.com/sapir-berko',
    'Golden hour session moving through progressive house, melodic techno and deep trance. '
    'Episode premiere of the mangasm.app online visual experience - all welcome.'
)
on conflict (mix_id) do nothing;

insert into public.mangasm_subscriptions (user_id, tier, status, current_period_end)
values
    ('plus-member-demo', 'plus', 'active',   now() + interval '30 days'),
    ('free-member-demo', 'free', 'active',   null),
    ('lapsed-plus-demo', 'plus', 'canceled', now() - interval '1 day')
on conflict (user_id) do nothing;
