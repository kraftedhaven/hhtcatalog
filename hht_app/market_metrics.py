from __future__ import annotations
import os
import statistics
from typing import Any

import requests

def sold_price_summary(item: dict[str, Any]) -> dict[str, Any]:
    """Use an explicitly configured sold-comps endpoint; never label active data as sold."""
    configured = os.environ.get("SOLD_COMPS_API_URL", "").strip()
    if not configured:
        return {"status": "not_configured", "kind": "sold_comps", "sampleSize": 0, "message": "Sold-comparable provider is not configured; no price change is proposed."}
    query = " ".join(str(item.get(key) or "").strip() for key in ("brand", "model", "type", "style", "size") if str(item.get(key) or "").strip())
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
            return {"status": "unavailable", "kind": "sold_comps", "sampleSize": 0, "message": "Sold-comparable provider was unavailable; no price change is proposed."}
        body = response.json()
    except (requests.RequestException, ValueError, TypeError):
        return {"status": "unavailable", "kind": "sold_comps", "sampleSize": 0, "message": "Sold-comparable provider was unavailable; no price change is proposed."}

    records = body.get("items", body.get("results", [])) if isinstance(body, dict) else body
    if not isinstance(records, list):
        records = []
    prices: list[float] = []
    for record in records[:100]:
        if not isinstance(record, dict):
            continue
        value = record.get("soldPrice", record.get("price", record.get("value")))
        if isinstance(value, dict):
            value = value.get("value")
        try:
            price = float(value)
        except (TypeError, ValueError):
            continue
        if price > 0:
            prices.append(price)
    if not prices:
        return {"status": "no_matches", "kind": "sold_comps", "sampleSize": 0, "query": query, "message": "No usable sold comparables were returned; no price change is proposed."}
    return {
        "status": "ok", "kind": "sold_comps", "sampleSize": len(prices), "query": query,
        "lowSoldPrice": round(min(prices), 2), "medianSoldPrice": round(statistics.median(prices), 2), "highSoldPrice": round(max(prices), 2),
        "message": "Sold-comparable summary is advisory; no price change is proposed automatically.",
    }

def demand_score(item: dict[str, Any]) -> dict[str, Any]:
    sold = float(item.get("quantitySold") or 0)
    quantity = max(1.0, float(item.get("quantity") or 1))
    ratio = min(1.0, sold / (sold + quantity))
    score = round(ratio * 100, 1)
    return {"score": score, "sellThroughProxy": round(ratio, 3), "quantitySold": int(sold), "quantity": int(quantity), "confidence": "low", "message": "Proxy based on listing quantity fields; not a marketplace-wide sell-through rate."}

def seller_recovery_metrics(recommendations: list[dict[str, Any]], listings: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(listings)
    pending = sum(1 for r in recommendations if r.get("status") == "Pending")
    applied = sum(1 for r in recommendations if r.get("status") == "Applied")
    high_risk = sum(1 for r in recommendations if r.get("risk") == "high" and r.get("status") == "Pending")
    return {"catalogListings": total, "pendingReviews": pending, "appliedChanges": applied, "highRiskPending": high_risk, "coverage": round((applied / total) * 100, 1) if total else 0.0, "note": "Operational recovery indicators only; eBay seller-performance metrics require Seller Hub data or an approved API source."}
