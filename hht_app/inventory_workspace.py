"""Persisted spreadsheet-first Inventory Workspace for HHT Commerce Agent.

Provides seller-scoped normalized persistence for:
- inventory_batches
- inventory_items
- inventory_item_photos
- inventory_submissions

Compatible with existing schema.py, export_ebay_draft_csv, and Seller Hub draft feed.
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from .commerce_agent import (
    connect,
    init_db,
    check_listing_rules,
    validate_listing,
    start_listing_analysis_job,
    ebay_scope_status,
)
from .ebay_feed import (
    upload_seller_hub_draft_csv,
    get_feed_task_details,
    EbayFeedError,
    validate_draft_photo_urls,
)
from .market_metrics import pricing_recommendation, sold_price_summary
from .schema import fit_title, normalize_listing
from .title_optimizer import optimize_title

logger = logging.getLogger(__name__)

WORKFLOW_STATES = (
    "photo_staged",
    "grouped",
    "analysis_queued",
    "analysis_running",
    "needs_review",
    "ready_for_approval",
    "approved",
    "draft_submitted",
    "draft_result_available",
    "draft_failed",
)

REQUIRED_FIELDS = ("title", "price", "cat", "size")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False)


def _decode(val: Any, fallback: Any = None) -> Any:
    if val is None:
        return fallback
    if isinstance(val, (dict, list)):
        return val
    try:
        return json.loads(str(val))
    except (ValueError, TypeError):
        return fallback


# ---------------------------------------------------------------------------
# Batch Operations
# ---------------------------------------------------------------------------

def create_batch(seller_id: str, name: str = "", grouping_mode: str = "auto") -> dict[str, Any]:
    if not seller_id:
        raise ValueError("Seller ownership is required.")
    init_db()
    batch_id = str(uuid.uuid4())
    name = (name or "").strip() or f"Batch {datetime.now(timezone.utc).strftime('%b %d, %Y %H:%M')}"
    if grouping_mode not in {"1", "2", "3", "auto"}:
        grouping_mode = "auto"
    now = _now_iso()

    with connect() as db:
        db.execute(
            """INSERT INTO inventory_batches(id, seller_id, name, status, photo_grouping_mode, created_at, updated_at)
               VALUES(?, ?, ?, 'open', ?, ?, ?)""",
            (batch_id, seller_id, name, grouping_mode, now, now),
        )

    return get_batch(batch_id, seller_id)


def list_batches(seller_id: str) -> list[dict[str, Any]]:
    if not seller_id:
        raise ValueError("Seller ownership is required.")
    init_db()
    with connect() as db:
        rows = db.execute(
            """SELECT b.*,
                      (SELECT COUNT(*) FROM inventory_items i WHERE i.batch_id = b.id) AS item_count,
                      (SELECT COUNT(*) FROM inventory_item_photos p WHERE p.batch_id = b.id) AS photo_count
               FROM inventory_batches b
               WHERE b.seller_id = ?
               ORDER BY b.created_at DESC""",
            (seller_id,),
        ).fetchall()
        return [
            {
                "id": str(r["id"]),
                "sellerId": str(r["seller_id"]),
                "name": str(r["name"]),
                "status": str(r["status"]),
                "photoGroupingMode": str(r["photo_grouping_mode"]),
                "itemCount": int(r["item_count"] or 0),
                "photoCount": int(r["photo_count"] or 0),
                "createdAt": str(r["created_at"]),
                "updatedAt": str(r["updated_at"]),
            }
            for r in rows
        ]


def get_batch(batch_id: str, seller_id: str) -> dict[str, Any]:
    if not seller_id:
        raise ValueError("Seller ownership is required.")
    init_db()
    with connect() as db:
        row = db.execute(
            """SELECT b.*,
                      (SELECT COUNT(*) FROM inventory_items i WHERE i.batch_id = b.id) AS item_count,
                      (SELECT COUNT(*) FROM inventory_item_photos p WHERE p.batch_id = b.id) AS photo_count
               FROM inventory_batches b
               WHERE b.id = ? AND b.seller_id = ?""",
            (batch_id, seller_id),
        ).fetchone()
        if not row:
            raise KeyError(f"Batch {batch_id} not found.")
        return {
            "id": str(row["id"]),
            "sellerId": str(row["seller_id"]),
            "name": str(row["name"]),
            "status": str(row["status"]),
            "photoGroupingMode": str(row["photo_grouping_mode"]),
            "itemCount": int(row["item_count"] or 0),
            "photoCount": int(row["photo_count"] or 0),
            "createdAt": str(row["created_at"]),
            "updatedAt": str(row["updated_at"]),
        }


# ---------------------------------------------------------------------------
# Photo Registration & Grouping
# ---------------------------------------------------------------------------

def register_batch_photos(
    batch_id: str, seller_id: str, photos: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Register uploaded photos into an inventory batch and create initial item groups."""
    if not seller_id:
        raise ValueError("Seller ownership is required.")
    if not photos:
        return []
    batch = get_batch(batch_id, seller_id)
    grouping_mode = batch["photoGroupingMode"]

    registered_photos = []
    now = _now_iso()

    with connect() as db:
        # Get current highest position in batch
        pos_row = db.execute(
            "SELECT COALESCE(MAX(position), -1) AS max_pos FROM inventory_item_photos WHERE batch_id = ?",
            (batch_id,),
        ).fetchone()
        start_pos = (int(pos_row["max_pos"]) if pos_row else -1) + 1

        for idx, photo in enumerate(photos):
            photo_id = str(uuid.uuid4())
            photo_asset_id = photo.get("photoAssetId") or photo.get("assetId") or None
            original_filename = str(photo.get("filename") or photo.get("name") or f"photo_{start_pos + idx}.jpg")[:180]
            preview_url = str(photo.get("previewUrl") or photo.get("url") or "")
            storage_url = str(photo.get("storageUrl") or photo.get("ebayUrl") or preview_url)

            db.execute(
                """INSERT INTO inventory_item_photos(
                    id, batch_id, item_id, photo_asset_id, original_filename,
                    preview_url, storage_url, position, is_cover, created_at
                ) VALUES (?, ?, NULL, ?, ?, ?, ?, ?, 0, ?)""",
                (
                    photo_id,
                    batch_id,
                    photo_asset_id,
                    original_filename,
                    preview_url,
                    storage_url,
                    start_pos + idx,
                    now,
                ),
            )
            registered_photos.append({
                "id": photo_id,
                "batchId": batch_id,
                "photoAssetId": photo_asset_id,
                "originalFilename": original_filename,
                "previewUrl": preview_url,
                "storageUrl": storage_url,
                "position": start_pos + idx,
                "isCover": False,
            })

    # Apply grouping according to batch grouping mode
    apply_photo_grouping(batch_id, seller_id, mode=grouping_mode)
    return registered_photos


