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
from hht_app.providers import UploadedImage
from hht_app.schema import HEADERS, export_ebay_csv, fit_title, normalize_listing


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


@contextmanager
def env(**values):
    keys = [
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

    def test_health_is_safe(self):
        with env(OPENROUTER_API_KEY="secret"):
            response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["providers"]["openrouter"], True)
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

    def test_provider_priority_openrouter_first(self):
        calls = []

        def fake_post(url, **kwargs):
            calls.append(url)
            return FakeResponse(payload=provider_payload())

        with env(OPENROUTER_API_KEY="or", GEMINI_API_KEY="gm", GROQ_API_KEY="gr"):
            with mock.patch.object(providers.requests, "post", side_effect=fake_post):
                result = providers.analyze_images([self.image])
        self.assertEqual(result["provider"], "openrouter")
        self.assertEqual(len(calls), 1)
        self.assertIn("openrouter.ai", calls[0])

    def test_provider_falls_back_to_gemini_then_groq_order(self):
        calls = []

        def fake_post(url, **kwargs):
            calls.append(url)
            if "openrouter" in url:
                return FakeResponse(status_code=429, payload={"error": {"message": "rate limited"}})
            return FakeResponse(payload={
                "candidates": [{"content": {"parts": [{"text": provider_payload()["choices"][0]["message"]["content"]}]}}]
            })

        with env(OPENROUTER_API_KEY="or", GEMINI_API_KEY="gm", GROQ_API_KEY="gr"):
            with mock.patch.object(providers.requests, "post", side_effect=fake_post):
                result = providers.analyze_images([self.image])
        self.assertEqual(result["provider"], "gemini")
        self.assertEqual(len(calls), 2)

    def test_provider_failure_is_non_demo(self):
        with env(OPENROUTER_API_KEY="or", DEMO_MODE="false"):
            with mock.patch.object(providers.requests, "post", return_value=FakeResponse(status_code=500)):
                with self.assertRaises(providers.ProviderError) as ctx:
                    providers.analyze_images([self.image])
        self.assertIn("All configured vision providers failed", str(ctx.exception))

    def test_provider_request_timeout_stays_below_heroku_limit(self):
        with env(OPENROUTER_API_KEY="or"):
            with mock.patch.object(providers.requests, "post", return_value=FakeResponse(payload=provider_payload())) as post:
                providers.analyze_images([self.image])
        self.assertLessEqual(post.call_args.kwargs["timeout"], 8.0)

    def test_mock_provider_response_normalizes(self):
        result = normalize_listing(json.loads(provider_payload()["choices"][0]["message"]["content"]))
        self.assertEqual(result["titleLength"], len(result["title"]))
        self.assertLessEqual(len(result["title"]), 80)
        self.assertEqual(result["brand"], "Levi's")

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
        self.assertIn("OPENROUTER_API_KEY", response.get_json()["error"])


if __name__ == "__main__":
    unittest.main()
