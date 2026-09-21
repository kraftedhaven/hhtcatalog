"""Persistence and business logic for the HHT Commerce Agent MVP.

The default mode is recommendation-only. Live eBay changes are performed only by
an explicit approval route, and are delegated to the existing offer update flow.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import requests

try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError:  # Local SQLite fallback remains available without psycopg.
    psycopg = None
    dict_row = None

from .ebay_auth import EbayAuthError, seller_access_token
from .ebay_active import EbayActiveError, fetch_active_listings, fetch_listing_detail
from .ebay_drafts import EbayDraftError, get_ebay_offer, update_ebay_offer
from .schema import normalize_listing
from .evidence import evidence_for_listing, evidence_summary, normalize_evidence
from .ebay_taxonomy import validate_listing
from .title_optimizer import optimize_title
from .market_metrics import demand_score, pricing_recommendation, seller_recovery_metrics, sold_price_summary

logger = logging.getLogger(__name__)
DB_PATH = os.environ.get("COMMERCE_AGENT_DB", "commerce_agent.sqlite3")
MAX_TITLE_LENGTH = 80
EDITABLE_FIELDS = {"title", "price", "cid", "desc", "cat", "cnote", "notes", "pic", "brand", "size", "color", "dept", "type", "model", "style", "theme", "mat", "pat", "slv", "nk", "sea", "occ", "st", "vin", "madeIn", "serialNumber", "measurements"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _decode(value: str | None, fallback: Any) -> Any:
    try:
        return json.loads(value or "")
    except (TypeError, ValueError):
        return fallback


def _database_url() -> str:
    url = (os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DATABASE_URL") or os.environ.get("POSTGRES_URL") or "").strip()
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url and "sslmode=" not in url:
        url += "&sslmode=require" if "?" in url else "?sslmode=require"
    return url


class _Database:
    def __init__(self, connection, postgres: bool):
        self.connection = connection
        self.postgres = postgres

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        if exc_type:
            self.connection.rollback()
        else:
            self.connection.commit()
        self.connection.close()

    def execute(self, query: str, params: tuple[Any, ...] = ()):
        if self.postgres:
            query = query.replace("?", "%s")
        return self.connection.execute(query, params)

    def executescript(self, query: str):
        if self.postgres:
            for statement in query.split(";"):
                statement = statement.strip()
                if statement:
                    self.connection.execute(statement)
        else:
            self.connection.executescript(query)


def connect() -> _Database:
    url = _database_url()
    if url:
        if psycopg is None:
            raise RuntimeError("DATABASE_URL is configured but psycopg is not installed.")
        try:
            return _Database(psycopg.connect(url, row_factory=dict_row, connect_timeout=8), True)
        except Exception as exc:
            logger.error("Commerce Agent PostgreSQL connection failed: %s", type(exc).__name__)
            raise RuntimeError("Commerce Agent PostgreSQL connection failed.") from exc
    connection = sqlite3.connect(os.environ.get("COMMERCE_AGENT_DB", DB_PATH))
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return _Database(connection, False)


def init_db() -> None:
    with connect() as db:
        if db.postgres:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS listings (
                    id BIGSERIAL PRIMARY KEY,
                    listing_id TEXT NOT NULL DEFAULT '', offer_id TEXT NOT NULL DEFAULT '', sku TEXT NOT NULL,
                    marketplace TEXT NOT NULL DEFAULT 'EBAY_US', data_json TEXT NOT NULL,
                    source_updated_at TEXT, imported_at TEXT NOT NULL, UNIQUE(sku, marketplace)
                );
                CREATE TABLE IF NOT EXISTS recommendations (
                    id TEXT PRIMARY KEY, listing_row_id BIGINT NOT NULL REFERENCES listings(id),
                    current_json TEXT NOT NULL, proposed_json TEXT NOT NULL, findings_json TEXT NOT NULL,
                    score INTEGER NOT NULL, classification TEXT NOT NULL, reason TEXT NOT NULL,
                    confidence TEXT NOT NULL, risk TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'Pending',
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS actions (
                    id TEXT PRIMARY KEY, recommendation_id TEXT NOT NULL REFERENCES recommendations(id),
                    listing_row_id BIGINT NOT NULL REFERENCES listings(id), approved_json TEXT NOT NULL,
                    old_json TEXT NOT NULL, new_json TEXT NOT NULL DEFAULT '{}', status TEXT NOT NULL DEFAULT 'Approved',
                    ebay_result_json TEXT NOT NULL DEFAULT '{}', error TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL, applied_at TEXT
                );
                CREATE TABLE IF NOT EXISTS settings (
                    id INTEGER PRIMARY KEY CHECK(id = 1), mode TEXT NOT NULL DEFAULT 'recommend',
                    max_price_reduction_pct DOUBLE PRECISION NOT NULL DEFAULT 10, minimum_price DOUBLE PRECISION NOT NULL DEFAULT 0,
                    minimum_profit DOUBLE PRECISION NOT NULL DEFAULT 0, high_value_threshold DOUBLE PRECISION NOT NULL DEFAULT 250,
                    require_vintage INTEGER NOT NULL DEFAULT 1, require_designer INTEGER NOT NULL DEFAULT 1,
                    require_collectible INTEGER NOT NULL DEFAULT 1
                );
                CREATE TABLE IF NOT EXISTS commerce_jobs (
                    id TEXT PRIMARY KEY, kind TEXT NOT NULL, status TEXT NOT NULL,
                    progress INTEGER NOT NULL DEFAULT 0, result_json TEXT NOT NULL DEFAULT '{}',
                    error TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                INSERT INTO settings(id) VALUES(1) ON CONFLICT (id) DO NOTHING
                """
            )
        else:
            db.executescript(
                """
            CREATE TABLE IF NOT EXISTS listings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                listing_id TEXT NOT NULL DEFAULT '', offer_id TEXT NOT NULL DEFAULT '', sku TEXT NOT NULL,
                marketplace TEXT NOT NULL DEFAULT 'EBAY_US', data_json TEXT NOT NULL,
                source_updated_at TEXT, imported_at TEXT NOT NULL, UNIQUE(sku, marketplace)
            );
            CREATE TABLE IF NOT EXISTS recommendations (
                id TEXT PRIMARY KEY, listing_row_id INTEGER NOT NULL REFERENCES listings(id),
                current_json TEXT NOT NULL, proposed_json TEXT NOT NULL, findings_json TEXT NOT NULL,
                score INTEGER NOT NULL, classification TEXT NOT NULL, reason TEXT NOT NULL,
                confidence TEXT NOT NULL, risk TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'Pending',
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS actions (
                id TEXT PRIMARY KEY, recommendation_id TEXT NOT NULL REFERENCES recommendations(id),
                listing_row_id INTEGER NOT NULL REFERENCES listings(id), approved_json TEXT NOT NULL,
                old_json TEXT NOT NULL, new_json TEXT NOT NULL DEFAULT '{}', status TEXT NOT NULL DEFAULT 'Approved',
                ebay_result_json TEXT NOT NULL DEFAULT '{}', error TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL, applied_at TEXT
            );
            CREATE TABLE IF NOT EXISTS settings (
                id INTEGER PRIMARY KEY CHECK(id = 1), mode TEXT NOT NULL DEFAULT 'recommend',
                max_price_reduction_pct REAL NOT NULL DEFAULT 10, minimum_price REAL NOT NULL DEFAULT 0,
                minimum_profit REAL NOT NULL DEFAULT 0, high_value_threshold REAL NOT NULL DEFAULT 250,
                require_vintage INTEGER NOT NULL DEFAULT 1, require_designer INTEGER NOT NULL DEFAULT 1,
                require_collectible INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS commerce_jobs (
                id TEXT PRIMARY KEY, kind TEXT NOT NULL, status TEXT NOT NULL,
                progress INTEGER NOT NULL DEFAULT 0, result_json TEXT NOT NULL DEFAULT '{}',
                error TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            INSERT OR IGNORE INTO settings(id) VALUES (1);
            """
            )


