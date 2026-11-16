# HHTCatalog Integrity Repair Release V3 - Migration & Operations Guide

## Overview

This release addresses the 9 critical integrity and security release blockers identified during independent review of the HHTCatalog workspace and eBay draft submission subsystem.

### Key Functional & Safety Guarantees
- **Submission Ambiguity & Duplicate Prevention**: Unambiguous stage tracking (`validating`, `task_created`, `upload_sent`, `upload_confirmed`). When an upload is accepted but task polling returns 404 or connection fails, the feed task ID is retained, state is marked `ambiguous`, and retry is blocked until reconciled.
- **Centralized Submission Authorization**: Preflight checks inside `submit_approved_drafts()` require:
  1. Authenticated seller context
  2. `EBAY_DRAFTS_ENABLED=true`
  3. `EBAY_MUTATIONS_ENABLED=true`
  4. Verified eBay OAuth scope proofs (`ebay_scope_status() == "verified"`)
  5. Immutable listing snapshot persisted on submission claim
  6. Idempotency key per submission batch
- **Approval Content Hash Binding**: `approved_content_hash` column on `inventory_items`. Any modification to listing fields, photo ordering, or donor groupings invalidates approval (binding genuine Boolean `False` in SQL) and clears the content hash.
- **Strict Destructive Booleans**: Route handlers enforce JSON boolean type (`_strict_json_bool`) for `allowDestructive`. String (`"true"`) and integer (`1`) values are strictly rejected.
- **Hosted Image Security**: Photo validation enforces HTTPS, validates destination IP before connection, verifies socket peer IP at connection time, blocks loopback/private/link-local/multicast IP addresses, rejects redirects to private destinations, streams chunks with a 10 MB limit (`PHOTO_VALIDATION_MAX_BYTES`), and validates image decoding via Pillow.
- **Feed Task Isolation**: `/api/ebay/feed/tasks/<task_id>` routes require seller authentication and forbid cross-seller access.
- **Manual CSV Export Preserved**: Manual Seller Hub CSV export remains fully functional when `EBAY_MUTATIONS_ENABLED=false`.

> [!NOTE]
> Historical `BAF.Error.5` behavior remains unproven against production eBay feed endpoints until actual live eBay processing evidence exists. In this release, task submission status and response payloads are strictly preserved without speculative mutation side-effects.

---

## Pre-Deployment Verification & Preflight Checks

Before applying database migrations, execute the following preflight SQL queries on your PostgreSQL instance:

### 1. Check for Duplicate Active Submissions
```sql
SELECT item_id, seller_id, COUNT(*) as active_count
FROM inventory_submissions
WHERE status IN ('submitted', 'processing', 'claiming', 'ambiguous')
GROUP BY item_id, seller_id
HAVING COUNT(*) > 1;
```
If duplicates exist from prior aborted attempts, reconcile or mark old duplicates as `cancelled` or `failed` before proceeding.

### 2. Check for Missing Schema Prerequisites
```sql
SELECT column_name, data_type
FROM information_schema.columns
WHERE table_name IN ('inventory_items', 'inventory_submissions');
```

---

## Backup Requirements

Before executing migration scripts on the production database:
1. Perform a full pg_dump backup:
   ```bash
   pg_dump -Fc -h "$DB_HOST" -U "$DB_USER" "$DB_NAME" > "hhtcatalog_pre_v3_$(date +%Y%m%d%H%M%S).dump"
   ```
2. Verify backup file size and integrity:
   ```bash
   pg_restore --list "hhtcatalog_pre_v3_*.dump" | head -n 20
   ```

---

## Deployment Ordering

Follow this precise sequence during deployment:

