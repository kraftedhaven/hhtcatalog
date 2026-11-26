"""Market evidence pricing recommendations and honest demand metrics."""

from __future__ import annotations

import csv
import os
import statistics
from typing import Any

import requests

from .market_evidence import (
    INSUFFICIENT_MARKET_MESSAGE,
    PRICING_SOURCES,
    _money,
    _now,
    build_error_pricing_result,
    build_pricing_result,
    sanitize_market_evidence,
)

__all__ = [
    "INSUFFICIENT_MARKET_MESSAGE",
    "PRICING_SOURCES",
    "demand_score",
    "pricing_recommendation",
    "sanitize_market_evidence",
    "seller_recovery_metrics",
    "sold_price_summary",
]

_DEFAULT_NON_EVIDENCE_SEASONS = {"", "all seasons", "not visible", "n/a", "unknown"}


def _sold_query(item: dict[str, Any]) -> str:
    parts = [str(item.get(key) or "").strip() for key in ("brand", "model", "type", "style", "size")]
    return " ".join(part for part in parts if part)


def _fetch_remote_sold_records(item: dict[str, Any], query: str, configured: str) -> tuple[list[Any] | None, str]:
    headers = {"Accept": "application/json"}
    token = os.environ.get("SOLD_COMPS_API_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    timeout = float(os.environ.get("SOLD_COMPS_TIMEOUT_SECONDS", "5"))
    try:
        response = requests.get(
            configured,
            params={"query": query, "categoryId": str(item.get("cat") or "")},
            headers=headers,
            timeout=timeout,
        )
        if response.status_code >= 400:
            return None, "Sold-comparable provider was unavailable."
        body = response.json()
    except (requests.RequestException, ValueError, TypeError):
        return None, "Sold-comparable provider was unavailable."
    records = body.get("items", body.get("results", [])) if isinstance(body, dict) else body
    return (records if isinstance(records, list) else []), ""


def _partition_sold_prices(item: dict[str, Any], records: list[Any]) -> tuple[list[float], list[float]]:
    exact: list[float] = []
    similar: list[float] = []
    for record in records[:100]:
        if not isinstance(record, dict) or not _usable_sold_record(item, record):
            continue
        price = _record_price(record)
        if price <= 0:
            continue
        (exact if _exact_match(item, record) else similar).append(price)
    return exact, similar


def _build_sold_stats(prices: list[float], source: str, source_type: str, query: str) -> dict[str, Any]:
    prices.sort()
    count = len(prices)
    p25 = round(float(prices[max(0, int((count - 1) * 0.25))]), 2)
    p75 = round(float(prices[min(count - 1, int((count - 1) * 0.75))]), 2)
    return {
        "status": "ok",
        "kind": "sold_comps",
        "source": source,
        "pricingSource": source_type,
        "sampleSize": count,
        "query": query,
        "lowSoldPrice": round(min(prices), 2),
        "medianSoldPrice": round(statistics.median(prices), 2),
        "highSoldPrice": round(max(prices), 2),
        "p25SoldPrice": p25,
        "p75SoldPrice": p75,
        "dataFreshness": _now(),
        "message": "Sold-comparable summary is advisory; no price change is applied automatically.",
    }


def sold_price_summary(item: dict[str, Any]) -> dict[str, Any]:
    """Return verified sold-comparable data only; never relabel active listings as sold."""
    existing = _existing_sold_summary(item)
    if existing:
        return existing

    records = _sold_records_from_csv()
    source = "seller_uploaded_csv" if records else ""
    configured = os.environ.get("SOLD_COMPS_API_URL", "").strip()
    query = _sold_query(item)

    if not records and not configured:
        return {
            "status": "not_configured",
            "kind": "sold_comps",
            "sampleSize": 0,
            "query": query,
            "message": "Sold-comparable provider is not configured.",
        }

    if not records:
        fetched, err_msg = _fetch_remote_sold_records(item, query, configured)
        if fetched is None:
            return {"status": "unavailable", "kind": "sold_comps", "sampleSize": 0, "query": query, "message": err_msg}
        records = fetched
        source = "marketplace_insights_or_external_api"

    exact, similar = _partition_sold_prices(item, records)
    prices = exact or similar
    if not prices:
        return {
            "status": "no_matches",
            "kind": "sold_comps",
            "sampleSize": 0,
            "query": query,
            "message": "No usable sold comparables were returned.",
        }
    return _build_sold_stats(prices, source, "exact_used_sold" if exact else "similar_used_sold", query)


def _pricing_query(item: dict[str, Any], clean_sold: dict[str, Any], clean_active: dict[str, Any]) -> str:
    for candidate in (clean_sold.get("query"), clean_active.get("keyword"), item.get("pricingSearchKeywords")):
        text = str(candidate or "").strip()
        if text:
            return text
    return ""


def _ai_fallback_price(item: dict[str, Any]) -> float:
    for key in ("aiEstimatedPrice", "modelEstimatedPrice", "visionEstimatedPrice"):
        val = _money(item.get(key))
        if val > 0:
            return val
    return 0.0


def _sold_pricing_result(
    clean_sold: dict[str, Any],
    clean_active: dict[str, Any],
    current: float,
    ai_price: float,
    query: str,
    demand_data: dict[str, Any],
) -> dict[str, Any]:
    src = clean_sold.get("pricingSource")
    source = src if src in PRICING_SOURCES else "similar_used_sold"
    n = int(clean_sold.get("sampleSize") or 0)
    conf = "high" if (source == "exact_used_sold" and n >= 3) else "medium"
    return build_pricing_result(
        source, _money(clean_sold.get("medianSoldPrice")), current, conf, n,
        _money(clean_sold.get("lowSoldPrice")), _money(clean_sold.get("highSoldPrice")),
        "Price is based on verified used sold-comparable records.", query,
        ai_price=ai_price, sold_summary=clean_sold, active_summary=clean_active,
        demand=demand_data, p25=_money(clean_sold.get("p25SoldPrice")),
        p75=_money(clean_sold.get("p75SoldPrice")), insufficient=False,
    )


def _active_pricing_result(
    clean_sold: dict[str, Any],
    clean_active: dict[str, Any],
    current: float,
    ai_price: float,
    query: str,
    demand_data: dict[str, Any],
) -> dict[str, Any]:
    n = int(clean_active.get("sampleSize") or 0)
    resolved_query = str(clean_active.get("keyword") or query)
    return build_pricing_result(
        "active_comparable", _money(clean_active.get("medianActivePrice")), current,
        "medium" if n >= 3 else "low", n,
        _money(clean_active.get("lowActivePrice")), _money(clean_active.get("highActivePrice")),
        f"Sold comps unavailable; based on {n} active eBay comparable listings (advisory ask prices).",
        resolved_query, ai_price=ai_price, sold_summary=clean_sold, active_summary=clean_active,
        demand=demand_data, p25=_money(clean_active.get("p25ActivePrice")),
        p75=_money(clean_active.get("p75ActivePrice")), insufficient=False,
    )


def pricing_recommendation(
    item: dict[str, Any],
    *,
    sold_summary: dict[str, Any] | None = None,
    active_summary: dict[str, Any] | None = None,
    demand: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a market-backed or clearly labeled fallback pricing recommendation."""
    raw_sold = sold_price_summary(item) if sold_summary is None else sold_summary
    raw_active = item.get("activeListingEstimate") if active_summary is None else active_summary
    clean_sold, clean_active = sanitize_market_evidence(
        raw_sold, raw_active if isinstance(raw_active, dict) else None
    )
    current = _money(item.get("currentPrice", item.get("price")))
    ai_price = _ai_fallback_price(item)
    query = _pricing_query(item, clean_sold, clean_active)
    demand_data = demand if isinstance(demand, dict) else demand_score(
        item, sold_summary=clean_sold, active_summary=clean_active
    )

    if clean_sold.get("status") == "ok" and _money(clean_sold.get("medianSoldPrice")) > 0:
        return _sold_pricing_result(clean_sold, clean_active, current, ai_price, query, demand_data)
    if clean_active.get("status") == "ok" and _money(clean_active.get("medianActivePrice")) > 0:
        return _active_pricing_result(clean_sold, clean_active, current, ai_price, query, demand_data)
    if ai_price > 0:
        return build_pricing_result(
            "ai_estimate", ai_price, current, "low", 0, ai_price, ai_price,
            INSUFFICIENT_MARKET_MESSAGE, query, ai_price=ai_price,
            sold_summary=clean_sold, active_summary=clean_active, demand=demand_data, insufficient=True,
        )
    if current > 0:
        return build_pricing_result(
            "seller_price_fallback", current, current, "low", 0, current, current,
            f"{INSUFFICIENT_MARKET_MESSAGE} Existing seller price retained.", query,
            ai_price=ai_price, sold_summary=clean_sold, active_summary=clean_active,
            demand=demand_data, insufficient=True,
        )
    return build_error_pricing_result(current, query, clean_sold, clean_active)


def _has_verified_season_source(item: dict[str, Any]) -> bool:
    prov = item.get("provenance")
    sea_prov = prov.get("sea") if isinstance(prov, dict) else None
    if not isinstance(sea_prov, dict):
        return False
    if sea_prov.get("sellerConfirmed"):
        return True
    return str(sea_prov.get("source") or "") in {"seller_input", "observed_photo_fact", "demand_signal"}


def _explicit_seasonal_signal(item: dict[str, Any]) -> dict[str, Any]:
    explicit = item.get("seasonalEvidence")
    if isinstance(explicit, dict) and explicit.get("supported") and explicit.get("signal"):
        return {
            "status": "supported",
            "signal": str(explicit["signal"]),
            "evidence": str(explicit.get("evidence") or "Verified seasonal market evidence"),
        }
    sea_raw = str(item.get("sea") or "").strip()
    if _has_verified_season_source(item) and sea_raw.casefold() not in _DEFAULT_NON_EVIDENCE_SEASONS:
        return {
            "status": "supported",
            "signal": sea_raw,
            "evidence": f"Attribute-backed seasonality ({sea_raw})",
        }
    return {
        "status": "unsupported",
        "signal": None,
        "evidence": "No seasonal or future-demand dataset is available.",
    }


def _sold_demand_metrics(sold_count: int, active_count: int, active_observed: bool) -> dict[str, Any]:
    if not active_observed:
        return {
            "sellThroughRate": None,
            "sellThroughSignal": "unavailable",
            "currentDemandStatus": "sold_evidence_only",
            "currentDemandSignal": f"Sold comps observed ({sold_count} verified sold; active competition unavailable)",
            "provenanceSource": "market_sold_comp",
            "confidence": "medium" if sold_count >= 3 else "low",
            "score": None,
            "explanation": f"Verified {sold_count} sold comps, but active competition is unavailable so sell-through cannot be computed.",
        }

    rate = round(sold_count / (sold_count + max(active_count, 0)), 3)
    velocity = "high" if rate >= 0.6 else ("moderate" if rate >= 0.35 else "slow")
    signal = "strong" if (sold_count >= 5 and rate >= 0.5) else ("moderate" if sold_count >= 2 else "limited")
    return {
        "sellThroughRate": rate,
        "sellThroughSignal": velocity,
        "currentDemandStatus": "supported",
        "currentDemandSignal": signal,
        "provenanceSource": "demand_signal",
        "confidence": "high" if sold_count >= 5 else "medium",
        "score": round(rate * 100, 1),
        "explanation": f"Derived from {sold_count} verified sold comps and {active_count} active listings.",
    }


def _active_only_demand_metrics(active_count: int) -> dict[str, Any]:
    return {
        "sellThroughRate": None,
        "sellThroughSignal": "unavailable",
        "currentDemandStatus": "active_competition_only",
        "currentDemandSignal": f"Active competition observed ({active_count} comparable listings; sold velocity unavailable)",
        "provenanceSource": "market_active_comp",
        "confidence": "low",
        "score": None,
        "explanation": f"{active_count} active listings observed; sold count and sell-through rate are unavailable.",
    }


def _traffic_or_proxy_demand(item: dict[str, Any], listing_ratio: float) -> dict[str, Any]:
    views = float(item.get("views") or 0)
    impressions = float(item.get("impressions") or 0)
    watches = float(item.get("watchCount") or 0)
    if views > 0 or impressions > 0 or watches > 0:
        return {
            "currentDemandStatus": "listing_traffic",
            "currentDemandSignal": "Listing traffic observed",
            "provenanceSource": "demand_signal",
            "score": round(min(100.0, views * 2 + watches * 5 + impressions * 0.2), 1),
            "explanation": "Derived from seller listing traffic metrics.",
        }
    if "quantitySold" in item and float(item.get("quantitySold") or 0) > 0:
        return {
            "currentDemandStatus": "listing_quantity_proxy",
            "currentDemandSignal": "Listing quantity proxy",
            "provenanceSource": "demand_signal",
            "score": round(listing_ratio * 100, 1),
            "explanation": "Proxy based on listing quantity fields; not official eBay-wide sell-through data.",
        }
    return {
        "currentDemandStatus": "insufficient_evidence",
        "currentDemandSignal": "Insufficient demand evidence",
        "provenanceSource": "fallback_estimate",
        "score": 0.0 if "quantitySold" in item else None,
        "explanation": "Insufficient market evidence to establish category sell-through or demand.",
    }


def _resolve_demand_signal(
    sold_count: int, active_count: int, active_observed: bool, item: dict[str, Any], listing_ratio: float
) -> dict[str, Any]:
    if sold_count > 0:
        return _sold_demand_metrics(sold_count, active_count, active_observed)
    if active_count > 0:
        return _active_only_demand_metrics(active_count)
    return _traffic_or_proxy_demand(item, listing_ratio)


def demand_score(
    item: dict[str, Any],
    *,
    sold_summary: dict[str, Any] | None = None,
    active_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compute honest demand and velocity signals without fabricating statistics."""
    raw_sold = (
        sold_summary if sold_summary is not None
        else (item.get("soldComparableSummary") or item.get("soldPricing"))
    )
    raw_active = item.get("activeListingEstimate") if active_summary is None else active_summary
    clean_sold, clean_active = sanitize_market_evidence(
        raw_sold if isinstance(raw_sold, dict) else None,
        raw_active if isinstance(raw_active, dict) else None,
    )
    sold_count = int(clean_sold.get("sampleSize") or 0) if clean_sold.get("status") == "ok" else 0
    active_observed = clean_active.get("status") in {"ok", "no_matches"}
    active_count = int(clean_active.get("sampleSize") or 0) if clean_active.get("status") == "ok" else 0

    qty_sold = max(0.0, float(item.get("quantitySold") or 0))
    quantity = max(1.0, float(item.get("quantity") or 1))
    listing_ratio = min(1.0, qty_sold / (qty_sold + quantity))
    signal_data = _resolve_demand_signal(sold_count, active_count, active_observed, item, listing_ratio)
    has_market_obs = sold_count > 0 or active_count > 0

    return {
        "score": signal_data["score"],
        "confidence": signal_data.get("confidence", "low"),
        "methodology": "market_evidence" if has_market_obs else "listing_quantity_proxy",
        "provenanceSource": signal_data["provenanceSource"],
        "sampleSize": (sold_count + active_count) if has_market_obs else int(qty_sold + quantity),
        "soldCount": sold_count,
        "activeCount": active_count,
        "sellThroughRate": signal_data.get("sellThroughRate"),
        "sellThroughSignal": signal_data.get("sellThroughSignal", "unavailable"),
        "currentDemandStatus": signal_data["currentDemandStatus"],
        "currentDemandSignal": signal_data["currentDemandSignal"],
        "seasonalDemand": _explicit_seasonal_signal(item),
        "dataFreshness": _now(),
        "explanation": signal_data["explanation"],
        "sellThroughProxy": round(listing_ratio, 3),
        "quantitySold": int(qty_sold),
        "quantity": int(quantity),
    }


def _count_finding(recommendations: list[dict[str, Any]], field: str) -> int:
    return sum(
        1 for rec in recommendations if any(f.get("field") == field for f in rec.get("findings", []))
    )


def _needs_cat_confirmation(rec: dict[str, Any]) -> bool:
    tax = rec.get("taxonomy") or {}
    return tax.get("status") == "missing" or bool(tax.get("aspectReviewRequired"))


def seller_recovery_metrics(recommendations: list[dict[str, Any]], listings: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(listings)
    pending = sum(1 for r in recommendations if r.get("status") == "Pending")
    approved = sum(1 for r in recommendations if r.get("status") == "Approved")
    applied = sum(1 for r in recommendations if r.get("status") == "Applied")
    high_risk = sum(1 for r in recommendations if r.get("risk") == "high" and r.get("status") == "Pending")
    taxonomy_unavailable = sum(
        1 for r in recommendations if (r.get("taxonomy") or {}).get("status") in {"unavailable", "not_configured"}
    )
    low_conf = sum(
        1 for r in recommendations
        if r.get("confidence") == "low" or (r.get("soldPricing") or {}).get("pricingConfidence") == "low"
    )
    evidence_gaps = sum(
        1 for r in recommendations
        if any(f.get("field") in {"item_specifics", "taxonomy", "price"} for f in r.get("findings", []))
    )
    return {
        "totalActiveListingsImported": total,
        "listingsNeedingReview": pending,
        "highRiskPendingReviews": high_risk,
        "missingSpecificsOpportunities": _count_finding(recommendations, "item_specifics"),
        "titleOptimizationOpportunities": _count_finding(recommendations, "title"),
        "pricingAdjustmentOpportunities": _count_finding(recommendations, "price"),
        "approvedChanges": approved,
        "appliedChanges": applied,
        "catalogReviewCoverage": round((len(recommendations) / total) * 100, 1) if total else 0.0,
        "lowConfidenceRecommendations": low_conf,
        "categoryValidationFailures": taxonomy_unavailable,
        "categoryConfirmationNeeded": sum(1 for r in recommendations if _needs_cat_confirmation(r)),
        "taxonomyValidationUnavailable": taxonomy_unavailable,
        "unresolvedEvidenceGaps": evidence_gaps,
        "label": "Operational catalog metrics only",
        "note": "These are operational recovery indicators only; official eBay Seller Hub performance metrics are not connected.",
    }


def _sold_records_from_csv() -> list[dict[str, Any]]:
    path = os.environ.get("SOLD_COMPS_CSV") or os.environ.get("SOLD_COMPS_CSV_PATH") or _default_sold_comps_csv()
    if not path:
        return []
    try:
        with open(path, newline="", encoding="utf-8-sig") as handle:
            return list(csv.DictReader(handle))
    except OSError:
        return []


def _usable_sold_record(item: dict[str, Any], record: dict[str, Any]) -> bool:
    category = str(item.get("cat") or "").strip()
    record_category = str(record.get("categoryId") or record.get("cat") or "").strip()
    if category and record_category and category != record_category:
        return False
    condition = " ".join(str(record.get(k) or "") for k in ("condition", "conditionId", "title")).lower()
    if any(marker in condition for marker in ("parts only", "for parts", "damaged", "broken")):
        return False
    return str(record.get("listingState") or record.get("state") or "sold").lower() not in {"active", "unsold"}


def _record_matches_field(expected: str, actual: str, title: str) -> bool:
    if not expected or expected == "not visible":
        return True
    if actual:
        return expected == actual
    return expected in title


def _exact_match(item: dict[str, Any], record: dict[str, Any]) -> bool:
    brand = str(item.get("brand") or "").strip().casefold()
    model = str(item.get("model") or "").strip().casefold()
    rec_brand = str(record.get("brand") or "").strip().casefold()
    rec_model = str(record.get("model") or "").strip().casefold()
    title = str(record.get("title") or record.get("itemTitle") or "").casefold()
    if not _record_matches_field(brand, rec_brand, title):
        return False
    if not _record_matches_field(model, rec_model, title):
        return False
    return bool(brand or model)


def _record_price(record: dict[str, Any]) -> float:
    for key in ("soldPrice", "sold_price", "salePrice", "Sale Price", "totalPrice", "price", "value"):
        val = record.get(key)
        if val:
            return _money(val.get("value") if isinstance(val, dict) else val)
    return 0.0


def _existing_sold_summary(item: dict[str, Any]) -> dict[str, Any]:
    for key in ("soldComparableSummary", "soldPricing"):
        value = item.get(key)
        if isinstance(value, dict) and value.get("status") == "ok" and _money(value.get("medianSoldPrice")) > 0:
            result = dict(value)
            result.setdefault("kind", "sold_comps")
            result.setdefault("pricingSource", result.get("pricingSource") or "similar_used_sold")
            result.setdefault("query", result.get("query") or "")
            return result
    return {}


def _default_sold_comps_csv() -> str:
    for path in ("sold_comps.csv", "sold-comps.csv", os.path.join("data", "sold_comps.csv")):
        if os.path.exists(path):
            return path
    return ""
