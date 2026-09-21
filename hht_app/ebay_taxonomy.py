"""eBay Taxonomy API adapter; advisory during audits and strict before mutations."""
from __future__ import annotations
import os, time
from typing import Any
import requests
from .ebay_pricing import EbayBrowseError, ebay_access_token
from .schema import EBAY_ITEM_SPECIFICS
from .evidence import has_confirmed_value

_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_tree_cache: dict[str, tuple[float, str]] = {}
_aspect_cache: dict[str, tuple[float, list[str]]] = {}


def _default_tree_id(token: str, marketplace: str, timeout: float) -> str:
    cached = _tree_cache.get(marketplace)
    if cached and cached[0] > time.time():
        return cached[1]
    response = requests.get(
        f"{_api_base_url()}/commerce/taxonomy/v1/get_default_category_tree_id",
        headers={"Authorization": f"Bearer {token}", "X-EBAY-C-MARKETPLACE-ID": marketplace},
        params={"marketplace_id": marketplace}, timeout=timeout,
    )
    body = response.json() if response.status_code < 400 else {}
    tree_id = str(body.get("categoryTreeId") or "") if isinstance(body, dict) else ""
    if not tree_id:
        raise ValueError("default category tree unavailable")
    _tree_cache[marketplace] = (time.time() + 86400, tree_id)
    return tree_id

def validate_listing(item: dict[str, Any], timeout: float = 5.0) -> dict[str, Any]:
    category = str(item.get("cat") or "").strip()
    if not category:
        return {"status": "missing", "categoryId": "", "message": "Category is required and must be seller-confirmed."}
    if os.environ.get("EBAY_TAXONOMY_ENABLED", "false").lower() not in {"1", "true", "yes", "on"}:
        return {"status": "not_configured", "categoryId": category, "message": "Taxonomy validation is not enabled; seller must verify category and specifics."}
    try:
        token = ebay_access_token(timeout=timeout)
        marketplace = os.environ.get("EBAY_MARKETPLACE_ID", "EBAY_US")
        tree_id = _default_tree_id(token, marketplace, timeout)
        cache_key = f"{marketplace}:{tree_id}:{category}"
        cached = _aspect_cache.get(cache_key)
        if cached and cached[0] > time.time():
            required = cached[1]
        else:
            url = f"{_api_base_url()}/commerce/taxonomy/v1/category_tree/{tree_id}/get_item_aspects_for_category"
            response = requests.get(url, headers={"Authorization": f"Bearer {token}", "X-EBAY-C-MARKETPLACE-ID": marketplace}, params={"category_id": category}, timeout=timeout)
            if response.status_code >= 400:
                return {"status": "unavailable", "categoryId": category, "statusCode": response.status_code, "message": "eBay Taxonomy validation was unavailable; seller review required."}
            body = response.json()
            aspect_entries = body.get("aspects", []) if isinstance(body, dict) else []
            required = []
            for entry in aspect_entries:
                if not isinstance(entry, dict):
                    continue
                name = str(entry.get("localizedAspectName") or "")
                constraint = entry.get("aspectConstraint") if isinstance(entry.get("aspectConstraint"), dict) else {}
                if name and constraint.get("aspectRequired") is True:
                    required.append(name)
            _aspect_cache[cache_key] = (time.time() + 86400, required)
        field_for_label = {label.casefold(): key for label, key in EBAY_ITEM_SPECIFICS}
        missing = [name for name in required if not has_confirmed_value(item.get(field_for_label.get(name.casefold(), "")))]
        message = "Required item specifics are missing." if missing else "Category and required item specifics accepted by eBay Taxonomy API."
        return {
            "status": "valid",
            "categoryId": category,
            "requiredAspects": required[:80],
            "missingRequiredAspects": missing[:80],
            "aspectReviewRequired": bool(missing),
            "message": message,
        }
    except EbayBrowseError as exc:
        if exc.category == "configuration":
            return {"status": "not_configured", "categoryId": category, "message": "Taxonomy validation is enabled but eBay application credentials are not configured."}
        return {"status": "unavailable", "categoryId": category, "statusCode": exc.status_code, "category": exc.category, "message": "eBay Taxonomy validation was unavailable; seller review required."}
    except Exception:
        return {"status": "unavailable", "categoryId": category, "message": "eBay Taxonomy validation was unavailable; seller review required."}

def suggest_category(query: str, timeout: float = 5.0) -> dict[str, Any]:
    query = str(query or "").strip()
    if not query:
        return {"status": "missing", "suggestions": []}
    key = query.lower()
    cached = _cache.get(key)
    if cached and cached[0] > time.time():
        return cached[1]
    if os.environ.get("EBAY_TAXONOMY_ENABLED", "false").lower() not in {"1", "true", "yes", "on"}:
        return {"status": "not_configured", "suggestions": []}
    try:
        token = ebay_access_token(timeout=timeout)
        marketplace = os.environ.get("EBAY_MARKETPLACE_ID", "EBAY_US")
        tree_id = _default_tree_id(token, marketplace, timeout)
        response = requests.get(f"{_api_base_url()}/commerce/taxonomy/v1/category_tree/{tree_id}/get_category_suggestions", headers={"Authorization": f"Bearer {token}", "X-EBAY-C-MARKETPLACE-ID": marketplace}, params={"q": query}, timeout=timeout)
        body = response.json() if response.status_code < 400 else {}
        result = {"status": "ok", "suggestions": body.get("categorySuggestions", [])[:10]}
    except Exception:
        result = {"status": "unavailable", "suggestions": []}
    _cache[key] = (time.time() + 900, result)
    return result


def _api_base_url() -> str:
    return "https://api.sandbox.ebay.com" if os.environ.get("EBAY_ENVIRONMENT", "production").strip().lower() == "sandbox" else "https://api.ebay.com"
