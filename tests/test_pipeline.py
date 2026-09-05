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
from hht_app import ebay_auth
from hht_app import ebay_drafts
from hht_app.ebay_pricing import (
    active_listing_keywords,
    clear_token_cache,
    ebay_access_token,
    enrich_with_ebay_active_pricing,
)
from hht_app.providers import UploadedImage
from hht_app.schema import EBAY_DRAFT_COLUMNS, HEADERS, build_ebay_draft_csv_row, csv_from_draft_row, export_ebay_csv, export_ebay_draft_csv, fit_title, normalize_listing


class FakeResponse:
    def __init__(self, status_code=200, payload=None, json_error=None, headers=None):
        self.status_code = status_code
        self._payload = payload or {}
        self._json_error = json_error
        self.headers = headers or {}

    def json(self):
        if self._json_error:
            raise self._json_error
        return self._payload


@contextmanager
def env(**values):
    keys = [
        "PRIMARY_VISION_PROVIDER", "ZAI_API_KEY", "ZAI_BASE_URL", "ZAI_MODEL",
        "ANALYZE_DEADLINE_SECONDS", "PROVIDER_REQUEST_TIMEOUT_SECONDS",
        "EBAY_CLIENT_ID", "EBAY_CLIENT_SECRET", "EBAY_ENVIRONMENT", "EBAY_MARKETPLACE_ID", "EBAY_SITE_ID",
        "EBAY_REDIRECT_URI", "EBAY_RUNAME", "EBAY_REFRESH_TOKEN", "EBAY_USER_SCOPES", "EBAY_AUTH_STATE",
        "EBAY_MERCHANT_LOCATION_KEY", "EBAY_PAYMENT_POLICY_ID", "EBAY_FULFILLMENT_POLICY_ID",
        "EBAY_RETURN_POLICY_ID", "EBAY_CURRENCY", "EBAY_LISTING_DURATION",
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
        ebay_auth.clear_seller_token_cache()

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

    def test_analyze_accepts_iphone_heic_by_filename(self):
        captured = []

        def fake_analyze(images, _context):
            captured.extend(images)
            return {"provider": "zai", "demo": False, "title": "HEIC Item"}

        with mock.patch.object(app, "analyze_images", side_effect=fake_analyze):
            response = self.client.post(
                "/analyze",
                data={"file": (io.BytesIO(b"heic bytes"), "IMG_1001.HEIC")},
                content_type="multipart/form-data",
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(captured[0].mime_type, "image/heic")
        self.assertEqual(captured[0].filename, "IMG_1001.HEIC")

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
        self.assertEqual(calls[0][1]["json"]["temperature"], 0.1)
        self.assertEqual(calls[0][1]["json"]["max_tokens"], 800)
        self.assertNotIn("thinking", calls[0][1]["json"])
        content = calls[0][1]["json"]["messages"][0]["content"]
        self.assertEqual(content[0]["type"], "image_url")
        self.assertTrue(content[0]["image_url"]["url"].startswith("data:image/jpeg;base64,"))
        self.assertEqual(content[-1]["type"], "text")
        self.assertIn("Buy It Now estimate", content[-1]["text"])
        self.assertIn("not sold comps", content[-1]["text"])
        self.assertLess(len(content[-1]["text"]), 900)
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

    def test_groq_mock_success_uses_compressed_multimodal_json_mode(self):
        with env(PRIMARY_VISION_PROVIDER="groq", GROQ_API_KEY="groq-key"):
            with mock.patch.object(providers.requests, "post", return_value=FakeResponse(payload=provider_payload())) as post:
                result = providers.analyze_images([self.image] * 5)
        self.assertEqual(result["provider"], "groq")
        self.assertEqual(post.call_args.args[0], "https://api.groq.com/openai/v1/chat/completions")
        headers = post.call_args.kwargs["headers"]
        payload = post.call_args.kwargs["json"]
        self.assertEqual(headers["Authorization"], "Bearer groq-key")
        self.assertNotIn("groq-key", json.dumps(payload))
        self.assertEqual(payload["model"], "qwen/qwen3.6-27b")
        self.assertEqual(payload["response_format"], {"type": "json_object"})
        self.assertEqual(payload["max_completion_tokens"], 900)
        content = payload["messages"][0]["content"]
        self.assertEqual(content[0]["type"], "text")
        self.assertEqual(len([part for part in content if part["type"] == "image_url"]), 3)

    def test_groq_429_honors_retry_after_once(self):
        payload = {"error": {"code": "rate_limit_exceeded", "message": "too many requests"}}
        with env(PRIMARY_VISION_PROVIDER="groq", GROQ_API_KEY="groq-key", DEMO_MODE="false"):
            with mock.patch.object(providers.requests, "post", return_value=FakeResponse(status_code=429, payload=payload, headers={"retry-after": "2"})) as post:
                with mock.patch.object(providers.time, "sleep") as sleep:
                    with self.assertRaises(providers.ProviderError) as ctx:
                        providers.analyze_images([self.image])
        failure = ctx.exception.failures[0]
        self.assertEqual(failure["provider"], "groq")
        self.assertEqual(failure["category"], "rate_limited")
        self.assertEqual(failure["retryable"], True)
        self.assertEqual(failure["message"], "Groq rate limit reached. Wait briefly, then retry one small photo (code rate_limit_exceeded).")
        self.assertEqual(post.call_count, 2)
        sleep.assert_called_once_with(2.0)

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
        self.assertLessEqual(post.call_args.kwargs["timeout"], 18.0)

    def test_provider_request_timeout_can_be_configured(self):
        with env(PRIMARY_VISION_PROVIDER="zai", ZAI_API_KEY="zai", PROVIDER_REQUEST_TIMEOUT_SECONDS="12"):
            with mock.patch.object(providers.requests, "post", return_value=FakeResponse(payload=provider_payload())) as post:
                providers.analyze_images([self.image])
        self.assertEqual(post.call_args.kwargs["timeout"], 12.0)

    def test_provider_request_timeout_caps_old_high_config(self):
        with env(PRIMARY_VISION_PROVIDER="zai", ZAI_API_KEY="zai", PROVIDER_REQUEST_TIMEOUT_SECONDS="24"):
            with mock.patch.object(providers.requests, "post", return_value=FakeResponse(payload=provider_payload())) as post:
                providers.analyze_images([self.image])
        self.assertLessEqual(post.call_args.kwargs["timeout"], 18.0)

    def test_zai_accepts_one_to_five_images(self):
        images = [self.image] * 5
        with env(PRIMARY_VISION_PROVIDER="zai", ZAI_API_KEY="zai"):
            with mock.patch.object(providers.requests, "post", return_value=FakeResponse(payload=provider_payload())) as post:
                providers.analyze_images(images)
        content = post.call_args.kwargs["json"]["messages"][0]["content"]
        self.assertEqual(len([part for part in content if part["type"] == "image_url"]), 3)

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
        self._assert_zai_failure(429, "rate_limited", True)

    def test_zai_429_honors_retry_after_once(self):
        payload = {"error": {"code": "1305", "message": "rate limited"}}
        with env(PRIMARY_VISION_PROVIDER="zai", ZAI_API_KEY="zai", DEMO_MODE="false"):
            with mock.patch.object(providers.requests, "post", return_value=FakeResponse(status_code=429, payload=payload, headers={"Retry-After": "2"})) as post:
                with mock.patch.object(providers.time, "sleep") as sleep:
                    with self.assertRaises(providers.ProviderError) as ctx:
                        providers.analyze_images([self.image])
        failure = ctx.exception.failures[0]
        self.assertEqual(failure["category"], "rate_limited")
        self.assertEqual(failure["retryable"], True)
        self.assertEqual(failure["message"], "Z.AI rate limit reached. Wait a few minutes, then retry one small photo (code 1305).")
        self.assertEqual(post.call_count, 2)
        sleep.assert_called_once_with(2.0)

    def test_zai_429_without_retry_after_uses_bounded_backoff(self):
        payload = {"error": {"code": "1305", "message": "rate limited"}}
        with env(PRIMARY_VISION_PROVIDER="zai", ZAI_API_KEY="zai", DEMO_MODE="false"):
            with mock.patch.object(providers.requests, "post", return_value=FakeResponse(status_code=429, payload=payload)) as post:
                with mock.patch.object(providers.time, "sleep") as sleep:
                    with self.assertRaises(providers.ProviderError) as ctx:
                        providers.analyze_images([self.image])
        self.assertEqual(ctx.exception.failures[0]["category"], "rate_limited")
        self.assertEqual(post.call_count, 2)
        sleep.assert_called_once_with(0.25)

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

    def test_zai_timeout_retries_once_with_smaller_images(self):
        with env(PRIMARY_VISION_PROVIDER="zai", ZAI_API_KEY="zai"):
            with mock.patch.object(providers.requests, "post", side_effect=providers.requests.Timeout) as post:
                with self.assertRaises(providers.ProviderError) as ctx:
                    providers.analyze_images([self.image])
        self.assertEqual(ctx.exception.failures[0]["category"], "timeout")
        self.assertEqual(ctx.exception.failures[0]["retryable"], True)
        self.assertEqual(post.call_count, 2)

    def test_zai_concurrency_lock_blocks_overlapping_request(self):
        acquired = providers.ZAI_REQUEST_LOCK.acquire(timeout=0.1)
        self.assertTrue(acquired)
        try:
            with env(PRIMARY_VISION_PROVIDER="zai", ZAI_API_KEY="zai"):
                context = {"deadline": providers.time.monotonic() + 6}
                with mock.patch.object(providers.requests, "post") as post:
                    with self.assertRaises(providers.ProviderError) as ctx:
                        providers.analyze_images([self.image], context)
        finally:
            providers.ZAI_REQUEST_LOCK.release()
        failure = ctx.exception.failures[0]
        self.assertEqual(failure["category"], "rate_limited")
        self.assertEqual(failure["retryable"], True)
        self.assertIn("already analyzing", failure["message"])
        post.assert_not_called()

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

    def test_ebay_seller_oauth_start_url_uses_user_scopes(self):
        with env(
            EBAY_CLIENT_ID="client",
            EBAY_CLIENT_SECRET="secret",
            EBAY_REDIRECT_URI="https://hht.example/api/ebay/oauth/callback",
            EBAY_RUNAME="Korin_KraftedHaven-KraftedHHT-PRD-abc",
            EBAY_AUTH_STATE="setup-state",
            EBAY_ENVIRONMENT="sandbox",
        ):
            response = self.client.get("/api/ebay/oauth/start")
        body = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertIn("https://auth.sandbox.ebay.com/oauth2/authorize?", body["authorizationUrl"])
        self.assertIn("client_id=client", body["authorizationUrl"])
        self.assertIn("redirect_uri=Korin_KraftedHaven-KraftedHHT-PRD-abc", body["authorizationUrl"])
        self.assertNotIn("hht.example", body["authorizationUrl"])
        self.assertIn("sell.inventory", body["authorizationUrl"])
        self.assertIn("state=setup-state", body["authorizationUrl"])
        self.assertNotIn("secret", body["authorizationUrl"])

    def test_ebay_seller_exchange_code_returns_refresh_token_once(self):
        token_payload = {
            "access_token": "access-1",
            "refresh_token": "refresh-1",
            "expires_in": 7200,
            "token_type": "User Access Token",
        }
        with env(EBAY_CLIENT_ID="client", EBAY_CLIENT_SECRET="secret", EBAY_REDIRECT_URI="https://hht.example/callback", EBAY_RUNAME="Korin_KraftedHaven-KraftedHHT-PRD-abc"):
            with mock.patch("hht_app.ebay_auth.requests.post", return_value=FakeResponse(payload=token_payload)) as post:
                result = ebay_auth.exchange_authorization_code("code-1")
        self.assertEqual(result["refresh_token"], "refresh-1")
        self.assertEqual(post.call_args.args[0], "https://api.ebay.com/identity/v1/oauth2/token")
        self.assertTrue(post.call_args.kwargs["headers"]["Authorization"].startswith("Basic "))
        self.assertEqual(post.call_args.kwargs["data"]["grant_type"], "authorization_code")
        self.assertEqual(post.call_args.kwargs["data"]["code"], "code-1")
        self.assertEqual(post.call_args.kwargs["data"]["redirect_uri"], "Korin_KraftedHaven-KraftedHHT-PRD-abc")
        self.assertNotIn("secret", json.dumps(post.call_args.kwargs["data"]))

    def test_ebay_seller_oauth_falls_back_to_redirect_uri_for_existing_setup(self):
        with env(
            EBAY_CLIENT_ID="client",
            EBAY_CLIENT_SECRET="secret",
            EBAY_REDIRECT_URI="https://hht.example/api/ebay/oauth/callback",
            EBAY_ENVIRONMENT="sandbox",
        ):
            response = self.client.get("/api/ebay/oauth/start")
        body = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertIn("redirect_uri=https%3A%2F%2Fhht.example%2Fapi%2Febay%2Foauth%2Fcallback", body["authorizationUrl"])

    def test_ebay_seller_refresh_token_is_cached(self):
        token_payload = {"access_token": "seller-access", "expires_in": 7200}
        with env(EBAY_CLIENT_ID="client", EBAY_CLIENT_SECRET="secret", EBAY_REDIRECT_URI="https://hht.example/callback", EBAY_REFRESH_TOKEN="refresh-1"):
            with mock.patch("hht_app.ebay_auth.requests.post", return_value=FakeResponse(payload=token_payload)) as post:
                first = ebay_auth.seller_access_token()
                second = ebay_auth.seller_access_token()
        self.assertEqual(first, "seller-access")
        self.assertEqual(second, "seller-access")
        self.assertEqual(post.call_count, 1)
        self.assertEqual(post.call_args.kwargs["data"]["grant_type"], "refresh_token")
        self.assertEqual(post.call_args.kwargs["data"]["refresh_token"], "refresh-1")

    def test_ebay_seller_oauth_failure_is_sanitized(self):
        with env(EBAY_CLIENT_ID="client", EBAY_CLIENT_SECRET="super-secret", EBAY_REDIRECT_URI="https://hht.example/callback"):
            with mock.patch("hht_app.ebay_auth.requests.post", return_value=FakeResponse(status_code=401, payload={"error": "invalid_client", "error_description": "Client authentication failed"})):
                with self.assertRaises(ebay_auth.EbayAuthError) as ctx:
                    ebay_auth.exchange_authorization_code("bad-code")
        public = ctx.exception.to_public()
        self.assertEqual(public["provider"], "ebay_oauth")
        self.assertEqual(public["category"], "authentication")
        self.assertEqual(public["message"], "eBay OAuth authentication failed (invalid_client).")
        self.assertNotIn("super-secret", json.dumps(public))
        self.assertNotIn("Client authentication failed", json.dumps(public))

    def test_ebay_oauth_callback_validates_state_and_returns_refresh_token(self):
        token_payload = {
            "access_token": "access-1",
            "refresh_token": "refresh-1",
            "expires_in": 7200,
            "token_type": "User Access Token",
        }
        with env(EBAY_CLIENT_ID="client", EBAY_CLIENT_SECRET="secret", EBAY_REDIRECT_URI="https://hht.example/callback", EBAY_AUTH_STATE="state-1"):
            bad = self.client.post("/api/ebay/oauth/callback", json={"code": "code-1", "state": "wrong"})
            with mock.patch("hht_app.ebay_auth.requests.post", return_value=FakeResponse(payload=token_payload)):
                good = self.client.post("/api/ebay/oauth/callback", json={"code": "code-1", "state": "state-1"})
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(good.status_code, 200)
        body = good.get_json()
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["refreshToken"], "refresh-1")
        self.assertNotIn("access-1", good.get_data(as_text=True))

    def test_ebay_draft_creation_creates_unpublished_inventory_offer(self):
        item = {
            "sku": "LEVIS-123",
            "title": "Levi's Denim Jacket",
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
            "pic": "https://example.com/photo.jpg",
        }
        calls = []

        def fake_request(method, url, **kwargs):
            calls.append((method, url, kwargs))
            if method == "PUT":
                return FakeResponse(status_code=204)
            return FakeResponse(status_code=201, payload={"offerId": "offer-123"})

        with env(
            EBAY_MERCHANT_LOCATION_KEY="warehouse-1",
            EBAY_PAYMENT_POLICY_ID="pay-1",
            EBAY_FULFILLMENT_POLICY_ID="ship-1",
            EBAY_RETURN_POLICY_ID="return-1",
            EBAY_MARKETPLACE_ID="EBAY_US",
            EBAY_ENVIRONMENT="sandbox",
        ):
            with mock.patch("hht_app.ebay_drafts.seller_access_token", return_value="seller-token"):
                with mock.patch("hht_app.ebay_drafts.requests.request", side_effect=fake_request):
                    result = ebay_drafts.create_ebay_draft(item)

        self.assertEqual(result["status"], "draft_created")
        self.assertEqual(result["offerId"], "offer-123")
        self.assertFalse(result["published"])
        self.assertEqual(result["sku"], "LEVIS-123")
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][0], "PUT")
        self.assertEqual(calls[0][1], "https://api.sandbox.ebay.com/sell/inventory/v1/inventory_item/LEVIS-123")
        self.assertEqual(calls[1][0], "POST")
        self.assertEqual(calls[1][1], "https://api.sandbox.ebay.com/sell/inventory/v1/offer")
        self.assertFalse(any("/publish" in call[1] for call in calls))
        self.assertEqual(calls[0][2]["headers"]["Authorization"], "Bearer seller-token")
        self.assertEqual(calls[0][2]["json"]["condition"], "USED_EXCELLENT")
        self.assertEqual(calls[0][2]["json"]["product"]["imageUrls"], ["https://example.com/photo.jpg"])
        self.assertEqual(calls[1][2]["json"]["listingPolicies"]["paymentPolicyId"], "pay-1")
        self.assertEqual(calls[1][2]["json"]["merchantLocationKey"], "warehouse-1")
        self.assertEqual(calls[1][2]["json"]["pricingSummary"]["price"]["value"], "24.99")

    def test_ebay_draft_endpoint_returns_result(self):
        with mock.patch("app.create_ebay_draft", return_value={"status": "draft_created", "offerId": "offer-1", "published": False}) as create:
            response = self.client.post("/api/ebay/drafts", json={"item": {"title": "Levi's Jacket", "price": 24.99, "cat": "57988"}})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["result"]["offerId"], "offer-1")
        self.assertFalse(response.get_json()["result"]["published"])
        create.assert_called_once()

    def test_ebay_draft_missing_policy_config_fails_before_write(self):
        item = {"title": "Levi's Jacket", "price": 24.99, "cat": "57988", "brand": "Levi's", "type": "Jacket"}
        with env(EBAY_PAYMENT_POLICY_ID="pay-1", EBAY_FULFILLMENT_POLICY_ID="ship-1", EBAY_RETURN_POLICY_ID="return-1"):
            with mock.patch("hht_app.ebay_drafts.seller_access_token", return_value="seller-token") as token:
                with mock.patch("hht_app.ebay_drafts.requests.request") as request:
                    with self.assertRaises(ebay_drafts.EbayDraftError) as ctx:
                        ebay_drafts.create_ebay_draft(item)
        self.assertEqual(ctx.exception.status_code, 503)
        self.assertEqual(ctx.exception.category, "configuration")
        self.assertIn("EBAY_MERCHANT_LOCATION_KEY", ctx.exception.safe_message)
        token.assert_not_called()
        request.assert_not_called()

    def test_ebay_draft_upstream_error_is_sanitized(self):
        item = {
            "sku": "LEVIS-123",
            "title": "Levi's Jacket",
            "price": 24.99,
            "cat": "57988",
            "brand": "Levi's",
            "type": "Jacket",
        }
        with env(
            EBAY_MERCHANT_LOCATION_KEY="warehouse-1",
            EBAY_PAYMENT_POLICY_ID="pay-1",
            EBAY_FULFILLMENT_POLICY_ID="ship-1",
            EBAY_RETURN_POLICY_ID="return-1",
        ):
            with mock.patch("hht_app.ebay_drafts.seller_access_token", return_value="seller-secret-token"):
                with mock.patch("hht_app.ebay_drafts.requests.request", return_value=FakeResponse(status_code=403, payload={"errors": [{"errorId": "25002", "message": "Bad auth"}]})):
                    with self.assertRaises(ebay_drafts.EbayDraftError) as ctx:
                        ebay_drafts.create_ebay_draft(item)
        public = ctx.exception.to_public()
        self.assertEqual(public["provider"], "ebay_inventory")
        self.assertEqual(public["status"], 403)
        self.assertEqual(public["category"], "authentication")
        self.assertEqual(public["code"], "25002")
        self.assertNotIn("seller-secret-token", json.dumps(public))
        self.assertNotIn("Bad auth", json.dumps(public))

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

    def test_draft_csv_helper_is_separate_from_exact_35_column_export(self):
        item = {
            "sku": "LEVIS-123",
            "title": "Levi's Jacket",
            "price": 24.99,
            "cid": "3000",
            "cnote": "Pre-owned",
            "cat": "57988",
            "brand": "Levi's",
            "type": "Jacket",
            "pic": "https://example.com/photo.jpg",
        }
        row = build_ebay_draft_csv_row(item)
        text = csv_from_draft_row(row)
        rows = list(csv.DictReader(io.StringIO(text)))
        self.assertEqual(list(csv.reader(io.StringIO(text)))[0], EBAY_DRAFT_COLUMNS)
        self.assertEqual(rows[0]["Action(SiteID=US|Country=US|Currency=USD|Version=1193|CC=UTF-8)"], "Draft")
        self.assertEqual(rows[0]["Custom label (SKU)"], "LEVIS-123")
        self.assertEqual(rows[0]["Category ID"], "57988")
        self.assertEqual(rows[0]["Condition ID"], "3000")
        self.assertEqual(rows[0]["Format"], "FixedPrice")
        self.assertEqual(len(HEADERS), 35)

    def test_export_draft_csv_endpoint_returns_11_column_template(self):
        response = self.client.post(
            "/export/draft-csv",
            json={"items": [{"title": "Levi's Jacket", "price": 24.99, "cat": "57988", "brand": "Levi's", "type": "Jacket"}]},
        )
        self.assertEqual(response.status_code, 200)
        rows = list(csv.reader(io.StringIO(response.get_data(as_text=True))))
        self.assertEqual(rows[0], EBAY_DRAFT_COLUMNS)
        self.assertEqual(len(rows[1]), 11)

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
        self.assertIn("PRIMARY_VISION_PROVIDER=groq", response.get_json()["error"])


if __name__ == "__main__":
    unittest.main()
