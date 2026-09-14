import os
import tempfile
import unittest

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

    def test_dashboard_counts_imported_records_and_recommendations(self):
        commerce_agent.audit_all()
        dashboard = commerce_agent.dashboard()
        self.assertEqual(dashboard["listingsFound"], 1)
        self.assertEqual(dashboard["recommendations"], 1)
        self.assertEqual(dashboard["mode"], "recommend")

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
        self.assertEqual(active["ebayUrl"], "https://www.ebay.com/itm/L1")


if __name__ == "__main__":
    unittest.main()
