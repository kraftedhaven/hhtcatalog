"""Persistence and business logic for the HHT Commerce Agent MVP.

The default mode is recommendation-only. Live eBay changes are performed only by
an explicit approval route, and are delegated to the existing offer update flow.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sqlite3
import threading
import time
import uuid
import base64
from datetime import datetime, timedelta, timezone
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
from .ebay_drafts import EbayDraftError, update_ebay_offer
from .schema import normalize_listing
from .evidence import evidence_for_listing, evidence_summary, normalize_evidence
from .ebay_taxonomy import validate_listing
from .title_optimizer import optimize_title
from .market_metrics import demand_score, pricing_recommendation, seller_recovery_metrics, sold_price_summary

logger = logging.getLogger(__name__)
DB_PATH = os.environ.get("COMMERCE_AGENT_DB", "commerce_agent.sqlite3")
MAX_TITLE_LENGTH = 80
EDITABLE_FIELDS = {"title", "price", "cid", "desc", "cat", "cnote", "notes", "pic", "brand", "size", "color", "dept", "type", "model", "style", "theme", "mat", "pat", "slv", "nk", "sea", "occ", "st", "vin", "madeIn", "serialNumber", "measurements"}
ENRICHMENT_PAGE_SIZE = 25
NVIDIA_HEAVY_ITEM_THRESHOLD = 30
NVIDIA_HEAVY_PHOTO_THRESHOLD = 300
NVIDIA_BATCH_MAX_PHOTOS = 500
NVIDIA_BATCH_MAX_BYTES = 100 * 1024 * 1024
ENRICHMENT_CHUNK_SIZE = 20
MAX_VISION_JOB_IMAGES = 3
MAX_VISION_JOB_BYTES = 6 * 1024 * 1024


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _decode(value: str | None, fallback: Any) -> Any:
    try:
        return json.loads(value or "")
    except (TypeError, ValueError):
        return fallback


def _listing_state_hash(value: dict[str, Any]) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


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
            required = {"listings", "recommendations", "actions", "settings", "commerce_jobs", "enrichment_checkpoints", "listing_performance_daily", "listing_versions", "fulfillment_orders", "rotation_actions", "sellers", "ebay_accounts"}
            rows = db.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_name = ANY(?)", (list(required),)).fetchall()
            present = {str(row["table_name"]) for row in rows}
            missing = sorted(required - present)
            if missing:
                raise RuntimeError("Commerce Agent database schema is incomplete. Apply the ordered Supabase migrations before starting the app: " + ", ".join(missing))
            columns = db.execute("SELECT table_name, column_name FROM information_schema.columns WHERE table_schema='public' AND table_name IN ('recommendations','actions','listings')").fetchall()
            required_columns = {
                "recommendations": {"version_number", "is_current", "seller_id"},
                "actions": {"recommendation_version", "listing_snapshot_json", "listing_state_hash", "seller_id", "approved_by", "applied_by", "rolled_back_by"},
                "listings": {"lifecycle_status", "listing_start_time", "quantity_sold", "watch_count", "ownership_classification", "seller_id", "ebay_account_id"},
            }
            present_columns = {(str(row["table_name"]), str(row["column_name"])) for row in columns}
            missing_columns = sorted(f"{table}.{column}" for table, names in required_columns.items() for column in names if (table, column) not in present_columns)
            if missing_columns:
                raise RuntimeError("Commerce Agent database columns are incomplete. Apply the ordered Supabase migrations before starting the app: " + ", ".join(missing_columns))
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
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                version_number INTEGER NOT NULL DEFAULT 1, is_current INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS actions (
                id TEXT PRIMARY KEY, recommendation_id TEXT NOT NULL REFERENCES recommendations(id),
                listing_row_id INTEGER NOT NULL REFERENCES listings(id), approved_json TEXT NOT NULL,
                old_json TEXT NOT NULL, new_json TEXT NOT NULL DEFAULT '{}', status TEXT NOT NULL DEFAULT 'Approved',
                ebay_result_json TEXT NOT NULL DEFAULT '{}', error TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL, applied_at TEXT,
                recommendation_version INTEGER NOT NULL DEFAULT 1,
                listing_snapshot_json TEXT NOT NULL DEFAULT '{}', listing_state_hash TEXT NOT NULL DEFAULT ''
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
            CREATE TABLE IF NOT EXISTS enrichment_checkpoints (
                id INTEGER PRIMARY KEY AUTOINCREMENT, listing_row_id INTEGER NOT NULL UNIQUE REFERENCES listings(id),
                listing_id TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
                enriched_at TEXT, last_error TEXT NOT NULL DEFAULT '', details_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS enrichment_checkpoints_status_idx ON enrichment_checkpoints(status, updated_at);
            INSERT OR IGNORE INTO settings(id) VALUES (1);
            """
            )
            _ensure_phase4_schema(db)
            _ensure_recommendation_versioning(db)
            _ensure_seller_schema(db)


