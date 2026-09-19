"""eBay Taxonomy API adapter; advisory during audits and strict before mutations."""
from __future__ import annotations
import os, time
from typing import Any
import requests
from .ebay_pricing import ebay_access_token

_cache: dict[str, tuple[float, dict[str, Any]]] = {}

def validate_listing(item: dict[str, Any], timeout: float = 5.0) -> dict[str, Any]:
    category = str(item.get("cat") or "").strip()
    if not category:
        return {"status": "missing", "categoryId": "", "message": "Category is required and must be seller-confirmed."}
    if os.environ.get("EBAY_TAXONOMY_ENABLED", "false").lower() not in {"1", "true", "yes", "on"}:
        return {"status": "not_configured", "categoryId": category, "message": "Taxonomy validation is not enabled; seller must verify category and specifics."}
    try:
        token = ebay_access_token(timeout=timeout)
        marketplace = os.environ.get("EBAY_MARKETPLACE_ID", "EBAY_US")
        url = f"https://api.ebay.com/commerce/taxonomy/v1/category_tree/{marketplace}/get_item_aspects_for_category"
        response = requests.get(url, headers={"Authorization": f"Bearer {token}", "X-EBAY-C-MARKETPLACE-ID": marketplace}, params={"category_id": category}, timeout=timeout)
        if response.status_code >= 400:
            return {"status": "unavailable", "categoryId": category, "message": "eBay Taxonomy validation was unavailable; seller review required."}
        body = response.json()
        aspects = [entry.get("localizedAspectName") for entry in body.get("aspects", []) if isinstance(entry, dict) and entry.get("localizedAspectName")]
        return {"status": "valid", "categoryId": category, "requiredAspects": aspects[:80], "message": "Category accepted by eBay Taxonomy API."}
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
        response = requests.get("https://api.ebay.com/commerce/taxonomy/v1/category_tree/0/get_category_suggestions", headers={"Authorization": f"Bearer {token}", "X-EBAY-C-MARKETPLACE-ID": marketplace}, params={"q": query}, timeout=timeout)
        body = response.json() if response.status_code < 400 else {}
        result = {"status": "ok", "suggestions": body.get("categorySuggestions", [])[:10]}
    except Exception:
        result = {"status": "unavailable", "suggestions": []}
    _cache[key] = (time.time() + 900, result)
    return result
