import json
import os
import unittest
from unittest import mock

import app as hht_app
from hht_app import commerce_agent, inventory_workspace
from hht_app.ebay_feed import EbayFeedError


class InventoryWorkspaceTests(unittest.TestCase):
    def setUp(self):
        # Use temporary in-memory or test SQLite database
        self.seller_id = "11111111-1111-4111-8111-111111111111"
        self.auth_user_id = "user-12345"
        commerce_agent.init_db()
        with commerce_agent.connect() as db:
            if not db.postgres:
                db.execute(
                    "INSERT OR IGNORE INTO sellers(id, auth_user_id, status, created_at) VALUES (?, ?, 'active', ?)",
                    (self.seller_id, self.auth_user_id, "2026-10-15T00:00:00Z"),
                )

    def test_batch_creation_and_listing(self):
        batch = inventory_workspace.create_batch(self.seller_id, name="Test Fall Batch", grouping_mode="auto")
        self.assertIn("id", batch)
        self.assertEqual(batch["name"], "Test Fall Batch")
        self.assertEqual(batch["photoGroupingMode"], "auto")
        self.assertEqual(batch["itemCount"], 0)

        batches = inventory_workspace.list_batches(self.seller_id)
        self.assertTrue(any(b["id"] == batch["id"] for b in batches))

    def test_photo_registration_and_grouping_modes(self):
        # Create batch with 6 photos and grouping mode 3 (should make 2 items)
        batch = inventory_workspace.create_batch(self.seller_id, name="6 Photo Batch", grouping_mode="3")
        photos = [
            {"filename": f"shirt_photo_{i}.jpg", "previewUrl": f"https://example.com/p{i}.jpg", "storageUrl": f"https://example.com/p{i}.jpg"}
            for i in range(6)
        ]
        registered = inventory_workspace.register_batch_photos(batch["id"], self.seller_id, photos)
        self.assertEqual(len(registered), 6)

        items = inventory_workspace.list_batch_items(batch["id"], self.seller_id)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["photoCount"], 3)
        self.assertEqual(items[1]["photoCount"], 3)
        self.assertEqual(items[0]["workflowStatus"], "grouped")
        self.assertEqual(items[1]["workflowStatus"], "grouped")

        # Test grouping mode 2 (should regroup into 3 items)
        regrouped = inventory_workspace.apply_photo_grouping(batch["id"], self.seller_id, mode="2")
        self.assertEqual(len(regrouped), 3)
        for item in regrouped:
            self.assertEqual(item["photoCount"], 2)

    def test_persistence_of_groups_and_inline_edits(self):
        batch = inventory_workspace.create_batch(self.seller_id, name="Persistence Test", grouping_mode="1")
        photos = [
            {"filename": "jacket_front.jpg", "previewUrl": "https://example.com/j1.jpg"},
            {"filename": "bag_front.jpg", "previewUrl": "https://example.com/b1.jpg"},
        ]
        inventory_workspace.register_batch_photos(batch["id"], self.seller_id, photos)

        items_initial = inventory_workspace.list_batch_items(batch["id"], self.seller_id)
        self.assertEqual(len(items_initial), 2)
        item1_id = items_initial[0]["id"]

        # Edit title and price inline
        patched = inventory_workspace.patch_item(
            item1_id,
            self.seller_id,
            {"title": "Vintage Denim Jacket Levi's Size M", "price": "49.99", "size": "M", "cat": "Men's Jackets", "cid": "57988", "brand": "Levi's"},
        )
        self.assertEqual(patched["listing"]["title"], "Vintage Denim Jacket Levi's Size M")
        self.assertEqual(patched["listing"]["price"], "49.99")
        self.assertEqual(patched["provenance"]["title"]["source"], "seller_input")
        self.assertTrue(patched["provenance"]["title"]["sellerConfirmed"])
        self.assertEqual(patched["provenance"]["price"]["source"], "seller_input")
        self.assertTrue(patched["provenance"]["price"]["sellerConfirmed"])

        # Reload from database and confirm edits persisted
        reloaded = inventory_workspace.get_item(item1_id, self.seller_id)
        self.assertEqual(reloaded["listing"]["title"], "Vintage Denim Jacket Levi's Size M")
        self.assertEqual(reloaded["listing"]["price"], "49.99")
        self.assertEqual(reloaded["listing"]["brand"], "Levi's")
        self.assertTrue(reloaded["provenance"]["title"]["sellerConfirmed"])

    def test_ai_prepopulation_and_preservation_of_seller_edits(self):
        batch = inventory_workspace.create_batch(self.seller_id, name="AI Batch", grouping_mode="1")
        inventory_workspace.register_batch_photos(batch["id"], self.seller_id, [{"filename": "sample.jpg", "previewUrl": "https://example.com/s.jpg"}])
        item = inventory_workspace.list_batch_items(batch["id"], self.seller_id)[0]

        # First, seller provides their own custom price
        inventory_workspace.patch_item(item["id"], self.seller_id, {"price": "85.00"})

        # Simulated AI analysis output
        analysis_mock = {
            "listing": {
                "title": "Vintage 90s Nike Windbreaker Jacket Large",
                "brand": "Nike",
                "model": "Windbreaker",
                "size": "L",
                "cat": "Men's Coats & Jackets",
                "cid": "57988",
                "color": "Blue",
                "price": "45.00",
                "cnote": "Minor wear consistent with age.",
                "provenance": {"brand": "Known from image", "size": "Known from image"},
            }
        }

        populated = inventory_workspace.prepopulate_item_from_analysis(
            item["id"], self.seller_id, analysis_mock, preserve_seller_edits=True
        )

        # Confirm AI prepopulated fields
        self.assertEqual(populated["listing"]["brand"], "Nike")
        self.assertEqual(populated["listing"]["size"], "L")
        self.assertEqual(populated["provenance"]["brand"]["source"], "photo_evidence")
        self.assertEqual(populated["provenance"]["brand"]["confidence"], "high")
        self.assertFalse(populated["provenance"]["brand"]["sellerConfirmed"])

        # Confirm uncertain fields are flagged as missing instead of invented
        self.assertEqual(populated["provenance"]["measurements"]["source"], "missing")
        self.assertEqual(populated["provenance"]["serialNumber"]["source"], "missing")

        # Confirm title candidates and pricing tiers were generated
        self.assertTrue(len(populated["titleCandidates"]) >= 2)
        self.assertIn("balanced", populated["pricingOptions"])
        self.assertIn("quick_sale", populated["pricingOptions"])

        # Confirm seller's edited price of 85.00 was PRESERVED
        self.assertEqual(populated["listing"]["price"], "85.00")
        self.assertEqual(populated["provenance"]["price"]["source"], "seller_input")
        self.assertTrue(populated["provenance"]["price"]["sellerConfirmed"])

    def test_validation_and_approval_gating(self):
        batch = inventory_workspace.create_batch(self.seller_id, name="Approval Test", grouping_mode="1")
        inventory_workspace.register_batch_photos(batch["id"], self.seller_id, [
            {"filename": "valid_item.jpg", "previewUrl": "https://example.com/v.jpg"},
            {"filename": "invalid_item.jpg", "previewUrl": "https://example.com/iv.jpg"},
        ])
        items = inventory_workspace.list_batch_items(batch["id"], self.seller_id)
        valid_item_id = items[0]["id"]
        invalid_item_id = items[1]["id"]

        # Make valid item complete and confirmed
        inventory_workspace.patch_item(valid_item_id, self.seller_id, {
            "title": "Vintage Graphic T-Shirt Cotton Large",
            "price": "24.99",
            "cat": "Men's T-Shirts",
            "cid": "15687",
            "size": "L",
            "brand": "Vintage",
            "confirmAll": True,
        })

        # Leave invalid item incomplete (missing price and title)
        inventory_workspace.patch_item(invalid_item_id, self.seller_id, {
            "title": "",
            "price": "",
        })

        # Try to approve both items
        approval_result = inventory_workspace.approve_items([valid_item_id, invalid_item_id], self.seller_id)

        # Valid item must be approved
        self.assertIn(valid_item_id, approval_result["approvedItemIds"])
        self.assertEqual(approval_result["approvedCount"], 1)

        # Invalid item MUST NOT be approved
        self.assertEqual(approval_result["rejectedCount"], 1)
        self.assertEqual(approval_result["rejected"][0]["itemId"], invalid_item_id)

        # Check statuses
        valid_reloaded = inventory_workspace.get_item(valid_item_id, self.seller_id)
        invalid_reloaded = inventory_workspace.get_item(invalid_item_id, self.seller_id)
        self.assertEqual(valid_reloaded["workflowStatus"], "approved")
        self.assertNotEqual(invalid_reloaded["workflowStatus"], "approved")

    def test_draft_feed_submission_rejects_unapproved_items(self):
        batch = inventory_workspace.create_batch(self.seller_id, name="Draft Feed Gate", grouping_mode="1")
        inventory_workspace.register_batch_photos(batch["id"], self.seller_id, [{"filename": "test.jpg", "previewUrl": "https://example.com/t.jpg"}])
        item = inventory_workspace.list_batch_items(batch["id"], self.seller_id)[0]

        # Item is still in 'grouped' state
        with self.assertRaises(ValueError) as ctx:
            inventory_workspace.submit_approved_drafts([item["id"]], self.seller_id)
        self.assertIn("Only explicitly approved items can reach the Seller Hub draft feed", str(ctx.exception))

    def test_draft_feed_submission_calls_feed_upload_when_approved(self):
        batch = inventory_workspace.create_batch(self.seller_id, name="Draft Feed Pass", grouping_mode="1")
        photos = [
            {"filename": f"approved_{i}.jpg", "previewUrl": f"https://example.com/app_{i}.jpg", "storageUrl": f"https://example.com/app_{i}.jpg"}
            for i in range(3)
        ]
        inventory_workspace.register_batch_photos(batch["id"], self.seller_id, photos)
        items = inventory_workspace.list_batch_items(batch["id"], self.seller_id)

        item_ids = []
        for i, item in enumerate(items):
            inventory_workspace.patch_item(item["id"], self.seller_id, {
                "title": f"Approved Item Listing {i} Vintage",
                "price": "29.99",
                "cat": "Collectibles",
                "cid": "1",
                "size": "One Size",
                "brand": "Vintage",
                "confirmAll": True,
            })
            item_ids.append(item["id"])

        # Approve all
        approval = inventory_workspace.approve_items(item_ids, self.seller_id)
        self.assertEqual(approval["approvedCount"], 3)

        # Mock the eBay draft feed upload
        mock_feed_result = {"taskId": "feed-task-12345", "status": "IN_PROCESS", "itemCount": 3}
        with mock.patch("hht_app.inventory_workspace.upload_seller_hub_draft_csv", return_value=mock_feed_result) as mock_upload:
            submission = inventory_workspace.submit_approved_drafts(item_ids, self.seller_id)
            self.assertEqual(submission["feedTaskId"], "feed-task-12345")
            self.assertEqual(submission["submittedCount"], 3)
            mock_upload.assert_called_once()

        # Confirm all 3 items moved to draft_submitted
        for item_id in item_ids:
            it = inventory_workspace.get_item(item_id, self.seller_id)
            self.assertEqual(it["workflowStatus"], "draft_submitted")

    def test_flask_inventory_routes_end_to_end(self):
        client = hht_app.app.test_client()
        def authenticate():
            # pyrefly: ignore[missing-import]
            from flask import g
            g.supabase_user = {"sub": self.auth_user_id}

        with mock.patch("app.authenticate_request", side_effect=authenticate), \
             mock.patch("app.commerce_agent.ensure_seller_identity", return_value={"id": self.seller_id, "auth_user_id": self.auth_user_id}):

            # 1. Create batch
            res = client.post("/api/inventory/batches", json={"name": "API Batch", "groupingMode": "2"})
            self.assertEqual(res.status_code, 201)
            batch = res.get_json()["result"]
            batch_id = batch["id"]

            # 2. List batches
            res = client.get("/api/inventory/batches")
            self.assertEqual(res.status_code, 200)
            self.assertTrue(any(b["id"] == batch_id for b in res.get_json()["result"]))

            # 3. Register photos
            res = client.post(f"/api/inventory/batches/{batch_id}/photos", json={
                "photos": [
                    {"filename": "p1.jpg", "previewUrl": "https://example.com/p1.jpg"},
                    {"filename": "p2.jpg", "previewUrl": "https://example.com/p2.jpg"},
                ]
            })
            self.assertEqual(res.status_code, 201)

            # 4. List batch items
            res = client.get(f"/api/inventory/batches/{batch_id}/items")
            self.assertEqual(res.status_code, 200)
            items = res.get_json()["result"]
            self.assertEqual(len(items), 1)
            item_id = items[0]["id"]

            # 5. Patch item
            res = client.patch(f"/api/inventory/items/{item_id}", json={
                "title": "API Patched Vintage Jacket L",
                "price": "39.99",
                "size": "L",
                "cat": "Jackets",
                "brand": "Vintage",
                "confirmAll": True,
            })
            self.assertEqual(res.status_code, 200)
            self.assertEqual(res.get_json()["result"]["listing"]["title"], "API Patched Vintage Jacket L")

            # 6. Validate item
            res = client.post("/api/inventory/items/validate", json={"itemIds": [item_id]})
            self.assertEqual(res.status_code, 200)
            self.assertTrue(res.get_json()["result"]["results"][0]["passed"])

            # 7. Approve item
            res = client.post("/api/inventory/items/approve", json={"itemIds": [item_id]})
            self.assertEqual(res.status_code, 200)
            self.assertEqual(res.get_json()["result"]["approvedCount"], 1)

            # 8. Submit drafts (mock feed upload)
            with mock.patch("hht_app.inventory_workspace.upload_seller_hub_draft_csv", return_value={"taskId": "feed-99", "status": "IN_PROCESS"}):
                res = client.post("/api/inventory/items/submit-drafts", json={"itemIds": [item_id]})
                self.assertEqual(res.status_code, 200)
                self.assertEqual(res.get_json()["result"]["feedTaskId"], "feed-99")

            # 9. Get submission status
            with mock.patch("hht_app.inventory_workspace.get_feed_task_details", return_value={"status": "COMPLETED"}):
                res = client.get(f"/api/inventory/items/{item_id}/submission")
                self.assertEqual(res.status_code, 200)
                self.assertEqual(res.get_json()["result"]["status"], "completed")

    def test_no_live_ebay_mutations_called(self):
        """Confirm that EBAY_MUTATIONS_ENABLED remains false and live mutation endpoints are forbidden."""
        self.assertFalse(commerce_agent.ebay_mutations_enabled())
        client = hht_app.app.test_client()
        def authenticate():
            # pyrefly: ignore[missing-import]
            from flask import g
            g.supabase_user = {"sub": self.auth_user_id}

        with mock.patch("app.authenticate_request", side_effect=authenticate), \
             mock.patch("app.commerce_agent.ensure_seller_identity", return_value={"id": self.seller_id, "auth_user_id": self.auth_user_id}):
            res = client.post("/api/ebay/drafts", json={"item": {"title": "Test"}})
            self.assertEqual(res.status_code, 403)
            self.assertIn("mutations are disabled", res.get_json()["error"])


if __name__ == "__main__":
    unittest.main()