def settings() -> dict[str, Any]:
    init_db()
    with connect() as db:
        row = db.execute("SELECT * FROM settings WHERE id=1").fetchone()
    return dict(row) if row else {"mode": "recommend", "max_price_reduction_pct": 10}


def update_settings(payload: dict[str, Any]) -> dict[str, Any]:
    current = settings()
    allowed = {"mode", "max_price_reduction_pct", "minimum_price", "minimum_profit", "high_value_threshold", "require_vintage", "require_designer", "require_collectible"}
    values = {key: payload[key] for key in allowed if key in payload}
    if values.get("mode") not in {None, "recommend", "auto-optimize", "autonomous"}:
        raise ValueError("mode must be recommend, auto-optimize, or autonomous")
    for key in ("max_price_reduction_pct", "minimum_price", "minimum_profit", "high_value_threshold"):
        if key in values and float(values[key]) < 0:
            raise ValueError(f"{key} must be non-negative")
    merged = {**current, **values}
    # Modes beyond recommend are intentionally not activated in this MVP.
    merged["mode"] = "recommend"
    with connect() as db:
        db.execute("UPDATE settings SET mode=?, max_price_reduction_pct=?, minimum_price=?, minimum_profit=?, high_value_threshold=?, require_vintage=?, require_designer=?, require_collectible=? WHERE id=1", (merged["mode"], float(merged["max_price_reduction_pct"]), float(merged["minimum_price"]), float(merged["minimum_profit"]), float(merged["high_value_threshold"]), int(bool(merged["require_vintage"])), int(bool(merged["require_designer"])), int(bool(merged["require_collectible"]))))
    return settings()


def _api_base_url() -> str:
    return "https://api.sandbox.ebay.com" if os.environ.get("EBAY_ENVIRONMENT", "production").strip().lower() == "sandbox" else "https://api.ebay.com"


def _marketplace() -> str:
    return os.environ.get("EBAY_MARKETPLACE_ID", "EBAY_US")


def _seller_token() -> str:
    try:
        return seller_access_token()
    except EbayAuthError as exc:
        raise EbayDraftError(exc.status_code, exc.category, exc.safe_message, exc.code) from exc