def apply_photo_grouping(
    batch_id: str, seller_id: str, mode: str = "auto", custom_groups: list[list[str]] | None = None
) -> list[dict[str, Any]]:
    """Group photos in a batch into inventory items.
    
    mode can be '1', '2', '3', or 'auto'.
    If custom_groups is given, each element is a list of photo IDs.
    """
    if not seller_id:
        raise ValueError("Seller ownership is required.")
    get_batch(batch_id, seller_id)
    now = _now_iso()

    with connect() as db:
        all_photos = db.execute(
            """SELECT * FROM inventory_item_photos
               WHERE batch_id = ?
               ORDER BY position ASC, created_at ASC""",
            (batch_id,),
        ).fetchall()
        photo_records = [dict(p) for p in all_photos]

        if not photo_records:
            return []

        # Determine groupings
        groupings: list[list[dict[str, Any]]] = []
        if custom_groups is not None:
            photo_by_id = {str(p["id"]): p for p in photo_records}
            for group in custom_groups:
                g = [photo_by_id[pid] for pid in group if pid in photo_by_id]
                if g:
                    groupings.append(g)
            # Gather unassigned photos
            assigned_ids = {str(p["id"]) for g in groupings for p in g}
            unassigned = [p for p in photo_records if str(p["id"]) not in assigned_ids]
            if unassigned:
                # Keep unassigned in their own single-photo groups or unassigned
                for p in unassigned:
                    groupings.append([p])
        else:
            step = 3
            if mode == "1":
                step = 1
            elif mode == "2":
                step = 2
            elif mode == "3":
                step = 3
            elif mode == "auto":
                step = 3

            for i in range(0, len(photo_records), step):
                groupings.append(photo_records[i : i + step])

        # Delete existing items for this batch to cleanly re-create or re-assign
        # Note: cascade removes items and dissociates photos (item_id SET NULL)
        db.execute("DELETE FROM inventory_items WHERE batch_id = ?", (batch_id,))

        created_items = []
        for g_idx, group in enumerate(groupings):
            item_id = str(uuid.uuid4())
            cover_photo = group[0] if group else None
            cover_url = cover_photo["storage_url"] or cover_photo["preview_url"] if cover_photo else ""

            # Check if group might be duplicate
            filenames = [p["original_filename"] for p in group]
            has_duplicates = len(filenames) != len(set(filenames))

            # Base empty listing JSON
            initial_listing = {
                "title": "",
                "price": "",
                "cat": "",
                "cid": "",
                "brand": "",
                "size": "",
                "cnote": "Pre-owned in good condition.",
                "cid_condition": "3000",
                "quantity": 1,
                "pic": " ".join(filter(None, [p["storage_url"] or p["preview_url"] for p in group])),
                "coverUrl": cover_url,
                "photoCount": len(group),
                "notes": "Group flagged: duplicate photo filenames detected." if has_duplicates else "",
            }

            initial_provenance = {
                "title": {"source": "missing", "confidence": "none", "evidence": "Unanalyzed", "sellerConfirmed": False},
                "price": {"source": "missing", "confidence": "none", "evidence": "Unanalyzed", "sellerConfirmed": False},
                "brand": {"source": "missing", "confidence": "none", "evidence": "Unanalyzed", "sellerConfirmed": False},
                "size": {"source": "missing", "confidence": "none", "evidence": "Unanalyzed", "sellerConfirmed": False},
                "cat": {"source": "missing", "confidence": "none", "evidence": "Unanalyzed", "sellerConfirmed": False},
            }

            validation = check_listing_rules(initial_listing, len(group), {})

            db.execute(
                """INSERT INTO inventory_items(
                    id, batch_id, seller_id, listing_json, provenance_json,
                    workflow_status, validation_json, pricing_options_json,
                    title_candidates_json, analysis_job_id, seller_confirmed,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'grouped', ?, '{}', '{}', '', 0, ?, ?)""",
                (
                    item_id,
                    batch_id,
                    seller_id,
                    _json(initial_listing),
                    _json(initial_provenance),
                    _json(validation),
                    now,
                    now,
                ),
            )

            # Update photos for this item
            for p_pos, photo in enumerate(group):
                is_cover = 1 if p_pos == 0 else 0
                db.execute(
                    """UPDATE inventory_item_photos
                       SET item_id = ?, position = ?, is_cover = ?
                       WHERE id = ?""",
                    (item_id, p_pos, is_cover, photo["id"]),
                )

            created_items.append(item_id)

    return list_batch_items(batch_id, seller_id)


