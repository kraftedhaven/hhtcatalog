BEGIN;

ALTER TABLE public.commerce_jobs
ADD COLUMN IF NOT EXISTS seller_id text;

CREATE INDEX IF NOT EXISTS commerce_jobs_seller_idx ON public.commerce_jobs (seller_id, created_at DESC);

CREATE TABLE
    IF NOT EXISTS public.vision_result_cache (
        cache_key text PRIMARY KEY,
        result_json text NOT NULL,
        expires_at timestamptz NOT NULL,
        created_at timestamptz NOT NULL
    );

CREATE INDEX IF NOT EXISTS vision_result_cache_expiry_idx ON public.vision_result_cache (expires_at);

REVOKE ALL ON TABLE public.vision_result_cache
FROM
    anon,
    authenticated;

GRANT ALL ON TABLE public.vision_result_cache TO service_role;

COMMIT;