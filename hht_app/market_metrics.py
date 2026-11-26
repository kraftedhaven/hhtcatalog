from __future__ import annotations

import csv
import os
import statistics
from datetime import datetime, timezone
from typing import Any

import requests

PRICING_SOURCES = {
    "exact_used_sold",
    "similar_used_sold",
    "active_comparable",
    "ai_estimate",
    "seller_price_fallback",
    "fallback_estimate",
}

INSUFFICIENT_MARKET_MESSAGE = "Insufficient market evidence — seller confirmation required."

_DEFAULT_NON_EVIDENCE_SEASONS = {"", "all seasons", "not visible", "n/a", "unknown"}


def sanitize_market_evidence(
    sold_summary: dict[str, Any] | None = None,
    active_summary: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate market comp summaries and reject fabricated or malformed statistics."""
    clean_sold: dict[str, Any] = {
        "status": "not_configured",
        "kind": "sold_comps",
        "sampleSize": 0,
        "query": "",
    }
    if isinstance(sold_summary, dict):
        query = str(sold_summary.get("query") or "").strip()
        raw_status = str(sold_summary.get("status") or "").strip()
        try:
            sample_size = int(sold_summary.get("sampleSize") or 0)
        except (TypeError, ValueError):
            sample_size = -1
        median_sold = _money(sold_summary.get("medianSoldPrice"))
        low_sold = _money(sold_summary.get("lowSoldPrice")) or median_sold
        high_sold = _money(sold_summary.get("highSoldPrice")) or median_sold
        if raw_status == "ok" and sample_size > 0 and median_sold > 0:
            if low_sold > median_sold:
                low_sold = median_sold
            if high_sold < median_sold:
                high_sold = median_sold
            clean_sold = {
                "status": "ok",
                "kind": "sold_comps",
                "source": str(sold_summary.get("source") or "verified_sold_comps"),
                "pricingSource": sold_summary.get("pricingSource")
                if sold_summary.get("pricingSource") in {"exact_used_sold", "similar_used_sold"}
                else "similar_used_sold",
                "sampleSize": sample_size,
                "query": query,
                "lowSoldPrice": low_sold,
                "medianSoldPrice": median_sold,
                "highSoldPrice": high_sold,
                "p25SoldPrice": _money(sold_summary.get("p25SoldPrice")),
                "p75SoldPrice": _money(sold_summary.get("p75SoldPrice")),
                "dataFreshness": str(sold_summary.get("dataFreshness") or _now()),
                "message": str(
                    sold_summary.get("message")
                    or "Sold-comparable summary is advisory; no price change is applied automatically."
                ),
            }
        else:
            rejected_reason = "rejected_fabricated_or_invalid_stats" if (
                sample_size < 0
                or (raw_status == "ok" and (sample_size <= 0 or median_sold <= 0))
            ) else ""
            clean_sold = {
                "status": "invalid_evidence" if rejected_reason else (raw_status or "not_configured"),
                "kind": "sold_comps",
                "sampleSize": 0,
                "query": query,
                "message": str(sold_summary.get("message") or INSUFFICIENT_MARKET_MESSAGE),
            }
            if rejected_reason:
                clean_sold["validationFlag"] = rejected_reason

    clean_active: dict[str, Any] = {
        "provider": "ebay_browse",
        "kind": "active_comparable",
        "status": "no_matches",
        "keyword": "",
        "sampleSize": 0,
        "medianActivePrice": 0.0,
        "lowActivePrice": 0.0,
        "highActivePrice": 0.0,
    }
    if isinstance(active_summary, dict):
        keyword = str(active_summary.get("keyword") or active_summary.get("query") or "").strip()
        try:
            sample_size = int(active_summary.get("sampleSize") or 0)
        except (TypeError, ValueError):
            sample_size = -1
        median_active = _money(active_summary.get("medianActivePrice"))
        low_active = _money(active_summary.get("lowActivePrice")) or median_active
        high_active = _money(active_summary.get("highActivePrice")) or median_active
        if sample_size > 0 and median_active > 0:
            if low_active > median_active:
                low_active = median_active
            if high_active < median_active:
                high_active = median_active
            clean_active = {
                "provider": str(active_summary.get("provider") or "ebay_browse"),
                "kind": "active_comparable",
                "status": "ok",
                "keyword": keyword,
                "sampleSize": sample_size,
                "medianActivePrice": median_active,
                "lowActivePrice": low_active,
                "highActivePrice": high_active,
                "p25ActivePrice": _money(active_summary.get("p25ActivePrice")),
                "p75ActivePrice": _money(active_summary.get("p75ActivePrice")),
                "dataFreshness": str(active_summary.get("dataFreshness") or _now()),
            }
        else:
            rejected_reason = "rejected_fabricated_or_invalid_stats" if (
                sample_size < 0 or (sample_size > 0 and median_active <= 0)
            ) else ""
            clean_active["keyword"] = keyword
            if rejected_reason:
                clean_active["status"] = "invalid_evidence"
                clean_active["validationFlag"] = rejected_reason

    return clean_sold, clean_active


def sold_price_summary(item: dict[str, Any]) -> dict[str, Any]:
    """Return verified sold-comparable data only; never relabel active listings as sold."""
    existing = _existing_sold_summary(item)
    if existing:
        return existing

    records = _sold_records_from_csv()
    source = "seller_uploaded_csv" if records else ""
    configured = os.environ.get("SOLD_COMPS_API_URL", "").strip()
    query = " ".join(
        str(item.get(key) or "").strip()
        for key in ("brand", "model", "type", "style", "size")
        if str(item.get(key) or "").strip()
    )
    if not records and not configured:
        return {
            "status": "not_configured",
            "kind": "sold_comps",
            "sampleSize": 0,
            "query": query,
            "message": "Sold-comparable provider is not configured.",
        }

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
                return {
                    "status": "unavailable",
                    "kind": "sold_comps",
                    "sampleSize": 0,
                    "query": query,
                    "message": "Sold-comparable provider was unavailable.",
                }
            body = response.json()
        except (requests.RequestException, ValueError, TypeError):
            return {
                "status": "unavailable",
                "kind": "sold_comps",
                "sampleSize": 0,
                "query": query,
                "message": "Sold-comparable provider was unavailable.",
            }
        records = body.get("items", body.get("results", [])) if isinstance(body, dict) else body
        source = "marketplace_insights_or_external_api"

    if not isinstance(records, list):
        records = []
    exact: list[float] = []
    similar: list[float] = []
    for record in records[:100]:
        if not isinstance(record, dict) or not _usable_sold_record(item, record):
            continue
        price = _record_price(record)
        if price <= 0:
            continue
        if _exact_match(item, record):
            exact.append(price)
        else:
            similar.append(price)

    prices = exact or similar
    if not prices:
        return {
            "status": "no_matches",
            "kind": "sold_comps",
            "sampleSize": 0,
            "query": query,
            "message": "No usable sold comparables were returned.",
        }

    prices.sort()
    count = len(prices)
    p25 = round(float(prices[max(0, int((count - 1) * 0.25))]), 2)
    p75 = round(float(prices[min(count - 1, int((count - 1) * 0.75))]), 2)
    source_type = "exact_used_sold" if exact else "similar_used_sold"
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


def pricing_recommendation(
    item: dict[str, Any],
    *,
    sold_summary: dict[str, Any] | None = None,
    active_summary: dict[str, Any] | None = None,
    demand: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a market-backed or clearly labeled fallback pricing recommendation.

    Never fabricates market comps or relabels an AI visual guess as market evidence.
    """
    raw_sold = sold_summary if sold_summary is not None else sold_price_summary(item)
    raw_active = active_summary if active_summary is not None else (
        item.get("activeListingEstimate") if isinstance(item.get("activeListingEstimate"), dict) else None
    )
    clean_sold, clean_active = sanitize_market_evidence(raw_sold, raw_active)
    current = _money(item.get("currentPrice", item.get("price")))
    ai_price = _money(item.get("aiEstimatedPrice") or item.get("modelEstimatedPrice") or item.get("visionEstimatedPrice"))
    query = str(
        clean_sold.get("query")
        or clean_active.get("keyword")
        or item.get("pricingSearchKeywords")
        or ""
    ).strip()

    demand_data = demand if isinstance(demand, dict) else demand_score(item, sold_summary=clean_sold, active_summary=clean_active)

    if clean_sold.get("status") == "ok" and _money(clean_sold.get("medianSoldPrice")) > 0:
        source = clean_sold.get("pricingSource") if clean_sold.get("pricingSource") in PRICING_SOURCES else "similar_used_sold"
        sample_size = int(clean_sold.get("sampleSize") or 0)
        return _pricing_result(
            source,
            _money(clean_sold.get("medianSoldPrice")),
            current,
            "high" if source == "exact_used_sold" and sample_size >= 3 else "medium",
            sample_size,
            _money(clean_sold.get("lowSoldPrice")),
            _money(clean_sold.get("highSoldPrice")),
            "Price is based on verified used sold-comparable records.",
            query,
            ai_price=ai_price,
            sold_summary=clean_sold,
            active_summary=clean_active,
            demand=demand_data,
            p25=_money(clean_sold.get("p25SoldPrice")),
            p75=_money(clean_sold.get("p75SoldPrice")),
            insufficient=False,
        )

    if clean_active.get("status") == "ok" and _money(clean_active.get("medianActivePrice")) > 0:
        sample_size = int(clean_active.get("sampleSize") or 0)
        return _pricing_result(
            "active_comparable",
            _money(clean_active.get("medianActivePrice")),
            current,
            "medium" if sample_size >= 3 else "low",
            sample_size,
            _money(clean_active.get("lowActivePrice")),
            _money(clean_active.get("highActivePrice")),
            f"Sold comps unavailable; based on {sample_size} active eBay comparable listings (advisory ask prices).",
            str(clean_active.get("keyword") or query),
            ai_price=ai_price,
            sold_summary=clean_sold,
            active_summary=clean_active,
            demand=demand_data,
            p25=_money(clean_active.get("p25ActivePrice")),
            p75=_money(clean_active.get("p75ActivePrice")),
            insufficient=False,
        )

    if ai_price > 0:
        return _pricing_result(
            "ai_estimate",
            ai_price,
            current,
            "low",
            0,
            ai_price,
            ai_price,
            INSUFFICIENT_MARKET_MESSAGE,
            query,
            ai_price=ai_price,
            sold_summary=clean_sold,
            active_summary=clean_active,
            demand=demand_data,
            insufficient=True,
        )

    if current > 0:
        return _pricing_result(
            "seller_price_fallback",
            current,
            current,
            "low",
            0,
            current,
            current,
            f"{INSUFFICIENT_MARKET_MESSAGE} Existing seller price retained.",
            query,
            ai_price=ai_price,
            sold_summary=clean_sold,
            active_summary=clean_active,
            demand=demand_data,
            insufficient=True,
        )

    return {
        "status": "error",
        "error": "numeric_price_required",
        "message": INSUFFICIENT_MARKET_MESSAGE,
        "marketMessage": INSUFFICIENT_MARKET_MESSAGE,
        "marketStatus": "insufficient_market_evidence",
        "insufficientMarketEvidence": True,
        "requiresSellerConfirmation": True,
        "isFallback": True,
        "recommendedPrice": None,
        "marketRecommendedPrice": None,
        "fastSalePrice": None,
        "quickSalePrice": None,
        "balancedPrice": None,
        "targetPrice": None,
        "premiumPrice": None,
        "aiEstimatedPrice": None,
        "pricingSource": "",
        "provenanceSource": "fallback_estimate",
        "pricingStrategy": "seller_input_required",
        "pricingConfidence": "low",
        "sampleSize": 0,
        "lowPrice": None,
        "highPrice": None,
        "currentPrice": current,
        "recommendedChangePct": None,
        "adjustmentReason": INSUFFICIENT_MARKET_MESSAGE,
        "soldComparableSummary": clean_sold,
        "activeCompetitionSummary": clean_active,
        "dataFreshness": _now(),
        "comparableQuery": query,
    }


def _explicit_seasonal_signal(item: dict[str, Any]) -> dict[str, Any]:
    explicit = item.get("seasonalEvidence")
    if isinstance(explicit, dict) and explicit.get("supported") and explicit.get("signal"):
        return {
            "status": "supported",
            "signal": str(explicit["signal"]),
            "evidence": str(explicit.get("evidence") or "Verified seasonal market evidence"),
        }
    sea_raw = str(item.get("sea") or "").strip()
    prov = item.get("provenance") if isinstance(item.get("provenance"), dict) else {}
    sea_prov = prov.get("sea") if isinstance(prov.get("sea"), dict) else {}
    sea_prov_source = str(sea_prov.get("source") or "")
    has_real_season_source = (
        bool(sea_prov.get("sellerConfirmed"))
        or sea_prov_source in {"seller_input", "observed_photo_fact", "demand_signal"}
    )
    if has_real_season_source and sea_raw.casefold() not in _DEFAULT_NON_EVIDENCE_SEASONS:
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


def demand_score(
    item: dict[str, Any],
    *,
    sold_summary: dict[str, Any] | None = None,
    active_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compute honest demand and velocity signals without fabricating statistics."""
    raw_sold = sold_summary if sold_summary is not None else (
        item.get("soldComparableSummary") if isinstance(item.get("soldComparableSummary"), dict) else item.get("soldPricing")
    )
    raw_active = active_summary if active_summary is not None else item.get("activeListingEstimate")
    clean_sold, clean_active = sanitize_market_evidence(
        raw_sold if isinstance(raw_sold, dict) else None,
        raw_active if isinstance(raw_active, dict) else None,
    )
    sold_count = int(clean_sold.get("sampleSize") or 0) if clean_sold.get("status") == "ok" else 0
    active_count = int(clean_active.get("sampleSize") or 0) if clean_active.get("status") == "ok" else 0

    qty_sold = max(0.0, float(item.get("quantitySold") or 0))
    quantity = max(1.0, float(item.get("quantity") or 1))
    has_listing_qty = ("quantitySold" in item and float(item.get("quantitySold") or 0) > 0)
    listing_ratio = min(1.0, qty_sold / (qty_sold + quantity))

    views = float(item.get("views") or 0)
    impressions = float(item.get("impressions") or 0)
    watches = float(item.get("watchCount") or 0)
    has_traffic = views > 0 or impressions > 0 or watches > 0

    sell_through_rate: float | None = None
    sell_through_signal = "unavailable"
    current_demand_status = "insufficient_evidence"
    current_demand_signal = "Insufficient demand evidence"
    provenance_source = "fallback_estimate"
    methodology = "listing_quantity_proxy" if (sold_count == 0 and active_count == 0) else "market_evidence"
    confidence = "low"
    explanation = "Insufficient market evidence to establish category sell-through or demand."
    score: float | None = round(listing_ratio * 100, 1) if has_listing_qty else (0.0 if "quantitySold" in item else None)

    if sold_count > 0:
        sell_through_rate = round(sold_count / (sold_count + max(active_count, 0)), 3)
        sell_through_signal = "high" if sell_through_rate >= 0.6 else "moderate" if sell_through_rate >= 0.35 else "slow"
        current_demand_status = "supported"
        current_demand_signal = "strong" if sold_count >= 5 and sell_through_rate >= 0.5 else "moderate" if sold_count >= 2 else "limited"
        provenance_source = "demand_signal"
        confidence = "high" if sold_count >= 5 else "medium"
        score = round(sell_through_rate * 100, 1)
        explanation = f"Derived from {sold_count} verified sold comps and {active_count} active listings."
    elif active_count > 0:
        current_demand_status = "active_competition_only"
        current_demand_signal = f"Active competition observed ({active_count} comparable listings; sold velocity unavailable)"
        provenance_source = "market_active_comp"
        confidence = "low"
        explanation = f"{active_count} active listings observed; sold count and sell-through rate are unavailable."
    elif has_traffic:
        current_demand_status = "listing_traffic"
        current_demand_signal = "Listing traffic observed"
        provenance_source = "demand_signal"
        confidence = "low"
        score = round(min(100.0, (views * 2 + watches * 5 + impressions * 0.2)), 1)
        explanation = "Derived from seller listing traffic metrics."
    elif has_listing_qty:
        current_demand_status = "listing_quantity_proxy"
        current_demand_signal = "Listing quantity proxy"
        provenance_source = "demand_signal"
        explanation = "Proxy based on listing quantity fields; not official eBay-wide sell-through data."

    seasonal = _explicit_seasonal_signal(item)
    return {
        "score": score,
        "confidence": confidence,
        "methodology": methodology,
        "provenanceSource": provenance_source,
        "sampleSize": sold_count + active_count if (sold_count or active_count) else int(qty_sold + quantity),
        "soldCount": sold_count,
        "activeCount": active_count,
        "sellThroughRate": sell_through_rate,
        "sellThroughSignal": sell_through_signal,
        "currentDemandStatus": current_demand_status,
        "currentDemandSignal": current_demand_signal,
        "seasonalDemand": seasonal,
        "dataFreshness": _now(),
        "explanation": explanation,
        "sellThroughProxy": round(listing_ratio, 3),
        "quantitySold": int(qty_sold),
        "quantity": int(quantity),
    }


def seller_recovery_metrics(recommendations: list[dict[str, Any]], listings: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(listings)
    pending = sum(1 for recommendation in recommendations if recommendation.get("status") == "Pending")
    approved = sum(1 for recommendation in recommendations if recommendation.get("status") == "Approved")
    applied = sum(1 for recommendation in recommendations if recommendation.get("status") == "Applied")
    high_risk = sum(1 for recommendation in recommendations if recommendation.get("risk") == "high" and recommendation.get("status") == "Pending")
    missing_specifics = sum(1 for recommendation in recommendations if any(finding.get("field") == "item_specifics" for finding in recommendation.get("findings", [])))
    title_opportunities = sum(1 for recommendation in recommendations if any(finding.get("field") == "title" for finding in recommendation.get("findings", [])))
    pricing_opportunities = sum(1 for recommendation in recommendations if any(finding.get("field") == "price" for finding in recommendation.get("findings", [])))
    category_confirmation_needed = sum(
        1
        for recommendation in recommendations
        if (recommendation.get("taxonomy") or {}).get("status") == "missing"
        or (recommendation.get("taxonomy") or {}).get("aspectReviewRequired")
    )
    taxonomy_unavailable = sum(
        1
        for recommendation in recommendations
        if (recommendation.get("taxonomy") or {}).get("status") in {"unavailable", "not_configured"}
    )
    low_confidence = sum(1 for recommendation in recommendations if recommendation.get("confidence") == "low" or (recommendation.get("soldPricing") or {}).get("pricingConfidence") == "low")
    evidence_gaps = sum(1 for recommendation in recommendations if any(finding.get("field") in {"item_specifics", "taxonomy", "price"} for finding in recommendation.get("findings", [])))
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
        # Kept for compatibility with early dashboard clients. A missing seller
        # category is a confirmation task, not a failed eBay validation.
        "categoryValidationFailures": taxonomy_unavailable,
        "categoryConfirmationNeeded": category_confirmation_needed,
        "taxonomyValidationUnavailable": taxonomy_unavailable,
        "unresolvedEvidenceGaps": evidence_gaps,
        "label": "Operational catalog metrics only",
        "note": "These are operational recovery indicators only; official eBay Seller Hub performance metrics are not connected.",
    }


def _pricing_result(
    source: str,
    recommended: float,
    current: float,
    confidence: str,
    sample_size: int,
    low: float,
    high: float,
    reason: str,
    query: str,
    *,
    ai_price: float = 0.0,
    sold_summary: dict[str, Any] | None = None,
    active_summary: dict[str, Any] | None = None,
    demand: dict[str, Any] | None = None,
    p25: float = 0.0,
    p75: float = 0.0,
    insufficient: bool = False,
) -> dict[str, Any]:
    change = round(((recommended - current) / current) * 100, 1) if current > 0 else None
    low_value = round(low or recommended, 2)
    high_value = round(high or recommended, 2)
    target_value = round(recommended, 2)
    if source in {"exact_used_sold", "similar_used_sold"}:
        quick_value = min(low_value, round(target_value * 0.9, 2)) if low_value < target_value else round(target_value * 0.88, 2)
        if p25 > 0 and p25 < target_value and p25 >= low_value:
            quick_value = min(quick_value, p25) if quick_value > p25 else quick_value
        premium_value = high_value if high_value > target_value else round(target_value * 1.15, 2)
        provenance_source = "market_sold_comp"
        pricing_strategy = "sold_comp_anchored"
        market_status = "verified_sold_evidence"
    elif source == "active_comparable":
        if p25 > 0 and p25 < target_value:
            quick_value = max(low_value, min(p25, round(target_value * 0.92, 2)))
        else:
            quick_value = round(target_value * 0.85, 2)
        if p75 > target_value:
            premium_value = min(max(p75, round(target_value * 1.10, 2)), round(target_value * 1.65, 2))
        elif high_value > target_value:
            premium_value = high_value if high_value <= target_value * 2.0 else round(target_value * 1.35, 2)
        else:
            premium_value = round(target_value * 1.15, 2)
        provenance_source = "market_active_comp"
        pricing_strategy = "active_comp_quartile"
        market_status = "active_comp_evidence"
    else:
        quick_value = round(target_value * 0.9, 2)
        premium_value = round(target_value * 1.1, 2)
        provenance_source = "fallback_estimate" if source == "ai_estimate" else "seller_input"
        pricing_strategy = "ai_fallback_unverified" if source == "ai_estimate" else "seller_price_fallback"
        market_status = "insufficient_market_evidence"

    # Guarantee distinct pricing tiers whenever target >= $2.00
    if target_value >= 2.0:
        if quick_value >= target_value:
            quick_value = round(target_value * 0.9, 2)
        if premium_value <= target_value:
            premium_value = round(target_value * 1.15, 2)

    demand_payload = demand if isinstance(demand, dict) else {}
    return {
        "status": "ok",
        "recommendedPrice": target_value,
        "marketRecommendedPrice": None if insufficient else target_value,
        "fastSalePrice": quick_value,
        "quickSalePrice": quick_value,
        "balancedPrice": target_value,
        "targetPrice": target_value,
        "premiumPrice": premium_value,
        "aiEstimatedPrice": round(ai_price, 2) if ai_price > 0 else None,
        "pricingSource": source,
        "provenanceSource": provenance_source,
        "pricingStrategy": pricing_strategy,
        "pricingConfidence": confidence,
        "insufficientMarketEvidence": bool(insufficient),
        "marketStatus": market_status,
        "marketMessage": INSUFFICIENT_MARKET_MESSAGE if insufficient else "",
        "requiresSellerConfirmation": bool(insufficient),
        "isFallback": bool(insufficient),
        "sampleSize": sample_size,
        "lowPrice": low_value,
        "highPrice": high_value,
        "currentPrice": round(current, 2),
        "recommendedChangePct": change,
        "adjustmentReason": reason,
        "soldComparableSummary": sold_summary or {"status": "not_configured", "sampleSize": 0},
        "activeCompetitionSummary": active_summary or {"status": "no_matches", "sampleSize": 0},
        "demandSignal": demand_payload.get("currentDemandSignal", ""),
        "currentDemandStatus": demand_payload.get("currentDemandStatus", "insufficient_evidence"),
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
    return str(record.get("listingState") or record.get("state") or "sold").lower() not in {"active", "unsold"}


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
    value = record.get("soldPrice") or record.get("sold_price") or record.get("salePrice") or record.get("Sale Price") or record.get("totalPrice") or record.get("price") or record.get("value")
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
