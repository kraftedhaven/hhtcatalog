from __future__ import annotations
import re
from typing import Any
from .schema import fit_title

STOP = {"not visible", "unknown", "n/a", "n/a - bag", "n/a - footwear", "no brand"}

def optimize_title(item: dict[str, Any], *, category_name: str = "") -> dict[str, Any]:
    current = str(item.get("title") or "").strip()
    if item.get("sellerEditedPrice") is True or item.get("sellerEditedTitle") is True or item.get("sellerEditedMetadata") is True:
        return {
            "title": "",
            "candidates": {},
            "changed": False,
            "length": len(current),
            "confidence": "low",
            "reason": "Seller-edited metadata is present; title is preserved for seller review.",
        }
    values = {}
    for key in ("brand", "model", "type", "style", "theme", "mat", "color", "pat", "size"):
        value = str(item.get(key) or "").strip()
        if value and value.lower() not in STOP:
            values[key] = value
    vintage = str(item.get("vin") or item.get("vintage") or "").strip()
    if vintage.lower().startswith("yes"):
        values["vintage"] = "Vintage"

    def build(keys: tuple[str, ...]) -> str:
        parts = []
        for key in keys:
            value = values.get(key, "")
            if value and value.casefold() not in {p.casefold() for p in parts}:
                parts.append(value)
        return fit_title(re.sub(r"\s+", " ", " ".join(parts)).strip(), values.get("brand", ""))

    candidates = {}
    for name, keys, reason in (
        ("search_first", ("brand", "model", "type", "style", "theme", "mat", "color", "pat", "size", "vintage"), "Puts the strongest confirmed identity and item terms first."),
        ("balanced", ("brand", "model", "type", "color", "mat", "style", "pat", "size", "vintage"), "Balances buyer-readable wording with confirmed search attributes."),
        ("quick_sale", ("brand", "type", "model", "color", "size", "style", "mat", "pat", "vintage"), "Leads with the clearest item type and buyer-facing details."),
    ):
        candidate = build(keys)
        if candidate:
            candidates[name] = {
                "title": candidate,
                "length": len(candidate),
                "attributesUsed": [key for key in keys if key in values],
                "confidence": "medium" if len(values) >= 3 else "low",
                "reason": reason,
            }

    parts = [values[key] for key in ("brand", "model", "type", "style", "theme", "mat", "color", "pat", "size", "vintage") if key in values]
    if current:
        parts = [current] + [p for p in parts if p.casefold() not in current.casefold()]
    candidate = fit_title(re.sub(r"\s+", " ", " ".join(parts)).strip(), values.get("brand", ""))
    changed = bool(candidate and candidate.casefold() != current.casefold())
    confidence = "medium" if changed else "low"
    return {
        "title": candidate if changed else "",
        "candidates": candidates,
        "changed": changed,
        "length": len(candidate),
        "confidence": confidence,
        "reason": "Uses confirmed/imported attributes in a category-aware order; seller must verify inferred fields.",
    }
