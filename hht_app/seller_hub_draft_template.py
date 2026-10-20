"""Official eBay Seller Hub Reports Draft CSV template parser and generator.

Targets the official eBay Seller Hub Reports "Create drafts" template:
Reports → Upload → Get template → Listings → Create drafts template → CSV.

Strictly adheres to:
1. Preserving official #INFO rows and header columns.
2. Action = Draft (never Add, Revise, Relist, End, or Publish).
3. Numeric Category ID.
4. Title 1-80 characters.
5. Approved Price > 0.
6. Quantity positive integer.
7. Valid HTTPS IBM COS / public image URLs (pipe-separated).
8. Preserving category-specific item specifics without inventing facts.
9. No custom HHT-only columns in the export.
10. Blocking export when any row fails validation with exact row and field.
"""
from __future__ import annotations

import csv
import io
import re
from typing import Any
from urllib.parse import urlparse

from .schema import (
    DRAFT_ACTION_HEADER,
    EBAY_DRAFT_COLUMNS,
    EBAY_DRAFT_INFO_ROWS,
    EBAY_ITEM_SPECIFICS,
    _draft_condition,
    _draft_sku,
    _price,
    _split_draft_urls,
    _text,
    deduplicate_draft_items,
    draft_photo_urls,
    fit_title,
    normalize_listing,
)


LIVE_ACTIONS = {"add", "revise", "relist", "end", "publish", "verifyadd"}


class DraftExportValidationError(ValueError):
    """Raised when one or more items fail Seller Hub Draft CSV validation."""
    def __init__(self, errors: list[dict[str, Any]]):
        self.errors = errors
        first_msg = errors[0]["message"] if errors else "Validation failed"
        super().__init__(f"Seller Hub Draft CSV validation failed: {first_msg} ({len(errors)} error(s))")


def build_default_template_text() -> str:
    """Build the official default eBay Create Drafts CSV template text."""
    out = io.StringIO(newline="")
    writer = csv.writer(out, lineterminator="\n")
    for info_row in EBAY_DRAFT_INFO_ROWS:
        writer.writerow(info_row + [""] * (len(EBAY_DRAFT_COLUMNS) - len(info_row)))
    writer.writerow(EBAY_DRAFT_COLUMNS)
    return out.getvalue()


def parse_official_template(template_text: str) -> dict[str, Any]:
    """Parse an official eBay Seller Hub Create Drafts CSV template.

    Extracts:
    - info_rows: preserved #INFO rows
    - headers: official column headers in exact order
    - action_header: header name for the Action column
    - category_specific_columns: columns starting with C: or matching item specifics
    """
    if not template_text or not template_text.strip():
        raise ValueError("Template content is empty.")

    reader = csv.reader(io.StringIO(template_text.strip()))
    info_rows: list[list[str]] = []
    headers: list[str] = []

    for row in reader:
        if not row:
            continue
        first_col = row[0].strip()
        if first_col.startswith("#INFO"):
            info_rows.append(row)
        else:
            headers = [c.strip() for c in row if c.strip() != ""]
            break

    if not headers:
        raise ValueError("Official template missing header row.")

    # Find the Action column
    action_header = ""
    for h in headers:
        if h.lower().startswith("action"):
            action_header = h
            break

    if not action_header:
        raise ValueError("Invalid official eBay template: missing 'Action' column in header.")

    # Check for Category ID
    cat_header = ""
    for h in headers:
        norm = re.sub(r"[^a-z0-9]+", "", h.lower())
        if norm in {"categoryid", "category", "catid"}:
            cat_header = h
            break

    if not cat_header:
        raise ValueError("Invalid official eBay template: missing 'Category ID' column in header.")

    # Check for Title
    title_header = ""
    for h in headers:
        if h.lower() == "title":
            title_header = h
            break

    if not title_header:
        raise ValueError("Invalid official eBay template: missing 'Title' column in header.")

    return {
        "info_rows": info_rows,
        "headers": headers,
        "action_header": action_header,
        "category_header": cat_header,
        "title_header": title_header,
    }


def validate_draft_photo_url(url: str) -> tuple[bool, str]:
    """Verify photo URL is valid HTTPS image URL."""
    clean = str(url or "").strip()
    if not clean:
        return False, "Photo URL is empty."
    if clean.lower().startswith(("file://", "blob:", "data:", "http://")):
        return False, f"Photo URL '{clean[:40]}...' must be HTTPS (no local file, blob, or unencrypted HTTP)."
    parsed = urlparse(clean)
    if parsed.scheme.lower() != "https" or not parsed.netloc:
        return False, f"Photo URL '{clean[:40]}...' is not a valid HTTPS URL."
    if parsed.netloc.lower() in {"localhost", "127.0.0.1", "0.0.0.0"}:
        return False, f"Photo URL cannot point to localhost."
    return True, ""


