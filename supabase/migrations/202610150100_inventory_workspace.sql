BEGIN;

CREATE TABLE IF NOT EXISTS public.inventory_batches (
    id uuid PRIMARY KEY,
    seller_id uuid NOT NULL REFERENCES public.sellers(id),
    name text NOT NULL,
    status text NOT NULL DEFAULT 'open',
    photo_grouping_mode text NOT NULL DEFAULT 'auto',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS inventory_batches_seller_idx
    ON public.inventory_batches(seller_id, created_at DESC);

CREATE TABLE IF NOT EXISTS public.inventory_items (
    id uuid PRIMARY KEY,
    batch_id uuid NOT NULL REFERENCES public.inventory_batches(id) ON DELETE CASCADE,
    seller_id uuid NOT NULL REFERENCES public.sellers(id),
    listing_json jsonb NOT NULL DEFAULT '{}'::jsonb,
    provenance_json jsonb NOT NULL DEFAULT '{}'::jsonb,
    workflow_status text NOT NULL DEFAULT 'grouped'
        CHECK (workflow_status IN (
            'photo_staged',
            'grouped',
            'analysis_queued',
            'analysis_running',
            'needs_review',
            'ready_for_approval',
            'approved',
            'draft_submitted',
            'draft_result_available',
            'draft_failed'
        )),
    validation_json jsonb NOT NULL DEFAULT '{}'::jsonb,
    pricing_options_json jsonb NOT NULL DEFAULT '{}'::jsonb,
    title_candidates_json jsonb NOT NULL DEFAULT '{}'::jsonb,
    analysis_job_id text NOT NULL DEFAULT '',
    seller_confirmed boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS inventory_items_batch_idx
    ON public.inventory_items(batch_id, created_at ASC);

CREATE INDEX IF NOT EXISTS inventory_items_seller_status_idx
    ON public.inventory_items(seller_id, workflow_status, created_at DESC);

CREATE TABLE IF NOT EXISTS public.inventory_item_photos (
    id uuid PRIMARY KEY,
    batch_id uuid NOT NULL REFERENCES public.inventory_batches(id) ON DELETE CASCADE,
    item_id uuid REFERENCES public.inventory_items(id) ON DELETE SET NULL,
    photo_asset_id uuid REFERENCES public.photo_assets(id) ON DELETE SET NULL,
    original_filename text NOT NULL DEFAULT '',
    preview_url text NOT NULL DEFAULT '',
    storage_url text NOT NULL DEFAULT '',
    position integer NOT NULL DEFAULT 0,
    is_cover boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS inventory_item_photos_batch_idx
    ON public.inventory_item_photos(batch_id, position ASC);

CREATE INDEX IF NOT EXISTS inventory_item_photos_item_idx
    ON public.inventory_item_photos(item_id, position ASC);

CREATE TABLE IF NOT EXISTS public.inventory_submissions (
    id uuid PRIMARY KEY,
    item_id uuid NOT NULL REFERENCES public.inventory_items(id) ON DELETE CASCADE,
    seller_id uuid NOT NULL REFERENCES public.sellers(id),
    batch_id uuid REFERENCES public.inventory_batches(id) ON DELETE SET NULL,
    feed_task_id text NOT NULL,
    status text NOT NULL DEFAULT 'submitted',
    result_json jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS inventory_submissions_item_idx
    ON public.inventory_submissions(item_id, created_at DESC);

CREATE INDEX IF NOT EXISTS inventory_submissions_task_idx
    ON public.inventory_submissions(feed_task_id);

ALTER TABLE public.inventory_batches ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.inventory_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.inventory_item_photos ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.inventory_submissions ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE public.inventory_batches, public.inventory_items, public.inventory_item_photos, public.inventory_submissions FROM anon, authenticated;
GRANT ALL ON TABLE public.inventory_batches, public.inventory_items, public.inventory_item_photos, public.inventory_submissions TO service_role;

COMMIT;
