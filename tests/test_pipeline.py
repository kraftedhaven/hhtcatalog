import csv
import io
import json
import os
import sys
import unittest
from contextlib import contextmanager
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import app
from hht_app import providers
from hht_app.ebay_pricing import (
    active_listing_keywords,
    clear_token_cache,
    ebay_access_token,
    enrich_with_ebay_active_pricing,
)
from hht_app.providers import UploadedImage
from hht_app.schema import HEADERS, export_ebay_csv, fit_title, normalize_listing


class FakeResponse:
    def __init__(self, status_code=200, payload=None, json_error=None):
        self.status_code = status_code
        self._payload = payload or {}
        self._json_error = json_error

    def json(self):
        if self._json_error:
            raise self._json_error
        return self._payload


@contextmanager
def env(**values):
    keys = [
        "PRIMARY_VISION_PROVIDER", "ZAI_API_KEY", "ZAI_BASE_URL", "ZAI_MODEL",
        "EBAY_CLIENT_ID", "EBAY_CLIENT_SECRET", "EBAY_ENVIRONMENT", "EBAY_MARKETPLACE_ID", "EBAY_SITE_ID",
        "OPENROUTER_API_KEY", "OPENROUTER_MODEL", "GEMINI_API_KEY", "GEMINI_MODEL",
        "GROQ_API_KEY", "GROQ_MODEL", "DEMO_MODE"
    ]
    old = {key: os.environ.get(key) for key in keys}
    for key in keys:
        os.environ.pop(key, None)
    for key, value in values.items():
        if value is not None:
            os.environ[key] = value
    try:
        yield
    finally:
        for key, value in old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def provider_payload(title="Levi's Denim Jacket"):
    content = json.dumps({
        "title": title,
        "price": 24.99,
        "cid": "3000",
        "cnote": "Pre-owned with light wear.",
        "cat": "57988",
        "brand": "Levi's",
        "size": "L",
        "color": "Blue",
        "dept": "Men",
        "type": "Jacket",
        "style": "Trucker",
        "mat": "Cotton",
        "pat": "Solid",
        "slv": "Long Sleeve",
        "nk": "Collared",
        "sea": "All Seasons",
        "occ": "Casual",
        "st": "Regular",
        "vin": "No",
        "desc": "<p>Nice jacket.</p>",
        "notes": "Seller should verify condition.",
        "madeIn": "Made in USA",
        "serialNumber": "",
        "measurements": "Not visible",
    })
    return {"choices": [{"message": {"content": content}}]}


