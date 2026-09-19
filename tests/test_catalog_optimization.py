import os
import unittest
from unittest import mock

from hht_app import ebay_drafts, ebay_taxonomy, market_metrics
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


if __name__ == "__main__":
    unittest.main()
