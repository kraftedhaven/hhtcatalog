"""Regression tests for the unified Commerce Agent optimization pipeline."""

import os
import unittest

os.environ.setdefault("DEMO_MODE", "true")
os.environ.setdefault("EBAY_MUTATIONS_ENABLED", "false")

from hht_app import commerce_agent
from hht_app.title_optimizer import optimize_title


class UnifiedCommerceAgentTests(unittest.TestCase):
    def test_rejects_fabricated_market_statistics(self):
        res = commerce_agent.run_commerce_agent_optimization(
            {"title": "Jacket", "brand": "Nike", "type": "Jacket", "aiEstimatedPrice": 40.0, "sea": "All Seasons"},
            raw_ai_listing={
                "soldPricing": {"status": "ok", "sampleSize": 0, "medianSoldPrice": 99.0},
                "activeListingEstimate": {"status": "ok", "sampleSize": -3, "medianActivePrice": 55.0},
            },
            fetch_live_market=False,
        )
        self.assertEqual(res["soldSummary"]["sampleSize"], 0)
        self.assertEqual(res["activeSummary"]["sampleSize"], 0)
        self.assertEqual(res["soldSummary"].get("validationFlag"), "rejected_fabricated_or_invalid_stats")
        self.assertEqual(res["activeSummary"].get("validationFlag"), "rejected_fabricated_or_invalid_stats")
        self.assertTrue(res["pricingRecommendation"]["insufficientMarketEvidence"])
        self.assertEqual(res["demand"]["sellThroughSignal"], "unavailable")
        self.assertEqual(res["demand"]["seasonalDemand"]["status"], "unsupported")

    def test_sold_only_with_unavailable_active_competition_does_not_fabricate_sell_through(self):
        res = commerce_agent.run_commerce_agent_optimization(
            {"title": "Polo", "brand": "Ralph Lauren", "type": "Polo Shirt"},
            raw_ai_listing={
                "soldComparableSummary": {
                    "status": "ok",
                    "pricingSource": "exact_used_sold",
                    "sampleSize": 5,
                    "lowSoldPrice": 20.0,
                    "medianSoldPrice": 30.0,
                    "highSoldPrice": 42.0,
                    "query": "Ralph Lauren Polo Shirt",
                },
                "activeListingEstimate": {"status": "unavailable", "sampleSize": 0},
            },
            fetch_live_market=False,
        )
        demand = res["demand"]
        self.assertEqual(demand["soldCount"], 5)
        self.assertIsNone(demand["sellThroughRate"])
        self.assertEqual(demand["sellThroughSignal"], "unavailable")
        self.assertEqual(demand["currentDemandStatus"], "sold_evidence_only")
        self.assertEqual(demand["provenanceSource"], "market_sold_comp")

    def test_valid_sold_and_active_evidence_produces_distinct_tiers_and_seo_title(self):
        backed = commerce_agent.run_commerce_agent_optimization(
            {
                "title": "Polo",
                "brand": "Polo Ralph Lauren",
                "model": "Classic Fit",
                "type": "Polo Shirt",
                "color": "Green",
                "mat": "100% Cotton",
                "size": "L",
                "cat": "57990",
            },
            raw_ai_listing={
                "soldComparableSummary": {
                    "status": "ok",
                    "pricingSource": "exact_used_sold",
                    "sampleSize": 8,
                    "lowSoldPrice": 22.0,
                    "p25SoldPrice": 26.0,
                    "medianSoldPrice": 32.0,
                    "p75SoldPrice": 38.0,
                    "highSoldPrice": 45.0,
                    "query": "Polo Ralph Lauren Classic Fit Polo Shirt",
                },
                "activeListingEstimate": {
                    "status": "ok",
                    "sampleSize": 10,
                    "lowActivePrice": 25.0,
                    "medianActivePrice": 35.0,
                    "highActivePrice": 50.0,
                },
            },
            fetch_live_market=False,
        )
        pr = backed["pricingRecommendation"]
        self.assertFalse(pr["insufficientMarketEvidence"])
        self.assertLess(pr["fastSalePrice"], pr["balancedPrice"])
        self.assertLess(pr["balancedPrice"], pr["premiumPrice"])
        self.assertEqual(backed["provenance"]["price"]["source"], "market_sold_comp")
        self.assertEqual(backed["provenance"]["title"]["source"], "demand_signal")
        self.assertLessEqual(len(backed["listing"]["title"]), 80)
        self.assertEqual(backed["demand"]["currentDemandStatus"], "supported")
        self.assertAlmostEqual(backed["demand"]["sellThroughRate"], 0.444, places=3)
        self.assertIn("commerceAgentReport", backed)

    def test_active_listing_audit_preserves_unified_report(self):
        audit = commerce_agent.audit_listing({
            "title": "Polo Ralph Lauren Classic Fit Polo Shirt",
            "brand": "Polo Ralph Lauren",
            "type": "Polo Shirt",
            "size": "L",
            "price": 32.0,
            "cat": "57990",
        })
        self.assertIn("commerceAgentReport", audit)
        self.assertEqual(audit["soldPricing"]["provenanceSource"], "seller_input")

    def test_title_optimizer_preserves_identifying_title_when_attributes_absent(self):
        unstructured = optimize_title({"title": "Apple iPhone 13 Pro Max 256GB"})
        self.assertEqual(unstructured["recommendedTitle"], "Apple iPhone 13 Pro Max 256GB")
        self.assertFalse(unstructured["changed"])
        self.assertEqual(unstructured["title"], "")

        categorized_only = optimize_title(
            {"title": "Apple iPhone 13 Pro Max 256GB"},
            category_name="Cell Phones & Smartphones",
        )
        self.assertEqual(categorized_only["recommendedTitle"], "Apple iPhone 13 Pro Max 256GB")
        self.assertFalse(categorized_only["changed"])

        sparse_brand = optimize_title({"title": "WH-1000XM5 Wireless Headphones", "brand": "Sony"})
        self.assertIn("WH-1000XM5", sparse_brand["recommendedTitle"])
        self.assertIn("Sony", sparse_brand["recommendedTitle"])
        self.assertLessEqual(len(sparse_brand["recommendedTitle"]), 80)

        audit = commerce_agent.audit_listing(
            {"title": "Apple iPhone 13 Pro Max 256GB", "price": 500.0, "cat": "9355"}
        )
        self.assertNotIn("title", audit["proposed"])

    def test_reanalysis_seeds_unconfirmed_title_from_new_analysis_and_preserves_confirmed_title(self):
        with_new_title = commerce_agent.run_commerce_agent_optimization(
            {"title": "Nike Red Shirt", "brand": "Nike", "color": "Red", "type": "Shirt"},
            raw_ai_listing={"title": "Adidas Blue Shirt", "brand": "Adidas", "color": "Blue", "type": "Shirt"},
            fetch_live_market=False,
        )
        self.assertIn("Adidas", with_new_title["listing"]["title"])
        self.assertIn("Blue", with_new_title["listing"]["title"])
        self.assertNotIn("Nike", with_new_title["listing"]["title"])
        self.assertNotIn("Red", with_new_title["listing"]["title"])

        with_attrs_only = commerce_agent.run_commerce_agent_optimization(
            {"title": "Nike Red Shirt", "brand": "Nike", "color": "Red", "type": "Shirt"},
            raw_ai_listing={"brand": "Adidas", "color": "Blue", "type": "Shirt"},
            fetch_live_market=False,
        )
        self.assertIn("Adidas", with_attrs_only["listing"]["title"])
        self.assertIn("Blue", with_attrs_only["listing"]["title"])
        self.assertNotIn("Nike", with_attrs_only["listing"]["title"])
        self.assertNotIn("Red", with_attrs_only["listing"]["title"])

        confirmed = commerce_agent.run_commerce_agent_optimization(
            {"title": "Nike Red Shirt", "brand": "Nike", "color": "Red", "type": "Shirt"},
            raw_ai_listing={"title": "Adidas Blue Shirt", "brand": "Adidas", "color": "Blue", "type": "Shirt"},
            existing_provenance={"title": {"source": "seller_input", "sellerConfirmed": True}},
            fetch_live_market=False,
        )
        self.assertEqual(confirmed["listing"]["title"], "Nike Red Shirt")


if __name__ == "__main__":
    unittest.main()