class MergePipelineTests(unittest.TestCase):
    def setUp(self):
        self.client = app.app.test_client()
        self.image = UploadedImage(b"fake image data", "image/jpeg", "test.jpg")
        clear_token_cache()

    def test_health_is_safe(self):
        with env(PRIMARY_VISION_PROVIDER="zai", ZAI_API_KEY="secret"):
            response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["providers"]["zai"], True)
        self.assertNotIn("secret", response.get_data(as_text=True))

    def test_analyze_rejects_missing_file(self):
        response = self.client.post("/analyze", data={})
        self.assertEqual(response.status_code, 400)
        self.assertIn("No image uploaded", response.get_json()["error"])

    def test_analyze_rejects_unsupported_file(self):
        response = self.client.post(
            "/analyze",
            data={"file": (io.BytesIO(b"hello"), "note.txt")},
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 415)
        self.assertIn("Unsupported file type", response.get_json()["error"])

    def test_analyze_rejects_oversized_file(self):
        original_limit = app.app.config["MAX_CONTENT_LENGTH"]
        app.app.config["MAX_CONTENT_LENGTH"] = 4
        try:
            response = self.client.post(
                "/analyze",
                data={"file": (io.BytesIO(b"too large"), "photo.jpg")},
                content_type="multipart/form-data",
            )
        finally:
            app.app.config["MAX_CONTENT_LENGTH"] = original_limit
        self.assertEqual(response.status_code, 413)

    def test_zai_mock_success_uses_explicit_provider_and_official_defaults(self):
        calls = []

        def fake_post(url, **kwargs):
            calls.append((url, kwargs))
            return FakeResponse(payload=provider_payload())

        with env(PRIMARY_VISION_PROVIDER="zai", ZAI_API_KEY="zai-key"):
            with mock.patch.object(providers.requests, "post", side_effect=fake_post):
                result = providers.analyze_images([self.image])
        self.assertEqual(result["provider"], "zai")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "https://api.z.ai/api/paas/v4/chat/completions")
        self.assertEqual(calls[0][1]["headers"]["Authorization"], "Bearer zai-key")
        self.assertNotIn("zai-key", json.dumps(calls[0][1]["json"]))
        self.assertEqual(calls[0][1]["json"]["model"], "glm-4.6v-flash")
        self.assertEqual(calls[0][1]["json"]["temperature"], 0.2)
        self.assertNotIn("thinking", calls[0][1]["json"])
        content = calls[0][1]["json"]["messages"][0]["content"]
        self.assertEqual(content[0]["type"], "image_url")
        self.assertTrue(content[0]["image_url"]["url"].startswith("data:image/jpeg;base64,"))
        self.assertEqual(content[-1]["type"], "text")
        self.assertIn("Buy It Now estimate", content[-1]["text"])
        self.assertIn("Do not claim checked sold comps", content[-1]["text"])
        self.assertLess(len(content[-1]["text"]), 1200)
        self.assertEqual(result["pricingSource"], "ai_estimate")
        self.assertEqual(result["pricingSearchKeywords"], "Levi's Jacket L Cotton Trucker")

    def test_explicit_selection_does_not_call_every_provider(self):
        calls = []

        def fake_post(url, **kwargs):
            calls.append(url)
            return FakeResponse(payload=provider_payload())

        with env(PRIMARY_VISION_PROVIDER="zai", ZAI_API_KEY="zai", OPENROUTER_API_KEY="or", GEMINI_API_KEY="gm", GROQ_API_KEY="gr"):
            with mock.patch.object(providers.requests, "post", side_effect=fake_post):
                result = providers.analyze_images([self.image])
        self.assertEqual(result["provider"], "zai")
        self.assertEqual(len(calls), 1)
        self.assertIn("api.z.ai", calls[0])

    def test_provider_failure_is_non_demo(self):
        with env(PRIMARY_VISION_PROVIDER="zai", ZAI_API_KEY="zai", DEMO_MODE="false"):
            with mock.patch.object(providers.requests, "post", return_value=FakeResponse(status_code=500)):
                with self.assertRaises(providers.ProviderError) as ctx:
                    providers.analyze_images([self.image])
        self.assertIn("Configured vision provider failed", str(ctx.exception))
        self.assertEqual(ctx.exception.failures[0]["category"], "server_error")

    def test_provider_request_timeout_stays_below_heroku_limit(self):
        with env(PRIMARY_VISION_PROVIDER="zai", ZAI_API_KEY="zai"):
            with mock.patch.object(providers.requests, "post", return_value=FakeResponse(payload=provider_payload())) as post:
                providers.analyze_images([self.image])
        self.assertLessEqual(post.call_args.kwargs["timeout"], 8.0)

    def test_zai_accepts_one_to_five_images(self):
        images = [self.image] * 5
        with env(PRIMARY_VISION_PROVIDER="zai", ZAI_API_KEY="zai"):
            with mock.patch.object(providers.requests, "post", return_value=FakeResponse(payload=provider_payload())) as post:
                providers.analyze_images(images)
        content = post.call_args.kwargs["json"]["messages"][0]["content"]
        self.assertEqual(len([part for part in content if part["type"] == "image_url"]), 5)

    def test_zai_rejects_more_than_five_images(self):
        with self.assertRaises(providers.ProviderError) as ctx:
            providers.analyze_images([self.image] * 6)
        self.assertEqual(ctx.exception.status_code, 400)

    def test_zai_uses_configured_base_url_and_model(self):
        with env(PRIMARY_VISION_PROVIDER="zai", ZAI_API_KEY="zai", ZAI_BASE_URL="https://console.example/v4", ZAI_MODEL="glm-4.6v-flash"):
            with mock.patch.object(providers.requests, "post", return_value=FakeResponse(payload=provider_payload())) as post:
                providers.analyze_images([self.image])
        self.assertEqual(post.call_args.args[0], "https://console.example/v4/chat/completions")

    def test_zai_appends_chat_completions_once(self):
        with env(PRIMARY_VISION_PROVIDER="zai", ZAI_API_KEY="zai", ZAI_BASE_URL="https://console.example/v4/chat/completions/"):
            with mock.patch.object(providers.requests, "post", return_value=FakeResponse(payload=provider_payload())) as post:
                providers.analyze_images([self.image])
        self.assertEqual(post.call_args.args[0], "https://console.example/v4/chat/completions")

    def test_zai_appends_chat_completions_once_without_trailing_slash(self):
        with env(PRIMARY_VISION_PROVIDER="zai", ZAI_API_KEY="zai", ZAI_BASE_URL="https://console.example/v4/chat/completions"):
            with mock.patch.object(providers.requests, "post", return_value=FakeResponse(payload=provider_payload())) as post:
                providers.analyze_images([self.image])
        self.assertEqual(post.call_args.args[0], "https://console.example/v4/chat/completions")

    def test_zai_uses_configured_model(self):
        with env(PRIMARY_VISION_PROVIDER="zai", ZAI_API_KEY="zai", ZAI_MODEL="custom-vision-4.6v"):
            with mock.patch.object(providers.requests, "post", return_value=FakeResponse(payload=provider_payload())) as post:
                providers.analyze_images([self.image])
        self.assertEqual(post.call_args.kwargs["json"]["model"], "custom-vision-4.6v")

    def _assert_zai_failure(self, status_code, category, retryable):
        upstream_payload = {"error": {"code": "1001", "message": "Authentication parameter not received in Header, unable to authenticate"}}
        with env(PRIMARY_VISION_PROVIDER="zai", ZAI_API_KEY="zai", DEMO_MODE="false"):
            with mock.patch.object(providers.requests, "post", return_value=FakeResponse(status_code=status_code, payload=upstream_payload)) as post:
                with self.assertRaises(providers.ProviderError) as ctx:
                    providers.analyze_images([self.image])
        failure = ctx.exception.failures[0]
        self.assertEqual(failure["provider"], "zai")
        self.assertEqual(failure["model"], "glm-4.6v-flash")
        self.assertEqual(failure["status"], status_code)
        self.assertEqual(failure["httpStatus"], status_code)
        self.assertEqual(failure["category"], category)
        self.assertEqual(failure["retryable"], retryable)
        self.assertEqual(post.call_count, 2 if retryable else 1)
        if status_code != 413:
            self.assertIn("code 1001", failure["message"])
        self.assertNotIn("Authentication parameter not received", failure["message"])

    def test_zai_400_failure_is_sanitized(self):
        self._assert_zai_failure(400, "request_error", False)

    def test_zai_401_failure_is_sanitized(self):
        self._assert_zai_failure(401, "authentication", False)

    def test_zai_403_failure_is_sanitized(self):
        self._assert_zai_failure(403, "permission", False)

    def test_zai_404_failure_is_sanitized(self):
        self._assert_zai_failure(404, "not_found", False)

    def test_zai_413_failure_is_sanitized(self):
        self._assert_zai_failure(413, "payload_too_large", False)

    def test_zai_429_failure_retries_once(self):
        self._assert_zai_failure(429, "rate_limit", True)

    def test_zai_500_failure_does_not_retry(self):
        self._assert_zai_failure(500, "server_error", False)

    def test_zai_502_failure_retries_once(self):
        self._assert_zai_failure(502, "server_error", True)

    def test_zai_503_failure_retries_once(self):
        self._assert_zai_failure(503, "server_error", True)

    def test_zai_malformed_json_failure_is_sanitized(self):
        with env(PRIMARY_VISION_PROVIDER="zai", ZAI_API_KEY="zai"):
            with mock.patch.object(providers.requests, "post", return_value=FakeResponse(payload={"choices": [{"message": {"content": "not json"}}]})):
                with self.assertRaises(providers.ProviderError) as ctx:
                    providers.analyze_images([self.image])
        self.assertEqual(ctx.exception.failures[0]["category"], "malformed_json")

    def test_zai_provider_response_json_decode_error_is_sanitized(self):
        with env(PRIMARY_VISION_PROVIDER="zai", ZAI_API_KEY="zai"):
            with mock.patch.object(providers.requests, "post", return_value=FakeResponse(json_error=ValueError("bad json"))):
                with self.assertRaises(providers.ProviderError) as ctx:
                    providers.analyze_images([self.image])
        self.assertEqual(ctx.exception.failures[0]["category"], "malformed_json")

    def test_zai_fenced_json_response_normalizes(self):
        fenced = "```json\n" + provider_payload()["choices"][0]["message"]["content"] + "\n```"
        with env(PRIMARY_VISION_PROVIDER="zai", ZAI_API_KEY="zai"):
            with mock.patch.object(providers.requests, "post", return_value=FakeResponse(payload={"choices": [{"message": {"content": fenced}}]})):
                result = providers.analyze_images([self.image])
        self.assertEqual(result["provider"], "zai")
        self.assertEqual(result["brand"], "Levi's")

    def test_zai_missing_message_content_is_non_vision(self):
        with env(PRIMARY_VISION_PROVIDER="zai", ZAI_API_KEY="zai"):
            with mock.patch.object(providers.requests, "post", return_value=FakeResponse(payload={"choices": [{"message": {}}]})):
                with self.assertRaises(providers.ProviderError) as ctx:
                    providers.analyze_images([self.image])
        self.assertEqual(ctx.exception.failures[0]["category"], "non_vision_model")

    def test_zai_timeout_failure_does_not_auto_retry(self):
        with env(PRIMARY_VISION_PROVIDER="zai", ZAI_API_KEY="zai"):
            with mock.patch.object(providers.requests, "post", side_effect=providers.requests.Timeout) as post:
                with self.assertRaises(providers.ProviderError) as ctx:
                    providers.analyze_images([self.image])
        self.assertEqual(ctx.exception.failures[0]["category"], "timeout")
        self.assertEqual(ctx.exception.failures[0]["retryable"], True)
        self.assertEqual(post.call_count, 1)

    def test_zai_non_vision_model_response_is_sanitized(self):
        with env(PRIMARY_VISION_PROVIDER="zai", ZAI_API_KEY="zai", ZAI_MODEL="glm-4.6"):
            with mock.patch.object(providers.requests, "post") as post:
                with self.assertRaises(providers.ProviderError) as ctx:
                    providers.analyze_images([self.image])
        self.assertEqual(ctx.exception.failures[0]["category"], "non_vision_model")
        post.assert_not_called()

    def test_analyze_returns_sanitized_provider_errors(self):
        with env(PRIMARY_VISION_PROVIDER="zai", ZAI_API_KEY="super-secret", DEMO_MODE="false"):
            upstream_payload = {"error": {"code": "1001", "message": "Authentication parameter not received in Header, unable to authenticate"}}
            with mock.patch.object(providers.requests, "post", return_value=FakeResponse(status_code=401, payload=upstream_payload)):
                response = self.client.post(
                    "/analyze",
                    data={"file": (io.BytesIO(b"fake"), "photo.jpg")},
                    content_type="multipart/form-data",
                )
        body = response.get_json()
        self.assertEqual(response.status_code, 502)
        self.assertEqual(body["error"], "Vision analysis failed")
        self.assertEqual(body["demo"], False)
        self.assertEqual(body["provider_errors"][0]["category"], "authentication")
        self.assertEqual(body["provider_errors"][0]["message"], "Z.AI authentication failed (code 1001).")
        self.assertEqual(body["providerFailures"], body["provider_errors"])
        self.assertNotIn("super-secret", response.get_data(as_text=True))
        self.assertNotIn("base64", response.get_data(as_text=True).lower())
        self.assertNotIn("Authentication parameter not received", response.get_data(as_text=True))

    def test_analyze_rejects_more_than_five_images(self):
        data = {"file": [(io.BytesIO(b"fake"), f"photo-{index}.jpg") for index in range(6)]}
        with env(PRIMARY_VISION_PROVIDER="zai", ZAI_API_KEY="zai", DEMO_MODE="false"):
            response = self.client.post("/analyze", data=data, content_type="multipart/form-data")
        self.assertEqual(response.status_code, 400)

    def test_mock_provider_response_normalizes(self):
        result = normalize_listing(json.loads(provider_payload()["choices"][0]["message"]["content"]))
        self.assertEqual(result["titleLength"], len(result["title"]))
        self.assertLessEqual(len(result["title"]), 80)
        self.assertEqual(result["brand"], "Levi's")

    def test_active_listing_keywords_use_brand_type_size_material_style(self):
        listing = normalize_listing({
            "brand": "Patagonia",
            "type": "Fleece Jacket",
            "size": "M",
            "mat": "Polyester",
            "style": "Full Zip",
        })
        self.assertEqual(active_listing_keywords(listing), "Patagonia Fleece Jacket M Polyester Full Zip")

    def test_ebay_token_success_is_cached(self):
        with env(EBAY_CLIENT_ID="client", EBAY_CLIENT_SECRET="secret"):
            token_response = FakeResponse(payload={"access_token": "token-1", "expires_in": 7200})
            with mock.patch("hht_app.ebay_pricing.requests.post", return_value=token_response) as post:
                first = ebay_access_token()
                second = ebay_access_token()
        self.assertEqual(first, "token-1")
        self.assertEqual(second, "token-1")
        self.assertEqual(post.call_count, 1)
        self.assertTrue(post.call_args.kwargs["headers"]["Authorization"].startswith("Basic "))

    def test_ebay_token_failure_is_sanitized(self):
        with env(EBAY_CLIENT_ID="real-client-id", EBAY_CLIENT_SECRET="real-secret"):
            with mock.patch("hht_app.ebay_pricing.requests.post", return_value=FakeResponse(status_code=401, payload={"error": "invalid_client"})):
                result = enrich_with_ebay_active_pricing(normalize_listing({"brand": "Nike", "type": "Shoes", "size": "9", "price": 25}))
        self.assertEqual(result["pricingSource"], "ai_estimate")
        self.assertEqual(result["pricingError"]["provider"], "ebay_browse")
        self.assertEqual(result["pricingError"]["category"], "authentication")
        self.assertNotIn("real-client-id", json.dumps(result))
        self.assertNotIn("real-secret", json.dumps(result))
        self.assertNotIn("Basic ", json.dumps(result))

    def test_ebay_browse_price_range_sets_active_listing_estimate(self):
        listing = normalize_listing({
            "title": "Patagonia Fleece Jacket",
            "brand": "Patagonia",
            "type": "Fleece Jacket",
            "size": "M",
            "mat": "Polyester",
            "style": "Full Zip",
            "price": 20,
            "cid": "3000",
            "cat": "57988",
        })
        browse_payload = {
            "itemSummaries": [
                {"categoryId": "57988", "price": {"value": "29.99", "currency": "USD"}},
                {"categoryId": "57988", "price": {"value": "35.00", "currency": "USD"}},
                {"categoryId": "57988", "price": {"value": "41.00", "currency": "USD"}},
            ]
        }
        with env(EBAY_CLIENT_ID="client", EBAY_CLIENT_SECRET="secret"):
            with mock.patch("hht_app.ebay_pricing.requests.post", return_value=FakeResponse(payload={"access_token": "token", "expires_in": 7200})):
                with mock.patch("hht_app.ebay_pricing.requests.get", return_value=FakeResponse(payload=browse_payload)) as get:
                    result = enrich_with_ebay_active_pricing(listing)
        self.assertEqual(result["pricingSource"], "active_listing_estimate")
        self.assertEqual(result["price"], 35.00)
        self.assertEqual(result["activeListingEstimate"]["lowActivePrice"], 29.99)
        self.assertEqual(result["activeListingEstimate"]["highActivePrice"], 41.00)
        self.assertEqual(result["activeListingEstimate"]["sampleSize"], 3)
        self.assertEqual(get.call_args.kwargs["headers"]["Authorization"], "Bearer token")
        self.assertEqual(get.call_args.kwargs["params"]["q"], "Patagonia Fleece Jacket M Polyester Full Zip")
        self.assertEqual(get.call_args.kwargs["params"]["category_ids"], "57988")

    def test_ebay_browse_falls_back_without_credentials(self):
        listing = normalize_listing({
            "brand": "Patagonia",
            "type": "Fleece Jacket",
            "size": "M",
            "mat": "Polyester",
            "style": "Full Zip",
            "price": 20,
        })
        with env():
            result = enrich_with_ebay_active_pricing(listing)
        self.assertEqual(result["pricingSource"], "ai_estimate")
        self.assertEqual(result["price"], 20)
        self.assertIn("Search active eBay listings", result["notes"])

    def _assert_ebay_browse_failure(self, status_code, category):
        listing = normalize_listing({
            "brand": "Patagonia",
            "type": "Fleece Jacket",
            "size": "M",
            "mat": "Polyester",
            "style": "Full Zip",
            "price": 20,
        })
        with env(EBAY_CLIENT_ID="client", EBAY_CLIENT_SECRET="secret"):
            with mock.patch("hht_app.ebay_pricing.requests.post", return_value=FakeResponse(payload={"access_token": "token", "expires_in": 7200})):
                with mock.patch("hht_app.ebay_pricing.requests.get", return_value=FakeResponse(status_code=status_code, payload={"errors": [{"errorId": 12345}]})):
                    result = enrich_with_ebay_active_pricing(listing)
        self.assertEqual(result["pricingSource"], "ai_estimate")
        self.assertEqual(result["price"], 20)
        self.assertEqual(result["pricingError"]["provider"], "ebay_browse")
        self.assertEqual(result["pricingError"]["status"], status_code)
        self.assertEqual(result["pricingError"]["category"], category)
        self.assertEqual(result["pricingError"]["code"], "12345")

    def test_ebay_browse_401_falls_back_to_ai_estimate(self):
        self._assert_ebay_browse_failure(401, "authentication")

    def test_ebay_browse_403_falls_back_to_ai_estimate(self):
        self._assert_ebay_browse_failure(403, "authentication")

    def test_ebay_browse_429_falls_back_to_ai_estimate(self):
        self._assert_ebay_browse_failure(429, "rate_limit")

    def test_ebay_browse_500_falls_back_to_ai_estimate(self):
        self._assert_ebay_browse_failure(500, "server_error")

    def test_ebay_browse_empty_results_keeps_ai_estimate(self):
        listing = normalize_listing({"brand": "Patagonia", "type": "Fleece Jacket", "size": "M", "price": 20})
        with env(EBAY_CLIENT_ID="client", EBAY_CLIENT_SECRET="secret"):
            with mock.patch("hht_app.ebay_pricing.requests.post", return_value=FakeResponse(payload={"access_token": "token", "expires_in": 7200})):
                with mock.patch("hht_app.ebay_pricing.requests.get", return_value=FakeResponse(payload={"itemSummaries": []})):
                    result = enrich_with_ebay_active_pricing(listing)
        self.assertEqual(result["pricingSource"], "ai_estimate")
        self.assertEqual(result["activeListingEstimate"]["sampleSize"], 0)
        self.assertEqual(result["price"], 20)

    def test_ebay_browse_malformed_results_keeps_ai_estimate(self):
        listing = normalize_listing({"brand": "Patagonia", "type": "Fleece Jacket", "size": "M", "price": 20})
        with env(EBAY_CLIENT_ID="client", EBAY_CLIENT_SECRET="secret"):
            with mock.patch("hht_app.ebay_pricing.requests.post", return_value=FakeResponse(payload={"access_token": "token", "expires_in": 7200})):
                with mock.patch("hht_app.ebay_pricing.requests.get", return_value=FakeResponse(payload={"bad": []})):
                    result = enrich_with_ebay_active_pricing(listing)
        self.assertEqual(result["pricingSource"], "ai_estimate")
        self.assertEqual(result["pricingError"]["category"], "malformed_json")

    def test_ebay_browse_mismatched_category_is_ignored(self):
        listing = normalize_listing({"brand": "Patagonia", "type": "Fleece Jacket", "size": "M", "price": 20, "cat": "57988"})
        browse_payload = {"itemSummaries": [{"categoryId": "15687", "price": {"value": "99.00", "currency": "USD"}}]}
        with env(EBAY_CLIENT_ID="client", EBAY_CLIENT_SECRET="secret"):
            with mock.patch("hht_app.ebay_pricing.requests.post", return_value=FakeResponse(payload={"access_token": "token", "expires_in": 7200})):
                with mock.patch("hht_app.ebay_pricing.requests.get", return_value=FakeResponse(payload=browse_payload)):
                    result = enrich_with_ebay_active_pricing(listing)
        self.assertEqual(result["pricingSource"], "ai_estimate")
        self.assertEqual(result["activeListingEstimate"]["sampleSize"], 0)
        self.assertEqual(result["price"], 20)

    def test_ebay_browse_does_not_overwrite_seller_edited_price(self):
        listing = normalize_listing({
            "brand": "Patagonia",
            "type": "Fleece Jacket",
            "size": "M",
            "mat": "Polyester",
            "style": "Full Zip",
            "price": 50,
            "cat": "57988",
        })
        listing["sellerEditedPrice"] = True
        browse_payload = {
            "itemSummaries": [
                {"categoryId": "57988", "price": {"value": "29.99", "currency": "USD"}},
                {"categoryId": "57988", "price": {"value": "35.00", "currency": "USD"}},
                {"categoryId": "57988", "price": {"value": "41.00", "currency": "USD"}},
            ]
        }
        with env(EBAY_CLIENT_ID="client", EBAY_CLIENT_SECRET="secret"):
            with mock.patch("hht_app.ebay_pricing.requests.post", return_value=FakeResponse(payload={"access_token": "token", "expires_in": 7200})):
                with mock.patch("hht_app.ebay_pricing.requests.get", return_value=FakeResponse(payload=browse_payload)):
                    result = enrich_with_ebay_active_pricing(listing)
        self.assertEqual(result["pricingSource"], "seller_price")
        self.assertEqual(result["price"], 50)
        self.assertEqual(result["activeListingEstimate"]["sampleSize"], 3)

    def test_bag_rules(self):
        result = normalize_listing({"title": "Coach Tote", "brand": "Coach", "type": "Tote", "cat": "169291"})
        self.assertEqual(result["slv"], "N/A - bag")
        self.assertEqual(result["nk"], "N/A - bag")
        self.assertEqual(result["size"], "N/A - bag")
        self.assertEqual(result["st"], "N/A - bag")

    def test_shoe_rules(self):
        result = normalize_listing({"title": "Nike Shoes", "brand": "Nike", "type": "Shoes", "cat": "93427"})
        self.assertEqual(result["slv"], "N/A - footwear")
        self.assertEqual(result["nk"], "N/A - footwear")

    def test_vintage_title_behavior(self):
        result = normalize_listing({"title": "Levi's Denim Jacket", "brand": "Levi's", "type": "Jacket", "vin": "pre-1999 tag visible"})
        self.assertEqual(result["vin"], "Yes (pre-1999)")
        self.assertIn("Vintage", result["title"])

    def test_luxury_origin_warning(self):
        result = normalize_listing({"title": "Authentic Gucci Bag", "brand": "Gucci", "type": "Bag", "madeIn": "Made in Korea"})
        self.assertNotIn("Authentic", result["title"])
        self.assertIn("Gucci origin conflict", result["notes"])
        self.assertIn("Made in Korea", result["desc"])

    def test_title_limit_preserves_brand(self):
        title = fit_title("Levi's " + " ".join(["excellent"] * 30), "Levi's")
        self.assertLessEqual(len(title), 80)
        self.assertTrue(title.startswith("Levi's"))

    def test_csv_exact_columns_and_escaping(self):
        item = {
            "title": "Levi's Jacket",
            "price": 24.99,
            "cid": "3000",
            "cnote": "Has comma, quote \" and newline\nsee photos",
            "cat": "57988",
            "brand": "Levi's",
            "size": "L",
            "color": "Blue",
            "dept": "Men",
            "type": "Jacket",
            "desc": "<p>HTML description</p>",
        }
        text = export_ebay_csv([item])
        rows = list(csv.reader(io.StringIO(text)))
        self.assertEqual(rows[0], HEADERS)
        self.assertEqual(len(rows[0]), 35)
        self.assertEqual(len(rows[1]), 35)
        self.assertIn('"<p>HTML description</p>"', text)

    def test_multiple_rows_export(self):
        text = export_ebay_csv([
            {"title": "A", "brand": "No Brand", "type": "Shirt", "price": 1, "cat": "15724"},
            {"title": "B", "brand": "No Brand", "type": "Dress", "price": 2, "cat": "63861"},
        ])
        rows = list(csv.reader(io.StringIO(text)))
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0], HEADERS)

    def test_analyze_without_provider_reports_config_var(self):
        with env(DEMO_MODE="false"):
            response = self.client.post(
                "/analyze",
                data={"file": (io.BytesIO(b"fake"), "photo.jpg")},
                content_type="multipart/form-data",
            )
        self.assertEqual(response.status_code, 503)
        self.assertIn("PRIMARY_VISION_PROVIDER=zai", response.get_json()["error"])


if __name__ == "__main__":
    unittest.main()
