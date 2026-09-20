from __future__ import annotations
from datetime import datetime, timezone
import os
import statistics
from typing import Any

import requests


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
                return _result(median, "similar_used_sold", "medium" if len(prices) >= 5 else "low", len(prices), min(prices), max(prices), current, now, item, "Used-sold comparable median from the configured provider.")
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


def demand_score(item: dict[str, Any]) -> dict[str, Any]:
    sold = float(item.get("quantitySold") or 0)
    quantity = max(1.0, float(item.get("quantity") or 1))
    ratio = min(1.0, sold / (sold + quantity))
    score = round(ratio * 100, 1)
    return {"score": score, "sellThroughProxy": round(ratio, 3), "quantitySold": int(sold), "quantity": int(quantity), "confidence": "low", "methodology": "listing_quantity_proxy", "message": "Proxy based on listing quantity fields; not a marketplace-wide sell-through rate."}


def seller_recovery_metrics(recommendations: list[dict[str, Any]], listings: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(listings)
    pending = sum(1 for r in recommendations if r.get("status") == "Pending")
    applied = sum(1 for r in recommendations if r.get("status") == "Applied")
    high_risk = sum(1 for r in recommendations if r.get("risk") == "high" and r.get("status") == "Pending")
    return {"catalogListings": total, "pendingReviews": pending, "appliedChanges": applied, "highRiskPending": high_risk, "coverage": round((applied / total) * 100, 1) if total else 0.0, "note": "Operational recovery indicators only; eBay seller-performance metrics require Seller Hub data or an approved API source."}
