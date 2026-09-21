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

    def test_audit_always_has_actionable_proposed_change_for_findings(self):
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
        self.assertIn("notes", audit["proposed"])
        self.assertIn("Seller review required", audit["proposed"]["notes"])

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

<<<<<<< HEAD
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
=======
    def test_pricing_always_returns_numeric_seller_fallback(self):
        audit = commerce_agent.audit_listing({"title": "Used Coat", "brand": "Brand", "type": "Coat", "price": 42.0, "cat": "57988"})
        self.assertEqual(audit["soldPricing"]["pricingSource"], "seller_price_fallback")
        self.assertEqual(audit["soldPricing"]["recommendedPrice"], 42.0)

    def test_active_price_is_not_labeled_sold(self):
        from hht_app.market_metrics import sold_price_summary
        result = sold_price_summary({"title": "Used Coat", "price": 42.0, "activeListingEstimate": {"sampleSize": 6, "medianActivePrice": 55.0, "lowActivePrice": 45.0, "highActivePrice": 70.0}})
        self.assertEqual(result["pricingSource"], "active_comparable")
        self.assertNotIn("sold", result["pricingSource"])
        self.assertEqual(result["recommendedPrice"], 55.0)
>>>>>>> eeed06fa7cec048409069a56ede856d0961027fa


if __name__ == "__main__":
    unittest.main()
