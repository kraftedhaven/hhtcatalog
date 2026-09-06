import os
import re
import time
from typing import Any
from urllib.parse import quote

import requests

from .ebay_auth import EbayAuthError, seller_access_token
from .ebay_pricing import DEFAULT_MARKETPLACE_ID
from .schema import normalize_listing


DEFAULT_TIMEOUT_SECONDS = 8.0
DEFAULT_CURRENCY = "USD"
DEFAULT_LISTING_DURATION = "GTC"


class EbayDraftError(RuntimeError):
    def __init__(self, status_code: int, category: str, message: str, code: str = ""):
        super().__init__(message)
        self.status_code = status_code
        self.category = category
        self.safe_message = message
        self.code = code

    def to_public(self) -> dict[str, Any]:
        body = {
            "provider": "ebay_inventory",
            "status": self.status_code,
            "category": self.category,
            "retryable": self.status_code in {429, 500, 502, 503, 504},
            "message": self.safe_message,
        }
        if self.code:
            body["code"] = self.code
        return body


def create_ebay_draft(item: dict[str, Any], timeout: float = DEFAULT_TIMEOUT_SECONDS) -> dict[str, Any]:
    listing = normalize_listing(item)
    _validate_listing(listing)
    _validate_draft_config()
    sku = _sku(item, listing)
    quantity = _quantity(item)
    price = float(listing.get("price") or 0)
    token = _seller_token(timeout)

    inventory_payload = _inventory_item_payload(listing, quantity)
    _request(
        "PUT",
        f"{_api_base_url()}/sell/inventory/v1/inventory_item/{quote(sku, safe='')}",
        token,
        inventory_payload,
        timeout,
        expected_statuses={200, 201, 204},
    )

    offer_payload = _offer_payload(sku, listing, quantity, price)
    offer_response = _request(
        "POST",
        f"{_api_base_url()}/sell/inventory/v1/offer",
        token,
        offer_payload,
        timeout,
        expected_statuses={200, 201},
    )
    offer_id = str(offer_response.get("offerId") or "")
    if not offer_id:
        raise EbayDraftError(502, "malformed_json", "eBay created an offer response without an offerId.")

    return {
        "status": "draft_created",
        "provider": "ebay_inventory",
        "sku": sku,
        "offerId": offer_id,
        "marketplaceId": _marketplace_id(),
        "published": False,
        "title": listing["title"],
        "price": round(price, 2),
        "warnings": _draft_warnings(listing),
    }


def _seller_token(timeout: float) -> str:
    try:
        return seller_access_token(timeout=timeout)
    except EbayAuthError as exc:
        raise EbayDraftError(exc.status_code, exc.category, exc.safe_message, exc.code) from exc


def _request(
    method: str,
    url: str,
    token: str,
    payload: dict[str, Any],
    timeout: float,
    expected_statuses: set[int],
) -> dict[str, Any]:
    try:
        response = requests.request(
            method,
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Content-Language": "en-US",
                "X-EBAY-C-MARKETPLACE-ID": _marketplace_id(),
            },
            json=payload,
            timeout=timeout,
        )
    except requests.Timeout as exc:
        raise EbayDraftError(504, "timeout", "eBay draft request timed out.") from exc
    except requests.RequestException as exc:
        raise EbayDraftError(502, "transport", "eBay draft request failed.") from exc

    if response.status_code not in expected_statuses:
        code = _ebay_error_code(response)
        raise EbayDraftError(
            response.status_code,
            _category_for_status(response.status_code),
            _safe_error_message(response.status_code, code),
            code,
        )
    if response.status_code == 204:
        return {}
    try:
        body = response.json()
    except ValueError as exc:
        raise EbayDraftError(502, "malformed_json", "eBay returned invalid JSON.") from exc
    if not isinstance(body, dict):
        raise EbayDraftError(502, "malformed_json", "eBay returned an unexpected draft response.")
    return body


def _inventory_item_payload(listing: dict[str, Any], quantity: int) -> dict[str, Any]:
    image_urls = _image_urls(listing.get("pic"))
    product: dict[str, Any] = {
        "title": listing["title"],
        "description": listing["desc"],
        "aspects": _product_aspects(listing),
    }
    if image_urls:
        product["imageUrls"] = image_urls
    return {
        "availability": {"shipToLocationAvailability": {"quantity": quantity}},
        "condition": _inventory_condition(listing.get("cid")),
        "conditionDescription": listing.get("cnote") or "See seller description and photos.",
        "product": product,
    }


def _offer_payload(sku: str, listing: dict[str, Any], quantity: int, price: float) -> dict[str, Any]:
    return {
        "sku": sku,
        "marketplaceId": _marketplace_id(),
        "format": "FIXED_PRICE",
        "availableQuantity": quantity,
        "categoryId": listing["cat"],
        "listingDescription": listing["desc"],
        "listingDuration": os.environ.get("EBAY_LISTING_DURATION", DEFAULT_LISTING_DURATION).strip() or DEFAULT_LISTING_DURATION,
        "merchantLocationKey": _required_env("EBAY_MERCHANT_LOCATION_KEY"),
        "listingPolicies": {
            "fulfillmentPolicyId": _required_env("EBAY_FULFILLMENT_POLICY_ID"),
            "paymentPolicyId": _required_env("EBAY_PAYMENT_POLICY_ID"),
            "returnPolicyId": _required_env("EBAY_RETURN_POLICY_ID"),
        },
        "pricingSummary": {
            "price": {
                "value": f"{price:.2f}",
                "currency": os.environ.get("EBAY_CURRENCY", DEFAULT_CURRENCY).strip() or DEFAULT_CURRENCY,
            }
        },
    }


