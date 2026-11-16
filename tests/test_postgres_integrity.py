"""End-to-end PostgreSQL execution tests for grouping, approval invalidation, CSV export, and claims."""
from __future__ import annotations

import os
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

from hht_app import commerce_agent, inventory_workspace
from hht_app.ebay_feed import EbayFeedError


@unittest.skipUnless(
    os.environ.get("TEST_POSTGRES_URL"),
    "Set TEST_POSTGRES_URL for live disposable PostgreSQL execution tests",
)
class PostgresIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.pg_url = os.environ["TEST_POSTGRES_URL"]
        self.pg_seller = str(uuid.uuid4())
        self.pg_auth_user = str(uuid.uuid4())
        self.db_patch = mock.patch.dict(
            os.environ,
            {
                "DATABASE_URL": self.pg_url,
                "EBAY_DRAFTS_ENABLED": "true",
                "EBAY_MUTATIONS_ENABLED": "true",
            },
        )
        self.scope_patch = mock.patch(
            "hht_app.inventory_workspace.ebay_scope_status",
            return_value={"grantedScopesStatus": "verified"},
        )
        self.db_patch.start()
        self.scope_patch.start()
        self.addCleanup(self.scope_patch.stop)
        self.addCleanup(self.db_patch.stop)
        with commerce_agent.connect() as db:
            self.assertTrue(db.postgres)
            db.execute(
                "INSERT INTO sellers(id, auth_user_id, status, created_at) VALUES (?, ?, 'active', ?)",
                (self.pg_seller, self.pg_auth_user, commerce_agent.utc_now()),
            )
        self.addCleanup(self._cleanup_pg_seller)

    def _cleanup_pg_seller(self):
        with mock.patch.dict(os.environ, {"DATABASE_URL": self.pg_url}):
            with commerce_agent.connect() as db:
                db.execute("DELETE FROM inventory_submissions WHERE seller_id = ?", (self.pg_seller,))
                db.execute("DELETE FROM inventory_batches WHERE seller_id = ?", (self.pg_seller,))
                db.execute("DELETE FROM sellers WHERE id = ?", (self.pg_seller,))

    def test_disposable_postgresql_grouping_moves_merges_invalidation_and_csv_export(self):
        """Execute photo moves, donor sync, merges, boolean approval invalidation, and manual CSV export on PostgreSQL."""
        batch = inventory_workspace.create_batch(self.pg_seller, name="PG Integrity Batch", grouping_mode="manual")
        photos = inventory_workspace.register_batch_photos(batch["id"], self.pg_seller, [
            {"filename": "pg1.jpg", "previewUrl": "https://example.com/pg1.jpg", "storageUrl": "https://example.com/pg1.jpg"},
            {"filename": "pg2.jpg", "previewUrl": "https://example.com/pg2.jpg", "storageUrl": "https://example.com/pg2.jpg"},
            {"filename": "pg3.jpg", "previewUrl": "https://example.com/pg3.jpg", "storageUrl": "https://example.com/pg3.jpg"},
        ])
        donor = inventory_workspace.create_item(batch["id"], self.pg_seller, {
            "photoIds": [photos[0]["id"], photos[1]["id"]],
            "listing": {"title": "PG Donor Jacket L", "price": "45.00", "cat": "57988", "size": "L", "brand": "Vintage"},
        })
        receiver = inventory_workspace.create_item(batch["id"], self.pg_seller, {
            "photoIds": [photos[2]["id"]],
            "listing": {"title": "PG Receiver Shirt M", "price": "25.00", "cat": "57988", "size": "M", "brand": "Vintage"},
        })
        for it in (donor, receiver):
            inventory_workspace.patch_item(it["id"], self.pg_seller, {"confirmAll": True})
        self.assertEqual(inventory_workspace.approve_items([donor["id"], receiver["id"]], self.pg_seller)["approvedCount"], 2)

        with self.assertRaises(ValueError):
            inventory_workspace.update_item_photos(receiver["id"], self.pg_seller, [photos[2]["id"], photos[1]["id"]], allow_destructive=False)

        moved = inventory_workspace.update_item_photos(
            receiver["id"], self.pg_seller, [photos[2]["id"], photos[1]["id"]], allow_destructive=True
        )
        self.assertEqual(moved["photoCount"], 2)
        self.assertFalse(moved["sellerConfirmed"])
        donor_after = inventory_workspace.get_item(donor["id"], self.pg_seller)
        self.assertEqual(donor_after["photoCount"], 1)
        self.assertFalse(donor_after["sellerConfirmed"])

        merged = inventory_workspace.merge_items(donor["id"], receiver["id"], self.pg_seller, allow_destructive=True)
        self.assertEqual(merged["photoCount"], 3)
        inventory_workspace.patch_item(merged["id"], self.pg_seller, {"confirmAll": True})
        self.assertEqual(inventory_workspace.approve_items([merged["id"]], self.pg_seller)["approvedCount"], 1)
        edited = inventory_workspace.patch_item(merged["id"], self.pg_seller, {"price": "55.00"})
        self.assertFalse(edited["sellerConfirmed"])

        inventory_workspace.patch_item(merged["id"], self.pg_seller, {"confirmAll": True})
        self.assertEqual(inventory_workspace.approve_items([merged["id"]], self.pg_seller)["approvedCount"], 1)
        with mock.patch.dict(os.environ, {"EBAY_MUTATIONS_ENABLED": "false", "EBAY_DRAFTS_ENABLED": "false"}):
            with mock.patch("hht_app.inventory_workspace.validate_draft_photo_urls", return_value=None):
                csv_out = inventory_workspace.export_batch_seller_hub_draft_csv(batch["id"], self.pg_seller)
        self.assertIn("PG Receiver Shirt M", csv_out)

    def test_disposable_postgresql_concurrent_claims_and_ambiguous_submission(self):
        """Verify concurrent submission claim uniqueness and ambiguous_submission workflow constraints on PostgreSQL."""
        batch = inventory_workspace.create_batch(self.pg_seller, name="PG Claim Batch", grouping_mode="manual")
        photos = inventory_workspace.register_batch_photos(batch["id"], self.pg_seller, [
            {"filename": "pgc1.jpg", "previewUrl": "https://example.com/pgc1.jpg", "storageUrl": "https://example.com/pgc1.jpg"},
            {"filename": "pgc2.jpg", "previewUrl": "https://example.com/pgc2.jpg", "storageUrl": "https://example.com/pgc2.jpg"},
        ])
        item = inventory_workspace.create_item(batch["id"], self.pg_seller, {
            "photoIds": [photos[0]["id"]],
            "listing": {"title": "PG Concurrent Item", "price": "35.00", "cat": "57988", "size": "M", "brand": "Vintage"},
        })
        inventory_workspace.patch_item(item["id"], self.pg_seller, {"confirmAll": True})
        self.assertEqual(inventory_workspace.approve_items([item["id"]], self.pg_seller)["approvedCount"], 1)

        def submit_once():
            with mock.patch.dict(os.environ, {"DATABASE_URL": self.pg_url}):
                try:
                    return ("ok", inventory_workspace.submit_approved_drafts([item["id"]], self.pg_seller))
                except Exception as exc:
                    return ("error", str(exc))

        with mock.patch("hht_app.inventory_workspace.upload_seller_hub_draft_csv", return_value={"taskId": "pg-task-1"}) as upload:
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(lambda _i: submit_once(), range(2)))
        self.assertEqual(sum(k == "ok" for k, _ in results), 1)
        self.assertEqual(sum(k == "error" for k, _ in results), 1)
        upload.assert_called_once()

        amb_item = inventory_workspace.create_item(batch["id"], self.pg_seller, {
            "photoIds": [photos[1]["id"]],
            "listing": {"title": "PG Ambiguous Item", "price": "30.00", "cat": "57988", "size": "M", "brand": "Vintage"},
        })
        inventory_workspace.patch_item(amb_item["id"], self.pg_seller, {"confirmAll": True})
        inventory_workspace.approve_items([amb_item["id"]], self.pg_seller)

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
                inventory_workspace.submit_approved_drafts([amb_item["id"]], self.pg_seller)
        self.assertEqual(inventory_workspace.get_item(amb_item["id"], self.pg_seller)["workflowStatus"], "ambiguous_submission")


if __name__ == "__main__":
    unittest.main()
