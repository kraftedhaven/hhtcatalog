from __future__ import annotations
<<<<<<< HEAD
import csv
=======
from datetime import datetime, timezone
>>>>>>> eeed06fa7cec048409069a56ede856d0961027fa
import os
import statistics
from datetime import datetime, timezone
from typing import Any

import requests

<<<<<<< HEAD
PRICING_SOURCES = {
    "exact_used_sold",
    "similar_used_sold",
    "active_comparable",
    "ai_estimate",
    "seller_price_fallback",
}


def sold_price_summary(item: dict[str, Any]) -> dict[str, Any]:
    """Use explicitly configured sold-comps data; never label active data as sold."""
    existing = _existing_sold_summary(item)
    if existing:
        return existing
    records = _sold_records_from_csv()
    source = "seller_uploaded_csv" if records else ""
    configured = os.environ.get("SOLD_COMPS_API_URL", "").strip()
    query = " ".join(str(item.get(key) or "").strip() for key in ("brand", "model", "type", "style", "size") if str(item.get(key) or "").strip())
    if not records and not configured:
        return {"status": "not_configured", "kind": "sold_comps", "sampleSize": 0, "query": query, "message": "Sold-comparable provider is not configured."}
    if not records:
        headers = {"Accept": "application/json"}
        token = os.environ.get("SOLD_COMPS_API_TOKEN", "").strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        try:
            response = requests.get(
                configured,
                params={"query": query, "categoryId": str(item.get("cat") or "")},
                headers=headers,
                timeout=float(os.environ.get("SOLD_COMPS_TIMEOUT_SECONDS", "5")),
            )
            if response.status_code >= 400:
                return {"status": "unavailable", "kind": "sold_comps", "sampleSize": 0, "query": query, "message": "Sold-comparable provider was unavailable."}
            body = response.json()
        except (requests.RequestException, ValueError, TypeError):
            return {"status": "unavailable", "kind": "sold_comps", "sampleSize": 0, "query": query, "message": "Sold-comparable provider was unavailable."}
        records = body.get("items", body.get("results", [])) if isinstance(body, dict) else body
        source = "marketplace_insights_or_external_api"

    if not isinstance(records, list):
        records = []
    exact: list[float] = []
    similar: list[float] = []
    for record in records[:100]:
        if not isinstance(record, dict):
            continue
        if not _usable_sold_record(item, record):
            continue
        price = _record_price(record)
        if price > 0:
            if _exact_match(item, record):
                exact.append(price)
            else:
                similar.append(price)
    prices = exact or similar
    if not prices:
        return {"status": "no_matches", "kind": "sold_comps", "sampleSize": 0, "query": query, "message": "No usable sold comparables were returned."}
    source_type = "exact_used_sold" if exact else "similar_used_sold"
    return {
        "status": "ok", "kind": "sold_comps", "source": source, "pricingSource": source_type, "sampleSize": len(prices), "query": query,
        "lowSoldPrice": round(min(prices), 2), "medianSoldPrice": round(statistics.median(prices), 2), "highSoldPrice": round(max(prices), 2),
        "message": "Sold-comparable summary is advisory; no price change is applied automatically.",
    }


