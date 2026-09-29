BEGIN;

CREATE TABLE IF NOT EXISTS public.ebay_scope_proofs (
    scope text PRIMARY KEY,
    status text NOT NULL CHECK (status IN ('verified', 'failed', 'unknown')),
    probe text NOT NULL,
    http_status integer,
    checked_at timestamptz,
    token_fingerprint text NOT NULL,
    token_issued_at timestamptz,
    error_category text NOT NULL DEFAULT ''
);

ALTER TABLE public.ebay_scope_proofs ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.ebay_scope_proofs FROM anon, authenticated;
GRANT ALL ON TABLE public.ebay_scope_proofs TO service_role;

COMMIT;
