"""Active seller listing discovery through eBay's Trading API."""
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


def fetch_active_listings(page: int = 1, entries_per_page: int = 200, timeout: float = 20.0) -> dict[str, Any]:
    try:
        token = seller_access_token(timeout=timeout)
    except EbayAuthError as exc:
        raise EbayActiveError(exc.status_code, exc.safe_message) from exc
    site_id = os.environ.get("EBAY_SITE_ID", "0")
    version = os.environ.get("EBAY_TRADING_API_VERSION", "1221")
    body = f"""<?xml version=\"1.0\" encoding=\"utf-8\"?>
<GetMyeBaySellingRequest xmlns=\"{NS}\">
  <RequesterCredentials><eBayAuthToken>{_xml(token)}</eBayAuthToken></RequesterCredentials>
  <ActiveList><Include>true</Include><Pagination><EntriesPerPage>{int(entries_per_page)}</EntriesPerPage><PageNumber>{int(page)}</PageNumber></Pagination></ActiveList>
</GetMyeBaySellingRequest>"""
    headers = {
        "Content-Type": "text/xml",
        "X-EBAY-API-CALL-NAME": "GetMyeBaySelling",
        "X-EBAY-API-SITEID": site_id,
        "X-EBAY-API-COMPATIBILITY-LEVEL": version,
        "X-EBAY-API-IAF-TOKEN": token,
    }
    try:
        response = requests.post(TRADING_ENDPOINT, headers=headers, data=body.encode("utf-8"), timeout=timeout)
    except requests.Timeout as exc:
        raise EbayActiveError(504, "eBay active listing retrieval timed out.") from exc
    except requests.RequestException as exc:
        raise EbayActiveError(502, "eBay active listing retrieval failed.") from exc
    if response.status_code >= 400:
        raise EbayActiveError(response.status_code, "eBay rejected active listing retrieval.")
    try:
        root = ET.fromstring(response.content)
    except ET.ParseError as exc:
        raise EbayActiveError(502, "eBay returned invalid active listing data.") from exc
    ack = _text(root, "Ack")
    if ack not in {"Success", "Warning"}:
        detail = _text(root, "LongMessage") or "eBay returned an active listing error."
        raise EbayActiveError(502, detail[:240])
    items = []
    for node in root.findall(f".//{{{NS}}}ActiveList/{{{NS}}}ItemArray/{{{NS}}}Item"):
        items.append(_item(node))
    pagination = root.find(f".//{{{NS}}}ActiveList/{{{NS}}}PaginationResult")
    return {
        "items": items,
        "page": page,
        "entriesPerPage": entries_per_page,
        "totalEntries": int(_text(pagination, "TotalNumberOfEntries") or 0) if pagination is not None else len(items),
        "totalPages": int(_text(pagination, "TotalNumberOfPages") or 1) if pagination is not None else 1,
    }


def _item(node: ET.Element) -> dict[str, Any]:
    def text(name: str) -> str:
        return _text(node, name)
    picture = [value.text or "" for value in node.findall(f".//{{{NS}}}PictureDetails/{{{NS}}}PictureURL")]
    return {
        "listingId": text("ItemID"), "sku": text("SKU") or text("CustomLabel"), "customLabel": text("CustomLabel"),
        "title": text("Title"), "desc": text("Description"), "price": float(_text(node.find(f"{{{NS}}}SellingStatus"), "CurrentPrice") or 0),
        "currency": (node.find(f"{{{NS}}}SellingStatus/{{{NS}}}CurrentPrice").attrib.get("currencyID", "USD") if node.find(f"{{{NS}}}SellingStatus/{{{NS}}}CurrentPrice") is not None else "USD"),
        "quantity": int(text("Quantity") or 1), "quantitySold": int(_text(node.find(f"{{{NS}}}SellingStatus"), "QuantitySold") or 0),
        "cat": text("PrimaryCategoryID"), "condition": text("ConditionID"), "cnote": text("ConditionDescription"),
        "pic": " ".join(picture), "lifecycle": "Active listing", "source": "trading_active",
        "ebayUrl": f"https://www.ebay.com/itm/{text('ItemID')}" if text("ItemID") else "",
    }


def _text(node: ET.Element | None, name: str) -> str:
    if node is None:
        return ""
    child = node.find(f"{{{NS}}}{name}")
    return (child.text or "").strip() if child is not None else ""


def _xml(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;").replace("'", "&apos;")
