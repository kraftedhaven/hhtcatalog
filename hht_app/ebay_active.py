"""Active seller listing discovery and read-only detail enrichment through eBay's Trading API."""
from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from typing import Any

import requests

from .ebay_auth import EbayAuthError, seller_access_token

TRADING_ENDPOINT = "https://api.sandbox.ebay.com/ws/api.dll" if os.environ.get("EBAY_ENVIRONMENT", "production").lower() == "sandbox" else "https://api.ebay.com/ws/api.dll"
NS = "urn:ebay:apis:eBLBaseComponents"


class EbayActiveError(RuntimeError):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.safe_message = message


def _token(timeout: float) -> str:
    try:
        return seller_access_token(timeout=timeout)
    except EbayAuthError as exc:
        raise EbayActiveError(exc.status_code, exc.safe_message) from exc


def _trading_request(call_name: str, body: str, token: str, timeout: float) -> ET.Element:
    headers = {
        "Content-Type": "text/xml",
        "X-EBAY-API-CALL-NAME": call_name,
        "X-EBAY-API-SITEID": os.environ.get("EBAY_SITE_ID", "0"),
        "X-EBAY-API-COMPATIBILITY-LEVEL": os.environ.get("EBAY_TRADING_API_VERSION", "1221"),
        "X-EBAY-API-IAF-TOKEN": token,
    }
    try:
        response = requests.post(TRADING_ENDPOINT, headers=headers, data=body.encode("utf-8"), timeout=timeout)
    except requests.Timeout as exc:
        raise EbayActiveError(504, "eBay listing retrieval timed out.") from exc
    except requests.RequestException as exc:
        raise EbayActiveError(502, "eBay listing retrieval failed.") from exc
    if response.status_code >= 400:
        raise EbayActiveError(response.status_code, "eBay rejected listing detail retrieval.")
    try:
        root = ET.fromstring(response.content)
    except ET.ParseError as exc:
        raise EbayActiveError(502, "eBay returned invalid listing detail data.") from exc
    ack = _text(root, "Ack")
    if ack not in {"Success", "Warning"}:
        detail = _text(root, "LongMessage") or "eBay returned a listing detail error."
        raise EbayActiveError(502, detail[:240])
    return root


def fetch_listing_detail(item_id: str, timeout: float = 20.0) -> dict[str, Any]:
    """Fetch official read-only detail fields for one active eBay listing."""
    item_id = str(item_id or "").strip()
    if not item_id:
        raise EbayActiveError(400, "A listing ID is required for detail retrieval.")
    token = _token(timeout)
    body = f'''<?xml version="1.0" encoding="utf-8"?>
<GetItemRequest xmlns="{NS}">
  <RequesterCredentials><eBayAuthToken>{_xml(token)}</eBayAuthToken></RequesterCredentials>
  <ItemID>{_xml(item_id)}</ItemID>
  <DetailLevel>ReturnAll</DetailLevel>
  <IncludeItemSpecifics>true</IncludeItemSpecifics>
  <IncludeWatchCount>true</IncludeWatchCount>
</GetItemRequest>'''
    root = _trading_request("GetItem", body, token, timeout)
    item = root.find(f".//{{{NS}}}Item")
    if item is None:
        raise EbayActiveError(502, "eBay returned no detail record for the listing.")
    specifics: dict[str, str] = {}
    for entry in item.findall(f".//{{{NS}}}ItemSpecifics/{{{NS}}}NameValueList"):
        name = _text(entry, "Name")
        values = [str(node.text or "").strip() for node in entry.findall(f"{{{NS}}}Value") if str(node.text or "").strip()]
        if name and values:
            specifics[name] = ", ".join(dict.fromkeys(values))
    picture_urls = [str(node.text or "").strip() for node in item.findall(f".//{{{NS}}}PictureDetails/{{{NS}}}PictureURL") if str(node.text or "").strip()]
    current_price_node = item.find(f".//{{{NS}}}SellingStatus/{{{NS}}}CurrentPrice")
    return {
        "listingId": _text(item, "ItemID") or item_id,
        "title": _text(item, "Title"),
        "desc": _text(item, "Description"),
        "price": float((current_price_node.text or "0") if current_price_node is not None else 0),
        "currency": current_price_node.attrib.get("currencyID", "USD") if current_price_node is not None else "USD",
        "cat": _text(item.find(f"{{{NS}}}PrimaryCategory"), "CategoryID"),
        "catName": _text(item.find(f"{{{NS}}}PrimaryCategory"), "CategoryName"),
        "condition": _text(item, "ConditionID"),
        "cnote": _text(item, "ConditionDescription"),
        "quantity": int(_text(item, "Quantity") or 1),
        "quantitySold": int(_text(item.find(f"{{{NS}}}SellingStatus"), "QuantitySold") or 0),
        "pic": " ".join(picture_urls),
        "itemSpecifics": specifics,
        "watchCount": int(_text(item.find(f"{{{NS}}}SellingStatus"), "WatchCount") or 0),
        "country": _text(item, "Country"),
        "location": _text(item, "Location"),
        "sourceUpdatedAt": _text(item, "TimeLeft"),
        "source": "trading_get_item",
    }


