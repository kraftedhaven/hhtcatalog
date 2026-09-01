import os
import statistics
from typing import Any
from urllib.parse import urljoin

import requests


COMPSNIPER_DEFAULT_BASE_URL = "https://api.compsniper.com/"
COMPS_COUNT = 30


def enrich_with_pricing_comps(listing: dict[str, Any], timeout: float = 5.0) -> dict[str, Any]:
    result = dict(listing)
    keywords = comp_search_keywords(result)
    result["compSearchKeywords"] = keywords
    if not keywords:
        result["pricingSource"] = "none"
        result["notes"] = _append_note(result.get("notes"), "Pricing comps need seller review; not enough visible item details to build a sold-comps search.")
        return result

    api_key = os.environ.get("COMPSNIPER_API_KEY")
    if not api_key:
        result["pricingSource"] = "photo_estimate"
        result["notes"] = _append_note(result.get("notes"), f"Pricing is a photo-based estimate. Search sold comps before listing: {keywords}.")
        return result

    try:
        summary = _fetch_compsniper_summary(result, keywords, api_key, timeout)
    except PricingCompsError as exc:
        result["pricingSource"] = "photo_estimate"
        result["pricingCompsError"] = exc.to_public()
        result["notes"] = _append_note(result.get("notes"), f"Pricing comps unavailable ({exc.category}); search sold comps before listing: {keywords}.")
        return result

    if summary["sampleSize"] < 3:
        result["pricingSource"] = "photo_estimate"
        result["pricingComps"] = summary
        result["notes"] = _append_note(result.get("notes"), f"Only {summary['sampleSize']} sold comps found for '{keywords}'. Verify price manually.")
        return result

    result["price"] = summary["medianSoldPrice"]
    result["pricingSource"] = "sold_comps"
    result["pricingComps"] = summary
    result["notes"] = _append_note(
        result.get("notes"),
        f"Price set from {summary['sampleSize']} sold comps for '{keywords}' with middle range ${summary['p25SoldPrice']:.2f}-${summary['p75SoldPrice']:.2f}.",
    )
    return result


def comp_search_keywords(listing: dict[str, Any]) -> str:
    parts = [
        listing.get("brand"),
        listing.get("type"),
        listing.get("size"),
        listing.get("mat"),
        listing.get("style"),
    ]
    cleaned = []
    for part in parts:
        text = str(part or "").strip()
        if not text or text.lower() in {"not visible", "n/a - bag", "n/a - footwear", "no brand"}:
            continue
        if text.lower() not in {item.lower() for item in cleaned}:
            cleaned.append(text)
    return " ".join(cleaned)


def _fetch_compsniper_summary(listing: dict[str, Any], keywords: str, api_key: str, timeout: float) -> dict[str, Any]:
    response = requests.get(
        urljoin(_compsniper_base_url(), "v1/scrape"),
        headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
        params=_compsniper_params(listing, keywords),
        timeout=timeout,
    )
    if response.status_code >= 400:
        raise PricingCompsError(response.status_code, _category_for_status(response.status_code), _comps_error_code(response))
    try:
        body = response.json()
    except ValueError as exc:
        raise PricingCompsError(502, "malformed_json") from exc

    items = body.get("items") if isinstance(body, dict) else None
    if not isinstance(items, list):
        raise PricingCompsError(502, "malformed_json")

    prices = sorted(
        price for price in (_comp_price(item) for item in items)
        if price is not None and price > 0
    )
    if not prices:
        return _empty_summary(keywords)
    return {
        "provider": "compsniper",
        "keyword": keywords,
        "sampleSize": len(prices),
        "medianSoldPrice": round(float(statistics.median(prices)), 2),
        "p25SoldPrice": round(_percentile(prices, 0.25), 2),
        "p75SoldPrice": round(_percentile(prices, 0.75), 2),
        "minSoldPrice": round(float(prices[0]), 2),
        "maxSoldPrice": round(float(prices[-1]), 2),
    }


def _compsniper_params(listing: dict[str, Any], keywords: str) -> dict[str, str]:
    params = {
        "keyword": keywords,
        "count": str(COMPS_COUNT),
        "ebaySite": os.environ.get("COMPS_EBAY_SITE", "ebay.com"),
        "sold": "true",
        "buyingFormat": "buyItNow",
    }
    condition_id = str(listing.get("cid") or "").strip()
    if condition_id:
        params["conditionId"] = condition_id
    category_id = str(listing.get("cat") or "").strip()
    if category_id:
        params["categoryId"] = category_id
    return params


def _comp_price(item: Any) -> float | None:
    if not isinstance(item, dict) or item.get("bestOfferAccepted") is True:
        return None
    for key in ("soldPrice", "totalPrice"):
        try:
            return float(item.get(key))
        except (TypeError, ValueError):
            continue
    return None


def _percentile(values: list[float], fraction: float) -> float:
    if len(values) == 1:
        return float(values[0])
    index = (len(values) - 1) * fraction
    lower = int(index)
    upper = min(lower + 1, len(values) - 1)
    weight = index - lower
    return float(values[lower] * (1 - weight) + values[upper] * weight)


def _empty_summary(keywords: str) -> dict[str, Any]:
    return {
        "provider": "compsniper",
        "keyword": keywords,
        "sampleSize": 0,
        "medianSoldPrice": 0.0,
        "p25SoldPrice": 0.0,
        "p75SoldPrice": 0.0,
        "minSoldPrice": 0.0,
        "maxSoldPrice": 0.0,
    }


def _compsniper_base_url() -> str:
    return (os.environ.get("COMPSNIPER_BASE_URL") or COMPSNIPER_DEFAULT_BASE_URL).rstrip("/") + "/"


def _category_for_status(status_code: int) -> str:
    if status_code in {401, 403}:
        return "authentication"
    if status_code == 429:
        return "rate_limit"
    if status_code >= 500:
        return "server_error"
    return "request_error"


def _comps_error_code(response: requests.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return ""
    if not isinstance(body, dict):
        return ""
    return str(body.get("code") or "")[:32]


def _append_note(notes: Any, addition: str) -> str:
    text = str(notes or "").strip()
    return f"{text} {addition}".strip() if text else addition


class PricingCompsError(RuntimeError):
    def __init__(self, status_code: int, category: str, code: str = ""):
        super().__init__(category)
        self.status_code = status_code
        self.category = category
        self.code = code

    def to_public(self) -> dict[str, Any]:
        return {
            "provider": "compsniper",
            "status": self.status_code,
            "category": self.category,
            "retryable": self.status_code in {429, 502, 503},
            "code": self.code,
        }
