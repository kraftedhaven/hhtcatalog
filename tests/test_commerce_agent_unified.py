"""Regression tests for the unified Commerce Agent optimization pipeline."""

import os
import unittest

os.environ.setdefault("DEMO_MODE", "true")
os.environ.setdefault("EBAY_MUTATIONS_ENABLED", "false")

from hht_app import commerce_agent
from hht_app.schema import normalize_listing
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

    def test_reanalysis_merges_unconfirmed_item_specifics_condition_and_preserves_confirmed(self):
        unconfirmed = commerce_agent.run_commerce_agent_optimization(
            {
                "title": "Polo Shirt",
                "condition": "Pre-owned",
                "cid": "3000",
                "itemSpecifics": {"Brand": "OldBrand", "neckline": "Crew"},
                "attributeEvidence": {"brand": {"value": "OldBrand", "confidence": "low"}},
            },
            raw_ai_listing={
                "title": "Ralph Lauren Rugby Shirt",
                "condition": "New with tags",
                "cid": "1000",
                "itemSpecifics": {
                    "Brand": "Ralph Lauren",
                    "Neckline": "Collared",
                    "Sleeve Length": "Long Sleeve",
                    "Features": ["Heavyweight", "Embroidered Crest"],
                    "Ignored": "Not visible",
                },
                "attributeEvidence": {"brand": {"value": "Ralph Lauren", "confidence": "high"}},
            },
            fetch_live_market=False,
        )
        merged_specs = unconfirmed["listing"]["itemSpecifics"]
        self.assertEqual(merged_specs["Brand"], "Ralph Lauren")
        self.assertEqual(merged_specs["Neckline"], "Collared")
        self.assertNotIn("neckline", merged_specs)
        self.assertEqual(merged_specs["Sleeve Length"], "Long Sleeve")
        self.assertEqual(merged_specs["Features"], ["Heavyweight", "Embroidered Crest"])
        self.assertNotIn("Ignored", merged_specs)
        self.assertEqual(unconfirmed["listing"]["condition"], "New with tags")
        self.assertEqual(unconfirmed["listing"]["cid"], "1000")
        self.assertEqual(unconfirmed["listing"]["attributeEvidence"]["brand"]["value"], "Ralph Lauren")

        aspect_protected = commerce_agent.run_commerce_agent_optimization(
            {
                "title": "Shirt",
                "itemSpecifics": {"Brand": "CustomSellerBrand", "Sleeve Length": "Short Sleeve"},
            },
            raw_ai_listing={
                "itemSpecifics": {"Brand": "AIUpdatedBrand", "Sleeve Length": "Long Sleeve"},
            },
            existing_provenance={"Brand": {"source": "seller_input", "sellerConfirmed": True}},
            fetch_live_market=False,
        )
        self.assertEqual(aspect_protected["listing"]["itemSpecifics"]["Brand"], "CustomSellerBrand")
        self.assertEqual(aspect_protected["listing"]["itemSpecifics"]["Sleeve Length"], "Long Sleeve")

        dict_and_cond_protected = commerce_agent.run_commerce_agent_optimization(
            {
                "title": "Shirt",
                "condition": "Pre-owned - Good",
                "cid": "3000",
                "itemSpecifics": {"Brand": "KeptWholeDict"},
            },
            raw_ai_listing={
                "condition": "New with tags",
                "cid": "1000",
                "itemSpecifics": {"Brand": "ShouldNotReplace", "Sleeve Length": "Long Sleeve"},
            },
            seller_confirmed_fields={"itemSpecifics", "cid"},
            fetch_live_market=False,
        )
        self.assertEqual(dict_and_cond_protected["listing"]["itemSpecifics"], {"Brand": "KeptWholeDict"})
        self.assertEqual(dict_and_cond_protected["listing"]["condition"], "Pre-owned - Good")
        self.assertEqual(dict_and_cond_protected["listing"]["cid"], "3000")

    def test_title_optimizer_retains_generic_product_noun_when_type_missing(self):
        shirt = optimize_title(normalize_listing({"title": "Shirt", "color": "Red", "size": "L"}))
        self.assertIn("Shirt", shirt["recommendedTitle"])
        self.assertNotEqual(shirt["recommendedTitle"], "Red L")
        for cand in shirt["candidates"].values():
            self.assertIn("Shirt", cand["title"])

        placeholder = optimize_title({"title": "Review Needed Item", "color": "Red", "size": "L"})
        self.assertNotIn("Review Needed Item", placeholder["recommendedTitle"])

        with_type = optimize_title({"title": "Bag", "brand": "Coach", "type": "Handbag", "color": "Brown"})
        self.assertNotIn("Bag", with_type["recommendedTitle"].split())

    def test_reanalysis_and_identity_change_invalidate_cached_comparables(self):
        stale_item = {
            "title": "Nike Red Shirt",
            "brand": "Nike",
            "color": "Red",
            "type": "Shirt",
            "pricingSearchKeywords": "Nike Red Shirt",
            "activeListingEstimate": {
                "status": "ok",
                "sampleSize": 5,
                "medianActivePrice": 25.0,
                "lowActivePrice": 20.0,
                "highActivePrice": 30.0,
                "keyword": "Nike Red Shirt",
            },
        }
        reanalyzed = commerce_agent.run_commerce_agent_optimization(
            stale_item,
            raw_ai_listing={"title": "Adidas Blue Shirt", "brand": "Adidas", "color": "Blue", "type": "Shirt"},
            fetch_live_market=False,
        )
        self.assertNotIn("Nike", reanalyzed["listing"]["title"])
        self.assertNotIn("Red", reanalyzed["listing"]["title"])
        self.assertEqual(reanalyzed["activeSummary"]["sampleSize"], 0)
        self.assertTrue(reanalyzed["pricingRecommendation"]["insufficientMarketEvidence"])
        self.assertNotIn("Nike", reanalyzed["listing"].get("pricingSearchKeywords", ""))

        edited_in_place = commerce_agent.run_commerce_agent_optimization(
            {
                **stale_item,
                "brand": "Adidas",
                "color": "Blue",
                "marketEvidenceIdentity": "brand=nike|type=shirt|color=red",
            },
            raw_ai_listing=None,
            fetch_live_market=False,
        )
        self.assertEqual(edited_in_place["activeSummary"]["sampleSize"], 0)
        self.assertTrue(edited_in_place["pricingRecommendation"]["insufficientMarketEvidence"])
        self.assertIn("brand=adidas", edited_in_place["listing"]["marketEvidenceIdentity"])

    def test_title_optimizer_preserves_single_character_identifiers(self):
        ps5 = optimize_title({"title": "Sony PlayStation 5", "brand": "Sony", "color": "White"})
        self.assertIn("5", ps5["recommendedTitle"].split())
        self.assertIn("PlayStation", ps5["recommendedTitle"].split())

        ps5_storage = optimize_title({"title": "Sony PlayStation 5", "brand": "Sony", "size": "500GB"})
        self.assertIn("5", ps5_storage["recommendedTitle"].split())

        jordan1 = optimize_title({"title": "Nike Air Jordan 1", "brand": "Nike", "color": "Red"})
        self.assertIn("1", jordan1["recommendedTitle"].split())

    def test_title_optimizer_preserves_internal_punctuation_in_specs(self):
        shoes = optimize_title({"title": "Nike Shoes Size 10.5", "brand": "Nike", "type": "Shoes", "color": "Black"})
        self.assertIn("10.5", shoes["recommendedTitle"])
        self.assertNotIn("10 5", shoes["recommendedTitle"])

        lens = optimize_title({"title": "Canon 50mm f/1.8 Lens", "brand": "Canon", "type": "Lens"})
        self.assertIn("f/1.8", lens["recommendedTitle"])
        self.assertIn("50mm", lens["recommendedTitle"])

        size_and_model = optimize_title({"title": "Nike Shoes Size 10.5", "brand": "Nike", "size": "10", "model": "5"})
        self.assertIn("10.5", size_and_model["recommendedTitle"])


if __name__ == "__main__":
    unittest.main()