def fetch_active_listings(page: int = 1, entries_per_page: int = 200, timeout: float = 20.0) -> dict[str, Any]:
    token = _token(timeout)
    site_id = os.environ.get("EBAY_SITE_ID", "0")
    version = os.environ.get("EBAY_TRADING_API_VERSION", "1221")
    body = f'''<?xml version="1.0" encoding="utf-8"?>
<GetMyeBaySellingRequest xmlns="{NS}">
  <RequesterCredentials><eBayAuthToken>{_xml(token)}</eBayAuthToken></RequesterCredentials>
  <DetailLevel>ReturnAll</DetailLevel>
  <ActiveList><Include>true</Include><Pagination><EntriesPerPage>{int(entries_per_page)}</EntriesPerPage><PageNumber>{int(page)}</PageNumber></Pagination></ActiveList>
</GetMyeBaySellingRequest>'''
    root = _trading_request("GetMyeBaySelling", body, token, timeout)
    items = [_item(node) for node in root.findall(f".//{{{NS}}}ActiveList/{{{NS}}}ItemArray/{{{NS}}}Item")]
    pagination = root.find(f".//{{{NS}}}ActiveList/{{{NS}}}PaginationResult")
    return {"items": items, "page": page, "entriesPerPage": entries_per_page, "totalEntries": int(_text(pagination, "TotalNumberOfEntries") or 0) if pagination is not None else len(items), "totalPages": int(_text(pagination, "TotalNumberOfPages") or 1) if pagination is not None else 1}


def _item(node: ET.Element) -> dict[str, Any]:
    def text(name: str) -> str:
        return _text(node, name)
    picture = [value.text or "" for value in node.findall(f".//{{{NS}}}PictureDetails/{{{NS}}}PictureURL")]
    current = node.find(f"{{{NS}}}SellingStatus/{{{NS}}}CurrentPrice")
    primary_category = node.find(f"{{{NS}}}PrimaryCategory")
    # GetMyeBaySelling returns ItemType.PrimaryCategory as a nested object.
    # Retain the legacy flat fallback only for older or non-standard fixtures.
    category_id = _text(primary_category, "CategoryID") or text("PrimaryCategoryID")
    category_name = _text(primary_category, "CategoryName")
    return {"listingId": text("ItemID"), "sku": text("SKU") or text("CustomLabel"), "customLabel": text("CustomLabel"), "title": text("Title"), "desc": text("Description"), "price": float(_text(node.find(f"{{{NS}}}SellingStatus"), "CurrentPrice") or 0), "currency": current.attrib.get("currencyID", "USD") if current is not None else "USD", "quantity": int(text("Quantity") or 1), "quantitySold": int(_text(node.find(f"{{{NS}}}SellingStatus"), "QuantitySold") or 0), "cat": category_id, "categoryName": category_name, "condition": text("ConditionID"), "cnote": text("ConditionDescription"), "pic": " ".join(picture), "lifecycle": "Active listing", "source": "trading_active", "ebayUrl": f"https://www.ebay.com/itm/{text('ItemID')}" if text("ItemID") else ""}


def _text(node: ET.Element | None, name: str) -> str:
    if node is None:
        return ""
    child = node.find(f"{{{NS}}}{name}")
    return (child.text or "").strip() if child is not None else ""


def _xml(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;").replace("'", "&apos;")
