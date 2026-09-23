-- HHT Commerce Agent: deny public/anon table access by default.
-- The Heroku app uses its trusted PostgreSQL connection; table owners and
-- Supabase service_role continue to bypass RLS as intended.

alter table if exists public.listings enable row level security;
alter table if exists public.recommendations enable row level security;
alter table if exists public.actions enable row level security;
alter table if exists public.settings enable row level security;
alter table if exists public.commerce_jobs enable row level security;
alter table if exists public.enrichment_checkpoints enable row level security;

-- No anon/authenticated policies are created deliberately. These tables are
-- application-internal and must not be readable or writable from the public
-- Supabase API. Add scoped policies only if a future authenticated UI needs
-- direct Supabase access.

-- Verification query (run in Supabase SQL Editor):
-- select schemaname, tablename, rowsecurity
-- from pg_tables
-- where schemaname = 'public'
--   and tablename in ('listings','recommendations','actions','settings','commerce_jobs','enrichment_checkpoints');
