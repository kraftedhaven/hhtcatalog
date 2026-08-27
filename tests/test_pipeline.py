"""Smoke test for the HHT pipeline — no API keys required (demo mode)."""
import json, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import app

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
