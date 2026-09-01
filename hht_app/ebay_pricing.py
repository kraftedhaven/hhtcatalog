import base64
import os
import statistics
import time
from typing import Any

import requests


EBAY_OAUTH_SCOPE = "https://api.ebay.com/oauth/api_scope"
TOKEN_CACHE_SKEW_SECONDS = 60
DEFAULT_MARKETPLACE_ID = "EBAY_US"
DEFAULT_SITE_ID = "0"

_token_cache: dict[str, Any] = {"token": "", "expires_at": 0.0, "environment": ""}


def enrich_with_ebay_active_pricing(listing: dict[str, Any], timeout: float = 5.0) -> dict[str, Any]:
    result = dict(listing)
    keywords = active_listing_keywords(result)
    result["pricingSearchKeywords"] = keywords
    result["pricingSource"] = "ai_estimate"
    result["aiEstimatedPrice"] = result.get("price") or 0.0

    if not keywords:
        result["notes"] = _append_note(result.get("notes"), "Pricing is an AI estimate; not enough visible item details to search active eBay listings.")
        return result
    if not _browse_configured():
        result["notes"] = _append_note(result.get("notes"), f"Pricing is an AI estimate. Search active eBay listings before listing: {keywords}.")
        return result

    try:
        summary = fetch_active_listing_estimate(result, keywords, timeout)
    except EbayBrowseError as exc:
        result["pricingError"] = exc.to_public()
        result["notes"] = _append_note(result.get("notes"), f"eBay Browse pricing unavailable ({exc.category}); AI price estimate kept. Search active listings: {keywords}.")
        return result

    if summary["sampleSize"] < 3:
        result["activeListingEstimate"] = summary
        result["notes"] = _append_note(result.get("notes"), f"Only {summary['sampleSize']} matching active eBay listings found for '{keywords}'. AI price estimate kept.")
        return result

    if result.get("sellerEditedPrice") is True:
        result["pricingSource"] = "seller_price"
        result["activeListingEstimate"] = summary
        result["notes"] = _append_note(result.get("notes"), f"Seller price kept. Active eBay listings for '{keywords}' range ${summary['lowActivePrice']:.2f}-${summary['highActivePrice']:.2f}.")
        return result

    result["price"] = summary["medianActivePrice"]
    result["pricingSource"] = "active_listing_estimate"
    result["activeListingEstimate"] = summary
    result["notes"] = _append_note(
        result.get("notes"),
        f"Price set from {summary['sampleSize']} active eBay listings for '{keywords}' with range ${summary['lowActivePrice']:.2f}-${summary['highActivePrice']:.2f}; verify against sold comps before listing.",
    )
    return result


def active_listing_keywords(listing: dict[str, Any]) -> str:
    parts = [
        listing.get("brand"),
        listing.get("type"),
        listing.get("size"),
        listing.get("mat"),
        listing.get("style"),
    ]
    cleaned: list[str] = []
    seen: set[str] = set()
    for part in parts:
        text = str(part or "").strip()
        lowered = text.lower()
        if not text or lowered in {"not visible", "n/a - bag", "n/a - footwear", "no brand"}:
            continue
        if lowered not in seen:
            cleaned.append(text)
            seen.add(lowered)
    return " ".join(cleaned)


def fetch_active_listing_estimate(listing: dict[str, Any], keywords: str, timeout: float = 5.0) -> dict[str, Any]:
    token = ebay_access_token(timeout=timeout)
    response = requests.get(
        f"{_api_base_url()}/buy/browse/v1/item_summary/search",
        headers={
            "Authorization": f"Bearer {token}",
            "X-EBAY-C-MARKETPLACE-ID": _marketplace_id(),
            "Accept": "application/json",
        },
        params=_browse_params(listing, keywords),
        timeout=timeout,
    )
    if response.status_code >= 400:
        raise EbayBrowseError(response.status_code, _category_for_status(response.status_code), _ebay_error_code(response))
    try:
        body = response.json()
    except ValueError as exc:
        raise EbayBrowseError(502, "malformed_json") from exc

    items = body.get("itemSummaries") if isinstance(body, dict) else None
    if not isinstance(items, list):
        raise EbayBrowseError(502, "malformed_json")

    category = str(listing.get("cat") or "").strip()
    prices = sorted(
        price for price in (_active_listing_price(item, category) for item in items)
        if price is not None and price > 0
    )
    return _active_summary(keywords, prices)


