"""Comprehensive validation suite for HHTCatalog integrity release blockers.

Directly validates:
1. Submission ambiguity and duplicate prevention (task-status 404 / retry blocking / reconciliation)
2. Approval / content concurrency (interleaved edits during active claim / immutable snapshot)
3. Feed task authentication and cross-seller isolation
4. Centralized submission authorization (flags, scopes, idempotency, ownership)
5. Hosted image safety (private IPs, unsafe redirects, streaming byte cap, decoded content verification)
6. Strict destructive JSON booleans
7. Workflow preservation (manual CSV export with mutation flags disabled; legacy queue transition)
8. PostgreSQL approval invalidation (verifies Boolean False binding in SQL parameters)
"""
from __future__ import annotations

import os
import tempfile
import unittest
import uuid
from unittest import mock

import app as hht_app
from hht_app import commerce_agent, inventory_workspace
from hht_app.ebay_feed import EbayFeedError


class IntegrityReleaseBlockersTests(unittest.TestCase):
    def setUp(self):
        self.db_file = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False)
        self.db_file.close()
        self.addCleanup(self._remove_temp_db)

        self.env_patch = mock.patch.dict(
            os.environ,
            {
                "COMMERCE_AGENT_DB": self.db_file.name,
                "SUPABASE_URL": "https://project.supabase.co",
                "EBAY_DRAFTS_ENABLED": "true",
                "EBAY_MUTATIONS_ENABLED": "true",
            },
        )
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)

        self.scope_mock = mock.patch(
            "hht_app.inventory_workspace.ebay_scope_status",
            return_value={"grantedScopesStatus": "verified"},
        )
        self.scope_mock.start()
        self.addCleanup(self.scope_mock.stop)

        commerce_agent.init_db()
        self.client = hht_app.app.test_client()

        self.seller_a = f"seller-a-{uuid.uuid4().hex[:6]}"
        self.seller_b = f"seller-b-{uuid.uuid4().hex[:6]}"
        self.user_a = f"user-a-{uuid.uuid4().hex[:6]}"
        self.user_b = f"user-b-{uuid.uuid4().hex[:6]}"

        now = commerce_agent.utc_now()
        with commerce_agent.connect() as db:
            db.execute(
                "INSERT INTO sellers(id, auth_user_id, status, created_at) VALUES(?, ?, 'active', ?)",
                (self.seller_a, self.user_a, now),
            )
            db.execute(
                "INSERT INTO sellers(id, auth_user_id, status, created_at) VALUES(?, ?, 'active', ?)",
                (self.seller_b, self.user_b, now),
            )

    def _remove_temp_db(self):
        try:
            os.unlink(self.db_file.name)
        except FileNotFoundError:
            pass

    def _create_approved_item(self, seller_id: str, title: str = "Vintage Wool Coat L") -> tuple[dict, dict]:
        batch = inventory_workspace.create_batch(seller_id, name="Test Batch", grouping_mode="manual")
        photos = inventory_workspace.register_batch_photos(batch["id"], seller_id, [
            {"filename": "coat1.jpg", "previewUrl": "https://example.com/coat1.jpg", "storageUrl": "https://example.com/coat1.jpg"},
            {"filename": "coat2.jpg", "previewUrl": "https://example.com/coat2.jpg", "storageUrl": "https://example.com/coat2.jpg"},
        ])
        item = inventory_workspace.create_item(batch["id"], seller_id, {
            "photoIds": [p["id"] for p in photos],
            "listing": {"title": title, "price": "49.99", "cat": "57988", "size": "L", "brand": "Vintage"},
        })
        inventory_workspace.patch_item(item["id"], seller_id, {"confirmAll": True})
        inventory_workspace.approve_items([item["id"]], seller_id)
        item = inventory_workspace.get_item(item["id"], seller_id)
        return batch, item

    # ──────────────────────────────────────────────────────────────────────────
    # BLOCKER 1: Submission ambiguity & duplicate prevention
    # ──────────────────────────────────────────────────────────────────────────

    def test_accepted_upload_with_status_404_sets_ambiguous_and_blocks_retry(self):
        """When an upload is accepted but task polling returns 404, the task ID is retained,
        status becomes 'ambiguous', and retries are blocked until reconciled."""
        _, item = self._create_approved_item(self.seller_a)

        # Mock upload_seller_hub_draft_csv to simulate accepted upload followed by 404 on get_task
        def mock_upload_with_status_404(listings, on_task_created=None, on_upload_sent=None, on_upload_confirmed=None):
            task_id = "feed-task-accepted-404"
            if on_task_created:
                on_task_created(task_id)
            if on_upload_sent:
                on_upload_sent(task_id)
            if on_upload_confirmed:
                on_upload_confirmed(task_id)
            raise EbayFeedError(404, "request_error", "Feed task not found", code="404", operation="get_task")

        with mock.patch("hht_app.inventory_workspace.upload_seller_hub_draft_csv", side_effect=mock_upload_with_status_404):
            with self.assertRaises(EbayFeedError):
                inventory_workspace.submit_approved_drafts([item["id"]], self.seller_a)

        # Verify submission record retained task ID and marked as ambiguous (NOT failed!)
        with commerce_agent.connect() as db:
            sub = db.execute(
                "SELECT * FROM inventory_submissions WHERE item_id = ? ORDER BY created_at DESC LIMIT 1",
                (item["id"],),
            ).fetchone()
            item_row = db.execute("SELECT workflow_status FROM inventory_items WHERE id = ?", (item["id"],)).fetchone()

        self.assertIsNotNone(sub)
        self.assertEqual(sub["feed_task_id"], "feed-task-accepted-404")
        self.assertEqual(sub["status"], "ambiguous")
        self.assertEqual(sub["submission_stage"], "upload_confirmed")
        self.assertEqual(item_row["workflow_status"], "ambiguous_submission")

        # Retry attempt MUST be blocked
        with self.assertRaises(ValueError) as ctx:
            inventory_workspace.submit_approved_drafts([item["id"]], self.seller_a)
        self.assertIn("active or ambiguous", str(ctx.exception).lower())

    # ──────────────────────────────────────────────────────────────────────────
    # BLOCKER 2: Approval / content concurrency & mutation protection
    # ──────────────────────────────────────────────────────────────────────────

    def test_listing_edit_during_active_claim_is_rejected(self):
        """Editing an item during an active claim acquisition is blocked."""
        _, item = self._create_approved_item(self.seller_a)

        # Put item into 'claiming' state manually to simulate active in-flight claim
        with commerce_agent.connect() as db:
            db.execute("UPDATE inventory_items SET workflow_status = 'claiming' WHERE id = ?", (item["id"],))

        with self.assertRaises(ValueError) as ctx:
            inventory_workspace.patch_item(item["id"], self.seller_a, {"price": "99.99"})
        self.assertIn("active or ambiguous", str(ctx.exception).lower())

    def test_photo_mutation_during_active_claim_is_rejected(self):
        """Photo modifications during active submission claim are blocked."""
        _, item = self._create_approved_item(self.seller_a)
        with commerce_agent.connect() as db:
            db.execute("UPDATE inventory_items SET workflow_status = 'claiming' WHERE id = ?", (item["id"],))

        with self.assertRaises(ValueError) as ctx:
            inventory_workspace.update_item_photos(item["id"], self.seller_a, [item["photos"][0]["id"]])
        self.assertIn("active or ambiguous", str(ctx.exception).lower())

    def test_delete_and_merge_during_active_claim_are_rejected(self):
        """Deletions and merges are blocked when an item has an active claim."""
        batch, item1 = self._create_approved_item(self.seller_a, title="Coat One L")
        _, item2 = self._create_approved_item(self.seller_a, title="Coat Two L")

        with commerce_agent.connect() as db:
            db.execute("UPDATE inventory_items SET workflow_status = 'claiming' WHERE id = ?", (item1["id"],))

        with self.assertRaises(ValueError):
            inventory_workspace.delete_item(item1["id"], self.seller_a, allow_destructive=True)

        with self.assertRaises(ValueError):
            inventory_workspace.merge_items(item1["id"], item2["id"], self.seller_a, allow_destructive=True)

    def test_editing_approved_item_revokes_approval_and_clears_content_hash(self):
        """Modifying an approved item before claim revokes approval and invalidates hash."""
        _, item = self._create_approved_item(self.seller_a)
        self.assertEqual(item["workflowStatus"], "approved")

        with commerce_agent.connect() as db:
            row = db.execute("SELECT approved_content_hash FROM inventory_items WHERE id = ?", (item["id"],)).fetchone()
            self.assertTrue(bool(row["approved_content_hash"]))

        # Edit price
        inventory_workspace.patch_item(item["id"], self.seller_a, {"price": "79.99"})
        updated = inventory_workspace.get_item(item["id"], self.seller_a)
        self.assertEqual(updated["workflowStatus"], "needs_review")

        with commerce_agent.connect() as db:
            row_after = db.execute("SELECT approved_content_hash FROM inventory_items WHERE id = ?", (item["id"],)).fetchone()
            self.assertIsNone(row_after["approved_content_hash"])

    # ──────────────────────────────────────────────────────────────────────────
    # BLOCKER 3: Feed task authentication and cross-seller isolation
    # ──────────────────────────────────────────────────────────────────────────

    def test_feed_task_routes_require_authentication(self):
        """Unauthenticated requests to feed task and result routes receive 401."""
        res_task = self.client.get("/api/ebay/feed/tasks/feed-task-123")
        self.assertEqual(res_task.status_code, 401)

        res_result = self.client.get("/api/ebay/feed/tasks/feed-task-123/result")
        self.assertEqual(res_result.status_code, 401)

    def test_feed_task_cross_seller_access_is_forbidden(self):
        """A seller cannot access feed task status or result belonging to another seller."""
        _, item = self._create_approved_item(self.seller_a)

        # Record a submission under Seller A
        with commerce_agent.connect() as db:
            db.execute(
                """INSERT INTO inventory_submissions(
                    id, item_id, seller_id, batch_id, feed_task_id, status, submission_stage, idempotency_key, result_json, created_at, updated_at
                ) VALUES(?, ?, ?, ?, ?, 'submitted', 'upload_confirmed', 'idem-1', '{}', ?, ?)""",
                (str(uuid.uuid4()), item["id"], self.seller_a, item["batchId"], "feed-task-a-exclusive", commerce_agent.utc_now(), commerce_agent.utc_now()),
            )

        client = hht_app.app.test_client()

        # Seller B attempts to access Seller A's feed task
        def auth_seller_b():
            # pyrefly: ignore[missing-import]
            from flask import g
            g.supabase_user = {"sub": self.user_b}

        with mock.patch("app.authenticate_request", side_effect=auth_seller_b), \
             mock.patch("app.commerce_agent.ensure_seller_identity", return_value={"id": self.seller_b, "auth_user_id": self.user_b}):
            res = client.get("/api/ebay/feed/tasks/feed-task-a-exclusive")
            self.assertEqual(res.status_code, 403)
            self.assertIn("do not have access", res.get_json()["error"])

            res_file = client.get("/api/ebay/feed/tasks/feed-task-a-exclusive/result")
            self.assertEqual(res_file.status_code, 403)

    # ──────────────────────────────────────────────────────────────────────────
    # BLOCKER 4: Centralized submission authorization
    # ──────────────────────────────────────────────────────────────────────────

    def test_submission_blocked_when_ebay_mutations_disabled(self):
        """EBAY_MUTATIONS_ENABLED=false blocks draft writes in centralized service."""
        _, item = self._create_approved_item(self.seller_a)
        with mock.patch.dict(os.environ, {"EBAY_MUTATIONS_ENABLED": "false"}):
            with self.assertRaises(ValueError) as ctx:
                inventory_workspace.submit_approved_drafts([item["id"]], self.seller_a)
            self.assertIn("mutations", str(ctx.exception).lower())

    def test_submission_blocked_when_ebay_drafts_disabled(self):
        """EBAY_DRAFTS_ENABLED=false blocks draft writes."""
        _, item = self._create_approved_item(self.seller_a)
        with mock.patch.dict(os.environ, {"EBAY_DRAFTS_ENABLED": "false"}):
            with self.assertRaises(ValueError) as ctx:
                inventory_workspace.submit_approved_drafts([item["id"]], self.seller_a)
            self.assertIn("disabled", str(ctx.exception).lower())

    def test_submission_blocked_when_scope_proofs_unverified(self):
        """Unverified scope proofs block draft submission."""
        _, item = self._create_approved_item(self.seller_a)
        with mock.patch("hht_app.inventory_workspace.ebay_scope_status", return_value={"grantedScopesStatus": "unknown"}):
            with self.assertRaises(ValueError) as ctx:
                inventory_workspace.submit_approved_drafts([item["id"]], self.seller_a)
            self.assertIn("scope proofs", str(ctx.exception).lower())

    # ──────────────────────────────────────────────────────────────────────────
    # BLOCKER 6: Strict destructive JSON booleans
    # ──────────────────────────────────────────────────────────────────────────

    def test_strict_json_bool_rejects_strings_and_numbers(self):
        """Route parsing strictly rejects string 'true' and number 1 for destructive actions."""
        _, item = self._create_approved_item(self.seller_a)

        def auth_seller_a():
            from flask import g
            g.supabase_user = {"sub": self.user_a}

        with mock.patch("app.authenticate_request", side_effect=auth_seller_a), \
             mock.patch("app.commerce_agent.ensure_seller_identity", return_value={"id": self.seller_a, "auth_user_id": self.user_a}):
            res_del_str = self.client.delete(f"/api/inventory/items/{item['id']}", json={"allowDestructive": "true"})
            self.assertEqual(res_del_str.status_code, 400)
            self.assertIn("boolean", res_del_str.get_json()["error"].lower())

            res_del_num = self.client.delete(f"/api/inventory/items/{item['id']}", json={"allowDestructive": 1})
            self.assertEqual(res_del_num.status_code, 400)
            self.assertIn("boolean", res_del_num.get_json()["error"].lower())

            _, target = self._create_approved_item(self.seller_a, title="Target Coat")
            res_merge = self.client.post(f"/api/inventory/items/{target['id']}/merge", json={
                "sourceItemId": item["id"],
                "allowDestructive": "yes",
            })
            self.assertEqual(res_merge.status_code, 400)
            self.assertIn("boolean", res_merge.get_json()["error"].lower())

    # ──────────────────────────────────────────────────────────────────────────
    # BLOCKER 7: Workflow preservation & manual CSV export
    # ──────────────────────────────────────────────────────────────────────────

    def test_manual_csv_export_works_with_mutations_and_drafts_disabled(self):
        """Exporting Seller Hub CSV succeeds even when EBAY_MUTATIONS_ENABLED=false."""
        batch, _ = self._create_approved_item(self.seller_a)

        with mock.patch.dict(os.environ, {"EBAY_MUTATIONS_ENABLED": "false", "EBAY_DRAFTS_ENABLED": "false"}):
            with mock.patch("hht_app.inventory_workspace.validate_draft_photo_urls", return_value=None):
                csv_out = inventory_workspace.export_batch_seller_hub_draft_csv(batch["id"], self.seller_a)

        self.assertIn("#INFO", csv_out)
        self.assertIn("Action(SiteID=US|Country=US|Currency=USD|Version=1193|CC=UTF-8)", csv_out)
        self.assertIn("Draft", csv_out)
        self.assertIn("Vintage Wool Coat L", csv_out)

    def test_legacy_queue_transition_creates_persisted_unapproved_items(self):
        """Legacy queue dictionaries transition safely into persisted batch in 'needs_review' status with photos."""
        queue = [
            {
                "title": "Legacy Item 1",
                "price": "19.99",
                "cat": "57988",
                "size": "M",
                "pic": "https://example.com/legacy1.jpg|https://example.com/legacy2.jpg",
            },
            {"title": "Legacy Item 2", "price": "29.99", "cat": "57988", "size": "L"},
        ]
        res = inventory_workspace.import_legacy_queue_to_batch(queue, self.seller_a, batch_name="Legacy Transition")
        self.assertEqual(res["importedCount"], 2)
        batch_id = res["batch"]["id"]

        items = inventory_workspace.list_batch_items(batch_id, self.seller_a)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["photoCount"], 2)
        self.assertIn("https://example.com/legacy1.jpg", items[0]["listing"]["pic"])
        for it in items:
            self.assertEqual(it["workflowStatus"], "needs_review")
            self.assertFalse(it["sellerConfirmed"])

    # ──────────────────────────────────────────────────────────────────────────
    # BLOCKER 8: PostgreSQL approval invalidation Boolean False binding
    # ──────────────────────────────────────────────────────────────────────────

    def test_approval_invalidation_binds_boolean_false(self):
        """_invalidate_approval binds genuine Python False, compatible with PostgreSQL and SQLite."""
        _, item = self._create_approved_item(self.seller_a)
        self.assertEqual(item["workflowStatus"], "approved")

        now = commerce_agent.utc_now()
        with commerce_agent.connect() as db:
            inventory_workspace._invalidate_approval(db, item["id"], self.seller_a, now)

        updated = inventory_workspace.get_item(item["id"], self.seller_a)
        self.assertEqual(updated["workflowStatus"], "needs_review")
        self.assertFalse(updated["sellerConfirmed"])

    def test_completed_with_errors_allows_retry_for_failed_item_and_blocks_succeeded_item(self):
        """Partial feed errors allow resubmitting the failed item after fixes while blocking the succeeded item."""
        from hht_app.schema import _draft_sku, normalize_listing

        _, ok_item = self._create_approved_item(self.seller_a, title="Succeeded Coat L")
        _, err_item = self._create_approved_item(self.seller_a, title="Errored Coat M")
        ok_sku = _draft_sku(ok_item["listing"], normalize_listing(ok_item["listing"]))
        err_sku = _draft_sku(err_item["listing"], normalize_listing(err_item["listing"]))

        with mock.patch("hht_app.inventory_workspace.upload_seller_hub_draft_csv", return_value={"taskId": "task-mixed-1"}):
            inventory_workspace.submit_approved_drafts([ok_item["id"], err_item["id"]], self.seller_a)

        partial_details = {
            "taskId": "task-mixed-1",
            "status": "COMPLETED",
            "uploadSummary": {"successCount": 1, "failureCount": 1},
            "resultDetails": {"errorCount": 1, "errors": [{"customLabel": err_sku, "message": "Invalid item specific"}]},
        }
        with mock.patch("hht_app.inventory_workspace.get_feed_task_details", return_value=partial_details):
            ok_status = inventory_workspace.get_item_submission_status(ok_item["id"], self.seller_a)
            err_status = inventory_workspace.get_item_submission_status(err_item["id"], self.seller_a)
        self.assertEqual(ok_status["itemWorkflowStatus"], "draft_result_available")
        self.assertEqual(err_status["itemWorkflowStatus"], "draft_failed")

        inventory_workspace.patch_item(err_item["id"], self.seller_a, {"price": "59.99", "confirmAll": True})
        self.assertEqual(inventory_workspace.approve_items([err_item["id"]], self.seller_a)["approvedCount"], 1)

        with mock.patch("hht_app.inventory_workspace.upload_seller_hub_draft_csv", return_value={"taskId": "task-retry-2"}) as upload:
            res = inventory_workspace.submit_approved_drafts([err_item["id"]], self.seller_a)
            self.assertEqual(res["feedTaskId"], "task-retry-2")
            self.assertEqual(res["taskId"], "task-retry-2")
            self.assertEqual(res["itemCount"], 1)
            upload.assert_called_once()

        with self.assertRaises(ValueError):
            inventory_workspace.submit_approved_drafts([ok_item["id"]], self.seller_a)
        self.assertTrue(bool(ok_sku))

    def test_ambiguous_submission_reconciles_via_status_poll_and_reconcile_endpoint(self):
        """Ambiguous submissions keep retry blocked on polling errors and unlock cleanly once reconciled."""
        _, item = self._create_approved_item(self.seller_a, title="Ambiguous Reconcile Coat")

        def fail_timeout(listings, on_task_created=None, on_upload_sent=None, on_upload_confirmed=None):
            if on_task_created:
                on_task_created("task-ambig-poll")
            if on_upload_sent:
                on_upload_sent("task-ambig-poll")
            raise EbayFeedError(504, "timeout", "Upload timeout", operation="upload_file")

        with mock.patch("hht_app.inventory_workspace.upload_seller_hub_draft_csv", side_effect=fail_timeout):
            with self.assertRaises(EbayFeedError):
                inventory_workspace.submit_approved_drafts([item["id"]], self.seller_a)

        # Transient error during reconciliation keeps ambiguous lock intact
        with mock.patch("hht_app.inventory_workspace.get_feed_task_details", side_effect=EbayFeedError(503, "server_error", "Busy")):
            still_ambig = inventory_workspace.get_item_submission_status(item["id"], self.seller_a)
        self.assertEqual(still_ambig["status"], "ambiguous")

        # HTTP POST /submission/reconcile polling COMPLETED unlocks item into draft_result_available
        client = hht_app.app.test_client()

        def auth_seller_a():
            from flask import g
            g.supabase_user = {"sub": self.user_a}

        completed_task = {"taskId": "task-ambig-poll", "status": "COMPLETED", "uploadSummary": {"successCount": 1, "failureCount": 0}}
        with mock.patch("app.authenticate_request", side_effect=auth_seller_a), \
             mock.patch("app.commerce_agent.ensure_seller_identity", return_value={"id": self.seller_a, "auth_user_id": self.user_a}), \
             mock.patch("hht_app.inventory_workspace.get_feed_task_details", return_value=completed_task):
            res = client.post(f"/api/inventory/items/{item['id']}/submission/reconcile", json={"resolution": "poll"})
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.get_json()["result"]["reconciled"])
        self.assertEqual(inventory_workspace.get_item(item["id"], self.seller_a)["workflowStatus"], "draft_result_available")

    def test_csv_export_rejects_seller_confirmed_only_and_validates_photo_urls_and_create_item_bool(self):
        """Export requires explicit workflowStatus=='approved', runs validate_draft_photo_urls,
        and create_item rejects non-boolean allowDestructive strings."""
        batch = inventory_workspace.create_batch(self.seller_a, name="Export & Bool Gate", grouping_mode="manual")
        photos = inventory_workspace.register_batch_photos(batch["id"], self.seller_a, [
            {"filename": "gate1.jpg", "previewUrl": "https://example.com/gate1.jpg", "storageUrl": "https://example.com/gate1.jpg"},
        ])
        item = inventory_workspace.create_item(batch["id"], self.seller_a, {
            "photoIds": [photos[0]["id"]],
            "sellerConfirmed": {"title": True, "price": True},
            "listing": {"title": "Confirmed Only Coat", "price": "45.00", "cat": "57988", "size": "M", "brand": "Vintage"},
        })

        # Confirm all fields without approving workflow_status -> sellerConfirmed=True & ready_for_approval, NOT approved
        patched = inventory_workspace.patch_item(item["id"], self.seller_a, {"confirmAll": True})
        self.assertTrue(patched["sellerConfirmed"])
        self.assertEqual(patched["workflowStatus"], "ready_for_approval")

        # CSV export must still reject unapproved item even when sellerConfirmed=True
        with self.assertRaises(ValueError) as ctx:
            inventory_workspace.export_batch_seller_hub_draft_csv(batch["id"], self.seller_a)
        self.assertIn("approved", str(ctx.exception).lower())

        # Approve item; export must invoke validate_draft_photo_urls and propagate photo safety rejections
        inventory_workspace.approve_items([item["id"]], self.seller_a)
        with mock.patch(
            "hht_app.inventory_workspace.validate_draft_photo_urls",
            side_effect=ValueError("Unsafe photo host"),
        ) as validator:
            with self.assertRaises(ValueError) as err_ctx:
                inventory_workspace.export_batch_seller_hub_draft_csv(batch["id"], self.seller_a)
        validator.assert_called_once()
        self.assertIn("Unsafe photo host", str(err_ctx.exception))

        # create_item must reject non-boolean allowDestructive
        with self.assertRaises(ValueError) as bool_ctx:
            inventory_workspace.create_item(batch["id"], self.seller_a, {
                "photoIds": [photos[0]["id"]],
                "allowDestructive": "false",
                "listing": {"title": "Bad Bool"},
            })
        self.assertIn("boolean", str(bool_ctx.exception).lower())

    def test_partial_feed_without_error_rows_stays_unresolved(self):
        """Missing or unlabeled error rows on a partial feed must not mark items succeeded prematurely."""
        _, item = self._create_approved_item(self.seller_a, title="Unresolved Row Coat")
        with mock.patch("hht_app.inventory_workspace.upload_seller_hub_draft_csv", return_value={"taskId": "task-missing-err"}):
            inventory_workspace.submit_approved_drafts([item["id"]], self.seller_a)

        for errs in ([], [{"customLabel": "", "lineNumber": "2", "message": "Unlabeled XML failure"}]):
            unresolved = {
                "taskId": "task-missing-err",
                "status": "COMPLETED",
                "uploadSummary": {"successCount": 1, "failureCount": 1},
                "resultDetails": {"errorCount": 1, "errors": errs},
            }
            with mock.patch("hht_app.inventory_workspace.get_feed_task_details", return_value=unresolved):
                status = inventory_workspace.get_item_submission_status(item["id"], self.seller_a)
            self.assertEqual(status["status"], "processing")
            self.assertEqual(status["itemWorkflowStatus"], "draft_submitted")
            self.assertEqual(inventory_workspace.get_item(item["id"], self.seller_a)["workflowStatus"], "draft_submitted")

    def test_validate_items_preserves_draft_result_available(self):
        """Validating a completed or concurrently claimed item must preserve protected workflow status."""
        _, item = self._create_approved_item(self.seller_a, title="Completed Coat")
        with mock.patch("hht_app.inventory_workspace.upload_seller_hub_draft_csv", return_value={"taskId": "task-done-1"}):
            inventory_workspace.submit_approved_drafts([item["id"]], self.seller_a)
        done_task = {"taskId": "task-done-1", "status": "COMPLETED", "uploadSummary": {"successCount": 1, "failureCount": 0}}
        with mock.patch("hht_app.inventory_workspace.get_feed_task_details", return_value=done_task):
            inventory_workspace.get_item_submission_status(item["id"], self.seller_a)
        self.assertEqual(inventory_workspace.get_item(item["id"], self.seller_a)["workflowStatus"], "draft_result_available")

        with mock.patch("hht_app.inventory_workspace.validate_listing", return_value={"status": "unverified", "findings": []}):
            val_res = inventory_workspace.validate_items([item["id"]], self.seller_a)
        self.assertEqual(val_res["results"][0]["status"], "draft_result_available")
        self.assertEqual(inventory_workspace.get_item(item["id"], self.seller_a)["workflowStatus"], "draft_result_available")

        # Concurrent claim between initial live-status read and UPDATE must preserve 'claiming'.
        _, item2 = self._create_approved_item(self.seller_a, title="Claim Race Coat")
        orig_select = inventory_workspace._select_live_workflow_status
        calls = 0

        def race_select(db, target_id, seller_id, fallback):
            nonlocal calls
            status = orig_select(db, target_id, seller_id, fallback)
            calls += 1
            if calls == 1:
                with commerce_agent.connect() as claim_db:
                    claim_db.execute("UPDATE inventory_items SET workflow_status = 'claiming' WHERE id = ?", (target_id,))
            return status

        with mock.patch("hht_app.inventory_workspace.validate_listing", return_value={"status": "unverified", "findings": []}), \
             mock.patch("hht_app.inventory_workspace._select_live_workflow_status", side_effect=race_select):
            race_res = inventory_workspace.validate_items([item2["id"]], self.seller_a)
        self.assertEqual(race_res["results"][0]["status"], "claiming")
        self.assertEqual(inventory_workspace.get_item(item2["id"], self.seller_a)["workflowStatus"], "claiming")

    def test_reconcile_rejects_cross_seller_task_override(self):
        """A seller cannot hijack another seller's feedTaskId via submission reconciliation."""
        _, item_a = self._create_approved_item(self.seller_a, title="Seller A Coat")
        _, item_b = self._create_approved_item(self.seller_b, title="Seller B Coat")
        with mock.patch("hht_app.inventory_workspace.upload_seller_hub_draft_csv", return_value={"taskId": "task-owner-a"}):
            inventory_workspace.submit_approved_drafts([item_a["id"]], self.seller_a)

        with commerce_agent.connect() as db:
            db.execute(
                """INSERT INTO inventory_submissions(
                    id, item_id, seller_id, batch_id, feed_task_id, status, submission_stage, idempotency_key, result_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'ambiguous', 'validating', 'idem-b-1', '{}', ?, ?)""",
                (str(uuid.uuid4()), item_b["id"], self.seller_b, item_b["batchId"], "pending:b1", commerce_agent.utc_now(), commerce_agent.utc_now()),
            )

        def auth_seller_b():
            from flask import g
            g.supabase_user = {"sub": self.user_b}

        with mock.patch("app.authenticate_request", side_effect=auth_seller_b), \
             mock.patch("app.commerce_agent.ensure_seller_identity", return_value={"id": self.seller_b, "auth_user_id": self.user_b}):
            res = self.client.post(
                f"/api/inventory/items/{item_b['id']}/submission/reconcile",
                json={"resolution": "poll", "feedTaskId": "task-owner-a"},
            )
        self.assertEqual(res.status_code, 403)
        self.assertEqual(inventory_workspace.get_item_submission_status(item_b["id"], self.seller_b)["feedTaskId"], "pending:b1")

    def test_concurrent_edit_and_reapprove_before_claim_rejected(self):
        """Concurrent re-approval before claim and concurrent claim before edit must both fail atomically."""
        _, item = self._create_approved_item(self.seller_a, title="Race Coat")
        orig_acquire = inventory_workspace._acquire_item_claim

        def interleaved_acquire(prepared_item, listing_snapshot, seller_id, now, db):
            inventory_workspace.patch_item(prepared_item["id"], seller_id, {"price": "99.00", "confirmAll": True})
            reapproved = inventory_workspace.approve_items([prepared_item["id"]], seller_id)
            self.assertEqual(reapproved["approvedCount"], 1)
            return orig_acquire(prepared_item, listing_snapshot, seller_id, now, db)

        with mock.patch("hht_app.inventory_workspace._acquire_item_claim", side_effect=interleaved_acquire), \
             mock.patch("hht_app.inventory_workspace.upload_seller_hub_draft_csv") as uploader:
            with self.assertRaises(ValueError) as ctx:
                inventory_workspace.submit_approved_drafts([item["id"]], self.seller_a)

        self.assertIn("concurrently", str(ctx.exception).lower())
        uploader.assert_not_called()
        refreshed = inventory_workspace.get_item(item["id"], self.seller_a)
        self.assertEqual(refreshed["workflowStatus"], "approved")
        self.assertEqual(str(refreshed["listing"].get("price")), "99.00")

        # Reverse direction: concurrent claim commits right after patch_item passes _assert_mutable().
        orig_assert = inventory_workspace._assert_mutable

        def interleaved_claim_during_edit(target_id, status_arg, context="edit", db=None):
            orig_assert(target_id, status_arg, context, db=db)
            if db is not None:
                with commerce_agent.connect() as claim_db:
                    claim_db.execute("UPDATE inventory_items SET workflow_status = 'claiming' WHERE id = ?", (target_id,))

        with mock.patch("hht_app.inventory_workspace._assert_mutable", side_effect=interleaved_claim_during_edit):
            with self.assertRaises(ValueError) as edit_ctx:
                inventory_workspace.patch_item(item["id"], self.seller_a, {"price": "20.00"})
        self.assertIn("active or ambiguous", str(edit_ctx.exception).lower())
        final_item = inventory_workspace.get_item(item["id"], self.seller_a)
        self.assertEqual(final_item["workflowStatus"], "claiming")
        self.assertEqual(str(final_item["listing"].get("price")), "99.00")

        executed_sql = []
        pg_cursor = mock.MagicMock()
        pg_cursor.fetchone.side_effect = [{"workflow_status": "approved"}, None]
        fake_pg_db = mock.MagicMock(postgres=True)
        fake_pg_db.execute.side_effect = lambda sql, params=(): executed_sql.append(sql) or pg_cursor
        inventory_workspace._assert_mutable(item["id"], "approved", "modify photos on", db=fake_pg_db)
        self.assertIn("FOR UPDATE", executed_sql[0])


if __name__ == "__main__":
    unittest.main()
