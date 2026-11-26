"""SEO title optimization within eBay's 80-character limit."""

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

_ATTR_ORDER = ("brand", "vintage", "model", "type", "dept", "style", "mat", "color", "size", "pat", "nk", "slv", "theme")

_STRATEGY_SPECS = (
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

_DEPT_ALIASES = {
    "men": "Men's",
    "man": "Men's",
    "mens": "Men's",
    "men's": "Men's",
    "women": "Women's",
    "woman": "Women's",
    "womens": "Women's",
    "women's": "Women's",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _clean_department(raw_dept: str) -> str:
    stripped = raw_dept.strip()
    return _DEPT_ALIASES.get(stripped.casefold(), stripped)


def _is_usable_attr(raw: str) -> bool:
    if not raw:
        return False
    lowered = raw.casefold()
    return lowered not in STOP and lowered not in DEFAULT_FILLER_VALUES


def _valid_active(active: Any) -> bool:
    if not isinstance(active, dict):
        return False
    try:
        return int(active.get("sampleSize") or 0) > 0 and float(active.get("medianActivePrice") or 0) > 0
    except (TypeError, ValueError):
        return False


def _valid_sold_count(sold: Any, ctx: dict[str, Any]) -> int:
    explicit = int(ctx.get("soldSampleSize") or 0)
    if explicit > 0:
        return explicit
    if isinstance(sold, dict) and sold.get("status") == "ok":
        return int(sold.get("sampleSize") or 0)
    return 0


def _dict_query_value(mapping: Any, field: str, sample_count: int) -> Any:
    if not isinstance(mapping, dict) or sample_count <= 0:
        return ""
    return mapping.get(field)


def _collect_market_queries(ctx: dict[str, Any], sources: dict[str, Any]) -> list[str]:
    sold_count = int(sources.get("soldCount") or 0)
    active_count = int(sources.get("activeCount") or 0)
    item = sources.get("item") or {}
    fallback_kw = item.get("pricingSearchKeywords") if (sold_count > 0 or active_count > 0) else ""
    raw_candidates = [
        ctx.get("keyword"),
        ctx.get("query"),
        _dict_query_value(sources.get("sold"), "query", sold_count),
        _dict_query_value(sources.get("active"), "keyword", active_count),
        fallback_kw,
    ]
    seen: list[str] = []
    for candidate in raw_candidates:
        text = str(candidate or "").strip()
        if text and text not in seen:
            seen.append(text)
    return seen


def _first_dict(*candidates: Any) -> Any:
    for c in candidates:
        if isinstance(c, dict):
            return c
    return None


def _extract_market_context(item: dict[str, Any], market_context: dict[str, Any] | None) -> dict[str, Any]:
    ctx = dict(market_context) if isinstance(market_context, dict) else {}
    sold = _first_dict(ctx.get("soldSummary"), item.get("soldComparableSummary"), item.get("soldPricing"))
    active = _first_dict(ctx.get("activeSummary"), item.get("activeListingEstimate"))
    sold_count = _valid_sold_count(sold, ctx)
    raw_active_n = active.get("sampleSize") if _valid_active(active) else 0
    active_count = int(ctx.get("activeSampleSize") or raw_active_n or 0)
    queries = _collect_market_queries(
        ctx,
        {"sold": sold, "active": active, "item": item, "soldCount": sold_count, "activeCount": active_count},
    )
    return {
        "soldSampleSize": sold_count,
        "activeSampleSize": active_count,
        "queries": queries,
        "hasMarketEvidence": sold_count > 0 or active_count > 0,
    }


def _extract_category_leaf(item: dict[str, Any], category_name: str) -> str:
    cat_label = str(category_name or item.get("categoryName") or "").strip()
    if not cat_label or "not" in cat_label.casefold():
        return ""
    leaf = cat_label.split(">")[-1].strip()
    return leaf if (leaf and leaf.casefold() not in STOP) else ""


def _clean_values(item: dict[str, Any], category_name: str = "") -> dict[str, str]:
    values: dict[str, str] = {}
    for key in ("brand", "model", "dept", "type", "style", "theme", "mat", "color", "pat", "size", "nk", "slv"):
        raw = str(item.get(key) or "").strip()
        if _is_usable_attr(raw):
            values[key] = _clean_department(raw) if key == "dept" else raw
    vintage = str(item.get("vin") or item.get("vintage") or "").strip()
    if vintage.casefold().startswith("yes"):
        values["vintage"] = "Vintage"
    leaf = _extract_category_leaf(item, category_name)
    if leaf:
        values["category"] = leaf
    return values


def _should_skip_token(cleaned: str, excluded_cf: str, seen_cf: set[str]) -> bool:
    if len(cleaned) < 2:
        return True
    low = cleaned.casefold()
    if low in excluded_cf or low in STOP:
        return True
    return low in seen_cf


def _unique_novel_tokens(text: str, excluded_cf: str, limit: int) -> list[str]:
    tokens: list[str] = []
    seen_cf: set[str] = set()
    for raw in re.findall(r"[A-Za-z0-9'&-]+", text):
        cleaned = raw.strip("-'&")
        if _should_skip_token(cleaned, excluded_cf, seen_cf):
            continue
        tokens.append(cleaned)
        seen_cf.add(cleaned.casefold())
        if len(tokens) >= limit:
            break
    return tokens


def _extra_descriptor_tokens(current: str, values: dict[str, str]) -> list[str]:
    if not current or current.strip().casefold() in GENERIC_TITLES:
        return []
    return _unique_novel_tokens(current, " ".join(values.values()).casefold(), 4)


def _extract_market_tokens(queries: list[str], values: dict[str, str]) -> list[str]:
    return _unique_novel_tokens(" ".join(queries), " ".join(values.values()).casefold(), 3)


def _append_tokens_dedup(parts: list[str], tokens: list[str]) -> list[str]:
    seen_words = {word.casefold() for part in parts for word in re.findall(r"[A-Za-z0-9']+", part)}
    for token in tokens:
        words = [w.casefold() for w in re.findall(r"[A-Za-z0-9']+", token)]
        if words and all(w in seen_words for w in words):
            continue
        parts.append(token)
        seen_words.update(words)
    return parts


def _build_candidate_title(
    values: dict[str, str],
    keys: tuple[str, ...],
    token_bundle: dict[str, Any],
) -> str:
    brand_prefix = values.get("brand", "")
    parts: list[str] = []
    _append_tokens_dedup(parts, [values[k] for k in keys if k in values])
    _append_tokens_dedup(parts, list(token_bundle.get("extras") or []))
    _append_tokens_dedup(parts, list(token_bundle.get("market") or []))
    current = str(token_bundle.get("current") or "")
    if not parts:
        return fit_title(current, brand_prefix) if current else ""
    return fit_title(re.sub(r"\s+", " ", " ".join(parts)).strip(), brand_prefix)


def _compute_title_confidence(mctx: dict[str, Any], attr_count: int) -> str:
    if mctx["soldSampleSize"] >= 3:
        return "high"
    if mctx["activeSampleSize"] >= 5 and attr_count >= 3:
        return "high"
    if mctx["hasMarketEvidence"] or attr_count >= 3:
        return "medium"
    return "low"


def _build_keyword_priority(values: dict[str, str], market_tokens: list[str]) -> list[dict[str, Any]]:
    priority = [
        {"term": values[k], "field": k, "score": ATTRIBUTE_WEIGHTS.get(k, 40), "source": "observed_or_inferred_attribute"}
        for k in _ATTR_ORDER
        if k in values
    ]
    for tok in market_tokens:
        priority.append({"term": tok, "field": "market_query", "score": 55, "source": "demand_signal"})
    return priority


def _strategy_profile(has_evidence: bool) -> dict[str, Any]:
    return {
        "strategy": "demand_backed_seo" if has_evidence else "ai_attribute_fallback",
        "label": "Demand-Backed SEO" if has_evidence else "AI Fallback (No Market Comps)",
        "provenanceSource": "demand_signal" if has_evidence else "fallback_estimate",
    }


def _build_candidates(
    values: dict[str, str],
    mctx: dict[str, Any],
    candidate_ctx: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    has_evidence = bool(mctx["hasMarketEvidence"])
    prof = _strategy_profile(has_evidence)
    extras = list(candidate_ctx.get("extras") or [])
    market_tokens = list(candidate_ctx.get("market") or []) if has_evidence else []
    current = str(candidate_ctx.get("current") or "")
    confidence = str(candidate_ctx.get("confidence") or "low")
    candidates: dict[str, dict[str, Any]] = {}

    for name, keys, reason, base_score in _STRATEGY_SPECS:
        bundle = {"extras": [] if name == "quick_sale" else extras, "market": market_tokens, "current": current}
        cand_title = _build_candidate_title(values, keys, bundle)
        if not cand_title:
            continue
        score = min(100, base_score if has_evidence else max(50, base_score - 20))
        candidates[name] = {
            "title": cand_title,
            "length": len(cand_title),
            "score": score,
            "attributesUsed": [key for key in keys if key in values],
            "confidence": confidence,
            "strategy": prof["strategy"],
            "strategyLabel": prof["label"],
            "isFallback": not has_evidence,
            "provenanceSource": prof["provenanceSource"],
            "reason": reason if has_evidence else f"{reason} AI attribute fallback (no market comps).",
        }
    return candidates


def _seller_preserved_result(current: str, now: str) -> dict[str, Any]:
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


def _keyword_evidence_list(mctx: dict[str, Any]) -> list[str]:
    evidence: list[str] = []
    if mctx["soldSampleSize"] > 0:
        evidence.append(f"{mctx['soldSampleSize']} sold comps")
    if mctx["activeSampleSize"] > 0:
        evidence.append(f"{mctx['activeSampleSize']} active comps")
    if mctx["queries"]:
        evidence.append(f"Query traction: {', '.join(mctx['queries'][:2])}")
    return evidence


def _build_product_reasoning(values: dict[str, str], keyword_evidence: list[str]) -> str:
    noun = values.get("type") or values.get("category") or "item"
    raw = f"Identified {values.get('brand', 'unbranded')} {values.get('model', '')} {noun}"
    base = re.sub(r"\s+", " ", raw).strip()
    if keyword_evidence:
        return f"{base} backed by {'; '.join(keyword_evidence)}."
    return f"{base} without external market comp evidence (AI fallback)."


def _seller_edited(item: dict[str, Any]) -> bool:
    return any(item.get(f) is True for f in ("sellerEditedPrice", "sellerEditedTitle", "sellerEditedMetadata"))


def _compute_keyword_coverage(confirmed_keywords: list[str], best: str) -> float:
    if not confirmed_keywords or not best:
        return 0.0
    lowered_best = best.casefold()
    matched = sum(1 for val in confirmed_keywords if val.casefold() in lowered_best)
    return round(matched / len(confirmed_keywords), 2)


def _assemble_seo_metadata(meta_ctx: dict[str, Any]) -> dict[str, Any]:
    prof = meta_ctx["profile"]
    has_evidence = bool(meta_ctx["has_evidence"])
    return {
        "confirmedKeywords": meta_ctx["confirmed_keywords"],
        "keywordPriority": meta_ctx["keyword_priority"],
        "keywordEvidence": meta_ctx["keyword_evidence"],
        "keywordCoverage": meta_ctx["coverage"],
        "titleLimit": 80,
        "strategy": prof["strategy"],
        "strategyLabel": prof["label"],
        "isFallback": not has_evidence,
        "confidence": meta_ctx["confidence"],
        "provenanceSource": prof["provenanceSource"],
        "productReasoning": meta_ctx["reasoning"],
        "timestamp": meta_ctx["now"],
        "source": "market_and_attributes" if has_evidence else "ai_attribute_fallback",
        "note": (
            "Title prioritized using brand, model, product attributes, and market query traction."
            if has_evidence
            else "No market evidence was available; title is an AI attribute fallback requiring seller verification."
        ),
    }


def optimize_title(
    item: dict[str, Any],
    *,
    category_name: str = "",
    market_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Generate an eBay SEO title and 3 strategy candidates within 80 characters."""
    current = str(item.get("title") or "").strip()
    now = _now_iso()
    if _seller_edited(item):
        return _seller_preserved_result(current, now)

    values = _clean_values(item, category_name=category_name)
    mctx = _extract_market_context(item, market_context)
    has_evidence = mctx["hasMarketEvidence"]
    prof = _strategy_profile(has_evidence)
    extras = _extra_descriptor_tokens(current, values)
    market_tokens = _extract_market_tokens(mctx["queries"], values) if has_evidence else []
    keyword_priority = _build_keyword_priority(values, market_tokens)
    confidence = _compute_title_confidence(mctx, len([k for k in values if k != "category"]))
    candidates = _build_candidates(
        values, mctx, {"extras": extras, "market": market_tokens, "current": current, "confidence": confidence}
    )

    best = (candidates.get("search_first") or {}).get("title") or fit_title(current, values.get("brand", ""))
    changed = bool(best and best.casefold() != current.casefold())
    confirmed_keywords = [val for key, val in values.items() if key != "category" and val]
    keyword_evidence = _keyword_evidence_list(mctx)
    reasoning = _build_product_reasoning(values, keyword_evidence)
    coverage = _compute_keyword_coverage(confirmed_keywords, best)

    return {
        "title": best if changed else "",
        "recommendedTitle": best,
        "originalTitle": current,
        "candidates": candidates,
        "changed": changed,
        "length": len(best),
        "confidence": confidence,
        "strategy": prof["strategy"],
        "strategyLabel": prof["label"],
        "isFallback": not has_evidence,
        "provenanceSource": prof["provenanceSource"],
        "keywordPriority": keyword_priority,
        "keywordEvidence": keyword_evidence,
        "productReasoning": reasoning,
        "timestamp": now,
        "reason": f"{prof['label']}: {reasoning}",
        "seoMetadata": _assemble_seo_metadata({
            "confirmed_keywords": confirmed_keywords,
            "keyword_priority": keyword_priority,
            "keyword_evidence": keyword_evidence,
            "coverage": coverage,
            "profile": prof,
            "confidence": confidence,
            "reasoning": reasoning,
            "now": now,
            "has_evidence": has_evidence,
        }),
    }