# ---------------------------------------------------------------------------
# Listing & Inspecting Items
# ---------------------------------------------------------------------------

def list_batch_items(batch_id: str, seller_id: str) -> list[dict[str, Any]]:
    if not seller_id:
        raise ValueError("Seller ownership is required.")
    init_db()
    with connect() as db:
        rows = db.execute(
            """SELECT i.*
               FROM inventory_items i
               WHERE i.batch_id = ? AND i.seller_id = ?
               ORDER BY i.created_at ASC""",
            (batch_id, seller_id),
        ).fetchall()

        # Fetch all photos for this batch
        photos_rows = db.execute(
            """SELECT p.*
               FROM inventory_item_photos p
               WHERE p.batch_id = ?
               ORDER BY p.item_id, p.position ASC""",
            (batch_id,),
        ).fetchall()

        photos_by_item: dict[str, list[dict[str, Any]]] = {}
        for p in photos_rows:
            item_id = str(p["item_id"]) if p["item_id"] else "unassigned"
            photos_by_item.setdefault(item_id, []).append({
                "id": str(p["id"]),
                "photoAssetId": str(p["photo_asset_id"]) if p["photo_asset_id"] else None,
                "originalFilename": str(p["original_filename"]),
                "previewUrl": str(p["preview_url"]),
                "storageUrl": str(p["storage_url"]),
                "position": int(p["position"] or 0),
                "isCover": bool(p["is_cover"]),
            })

        items = []
        for r in rows:
            iid = str(r["id"])
            item_photos = photos_by_item.get(iid, [])
            listing = _decode(r["listing_json"], {})
            provenance = _decode(r["provenance_json"], {})
            validation = _decode(r["validation_json"], {})
            pricing_options = _decode(r["pricing_options_json"], {})
            title_candidates = _decode(r["title_candidates_json"], {})

            cover_photo = next((p for p in item_photos if p["isCover"]), None) or (item_photos[0] if item_photos else None)

            items.append({
                "id": iid,
                "batchId": str(r["batch_id"]),
                "sellerId": str(r["seller_id"]),
                "listing": listing,
                "provenance": provenance,
                "workflowStatus": str(r["workflow_status"]),
                "validation": validation,
                "pricingOptions": pricing_options,
                "titleCandidates": title_candidates,
                "analysisJobId": str(r["analysis_job_id"] or ""),
                "sellerConfirmed": bool(r["seller_confirmed"]),
                "photos": item_photos,
                "photoCount": len(item_photos),
                "coverThumbnail": cover_photo["previewUrl"] if cover_photo else "",
                "createdAt": str(r["created_at"]),
                "updatedAt": str(r["updated_at"]),
            })
        return items


