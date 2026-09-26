BEGIN;

CREATE TABLE IF NOT EXISTS public.sellers (
    id uuid PRIMARY KEY,
    auth_user_id uuid NOT NULL UNIQUE,
    status text NOT NULL DEFAULT 'active',
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.ebay_accounts (
    id uuid PRIMARY KEY,
    seller_id uuid NOT NULL REFERENCES public.sellers(id),
    ebay_account_id text NOT NULL UNIQUE,
    connected_at timestamptz NOT NULL DEFAULT now(),
    disconnected_at timestamptz
);

ALTER TABLE public.listings
    ADD COLUMN IF NOT EXISTS seller_id uuid REFERENCES public.sellers(id),
    ADD COLUMN IF NOT EXISTS ebay_account_id uuid REFERENCES public.ebay_accounts(id);

ALTER TABLE public.recommendations
    ADD COLUMN IF NOT EXISTS seller_id uuid REFERENCES public.sellers(id);

ALTER TABLE public.actions
    ADD COLUMN IF NOT EXISTS seller_id uuid REFERENCES public.sellers(id),
    ADD COLUMN IF NOT EXISTS approved_by uuid REFERENCES public.sellers(auth_user_id),
    ADD COLUMN IF NOT EXISTS applied_by uuid REFERENCES public.sellers(auth_user_id),
    ADD COLUMN IF NOT EXISTS rolled_back_by uuid REFERENCES public.sellers(auth_user_id);

ALTER TABLE public.rotation_actions
    ADD COLUMN IF NOT EXISTS seller_id uuid REFERENCES public.sellers(id),
    ADD COLUMN IF NOT EXISTS approved_by uuid REFERENCES public.sellers(auth_user_id);

CREATE INDEX IF NOT EXISTS listings_seller_id_idx ON public.listings(seller_id, imported_at DESC);
CREATE INDEX IF NOT EXISTS recommendations_seller_id_idx ON public.recommendations(seller_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS actions_seller_id_idx ON public.actions(seller_id, created_at DESC);
CREATE INDEX IF NOT EXISTS rotation_actions_seller_id_idx ON public.rotation_actions(seller_id, created_at DESC);

ALTER TABLE public.sellers ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.ebay_accounts ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.sellers, public.ebay_accounts FROM anon, authenticated;
GRANT ALL ON TABLE public.sellers, public.ebay_accounts TO service_role;

COMMIT;
