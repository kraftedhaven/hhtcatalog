BEGIN;

CREATE TABLE IF NOT EXISTS public.photo_assets (
    id uuid PRIMARY KEY,
    seller_id uuid NOT NULL REFERENCES public.sellers(id),
    listing_key text NOT NULL DEFAULT 'unassigned',
    original_key text NOT NULL,
    derivative_key text NOT NULL,
    provider text NOT NULL CHECK (provider IN ('supabase', 'ibm_cos')),
    original_mime text NOT NULL,
    derivative_mime text NOT NULL,
    original_bytes integer NOT NULL CHECK (original_bytes > 0),
    derivative_bytes integer NOT NULL CHECK (derivative_bytes > 0),
    checksum_sha256 text NOT NULL,
    ebay_url text NOT NULL,
    url_expires_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS photo_assets_seller_idx
    ON public.photo_assets(seller_id, created_at DESC);

CREATE INDEX IF NOT EXISTS photo_assets_listing_idx
    ON public.photo_assets(seller_id, listing_key, created_at DESC);

ALTER TABLE public.photo_assets ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.photo_assets FROM anon, authenticated;
GRANT ALL ON TABLE public.photo_assets TO service_role;

COMMIT;