def get_item(item_id: str, seller_id: str) -> dict[str, Any]:
    if not seller_id:
        raise ValueError("Seller ownership is required.")
    init_db()
    with connect() as db:
        row = db.execute(
            "SELECT * FROM inventory_items WHERE id = ? AND seller_id = ?",
            (item_id, seller_id),
        ).fetchone()
        if not row:
            raise KeyError(f"Item {item_id} not found.")

        photo_rows = db.execute(
            "SELECT * FROM inventory_item_photos WHERE item_id = ? ORDER BY position ASC",
            (item_id,),
        ).fetchall()
        photos = [
            {
                "id": str(p["id"]),
                "photoAssetId": str(p["photo_asset_id"]) if p["photo_asset_id"] else None,
                "originalFilename": str(p["original_filename"]),
                "previewUrl": str(p["preview_url"]),
                "storageUrl": str(p["storage_url"]),
                "position": int(p["position"] or 0),
                "isCover": bool(p["is_cover"]),
            }
            for p in photo_rows
        ]
        cover_photo = next((p for p in photos if p["isCover"]), None) or (photos[0] if photos else None)

        return {
            "id": str(row["id"]),
            "batchId": str(row["batch_id"]),
            "sellerId": str(row["seller_id"]),
            "listing": _decode(row["listing_json"], {}),
            "provenance": _decode(row["provenance_json"], {}),
            "workflowStatus": str(row["workflow_status"]),
            "validation": _decode(row["validation_json"], {}),
            "pricingOptions": _decode(row["pricing_options_json"], {}),
            "titleCandidates": _decode(row["title_candidates_json"], {}),
            "analysisJobId": str(row["analysis_job_id"] or ""),
            "sellerConfirmed": bool(row["seller_confirmed"]),
            "photos": photos,
            "photoCount": len(photos),
            "coverThumbnail": cover_photo["previewUrl"] if cover_photo else "",
            "createdAt": str(row["created_at"]),
            "updatedAt": str(row["updated_at"]),
        }


# ---------------------------------------------------------------------------
# Patching Editable Fields & Provenance Tracking
# ---------------------------------------------------------------------------

