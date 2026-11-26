from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any

from .schema import fit_title

STOP = {
    "not visible",
    "unknown",
    "n/a",
    "n/a - bag",
    "n/a - footwear",
    "no brand",
    "none",
    "unbranded",
}

GENERIC_TITLES = {
    "bag",
    "shirt",
    "polo shirt",
    "top",
    "jacket",
    "coat",
    "pants",
    "jeans",
    "dress",
    "shoes",
    "hat",
    "item",
    "clothing",
    "apparel",
    "review needed item",
}

DEFAULT_FILLER_VALUES = {"all seasons", "casual", "regular", "no", "pre-owned"}

ATTRIBUTE_WEIGHTS = {
    "brand": 100,
    "vintage": 92,
    "model": 90,
    "type": 86,
    "dept": 80,
    "style": 75,
    "mat": 72,
    "color": 68,
    "size": 64,
    "pat": 56,
    "nk": 52,
    "slv": 50,
    "theme": 48,
    "category": 45,
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _clean_department(raw_dept: str) -> str:
    low = raw_dept.strip().casefold()
    if low in {"men", "man", "mens", "men's"}:
        return "Men's"
    if low in {"women", "woman", "womens", "women's"}:
        return "Women's"
    return raw_dept.strip()


def _extract_market_context(item: dict[str, Any], market_context: dict[str, Any] | None) -> dict[str, Any]:
    ctx = dict(market_context) if isinstance(market_context, dict) else {}
    sold = ctx.get("soldSummary") if isinstance(ctx.get("soldSummary"), dict) else (
        item.get("soldComparableSummary") if isinstance(item.get("soldComparableSummary"), dict) else item.get("soldPricing")
    )
    active = ctx.get("activeSummary") if isinstance(ctx.get("activeSummary"), dict) else item.get("activeListingEstimate")
    sold_count = int(ctx.get("soldSampleSize") or (sold.get("sampleSize") if isinstance(sold, dict) and sold.get("status") == "ok" else 0) or 0)
    active_count = int(ctx.get("activeSampleSize") or (active.get("sampleSize") if isinstance(active, dict) and _valid_active(active) else 0) or 0)
    queries = []
    for candidate in (
        ctx.get("keyword"),
        ctx.get("query"),
        sold.get("query") if isinstance(sold, dict) and sold_count > 0 else "",
        active.get("keyword") if isinstance(active, dict) and active_count > 0 else "",
        item.get("pricingSearchKeywords") if (sold_count > 0 or active_count > 0) else "",
    ):
        text = str(candidate or "").strip()
        if text and text not in queries:
            queries.append(text)
    return {
        "soldSampleSize": sold_count,
        "activeSampleSize": active_count,
        "queries": queries,
        "hasMarketEvidence": sold_count > 0 or active_count > 0,
    }


def _valid_active(active: dict[str, Any]) -> bool:
    try:
        return int(active.get("sampleSize") or 0) > 0 and float(active.get("medianActivePrice") or 0) > 0
    except (TypeError, ValueError):
        return False


def _clean_values(item: dict[str, Any], category_name: str = "") -> dict[str, str]:
    values: dict[str, str] = {}
    for key in ("brand", "model", "dept", "type", "style", "theme", "mat", "color", "pat", "size", "nk", "slv"):
        raw = str(item.get(key) or "").strip()
        lowered = raw.casefold()
        if not raw or lowered in STOP or lowered in DEFAULT_FILLER_VALUES:
            continue
        if key == "dept":
            raw = _clean_department(raw)
        values[key] = raw
    vintage = str(item.get("vin") or item.get("vintage") or "").strip()
    if vintage.casefold().startswith("yes"):
        values["vintage"] = "Vintage"
    cat_label = str(category_name or item.get("categoryName") or "").strip()
    if cat_label and "not" not in cat_label.casefold():
        leaf = cat_label.split(">")[-1].strip()
        if leaf and leaf.casefold() not in STOP:
            values["category"] = leaf
    return values


def _extra_descriptor_tokens(current: str, values: dict[str, str]) -> list[str]:
    if not current or current.strip().casefold() in GENERIC_TITLES:
        return []
    existing_cf = " ".join(values.values()).casefold()
    extras: list[str] = []
    for raw_token in re.findall(r"[A-Za-z0-9'&-]+", current):
        cleaned = raw_token.strip("-'&")
        if len(cleaned) < 2:
            continue
        low = cleaned.casefold()
        if low in existing_cf or low in STOP:
            continue
        if low not in {e.casefold() for e in extras}:
            extras.append(cleaned)
    return extras[:4]


def _append_tokens_dedup(parts: list[str], tokens: list[str]) -> list[str]:
    seen_words = {word.casefold() for part in parts for word in re.findall(r"[A-Za-z0-9']+", part)}
    for token in tokens:
        words = [w.casefold() for w in re.findall(r"[A-Za-z0-9']+", token)]
        if words and all(w in seen_words for w in words):
            continue
        parts.append(token)
        seen_words.update(words)
    return parts


def optimize_title(
    item: dict[str, Any],
    *,
    category_name: str = "",
    market_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Generate an eBay SEO title and 3 strategy candidates within 80 characters."""
    current = str(item.get("title") or "").strip()
    now = _now_iso()
    if item.get("sellerEditedPrice") is True or item.get("sellerEditedTitle") is True or item.get("sellerEditedMetadata") is True:
        return {
            "title": "",
            "originalTitle": current,
            "candidates": {},
            "changed": False,
            "length": len(current),
            "confidence": "low",
            "strategy": "seller_preserved",
            "strategyLabel": "Seller Input Preserved",
            "isFallback": False,
            "provenanceSource": "seller_input",
            "keywordPriority": [],
            "keywordEvidence": [],
            "productReasoning": "Seller-edited metadata is present; title preserved.",
            "timestamp": now,
            "reason": "Seller-edited metadata is present; title is preserved for seller review.",
        }

    values = _clean_values(item, category_name=category_name)
    mctx = _extract_market_context(item, market_context)
    has_market_evidence = mctx["hasMarketEvidence"]
    extra_descriptors = _extra_descriptor_tokens(current, values)

    # Market traction keywords taken strictly from verified market comp queries
    market_tokens: list[str] = []
    if has_market_evidence:
        known_cf = " ".join(values.values()).casefold()
        for q in mctx["queries"]:
            for tok in re.findall(r"[A-Za-z0-9'&-]+", q):
                t = tok.strip("-'&")
                if len(t) >= 2 and t.casefold() not in known_cf and t.casefold() not in {x.casefold() for x in market_tokens}:
                    market_tokens.append(t)

    keyword_priority: list[dict[str, Any]] = []
    for key in ("brand", "vintage", "model", "type", "dept", "style", "mat", "color", "size", "pat", "nk", "slv", "theme"):
        if key in values:
            keyword_priority.append({
                "term": values[key],
                "field": key,
                "score": ATTRIBUTE_WEIGHTS.get(key, 40),
                "source": "observed_or_inferred_attribute",
            })
    for tok in market_tokens[:3]:
        keyword_priority.append({
            "term": tok,
            "field": "market_query",
            "score": 55,
            "source": "demand_signal",
        })

    strategy = "demand_backed_seo" if has_market_evidence else "ai_attribute_fallback"
    is_fallback = not has_market_evidence
    provenance_source = "demand_signal" if has_market_evidence else "fallback_estimate"
    strategy_label = (
        "Demand-Backed SEO"
        if has_market_evidence
        else "AI Fallback (No Market Comps)"
    )

    brand_prefix = values.get("brand", "")

    def build_candidate(keys: tuple[str, ...], *, include_extras: bool = True, include_market: bool = True) -> str:
        parts: list[str] = []
        raw_seq = [values[k] for k in keys if k in values]
        _append_tokens_dedup(parts, raw_seq)
        if include_extras and extra_descriptors:
            _append_tokens_dedup(parts, extra_descriptors)
        if include_market and market_tokens:
            _append_tokens_dedup(parts, market_tokens[:3])
        if not parts and current:
            return fit_title(current, brand_prefix)
        text = re.sub(r"\s+", " ", " ".join(parts)).strip()
        return fit_title(text, brand_prefix)

    attr_count = len([k for k in values if k != "category"])
    cand_confidence = (
        "high"
        if (mctx["soldSampleSize"] >= 3 or (mctx["activeSampleSize"] >= 5 and attr_count >= 3))
        else "medium"
        if (has_market_evidence or attr_count >= 3)
        else "low"
    )

    candidates: dict[str, dict[str, Any]] = {}
    strategy_specs = (
        (
            "search_first",
            ("brand", "vintage", "model", "dept", "type", "style", "theme", "mat", "color", "pat", "size"),
            "Prioritizes Brand, Model, Type, and high-intent search keywords first.",
            95,
        ),
        (
            "balanced",
            ("brand", "vintage", "dept", "color", "mat", "model", "type", "style", "pat", "size"),
            "Balances natural buyer readability with core search keywords.",
            90,
        ),
        (
            "quick_sale",
            ("brand", "type", "model", "size", "color", "mat", "style", "vintage"),
            "Leads with product type, brand, and key buyer sizing/color filters.",
            85,
        ),
    )
    for name, keys, reason, base_score in strategy_specs:
        cand_title = build_candidate(keys, include_extras=(name != "quick_sale"), include_market=has_market_evidence)
        if cand_title:
            candidates[name] = {
                "title": cand_title,
                "length": len(cand_title),
                "score": min(100, base_score if has_market_evidence else max(50, base_score - 20)),
                "attributesUsed": [key for key in keys if key in values],
                "confidence": cand_confidence,
                "strategy": strategy,
                "strategyLabel": strategy_label,
                "isFallback": is_fallback,
                "provenanceSource": provenance_source,
                "reason": reason if has_market_evidence else f"{reason} AI attribute fallback (no market comps).",
            }

    best_candidate = candidates.get("search_first", {}).get("title") or build_candidate(
        ("brand", "vintage", "model", "dept", "type", "style", "theme", "mat", "color", "pat", "size")
    )
    # Fallback to non-generic original only when no structured brand/model/type could improve it
    if not best_candidate:
        best_candidate = fit_title(current, brand_prefix)

    changed = bool(best_candidate and best_candidate.casefold() != current.casefold())
    confirmed_keywords = [value for key, value in values.items() if key != "category" and value]
    keyword_evidence = [
        f"{mctx['soldSampleSize']} sold comps" if mctx["soldSampleSize"] > 0 else "",
        f"{mctx['activeSampleSize']} active comps" if mctx["activeSampleSize"] > 0 else "",
        f"Query traction: {', '.join(mctx['queries'][:2])}" if mctx["queries"] else "",
    ]
    keyword_evidence = [entry for entry in keyword_evidence if entry]

    product_reasoning = (
        f"Identified {values.get('brand', 'unbranded')} {values.get('model', '')} "
        f"{values.get('type', values.get('category', 'item'))}".replace("  ", " ").strip()
    )
    if keyword_evidence:
        product_reasoning += f" backed by {'; '.join(keyword_evidence)}."
    else:
        product_reasoning += " without external market comp evidence (AI fallback)."

    seo_metadata = {
        "confirmedKeywords": confirmed_keywords,
        "keywordPriority": keyword_priority,
        "keywordEvidence": keyword_evidence,
        "keywordCoverage": round(
            sum(1 for value in confirmed_keywords if value.casefold() in best_candidate.casefold())
            / len(confirmed_keywords),
            2,
        )
        if confirmed_keywords and best_candidate
        else 0.0,
        "titleLimit": 80,
        "strategy": strategy,
        "strategyLabel": strategy_label,
        "isFallback": is_fallback,
        "confidence": cand_confidence,
        "provenanceSource": provenance_source,
        "productReasoning": product_reasoning,
        "timestamp": now,
        "source": "market_and_attributes" if has_market_evidence else "ai_attribute_fallback",
        "note": (
            "Title prioritized using brand, model, product attributes, and market query traction."
            if has_market_evidence
            else "No market evidence was available; title is an AI attribute fallback requiring seller verification."
        ),
    }

    return {
        "title": best_candidate if changed else "",
        "recommendedTitle": best_candidate,
        "originalTitle": current,
        "candidates": candidates,
        "changed": changed,
        "length": len(best_candidate),
        "confidence": cand_confidence,
        "strategy": strategy,
        "strategyLabel": strategy_label,
        "isFallback": is_fallback,
        "provenanceSource": provenance_source,
        "keywordPriority": keyword_priority,
        "keywordEvidence": keyword_evidence,
        "productReasoning": product_reasoning,
        "timestamp": now,
        "reason": (
            f"{strategy_label}: {product_reasoning}"
            if has_market_evidence
            else f"AI Fallback (no market evidence): {product_reasoning}"
        ),
        "seoMetadata": seo_metadata,
    }
