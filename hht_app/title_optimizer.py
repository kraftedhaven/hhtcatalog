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
            "changed": False,
            "length": len(current),
            "confidence": "low",
            "reason": "Seller-edited metadata is present; title is preserved for seller review.",
        }
    parts = []
    for key in ("brand", "model", "type", "style", "theme", "mat", "color", "pat", "size"):
        value = str(item.get(key) or "").strip()
        if value and value.lower() not in STOP and value.casefold() not in {p.casefold() for p in parts}:
            parts.append(value)
    vintage = str(item.get("vin") or item.get("vintage") or "").strip()
    if vintage.lower().startswith("yes") and "vintage" not in {p.casefold() for p in parts}:
        parts.append("Vintage")
    if current:
        parts = [current] + [p for p in parts if p.casefold() not in current.casefold()]
    candidate = fit_title(re.sub(r"\s+", " ", " ".join(parts)).strip(), str(item.get("brand") or ""))
    changed = bool(candidate and candidate.casefold() != current.casefold())
    confidence = "medium" if changed else "low"
    return {
        "title": candidate if changed else "",
        "changed": changed,
        "length": len(candidate),
        "confidence": confidence,
        "reason": "Uses confirmed/imported attributes in a category-aware order; seller must verify inferred fields.",
    }