def patch_item(item_id: str, seller_id: str, patch: dict[str, Any]) -> dict[str, Any]:
    """Patch editable item fields with seller provenance tracking and auto re-validation."""
    if not seller_id:
        raise ValueError("Seller ownership is required.")
    current_item = get_item(item_id, seller_id)
    listing = dict(current_item["listing"])
    provenance = dict(current_item["provenance"])
    pricing_options = dict(current_item["pricingOptions"])
    title_candidates = dict(current_item["titleCandidates"])
    current_status = current_item["workflowStatus"]

    # Allowed fields to patch
    editable_keys = {
        "title", "price", "cat", "cid", "brand", "model", "size",
        "color", "dept", "type", "style", "mat", "pat", "cnote",
        "cid_condition", "quantity", "desc", "madeIn", "serialNumber",
        "measurements", "itemSpecifics"
    }

    modified_fields = set()
    for key, val in patch.items():
        if key in editable_keys:
            prev_val = listing.get(key)
            if prev_val != val:
                listing[key] = val
                modified_fields.add(key)
                provenance[key] = {
                    "source": "seller_input",
                    "confidence": "high",
                    "evidence": "Seller edited value",
                    "sellerConfirmed": True,
                }
                if key == "title":
                    listing["sellerEditedTitle"] = True
                elif key == "price":
                    listing["sellerEditedPrice"] = True

    # Handle selecting a title candidate
    chosen_candidate = patch.get("selectedTitleCandidate")
    if chosen_candidate and chosen_candidate in title_candidates:
        candidate_info = title_candidates[chosen_candidate]
        listing["title"] = candidate_info.get("title", listing.get("title", ""))
        listing["sellerEditedTitle"] = True
        modified_fields.add("title")
        provenance["title"] = {
            "source": "ai_estimate",
            "confidence": candidate_info.get("confidence", "medium"),
            "evidence": f"Selected candidate: {chosen_candidate}",
            "sellerConfirmed": True,
        }

    # Handle selecting a pricing option
    chosen_pricing = patch.get("selectedPricingOption")
    if chosen_pricing and chosen_pricing in pricing_options:
        opt = pricing_options[chosen_pricing]
        listing["price"] = str(opt.get("price", listing.get("price", "")))
        requires_confirm = bool(patch.get("confirmPrice", True))
        modified_fields.add("price")
        provenance["price"] = {
            "source": "ai_estimate",
            "confidence": opt.get("confidence", "medium"),
            "evidence": f"Selected pricing option: {chosen_pricing}",
            "sellerConfirmed": not requires_confirm,
        }

    # Handle explicit seller confirmations
    confirm_field = patch.get("confirmField")
    if confirm_field and confirm_field in provenance:
        provenance[confirm_field]["sellerConfirmed"] = True

    if patch.get("confirmAll") is True:
        for f in provenance.values():
            if isinstance(f, dict):
                f["sellerConfirmed"] = True

    # Auto-normalize listing
    listing = normalize_listing(listing)

    # Re-evaluate validation
    photo_count = current_item["photoCount"]
    validation = check_listing_rules(listing, photo_count, {})

    # Evaluate workflow status transition
    # Required fields must exist and pass validation
    has_required = all(bool(str(listing.get(f) or "").strip()) for f in ("title", "price", "cat", "size"))
    all_confirmed = all(
        provenance.get(f, {}).get("sellerConfirmed") is True for f in ("title", "price")
    )
    
    new_status = current_status
    if current_status not in {"draft_submitted", "draft_result_available"}:
        if validation["passed"] and has_required and all_confirmed:
            if current_status in {"needs_review", "grouped", "analysis_running"}:
                new_status = "ready_for_approval"
        else:
            if current_status in {"ready_for_approval", "approved"}:
                new_status = "needs_review"

    now = _now_iso()
    with connect() as db:
        db.execute(
            """UPDATE inventory_items
               SET listing_json = ?, provenance_json = ?, validation_json = ?,
                   workflow_status = ?, updated_at = ?
               WHERE id = ? AND seller_id = ?""",
            (
                _json(listing),
                _json(provenance),
                _json(validation),
                new_status,
                now,
                item_id,
                seller_id,
            ),
        )

    return get_item(item_id, seller_id)


# ---------------------------------------------------------------------------
# Validation & Approval Gating
# ---------------------------------------------------------------------------

def validate_items(item_ids: list[str], seller_id: str) -> dict[str, Any]:
    """Run validation checks on selected items and update their validation states."""
    if not seller_id:
        raise ValueError("Seller ownership is required.")
    results = []
    for item_id in item_ids:
        item = get_item(item_id, seller_id)
        listing = item["listing"]
        photo_count = item["photoCount"]
        # In production taxonomy validation is attempted
        taxonomy = validate_listing(listing, timeout=2.0)
        validation = check_listing_rules(listing, photo_count, taxonomy)

        has_required = all(bool(str(listing.get(f) or "").strip()) for f in ("title", "price", "cat", "size"))
        all_confirmed = all(
            item["provenance"].get(f, {}).get("sellerConfirmed") is True for f in ("title", "price")
        )

        current_status = item["workflowStatus"]
        new_status = current_status
        if current_status not in {"draft_submitted", "draft_result_available"}:
            if validation["passed"] and has_required and all_confirmed:
                new_status = "ready_for_approval"
            else:
                new_status = "needs_review"

        now = _now_iso()
        with connect() as db:
            db.execute(
                """UPDATE inventory_items
                   SET validation_json = ?, workflow_status = ?, updated_at = ?
                   WHERE id = ? AND seller_id = ?""",
                (_json(validation), new_status, now, item_id, seller_id),
            )
        results.append({
            "itemId": item_id,
            "status": new_status,
            "passed": validation["passed"],
            "findings": validation["findings"],
        })
    return {"validatedCount": len(results), "results": results}