def _ensure_seller_schema(db: _Database) -> None:
    """Keep local SQLite tests aligned with the migration-owned Postgres schema."""
    if db.postgres:
        return
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS sellers (
            id TEXT PRIMARY KEY,
            auth_user_id TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL DEFAULT 'active',
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS ebay_accounts (
            id TEXT PRIMARY KEY,
            seller_id TEXT NOT NULL REFERENCES sellers(id),
            ebay_account_id TEXT NOT NULL UNIQUE,
            connected_at TEXT NOT NULL,
            disconnected_at TEXT
        );
        """
    )
    for table, column, definition in (
        ("listings", "seller_id", "TEXT"),
        ("listings", "ebay_account_id", "TEXT"),
        ("recommendations", "seller_id", "TEXT"),
        ("actions", "seller_id", "TEXT"),
        ("actions", "approved_by", "TEXT"),
        ("actions", "applied_by", "TEXT"),
        ("actions", "rolled_back_by", "TEXT"),
        ("rotation_actions", "seller_id", "TEXT"),
        ("rotation_actions", "approved_by", "TEXT"),
    ):
        columns = {str(row[1]) for row in db.connection.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in columns:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def ensure_seller_identity(auth_user_id: str) -> dict[str, Any]:
    """Return the seller bound to the caller, bootstrapping only the configured pilot."""
    init_db()
    with connect() as db:
        seller = db.execute(
            "SELECT * FROM sellers WHERE auth_user_id=? AND status='active'",
            (auth_user_id,),
        ).fetchone()
        if seller:
            return dict(seller)

        bootstrap_user_id = os.environ.get("SELLER_BOOTSTRAP_AUTH_USER_ID", "").strip()
        ebay_account_id = os.environ.get("EBAY_ACCOUNT_ID", "").strip()
        if not bootstrap_user_id or not ebay_account_id or auth_user_id != bootstrap_user_id:
            raise PermissionError("This Supabase user is not bound to an HHT seller account.")

        seller_id = str(uuid.uuid4())
        account_id = str(uuid.uuid4())
        now = utc_now()
        db.execute(
            "INSERT INTO sellers(id,auth_user_id,status,created_at) VALUES(?,?,?,?)",
            (seller_id, auth_user_id, "active", now),
        )
        db.execute(
            "INSERT INTO ebay_accounts(id,seller_id,ebay_account_id,connected_at) VALUES(?,?,?,?)",
            (account_id, seller_id, ebay_account_id, now),
        )
        # Legacy catalog data is adopted only by the preconfigured pilot user.
        db.execute(
            "UPDATE listings SET seller_id=?, ebay_account_id=? WHERE seller_id IS NULL",
            (seller_id, account_id),
        )
        db.execute(
            "UPDATE recommendations SET seller_id=? WHERE seller_id IS NULL",
            (seller_id,),
        )
        db.execute(
            "UPDATE actions SET seller_id=? WHERE seller_id IS NULL",
            (seller_id,),
        )
        db.execute(
            "UPDATE rotation_actions SET seller_id=? WHERE seller_id IS NULL",
            (seller_id,),
        )
        return {"id": seller_id, "auth_user_id": auth_user_id, "status": "active"}


def _ensure_phase4_schema(db: _Database) -> None:
    """Add performance/version tables without disturbing existing deployments."""
    if db.postgres:
        for statement in (
            "ALTER TABLE listings ADD COLUMN IF NOT EXISTS lifecycle_status TEXT NOT NULL DEFAULT 'active'",
            "ALTER TABLE listings ADD COLUMN IF NOT EXISTS listing_start_time TEXT",
            "ALTER TABLE listings ADD COLUMN IF NOT EXISTS quantity_sold INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE listings ADD COLUMN IF NOT EXISTS watch_count INTEGER",
            "ALTER TABLE listings ADD COLUMN IF NOT EXISTS ownership_classification TEXT NOT NULL DEFAULT 'unknown'",
        ):
            db.execute(statement)
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS listing_performance_daily (
                id TEXT PRIMARY KEY, listing_row_id BIGINT REFERENCES listings(id), ebay_listing_id TEXT NOT NULL,
                metric_date TEXT NOT NULL, snapshot_kind TEXT NOT NULL DEFAULT 'rolling_listing_snapshot',
                impressions INTEGER, search_impressions INTEGER, views INTEGER, ctr DOUBLE PRECISION,
                conversion_rate DOUBLE PRECISION, transactions INTEGER, watch_count INTEGER,
                source TEXT NOT NULL DEFAULT 'ebay_analytics', metric_provenance TEXT NOT NULL DEFAULT 'official_ebay_metric', raw_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(ebay_listing_id, metric_date)
            );
            CREATE TABLE IF NOT EXISTS listing_versions (
                id TEXT PRIMARY KEY, listing_row_id BIGINT NOT NULL REFERENCES listings(id), version_number INTEGER NOT NULL,
                source TEXT NOT NULL, state_json TEXT NOT NULL, evidence_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS fulfillment_orders (
                order_id TEXT PRIMARY KEY, created_at TEXT, total_value DOUBLE PRECISION NOT NULL DEFAULT 0,
                line_items_json TEXT NOT NULL DEFAULT '[]', raw_json TEXT NOT NULL DEFAULT '{}', synced_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS rotation_actions (
                id TEXT PRIMARY KEY, ebay_listing_id TEXT NOT NULL, action TEXT NOT NULL, evidence_json TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'Pending', created_at TEXT NOT NULL, approved_at TEXT
            );
            CREATE INDEX IF NOT EXISTS listing_performance_daily_date_idx ON listing_performance_daily(metric_date, ebay_listing_id);
            """
        )
        db.execute("ALTER TABLE listing_performance_daily ADD COLUMN IF NOT EXISTS snapshot_kind TEXT NOT NULL DEFAULT 'rolling_listing_snapshot'")
        db.execute("ALTER TABLE listing_performance_daily ADD COLUMN IF NOT EXISTS metric_provenance TEXT NOT NULL DEFAULT 'official_ebay_metric'")
    else:
        columns = {str(row[1]) for row in db.connection.execute("PRAGMA table_info(listings)").fetchall()}
        for name, definition in (
            ("lifecycle_status", "TEXT NOT NULL DEFAULT 'active'"),
            ("listing_start_time", "TEXT"),
            ("quantity_sold", "INTEGER NOT NULL DEFAULT 0"),
            ("watch_count", "INTEGER"),
            ("ownership_classification", "TEXT NOT NULL DEFAULT 'unknown'"),
        ):
            if name not in columns:
                db.execute(f"ALTER TABLE listings ADD COLUMN {name} {definition}")
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS listing_performance_daily (
                id TEXT PRIMARY KEY, listing_row_id INTEGER REFERENCES listings(id), ebay_listing_id TEXT NOT NULL,
                metric_date TEXT NOT NULL, snapshot_kind TEXT NOT NULL DEFAULT 'rolling_listing_snapshot',
                impressions INTEGER, search_impressions INTEGER, views INTEGER, ctr REAL,
                conversion_rate REAL, transactions INTEGER, watch_count INTEGER,
                source TEXT NOT NULL DEFAULT 'ebay_analytics', metric_provenance TEXT NOT NULL DEFAULT 'official_ebay_metric', raw_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(ebay_listing_id, metric_date)
            );
            CREATE TABLE IF NOT EXISTS listing_versions (
                id TEXT PRIMARY KEY, listing_row_id INTEGER NOT NULL REFERENCES listings(id), version_number INTEGER NOT NULL,
                source TEXT NOT NULL, state_json TEXT NOT NULL, evidence_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS fulfillment_orders (
                order_id TEXT PRIMARY KEY, created_at TEXT, total_value REAL NOT NULL DEFAULT 0,
                line_items_json TEXT NOT NULL DEFAULT '[]', raw_json TEXT NOT NULL DEFAULT '{}', synced_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS rotation_actions (
                id TEXT PRIMARY KEY, ebay_listing_id TEXT NOT NULL, action TEXT NOT NULL, evidence_json TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'Pending', created_at TEXT NOT NULL, approved_at TEXT
            );
            CREATE INDEX IF NOT EXISTS listing_performance_daily_date_idx ON listing_performance_daily(metric_date, ebay_listing_id);
            """
        )


def _ensure_recommendation_versioning(db: _Database) -> None:
    """Keep recommendation history while exposing exactly one current row per listing."""
    if db.postgres:
        db.execute("ALTER TABLE recommendations ADD COLUMN IF NOT EXISTS version_number INTEGER NOT NULL DEFAULT 1")
        db.execute("ALTER TABLE recommendations ADD COLUMN IF NOT EXISTS is_current BOOLEAN NOT NULL DEFAULT TRUE")
    else:
        columns = {str(row[1]) for row in db.connection.execute("PRAGMA table_info(recommendations)").fetchall()}
        if "version_number" not in columns:
            db.execute("ALTER TABLE recommendations ADD COLUMN version_number INTEGER NOT NULL DEFAULT 1")
        if "is_current" not in columns:
            db.execute("ALTER TABLE recommendations ADD COLUMN is_current INTEGER NOT NULL DEFAULT 1")
        action_columns = {str(row[1]) for row in db.connection.execute("PRAGMA table_info(actions)").fetchall()}
        for name, definition in (("recommendation_version", "INTEGER NOT NULL DEFAULT 1"), ("listing_snapshot_json", "TEXT NOT NULL DEFAULT '{}'"), ("listing_state_hash", "TEXT NOT NULL DEFAULT ''")):
            if name not in action_columns:
                db.execute(f"ALTER TABLE actions ADD COLUMN {name} {definition}")
    rows = db.execute("SELECT id, listing_row_id, status, updated_at, created_at FROM recommendations ORDER BY listing_row_id, updated_at DESC, created_at DESC, id DESC").fetchall()
    seen: set[Any] = set()
    for row in rows:
        listing_row_id = row["listing_row_id"]
        if listing_row_id in seen:
            db.execute("UPDATE recommendations SET is_current=?, status=? WHERE id=?", (False if db.postgres else 0, "Superseded" if str(row["status"]) == "Pending" else row["status"], row["id"]))
        else:
            seen.add(listing_row_id)
    for listing_row_id in seen:
        version_rows = db.execute("SELECT id FROM recommendations WHERE listing_row_id=? ORDER BY updated_at ASC, created_at ASC, id ASC", (listing_row_id,)).fetchall()
        for number, version_row in enumerate(version_rows, 1):
            db.execute("UPDATE recommendations SET version_number=? WHERE id=?", (number, version_row["id"]))
    predicate = "is_current = TRUE" if db.postgres else "is_current = 1"
    db.execute(f"CREATE UNIQUE INDEX IF NOT EXISTS recommendations_one_current_per_listing ON recommendations(listing_row_id) WHERE {predicate}")


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


def choose_vision_route(item_count: int = 1, photo_count: int = 0) -> dict[str, Any]:
    """Choose the economical default for normal work and the heavy-batch path."""
    try:
        items = max(1, int(item_count))
    except (TypeError, ValueError):
        items = 1
    try:
        photos = max(0, int(photo_count))
    except (TypeError, ValueError):
        photos = 0
    item_threshold = _positive_int(os.environ.get("NVIDIA_HEAVY_ITEM_THRESHOLD"), NVIDIA_HEAVY_ITEM_THRESHOLD, 5000)
    photo_threshold = _positive_int(os.environ.get("NVIDIA_HEAVY_PHOTO_THRESHOLD"), NVIDIA_HEAVY_PHOTO_THRESHOLD, 10000)
    heavy = items >= item_threshold or photos >= photo_threshold
    return {"route": "nvidia_worker" if heavy else "groq", "workload": "heavy_batch" if heavy else "normal", "itemCount": items, "photoCount": photos, "thresholds": {"items": item_threshold, "photos": photo_threshold}, "reason": "NVIDIA/Brev worker is reserved for large batches; normal listing analysis stays on Groq." if heavy else "Normal listing analysis uses Groq; NVIDIA is reserved for large batches."}


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
                db.execute("UPDATE listings SET lifecycle_status=?, listing_start_time=?, quantity_sold=?, watch_count=? WHERE sku=? AND marketplace=?", (str(listing.get("status") or "draft"), listing.get("listingStartTime"), int(_metric_number(listing.get("quantitySold"))), int(_metric_number(listing.get("watchCount"))), sku, _marketplace()))
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
    active_skus: set[str] = set()
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
                previous = db.execute("SELECT * FROM listings WHERE sku=? AND marketplace=?", (sku, _marketplace())).fetchone()
                existing = _row_listing(previous) if previous else {}
                normalized = _merge_active_listing(existing, listing, sku)
                ownership = classify_listing_ownership(normalized)
                normalized["ownershipClassification"] = ownership
                db.execute("INSERT INTO listings(listing_id,offer_id,sku,marketplace,data_json,source_updated_at,imported_at) VALUES(?,?,?,?,?,?,?) ON CONFLICT(sku,marketplace) DO UPDATE SET listing_id=excluded.listing_id, data_json=excluded.data_json, source_updated_at=excluded.source_updated_at, imported_at=excluded.imported_at", (str(listing.get("listingId", "")), "", sku, _marketplace(), _json(normalized), "", now))
                db.execute("UPDATE listings SET lifecycle_status=?, listing_start_time=?, quantity_sold=?, watch_count=?, ownership_classification=? WHERE sku=? AND marketplace=?", (str(normalized.get("status") or "active"), normalized.get("listingStartTime"), int(_metric_number(normalized.get("quantitySold"))), _metric_optional_number(normalized.get("watchCount")), ownership, sku, _marketplace()))
                active_skus.add(sku)
                imported += 1
        if page >= int(result.get("totalPages") or 1) or not items:
            break
        page += 1
    stale = _mark_inactive_active_import_records(active_skus)
    return {"imported": imported, "totalEntries": total_entries, "total": count_listings(), "staleMarkedInactive": stale, "source": "trading_active"}


def _merge_active_listing(existing: dict[str, Any], listing: dict[str, Any], sku: str) -> dict[str, Any]:
    """Keep prior official detail values when the active-list summary omits them."""
    normalized = {**existing, **listing}
    for field in (
        "cat", "categoryName", "itemSpecifics", "brand", "model", "size", "color",
        "dept", "type", "style", "theme", "mat", "pat", "slv", "nk", "sea", "occ",
        "st", "vin", "madeIn", "serialNumber", "measurements", "attributeEvidence",
        "sourceTitle", "sourceDescription", "watchCount", "location",
    ):
        if not listing.get(field) and existing.get(field):
            normalized[field] = existing[field]
    normalized.update({
        "sku": sku,
        "marketplace": _marketplace(),
        "source": existing.get("source") if existing.get("source") == "trading_get_item" else "trading_active",
        "activeSource": "trading_active",
        "status": "active",
        "lifecycle": listing.get("lifecycle") or "Active listing",
    })
    return normalized


def _mark_inactive_active_import_records(active_skus: set[str]) -> int:
    """Mark local active-import records absent from a completed refresh as inactive.

    This changes local catalog state only. It never calls eBay or changes a
    seller listing, and it runs only after every active-list page completed.
    """
    marked = 0
    with connect() as db:
        rows = db.execute("SELECT id,sku,data_json FROM listings WHERE marketplace=?", (_marketplace(),)).fetchall()
        for row in rows:
            item = _decode(row["data_json"], {})
            if str(row["sku"]) in active_skus:
                continue
            active_origin = str(item.get("activeSource") or item.get("source") or "")
            if str(item.get("status") or "active").lower() != "active":
                continue
            item["status"] = "inactive"
            item["lifecycleStatus"] = "inactive_unknown"
            if active_origin in {"trading_active", "trading_get_item"}:
                item["lifecycle"] = "Not returned by latest active eBay import"
                item["inactiveReason"] = "Not returned by latest completed active listing refresh."
            else:
                item["lifecycle"] = "Legacy local record"
                item["inactiveReason"] = "No active eBay listing was returned for this locally stored record."
            db.execute("UPDATE listings SET data_json=?, lifecycle_status=?, imported_at=? WHERE id=?", (_json(item), "inactive_unknown", utc_now(), row["id"]))
            marked += 1
    return marked


PERFORMANCE_METRICS = ",".join((
    "CLICK_THROUGH_RATE", "LISTING_IMPRESSION_SEARCH_RESULTS_PAGE", "LISTING_IMPRESSION_TOTAL",
    "LISTING_VIEWS_TOTAL", "SALES_CONVERSION_RATE", "TRANSACTION",
))


def _date_window(days: int = 30) -> tuple[str, str]:
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=max(1, min(int(days), 90)) - 1)
    return start.strftime("%Y%m%d"), end.strftime("%Y%m%d")


def _metric_number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _metric_optional_number(value: Any) -> float | None:
    """Parse an official metric without manufacturing zero for an absent value."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _analytics_get(params: dict[str, Any]) -> dict[str, Any]:
    try:
        return _ebay_get("/sell/analytics/v1/traffic_report", params)
    except EbayDraftError as exc:
        if exc.status_code in {401, 403}:
            raise ValueError("eBay Analytics access requires reauthorization with sell.analytics.readonly.") from exc
        raise


def _report_records(body: dict[str, Any], fallback_date: str) -> list[dict[str, Any]]:
    header = body.get("header") if isinstance(body.get("header"), dict) else {}
    metrics = [str(entry.get("key") or "").upper() for entry in (header.get("metrics") or []) if isinstance(entry, dict)]
    dimension_keys = [str(entry.get("key") or "").upper() for entry in (header.get("dimensionKeys") or []) if isinstance(entry, dict)]
    rows: list[dict[str, Any]] = []
    for record in body.get("records") if isinstance(body.get("records"), list) else []:
        if not isinstance(record, dict):
            continue
        dimensions = record.get("dimensionValues") if isinstance(record.get("dimensionValues"), list) else []
        values = record.get("metricValues") if isinstance(record.get("metricValues"), list) else []
        dimensions = [entry.get("value") if isinstance(entry, dict) else entry for entry in dimensions]
        values = [entry.get("value") if isinstance(entry, dict) else entry for entry in values]
        mapped = {metric: value for metric, value in zip(metrics, values)}
        listing_id = str(record.get("listingId") or "")
        metric_date = fallback_date
        for key, value in zip(dimension_keys, dimensions):
            if key in {"LISTING", "LISTING_ID"} and not listing_id:
                listing_id = str(value or "")
            if key in {"DAY", "DATE"} and value:
                metric_date = str(value)[:10]
        rows.append({"listingId": listing_id, "metricDate": metric_date, "metrics": mapped, "raw": record})
    return rows


def _store_performance_rows(rows: list[dict[str, Any]], fallback_date: str) -> int:
    init_db()
    listing_map: dict[str, Any] = {}
    with connect() as db:
        for listing in db.execute("SELECT id, listing_id FROM listings WHERE marketplace=?", (_marketplace(),)).fetchall():
            listing_map[str(listing["listing_id"])] = listing["id"]
        stored = 0
        now = utc_now()
        for row in rows:
            listing_id = str(row.get("listingId") or "").strip()
            if not listing_id:
                continue
            metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
            values = {
                "impressions": _metric_optional_number(metrics.get("LISTING_IMPRESSION_TOTAL")),
                "search_impressions": _metric_optional_number(metrics.get("LISTING_IMPRESSION_SEARCH_RESULTS_PAGE")),
                "views": _metric_optional_number(metrics.get("LISTING_VIEWS_TOTAL")),
                "ctr": _metric_optional_number(metrics.get("CLICK_THROUGH_RATE")),
                "conversion_rate": _metric_optional_number(metrics.get("SALES_CONVERSION_RATE")),
                "transactions": _metric_optional_number(metrics.get("TRANSACTION")),
                "watch_count": None,
            }
            metric_date = str(row.get("metricDate") or fallback_date)[:32]
            existing = db.execute("SELECT id FROM listing_performance_daily WHERE ebay_listing_id=? AND metric_date=?", (listing_id, metric_date)).fetchone()
            params = (listing_map.get(listing_id), listing_id, metric_date, "rolling_listing_snapshot", values["impressions"], values["search_impressions"], values["views"], values["ctr"], values["conversion_rate"], values["transactions"], values["watch_count"], "ebay_analytics", "official_ebay_metric", _json(row.get("raw") or {}), now)
            if existing:
                db.execute("UPDATE listing_performance_daily SET listing_row_id=?, snapshot_kind=?, impressions=?, search_impressions=?, views=?, ctr=?, conversion_rate=?, transactions=?, watch_count=?, source=?, metric_provenance=?, raw_json=?, updated_at=? WHERE id=?", (params[0], params[3], params[4], params[5], params[6], params[7], params[8], params[9], params[10], params[11], params[12], params[13], params[14], existing["id"]))
            else:
                db.execute("INSERT INTO listing_performance_daily(id,listing_row_id,ebay_listing_id,metric_date,snapshot_kind,impressions,search_impressions,views,ctr,conversion_rate,transactions,watch_count,source,metric_provenance,raw_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (str(uuid.uuid4()),) + params[:-1] + (now, now))
            stored += 1
        return stored


def sync_performance(days: int = 30, listing_ids: list[str] | None = None) -> dict[str, Any]:
    """Read official eBay traffic metrics in the documented 200-listing batches."""
    init_db()
    if listing_ids is None:
        listing_ids = [str(row["listing_id"]) for row in connect_listing_ids()]
    listing_ids = list(dict.fromkeys(value.strip() for value in listing_ids if value.strip()))
    start, end = _date_window(days)
    all_rows: list[dict[str, Any]] = []
    for offset in range(0, len(listing_ids), 200):
        chunk = listing_ids[offset:offset + 200]
        filters = f"marketplace_ids:{{{_marketplace()}}},date_range:[{start}..{end}],listing_ids:{{{'|'.join(chunk)}}}"
        body = _analytics_get({"dimension": "LISTING", "filter": filters, "metric": PERFORMANCE_METRICS})
        all_rows.extend(_report_records(body, end[:4] + "-" + end[4:6] + "-" + end[6:]))
    stored = _store_performance_rows(all_rows, end[:4] + "-" + end[4:6] + "-" + end[6:]) if all_rows else 0
    return {"requested": len(listing_ids), "batches": (len(listing_ids) + 199) // 200, "records": len(all_rows), "stored": stored, "days": days, "source": "ebay_analytics"}


def connect_listing_ids() -> list[dict[str, Any]]:
    with connect() as db:
        return [dict(row) for row in db.execute("SELECT listing_id FROM listings WHERE marketplace=? AND listing_id<>''", (_marketplace(),)).fetchall()]


def sync_fulfillment_orders(days: int = 90) -> dict[str, Any]:
    start = datetime.now(timezone.utc) - timedelta(days=max(1, min(int(days), 730)))
    filter_value = f"creationdate:[{start.strftime('%Y-%m-%dT%H:%M:%S.000Z')}..]"
    offset = 0
    total = 0
    while True:
        body = _ebay_get("/sell/fulfillment/v1/order", {"filter": filter_value, "limit": 200, "offset": offset})
        orders = body.get("orders") if isinstance(body.get("orders"), list) else []
        if not orders:
            break
        now = utc_now()
        with connect() as db:
            for order in orders:
                if not isinstance(order, dict) or not order.get("orderId"):
                    continue
                pricing = order.get("pricingSummary") if isinstance(order.get("pricingSummary"), dict) else {}
                total_value = _metric_number((pricing.get("total") or {}).get("value") if isinstance(pricing.get("total"), dict) else 0)
                values = (str(order.get("orderId")), str(order.get("creationDate") or ""), total_value, _json(order.get("lineItems") or []), _json(order), now)
                db.execute("INSERT INTO fulfillment_orders(order_id,created_at,total_value,line_items_json,raw_json,synced_at) VALUES(?,?,?,?,?,?) ON CONFLICT(order_id) DO UPDATE SET created_at=excluded.created_at,total_value=excluded.total_value,line_items_json=excluded.line_items_json,raw_json=excluded.raw_json,synced_at=excluded.synced_at", values)
                total += 1
        if len(orders) < 200:
            break
        offset += len(orders)
    return {"orders": total, "days": days, "source": "ebay_fulfillment"}


def performance_dashboard() -> dict[str, Any]:
    init_db()
    with connect() as db:
        rows = db.execute("SELECT p.*, l.data_json FROM listing_performance_daily p LEFT JOIN listings l ON l.id=p.listing_row_id ORDER BY p.metric_date DESC, p.updated_at DESC").fetchall()
    latest: dict[str, dict[str, Any]] = {}
    for row in rows:
        listing_id = str(row["ebay_listing_id"])
        if listing_id not in latest:
            entry = dict(row)
            entry["listing"] = _decode(entry.pop("data_json", "{}"), {})
            entry.pop("raw_json", None)
            latest[listing_id] = entry
    return {"lastSync": max((str(row["metric_date"]) for row in rows), default=""), "records": list(latest.values()), "rotation": rotation_queue(list(latest.values())), "capacity": listing_capacity()}


def listing_capacity() -> dict[str, Any]:
    active = sum(1 for item in list_listings() if str(item.get("status") or "active").lower() == "active")
    raw_max = os.environ.get("EBAY_LISTING_CAPACITY", "").strip()
    try:
        maximum = int(raw_max) if raw_max else None
    except ValueError:
        maximum = None
    state = "configured" if maximum is not None else "unknown"
    return {
        "activeListingCount": active,
        "configuredCapacity": maximum,
        "verifiedEbayAllowance": None,
        "capacityState": state,
        "remainingConfigured": max(0, maximum - active) if maximum is not None else None,
        "source": "hht_configured_value" if maximum is not None else "unknown",
        "note": "The active count is local inventory reconciliation. A configured value is not verified as an official eBay allowance.",
    }


def rotation_queue(performance: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    performance = performance or []
    queue: list[dict[str, Any]] = []
    for row in performance:
        listing = row.get("listing") if isinstance(row.get("listing"), dict) else {}
        active = str(listing.get("status") or "active").lower() == "active"
        ctr = _metric_optional_number(row.get("ctr")); views = _metric_optional_number(row.get("views")); impressions = _metric_optional_number(row.get("impressions"))
        if active and impressions is not None and ctr is not None and impressions >= 25 and ctr < 1:
            action, reason = "Optimize", "Low click-through rate despite listing impressions. Review title, category, and price evidence."
        elif active and (impressions is None or views is None):
            action, reason = "Keep", "Official traffic data is unavailable for one or more metrics; no lifecycle action is supported."
        elif active and impressions == 0 and views == 0:
            action, reason = "Keep", "No official traffic signal was returned for this listing; do not deactivate without more evidence."
        elif not active:
            action, reason = "Keep", "The listing was not returned by the latest active refresh; its final eBay lifecycle state is unknown."
        else:
            action, reason = "Keep", "Official traffic evidence does not justify a status change."
        queue.append({"id": f"rotation-{row.get('ebay_listing_id')}-{row.get('metric_date')}", "listingId": row.get("ebay_listing_id"), "title": listing.get("title") or row.get("ebay_listing_id"), "action": action, "current": "Active" if active else "Inactive/unknown", "proposed": action, "evidence": {"impressions": impressions, "views": views, "ctr": ctr, "conversionRate": _metric_optional_number(row.get("conversion_rate")), "transactions": _metric_optional_number(row.get("transactions")), "metricDate": row.get("metric_date"), "snapshotKind": row.get("snapshot_kind") or "rolling_listing_snapshot", "seasonalitySource": "seller_provided_only" if listing.get("sea") else "none"}, "confidence": "medium" if action != "Keep" else "low", "risk": "high" if action in {"Deactivate", "Reactivate"} else "low", "reason": reason, "approvalOnly": True})
    return queue


def approve_rotation_actions(
    action_ids: list[str],
    seller_id: str | None = None,
    auth_user_id: str | None = None,
) -> dict[str, Any]:
    raise ValueError("Rotation approvals are paused until rotation recommendations use the existing actions approval and rollback model.")


def start_active_import_job() -> dict[str, Any]:
    return _start_background_job("active_import", {})


def start_performance_sync_job(days: int = 30, listing_ids: list[str] | None = None) -> dict[str, Any]:
    payload = {"days": max(1, min(int(days), 90)), "listingIds": list(listing_ids or [])}
    return _start_background_job("performance_sync", payload)


def start_fulfillment_sync_job(days: int = 90) -> dict[str, Any]:
    return _start_background_job("fulfillment_sync", {"days": max(1, min(int(days), 730))})


def start_enrichment_job(listing_ids: list[str]) -> dict[str, Any]:
    requested = list(dict.fromkeys(str(value).strip() for value in listing_ids if str(value).strip()))
    if not requested:
        raise ValueError("Select at least one eBay listing before enrichment.")
    if len(requested) > 20:
        raise ValueError("Pilot enrichment is limited to 20 listing IDs per job.")
    return _start_background_job("enrichment", {"listingIds": requested})


def _positive_int(value: Any, default: int, maximum: int) -> int:
    try:
        return max(1, min(int(value), maximum))
    except (TypeError, ValueError):
        return default


def _enrichment_chunk_size() -> int:
    return _positive_int(os.environ.get("ENRICHMENT_CHUNK_SIZE"), ENRICHMENT_CHUNK_SIZE, ENRICHMENT_CHUNK_SIZE)


def _enrichment_rate_limit_seconds() -> float:
    try:
        return max(0.0, min(float(os.environ.get("ENRICHMENT_RATE_LIMIT_SECONDS", "0.35")), 10.0))
    except (TypeError, ValueError):
        return 0.35


def _checkpoint_counts(db: _Database) -> dict[str, int]:
    rows = db.execute("SELECT status, COUNT(*) AS count FROM enrichment_checkpoints GROUP BY status").fetchall()
    counts = {"pending": 0, "processing": 0, "processed": 0, "failed": 0}
    for row in rows:
        counts[str(row["status"])] = int(row["count"])
    counts["total"] = sum(counts.values())
    return counts


def _seed_enrichment_checkpoints() -> dict[str, int]:
    """Create durable local checkpoints for the current active catalog only."""
    init_db()
    now = utc_now()
    seeded = 0
    with connect() as db:
        rows = db.execute("SELECT * FROM listings WHERE marketplace=?", (_marketplace(),)).fetchall()
        for row in rows:
            item = _row_listing(row)
            if str(item.get("status") or "active").lower() != "active":
                continue
            listing_id = str(item.get("listingId") or "").strip()
            if not listing_id:
                continue
            already_enriched = str(item.get("source") or "") == "trading_get_item" and bool(item.get("attributeEvidence"))
            checkpoint_status = "processed" if already_enriched else "pending"
            enriched_at = str(item.get("enrichedAt") or now) if already_enriched else None
            inserted = db.execute(
                "INSERT INTO enrichment_checkpoints(listing_row_id,listing_id,status,attempts,enriched_at,last_error,details_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(listing_row_id) DO NOTHING",
                (row["id"], listing_id, checkpoint_status, 0, enriched_at, "", "{}", now, now),
            )
            if getattr(inserted, "rowcount", 1) == 1:
                seeded += 1
        counts = _checkpoint_counts(db)
    return {"seeded": seeded, **counts}


def _requeue_stale_enrichment_work() -> None:
    """Release only clearly abandoned work; active chunks are left untouched."""
    stale_seconds = _positive_int(os.environ.get("ENRICHMENT_STALE_SECONDS"), 900, 86_400)
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=stale_seconds)).isoformat()
    now = utc_now()
    with connect() as db:
        db.execute(
            "UPDATE enrichment_checkpoints SET status='pending', updated_at=? WHERE status='processing' AND updated_at<?",
            (now, cutoff),
        )
        db.execute(
            "UPDATE commerce_jobs SET status='queued', updated_at=? WHERE kind='full_enrichment' AND status='running' AND updated_at<?",
            (now, cutoff),
        )


def start_full_catalog_enrichment_job(resume_failed: bool = False) -> dict[str, Any]:
    """Queue resumable 20-item GetItem enrichment with no eBay mutation path."""
    seeded = _seed_enrichment_checkpoints()
    _requeue_stale_enrichment_work()
    with connect() as db:
        if resume_failed:
            now = utc_now()
            db.execute("UPDATE enrichment_checkpoints SET status='pending', last_error='', updated_at=? WHERE status='failed'", (now,))
            seeded = _checkpoint_counts(db)
            seeded["resumedFailed"] = True
        running = db.execute("SELECT * FROM commerce_jobs WHERE kind='full_enrichment' AND status IN ('queued','running') ORDER BY created_at DESC LIMIT 1").fetchone()
        if running:
            if running["status"] == "queued":
                threading.Thread(target=_run_until_terminal, args=(running["id"],), daemon=True).start()
            return {"jobId": running["id"], "status": running["status"], "kind": "full_enrichment", "readOnly": True, "checkpointSummary": seeded}
    started = _start_background_job("full_enrichment", {"chunkSize": _enrichment_chunk_size(), "resumeFailed": bool(resume_failed)})
    started["checkpointSummary"] = seeded
    return started


def start_audit_job() -> dict[str, Any]:
    return _start_background_job("audit", {})


def start_nvidia_vision_job(images: list[dict[str, Any]], seller_defaults: dict[str, Any] | None = None) -> dict[str, Any]:
    """Persist NVIDIA work for the worker dyno rather than holding a web request.

    Image bytes remain in the queued job only until the worker replaces the
    payload with the result. This route never changes eBay data.
    """
    if not isinstance(images, list) or not images:
        raise ValueError("Upload at least one image for NVIDIA analysis.")
    if len(images) > MAX_VISION_JOB_IMAGES:
        raise ValueError(f"NVIDIA analysis accepts at most {MAX_VISION_JOB_IMAGES} images per job.")
    prepared: list[dict[str, str]] = []
    total_bytes = 0
    for image in images:
        if not isinstance(image, dict):
            raise ValueError("Invalid image payload.")
        data = image.get("data")
        if not isinstance(data, (bytes, bytearray)) or not data:
            raise ValueError("Invalid image payload.")
        total_bytes += len(data)
        prepared.append({
            "data": base64.b64encode(bytes(data)).decode("ascii"),
            "mimeType": str(image.get("mimeType") or "image/jpeg"),
            "filename": str(image.get("filename") or "image.jpg")[:180],
        })
    if total_bytes > MAX_VISION_JOB_BYTES:
        raise ValueError("NVIDIA analysis photos exceed the 6 MB worker-job limit. Choose fewer or smaller photos.")
    return _start_background_job(
        "nvidia_vision",
        {"images": prepared, "sellerDefaults": seller_defaults if isinstance(seller_defaults, dict) else {}},
        run_in_web_thread=False,
    )


def start_routed_vision_job(images: list[dict[str, Any]], item_count: int = 1, seller_defaults: dict[str, Any] | None = None) -> dict[str, Any]:
    """Route normal work to Groq and heavy batches to the NVIDIA worker."""
    if not isinstance(images, list) or not images:
        raise ValueError("Upload at least one image.")
    route = choose_vision_route(item_count, len(images))
    if route["route"] == "groq":
        return {"route": route, "status": "use_synchronous_groq", "readOnly": True}
    if len(images) > NVIDIA_BATCH_MAX_PHOTOS:
        raise ValueError(f"Heavy batch is limited to {NVIDIA_BATCH_MAX_PHOTOS} photos per job.")
    prepared: list[dict[str, str]] = []
    total_bytes = 0
    for image in images:
        if not isinstance(image, dict) or not isinstance(image.get("data"), (bytes, bytearray)) or not image.get("data"):
            raise ValueError("Invalid image payload.")
        data = bytes(image["data"])
        total_bytes += len(data)
        prepared.append({"data": base64.b64encode(data).decode("ascii"), "mimeType": str(image.get("mimeType") or "image/jpeg"), "filename": str(image.get("filename") or "image.jpg")[:180]})
    if total_bytes > NVIDIA_BATCH_MAX_BYTES:
        raise ValueError("NVIDIA heavy-batch photos exceed the 100 MB worker-job limit.")
    return _start_background_job("nvidia_vision_batch", {"images": prepared, "itemCount": route["itemCount"], "sellerDefaults": seller_defaults if isinstance(seller_defaults, dict) else {}, "route": route}, run_in_web_thread=False) | {"route": route}


def _start_background_job(kind: str, payload: dict[str, Any], *, run_in_web_thread: bool = True) -> dict[str, Any]:
    init_db()
    job_id = str(uuid.uuid4())
    now = utc_now()
    with connect() as db:
        db.execute(
            "INSERT INTO commerce_jobs(id,kind,status,progress,result_json,error,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
            (job_id, kind, "queued", 0, _json(payload), "", now, now),
        )
    if run_in_web_thread:
        target = _run_until_terminal if kind == "full_enrichment" else run_job
        thread = threading.Thread(target=target, args=(job_id,), daemon=True)
        thread.start()
    return {"jobId": job_id, "status": "queued", "kind": kind, "readOnly": True}


def _run_until_terminal(job_id: str) -> None:
    """Keep a web-process fallback moving the persisted job between chunks.

    The dedicated worker can claim the same job after a restart; compare-and-set
    job claiming prevents duplicate eBay reads.
    """
    while True:
        job = run_job(job_id)
        if not job or job.get("status") in {"completed", "failed"}:
            return
        time.sleep(0.1)


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
        elif kind == "performance_sync":
            result = sync_performance(payload.get("days", 30), payload.get("listingIds") or None)
        elif kind == "fulfillment_sync":
            result = sync_fulfillment_orders(payload.get("days", 90))
        elif kind == "enrichment":
            result = enrich_listings(payload.get("listingIds", []))
        elif kind == "full_enrichment":
            result = _run_full_enrichment_chunk(job_id, _positive_int(payload.get("chunkSize"), _enrichment_chunk_size(), ENRICHMENT_CHUNK_SIZE))
            if not result["terminal"]:
                _update_job(job_id, "queued", result["progress"], result, "")
                return active_import_job(job_id)
            audited = audit_all()
            result["auditCount"] = audited["count"]
        elif kind == "audit":
            audited = audit_all()
            result = {"count": audited["count"], "readOnly": True}
        elif kind == "nvidia_vision":
            from .providers import UploadedImage, analyze_images

            payload_images = payload.get("images") if isinstance(payload, dict) else []
            images: list[UploadedImage] = []
            for entry in (payload_images if isinstance(payload_images, list) else []):
                if not isinstance(entry, dict):
                    continue
                try:
                    data = base64.b64decode(str(entry.get("data") or ""), validate=True)
                except (ValueError, TypeError):
                    continue
                if data:
                    images.append(UploadedImage(
                        data=data,
                        mime_type=str(entry.get("mimeType") or "image/jpeg"),
                        filename=str(entry.get("filename") or "image.jpg"),
                    ))
            if not images:
                raise ValueError("NVIDIA analysis job did not contain a usable image.")
            result = analyze_images(images, {
                "seller_defaults": payload.get("sellerDefaults") if isinstance(payload.get("sellerDefaults"), dict) else {},
                "try_alternate": True,
                # The dedicated worker is not behind the browser/Heroku request
                # timeout. Give NVIDIA enough time for a genuine inference while
                # retaining the provider chain as a safe fallback.
                "background_worker": True,
                "provider_timeout_seconds": _positive_int(os.environ.get("NVIDIA_WORKER_PROVIDER_TIMEOUT_SECONDS"), 35, 40),
                "deadline": time.monotonic() + _positive_int(os.environ.get("NVIDIA_WORKER_DEADLINE_SECONDS"), 75, 120),
            })
            result["readOnly"] = True
        elif kind == "nvidia_vision_batch":
            from .providers import UploadedImage, analyze_images

            payload_images = payload.get("images") if isinstance(payload, dict) else []
            decoded: list[UploadedImage] = []
            for entry in payload_images if isinstance(payload_images, list) else []:
                if not isinstance(entry, dict):
                    continue
                try:
                    data = base64.b64decode(str(entry.get("data") or ""), validate=True)
                except (ValueError, TypeError):
                    continue
                if data:
                    decoded.append(UploadedImage(data=data, mime_type=str(entry.get("mimeType") or "image/jpeg"), filename=str(entry.get("filename") or "image.jpg")))
            if not decoded:
                raise ValueError("NVIDIA heavy-batch job did not contain usable images.")
            results: list[dict[str, Any]] = []
            for offset in range(0, len(decoded), 5):
                result = analyze_images(decoded[offset:offset + 5], {
                    "seller_defaults": payload.get("sellerDefaults") if isinstance(payload.get("sellerDefaults"), dict) else {},
                    "try_alternate": True,
                    "background_worker": True,
                    "provider_timeout_seconds": _positive_int(os.environ.get("NVIDIA_WORKER_PROVIDER_TIMEOUT_SECONDS"), 35, 40),
                    "deadline": time.monotonic() + _positive_int(os.environ.get("NVIDIA_WORKER_DEADLINE_SECONDS"), 75, 120),
                })
                results.append({"group": (offset // 5) + 1, "startPhoto": offset + 1, "photoCount": min(5, len(decoded) - offset), "result": result})
            result = {"route": payload.get("route", {}), "groups": results, "processedPhotos": len(decoded), "readOnly": True}
        else:
            raise ValueError("Unsupported Commerce Agent job kind.")
        _update_job(job_id, "completed", 100, result, "")
        return active_import_job(job_id)
    except Exception as exc:
        logger.exception("Commerce Agent job failed: %s", kind)
        details: dict[str, Any] = {}
        message = str(exc)[:240]
        # Vision jobs must expose only normalized, credential-safe diagnostics
        # through the existing job endpoint. This allows the UI to explain why
        # NVIDIA or its fallback could not complete without leaking upstream
        # bodies, image data, or API credentials.
        if kind == "nvidia_vision":
            try:
                from .providers import ProviderError
                if isinstance(exc, ProviderError):
                    details = {"providerFailures": exc.failures}
                    if exc.retry_after_seconds is not None:
                        details["retryAfterSeconds"] = exc.retry_after_seconds
                    message = str(exc.safe_message or exc)[:240]
            except Exception:
                pass
        _update_job(job_id, "failed", 100, details, message)
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


def _claim_enrichment_chunk(chunk_size: int) -> list[dict[str, Any]]:
    """Claim up to 20 local checkpoints. The claim never calls eBay."""
    now = utc_now()
    with connect() as db:
        rows = db.execute(
            "SELECT c.*, l.data_json FROM enrichment_checkpoints c JOIN listings l ON l.id=c.listing_row_id WHERE c.status='pending' ORDER BY c.created_at ASC LIMIT ?",
            (chunk_size,),
        ).fetchall()
        claimed: list[dict[str, Any]] = []
        for row in rows:
            item = _decode(row["data_json"], {})
            if str(item.get("status") or "active").lower() != "active":
                db.execute("UPDATE enrichment_checkpoints SET status='failed', last_error=?, updated_at=? WHERE id=?", ("Listing is no longer active in the local catalog.", now, row["id"]))
                continue
            updated = db.execute("UPDATE enrichment_checkpoints SET status='processing', attempts=attempts+1, updated_at=? WHERE id=? AND status='pending'", (now, row["id"]))
            if getattr(updated, "rowcount", 1) == 1:
                claimed.append({"id": row["id"], "listingId": row["listing_id"], "listingRowId": row["listing_row_id"]})
    return claimed


def _set_enrichment_checkpoint(checkpoint_id: Any, status: str, record: dict[str, Any] | None = None, error: str = "") -> None:
    now = utc_now()
    with connect() as db:
        db.execute(
            "UPDATE enrichment_checkpoints SET status=?, enriched_at=?, last_error=?, details_json=?, updated_at=? WHERE id=?",
            (status, now if status == "processed" else None, error[:240], _json(record or {}), now, checkpoint_id),
        )


def _run_full_enrichment_chunk(job_id: str, chunk_size: int) -> dict[str, Any]:
    """Run one safely bounded read-only enrichment chunk and persist outcomes."""
    claimed = _claim_enrichment_chunk(chunk_size)
    rate_limit = _enrichment_rate_limit_seconds()
    processed = 0
    failed = 0
    for checkpoint in claimed:
        try:
            result = enrich_listings([checkpoint["listingId"]])
            if result.get("updated"):
                record = (result.get("records") or [{}])[0]
                _set_enrichment_checkpoint(checkpoint["id"], "processed", record)
                processed += 1
            else:
                issue = (result.get("errors") or [{}])[0]
                _set_enrichment_checkpoint(checkpoint["id"], "failed", error=str(issue.get("error") or "eBay returned no enrichment record."))
                failed += 1
        except Exception:
            logger.exception("Full enrichment failed for listing %s", checkpoint["listingId"])
            _set_enrichment_checkpoint(checkpoint["id"], "failed", error="Read-only listing enrichment failed.")
            failed += 1
        if rate_limit:
            time.sleep(rate_limit)
    with connect() as db:
        counts = _checkpoint_counts(db)
    terminal = counts["pending"] == 0 and counts["processing"] == 0
    completed = counts["processed"] + counts["failed"]
    progress = 100 if terminal else max(1, int((completed / counts["total"]) * 100)) if counts["total"] else 100
    return {
        "jobId": job_id,
        "chunkSize": chunk_size,
        "chunkProcessed": processed,
        "chunkFailed": failed,
        "terminal": terminal,
        "progress": progress,
        "checkpoints": counts,
        "readOnly": True,
    }


def count_listings() -> int:
    init_db()
    with connect() as db:
        row = db.execute("SELECT COUNT(*) AS count FROM listings").fetchone()
    return int(row["count"] if isinstance(row, dict) else row[0])


def classify_listing_ownership(listing: dict[str, Any]) -> str:
    """Classify management origin without guessing a lifecycle mutation path."""
    source = str(listing.get("source") or listing.get("activeSource") or "").lower()
    if listing.get("offerId") or "inventory" in source:
        return "inventory_api_managed"
    if "trading" in source or listing.get("listingId") and not listing.get("offerId"):
        return "trading_legacy_managed"
    if "feed" in source or listing.get("ebayFeedTaskId"):
        return "seller_hub_feed_managed"
    return "unknown"


def _row_listing(row: sqlite3.Row) -> dict[str, Any]:
    item = _decode(row["data_json"], {})
    item.update({"id": row["id"], "listingId": row["listing_id"], "offerId": row["offer_id"], "sku": row["sku"], "marketplace": row["marketplace"]})
    item.setdefault("ownershipClassification", row["ownership_classification"] if "ownership_classification" in row.keys() else classify_listing_ownership(item))
    return item


def list_listings(filters: dict[str, Any] | None = None, seller_id: str | None = None) -> list[dict[str, Any]]:
    filters = filters or {}
    init_db()
    with connect() as db:
        if seller_id:
            rows = db.execute("SELECT * FROM listings WHERE seller_id=? ORDER BY imported_at DESC", (seller_id,)).fetchall()
        else:
            rows = db.execute("SELECT * FROM listings ORDER BY imported_at DESC").fetchall()
    items = [_row_listing(row) for row in rows]
    status = str(filters.get("status") or "").lower()
    if status:
        items = [item for item in items if str(item.get("status") or "").lower() == status]
    return items


def _fetch_listing_detail_with_backoff(listing_id: str, timeout: float) -> dict[str, Any]:
    """Retry transient read-only GetItem failures with bounded exponential backoff."""
    retries = _positive_int(os.environ.get("ENRICHMENT_MAX_RETRIES"), 3, 5)
    base_delay = _enrichment_rate_limit_seconds()
    for attempt in range(retries):
        try:
            return fetch_listing_detail(listing_id, timeout=timeout)
        except EbayActiveError as exc:
            retryable = exc.status_code in {429, 500, 502, 503, 504}
            if not retryable or attempt + 1 >= retries:
                raise
            time.sleep(max(0.1, base_delay) * (2 ** attempt))
    raise EbayActiveError(502, "eBay listing detail retrieval failed.")


def enriched_catalog_page(page: int = 1, page_size: int = ENRICHMENT_PAGE_SIZE) -> dict[str, Any]:
    """Return only successfully enriched records in stable 25-item review pages."""
    init_db()
    page = _positive_int(page, 1, 100_000)
    page_size = _positive_int(page_size, ENRICHMENT_PAGE_SIZE, ENRICHMENT_PAGE_SIZE)
    offset = (page - 1) * page_size
    with connect() as db:
        total_row = db.execute("SELECT COUNT(*) AS count FROM enrichment_checkpoints WHERE status='processed'").fetchone()
        total = int(total_row["count"])
        rows = db.execute(
            "SELECT l.*, c.status AS enrichment_status, c.attempts AS enrichment_attempts, c.enriched_at, c.last_error, c.details_json FROM enrichment_checkpoints c JOIN listings l ON l.id=c.listing_row_id WHERE c.status='processed' ORDER BY c.enriched_at DESC, l.id DESC LIMIT ? OFFSET ?",
            (page_size, offset),
        ).fetchall()
    records = []
    for row in rows:
        item = _row_listing(row)
        item["enrichment"] = {
            "status": row["enrichment_status"],
            "attempts": row["enrichment_attempts"],
            "enrichedAt": row["enriched_at"],
            "lastError": row["last_error"],
            "details": _decode(row["details_json"], {}),
        }
        records.append(item)
    return {"page": page, "pageSize": page_size, "total": total, "totalPages": max(1, (total + page_size - 1) // page_size), "items": records}


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
                detail = _fetch_listing_detail_with_backoff(listing_id, timeout=timeout)
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
                    "officialGetItem": detail,
                    "attributeEvidence": normalize_evidence(
                        normalized,
                        source="ebay_get_item",
                        default_evidence="Official eBay GetItem field.",
                    ),
                    "watchCount": detail.get("watchCount", 0),
                    "location": detail.get("location", ""),
                    "enrichedAt": utc_now(),
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
    if not str(item.get("cat") or "").strip():
        findings.append({"field": "cat", "severity": "high", "message": "Category is unavailable and requires seller review."})
    if not str(item.get("pic") or "").strip():
        findings.append({"field": "pic", "severity": "medium", "message": "No image URLs were available for photo coverage review."})
    price = _price_float(item.get("price"))
    if price <= 0:
        findings.append({"field": "price", "severity": "high", "message": "Price is missing or invalid."})
    taxonomy = validate_listing(item)
    missing_required = taxonomy.get("missingRequiredAspects") if isinstance(taxonomy.get("missingRequiredAspects"), list) else []
    if taxonomy.get("status") == "valid" and missing_required:
        findings.append({"field": "item_specifics", "severity": "medium", "message": f"Review eBay-required specifics only: {', '.join(str(value) for value in missing_required[:12])}."})
    elif taxonomy.get("status") in {"missing", "unavailable", "not_configured"}:
        findings.append({"field": "item_specifics", "severity": "low", "message": "Required item specifics could not be confirmed from eBay Taxonomy; seller review is advisory."})
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
    note_only_review = bool(findings and not proposed)
    score = max(0, min(100, 100 - sum(18 if f["severity"] == "high" else 10 for f in findings)))
    classification = "Excellent" if score >= 90 else "Good" if score >= 75 else "Needs Optimization" if score >= 50 else "High Priority"
    if any(f["severity"] == "high" for f in findings) or note_only_review:
        classification = "Needs Review"
    confidence = "low" if note_only_review else "high" if findings and all(f["field"] not in {"cat", "price"} for f in findings) else "medium"
    reason = "; ".join(f["message"] for f in findings) or "No material listing quality issue was identified from the imported data."
    return {"score": score, "classification": classification, "findings": findings, "proposed": proposed, "reason": reason, "confidence": confidence, "risk": "high" if any(f["severity"] == "high" for f in findings) or note_only_review else "low", "evidence": evidence_summary(item), "taxonomy": taxonomy, "soldPricing": pricing, "soldComparableSummary": sold, "demand": demand}


def audit_all(seller_id: str | None = None) -> dict[str, Any]:
    init_db()
    results = []
    now = utc_now()
    with connect() as db:
        query = "SELECT * FROM listings"
        params: tuple[Any, ...] = ()
        if seller_id:
            query += " WHERE seller_id=?"
            params = (seller_id,)
        for row in db.execute(query, params).fetchall():
            item = _row_listing(row)
            if str(item.get("status") or "active").lower() != "active":
                # A completed active-list refresh may retain historical local
                # records. They must not remain in the live approval queue.
                db.execute("UPDATE recommendations SET is_current=? WHERE listing_row_id=? AND is_current=?", (False if db.postgres else 0, row["id"], True if db.postgres else 1))
                continue
            audit = audit_listing(item)
            # Preserve every audit as history, but make the new audit the only
            # current review card for this listing.
            db.execute("UPDATE recommendations SET is_current=? WHERE listing_row_id=? AND is_current=?", (False if db.postgres else 0, row["id"], True if db.postgres else 1))
            version_row = db.execute("SELECT COALESCE(MAX(version_number), 0) AS version FROM recommendations WHERE listing_row_id=?", (row["id"],)).fetchone()
            version_number = int(version_row["version"] if version_row else 0) + 1
            recommendation_id = str(uuid.uuid4())
            stored_current = {**item, "attributeEvidence": audit["evidence"], "taxonomyValidation": audit["taxonomy"], "soldPricing": audit["soldPricing"], "soldComparableSummary": audit["soldComparableSummary"], "demandMetrics": audit["demand"]}
            db.execute("INSERT INTO recommendations(id,listing_row_id,current_json,proposed_json,findings_json,score,classification,reason,confidence,risk,status,created_at,updated_at,version_number,is_current,seller_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (recommendation_id, row["id"], _json(stored_current), _json(audit["proposed"]), _json(audit["findings"]), audit["score"], audit["classification"], audit["reason"], audit["confidence"], audit["risk"], "Pending", now, now, version_number, True if db.postgres else 1, row["seller_id"] if "seller_id" in row.keys() else seller_id))
            results.append({"recommendationId": recommendation_id, "listing": stored_current, **audit, "status": "Pending", "version": version_number, "isCurrent": True})
    return {"count": len(results), "results": results}


def _meaningful_proposed(listing: dict[str, Any], proposed: Any) -> dict[str, Any]:
    if not isinstance(proposed, dict):
        return {}
    aliases = {"material": "mat"}
    result = {}
    for key, value in proposed.items():
        # Legacy audits stored explanatory review text as an editable `notes`
        # proposal. Notes are advisory evidence, not an optimization change;
        # never present them as a current-to-proposed field mutation.
        if key == "notes":
            continue
        current = listing.get(aliases.get(key, key))
        if key == "price":
            if abs(_price_float(current) - _price_float(value)) < 0.01:
                continue
        elif str(current or "").strip().casefold() == str(value or "").strip().casefold():
            continue
        result[key] = value
    return result


def _presentation_findings(listing: dict[str, Any], findings: Any) -> list[dict[str, Any]]:
    """Hide legacy generic warnings when stored taxonomy evidence disproves them."""
    if not isinstance(findings, list):
        return []
    taxonomy = listing.get("taxonomyValidation") if isinstance(listing.get("taxonomyValidation"), dict) else {}
    normalized: list[dict[str, Any]] = []
    replaced_specifics = False
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        message = str(finding.get("message") or "")
        if finding.get("field") == "item_specifics" and taxonomy.get("status") == "valid":
            missing = taxonomy.get("missingRequiredAspects") if isinstance(taxonomy.get("missingRequiredAspects"), list) else []
            if message.lower().startswith("review missing or uncertain specifics:"):
                if missing and not replaced_specifics:
                    normalized.append({"field": "item_specifics", "severity": "medium", "message": f"Review eBay-required specifics only: {', '.join(str(value) for value in missing[:12])}."})
                    replaced_specifics = True
                continue
        if "condition id defaulted to pre-owned" in message.lower() and (listing.get("condition") or listing.get("conditionId")):
            continue
        normalized.append(finding)
    return normalized


def _recommendation(row: sqlite3.Row) -> dict[str, Any]:
    listing = _decode(row["current_json"], {})
    proposed = _meaningful_proposed(listing, _decode(row["proposed_json"], {}))
    findings = _presentation_findings(listing, _decode(row["findings_json"], []))
    return {"recommendationId": row["id"], "actionId": row["action_id"] if "action_id" in row.keys() else "", "listing": listing, "proposed": proposed, "findings": findings, "evidence": listing.get("attributeEvidence", []), "taxonomy": listing.get("taxonomyValidation", {}), "soldPricing": listing.get("soldPricing", {}), "soldComparableSummary": listing.get("soldComparableSummary", {}), "demand": listing.get("demandMetrics", {}), "score": row["score"], "classification": row["classification"], "reason": row["reason"], "confidence": row["confidence"], "risk": row["risk"], "status": row["status"], "version": row["version_number"] if "version_number" in row.keys() else 1, "isCurrent": bool(row["is_current"]) if "is_current" in row.keys() else True, "createdAt": row["created_at"], "updatedAt": row["updated_at"]}


def recommendations(status: str = "", seller_id: str | None = None) -> list[dict[str, Any]]:
    init_db()
    query = "SELECT r.*, (SELECT a.id FROM actions a WHERE a.recommendation_id=r.id ORDER BY a.created_at DESC LIMIT 1) AS action_id FROM recommendations r WHERE r.is_current=?"
    current_value = True
    params: tuple[Any, ...] = (current_value,)
    if status:
        query += " AND r.status=?"
        params = (current_value, status)
    if seller_id:
        query += " AND r.seller_id=?"
        params += (seller_id,)
    query += " ORDER BY score ASC, created_at DESC"
    with connect() as db:
        return [_recommendation(row) for row in db.execute(query, params).fetchall()]


def recommendations_page(status: str = "", page: int = 1, page_size: int = ENRICHMENT_PAGE_SIZE, seller_id: str | None = None) -> dict[str, Any]:
    """Return a stable small review page instead of an unbounded queue payload."""
    init_db()
    page = _positive_int(page, 1, 100_000)
    page_size = _positive_int(page_size, ENRICHMENT_PAGE_SIZE, ENRICHMENT_PAGE_SIZE)
    where = ""
    params: tuple[Any, ...] = ()
    if status:
        where = " WHERE r.is_current=? AND r.status=?"
        params = (True, status)
    else:
        where = " WHERE r.is_current=?"
        params = (True,)
    if seller_id:
        where += " AND r.seller_id=?"
        params += (seller_id,)
    with connect() as db:
        total_row = db.execute(f"SELECT COUNT(*) AS count FROM recommendations r{where}", params).fetchone()
        total = int(total_row["count"])
        query = "SELECT r.*, (SELECT a.id FROM actions a WHERE a.recommendation_id=r.id ORDER BY a.created_at DESC LIMIT 1) AS action_id FROM recommendations r"
        query += where + " ORDER BY r.score ASC, r.created_at DESC LIMIT ? OFFSET ?"
        rows = db.execute(query, params + (page_size, (page - 1) * page_size)).fetchall()
    return {"page": page, "pageSize": page_size, "total": total, "totalPages": max(1, (total + page_size - 1) // page_size), "items": [_recommendation(row) for row in rows]}


def get_recommendation(recommendation_id: str, seller_id: str | None = None) -> dict[str, Any] | None:
    init_db()
    with connect() as db:
        row = db.execute("SELECT * FROM recommendations WHERE id=?", (recommendation_id,)).fetchone()
    if row and seller_id and str(row["seller_id"] or "") != seller_id:
        raise PermissionError("This recommendation belongs to a different seller.")
    return _recommendation(row) if row else None


def approve_recommendation(
    recommendation_id: str,
    approved: dict[str, Any] | None = None,
    seller_id: str | None = None,
    auth_user_id: str | None = None,
) -> dict[str, Any]:
    recommendation = get_recommendation(recommendation_id, seller_id)
    if not recommendation:
        raise ValueError("Recommendation not found.")
    if not recommendation.get("isCurrent", True):
        raise ValueError("This recommendation version has been superseded; review the current listing recommendation.")
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
    recommendation_version = int(recommendation.get("version") or 1)
    listing_snapshot = dict(current)
    listing_state_hash = _listing_state_hash(listing_snapshot)
    with connect() as db:
        rec_row = db.execute("SELECT r.listing_row_id, r.version_number, r.is_current, r.seller_id, l.data_json, l.seller_id AS listing_seller_id FROM recommendations r JOIN listings l ON l.id=r.listing_row_id WHERE r.id=?", (recommendation_id,)).fetchone()
        if not rec_row or not bool(rec_row["is_current"]):
            raise ValueError("This recommendation version has been superseded; review the current listing recommendation.")
        if seller_id and (str(rec_row["seller_id"] or "") != seller_id or str(rec_row["listing_seller_id"] or "") != seller_id):
            raise PermissionError("This recommendation belongs to a different seller.")
        recommendation_version = int(rec_row["version_number"])
        listing_snapshot = _decode(rec_row["data_json"], current)
        listing_state_hash = _listing_state_hash(listing_snapshot)
        db.execute("INSERT INTO actions(id,recommendation_id,listing_row_id,approved_json,old_json,created_at,recommendation_version,listing_snapshot_json,listing_state_hash,seller_id,approved_by) VALUES(?,?,?,?,?,?,?,?,?,?,?)", (action_id, recommendation_id, rec_row["listing_row_id"], _json(changes), _json({key: current.get(key) for key in changes}), now, recommendation_version, _json(listing_snapshot), listing_state_hash, seller_id, auth_user_id))
        db.execute("UPDATE recommendations SET status='Approved', updated_at=? WHERE id=?", (now, recommendation_id))
        listing_row = db.execute("SELECT listing_row_id FROM recommendations WHERE id=?", (recommendation_id,)).fetchone()
        if listing_row:
            version_row = db.execute("SELECT COALESCE(MAX(version_number), 0) AS version FROM listing_versions WHERE listing_row_id=?", (listing_row["listing_row_id"],)).fetchone()
            version_number = int(version_row["version"] if version_row else 0) + 1
            db.execute("INSERT INTO listing_versions(id,listing_row_id,version_number,source,state_json,evidence_json,created_at) VALUES(?,?,?,?,?,?,?)", (str(uuid.uuid4()), listing_row["listing_row_id"], version_number, "seller_approved", _json({**current, **changes}), _json({"recommendationId": recommendation_id, "findings": recommendation.get("findings", [])}), now))
    return {"actionId": action_id, "recommendationId": recommendation_id, "status": "Approved", "approved": changes}


def apply_action(action_id: str, seller_id: str | None = None, auth_user_id: str | None = None) -> dict[str, Any]:
    init_db()
    with connect() as db:
        if db.postgres:
            row = db.execute("SELECT a.*, r.current_json, r.version_number, r.is_current, r.listing_row_id AS recommendation_listing_row_id, r.seller_id AS recommendation_seller_id, l.data_json AS listing_data_json, l.sku AS listing_sku, l.listing_id AS listing_listing_id, l.seller_id AS listing_seller_id, l.ownership_classification AS listing_ownership_classification FROM actions a JOIN recommendations r ON r.id=a.recommendation_id JOIN listings l ON l.id=r.listing_row_id WHERE a.id=? FOR UPDATE", (action_id,)).fetchone()
        else:
            db.connection.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT a.*, r.current_json, r.version_number, r.is_current, r.listing_row_id AS recommendation_listing_row_id, r.seller_id AS recommendation_seller_id, l.data_json AS listing_data_json, l.sku AS listing_sku, l.listing_id AS listing_listing_id, l.seller_id AS listing_seller_id, l.ownership_classification AS listing_ownership_classification FROM actions a JOIN recommendations r ON r.id=a.recommendation_id JOIN listings l ON l.id=r.listing_row_id WHERE a.id=?", (action_id,)).fetchone()
        if not row:
            raise ValueError("Approved action not found.")
        if seller_id and any(str(row[column] or "") != seller_id for column in ("seller_id", "recommendation_seller_id", "listing_seller_id")):
            raise PermissionError("This action belongs to a different seller.")
        if auth_user_id and str(row["approved_by"] or "") != auth_user_id:
            raise PermissionError("This action was not approved by the authenticated user.")
        if row["status"] not in {"Approved", "Failed"}:
            raise ValueError("Only an approved action can be applied.")
        current = _decode(row["current_json"], {})
        listing_state = _decode(row["listing_data_json"], {})
        stale_reason = ""
        if not bool(row["is_current"]):
            stale_reason = "The linked recommendation is superseded and requires reapproval."
        elif int(row["recommendation_version"] or 0) != int(row["version_number"] or 0):
            stale_reason = "The approved recommendation version no longer matches the current version."
        elif int(row["listing_row_id"]) != int(row["recommendation_listing_row_id"]):
            stale_reason = "The approved listing identity no longer matches the recommendation."
        elif str(current.get("sku") or "") != str(row["listing_sku"] or "") or str(current.get("listingId") or "") != str(row["listing_listing_id"] or ""):
            stale_reason = "The approved SKU or listing identity no longer matches the stored listing."
        elif not row["listing_state_hash"] or row["listing_state_hash"] != _listing_state_hash(listing_state):
            stale_reason = "The listing state changed after approval and requires reapproval."
        if stale_reason:
            db.execute("UPDATE actions SET status='Stale', error=? WHERE id=?", (stale_reason, action_id))
            db.connection.commit()
            raise ValueError(stale_reason)
        ownership = str(row["listing_ownership_classification"] or listing_state.get("ownershipClassification") or "").strip().lower()
        if ownership != "inventory_api_managed":
            raise PermissionError("Only inventory_api_managed listings may be updated through the Inventory API.")
        offer_id = str(current.get("offerId") or "")
        if not offer_id:
            raise ValueError("This listing has no eBay offer ID; it cannot be updated through the Inventory API.")
        changes = _decode(row["approved_json"], {})
        merged = {**current, **changes, "sku": current.get("sku")}
        try:
            result = update_ebay_offer(offer_id, merged)
        except EbayDraftError as exc:
            db.execute("UPDATE actions SET status='Failed', error=? WHERE id=?", (exc.safe_message, action_id))
            db.execute("UPDATE recommendations SET status='Failed', updated_at=? WHERE id=?", (utc_now(), row["recommendation_id"]))
            raise
        now = utc_now()
        db.execute("UPDATE actions SET status='Applied', new_json=?, ebay_result_json=?, applied_at=?, applied_by=? WHERE id=?", (_json(changes), _json(result), now, auth_user_id, action_id))
        db.execute("UPDATE recommendations SET status='Applied', updated_at=? WHERE id=?", (now, row["recommendation_id"]))
    return {"actionId": action_id, "recommendationId": row["recommendation_id"], "status": "Applied", "result": result}


def explain_recommendation(recommendation_id: str, seller_id: str | None = None) -> dict[str, Any]:
    recommendation = get_recommendation(recommendation_id, seller_id)
    if not recommendation:
        raise ValueError("Recommendation not found.")
    return {
        "recommendationId": recommendation_id,
        "status": recommendation["status"],
        "classification": recommendation["classification"],
        "score": recommendation["score"],
        "reason": recommendation["reason"],
        "findings": recommendation["findings"],
        "evidence": recommendation["evidence"],
        "taxonomy": recommendation["taxonomy"],
        "soldPricing": recommendation["soldPricing"],
        "demand": recommendation["demand"],
        "current": recommendation["listing"],
        "proposed": recommendation["proposed"],
        "disclaimer": "This explanation uses stored, validated evidence and does not add facts that were not present in the listing or configured data sources.",
    }


def set_recommendation_status(recommendation_id: str, status: str, seller_id: str | None = None) -> dict[str, Any]:
    if status not in {"Rejected", "Skipped"}:
        raise ValueError("Unsupported recommendation decision.")
    recommendation = get_recommendation(recommendation_id, seller_id)
    if not recommendation:
        raise ValueError("Recommendation not found.")
    if recommendation["status"] != "Pending":
        raise ValueError("Only pending recommendations can be rejected or skipped.")
    with connect() as db:
        db.execute("UPDATE recommendations SET status=?, updated_at=? WHERE id=?", (status, utc_now(), recommendation_id))
    return {"recommendationId": recommendation_id, "status": status}


def bulk_approve(
    recommendation_ids: list[str],
    seller_id: str | None = None,
    auth_user_id: str | None = None,
) -> dict[str, Any]:
    requested = list(dict.fromkeys(str(value).strip() for value in recommendation_ids if str(value).strip()))
    if not requested or len(requested) > 25:
        raise ValueError("Provide between 1 and 25 recommendation IDs.")
    approved: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for recommendation_id in requested:
        try:
            approved.append(approve_recommendation(recommendation_id, seller_id=seller_id, auth_user_id=auth_user_id))
        except (ValueError, PermissionError) as exc:
            errors.append({"recommendationId": recommendation_id, "error": str(exc)})
    return {"requested": len(requested), "approved": len(approved), "failed": len(errors), "results": approved, "errors": errors}


def rollback_action(action_id: str, seller_id: str | None = None, auth_user_id: str | None = None) -> dict[str, Any]:
    init_db()
    with connect() as db:
        row = db.execute("SELECT a.*, r.current_json, r.seller_id AS recommendation_seller_id, l.seller_id AS listing_seller_id FROM actions a JOIN recommendations r ON r.id=a.recommendation_id JOIN listings l ON l.id=a.listing_row_id WHERE a.id=?", (action_id,)).fetchone()
    if not row:
        raise ValueError("Action not found.")
    if seller_id and any(str(row[column] or "") != seller_id for column in ("seller_id", "recommendation_seller_id", "listing_seller_id")):
        raise PermissionError("This action belongs to a different seller.")
    if row["status"] != "Applied":
        raise ValueError("Only an applied action can be rolled back.")
    current = _decode(row["current_json"], {})
    old_values = _decode(row["old_json"], {})
    new_values = _decode(row["new_json"], {})
    listing_id = str(current.get("listingId") or "").strip()
    offer_id = str(current.get("offerId") or "").strip()
    if not listing_id or not offer_id or not old_values or not new_values:
        raise ValueError("This action does not contain enough official identifiers and snapshots for a safe rollback.")
    try:
        official = fetch_listing_detail(listing_id)
    except Exception as exc:
        raise ValueError("eBay could not verify the current listing state; rollback was not sent.") from exc
    for field, expected in new_values.items():
        actual = official.get(field, current.get(field))
        if str(actual or "") != str(expected or ""):
            raise ValueError(f"Rollback stopped because eBay field '{field}' no longer matches the applied value.")
    restored = {**current, **old_values, "sku": current.get("sku")}
    result = update_ebay_offer(offer_id, restored)
    now = utc_now()
    with connect() as db:
        db.execute("UPDATE actions SET status='RolledBack', ebay_result_json=?, applied_at=?, rolled_back_by=? WHERE id=?", (_json(result), now, auth_user_id, action_id))
        db.execute("UPDATE recommendations SET status='RolledBack', updated_at=? WHERE id=?", (now, row["recommendation_id"]))
        db.execute("UPDATE listings SET data_json=?, imported_at=? WHERE id=(SELECT listing_row_id FROM actions WHERE id=?)", (_json(restored), now, action_id))
    return {"actionId": action_id, "recommendationId": row["recommendation_id"], "status": "RolledBack", "result": result}


def history(seller_id: str | None = None) -> list[dict[str, Any]]:
    init_db()
    with connect() as db:
        query = "SELECT a.*, r.current_json FROM actions a JOIN recommendations r ON r.id=a.recommendation_id"
        params: tuple[Any, ...] = ()
        if seller_id:
            query += " WHERE a.seller_id=?"
            params = (seller_id,)
        rows = db.execute(query + " ORDER BY a.created_at DESC", params).fetchall()
    return [{"actionId": row["id"], "recommendationId": row["recommendation_id"], "listing": _decode(row["current_json"], {}), "approved": _decode(row["approved_json"], {}), "old": _decode(row["old_json"], {}), "new": _decode(row["new_json"], {}), "status": row["status"], "error": row["error"], "ebayResult": _decode(row["ebay_result_json"], {}), "createdAt": row["created_at"], "appliedAt": row["applied_at"], "rollbackEligible": row["status"] == "Applied" and bool(_decode(row["old_json"], {})) and bool(_decode(row["new_json"], {})), "rollbackNote": "Eligible only after eBay confirms the listing still has the applied values." if row["status"] == "Applied" else ""} for row in rows]


def dashboard(seller_id: str | None = None) -> dict[str, Any]:
    items = list_listings(seller_id=seller_id)
    recs = recommendations(seller_id=seller_id)
    active_items = [item for item in items if str(item.get("status") or "active").lower() == "active"]
    active_recs = [recommendation for recommendation in recs if str((recommendation.get("listing") or {}).get("status") or "active").lower() == "active"]
    return {"connectedStore": "eBay", "listingsFound": len(active_items), "recordsFound": len(items), "recommendations": len(active_recs), "needOptimization": sum(1 for r in active_recs if r["classification"] in {"Needs Optimization", "High Priority", "Needs Review"}), "titleImprovements": sum(1 for r in active_recs if any(f.get("field") == "title" for f in r["findings"])), "missingItemSpecifics": sum(1 for r in active_recs if any(f.get("field") == "item_specifics" for f in r["findings"])), "needsReview": sum(1 for r in active_recs if r["classification"] == "Needs Review"), "recovery": seller_recovery_metrics(active_recs, active_items), "mode": "recommend"}


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


__all__ = ["dashboard", "import_listings", "list_listings", "audit_all", "recommendations", "recommendations_page", "get_recommendation", "explain_recommendation", "approve_recommendation", "bulk_approve", "set_recommendation_status", "apply_action", "rollback_action", "history", "settings", "update_settings"]
