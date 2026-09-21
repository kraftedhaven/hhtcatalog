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
_aspect_metadata_cache: dict[str, tuple[float, dict[str, Any]]] = {}


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
    try:
        metadata = category_aspects(category, timeout)
        if metadata["status"] != "ok":
            return {**metadata, "message": metadata.get("message") or "eBay Taxonomy validation was unavailable; seller review required."}
        required = metadata["requiredAspects"]
        missing = [name for name in required if not has_confirmed_value(_aspect_value(item, name))]
        message = "Required item specifics are missing." if missing else "Category and required item specifics accepted by eBay Taxonomy API."
        return {
            "status": "valid",
            "categoryId": category,
            "requiredAspects": required[:80],
            "missingRequiredAspects": missing[:80],
            "aspectReviewRequired": bool(missing),
            "message": message,
        }
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
        if response.status_code >= 400:
            result = {"status": "unavailable", "suggestions": [], "message": "eBay category suggestions were unavailable."}
        else:
            body = response.json()
            source = body.get("categorySuggestions", []) if isinstance(body, dict) else []
            result = {"status": "ok", "suggestions": [_category_suggestion(entry) for entry in source[:10] if _category_suggestion(entry)]}
    except Exception:
        result = {"status": "unavailable", "suggestions": [], "message": "eBay category suggestions were unavailable."}
    _cache[key] = (time.time() + 900, result)
    return result


def category_aspects(category_id: str, timeout: float = 5.0) -> dict[str, Any]:
    """Return eBay's live required/recommended fields for one leaf category."""
    category = str(category_id or "").strip()
    if not category:
        return {"status": "missing", "categoryId": "", "fields": [], "requiredAspects": [], "message": "Select an eBay category first."}
    if os.environ.get("EBAY_TAXONOMY_ENABLED", "false").lower() not in {"1", "true", "yes", "on"}:
        return {"status": "not_configured", "categoryId": category, "fields": [], "requiredAspects": [], "message": "eBay Taxonomy is not enabled."}
    try:
        token = ebay_access_token(timeout=timeout)
        marketplace = os.environ.get("EBAY_MARKETPLACE_ID", "EBAY_US")
        tree_id = _default_tree_id(token, marketplace, timeout)
        cache_key = f"{marketplace}:{tree_id}:{category}"
        cached = _aspect_metadata_cache.get(cache_key)
        if cached and cached[0] > time.time():
            return cached[1]
        response = requests.get(
            f"{_api_base_url()}/commerce/taxonomy/v1/category_tree/{tree_id}/get_item_aspects_for_category",
            headers={"Authorization": f"Bearer {token}", "X-EBAY-C-MARKETPLACE-ID": marketplace},
            params={"category_id": category},
            timeout=timeout,
        )
        if response.status_code >= 400:
            return {"status": "unavailable", "categoryId": category, "statusCode": response.status_code, "fields": [], "requiredAspects": [], "message": "eBay Taxonomy fields were unavailable."}
        body = response.json()
        aspect_entries = body.get("aspects", []) if isinstance(body, dict) else []
        fields = [_aspect_field(entry) for entry in aspect_entries if _aspect_field(entry)]
        required = [field["name"] for field in fields if field["required"]]
        result = {
            "status": "ok",
            "categoryId": category,
            "fields": fields,
            "requiredAspects": required,
            "message": "Live eBay category fields loaded. Required fields are marked.",
        }
        _aspect_cache[cache_key] = (time.time() + 86400, required)
        _aspect_metadata_cache[cache_key] = (time.time() + 86400, result)
        return result
    except EbayBrowseError as exc:
        message = "Taxonomy is enabled but eBay application credentials are not configured." if exc.category == "configuration" else "eBay Taxonomy fields were unavailable."
        return {"status": "not_configured" if exc.category == "configuration" else "unavailable", "categoryId": category, "fields": [], "requiredAspects": [], "message": message}
    except Exception:
        return {"status": "unavailable", "categoryId": category, "fields": [], "requiredAspects": [], "message": "eBay Taxonomy fields were unavailable."}


def _category_suggestion(entry: Any) -> dict[str, Any] | None:
    if not isinstance(entry, dict):
        return None
    category = entry.get("category") if isinstance(entry.get("category"), dict) else {}
    category_id = str(category.get("categoryId") or "").strip()
    category_name = str(category.get("categoryName") or "").strip()
    if not category_id or not category_name:
        return None
    ancestors = entry.get("categoryTreeNodeAncestors") if isinstance(entry.get("categoryTreeNodeAncestors"), list) else []
    path = [str(ancestor.get("categoryName") or "").strip() for ancestor in reversed(ancestors) if isinstance(ancestor, dict) and str(ancestor.get("categoryName") or "").strip()]
    return {"categoryId": category_id, "categoryName": category_name, "path": " > ".join(path + [category_name])}


def _aspect_field(entry: Any) -> dict[str, Any] | None:
    if not isinstance(entry, dict):
        return None
    name = str(entry.get("localizedAspectName") or "").strip()
    if not name:
        return None
    constraint = entry.get("aspectConstraint") if isinstance(entry.get("aspectConstraint"), dict) else {}
    values = entry.get("aspectValues") if isinstance(entry.get("aspectValues"), list) else []
    return {
        "name": name,
        "required": constraint.get("aspectRequired") is True,
        "recommended": str(constraint.get("aspectUsage") or "").upper() == "RECOMMENDED",
        "multiSelect": str(constraint.get("itemToAspectCardinality") or "").upper() == "MULTI",
        "dataType": str(constraint.get("aspectDataType") or "STRING"),
        "values": [str(value.get("localizedValue") or "").strip() for value in values[:80] if isinstance(value, dict) and str(value.get("localizedValue") or "").strip()],
    }


def _aspect_value(item: dict[str, Any], label: str) -> Any:
    specifics = item.get("itemSpecifics") if isinstance(item.get("itemSpecifics"), dict) else {}
    for key, value in specifics.items():
        if str(key).casefold() == label.casefold():
            return value
    aliases = {label.casefold(): key for label, key in EBAY_ITEM_SPECIFICS}
    aliases.update({"model": "model", "theme": "theme", "country/region of manufacture": "madeIn", "country of manufacture": "madeIn", "measurements": "measurements"})
    return item.get(aliases.get(label.casefold(), ""))


def _api_base_url() -> str:
    return "https://api.sandbox.ebay.com" if os.environ.get("EBAY_ENVIRONMENT", "production").strip().lower() == "sandbox" else "https://api.ebay.com"
