import os
import tempfile
import unittest
from unittest import mock

from hht_app import commerce_agent


class CommerceAgentTests(unittest.TestCase):
    def setUp(self):
        self.db_file = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False)
        self.db_file.close()
        self.old_db = os.environ.get("COMMERCE_AGENT_DB")
        os.environ["COMMERCE_AGENT_DB"] = self.db_file.name
        commerce_agent.init_db()
        with commerce_agent.connect() as db:
            db.execute(
                "INSERT INTO listings(listing_id, offer_id, sku, marketplace, data_json, imported_at) VALUES(?,?,?,?,?,?)",
                ("L1", "O1", "SKU1", "EBAY_US", commerce_agent._json({
                    "listingId": "L1", "offerId": "O1", "sku": "SKU1", "title": "Short Coat", "price": 100,
                    "desc": "", "cat": "57988", "brand": "Brand", "size": "M", "color": "Blue", "mat": "",
                    "cnote": "", "pic": ""
                }), commerce_agent.utc_now()),
            )

    def tearDown(self):
        try:
            os.unlink(self.db_file.name)
        except FileNotFoundError:
            pass
        if self.old_db is None:
            os.environ.pop("COMMERCE_AGENT_DB", None)
        else:
            os.environ["COMMERCE_AGENT_DB"] = self.old_db

    def test_audit_creates_structured_recommendation(self):
        result = commerce_agent.audit_all()
        self.assertEqual(result["count"], 1)
        recommendation = commerce_agent.recommendations()[0]
        self.assertIn(recommendation["classification"], {"Needs Optimization", "High Priority", "Needs Review"})
        self.assertIsInstance(recommendation["findings"], list)
        self.assertEqual(recommendation["status"], "Pending")

    def test_short_title_gets_non_noop_candidate(self):
        audit = commerce_agent.audit_listing({"title": "Brown Signature Handbag", "brand": "Coach", "type": "Handbag"})
        self.assertIn("Coach", audit["proposed"]["title"])
        self.assertNotEqual(audit["proposed"]["title"].casefold(), "brown signature handbag")

    def test_audit_marks_note_only_findings_as_review_only(self):
        audit = commerce_agent.audit_listing({
            "title": "Complete Seller Reviewed Patagonia Fleece Jacket Blue Mens Medium",
            "price": 42,
            "desc": "This seller-reviewed description has enough buyer-facing detail and condition context for the listing.",
            "cat": "57988",
            "brand": "Patagonia",
            "size": "M",
            "color": "Blue",
            "mat": "Polyester",
            "cnote": "Pre-owned with light wear.",
            "pic": "",
        })
        self.assertEqual(audit["proposed"], {})
        self.assertEqual(audit["classification"], "Needs Review")
        self.assertEqual(audit["risk"], "high")
        self.assertEqual(audit["confidence"], "low")

    def test_sold_comparable_summary_creates_price_proposal(self):
        audit = commerce_agent.audit_listing({
            "title": "Coach Willow Leather Handbag Brown Large",
            "price": 129.99,
            "desc": "Seller-reviewed bag with visible exterior, interior, and condition notes for buyer review.",
            "cat": "169291",
            "brand": "Coach",
            "model": "Willow",
            "size": "Large",
            "color": "Brown",
            "mat": "Leather",
            "type": "Handbag",
            "cnote": "Pre-owned with light wear.",
            "pic": "https://example.test/photo.jpg",
            "soldComparableSummary": {
                "status": "ok",
                "pricingSource": "exact_used_sold",
                "medianSoldPrice": 99.99,
                "lowSoldPrice": 89.99,
                "highSoldPrice": 109.99,
                "sampleSize": 4,
                "query": "Coach Willow handbag",
            },
        })
        self.assertEqual(audit["soldPricing"]["pricingSource"], "exact_used_sold")
        self.assertEqual(audit["proposed"]["price"], 99.99)

    def test_price_safety_rejects_reduction_over_default_cap(self):
        commerce_agent.audit_all()
        recommendation = commerce_agent.recommendations()[0]
        with self.assertRaisesRegex(ValueError, "safety rules"):
            commerce_agent.approve_recommendation(recommendation["recommendationId"], {"price": 80})

    def test_approval_is_separate_from_apply(self):
        commerce_agent.audit_all()
        recommendation = commerce_agent.recommendations()[0]
        approved = commerce_agent.approve_recommendation(recommendation["recommendationId"], {"title": "Brand Short Coat"})
        self.assertEqual(approved["status"], "Approved")
        self.assertEqual(commerce_agent.recommendations()[0]["status"], "Approved")
        self.assertEqual(commerce_agent.history()[0]["status"], "Approved")

    def test_explain_and_skip_are_stored_review_decisions(self):
        commerce_agent.audit_all()
        recommendation = commerce_agent.recommendations()[0]
        explanation = commerce_agent.explain_recommendation(recommendation["recommendationId"])
        self.assertIn("reason", explanation)
        self.assertIn("evidence", explanation)
        skipped = commerce_agent.set_recommendation_status(recommendation["recommendationId"], "Skipped")
        self.assertEqual(skipped["status"], "Skipped")

    def test_bulk_approval_never_calls_ebay(self):
        commerce_agent.audit_all()
        recommendation = commerce_agent.recommendations()[0]
        with mock.patch.object(commerce_agent, "update_ebay_offer") as update:
            result = commerce_agent.bulk_approve([recommendation["recommendationId"]])
        update.assert_not_called()
        self.assertEqual(result["approved"], 1)

    def test_applied_action_is_marked_rollback_eligible(self):
        commerce_agent.audit_all()
        recommendation = commerce_agent.recommendations()[0]
        approved = commerce_agent.approve_recommendation(recommendation["recommendationId"], {"title": "Brand Short Coat"})
        with mock.patch.object(commerce_agent, "update_ebay_offer", return_value={"status": "offer_updated"}):
            commerce_agent.apply_action(approved["actionId"])
        entry = commerce_agent.history()[0]
        self.assertTrue(entry["rollbackEligible"])

    def test_approval_accepts_condition_and_notes_fields(self):
        commerce_agent.audit_all()
        recommendation = commerce_agent.recommendations()[0]
        approved = commerce_agent.approve_recommendation(
            recommendation["recommendationId"],
            {"cid": "4000", "notes": "Seller reviewed category and condition."},
        )
        self.assertEqual(approved["approved"]["cid"], "4000")
        self.assertEqual(approved["approved"]["notes"], "Seller reviewed category and condition.")

    def test_dashboard_counts_imported_records_and_recommendations(self):
        commerce_agent.audit_all()
        dashboard = commerce_agent.dashboard()
        self.assertEqual(dashboard["listingsFound"], 1)
        self.assertEqual(dashboard["recommendations"], 1)
        self.assertEqual(dashboard["mode"], "recommend")
        self.assertEqual(dashboard["recovery"]["totalActiveListingsImported"], 1)
        self.assertEqual(dashboard["recovery"]["label"], "Operational catalog metrics only")

    def test_count_listings_returns_integer(self):
        self.assertEqual(commerce_agent.count_listings(), 1)

    def test_inventory_lifecycle_is_explicit(self):
        base = {"sku": "SKU-X", "product": {"title": "Brown Signature Handbag"}}
        active = commerce_agent._inventory_to_listing(base, {"offerId": "O1", "listingId": "L1", "status": "PUBLISHED"})
        draft = commerce_agent._inventory_to_listing(base, {"offerId": "O2", "status": "UNPUBLISHED"})
        inventory_only = commerce_agent._inventory_to_listing(base, {})
        self.assertEqual(active["lifecycle"], "Active listing")
        self.assertEqual(draft["lifecycle"], "Unpublished offer")
        self.assertEqual(inventory_only["lifecycle"], "Inventory-only draft")
        self.assertEqual(active["status"], "active")
        self.assertEqual(inventory_only["status"], "draft")
        self.assertEqual(active["ebayUrl"], "https://www.ebay.com/itm/L1")

    def test_inventory_import_skips_inventory_only_records_by_default(self):
        responses = [
            {"inventoryItems": [{"sku": "INV-ONLY", "product": {"title": "Inventory Draft"}}]},
            {"offers": []},
        ]

        with mock.patch.object(commerce_agent, "_ebay_get", side_effect=responses):
            result = commerce_agent.import_listings()

        self.assertEqual(result["imported"], 0)
        self.assertEqual(result["skippedInventoryOnly"], 1)
        self.assertEqual(commerce_agent.count_listings(), 1)

    def test_active_import_paginates_over_500_plus_and_deduplicates(self):
        pages = {}
        for page in range(1, 4):
            start = (page - 1) * 200
            end = 550 if page == 3 else page * 200
            pages[page] = {
                "items": [
                    {
                        "listingId": f"L{index}",
                        "sku": f"SKU{index}",
                        "title": f"Active Item {index}",
                        "price": 19.99,
                        "cat": "57988",
                        "quantity": 1,
                    }
                    for index in range(start, end)
                ],
                "totalEntries": 550,
                "totalPages": 3,
            }
        pages[2]["items"].append({**pages[1]["items"][0], "title": "Duplicate Active Item"})

        with mock.patch.object(commerce_agent, "fetch_active_listings", side_effect=lambda page=1: pages[page]) as fetch:
            result = commerce_agent.import_active_listings()

        self.assertEqual(fetch.call_count, 3)
        self.assertEqual(result["totalEntries"], 550)
        self.assertEqual(commerce_agent.count_listings(), 550)
        stored = [item for item in commerce_agent.list_listings() if item["sku"] == "SKU0"][0]
        self.assertEqual(stored["title"], "Duplicate Active Item")

    def test_active_import_marks_absent_active_record_inactive_without_ebay_mutation(self):
        with commerce_agent.connect() as db:
            db.execute(
                "INSERT INTO listings(listing_id,offer_id,sku,marketplace,data_json,imported_at) VALUES(?,?,?,?,?,?)",
                ("STALE", "", "STALE-SKU", "EBAY_US", commerce_agent._json({
                    "listingId": "STALE", "sku": "STALE-SKU", "title": "No longer active", "status": "active", "source": "trading_active", "activeSource": "trading_active"
                }), commerce_agent.utc_now()),
            )
        page = {"items": [{"listingId": "L1", "sku": "SKU1", "title": "Current Active Item", "price": 19.99}], "totalEntries": 1, "totalPages": 1}
        with mock.patch.object(commerce_agent, "fetch_active_listings", return_value=page):
            result = commerce_agent.import_active_listings()
        self.assertEqual(result["staleMarkedInactive"], 1)
        stale = [item for item in commerce_agent.list_listings({"status": "inactive"}) if item["sku"] == "STALE-SKU"][0]
        self.assertEqual(stale["lifecycle"], "Not returned by latest active eBay import")

    def test_active_import_preserves_previous_get_item_details_when_summary_omits_them(self):
        with commerce_agent.connect() as db:
            db.execute(
                "UPDATE listings SET data_json=? WHERE sku=?",
                (commerce_agent._json({
                    "listingId": "L1", "sku": "SKU1", "title": "Official Coach Bag", "status": "active", "source": "trading_get_item", "activeSource": "trading_active",
                    "cat": "169291", "categoryName": "Handbags", "brand": "Coach", "itemSpecifics": {"Brand": "Coach"},
                    "attributeEvidence": [{"field": "brand", "source": "ebay_get_item"}],
                }), "SKU1"),
            )
        page = {"items": [{"listingId": "L1", "sku": "SKU1", "title": "Official Coach Bag", "price": 89.99}], "totalEntries": 1, "totalPages": 1}
        with mock.patch.object(commerce_agent, "fetch_active_listings", return_value=page):
            commerce_agent.import_active_listings()
        stored = [item for item in commerce_agent.list_listings() if item["sku"] == "SKU1"][0]
        self.assertEqual(stored["cat"], "169291")
        self.assertEqual(stored["brand"], "Coach")
        self.assertEqual(stored["source"], "trading_get_item")

    def test_approval_does_not_call_ebay_and_apply_requires_approval(self):
        commerce_agent.audit_all()
        recommendation = commerce_agent.recommendations()[0]
        with mock.patch.object(commerce_agent, "update_ebay_offer") as update:
            approved = commerce_agent.approve_recommendation(recommendation["recommendationId"], {"title": "Brand Short Coat"})
        update.assert_not_called()
        with mock.patch.object(commerce_agent, "update_ebay_offer", return_value={"status": "offer_updated"}) as update:
            result = commerce_agent.apply_action(approved["actionId"])
        update.assert_called_once()
        self.assertEqual(result["status"], "Applied")

    def test_pricing_always_returns_numeric_seller_fallback(self):
        audit = commerce_agent.audit_listing({"title": "Used Coat", "brand": "Brand", "type": "Coat", "price": 42.0, "cat": "57988"})
        self.assertEqual(audit["soldPricing"]["pricingSource"], "seller_price_fallback")
        self.assertEqual(audit["soldPricing"]["recommendedPrice"], 42.0)

    def test_active_price_is_not_labeled_sold(self):
        from hht_app.market_metrics import pricing_recommendation
        result = pricing_recommendation(
            {"title": "Used Coat", "price": 42.0},
            sold_summary={"status": "not_configured"},
            active_summary={"sampleSize": 6, "medianActivePrice": 55.0, "lowActivePrice": 45.0, "highActivePrice": 70.0},
        )
        self.assertEqual(result["pricingSource"], "active_comparable")
        self.assertNotIn("sold", result["pricingSource"])
        self.assertEqual(result["recommendedPrice"], 55.0)

    def test_enrichment_preserves_official_title_and_records_ebay_evidence(self):
        detail = {
            "title": "Official Coach Willow Leather Handbag Brown",
            "desc": "Official eBay description with condition and measurement information.",
            "cat": "169291",
            "price": 89.99,
            "itemSpecifics": {
                "Brand": "Coach",
                "Model": "Willow",
                "Material": "Leather",
                "Country of Origin": "United States",
            },
            "watchCount": 3,
            "pic": "https://example.test/coach.jpg",
        }
        with mock.patch.object(commerce_agent, "fetch_listing_detail", return_value=detail):
            result = commerce_agent.enrich_listings(["L1"])
        self.assertEqual(result["updated"], 1)
        enriched = commerce_agent.list_listings()[0]
        self.assertEqual(enriched["title"], detail["title"])
        self.assertEqual(enriched["sourceTitle"], detail["title"])
        self.assertEqual(enriched["brand"], "Coach")
        self.assertEqual(enriched["attributeEvidence"]["brand"]["source"], "ebay_get_item")

    def test_enrichment_job_is_queued_and_executes_read_only_work(self):
        with mock.patch.object(commerce_agent.threading, "Thread") as thread:
            started = commerce_agent.start_enrichment_job(["L1"])
        self.assertEqual(started["status"], "queued")
        self.assertTrue(started["readOnly"])
        thread.return_value.start.assert_called_once()
        with mock.patch.object(commerce_agent, "enrich_listings", return_value={"updated": 1, "readOnly": True}) as enrich:
            completed = commerce_agent.run_job(started["jobId"])
        enrich.assert_called_once_with(["L1"])
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(completed["kind"], "enrichment")

    def test_nvidia_vision_job_runs_only_in_worker_and_keeps_ebay_read_only(self):
        image = {"data": b"tiny-image", "mimeType": "image/jpeg", "filename": "coat.jpg"}
        with mock.patch.object(commerce_agent.threading, "Thread") as thread:
            started = commerce_agent.start_nvidia_vision_job([image], {"location": "Kettering, Ohio"})
        thread.assert_not_called()
        self.assertEqual(started["kind"], "nvidia_vision")
        with mock.patch("hht_app.providers.analyze_images", return_value={"title": "Reviewed Coat", "provider": "nvidia"}) as analyze:
            completed = commerce_agent.run_job(started["jobId"])
        self.assertEqual(completed["status"], "completed")
        result = commerce_agent._decode(completed["result_json"], {})
        self.assertEqual(result["title"], "Reviewed Coat")
        self.assertTrue(result["readOnly"])
        images, context = analyze.call_args.args
        self.assertEqual(images[0].filename, "coat.jpg")
        self.assertTrue(context["try_alternate"])
        self.assertTrue(context["background_worker"])
        self.assertGreater(context["provider_timeout_seconds"], 8)

    def test_failed_nvidia_vision_job_persists_sanitized_provider_diagnostics(self):
        from hht_app.providers import ProviderError

        image = {"data": b"tiny-image", "mimeType": "image/jpeg", "filename": "coat.jpg"}
        started = commerce_agent.start_nvidia_vision_job([image])
        failure = ProviderError(
            "Configured vision provider failed.",
            502,
            failures=[{"provider": "nvidia", "category": "timeout", "message": "NVIDIA analysis timed out.", "retryable": True}],
        )
        with mock.patch("hht_app.providers.analyze_images", side_effect=failure):
            completed = commerce_agent.run_job(started["jobId"])
        self.assertEqual(completed["status"], "failed")
        self.assertEqual(completed["error"], "Configured vision provider failed.")
        result = commerce_agent._decode(completed["result_json"], {})
        self.assertEqual(result["providerFailures"][0]["provider"], "nvidia")
        self.assertNotIn("tiny-image", str(result))

    def test_checkpointed_full_enrichment_runs_in_20_item_chunks_and_remains_read_only(self):
        for index in range(2, 23):
            with commerce_agent.connect() as db:
                db.execute(
                    "INSERT INTO listings(listing_id,offer_id,sku,marketplace,data_json,imported_at) VALUES(?,?,?,?,?,?)",
                    (f"L{index}", "", f"SKU{index}", "EBAY_US", commerce_agent._json({
                        "listingId": f"L{index}", "sku": f"SKU{index}", "title": f"Active item {index}", "status": "active", "source": "trading_active"
                    }), commerce_agent.utc_now()),
                )
        with mock.patch.object(commerce_agent.threading, "Thread") as thread:
            started = commerce_agent.start_full_catalog_enrichment_job()
        thread.return_value.start.assert_called_once()
        with mock.patch.object(commerce_agent, "enrich_listings", return_value={"updated": 1, "records": [{"source": "trading_get_item"}]}) as enrich:
            first = commerce_agent.run_job(started["jobId"])
            final = commerce_agent.run_job(started["jobId"])
        self.assertEqual(first["status"], "queued")
        self.assertEqual(final["status"], "completed")
        self.assertEqual(enrich.call_count, 22)
        result = commerce_agent._decode(final["result_json"], {})
        self.assertEqual(result["checkpoints"]["processed"], 22)
        self.assertEqual(result["checkpoints"]["failed"], 0)
        self.assertTrue(result["readOnly"])

    def test_enriched_catalog_page_uses_fixed_25_item_review_pages(self):
        with commerce_agent.connect() as db:
            now = commerce_agent.utc_now()
            for index in range(2, 28):
                db.execute(
                    "INSERT INTO listings(listing_id,offer_id,sku,marketplace,data_json,imported_at) VALUES(?,?,?,?,?,?)",
                    (f"E{index}", "", f"E-SKU{index}", "EBAY_US", commerce_agent._json({
                        "listingId": f"E{index}", "sku": f"E-SKU{index}", "title": f"Enriched item {index}", "status": "active", "source": "trading_get_item"
                    }), now),
                )
            rows = db.execute("SELECT id, listing_id FROM listings ORDER BY id").fetchall()
            for row in rows:
                db.execute(
                    "INSERT INTO enrichment_checkpoints(listing_row_id,listing_id,status,enriched_at,details_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                    (row["id"], row["listing_id"], "processed", now, "{}", now, now),
                )
        first = commerce_agent.enriched_catalog_page(1, 999)
        second = commerce_agent.enriched_catalog_page(2, 25)
        self.assertEqual(first["pageSize"], 25)
        self.assertEqual(first["total"], 27)
        self.assertEqual(len(first["items"]), 25)
        self.assertEqual(second["totalPages"], 2)
        self.assertEqual(len(second["items"]), 2)

    def test_recommendation_page_is_limited_to_25_records(self):
        for index in range(2, 28):
            with commerce_agent.connect() as db:
                db.execute(
                    "INSERT INTO listings(listing_id,offer_id,sku,marketplace,data_json,imported_at) VALUES(?,?,?,?,?,?)",
                    (f"R{index}", "", f"R-SKU{index}", "EBAY_US", commerce_agent._json({
                        "listingId": f"R{index}", "sku": f"R-SKU{index}", "title": f"Review item {index}", "status": "active", "cat": "57988", "price": 20
                    }), commerce_agent.utc_now()),
                )
        commerce_agent.audit_all()
        page = commerce_agent.recommendations_page(page=1, page_size=50)
        self.assertEqual(page["pageSize"], 25)
        self.assertEqual(page["total"], 27)
        self.assertEqual(len(page["items"]), 25)


if __name__ == "__main__":
    unittest.main()
