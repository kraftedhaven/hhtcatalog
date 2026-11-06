BEGIN;

-- Additive Phase 2 migration: Photo signatures, Perceptual deduplication, and Reviewable grouping proposals
-- Compatible with 202610150100_inventory_workspace.sql.
-- Preserves existing sellers, photo_assets, and inventory_* tables.

CREATE TABLE IF NOT EXISTS public.inventory_photo_signatures (
    id uuid PRIMARY KEY,
    photo_id uuid NOT NULL UNIQUE REFERENCES public.inventory_item_photos(id) ON DELETE CASCADE,
    seller_id uuid NOT NULL REFERENCES public.sellers(id),
    exact_checksum text NOT NULL DEFAULT '',
    perceptual_hash text NOT NULL DEFAULT '',
    width integer,
    height integer,
    access_status text NOT NULL DEFAULT 'ok',
    access_error text NOT NULL DEFAULT '',
    checked_at timestamptz NOT NULL,
    CHECK (access_status IN ('ok', 'inaccessible', 'invalid'))
);

CREATE TABLE IF NOT EXISTS public.inventory_group_proposals (
    id uuid PRIMARY KEY,
    batch_id uuid NOT NULL REFERENCES public.inventory_batches(id) ON DELETE CASCADE,
    seller_id uuid NOT NULL REFERENCES public.sellers(id),
    status text NOT NULL DEFAULT 'proposed',
    method text NOT NULL DEFAULT 'visual_similarity',
    confidence numeric NOT NULL DEFAULT 0,
    proposal_json jsonb NOT NULL DEFAULT '{}'::jsonb,
    duplicate_candidates_json jsonb NOT NULL DEFAULT '[]'::jsonb,
    created_at timestamptz NOT NULL,
    reviewed_at timestamptz,
    reviewed_by text NOT NULL DEFAULT '',
    CHECK (status IN ('proposed', 'reviewed', 'superseded')),
    CHECK (confidence >= 0 AND confidence <= 1)
);

-- Support tracking seller-confirmed overrides on inventory items
ALTER TABLE public.inventory_items
    ADD COLUMN IF NOT EXISTS seller_confirmed_json jsonb NOT NULL DEFAULT '{}'::jsonb;

CREATE INDEX IF NOT EXISTS inventory_group_proposals_batch_idx
    ON public.inventory_group_proposals (seller_id, batch_id, created_at DESC);

CREATE INDEX IF NOT EXISTS inventory_photo_signatures_seller_idx
    ON public.inventory_photo_signatures (seller_id, checked_at DESC);

ALTER TABLE public.inventory_photo_signatures ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.inventory_group_proposals ENABLE ROW LEVEL SECURITY;

DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        REVOKE ALL ON TABLE public.inventory_photo_signatures, public.inventory_group_proposals FROM anon, authenticated;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN
        GRANT ALL ON TABLE public.inventory_photo_signatures, public.inventory_group_proposals TO service_role;
    END IF;
END $$;

COMMIT;