def _product_aspects(listing: dict[str, Any]) -> dict[str, list[str]]:
    mapping = {
        "Brand": "brand",
        "Size": "size",
        "Color": "color",
        "Department": "dept",
        "Type": "type",
        "Style": "style",
        "Material": "mat",
        "Pattern": "pat",
        "Sleeve Length": "slv",
        "Neckline": "nk",
        "Season": "sea",
        "Occasion": "occ",
        "Size Type": "st",
        "Vintage": "vin",
    }
    aspects: dict[str, list[str]] = {}
    for label, key in mapping.items():
        value = _clean_aspect_value(listing.get(key))
        if value:
            aspects[label] = [value]
    return aspects


def _validate_listing(listing: dict[str, Any]) -> None:
    if not listing.get("title"):
        raise EbayDraftError(400, "invalid_request", "Title is required before creating an eBay draft.")
    if len(str(listing.get("title") or "")) > 80:
        raise EbayDraftError(400, "invalid_request", "Title must be 80 characters or fewer.")
    if not listing.get("cat"):
        raise EbayDraftError(400, "invalid_request", "Category ID is required before creating an eBay draft.")
    if float(listing.get("price") or 0) <= 0:
        raise EbayDraftError(400, "invalid_request", "Positive price is required before creating an eBay draft.")


def _validate_draft_config() -> None:
    _required_env("EBAY_MERCHANT_LOCATION_KEY")
    _required_env("EBAY_FULFILLMENT_POLICY_ID")
    _required_env("EBAY_PAYMENT_POLICY_ID")
    _required_env("EBAY_RETURN_POLICY_ID")


def _draft_warnings(listing: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    if not _image_urls(listing.get("pic")):
        warnings.append("No public image URL was sent to eBay. Add photos in eBay before publishing.")
    notes = str(listing.get("notes") or "").strip()
    if notes:
        warnings.append(notes[:240])
    return warnings


def _sku(item: dict[str, Any], listing: dict[str, Any]) -> str:
    existing = str(item.get("sku") or item.get("customLabel") or "").strip()
    if existing:
        return _safe_sku(existing)
    brand = str(listing.get("brand") or "HHT").strip()
    item_type = str(listing.get("type") or "ITEM").strip()
    stamp = time.strftime("%Y%m%d%H%M%S")
    return _safe_sku(f"{brand}-{item_type}-{stamp}")


def _safe_sku(value: str) -> str:
    sku = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-")
    return (sku or f"HHT-{time.strftime('%Y%m%d%H%M%S')}")[:50]


def _quantity(item: dict[str, Any]) -> int:
    try:
        quantity = int(item.get("quantity") or 1)
    except (TypeError, ValueError):
        quantity = 1
    return max(1, min(quantity, 99))


def _image_urls(value: Any) -> list[str]:
    urls = []
    for part in re.split(r"[\s,]+", str(value or "")):
        url = part.strip()
        if url.startswith("https://"):
            urls.append(url[:500])
    return urls[:12]


def _inventory_condition(condition_id: Any) -> str:
    return {
        "1000": "NEW",
        "1500": "NEW_OTHER",
        "3000": "USED_EXCELLENT",
        "4000": "USED_VERY_GOOD",
        "5000": "USED_GOOD",
        "6000": "USED_ACCEPTABLE",
    }.get(str(condition_id or "").strip(), "USED_EXCELLENT")


def _clean_aspect_value(value: Any) -> str:
    text = str(value or "").strip()
    if not text or text.lower() in {"not visible", "[seller to add image urls]"}:
        return ""
    return text[:65]


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise EbayDraftError(503, "configuration", f"{name} is not configured.")
    return value


def _environment() -> str:
    return os.environ.get("EBAY_ENVIRONMENT", "production").strip().lower()


def _marketplace_id() -> str:
    return os.environ.get("EBAY_MARKETPLACE_ID") or DEFAULT_MARKETPLACE_ID


def _api_base_url() -> str:
    return "https://api.sandbox.ebay.com" if _environment() == "sandbox" else "https://api.ebay.com"


def _category_for_status(status_code: int) -> str:
    if status_code in {401, 403}:
        return "authentication"
    if status_code == 409:
        return "conflict"
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
    return str(body.get("error") or body.get("code") or "")[:32]


def _safe_error_message(status_code: int, code: str) -> str:
    suffix = f" ({code})." if code else "."
    if status_code in {401, 403}:
        return f"eBay draft authentication failed{suffix}"
    if status_code == 409:
        return f"eBay draft already exists or conflicts with an existing offer{suffix}"
    if status_code == 429:
        return f"eBay draft rate limit reached{suffix}"
    if status_code >= 500:
        return f"eBay draft service failed{suffix}"
    return f"eBay draft request was rejected{suffix}"
