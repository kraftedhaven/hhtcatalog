BEGIN;

ALTER TABLE public.inventory_items
    ADD COLUMN IF NOT EXISTS approval_content_hash text NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS approved_at timestamptz,
    ADD COLUMN IF NOT EXISTS approved_by text NOT NULL DEFAULT '';

ALTER TABLE public.inventory_submissions
    ADD COLUMN IF NOT EXISTS idempotency_key text NOT NULL DEFAULT '';

CREATE UNIQUE INDEX IF NOT EXISTS inventory_submissions_active_item_idx
    ON public.inventory_submissions(item_id)
    WHERE status IN ('claiming', 'submitted', 'processing', 'ambiguous');

COMMIT;