def approve_items(item_ids: list[str], seller_id: str) -> dict[str, Any]:
    """Approve selected valid items. Invalid items CANNOT be approved."""
    if not seller_id:
        raise ValueError("Seller ownership is required.")
    approved = []
    rejected = []

    for item_id in item_ids:
        item = get_item(item_id, seller_id)
        listing = item["listing"]
        photo_count = item["photoCount"]
        validation = item["validation"]
        if not validation or not validation.get("passed"):
            # Re-check in case it hasn't been validated yet
            validation = check_listing_rules(listing, photo_count, {})

        has_required = all(bool(str(listing.get(f) or "").strip()) for f in ("title", "price", "cat", "size"))
        if not validation["passed"] or not has_required:
            rejected.append({
                "itemId": item_id,
                "reason": "Item failed validation rules or is missing required fields (title, price, category, size).",
                "findings": validation.get("findings", []),
            })
            continue

        now = _now_iso()
        with connect() as db:
            db.execute(
                """UPDATE inventory_items
                   SET workflow_status = 'approved', seller_confirmed = 1, updated_at = ?
                   WHERE id = ? AND seller_id = ?""",
                (now, item_id, seller_id),
            )
        approved.append(item_id)

    return {
        "approvedCount": len(approved),
        "approvedItemIds": approved,
        "rejectedCount": len(rejected),
        "rejected": rejected,
    }


# ---------------------------------------------------------------------------
# Prepopulation & Batch Analysis
# ---------------------------------------------------------------------------

