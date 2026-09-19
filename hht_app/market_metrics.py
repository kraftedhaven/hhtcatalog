from __future__ import annotations
import os, statistics
from typing import Any

def sold_price_summary(item: dict[str, Any]) -> dict[str, Any]:
    """Use an explicitly configured sold-comps endpoint; never label active data as sold."""
    configured = os.environ.get("SOLD_COMPS_API_URL", "").strip()
    if not configured:
        return {"status": "not_configured", "kind": "sold_comps", "sampleSize": 0, "message": "Sold-comparable provider is not configured; no price change is proposed."}
    return {"status": "not_implemented", "kind": "sold_comps", "sampleSize": 0, "message": "Sold-comparable adapter is reserved for the configured provider; no price change is proposed."}

def demand_score(item: dict[str, Any]) -> dict[str, Any]:
    sold = float(item.get("quantitySold") or 0)
    quantity = max(1.0, float(item.get("quantity") or 1))
    ratio = min(1.0, sold / quantity)
    score = round(ratio * 100, 1)
    return {"score": score, "sellThroughProxy": round(ratio, 3), "quantitySold": int(sold), "quantity": int(quantity), "confidence": "low", "message": "Proxy based on listing quantity fields; not a marketplace-wide sell-through rate."}

def seller_recovery_metrics(recommendations: list[dict[str, Any]], listings: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(listings)
    pending = sum(1 for r in recommendations if r.get("status") == "Pending")
    applied = sum(1 for r in recommendations if r.get("status") == "Applied")
    high_risk = sum(1 for r in recommendations if r.get("risk") == "high" and r.get("status") == "Pending")
    return {"catalogListings": total, "pendingReviews": pending, "appliedChanges": applied, "highRiskPending": high_risk, "coverage": round((applied / total) * 100, 1) if total else 0.0, "note": "Operational recovery indicators only; eBay seller-performance metrics require Seller Hub data or an approved API source."}
