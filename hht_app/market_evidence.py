"""Pure market evidence sanitization and pricing tier computation helpers."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

PRICING_SOURCES = {
    "exact_used_sold",
    "similar_used_sold",
    "active_comparable",
    "ai_estimate",
    "seller_price_fallback",
    "fallback_estimate",
}

INSUFFICIENT_MARKET_MESSAGE = "Insufficient market evidence \u2014 seller confirmation required."

_UNAVAILABLE_ACTIVE_STATUSES = {"unavailable", "error", "invalid_evidence", "not_configured"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _money(value: Any) -> float:
    try:
        parsed = float(value or 0)
    except (TypeError, ValueError):
        return 0.0
    return round(parsed, 2) if parsed > 0 else 0.0


def _safe_int(value: Any, default: int = -1) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return default


def _clamp_range(low: float, median: float, high: float) -> tuple[float, float]:
    clamped_low = median if (low <= 0 or low > median) else low
    clamped_high = median if high < median else high
    return round(clamped_low, 2), round(clamped_high, 2)


def _has_rejected_comp_stats(raw_status: str, sample_size: int, median: float) -> bool:
    if sample_size < 0:
        return True
    if sample_size > 0 and median <= 0:
        return True
    if raw_status == "ok":
        return sample_size <= 0 or median <= 0
    return False


def _empty_sold_summary(query: str = "", status: str = "not_configured", message: str = "") -> dict[str, Any]:
    return {
        "status": status,
        "kind": "sold_comps",
        "sampleSize": 0,
        "query": query,
        "message": message or INSUFFICIENT_MARKET_MESSAGE,
    }


def _valid_sold_payload(sold_summary: dict[str, Any], query: str, sample_size: int, median: float) -> dict[str, Any]:
    low_sold, high_sold = _clamp_range(
        _money(sold_summary.get("lowSoldPrice")), median, _money(sold_summary.get("highSoldPrice"))
    )
    src = sold_summary.get("pricingSource")
    pricing_src = src if src in {"exact_used_sold", "similar_used_sold"} else "similar_used_sold"
    default_msg = "Sold-comparable summary is advisory; no price change is applied automatically."
    return {
        "status": "ok",
        "kind": "sold_comps",
        "source": str(sold_summary.get("source") or "verified_sold_comps"),
        "pricingSource": pricing_src,
        "sampleSize": sample_size,
        "query": query,
        "lowSoldPrice": low_sold,
        "medianSoldPrice": median,
        "highSoldPrice": high_sold,
        "p25SoldPrice": _money(sold_summary.get("p25SoldPrice")),
        "p75SoldPrice": _money(sold_summary.get("p75SoldPrice")),
        "dataFreshness": str(sold_summary.get("dataFreshness") or _now()),
        "message": str(sold_summary.get("message") or default_msg),
    }


def _is_valid_sold_dict(raw_status: str, sample_size: int, median_sold: float) -> bool:
    if raw_status != "ok" or sample_size <= 0:
        return False
    return median_sold > 0


def _sanitize_sold_summary(sold_summary: dict[str, Any] | None) -> dict[str, Any]:
    """Validate sold comparable data and reject invalid or fabricated stats."""
    if not isinstance(sold_summary, dict):
        return {"status": "not_configured", "kind": "sold_comps", "sampleSize": 0, "query": ""}

    query = str(sold_summary.get("query") or "").strip()
    raw_status = str(sold_summary.get("status") or "").strip()
    sample_size = _safe_int(sold_summary.get("sampleSize"))
    median_sold = _money(sold_summary.get("medianSoldPrice"))

    if _is_valid_sold_dict(raw_status, sample_size, median_sold):
        return _valid_sold_payload(sold_summary, query, sample_size, median_sold)

    rejected = _has_rejected_comp_stats(raw_status, sample_size, median_sold)
    status = "invalid_evidence" if rejected else (raw_status or "not_configured")
    result = _empty_sold_summary(query, status, str(sold_summary.get("message") or ""))
    if rejected:
        result["validationFlag"] = "rejected_fabricated_or_invalid_stats"
    return result


def _base_active_summary(keyword: str = "", status: str = "not_configured") -> dict[str, Any]:
    return {
        "provider": "ebay_browse",
        "kind": "active_comparable",
        "status": status,
        "keyword": keyword,
        "sampleSize": 0,
        "medianActivePrice": 0.0,
        "lowActivePrice": 0.0,
        "highActivePrice": 0.0,
    }


def _valid_active_payload(
    active_summary: dict[str, Any], keyword: str, sample_size: int, median: float
) -> dict[str, Any]:
    low_active, high_active = _clamp_range(
        _money(active_summary.get("lowActivePrice")), median, _money(active_summary.get("highActivePrice"))
    )
    return {
        "provider": str(active_summary.get("provider") or "ebay_browse"),
        "kind": "active_comparable",
        "status": "ok",
        "keyword": keyword,
        "sampleSize": sample_size,
        "medianActivePrice": median,
        "lowActivePrice": low_active,
        "highActivePrice": high_active,
        "p25ActivePrice": _money(active_summary.get("p25ActivePrice")),
        "p75ActivePrice": _money(active_summary.get("p75ActivePrice")),
        "dataFreshness": str(active_summary.get("dataFreshness") or _now()),
    }


def _is_valid_active_dict(raw_status: str, sample_size: int, median_active: float) -> bool:
    if sample_size <= 0 or median_active <= 0:
        return False
    return raw_status not in _UNAVAILABLE_ACTIVE_STATUSES


def _sanitize_active_summary(active_summary: dict[str, Any] | None) -> dict[str, Any]:
    """Validate active competition summary without confusing unavailable data with zero listings."""
    if not isinstance(active_summary, dict):
        return _base_active_summary()

    keyword = str(active_summary.get("keyword") or active_summary.get("query") or "").strip()
    raw_status = str(active_summary.get("status") or "").strip()
    sample_size = _safe_int(active_summary.get("sampleSize"))
    median_active = _money(active_summary.get("medianActivePrice"))

    if _is_valid_active_dict(raw_status, sample_size, median_active):
        return _valid_active_payload(active_summary, keyword, sample_size, median_active)

    if _has_rejected_comp_stats(raw_status, sample_size, median_active):
        out = _base_active_summary(keyword, "invalid_evidence")
        out["validationFlag"] = "rejected_fabricated_or_invalid_stats"
        return out
    return _base_active_summary(keyword, raw_status or "not_configured")


def sanitize_market_evidence(
    sold_summary: dict[str, Any] | None = None,
    active_summary: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate market comp summaries and reject fabricated or malformed statistics."""
    return _sanitize_sold_summary(sold_summary), _sanitize_active_summary(active_summary)