def prepopulate_item_from_analysis(
    item_id: str, seller_id: str, analysis_result: dict[str, Any], preserve_seller_edits: bool = True
) -> dict[str, Any]:
    """Merge an AI analysis result into an inventory item.
    
    Generates:
    - 2-3 title candidates via optimize_title
    - Pricing options: quick_sale, balanced, premium via market_metrics
    - Stores field-level provenance (source, confidence, evidence, sellerConfirmed)
    - Flags uncertain or missing fields
    - Does NOT invent brand, size, model, measurements, condition, or market comps.
    """
    item = get_item(item_id, seller_id)
    listing = dict(item["listing"])
    provenance = dict(item["provenance"])
    ai_listing = analysis_result.get("listing", {})

    # Existing seller edits to preserve
    seller_edited_keys = {
        k for k, p in provenance.items()
        if isinstance(p, dict) and p.get("source") == "seller_input" and p.get("sellerConfirmed") is True
    }

    # Extract field candidates
    for key in (
        "brand", "model", "size", "color", "dept", "type", "style",
        "mat", "pat", "slv", "nk", "sea", "occ", "st", "vin", "madeIn",
        "serialNumber", "measurements", "cnote"
    ):
        if preserve_seller_edits and key in seller_edited_keys:
            continue
        ai_val = str(ai_listing.get(key) or "").strip()
        if ai_val and ai_val.lower() not in {"not visible", "unknown", "n/a", "none"}:
            listing[key] = ai_val
            # Provenance
            groq_prov = ai_listing.get("provenance", {}).get(key)
            is_verified = groq_prov == "Known from image"
            provenance[key] = {
                "source": "photo_evidence" if is_verified else "ai_estimate",
                "confidence": "high" if is_verified else "medium",
                "evidence": f"Observed in item photos: '{ai_val}'" if is_verified else f"AI suggestion from visual context: '{ai_val}'",
                "sellerConfirmed": False,
            }
        elif key not in listing or not listing[key]:
            provenance[key] = {
                "source": "missing",
                "confidence": "none",
                "evidence": "Not observed in photos; seller confirmation required",
                "sellerConfirmed": False,
            }

    # eBay Category
    if not (preserve_seller_edits and "cat" in seller_edited_keys):
        cat = str(ai_listing.get("cat") or "").strip()
        cid = str(ai_listing.get("cid") or "").strip()
        if cat:
            listing["cat"] = cat
            listing["cid"] = cid
            provenance["cat"] = {
                "source": "ai_estimate",
                "confidence": "medium",
                "evidence": f"Suggested eBay Category: {cat} (ID {cid})",
                "sellerConfirmed": False,
            }

    # Title & Candidates via optimize_title
    title_plan = optimize_title(listing)
    candidates_dict = title_plan.get("candidates", {})
    title_candidates = {}
    for c_name, c_data in candidates_dict.items():
        title_candidates[c_name] = {
            "title": c_data["title"],
            "length": c_data["length"],
            "reason": c_data["reason"],
            "confidence": c_data["confidence"],
        }

    best_title = title_plan.get("title") or ai_listing.get("title") or ""
    if not (preserve_seller_edits and "title" in seller_edited_keys) and best_title:
        listing["title"] = best_title
        provenance["title"] = {
            "source": "ai_estimate",
            "confidence": title_plan.get("confidence", "medium"),
            "evidence": title_plan.get("reason", "Constructed from confirmed attributes"),
            "sellerConfirmed": False,
        }

    # Pricing recommendations via pricing_recommendation
    pricing_rec = pricing_recommendation(listing)
    pricing_options = {}
    base_price = None
    if pricing_rec.get("status") != "error" and pricing_rec.get("recommendedPrice"):
        base_price = float(pricing_rec["recommendedPrice"])
        low_p = float(pricing_rec.get("lowSoldPrice") or round(base_price * 0.85, 2))
        high_p = float(pricing_rec.get("highSoldPrice") or round(base_price * 1.15, 2))

        pricing_options = {
            "quick_sale": {
                "price": low_p,
                "label": "Quick sale",
                "confidence": pricing_rec.get("confidence", "medium"),
                "reason": "Competitive price for faster sell-through.",
            },
            "balanced": {
                "price": base_price,
                "label": "Balanced",
                "confidence": pricing_rec.get("confidence", "high"),
                "reason": f"Median estimate from {pricing_rec.get('pricingSource', 'model estimate')}.",
            },
            "premium": {
                "price": high_p,
                "label": "Premium",
                "confidence": pricing_rec.get("confidence", "low"),
                "reason": "Upper market tier for patience or pristine condition.",
            },
        }

    if not (preserve_seller_edits and "price" in seller_edited_keys):
        if base_price:
            listing["price"] = str(base_price)
            provenance["price"] = {
                "source": "ai_estimate",
                "confidence": pricing_rec.get("confidence", "medium"),
                "evidence": f"Suggested price from {pricing_rec.get('pricingSource', 'comparable analysis')}. Seller confirmation required.",
                "sellerConfirmed": False,
            }
        else:
            provenance["price"] = {
                "source": "missing",
                "confidence": "none",
                "evidence": "No market comps available; seller must provide numeric price.",
                "sellerConfirmed": False,
            }

    # Condition & Specifics
    if not (preserve_seller_edits and "cnote" in seller_edited_keys):
        listing["cnote"] = ai_listing.get("cnote") or "Pre-owned in good condition."
        provenance["cnote"] = {
            "source": "ai_estimate",
            "confidence": "medium",
            "evidence": "Standard pre-owned condition note",
            "sellerConfirmed": False,
        }

    # Validate
    validation = check_listing_rules(listing, item["photoCount"], {})

    now = _now_iso()
    with connect() as db:
        db.execute(
            """UPDATE inventory_items
               SET listing_json = ?, provenance_json = ?, validation_json = ?,
                   pricing_options_json = ?, title_candidates_json = ?,
                   workflow_status = 'needs_review', updated_at = ?
               WHERE id = ? AND seller_id = ?""",
            (
                _json(listing),
                _json(provenance),
                _json(validation),
                _json(pricing_options),
                _json(title_candidates),
                now,
                item_id,
                seller_id,
            ),
        )

    return get_item(item_id, seller_id)


def start_workspace_analysis(
    batch_id: str, seller_id: str, item_ids: list[str] | None = None
) -> dict[str, Any]:
    """Queue analysis for grouped items in a batch."""
    if not seller_id:
        raise ValueError("Seller ownership is required.")
    items = list_batch_items(batch_id, seller_id)
    if item_ids:
        items = [it for it in items if it["id"] in item_ids]

    queued_count = 0
    now = _now_iso()

    for item in items:
        # Mark as queued
        with connect() as db:
            db.execute(
                """UPDATE inventory_items
                   SET workflow_status = 'analysis_queued', updated_at = ?
                   WHERE id = ? AND seller_id = ?""",
                (now, item["id"], seller_id),
            )
        queued_count += 1

    return {"queuedCount": queued_count, "batchId": batch_id}


# ---------------------------------------------------------------------------
# Submission of Approved Drafts through Seller Hub Draft Feed
# ---------------------------------------------------------------------------

