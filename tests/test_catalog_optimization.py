import os
import tempfile
import unittest
from unittest import mock

from hht_app import commerce_agent, ebay_drafts, ebay_taxonomy, market_metrics
from hht_app.evidence import normalize_evidence


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self.payload = payload or {}

    def json(self):
        return self.payload


class CatalogOptimizationTests(unittest.TestCase):
    def setUp(self):
        self.old_environment = dict(os.environ)
        ebay_taxonomy._cache.clear()
        ebay_taxonomy._tree_cache.clear()

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.old_environment)

    def test_vision_evidence_is_named_and_bounded(self):
        result = normalize_evidence(
            {"brand": "Levi's", "model": {"value": "Trucker", "confidence": "high"}},
            source="vision:groq",
            default_evidence="Photo review needed.",
        )
        self.assertEqual(result["brand"]["source"], "vision:groq")
        self.assertEqual(result["brand"]["evidence"], "Photo review needed.")
        self.assertEqual(result["model"]["confidence"], "high")

    def test_sold_comps_stay_advisory_and_calculate_median(self):
        os.environ["SOLD_COMPS_API_URL"] = "https://comps.example.test/search"
        with mock.patch.object(
            market_metrics.requests,
            "get",
            return_value=FakeResponse(payload={"items": [{"soldPrice": "12.00"}, {"price": {"value": "20"}}, {"value": 40}]}),
        ):
            summary = market_metrics.sold_price_summary({"brand": "Levi's", "type": "Jacket", "cat": "57988"})
        self.assertEqual(summary["status"], "ok")
        self.assertEqual(summary["sampleSize"], 3)
        self.assertEqual(summary["medianSoldPrice"], 20.0)
        self.assertIn("no price change", summary["message"])

    def test_sell_through_proxy_uses_sold_plus_remaining_inventory(self):
        result = market_metrics.demand_score({"quantitySold": 2, "quantity": 8})
        self.assertEqual(result["sellThroughProxy"], 0.2)

    def test_taxonomy_is_advisory_when_not_enabled(self):
        os.environ.pop("EBAY_TAXONOMY_ENABLED", None)
        result = ebay_taxonomy.validate_listing({"cat": "57988"})
        self.assertEqual(result["status"], "not_configured")

    def test_taxonomy_blocks_mutation_only_when_explicitly_enforced(self):
        os.environ["EBAY_TAXONOMY_ENFORCE"] = "true"
        listing = {"title": "Levi's Trucker Jacket", "cat": "57988", "price": 25}
        with mock.patch.object(ebay_drafts, "validate_taxonomy", return_value={"status": "needs_specifics", "message": "Required item specifics are missing."}):
            with self.assertRaises(ebay_drafts.EbayDraftError) as context:
                ebay_drafts._validate_listing(listing)
        self.assertIn("Required item specifics", context.exception.safe_message)

    def test_placeholder_values_are_not_attribute_evidence(self):
        self.assertEqual(normalize_evidence({"brand": "Not visible"}), {})


