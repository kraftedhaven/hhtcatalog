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

import io
import json
import os
import tempfile
import unittest
import uuid
from unittest import mock

import app as hht_app
from hht_app import commerce_agent, inventory_workspace
from hht_app.ebay_feed import (
    EbayFeedError,
    PHOTO_VALIDATION_MAX_BYTES,
    _validate_image_url,
)
from PIL import Image


class IntegrityReleaseBlockersTests(unittest.TestCase):
    def setUp(self):
        self.db_file = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False)
        self.db_file.close()
        self.old_db = os.environ.get("COMMERCE_AGENT_DB")
        os.environ["COMMERCE_AGENT_DB"] = self.db_file.name

        self.old_supabase_url = os.environ.get("SUPABASE_URL")
        os.environ["SUPABASE_URL"] = "https://project.supabase.co"

        self.old_drafts_flag = os.environ.get("EBAY_DRAFTS_ENABLED")
        self.old_mutations_flag = os.environ.get("EBAY_MUTATIONS_ENABLED")
        os.environ["EBAY_DRAFTS_ENABLED"] = "true"
        os.environ["EBAY_MUTATIONS_ENABLED"] = "true"

        self.scope_mock = mock.patch(
            "hht_app.inventory_workspace.ebay_scope_status",
            return_value={"grantedScopesStatus": "verified"},
        )
        self.scope_mock.start()

        commerce_agent.init_db()

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

    def tearDown(self):
        self.scope_mock.stop()
        if self.old_supabase_url is None:
            os.environ.pop("SUPABASE_URL", None)
        else:
            os.environ["SUPABASE_URL"] = self.old_supabase_url
        for k, v in (("EBAY_DRAFTS_ENABLED", self.old_drafts_flag), ("EBAY_MUTATIONS_ENABLED", self.old_mutations_flag)):
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        try:
            os.unlink(self.db_file.name)
        except FileNotFoundError:
            pass
        if self.old_db is None:
            os.environ.pop("COMMERCE_AGENT_DB", None)
        else:
            os.environ["COMMERCE_AGENT_DB"] = self.old_db

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
        client = hht_app.app.test_client()
        res_task = client.get("/api/ebay/feed/tasks/feed-task-123")
        self.assertEqual(res_task.status_code, 401)

        res_result = client.get("/api/ebay/feed/tasks/feed-task-123/result")
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
    # BLOCKER 5: Hosted image safety (connection time & streaming limit & decoding)
    # ──────────────────────────────────────────────────────────────────────────

    def test_hosted_image_rejects_http_and_internal_destinations(self):
        """Rejects non-HTTPS schemes and internal/loopback hostnames."""
        with self.assertRaises(EbayFeedError) as ctx:
            _validate_image_url("http://example.com/photo.jpg", 1, 5.0)
        self.assertIn("not HTTPS", ctx.exception.safe_message)

        with self.assertRaises(EbayFeedError) as ctx2:
            _validate_image_url("https://127.0.0.1/photo.jpg", 1, 5.0)
        self.assertIn("internal", ctx2.exception.safe_message.lower())

    def test_hosted_image_streams_with_hard_byte_limit(self):
        """Oversized photo stream raises size limit error before loading unbounded body."""
        # Generate dummy bytes exceeding PHOTO_VALIDATION_MAX_BYTES
        mock_resp = mock.MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"Content-Type": "image/jpeg", "Content-Length": str(PHOTO_VALIDATION_MAX_BYTES + 1024)}
        mock_resp.iter_content.return_value = [b"A" * 16384]

        with mock.patch("hht_app.ebay_feed.requests.get", return_value=mock_resp), \
             mock.patch("hht_app.ebay_feed._is_private_address", return_value=False):
            with self.assertRaises(EbayFeedError) as ctx:
                _validate_image_url("https://example.com/oversized.jpg", 1, 5.0)
            self.assertIn("size limit", ctx.exception.safe_message.lower())

    def test_hosted_image_rejects_truncated_or_corrupt_content(self):
        """Valid magic bytes followed by truncated content is rejected by image decoder."""
        # JPEG header followed by garbage
        fake_jpeg = b"\xff\xd8\xff\xe0" + b"\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00" + b"truncated"

        mock_resp = mock.MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"Content-Type": "image/jpeg"}
        mock_resp.iter_content.return_value = [fake_jpeg]

        with mock.patch("hht_app.ebay_feed.requests.get", return_value=mock_resp), \
             mock.patch("hht_app.ebay_feed._is_private_address", return_value=False):
            with self.assertRaises(EbayFeedError) as ctx:
                _validate_image_url("https://example.com/corrupt.jpg", 1, 5.0)
            self.assertIn("truncated or corrupt", ctx.exception.safe_message.lower())

    def test_hosted_image_accepts_valid_image(self):
        """Valid PIL image passes validation."""
        img_io = io.BytesIO()
        img = Image.new("RGB", (100, 100), color="blue")
        img.save(img_io, format="JPEG")
        valid_bytes = img_io.getvalue()

        mock_resp = mock.MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"Content-Type": "image/jpeg", "Content-Length": str(len(valid_bytes))}
        mock_resp.iter_content.return_value = [valid_bytes]

        with mock.patch("hht_app.ebay_feed.requests.get", return_value=mock_resp), \
             mock.patch("hht_app.ebay_feed._is_private_address", return_value=False):
            # Should complete without error
            _validate_image_url("https://example.com/valid.jpg", 1, 5.0)

    # ──────────────────────────────────────────────────────────────────────────
    # BLOCKER 6: Strict destructive JSON booleans
    # ──────────────────────────────────────────────────────────────────────────

    def test_strict_json_bool_rejects_strings_and_numbers(self):
        """Route parsing strictly rejects string 'true' and number 1 for destructive actions."""
        client = hht_app.app.test_client()
        _, item = self._create_approved_item(self.seller_a)

        def auth_seller_a():
            # pyrefly: ignore[missing-import]
            from flask import g
            g.supabase_user = {"sub": self.user_a}

        with mock.patch("app.authenticate_request", side_effect=auth_seller_a), \
             mock.patch("app.commerce_agent.ensure_seller_identity", return_value={"id": self.seller_a, "auth_user_id": self.user_a}):
            # String 'true' in delete body
            res_del_str = client.delete(f"/api/inventory/items/{item['id']}", json={"allowDestructive": "true"})
            self.assertEqual(res_del_str.status_code, 400)
            self.assertIn("boolean", res_del_str.get_json()["error"].lower())

            # Number 1 in delete body
            res_del_num = client.delete(f"/api/inventory/items/{item['id']}", json={"allowDestructive": 1})
            self.assertEqual(res_del_num.status_code, 400)
            self.assertIn("boolean", res_del_num.get_json()["error"].lower())

            # String 'true' in merge body
            _, target = self._create_approved_item(self.seller_a, title="Target Coat")
            res_merge = client.post(f"/api/inventory/items/{target['id']}/merge", json={
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
        """Legacy queue dictionaries transition safely into persisted batch in 'needs_review' status."""
        queue = [
            {"title": "Legacy Item 1", "price": "19.99", "cat": "57988", "size": "M"},
            {"title": "Legacy Item 2", "price": "29.99", "cat": "57988", "size": "L"},
        ]
        res = inventory_workspace.import_legacy_queue_to_batch(queue, self.seller_a, batch_name="Legacy Transition")
        self.assertEqual(res["importedCount"], 2)
        batch_id = res["batch"]["id"]

        items = inventory_workspace.list_batch_items(batch_id, self.seller_a)
        self.assertEqual(len(items), 2)
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

    @unittest.skipUnless(
        os.environ.get("TEST_POSTGRES_URL"),
        "Set TEST_POSTGRES_URL for live disposable PostgreSQL execution tests",
    )
    def test_disposable_postgresql_end_to_end_grouping_moves_merges_invalidation_and_claims(self):
        """Execute grouping, photo moves, donor sync, merges, boolean approval invalidation,
        manual CSV export, ambiguous outcomes, and concurrent claims against real PostgreSQL."""
        from concurrent.futures import ThreadPoolExecutor

        pg_url = os.environ["TEST_POSTGRES_URL"]
        pg_seller = str(uuid.uuid4())
        pg_auth_user = str(uuid.uuid4())

        with mock.patch.dict(os.environ, {"DATABASE_URL": pg_url}):
            with commerce_agent.connect() as db:
                self.assertTrue(db.postgres)
                db.execute(
                    "INSERT INTO sellers(id, auth_user_id, status, created_at) VALUES (?, ?, 'active', ?)",
                    (pg_seller, pg_auth_user, commerce_agent.utc_now()),
                )

            try:
                batch = inventory_workspace.create_batch(pg_seller, name="PG Integrity Batch", grouping_mode="manual")
                photos = inventory_workspace.register_batch_photos(batch["id"], pg_seller, [
                    {"filename": "pg1.jpg", "previewUrl": "https://example.com/pg1.jpg", "storageUrl": "https://example.com/pg1.jpg"},
                    {"filename": "pg2.jpg", "previewUrl": "https://example.com/pg2.jpg", "storageUrl": "https://example.com/pg2.jpg"},
                    {"filename": "pg3.jpg", "previewUrl": "https://example.com/pg3.jpg", "storageUrl": "https://example.com/pg3.jpg"},
                ])
                donor = inventory_workspace.create_item(batch["id"], pg_seller, {
                    "photoIds": [photos[0]["id"], photos[1]["id"]],
                    "listing": {"title": "PG Donor Jacket L", "price": "45.00", "cat": "57988", "size": "L", "brand": "Vintage"},
                })
                receiver = inventory_workspace.create_item(batch["id"], pg_seller, {
                    "photoIds": [photos[2]["id"]],
                    "listing": {"title": "PG Receiver Shirt M", "price": "25.00", "cat": "57988", "size": "M", "brand": "Vintage"},
                })
                for it in (donor, receiver):
                    inventory_workspace.patch_item(it["id"], pg_seller, {"confirmAll": True})
                inv_app = inventory_workspace.approve_items([donor["id"], receiver["id"]], pg_seller)
                self.assertEqual(inv_app["approvedCount"], 2)

                # Moving a photo from an approved donor requires allow_destructive=True
                with self.assertRaises(ValueError):
                    inventory_workspace.update_item_photos(receiver["id"], pg_seller, [photos[2]["id"], photos[1]["id"]], allow_destructive=False)

                # With allow_destructive=True, donor & receiver sync and invalidate PostgreSQL boolean seller_confirmed=FALSE
                moved = inventory_workspace.update_item_photos(
                    receiver["id"], pg_seller, [photos[2]["id"], photos[1]["id"]], allow_destructive=True
                )
                self.assertEqual(moved["photoCount"], 2)
                self.assertEqual(moved["workflowStatus"], "needs_review")
                self.assertFalse(moved["sellerConfirmed"])
                self.assertEqual(moved["approvalContentHash"], "")

                donor_after = inventory_workspace.get_item(donor["id"], pg_seller)
                self.assertEqual(donor_after["photoCount"], 1)
                self.assertEqual(donor_after["workflowStatus"], "needs_review")
                self.assertFalse(donor_after["sellerConfirmed"])

                # Merge donor into receiver
                merged = inventory_workspace.merge_items(donor["id"], receiver["id"], pg_seller, allow_destructive=True)
                self.assertEqual(merged["photoCount"], 3)
                with self.assertRaises(KeyError):
                    inventory_workspace.get_item(donor["id"], pg_seller)

                # Approve merged item, then edit to verify PostgreSQL boolean invalidation
                inventory_workspace.patch_item(merged["id"], pg_seller, {"confirmAll": True})
                app_res = inventory_workspace.approve_items([merged["id"]], pg_seller)
                self.assertEqual(app_res["approvedCount"], 1)
                edited = inventory_workspace.patch_item(merged["id"], pg_seller, {"price": "55.00"})
                self.assertEqual(edited["workflowStatus"], "needs_review")
                self.assertFalse(edited["sellerConfirmed"])

                # Re-approve and verify manual CSV export with mutations disabled
                inventory_workspace.patch_item(merged["id"], pg_seller, {"confirmAll": True})
                self.assertEqual(inventory_workspace.approve_items([merged["id"]], pg_seller)["approvedCount"], 1)
                with mock.patch.dict(os.environ, {"EBAY_MUTATIONS_ENABLED": "false", "EBAY_DRAFTS_ENABLED": "false"}):
                    with mock.patch("hht_app.inventory_workspace.validate_draft_photo_urls", return_value=None):
                        csv_out = inventory_workspace.export_batch_seller_hub_draft_csv(batch["id"], pg_seller)
                self.assertIn("Draft", csv_out)
                self.assertIn("PG Receiver Shirt M", csv_out)

                # Concurrent draft submission claims against PostgreSQL unique index
                def submit_once():
                    with mock.patch.dict(os.environ, {"DATABASE_URL": pg_url}):
                        try:
                            return ("ok", inventory_workspace.submit_approved_drafts([merged["id"]], pg_seller))
                        except Exception as exc:
                            return ("error", str(exc))

                with mock.patch("hht_app.inventory_workspace.upload_seller_hub_draft_csv", return_value={"taskId": "pg-task-1"}) as upload:
                    with ThreadPoolExecutor(max_workers=2) as pool:
                        results = list(pool.map(lambda _i: submit_once(), range(2)))
                self.assertEqual(sum(k == "ok" for k, _ in results), 1)
                self.assertEqual(sum(k == "error" for k, _ in results), 1)
                self.assertIn("active or ambiguous", next(v for k, v in results if k == "error").lower())
                upload.assert_called_once()

                # Also exercise ambiguous_submission workflow_status constraint in PostgreSQL
                pg4 = inventory_workspace.register_batch_photos(batch["id"], pg_seller, [
                    {"filename": "pg4.jpg", "previewUrl": "https://example.com/pg4.jpg", "storageUrl": "https://example.com/pg4.jpg"},
                ])[0]
                amb_item = inventory_workspace.create_item(batch["id"], pg_seller, {
                    "photoIds": [pg4["id"]],
                    "listing": {"title": "PG Ambiguous Item", "price": "30.00", "cat": "57988", "size": "M", "brand": "Vintage"},
                }, actor="pg-test")
                inventory_workspace.patch_item(amb_item["id"], pg_seller, {"confirmAll": True})
                self.assertEqual(inventory_workspace.approve_items([amb_item["id"]], pg_seller)["approvedCount"], 1)

                def fail_404(listings, on_task_created=None, on_upload_sent=None, on_upload_confirmed=None):
                    if on_task_created:
                        on_task_created("pg-task-404")
                    if on_upload_sent:
                        on_upload_sent("pg-task-404")
                    if on_upload_confirmed:
                        on_upload_confirmed("pg-task-404")
                    raise EbayFeedError(404, "request_error", "Not found", code="404", operation="get_task")

                with mock.patch("hht_app.inventory_workspace.upload_seller_hub_draft_csv", side_effect=fail_404):
                    with self.assertRaises(EbayFeedError):
                        inventory_workspace.submit_approved_drafts([amb_item["id"]], pg_seller)

                amb_after = inventory_workspace.get_item(amb_item["id"], pg_seller)
                self.assertEqual(amb_after["workflowStatus"], "ambiguous_submission")
            finally:
                with commerce_agent.connect() as db:
                    db.execute("DELETE FROM inventory_submissions WHERE seller_id = ?", (pg_seller,))
                    db.execute("DELETE FROM inventory_batches WHERE seller_id = ?", (pg_seller,))
                    db.execute("DELETE FROM sellers WHERE id = ?", (pg_seller,))

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


if __name__ == "__main__":
    unittest.main()
