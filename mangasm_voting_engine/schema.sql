-- =============================================================================
-- Mangasm+ Live Mix Voting Engine - Supabase Schema & RPC Definition
-- =============================================================================

-- 1. Create table for user subscriptions to track Mangasm+ membership
CREATE TABLE IF NOT EXISTS public.user_subscriptions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id TEXT NOT NULL UNIQUE,
    plan_tier TEXT NOT NULL DEFAULT 'mangasm+',
    status TEXT NOT NULL DEFAULT 'active',
    is_active BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now()),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now())
);

-- Index for quick lookup by user_id
CREATE INDEX IF NOT EXISTS idx_user_subscriptions_user_id ON public.user_subscriptions(user_id);

-- 2. Create table for Friday/Saturday live mixes
CREATE TABLE IF NOT EXISTS public.mixes (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    dj_name TEXT NOT NULL,
    genre TEXT DEFAULT 'Progressive House & Melodic Techno',
    soundcloud_url TEXT,
    spotify_url TEXT,
    instagram_url TEXT,
    vote_count BIGINT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now()),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now())
);

-- Index for queue ranking by vote_count descending
CREATE INDEX IF NOT EXISTS idx_mixes_vote_count ON public.mixes(vote_count DESC);

-- 3. Create table for storing user vote records to prevent duplicate votes
CREATE TABLE IF NOT EXISTS public.mix_votes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id TEXT NOT NULL,
    mix_id TEXT NOT NULL REFERENCES public.mixes(id) ON DELETE CASCADE,
    vote_timestamp TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now()),
    CONSTRAINT uq_user_mix_vote UNIQUE (user_id, mix_id)
);

-- Indexes for mix_votes
CREATE INDEX IF NOT EXISTS idx_mix_votes_user_mix ON public.mix_votes(user_id, mix_id);
CREATE INDEX IF NOT EXISTS idx_mix_votes_timestamp ON public.mix_votes(vote_timestamp);

-- 4. Initial Seed for Golden Hour DJ Set
INSERT INTO public.mixes (id, title, dj_name, genre, soundcloud_url, spotify_url, instagram_url, vote_count)
VALUES (
    'mix_golden_hour_berko_01',
    'Berko - Golden Hour Progressive House | 4K DJ Set',
    'MATRYXX & BERKO',
    'Progressive House & Melodic Techno',
    'https://soundcloud.com/sapir-berko',
    'https://open.spotify.com/artist/5Lrm3iLbY5LEsjXecGd83x?si=c81J_cGATNOv73L1-I6t8w',
    'https://www.instagram.com/berko_ofc',
    0
)
ON CONFLICT (id) DO UPDATE SET
    title = EXCLUDED.title,
    dj_name = EXCLUDED.dj_name;

-- 5. Atomic RPC: cast_mix_vote
-- Atomically validates duplicate vote, records the vote, increments vote count,
-- and returns updated vote count + queue rankings.
CREATE OR REPLACE FUNCTION public.cast_mix_vote(
    p_user_id TEXT,
    p_mix_id TEXT
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
    v_timestamp TIMESTAMPTZ := timezone('utc', now());
    v_new_vote_count BIGINT;
    v_rankings JSONB;
BEGIN
    -- Check if mix exists; if not, create placeholder or raise exception
    IF NOT EXISTS (SELECT 1 FROM public.mixes WHERE id = p_mix_id) THEN
        INSERT INTO public.mixes (id, title, dj_name, vote_count)
        VALUES (p_mix_id, 'Live Mix ' || p_mix_id, 'Resident DJ', 0)
        ON CONFLICT (id) DO NOTHING;
    END IF;

    -- Check for duplicate vote
    IF EXISTS (
        SELECT 1 FROM public.mix_votes
        WHERE user_id = p_user_id AND mix_id = p_mix_id
    ) THEN
        -- Return 409 status structure or raise exception
        RAISE EXCEPTION 'User % already voted for mix %', p_user_id, p_mix_id
            USING ERRCODE = 'unique_violation';
    END IF;

    -- Store vote record (prevent duplicates)
    INSERT INTO public.mix_votes (user_id, mix_id, vote_timestamp)
    VALUES (p_user_id, p_mix_id, v_timestamp);

    -- Atomically increment mix vote count
    UPDATE public.mixes
    SET vote_count = vote_count + 1,
        updated_at = v_timestamp
    WHERE id = p_mix_id
    RETURNING vote_count INTO v_new_vote_count;

    -- Calculate current queue rankings
    WITH ranked_mixes AS (
        SELECT
            id AS mix_id,
            title,
            dj_name,
            vote_count,
            ROW_NUMBER() OVER (ORDER BY vote_count DESC, updated_at ASC) AS rank
        FROM public.mixes
    )
    SELECT jsonb_agg(
        jsonb_build_object(
            'mix_id', mix_id,
            'title', title,
            'dj_name', dj_name,
            'vote_count', vote_count,
            'rank', rank
        )
    ) INTO v_rankings
    FROM ranked_mixes;

    -- Return JSON payload
    RETURN jsonb_build_object(
        'success', true,
        'user_id', p_user_id,
        'mix_id', p_mix_id,
        'updated_vote_count', v_new_vote_count,
        'vote_timestamp', v_timestamp,
        'queue_rankings', COALESCE(v_rankings, '[]'::jsonb)
    );
END;
$$;
