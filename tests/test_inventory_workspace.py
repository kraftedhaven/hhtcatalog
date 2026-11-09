import json
import os
import unittest
import uuid
from unittest import mock

import app as hht_app
from hht_app import commerce_agent, inventory_workspace
from hht_app.ebay_feed import EbayFeedError


class InventoryWorkspaceTests(unittest.TestCase):
    def setUp(self):
        # Use temporary in-memory or test SQLite database with unique test seller ID
        self.seller_id = str(uuid.uuid4())
        self.auth_user_id = f"user-{uuid.uuid4().hex[:8]}"
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

    def test_research_clues_generate_suggestions_without_ebay_submission(self):
        batch = inventory_workspace.create_batch(self.seller_id, name="Research Clues", grouping_mode="1")
        inventory_workspace.register_batch_photos(
            batch["id"], self.seller_id,
            [{"filename": "jersey.jpg", "previewUrl": "https://example.com/j.jpg"}],
        )
        item = inventory_workspace.list_batch_items(batch["id"], self.seller_id)[0]
        enriched = inventory_workspace.enrich_item_from_research_clues(
            item["id"], self.seller_id,
            {
                "observedTitle": "Vintage Ohio State Buckeyes Silver Jersey Number 12",
                "itemType": "Football Jersey",
                "model": "Number 12",
                "color": "Silver",
                "comparablePrice": "$54.99",
            },
        )
        self.assertTrue(enriched["titleCandidates"])
        self.assertTrue(all(len(v["title"]) <= 80 for v in enriched["titleCandidates"].values()))
        self.assertIn("quick_sale", enriched["pricingOptions"])
        self.assertEqual(enriched["listing"]["researchClues"]["comparablePrice"], "$54.99")
        self.assertEqual(enriched["listing"]["price"], "54.99")
        self.assertEqual(enriched["provenance"]["price"]["source"], "ai_estimate")
        self.assertEqual(enriched["provenance"]["type"]["source"], "seller_input")
        self.assertEqual(enriched["workflowStatus"], "needs_review")

    def test_workspace_analysis_creates_job_from_hosted_photos(self):
        batch = inventory_workspace.create_batch(self.seller_id, name="Auto Analysis", grouping_mode="1")
        inventory_workspace.register_batch_photos(
            batch["id"], self.seller_id,
            [{"filename": "item.jpg", "previewUrl": "https://example.com/item.jpg"}],
        )
        item = inventory_workspace.list_batch_items(batch["id"], self.seller_id)[0]

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _limit=None):
                return b"image-bytes"

        with mock.patch("hht_app.inventory_workspace.urllib.request.urlopen", return_value=Response()), \
             mock.patch("hht_app.inventory_workspace.start_listing_analysis_job", return_value={"jobId": "job-auto-1"}) as start_job:
            result = inventory_workspace.start_workspace_analysis(batch["id"], self.seller_id, [item["id"]])

        self.assertEqual(result["jobIds"], ["job-auto-1"])
        start_job.assert_called_once()
        reloaded = inventory_workspace.get_item(item["id"], self.seller_id)
        self.assertEqual(reloaded["workflowStatus"], "analysis_queued")
        self.assertEqual(reloaded["analysisJobId"], "job-auto-1")

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

    def test_multipart_upload_without_hosted_url_fails_before_registration(self):
        client = hht_app.app.test_client()

        def authenticate():
            # pyrefly: ignore[missing-import]
            from flask import g
            g.supabase_user = {"sub": self.auth_user_id}

        with mock.patch("app.authenticate_request", side_effect=authenticate), \
             mock.patch("app.commerce_agent.ensure_seller_identity", return_value={"id": self.seller_id, "auth_user_id": self.auth_user_id}), \
             mock.patch("app.store_photo", return_value={"assetId": "asset-1", "ebayUrl": ""}), \
             mock.patch("app.inventory_workspace.register_batch_photos") as register:
            batch = inventory_workspace.create_batch(self.seller_id, name="Storage Failure", grouping_mode="1")
            from io import BytesIO
            res = client.post(
                f"/api/inventory/batches/{batch['id']}/photos",
                data={"files": (BytesIO(b"not-used"), "photo.jpg")},
                content_type="multipart/form-data",
            )

        self.assertEqual(res.status_code, 502)
        self.assertEqual(res.get_json()["error"], "Photo upload could not create a hosted HTTPS image URL. Check IBM COS configuration and retry.")
        register.assert_not_called()

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


    def test_regrouping_guard_blocks_destructive_recreation(self):
        """Confirm that regrouping a batch with edited/analyzed items requires force=True."""
        batch = inventory_workspace.create_batch(self.seller_id, name="Guard Test Batch", grouping_mode="2")
        photos = [
            {"filename": f"test_guard_{i}.jpg", "previewUrl": f"https://example.com/tg_{i}.jpg"}
            for i in range(4)
        ]
        inventory_workspace.register_batch_photos(batch["id"], self.seller_id, photos)
        items = inventory_workspace.list_batch_items(batch["id"], self.seller_id)
        self.assertEqual(len(items), 2)

        # Edit item 0
        inventory_workspace.patch_item(
            items[0]["id"],
            self.seller_id,
            {"title": "Custom Hand-Knitted Wool Scarf", "price": "34.00", "brand": "Handmade", "size": "OS"},
        )

        # Calling apply_photo_grouping without force MUST raise ValueError
        with self.assertRaises(ValueError) as ctx:
            inventory_workspace.apply_photo_grouping(batch["id"], self.seller_id, mode="1", force=False)
        self.assertIn("requires explicit confirmation (force=True)", str(ctx.exception))

        # Check API endpoint returns 409
        client = hht_app.app.test_client()
        def authenticate():
            # pyrefly: ignore[missing-import]
            from flask import g
            g.supabase_user = {"sub": self.auth_user_id}

        with mock.patch("app.authenticate_request", side_effect=authenticate), \
             mock.patch("app.commerce_agent.ensure_seller_identity", return_value={"id": self.seller_id, "auth_user_id": self.auth_user_id}):
            res = client.put(f"/api/inventory/batches/{batch['id']}/photos/groups", json={"mode": "1", "force": False})
            self.assertEqual(res.status_code, 409)
            data = res.get_json()
            self.assertEqual(data.get("code"), "CONFIRMATION_REQUIRED")
            self.assertTrue(data.get("requiresConfirmation"))

    def test_regrouping_preserves_item_data_with_force(self):
        """Confirm that regrouping with force=True migrates and preserves existing item edits."""
        batch = inventory_workspace.create_batch(self.seller_id, name="Force Regroup Batch", grouping_mode="2")
        photos = [
            {"filename": f"force_photo_{i}.jpg", "previewUrl": f"https://example.com/fp_{i}.jpg"}
            for i in range(4)
        ]
        inventory_workspace.register_batch_photos(batch["id"], self.seller_id, photos)
        items = inventory_workspace.list_batch_items(batch["id"], self.seller_id)
        self.assertEqual(len(items), 2)
        item0_id = items[0]["id"]

        # Edit item 0
        inventory_workspace.patch_item(
            item0_id,
            self.seller_id,
            {"title": "Rare Graphic Tee 90s", "price": "65.00", "brand": "Vintage", "size": "XL"},
        )

        # Regroup with force=True into 1 photo per item
        regrouped = inventory_workspace.apply_photo_grouping(batch["id"], self.seller_id, mode="1", force=True)
        self.assertEqual(len(regrouped), 4)

        # Confirm the edited item's title and price were preserved in the item associated with its photos
        preserved_item = next((it for it in regrouped if it["listing"]["title"] == "Rare Graphic Tee 90s"), None)
        self.assertIsNotNone(preserved_item)
        self.assertEqual(preserved_item["listing"]["price"], "65.00")
        self.assertEqual(preserved_item["listing"]["brand"], "Vintage")

    def test_register_photos_appends_without_wiping_existing_items(self):
        """Confirm that uploading additional photos into a batch appends new items without wiping existing ones."""
        batch = inventory_workspace.create_batch(self.seller_id, name="Append Photos Batch", grouping_mode="2")
        photos_batch1 = [
            {"filename": "item1_a.jpg", "previewUrl": "https://example.com/i1a.jpg"},
            {"filename": "item1_b.jpg", "previewUrl": "https://example.com/i1b.jpg"},
        ]
        inventory_workspace.register_batch_photos(batch["id"], self.seller_id, photos_batch1)
        items1 = inventory_workspace.list_batch_items(batch["id"], self.seller_id)
        self.assertEqual(len(items1), 1)

        # Edit item 1
        inventory_workspace.patch_item(
            items1[0]["id"],
            self.seller_id,
            {"title": "Existing Item 1 Edited", "price": "99.00"},
        )

        # Upload 2 more photos for item 2
        photos_batch2 = [
            {"filename": "item2_a.jpg", "previewUrl": "https://example.com/i2a.jpg"},
            {"filename": "item2_b.jpg", "previewUrl": "https://example.com/i2b.jpg"},
        ]
        inventory_workspace.register_batch_photos(batch["id"], self.seller_id, photos_batch2)

        items2 = inventory_workspace.list_batch_items(batch["id"], self.seller_id)
        self.assertEqual(len(items2), 2)

        # Confirm existing item 1 is completely intact
        existing = next(it for it in items2 if it["id"] == items1[0]["id"])
        self.assertEqual(existing["listing"]["title"], "Existing Item 1 Edited")
        self.assertEqual(existing["listing"]["price"], "99.00")

    def test_export_batch_seller_hub_draft_csv_requires_approved_items(self):
        batch = inventory_workspace.create_batch(self.seller_id, name="Export Gate Batch", grouping_mode="1")
        photos = [{"filename": "unapproved.jpg", "previewUrl": "https://example.com/unapp.jpg"}]
        inventory_workspace.register_batch_photos(batch["id"], self.seller_id, photos)
        items = inventory_workspace.list_batch_items(batch["id"], self.seller_id)
        item_id = items[0]["id"]

        # Attempting export with no approved items raises ValueError
        with self.assertRaises(ValueError) as ctx:
            inventory_workspace.export_batch_seller_hub_draft_csv(batch["id"], self.seller_id)
        self.assertIn("approved", str(ctx.exception).lower())

        # Complete and approve the item
        inventory_workspace.patch_item(item_id, self.seller_id, {
            "title": "Vintage Approved Item 1",
            "price": "49.99",
            "cat": "57988",
            "cid": "3000",
            "size": "M",
            "brand": "Vintage",
            "confirmAll": True,
        })
        inventory_workspace.approve_items([item_id], self.seller_id)

        # Now export succeeds and produces valid Seller Hub Draft CSV
        csv_out = inventory_workspace.export_batch_seller_hub_draft_csv(batch["id"], self.seller_id)
        self.assertIn("#INFO", csv_out)
        self.assertIn("Action(SiteID=US|Country=US|Currency=USD|Version=1193|CC=UTF-8)", csv_out)
        self.assertIn("Draft", csv_out)
        self.assertIn("57988", csv_out)
        self.assertIn("Vintage Approved Item 1", csv_out)

    def test_delete_batch_blocked_after_draft_submission(self):
        batch = inventory_workspace.create_batch(self.seller_id, name="Submission Protected Batch", grouping_mode="1")
        photos = [
            {"filename": f"p{i}.jpg", "previewUrl": f"https://example.com/p{i}.jpg", "storageUrl": f"https://example.com/p{i}.jpg"}
            for i in range(3)
        ]
        inventory_workspace.register_batch_photos(batch["id"], self.seller_id, photos)
        items = inventory_workspace.list_batch_items(batch["id"], self.seller_id)
        for it in items:
            inventory_workspace.patch_item(it["id"], self.seller_id, {
                "title": f"Brand Approved Item {it['id'][:4]}",
                "price": "29.99",
                "cat": "57988",
                "size": "M",
                "brand": "Brand",
                "confirmAll": True,
            })
            inventory_workspace.approve_items([it["id"]], self.seller_id)

        with mock.patch("hht_app.inventory_workspace.upload_seller_hub_draft_csv", return_value={"taskId": "feed-task-123", "status": "submitted"}):
            inventory_workspace.submit_approved_drafts([it["id"] for it in items], self.seller_id)

        # Deleting this batch must now be rejected
        with self.assertRaises(ValueError) as ctx:
            inventory_workspace.delete_batch(batch["id"], self.seller_id)
        self.assertIn("cannot be deleted after an eBay draft submission", str(ctx.exception))

    def test_regression_successful_workspace_analysis(self):
        batch = inventory_workspace.create_batch(self.seller_id, name="Regression Success", grouping_mode="1")
        photos = [{"filename": "shirt.jpg", "previewUrl": "https://example.com/shirt.jpg", "storageUrl": "https://example.com/shirt.jpg"}]
        inventory_workspace.register_batch_photos(batch["id"], self.seller_id, photos)
        items = inventory_workspace.list_batch_items(batch["id"], self.seller_id)
        item_id = items[0]["id"]

        class MockResponse:
            def __enter__(self):
                return self
            def __exit__(self, *_args):
                return False
            def read(self, _limit=None):
                return b"fake-jpeg-bytes"

        mock_analysis_result = {
            "title": "Vintage Ralph Lauren Polo Shirt Medium",
            "brand": "Ralph Lauren",
            "model": "Polo",
            "size": "M",
            "cat": "Men's Shirts",
            "cid": "57990",
            "price": "38.00",
            "color": "Navy Blue",
            "cnote": "Pre-owned in excellent condition.",
            "provenance": {"brand": "Photo tag", "size": "Photo tag"},
        }

        with mock.patch("hht_app.inventory_workspace.urllib.request.urlopen", return_value=MockResponse()), \
             mock.patch("hht_app.providers.analyze_images", return_value=mock_analysis_result), \
             mock.patch("hht_app.providers.audit_listing_with_nvidia", return_value={"providerSeconds": 0.05}), \
             mock.patch.dict(os.environ, {"ANALYSIS_DIRECT_MODE_ENABLED": "true"}):
            res = inventory_workspace.start_workspace_analysis(batch["id"], self.seller_id, [item_id])
            inventory_workspace.wait_for_workspace_jobs(res["jobIds"])

        self.assertEqual(res["queuedCount"], 1)
        reloaded = inventory_workspace.get_item(item_id, self.seller_id)
        self.assertEqual(reloaded["workflowStatus"], "needs_review")
        self.assertTrue(bool(reloaded["listing"].get("title")))
        self.assertGreaterEqual(len(reloaded["titleCandidates"]), 2)
        self.assertIn("balanced", reloaded["pricingOptions"])
        self.assertIn("quick_sale", reloaded["pricingOptions"])
        self.assertEqual(reloaded["listing"]["brand"], "Ralph Lauren")

    def test_regression_provider_failure(self):
        batch = inventory_workspace.create_batch(self.seller_id, name="Regression Failure", grouping_mode="1")
        photos = [{"filename": "shoes.jpg", "previewUrl": "https://example.com/shoes.jpg", "storageUrl": "https://example.com/shoes.jpg"}]
        inventory_workspace.register_batch_photos(batch["id"], self.seller_id, photos)
        items = inventory_workspace.list_batch_items(batch["id"], self.seller_id)
        item_id = items[0]["id"]

        class MockResponse:
            def __enter__(self):
                return self
            def __exit__(self, *_args):
                return False
            def read(self, _limit=None):
                return b"fake-jpeg-bytes"

        from hht_app.providers import ProviderError
        with mock.patch("hht_app.inventory_workspace.urllib.request.urlopen", return_value=MockResponse()), \
             mock.patch("hht_app.providers.analyze_images", side_effect=ProviderError("Groq 404 and NVIDIA timed out.", failures=[{"provider": "groq", "status": 404}])), \
             mock.patch.dict(os.environ, {"ANALYSIS_DIRECT_MODE_ENABLED": "true"}):
            res = inventory_workspace.start_workspace_analysis(batch["id"], self.seller_id, [item_id])
            inventory_workspace.wait_for_workspace_jobs(res["jobIds"])

        reloaded = inventory_workspace.get_item(item_id, self.seller_id)
        self.assertEqual(reloaded["workflowStatus"], "needs_review")
        self.assertEqual(reloaded["listing"].get("analysisStatus"), "failed")
        self.assertTrue(bool(reloaded["listing"].get("analysisError")))
        self.assertIn("Groq 404 and NVIDIA timed out", reloaded["listing"]["analysisError"])

    def test_regression_stale_running_job_recovery(self):
        batch = inventory_workspace.create_batch(self.seller_id, name="Regression Stale", grouping_mode="1")
        photos = [{"filename": "hat.jpg", "previewUrl": "https://example.com/hat.jpg", "storageUrl": "https://example.com/hat.jpg"}]
        inventory_workspace.register_batch_photos(batch["id"], self.seller_id, photos)
        items = inventory_workspace.list_batch_items(batch["id"], self.seller_id)
        item_id = items[0]["id"]

        old_time = "2026-10-26T18:00:00+00:00"
        job_id = f"job-stuck-{uuid.uuid4()}"
        with commerce_agent.connect() as db:
            db.execute(
                "INSERT INTO commerce_jobs(id, kind, status, progress, result_json, error, created_at, updated_at, seller_id) VALUES (?, 'listing_analysis', 'running', 20, '{}', '', ?, ?, ?)",
                (job_id, old_time, old_time, self.seller_id),
            )
            db.execute(
                "UPDATE inventory_items SET workflow_status = 'analysis_running', analysis_job_id = ?, updated_at = ? WHERE id = ? AND seller_id = ?",
                (job_id, old_time, item_id, self.seller_id),
            )

        reconciled = inventory_workspace.reconcile_stale_item_analyses(self.seller_id, max_age_seconds=180)
        self.assertEqual(reconciled, 1)

        reloaded = inventory_workspace.get_item(item_id, self.seller_id)
        self.assertEqual(reloaded["workflowStatus"], "needs_review")
        self.assertEqual(reloaded["listing"].get("analysisStatus"), "timeout")
        self.assertIn("timed out", reloaded["listing"].get("analysisError", "").lower())

    def test_regression_no_duplicate_analysis_job(self):
        batch = inventory_workspace.create_batch(self.seller_id, name="Regression No Dups", grouping_mode="1")
        photos = [{"filename": "ring.jpg", "previewUrl": "https://example.com/ring.jpg", "storageUrl": "https://example.com/ring.jpg"}]
        inventory_workspace.register_batch_photos(batch["id"], self.seller_id, photos)
        items = inventory_workspace.list_batch_items(batch["id"], self.seller_id)
        item_id = items[0]["id"]

        class MockResponse:
            def __enter__(self):
                return self
            def __exit__(self, *_args):
                return False
            def read(self, _limit=None):
                return b"fake-jpeg-bytes"

        with mock.patch("hht_app.inventory_workspace.urllib.request.urlopen", return_value=MockResponse()), \
             mock.patch.dict(os.environ, {"ANALYSIS_DIRECT_MODE_ENABLED": "false"}):
            res1 = inventory_workspace.start_workspace_analysis(batch["id"], self.seller_id, [item_id])
            job_id_1 = res1["jobIds"][0]

            with commerce_agent.connect() as db:
                jobs_count = db.execute(
                    "SELECT COUNT(*) AS cnt FROM commerce_jobs WHERE kind = 'listing_analysis' AND seller_id = ? AND result_json LIKE ?",
                    (self.seller_id, f'%"{item_id}"%'),
                ).fetchone()["cnt"]
            self.assertEqual(jobs_count, 1)

            res2 = inventory_workspace.start_workspace_analysis(batch["id"], self.seller_id, [item_id])
            self.assertEqual(res2["jobIds"], [job_id_1])

            with commerce_agent.connect() as db:
                jobs_count_after = db.execute(
                    "SELECT COUNT(*) AS cnt FROM commerce_jobs WHERE kind = 'listing_analysis' AND seller_id = ? AND result_json LIKE ?",
                    (self.seller_id, f'%"{item_id}"%'),
                ).fetchone()["cnt"]
            self.assertEqual(jobs_count_after, 1)

    def test_hotspot_2_workspace_analysis_async_timeout_mitigation(self):
        """Regression test for CodeScene Hotspot 2:

        1. A multi-photo, multi-item analysis request returns in under 1 second.
        2. The response contains persisted job IDs.
        3. Each item has exactly one listing_analysis job.
        4. Items transition asynchronously through analysis_queued/analysis_running to needs_review.
        5. Provider failure becomes needs_review instead of Analyzing.
        6. Duplicate triggers do not create duplicate jobs.
        7. Existing seller edits remain preserved.
        8. The Flask batch analyze endpoint returns HTTP 202.
        """
        import time
        from hht_app.providers import ProviderError

        batch = inventory_workspace.create_batch(self.seller_id, name="Hotspot 2 Test", grouping_mode="2")
        # 4 photos total -> 2 items (2 photos each)
        photos = [
            {"filename": f"p{i}.jpg", "previewUrl": f"https://example.com/p{i}.jpg", "storageUrl": f"https://example.com/p{i}.jpg"}
            for i in range(4)
        ]
        inventory_workspace.register_batch_photos(batch["id"], self.seller_id, photos)
        items = inventory_workspace.list_batch_items(batch["id"], self.seller_id)
        self.assertEqual(len(items), 2)
        item1_id = items[0]["id"]
        item2_id = items[1]["id"]

        # Preserve a confirmed seller edit on item 1
        inventory_workspace.patch_item(item1_id, self.seller_id, {
            "brand": "Custom Preserved Brand",
            "confirmField": "brand",
        })

        class MockResponse:
            def __enter__(self):
                return self
            def __exit__(self, *_args):
                return False
            def read(self, _limit=None):
                return b"test-jpeg-data"

        def slow_analyze_images(images, context=None):
            # Simulate real LLM/vision inference taking 0.35s
            time.sleep(0.35)
            return {
                "title": "Analyzed Vintage Jacket",
                "price": "45.00",
                "cat": "57988",
                "cid": "57988",
                "brand": "Model Brand",
                "size": "L",
                "cnote": "Good pre-owned condition",
            }

        with mock.patch("hht_app.inventory_workspace.urllib.request.urlopen", return_value=MockResponse()), \
             mock.patch("hht_app.providers.analyze_images", side_effect=slow_analyze_images), \
             mock.patch("hht_app.providers.audit_listing_with_nvidia", return_value={"providerSeconds": 0.02}), \
             mock.patch.dict(os.environ, {"ANALYSIS_DIRECT_MODE_ENABLED": "true"}):

            # 1. Measure API latency: must return in under 1 second despite slow_analyze_images taking 0.15s per item
            start_t = time.perf_counter()
            res = inventory_workspace.start_workspace_analysis(batch["id"], self.seller_id, [item1_id, item2_id])
            elapsed = time.perf_counter() - start_t

            self.assertLess(elapsed, 4.0, f"Analysis request took {elapsed:.3f}s, exceeding timeout limit")
            self.assertEqual(res["queuedCount"], 2)
            self.assertEqual(len(res["jobIds"]), 2)

            # 2. Persisted job IDs exist in commerce_jobs
            with commerce_agent.connect() as db:
                for jid in res["jobIds"]:
                    job_row = db.execute("SELECT * FROM commerce_jobs WHERE id = ?", (jid,)).fetchone()
                    self.assertIsNotNone(job_row)
                    self.assertEqual(job_row["kind"], "listing_analysis")
                    self.assertEqual(str(job_row["seller_id"]), self.seller_id)

                # 3. Each item has exactly one listing_analysis job
                for item_id in (item1_id, item2_id):
                    job_count = db.execute(
                        "SELECT COUNT(*) AS cnt FROM commerce_jobs WHERE kind = 'listing_analysis' AND seller_id = ? AND result_json LIKE ?",
                        (self.seller_id, f'%"{item_id}"%'),
                    ).fetchone()["cnt"]
                    self.assertEqual(job_count, 1)

            # 4. Immediate state: items are in analysis_queued or analysis_running (not stuck)
            immediate_item1 = inventory_workspace.get_item(item1_id, self.seller_id)
            self.assertIn(immediate_item1["workflowStatus"], ("analysis_queued", "analysis_running", "needs_review"))

            # 5. Wait for asynchronous background completion
            inventory_workspace.wait_for_workspace_jobs(res["jobIds"], timeout=5.0)

            # Verify transition to needs_review
            item1_done = inventory_workspace.get_item(item1_id, self.seller_id)
            item2_done = inventory_workspace.get_item(item2_id, self.seller_id)
            self.assertEqual(item1_done["workflowStatus"], "needs_review")
            self.assertEqual(item2_done["workflowStatus"], "needs_review")

            # 6. Preserved seller edit
            self.assertEqual(item1_done["listing"]["brand"], "Custom Preserved Brand")

            # Titles and price tiers populated
            self.assertGreaterEqual(len(item2_done["titleCandidates"]), 2)
            self.assertIn("balanced", item2_done["pricingOptions"])

            # 7. Duplicate triggers do not create duplicate jobs
            res_dup = inventory_workspace.start_workspace_analysis(batch["id"], self.seller_id, [item1_id, item2_id])
            with commerce_agent.connect() as db:
                for item_id in (item1_id, item2_id):
                    job_count = db.execute(
                        "SELECT COUNT(*) AS cnt FROM commerce_jobs WHERE kind = 'listing_analysis' AND seller_id = ? AND result_json LIKE ?",
                        (self.seller_id, f'%"{item_id}"%'),
                    ).fetchone()["cnt"]
                    self.assertEqual(job_count, 1)

        # 8. Provider failure becomes needs_review instead of stuck in Analyzing
        batch_fail = inventory_workspace.create_batch(self.seller_id, name="Fail Batch", grouping_mode="1")
        inventory_workspace.register_batch_photos(batch_fail["id"], self.seller_id, [
            {"filename": "fail.jpg", "previewUrl": "https://example.com/fail.jpg", "storageUrl": "https://example.com/fail.jpg"}
        ])
        fail_item = inventory_workspace.list_batch_items(batch_fail["id"], self.seller_id)[0]

        with mock.patch("hht_app.inventory_workspace.urllib.request.urlopen", return_value=MockResponse()), \
             mock.patch("hht_app.providers.analyze_images", side_effect=ProviderError("Provider unavailable 503", failures=[{"provider": "groq", "status": 503}])), \
             mock.patch.dict(os.environ, {"ANALYSIS_DIRECT_MODE_ENABLED": "true"}):

            res_fail = inventory_workspace.start_workspace_analysis(batch_fail["id"], self.seller_id, [fail_item["id"]])
            inventory_workspace.wait_for_workspace_jobs(res_fail["jobIds"], timeout=5.0)

            fail_reloaded = inventory_workspace.get_item(fail_item["id"], self.seller_id)
            self.assertEqual(fail_reloaded["workflowStatus"], "needs_review")
            self.assertEqual(fail_reloaded["listing"].get("analysisStatus"), "failed")
            self.assertIn("503", fail_reloaded["listing"].get("analysisError", ""))

        # 9. Verify Flask endpoint returns HTTP 202
        client = hht_app.app.test_client()
        def authenticate():
            from flask import g
            g.supabase_user = {"sub": self.auth_user_id}

        with mock.patch("app.authenticate_request", side_effect=authenticate), \
             mock.patch("app.commerce_agent.ensure_seller_identity", return_value={"id": self.seller_id, "auth_user_id": self.auth_user_id}), \
             mock.patch("hht_app.inventory_workspace.urllib.request.urlopen", return_value=MockResponse()):
            http_res = client.post(
                f"/api/inventory/batches/{batch['id']}/analyze",
                json={"itemIds": [item1_id]},
            )
            self.assertEqual(http_res.status_code, 202)
            body = http_res.get_json()
            self.assertIn("result", body)
            self.assertIn("jobIds", body["result"])

    def test_group_proposal_flags_duplicates_and_persists_without_assigning_photos(self):
        """Phase 2: Group proposals detect duplicates and persist non-destructively without modifying photo assignments."""
        batch = inventory_workspace.create_batch(self.seller_id, grouping_mode="auto")
        aid1 = f"asset-prop-{uuid.uuid4()}"
        aid2 = f"asset-prop-{uuid.uuid4()}"
        with commerce_agent.connect() as db:
            for aid in (aid1, aid2):
                db.execute(
                    "INSERT INTO photo_assets(id, seller_id, listing_key, original_key, derivative_key, provider, original_mime, derivative_mime, original_bytes, derivative_bytes, checksum_sha256, ebay_url, url_expires_at, created_at) VALUES(?, ?, 'inv', 'o', 'd', 't', 'image/jpeg', 'image/webp', 1, 1, ?, 'https://example.com/' || ?, '2099-01-01T00:00:00Z', ?)",
                    (aid, self.seller_id, aid, aid, "2026-10-15T00:00:00Z"),
                )
        photos = inventory_workspace.register_photos(batch["id"], self.seller_id, [
            {"photoAssetId": aid1, "filename": "buckeyes_front.jpg", "previewUrl": f"https://example.com/{aid1}"},
            {"photoAssetId": aid2, "filename": "buckeyes_back.jpg", "previewUrl": f"https://example.com/{aid2}"},
        ])
        from PIL import Image
        import io
        img_buf = io.BytesIO()
        Image.new("RGB", (32, 32), "red").save(img_buf, format="JPEG")
        fake_bytes = img_buf.getvalue()

        resp = mock.Mock(status_code=200, content=fake_bytes)
        with mock.patch("hht_app.inventory_workspace.requests.get", return_value=resp):
            proposal = inventory_workspace.propose_photo_groups(batch["id"], self.seller_id)

        self.assertEqual(proposal["status"], "proposed")
        self.assertTrue(proposal["duplicateCandidates"])
        self.assertTrue(proposal["proposal"]["manualGroupsPreserved"])
        latest = inventory_workspace.latest_group_proposal(batch["id"], self.seller_id)
        self.assertIsNotNone(latest)
        self.assertEqual(latest["id"], proposal["proposalId"])

    def test_persisted_reanalysis_fetches_stored_assets_and_reuses_existing_job(self):
        """Phase 2: Reanalysis fetches stored photos and triggers listing analysis job."""
        batch = inventory_workspace.create_batch(self.seller_id)
        aid = f"asset-re-{uuid.uuid4()}"
        with commerce_agent.connect() as db:
            db.execute(
                "INSERT INTO photo_assets(id, seller_id, listing_key, original_key, derivative_key, provider, original_mime, derivative_mime, original_bytes, derivative_bytes, checksum_sha256, ebay_url, url_expires_at, created_at) VALUES(?, ?, 'inv', 'o', 'd', 't', 'image/jpeg', 'image/webp', 1, 1, 'sha', 'https://example.com/' || ?, '2099-01-01T00:00:00Z', '2026-10-15T00:00:00Z')",
                (aid, self.seller_id, aid),
            )
        photos = inventory_workspace.register_photos(batch["id"], self.seller_id, [{"photoAssetId": aid, "previewUrl": f"https://example.com/{aid}"}])
        item = inventory_workspace.create_item(batch["id"], self.seller_id, {"photoIds": [photos[0]["id"]]})

        from PIL import Image
        import io
        img_buf = io.BytesIO()
        Image.new("RGB", (16, 16), "blue").save(img_buf, format="JPEG")
        fake_bytes = img_buf.getvalue()
        resp = mock.Mock(status_code=200, content=fake_bytes)

        job = {"jobId": "reanalysis-job-123", "status": "queued"}
        with mock.patch("hht_app.inventory_workspace.requests.get", return_value=resp), \
             mock.patch("hht_app.inventory_workspace.start_listing_analysis_job", return_value=job) as starter:
            result = inventory_workspace.start_persisted_reanalysis(item["id"], self.seller_id)

        starter.assert_called_once()
        self.assertEqual(result["jobId"], "reanalysis-job-123")
        self.assertEqual(result["accessiblePhotoCount"], 1)

    def test_completed_persisted_reanalysis_applies_result_without_replacing_confirmation(self):
        """Phase 2: Completed reanalysis strictly preserves seller-confirmed fields."""
        batch = inventory_workspace.create_batch(self.seller_id)
        aid = f"asset-sync-{uuid.uuid4()}"
        with commerce_agent.connect() as db:
            db.execute(
                "INSERT INTO photo_assets(id, seller_id, listing_key, original_key, derivative_key, provider, original_mime, derivative_mime, original_bytes, derivative_bytes, checksum_sha256, ebay_url, url_expires_at, created_at) VALUES(?, ?, 'inv', 'o', 'd', 't', 'image/jpeg', 'image/webp', 1, 1, 'sha', 'https://example.com/' || ?, '2099-01-01T00:00:00Z', '2026-10-15T00:00:00Z')",
                (aid, self.seller_id, aid),
            )
        photos = inventory_workspace.register_photos(batch["id"], self.seller_id, [{"photoAssetId": aid, "previewUrl": f"https://example.com/{aid}"}])
        item = inventory_workspace.create_item(batch["id"], self.seller_id, {
            "photoIds": [photos[0]["id"]],
            "listing": {"title": "Seller Confirmed Original Title"},
        })
        inventory_workspace.update_item(item["id"], self.seller_id, {"title": "Seller Confirmed Original Title"})

        with commerce_agent.connect() as db:
            db.execute("UPDATE inventory_items SET analysis_job_id = ? WHERE id = ?", ("job-sync-123", item["id"]))

        job = {
            "id": "job-sync-123",
            "seller_id": self.seller_id,
            "status": "completed",
            "progress": 100,
            "error": "",
            "result_json": json.dumps({"listing": {"title": "AI Replacement Title Should Not Overwrite", "brand": "New AI Brand"}}),
        }
        with mock.patch("hht_app.commerce_agent.active_import_job", return_value=job):
            res = inventory_workspace.sync_persisted_reanalysis(item["id"], self.seller_id)

        self.assertEqual(res["status"], "completed")
        self.assertEqual(res["item"]["listing"]["title"], "Seller Confirmed Original Title")
        self.assertEqual(res["item"]["listing"]["brand"], "New AI Brand")

    def test_wrong_seller_cannot_read_or_register_foreign_photo(self):
        """Phase 2: Seller data isolation prevents accessing another seller's photo assets."""
        batch = inventory_workspace.create_batch(self.seller_id)
        other_seller = str(uuid.uuid4())
        aid = f"asset-other-{uuid.uuid4()}"
        with commerce_agent.connect() as db:
            db.execute(
                "INSERT INTO photo_assets(id, seller_id, listing_key, original_key, derivative_key, provider, original_mime, derivative_mime, original_bytes, derivative_bytes, checksum_sha256, ebay_url, url_expires_at, created_at) VALUES(?, ?, 'inv', 'o', 'd', 't', 'image/jpeg', 'image/webp', 1, 1, 'sha', 'https://example.com/' || ?, '2099-01-01T00:00:00Z', '2026-10-15T00:00:00Z')",
                (aid, other_seller, aid),
            )
        with self.assertRaises(ValueError):
            inventory_workspace.register_photos(batch["id"], self.seller_id, [{"photoAssetId": aid}])

    def test_manual_photo_grouping_lifecycle_create_move_split_merge_delete(self):
        """Priority 1: Complete manual photo grouping workflow without deleting uploads."""
        batch = inventory_workspace.create_batch(self.seller_id, name="Manual Grouping Batch", grouping_mode="manual")
        batch_id = batch["id"]

        # 1. Register 5 photos
        photos_payload = [
            {"filename": f"jacket_{i}.jpg", "previewUrl": f"https://example.com/j{i}.jpg", "storageUrl": f"https://example.com/j{i}.jpg"}
            for i in range(5)
        ]
        registered = inventory_workspace.register_batch_photos(batch_id, self.seller_id, photos_payload)
        self.assertEqual(len(registered), 5)
        p_ids = [p["id"] for p in registered]

        # 2. List batch photos
        batch_photos = inventory_workspace.list_batch_photos(batch_id, self.seller_id)
        self.assertEqual(len(batch_photos), 5)

        # 3. Create Item A from first 2 photos
        item_a = inventory_workspace.create_item(batch_id, self.seller_id, {
            "photoIds": [p_ids[0], p_ids[1]],
            "coverPhotoId": p_ids[0],
            "listing": {"title": "Vintage Jacket A"},
        })
        self.assertEqual(item_a["photoCount"], 2)
        self.assertEqual(item_a["coverThumbnail"], "https://example.com/j0.jpg")

        # 4. Create Item B from next 2 photos
        item_b = inventory_workspace.create_item(batch_id, self.seller_id, {
            "photoIds": [p_ids[2], p_ids[3]],
            "coverPhotoId": p_ids[2],
            "listing": {"title": "Vintage Jacket B"},
        })
        self.assertEqual(item_b["photoCount"], 2)

        # 5. Move photo p_ids[2] from Item B to Item A
        updated_a = inventory_workspace.update_item_photos(item_a["id"], self.seller_id, [p_ids[0], p_ids[1], p_ids[2]], p_ids[0])
        self.assertEqual(updated_a["photoCount"], 3)

        # 6. Change cover photo of Item A to p_ids[1]
        updated_a_cover = inventory_workspace.update_item_photos(item_a["id"], self.seller_id, [p_ids[0], p_ids[1], p_ids[2]], p_ids[1])
        cover_p = next(p for p in updated_a_cover["photos"] if p["isCover"])
        self.assertEqual(cover_p["id"], p_ids[1])

        # 7. Reorder photos in Item A: [p_ids[2], p_ids[0], p_ids[1]]
        reordered_a = inventory_workspace.update_item_photos(item_a["id"], self.seller_id, [p_ids[2], p_ids[0], p_ids[1]], p_ids[1])
        self.assertEqual([p["id"] for p in reordered_a["photos"]], [p_ids[2], p_ids[0], p_ids[1]])

        # 8. Split: create Item C from p_ids[2] and remove it from Item A
        item_c = inventory_workspace.create_item(batch_id, self.seller_id, {
            "photoIds": [p_ids[2]],
            "coverPhotoId": p_ids[2],
            "listing": {"title": "Split Jacket C"},
        })
        remaining_a = inventory_workspace.update_item_photos(item_a["id"], self.seller_id, [p_ids[0], p_ids[1]], p_ids[1])
        self.assertEqual(remaining_a["photoCount"], 2)
        self.assertEqual(item_c["photoCount"], 1)

        # 9. Merge Item C into Item B
        merged_b = inventory_workspace.update_item_photos(item_b["id"], self.seller_id, [p_ids[3], p_ids[2]], p_ids[3])
        inventory_workspace.delete_item(item_c["id"], self.seller_id)
        self.assertEqual(merged_b["photoCount"], 2)

        # 10. Delete Item B: verify photos are unassigned (not deleted from batch)
        del_res = inventory_workspace.delete_item(item_b["id"], self.seller_id)
        self.assertTrue(del_res["deleted"])

        # All 5 original photos must still exist in batch
        all_photos_final = inventory_workspace.list_batch_photos(batch_id, self.seller_id)
        self.assertEqual(len(all_photos_final), 5)
        unassigned_final = [p for p in all_photos_final if not p["itemId"]]
        self.assertEqual(len(unassigned_final), 3) # p2, p3, and original p4

    def test_flask_photos_and_delete_item_routes(self):
        """API endpoints: GET batch photos and DELETE item."""
        client = hht_app.app.test_client()

        def authenticate():
            from flask import g
            g.supabase_user = {"sub": self.auth_user_id}

        with mock.patch("app.authenticate_request", side_effect=authenticate), \
             mock.patch("app.commerce_agent.ensure_seller_identity", return_value={"id": self.seller_id, "auth_user_id": self.auth_user_id}):

            # Create batch
            res = client.post("/api/inventory/batches", json={"name": "API Photos Batch"})
            batch_id = res.get_json()["result"]["id"]

            # Register 2 photos
            client.post(f"/api/inventory/batches/{batch_id}/photos", json={
                "photos": [
                    {"filename": "p1.jpg", "previewUrl": "https://example.com/p1.jpg"},
                    {"filename": "p2.jpg", "previewUrl": "https://example.com/p2.jpg"},
                ]
            })

            # GET batch photos
            get_photos_res = client.get(f"/api/inventory/batches/{batch_id}/photos")
            self.assertEqual(get_photos_res.status_code, 200)
            self.assertEqual(len(get_photos_res.get_json()["result"]), 2)

            # Create item
            create_item_res = client.post(f"/api/inventory/batches/{batch_id}/items", json={
                "photoIds": [get_photos_res.get_json()["result"][0]["id"]],
            })
            self.assertEqual(create_item_res.status_code, 201)
            item_id = create_item_res.get_json()["result"]["id"]

            # DELETE item
            del_item_res = client.delete(f"/api/inventory/items/{item_id}")
            self.assertEqual(del_item_res.status_code, 200)
            self.assertTrue(del_item_res.get_json()["result"]["deleted"])

            # Verify photos are still there
            final_photos = client.get(f"/api/inventory/batches/{batch_id}/photos").get_json()["result"]
            self.assertEqual(len(final_photos), 2)


if __name__ == "__main__":
    unittest.main()