def submit_approved_drafts(
    item_ids: list[str], seller_id: str
) -> dict[str, Any]:
    """Submit only explicitly approved items through the existing Seller Hub draft-feed route."""
    if not seller_id:
        raise ValueError("Seller ownership is required.")
    if not item_ids:
        raise ValueError("No items selected for submission.")

    items_to_submit = []
    for item_id in item_ids:
        item = get_item(item_id, seller_id)
        if item["workflowStatus"] != "approved":
            raise ValueError(
                f"Item {item_id} is in status '{item['workflowStatus']}' and cannot be submitted. "
                "Only explicitly approved items can reach the Seller Hub draft feed."
            )
        listing = dict(item["listing"])
        # Ensure reviewAcknowledged is True for draft feed validation
        listing["reviewAcknowledged"] = True
        items_to_submit.append((item, listing))

    # Existing draft feed validation & upload
    listings = [l for _, l in items_to_submit]
    try:
        feed_result = upload_seller_hub_draft_csv(listings)
    except EbayFeedError as exc:
        now = _now_iso()
        with connect() as db:
            for item, _ in items_to_submit:
                db.execute(
                    """UPDATE inventory_items
                       SET workflow_status = 'draft_failed', updated_at = ?
                       WHERE id = ? AND seller_id = ?""",
                    (now, item["id"], seller_id),
                )
        raise

    feed_task_id = str(feed_result.get("taskId") or "")
    now = _now_iso()

    submissions = []
    with connect() as db:
        for item, _ in items_to_submit:
            sub_id = str(uuid.uuid4())
            db.execute(
                """INSERT INTO inventory_submissions(
                    id, item_id, seller_id, batch_id, feed_task_id, status, result_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'submitted', ?, ?, ?)""",
                (
                    sub_id,
                    item["id"],
                    seller_id,
                    item["batchId"],
                    feed_task_id,
                    _json(feed_result),
                    now,
                    now,
                ),
            )
            db.execute(
                """UPDATE inventory_items
                   SET workflow_status = 'draft_submitted', updated_at = ?
                   WHERE id = ? AND seller_id = ?""",
                (now, item["id"], seller_id),
            )
            submissions.append(sub_id)

    return {
        "feedTaskId": feed_task_id,
        "submittedCount": len(items_to_submit),
        "submissions": submissions,
        "feedResult": feed_result,
    }


def get_item_submission_status(item_id: str, seller_id: str) -> dict[str, Any]:
    """Retrieve actual per-item submission results from eBay feed task."""
    if not seller_id:
        raise ValueError("Seller ownership is required.")
    init_db()
    with connect() as db:
        row = db.execute(
            """SELECT * FROM inventory_submissions
               WHERE item_id = ? AND seller_id = ?
               ORDER BY created_at DESC LIMIT 1""",
            (item_id, seller_id),
        ).fetchone()

        if not row:
            return {"status": "none", "message": "No submission attempt recorded for this item."}

        feed_task_id = str(row["feed_task_id"])
        current_status = str(row["status"])
        result = _decode(row["result_json"], {})

        # If submitted, check task status from eBay
        if current_status in {"submitted", "processing"}:
            try:
                task_details = get_feed_task_details(feed_task_id)
                task_status = str(task_details.get("status") or "").lower()
                now = _now_iso()
                new_sub_status = current_status
                new_item_status = "draft_submitted"

                if task_status in {"completed", "success"}:
                    new_sub_status = "completed"
                    new_item_status = "draft_result_available"
                elif task_status in {"failed", "error"}:
                    new_sub_status = "failed"
                    new_item_status = "draft_failed"

                db.execute(
                    """UPDATE inventory_submissions
                       SET status = ?, result_json = ?, updated_at = ?
                       WHERE id = ?""",
                    (new_sub_status, _json(task_details), now, row["id"]),
                )
                db.execute(
                    """UPDATE inventory_items
                       SET workflow_status = ?, updated_at = ?
                       WHERE id = ?""",
                    (new_item_status, now, item_id),
                )
                return {
                    "submissionId": str(row["id"]),
                    "feedTaskId": feed_task_id,
                    "status": new_sub_status,
                    "itemWorkflowStatus": new_item_status,
                    "details": task_details,
                }
            except Exception as exc:
                logger.warning("Failed to refresh feed task %s: %s", feed_task_id, exc)

        return {
            "submissionId": str(row["id"]),
            "feedTaskId": feed_task_id,
            "status": current_status,
            "details": result,
        }
