BEGIN;

CREATE TABLE IF NOT EXISTS public.analysis_runs (
    id uuid PRIMARY KEY,
    seller_id uuid REFERENCES public.sellers(id),
    listing_row_id bigint REFERENCES public.listings(id),
    provider text NOT NULL,
    model text NOT NULL DEFAULT '',
    job_id text NOT NULL DEFAULT '',
    input_photo_count integer NOT NULL DEFAULT 0 CHECK (input_photo_count >= 0),
    token_usage_json jsonb NOT NULL DEFAULT '{}'::jsonb,
    prompt_version text NOT NULL DEFAULT '',
    schema_version text NOT NULL DEFAULT '',
    started_at timestamptz NOT NULL,
    completed_at timestamptz,
    status text NOT NULL,
    review_status text NOT NULL DEFAULT 'pending',
    error text NOT NULL DEFAULT '',
    result_json jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS analysis_runs_listing_idx
    ON public.analysis_runs(listing_row_id, created_at DESC);
CREATE INDEX IF NOT EXISTS analysis_runs_job_idx
    ON public.analysis_runs(job_id, created_at DESC)
    WHERE job_id <> '';

ALTER TABLE public.analysis_runs ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.analysis_runs FROM anon, authenticated;
GRANT ALL ON TABLE public.analysis_runs TO service_role;

COMMIT;