class CatalogOptimizationActionabilityTests(unittest.TestCase):
    def setUp(self):
        self.old_environment = dict(os.environ)
        self.db_file = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False)
        self.db_file.close()
        os.environ["COMMERCE_AGENT_DB"] = self.db_file.name
        ebay_taxonomy._cache.clear()
        ebay_taxonomy._tree_cache.clear()
        commerce_agent.init_db()

    def tearDown(self):
        try:
            os.unlink(self.db_file.name)
        except FileNotFoundError:
            pass
        os.environ.clear()
        os.environ.update(self.old_environment)

    def _insert_listing(self, listing_id: str, sku: str, data: dict):
        with commerce_agent.connect() as db:
            db.execute(
                "INSERT INTO listings(listing_id, offer_id, sku, marketplace, data_json, imported_at) VALUES(?,?,?,?,?,?)",
                (listing_id, f"O-{listing_id}", sku, "EBAY_US", commerce_agent._json(data), commerce_agent.utc_now()),
            )

    def test_low_risk_enriched_record_gets_real_title_or_price_proposal(self):
        with mock.patch.object(commerce_agent, "validate_listing", return_value={"status": "valid", "message": "ok"}), mock.patch.object(
            commerce_agent,
            "sold_price_summary",
            return_value={"recommendedPrice": 50.0, "recommendedChangePct": 25, "pricingSource": "similar_used_sold"},
        ):
            audit = commerce_agent.audit_listing(
                {
                    "title": "Levi Jacket",
                    "brand": "Levi's",
                    "type": "Jacket",
                    "style": "Trucker",
                    "color": "Blue",
                    "size": "M",
                    "cat": "57988",
                    "desc": "Excellent pre-owned condition with measurements and material details included for buyers.",
                    "pic": "https://example.test/a.jpg",
                    "price": 40.0,
                    "cnote": "Great shape",
                }
            )
        self.assertEqual(audit["risk"], "low")
        self.assertTrue(audit["proposed"])
        self.assertNotEqual(audit["proposed"].get("title", "").casefold(), "levi jacket")

    def test_identical_price_recommendation_is_not_proposed(self):
        with mock.patch.object(commerce_agent, "validate_listing", return_value={"status": "valid", "message": "ok"}), mock.patch.object(
            commerce_agent,
            "sold_price_summary",
            return_value={"recommendedPrice": 42.0, "recommendedChangePct": 8, "pricingSource": "similar_used_sold"},
        ):
            audit = commerce_agent.audit_listing({"title": "Brand Coat", "brand": "Brand", "type": "Coat", "cat": "57988", "price": 42.0})
        self.assertNotIn("price", audit["proposed"])

    def test_blocked_missing_category_cannot_be_approved_even_with_proposal(self):
        self._insert_listing(
            "L-MISSING-CAT",
            "SKU-MISSING-CAT",
            {
                "listingId": "L-MISSING-CAT",
                "offerId": "O-L-MISSING-CAT",
                "sku": "SKU-MISSING-CAT",
                "title": "Brand Jacket",
                "brand": "Brand",
                "type": "Jacket",
                "style": "Bomber",
                "color": "Black",
                "size": "L",
                "price": 80.0,
                "desc": "Detailed condition and fit notes for buyers with measurements and fabric information.",
                "pic": "https://example.test/b.jpg",
                "cat": "",
                "cnote": "Clean",
            },
        )
        with mock.patch.object(commerce_agent, "sold_price_summary", return_value={"recommendedPrice": 92.0, "recommendedChangePct": 15, "pricingSource": "similar_used_sold"}):
            commerce_agent.audit_all()
        recommendation = commerce_agent.recommendations()[0]
        self.assertTrue(recommendation["proposed"])
        self.assertEqual(recommendation["risk"], "high")
        with self.assertRaisesRegex(ValueError, "High-risk recommendations are review-only"):
            commerce_agent.approve_recommendation(recommendation["recommendationId"])

    def test_low_risk_proposal_can_be_approved_without_applying(self):
        self._insert_listing(
            "L-LOW-RISK",
            "SKU-LOW-RISK",
            {
                "listingId": "L-LOW-RISK",
                "offerId": "O-L-LOW-RISK",
                "sku": "SKU-LOW-RISK",
                "title": "Levi Jacket",
                "brand": "Levi's",
                "type": "Jacket",
                "style": "Trucker",
                "color": "Blue",
                "size": "M",
                "price": 40.0,
                "desc": "Excellent pre-owned jacket with condition details, measurements, and materials for buyers.",
                "pic": "https://example.test/c.jpg",
                "cat": "57988",
                "cnote": "Great shape",
            },
        )
        with mock.patch.object(commerce_agent, "validate_listing", return_value={"status": "valid", "message": "ok"}), mock.patch.object(
            commerce_agent,
            "sold_price_summary",
            return_value={"recommendedPrice": 50.0, "recommendedChangePct": 25, "pricingSource": "similar_used_sold"},
        ):
            commerce_agent.audit_all()
        recommendation = commerce_agent.recommendations()[0]
        self.assertEqual(recommendation["risk"], "low")
        self.assertTrue(recommendation["proposed"])
        approved = commerce_agent.approve_recommendation(recommendation["recommendationId"])
        self.assertEqual(approved["status"], "Approved")
        self.assertEqual(commerce_agent.recommendations()[0]["status"], "Approved")
        self.assertEqual(commerce_agent.history()[0]["status"], "Approved")


if __name__ == "__main__":
    unittest.main()