def _sold_tier_prices(
    target: float, low: float, high: float, p25: float
) -> tuple[float, float, str, str, str]:
    quick = min(low, round(target * 0.9, 2)) if low < target else round(target * 0.88, 2)
    if 0 < p25 < target and p25 >= low:
        quick = min(quick, p25)
    premium = high if high > target else round(target * 1.15, 2)
    return quick, premium, "market_sold_comp", "sold_comp_anchored", "verified_sold_evidence"


def _active_tier_prices(
    target: float, low: float, high: float, p25: float, p75: float
) -> tuple[float, float, str, str, str]:
    quick = max(low, min(p25, round(target * 0.92, 2))) if 0 < p25 < target else round(target * 0.85, 2)
    if p75 > target:
        premium = min(max(p75, round(target * 1.10, 2)), round(target * 1.65, 2))
    elif high > target:
        premium = high if high <= target * 2.0 else round(target * 1.35, 2)
    else:
        premium = round(target * 1.15, 2)
    return quick, premium, "market_active_comp", "active_comp_quartile", "active_comp_evidence"


def _fallback_tier_prices(source: str, target: float) -> tuple[float, float, str, str, str]:
    prov = "fallback_estimate" if source == "ai_estimate" else "seller_input"
    strategy = "ai_fallback_unverified" if source == "ai_estimate" else "seller_price_fallback"
    return round(target * 0.9, 2), round(target * 1.1, 2), prov, strategy, "insufficient_market_evidence"


