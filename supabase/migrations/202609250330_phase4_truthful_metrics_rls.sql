-- HHT Commerce Agent Phase 4 remediation
-- Apply with Supabase's migration runner, not from Flask startup.
-- The Heroku application uses its trusted database role; anonymous Supabase API
-- access is denied because the current schema has no tenant/user ownership key.

BEGIN;

ALTER TABLE public.listings
    ADD COLUMN IF NOT EXISTS lifecycle_status text NOT NULL DEFAULT 'active',
    ADD COLUMN IF NOT EXISTS listing_start_time text,
    ADD COLUMN IF NOT EXISTS quantity_sold integer NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS watch_count integer,
    ADD COLUMN IF NOT EXISTS ownership_classification text NOT NULL DEFAULT 'unknown';

ALTER TABLE public.listing_performance_daily
    ADD COLUMN IF NOT EXISTS snapshot_kind text NOT NULL DEFAULT 'rolling_listing_snapshot',
    ADD COLUMN IF NOT EXISTS metric_provenance text NOT NULL DEFAULT 'official_ebay_metric';

ALTER TABLE public.listing_performance_daily
    ALTER COLUMN impressions DROP DEFAULT,
    ALTER COLUMN search_impressions DROP DEFAULT,
    ALTER COLUMN views DROP DEFAULT,
    ALTER COLUMN ctr DROP DEFAULT,
    ALTER COLUMN conversion_rate DROP DEFAULT,
    ALTER COLUMN transactions DROP DEFAULT,
    ALTER COLUMN watch_count DROP DEFAULT;

UPDATE public.listing_performance_daily
SET snapshot_kind = 'rolling_listing_snapshot'
WHERE snapshot_kind IS NULL OR snapshot_kind = '';

UPDATE public.listing_performance_daily
SET metric_provenance = 'official_ebay_metric'
WHERE metric_provenance IS NULL OR metric_provenance = '';

ALTER TABLE public.listings ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.recommendations ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.actions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.settings ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.commerce_jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.enrichment_checkpoints ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.listing_performance_daily ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.listing_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.fulfillment_orders ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.rotation_actions ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE public.listings, public.recommendations, public.actions,
    public.settings, public.commerce_jobs, public.enrichment_checkpoints,
    public.listing_performance_daily, public.listing_versions,
    public.fulfillment_orders, public.rotation_actions FROM anon;

-- The application connects with the trusted database role. Supabase's service
-- role bypasses RLS; no broad authenticated policy is created until the schema
-- has a tenant/user ownership key and can enforce per-seller isolation.
GRANT ALL ON TABLE public.listings, public.recommendations, public.actions,
    public.settings, public.commerce_jobs, public.enrichment_checkpoints,
    public.listing_performance_daily, public.listing_versions,
    public.fulfillment_orders, public.rotation_actions TO service_role;

COMMIT;
