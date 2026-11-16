BEGIN;

-- Integrity Repair v3 — Submission stage tracking, approval content binding,
-- and duplicate-active-submission detection.
-- Compatible with all prior migrations.
-- Apply AFTER backing up the database.
--
-- PREFLIGHT: Check for existing active submissions before applying:
--   SELECT id, item_id, status, created_at
--   FROM inventory_submissions
--   WHERE status IN ('submitted', 'processing', 'pending')
--   ORDER BY created_at DESC;
-- If any rows exist and their eBay tasks are still unknown, reconcile them first.

-- ── inventory_items: approval content hash & workflow states ──────────────────
-- Binds the approved state to the exact content version at approval time.
-- Cleared whenever the item is edited, photo-mutated, or approval is revoked.
ALTER TABLE public.inventory_items
    ADD COLUMN IF NOT EXISTS approval_content_hash text NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS approved_content_hash text,
    ADD COLUMN IF NOT EXISTS approved_at timestamptz,
    ADD COLUMN IF NOT EXISTS approved_by text NOT NULL DEFAULT '';

ALTER TABLE public.inventory_items
    DROP CONSTRAINT IF EXISTS inventory_items_workflow_status_check;

ALTER TABLE public.inventory_items
    ADD CONSTRAINT inventory_items_workflow_status_check
    CHECK (workflow_status IN (
        'photo_staged',
        'grouped',
        'analysis_queued',
        'analysis_running',
        'needs_review',
        'ready_for_approval',
        'approved',
        'claiming',
        'ambiguous_submission',
        'draft_submitted',
        'draft_result_available',
        'draft_failed'
    ));

-- ── inventory_submissions: stage tracking + idempotency + snapshot ───────────
-- submission_stage classifies exactly how far each submission progressed,
-- enabling safe retry decisions after uncertain outcomes.
ALTER TABLE public.inventory_submissions
    ADD COLUMN IF NOT EXISTS submission_stage text NOT NULL DEFAULT 'validating'
        CHECK (submission_stage IN (
            'validating', 'task_created', 'upload_sent',
            'upload_confirmed', 'completed'
        ));

-- Idempotency key: seller_id:item_id:content_hash — prevents duplicate submissions
-- of the same approved content version.
ALTER TABLE public.inventory_submissions
    ADD COLUMN IF NOT EXISTS idempotency_key text NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS listing_snapshot jsonb NOT NULL DEFAULT '{}'::jsonb;

-- Unique active-claim index per item across concurrent processes
CREATE UNIQUE INDEX IF NOT EXISTS inventory_submissions_active_item_idx
    ON public.inventory_submissions(item_id)
    WHERE status IN ('claiming', 'submitted', 'processing', 'ambiguous');

-- Index for active-submission duplicate detection
CREATE INDEX IF NOT EXISTS inventory_submissions_active_idx
    ON public.inventory_submissions (item_id, seller_id, status, submission_stage)
    WHERE status NOT IN ('failed', 'cancelled', 'completed');

-- Index for idempotency lookups
CREATE INDEX IF NOT EXISTS inventory_submissions_idempotency_idx
    ON public.inventory_submissions (seller_id, idempotency_key)
    WHERE status NOT IN ('failed', 'cancelled', 'completed');

-- ── SQLite compatibility (applied by init_db in commerce_agent.py) ───────────
-- The Python layer already handles ADD COLUMN IF NOT EXISTS for SQLite via
-- the existing migration-compat path in connect(). No SQLite-specific DDL here.

COMMIT;

-- ── ROLLBACK ─────────────────────────────────────────────────────────────────
-- To roll back this migration:
--
--   BEGIN;
--   ALTER TABLE public.inventory_items       DROP COLUMN IF EXISTS approved_content_hash;
--   ALTER TABLE public.inventory_submissions DROP COLUMN IF EXISTS submission_stage;
--   ALTER TABLE public.inventory_submissions DROP COLUMN IF EXISTS idempotency_key;
--   DROP INDEX IF EXISTS inventory_submissions_active_idx;
--   DROP INDEX IF EXISTS inventory_submissions_idempotency_idx;
--   COMMIT;
--
-- Then redeploy the previous application version (bfdb205).
-- No data loss occurs during rollback; the dropped columns held metadata only.

-- ── DEPLOYMENT ORDER ─────────────────────────────────────────────────────────
-- 1. Take a full database backup.
-- 2. Run PREFLIGHT query above and reconcile any active submissions.
-- 3. Apply this migration file against the target database.
-- 4. Deploy the new application version (fix/catalog-integrity-release).
-- 5. Restart the application.
-- 6. For any items still in 'approved' state without an approved_content_hash,
--    prompt sellers to re-approve via the UI (see APPROVAL-RENEWAL below).
--
-- ── APPROVAL-RENEWAL ─────────────────────────────────────────────────────────
-- After deployment, existing approved items will have approved_content_hash = NULL.
-- The submission guard treats NULL as "no hash — allow submission" for backward
-- compatibility, so existing approved items can still be submitted once.
-- To require re-approval of all existing items:
--
--   UPDATE inventory_items
--   SET workflow_status = 'needs_review', approved_content_hash = NULL
--   WHERE workflow_status = 'approved';
--
-- Then notify sellers to review and re-approve their items.