def _resolve_tier_tuple(
    source: str, target: float, low_val: float, high_val: float, p25: float, p75: float
) -> tuple[float, float, str, str, str]:
    if source in {"exact_used_sold", "similar_used_sold"}:
        return _sold_tier_prices(target, low_val, high_val, p25)
    if source == "active_comparable":
        return _active_tier_prices(target, low_val, high_val, p25, p75)
    return _fallback_tier_prices(source, target)


def _ensure_distinct_tiers(quick: float, target: float, premium: float) -> tuple[float, float]:
    if target < 2.0:
        return quick, premium
    adjusted_quick = round(target * 0.9, 2) if quick >= target else quick
    adjusted_premium = round(target * 1.15, 2) if premium <= target else premium
    return adjusted_quick, adjusted_premium


def _demand_meta_fields(demand: Any) -> tuple[str, str]:
    if not isinstance(demand, dict):
        return "", "insufficient_evidence"
    return (
        str(demand.get("currentDemandSignal") or ""),
        str(demand.get("currentDemandStatus") or "insufficient_evidence"),
    )


def build_pricing_result(
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
    """Assemble Fast Sale, Balanced, and Premium pricing outputs with strict provenance."""
    low_val = round(low or recommended, 2)
    high_val = round(high or recommended, 2)
    target = round(recommended, 2)
    quick, premium, prov, strategy, market_status = _resolve_tier_tuple(
        source, target, low_val, high_val, p25, p75
    )
    quick, premium = _ensure_distinct_tiers(quick, target, premium)
    change = round(((recommended - current) / current) * 100, 1) if current > 0 else None
    demand_signal, demand_status = _demand_meta_fields(demand)
    empty_sold = {"status": "not_configured", "sampleSize": 0}
    empty_active = {"status": "not_configured", "sampleSize": 0}

    return {
        "status": "ok",
        "recommendedPrice": target,
        "marketRecommendedPrice": None if insufficient else target,
        "fastSalePrice": quick,
        "quickSalePrice": quick,
        "balancedPrice": target,
        "targetPrice": target,
        "premiumPrice": premium,
        "aiEstimatedPrice": round(ai_price, 2) if ai_price > 0 else None,
        "pricingSource": source,
        "provenanceSource": prov,
        "pricingStrategy": strategy,
        "pricingConfidence": confidence,
        "insufficientMarketEvidence": bool(insufficient),
        "marketStatus": market_status,
        "marketMessage": INSUFFICIENT_MARKET_MESSAGE if insufficient else "",
        "requiresSellerConfirmation": bool(insufficient),
        "isFallback": bool(insufficient),
        "sampleSize": sample_size,
        "lowPrice": low_val,
        "highPrice": high_val,
        "currentPrice": round(current, 2),
        "recommendedChangePct": change,
        "adjustmentReason": reason,
        "soldComparableSummary": sold_summary if isinstance(sold_summary, dict) else empty_sold,
        "activeCompetitionSummary": active_summary if isinstance(active_summary, dict) else empty_active,
        "demandSignal": demand_signal,
        "currentDemandStatus": demand_status,
        "dataFreshness": _now(),
        "comparableQuery": query,
    }


def build_error_pricing_result(
    current: float,
    query: str,
    sold_summary: dict[str, Any],
    active_summary: dict[str, Any],
) -> dict[str, Any]:
    """Return structured insufficient-evidence error result when no numeric price exists."""
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
        "soldComparableSummary": sold_summary,
        "activeCompetitionSummary": active_summary,
        "dataFreshness": _now(),
        "comparableQuery": query,
    }