def pricing_recommendation(
    item: dict[str, Any],
    *,
    sold_summary: dict[str, Any] | None = None,
    active_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    sold_summary = sold_summary if sold_summary is not None else sold_price_summary(item)
    current = _money(item.get("currentPrice", item.get("price")))
    query = str((sold_summary or {}).get("query") or item.get("pricingSearchKeywords") or "").strip()
    if sold_summary.get("status") == "ok" and _money(sold_summary.get("medianSoldPrice")) > 0:
        source = sold_summary.get("pricingSource") if sold_summary.get("pricingSource") in PRICING_SOURCES else "similar_used_sold"
        return _pricing_result(
            source,
            _money(sold_summary.get("medianSoldPrice")),
            current,
            "high" if source == "exact_used_sold" and int(sold_summary.get("sampleSize") or 0) >= 3 else "medium",
            int(sold_summary.get("sampleSize") or 0),
            _money(sold_summary.get("lowSoldPrice")),
            _money(sold_summary.get("highSoldPrice")),
            "Price is based on actual used sold-comparable records.",
            query,
        )
    if active_summary and _money(active_summary.get("medianActivePrice")) > 0:
        return _pricing_result(
            "active_comparable",
            _money(active_summary.get("medianActivePrice")),
            current,
            "medium" if int(active_summary.get("sampleSize") or 0) >= 3 else "low",
            int(active_summary.get("sampleSize") or 0),
            _money(active_summary.get("lowActivePrice")),
            _money(active_summary.get("highActivePrice")),
            "Sold data was unavailable; active eBay comparable median is advisory and not sold-based.",
            str(active_summary.get("keyword") or query),
        )
    ai_price = _money(item.get("aiEstimatedPrice") or item.get("modelEstimatedPrice") or item.get("visionEstimatedPrice"))
    if ai_price > 0:
        return _pricing_result("ai_estimate", ai_price, current, "low", 0, ai_price, ai_price, "Sold and active comparable data were unavailable; AI/model estimate used.", query)
    if current > 0:
        return _pricing_result("seller_price_fallback", current, current, "low", 0, current, current, "No comparable or model estimate was available; existing seller price kept.", query)
    return {
        "status": "error",
        "error": "numeric_price_required",
        "message": "No numeric sold, active, AI, or seller price is available; seller entry is required.",
        "recommendedPrice": None,
        "pricingSource": "",
        "pricingConfidence": "low",
        "sampleSize": 0,
        "lowPrice": None,
        "highPrice": None,
        "currentPrice": current,
        "recommendedChangePct": None,
        "adjustmentReason": "Seller must enter a numeric price.",
        "dataFreshness": _now(),
        "comparableQuery": query,
    }
=======

def _number(value: Any) -> float:
    try:
        value = float(value)
        return round(value, 2) if value > 0 else 0.0
    except (TypeError, ValueError):
        return 0.0


def _range_from_summary(summary: Any) -> tuple[float, float, float]:
    if not isinstance(summary, dict):
        return 0.0, 0.0, 0.0
    recommended = _number(summary.get("medianActivePrice") or summary.get("medianPrice") or summary.get("recommendedPrice") or summary.get("medianSoldPrice"))
    low = _number(summary.get("lowActivePrice") or summary.get("lowPrice") or summary.get("lowSoldPrice"))
    high = _number(summary.get("highActivePrice") or summary.get("highPrice") or summary.get("highSoldPrice"))
    return recommended, low or recommended, high or recommended


def _query(item: dict[str, Any]) -> str:
    parts = []
    for key in ("brand", "model", "type", "style", "mat", "color"):
        value = str(item.get(key) or "").strip()
        if value and value.lower() not in {"not visible", "unknown", "n/a", "n/a - bag", "n/a - footwear"} and value.lower() not in {p.lower() for p in parts}:
            parts.append(value)
    return " ".join(parts) or str(item.get("title") or "").strip()


def _result(price: float, source: str, confidence: str, sample: int, low: float, high: float, current: float, now: str, item: dict[str, Any], reason: str) -> dict[str, Any]:
    change = round(((price - current) / current) * 100, 1) if current > 0 else 0.0
    return {"status": "ok", "kind": "pricing", "recommendedPrice": round(price, 2), "pricingSource": source, "pricingConfidence": confidence, "sampleSize": sample, "lowPrice": round(low, 2), "highPrice": round(high, 2), "currentPrice": round(current, 2), "recommendedChangePct": change, "adjustmentReason": reason, "dataFreshness": now, "comparableQuery": _query(item), "message": reason}


def sold_price_summary(item: dict[str, Any]) -> dict[str, Any]:
    """Always return a numeric recommendation when a fallback price exists.

    Sold data is labeled sold only when actual records are returned by the configured
    provider. Active comparables, AI estimates, and seller prices have distinct labels.
    """
    now = datetime.now(timezone.utc).isoformat()
    current = _number(item.get("price"))
    configured = os.environ.get("SOLD_COMPS_API_URL", "").strip()
    query = _query(item)

    sold_summary = item.get("soldComparableSummary") or item.get("soldPricing")
    if isinstance(sold_summary, dict):
        median, low, high = _range_from_summary(sold_summary)
        if median > 0 and str(sold_summary.get("status", "")).lower() not in {"not_configured", "unavailable", "not_implemented"}:
            sample = int(sold_summary.get("sampleSize") or 0)
            source = "exact_used_sold" if str(sold_summary.get("matchType", "")).lower() == "exact" else "similar_used_sold"
            return _result(median, source, "high" if source == "exact_used_sold" and sample >= 5 else "medium", sample, low, high, current, now, item, "Used-sold comparable median.")

    if configured:
        headers = {"Accept": "application/json"}
        token = os.environ.get("SOLD_COMPS_API_TOKEN", "").strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        try:
            response = requests.get(configured, params={"query": query, "categoryId": str(item.get("cat") or "")}, headers=headers, timeout=float(os.environ.get("SOLD_COMPS_TIMEOUT_SECONDS", "5")))
            body = response.json() if response.status_code < 400 else {}
            records = body.get("items", body.get("results", [])) if isinstance(body, dict) else body
            prices = []
            for record in records[:100] if isinstance(records, list) else []:
                if not isinstance(record, dict):
                    continue
                value = record.get("soldPrice", record.get("price", record.get("value")))
                if isinstance(value, dict):
                    value = value.get("value")
                price = _number(value)
                if price > 0:
                    prices.append(price)
            if prices:
                median = round(statistics.median(prices), 2)
                result = _result(median, "similar_used_sold", "medium" if len(prices) >= 5 else "low", len(prices), min(prices), max(prices), current, now, item, "Used-sold comparable median from the configured provider.")
                result.update({"kind": "sold_comps", "query": query, "lowSoldPrice": min(prices), "medianSoldPrice": median, "highSoldPrice": max(prices), "message": "Sold-comparable summary is advisory; no price change is proposed automatically."})
                return result
        except (requests.RequestException, ValueError, TypeError):
            pass

    active = item.get("activeListingEstimate")
    median, low, high = _range_from_summary(active)
    if median > 0:
        sample = int((active or {}).get("sampleSize") or 0) if isinstance(active, dict) else 0
        return _result(median, "active_comparable", "medium" if sample >= 5 else "low", sample, low, high, current, now, item, "Active eBay comparable median; this is not sold-data pricing.")

    ai_estimate = _number(item.get("aiEstimatedPrice"))
    if ai_estimate > 0:
        return _result(ai_estimate, "ai_estimate", "low", 0, ai_estimate, ai_estimate, current, now, item, "AI estimate; sold comparable data was not available.")

    if current > 0:
        return _result(current, "seller_price_fallback", "low", 0, current, current, current, now, item, "Existing seller price retained because no sold, active-comparable, or AI price was available.")

    return {"status": "no_numeric_price", "kind": "pricing", "recommendedPrice": 0.0, "pricingSource": "unavailable", "pricingConfidence": "low", "sampleSize": 0, "lowPrice": 0.0, "highPrice": 0.0, "currentPrice": 0.0, "recommendedChangePct": 0.0, "adjustmentReason": "Seller must enter a price; no numeric evidence or fallback price was available.", "dataFreshness": now, "comparableQuery": query, "message": "No numeric price available; seller entry required."}

>>>>>>> eeed06fa7cec048409069a56ede856d0961027fa

def demand_score(item: dict[str, Any]) -> dict[str, Any]:
    sold = float(item.get("quantitySold") or 0)
    quantity = max(1.0, float(item.get("quantity") or 1))
    ratio = min(1.0, sold / (sold + quantity))
    score = round(ratio * 100, 1)
<<<<<<< HEAD
    sample_size = int(sold + quantity)
    return {
        "score": score,
        "confidence": "low",
        "methodology": "listing_quantity_proxy",
        "sampleSize": sample_size,
        "dataFreshness": _now(),
        "explanation": "Proxy based on listing quantity fields; not official eBay-wide sell-through data.",
        "sellThroughProxy": round(ratio, 3),
        "quantitySold": int(sold),
        "quantity": int(quantity),
    }
=======
    return {"score": score, "sellThroughProxy": round(ratio, 3), "quantitySold": int(sold), "quantity": int(quantity), "confidence": "low", "methodology": "listing_quantity_proxy", "message": "Proxy based on listing quantity fields; not a marketplace-wide sell-through rate."}

>>>>>>> eeed06fa7cec048409069a56ede856d0961027fa

def seller_recovery_metrics(recommendations: list[dict[str, Any]], listings: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(listings)
    pending = sum(1 for r in recommendations if r.get("status") == "Pending")
    approved = sum(1 for r in recommendations if r.get("status") == "Approved")
    applied = sum(1 for r in recommendations if r.get("status") == "Applied")
    high_risk = sum(1 for r in recommendations if r.get("risk") == "high" and r.get("status") == "Pending")
    missing_specifics = sum(1 for r in recommendations if any(f.get("field") == "item_specifics" for f in r.get("findings", [])))
    title_opportunities = sum(1 for r in recommendations if any(f.get("field") == "title" for f in r.get("findings", [])))
    pricing_opportunities = sum(1 for r in recommendations if any(f.get("field") == "price" for f in r.get("findings", [])))
    taxonomy_failures = sum(1 for r in recommendations if (r.get("taxonomy") or {}).get("status") in {"missing", "unavailable"} or (r.get("taxonomy") or {}).get("aspectReviewRequired"))
    low_confidence = sum(1 for r in recommendations if r.get("confidence") == "low" or (r.get("soldPricing") or {}).get("pricingConfidence") == "low")
    evidence_gaps = sum(1 for r in recommendations if any(f.get("field") in {"item_specifics", "taxonomy", "price"} for f in r.get("findings", [])))
    coverage = round((len(recommendations) / total) * 100, 1) if total else 0.0
    return {
        "totalActiveListingsImported": total,
        "listingsNeedingReview": pending,
        "highRiskPendingReviews": high_risk,
        "listingsWithMissingSpecifics": missing_specifics,
        "titleOpportunities": title_opportunities,
        "pricingOpportunities": pricing_opportunities,
        "approvedRecommendations": approved,
        "appliedChanges": applied,
        "catalogReviewCoverage": coverage,
        "lowConfidenceRecommendations": low_confidence,
        "categoryValidationFailures": taxonomy_failures,
        "unresolvedEvidenceGaps": evidence_gaps,
        "label": "Operational catalog metrics only",
        "note": "These are operational recovery indicators only; official eBay Seller Hub performance metrics are not connected.",
    }


def _pricing_result(source: str, recommended: float, current: float, confidence: str, sample_size: int, low: float, high: float, reason: str, query: str) -> dict[str, Any]:
    change = round(((recommended - current) / current) * 100, 1) if current > 0 else None
    return {
        "status": "ok",
        "recommendedPrice": round(recommended, 2),
        "pricingSource": source,
        "pricingConfidence": confidence,
        "sampleSize": sample_size,
        "lowPrice": round(low or recommended, 2),
        "highPrice": round(high or recommended, 2),
        "currentPrice": round(current, 2),
        "recommendedChangePct": change,
        "adjustmentReason": reason,
        "dataFreshness": _now(),
        "comparableQuery": query,
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
    condition = " ".join(str(record.get(key) or "") for key in ("condition", "conditionId", "title")).lower()
    if any(marker in condition for marker in ("parts only", "for parts", "damaged", "broken")):
        return False
    if str(record.get("listingState") or record.get("state") or "sold").lower() in {"active", "unsold"}:
        return False
    return True


def _exact_match(item: dict[str, Any], record: dict[str, Any]) -> bool:
    brand = str(item.get("brand") or "").strip().casefold()
    model = str(item.get("model") or "").strip().casefold()
    record_brand = str(record.get("brand") or "").strip().casefold()
    record_model = str(record.get("model") or "").strip().casefold()
    title = str(record.get("title") or record.get("itemTitle") or "").casefold()
    if not record_brand and brand and brand != "not visible" and brand in title:
        record_brand = brand
    if not record_model and model and model != "not visible" and model in title:
        record_model = model
    if brand and brand != "not visible" and brand != record_brand:
        return False
    if model and model != "not visible" and model != record_model:
        return False
    return bool(brand or model)


def _record_price(record: dict[str, Any]) -> float:
    value = (
        record.get("soldPrice")
        or record.get("sold_price")
        or record.get("salePrice")
        or record.get("Sale Price")
        or record.get("totalPrice")
        or record.get("price")
        or record.get("value")
    )
    if isinstance(value, dict):
        value = value.get("value")
    return _money(value)


def _money(value: Any) -> float:
    try:
        parsed = float(value or 0)
    except (TypeError, ValueError):
        return 0.0
    return round(parsed, 2) if parsed > 0 else 0.0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


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