1. **Phase 1: Database Migration (Ordered)**
   Apply `supabase/migrations/202611150100_catalog_integrity.sql` followed by `supabase/migrations/202611160100_integrity_repair_v3.sql`:
   ```bash
   psql -h "$DB_HOST" -U "$DB_USER" -d "$DB_NAME" -f supabase/migrations/202611150100_catalog_integrity.sql
   psql -h "$DB_HOST" -U "$DB_USER" -d "$DB_NAME" -f supabase/migrations/202611160100_integrity_repair_v3.sql
   ```
   All schema additions (`approval_content_hash`, `approved_content_hash`, `approved_at`, `approved_by`, `submission_stage`, `idempotency_key`, `listing_snapshot`, `inventory_submissions_active_item_idx`) are additive and update `inventory_items_workflow_status_check` to permit `'claiming'` and `'ambiguous_submission'`.

2. **Phase 2: Application Code Deployment**
   - Deploy backend code (`app.py`, `hht_app/`).
   - Deploy compiled frontend bundle (`frontend/dist/`).
   - Restart application worker dynos / service instances.

3. **Phase 3: Smoke Verification**
   - Call `/health` (expect HTTP 200 `{"status": "ok"}`).
   - Verify unauthenticated calls to `/api/inventory/batches` return HTTP 401.
   - Verify unauthenticated calls to `/api/ebay/feed/tasks/test-id` return HTTP 401.

4. **Phase 4: Ambiguous Task Reconciliation**
   - Query items in `ambiguous_submission` state:
     ```sql
     SELECT id, seller_id, updated_at FROM inventory_items WHERE workflow_status = 'ambiguous_submission';
     ```
   - For any ambiguous task, use eBay Seller Hub task query to verify if eBay processed the file before retrying.

---

## Rollback Procedure

If a rollback of the application code is required:
1. Revert application code to previous deployment tag.
2. First reconcile or resolve any rows in `'claiming'` or `'ambiguous_submission'` status back to `'needs_review'` / `'draft_failed'` so the prior constraint remains valid.
3. The database columns and indexes added by `202611150100_catalog_integrity.sql` and `202611160100_integrity_repair_v3.sql` are backwards-compatible and can remain safely in place.
4. If complete database schema rollback is explicitly desired:
   ```sql
   DROP INDEX IF EXISTS inventory_submissions_active_item_idx;
   DROP INDEX IF EXISTS inventory_submissions_active_idx;
   DROP INDEX IF EXISTS inventory_submissions_idempotency_idx;
   ALTER TABLE inventory_submissions DROP COLUMN IF EXISTS submission_stage;
   ALTER TABLE inventory_submissions DROP COLUMN IF EXISTS idempotency_key;
   ALTER TABLE inventory_submissions DROP COLUMN IF EXISTS listing_snapshot;
   ALTER TABLE inventory_items DROP COLUMN IF EXISTS approval_content_hash;
   ALTER TABLE inventory_items DROP COLUMN IF EXISTS approved_content_hash;
   ALTER TABLE inventory_items DROP COLUMN IF EXISTS approved_at;
   ALTER TABLE inventory_items DROP COLUMN IF EXISTS approved_by;
   ```

---

## Approval Renewal Instructions for Sellers

Because approval is now strictly bound to the canonical content hash (`approval_content_hash` and `approved_content_hash`):
1. Any modification to an item's title, price, category, condition, or specifics immediately revokes approval (`workflow_status` reverts to `needs_review`, `seller_confirmed = FALSE`, and approval hashes/timestamps are cleared).
2. Any photo rearrangement, addition, deletion, merge, or cover photo change immediately invalidates approval.
3. Previously approved items lacking `approval_content_hash` will fail pre-submission hash validation and prompt the seller to review and re-approve. To bulk-reset legacy approved rows prior to cutover:
   ```sql
   UPDATE public.inventory_items
   SET workflow_status = 'needs_review',
       seller_confirmed = FALSE,
       approval_content_hash = '',
       approved_content_hash = NULL,
       approved_at = NULL,
       approved_by = ''
   WHERE workflow_status = 'approved'
     AND (approval_content_hash = '' OR approval_content_hash IS NULL);
   ```
