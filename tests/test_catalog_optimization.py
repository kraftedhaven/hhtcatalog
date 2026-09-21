import csv
import os
import tempfile
import unittest
from unittest import mock

from hht_app import commerce_agent, ebay_drafts, ebay_taxonomy, market_metrics
from hht_app.evidence import normalize_evidence
from hht_app.title_optimizer import optimize_title


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
        ebay_taxonomy._aspect_cache.clear()
        ebay_taxonomy._aspect_metadata_cache.clear()

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
        self.assertIn("advisory", summary["message"])
        self.assertEqual(summary["pricingSource"], "similar_used_sold")

    def test_seller_uploaded_sold_csv_can_drive_exact_price(self):
        with tempfile.NamedTemporaryFile("w", suffix=".csv", newline="", delete=False) as handle:
            writer = csv.DictWriter(handle, fieldnames=["brand", "model", "categoryId", "soldPrice", "condition"])
            writer.writeheader()
            writer.writerow({"brand": "Coach", "model": "Willow", "categoryId": "169291", "soldPrice": "79.99", "condition": "used"})
            path = handle.name
        try:
            os.environ["SOLD_COMPS_CSV"] = path
            summary = market_metrics.sold_price_summary({"brand": "Coach", "model": "Willow", "cat": "169291", "price": 129.99})
            pricing = market_metrics.pricing_recommendation({"brand": "Coach", "model": "Willow", "cat": "169291", "price": 129.99}, sold_summary=summary)
        finally:
            os.unlink(path)
        self.assertEqual(pricing["recommendedPrice"], 79.99)
        self.assertEqual(pricing["pricingSource"], "exact_used_sold")
        self.assertEqual(pricing["recommendedChangePct"], -38.5)

    def test_active_ai_and_seller_price_fallback_labels_are_distinct(self):
        active = market_metrics.pricing_recommendation(
            {"price": 100, "aiEstimatedPrice": 88},
            sold_summary={"status": "not_configured", "query": "Coach handbag"},
            active_summary={"sampleSize": 2, "medianActivePrice": 75, "lowActivePrice": 60, "highActivePrice": 90, "keyword": "Coach handbag"},
        )
        ai = market_metrics.pricing_recommendation({"price": 100, "aiEstimatedPrice": 88}, sold_summary={"status": "not_configured"})
        seller = market_metrics.pricing_recommendation({"price": 100}, sold_summary={"status": "not_configured"})
        self.assertEqual(active["pricingSource"], "active_comparable")
        self.assertEqual(ai["pricingSource"], "ai_estimate")
        self.assertEqual(seller["pricingSource"], "seller_price_fallback")
        self.assertNotIn("sold", active["pricingSource"])

    def test_sell_through_proxy_uses_sold_plus_remaining_inventory(self):
        result = market_metrics.demand_score({"quantitySold": 2, "quantity": 8})
        self.assertEqual(result["sellThroughProxy"], 0.2)
        self.assertEqual(result["methodology"], "listing_quantity_proxy")
        self.assertEqual(result["confidence"], "low")

    def test_taxonomy_is_advisory_when_not_enabled(self):
        os.environ.pop("EBAY_TAXONOMY_ENABLED", None)
        result = ebay_taxonomy.validate_listing({"cat": "57988"})
        self.assertEqual(result["status"], "not_configured")

    def test_taxonomy_uses_sandbox_host_and_returns_valid_metadata(self):
        os.environ["EBAY_TAXONOMY_ENABLED"] = "true"
        os.environ["EBAY_ENVIRONMENT"] = "sandbox"
        calls = []

        def fake_get(url, **kwargs):
            calls.append((url, kwargs))
            if "get_default_category_tree_id" in url:
                return FakeResponse(payload={"categoryTreeId": "0"})
            return FakeResponse(payload={"aspects": [{"localizedAspectName": "Brand", "aspectConstraint": {"aspectRequired": True}}]})

        with mock.patch.object(ebay_taxonomy, "ebay_access_token", return_value="token"):
            with mock.patch.object(ebay_taxonomy.requests, "get", side_effect=fake_get):
                result = ebay_taxonomy.validate_listing({"cat": "57988", "brand": "Levi's"})
        self.assertEqual(result["status"], "valid")
        self.assertFalse(result["aspectReviewRequired"])
        self.assertTrue(all(call[0].startswith("https://api.sandbox.ebay.com/") for call in calls))

    def test_taxonomy_caches_category_aspects_for_repeated_listings(self):
        os.environ["EBAY_TAXONOMY_ENABLED"] = "true"
        requests_seen = []

        def fake_get(url, **kwargs):
            requests_seen.append(url)
            if "get_default_category_tree_id" in url:
                return FakeResponse(payload={"categoryTreeId": "0"})
            return FakeResponse(payload={"aspects": [{"localizedAspectName": "Brand", "aspectConstraint": {"aspectRequired": True}}]})

        with mock.patch.object(ebay_taxonomy, "ebay_access_token", return_value="token"), mock.patch.object(ebay_taxonomy.requests, "get", side_effect=fake_get):
            first = ebay_taxonomy.validate_listing({"cat": "57988", "brand": "Levi's"})
            second = ebay_taxonomy.validate_listing({"cat": "57988", "brand": "Levi's"})
        self.assertEqual(first["status"], "valid")
        self.assertEqual(second["status"], "valid")
        aspect_requests = [url for url in requests_seen if "get_item_aspects_for_category" in url]
        self.assertEqual(len(aspect_requests), 1)

    def test_taxonomy_enabled_without_credentials_is_not_configured(self):
        os.environ["EBAY_TAXONOMY_ENABLED"] = "true"
        with mock.patch.object(ebay_taxonomy, "ebay_access_token", side_effect=ebay_taxonomy.EbayBrowseError(503, "configuration")):
            result = ebay_taxonomy.validate_listing({"cat": "57988"})
        self.assertEqual(result["status"], "not_configured")

    def test_category_suggestions_are_simplified_for_the_editor(self):
        os.environ["EBAY_TAXONOMY_ENABLED"] = "true"

        def fake_get(url, **kwargs):
            if "get_default_category_tree_id" in url:
                return FakeResponse(payload={"categoryTreeId": "0"})
            return FakeResponse(payload={"categorySuggestions": [{
                "category": {"categoryId": "123", "categoryName": "Baseball Caps"},
                "categoryTreeNodeAncestors": [
                    {"categoryName": "Hats"}, {"categoryName": "Clothing, Shoes & Accessories"},
                ],
            }]})

        with mock.patch.object(ebay_taxonomy, "ebay_access_token", return_value="token"), mock.patch.object(ebay_taxonomy.requests, "get", side_effect=fake_get):
            result = ebay_taxonomy.suggest_category("kids baseball hat")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["suggestions"][0]["categoryId"], "123")
        self.assertEqual(result["suggestions"][0]["categoryName"], "Baseball Caps")
        self.assertIn("Hats", result["suggestions"][0]["path"])

    def test_category_aspects_include_required_values_and_dynamic_specifics(self):
        os.environ["EBAY_TAXONOMY_ENABLED"] = "true"

        def fake_get(url, **kwargs):
            if "get_default_category_tree_id" in url:
                return FakeResponse(payload={"categoryTreeId": "0"})
            return FakeResponse(payload={"aspects": [{
                "localizedAspectName": "Hat Size",
                "aspectConstraint": {"aspectRequired": True, "aspectUsage": "RECOMMENDED", "itemToAspectCardinality": "SINGLE"},
                "aspectValues": [{"localizedValue": "One Size"}],
            }]})

        with mock.patch.object(ebay_taxonomy, "ebay_access_token", return_value="token"), mock.patch.object(ebay_taxonomy.requests, "get", side_effect=fake_get):
            result = ebay_taxonomy.category_aspects("52365")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["requiredAspects"], ["Hat Size"])
        self.assertTrue(result["fields"][0]["required"])
        self.assertEqual(result["fields"][0]["values"], ["One Size"])

    def test_taxonomy_blocks_mutation_only_when_explicitly_enforced(self):
        os.environ["EBAY_TAXONOMY_ENFORCE"] = "true"
        listing = {"title": "Levi's Trucker Jacket", "cat": "57988", "price": 25}
        with mock.patch.object(ebay_drafts, "validate_taxonomy", return_value={"status": "needs_specifics", "message": "Required item specifics are missing."}):
            with self.assertRaises(ebay_drafts.EbayDraftError) as context:
                ebay_drafts._validate_listing(listing)
        self.assertIn("Required item specifics", context.exception.safe_message)

    def test_placeholder_values_are_not_attribute_evidence(self):
        self.assertEqual(normalize_evidence({"brand": "Not visible"}), {})

    def test_full_evidence_schema_includes_aliases_and_review_status(self):
        result = normalize_evidence(
            {"mat": {"value": "Leather", "confidence": "high", "evidence": "Tag reads leather", "reviewed": True}, "pat": "Signature", "vin": "Yes (pre-1999)"},
            source="vision:groq",
        )
        self.assertEqual(result["material"]["value"], "Leather")
        self.assertTrue(result["material"]["reviewed"])
        self.assertEqual(result["pattern"]["value"], "Signature")
        self.assertEqual(result["vintage"]["value"], "Yes (pre-1999)")

    def test_title_optimizer_noops_seller_edited_title_and_limits_length(self):
        preserved = optimize_title({"title": "Seller Custom Title", "brand": "Coach", "type": "Bag", "sellerEditedTitle": True})
        candidate = optimize_title({"title": "Bag", "brand": "Coach", "model": "Willow", "type": "Handbag", "style": "Tote", "theme": "Classic", "mat": "Leather", "color": "Brown", "pat": "Signature", "size": "Large"})
        self.assertFalse(preserved["changed"])
        self.assertLessEqual(candidate["length"], 80)
        self.assertIn("Coach", candidate["title"])


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
        for approved in (None, recommendation["proposed"]):
            with self.subTest(approved=approved):
                with self.assertRaisesRegex(ValueError, "High-risk recommendations are review-only"):
                    commerce_agent.approve_recommendation(recommendation["recommendationId"], approved)

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