def _ebay_get(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    try:
        response = requests.get(f"{_api_base_url()}{path}", headers={"Authorization": f"Bearer {_seller_token()}", "Accept": "application/json", "X-EBAY-C-MARKETPLACE-ID": _marketplace()}, params=params or {}, timeout=12)
    except requests.Timeout as exc:
        raise EbayDraftError(504, "timeout", "eBay listing retrieval timed out.") from exc
    except requests.RequestException as exc:
        raise EbayDraftError(502, "transport", "eBay listing retrieval failed.") from exc
    if response.status_code >= 400:
        raise EbayDraftError(response.status_code, "authentication" if response.status_code in {401, 403} else "request_error", "eBay listing retrieval was rejected.", operation="import_listings")
    try:
        body = response.json()
    except ValueError as exc:
        raise EbayDraftError(502, "malformed_json", "eBay returned invalid listing JSON.") from exc
    if not isinstance(body, dict):
        raise EbayDraftError(502, "malformed_json", "eBay returned an unexpected listing response.")
    return body


def _aspects(item: dict[str, Any]) -> dict[str, Any]:
    aspects = item.get("product", {}).get("aspects", {}) if isinstance(item.get("product"), dict) else {}
    if not isinstance(aspects, dict):
        return {}
    return {str(k): (v[0] if isinstance(v, list) and v else v) for k, v in aspects.items()}


def _inventory_to_listing(item: dict[str, Any], offer: dict[str, Any] | None = None) -> dict[str, Any]:
    product = item.get("product") if isinstance(item.get("product"), dict) else {}
    aspects = _aspects(item)
    offer = offer or {}
    listing_id = str(offer.get("listingId") or "")
    offer_id = str(offer.get("offerId") or "")
    offer_status = str(offer.get("status") or "").upper()
    lifecycle = "Active listing" if listing_id and offer_status == "PUBLISHED" else "Unpublished offer" if offer_id else "Inventory-only draft"
    record_status = "active" if lifecycle == "Active listing" else "draft"
    price = (offer or {}).get("pricingSummary", {}).get("price", {}).get("value") if isinstance((offer or {}).get("pricingSummary"), dict) else None
    source = {
        "sku": item.get("sku", ""), "offerId": offer_id, "listingId": listing_id,
        "offerStatus": offer_status or "NOT_FOUND", "lifecycle": lifecycle, "status": record_status,
        "ebayUrl": f"https://www.ebay.com/itm/{listing_id}" if listing_id else "",
        "title": product.get("title", ""), "desc": product.get("description", ""), "price": price or 0,
        "quantity": item.get("availability", {}).get("shipToLocationAvailability", {}).get("quantity", 1) if isinstance(item.get("availability"), dict) else 1,
        "pic": " ".join(product.get("imageUrls", []) if isinstance(product.get("imageUrls"), list) else []),
        "cat": (offer or {}).get("categoryId", ""), "cid": item.get("condition", "3000"), "cnote": item.get("conditionDescription", ""),
        "brand": aspects.get("Brand", ""), "model": aspects.get("Model", ""), "size": aspects.get("Size", ""), "color": aspects.get("Color", ""), "dept": aspects.get("Department", ""),
        "type": aspects.get("Type", ""), "style": aspects.get("Style", ""), "theme": aspects.get("Theme", ""), "mat": aspects.get("Material", ""), "pat": aspects.get("Pattern", ""),
        "slv": aspects.get("Sleeve Length", ""), "nk": aspects.get("Neckline", ""), "sea": aspects.get("Season", ""), "occ": aspects.get("Occasion", ""),
        "st": aspects.get("Size Type", ""), "vin": aspects.get("Vintage", "No"), "sourceUpdatedAt": item.get("product", {}).get("upc", ""),
    }
    return {**source, **normalize_listing(source)}


def import_listings() -> dict[str, Any]:
    init_db()
    imported = 0
    skipped_inventory_only = 0
    include_inventory_only = os.environ.get("IMPORT_INVENTORY_ONLY", "false").lower() in {"1", "true", "yes", "on"}
    offset = 0
    limit = 100
    while True:
        body = _ebay_get("/sell/inventory/v1/inventory_item", {"limit": limit, "offset": offset})
        items = body.get("inventoryItems") if isinstance(body.get("inventoryItems"), list) else []
        if not items:
            break
        with connect() as db:
            for raw in items:
                sku = str(raw.get("sku") or "").strip()
                if not sku:
                    continue
                offer = {}
                try:
                    offers = _ebay_get("/sell/inventory/v1/offer", {"sku": sku, "limit": 20}).get("offers", [])
                    offers = offers if isinstance(offers, list) else []
                    offer = next((entry for entry in offers if str(entry.get("status", "")).upper() == "PUBLISHED"), offers[0] if offers else {})
                except EbayDraftError:
                    offer = {}
                listing = _inventory_to_listing(raw, offer)
                if listing.get("lifecycle") == "Inventory-only draft" and not include_inventory_only:
                    skipped_inventory_only += 1
                    continue
                now = utc_now()
                db.execute("INSERT INTO listings(listing_id,offer_id,sku,marketplace,data_json,source_updated_at,imported_at) VALUES(?,?,?,?,?,?,?) ON CONFLICT(sku,marketplace) DO UPDATE SET listing_id=excluded.listing_id, offer_id=excluded.offer_id, data_json=excluded.data_json, source_updated_at=excluded.source_updated_at, imported_at=excluded.imported_at", (str(listing.get("listingId", "")), str(listing.get("offerId", "")), sku, _marketplace(), _json(listing), str(listing.get("sourceUpdatedAt", "")), now))
                imported += 1
        if len(items) < limit:
            break
        offset += len(items)
    return {"imported": imported, "skippedInventoryOnly": skipped_inventory_only, "total": count_listings()}


def import_active_listings() -> dict[str, Any]:
    """Import all active seller listings separately from Inventory API records."""
    init_db()
    page = 1
    imported = 0
    total_entries = 0
    while True:
        result = fetch_active_listings(page=page)
        total_entries = result["totalEntries"]
        items = result["items"]
        with connect() as db:
            for listing in items:
                sku = str(listing.get("sku") or listing.get("listingId") or "").strip()
                if not sku:
                    continue
                now = utc_now()
                normalized = {**listing, "sku": sku, "marketplace": _marketplace(), "source": "trading_active", "status": "active", "lifecycle": listing.get("lifecycle") or "Active listing"}
                db.execute("INSERT INTO listings(listing_id,offer_id,sku,marketplace,data_json,source_updated_at,imported_at) VALUES(?,?,?,?,?,?,?) ON CONFLICT(sku,marketplace) DO UPDATE SET listing_id=excluded.listing_id, data_json=excluded.data_json, source_updated_at=excluded.source_updated_at, imported_at=excluded.imported_at", (str(listing.get("listingId", "")), "", sku, _marketplace(), _json(normalized), "", now))
                imported += 1
        if page >= int(result.get("totalPages") or 1) or not items:
            break
        page += 1
    return {"imported": imported, "totalEntries": total_entries, "total": count_listings(), "source": "trading_active"}


def start_active_import_job() -> dict[str, Any]:
    return _start_background_job("active_import", {})


def start_enrichment_job(listing_ids: list[str]) -> dict[str, Any]:
    requested = list(dict.fromkeys(str(value).strip() for value in listing_ids if str(value).strip()))
    if not requested:
        raise ValueError("Select at least one eBay listing before enrichment.")
    if len(requested) > 20:
        raise ValueError("Pilot enrichment is limited to 20 listing IDs per job.")
    return _start_background_job("enrichment", {"listingIds": requested})


def start_audit_job() -> dict[str, Any]:
    return _start_background_job("audit", {})


def _start_background_job(kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    init_db()
    job_id = str(uuid.uuid4())
    now = utc_now()
    with connect() as db:
        db.execute(
            "INSERT INTO commerce_jobs(id,kind,status,progress,result_json,error,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
            (job_id, kind, "queued", 0, _json(payload), "", now, now),
        )
    thread = threading.Thread(target=run_job, args=(job_id,), daemon=True)
    thread.start()
    return {"jobId": job_id, "status": "queued", "kind": kind, "readOnly": True}


def active_import_job(job_id: str) -> dict[str, Any] | None:
    init_db()
    with connect() as db:
        row = db.execute("SELECT * FROM commerce_jobs WHERE id=?", (job_id,)).fetchone()
    return dict(row) if row else None


def run_job(job_id: str) -> dict[str, Any] | None:
    """Claim and execute one read-only catalog job.

    The compare-and-set transition lets a web-thread or a separate worker claim a
    job safely without ever scheduling an eBay mutation.
    """
    init_db()
    with connect() as db:
        row = db.execute("SELECT * FROM commerce_jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            return None
        if row["status"] != "queued":
            return dict(row)
        claimed = db.execute(
            "UPDATE commerce_jobs SET status='running', progress=?, updated_at=? WHERE id=? AND status='queued'",
            (1, utc_now(), job_id),
        )
        if getattr(claimed, "rowcount", 1) != 1:
            return dict(db.execute("SELECT * FROM commerce_jobs WHERE id=?", (job_id,)).fetchone())
        kind = str(row["kind"])
        payload = _decode(row["result_json"], {})
    try:
        if kind == "active_import":
            result = import_active_listings()
        elif kind == "enrichment":
            result = enrich_listings(payload.get("listingIds", []))
        elif kind == "audit":
            audited = audit_all()
            result = {"count": audited["count"], "readOnly": True}
        else:
            raise ValueError("Unsupported Commerce Agent job kind.")
        _update_job(job_id, "completed", 100, result, "")
        return active_import_job(job_id)
    except Exception as exc:
        logger.exception("Commerce Agent job failed: %s", kind)
        _update_job(job_id, "failed", 100, {}, str(exc)[:240])
        return active_import_job(job_id)


def run_next_queued_job() -> dict[str, Any] | None:
    """Worker entrypoint: run one queued read-only catalog job, if one exists."""
    init_db()
    with connect() as db:
        row = db.execute("SELECT id FROM commerce_jobs WHERE status='queued' ORDER BY created_at ASC LIMIT 1").fetchone()
    return run_job(str(row["id"])) if row else None


def _update_job(job_id: str, status: str, progress: int, result: dict[str, Any], error: str) -> None:
    with connect() as db:
        db.execute("UPDATE commerce_jobs SET status=?, progress=?, result_json=?, error=?, updated_at=? WHERE id=?", (status, progress, _json(result), error, utc_now(), job_id))


def count_listings() -> int:
    init_db()
    with connect() as db:
        row = db.execute("SELECT COUNT(*) AS count FROM listings").fetchone()
    return int(row["count"] if isinstance(row, dict) else row[0])


def _row_listing(row: sqlite3.Row) -> dict[str, Any]:
    item = _decode(row["data_json"], {})
    item.update({"id": row["id"], "listingId": row["listing_id"], "offerId": row["offer_id"], "sku": row["sku"], "marketplace": row["marketplace"]})
    return item


def list_listings(filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    filters = filters or {}
    init_db()
    with connect() as db:
        rows = db.execute("SELECT * FROM listings ORDER BY imported_at DESC").fetchall()
    items = [_row_listing(row) for row in rows]
    status = str(filters.get("status") or "").lower()
    if status:
        items = [item for item in items if str(item.get("status", "active")).lower() == status]
    return items


def enrich_listings(listing_ids: list[str], timeout: float = 20.0) -> dict[str, Any]:
    """Refresh official eBay detail fields for selected listings only.

    This is read-only against eBay. It updates the local normalized catalog and
    never approves, applies, updates, or publishes an eBay listing.
    """
    requested = list(dict.fromkeys(str(value).strip() for value in listing_ids if str(value).strip()))
    if not requested:
        raise ValueError("At least one eBay listing ID is required.")
    if len(requested) > 20:
        raise ValueError("Pilot enrichment is limited to 20 listing IDs per request.")
    init_db()
    updated: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    specifics_map = {
        label.casefold(): key
        for label, key in (
            ("Brand", "brand"), ("Model", "model"), ("Material", "mat"),
            ("Exterior Material", "mat"), ("Upper Material", "mat"),
            ("Made In", "madeIn"), ("Country of Origin", "madeIn"),
            ("Size", "size"), ("US Shoe Size", "size"), ("Color", "color"),
            ("Main Color", "color"), ("Exterior Color", "color"),
            ("Department", "dept"), ("Type", "type"), ("Style", "style"),
            ("Theme", "theme"), ("Pattern", "pat"), ("Sleeve Length", "slv"),
            ("Neckline", "nk"), ("Season", "sea"), ("Occasion", "occ"),
            ("Size Type", "st"), ("Vintage", "vin"),
        )
    }
    with connect() as db:
        for listing_id in requested:
            try:
                detail = fetch_listing_detail(listing_id, timeout=timeout)
                row = db.execute("SELECT * FROM listings WHERE listing_id=? ORDER BY id DESC LIMIT 1", (listing_id,)).fetchone()
                if not row:
                    errors.append({"listingId": listing_id, "error": "Listing is not present in the local imported catalog."})
                    continue
                current = _row_listing(row)
                raw = {**current, **{key: value for key, value in detail.items() if key not in {"itemSpecifics"}}, "listingId": listing_id}
                for label, value in (detail.get("itemSpecifics") or {}).items():
                    field = specifics_map.get(str(label).casefold())
                    if field and value:
                        raw[field] = value
                raw["source"] = "trading_get_item"
                normalized = normalize_listing(raw)
                official_title = str(detail.get("title") or current.get("title") or normalized.get("title") or "").strip()
                official_description = str(detail.get("desc") or current.get("desc") or "").strip()
                normalized.update({
                    "listingId": listing_id,
                    "offerId": current.get("offerId", ""),
                    "sku": current.get("sku", ""),
                    "marketplace": current.get("marketplace", _marketplace()),
                    "ebayUrl": f"https://www.ebay.com/itm/{listing_id}",
                    "source": "trading_get_item",
                    "sourceTitle": official_title,
                    "sourceDescription": official_description,
                    "title": official_title,
                    "desc": official_description,
                    "itemSpecifics": detail.get("itemSpecifics", {}),
                    "attributeEvidence": normalize_evidence(
                        normalized,
                        source="ebay_get_item",
                        default_evidence="Official eBay GetItem field.",
                    ),
                    "watchCount": detail.get("watchCount", 0),
                    "location": detail.get("location", ""),
                })
                db.execute("UPDATE listings SET data_json=?, source_updated_at=?, imported_at=? WHERE id=?", (_json(normalized), str(detail.get("sourceUpdatedAt", "")), utc_now(), row["id"]))
                updated.append({"listingId": listing_id, "sku": normalized.get("sku", ""), "itemSpecifics": detail.get("itemSpecifics", {}), "category": normalized.get("cat", ""), "source": "trading_get_item"})
            except EbayActiveError as exc:
                errors.append({"listingId": listing_id, "error": exc.safe_message, "statusCode": exc.status_code})
            except Exception:
                logger.exception("eBay detail enrichment failed for listing %s", listing_id)
                errors.append({"listingId": listing_id, "error": "Listing detail enrichment failed."})
    return {"requested": len(requested), "updated": len(updated), "failed": len(errors), "records": updated, "errors": errors, "readOnly": True}


def _price_float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _same_price(current: float, proposed: float) -> bool:
    return abs(current - proposed) < 0.01


def _title_candidate(item: dict[str, Any]) -> str:
    """Build a conservative, category-aware title from confirmed listing fields."""
    current = str(item.get("title") or "").strip()
    parts: list[str] = []
    for key in ("brand", "model", "type", "style", "mat", "color", "pat", "size"):
        value = str(item.get(key) or "").strip()
        if value and value.lower() not in {"not visible", "n/a", "unknown"}:
            parts.append(value)
    if current:
        parts = [current] + [part for part in parts if part.lower() not in current.lower()]
    if current and len(parts) == 1:
        lowered = current.lower()
        if "handbag" in lowered and "purse" not in lowered:
            parts.append("Purse")
        elif "wallet" in lowered and "wristlet" not in lowered:
            parts.append("Wristlet")
        elif "shirt" in lowered and "top" not in lowered:
            parts.append("Top")
    result = " ".join(parts)
    return re.sub(r"\s+", " ", result).strip()[:MAX_TITLE_LENGTH].rstrip()


def audit_listing(item: dict[str, Any]) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    proposed: dict[str, Any] = {}
    title = str(item.get("title") or "").strip()
    title_plan = optimize_title(item)
    if not title or len(title) < 35:
        findings.append({"field": "title", "severity": "medium", "message": "Title is short and may be missing searchable product attributes."})
        candidate = title_plan.get("title") or _title_candidate(item)
        if candidate and candidate.casefold() != title.casefold():
            proposed["title"] = candidate
            findings.append({"field": "title", "severity": "medium", "message": f"Suggested title candidate: {candidate}. Verify every attribute before approval."})
        elif title:
            findings.append({"field": "title", "severity": "medium", "message": "No safe title change can be generated from the imported fields; image/model review is required."})
    if len(title) > MAX_TITLE_LENGTH:
        findings.append({"field": "title", "severity": "high", "message": "Title exceeds eBay's 80-character limit."})
        proposed["title"] = title[:MAX_TITLE_LENGTH].rstrip()
    description = str(item.get("desc") or "").strip()
    if not description or len(re.sub(r"<[^>]+>", "", description)) < 80:
        findings.append({"field": "desc", "severity": "medium", "message": "Description lacks enough buyer-facing detail and condition context."})
    missing = [label for label, key in (("brand", "brand"), ("size", "size"), ("color", "color"), ("material", "mat"), ("condition", "cnote")) if not str(item.get(key) or "").strip() or str(item.get(key)).lower() == "not visible"]
    if missing:
        findings.append({"field": "item_specifics", "severity": "medium", "message": f"Review missing or uncertain specifics: {', '.join(missing)}."})
    if not str(item.get("cat") or "").strip():
        findings.append({"field": "cat", "severity": "high", "message": "Category is unavailable and requires seller review."})
    if not str(item.get("pic") or "").strip():
        findings.append({"field": "pic", "severity": "medium", "message": "No image URLs were available for photo coverage review."})
    price = _price_float(item.get("price"))
    if price <= 0:
        findings.append({"field": "price", "severity": "high", "message": "Price is missing or invalid."})
    taxonomy = validate_listing(item)
    if taxonomy.get("status") == "missing":
        findings.append({"field": "taxonomy", "severity": "high", "message": taxonomy.get("message", "Seller category review required.")})
    elif taxonomy.get("status") == "unavailable":
        findings.append({"field": "taxonomy", "severity": "medium", "message": taxonomy.get("message", "Taxonomy unavailable; seller category review remains advisory.")})
    sold = sold_price_summary(item)
    pricing = pricing_recommendation(item, sold_summary=sold)
    if pricing.get("status") == "error":
        findings.append({"field": "price", "severity": "high", "message": pricing.get("message", "Seller must enter a numeric price.")})
    elif _safe_price_proposal(item, pricing):
        proposed["price"] = pricing["recommendedPrice"]
        findings.append({"field": "price", "severity": "medium", "message": f"Suggested price candidate: ${pricing['recommendedPrice']:.2f} from {pricing['pricingSource']}. Approval required before eBay update."})
    demand = demand_score(item)
    evidence = evidence_for_listing(item)
    if findings and not proposed:
        proposed["notes"] = _review_note(item, findings, taxonomy, pricing)
    score = max(0, min(100, 100 - sum(18 if f["severity"] == "high" else 10 for f in findings)))
    classification = "Excellent" if score >= 90 else "Good" if score >= 75 else "Needs Optimization" if score >= 50 else "High Priority"
    if any(f["severity"] == "high" for f in findings):
        classification = "Needs Review"
    confidence = "high" if findings and all(f["field"] not in {"cat", "price"} for f in findings) else "medium"
    reason = "; ".join(f["message"] for f in findings) or "No material listing quality issue was identified from the imported data."
    return {"score": score, "classification": classification, "findings": findings, "proposed": proposed, "reason": reason, "confidence": confidence, "risk": "high" if any(f["severity"] == "high" for f in findings) else "low", "evidence": evidence_summary(item), "taxonomy": taxonomy, "soldPricing": pricing, "soldComparableSummary": sold, "demand": demand}


def audit_all() -> dict[str, Any]:
    init_db()
    results = []
    now = utc_now()
    with connect() as db:
        for row in db.execute("SELECT * FROM listings").fetchall():
            item = _row_listing(row)
            audit = audit_listing(item)
            # Re-auditing is idempotent for pending work. Preserve approved and
            # applied history, but never create a second pending card for a row.
            db.execute("DELETE FROM recommendations WHERE listing_row_id=? AND status='Pending'", (row["id"],))
            recommendation_id = str(uuid.uuid4())
            stored_current = {**item, "attributeEvidence": audit["evidence"], "taxonomyValidation": audit["taxonomy"], "soldPricing": audit["soldPricing"], "soldComparableSummary": audit["soldComparableSummary"], "demandMetrics": audit["demand"]}
            db.execute("INSERT INTO recommendations(id,listing_row_id,current_json,proposed_json,findings_json,score,classification,reason,confidence,risk,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (recommendation_id, row["id"], _json(stored_current), _json(audit["proposed"]), _json(audit["findings"]), audit["score"], audit["classification"], audit["reason"], audit["confidence"], audit["risk"], "Pending", now, now))
            results.append({"recommendationId": recommendation_id, "listing": stored_current, **audit, "status": "Pending"})
    return {"count": len(results), "results": results}


def _recommendation(row: sqlite3.Row) -> dict[str, Any]:
    listing = _decode(row["current_json"], {})
    return {"recommendationId": row["id"], "actionId": row["action_id"] if "action_id" in row.keys() else "", "listing": listing, "proposed": _decode(row["proposed_json"], {}), "findings": _decode(row["findings_json"], []), "evidence": listing.get("attributeEvidence", []), "taxonomy": listing.get("taxonomyValidation", {}), "soldPricing": listing.get("soldPricing", {}), "soldComparableSummary": listing.get("soldComparableSummary", {}), "demand": listing.get("demandMetrics", {}), "score": row["score"], "classification": row["classification"], "reason": row["reason"], "confidence": row["confidence"], "risk": row["risk"], "status": row["status"], "createdAt": row["created_at"], "updatedAt": row["updated_at"]}


def recommendations(status: str = "") -> list[dict[str, Any]]:
    init_db()
    query = "SELECT r.*, (SELECT a.id FROM actions a WHERE a.recommendation_id=r.id ORDER BY a.created_at DESC LIMIT 1) AS action_id FROM recommendations r"
    params: tuple[Any, ...] = ()
    if status:
        query += " WHERE status=?"
        params = (status,)
    query += " ORDER BY score ASC, created_at DESC"
    with connect() as db:
        return [_recommendation(row) for row in db.execute(query, params).fetchall()]


def get_recommendation(recommendation_id: str) -> dict[str, Any] | None:
    init_db()
    with connect() as db:
        row = db.execute("SELECT * FROM recommendations WHERE id=?", (recommendation_id,)).fetchone()
    return _recommendation(row) if row else None


def approve_recommendation(recommendation_id: str, approved: dict[str, Any] | None = None) -> dict[str, Any]:
    recommendation = get_recommendation(recommendation_id)
    if not recommendation:
        raise ValueError("Recommendation not found.")
    changes = approved if approved is not None else recommendation["proposed"]
    if not isinstance(changes, dict) or not changes:
        raise ValueError("At least one approved field is required.")
    changes = {key: value for key, value in changes.items() if key in EDITABLE_FIELDS}
    if not changes:
        raise ValueError("No editable fields were approved.")
    if recommendation.get("risk") == "high":
        proposed = recommendation.get("proposed", {})
        baseline = {key: _price_float(value) if key == "price" else value for key, value in proposed.items() if key in EDITABLE_FIELDS} if isinstance(proposed, dict) else {}
        comparable_changes = {key: _price_float(value) if key == "price" else value for key, value in changes.items()}
        if approved is None or comparable_changes == baseline:
            raise ValueError("High-risk recommendations are review-only until a seller-reviewed subset of changes is explicitly approved.")
    current = recommendation["listing"]
    rules = settings()
    if "price" in changes:
        old_price = _price_float(current.get("price")); new_price = _price_float(changes["price"])
        max_reduction = old_price * (1 - float(rules["max_price_reduction_pct"]) / 100)
        floor = max(float(rules["minimum_price"]), float(rules["minimum_profit"]))
        if new_price < max_reduction or new_price < floor:
            raise ValueError("Approved price violates the configured safety rules.")
        if old_price >= float(rules["high_value_threshold"]):
            raise ValueError("High-value listing price changes require manual review and cannot be applied by this MVP.")
    action_id = str(uuid.uuid4())
    now = utc_now()
    with connect() as db:
        db.execute("INSERT INTO actions(id,recommendation_id,listing_row_id,approved_json,old_json,created_at) SELECT ?,id,listing_row_id,?,?,? FROM recommendations WHERE id=?", (action_id, _json(changes), _json({key: current.get(key) for key in changes}), now, recommendation_id))
        db.execute("UPDATE recommendations SET status='Approved', updated_at=? WHERE id=?", (now, recommendation_id))
    return {"actionId": action_id, "recommendationId": recommendation_id, "status": "Approved", "approved": changes}


def apply_action(action_id: str) -> dict[str, Any]:
    init_db()
    with connect() as db:
        row = db.execute("SELECT a.*, r.current_json FROM actions a JOIN recommendations r ON r.id=a.recommendation_id WHERE a.id=?", (action_id,)).fetchone()
    if not row:
        raise ValueError("Approved action not found.")
    if row["status"] not in {"Approved", "Failed"}:
        raise ValueError("Only an approved action can be applied.")
    offer_id = str(_decode(row["current_json"], {}).get("offerId") or "")
    if not offer_id:
        raise ValueError("This listing has no eBay offer ID; it cannot be updated through the Inventory API.")
    current = _decode(row["current_json"], {})
    changes = _decode(row["approved_json"], {})
    merged = {**current, **changes, "sku": current.get("sku")}
    try:
        result = update_ebay_offer(offer_id, merged)
    except EbayDraftError as exc:
        with connect() as db:
            db.execute("UPDATE actions SET status='Failed', error=? WHERE id=?", (exc.safe_message, action_id))
            db.execute("UPDATE recommendations SET status='Failed', updated_at=? WHERE id=?", (utc_now(), row["recommendation_id"]))
        raise
    now = utc_now()
    with connect() as db:
        db.execute("UPDATE actions SET status='Applied', new_json=?, ebay_result_json=?, applied_at=? WHERE id=?", (_json(changes), _json(result), now, action_id))
        db.execute("UPDATE recommendations SET status='Applied', updated_at=? WHERE id=?", (now, row["recommendation_id"]))
    return {"actionId": action_id, "recommendationId": row["recommendation_id"], "status": "Applied", "result": result}


def history() -> list[dict[str, Any]]:
    init_db()
    with connect() as db:
        rows = db.execute("SELECT a.*, r.current_json FROM actions a JOIN recommendations r ON r.id=a.recommendation_id ORDER BY a.created_at DESC").fetchall()
    return [{"actionId": row["id"], "recommendationId": row["recommendation_id"], "listing": _decode(row["current_json"], {}), "approved": _decode(row["approved_json"], {}), "old": _decode(row["old_json"], {}), "new": _decode(row["new_json"], {}), "status": row["status"], "error": row["error"], "ebayResult": _decode(row["ebay_result_json"], {}), "createdAt": row["created_at"], "appliedAt": row["applied_at"]} for row in rows]


def dashboard() -> dict[str, Any]:
    items = list_listings()
    recs = recommendations()
    active_items = [item for item in items if str(item.get("status") or "active").lower() == "active"]
    return {"connectedStore": "eBay", "listingsFound": len(active_items), "recordsFound": len(items), "recommendations": len(recs), "needOptimization": sum(1 for r in recs if r["classification"] in {"Needs Optimization", "High Priority", "Needs Review"}), "titleImprovements": sum(1 for r in recs if any(f.get("field") == "title" for f in r["findings"])), "missingItemSpecifics": sum(1 for r in recs if any(f.get("field") == "item_specifics" for f in r["findings"])), "needsReview": sum(1 for r in recs if r["classification"] == "Needs Review"), "recovery": seller_recovery_metrics(recs, active_items), "mode": "recommend"}


def _safe_price_proposal(item: dict[str, Any], pricing: dict[str, Any]) -> bool:
    if pricing.get("pricingSource") == "seller_price_fallback":
        return False
    recommended = _price_float(pricing.get("recommendedPrice"))
    current = _price_float(item.get("price"))
    return recommended > 0 and (current <= 0 or abs(recommended - current) >= 0.01)


def _review_note(item: dict[str, Any], findings: list[dict[str, Any]], taxonomy: dict[str, Any], pricing: dict[str, Any]) -> str:
    existing = str(item.get("notes") or "").strip()
    summary = "; ".join(str(f.get("message") or "") for f in findings[:4] if f.get("message"))
    taxonomy_status = str(taxonomy.get("status") or "")
    pricing_source = str(pricing.get("pricingSource") or "")
    addition = f"Seller review required before applying changes. {summary}".strip()
    if taxonomy_status:
        addition = f"{addition} Taxonomy status: {taxonomy_status}."
    if pricing_source:
        addition = f"{addition} Pricing source: {pricing_source}."
    return f"{existing} {addition}".strip()[:900]


__all__ = ["dashboard", "import_listings", "list_listings", "audit_all", "recommendations", "get_recommendation", "approve_recommendation", "apply_action", "history", "settings", "update_settings"]
