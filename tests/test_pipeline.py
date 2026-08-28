"""Smoke test for the HHT pipeline — no API keys required (demo mode)."""
import json, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import app


def test_gemini_uses_api_key_header():
    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "candidates": [{
                    "content": {
                        "parts": [{
                            "text": json.dumps({
                                "brand": "Levi's",
                                "garment_type": "jacket",
                                "era": "90s",
                                "color": "indigo",
                                "size": "L",
                                "condition": "very good",
                                "designer_tier": "mid",
                                "title": "Levi's Vintage 90s Denim Trucker Jacket",
                                "description": "Classic denim trucker jacket.",
                                "labels": ["denim", "jacket"],
                                "text": "LEVI'S",
                            })
                        }]
                    }
                }]
            }

    calls = []
    original_key = app.GEMINI_KEY
    original_model = app.GEMINI_MODEL
    original_requests = app.requests
    original_has_requests = app.HAS_REQUESTS

    class FakeRequests:
        @staticmethod
        def post(url, headers=None, json=None, timeout=None):
            calls.append({
                "url": url,
                "headers": headers,
                "json": json,
                "timeout": timeout,
            })
            return FakeResponse()

    try:
        app.GEMINI_KEY = "test-key"
        app.GEMINI_MODEL = "gemini-2.5-flash"
        app.requests = FakeRequests
        app.HAS_REQUESTS = True

        parsed = app.analyze_with_gemini(b"fake-image", "image/jpeg")
    finally:
        app.GEMINI_KEY = original_key
        app.GEMINI_MODEL = original_model
        app.requests = original_requests
        app.HAS_REQUESTS = original_has_requests

    assert parsed["garment_type"] == "jacket"
    assert len(calls) == 1
    assert calls[0]["url"] == (
        "https://generativelanguage.googleapis.com/v1beta/"
        "models/gemini-2.5-flash:generateContent"
    )
    assert "key=" not in calls[0]["url"]
    assert calls[0]["headers"] == {"x-goog-api-key": "test-key"}
    assert calls[0]["json"]["contents"][0]["parts"][1]["inline_data"]["data"]


test_gemini_uses_api_key_header()

IMG = os.path.join(os.path.dirname(os.path.dirname(__file__)), "tests", "images", "test_sample.jpg")
with open(IMG, "rb") as f:
    data = f.read()

result = app.run_pipeline(data, "image/jpeg", "denim-jacket.jpg")
print(json.dumps(result, indent=2))

# Assertions
assert result["sku"]["code"].startswith("HHT-"), "SKU code prefix wrong"
assert result["sku"]["code"].count("-") == 5, "SKU should have 5 dashes: " + result["sku"]["code"]
# era code must be a real 2-char code, never the raw "90" from era[:2] bug
parts = result["sku"]["code"].split("-")
assert parts[2] in {"50", "60", "70", "80", "90", "Y2", "00", "MD", "VT"}, "bad era code: " + parts[2]
assert parts[2] == "90", "90s era should map to '90', got: " + parts[2]
if app.HAS_PIL:
    assert len(result["vision"]["colors"]) >= 1, "should extract colors"
assert result["pricing"]["list_price"] > 0, "list price > 0"
assert result["pricing"]["depop"] >= result["pricing"]["list_price"], "depop price includes fee"
assert result["seo"]["title"], "seo title present"
assert len(result["seo"]["keywords"]) >= 3, "keywords present"
assert result["draft"]["title"], "draft title present"
assert result["draft"]["description"], "draft description present"
assert result["draft"]["condition"], "draft condition present"
assert result["draft"]["price_suggestion"] > 0, "draft price suggestion present"
assert len(result["draft"]["tags"]) >= 3, "draft tags present"
assert result["draft"]["sku"].startswith("HHT-"), "draft SKU present"

client = app.app.test_client()
with open(IMG, "rb") as upload:
    response = client.post(
        "/analyze",
        data={"file": (upload, "denim-jacket.jpg")},
        content_type="multipart/form-data",
    )
assert response.status_code == 200, response.get_data(as_text=True)
payload = response.get_json()
assert payload["draft"]["title"], "POST /analyze returns editable draft"
assert payload["draft"]["sku"].startswith("HHT-"), "POST /analyze returns SKU"
print("\nALL CHECKS PASSED")