def validate_item_for_draft(
    item: dict[str, Any],
    row_index: int = 1,
    category_metadata: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Validate that one item satisfies all eBay Seller Hub Create Drafts requirements."""
    errors: list[dict[str, Any]] = []
    item_id = str(item.get("id") or item.get("itemId") or f"row-{row_index}")
    title = str(item.get("title") or item.get("listing", {}).get("title") or "").strip()
    cat = str(item.get("cat") or item.get("listing", {}).get("cat") or "").strip()
    price_val = item.get("price") if item.get("price") is not None else item.get("listing", {}).get("price")
    quantity_val = item.get("quantity") or item.get("listing", {}).get("quantity") or "1"
    workflow_status = str(item.get("workflowStatus") or "")

    def add_error(field: str, message: str) -> None:
        errors.append({
            "rowIndex": row_index,
            "itemId": item_id,
            "itemTitle": title[:40] if title else "(untitled)",
            "field": field,
            "message": message,
        })

    # Category ID must be numeric
    if not cat:
        add_error("Category ID", "Category ID is required.")
    elif not re.fullmatch(r"\d+", cat):
        add_error("Category ID", f"Category ID must be numeric (received: '{cat}').")

    # Title must be 1 to 80 characters
    if not title:
        add_error("Title", "Title is required.")
    elif len(title) > 80:
        add_error("Title", f"Title exceeds 80 characters (current length: {len(title)}).")

    # Price must be greater than zero
    parsed_price = _price(price_val)
    if parsed_price is None or parsed_price <= 0:
        add_error("Price", "Price must be a positive number greater than zero.")

    # Quantity must be a positive integer
    try:
        qty_int = int(quantity_val)
        if qty_int < 1:
            add_error("Quantity", "Quantity must be at least 1.")
    except (ValueError, TypeError):
        add_error("Quantity", f"Quantity must be a valid integer (received: '{quantity_val}').")

    # Photos must be valid HTTPS image URLs
    photo_urls = draft_photo_urls(item)
    if not photo_urls:
        add_error("Item photo URL", "At least one valid HTTPS item photo URL is required.")
    else:
        for p_url in photo_urls:
            valid, reason = validate_draft_photo_url(p_url)
            if not valid:
                add_error("Item photo URL", reason)

    # Category-specific required fields from metadata
    if category_metadata and category_metadata.get("status") == "ok":
        fields_meta = category_metadata.get("fields", [])
        field_dict = {f.get("name", "").casefold(): f for f in fields_meta if isinstance(f, dict)}
        specifics = item.get("itemSpecifics") or item.get("listing", {}).get("itemSpecifics") or {}
        if not isinstance(specifics, dict):
            specifics = {}

        for req_aspect in category_metadata.get("requiredAspects", []):
            req_name = str(req_aspect).strip()
            req_key = req_name.casefold()

            # Check in specifics, item top-level, or listing
            val = specifics.get(req_name)
            if val is None:
                val = next((v for k, v in specifics.items() if str(k).casefold() == req_key), None)
            if val is None:
                aliases = {label.casefold(): key for label, key in EBAY_ITEM_SPECIFICS}
                aliases.update({"model": "model", "size": "size", "brand": "brand", "color": "color", "country of manufacture": "madeIn", "made in": "madeIn"})
                field_alias = aliases.get(req_key, "")
                if field_alias:
                    val = item.get(field_alias) or item.get("listing", {}).get(field_alias)

            if not val or not str(val).strip() or str(val).lower() in {"not visible", "unknown", "n/a", "none"}:
                add_error(req_name, f"Required eBay category specific '{req_name}' is missing.")
            else:
                # If metadata has allowed values, check if value matches
                meta_field = field_dict.get(req_key)
                if meta_field and meta_field.get("values"):
                    allowed_vals = {v.casefold(): v for v in meta_field["values"]}
                    val_str = str(val).strip()
                    if val_str.casefold() not in allowed_vals and val_str.casefold() != "other":
                        # If free text is not indicated, flag invalid value
                        add_error(req_name, f"Value '{val_str}' is not in allowed eBay values for '{req_name}'.")

    return errors


def export_seller_hub_draft_csv(
    items: list[dict[str, Any]],
    template_text: str | None = None,
    category_metadata_map: dict[str, Any] | None = None,
) -> str:
    """Generate an official eBay Seller Hub Create Drafts CSV upload file.

    Guarantees:
    - Target: eBay Seller Hub Reports Create Drafts template.
    - Preserves #INFO rows and official column headers.
    - Action = Draft (never Add, Revise, Relist, End, or Publish).
    - Numeric Category ID.
    - Title 1-80 characters.
    - Valid HTTPS photo URLs.
    - Category-specific item specific columns preserved and populated only from verified facts.
    - No live-listing actions or custom HHT columns emitted.
    - Blocks export when any item fails validation.
    """
    unique_items = deduplicate_draft_items(items)
    if not unique_items:
        raise ValueError("Cannot export empty item list to Seller Hub Draft CSV.")

    parsed_template = parse_official_template(template_text or build_default_template_text())
    info_rows = parsed_template["info_rows"]
    headers = parsed_template["headers"]
    action_header = parsed_template["action_header"]
    cat_header = parsed_template["category_header"]
    title_header = parsed_template["title_header"]

    category_metadata_map = category_metadata_map or {}

    # Strict Validation of every row before export
    all_errors: list[dict[str, Any]] = []
    for idx, item in enumerate(unique_items, start=1):
        cat = str(item.get("cat") or item.get("listing", {}).get("cat") or "").strip()
        cat_meta = category_metadata_map.get(cat)
        row_errors = validate_item_for_draft(item, row_index=idx, category_metadata=cat_meta)
        all_errors.extend(row_errors)

    if all_errors:
        raise DraftExportValidationError(all_errors)

    out = io.StringIO(newline="")
    writer = csv.writer(out, lineterminator="\n")

    # 1. Preserve #INFO rows
    for row in info_rows:
        padded = row + [""] * max(0, len(headers) - len(row))
        writer.writerow(padded[:len(headers)])

    # 2. Write exact official headers
    writer.writerow(headers)

    # 3. Populate rows
    for item in unique_items:
        row_dict = _build_official_draft_row(item, headers, action_header, cat_header, title_header)
        # Safety check: ensure Action is strictly Draft
        action_val = str(row_dict.get(action_header) or "").strip()
        if action_val.lower() in LIVE_ACTIONS or action_val.lower() != "draft":
            row_dict[action_header] = "Draft"

        writer.writerow([row_dict.get(col, "") for col in headers])

    return out.getvalue()


def _build_official_draft_row(
    item: dict[str, Any],
    headers: list[str],
    action_header: str,
    cat_header: str,
    title_header: str,
) -> dict[str, Any]:
    listing_data = dict(item.get("listing", {})) if isinstance(item.get("listing"), dict) and item.get("listing") else {}
    for k, v in item.items():
        if k not in listing_data and k not in {"listing", "provenance", "validation", "pricingOptions", "titleCandidates"}:
            listing_data[k] = v
    listing = normalize_listing(listing_data)
    price = _price(listing.get("price"))
    sku = _draft_sku(item, listing)
    photos = draft_photo_urls(item)
    specifics = item.get("itemSpecifics") or listing.get("itemSpecifics") or {}
    if not isinstance(specifics, dict):
        specifics = {}

    row: dict[str, Any] = {}

    # Mandatory official fields
    row[action_header] = "Draft"
    row[cat_header] = _text(listing.get("cat"))
    row[title_header] = fit_title(_text(listing.get("title")), _text(listing.get("brand")))

    # Specifics lookup helper
    def get_specific_val(label: str) -> str:
        clean_lbl = label.strip()
        if clean_lbl.startswith("C:"):
            clean_lbl = clean_lbl[2:].strip()
        # Direct lookup
        if clean_lbl in specifics:
            v = specifics[clean_lbl]
            return "|".join(v) if isinstance(v, list) else str(v)
        # Case-insensitive lookup
        for k, v in specifics.items():
            if str(k).casefold() == clean_lbl.casefold():
                return "|".join(v) if isinstance(v, list) else str(v)
        # Normalized listing attributes
        aliases = {lbl.casefold(): key for lbl, key in EBAY_ITEM_SPECIFICS}
        aliases.update({"model": "model", "size": "size", "brand": "brand", "color": "color", "made in": "madeIn", "country of manufacture": "madeIn"})
        field_key = aliases.get(clean_lbl.casefold())
        if field_key:
            val = _text(item.get(field_key) or listing.get(field_key))
            if val and val.lower() not in {"not visible", "unknown", "n/a", "none"}:
                return val
        return ""

    for col in headers:
        if col in row:
            continue
        col_norm = re.sub(r"[^a-z0-9]+", "", col.lower())

        if col_norm in {"customlabelsku", "customlabel", "sku"}:
            row[col] = sku
        elif col_norm == "price":
            row[col] = f"{price:.2f}" if price else "0.00"
        elif col_norm == "quantity":
            row[col] = _text(item.get("quantity") or listing.get("quantity")) or "1"
        elif col_norm in {"itemphotourl", "picurl", "photourl"}:
            row[col] = "|".join(photos)
        elif col_norm in {"conditionid", "condition"}:
            row[col] = _draft_condition(listing.get("cid"))
        elif col_norm == "description":
            row[col] = _text(listing.get("desc"))
        elif col_norm == "format":
            row[col] = "FixedPrice"
        elif col_norm == "upc":
            upc = _text(item.get("upc") or listing.get("upc"))
            row[col] = upc if upc and upc.lower() not in {"not visible", "unknown", "n/a", "none"} else ""
        elif col.startswith("C:") or col_norm in {lbl.casefold() for lbl, _ in EBAY_ITEM_SPECIFICS}:
            row[col] = get_specific_val(col)
        else:
            # Check if this header matches an item specific
            val = get_specific_val(col)
            row[col] = val

    return row