def ebay_access_token(timeout: float = 5.0) -> str:
    environment = _environment()
    now = time.monotonic()
    if _token_cache["token"] and _token_cache["environment"] == environment and float(_token_cache["expires_at"]) > now:
        return str(_token_cache["token"])

    client_id = os.environ.get("EBAY_CLIENT_ID", "")
    client_secret = os.environ.get("EBAY_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        raise EbayBrowseError(503, "configuration")
    credentials = base64.b64encode(f"{client_id}:{client_secret}".encode("utf-8")).decode("ascii")
    response = requests.post(
        f"{_auth_base_url()}/identity/v1/oauth2/token",
        headers={
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={"grant_type": "client_credentials", "scope": EBAY_OAUTH_SCOPE},
        timeout=timeout,
    )
    if response.status_code >= 400:
        raise EbayBrowseError(response.status_code, _category_for_status(response.status_code), _ebay_error_code(response))
    try:
        body = response.json()
    except ValueError as exc:
        raise EbayBrowseError(502, "malformed_json") from exc
    token = body.get("access_token") if isinstance(body, dict) else None
    if not token:
        raise EbayBrowseError(502, "malformed_json")
    expires_in = max(0, int(body.get("expires_in") or 0) - TOKEN_CACHE_SKEW_SECONDS)
    _token_cache.update({"token": str(token), "expires_at": now + expires_in, "environment": environment})
    return str(token)


def clear_token_cache() -> None:
    _token_cache.update({"token": "", "expires_at": 0.0, "environment": ""})


def _browse_configured() -> bool:
    return bool(os.environ.get("EBAY_CLIENT_ID") and os.environ.get("EBAY_CLIENT_SECRET"))


def _browse_params(listing: dict[str, Any], keywords: str) -> dict[str, str]:
    params = {"q": keywords, "limit": "30"}
    category = str(listing.get("cat") or "").strip()
    if category:
        params["category_ids"] = category
    filters = []
    condition = _condition_filter(listing.get("cid"))
    if condition:
        filters.append(f"conditions:{{{condition}}}")
    if filters:
        params["filter"] = ",".join(filters)

    aspects = []
    brand = _clean_aspect(listing.get("brand"))
    if category and brand:
        aspects.append(f"Brand:{{{brand}}}")
    size = _clean_aspect(listing.get("size"))
    if category and size:
        aspects.append(f"Size:{{{size}}}")
    if category and aspects:
        params["aspect_filter"] = ",".join([f"categoryId:{category}", *aspects])
    return params


def _active_listing_price(item: Any, expected_category: str) -> float | None:
    if not isinstance(item, dict):
        return None
    if expected_category:
        leaf_categories = item.get("leafCategoryIds") if isinstance(item.get("leafCategoryIds"), list) else []
        item_category = str(item.get("categoryId") or (leaf_categories[0] if leaf_categories else ""))
        if item_category and item_category != expected_category:
            return None
    price = item.get("price") or {}
    if not isinstance(price, dict):
        return None
    try:
        return float(price.get("value"))
    except (TypeError, ValueError):
        return None


def _active_summary(keywords: str, prices: list[float]) -> dict[str, Any]:
    if not prices:
        return {
            "provider": "ebay_browse",
            "kind": "active_listing_estimate",
            "keyword": keywords,
            "sampleSize": 0,
            "medianActivePrice": 0.0,
            "lowActivePrice": 0.0,
            "highActivePrice": 0.0,
        }
    return {
        "provider": "ebay_browse",
        "kind": "active_listing_estimate",
        "keyword": keywords,
        "sampleSize": len(prices),
        "medianActivePrice": round(float(statistics.median(prices)), 2),
        "lowActivePrice": round(float(prices[0]), 2),
        "highActivePrice": round(float(prices[-1]), 2),
    }


def _condition_filter(condition_id: Any) -> str:
    value = str(condition_id or "").strip()
    return {
        "1000": "NEW",
        "1500": "NEW_OTHER",
        "3000": "USED",
        "4000": "USED",
        "5000": "USED",
        "6000": "USED",
    }.get(value, "")


def _clean_aspect(value: Any) -> str:
    text = str(value or "").strip()
    if not text or text.lower() in {"not visible", "n/a - bag", "n/a - footwear", "no brand"}:
        return ""
    return text.replace("{", "").replace("}", "").replace(",", " ")[:80]


def _environment() -> str:
    return os.environ.get("EBAY_ENVIRONMENT", "production").strip().lower()


def _marketplace_id() -> str:
    return os.environ.get("EBAY_MARKETPLACE_ID") or ("EBAY_US" if _site_id() == "0" else DEFAULT_MARKETPLACE_ID)


def _site_id() -> str:
    return os.environ.get("EBAY_SITE_ID") or DEFAULT_SITE_ID


def _api_base_url() -> str:
    return "https://api.sandbox.ebay.com" if _environment() == "sandbox" else "https://api.ebay.com"


def _auth_base_url() -> str:
    return "https://api.sandbox.ebay.com" if _environment() == "sandbox" else "https://api.ebay.com"


def _category_for_status(status_code: int) -> str:
    if status_code in {401, 403}:
        return "authentication"
    if status_code == 429:
        return "rate_limit"
    if status_code >= 500:
        return "server_error"
    return "request_error"


def _ebay_error_code(response: requests.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return ""
    if not isinstance(body, dict):
        return ""
    errors = body.get("errors")
    if isinstance(errors, list) and errors and isinstance(errors[0], dict):
        return str(errors[0].get("errorId") or "")[:32]
    return str(body.get("error") or "")[:32]


def _append_note(notes: Any, addition: str) -> str:
    text = str(notes or "").strip()
    return f"{text} {addition}".strip() if text else addition


class EbayBrowseError(RuntimeError):
    def __init__(self, status_code: int, category: str, code: str = ""):
        super().__init__(category)
        self.status_code = status_code
        self.category = category
        self.code = code

    def to_public(self) -> dict[str, Any]:
        return {
            "provider": "ebay_browse",
            "status": self.status_code,
            "category": self.category,
            "retryable": self.status_code in {429, 500, 502, 503, 504},
            "code": self.code,
        }
